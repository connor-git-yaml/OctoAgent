# Feature Specification: Pydantic Skill Runner

**Feature Branch**: `codex/feat-005-pydantic-skill-runner`
**Created**: 2026-03-02
**Status**: Draft
**Blueprint FR**: FR-SKILL-1（Skill 框架）
**Input**: User description: "根据 blueprint 和 m1-feature-split 开始 Feature 005 的研发工作。在技术调研和编码过程中请充分参考 AgentZero 与 OpenClaw 的优秀实践，如果发现设计文档不合理可以进行讨论并修改调整。"
**调研基础**: `research/tech-research.md`（tech-only）

---

## User Scenarios & Testing

### User Story 1 - 结构化 Skill 执行闭环（Priority: P1）

作为 Worker 开发者，我希望定义一个带 InputModel/OutputModel 的 Skill，并由系统执行完整闭环（输入校验 -> 模型输出校验 -> 完成信号），从而保证 Skill 行为可预测、可测试。

**Why this priority**: 这是 Feature 005 的核心价值，也是 Blueprint §8.4 的最小闭环，没有它后续工具调用、审批联动都无法落地。

**Independent Test**: 注册一个 `echo_skill`，输入合法参数后单次执行成功，输出通过 OutputModel 校验并返回 `complete=true`。

**Acceptance Scenarios**:

1. **Given** Skill 已声明 InputModel 和 OutputModel，**When** 传入合法输入执行 Skill，**Then** 系统完成输入校验、模型调用和输出校验，并返回结构化结果。
2. **Given** Skill 输出包含完成信号，**When** Runner 检测到完成信号，**Then** 当前 Skill 迭代立即终止，不再追加新步骤。

---

### User Story 2 - tool_calls 执行与结果回灌（Priority: P1）

作为 Worker 开发者，我希望 Skill 在需要时调用工具，并把工具结果结构化回灌到后续推理步骤，从而完成“模型决策 + 工具执行”的联动。

**Why this priority**: Blueprint 和 M1 拆分都要求 005 对接 004 的 ToolBroker 契约，这是 007 集成阶段的关键接口。

**Independent Test**: 使用 `file_summary_skill` 触发一次 `tool_call`，由 ToolBroker mock 返回结果，Skill 最终产出 summary。

**Acceptance Scenarios**:

1. **Given** Skill 输出包含合法 `tool_calls`，**When** Runner 执行该步骤，**Then** Runner 通过 ToolBrokerProtocol 调用工具并获取结构化 ToolResult。
2. **Given** ToolResult 返回错误标记，**When** Runner 回灌结果，**Then** 模型收到结构化错误上下文并继续重试或终止，不出现静默失败。
3. **Given** 工具结果体量超出上下文预算，**When** Runner 准备回灌，**Then** Runner 使用 artifact 引用或摘要而非全文回灌。

---

### User Story 3 - 失败重试与异常分流（Priority: P1）

作为平台维护者，我希望 Skill 执行失败时能够区分可重试、可修复和不可恢复错误，并按策略处理，从而提高成功率且保持失败可解释。

**Why this priority**: AgentZero/OpenClaw 的实践均表明“统一失败语义”是稳定运行的关键；Constitution C13 要求失败必须可解释。

**Independent Test**: 构造 OutputModel 校验失败场景，验证 Runner 生成结构化反馈并在 max_attempts 内重试；超过上限后进入失败终态。

**Acceptance Scenarios**:

1. **Given** 模型输出不满足 OutputModel，**When** Runner 处理该输出，**Then** Runner 记录校验失败并按 retry_policy 重试。
2. **Given** 连续重试达到上限，**When** 仍无法通过校验，**Then** Runner 返回明确失败分类和恢复建议。
3. **Given** 工具执行返回不可恢复错误，**When** Runner 识别该错误类型，**Then** 当前 Skill 终止并输出可审计错误信息。

---

### User Story 4 - 循环检测与终止保护（Priority: P2）

作为系统维护者，我希望 Runner 自动检测重复 tool_calls 或无进展循环，并在触发阈值时终止执行，从而防止 token 浪费和死循环。

**Why this priority**: 这是 OpenClaw 最有价值的防护模式之一，且 Blueprint §8.4.3 已明确列为 005 的高优先级借鉴项。

**Independent Test**: 构造连续三次相同工具签名调用，验证 Runner 触发循环告警并终止。

**Acceptance Scenarios**:

1. **Given** 相同 tool_calls 签名连续出现超过阈值，**When** Runner 检测到重复模式，**Then** Runner 终止当前 Skill 并记录循环原因。
2. **Given** Skill 步数达到 `max_steps`，**When** 未出现完成信号，**Then** Runner 强制终止并返回 step limit 错误。

---

### User Story 5 - 生命周期可观测与可审计（Priority: P2）

作为运维开发者，我希望每次 Skill 执行都有完整的状态、步骤与消耗记录，从而可以在 UI/日志中追踪问题与成本。

**Why this priority**: Constitution C2/C8 对事件记录和可观测是硬性要求。

**Independent Test**: 执行任意 Skill，一次成功和一次失败都能在事件流中看到 Skill 级开始/结束、模型调用、工具调用链路。

**Acceptance Scenarios**:

1. **Given** Skill 开始执行，**When** Runner 进入执行流程，**Then** 系统写入 Skill 开始事件并绑定 trace_id/task_id。
2. **Given** Skill 执行结束（成功或失败），**When** Runner 完成收尾，**Then** 系统写入终态事件与关键统计（steps、attempts、duration、error_type）。

---

## Edge Cases

- **EC-1**: InputModel 校验失败（输入缺字段或类型错误）— 必须在调用模型前直接失败并返回校验细节。
- **EC-2**: OutputModel 一直不合法 — 超过重试上限后失败，不能无限重试。
- **EC-3**: ToolBroker 不可用或未注册目标工具 — 返回结构化工具错误并进入可恢复分流。
- **EC-4**: 连续重复 tool_calls 导致循环 — 必须触发循环保护并终止。
- **EC-5**: 工具输出超大文本 — 只回灌摘要/引用，避免污染上下文。
- **EC-6**: Skill 描述文档缺失（description_md 路径不存在）— 记录警告并降级为短描述执行。

---

## Requirements

### Functional Requirements

#### Skill Manifest 与注册

- **FR-001**: 系统 MUST 支持声明 SkillManifest，至少包含 `skill_id`、`version`、`input_model`、`output_model`、`model_alias`、`tools_allowed`、`retry_policy`。
- **FR-002**: 系统 MUST 提供 SkillRegistry，用于注册、发现、读取 Skill 元数据。
- **FR-003**: 系统 MUST 支持 SkillManifest 可选字段 `description_md`，用于加载长描述；路径不存在时降级并记录告警。

#### SkillRunner 主流程

- **FR-004**: Runner MUST 在模型调用前校验 InputModel；校验失败时不得进入模型调用。
- **FR-005**: Runner MUST 对模型输出执行 OutputModel 校验，校验失败时生成结构化反馈并触发重试。
- **FR-006**: Runner MUST 支持完成信号（如 `complete` 或等价终止标记），检测到后立即终止当前 Skill 迭代。
- **FR-007**: 当输出包含 `tool_calls` 时，Runner MUST 通过 ToolBrokerProtocol 执行工具并回灌结构化结果。
- **FR-008**: Runner MUST 保留 ToolResult 的结构化字段（至少 `output/is_error/error/duration/artifact_ref`）用于回灌与审计。

#### 重试、异常与防护

- **FR-009**: Runner MUST 支持 retry_policy（至少 `max_attempts`），重试超过上限后返回明确失败原因。
- **FR-010**: Runner MUST 区分至少三类错误：可重试错误、校验错误、工具执行错误，并提供可解释分类。
- **FR-011**: Runner MUST 支持循环检测（基于 tool_calls 签名重复阈值）并在触发时终止执行。
- **FR-012**: Runner MUST 支持 `max_steps` 限制，超限后强制终止并记录原因。
- **FR-013**: Runner MUST 在工具结果回灌前做上下文预算检查，超限时使用摘要或 artifact 引用。

#### 可观测与合规

- **FR-014**: Skill 执行全过程 MUST 产生日志/事件链，至少覆盖 Skill 开始、Skill 结束、模型调用、工具调用四类关键节点。
- **FR-015**: 事件与日志 MUST 绑定 `task_id` 和 `trace_id`，并遵循敏感信息最小化原则。

#### 交付与验证

- **FR-016**: 系统 MUST 提供至少两个示例 Skill（`echo_skill`、`file_summary_skill`）用于端到端验证。
- **FR-017**: 系统 MUST 提供契约测试，验证 SkillRunner 与 ToolBrokerProtocol 的交互不依赖 ToolBroker 内部实现。

### Key Entities

- **SkillManifest**: Skill 的声明模型，定义输入输出契约、模型别名、允许工具集、重试策略与元信息。
- **SkillExecutionContext**: 单次 Skill 执行上下文，至少包含 task_id、trace_id、attempt、step。
- **SkillOutputEnvelope**: Skill 输出封装，包含业务输出、完成信号、tool_calls 与诊断字段。
- **SkillRunResult**: Runner 最终返回结果，包含状态、输出、错误分类、执行统计和事件引用。
- **ToolFeedbackMessage**: 工具执行回灌模型，保留结构化字段与可选 `parts`。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: `echo_skill` 端到端执行通过，输入与输出均通过模型校验。
- **SC-002**: `file_summary_skill` 在至少一次 tool_call 场景下执行通过，并完成结构化回灌。
- **SC-003**: OutputModel 校验失败场景中，Runner 能在 `max_attempts` 内重试并给出可解释失败分类。
- **SC-004**: 连续重复 tool_call 签名达到阈值时，Runner 能在 1 次检测周期内终止执行并记录循环原因。
- **SC-005**: 任意一次 Skill 执行都可追溯完整事件链（Skill 开始/结束 + Model + Tool）。

---

## Scope Exclusions

- 本 Feature 不实现审批决策矩阵（属于 Feature 006）。
- 本 Feature 不实现多 Agent/多 Workspace 的策略级权限覆盖（属于 M2）。
- 本 Feature 不实现完整 Skill Pipeline Graph Engine（保持 free loop Runner，Graph 留待后续阶段）。

---

## Clarifications

### Session 2026-03-02

**Q1 - 005 是否直接依赖 Feature 004 的具体 ToolBroker 实现？**

- **状态**: [AUTO-CLARIFIED: 仅依赖 Protocol，不依赖实现]
- **理由**: 为满足三轨并行策略与 007 集成替换要求，005 仅绑定 `ToolBrokerProtocol` 契约。

**Q2 - 循环终止信号字段使用哪种命名？**

- **状态**: [AUTO-CLARIFIED: 规范层抽象为“完成信号”]
- **理由**: Spec 聚焦行为，不绑定具体字段名；字段命名在 plan 阶段确定。

**Q3 - description_md 缺失时行为？**

- **状态**: [AUTO-CLARIFIED: 告警并降级执行]
- **理由**: 对齐 Constitution C6（Degrade Gracefully），避免文档缺失阻断主流程。

### Session 2026-03-02 Clarify

**CRITICAL 问题**: 无。

**自动澄清项**:
- 将“完成信号”定义为语义要求而非字段强绑定，避免规范过早冻结实现细节。
- 将“循环检测”最低要求固定为“重复签名阈值触发终止”，其余高级模式（振荡、多桶策略）留到后续版本。
- 将“上下文预算防护”定义为结果行为（摘要/引用）而非指定具体压缩算法，避免阻塞实现选型。
