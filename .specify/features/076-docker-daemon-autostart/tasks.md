# Feature 076 — 任务分解

> 来源：`plan.md`
> 执行顺序严格遵循下列 T-ID 依赖

## Task T1：新增 `docker_daemon.py` 核心模块

**文件**：`octoagent/packages/provider/src/octoagent/provider/dx/docker_daemon.py`

**内容**：
- `DockerDaemonStatus` Pydantic 模型（字段见 plan §2.1）
- `_detect_platform()` → `Literal["darwin","linux","windows","other"]`，基于 `sys.platform`
- `_run_cmd_default(args, *, timeout)` → `CommandResult(returncode, stdout, stderr, error)` 包装 `asyncio.create_subprocess_exec`
- `_probe_daemon(run_cmd)` → 执行 `docker info` 并返回 bool
- `async def ensure_docker_daemon(...)` 主函数，按 plan §2.1 流程实现
- CLI `main()`：`argparse` 解析 `--ensure/--quiet/--timeout`，`asyncio.run` 执行；stdout 输出 JSON；非 quiet 模式额外调用 `dx.console_output.render_panel`
- 模块底部 `if __name__ == "__main__": main()`

**约束**：
- 全部子进程调用走参数列表（no shell）
- 异常必须被捕获并写入 `DockerDaemonStatus.error`
- `structlog` 事件：`docker_daemon_probe` / `docker_daemon_autostart_attempt` / `docker_daemon_ready` / `docker_daemon_unavailable`

**产物**：单文件，约 180-220 行

---

## Task T2：单元测试

**文件**：`octoagent/tests/provider/dx/test_docker_daemon.py`

**用例矩阵**：
1. `test_already_running_skips_autostart` — mock `docker info` returncode=0，断言 `auto_started=False`, `elapsed_s < 0.1`
2. `test_darwin_autostart_success` — platform="darwin", 前两次 probe 失败，第三次成功；断言 `attempts` 包含 `open -a "Docker Desktop"`
3. `test_linux_systemctl_user_success` — platform="linux", `systemctl --user start docker` returncode=0, 随后 probe 成功
4. `test_linux_fallback_to_sudo` — `systemctl --user` 失败，`sudo -n systemctl` 成功
5. `test_linux_all_attempts_fail_returns_unavailable` — 所有启动命令失败 + probe 持续失败 → `available=False`, `attempts` 记录两条
6. `test_timeout_returns_unavailable` — probe 永远失败，超时触发（通过小 timeout_s + 快速 poll_interval 验证）
7. `test_windows_no_autostart` — platform="windows"，直接返回 `available=False`, `auto_started=False`, attempts 空
8. `test_auto_start_disabled` — `auto_start=False` 且初次 probe 失败 → 不尝试任何启动命令

**mock 策略**：通过 `ensure_docker_daemon` 的 `run_cmd` 和 `platform_name` 参数注入假命令执行器

**产物**：约 200-250 行

---

## Task T3：集成 `ProxyProcessManager`

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py`

**改动**：
1. 顶部 import：`from octoagent.provider.dx.docker_daemon import ensure_docker_daemon`
2. 类 `__init__` 新增 `self._docker_autostart_timeout_s = float(os.environ.get("OCTOAGENT_DOCKER_DAEMON_TIMEOUT", "60"))`
3. `_start()` 方法中第 101 行附近：
   - 把 `if compose_file is not None and await self._docker_available()` 重写为：
     ```python
     if compose_file is not None:
         status = await ensure_docker_daemon(
             timeout_s=self._docker_autostart_timeout_s,
         )
         if status.available:
             log.info("proxy_docker_daemon_ready",
                      auto_started=status.auto_started,
                      elapsed_s=status.elapsed_s)
             ... # 原有 _start_docker 分支
         else:
             log.warning("proxy_docker_daemon_unavailable",
                         platform=status.platform,
                         attempts=status.attempts,
                         error=status.error)
             # 继续往下走直接进程 / 降级分支
     ```
4. `stop()` 路径保留对 `_docker_available()` 的调用（已是轻量探测，不需要改）
5. **不删除** `_docker_available()` 方法，作为 stop 路径的瘦 wrapper（也可改为 `return (await ensure_docker_daemon(auto_start=False)).available`，但会增加一次耗时，保留原实现）

**约束**：除 `_start()` 相关分支外不动其他逻辑

---

## Task T4：`run-octo-home.sh` 增强

**文件**：`octoagent/scripts/run-octo-home.sh`

**改动**：在第 23 行（source `.env.litellm` 之后）和第 25 行（`cd "${PROJECT_ROOT}"`）之间插入：

```bash
# Docker daemon best-effort 预热：提前触发 Docker Desktop 启动，
# 避免后续 LiteLLM Proxy 激活时因冷启动等待过久。
# 设置 OCTOAGENT_AUTOSTART_DOCKER=0 可禁用。
if [[ "${OCTOAGENT_AUTOSTART_DOCKER:-1}" == "1" ]]; then
  (
    cd "${PROJECT_ROOT}"
    uv run python -m octoagent.provider.dx.docker_daemon \
      --ensure --quiet \
      --timeout "${OCTOAGENT_DOCKER_DAEMON_TIMEOUT:-30}" \
      >/dev/null 2>&1 || true
  ) &
fi
```

**验证**：`bash -n octoagent/scripts/run-octo-home.sh`（语法检查）

---

## Task T5：Blueprint 同步（最小）

**文件**：扫描 `docs/blueprint/` 下所有涉及 Docker/LiteLLM Proxy 启动的文档，找到描述"运行时激活链路"的段落，补充一行：

> Docker daemon 未运行时由 `octoagent.provider.dx.docker_daemon.ensure_docker_daemon` 自动检测与启动（macOS/Linux），超时则降级至直接进程。

若无相关段落则跳过（不强制新增）。

---

## Task T6：本地真实验证（Phase 4.5 编排器执行）

**命令**：
```bash
# 1. 单元测试
uv run pytest octoagent/tests/provider/dx/test_docker_daemon.py -v

# 2. 真实探测（无 auto_start 侧效应）
uv run python -m octoagent.provider.dx.docker_daemon --ensure --quiet --timeout 5

# 3. Lint（按 spec-driver 默认命令）
uv run ruff check octoagent/packages/provider/src/octoagent/provider/dx/docker_daemon.py \
                  octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py
```

**通过标准**：
- 单元测试全部通过
- `docker_daemon --ensure` 在 daemon 就绪场景下 elapsed_s < 1s
- ruff 无 error（warning 可接受）

---

## 依赖关系

```
T1 ──► T2 ──► T6
 │      │
 └──► T3 ──► T6
 └──► T4 ──► T6
        ▲
        │
        T5（独立，可与 T3/T4 并行）
```

T1 必须最先完成；T2/T3/T4 可并行；T5 独立；T6 是最后的验证关。
