# §10 API 与协议（Interface Spec）

> 本文件是 [blueprint.md](../blueprint.md) §10 的完整内容。

---

### 10.1 Gateway application API（HTTP）

- 当前只有 `apps/gateway` 这一 application host，不存在独立 Kernel 服务，也不存在 `/kernel/*` 内部网络协议。
- 渠道与 Web 通过 Gateway 的公开 `/api/*` 路由创建消息、查询任务、取消任务、处理审批与消费 SSE。
- 具体 path 以 `apps/gateway/src/octoagent/gateway/routes/` 和生成的 OpenAPI 为单一事实源；Blueprint 不再复制一套可能漂移的 route 清单。

内部协调、TaskRunner、Policy、Memory 与 Worker runtime 角色通过同一进程内的 application service 调用，并共享 SQLite/Event/Work 事实源。

SSE 的终止信号仍由终态事件携带 `"final": true`；客户端据此关闭连接。

### 10.2 Gateway runtime 内逻辑 Agent 间的 A2A-Lite Envelope

```yaml
A2AMessage:
  schema_version: "0.1"
  message_id: "uuid"
  task_id: "uuid"
  context_id: "a2a_conversation_id"   # 对齐 A2A contextId，关联主 Agent ↔ Worker 对话
  from: "agent://main"                # F090 后命名：butler → main
  to: "agent://worker.ops/default"
  type: TASK|UPDATE|CANCEL|RESULT|ERROR|HEARTBEAT
  idempotency_key: "string"
  timestamp_ms: 0
  payload: { ... }
  trace: { trace_id, parent_span_id }
  metadata:
    # 注：A2AMessageMetadata typed model 含 hop_count/max_hops/route_reason/worker_capability/
    #     tool_profile/model_alias/internal_status/retryable 8 个 typed 字段。其余通过 dict
    #     access（向后兼容），如下：
    source_session_id: "session://main-user/..."
    target_session_id: "session://worker-a2a/..."
    source_runtime_kind: "main"|"worker"|"subagent"|"automation"|"user_channel"  # F099 引入，dict key
    origin_user_thread_id: "stable_thread_key"
    work_id: "work_id"
    context_capsule_ref: "artifact://..."
    recall_frame_id: "optional uuid"
```

语义要求：
- UPDATE 必须可投递到"正在运行的 `A2AConversation + AgentSession`"；否则进入 WAITING_INPUT 并提示用户
- CANCEL 必须推进终态（CANCELLED），不可"卡 RUNNING"
- 主 Agent 当前是唯一 user-facing speaker（H1）；Worker 返回 RESULT/ERROR 后，由主 Agent 负责综合并对用户发言
- Worker 默认接收 `context_capsule_ref`，而不是主 Agent Session 的完整原始历史；若需要更宽上下文，必须显式声明授权与 provenance
- **F099 `source_runtime_kind` 5 值枚举**（`packages/core/src/octoagent/core/models/source_kinds.py`）：`MAIN / WORKER / SUBAGENT / AUTOMATION / USER_CHANNEL`；A2A source 派生仅信任显式 `envelope.metadata.source_runtime_kind` 信号（缺信号默认 `main`）
- **F098 关闭 D14**：删除 `_enforce_child_target_kind_policy`，Worker↔Worker A2A 已解禁；BaseDelegation 公共抽象提取（F097 `SubagentDelegation` + F098 A2A `WorkerDelegation` 共享）
- 未来若开放 DirectWorkerSession，仍必须走独立 `AgentSession + A2AConversation + MemoryNamespace` 链路，不得把 user-facing 会话和 internal worker session 复用成同一个对象

#### 10.2.1 A2A 状态映射（A2A TaskState Compatibility）

OctoAgent 内部状态是 A2A 协议的**超集**。同一 Gateway runtime 内的逻辑 Agent 通信使用完整状态；对外暴露 A2A 接口时通过映射层转换。

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

**WAITING_APPROVAL 状态机改造**（F101 引入）：

- task_runner 单 owner + CAS（compare-and-swap）防并发竞争
- 双注册桥接 ApprovalManager + ApprovalGate
- startup recovery：进程重启后扫描 `WAITING_APPROVAL` task，重新绑定到 ApprovalGate
- ApprovalGate SSE production 接入：`escalate_permission` 工具（F099）走 `approval_gate.request_approval()` + `wait_for_decision()` 真闭环

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

**WriteResult 通用回显契约**（F084 引入）：
- 18+ 写工具 return type 强制 `WriteResult` 子类，注册期 fail-fast
- 保留 `task_id` / `memory_id` / `run_id` 等关联键，不压扁

### 10.4 Notification API（F101 引入）

#### HTTP endpoint

- `GET /api/notifications?session_id=...`：list_active（自动过滤 dismissed）
  - 返回：notifications 数组（含 `notification_id` / `priority` / `task_id` / `notification_type` / `created_at` / `dismissed`）
- `POST /api/notifications/{notification_id}/dismiss`：dismiss notification（跨通道统一 state + F116 跨重启持久化）
  - 返回：`{ok, notification_id, persisted}`；`persisted=false` 表示仅内存生效未 durable 落盘（DB 故障降级，不升级为 500，前端可提示/重试，Constitution #6）
- Web SSE：通过 SSEHub 推送 `NOTIFICATION_DISPATCHED` 事件给前端红点 badge

#### `NotificationService.notify_task_state_change()` 签名

```python
def notify_task_state_change(
    task_id: str,
    new_state: TaskStatus,
    priority: NotificationPriority,  # CRITICAL | HIGH | MEDIUM | LOW
    session_id: str | None,
    state_transition_event_id: str,
    channels: frozenset[str] | None = None,  # F102 加，向后兼容
) -> None
```

- `channels=None`（默认）：对所有已注册 channel push
- `channels=frozenset({"telegram", "web_sse"})`：仅对匹配 channel 推送（F102 daily routine 用）

#### Telegram callback

- `TelegramNotificationChannel` 支持 inline keyboard + dismiss button
- callback_data 含 notification_id；用户点击 dismiss 等价于 `POST /api/notifications/{id}/dismiss`

### 10.5 Routine Audit API（F102 引入）

#### 系统 routine audit task 占位

`_daily_routine_audit` 是系统 routine 的 audit task 占位（参照 F086 ObservationRoutine pattern）：

- 启动时 `await task_store.get_task("_daily_routine_audit")`，不存在则 create_task + commit
- `task.status = SUCCEEDED`（避免被业务逻辑捡起）
- 所有 ROUTINE_* 事件 `task_id` 引用此占位

#### 4 EventType

```python
ROUTINE_TRIGGERED = "ROUTINE_TRIGGERED"    # cron 触发时刻
ROUTINE_COMPLETED = "ROUTINE_COMPLETED"    # 含 elapsed_ms / fallback / worker/failed/attention counts
ROUTINE_FAILED = "ROUTINE_FAILED"          # error_type + error_msg（无 traceback）
ROUTINE_SKIPPED = "ROUTINE_SKIPPED"        # reason (routine_disabled / no_user_timezone)
```

### 10.6 EventType 清单（F084-F127 新增）

> 完整 EventType 见 `packages/core/src/octoagent/core/models/enums.py`。

| EventType | 引入 Feature | 用途 |
|-----------|-------------|------|
| `NOTIFICATION_DISPATCHED` | F101 | 每条 notification（含 quiet hours 过滤的，payload `filtered=True/False`） |
| `ROUTINE_TRIGGERED` / `ROUTINE_COMPLETED` / `ROUTINE_FAILED` / `ROUTINE_SKIPPED` | F102 | DailyRoutine 4 个生命周期事件 |
| `CONTROL_METADATA_UPDATED` | F098 | only carries control_metadata，不污染 latest_user_text |
| `SUBAGENT_SPAWNED` | F092 | Subagent spawn（emit_audit_event 参数控制写入）|
| `SUBAGENT_COMPLETED` | F097 | Subagent 终态 |
| `AGENT_SESSION_TURN_PERSISTED` | F093 | Worker turn 写入持久化审计 |
| `BEHAVIOR_PACK_LOADED` | F095 schema / F096 EventStore 接入 | BehaviorPack 加载（含 `agent_id` + `agent_kind` 字段）|
| `BEHAVIOR_PACK_USED` | F096 | BehaviorPack 实际被消费（dispatch e2e 验证）|
| `MEMORY_RECALL_SCHEDULED` / `MEMORY_RECALL_COMPLETED` / `MEMORY_RECALL_FAILED` | F094 引入 + F096 覆盖范围扩大到同步路径 | Recall 三态事件（scheduled / completed / failed）；F096 引入 `list_recall_frames` audit endpoint 暴露 agent_runtime_id 维度审计 |
| `MEMORY_CONSOLIDATION_TRIGGERED` / `COMPLETED` / `FAILED` / `SKIPPED` + `PROPOSED` / `APPROVED` / `REJECTED` / `CONFLICTED` | F127（M7） | 睡眠时记忆巩固：运行级 4 + 提议级 4；`CONFLICTED` actor=SYSTEM（accept 时源过期 / 敏感最后闸检测，与用户决策 REJECTED 区分）；payload PII 防护（content_hash / id 引用不含原文）；全部挂 `_memory_consolidation_root` 系统占位 task |

### 10.7 ask_back 三工具（F099 引入）

`apps/gateway/src/octoagent/gateway/services/builtin_tools/ask_back_tools.py`：

- **`worker.ask_back(question: str, target: AskBackTarget)`**：向上游（user / butler / caller worker）提问，task 进入 `WAITING_INPUT`
- **`worker.request_input(prompt: str, expected_fields: list[str])`**：结构化输入请求
- **`worker.escalate_permission(action: str, reason: str)`**：升级权限请求（走 ApprovalGate SSE 路径）

统一 emit `CONTROL_METADATA_UPDATED` 审计事件；`source_runtime_kind` 严格按调用方真实身份注入。

**is_caller_worker resume 持久化**（F099 N-H1 修复）：

- 通过 `CONTROL_METADATA_UPDATED` 事件机制持久化 `is_caller_worker_signal`
- resume 路径从 `resume_state_snapshot` 读取信号恢复
- 桥接：`worker_runtime emit` + `task_runner attach_input` + `connection_metadata TASK_SCOPED_CONTROL_KEYS`

### 10.8 Consolidation Candidates API（F127 引入）

巩固合并提议人审 REST（C4/C7 Two-Phase 的 Gate 面，`routes/consolidation_candidates.py`）：

- `GET /api/consolidation/candidates?scope_id=...&status=...`：候选列表（默认 pending）
- `POST /api/consolidation/candidates/{candidate_id}/accept`：atomic claim（PENDING→APPLYING CAS）→ commit 前验证（敏感最后闸 + 逐源仍 current）→ 写管道 commit MERGE（源 superseded）→ APPLIED；**验证判定失败 → CONFLICT 终态 + 409**（detail 引导等下次巩固基于新源重新提议）；验证自身异常 → 回滚 PENDING 可重试
- `POST /api/consolidation/candidates/{candidate_id}/reject`：REJECTED（不碰 SoR）
- `POST /api/consolidation/candidates/bulk_reject`：批量 reject

系统 audit task 占位 `_memory_consolidation_root`（同 F102 `_daily_routine_audit` 范式，**task+work 成对合成**——`spawn_child` 必需真 parent 对；经 `SYSTEM_INTERNAL_WORK_IDS` 排除不泄漏用户可见委派 / Worker 视图）。

---
