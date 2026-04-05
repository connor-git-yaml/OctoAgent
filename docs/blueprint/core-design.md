# §8 核心设计（Core Design）

> 本文件是 [blueprint.md](../blueprint.md) §8 的完整内容。

---

### 8.1 统一数据模型（Domain Model）

#### 8.1.1 NormalizedMessage

```yaml
NormalizedMessage:
  channel: "telegram" | "web" | "wechat_import" | ...
  thread_id: "stable_thread_key"
  scope_id: "chat:<channel>:<thread_id>"
  sender_id: "..."
  sender_name: "..."
  timestamp: "RFC3339"
  text: "..."
  attachments:
    - id: "..."
      mime: "..."
      filename: "..."
      size: 123
      storage_ref: "artifact://..."
  raw_ref: "pointer to original event"
  meta:
    message_id: "optional upstream id"
    reply_to: "optional"
```

#### 8.1.2 Task / Event / Artifact

```yaml
Task:
  task_id: "uuid"
  created_at: "..."
  updated_at: "..."
  status: CREATED|QUEUED|RUNNING|WAITING_INPUT|WAITING_APPROVAL|PAUSED|SUCCEEDED|FAILED|CANCELLED|REJECTED
  title: "short"
  thread_id: "..."
  scope_id: "..."
  owner_agent_id: "agent://butler.main"
  owner_session_id: "session://butler-user/..."
  a2a_conversation_id: "optional uuid"
  parent_task_id: "optional uuid"   # 子任务层级（Orchestrator → Worker 派发时关联）
  requester: { channel, sender_id }
  assigned_worker: "worker_id"
  risk_level: low|medium|high
  budget:
    max_cost_usd: 0.0
    max_tokens: 0
    deadline_at: "optional"
  pointers:
    latest_event_id: "..."
    latest_checkpoint_id: "optional"
```

```yaml
Event:
  event_id: "ulid"
  task_id: "uuid"
  task_seq: 1                    # 同一 task 内单调递增序号（用于确定性回放）
  ts: "..."
  type: TASK_CREATED|USER_MESSAGE|MODEL_CALL|MODEL_CALL_STARTED|MODEL_CALL_COMPLETED|MODEL_CALL_FAILED|TOOL_CALL|TOOL_RESULT|STATE_TRANSITION|ARTIFACT_CREATED|APPROVAL_REQUESTED|APPROVED|REJECTED|TASK_REJECTED|ERROR|HEARTBEAT|CHECKPOINT_SAVED|A2A_MESSAGE_SENT|A2A_MESSAGE_RECEIVED|A2A_MESSAGE_ACKED
  schema_version: 1               # 事件格式版本，便于后续兼容迁移
  actor: user|butler|worker|tool|system
  payload: { ... }   # 强结构化（默认不放原始大文本/敏感原文）
  trace_id: "..."
  span_id: "..."
  causality:
    parent_event_id: "optional"
    idempotency_key: "required for ingress/side-effects"
```

- `MODEL_CALL` 为历史兼容事件类型（M0 / 旧 schema）；新写入默认使用 `MODEL_CALL_STARTED|MODEL_CALL_COMPLETED|MODEL_CALL_FAILED` 三段事件。

```yaml
Artifact:
  artifact_id: "ulid"            # 全局唯一（A2A 只有 index，我们更强）
  task_id: "uuid"
  ts: "..."
  name: "..."
  description: "optional"        # 新增，对齐 A2A
  parts:                         # 改为 parts 数组，对齐 A2A Artifact.parts
    - type: text|file|json|image # 对应 A2A 的 TextPart/FilePart/JsonPart
      mime: "..."                # Part 级别 MIME
      content: "inline 或 null"  # 小内容 inline（对齐 A2A data/text）
      uri: "file:///... 或 null" # 大文件引用（对齐 A2A FilePart.uri）
  storage_ref: "..."             # 保留，整体大文件外部存储引用
  size: 123                      # 保留，A2A 没有
  hash: "sha256"                 # 保留，完整性校验
  version: 1                     # 保留，版本化能力（A2A immutable，我们支持版本迭代）
  append: false                  # 新增，对齐 A2A 流式追加
  last_chunk: false              # 新增，标记流式最后一块
  meta: { ... }
```

Part 类型说明（对齐 A2A Part 规范）：
- `text`：纯文本 / markdown（对应 A2A TextPart）
- `file`：文件引用或 inline Base64（对应 A2A FilePart）
- `json`：结构化 JSON 数据（对应 A2A JsonPart）
- `image`：图片（本质是 file 的特化，便于 UI 渲染）
- 暂不支持 A2A 的 FormPart / IFramePart，按需扩展

#### 8.1.3 Agent Runtime Objects（2026-03-13 架构纠偏）

```yaml
AgentRuntime:
  agent_id: "agent://butler.main" | "agent://worker.research/default"
  agent_kind: butler|worker
  role: supervisor|research|dev|ops|custom
  project_id: "project_id"
  workspace_id: "optional workspace_id"
  agent_profile_id: "optional root/default agent profile id"
  worker_profile_id: "optional worker owner profile id"
  persona_refs:
    - "artifact://persona.md"
  instruction_refs:
    - "artifact://project-instructions.md"
  permission_preset: minimal|normal|full
  auth_profile: "optional"
  policy_profile: "optional"
  memory_namespace_ids:
    - "memory://project/<project_id>/shared"
    - "memory://agent/<agent_id>/private"
  default_session_kind: butler_user|worker_a2a|worker_direct
```

```yaml
AgentSession:
  session_id: "uuid"
  agent_id: "agent://..."
  session_kind: butler_user|worker_a2a|worker_direct
  project_id: "project_id"
  workspace_id: "optional workspace_id"
  channel_thread_id: "optional stable_thread_key"
  session_owner_profile_id: "用户当前默认在和谁对话"
  turn_executor_kind: self|worker|subagent
  delegation_target_profile_id: "仅在本轮显式委派时存在"
  inherited_context_owner_profile_id: "可选，连续性/记忆来源 owner"
  parent_session_id: "optional uuid"
  a2a_conversation_id: "optional uuid"
  effective_config_snapshot_ref: "artifact://..."
  recent_turn_refs:
    - "task_id or artifact_id"
  rolling_summary_ref: "artifact://..."
  compaction_state:
    enabled: true
    last_compacted_at: "optional"
```

```yaml
MemoryNamespace:
  namespace_id: "memory://project/<project_id>/shared" | "memory://agent/<agent_id>/private"
  owner_kind: project|agent
  owner_id: "project_id or agent_id"
  visibility: shared|private
  partitions:
    - profile
    - work
    - chat:web:thread_id
  backend: sqlite|lancedb|hybrid
```

```yaml
RecallFrame:
  recall_frame_id: "uuid"
  agent_id: "agent://..."
  session_id: "session://..."
  trigger_task_id: "task_id"
  query: "当前问题 / A2A payload / task goal"
  sources:
    session_recency: ["turn_ref"]
    agent_private_memory: ["memory_ref"]
    project_shared_memory: ["memory_ref"]
    work_evidence: ["artifact_ref"]
  provenance_ref: "artifact://..."
```

```yaml
A2AConversation:
  conversation_id: "uuid"
  source_agent_id: "agent://butler.main"
  target_agent_id: "agent://worker.research/default"
  source_session_id: "session://butler-user/..."
  target_session_id: "session://worker-a2a/..."
  work_id: "work_id"
  status: active|completed|failed|cancelled
  last_message_id: "optional uuid"
```

关系约束：
- `Project` 提供共享 instructions / knowledge / shared memory / secrets / channel bindings。
- `Butler` 与每个 `Worker` 都是独立 `AgentRuntime`，各自拥有 session、memory、recall、compaction。
- `Work` 是执行与委派单元，不再兼职承载"Agent 私有会话"语义。
- `A2AConversation` 是 Butler 与 Worker 之间的 durable carrier；没有 durable A2A conversation，就不算完成多 Agent 主链。

---

### 8.2 Task/Event Store：事件溯源与视图

#### 8.2.1 事件溯源（Event Sourcing）策略

- 事实来源：Event 表（append-only）
- Task 表：是 Event 的"物化视图"（projection），用于快速查询
- 任何对 Task 的状态更新都必须通过写入事件触发 projection 更新
- Event payload 默认写摘要与引用（artifact_ref），避免在事件中存储大体积/敏感原文

**好处：**
- 可回放（replay）
- 可审计（audit）
- 可恢复（rebuild projections）

#### 8.2.2 SQLite 表建议（MVP）

- `tasks`：task_id PK，status，meta，timestamps，indexes(thread_id, status)
- `events`：event_id PK，task_id FK，task_seq，ts，type，payload_json，idempotency_key，indexes(task_id, task_seq), indexes(task_id, ts), unique(task_id, task_seq), unique(idempotency_key where not null)
- `artifacts`：artifact_id PK，task_id FK，parts_json，storage_ref，hash，version
- `checkpoints`：checkpoint_id PK，task_id FK，node_id，state_json，ts
- `approvals`：approval_id PK，task_id FK，status，request_json，decision_json
- `agent_runtimes`：agent_id PK，agent_kind，role，project_id，profile_id，tool/auth/policy profile refs，persona/instruction refs
- `agent_sessions`：session_id PK，agent_id FK，session_kind，project_id，channel_thread_id，parent_session_id，a2a_conversation_id，summary refs，updated_at
- `memory_namespaces`：namespace_id PK，owner_kind，owner_id，visibility，backend，partition config
- `recall_frames`：recall_frame_id PK，agent_id FK，session_id FK，task_id FK，query，sources_json，provenance_ref
- `a2a_conversations`：conversation_id PK，source_agent_id，target_agent_id，source_session_id，target_session_id，work_id，status
- `a2a_messages`：message_id PK，conversation_id FK，type，from_agent_id，to_agent_id，from_session_id，to_session_id，payload_json，ts，idempotency_key

**一致性要求：**
- 写事件与更新 projection 必须在同一事务内（SQLite transaction）
- events 使用 ULID/时间有序 id 便于流式读取
- 同一 task 的 `task_seq` 必须严格单调递增（无重复、无回退）
- 外部入口写入与带副作用动作必须携带 `idempotency_key`（用于去重与重试安全）

---

### 8.3 编排模型：全层 Free Loop + Skill Pipeline

#### 8.3.1 设计原则

Orchestrator 和 Workers **永远以 Free Loop 运行**，保证最大灵活性和自主决策能力。
确定性编排（Graph）**下沉为 Worker 的工具**——Skill Pipeline，仅在需要时由 Worker 主动调用。

- **Free Loop**（Orchestrator / Workers）：LLM 驱动的推理循环，自主决策下一步行动
- **Skill Pipeline**（Worker 的子流程）：确定性 DAG/FSM，用于有副作用/需要 checkpoint/需要审计的子任务

> Graph 不是"执行模式的一种选择"，而是 Worker 手中的编排工具——类似于 Worker 可以调用单个 Skill，也可以调用一条 Skill Pipeline。

#### 8.3.2 Worker 何时调用 Skill Pipeline（建议默认规则）

Worker 在 Free Loop 中自主决策。满足任一条件时，倾向于使用 Skill Pipeline：
- 有不可逆副作用（发消息/改配置/支付/删除）
- 对接"正式系统"（calendar/email/生产配置）
- 需要可审计/可回放（对外承诺、重要决策）
- 需要强 SLA（定时任务、稳定交付）
- 多步骤流程需要节点级 checkpoint（崩溃后可从中间恢复）

其余情况，Worker 在 Free Loop 中直接调用单个 Skill 或 Tool 即可。

#### 8.3.3 Skill Pipeline 类型

- DAG：一次性流水线（抽取→规划→执行→总结）
- FSM：多轮交互、审批、等待外部事件（审批通过→执行，否则回退）

#### 8.3.4 Skill Pipeline Engine MVP 要求（基于 pydantic-graph）

- 节点 contract 校验（输入/输出）— pydantic-graph 原生类型安全
- checkpoint（每个节点结束写 checkpoint）— pydantic-graph 内置 persistence，需适配 SQLite
- retry 策略：
  - 同模型重试
  - 升级模型（cheap → main）
  - 切换 provider（由 LiteLLM 处理）
- interrupt（HITL）— pydantic-graph 内置 iter/resume：
  - WAITING_APPROVAL
  - WAITING_INPUT
- 事件化：节点运行与迁移必须发事件 — 需薄包装 EventEmitter

#### 8.3.5 崩溃恢复策略

| 崩溃位置                   | 恢复方式                                                       |
| -------------------------- | -------------------------------------------------------------- |
| Skill Pipeline 节点内      | 从最后 checkpoint 确定性恢复                                   |
| Worker Free Loop 内        | 重启 Loop，将 Event 历史注入为上下文，LLM 自主判断续接点       |
| Orchestrator Free Loop 内  | 重启 Loop，扫描未完成 Task，重新派发或等待人工确认             |

---

### 8.4 Skills（Pydantic AI）设计

#### 8.4.1 Skill 模板

```yaml
SkillSpec:
  name: "string"
  version: "semver"
  risk_level: low|medium|high
  input_model: "PydanticModel"
  output_model: "PydanticModel"
  permission_mode: inherit|restrict
  tools_allowed:
    - tool_id                 # 可选收窄器；只有 restrict 模式强制生效
  model_alias: "planner"          # LiteLLM alias（见 §8.9.1）
  timeout_s: 300                  # Skill 级超时
  tool_policy: sequential|parallel|mixed
  tool_profile: standard          # trusted local 默认基线；实际执行仍受 policy 上限控制
  retry_policy:
    max_attempts: 3
    backoff_ms: 500
    upgrade_model_on_fail: true
  approval_policy:
    mode: none|rule_based|human_in_loop
```

#### 8.4.2 Skill 运行语义（必须一致）

1. 校验输入（InputModel）
2. 调用模型（通过 LiteLLM alias）
3. 解析并校验输出（OutputModel）
4. 若输出包含 tool_calls：
   - 校验工具参数 schema
   - Policy Engine 判定 allow/ask/deny
   - allow → 执行；ask → 进入审批；deny → 返回错误并可重试
5. 工具结果回灌模型（结构化）
6. 输出最终结果（校验 + 产物）

#### 8.4.3 SkillRunner 演进方向（竞品源码深度分析）

> Agent Zero / OpenClaw 源码深度分析的关键发现，指导 Feature 005 SkillRunner 设计。

##### 循环控制与终止

Agent Zero 使用双层循环（外层 monologue loop + 内层 message_loop），工具可通过返回 `Response(break_loop=True)` 终止内层循环。OctoAgent SkillRunner 应借鉴此模式：

- OutputModel 增加 `complete: bool` 字段，Skill 判定任务完成时主动通知 SkillRunner 停止迭代
- 重复调用检测：hash 每轮 tool_calls 签名，连续 3 次相同签名触发告警并终止（参考 OpenClaw 4 型循环检测）

##### 异常分流

Agent Zero 使用三层异常处理：InterventionException（暂停等审批）→ RepairableException（重试修复）→ Generic（报告失败）。SkillRunner 应实现类似分流：

- `SkillRepeatError`：可重试（如 LLM 输出格式不符，自动重试含错误反馈）
- `SkillValidationError`：需修复输入后重试（参考 OpenClaw `ToolInputError` 即时通知 LLM）
- `ToolExecutionError`：不可恢复，记录并报告

##### 生命周期钩子

Agent Zero 提供 15+ Extension hook 点（monologue_start/end、message_loop_start/end、before/after_llm_call、tool_execute_before/after 等）。SkillRunner 应在关键点提供钩子：

- `skill_start` / `skill_end`：Skill 级可观测
- `before_llm_call` / `after_llm_call`：模型调用拦截
- `before_tool_execute` / `after_tool_execute`：工具执行拦截（与 ToolBroker Hook Chain 协作）

##### Context Budget Guard

OpenClaw 的 tool-result-context-guard 在工具返回结果超出 context 预算时自动截断。SkillRunner 应在工具结果回灌前检查 context 预算，超限时使用 artifact 路径引用替代全文。

---

### 8.5 Tooling：工具契约 + 动态注入 + 安全门禁

#### 8.5.1 工具分级（必须）

- Read-only：检索、查询、读取日历/邮件、读取配置
- Write-but-reversible：写草稿、创建临时记录、生成建议但不提交
- Irreversible / High-risk：发邮件、发送消息、支付、写生产配置、删除数据

#### 8.5.2 工具元数据（Tool Metadata）

```yaml
ToolMeta:
  tool_id: "namespace.name"
  version: "hash or semver"
  side_effect: none|reversible|irreversible
  timeout_s: 30
  tool_group: "filesystem|terminal|web|memory|..."
  tier: CORE|DEFERRED
  tags:
    - "read"
    - "local"
  outputs:
    max_inline_chars: 500
    store_full_as_artifact: true
```

#### 8.5.3 Tool Index（MVP）

- 向量数据库（LanceDB）：embedding 索引 tool 描述 + 参数 + tags + examples
- Orchestrator 在运行时检索：
  - 语义相似度匹配候选工具集合（Top-K）
  - 再由 Policy Engine 过滤
  - 最终注入到 Skill 的可用工具列表（减少工具膨胀）

#### 8.5.4 权限 Preset 分级（Feature 070 简化重构后）

工具访问控制由 `PermissionPreset`（Agent 实例级）× `SideEffectLevel`（工具声明级）矩阵决定：

| Preset \ SideEffect | NONE | REVERSIBLE | IRREVERSIBLE |
|---------------------|------|------------|--------------|
| MINIMAL | ALLOW | ASK | ASK |
| NORMAL | ALLOW | ALLOW | ASK |
| FULL | ALLOW | ALLOW | ALLOW |

- Butler 默认 FULL，Worker 默认 NORMAL，Subagent 继承 Worker
- 没有硬 DENY——所有 ASK 场景都可通过用户审批临时提升（Constitution 原则 7）
- 权限检查由 `tooling.permission.check_permission()` 单函数内联执行，不使用 Hook Chain
- 路径访问由 `tooling.path_policy.PathAccessPolicy` 白名单/黑名单/灰名单强制拦截

#### 8.5.5 工具输出压缩（Context GC）

规则（建议默认）：

- 工具输出 > `max_inline_chars`（默认 500）字符：
  - 全量输出存 artifact
  - **M1 Phase 1**（Feature 004）：裁切后保留 artifact 路径引用在上下文中（参考 AgentZero `_90_save_tool_call_file.py`，零 LLM 依赖）
  - **M1 Phase 2**（Feature 005 就绪后）：可选启用 summarizer（通过 cheap alias 生成摘要回灌上下文）
- 工具输出含敏感信息：
  - 自动 redaction（屏蔽）
  - 存入 Vault 分区（需要授权检索）

#### 8.5.6 后续演进方向（Feature 004 对标洞见）

> Feature 004 交付后，与 Agent Zero、OpenClaw 工具系统对标分析的关键发现。以下能力按优先级排列，标注目标 Feature。

##### 交互式工具执行（M2, Feature 009）

Agent Zero 支持 Shell Session 保持 + 增量输出 + 提示符检测（`LocalInteractiveSession` / `SSHInteractiveSession`），工具可以多轮交互。当前 ToolBroker 为"一进一出"模型（`execute() → ToolResult`），不支持多轮。

- 演进方向：ToolResult 增加 `continuation_token` 字段 + Broker 支持 `resume(token, input)` 方法
- 参考：Agent Zero `python/helpers/docker.py`、`code_execution_tool.py`

##### 工具循环检测（M1.5, Feature 004-b 增强）

OpenClaw 通过 bucket strategy 检测工具被反复调用的异常模式（LLM 陷入死循环）。当前 ToolBroker 无此防护。

- 演进方向：作为 BeforeHook 实现 `ToolLoopGuard`，per-task 维度计数，超阈值生成 WARNING 事件
- 参考：OpenClaw `src/agents/pi-tools.before-tool-call.ts`

##### 细粒度超时分级（M1.5, Feature 004-b 增强）

Agent Zero 对代码执行工具使用 4 层超时：`first_output`（30s）/ `between_output`（15s）/ `max_exec`（180s）/ `dialog_timeout`（60s）。当前 ToolMeta 仅有单一 `timeout_seconds`。

- 演进方向：ToolMeta 增加 `timeout_config: TimeoutConfig` 嵌套对象，按阶段拆分超时
- 适用场景：Docker 执行、SSH 远程命令等长时工具

##### MCP 工具集成（M1, Feature 007）

Agent Zero 已原生支持 MCP 协议（stdio + SSE），可发现和调用外部 MCP Server 暴露的工具。

- 演进方向：MCP tools 默认注册为 `standard` Profile（可通过配置覆盖为 `privileged`），区分 Tool vs Resource
- 参考：Agent Zero `python/helpers/mcp_handler.py`、`prompts/agent.system.mcp_tools.md`

##### 插件加载隔离 + 诊断（M1.5）

OpenClaw 单个插件加载失败不影响其他插件和核心工具，失败信息记录到 `registry.diagnostics[]`。当前 Broker 注册失败会抛异常。

- 演进方向：`ToolBroker.register()` 增加 `try_register()` 变体，失败工具进 `_diagnostics` 列表
- 参考：OpenClaw `src/plugins/loader.ts`

##### 敏感参数标记（M2, UI 集成）

OpenClaw 的 `ChannelConfigUiHint` 支持 `sensitive: true` 标记，UI 自动隐藏输入。

- 演进方向：ToolMeta 增加 `sensitive_params: list[str]` 字段，Web UI 渲染时自动遮蔽

##### 工具输出截断策略（已实现）

参考 OpenClaw `tool-result-truncation.ts` + Agent Zero 的分层策略，工具输出管理分为两层：

- **工具层**：返回尽量完整的内容。`filesystem.read_text` 默认 100K、`terminal.exec` 默认 200K、`web.fetch` / `browser.snapshot` 默认 100K。上限 500K（安全保底，防极端输出撑爆内存）。
- **LargeOutputHandler**（after hook）：按上下文窗口动态计算截断阈值（`context_window_tokens × 50% × 4`，128K 上下文 → 256K 字符）。超阈值时采用 **Head + Tail 智能截断**——检测尾部是否有 error/summary 关键信息，有则保留头尾，无则只保留头。截断标记引导 LLM 使用 offset/limit 参数分段重读。完整内容存入 ArtifactStore 用于审计。

对比：OpenClaw 单工具结果占上下文 30%（硬上限 400K），Agent Zero 工具返回全量（代码执行 1M）、上下文压缩在历史管理层处理。

##### 文件系统路径访问策略（已对齐 PathAccessPolicy）

文件系统工具的路径访问由 `PathAccessPolicy`（`tooling/path_policy.py`）强制拦截：
- **白名单**：当前 project 目录 + behavior/ + skills/ → 自动放行
- **黑名单**：app/（源码）+ data/（DB）+ .env*（密钥）+ 配置文件 + bin/ + 跨 project → 直接拒绝
- **灰名单**：instance root 外路径 → `check_permission()` 升级为 IRREVERSIBLE，触发 ASK 审批

`terminal.exec` 的 cwd 受同样策略保护。命令级过滤留给 Docker sandbox（M2+）。

OctoAgent 的双维度审批模型与 OpenClaw 完全语义对齐（2026-03-21 确认）：

| OctoAgent PolicyAction | OpenClaw Security | 语义 |
|----------------------|------------------|------|
| `ALLOW` | `full` | 不限制，workspace 外路径也放行，不额外审批 |
| `ASK` | `allowlist` | 不在白名单则弹审批 |
| `DENY` | `deny` | 直接拒绝 |

| OctoAgent ApprovalDecision | OpenClaw Decision | 语义 |
|--------------------------|------------------|------|
| `ALLOW_ONCE` | `allow-once` | 一次性放行 |
| `ALLOW_ALWAYS` | `allow-always` | 加入白名单永久放行 |
| `DENY` | `deny` | 拒绝 |

---

### 8.6 Policy Engine：allow/ask/deny + 审批工作流

#### 8.6.1 最小策略模型

- 输入：tool_call / action_plan / task_meta / user_context
- 输出：Decision
  - allow（自动执行）
  - ask（请求审批）
  - deny（拒绝并解释原因）

#### 8.6.2 默认策略（建议）

- irreversible 工具：默认 ask
- reversible 工具：默认 allow，但可按 project 提升为 ask
- read-only：默认 allow
- 任何涉及外部发送/支付/删除：默认 ask（需要策略白名单或显式审批才可 silent allow）

**策略可配原则（与 Constitution 原则 7 对齐）：**
- 所有门禁 safe by default，但用户可通过 PermissionPreset + ApprovalOverride 调整
- 对用户已明确授权的场景（如定时任务、低风险工具链），自动批准以减少打扰
- 策略变更本身是事件，可审计可回滚

#### 8.6.3 审批交互：Two-Phase Approval（M1）

> 参考实现：OpenClaw `exec-approvals.ts` 的 register → wait 双阶段模式。

**Two-Phase 设计**（防并发竞态）：

```python
# Phase 1: 注册审批请求（立即返回 approval_id，防止竞态）
approval = await approval_service.register(
    task_id=..., tool_call=..., risk_explanation=...
)  # → { approval_id, expires_at }

# Phase 2: 阻塞等待用户决策
decision = await approval_service.wait_for_decision(
    approval_id, timeout_s=120
)  # → allow / deny
```

- 分离"注册请求"与"等待决策"两个操作，避免同一审批被重复处理
- 超时默认策略：`deny`（参考 OpenClaw `DEFAULT_ASK_FALLBACK = "deny"`，120s）
- 用户可配置超时后 escalate（通知 Owner）而非直接 deny

**审批状态流转**：

- 触发 ask：
  - 写入 APPROVAL_REQUESTED 事件（含 approval_id）
  - task 状态进入 WAITING_APPROVAL
- 用户批准：
  - 写入 APPROVED 事件
  - task 状态回到 RUNNING，Skill Pipeline 从 gate 节点继续
- 用户拒绝：
  - 写入 REJECTED 事件
  - task 进入终态
- 超时：
  - 按配置执行 deny 或 escalate

审批载荷（建议）：

- action summary
- risk explanation
- idempotency_key
- dry_run 结果（若有）
- rollback/compensation 提示

#### 8.6.4 权限决策架构（Feature 070 简化后）

权限决策由 `check_permission()` 单函数完成四步短路：
1. always 覆盖快速路径（ApprovalOverrideCache）
2. PermissionPreset × SideEffectLevel 矩阵查表
3. ASK → ApprovalManager.register() + wait_for_decision()
4. 审批结果 → 放行 / 拒绝

不再使用 PolicyEngine / PolicyPipeline / PolicyCheckHook / PresetBeforeHook 等多层体系。

#### 8.6.5 权限系统演进方向（竞品源码深度分析）

> OpenClaw / AgentStudio / Agent Zero 源码深度分析的关键发现，指导 Feature 006 设计。

##### Label 决策追踪（OpenClaw，高优先级）

OpenClaw 的 Policy Pipeline 每层决策附带 label（如 `{ decision: "allow", label: "workspace:allowlist" }`），完整追溯决策来源。OctoAgent 的 check_permission() 每个 Decision 必须包含 label 字段，记录是哪一层、哪条规则产生了该决策。这是审计合规的基础。

##### consumeAllowOnce 原子审批消费（OpenClaw，高优先级）

OpenClaw `exec-approval-manager.ts` 实现原子性一次性审批令牌消费，防止同一审批被重放。配合 15s 宽限期（审批通过后保留 15s，允许迟到的 await 调用找到已解决条目）和幂等注册（同 ID 不重复添加到审批队列）。OctoAgent 直接采用此模式，但审批状态必须持久化到 Event Store（Agent Zero 仅内存存储是反模式）。

##### Provider Pipeline + Fast-Fail（AgentStudio，中优先级）

AgentStudio Pre-Send Guard 的 Provider Pipeline 支持 allow/block/rewrite/require_confirm 四种决策。借鉴点：

- 可插拔 Provider 链式评估
- block 决策立即短路返回（AgentStudio 未实现此 fast-fail，是反模式）
- 每个 Provider 声明 `failureStrategy`：`block_on_failure`（强制型）vs `continue_on_failure`（建议型）

##### 前端审批 UX（OpenClaw，中优先级）

OpenClaw 前端提供三按钮决策（Allow Once / Always Allow / Deny）+ 队列 Badge（`"3 pending"`）+ 独立过期倒计时。M1 Approvals 面板直接采用此 UX 模式；Telegram 渠道通过 inline keyboard 实现等价交互。

##### 必须避免的反模式

1. **审批状态非持久化**（Agent Zero）— 进程重启丢失所有审批状态 → 必须写入 Event Store
2. **轮询等待**（Agent Zero `asyncio.sleep(0.1)`）— CPU 浪费 → 使用 `asyncio.Event` + SSE 事件驱动
3. **枚举值部分实现**（AgentStudio `require_confirm`）— 定义了 4 种决策但只实现 3 种 → Python 用 exhaustive match + `assert_never()` 确保全覆盖

---

### 8.7 Memory：SoR/Fragments/Vault + 写入仲裁

#### 8.7.1 两条记忆线

- Fragments（事件线）：append-only；保存对话/工具执行/聊天窗口摘要；用于证据与回放
- SoR（权威线）：同一 subject_key 只有一个 current；旧版 superseded

**默认回答策略：**
- 问"现在是什么" → 只查 SoR.current
- 问"为什么/过程" → SoR + Fragments + superseded 版本（可选）

#### 8.7.2 六大分区（建议）

- `core`：系统运行信息（tasks、incidents、configs）
- `profile`：用户偏好/长期事实（非敏感）
- `work`：工作项目与知识（可更新）
- `health`：健康相关（敏感，默认 Vault）
- `finance`：财务相关（敏感，默认 Vault）
- `chat:<channel>:<thread_id>`：聊天 scope（可维护群规/约定/项目状态）

术语约束（2026-03-08）：
- `profile` 在本节仅指记忆分区名，不等同于 tool profile、auth profile、agent profile
- 其余设计文档必须显式写出 `tool profile`、`auth profile`、`agent profile`、`readiness level`，避免裸 `profile` 歧义

补充约束（2026-03-07）：
- `partition/scope` 与 `layer` 分离建模：`partition` 表示业务域（如 `work` / `health`），`layer` 表示记忆层（SoR / Fragments / Vault）
- 敏感分区可以保留安全摘要到 SoR，但原始敏感内容默认只留在 Vault 引用路径，不参与普通检索

#### 8.7.3 写入治理：两阶段仲裁

- 阶段 A（cheap 模型）：提出 WriteProposal
- 阶段 B（规则 + 可选强模型）：校验合法性/冲突/证据存在性 → commit

WriteProposal 示例：

```yaml
WriteProposal:
  action: ADD|UPDATE|DELETE|NONE
  subject_key: "work.projectX.status"
  partition: "work"
  new_value: { ... }
  rationale: "..."
  evidence_refs:
    - fragment_id
    - artifact_id
  confidence: 0.0-1.0
```

服务接口（M2 Feature 020 冻结 → 2026-04-05 架构整治后更新）：

**MemoryService（Facade）**——组合以下子服务，公共方法签名保持向后兼容：
- `MemoryWriteService`：`propose_write()` / `validate_proposal()` / `commit_memory()` / `fast_commit()`（低风险快速路径）
- `MemoryRecallService`：`search_memory()` / `recall_memory()`（多 scope 并行 asyncio.gather + hooks pipeline）
- `VaultAccessService`：vault 授权全生命周期（request / resolve / grant / audit）
- `MemoryBackendManager`：backend 健康管理（sync / probe / degrade / recover）

快速写入路径（`fast_commit`）：`confidence >= 0.75` + `action == ADD` + 非敏感分区时跳过 validate 查询，减少 1/3 DB 写入。proposal 仍落盘保证审计轨迹。

`MemoryBackend`（engine protocol）：`is_available` / `get_status` / `search` / `sync_batch` / `ingest_batch` / `list_derivations` / `resolve_evidence` / `run_maintenance`


#### 8.7.4 语义检索集成（LanceDB）

- MemoryItem 的 embedding 存入 LanceDB（与 SQLite 元信息分离）
- 检索时：vector 相似度 + metadata filter（partition / scope_id / status）
- SoR 查询：先 metadata filter `status=current`，再 vector 排序
- Fragments 查询：vector 检索 + 时间范围过滤
- 写入时：WriteProposal commit 成功后，异步更新 LanceDB embedding

当前 Memory backend（BuiltinMemUBridge，2026-04-05 更新）：
- 主 backend：BuiltinMemUBridge（LanceDB hybrid search：Qwen3-Embedding-0.6B 向量 + jieba BM25 FTS）
- 降级路径：Qwen3 embedding 不可用时自动降级到 BM25 FTS-only；LanceDB 不可用时降级到 SQLite 直查
- 写入管道：SessionMemoryExtractor（Session 驱动，cursor-based 增量提取）→ LLM 结构化输出（enable_thinking=false）→ `fast_commit` / `propose-validate-commit`
- Recall：多 scope 并行查询（asyncio.gather）+ 5 级 hooks pipeline（keyword overlap → heuristic/model rerank → temporal decay → MMR dedup）
- 可观测：`memory_recall_completed` 结构化日志（latency_ms / scope_hit_distribution / candidate→delivered 漏斗）
- governance 由 SQLite + WriteProposal 仲裁控制
- 多模态记忆、Category、ToM、关系抽取等高级能力只能产出 `Fragments`、派生索引或 `WriteProposal` 草案，不能绕过 SoR / Vault 直接成为权威事实
- 所有高级记忆结果都必须带 `evidence_refs` / artifact 引用，确保 Memory 浏览与证据追溯可落地

> **历史记录**：2026-03-17 移除了 `MemUBackend`（HTTP/Command bridge）实现。2026-04-05 架构整治：MemoryService（2260行）拆分为 4 子服务 + Facade（680行）；MemoryConsoleService（1685行）拆分为 5 模块；models/integration.py 拆分为 7 域文件；SqliteMemoryStore 提取通用 _row_to_model 和 _build_filtered_query helpers；workspace_id 从 memory 包全面移除。

2026-03-13 运行时上下文纠偏（参考 Agent Zero Projects / OpenClaw session-key + compaction）：
- `Memory` 与 `Recall` 必须分离建模：Memory 回答"长期保留了什么"，Recall 回答"当前问题该取回什么"
- 每个 Agent 必须拥有自己的私有 `MemoryNamespace` 与 `AgentSession`；`Project` 只提供共享上下文与共享记忆，不替代 Agent 私有上下文
- Butler 默认只读取 `ButlerSession + ButlerMemory + ProjectMemory + child result summary`
- Worker 默认只读取 `WorkerSession + WorkerMemory + 被授权的 ProjectMemory + 当前 Work evidence`
- Worker 默认**不得直接读取完整用户主聊天历史**；Butler 必须通过 A2A payload / context capsule 选择性转述
- 高级 Memory backend 的索引维度必须至少覆盖 `namespace_id + agent_id + session_id + partition + scope_id`，不能只按 project/thread 粗暴混用
- Recall pipeline 必须显式可观测，默认顺序为：`session recency -> agent private memory -> project shared memory -> work evidence -> explicit knowledge`

#### 8.7.5 Chat Import Core（通用内核）

- 用户入口：`octo import chats [--dry-run] [--resume]`
- thread/scope 隔离：`scope_id=chat:<channel>:<thread_id>`
- 增量去重：`msg_key = hash(sender + timestamp + normalized_text)` 或原 msg_id
- 导入报告：每次执行都返回 `ImportReport`（新增数 / 重复数 / warnings / errors / cursor）
- 窗口化摘要：
  - chatlogs：原文可审计
  - fragments：可检索摘要片段
- 可选：实体提取与关系索引
- 可选：在 chat scope 内更新 SoR（群规/约定/项目状态）

说明：上下文压缩 / auto-compaction 不属于 Memory Core 本体；Memory 仅提供 `_before_compaction_flush()`（private 方法）钩子承接 cheap/summarizer 模型产出的摘要与 WriteProposal 草案。记忆提取的主入口已由 Feature 067 的 SessionMemoryExtractor 承接，不再依赖 compaction flush 路径。

Feature 034（2026-03-09，M4 hardening）补充约束：

- 上下文压缩必须落在主 Agent / Worker 的真实 prompt assembly 路径，不能只做离线 helper
- Subagent 默认不接入上下文压缩，避免 delegation 运行链双重压缩
- compaction 必须产生 request snapshot artifact、summary artifact 与结构化事件，并通过 maintenance/flush evidence 链接入 Memory
- summarizer 不可用时必须优雅降级到原始历史，不能静默丢轮次或让主模型调用一起失败

---

### 8.8 Execution Plane：Worker + JobRunner + Sandboxing

#### 8.8.1 Worker 责任边界

**Worker 是自治智能体**，以 Free Loop（LLM 驱动循环）运行，自主决策下一步行动。

Worker 不负责：
- 多渠道 I/O（由 Gateway 负责）
- 全局策略决策（由 Kernel Policy 负责）
- 全局路由与监督（由 Orchestrator 负责）

Worker 负责：

- 以 Free Loop 自主执行任务
- 决策何时调用单个 Skill、Skill Pipeline（Graph）、或 Tool
- 维护 project workspace
- 产出 artifact
- 回传事件与心跳

#### 8.8.2 JobRunner 接口（概念）

```python
class JobRunner(Protocol):
    async def start(self, job_spec) -> str: ...
    async def status(self, job_id) -> dict: ...
    async def stream_logs(self, job_id, cursor=None): ...
    async def cancel(self, job_id) -> None: ...
    async def collect_artifacts(self, job_id) -> list[Artifact]: ...
```

backend：
- local_docker：默认
- ssh：控制 LAN 设备
- remote_gpu：跑大模型/训练/批处理（可选）

#### 8.8.3 Sandboxing 策略

- 默认 Docker：
  - 非 root
  - 网络默认禁用
  - 只挂载白名单目录
- 需要网络的任务：
  - 通过策略显式开启（并记录事件）
- 对宿主机操作：
  - 必须通过专用 tool，并默认 ask（除非白名单）

---

### 8.9 Provider Plane：LiteLLM alias 策略

#### 8.9.1 语义 alias（业务侧）

- `router`：意图分类、风险分级（小模型）
- `extractor`：结构化抽取（小/中模型）
- `planner`：多约束规划（大模型）
- `executor`：高风险执行前确认（大模型，稳定优先）
- `summarizer`：摘要/压缩（小模型）
- `fallback`：备用 provider

#### 8.9.2 运行时 alias（Proxy 侧）

- `octoagent.yaml.model_aliases` 是运行时 alias 的主事实源；LiteLLM `model_name` 与 Gateway runtime 必须直接对齐这些 alias key
- `cheap` / `main` 仍是默认建议 alias，但不再是唯一允许的运行时组名
- `router/extractor/planner/executor/summarizer/fallback` 只保留为 legacy 语义 alias 兼容层；若用户显式配置了同名 alias，必须优先使用显式配置值
- `AliasRegistry` 的职责是"显式配置 alias 优先 + legacy 语义 alias fallback"，避免配置层、运行时层、Proxy 层出现三套事实源

#### 8.9.3 统一成本治理

- 每次模型调用写入事件：
  - model_alias、provider、latency、tokens、cost
- per-task 预算阈值触发策略：
  - 超预算 → 降级到 cheap 模型 / 提示用户 / 暂停等待确认

#### 8.9.4 多 Provider 扩展与 Auth Adapter（M1）

> 目标：新增 Provider 时**零代码变更**（仅修改 `octoagent.yaml`，再自动生成 `litellm-config.yaml`），同时支持非标准认证模式。

**当前架构**（M0/M1）：

- 业务代码通过语义 alias（`router`/`planner`/...）调用 → AliasRegistry 映射到运行时 group（`cheap`/`main`/`fallback`）→ LiteLLM Proxy 路由到真实模型
- 新增 Provider 只需在 `octoagent.yaml` 的 `providers[]` 中追加条目，需要自定义网关时填写 `base_url`；`litellm-config.yaml` 由系统自动推导生成，支持 100+ Provider（OpenAI、Anthropic、OpenRouter、Azure、Google、本地 Ollama 等）
- 示例：从 OpenAI 切换到 OpenRouter 仅需修改 `model` 前缀和 `api_key` 环境变量名

**Auth Adapter 抽象层**（M1 基础 + M1.5 增强）：

> 参考实现：OpenClaw（`_references/opensource/openclaw/src/agents/auth-profiles/`）
> 已验证支持 OpenAI（API Key + Codex OAuth）、Anthropic（Setup Token + API Key）、Google、GitHub Copilot 等。

不同 Provider 存在多种认证模式，需要统一抽象：

| 认证模式 | 代表 Provider | 说明 | OpenClaw 参考 |
|----------|--------------|------|--------------|
| 标准 API Key | OpenAI / Anthropic / OpenRouter | `Authorization: Bearer sk-xxx`，LiteLLM 原生支持 | `ApiKeyCredential` type |
| Setup Token | Anthropic Claude Code | `sk-ant-oat01-...` 格式，有过期时间，需 `claude setup-token` 生成 | `TokenCredential` type |
| OAuth / Device Flow | OpenAI Codex / Google Gemini CLI | 需要 token 刷新、设备授权流程 | `OAuthCredential` type + pi-ai 库 |
| 平台托管 | Azure OpenAI / GCP Vertex AI | 需要 Azure AD token 或 GCP service account | LiteLLM Proxy 内置支持 |
| 本地部署 | Ollama / vLLM / LocalAI | 无认证或自定义 header | 直接配置 `api_base` |

**设计方向**（参考 OpenClaw 双层架构，M1 实现）：

1. **Config / Credential 分离**：
   - Config 层（`.env` 或 `octoagent.yaml`）：声明 auth profile 元数据（provider, mode）
   - Credential 层（`auth-profiles.json`，`.gitignore` 保护）：存储实际凭证
   - 对齐 Constitution C5（Least Privilege）：凭证与配置物理隔离

2. **三种凭证类型**（对齐 OpenClaw 模型）：

   ```python
   # packages/provider/auth/credentials.py
   class ApiKeyCredential(BaseModel):
       type: Literal["api_key"] = "api_key"
       provider: str
       key: SecretStr

   class TokenCredential(BaseModel):
       type: Literal["token"] = "token"
       provider: str
       token: SecretStr
       expires_at: datetime | None = None  # Setup Token 有过期时间

   class OAuthCredential(BaseModel):
       type: Literal["oauth"] = "oauth"
       provider: str
       access_token: SecretStr
       refresh_token: SecretStr
       expires_at: datetime
   ```

3. **AuthAdapter 接口**（`packages/provider/auth/adapter.py`）：

   ```python
   class AuthAdapter(ABC):
       @abstractmethod
       async def resolve(self) -> str:
           """返回可用的 API key / access token"""
       @abstractmethod
       async def refresh(self) -> str | None:
           """刷新凭证，返回新 token；不支持刷新返回 None"""
       @abstractmethod
       def is_expired(self) -> bool:
           """检查凭证是否过期"""
   ```

4. **Handler Chain 模式**（参考 OpenClaw `applyAuthChoice`）：
   - 每个 Provider 一个 handler，Chain of Responsibility 依次匹配
   - `octo init`（历史路径）/ `octo config`（当前路径）引导时调用对应 handler 完成认证配置
   - 运行时解析优先级：显式 profile → auth-profiles.json → 环境变量 → 默认值

5. **Token 自动刷新**（参考 OpenClaw `refreshOAuthTokenWithLock`，M1 实现 Setup Token 过期检测，M1.5 实现完整 OAuth 刷新）：
   - 每次 LLM 调用前检查 `expires_at`
   - 过期时获取文件锁 → 调用 `adapter.refresh()` → 持久化新凭证
   - 刷新失败时降级到 fallback Provider（对齐 C6）
   - LiteLLM Proxy 已内置 Azure AD / Vertex AI 刷新，优先复用 Proxy 能力
   - 仅当 Proxy 不支持的认证模式（如 Codex Device Flow、Anthropic Setup Token）才在应用层实现

6. **凭证注入到 LiteLLM Proxy**：
   - API Key 类型：写入 `.env.litellm` 环境变量
   - OAuth/Token 类型：动态更新 Proxy 配置（LiteLLM Proxy 支持 `/model/update` API）
   - 或通过 `litellm-config.yaml` 的 `get_key_from_env` 配合环境变量刷新

7. **OAuth Authorization Code + PKCE 流程**（Feature 003-b，M1.5 已交付）：
   - 支持 Authorization Code + PKCE（RFC 7636）标准流程，取代纯 Device Flow
   - `OAuthProviderRegistry` 注册表管理多 Provider 的 OAuth 配置（内置 openai-codex + github-copilot）
   - `PkceOAuthAdapter` 适配器继承 `AuthAdapter`，实现 PKCE 流程编排
   - 环境检测（SSH/容器/无 GUI）自动选择浏览器 / 手动粘贴模式
   - 本地回调服务器（`127.0.0.1:1455`）接收授权码，端口冲突自动降级到手动模式
   - JWT access_token 直连 ChatGPT Backend API（Codex Responses API），不经过 LiteLLM Proxy

8. **多认证路由隔离**（Feature 003-b 集成阶段发现）：
   - JWT OAuth 路径需绕过 LiteLLM Proxy 直连 `chatgpt.com/backend-api`，API Key 路径继续走 Proxy
   - `HandlerChainResult` 扩展 `api_base_url: str | None` 和 `extra_headers: dict[str, str]` 字段
   - `LiteLLMClient.complete()` 新增 `api_base`、`api_key`、`extra_headers` keyword-only 参数，支持按调用覆盖路由
   - `PkceOAuthAdapter` 通过 `get_api_base_url()` / `get_extra_headers()` 提供路由覆盖信息

9. **Codex Reasoning/Thinking 模式**（Feature 003-b 增量能力）：
   - `ReasoningConfig` 模型：`effort`（none/low/medium/high/xhigh）+ `summary`（auto/concise/detailed）
   - `LiteLLMClient.complete()` 新增 `reasoning: ReasoningConfig | None` 参数
   - 双路径适配：Responses API 使用嵌套 `reasoning` 对象，Chat Completions API 使用顶层 `reasoning_effort` 字符串

**扩展原则**：

- 业务代码（Kernel/Worker/Skill）永远不感知具体 Provider 或认证方式
- Provider 变更的用户主入口限定在 `octoagent.yaml`；`litellm-config.yaml` 只是衍生文件，`.env.litellm` 负责承载运行时凭证
- Auth Adapter 变更的影响范围限定在 `packages/provider/auth/`
- 新增 Provider 只需实现对应 `AuthAdapter` 子类 + 注册到 Handler Chain
- JWT 直连路径通过 HandlerChainResult 路由覆盖实现，不影响 API Key 路径的默认行为

M3 用户化约束（2026-03-07）：
- 环境变量继续作为高级/CI 路径保留，但不应再是普通用户完成 provider/channel/gateway 配置的默认方式
- 用户视角的主路径应收敛为统一 Secret Store + Wizard：Provider Key、OAuth Token、Telegram Bot Token、Gateway Token 等通过同一配置入口管理、审计、轮换与 reload
- CLI / Web / 未来桌面端应共用 onboarding + config protocol，避免重复实现两套配置逻辑
- secret 配置的默认目标应从"让用户记住要 export 哪些 env"转为"告诉用户存到哪个 project/scope，系统负责注入运行时"

---
