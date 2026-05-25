# 三层消息模型（Work × DispatchEnvelope × A2AMessage）

> 关闭 D13 架构债：三层消息模型文档缺失。
> 引入 Feature：F103 Blueprint v0.1 Incremental 修订（M5 阶段 3）
> 上游设计：[blueprint/api-and-protocol.md](../blueprint/api-and-protocol.md) §10.2 A2A-Lite Envelope
> 哲学依据：[blueprint/agent-collaboration-philosophy.md](../blueprint/agent-collaboration-philosophy.md) §4 H3 两种委托模式

---

## §1 三层关系总览

OctoAgent 内部任何一次"派人做事"都会同时存在三个不同抽象层的对象：

```text
┌──────────────────────────────────────────────────────────────┐
│                  Work（执行单元 / Persistent）                │
│   • work_id / task_id / status / target_kind                  │
│   • project_id / agent_profile_id / runtime_id                │
│   • selected_tools / context_frame_id / metadata              │
│   • 生命周期：CREATED → ASSIGNED → RUNNING → 终态             │
│                                                                │
│           ▼ 触发派发                                           │
│                                                                │
│   ┌──────────────────────────────────────────────────────┐   │
│   │   DispatchEnvelope（运行时包装 / Ephemeral）          │   │
│   │   • dispatch_id / task_id / trace_id                  │   │
│   │   • contract_version / route_reason                   │   │
│   │   • worker_capability / hop_count / max_hops          │   │
│   │   • runtime_context（RuntimeControlContext）          │   │
│   │   • user_text / model_alias / resume_state_snapshot   │   │
│   │                                                        │   │
│   │              ▼ 真 A2A 通信场景下序列化                │   │
│   │                                                        │   │
│   │   ┌──────────────────────────────────────────────┐   │   │
│   │   │   A2AMessage（对话消息 / Wire-level）         │   │   │
│   │   │   • message_id / task_id / context_id         │   │   │
│   │   │   • from_agent / to_agent / type              │   │   │
│   │   │     (TASK/UPDATE/CANCEL/RESULT/ERROR/         │   │   │
│   │   │      HEARTBEAT)                               │   │   │
│   │   │   • payload / trace / metadata                │   │   │
│   │   │   • source_runtime_kind（F099 5 值）          │   │   │
│   │   │                                                │   │   │
│   │   │             ▼ 持久化                          │   │   │
│   │   │                                                │   │   │
│   │   │   ┌──────────────────────────────────────┐   │   │   │
│   │   │   │   A2AMessageRecord                    │   │   │   │
│   │   │   │   (持久化审计记录，含 message_seq +   │   │   │   │
│   │   │   │    direction + raw_message)           │   │   │   │
│   │   │   │   挂在 A2AConversation                │   │   │   │
│   │   │   └──────────────────────────────────────┘   │   │   │
│   │   └──────────────────────────────────────────────┘   │   │
│   └──────────────────────────────────────────────────────┘   │
│                                                                │
│           ▼ 产物回写                                           │
│                                                                │
│         Artifact（结构化产物）                                 │
└──────────────────────────────────────────────────────────────┘
```

**核心论断**：A2A 是 Work 的**一种通信形式**，Work 不必由 A2A 触发（inline 执行场景下 DispatchEnvelope 不需要序列化为 A2AMessage）。

---

## §2 Work 层（执行单元 / Persistent）

### §2.1 定位

- **Persistent**：Work 落盘到 `works` 表，进程重启后可恢复（Constitution #1 Durability First）
- **委派单元**：一个 Work 表示"我要派给某人做的一件事"
- **状态机驱动**：13 个状态值，VALID_WORK_TRANSITIONS 表完整建模
- **不持有运行时上下文**：runtime_context 在 DispatchEnvelope 层，Work 本身只持有"待委派的事实"

### §2.2 字段定义

**源码位置**：`packages/core/src/octoagent/core/models/delegation.py:258` `class Work(BaseModel)`

| 字段 | 类型 | 用途 |
|------|------|------|
| `work_id` | str | 唯一 ID（min_length=1） |
| `task_id` | str | 关联 Task |
| `parent_work_id` | str \| None | 父 Work（H3-B 真 P2P 委托链）|
| `title` | str | 简短描述 |
| `kind` | `WorkKind` | DELEGATION / SUBAGENT_SPAWN / WORK_SPLIT / 等 |
| `status` | `WorkStatus` | 13 状态（CREATED → ASSIGNED → RUNNING → 终态）|
| `target_kind` | `DelegationTargetKind` | WORKER / SUBAGENT / GRAPH_PIPELINE |
| `owner_id` | str | 主负责人 agent_runtime_id |
| `requested_capability` | str | 申请能力标签 |
| `selected_worker_type` | str | "general" / "ops" / "research" / "dev" |
| `route_reason` | str | 路由决策理由 |
| `project_id` | str | 所属 Project |
| `session_owner_profile_id` | str | session 主责 profile（F071 session owner / turn executor 拆分）|
| `inherited_context_owner_profile_id` | str | 继承上下文 profile（F071）|
| `delegation_target_profile_id` | str | 委托目标 profile（F071）|
| `turn_executor_kind` | `TurnExecutorKind` | 本 turn 实际执行 kind |
| `agent_profile_id` | str | 当前 effective AgentProfile |
| `requested_worker_profile_id` / `requested_worker_profile_version` | str / int | 申请 WorkerProfile + revision |
| `effective_worker_snapshot_id` | str | effective config snapshot |
| `context_frame_id` | str | 关联 ContextFrame |
| `tool_selection_id` / `selected_tools` | str / list[str] | 工具选择 |
| `pipeline_run_id` | str | 关联 Pipeline 执行（GRAPH_PIPELINE）|
| `delegation_id` | str | 关联 DelegationEnvelope |
| `runtime_id` | str | 当前 runtime 实例 |
| `retry_count` / `escalation_count` | int | 重试 / 升级计数 |
| `metadata` | dict | 扩展字段 |
| `created_at` / `updated_at` / `completed_at` | datetime | 生命周期时间戳 |

### §2.3 状态机

```
CREATED → ASSIGNED → RUNNING → (SUCCEEDED | FAILED | CANCELLED | REJECTED | WAITING_INPUT | WAITING_APPROVAL)
                                  ↓ resume                    ↑
                                  └────────────────────────────┘
                                ESCALATED / MERGED / DELETED   （F091 显式 raise ValueError）
```

**F091 关键决策**：`work_status_to_task_status` 对 `MERGED / ESCALATED / DELETED` 显式 raise ValueError（语义不能压扁，调用方必须显式处理）。

### §2.4 生命周期事件

- `WORK_CREATED` → `WORK_ASSIGNED` → `WORK_RUNNING` → `WORK_*_TERMINAL`
- `SUBAGENT_SPAWNED` / `SUBAGENT_COMPLETED`（F092 emit_audit_event 参数控制写入）
- `WORK_SPLIT` / `WORK_MERGED`

---

## §3 DispatchEnvelope 层（运行时包装 / Ephemeral）

### §3.1 定位

- **Ephemeral**：DispatchEnvelope 不直接落盘（payload 部分进 metadata.dispatch_envelope_json，但 envelope 本身是函数调用参数）
- **运行时包装**：把 Work + RuntimeControlContext + user_text + resume snapshot 打包为一次"派发动作"的全量上下文
- **跳数保护**：`hop_count <= max_hops`（F091 model_validator 强制校验）
- **运行时上下文承载**：通过 `runtime_context: RuntimeControlContext` 承载 H1 override + 决策环 hint

### §3.2 字段定义

**源码位置**：`packages/core/src/octoagent/core/models/orchestrator.py:156` `class DispatchEnvelope(BaseModel)`

| 字段 | 类型 | 用途 |
|------|------|------|
| `dispatch_id` | str | 唯一派发 ID |
| `task_id` | str | 关联 Task |
| `trace_id` | str | 链路追踪 |
| `contract_version` | str | 协议版本（默认 "1.0"）|
| `route_reason` | str | 路由理由（与 Work.route_reason 一致）|
| `worker_capability` | str | 目标 worker 能力标签 |
| `hop_count` / `max_hops` | int | 跳数保护（防递归 spawn）|
| `user_text` | str | 用户输入文本 |
| `model_alias` | str \| None | 模型别名 |
| `resume_from_node` / `resume_state_snapshot` | str / dict | Checkpoint 恢复（FR-TASK-4） |
| `tool_profile` | str | 工具权限级别 |
| **`runtime_context`** | `RuntimeControlContext \| None` | F090 引入的显式字段，承载 `delegation_mode` / `turn_executor_kind` / `recall_planner_mode` / **`force_full_recall: bool`**（F100 H1 override）|
| `metadata` | dict | 扩展元数据（含 source_runtime_kind 当 dispatch 后续需序列化为 A2AMessage 时）|

### §3.3 与 Work 的关系

DispatchEnvelope 由 `OrchestratorService` / `DelegationPlane` 从一个 Work 派生：

```python
# 简化伪代码
work = work_store.get(work_id)
envelope = DispatchEnvelope(
    dispatch_id=uuid4(),
    task_id=work.task_id,
    trace_id=trace_id,
    contract_version="1.0",
    route_reason=work.route_reason,
    worker_capability=work.requested_capability,
    hop_count=0,
    max_hops=2,           # F084 DelegationManager max_depth=2
    user_text=task.latest_user_text,
    runtime_context=runtime_control_ctx_from_work(work),  # F090 双轨写入
    metadata={"source_runtime_kind": "main"},             # F099 5 值之一
)
```

### §3.4 F090 D1 收尾（F100 完成）

F090 引入 `RuntimeControlContext` 显式字段时为保守路径采用了**双轨**（写入端切换 + 读取端 metadata fallback）；F100 Phase E1/E2 完成读取端切换 + 移除 metadata 写入。当前 unspecified→False 与 baseline 等价。

`force_full_recall` 字段是 H1 的核心 override 通道：主 Agent 在需要"强制走完整 recall"时通过此字段绕过 `RecallPlannerMode="auto"` 自动决议。

---

## §4 A2AMessage 层（对话消息 / Wire-level）

### §4.1 定位

- **Wire-level**：A2AMessage 是真 A2A 通信场景下的 wire envelope（跨 Agent 进程序列化 + 反序列化）
- **6 类型**：TASK / UPDATE / CANCEL / RESULT / ERROR / HEARTBEAT
- **持久化**：通过 `A2AMessageRecord` 落盘到 `a2a_messages` 表（一等可审计对象）
- **挂在 A2AConversation**：每条 A2AMessage 必属于一个 A2AConversation（context_id 字段）

### §4.2 字段定义

**源码位置**：`packages/protocol/src/octoagent/protocol/models.py:238` `class A2AMessage(BaseModel)`

| 字段 | 类型 | 用途 |
|------|------|------|
| `schema_version` | str | 协议版本（当前 "0.1"，frozen `SUPPORTED_SCHEMA_VERSIONS`）|
| `message_id` | str | 消息唯一 ID |
| `task_id` | str | 关联 Task |
| `context_id` | str | A2A conversation_id |
| `from_agent` / `to_agent` | str | sender/receiver agent URI（如 `agent://main` / `agent://worker.ops/default`）|
| `type` | `A2AMessageType` | TASK / UPDATE / CANCEL / RESULT / ERROR / HEARTBEAT |
| `idempotency_key` | str | 幂等键 |
| `timestamp_ms` | int | unix ms |
| `payload` | `A2APayload` | typed payload（按 type 自动 dispatch）|
| `trace` | `A2ATraceContext` | trace_id / parent_span_id |
| `metadata` | `A2AMessageMetadata` | source_session_id / target_session_id / **source_runtime_kind**（F099 5 值）/ origin_user_thread_id / work_id / context_capsule_ref / recall_frame_id |

### §4.3 持久化：`A2AMessageRecord`

**源码位置**：`packages/core/src/octoagent/core/models/a2a_runtime.py:56`

| 字段 | 类型 | 用途 |
|------|------|------|
| `a2a_message_id` / `a2a_conversation_id` / `message_seq` | str / str / int | 主键 + 顺序号 |
| `task_id` / `work_id` / `project_id` | str | 关联 ID |
| `source_agent_runtime_id` / `source_agent_session_id` | str | 来源 runtime/session（F098 ephemeral runtime 独立路径）|
| `target_agent_runtime_id` / `target_agent_session_id` | str | 目标 runtime/session |
| `direction` | `A2AMessageDirection` | INBOUND / OUTBOUND |
| `message_type` / `protocol_message_id` | str | 协议层信息 |
| `from_agent` / `to_agent` | str | agent URI |
| `idempotency_key` | str | 幂等键 |
| `payload` / `trace` / `metadata` / `raw_message` | dict | 4 个 dict 字段 |
| `created_at` | datetime | 时间戳 |

### §4.4 source_runtime_kind 5 值（F099 引入）

```python
# packages/core/src/octoagent/core/models/source_kinds.py
MAIN = "main"
WORKER = "worker"
SUBAGENT = "subagent"
AUTOMATION = "automation"      # F099 新增：cron-triggered routines
USER_CHANNEL = "user_channel"  # F099 新增：直连用户通道
KNOWN_SOURCE_RUNTIME_KINDS = frozenset({MAIN, WORKER, SUBAGENT, AUTOMATION, USER_CHANNEL})
```

**F098 Phase D post-review 关键修复**：A2A source 派生**仅信任**显式 `envelope.metadata.source_runtime_kind` 信号（缺信号默认 `main`）。原 baseline 用 `turn_executor_kind` 派生 source role（target 侧字段）→ 主 Agent 派 worker 时 source 误判 worker。

### §4.5 CONTROL_METADATA_UPDATED event（F098 引入）

`USER_MESSAGE` event 不再被复用为 control_metadata 承载体（F097 P1-1 高 known issue 修复）。新增 `CONTROL_METADATA_UPDATED` event：

- only carries `control_metadata`，不污染 `latest_user_text`
- consumer 影响：`ContextCompactionService._load_conversation_turns` 不再把 `USER_MESSAGE` event 当用户 turn 时错误捕捉到 marker text "[subagent delegation metadata]"
- F099 ask_back 三工具通过此事件机制持久化 `is_caller_worker_signal`（N-H1 修复）

---

## §5 三层字段映射表

> 关联键（task_id / work_id / agent_runtime_id 等）在三层之间的对应关系。

| 概念 | Work | DispatchEnvelope | A2AMessage |
|------|------|-----------------|------------|
| **Task 关联** | `task_id` | `task_id` | `task_id` |
| **Work 关联** | `work_id` | `metadata.work_id` | `metadata.work_id` |
| **Conversation** | （隐式 via session_owner_profile_id）| —— | `context_id`（= `a2a_conversation_id`）|
| **派发实例 ID** | `delegation_id` | `dispatch_id` | `message_id`（每条独立）|
| **能力标签** | `requested_capability` | `worker_capability` | `metadata.target_capability`（可选）|
| **路由理由** | `route_reason` | `route_reason` | （不持有）|
| **跳数保护** | （Work 是 Persistent 不保跳数）| `hop_count / max_hops` | （A2A 不保跳数）|
| **运行时上下文** | （引用 RuntimeControlContext via runtime_id）| **`runtime_context`** | `metadata.source_runtime_kind`（F099 5 值之一）|
| **Trace** | （隐式 trace_id in metadata）| `trace_id` | `trace.trace_id` / `trace.parent_span_id` |
| **Resume snapshot** | （不持有，恢复时从 events 重建）| `resume_from_node` / `resume_state_snapshot` | （A2A 不持 resume）|
| **agent_runtime** | `owner_id` / `runtime_id` | （runtime 通过 runtime_context 派生）| `metadata.source_agent_runtime_id` / `metadata.target_agent_runtime_id` |

---

## §6 三层职责边界

### §6.1 Work：Persistent 执行单元

**职责**：
- 落盘的"我要派给某人做的一件事"
- 状态机驱动的生命周期
- 持有委派意图（target_kind / requested_capability）
- 持有 Project / Profile / Tool selection 等"长期事实"

**不职责**：
- 不持有运行时控制 hint（`RuntimeControlContext` 在 DispatchEnvelope）
- 不持有 user_text（在 Task event chain 或 DispatchEnvelope）
- 不直接被 wire-level 序列化（必须经过 DispatchEnvelope → A2AMessage）

### §6.2 DispatchEnvelope：Ephemeral 运行时包装

**职责**：
- 把 Work + 当前 turn 的 RuntimeControlContext + user_text + resume snapshot 打包
- 通过 `runtime_context` 字段承载 H1 override（force_full_recall）+ 决策环 hint
- 跳数保护（model_validator 强制校验）
- 是 LLMService / OrchestratorService 之间的"派发参数"对象

**不职责**：
- 不直接落盘（dispatch_envelope_json 只在 metadata 中辅助 trace）
- 不持有 conversation context（A2A conversation 是 A2AMessage 层概念）
- 不持有 A2A wire 协议字段（idempotency_key / schema_version 等在 A2AMessage）

### §6.3 A2AMessage：Wire-level 对话消息

**职责**：
- 跨 Agent 进程的 wire envelope（序列化 + 反序列化）
- 6 类型消息（TASK / UPDATE / CANCEL / RESULT / ERROR / HEARTBEAT）
- 通过 A2AMessageRecord 持久化（一等可审计对象）
- 挂在 A2AConversation（context_id）

**不职责**：
- 不持有 RuntimeControlContext（在 DispatchEnvelope）
- 不持有 Work 状态机（在 Work）
- 不持有 resume snapshot（在 DispatchEnvelope）

### §6.4 关键论断："A2A 是 Work 的一种通信形式"

不是所有 Work 都需要 A2AMessage：

- **Inline 执行场景**（主 Agent 自己执行 Work）：Work → DispatchEnvelope → LLMService → ToolBroker → ToolResult。**没有 A2AMessage 序列化**
- **H3-A 临时 Subagent 场景**（F097）：Work（target_kind=SUBAGENT）→ DispatchEnvelope → 进程内 spawn ephemeral runtime → `SUBAGENT_INTERNAL` session。**有 A2AConversation + A2AMessage 但是进程内传递**
- **H3-B A2A 真 P2P 场景**（F098）：Work（target_kind=WORKER）→ DispatchEnvelope → 序列化为 A2AMessage（type=TASK）→ 写入 A2AMessageRecord → receiver Worker 反序列化执行。**真 wire-level 序列化**
- **A2A 跨外部 Agent 场景**（未来）：Work（target_kind=EXTERNAL_A2A）→ DispatchEnvelope → A2AMessage（标准 A2A TaskState）→ HTTP/SSE 跨进程 → receiver A2A peer。**经 A2AStateMapper 双向映射**

---

## §7 与三条设计哲学的对应

> 详见 [blueprint/agent-collaboration-philosophy.md](../blueprint/agent-collaboration-philosophy.md)

- **H1 管家 mediated**：DispatchEnvelope 的 `runtime_context.force_full_recall` 是 H1 override 通道
- **H2 完整 Agent 对等性**：Work 的 `project_id` + `agent_profile_id` + `runtime_id` 三元组保证每个 Agent 有完整上下文栈；A2AMessage `metadata.source_runtime_kind` 区分来源
- **H3 两种委托模式**：Work 的 `target_kind` + `kind` 决定走 H3-A（SUBAGENT_SPAWN）还是 H3-B（DELEGATION 到外部 Worker）；A2AMessage 的 6 类型 + ask_back 三工具支持 H3-B 中途澄清

---

## §8 引用

- `Work`：`packages/core/src/octoagent/core/models/delegation.py:258`
- `DispatchEnvelope`：`packages/core/src/octoagent/core/models/orchestrator.py:156`
- `A2AMessage`：`packages/protocol/src/octoagent/protocol/models.py:238`
- `A2AMessageRecord`：`packages/core/src/octoagent/core/models/a2a_runtime.py:56`
- `A2AConversation`：`packages/core/src/octoagent/core/models/a2a_runtime.py:29`
- `RuntimeControlContext`：`packages/core/src/octoagent/core/models/orchestrator.py:55`（F090 引入，F100 force_full_recall 字段）
- `RecallPlannerMode` Literal：`packages/core/src/octoagent/core/models/orchestrator.py:52`（值：`"full" | "skip" | "auto"`）
- `source_runtime_kind` 5 值：`packages/core/src/octoagent/core/models/source_kinds.py`（F099）
- 三层哲学映射：[blueprint/agent-collaboration-philosophy.md](../blueprint/agent-collaboration-philosophy.md)
- A2A envelope wire spec：[blueprint/api-and-protocol.md §10.2](../blueprint/api-and-protocol.md)
- F098 BaseDelegation 抽象：[blueprint/architecture-audit.md §14.12](../blueprint/architecture-audit.md)

---
