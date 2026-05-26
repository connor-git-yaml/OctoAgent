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

#### 8.5.7 Harness Layer（Feature 084 引入）

Harness 是工具与执行的**硬约束基础设施层**，从 capability_pack hardcoded dict 切换为数据驱动的中央 ToolRegistry + Toolset + Threat + Snapshot + Approval + Delegation 六组件协同体系。仿 Hermes Agent 模式：**Live State 与冻结快照二分，保护 prefix cache**（参考 Constitution 原则 8 Observability is a Feature + 原则 10 Policy-Driven Access）。

实现位置：`octoagent/apps/gateway/src/octoagent/gateway/harness/`（详见 `docs/codebase-architecture/harness-and-context.md`）。

**§8.5.7.1 ToolRegistry**（`tool_registry.py`）

中央 `ToolEntry` 注册表，**数据驱动 entrypoints 可见性**（取代旧 capability_pack 内 dict）：

| 字段 | 用途 |
|------|------|
| `name` | 工具唯一标识 |
| `entrypoints: frozenset[str]` | 工具可见入口（`web` / `agent_runtime` / `telegram`）|
| `handler` | 异步可调用 |
| `metadata: dict` | 含 `produces_write` 标记（驱动 §8.5.7.7 WriteResult 契约） |

注册期 hooks：
1. **AST 扫描自动发现**：`scan_and_register(registry, builtin_tools_path)` 启动时执行
2. **`func._tool_meta` 同步**：`register(entry)` 自动从 handler 同步 `produces_write` 等元数据
3. **WriteResult 契约 enforce**：`produces_write=True` 工具的 return type 必须是 `WriteResult` 子类（fail-fast 启动期）

**§8.5.7.2 ToolsetResolver**（`toolset_resolver.py`）

按 Worker / Subagent kind 解析可用工具集；与 capability_pack 协同（capability_pack 仅提供 capability 抽象，可见性由 ToolRegistry 数据驱动）。

**§8.5.7.3 ThreatScanner**（`threat_scanner.py`，Hermes pattern table 模式）

| 检测 | 实现 |
|------|------|
| Prompt Injection / Role Hijacking / Exfiltration / SSH backdoor / base64 payload | `_MEMORY_THREAT_PATTERNS` ≥17 条正则 |
| Invisible Unicode 字符（U+200B / U+200C / U+200D / ZWNBSP）| `_INVISIBLE_CHARS` frozenset O(n) 遍历 |

每条 pattern 含 `pattern_id` + `severity`（WARN / BLOCK）。BLOCK 命中由 PolicyGate 统一拦截，写 `MEMORY_ENTRY_BLOCKED` 事件（不含原始恶意内容，对齐 Constitution 原则 5 Least Privilege）。

**§8.5.7.4 SnapshotStore**（`snapshot_store.py`，Hermes 核心模式 — 冻结快照 + Live State 二分）

| 字段 | 语义 |
|------|------|
| `_system_prompt_snapshot: dict[str, str]` | session 启动时冻结，整个 session 不变（system prompt 注入路径读这里）|
| `_live_state: dict[str, str]` | 随每次写入更新（`user_profile.read` 路径读这里）|
| `_file_mtimes: dict[Path, float]` | 启动时记录，会话结束时 diff（漂移则 WARN 日志）|
| `_locks: dict[Path, asyncio.Lock]` | per-file async lock，配合 `fcntl.flock` 防 read-modify-write 竞态 |

关键 API：
- `load_snapshot(session_id, files)`：会话启动时冻结
- `format_for_system_prompt() → dict[str, str]`：返回**冻结副本**（不变，保护 prefix cache）
- `get_live_state(key) → str`：返回当前 live state（变，最新值）
- `write_through(file_path, new_content)`：`fcntl.flock` + tempfile + `os.replace` 原子写
- `append_entry(file_path, new_entry, char_limit)`：read+append+limit+write 全程持锁（防 concurrent 数据丢失）
- `persist_snapshot_record(tool_call_id, result_summary)`：每次工具调用回显落库（`snapshot_records` 表，TTL 30 天）

**§8.5.7.5 ApprovalGate**（`approval_gate.py`）

详细行为见 §8.6.6（与 Policy Engine 章节深度绑定）。

**§8.5.7.6 DelegationManager**（`delegation.py`）

| 约束 | 阈值 |
|------|------|
| `MAX_DEPTH` | 2（防无限递归 sub-agent）|
| `MAX_CONCURRENT_CHILDREN` | 3（防并发爆炸）|
| Worker blacklist | 默认空，可配 |

通过约束 → 写 `SUBAGENT_SPAWNED` 事件 + 调 `launch_child` 真实派发（与 `subagents.spawn` 同路径）。失败不写假事件。Feature 092 把 DelegationManager 收敛为 spawn 唯一编排入口（详见 §9 module-design + architecture-audit §14.10）。

**§8.5.7.7 WriteResult 通用回显契约**

所有 `produces_write=True` 工具（≥ 18 个）的 return type 必须是 `WriteResult` 子类，**保留关联键不压扁**：

```python
class WriteResult(BaseModel):
    status: Literal["written", "skipped", "rejected", "pending"]
    target: str          # 文件路径 / DB 表名 / 子任务 ID / 异步 job ID
    bytes_written: int | None
    preview: str | None  # 前 200 字符摘要
    mtime_iso: str | None
    reason: str | None   # 状态非 written 时必填
```

每个写工具定义子类保留关联键（如 `SubagentsSpawnResult.children: list[ChildSpawnInfo]` 含 task_id/work_id/session_id；`MemoryWriteResult.memory_id/version/action`）。详见 `.specify/features/084-context-harness-rebuild/contracts/tools-contract.md`。

注册期 enforce：`produces_write=True` 工具的 return annotation 必须是 `WriteResult` 子类，违规启动 fail-fast。

**§8.5.7.8 PolicyGate 统一入口**

工具层不再直接调 `threat_scan`；统一通过 `PolicyGate.check()` 入口（对齐 Constitution 原则 10 Policy-Driven Access）：
- `BLOCK` → 拦截 + 写 `MEMORY_ENTRY_BLOCKED` 事件（含 `ensure_audit_task` 防 FK violation）
- `WARN` → 由 GatewayToolBroker 触发 ApprovalGate（reversible+ 工具）
- 通过 → dispatch

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

#### 8.6.6 ApprovalGate（Feature 084 引入 + Feature 101 WAITING_APPROVAL 状态机）

ApprovalGate 是 §8.5.7.5 列出的 Harness 组件，承担**所有审批请求 + 决策注入 + 异步等待**的统一通道。实现位置：`octoagent/apps/gateway/src/octoagent/gateway/harness/approval_gate.py`。

**审批语义维度**：

| 维度 | 行为 |
|------|------|
| Session allowlist | 同 session + 同 operation_type 第二次不弹卡片（不跨 session 持久化）|
| 审批请求 | `request_approval` 写 `APPROVAL_REQUESTED` 事件 + SSE 推送审批卡片到 Web UI |
| 异步等待 | `wait_for_decision(handle, timeout=300)` 阻塞等 `asyncio.Event.set()` |
| 决策注入 | `resolve_approval(handle_id, decision)` 写 `APPROVAL_DECIDED` 事件（同时写 `handle_id` 和 `approval_id` 兼容字段）|
| Timeout | 显式 reject + 写 `APPROVAL_DECIDED` 终态事件（防事件重放悬挂）|

**WAITING_APPROVAL 状态机改造（Feature 101 Phase B）**：

Feature 101 在 ApprovalGate 上叠加显式 `WAITING_APPROVAL` 状态机，让审批等待成为 Task 状态机的一等公民：
- **task_runner 单 owner**：同一 Task 只能有一个 owner 进入 WAITING_APPROVAL，CAS（compare-and-swap）防 race
- **双注册**：task_runner + ApprovalManager 双注册同步桥接，状态机迁移走 ApprovalManager 中转
- **SSE production 接入**：Feature 099 引入的 `worker.escalate_permission` 三工具实际复用同一 ApprovalGate 路径，production 真闭环走 SSE 通道实时推送
- **startup recovery**：进程重启后未决审批从 EventStore 恢复（对齐 Constitution 原则 1 Durability First + 原则 2 Everything is an Event），不丢任何 pending 审批

详见 `.specify/features/101-notification-attention/spec.md` 和 architecture-audit.md §14.13。

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

#### 8.7.6 Context Layer：USER.md SoT（Feature 084 引入）

与 Memory 双线（Fragments / SoR）正交，Context 层处理"**用户档案 + 候选事实 + 写工具回显**"三件事。Feature 084 把 OwnerProfile 从权威源退化为派生只读视图，把 USER.md 提升为 SoT（Single Source of Truth）。

实现位置（详见 `docs/codebase-architecture/harness-and-context.md` §3）：

```
behavior/
├── system/
│   ├── USER.md       # SoT：用户档案，user_profile.update 写入
│   └── MEMORY.md     # MEMORY 持久化（Phase 5 扩展）
└── observations/     # observation_candidates 派生候选（待用户审核）
```

**§8.7.6.1 USER.md 是 SoT（FR-9.1）**

| 实体 | SoT | 派生方向 |
|------|-----|----------|
| **USER.md** | ✅ Single Source of Truth | OwnerProfile 解析回填 / SnapshotStore 注入 system prompt |
| `OwnerProfile` | ❌ 派生只读视图 | `sync_owner_profile_from_user_md(USER.md)` 解析 timezone/locale 等字段 |
| `observation_candidates` 表 | candidates 队列 | promote 后由用户决策写入 USER.md |

**§8.7.6.2 user_profile 三工具**

- `user_profile.update(operation, content)`：写 USER.md（走 PolicyGate → ThreatScanner → SnapshotStore.append_entry → `MEMORY_ENTRY_ADDED` 事件 → 异步 `sync_owner_profile_from_user_md`）
- `user_profile.read(key)`：读派生 OwnerProfile（live state）
- `user_profile.observe(turn)`：从对话提取候选 fact，写 `observation_candidates` 表 + `OBSERVATION_OBSERVED` 事件

**§8.7.6.3 Memory Candidates API**

候选事实从 `observation_candidates` 表流向 USER.md：

- `promote(candidate_id)`：单条 promote
- `discard(candidate_id)`：单条 discard
- `bulk_discard(candidate_ids)`：批量 discard，**atomic claim**（UPDATE status='promoting' WHERE status='pending'）防 concurrent 重复写入；返回 `skipped_ids` 让 LLM 看到哪些被并发抢占了
- Web UI 红点 badge（`useMemoryCandidateCount` hook 监听 `memory-candidates-changed` 事件刷新）

**§8.7.6.4 WriteResult 通用回显契约**

详见 §8.5.7.7。Context 层所有写工具（`user_profile.update` / `memory.write` / `subagents.spawn` / `mcp.install` 等 ≥ 18 个）return type 强制 `WriteResult` 子类；保留 `task_id` / `memory_id` / `run_id` 等关联键不压扁，确保 LLM 回显能精确感知"写到哪里 / 写了什么 / 后续怎么追"。

**§8.7.6.5 F082 退役清单（Feature 084 Phase 4）**

Feature 084 替代了 F082 Bootstrap 路径，**净删 ~2400 行 dead code**：

| 文件 | 状态 |
|------|------|
| `apps/gateway/.../builtin_tools/bootstrap_tools.py` | 删除（Phase 1） |
| `apps/gateway/.../services/user_md_renderer.py` | 删除（Phase 4） |
| `apps/gateway/.../services/bootstrap_integrity.py` | 删除（Phase 4） |
| `apps/gateway/.../services/bootstrap_orchestrator.py` | 删除（Phase 4） |
| `packages/provider/.../dx/bootstrap_commands.py` | 删除（Phase 4） |
| `packages/core/.../models/agent_context.BootstrapSession` | 删除（Phase 4） |
| `bootstrap_sessions` SQLite 表 | DROP migration 启动自动执行（Phase 4） |
| `octo bootstrap reset/migrate-082/rebuild-user-md` CLI | 删除（Phase 4） |
| ~50 个 F082 deprecated 测试 | 删除或迁移到新路径 |

**重装路径**（不依赖 migrate 命令）：

```bash
rm -rf ~/.octoagent/data/ ~/.octoagent/behavior/   # 清状态
# 保留：~/.octoagent/octoagent.yaml + .env（用户配置）
octo update                                         # 重启
# bootstrap 完成由 USER.md 实质填充（>100 字符）判定，不依赖任何旧表 / 状态机
```

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

### 8.9 Provider Plane：ProviderRouter 直连（Feature 080/081 重构）

> Feature 080 引入 ProviderRouter 替代 LiteLLM Proxy；Feature 081 把 LiteLLM 完全退役（删除 Proxy 子进程 + LiteLLMClient + docker-compose.litellm.yml 等共 9 个文件）。详细架构见 `docs/codebase-architecture/provider-direct-routing.md`。

#### 8.9.1 语义 alias（业务侧，保留）

业务代码（Kernel / Worker / Skill）通过语义 alias 调用 LLM，**与具体 Provider 解耦**：

- `router`：意图分类、风险分级（小模型）
- `extractor`：结构化抽取（小/中模型）
- `planner`：多约束规划（大模型）
- `executor`：高风险执行前确认（大模型，稳定优先）
- `summarizer`：摘要/压缩（小模型）
- `fallback`：备用 provider

`AliasRegistry` 解析顺序：显式配置 alias 优先 → legacy 语义 alias fallback。

#### 8.9.2 ProviderRouter + Multi-Transport 直连（替代 LiteLLM 运行时层）

```
Skill → ProviderModelClient / ProviderRouterMessageAdapter
      → ProviderRouter（alias → ProviderClient，按 task_scope 缓存）
      → ProviderClient（按 ProviderTransport 枚举派发）
      → Provider HTTP（OpenAI / Anthropic / SiliconFlow / OpenRouter / ...）
```

LLM 调用栈深度从历史 4 层（Skill → LiteLLMSkillClient → ChatCompletionsProvider → LiteLLM Proxy → Provider）缩短为 **2 层**（Skill → ProviderClient → Provider）。

**3 种 transport**（`ProviderTransport` 枚举）：

| Transport | HTTP 端点 | 代表 Provider |
|-----------|----------|--------------|
| `openai_chat` | `POST /v1/chat/completions` | OpenAI / OpenRouter / SiliconFlow / 本地 Ollama |
| `openai_responses` | `POST /v1/responses` | OpenAI Codex（JWT OAuth 直连 `chatgpt.com/backend-api/codex`）|
| `anthropic_messages` | `POST /v1/messages` | Anthropic Claude |

核心组件（位置 `octoagent/packages/provider/src/octoagent/provider/`）：

- **`ProviderRouter`**（`provider_router.py`）：alias → ProviderClient 解析（按 `task_scope` 缓存避免 mid-task provider 切换）+ 凭证管理 + HTTP client 共享（同 provider/transport 跨 task 复用）
- **`ProviderClient`**（`provider_client.py`）：按 `ProviderTransport` 派发到 3 种 transport 实现；401/403 反应式 OAuth 刷新（Feature 078）；统一 `LLMCallError` 错误分类
- **`ProviderRouterMessageAdapter`**（`router_message_adapter.py`）：把 ProviderRouter 包装成 `LLMProviderProtocol`（`complete(messages, alias)`），替代 LiteLLMClient 作为 `FallbackManager.primary`；让 `LLMService.call()` 在没有 SkillRunner 路径时（如 `context_compaction`）也能直连 provider
- **`ProviderModelClient`**（`packages/skills/.../provider_model_client.py`）：替代 LiteLLMSkillClient 对接 SkillRunner；per-`(task_id, trace_id)` history 缓存 + idle eviction；工具调用循环编排
- **`AuthResolver`**（`auth_resolver.py`）：`StaticApiKeyResolver`（环境变量）+ `OAuthResolver`（`auth-profiles.json` + 401/403 反应式刷新）

#### 8.9.3 统一成本治理

每次模型调用写入事件（与 LiteLLM 时代语义一致，记账层下移到 ProviderRouter 内部）：

- 字段：model_alias / provider / latency / tokens / cost
- per-task 预算阈值触发策略：超预算 → 降级到 cheap 模型 / 提示用户 / 暂停等待确认
- ProviderRouter 内部记账，不再依赖 LiteLLM `/spend` 接口或 Proxy 端聚合

#### 8.9.4 多 Provider 扩展与 Auth Adapter

> 目标：新增 Provider 时**零代码变更**（仅修改 `octoagent.yaml` 配置即可），同时支持非标准认证模式。

**Provider 扩展**：

- 业务代码通过语义 alias 调用 → AliasRegistry 映射到运行时 ProviderEntry → ProviderClient 按 `transport` 字段派发到对应 transport 实现
- 新增 Provider 只需在 `octoagent.yaml` 的 `providers[]` 中追加 `ProviderEntry`（`id` + `name` + `enabled` + `transport` + `api_base` + `auth`）
- 不再需要 `litellm-config.yaml` 衍生配置；`ProviderEntry.transport` 直接表达运行时 endpoint 派发逻辑

**ProviderEntry schema（v2）**：

```yaml
providers:
  - id: openai-codex
    name: OpenAI Codex
    enabled: true
    transport: openai_responses
    api_base: https://chatgpt.com/backend-api/codex
    auth:
      kind: oauth
      profile: openai-codex-default
  - id: openrouter
    name: OpenRouter
    enabled: true
    transport: openai_chat
    api_base: https://openrouter.ai/api/v1
    auth:
      kind: api_key
      env: OPENROUTER_API_KEY
```

**Auth Adapter 抽象层**（参考 OpenClaw 双层架构）：

- **Config / Credential 分离**：
  - Config 层（`octoagent.yaml`）：声明 auth profile 元数据（provider, mode）
  - Credential 层（`auth-profiles.json`，`.gitignore` 保护）：存储实际凭证
  - 对齐 Constitution 原则 5 Least Privilege：凭证与配置物理隔离

- **三种凭证类型**：`ApiKeyCredential` / `TokenCredential` / `OAuthCredential`（详见 Feature 078 / 003-b spec）

- **OAuth Authorization Code + PKCE 流程**（Feature 003-b 已交付）：
  - 标准 RFC 7636 流程取代纯 Device Flow
  - `OAuthProviderRegistry` 注册表管理多 Provider OAuth 配置（内置 openai-codex + github-copilot）
  - `PkceOAuthAdapter` 适配器实现 PKCE 流程编排
  - 环境检测（SSH/容器/无 GUI）自动选择浏览器 / 手动粘贴模式
  - 本地回调服务器（`127.0.0.1:1455`）接收授权码，端口冲突自动降级到手动模式

- **OAuth Token 反应式刷新**（Feature 078 OAuth Token Refresh Robustness）：
  - ProviderClient 在 401/403 时自动 `refresh()` + 持久化新 access_token
  - 文件锁防 concurrent 刷新 race
  - 刷新失败降级到 fallback Provider（对齐 Constitution 原则 6 Degrade Gracefully）

- **Codex Reasoning/Thinking 模式**（Feature 003-b 增量能力）：
  - `ReasoningConfig` 模型：`effort`（none/low/medium/high/xhigh）+ `summary`（auto/concise/detailed）
  - ProviderClient 支持 `reasoning` 参数
  - 双路径适配：Responses API 使用嵌套 `reasoning` 对象，Chat Completions API 使用顶层 `reasoning_effort` 字符串

#### 8.9.5 历史 yaml 兼容性 + migrate-080

老 v1 yaml（含 `runtime.llm_mode` / `litellm_proxy_url` / `master_key_env`）启动时：

1. `load_config` 在 raw YAML 层调用 `detect_legacy_yaml_keys`，命中 legacy keys 时 log warning 引导用户跑 `octo config migrate-080`
2. Pydantic 解析仍然成功（deprecated 字段保留默认值）
3. 运行时不再消费这些字段（ProviderRouter 直连）

**`octo config migrate-080` 命令**：

- yaml 迁移：v1 → v2（推断 `transport`，转 `auth_type+api_key_env` → `auth.kind+env`）
- 凭证迁移：`.env.litellm` → `.env`（合并已存在键不覆盖）
- 自动备份原文件 → `*.bak.080-{kind}-{timestamp}`
- `--dry-run` 仅打印 diff，不写文件
- 幂等：v2 yaml + 缺 `.env.litellm` → 重复跑直接 skip

#### 8.9.6 性能改进 + 退役清单

**性能改进**（vs LiteLLM 时代）：

- Gateway 启动时间：~10s → **~5s**（无 LiteLLM Proxy 子进程等待 + 无 docker-compose 启动）
- LLM 调用栈：4 层 → **2 层**
- 配置 source-of-truth：3 份（octoagent.yaml + auth-profiles.json + litellm-config.yaml）→ **2 份**
- 用户首次 setup 步骤：5 步 → **3 步**

**Feature 081 P4 已删除文件清单**：

| 文件 | 替代方案 |
|------|---------|
| `octoagent/skills/litellm_client.py` | `octoagent/skills/provider_model_client.py` |
| `octoagent/skills/providers.py` | `octoagent/provider/provider_client.py`（按 transport 派发） |
| `octoagent/skills/compactor.py` | `gateway/services/context_compaction.py`（主线 + 三级 fallback）|
| `octoagent/provider/client.py`（`LiteLLMClient`）| `octoagent/provider/provider_client.py`（`ProviderClient`）|
| `gateway/services/proxy_process_manager.py` | 不再需要（Provider 直连无子进程）|
| `gateway/services/config/litellm_generator.py` | 不再需要（无衍生配置） |
| `gateway/services/config/litellm_runtime.py` | `ProviderEntry.transport` 直接表达 |
| `octoagent/docker-compose.litellm.yml` | 不再需要（无 Docker 容器）|
| `octoagent/provider/dx/docker_daemon.py` | 不再需要（无 Docker daemon 依赖）|

#### 8.9.7 扩展原则（保留约束）

- 业务代码（Kernel/Worker/Skill）永远不感知具体 Provider 或认证方式
- Provider 变更的用户主入口限定在 `octoagent.yaml`，新增 Provider 仅追加 `ProviderEntry` 条目
- Auth Adapter 变更的影响范围限定在 `octoagent/packages/provider/`
- M3 用户化约束（2026-03-07）：环境变量仅作高级/CI 路径，主路径收敛为统一 Secret Store + Wizard，CLI / Web / 桌面端共用 onboarding + config protocol

---

### 8.10 Notification & Routine：用户感知 ROI 层（Feature 101 + Feature 102）

NotificationService 和 DailyRoutineService 是 M5 阶段 3 引入的"用户感知 ROI"子系统：把后台长任务（Worker / Subagent / cron）的进度与结论按用户偏好的优先级 + 通道，主动推送到 Telegram / Web；并按 USER.md 配置每日生成 routine summary。设计上严守 H1 主 Agent mediated 哲学（详见 `docs/blueprint/agent-collaboration-philosophy.md`）：所有通知名义上由主 Agent 代发，避免 Worker 直接对用户讲话。

#### 8.10.1 NotificationService（Feature 101）

实现位置：`octoagent/apps/gateway/src/octoagent/gateway/services/notification.py`。

**核心特性**：

| 维度 | 行为 |
|------|------|
| **4 级优先级** | `critical` / `high` / `medium` / `low` —— 各级对应不同 channel routing（critical → Telegram + Web 强提示；low → 仅 Web silent）|
| **Active hours**（USER.md SoT） | USER.md 配置 `active_hours` 字段（如 `09:00-23:00`），命中 active 时段 → 推送；外部时段视为 quiet hours，**discard 而非 enqueue**（避免堆积 backlog）；`CRITICAL` 优先级豁免 quiet hours 强制推送（FR-B4）|
| **dismiss 跨通道统一** | Telegram callback button 或 Web API `POST /api/notifications/{id}/dismiss` 任一通道 dismiss 后，通过共享 `_dismissed_set` 让另一端下次查询（`GET /api/notifications` / `list_active`）不再展示；Telegram 已推送消息不撤回（仅 Web 端会过滤掉）|
| **去重 + dedupe** | `notification_id = generate_notification_id(task_id, event_type, state_transition_event_id)`（前 16 位 sha256）；同 id 重复 emit 不重复推送 |
| **`NOTIFICATION_DISPATCHED` EventType** | 每条 notification 写 EventStore（含 quiet hours 内被过滤的，filtered=True 字段标记，对齐 H4 discard 审计链）；payload 含 task_id / notification_id / notification_type / priority / channels / filtered |

**WAITING_APPROVAL 状态机改造**（详见 §8.6.6）：task_runner 单 owner + CAS + 双注册桥接 ApprovalManager + ApprovalGate SSE production 接入 + startup recovery，确保审批通知不丢、不乱、不悬挂。

**ApprovalGate SSE production 接入**：Feature 099 的 `worker.escalate_permission` 三工具在 Feature 101 真正接通生产 ApprovalGate SSE 路径，前端审批卡片实时推送（不再仅是 stub）。

**已知 limitation（推迟到 F107）**：
- dismiss 跨重启持久化（当前 LOW 优先级 dismiss 状态进程重启清空）
- API 字段 `FR-D4` 显式参数化
- control_plane 参数 `FR-E1` 评估

#### 8.10.2 DailyRoutineService（Feature 102）

实现位置：`octoagent/apps/gateway/src/octoagent/gateway/services/daily_routine.py`。

**触发与执行流程**：

1. **cron 触发**：APScheduler 按 USER.md 的 `daily_summary_time` 字段（如 `09:00`）触发；时区由 USER.md `时区` 字段 + `OCTOAGENT_USER_TIMEZONE` env 兜底（SD-10 时区语义，全程 UTC 归一化）
2. **9 步执行流程**：扫描时间窗内的 Task → 聚合状态 → LLM 总结 → 通过 NotificationService 推送 → 写 `ROUTINE_*` 事件
3. **LLM 主路径 + deterministic fallback**：LLM 不可用（API 失败 / token budget 超限）时降级为 deterministic 摘要（直接列任务清单），保证总有产出（对齐 Constitution 原则 6 Degrade Gracefully）
4. **token budget 截断**：`max_input ≤ 2000 字符` + `max_output ≤ 512 token`，防 LLM 总结成本爆炸

**USER.md 3 个机器可读字段**：

| 字段 | 用途 |
|------|------|
| `daily_summary_time` | cron 触发时刻（如 `09:00`）|
| `routine_active` | 总开关（true / false）；false 时不触发 cron + 写 `ROUTINE_SKIPPED` 事件 |
| `summary_channels` | 推送通道清单（如 `[telegram, web]`）|

**4 个 EventType**（挂在 `_daily_routine_audit` task 上）：

| EventType | Payload 字段 |
|-----------|-------------|
| `ROUTINE_TRIGGERED` | `trigger_ts`（cron 触发时刻）|
| `ROUTINE_COMPLETED` | `elapsed_ms` / `worker_count` / `failed_count` / `attention_count` / `summary_artifact_id` |
| `ROUTINE_FAILED` | `error_type` / `error_msg`（不含 traceback 原始文本，防 PII 泄露）|
| `ROUTINE_SKIPPED` | `reason`（如 `"routine_active=False"` / `"no_tasks_in_window"`）|

**task_store.list_tasks_in_time_range 方法**：DailyRoutineService 依赖此 store 方法按时间窗聚合任务（详见 §8.2.1 事件溯源）；F102 顺便补全 UTC 归一化语义。

**范围排除**：
- WeeklyRoutine 不纳入 F102（推迟到独立 Feature）
- dismiss 持久化推迟到 F107

#### 8.10.3 NotificationService ↔ DailyRoutineService 协作

```
DailyRoutineService.run()
  → 扫描 task_store + memory + USER.md attention 候选
  → LLM 总结 / fallback
  → NotificationService.notify_task_state_change(summary, priority="medium", channels=USER.md.summary_channels)
    → 走 §8.10.1 全流程（quiet hours / priority routing / dismiss / NOTIFICATION_DISPATCHED 事件）
```

F101 NotificationService 在 F102 引入时新增 `channels` 可选参数（向后兼容）让 DailyRoutineService 按 USER.md 配置选择推送通道。

---
