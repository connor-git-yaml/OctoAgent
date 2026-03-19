# Feature 064 一致性分析报告

> **生成时间**: 2026-03-19
> **分析基准**: spec.md (Draft) + plan.md (v1.0) + tasks.md + clarify-report.md + contracts/
> **分析维度**: FR 覆盖完整性、plan-task 对齐、依赖链正确性、CRITICAL 解决确认、接口契约一致性

---

## 摘要

| 等级 | 数量 |
|------|------|
| CRITICAL | 2 |
| WARNING | 6 |
| INFO | 5 |

整体评价：三份制品在主干设计上高度一致。36 条 FR 全部覆盖，7 个 CRITICAL 问题在 plan.md 中均有解决方案。发现 2 个 CRITICAL 问题（契约缺失 + 心跳机制未在 SkillRunner 层面落地）和 6 个 WARNING 级别的细节偏差。

---

## 1. FR 覆盖完整性（spec.md FR → tasks.md Task）

### 分析方法

逐条比对 spec.md 中的 36 条 FR（FR-064-01 ~ FR-064-36）与 tasks.md 末尾的 FR 覆盖矩阵。

### 结论：36/36 已覆盖

tasks.md 底部提供了完整的 FR 覆盖矩阵，36 条 FR 均有至少一个 Task 对应。矩阵准确度验证如下：

| FR 范围 | 对应 Task 范围 | 覆盖状态 |
|---------|---------------|----------|
| FR-064-01 ~ FR-064-08（P0-A 并行工具调用） | T-064-01 ~ T-064-06 | 完整覆盖 |
| FR-064-09 ~ FR-064-12（P0-B 回填格式） | T-064-03, T-064-07, T-064-08 | 完整覆盖 |
| FR-064-13 ~ FR-064-20（P1-A Subagent 独立执行） | T-064-09 ~ T-064-15 | 完整覆盖 |
| FR-064-21 ~ FR-064-25（P1-B Subagent Announce） | T-064-16, T-064-17 | 完整覆盖 |
| FR-064-26 ~ FR-064-31（P2-A 上下文压缩） | T-064-18 ~ T-064-20 | 完整覆盖 |
| FR-064-32 ~ FR-064-36（P2-B 后台通知） | T-064-21, T-064-22 | 完整覆盖 |

**[INFO-01]** FR-064-19（Subagent UPDATE input-required）在 tasks.md 矩阵中映射到 T-064-12 和 T-064-13，但 T-064-12 的验收标准中没有显式提到 `UPDATE(input-required)` 消息的处理。T-064-13 的验收标准也未覆盖。建议在 T-064-12 或 T-064-13 的验收标准中补充 input-required 场景。

---

## 2. plan-task 对齐（plan.md 模块改动 → tasks.md Task）

### 分析方法

逐一比对 plan.md §3 "模块改动清单" 中列出的文件改动是否都有对应 Task。

### 结论：整体对齐良好，2 处偏差

**[WARNING-01]** plan.md §3 P2-A 列出 `skills/manifest.py` 需要新增 `compaction_model_alias` / `compaction_threshold_ratio` / `compaction_recent_turns` 三个字段。但 tasks.md 中将这些字段分散到了两个 Task：
- T-064-10 新增了 `heartbeat_interval_steps` 和 `max_concurrent_subagents`（P1-A 相关）
- T-064-18 新增了 `compaction_model_alias` / `compaction_threshold_ratio` / `compaction_recent_turns`（P2-A 相关）

但 T-064-10 的标题是"扩展 Subagent 相关字段"，它的 FR 标注中包含 FR-064-13（Task 关联）和 FR-064-17（心跳间隔），**不包含** compaction 相关的 FR。compaction 字段在 T-064-18 中覆盖。这个分配实际上是合理的，只是与 plan.md 中将所有 SkillManifest 字段归在同一处的组织方式不同。不构成真正遗漏，但需注意 T-064-10 和 T-064-18 都改 manifest.py，需协调。

**[WARNING-02]** plan.md §3 P1-B 列出 `gateway/services/subagent_lifecycle.py` 需要在结果发送时写入 `A2A_MESSAGE_RECEIVED` 事件（FR-064-22）。tasks.md 中 T-064-17（Orchestrator Subagent 结果接收）覆盖了 FR-064-22，但涉及文件列的改动包含 `subagent_lifecycle.py`（SubagentExecutor 完成时调用 Orchestrator.enqueue_subagent_result()），这与 T-064-12 对同一文件的改动存在时序依赖。T-064-17 依赖 T-064-12 已正确声明，但这两个 Task 对 `subagent_lifecycle.py` 的改动范围有重叠，实现时需注意合并冲突。

---

## 3. 依赖链正确性

### 分析方法

从 tasks.md 的依赖关系图和每个 Task 的依赖声明，构建 DAG，检查：
- 是否存在循环依赖
- 前置 Task 是否完备
- 跨 Phase 的依赖是否合理

### 结论：无循环依赖，1 处前置可能缺失

**依赖 DAG 验证**：

```
P0: T-01/02/03(无依赖) → T-04(→T-01) → T-05(→T-01,02,03,04) → T-06(→T-05)
    T-03 → T-07(→T-03) → T-08(→T-07)

P1: T-09/10(无依赖) → T-11(→T-09) → T-12(→T-09,10,11) → T-13(→T-12) → T-15(→T-13,14)
    T-12 → T-14(→T-12)
    T-09 → T-16(→T-09)
    T-12,16 → T-17(→T-12,16)

P2: T-18(无依赖) → T-19(→T-18) → T-20(→T-19)
    T-21(无依赖) → T-22(→T-21)
```

- **无循环依赖**：DAG 拓扑排序可正常完成
- **并行组声明正确**：A(T-01/02/03)、B(T-06/07)、C(T-09/10)、D(T-16)、E(T-18/21) 组内 Task 互不依赖

**[WARNING-03]** T-064-04（提取 `_execute_single_tool()`）声明仅依赖 T-064-01。但 T-064-04 的验收标准第 2 条要求 "将 ToolCallSpec.tool_call_id 传递到 ToolFeedbackMessage.tool_call_id"，这需要 `tool_call_id` 字段已存在于两个模型上——即需要 T-064-03 先完成。建议将 T-064-03 加入 T-064-04 的依赖列表。

**[INFO-02]** T-064-05 已正确依赖 T-064-01/02/03/04，涵盖了上述遗漏的传递依赖，因此最终执行顺序不受影响。但为严谨起见仍建议修正 T-064-04 的依赖声明。

---

## 4. CRITICAL 解决确认（clarify-report.md C-01~C-07 → plan.md/tasks.md）

### 分析方法

逐一检查 clarify-report.md 中 7 个 CRITICAL 问题是否在 plan.md 中有明确解决方案，且方案已落实到 tasks.md 中的具体 Task。

| # | 问题 | plan.md 解决方案 | tasks.md 落实 | 状态 |
|---|------|-----------------|---------------|------|
| C-01 | Task 模型缺少 `parent_task_id` | plan §2 C-01: 新增字段 + DB Migration + 索引 + TaskStore 适配 | T-064-09（模型+Migration）、T-064-11（TaskStore CRUD） | **已解决** |
| C-02 | ToolBrokerProtocol 缺少按名称查询接口 | plan §2 C-02: 新增 `get_tool_meta()` 方法 | T-064-01 | **已解决** |
| C-03 | 并行工具调用与 Feature 061 审批流交互 | plan §2 C-03: 分桶顺序固定、IRREVERSIBLE 逐个审批、审批等待 yield 回主循环 | T-064-05 验收标准 | **已解决** |
| C-04 | ToolFeedbackMessage 缺少 `tool_call_id` | plan §2 C-04: 新增字段 + 重写回填逻辑 | T-064-03（字段）、T-064-07（回填） | **已解决** |
| C-05 | Subagent 独立 SkillRunner 依赖来源 | plan §2 C-05: 从父 Worker manifest 衍生、共享 ToolBroker、新建 LiteLLMSkillClient | T-064-12（SubagentExecutor）、T-064-13（spawn 重写） | **已解决** |
| C-06 | 上下文压缩执行时机与机制 | plan §2 C-06: generate() 前同步执行、token 不计入 UsageTracker、独立 httpx 调用、补充 FAILED 事件 | T-064-18（ContextCompactor）、T-064-19（集成） | **已解决** |
| C-07 | Subagent 结果注入父 Worker 对话 | plan §2 C-07: event-driven 异步注入、SubagentResultQueue、每 step 前检查队列 | T-064-17 | **已解决** |

### 结论：7/7 全部解决

所有 CRITICAL 问题在 plan.md 中有详细的解决方案，且每个方案都落实到了具体的 Task 中。

**[CRITICAL-01]** 虽然 C-07 的方案描述了 "SkillRunner 每个 step 开始前检查队列" 的注入机制，但在 tasks.md 中没有对应的 Task 明确修改 SkillRunner 的主循环来添加队列检查逻辑。T-064-17 的涉及文件只列了 `orchestrator.py` 和 `subagent_lifecycle.py`，没有列 `runner.py`。这意味着父 Worker SkillRunner 读取 SubagentResultQueue 的代码无人负责。建议在 T-064-17 的涉及文件中补充 `skills/runner.py` 或 `skills/litellm_client.py`（根据 plan C-07 中提到的两种注入方式之一），并在验收标准中明确检查队列注入的测试用例。

---

## 5. 接口契约一致性（contracts/ → spec/plan/tasks）

### 分析方法

逐一比对 contracts/ 目录中 5 个文件定义的接口与 spec.md/plan.md/tasks.md 中的描述是否一致。

### 5.1 event-payloads.py

| 检查项 | 结果 |
|--------|------|
| `ToolBatchStartedPayload` 字段对齐 spec §Key Entities | spec 要求 `batch_id、tool_names、execution_mode`；契约新增了 `batch_size、agent_runtime_id、skill_id、bucket_*_count`。**超集，兼容** |
| `ToolBatchCompletedPayload` 字段对齐 spec §Key Entities | spec 要求 `batch_id、duration_ms、success_count、error_count`；契约新增了 `total_count、agent_runtime_id、skill_id`。**超集，兼容** |
| `ContextCompactionCompletedPayload` 对齐 FR-064-29 | spec 要求 `before_tokens、after_tokens、strategy_used`；契约新增了 `turns_before/after、compaction_model_alias、duration_ms`。**超集，兼容** |
| `ContextCompactionFailedPayload` 新增 | spec 未定义此事件但 clarify-report C-06 建议补充，plan §2 C-06 已采纳。**正确** |

**[INFO-03]** `ToolBatchStartedPayload` 中 `execution_mode` 字段描述了三种模式 `parallel/serial/gated_serial`。但 spec 的实现说明中，TOOL_BATCH 事件是包裹整个批次的（所有三个桶），而非按桶分别发射。如果一个 batch 包含多个桶（如同时有 NONE 和 REVERSIBLE 工具），`execution_mode` 应该填什么值？建议契约说明此字段的语义——是描述整个 batch 的主要模式，还是每个桶单独发射 BATCH 事件。

### 5.2 model-extensions.py

| 检查项 | 结果 |
|--------|------|
| `ToolCallSpecExtension.tool_call_id` | 与 spec FR-064-11/12、plan C-04 一致 |
| `ToolFeedbackMessageExtension.tool_call_id` | 与 plan C-04 一致；spec 原文未提及但 clarify-report C-04 建议补充 |
| `SkillExecutionContextExtension.parent_task_id` | 与 spec §Key Entities 一致 |
| `TaskExtension.parent_task_id` | 与 plan C-01 一致；含 DB Migration SQL |
| `SkillManifestExtension` | 包含 5 个字段，与 plan §4.1 完全一致 |
| `ToolBrokerProtocolExtension` | 与 plan C-02 一致 |
| `SSEHubBroadcastExtension` | 与 plan §5.2 一致 |
| `EventTypeExtension` | 列出 `TOOL_BATCH_STARTED/COMPLETED` + `CONTEXT_COMPACTION_FAILED`，与 plan §4.2 一致 |

**[INFO-04]** contracts/model-extensions.py 引用了源码行号（如 "skills/models.py 第 157-161 行"），这些行号在后续开发中可能变化。建议将行号改为类名锚点（如 "skills/models.py::ToolCallSpec"），已有部分使用了此风格。

### 5.3 notification-protocol.py

| 检查项 | 结果 |
|--------|------|
| `NotificationChannelProtocol.notify()` 签名 | 与 spec §Key Entities 中 `notify(task_id, event_type, payload) -> bool` 一致 |
| 新增 `send_approval_request()` | spec 未显式定义此方法，但 FR-064-33 要求 Telegram 发送审批请求含 inline keyboard。**plan 中 Telegram 审批在 P2-B 实现，此方法是合理扩展** |
| `NotificationServiceSpec` | 与 plan §3 P2-B 的描述一致 |
| `channel_name` 属性 | spec/plan 未提及，为合理新增 |

**[WARNING-04]** `NotificationChannelProtocol` 中 `event_type` 参数类型为 `str`，而 spec/plan 中描述为 `EventType`（枚举）。plan §5.3 的签名使用 `event_type: EventType`。建议契约保持与 plan 一致使用 `EventType` 枚举类型，而非 `str`。

### 5.4 subagent-executor.py

| 检查项 | 结果 |
|--------|------|
| `SpawnSubagentParams` | 与 plan C-05 一致；新签名包含所有必要参数 |
| `SubagentExecutorSpec` 伪代码 | 与 plan §3 P1-A SubagentExecutor 设计完全一致 |
| `A2ASubagentURISpec` | 与 plan 附录 B I-09 一致 |
| `SubagentResultInjectionSpec` | 与 plan C-07 一致 |

**[CRITICAL-02]** SubagentExecutorSpec 伪代码中 `_run_loop()` 没有包含心跳上报逻辑（FR-064-17）。spec 和 plan 均要求 "每 N 步发射 TASK_HEARTBEAT 事件"，但 SubagentExecutor 的 `_run_loop()` 直接调用 `self._runner.run()` 等待结果返回——SkillRunner.run() 是一个完整的执行循环，SubagentExecutor 无法在其内部"每 N 步"插入心跳逻辑。心跳上报需要在 SkillRunner 层面通过 hook 或 callback 实现，但 contracts/ 中未定义此 hook 接口。

建议：在 contracts/subagent-executor.py 中补充心跳 hook 机制的契约，例如 SkillRunner 支持 `on_step_completed` 回调，SubagentExecutor 在回调中发射 TASK_HEARTBEAT 事件。或者在 T-064-12 中明确说明 SkillRunner.run() 需要扩展以支持步级回调。

### 5.5 context-compactor.py

| 检查项 | 结果 |
|--------|------|
| `CompactionResult` | 字段完整，`strategies_used` 对应 spec FR-064-29 中的 `strategy_used` |
| `ContextCompactorSpec` 伪代码 | 三级压缩策略与 plan C-06 完全一致 |
| system prompt 保护 | 正确实现（`history[0] if role=system`） |
| 独立 httpx 调用 | 与 plan C-06 决策一致 |

**[WARNING-05]** `CompactionResult.strategies_used` 字段名为 `strategies_used`（复数），而 event-payloads.py 中 `ContextCompactionCompletedPayload.strategy_used` 字段名为 `strategy_used`（单数，但类型也是 `list[str]`）。虽然两个模型用途不同（一个是返回值，一个是事件 payload），但命名不一致容易导致数据映射错误。建议统一为 `strategies_used`。

**[WARNING-06]** ContextCompactorSpec 伪代码中 `_truncate_large_outputs()` 只检查 `role == "tool"` 的消息。但在 Responses API 路径下，工具结果可能不使用 `role` 字段（使用 `type: function_call_output`）。plan 附录 B I-07 指出"上下文压缩统一在 history 列表层面操作，两条 API 路径共享逻辑"。如果 Responses API 路径的历史消息格式不同，压缩逻辑需要适配。建议在 T-064-18 或 T-064-19 中明确两条路径的压缩兼容策略。

---

## 6. 额外发现

**[INFO-05]** tasks.md 中 T-064-02 的验收标准第 2 条要求"同步新增 `CONTEXT_COMPACTION_FAILED`"。这是合理的预留，但属于 P2-A 的范畴。将其放在 P0 阶段的 Task 中是可行的（只是加一个枚举值），但如果 P2-A 最终不实现，这个枚举值会成为死代码。这是一个组织决策，不影响正确性。

---

## 发现汇总

### CRITICAL（2 项）

| # | 发现 | 位置 | 建议 |
|---|------|------|------|
| CRITICAL-01 | SubagentResultQueue 的读取端（父 Worker SkillRunner 检查队列）未分配到任何 Task 的涉及文件 | tasks.md T-064-17 | 在 T-064-17 涉及文件中补充 `runner.py` 或 `litellm_client.py`，并在验收标准中新增队列消费的测试用例 |
| CRITICAL-02 | SubagentExecutor 伪代码中无心跳上报逻辑，SkillRunner 缺少步级回调 hook 契约 | contracts/subagent-executor.py | 补充心跳 hook 契约（如 `on_step_completed` 回调），或在 T-064-12 中明确 SkillRunner 扩展需求 |

### WARNING（6 项）

| # | 发现 | 位置 | 建议 |
|---|------|------|------|
| WARNING-01 | T-064-10 和 T-064-18 都改 manifest.py，需协调 | tasks.md | 在 T-064-18 备注中标注与 T-064-10 的文件重叠 |
| WARNING-02 | T-064-12 和 T-064-17 对 subagent_lifecycle.py 的改动范围重叠 | tasks.md | 实现时注意合并冲突协调 |
| WARNING-03 | T-064-04 应依赖 T-064-03（需要 tool_call_id 字段） | tasks.md T-064-04 依赖声明 | 将 T-064-03 加入 T-064-04 的依赖列表 |
| WARNING-04 | NotificationChannelProtocol.notify() 的 event_type 参数类型（str vs EventType）与 plan 不一致 | contracts/notification-protocol.py | 统一为 `EventType` 枚举类型 |
| WARNING-05 | CompactionResult.strategies_used 与 ContextCompactionCompletedPayload.strategy_used 命名不一致 | contracts/ | 统一字段名为 `strategies_used` |
| WARNING-06 | ContextCompactor 截断逻辑仅检查 role=="tool"，未适配 Responses API 路径的消息格式 | contracts/context-compactor.py | 在 T-064-18/19 中明确两条路径的压缩兼容策略 |

### INFO（5 项）

| # | 发现 | 位置 | 建议 |
|---|------|------|------|
| INFO-01 | FR-064-19（input-required）在 Task 验收标准中未显式覆盖 | tasks.md T-064-12/13 | 补充 input-required 场景的验收标准 |
| INFO-02 | T-064-04 缺失 T-064-03 依赖但不影响最终执行顺序（T-064-05 已覆盖） | tasks.md 依赖 DAG | 建议仍修正以保持严谨 |
| INFO-03 | TOOL_BATCH 事件中 execution_mode 语义不明（整 batch 还是单桶） | contracts/event-payloads.py | 建议说明此字段的语义或改为按桶发射 |
| INFO-04 | contracts/ 中引用源码行号，后续可能失效 | contracts/model-extensions.py | 改为类名锚点 |
| INFO-05 | CONTEXT_COMPACTION_FAILED 枚举值放在 P0 阶段 Task 中（预留） | tasks.md T-064-02 | 组织决策，无正确性影响 |
