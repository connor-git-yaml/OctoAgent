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
- Notification + Routine（用户感知 ROI，M5 阶段 3 引入）

关键内部组件：

- `Router`：决定 worker 派发
- `Supervisor`：watchdog + stop condition
- `ApprovalManager` + `ApprovalGate`（F101 改造）：审批状态机 + SSE production 接入；`WAITING_APPROVAL` 单 owner + CAS + 双注册桥接
- `MemoryService`：read/write arbitration；F094 引入 `AGENT_PRIVATE` namespace（Worker 路径生效）；F096 引入 `list_recall_frames` audit endpoint + `MEMORY_RECALL_COMPLETED` 同步路径 emit
- `SchedulerService`：APScheduler wrapper，定时任务触发 → 创建 Task（UC1 例行任务）
- `NotificationService`（F101 新增）：四级优先级（CRITICAL/HIGH/MEDIUM/LOW）+ quiet hours discard + dismiss 跨通道统一 + USER.md SoT；`NOTIFICATION_DISPATCHED` EventType 记录每条 notification（含 quiet hours 内被过滤的）；Telegram callback + Web `/api/notifications` endpoint
- `DailyRoutineService`（F102 新增）：cron 触发 + 9 步执行 + LLM/fallback 双路径（LLM token budget 截断 max_input ≤ 2000 字符 + max_output ≤ 512 token）；4 EventType（ROUTINE_TRIGGERED/COMPLETED/FAILED/SKIPPED）挂在 `_daily_routine_audit` task；USER.md 3 机器可读字段（daily_summary_time / routine_active / summary_channels）
- `OrchestratorService` D7 拆分（F098）：`A2ADispatchMixin` 提取到 `dispatch_service.py`（15 helpers / 972 行），orchestrator.py 3623→2733 行（-890）

### 9.5 workers/*

每个 worker 是自治智能体（Free Loop），具备：

- 独立运行（进程/容器均可）
- 拥有自己的工作目录（project workspace）
- 拥有自己的 `WorkerSession`、`WorkerMemory`、Recall/compaction 状态与 effective context frame
- 拥有自己的 persona / instruction overlays / tool set / capability set / permission set / auth context
- Skill Runner（Pydantic AI）+ Skill Pipeline（pydantic-graph）
- Tool Broker（schema、动态注入、执行编排）
- 暴露内部 RPC（HTTP/gRPC 均可；MVP 用 HTTP）

#### Worker 完整对等性（M5 阶段 1 引入，详见 [agent-collaboration-philosophy.md](agent-collaboration-philosophy.md) H2）

F093-F096 把 Worker 的上下文栈与主 Agent 真正对齐：

- **Session 对等**（F093）：Worker turn 写入 `AgentSession` + `rolling_summary` + `memory_cursor` 字段持久化；新增 `AGENT_SESSION_TURN_PERSISTED` event；agent_context.py 4112→4008 行（D6 拆分，抽出 turn-writer mixin）
- **Memory 对等**（F094）：`AGENT_PRIVATE` namespace 仅 Worker 路径生效，main direct 保留 `PROJECT_SHARED`（完整对等留 F107）；废弃 `WORKER_PRIVATE` 路径（合并进 `AGENT_PRIVATE`）；`RecallFrame.agent_runtime_id` 字段（非 `agent_id`）
- **Behavior 对等**（F095）：`_PROFILE_ALLOWLIST[WORKER]` 5 → **8 文件**（`AGENTS / TOOLS / IDENTITY / PROJECT / KNOWLEDGE / USER / SOUL / HEARTBEAT`，**去 BOOTSTRAP 加 USER**）；envelope 双过滤收敛 + `IDENTITY.worker.md` 真生效；`SOUL.worker.md` / `HEARTBEAT.worker.md` worker variant 模板
- **Recall Audit 对等**（F096）：`list_recall_frames` audit endpoint + `MEMORY_RECALL_COMPLETED` 同步路径 emit + `BEHAVIOR_PACK_LOADED` EventStore 接入 + `BEHAVIOR_PACK_USED` 新增；AC-7b 四层 audit chain（`AgentProfile.profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id`）

Worker 类型与专长（Orchestrator Router 据此派发）：

- `ops`：运维与系统操作——脚本执行、设备管理、文件同步、定时巡检
- `research`：调研与分析——信息检索、对比分析、报告生成、知识整理
- `dev`：开发与工程——代码编写、项目构建、测试运行、技术方案实现

当前产品边界：
- 默认只有主 Agent 直接面向用户；Worker 主要通过 `worker_a2a` session 与主 Agent 协作
- **F098 关闭 D14 Worker↔Worker 硬禁止**：删除 `_enforce_child_target_kind_policy`；Worker 现在可委托 Worker（A2A 真 P2P 模式，H3-B）
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
- 委托模型抽象（M5 阶段 2 引入，详见 [agent-collaboration-philosophy.md](agent-collaboration-philosophy.md) H3）：
  - `BaseDelegation`（F098 提取）：F097 `SubagentDelegation` + F098 A2A `WorkerDelegation` 共享字段抽象
  - `SubagentDelegation`（F097，H3-A 临时 Subagent）：spawn-and-die，共享 caller project；ephemeral `AgentProfile (kind=subagent)`；`SUBAGENT_INTERNAL` session 路径
  - A2A `WorkerDelegation`（F098，H3-B 真 P2P）：receiver 在自己 project 工作；source+target 双向独立加载；可中途 ask back
- `source_runtime_kind` 枚举（F099 引入，`source_kinds.py`）：5 个常量 `MAIN / WORKER / SUBAGENT / AUTOMATION / USER_CHANNEL` + `KNOWN_SOURCE_RUNTIME_KINDS` frozenset；A2A source 派生仅信任显式 `envelope.metadata.source_runtime_kind` 信号（缺信号默认 main）
- `CONTROL_METADATA_UPDATED` event（F098 引入）：only carries control_metadata，不污染 `latest_user_text`；解决 USER_MESSAGE event 复用为 control_metadata 承载体的污染问题

关键接口：

- `A2AMessage.wrap(payload) → envelope`
- `A2AStateMapper.to_a2a(internal_state) → a2a_state`
- `A2AStateMapper.from_a2a(a2a_state) → internal_state`
- `ArtifactMapper.to_a2a(artifact) → a2a_artifact`
- `worker.ask_back(...)` / `worker.request_input(...)` / `worker.escalate_permission(...)`（F099 三工具，`ask_back_tools.py`）：统一 emit `CONTROL_METADATA_UPDATED` 审计事件；`escalate_permission` 走 ApprovalGate（F101 production 接入）

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

职责（F081 LiteLLM 完全退役后，2026-05+ 实际状态）：

- **ProviderRouter**（F080/F081 引入，替代 LiteLLM Proxy 子进程）：
  - alias 解析 + 凭证管理 + 直连 provider HTTP
  - 三种 transport：OpenAI Chat / OpenAI Responses / Anthropic Messages
  - fallback 与错误分类
  - cost/tokens 解析
  - migrate-080 双对象迁移（保留向后兼容期）
- alias 与策略（router/extractor/planner/executor/summarizer 仍在用）
- 详见 [codebase-architecture/provider-direct-routing.md](../codebase-architecture/provider-direct-routing.md)

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
- M5 引入：Notification 红点 badge（F101）+ Memory Candidates promote/discard UI（F084）

技术栈：

- React + Vite（从 M0 起步，避免迁移债务）
- 独立于 Python 后端，通过 Gateway API 通信
- 开发时 Vite dev server 代理到 Gateway；生产时静态文件由 Gateway 托管

### 9.13 Harness Layer（F084 引入，apps/gateway/harness/）

> Hermes Agent 模式落地。详见 [codebase-architecture/harness-and-context.md](../codebase-architecture/harness-and-context.md)。

职责：

- **ToolRegistry**：中央工具注册表（数据驱动 entrypoints）；18+ 写工具 `WriteResult` 通用回显契约（注册期 fail-fast，保留 task_id / memory_id / run_id 等关联键不压扁）
- **ToolsetResolver**：根据 agent kind / context 解析工具集
- **ThreatScanner**：17+ pattern + invisible Unicode 检测（M5 引入硬门禁）
- **SnapshotStore**：冻结快照 + Live State 二分（保护 prefix cache）；USER.md / auth-profiles.json / mcp-servers/ sha256 跨 e2e_live 跑前后完全一致（SC-7 不变量）
- **ApprovalGate**：session allowlist + SSE；F101 production 接入（escalate_permission 真闭环）
- **DelegationManager**：`max_depth=2` / `max_concurrent=3`（F092 production 构造从 5+ 处收敛到 1 处）
- **OctoHarness**（F087）：抽象 4 个 DI 钩子（`credential_store` / `secret_store` / `transport_factory` / `clock`），暴露给 e2e_live test suite

退役（F084 Phase 4）：BootstrapSession / BootstrapOrchestrator / UserMdRenderer / bootstrap_integrity / bootstrap_commands CLI（净删 ~2400 行 dead code）。重装路径：清 `~/.octoagent/data + behavior` + `octo update` 重启（bootstrap 完成由 USER.md 实质填充判定，不依赖任何旧表/状态机）。

### 9.14 Context Layer（F084 引入）

职责：

- **USER.md 是 SoT**：用户长期偏好的唯一事实源；OwnerProfile 退化为派生只读视图
- **三工具**：`user_profile.update / user_profile.read / user_profile.observe`
- **Memory Candidates API**：`promote` / `discard` / `bulk_discard` with atomic claim + skipped_ids；Web UI 红点 badge
- **USER.md 机器可读字段**（M5 内逐步引入）：
  - `active_hours: "HH:MM-HH:MM"`（F101，影响 NotificationService quiet hours discard）
  - `approval_timeout_seconds: int`（F101，影响 ApprovalGate timeout）
  - `daily_summary_time: "HH:MM"` 默认 `"08:30"`（F102）
  - `routine_active: "true"/"false"` 默认 `true`（F102）
  - `summary_channels: "telegram,web"` 默认 `"telegram,web"`（F102，含 `"web"→"web_sse"` 映射）
- `RuntimeControlContext` 显式字段（F090 引入 + F100 收尾）：替代 metadata flag 控制流；含 `force_full_recall: bool = False`（H1 override）+ `delegation_mode` + `turn_executor_kind` + `recall_planner_mode` 等
- `RecallPlannerMode="auto"`（F100 启用）：按 delegation_mode 自动决议（main_inline / worker_inline → skip / main_delegate / subagent → full）

---
