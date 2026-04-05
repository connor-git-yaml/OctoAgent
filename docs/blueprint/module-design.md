# §9 模块设计（Module Breakdown）

> 本文件是 [blueprint.md](../blueprint.md) §9 的完整内容。

---

> 本节给出实现层面的模块拆分、职责、接口与边界，确保进入实现阶段时"有人照着写也不会打架"。

### 9.1 Repo 结构建议（Monorepo）

```text
octoagent/
  pyproject.toml
  uv.lock
  apps/
    gateway/                 # OctoGateway
    kernel/                  # OctoKernel
    workers/
      ops/
      research/
      dev/
  packages/
    core/                    # domain models + event store + common utils
    protocol/                # A2A-lite envelope + NormalizedMessage
    tooling/                 # 工具 schema 反射 + ToolBroker + permission + path_policy
    memory/                  # SoR/Fragments/Vault + arbitration
    provider/                # litellm client wrappers + cost model
    policy/                  # 审批管理 + Override 持久化
    skills/                  # SkillRunner + Manifest + Pipeline + Deferred Tools
  frontend/                    # React + Vite Web UI（M0 起步）
  plugins/
    channels/
      telegram/
      web/
      wechat_import/
    tools/
      filesystem/
      docker/
      ssh/
      web/
  data/
    sqlite/                  # local db
    artifacts/               # artifact files
    vault/                   # encrypted or restricted
  docs/
    blueprint.md
```

### 9.2 packages/core

职责：
- Domain models（Task/Event/Artifact/Checkpoint/Approval）
- SQLite store（event-sourcing + projections）
- 迁移与 schema version
- 幂等键处理

关键接口：
- `TaskStore.create_task(...)`
- `EventStore.append_event(...)`
- `Projection.apply_event(...)`
- `ArtifactStore.put/get/list(...)`

### 9.3 apps/gateway

职责：
- ChannelAdapter lifecycle（start/stop）
- 入站消息 normalization（NormalizedMessage）
- 出站消息发送（Telegram/Web）
- SSE/WS stream 转发（从 Kernel 订阅）

对外 API（Gateway 暴露给渠道/Web UI）：

- `POST /api/message`（接收用户消息）
- `GET /api/stream/task/{task_id}`（SSE 事件流）
- `POST /api/approve/{approval_id}`（审批决策）

内部调用（Gateway → Kernel）：

- `POST /kernel/ingest_message`
- `GET /kernel/stream/task/{task_id}`
- `POST /kernel/approvals/{approval_id}/decision`

### 9.4 apps/kernel

职责：

- Orchestrator Loop（目标理解、路由、监督；永远 Free Loop）
- Policy Engine（allow/ask/deny + approvals）
- Memory Core（检索、写入提案、仲裁、commit）

关键内部组件：

- `Router`：决定 worker 派发
- `Supervisor`：watchdog + stop condition
- `ApprovalService`：审批状态机
- `MemoryService`：read/write arbitration
- `SchedulerService`：APScheduler wrapper，定时任务触发 → 创建 Task（UC1 例行任务）

### 9.5 workers/*

每个 worker 是自治智能体（Free Loop），具备：

- 独立运行（进程/容器均可）
- 拥有自己的工作目录（project workspace）
- 拥有自己的 `WorkerSession`、`WorkerMemory`、Recall/compaction 状态与 effective context frame
- 拥有自己的 persona / instruction overlays / tool set / capability set / permission set / auth context
- Skill Runner（Pydantic AI）+ Skill Pipeline（pydantic-graph）
- Tool Broker（schema、动态注入、执行编排）
- 暴露内部 RPC（HTTP/gRPC 均可；MVP 用 HTTP）

Worker 类型与专长（Orchestrator Router 据此派发）：

- `ops`：运维与系统操作——脚本执行、设备管理、文件同步、定时巡检
- `research`：调研与分析——信息检索、对比分析、报告生成、知识整理
- `dev`：开发与工程——代码编写、项目构建、测试运行、技术方案实现

当前产品边界：
- 默认只有 Butler 直接面向用户；Worker 主要通过 `worker_a2a` session 与 Butler 协作
- 未来允许用户直连 Worker，但必须创建独立 `worker_direct` session，并维持独立 memory / recall / policy / audit 链

worker 的最小端点：
- `POST /a2a/run`（TASK）
- `POST /a2a/update`（UPDATE）
- `POST /a2a/cancel`（CANCEL）
- `GET /health`

### 9.6 packages/protocol

职责：

- A2AMessage envelope 定义（见 §10.2）
- NormalizedMessage 定义（见 §8.1.1）
- A2AStateMapper：内部状态 ↔ A2A TaskState 双向映射（见 §10.2.1）
- A2A Artifact 映射层（见 §10.2.2）

关键接口：

- `A2AMessage.wrap(payload) → envelope`
- `A2AStateMapper.to_a2a(internal_state) → a2a_state`
- `A2AStateMapper.from_a2a(a2a_state) → internal_state`
- `ArtifactMapper.to_a2a(artifact) → a2a_artifact`

### 9.7 packages/plugins

职责：
- Plugin manifest 解析
- Plugin Loader（enable/disable）
- Capability Graph（依赖解析、健康门禁）
- 插件隔离策略（超时、崩溃熔断）

Manifest 示例：

```yaml
id: "channel.telegram"
version: "0.1.0"
type: "channel"
requires:
  - "core>=0.1"
  - "provider.litellm"
capabilities:
  - "channel.ingest"
  - "channel.send"
healthcheck:
  kind: "http"
  url: "http://localhost:9001/health"
config_schema:
  ...
```

`plugins/` 实现目录约定（实际插件放在 repo 顶层 `plugins/` 下）：

- 每个插件一个子目录，包含 `manifest.yaml` + Python 模块
- 目录结构示例：`plugins/channels/telegram/`（manifest.yaml + adapter.py）
- 插件实现依赖 `packages/plugins` 提供的 Loader / Manifest 解析能力
- Channel 插件须实现 `ChannelAdapter` 协议；Tool 插件须实现 `ToolMeta` 声明

### 9.8 packages/tooling

职责：工具扫描与 schema 反射、ToolIndex 构建(LanceDB)、ToolBroker(执行/超时/结果压缩)、check_permission(权限决策)、PathAccessPolicy(路径访问控制)、Core/Deferred 分层(ToolTier + tool_search)、ToolResult 结构化回灌。

### 9.9 packages/memory

职责：

- Fragments/SoR/Vault 数据模型
- MemoryBackend 抽象（当前 SQLite metadata backend，后续可插 LanceDB 等向量引擎）
- 写入仲裁（WriteProposal → validate → commit）
- 基础检索契约（`search_memory` / `get_memory`）
- compaction 前 flush 钩子（`before_compaction_flush`）
- Chat Import Core（dedupe、window、summarize）

### 9.10 packages/provider

职责：

- LiteLLM proxy client wrapper
- alias 与策略（router/extractor/planner/executor/summarizer）
- fallback 与错误分类
- cost/tokens 解析

### 9.11 packages/observability

职责：

- Logfire init（自动 instrument Pydantic AI / FastAPI）
- structlog 配置（dev pretty / prod JSON）
- 统一 trace_id 贯穿 event payload
- Event Store metrics 查询辅助（cost/tokens 聚合）

### 9.12 frontend/（React + Vite Web UI）

职责：

- M0：TaskList + EventStream 两个核心组件
- SSE 消费：原生 EventSource 对接 Gateway `/api/stream/task/{id}`
- 后续扩展：Approvals 审批面板、Config 配置管理、Artifacts 查看、Memory 浏览

技术栈：

- React + Vite（从 M0 起步，避免迁移债务）
- 独立于 Python 后端，通过 Gateway API 通信
- 开发时 Vite dev server 代理到 Gateway；生产时静态文件由 Gateway 托管

---
