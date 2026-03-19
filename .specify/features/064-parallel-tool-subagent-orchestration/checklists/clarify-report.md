# Feature 064 需求澄清报告

> **生成时间**: 2026-03-19
> **Spec 版本**: Draft
> **分析基准**: spec.md + prior-research-summary.md + 源码逐行比对
> **分析模式**: quality-first

---

## 摘要

共发现 **28** 项需求澄清点，按严重等级分布：

| 等级 | 数量 | 说明 |
|------|------|------|
| CRITICAL | 7 | 阻断实现或可能导致运行时严重错误 |
| WARNING | 12 | 设计歧义或遗漏，不及时解决将在实现阶段产生返工 |
| INFO | 9 | 可优化项或建议 |

---

## CRITICAL 级别

### C-01: Task 模型缺少 `parent_task_id` 字段

**涉及 FR**: FR-064-13（Subagent spawn 创建 Child Task）
**现状**: `Task` 模型（`packages/core/src/octoagent/core/models/task.py`）当前仅有 `task_id`、`status`、`title`、`thread_id`、`scope_id`、`requester`、`risk_level`、`pointers`、`trace_id` 字段，**没有 `parent_task_id`**。
**问题**: spec 假设 Child Task 通过 `parent_task_id` 关联父 Task，但 Task 模型没有此字段。这是一个数据模型层面的缺失，不仅需要新增字段，还涉及数据库 migration、TaskStore CRUD 适配、事件投影逻辑变更。
**影响**: P1-A 全部 FR 不可实现。
**建议**: 在 plan.md 中明确将 "Task 模型扩展 parent_task_id" 作为 P1-A 的前置基础设施任务，包含 DB migration + Store 适配。同时需要评估 `parent_task_id` 是否需要加索引（查询子任务列表场景）。

---

### C-02: SkillRunner 并行分桶需要访问 ToolBroker registry 查询 SideEffectLevel，但当前接口不支持按名称查询

**涉及 FR**: FR-064-01（按 SideEffectLevel 分桶）
**现状**: `SkillRunner._execute_tool_calls()` 通过 `self._tool_broker.execute()` 执行工具，但 `ToolBrokerProtocol` 接口仅暴露 `execute()` 和 `discover()` 方法。`discover()` 返回所有工具元数据的列表，没有按名称查询单个工具 `SideEffectLevel` 的高效方法。
**问题**: 分桶算法需要对每个 `tool_call` 查询其 `SideEffectLevel`，如果每次都调用 `discover()` 获取全量工具列表再过滤，效率极低。ToolBroker 的 `_registry` 是内部字典，SkillRunner 无法直接访问。
**建议**: 需要在 `ToolBrokerProtocol` 上新增 `get_tool_meta(tool_name: str) -> ToolMeta | None` 方法，或者在 `ToolBroker` 上新增公开接口。spec 应明确此接口扩展。

---

### C-03: 并行工具调用与 Feature 061 审批流（ask 信号桥接）的交互未定义

**涉及 FR**: FR-064-04、FR-064-08
**现状**: 当前 `_execute_tool_calls()` 中，每个工具执行后会调用 `_handle_ask_bridge()` 处理 `ask:` 信号。在分桶执行模式下，`IRREVERSIBLE` 工具触发 `WAITING_APPROVAL` 流程（FR-064-04 说"复用 Feature 061 PresetBeforeHook"）。
**问题**:
1. `PresetBeforeHook` 工作在 ToolBroker 的 BeforeHook 链中，不在 SkillRunner 层。spec 说 FR-064-04 "触发 WAITING_APPROVAL 流程"——这是指 ToolBroker 层的 `ask:` 信号自动触发，还是 SkillRunner 层需要额外逻辑？
2. 如果 `IRREVERSIBLE` 工具在 ToolBroker 层被 `PresetBeforeHook` 拦截返回 `ask:` 信号，然后 SkillRunner 的 `_handle_ask_bridge()` 桥接审批——这个流程在并行分桶中的执行时机如何？是先把整个 READ_ONLY 并行组执行完，再执行 DESTRUCTIVE 审批组？审批期间 SkillRunner 主循环处于什么状态？
3. `WAITING_APPROVAL` 是 Task 级状态。如果一批 tool_calls 中有多个 IRREVERSIBLE 工具，每个都触发审批，Task 状态如何管理？是逐个审批还是批量审批？
**建议**: spec 需要明确：(a) 审批组中多个工具是逐个审批还是一次性批量审批；(b) 审批等待期间 SkillRunner 的状态（是否 yield 到 Orchestrator）；(c) 审批超时后整个 batch 的回退策略。

---

### C-04: LiteLLMSkillClient 工具结果回填重构与并行执行的协调未明确

**涉及 FR**: FR-064-09 ~ FR-064-12（回填格式修复）+ FR-064-01 ~ FR-064-08（并行执行）
**现状**: `LiteLLMSkillClient.generate()` 在 step > 1 时将 `feedback` 列表折叠为自然语言 user message（第 517-538 行）。P0-B 要求改为标准 `tool` role message，需要 `tool_call_id`。P0-A 的并行执行产出 `ToolFeedbackMessage` 列表，然后传给下一轮 `generate()`。
**问题**:
1. `ToolFeedbackMessage` 当前没有 `tool_call_id` 字段。spec 只在 `ToolCallSpec` 上新增了 `tool_call_id`，但 `ToolFeedbackMessage` 也需要携带对应的 `tool_call_id` 才能回填标准格式。这是一个数据模型遗漏。
2. 并行组（READ_ONLY）和串行组（WRITE）的工具结果需要**合并后一次性**传给 `generate()`。但当前 `generate()` 接收的 `feedback` 参数是扁平列表，不区分批次和执行模式。如何保证回填顺序与 LLM 发出的 `tool_calls` 顺序一致？
3. Chat Completions 路径：LLM 返回 `assistant` message 携带 `tool_calls` 数组后，回填时需要追加这个 `assistant` message（含完整 `tool_calls` 数组），然后逐个追加 `tool` role message。当前代码追加的是自然语言摘要（第 569-571 行 `tc_summary`），这需要完全重写。
**建议**: spec 需要：(a) 明确 `ToolFeedbackMessage` 也需新增 `tool_call_id` 字段；(b) 明确 `generate()` 在 step > 1 时如何从 feedback 列表中恢复 `tool_call_id` → `tool` role message 的映射；(c) 明确 assistant message 追加策略的变更。

---

### C-05: Subagent 独立 SkillRunner 的 SkillManifest 和工具集来源未定义

**涉及 FR**: FR-064-13、FR-064-15
**现状**: spec 说 "Subagent spawn 时创建独立的 SkillRunner 实例"。SkillRunner 运行需要 `SkillManifest`（定义模型、工具白名单、重试策略等）和 `ToolBroker` 实例。
**问题**:
1. Subagent 的 `SkillManifest` 从哪来？是复用父 Worker 的 manifest，还是基于 `task_description` 动态生成？如果动态生成，model_alias、tools_allowed 等如何决定？
2. Subagent 是否共享父 Worker 的 `ToolBroker` 实例？如果共享，工具注册/发现/hook 链是否一致？如果不共享，需要单独初始化。
3. Subagent 的 `LiteLLMSkillClient` 是新建实例还是复用？如果复用，对话历史 key 如何隔离（当前 key = `task_id:trace_id`）？
**建议**: spec 需要明确 Subagent SkillRunner 的初始化参数来源，特别是 manifest、tool broker、model client 三个核心依赖的获取策略。

---

### C-06: 上下文压缩中"LLM 生成摘要"的执行时机与主循环冲突

**涉及 FR**: FR-064-28（三级压缩策略）
**现状**: 压缩策略第 2 级是"将早期对话轮次替换为 LLM 生成的摘要"。这需要调用 LLM（可能是 `compaction_model_alias` 指定的快速模型）。
**问题**:
1. 摘要生成是在 `generate()` 调用前**同步阻塞**执行，还是异步后台执行？如果同步阻塞，会显著增加 step 延迟（NFR-064-04 要求 p95 < 3s）。
2. 摘要生成本身也消耗 token，是否计入 `UsageTracker` 的 `request_tokens`/`response_tokens`？如果计入，可能导致压缩操作本身触发 token limit。
3. 压缩使用的 `compaction_model_alias` 如何路由到 LiteLLM Proxy？是创建新的 `LiteLLMSkillClient` 实例还是复用当前实例？如果复用，model_alias 切换是否线程安全？
4. Constitution #2 要求 "Everything is an Event"。`CONTEXT_COMPACTION_COMPLETED` 事件在压缩完成后发射，但压缩过程中如果 LLM 调用失败，应发射什么事件？spec 未定义 `CONTEXT_COMPACTION_FAILED` 事件。
**建议**: spec 需要明确：(a) 压缩执行时机（generate 前还是 step 间隔）；(b) 压缩 token 消耗的计量策略；(c) 补充 `CONTEXT_COMPACTION_FAILED` 事件定义；(d) compaction model 的 client 实例策略。

---

### C-07: Subagent 结果注入父 Worker 对话的具体机制未定义

**涉及 FR**: FR-064-24（Orchestrator 将 Subagent 结果注入父 Worker 对话历史，触发下一轮 SkillRunner generate()）
**现状**: Orchestrator 是控制平面服务，通过事件驱动工作。`LiteLLMSkillClient` 维护独立的对话历史（`self._histories[key]`）。SkillRunner 的主循环是一个 `while` 循环。
**问题**:
1. 父 Worker 的 SkillRunner 可能正在执行自己的工具调用循环。当 Subagent 结果到达时，如何"注入"到正在运行中的对话历史？是中断当前循环还是等待当前 step 完成？
2. `LiteLLMSkillClient._histories` 是内存字典，Orchestrator 如何访问它？当前架构中，Orchestrator 和 SkillRunner 的耦合关系是什么？
3. "触发下一轮 SkillRunner generate()" 是指在当前 `while` 循环内自然进入下一步，还是需要外部信号唤醒？如果是后者，需要引入新的控制流机制（如 asyncio.Event/Condition）。
4. 多个 Subagent 并行完成时（FR-064-25），结果按到达顺序注入。但 LLM 对话历史是有序的，不同 Subagent 结果交错注入是否会导致 LLM 上下文混乱？
**建议**: 这是整个 P1-B 的核心架构决策，spec 需要详细设计父 Worker 等待/唤醒/注入的控制流模型。建议明确：(a) 父 Worker SkillRunner 在 spawn subagent 后是否暂停主循环；(b) 结果注入是通过什么通道（内存 callback、事件订阅、还是对话历史追加）；(c) 多结果并发注入的序列化策略。

---

## WARNING 级别

### W-01: 并行组中工具超时处理策略不完整

**涉及 FR**: FR-064-07
**现状**: spec 说"并行组中某个工具失败时，已完成的工具结果正常保留"。但 `asyncio.gather()` 默认行为是任一 coroutine 抛异常就取消其余。
**问题**: 需要使用 `asyncio.gather(*coros, return_exceptions=True)` 来隔离异常。但 ToolBroker 的超时由 `asyncio.wait_for()` 实现，`TimeoutError` 会向上传播。如果某个工具超时，是否应该等待其他工具完成？还是整个并行组立即返回？
**建议**: spec 应明确：(a) 并行 gather 使用 `return_exceptions=True`；(b) 单个工具超时不影响同批次其他工具；(c) 超时工具的 ToolFeedbackMessage 应标记 `is_error=True` 并携带超时信息。

---

### W-02: SideEffectLevel 枚举值名称与 spec 用词不一致

**涉及 FR**: FR-064-01 ~ FR-064-08
**现状**: 代码中 `SideEffectLevel` 枚举值为 `NONE / REVERSIBLE / IRREVERSIBLE`。spec 在多处使用混合术语：`READ_ONLY`（用于描述）、`none`（用于分桶条件）、`WRITE`（用于串行组）、`DESTRUCTIVE`（用于审批组）。
**问题**: spec 中 `bucket_read` 对应 `side_effect_level == NONE`（正确），`bucket_write` 对应 `REVERSIBLE`（正确），`bucket_destructive` 对应 `IRREVERSIBLE`（正确）。但 spec 正文中大量使用 `READ_ONLY`、`WRITE`、`DESTRUCTIVE` 这些非枚举值名称，容易造成实现者混淆。
**建议**: 在 spec 中统一使用代码枚举值名称 `NONE / REVERSIBLE / IRREVERSIBLE`，或在首次使用时建立明确的映射表。

---

### W-03: 并行分桶执行顺序与 LLM 意图可能冲突

**涉及 FR**: FR-064-08
**现状**: spec 规定执行顺序为 "READ_ONLY 先并行 -> WRITE 串行 -> DESTRUCTIVE 审批串行"。但 LLM 在单次 response 中返回的 `tool_calls` 数组可能隐含执行顺序意图。
**问题**: 如果 LLM 返回 `[write_file(a.txt), read_file(a.txt)]`（先写后读），按 spec 的分桶策略会变成先 read 后 write，导致读到旧数据。虽然这种场景在实践中可能不常见（LLM 通常会分多步执行），但 spec 应明确此限制。
**建议**: (a) 在 spec 中标注此限制为 **已知限制**（Known Limitation），与 NG3 呼应；(b) 考虑增加保守模式：当同一轮 tool_calls 中混合 NONE 和 REVERSIBLE 工具时，回退到全串行（作为可选配置）。

---

### W-04: Responses API 路径的 `call_id` 一致性问题未充分解决

**涉及 FR**: FR-064-10
**现状**: `litellm_client.py` 第 517-519 行注释明确提到："Responses API 在 Codex 代理链路上复用 function_call_output 时，call_id 可能与上一轮 function_call 脱节，导致 400 invalid_request"。这正是当前使用自然语言回填的原因。
**问题**: P0-B 要求 Responses API 路径使用 `function_call_output` type 回填。但当前代码中记录的 bug（call_id 脱节）是否已在上游（LiteLLM Proxy / OpenAI API）修复？如果未修复，FR-064-10 的验收标准可能无法满足。
**建议**: (a) 在实现前验证 LiteLLM Proxy 当前版本是否已修复 call_id 一致性问题；(b) FR-064-12 的向后兼容回退路径应覆盖 Responses API 路径；(c) 考虑将 Responses API 路径的标准回填标记为 "conditional"——仅在 call_id 验证通过时启用。

---

### W-05: SSE Hub 双路广播的订阅清理与内存泄漏风险

**涉及 FR**: FR-064-23
**现状**: SSE Hub 当前按 `task_id` 维护订阅者 Queue 集合。广播时如果 Queue 满则丢弃。FR-064-23 要求 Subagent 事件同时广播到 Child Task 和父 Task。
**问题**:
1. 如果父 Task 长期运行（如 Butler 主循环），其 SSE 订阅者会累积大量事件。增加 Subagent 事件冒泡后，事件量进一步增大。
2. 双路广播是在 SSE Hub 内部实现（broadcast 时自动查找 parent_task_id 并广播），还是由调用方分两次调用 broadcast？如果前者，SSE Hub 需要维护 parent_task_id 映射关系。
3. 当 Subagent 大量创建和销毁时，`_subscribers` 字典中会产生大量 task_id key。虽然当前在无订阅者时会删除 key，但 Subagent 的 Child Task 可能从未被订阅，导致 broadcast 调用无意义。
**建议**: spec 应明确双路广播的实现位置和策略，以及 parent_task_id 映射的维护方式。

---

### W-06: Subagent 继承父 Worker 的 permission_preset 但可能需要更严格

**涉及 FR**: FR-064-16
**现状**: spec 说 "Subagent 继承父 Worker 的 permission_preset（Constitution #5: Least Privilege）"。
**问题**: Constitution #5 的精神是"最小权限"，但 "继承" 意味着权限相同，而非更小。如果父 Worker 是 FULL preset，Subagent 也会是 FULL，这可能违背最小权限原则。Subagent 的任务范围通常比父 Worker 窄，理应获得更窄的权限。
**建议**: (a) 考虑 Subagent 默认降级一级（FULL -> NORMAL, NORMAL -> MINIMAL）；(b) 或允许 spawn 时通过参数指定 preset，但不得高于父 Worker。

---

### W-07: 上下文压缩的 "保留最近 N 轮" 中 N 值未定义

**涉及 FR**: FR-064-28
**现状**: 三级压缩策略第 2 级说 "保留最近 N 轮"，但 N 未给出具体值或配置方式。
**问题**: N 的选择直接影响压缩效果和推理质量。N 太小，LLM 丢失关键上下文；N 太大，压缩效果不明显。
**建议**: (a) 定义 N 的默认值（建议 5-10 轮）；(b) 允许通过 SkillManifest 或 execution_context 配置；(c) 考虑按 token 数而非轮次数来界定"最近"范围。

---

### W-08: 后台通知的 Notification Channel 注册和生命周期未定义

**涉及 FR**: FR-064-35
**现状**: spec 定义了 `NotificationChannelProtocol` 接口，但未说明 channel 如何注册、在哪注册、由谁管理。
**问题**:
1. Telegram channel 和 Web SSE channel 在系统启动时注册还是按需注册？
2. Channel 的注册存储在哪里？内存还是持久化？
3. Notification 分发器是新增服务还是挂载到 Orchestrator？
4. 用户可否配置通知偏好（如只接收 FAILED 通知，忽略 SUCCEEDED）？
**建议**: spec 应补充 Notification 子系统的初始化流程和 Channel 注册机制。

---

### W-09: Telegram inline keyboard 审批与 Feature 061 审批流的集成路径不明

**涉及 FR**: FR-064-33
**现状**: Feature 061 的审批由 `ApprovalBridgeProtocol` → `ApprovalManager` 处理。Telegram 渠道当前未实现 inline keyboard 审批。
**问题**: FR-064-33 说 Telegram 发送审批请求消息包含 inline keyboard，用户直接操作。但 Feature 061 的审批流是 `ask:` 信号 → `_handle_ask_bridge()` → `ApprovalManager`。Telegram inline keyboard 的审批决策如何回传到 ApprovalManager？需要新增 Telegram → Gateway → ApprovalManager 的回调链路。
**建议**: 此 FR 可能涉及 Telegram Bot 端的非平凡改动（inline keyboard handler + callback query 处理），建议在 plan 阶段单独评估工作量。

---

### W-10: 并行工具执行的 TOOL_CALL_STARTED/COMPLETED 事件 task_seq 冲突

**涉及 FR**: FR-064-06
**现状**: `ToolBroker.execute()` 中通过 `event_store.get_next_task_seq()` 生成递增的 `task_seq`。并行执行时多个工具同时请求 `task_seq`，可能产生竞争。
**问题**: 虽然 asyncio 是单线程模型，`get_next_task_seq()` 是 async 函数，如果内部有 await（如 DB 查询），并行的多个 coroutine 可能交错执行，导致 task_seq 不连续或重复。
**建议**: 确认 `get_next_task_seq()` 的实现是否是原子操作。如果不是，需要在并行执行前预分配一批 task_seq，或使用 asyncio.Lock 保护。

---

### W-11: Subagent HEARTBEAT 事件与 Feature 011 Watchdog 的交互

**涉及 FR**: FR-064-17
**现状**: EventType 枚举中已有 `TASK_HEARTBEAT`（Feature 011 Watchdog 使用）。spec 说 Subagent 发射 `TASK_HEARTBEAT` 事件到 Child Task。
**问题**: Feature 011 的 Watchdog 监听 `TASK_HEARTBEAT` 事件来检测 Worker 是否存活。如果 Subagent 也使用相同的事件类型，Watchdog 是否需要区分 Worker 心跳和 Subagent 心跳？心跳超时检测逻辑是否需要适配？
**建议**: (a) 确认 Watchdog 是否按 task_id 隔离监听（如果是，则 Child Task 的心跳不会影响父 Task 的 Watchdog）；(b) 考虑 Subagent 心跳是否需要不同的 payload schema 以区分来源。

---

### W-12: 上下文压缩时工具输出截断的边界条件

**涉及 FR**: FR-064-28
**现状**: 压缩策略第 1 级是 "截断 > 2000 字符的工具输出为摘要"。
**问题**:
1. 2000 字符阈值是否可配置？不同模型和场景对大输出的容忍度不同。
2. "截断为摘要" 具体如何实现？是简单截取前 N 个字符加省略号，还是用 LLM 生成摘要？如果是后者，就与第 2 级策略重叠了。
3. 已有的 `ContextBudgetPolicy.max_chars`（默认 1500）与此处的 2000 字符阈值是什么关系？
**建议**: 明确截断策略的具体实现（建议第 1 级为简单截断，第 2 级才引入 LLM 摘要），并与已有的 `ContextBudgetPolicy` 对齐。

---

## INFO 级别

### I-01: TOOL_BATCH 事件与已有 TOOL_CALL 事件的嵌套关系需要 Event Store 查询支持

**涉及 FR**: FR-064-05、FR-064-06
**说明**: 新增 `TOOL_BATCH_STARTED/COMPLETED` 事件包裹多个 `TOOL_CALL_STARTED/COMPLETED` 事件。前端 Task Detail 页面和事件查询 API 需要支持按 `batch_id` 聚合展示。当前 Event Store 查询接口是否支持按 payload 字段过滤？
**建议**: 评估前端和查询层是否需要适配。

---

### I-02: 单个 tool_call 场景的向后兼容验证

**涉及 FR**: NFR-064-02
**说明**: 当 LLM 只返回 1 个 tool_call 时，分桶算法仍会执行（只有 1 个桶，1 个元素）。需要确保 TOOL_BATCH 事件仍然生成，还是仅在 batch_size > 1 时才生成。
**建议**: 明确单工具调用是否生成 TOOL_BATCH 事件（建议不生成，以保持向后兼容）。

---

### I-03: 并行工具调用的并发度上限

**涉及 FR**: FR-064-02
**说明**: `asyncio.gather()` 无并发度限制。如果 LLM 一次返回 20 个 READ_ONLY 工具调用，全部并行可能造成资源压力（网络连接数、Docker 容器数等）。
**建议**: 考虑引入 `asyncio.Semaphore` 限制并发度（如最大 10），或作为 SkillManifest 可配置项。

---

### I-04: Subagent 的 UsageLimits 继承策略

**涉及 FR**: FR-064-13
**说明**: spec 提到 Subagent 继承 permission_preset，但未提及 `UsageLimits` 的继承。Subagent 是否应有独立的资源限制？是否应共享父 Worker 的 token 预算？
**建议**: 明确 Subagent 的 UsageLimits 策略：(a) 独立限制（默认值）；(b) 从父 Worker 分配配额；(c) 共享全局预算池。

---

### I-05: compaction_model_alias 默认使用主模型的成本问题

**涉及 FR**: FR-064-30
**说明**: spec 说 `compaction_model_alias` 默认使用主模型。如果主模型是 GPT-4 级别，用它来生成摘要的成本可能很高。
**建议**: 建议默认值为一个明确的快速模型 alias（如 `fast` 或 `compaction`），而非回退到主模型。在 LiteLLM Proxy 中预配置此 alias。

---

### I-06: Telegram 通知消息的格式和内容

**涉及 FR**: FR-064-32、FR-064-33
**说明**: spec 定义了通知触发条件，但未定义通知消息的具体格式（markdown/HTML/plain text）、内容模板、语言（中文/英文）。
**建议**: 在 plan 阶段定义通知消息模板，包含 Task 标题、状态、耗时等关键信息。

---

### I-07: 上下文压缩与 Responses API 路径的兼容性

**涉及 FR**: FR-064-26 ~ FR-064-31
**说明**: 上下文压缩操作在 `LiteLLMSkillClient` 层面修改对话历史。Chat Completions 路径和 Responses API 路径的历史格式不同（前者是 messages 数组，后者是 input items 数组）。压缩逻辑是否需要分路径实现？
**建议**: 评估压缩逻辑是否可以抽象为路径无关的操作，或需要为两个路径分别实现。

---

### I-08: Subagent 的 SkillRunner 实例与进程崩溃恢复

**涉及 FR**: FR-064-20、NFR-064-07
**说明**: Subagent SkillRunner 在 asyncio.Task 中运行。进程崩溃时，所有 asyncio.Task 丢失。spec 的 NFR-064-07 要求重启后孤儿 Subagent 自动流转到 FAILED。
**问题**: 进程重启后如何检测孤儿 Subagent？需要扫描 Task 表中 status 为 RUNNING 且 parent_task_id 不为空的记录，然后批量标记为 FAILED。但当前 Task 模型没有 parent_task_id（见 C-01）。
**建议**: 与 C-01 一起解决。重启恢复逻辑可复用 Feature 011 Watchdog 的孤儿检测机制。

---

### I-09: A2AConversation 在 Subagent 场景下的 source/target URI 规范

**涉及 FR**: FR-064-14
**说明**: A2A 协议要求 agent URI 格式为 `agent://xxx`。Subagent 的 URI 如何命名？是 `agent://subagent-{ULID}` 还是继承父 Worker 的 URI 前缀？
**建议**: 建议使用 `agent://workers/{parent_id}/subagents/{subagent_id}` 层级命名，便于审计追踪。

---

## 与前置 Feature 的兼容性分析

### Feature 059（Subagent CRUD）

- **兼容**: `spawn_subagent()` / `kill_subagent()` / `list_active_subagents()` 函数签名可直接复用
- **需扩展**: `spawn_subagent()` 需要新增 `task_description` 参数，创建 Child Task 和 SkillRunner
- **潜在冲突**: 当前 `spawn_subagent()` 只创建 Runtime/Session，不涉及 Task 创建。Task 创建逻辑需要从 Orchestrator 或新增的 SubagentExecutor 服务中调用

### Feature 061（Permission Preset）

- **兼容**: `PresetBeforeHook` 和 `ApprovalBridgeProtocol` 可直接复用
- **需关注**: 并行分桶中 IRREVERSIBLE 工具的审批流需要确保与 `_handle_ask_bridge()` 正确协调（见 C-03）
- **兼容**: `PermissionPreset` 继承逻辑对 Subagent 可直接复用

### Feature 062（Resource Limits）

- **兼容**: `UsageLimits` / `UsageTracker` 可直接复用
- **需关注**: 并行工具调用的 `tool_calls` 计数应在并行组完成后一次性累加，而非每个工具独立累加（否则可能在并行组执行中途触发限制）
- **需关注**: Subagent 是否共享父 Worker 的 UsageTracker 或独立追踪（见 I-04）

---

## 决策待定清单

| # | 问题 | 推荐决策 | 影响范围 |
|---|------|---------|---------|
| D-01 | Task 模型是否新增 parent_task_id | 是，必须新增 | P1-A 全部 FR |
| D-02 | ToolBrokerProtocol 是否新增 get_tool_meta() | 是，建议新增 | P0-A |
| D-03 | ToolFeedbackMessage 是否新增 tool_call_id | 是，必须新增 | P0-A + P0-B |
| D-04 | 审批组多工具逐个审批还是批量审批 | 建议逐个审批 | P0-A |
| D-05 | Subagent SkillManifest 来源 | 建议基于父 Worker manifest 衍生 | P1-A |
| D-06 | 父 Worker spawn 后是否暂停主循环 | 建议不暂停（异步模式） | P1-A + P1-B |
| D-07 | 单 tool_call 是否生成 TOOL_BATCH 事件 | 建议不生成（向后兼容） | P0-A |
| D-08 | 并行 gather 并发度上限 | 建议默认 10，可配置 | P0-A |
| D-09 | Subagent permission_preset 是否降级 | 建议不强制降级，但支持指定 | P1-A |
| D-10 | 上下文压缩默认模型 | 建议默认 fast alias | P2-A |
