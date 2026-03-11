# OctoAgent

OctoAgent 是一个面向个人使用的 AI OS：提供持久化任务账本、Web / Telegram 双入口、统一配置与健康检查、聊天导入/导出、备份恢复，以及带治理面的 Agent 运行时。

当前仓库同时提供两种使用方式：

- 普通用户路径：一键安装到 `~/.octoagent`，先用 `echo` 模式跑通 Web，再切真实模型
- 开发者路径：在仓库里直接运行、调试和测试

## 架构

```
Web UI (React 19)  ──HTTP/SSE──>  FastAPI Gateway  ──>  SQLite WAL
                                      |                    |
                                  Routes / Services     3 tables:
                                  - message              tasks
                                  - tasks                events
                                  - cancel               artifacts
                                  - stream (SSE)
                                  - health
```

**核心组件**:

- **packages/core** -- Domain Models (Pydantic) + SQLite Store + Projection Rebuild
- **apps/gateway** -- FastAPI 合并进程 (Routes + Services + Middleware)
- **frontend** -- React 19 + Vite 6 Web UI (TaskList + TaskDetail + SSE)

## 快速启动

### 前置条件

- Python 3.12+
- Node.js 20+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 脚本分层约定

仓库里现在保留两层脚本目录，职责明确分开：

- `repo-scripts/`
  - 仓库级脚本
  - 负责远程一键安装、agent-config 同步、仓库级验证
- `octoagent/scripts/`
  - 产品级脚本
  - 负责个人实例初始化、启动 Web runtime、执行实例 doctor

如果你是普通用户，优先使用 `repo-scripts/install-octo-user.sh` 作为远程入口；如果你已经在仓库里开发，再使用 `octoagent/scripts/` 下的脚本。

### 个人体验模式（推荐）

如果你只是想“像普通用户一样用起来”，不要先手动 clone 仓库，直接执行一键安装：

```bash
curl -fsSL https://raw.githubusercontent.com/connor-git-yaml/OctoAgent/master/repo-scripts/install-octo-user.sh | bash
```

这条命令会：

- 把源码拉到 `~/.octoagent/app`
- 把个人实例初始化到 `~/.octoagent/`
- 默认生成 `echo` 模式配置，方便先验证 Web 流程
- 生成 3 个可直接使用的入口：
  - `~/.octoagent/bin/octo-start`
  - `~/.octoagent/bin/octo-doctor`
  - `~/.octoagent/bin/octo`

安装完成后，先启动实例：

```bash
~/.octoagent/bin/octo-start
```

另开一个终端做健康检查：

```bash
~/.octoagent/bin/octo-doctor
curl 'http://127.0.0.1:8000/ready?profile=core'
```

看到 `/ready` 返回 `"status": "ready"` 后，就可以打开：

- Web UI: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`

如需把 CLI 加进 PATH：

```bash
export PATH="$HOME/.octoagent/bin:$PATH"
```

安装后的实例目录结构大致如下：

- `~/.octoagent/octoagent.yaml`
- `~/.octoagent/litellm-config.yaml`
- `~/.octoagent/data/sqlite`
- `~/.octoagent/data/artifacts`
- `~/.octoagent/app`

### 切换到真实模型

个人体验模式默认是 `echo`，这样可以先确认系统本身能跑。要切真实模型，现在推荐直接用一条交互式命令：

```bash
~/.octoagent/bin/octo setup
```

这条命令会一次完成：

- 选择 provider 预设（推荐先选 `openrouter`）
- 输入 API Key 或走 `openai-codex` 浏览器 OAuth
- 写入 `octoagent.yaml` 与 `~/.octoagent/.env.litellm`
- 启动 LiteLLM Proxy
- 在托管实例中自动切到真实模型
- 最后跑一次 `octo doctor --live`

如果你要走 ChatGPT Pro OAuth / Codex，也可以直接选 `openai-codex`，默认模型 preset 已经是 `gpt-5.4`。

### 可选：接入 Telegram

如果只想先用 Web，可以跳过。要接 Telegram，最简单的是 `polling`：

```bash
~/.octoagent/bin/octo config init --force --enable-telegram --telegram-mode polling
export TELEGRAM_BOT_TOKEN=你的_bot_token
~/.octoagent/bin/octo onboard --channel telegram
```

普通用户第一次接 Telegram 时，建议先用 `polling`；只有当你已经有公网 HTTPS 地址时，再切 `webhook`。

### 开发者模式

如果你已经在仓库内开发，直接执行：

```bash
cd octoagent
./scripts/install-octo-home.sh
```

这条命令会完成依赖安装、前端构建，并初始化 `~/.octoagent/`：

- `octoagent.yaml`
- `litellm-config.yaml`
- `data/sqlite`
- `data/artifacts`

默认会初始化为 `echo` 模式，方便先把 Web 流程跑通。启动实例：

```bash
cd octoagent
./scripts/run-octo-home.sh
```

健康检查：

```bash
cd octoagent
./scripts/doctor-octo-home.sh
```

如需切换到真实模型，直接执行 `uv run octo setup` 即可；如果你需要更细粒度地调试 provider/runtime，再退回 `uv run octo config init`。

### 后端

```bash
cd octoagent

# 安装依赖
uv sync

# 启动开发服务器
uv run uvicorn octoagent.gateway.main:app --reload --port 8000
```

### 前端

```bash
cd octoagent/frontend

# 安装依赖
npm install

# 开发模式（自动代理到后端 :8000）
npm run dev

# 生产构建
npm run build
```

生产模式下 FastAPI 自动托管 `frontend/dist/`，访问 `http://localhost:8000` 即可使用 Web UI。

### 测试

```bash
cd octoagent

# 运行全部测试（105 个）
uv run pytest -v

# 仅运行单元测试
uv run pytest packages/core/tests/ -v

# 仅运行 Gateway 测试
uv run pytest apps/gateway/tests/ -v

# 仅运行集成测试
uv run pytest tests/integration/ -v

# 代码风格检查
uv run ruff check packages/ apps/ tests/
```

### Projection 重建

```bash
uv run python -m octoagent.core rebuild-projections
```

## API 文档

启动后端后访问 `http://localhost:8000/docs` 查看 Swagger UI。

### 端点一览

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | /api/message | 发送消息，创建任务 |
| GET | /api/tasks | 任务列表（支持 ?status= 筛选） |
| GET | /api/tasks/{id} | 任务详情（含 events + artifacts） |
| POST | /api/tasks/{id}/cancel | 取消任务 |
| GET | /api/stream/task/{id} | SSE 事件流 |
| GET | /health | Liveness 检查 |
| GET | /ready | Readiness 检查 |

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.12+ / TypeScript 5.x |
| Web 框架 | FastAPI + Uvicorn |
| 数据模型 | Pydantic v2 |
| 数据库 | SQLite WAL (aiosqlite) |
| SSE | sse-starlette |
| ID 生成 | ULID (python-ulid) |
| 日志 | structlog + Logfire (可选) |
| 前端 | React 19 + Vite 6 + React Router 7 |
| 测试 | pytest + pytest-asyncio + httpx |
| Lint | ruff |
| 包管理 | uv (workspace) |

## 项目结构

```
octoagent/
  pyproject.toml              # uv workspace 根
  packages/
    core/                     # Domain Models + Store
      src/octoagent/core/
        models/               # Pydantic 数据模型
        store/                # SQLite Store 实现
        projection.py         # Projection 重建
        config.py             # 配置常量
  apps/
    gateway/                  # FastAPI 合并进程
      src/octoagent/gateway/
        routes/               # API 路由
        services/             # 业务服务
        middleware/            # 日志/追踪中间件
  frontend/                   # React + Vite Web UI
    src/
      pages/                  # TaskList + TaskDetail
      hooks/                  # useSSE
      api/                    # API client
  tests/
    integration/              # 端到端集成测试
```

## License

MIT
