# M0 基础底座 -- 快速上手指南

**特性**: 001-implement-m0-foundation
**日期**: 2026-02-28

---

## 前置条件

- Python 3.12+
- Node.js 20+（前端开发）
- uv（Python 包管理）

```bash
# 安装 uv（如尚未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 1. 项目初始化

```bash
# 克隆仓库并切换到特性分支
git clone https://github.com/connor-git-yaml/OctoAgent.git
cd OctoAgent
git checkout feat/001-implement-m0-foundation

# 初始化 uv workspace
uv sync
```

### uv workspace 结构

```
octoagent/
  pyproject.toml          # workspace 根配置
  packages/
    core/
      pyproject.toml      # packages/core 子包
      src/octoagent/core/
  apps/
    gateway/
      pyproject.toml      # apps/gateway 子包
      src/octoagent/gateway/
  frontend/
    package.json          # React + Vite
```

---

## 2. 启动后端

```bash
# 创建数据目录
mkdir -p data/sqlite data/artifacts

# 启动 FastAPI 开发服务器
uv run uvicorn octoagent.gateway.main:app --reload --host 0.0.0.0 --port 8000
```

服务启动后可访问：
- API 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`
- Readiness：`http://localhost:8000/ready`

---

## 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器（自动代理 API 到 localhost:8000）
npm run dev
```

前端访问：`http://localhost:5173`

### Vite 代理配置

```typescript
// frontend/vite.config.ts
export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/ready': 'http://localhost:8000',
    },
  },
});
```

---

## 4. 端到端验证

### 4.1 发送消息

```bash
curl -X POST http://localhost:8000/api/message \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello OctoAgent",
    "idempotency_key": "test-msg-001"
  }'
```

预期响应：

```json
{
  "task_id": "01JXYZ...",
  "status": "CREATED",
  "created": true
}
```

### 4.2 查看任务列表

```bash
curl http://localhost:8000/api/tasks
```

### 4.3 查看任务详情

```bash
curl http://localhost:8000/api/tasks/{task_id}
```

### 4.4 监听 SSE 事件流

```bash
curl -N http://localhost:8000/api/stream/task/{task_id}
```

预期看到事件流：
```
id: 01JXYZ001...
event: TASK_CREATED
data: {"event_id":"01JXYZ001...","type":"TASK_CREATED",...}

id: 01JXYZ002...
event: USER_MESSAGE
data: {"event_id":"01JXYZ002...","type":"USER_MESSAGE",...}

id: 01JXYZ003...
event: STATE_TRANSITION
data: {"event_id":"01JXYZ003...","type":"STATE_TRANSITION","payload":{"from_status":"CREATED","to_status":"RUNNING"},...}

id: 01JXYZ004...
event: MODEL_CALL_STARTED
data: {"event_id":"01JXYZ004...","type":"MODEL_CALL_STARTED",...}

id: 01JXYZ005...
event: MODEL_CALL_COMPLETED
data: {"event_id":"01JXYZ005...","type":"MODEL_CALL_COMPLETED",...}

id: 01JXYZ006...
event: STATE_TRANSITION
data: {"event_id":"01JXYZ006...","type":"STATE_TRANSITION","payload":{"from_status":"RUNNING","to_status":"SUCCEEDED"},"final":true}
```

### 4.5 取消任务

```bash
curl -X POST http://localhost:8000/api/tasks/{task_id}/cancel
```

### 4.6 验证持久性

```bash
# 强制终止后端进程
kill -9 $(pgrep -f uvicorn)

# 重启后端
uv run uvicorn octoagent.gateway.main:app --reload --host 0.0.0.0 --port 8000

# 验证任务仍然存在
curl http://localhost:8000/api/tasks
```

---

## 5. 运行测试

```bash
# 运行全部测试
uv run pytest

# 仅运行单元测试
uv run pytest tests/unit/

# 仅运行集成测试
uv run pytest tests/integration/

# 带覆盖率
uv run pytest --cov=octoagent --cov-report=html
```

---

## 6. Projection Rebuild

```bash
# 从事件重建任务状态（破坏性操作，先停止服务）
uv run python -m octoagent.core rebuild-projections
```

---

## 7. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OCTO_DB_PATH` | `data/sqlite/octoagent.db` | SQLite 数据库路径 |
| `OCTO_ARTIFACTS_DIR` | `data/artifacts` | Artifact 文件目录 |
| `OCTO_HOST` | `0.0.0.0` | 服务监听地址 |
| `OCTO_PORT` | `8000` | 服务监听端口 |
| `OCTO_LOG_LEVEL` | `INFO` | 日志级别 |
| `OCTO_LOG_FORMAT` | `dev` | 日志格式：dev（pretty）或 json |
| `LOGFIRE_SEND_TO_LOGFIRE` | `false` | 是否发送到 Logfire 云端 |
| `OCTO_LLM_MODE` | `echo` | LLM 模式：echo（回声）或 mock（固定响应） |
| `OCTO_EVENT_PAYLOAD_MAX_KB` | `8` | Event payload 最大 KB（超过存 Artifact） |
| `OCTO_ARTIFACT_INLINE_MAX_KB` | `4` | Artifact inline 最大 KB（超过写文件） |

---

## 8. 项目结构速览

```
octoagent/
  pyproject.toml                    # uv workspace 根配置
  packages/
    core/
      src/octoagent/core/
        __init__.py
        models/                     # Domain Models (Pydantic)
          enums.py                  # TaskStatus, EventType, ...
          task.py                   # Task
          event.py                  # Event
          artifact.py               # Artifact
          message.py                # NormalizedMessage
          payloads.py               # Event Payload 子类型
        store/                      # Store 层
          protocols.py              # Store 接口 (Protocol)
          sqlite_store.py           # SQLite 实现
          artifact_fs.py            # Artifact 文件系统实现
        projection.py               # Projection 应用与重建
        config.py                   # 配置常量
  apps/
    gateway/
      src/octoagent/gateway/
        __init__.py
        main.py                     # FastAPI app 入口
        routes/
          message.py                # POST /api/message
          tasks.py                  # GET /api/tasks, GET /api/tasks/{id}
          cancel.py                 # POST /api/tasks/{id}/cancel
          stream.py                 # GET /api/stream/task/{id}
          health.py                 # GET /health, GET /ready
        services/
          task_service.py           # 任务处理逻辑
          llm_service.py            # Echo/Mock LLM 客户端
          sse_hub.py                # SSE 事件广播
        middleware/
          logging_mw.py             # structlog 中间件 (request_id)
          trace_mw.py               # trace_id 中间件
  frontend/
    src/
      App.tsx                       # 路由
      pages/
        TaskList.tsx                # 任务列表页
        TaskDetail.tsx              # 任务详情页（事件时间线）
      hooks/
        useSSE.ts                   # SSE Hook
      api/
        client.ts                   # API 调用
  data/                             # 运行时数据（.gitignore）
    sqlite/
    artifacts/
  tests/
    unit/
    integration/
    conftest.py
```
