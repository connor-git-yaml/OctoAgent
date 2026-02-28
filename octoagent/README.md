# OctoAgent M0 -- 基础底座

OctoAgent 个人智能操作系统的 M0 基础底座层：基于 Event Sourcing 的持久化任务账本，支持 REST API、SSE 实时事件推送和最小 Web UI。

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
