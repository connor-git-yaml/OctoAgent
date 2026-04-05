# §10 API 与协议（Interface Spec）

> 本文件是 [blueprint.md](../blueprint.md) §10 的完整内容。

---

### 10.1 Gateway ↔ Kernel（HTTP）

- `POST /kernel/ingest_message`
  - body: NormalizedMessage
  - returns: `{task_id}`

- `GET /kernel/tasks/{task_id}`
  - returns: Task（当前状态快照）

- `POST /kernel/tasks/{task_id}/cancel`
  - returns: `{ok, task_id}`

- `GET /kernel/stream/task/{task_id}`
  - SSE events: Event（json）
  - 终止信号：终态事件携带 `"final": true`，客户端据此关闭连接（对齐 A2A SSE 规范）

- `POST /kernel/approvals/{approval_id}/decision`
  - body: `{decision: approve|reject, comment?: str}`

### 10.2 Kernel ↔ Worker（A2A-Lite Envelope）

```yaml
A2AMessage:
  schema_version: "0.1"
  message_id: "uuid"
  task_id: "uuid"
  context_id: "a2a_conversation_id"   # 对齐 A2A contextId，关联 Butler ↔ Worker 对话
  from: "agent://butler.main"
  to: "agent://worker.ops/default"
  type: TASK|UPDATE|CANCEL|RESULT|ERROR|HEARTBEAT
  idempotency_key: "string"
  timestamp_ms: 0
  payload: { ... }
  trace: { trace_id, parent_span_id }
  metadata:
    source_session_id: "session://butler-user/..."
    target_session_id: "session://worker-a2a/..."
    origin_user_thread_id: "stable_thread_key"
    work_id: "work_id"
    context_capsule_ref: "artifact://..."
    recall_frame_id: "optional uuid"
```

语义要求：
- UPDATE 必须可投递到"正在运行的 `A2AConversation + WorkerSession`"；否则进入 WAITING_INPUT 并提示用户
- CANCEL 必须推进终态（CANCELLED），不可"卡 RUNNING"
- Butler 当前是唯一 user-facing speaker；Worker 返回 RESULT/ERROR 后，由 Butler 负责综合并对用户发言
- Worker 默认接收 `context_capsule_ref`，而不是 ButlerSession 的完整原始历史；若需要更宽上下文，必须显式声明授权与 provenance
- 未来若开放 DirectWorkerSession，仍必须走独立 `AgentSession + A2AConversation + MemoryNamespace` 链路，不得把 user-facing 会话和 internal worker session 复用成同一个对象

#### 10.2.1 A2A 状态映射（A2A TaskState Compatibility）

OctoAgent 内部状态是 A2A 协议的**超集**。内部通信（Kernel ↔ Worker）使用完整状态；对外暴露 A2A 接口时通过映射层转换。

```yaml
# OctoAgent → A2A TaskState 映射
StateMapping:
  CREATED:           submitted     # 合并到 submitted（已接收未处理）
  QUEUED:            submitted
  RUNNING:           working
  WAITING_INPUT:     input-required
  WAITING_APPROVAL:  input-required  # 审批对外表现为"需要输入"
  PAUSED:            working         # 暂停是内部实现细节，对外仍为"处理中"
  SUCCEEDED:         completed
  FAILED:            failed
  CANCELLED:         canceled
  REJECTED:          rejected        # 直接映射

# A2A → OctoAgent 反向映射（外部 Agent 调入时）
ReverseMapping:
  submitted:      QUEUED
  working:        RUNNING
  input-required: WAITING_INPUT
  completed:      SUCCEEDED
  canceled:       CANCELLED
  failed:         FAILED
  rejected:       REJECTED
  auth-required:  WAITING_APPROVAL   # auth 语义映射到审批
  unknown:        FAILED             # 降级为失败
```

设计原则：
- **内部超集**：OctoAgent 保留 WAITING_APPROVAL、PAUSED、CREATED 等 A2A 没有的状态，满足内部治理需求
- **外部兼容**：对外通过 A2AStateMapper 暴露标准 A2A TaskState，实现 Worker ↔ SubAgent 通信一致性
- **映射无损**：终态（completed/canceled/failed/rejected）一一对应；非终态映射后语义明确

#### 10.2.2 A2A Artifact 映射

OctoAgent Artifact 是 A2A Artifact 的**超集**（多出 artifact_id、version、hash、size）。对外暴露时通过映射层转换。

```yaml
# OctoAgent Artifact → A2A Artifact 映射
ArtifactMapping:
  name:        → name
  description: → description
  parts:       → parts            # Part 结构已对齐（text/file/json → TextPart/FilePart/JsonPart）
  append:      → append
  last_chunk:  → lastChunk
  # 以下字段 A2A v0.3 部分支持，其余降级到 metadata
  artifact_id: → artifactId        # A2A v0.3 已支持 artifactId 字段
  version:     → metadata.version  # 降级到 metadata
  hash:        → metadata.hash
  size:        → metadata.size
  storage_ref: → 转为 parts[].uri  # storage_ref 映射到 Part 的 uri 字段

# Part 类型映射
PartTypeMapping:
  text:  → TextPart   (content → text)
  file:  → FilePart   (content → data[base64], uri → uri)
  json:  → JsonPart   (content → data)
  image: → FilePart   (mime: image/*, uri → uri)
```

### 10.3 Tool Call 协议

- LLM 输出：
  - `tool_calls: [{tool_id, args_json, idempotency_key}]`
- ToolBroker 执行：
  - 返回 `ToolResult { ok, data, error, artifact_refs }`
- 结果回灌：
  - 只回灌 summary + structured fields
  - 全量输出走 artifact

---
