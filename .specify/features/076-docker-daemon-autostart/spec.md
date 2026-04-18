# Feature 076 — Docker daemon 自动启动

> 状态：Draft
> 作者：Connor
> 创建时间：2026-04-18
> 模式：spec-driver-story（跳过调研）
> 分支：`076-docker-daemon-autostart`

## 1. 背景与问题

### 1.1 现状

OctoAgent 通过 LiteLLM Proxy 统一模型出口。`ProxyProcessManager`（[proxy_process_manager.py](octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py)）在启动 Proxy 时采用三层策略：

1. **Docker Compose**：当 `_docker_available()` 返回 True 且找到 `docker-compose.litellm.yml` 时启动
2. **直接进程**：回退路径，使用 `uv run litellm`
3. **降级**：两者都失败时只记日志、返回 `False`

`_docker_available()` (第 272-283 行) 通过 `docker info` 判断 daemon 是否可用。问题在于：

- **daemon 未运行时静默降级**：用户可能并未安装 `litellm` CLI，直接进程路径随即失败；最终用户看到的是 `proxy_start_no_strategy` 或 httpx 的连接错误，而非"Docker 未运行"
- **Docker Desktop 冷启动耗时长**：macOS 下 Docker Desktop 首次启动需 20-60 秒。用户手动启动后再执行 `octo-start` 很反直觉
- **启动入口无预检**：`octo-start → run-octo-home.sh → uvicorn` 路径中，Docker 检测仅在后续业务流（LiteLLM 激活）中触发

### 1.2 参考实现

`_references/opensource/agent-zero/helpers/docker.py` 和 `_references/opensource/hermes-agent/tools/environments/docker.py` 都实现了"检测 daemon → 自动启动 → 等待就绪"模式。Hermes 在第 169-232 行的 `_ensure_docker_available` 可作为直接参考。

## 2. User Stories

### US-1：macOS 用户一键启动

**As** 一名 macOS 用户
**I want** 在执行 `octo-start` 时 OctoAgent 自动启动 Docker Desktop（若未运行）
**So that** 我不必在启动前手动打开 Docker

**验收**：
- 若 Docker Desktop 未运行：控制台清晰提示"检测到 Docker 未运行，正在启动 Docker Desktop…"
- OctoAgent 等待 daemon 就绪（默认上限 60 秒）后继续后续流程
- 就绪后 LiteLLM Proxy 走 Docker Compose 路径而非降级

### US-2：Linux 用户 systemd 集成

**As** 一名使用 systemd 的 Linux 用户
**I want** 启动时若 `docker` 服务未运行，系统尝试 `systemctl start docker`
**So that** 在非登录会话或无 Docker Desktop 的环境中依然能自动就绪

**验收**：
- 优先尝试 `systemctl --user start docker`，失败后回退 `sudo systemctl start docker`（若已具备免密 sudo）；两者均失败则放弃自动启动
- 不要求强制提权；失败时走用户提示路径

### US-3：Docker 不可用的清晰降级

**As** 一名未安装 Docker 或 daemon 启动失败的用户
**I want** 看到清晰的原因说明，而不是 LiteLLM 的底层错误栈
**So that** 我知道下一步该如何修复

**验收**：
- 自动启动超时或失败时，输出结构化提示：
  - 检测到的平台（macOS/Linux/其它）
  - 已尝试的命令
  - 建议操作（安装 Docker、手动启动、或忽略 Docker 继续以直接进程模式运行）
- 失败不阻断：OctoAgent 继续走现有的"直接进程/降级"路径；除非用户显式要求 `--require-docker`

### US-4：已就绪时无感知

**As** 一名 daemon 已常驻运行的用户
**I want** 检测在 <1 秒内完成
**So that** 自动检测不拖慢正常启动

**验收**：
- `docker info` 返回成功时跳过任何启动动作，总耗时 <1s
- 不产生额外 UI 噪声（只在 structlog 记录 `docker_daemon_ready`）

## 3. Functional Requirements

| ID | 内容 |
|----|------|
| FR-1 | 新增可复用模块 `octoagent.provider.dx.docker_daemon`，暴露 `async def ensure_docker_daemon(*, timeout_s: float = 60.0, auto_start: bool = True) -> DockerDaemonStatus` |
| FR-2 | `DockerDaemonStatus` 为 Pydantic 模型，字段：`available: bool`, `auto_started: bool`, `platform: Literal["darwin","linux","windows","other"]`, `attempts: list[str]`, `error: str \| None`, `elapsed_s: float` |
| FR-3 | `ensure_docker_daemon` 内部流程：① `docker info` 快速探测 → ② 平台分支启动（macOS: `open -a "Docker Desktop"`；Linux: `systemctl --user start docker` → `sudo -n systemctl start docker`；其它平台：不自动启动）→ ③ 轮询 `docker info` 直到就绪或超时（间隔 2s） |
| FR-4 | `ProxyProcessManager._start()` 在 `compose_file` 存在且首次 `_docker_available()` 为 False 时，调用 `ensure_docker_daemon(auto_start=True)`；若返回 `available=True`，走 Docker Compose 路径；否则走当前降级逻辑 |
| FR-5 | `RuntimeActivationService.start_proxy()`（若仍在使用）与 `setup_service._activate_runtime_after_config_change` 不单独实现自动启动，而是复用 FR-1 的模块 |
| FR-6 | `run-octo-home.sh` 启动前增加可选 best-effort 调用：通过环境变量 `OCTOAGENT_AUTOSTART_DOCKER=1`（默认开启，设为 `0` 禁用）触发一次 `uv run python -m octoagent.provider.dx.docker_daemon --ensure --quiet --timeout 30`，提前触发 Docker Desktop 启动，缩短后续 LiteLLM 启动等待 |
| FR-7 | 失败/超时时通过 `structlog` 记录事件（`docker_daemon_autostart_attempt` / `docker_daemon_unavailable`），并通过 `rich.Console` 打印 panel（符合既有 `dx.console_output` 约定）给非技术用户 |
| FR-8 | 全程遵守"不强制提权"：不主动调用 `sudo`，仅在 `sudo -n` 可无交互执行时尝试 |

## 4. Non-Functional Requirements

| ID | 内容 |
|----|------|
| NFR-1 | **性能**：daemon 已就绪时 `ensure_docker_daemon` 开销 <1s；daemon 冷启动场景默认超时 60s（可配置） |
| NFR-2 | **可观测（宪法原则 VIII）**：每次尝试记录一条 structlog 事件，包含 platform/command/returncode/elapsed |
| NFR-3 | **降级优雅（宪法原则 VI）**：任何失败分支均不得抛出未捕获异常；返回 `DockerDaemonStatus(available=False, ...)` |
| NFR-4 | **最小权限（宪法原则 V）**：不读取 secrets、不写入受保护路径；启动命令白名单硬编码，避免 shell 注入 |
| NFR-5 | **可测试**：核心逻辑通过依赖注入支持 mock（`run_cmd: Callable` 参数），单元测试覆盖 4 种平台 + 4 种结果分支 |
| NFR-6 | **单一事实源（宪法原则 III）**：不把"检测+启动"分散到多个模块；所有调用点复用同一函数 |

## 5. 非目标（Out of Scope）

- ❌ 不自动**安装** Docker（仅启动已安装的 daemon）
- ❌ 不管理 Docker 容器的生命周期（那是 Compose 的职责）
- ❌ Windows 的自动启动（仅预留 platform 字段，实现为 no-op）
- ❌ 不新增 `--require-docker` CLI 参数（FR-3 提到的阻断模式作为后续迭代）

## 6. 受影响的文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `octoagent/packages/provider/src/octoagent/provider/dx/docker_daemon.py` | NEW | 核心模块 + `python -m` 入口 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/proxy_process_manager.py` | MODIFY | `_start()` 中插入 `ensure_docker_daemon` 调用 |
| `octoagent/scripts/run-octo-home.sh` | MODIFY | 启动前 best-effort 调用 |
| `octoagent/tests/provider/dx/test_docker_daemon.py` | NEW | 单元测试 |
| `docs/blueprint/` 相关章节 | MODIFY | 同步运行时激活链路描述 |

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Docker Desktop 首次启动超过 60s | 超时可通过环境变量 `OCTOAGENT_DOCKER_DAEMON_TIMEOUT` 配置；超时降级不阻断 |
| `open -a` 在无图形会话下失败（ssh headless macOS）| 捕获并走降级路径；structlog 记录原因 |
| sudo 提示词卡住启动 | 严格用 `sudo -n`（non-interactive），失败不阻塞 |
| 用户希望完全不碰 Docker | `OCTOAGENT_AUTOSTART_DOCKER=0` 禁用；`ensure_docker_daemon(auto_start=False)` 可纯探测 |

## 8. 验收测试思路（交给 tasks.md 细化）

1. **单元**：mock `asyncio.create_subprocess_exec`，覆盖 macOS/Linux/Windows/other × already-up/started-ok/start-timeout/start-fail
2. **集成**：在本地真实环境触发 `ensure_docker_daemon()`，验证 daemon 已就绪场景 <1s
3. **端到端**：临时 `docker desktop stop` 后执行 `octo-start`，观察自动启动日志 + 最终 LiteLLM Proxy 健康通过

## 9. Open Questions

无。此需求边界清晰，所有决策已在 FR/NFR 中固化。
