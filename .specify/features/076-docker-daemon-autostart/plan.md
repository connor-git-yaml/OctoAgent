# Feature 076 — 技术规划

> 规范来源：`spec.md`
> 模式：story

## 1. 整体架构

引入一个**单一事实源**函数 `ensure_docker_daemon()`，收敛所有"检测 Docker daemon + 按需启动"的逻辑。所有调用点（`ProxyProcessManager`、`run-octo-home.sh`、未来潜在的其它执行器）只调用此函数，不自行实现检测/启动逻辑。

```
┌───────────────────────────────────────────────┐
│ octo-start (bash)                             │
│  └─ run-octo-home.sh                          │
│       ├─ [NEW] 可选: uv run python -m         │
│       │   octoagent.provider.dx.docker_daemon │
│       │   --ensure --quiet --timeout 30       │
│       └─ uvicorn → gateway                    │
└──────────────────┬────────────────────────────┘
                   │ 后续业务（LiteLLM 激活）
                   ▼
┌───────────────────────────────────────────────┐
│ ProxyProcessManager._start()                  │
│  if compose_file 存在:                         │
│      status = await ensure_docker_daemon()    │
│      if status.available:                     │
│          走 Docker Compose 路径                │
│      else:                                    │
│          走既有降级路径（直接进程/报错）         │
└───────────────────────────────────────────────┘
```

两处调用的区别：
- `run-octo-home.sh`：**best-effort 预热**，目的是把 Docker Desktop 冷启动的 30s 摊入 gateway 启动时段；失败不阻塞
- `ProxyProcessManager`：**语义判断**，决定走 Docker 还是降级路径

## 2. 模块设计

### 2.1 `octoagent.provider.dx.docker_daemon`

```python
# 公共 API（伪代码）
class DockerDaemonStatus(BaseModel):
    available: bool
    auto_started: bool
    platform: Literal["darwin", "linux", "windows", "other"]
    attempts: list[str]  # 命令行级日志
    error: str | None = None
    elapsed_s: float

async def ensure_docker_daemon(
    *,
    timeout_s: float = 60.0,
    auto_start: bool = True,
    poll_interval_s: float = 2.0,
    run_cmd: Callable[..., Awaitable[CommandResult]] | None = None,  # DI for tests
    platform_name: str | None = None,  # DI for tests
) -> DockerDaemonStatus: ...
```

**内部流程**：

```
1. platform = platform_name or _detect_platform()  # darwin/linux/windows/other
2. 初次探测: result = await _probe_daemon(run_cmd)
   - returncode == 0 → return available=True, auto_started=False
3. 若 auto_start=False → return available=False
4. 按 platform 分支启动:
   - darwin: ["open", "-a", "Docker Desktop"]
   - linux:  先 ["systemctl", "--user", "start", "docker"]
            失败再 ["sudo", "-n", "systemctl", "start", "docker"]
   - windows / other: 跳过（不支持）
5. 轮询探测直到超时或就绪（间隔 poll_interval_s）
6. 汇总 attempts 字段，返回 DockerDaemonStatus
```

**CLI 入口**：`python -m octoagent.provider.dx.docker_daemon --ensure [--quiet] [--timeout N]` 直接调用 `asyncio.run(ensure_docker_daemon(...))`，stdout 打印 JSON（供 shell 脚本消费），非 quiet 模式额外 rich panel 输出。

### 2.2 `ProxyProcessManager` 改造

- 移除或替换现有私有 `_docker_available`（保留为瘦 wrapper → 调用 `ensure_docker_daemon(auto_start=False)` 的探测部分），避免双路径
- `_start()` 中对 "found compose_file" 分支的条件从 `await self._docker_available()` 改为：
  ```python
  status = await ensure_docker_daemon(timeout_s=self._docker_autostart_timeout_s)
  if status.available:
      log.info("proxy_docker_daemon_ready", auto_started=status.auto_started, elapsed_s=status.elapsed_s)
      ok = await self._start_docker(compose_file)
      ...
  else:
      log.warning("proxy_docker_daemon_unavailable", platform=status.platform, attempts=status.attempts, error=status.error)
      # 现有降级路径不变
  ```
- 新增 `_docker_autostart_timeout_s` 属性，构造时读取环境变量 `OCTOAGENT_DOCKER_DAEMON_TIMEOUT`（默认 60）
- `stop()` 路径保持不变（仍用轻量 `_docker_available` wrapper 探测）

### 2.3 `run-octo-home.sh` 增强

插入在 `cd "${PROJECT_ROOT}"` 之前：

```bash
if [[ "${OCTOAGENT_AUTOSTART_DOCKER:-1}" == "1" ]]; then
  # best-effort：失败不阻塞主流程
  (
    cd "${PROJECT_ROOT}"
    uv run python -m octoagent.provider.dx.docker_daemon \
      --ensure --quiet \
      --timeout "${OCTOAGENT_DOCKER_DAEMON_TIMEOUT:-30}" \
      || true
  ) &
  # 不等待，让它在后台推进 Docker Desktop 启动
  # ProxyProcessManager 后续调用会自行等待就绪
fi
```

**背景运行 + 不等待** 是关键：uvicorn 继续启动，Docker Desktop 在后台加热；当 LiteLLM Proxy 需要 daemon 时，用户感知延迟接近重叠。

## 3. 跨运行时兼容

- `asyncio.create_subprocess_exec` 所有调用使用参数列表（不走 shell），避免注入
- `subprocess.run` 用于 CLI 入口（同步包装），参数同样列表形式
- `open -a "Docker Desktop"`：macOS 特有；在非 macOS 平台由 platform 检测直接绕过
- `systemctl` 失败返回非零 returncode 被归入 `attempts`，不抛异常

## 4. 配置表面

| 变量 | 默认 | 作用 |
|------|------|------|
| `OCTOAGENT_AUTOSTART_DOCKER` | `1` | `run-octo-home.sh` 是否触发预热 |
| `OCTOAGENT_DOCKER_DAEMON_TIMEOUT` | `60`（shell 预热 `30`） | `ensure_docker_daemon` 超时秒数 |

不新增 CLI flag；所有行为通过环境变量控制，对用户透明。

## 5. 测试策略

- **单元测试**：`octoagent/tests/provider/dx/test_docker_daemon.py`
  - 注入 `run_cmd` mock + `platform_name` 覆盖 4 × 4 矩阵
  - 验证 `attempts` 字段语义
  - 验证超时分支：模拟永远 returncode=1 的 `_probe_daemon`
- **无需集成测试**（`verification_policy.require_real_execution: true` 改由编排器 Phase 4.5 直接跑一次真实的 `python -m octoagent.provider.dx.docker_daemon --ensure --quiet` 完成验证）

## 6. Blueprint 同步

改动涉及"运行时激活链路"，需在完成后同步：
- `docs/blueprint/` 中提及 Docker Compose 启动的章节，补一段"Docker daemon 自动检测 + 启动"
- 不新增宪法原则；现有原则 VI（Degrade Gracefully）/ VIII（Observability）覆盖新行为

## 7. 里程碑归属

归属 M5（文件工作台）下的 DX 增强，但不占用 Feature 序列的"核心里程碑"名额，作为 independent dx fix。

## 8. 回滚预案

- 新模块是纯增加：若出现回归，删除 `docker_daemon.py` + 还原 `ProxyProcessManager._start()` 单行条件 + 还原 `run-octo-home.sh` 一个 block 即可
- 无数据迁移 / schema 变更，回滚成本为零
