# Feature Specification: Policy Engine + Approvals + Chat UI

**Feature Branch**: `feat/006-policy-engine-approvals`
**Created**: 2026-03-02
**Status**: Draft
**Blueprint FR**: FR-TOOL-3（工具权限门禁）、FR-CH-1[M1]（Chat UI + Approvals 面板）
**Blueprint 设计**: 8.6（Policy Engine）全面实现
**Constitution 对齐**: C1 Durability, C2 Events, C4 Two-Phase, C7 User-in-Control, C8 Observability
**前序依赖**: Feature 004（ToolMeta + PolicyCheckpoint Protocol + BeforeHook Protocol）
**调研基础**: `research/tech-research.md`（技术调研，无产品调研）

---

## User Scenarios & Testing

### User Story 1 - 不可逆工具操作自动触发审批 (Priority: P1)

作为 OctoAgent 的用户，当 Agent 准备执行一个不可逆操作（如删除文件、执行 shell 命令、修改生产数据）时，系统应自动拦截该操作并请求我的审批，以确保危险操作不会在我不知情的情况下被执行。

**Why this priority**: 这是安全治理层的核心价值。没有审批门禁，Agent 执行不可逆操作将构成安全风险。此 Story 直接对齐 Constitution C4（Side-effect Must be Two-Phase）和 C7（User-in-Control），是系统安全底线。

**Independent Test**: 可通过注册一个 `side_effect_level=irreversible` 的 mock 工具，触发 Agent 调用，验证该工具是否被拦截并进入审批等待状态。在无前端 UI 的情况下，通过 REST API 验证审批请求已注册，即可独立确认此 Story 的核心价值。

**Acceptance Scenarios**:

1. **Given** Agent 正在执行一个任务，需要调用一个标记为 `irreversible` 的工具，**When** 系统对该工具调用进行策略评估，**Then** 系统返回"需要审批"决策，任务进入等待审批状态，并生成一条审批请求记录。

2. **Given** 一个审批请求已注册且处于等待状态，**When** 用户通过 API 或 UI 批准该请求，**Then** 系统立即恢复工具执行，任务回到运行状态，并记录"已批准"事件。

3. **Given** 一个审批请求已注册且处于等待状态，**When** 用户通过 API 或 UI 拒绝该请求，**Then** 系统终止该工具调用，任务进入拒绝终态，并记录"已拒绝"事件。

4. **Given** 一个审批请求已注册且处于等待状态，**When** 审批等待时间超过 120 秒，**Then** 系统自动按"拒绝"处理，任务终止，并记录"已过期"事件。

---

### User Story 2 - 安全工具操作无需审批直接执行 (Priority: P1)

作为 OctoAgent 的用户，当 Agent 执行只读操作（如查询时间、读取文件列表）或可逆操作（如写入临时文件）时，这些操作应直接执行而无需我介入审批，以保证 Agent 的工作流畅性和效率。

**Why this priority**: 与 Story 1 互补，共同构成策略决策的完整矩阵。如果所有工具都需要审批，Agent 将失去自治能力，用户体验极差。此 Story 确保"只在必要时打扰用户"的设计原则（Constitution C7）。

**Independent Test**: 注册一个 `side_effect_level=none` 和一个 `side_effect_level=reversible` 的 mock 工具，分别触发调用，验证两者均不触发审批流程、直接返回执行结果。

**Acceptance Scenarios**:

1. **Given** Agent 需要调用一个标记为 `none`（只读）的工具，**When** 系统对该工具调用进行策略评估，**Then** 系统返回"允许"决策，工具立即执行并返回结果，不生成审批请求。

2. **Given** Agent 需要调用一个标记为 `reversible` 的工具，**When** 系统对该工具调用进行策略评估，**Then** 系统返回"允许"决策，工具立即执行并返回结果，不生成审批请求。

---

### User Story 3 - Policy Pipeline 多层策略过滤与决策追溯 (Priority: P1)

作为系统管理员（即用户本人），我希望策略评估按多层管道逐级执行，每一层的决策结果都附带来源标签，以便我在调试或审计时能准确定位"是哪一层规则做出了拦截或放行决策"。

**Why this priority**: 策略管道是 PolicyEngine 的架构核心。没有可追溯的决策链，用户无法理解系统为何拦截某个操作，也无法调整策略配置。此 Story 对齐 Constitution C8（Observability is a Feature）。M1 实现 2 层（Profile 过滤 + Global 规则），预留 M2 扩展到 4 层。

**Independent Test**: 构造不同 ToolProfile 和 SideEffectLevel 的工具组合，调用策略评估函数，验证每次评估结果中包含决策来源标签（如 `tools.profile` 或 `global.irreversible`），且后层不可放松前层决策。

**Acceptance Scenarios**:

1. **Given** 当前策略配置为 `standard` Profile，**When** 一个 `privileged` 级别的工具尝试执行，**Then** 第一层（Profile 过滤）返回"拒绝"决策，标签为 `tools.profile`，后续层不再评估。

2. **Given** 一个 `standard` 级别的工具通过了 Profile 过滤，且其 `side_effect_level=irreversible`，**When** 进入第二层（Global 规则）评估，**Then** 返回"需要审批"决策，标签为 `global.irreversible`。

3. **Given** 一个 `minimal` 级别、只读的工具，**When** 经过完整 Pipeline 评估，**Then** 两层均返回"允许"，最终决策为"允许"，决策记录包含两层的完整标签链。

4. **Given** 第一层返回"允许"，第二层返回"拒绝"，**When** 系统合并多层决策，**Then** 最终决策取最严格结果（"拒绝"），且决策记录标注收紧发生在第二层。

---

### User Story 4 - Two-Phase Approval 竞态安全保障 (Priority: P1)

作为 OctoAgent 的用户，我希望审批流程在并发和进程重启场景下仍然安全可靠——同一审批请求不会被重复注册、不会被多次消费、审批通过后有足够的宽限期让等待方收到结果。

**Why this priority**: Two-Phase Approval 的竞态防护是安全治理层的可靠性保障。如果审批状态不一致，可能导致危险操作被重复执行或审批结果丢失。此 Story 直接对齐 Constitution C1（Durability First）。

**Independent Test**: 通过模拟并发注册相同审批 ID、审批通过后立即再次尝试消费、进程重启后查询审批状态等场景，验证幂等性、原子消费和持久化恢复能力。

**Acceptance Scenarios**:

1. **Given** 一个审批请求已注册，**When** 使用相同 ID 再次注册，**Then** 系统返回已有的审批记录（幂等），不创建新的审批请求。

2. **Given** 一个审批请求已被批准（allow-once），**When** 系统消费该一次性令牌后再次尝试消费，**Then** 第二次消费返回失败，防止同一审批被重放。

3. **Given** 一个审批请求刚被用户批准，**When** 等待方在 15 秒宽限期内查询审批结果，**Then** 等待方能成功获取到审批决策。

4. **Given** 一个审批请求处于等待状态，**When** 系统进程重启，**Then** 重启后通过 API 查询仍能看到该 pending 审批，且可以继续审批。

---

### User Story 5 - Approvals 面板查看和处理待审批操作 (Priority: P1)

作为 OctoAgent 的用户，我希望在 Web 界面上有一个审批面板，能够看到所有待审批的操作（包含工具名称、参数摘要、风险说明），并能通过按钮快速做出"允许一次"、"总是允许"或"拒绝"的决策。

**Why this priority**: 审批面板是用户与安全治理层交互的主要界面。没有 UI，用户只能通过 API 审批，实际使用价值大幅降低。此 Story 使 Policy Engine 的安全治理能力对用户真正可用。

**Independent Test**: 创建一个或多个审批请求后，打开 Web 审批面板，验证面板正确展示待审批列表及每个请求的详细信息，点击审批按钮后请求状态正确更新。

**Acceptance Scenarios**:

1. **Given** 存在 3 个待审批请求，**When** 用户打开 Approvals 面板，**Then** 面板展示 3 条待审批记录，每条包含工具名称、参数摘要、风险说明和剩余时间。

2. **Given** Approvals 面板展示一条待审批请求，**When** 用户点击"允许一次"按钮，**Then** 该请求从待审批列表消失，对应的工具调用恢复执行。

3. **Given** Approvals 面板展示一条待审批请求，**When** 用户点击"拒绝"按钮，**Then** 该请求从待审批列表消失，对应的工具调用被终止。

4. **Given** 一个审批请求正在面板展示中，**When** 该请求在其他渠道（如 API）被处理或超时过期，**Then** 面板实时更新，该请求从列表中移除。

---

### User Story 6 - 基础 Chat UI 与 SSE 流式输出 (Priority: P2)

作为 OctoAgent 的用户，我希望有一个基础的 Web 聊天界面，能够输入消息并实时看到 Agent 的流式回复（逐字显示），以便我能直观地与 Agent 交互。

**Why this priority**: Chat UI 是 OctoAgent 作为 Personal AI OS 的核心交互界面。但 M1 阶段的核心价值在于安全治理层（Policy Engine + Approvals），Chat UI 作为辅助交互通道优先级略低于审批核心功能。M0 已有 SSE 基础设施，Chat UI 主要是前端消费层。

**Independent Test**: 在 Web 界面输入一条消息，验证消息成功发送到后端，Agent 的回复以流式形式（逐字/逐块）显示在界面上。

**Acceptance Scenarios**:

1. **Given** 用户打开 Chat UI 界面，**When** 用户在输入框中输入消息并发送，**Then** 消息被提交到后端，界面显示发送状态。

2. **Given** 一条消息已提交到后端并开始处理，**When** Agent 产生流式响应，**Then** Chat UI 逐块显示响应内容，用户可以在回复完成前看到部分结果。

3. **Given** Agent 在处理过程中触发了审批请求，**When** Chat UI 接收到审批通知事件，**Then** Chat UI 展示审批提示信息，并引导用户前往 Approvals 面板处理。

---

### User Story 7 - 审批事件全链路可审计 (Priority: P2)

作为系统管理员（即用户本人），我希望所有与策略评估和审批相关的操作都被记录为事件，包括策略决策、审批请求、审批结果和审批过期，以便我随时查看历史审批记录和策略执行轨迹。

**Why this priority**: 可审计性是安全治理层的必要属性，对齐 Constitution C2（Everything is an Event）和 C8（Observability）。但其价值依赖于 Story 1-5 的核心功能已可用，因此优先级为 P2。

**Independent Test**: 触发一个完整的审批流程（从策略评估到用户决策），然后通过 Event Store 查询相关事件，验证事件链完整且字段正确。

**Acceptance Scenarios**:

1. **Given** 一个工具调用经过策略评估后被放行，**When** 查询该 Task 的事件流，**Then** 包含一条策略决策事件，记录了决策结果（allow）和来源标签。

2. **Given** 一个工具调用触发了审批流并被用户批准，**When** 查询该 Task 的事件流，**Then** 事件流依次包含：策略决策事件（ask）、审批请求事件、审批批准事件。

3. **Given** 一个审批请求超时过期，**When** 查询该 Task 的事件流，**Then** 包含审批过期事件，记录了过期时间和自动拒绝原因。

---

### User Story 8 - 策略配置可调整且变更可审计 (Priority: P2)

作为 OctoAgent 的用户，我希望能够调整策略配置（如将某些 `reversible` 工具也设为需要审批，或将某些已知安全的 `irreversible` 命令加入白名单免审批），并且每次配置变更都被记录为事件。

**Why this priority**: 策略可配是 Constitution C7（User-in-Control）的体现。但 M1 阶段策略配置以代码内静态规则为主，动态配置变更是进阶需求。此 Story 确保配置变更的审计基础设施就绪，M2 实现完整的动态配置 API。

**Independent Test**: 变更策略配置（如修改默认策略级别），验证变更生效且 Event Store 中记录了配置变更事件。

**Acceptance Scenarios**:

1. **Given** 当前默认策略将 `reversible` 工具设为 allow，**When** 用户将策略调整为 `reversible` 工具需要 ask，**Then** 后续 `reversible` 工具调用触发审批流。

2. **Given** 用户变更了策略配置，**When** 查询系统事件，**Then** 包含一条策略配置变更事件，记录了变更前后的配置差异。

---

### User Story 9 - 审批面板实时更新 (Priority: P3)

作为 OctoAgent 的用户，我希望 Approvals 面板能够实时反映审批状态的变化——新的审批请求自动出现在列表中，已处理的请求自动消失——无需我手动刷新页面。

**Why this priority**: 实时更新提升用户体验，但非核心功能。即使需要手动刷新，审批功能仍然可用。此 Story 属于体验优化层。

**Independent Test**: 保持 Approvals 面板打开，从后端触发新的审批请求，验证面板在无手动刷新的情况下自动展示新请求。

**Acceptance Scenarios**:

1. **Given** Approvals 面板已打开且当前无待审批请求，**When** 后端产生一个新的审批请求，**Then** 面板在 3 秒内自动展示该请求，无需手动刷新。

2. **Given** Approvals 面板展示若干待审批请求，**When** SSE 连接中断后重新连接，**Then** 面板自动恢复为最新状态，不丢失任何待审批请求。

---

### Edge Cases

- **EC-1 (关联 FR-006, US-4)**: 审批请求注册后、用户尚未决策时，系统进程崩溃。重启后审批请求应可恢复为 pending 状态，用户仍可做出决策。
- **EC-2 (关联 FR-007, US-4)**: 同一审批 ID 被并发解决（如用户同时在两个浏览器窗口点击审批按钮）。系统应保证只有第一次解决生效，第二次返回失败。
- **EC-3 (关联 FR-002, US-3)**: Pipeline 某一层 evaluator 抛出异常。由于 PolicyCheckpoint 强制 `fail_mode=closed`，系统应拒绝该工具执行，而非默认放行。
- **EC-4 (关联 FR-005, US-1)**: Agent 连续调用同一 irreversible 工具多次。系统应为每次调用生成独立的审批请求，不复用之前的审批结果（除非用户选择了"总是允许"）。
- **EC-5 (关联 FR-008, US-5)**: 用户在审批面板点击"总是允许"后，相同工具的后续调用。系统应将该工具加入 allow-always 名单，后续调用在 Pipeline 中直接放行。
- **EC-6 (关联 FR-024, US-6)**: Chat UI 的 SSE 连接在 Agent 回复过程中断开。重连后 Chat UI 应通过 API 获取完整消息历史，不丢失已产生的回复内容。
- **EC-7 (关联 FR-001, US-2)**: Profile 过滤层的 allowlist 配置意外排除了所有核心工具。系统应发出防御性警告，避免 Agent 完全失去工具调用能力。
- **EC-8 (关联 FR-005, US-1)**: 审批超时时间（120s）与外层 Task 超时时间冲突。Task 进入 WAITING_APPROVAL 状态时，Task 级别的超时计时器应暂停，审批超时独立管理。

---

## Requirements

### Functional Requirements

#### Policy Pipeline

- **FR-001**: 系统 **MUST** 提供多层策略评估管道，M1 实现两层：第一层为工具 Profile 过滤（基于 ToolProfile 分级），第二层为全局规则评估（基于 SideEffectLevel）。管道为纯函数设计，无副作用。
  - 追踪: US-3
  - 测试: 给定不同 ToolProfile 和 SideEffectLevel 组合的工具，调用 Pipeline 评估函数，验证输出的决策（allow/ask/deny）符合预期。

- **FR-002**: 策略管道中每一层的决策结果 **MUST** 附带一个来源标签（label），用于标识该决策由哪一层规则产生（例如 `tools.profile`、`global.irreversible`）。
  - 追踪: US-3, US-7
  - 测试: 检查 Pipeline 评估结果中的 label 字段是否与执行的层级一致。

- **FR-003**: 策略管道 **MUST** 遵循"只收紧不放松"原则——后续层只能将决策从 allow 收紧为 ask 或 deny，不可将 deny 放松为 allow。
  - 追踪: US-3
  - 测试: 构造第一层返回 deny 的场景，验证第二层即使评估为 allow，最终决策仍为 deny。

- **FR-004**: Profile 过滤层 **MUST** 根据当前执行上下文的 ToolProfile 级别过滤工具。不在当前 Profile 范围内的工具直接拒绝。Profile 层 **SHOULD** 包含防御性校验，检测 allowlist 是否意外排除核心工具，若检测到则发出警告。
  - 追踪: US-3, EC-7
  - 测试: 在 `standard` Profile 下调用 `privileged` 工具，验证被拒绝；allowlist 排除所有工具时验证警告产生。

#### 策略决策

- **FR-005**: 系统 **MUST** 支持三种策略决策结果：allow（允许执行）、ask（需要用户审批）、deny（拒绝执行）。默认策略为：`side_effect_level=none` 对应 allow；`side_effect_level=reversible` 对应 allow；`side_effect_level=irreversible` 对应 ask。
  - 追踪: US-1, US-2
  - 测试: 分别注册三种 SideEffectLevel 的工具，验证默认决策与预期一致。

- **FR-006**: 策略决策事件 **MUST** 被记录到 Event Store，包含决策结果、来源标签、工具名称和 SideEffectLevel。
  - 追踪: US-7
  - 测试: 触发策略评估后，查询 Event Store 验证 POLICY_DECISION 事件存在且字段完整。

#### Two-Phase Approval

- **FR-007**: 系统 **MUST** 支持 Two-Phase Approval 流程：第一阶段为审批请求注册（同步、幂等），第二阶段为异步等待用户决策。注册使用唯一 approval_id，相同 ID 重复注册返回已有记录。
  - 追踪: US-4
  - 测试: 使用相同 approval_id 调用注册接口两次，验证返回相同的审批记录且不创建新记录。

- **FR-008**: 系统 **MUST** 支持三种审批决策：allow-once（允许一次）、allow-always（总是允许同类操作）、deny（拒绝）。allow-once 为一次性令牌，消费后不可再次使用。
  - 追踪: US-4, US-5, EC-5
  - 测试: 对同一审批请求执行 allow-once 后再次尝试消费，验证第二次返回失败。

- **FR-009**: 系统 **MUST** 在审批请求解决后保留该记录一段宽限期（默认 15 秒），允许迟到的等待方在宽限期内获取决策结果。
  - 追踪: US-4
  - 测试: 审批解决后立即和 10 秒后分别查询，均能获取到决策结果；16 秒后查询返回不存在。

- **FR-010**: 系统 **MUST** 为每个审批请求设置超时时间（默认 120 秒）。超时后自动按 deny 处理，并记录 APPROVAL_EXPIRED 事件。
  - 追踪: US-1, EC-8
  - 测试: 注册审批请求后不做决策，等待超时后验证状态变为已过期，Event Store 记录过期事件。

- **FR-011**: 审批状态 **MUST** 持久化到 Event Store（双写：内存状态 + 事件记录）。系统重启后 **MUST** 能从 Event Store 恢复未完成的审批请求。
  - 追踪: US-4, EC-1
  - 测试: 注册审批请求后模拟进程重启，重启后通过 API 查询验证 pending 审批仍可见。

#### 审批工作流与状态机

- **FR-012**: 当策略决策为 ask 时，系统 **MUST** 执行以下状态流转：生成 APPROVAL_REQUESTED 事件，Task 进入 WAITING_APPROVAL 状态。用户批准后生成 APPROVED 事件，Task 回到 RUNNING 状态。用户拒绝后生成 REJECTED 事件，Task 进入终态。
  - 追踪: US-1, US-7
  - 测试: 完整执行审批流程（触发 -> 批准/拒绝），验证 Task 状态流转和事件序列正确。

- **FR-013**: 系统 **MUST** 扩展现有 Task 状态机的合法流转规则，支持 RUNNING -> WAITING_APPROVAL、WAITING_APPROVAL -> RUNNING 和 WAITING_APPROVAL -> REJECTED 三种新的状态转换。
  - 追踪: US-1
  - 测试: 尝试上述三种状态转换，验证 `validate_transition()` 返回 True；尝试非法转换（如 WAITING_APPROVAL -> SUCCEEDED）返回 False。

- **FR-014**: Task 进入 WAITING_APPROVAL 状态时，Task 级别的超时计时器 **SHOULD** 暂停，审批超时由 ApprovalManager 独立管理。**[M1 延迟]** M1 尚无 Task 级超时机制，此项延迟到 M2 实现。当前审批超时完全由 ApprovalManager 管理，不影响功能正确性。
  - 追踪: EC-8
  - 测试: 设置 Task 超时为 60s，审批等待 90s 后批准，验证 Task 不因超时而失败。

#### PolicyEngine 与 ToolBroker 集成

- **FR-015**: PolicyEngine **MUST** 通过实现 Feature 004 定义的 PolicyCheckpoint Protocol 接入 ToolBroker 的 before hook 链。PolicyCheckpoint 的 `fail_mode` 强制为 `closed`——评估过程中任何异常都导致拒绝执行。
  - 追踪: US-1, US-2, EC-3
  - 测试: 注册 PolicyCheckHook 到 ToolBroker，模拟 evaluator 抛出异常，验证工具被拒绝执行（非默认放行）。

- **FR-016**: 当 PolicyEngine 对 `irreversible` 工具调用产生 ask 决策时，BeforeHook **MUST** 在 hook 内部注册审批请求并异步等待用户决策，而非将审批状态泄漏给 ToolBroker。
  - 追踪: US-1
  - 测试: 在 hook 内部等待审批决策后，验证 ToolBroker 收到的 BeforeHookResult 为 proceed=True（批准）或 proceed=False + rejection_reason（拒绝）。

- **FR-017**: ToolBroker 对 `irreversible` 工具 **MUST** 检查是否注册了 PolicyCheckpoint hook。如果未注册，强制拒绝执行（FR-010a 对齐）。
  - 追踪: US-1
  - 测试: 注册 `irreversible` 工具但不注册 PolicyCheckpoint hook，调用 execute 验证返回拒绝结果。

#### Approvals REST API

- **FR-018**: 系统 **MUST** 提供 `GET /api/approvals` 端点，返回当前所有待审批请求的列表，包含 approval_id、工具名称、参数摘要、风险说明、剩余时间等信息。
  - 追踪: US-5
  - 测试: 创建多个审批请求后调用 GET /api/approvals，验证返回列表正确且字段完整。

- **FR-019**: 系统 **MUST** 提供 `POST /api/approve/{approval_id}` 端点，接受审批决策（allow-once / allow-always / deny）。对已解决或不存在的 approval_id 返回明确的错误响应。
  - 追踪: US-5
  - 测试: 对有效的 pending 审批发送 approve 请求，验证返回成功；对已过期的审批发送请求，验证返回错误。

#### 前端 Approvals 面板

- **FR-020**: Web 前端 **MUST** 提供 Approvals 面板组件，展示待审批请求列表，每条请求显示工具名称、参数摘要、风险说明和剩余倒计时。
  - 追踪: US-5
  - 测试: 存在待审批请求时打开面板，验证所有信息正确展示。

- **FR-021**: Approvals 面板 **MUST** 为每条待审批请求提供三个操作按钮：允许一次（Allow Once）、总是允许（Always Allow）、拒绝（Deny）。点击按钮后调用 Approvals REST API 并更新面板状态。
  - 追踪: US-5
  - 测试: 点击各按钮，验证 API 调用成功且面板更新。

- **FR-022**: Approvals 面板 **SHOULD** 通过 SSE 实时接收审批状态变更通知，新的审批请求自动出现、已处理的请求自动消失。在 SSE 不可用时，**MUST** 提供定期轮询兜底（间隔不超过 30 秒）。
  - 追踪: US-9, EC-6
  - 测试: SSE 连接正常时验证实时更新；断开 SSE 后验证轮询兜底生效。

#### 前端 Chat UI

- **FR-023**: Web 前端 **MUST** 提供基础 Chat UI 组件，包含消息输入框和消息展示区域。用户可输入文本消息并提交到后端。
  - 追踪: US-6
  - 测试: 在输入框输入消息并提交，验证消息成功发送到后端。

- **FR-024**: Chat UI **MUST** 支持 SSE 流式输出——Agent 的回复以流式形式逐块展示，用户可在回复完成前看到部分结果。
  - 追踪: US-6
  - 测试: 发送消息后验证回复以流式形式逐块渲染。

- **FR-025**: Chat UI **SHOULD** 在 Agent 触发审批请求时展示审批提示信息，引导用户前往 Approvals 面板处理。
  - 追踪: US-6
  - 测试: 触发审批后验证 Chat UI 展示审批提示。

#### 事件与可观测性

- **FR-026**: 系统 **MUST** 在 EventType 枚举中新增以下事件类型：APPROVAL_REQUESTED（审批请求已注册）、APPROVAL_APPROVED（审批已批准）、APPROVAL_REJECTED（审批已拒绝）、APPROVAL_EXPIRED（审批已过期）、POLICY_DECISION（策略决策记录）、POLICY_CONFIG_CHANGED（策略配置变更）。
  - 追踪: US-7
  - 测试: 验证新增 EventType 值可正确创建事件并写入 Event Store。

- **FR-027**: 策略配置变更 **SHOULD** 生成事件并记录到 Event Store，包含变更前后的配置差异。
  - 追踪: US-8
  - 测试: 修改策略配置后验证 Event Store 包含变更事件。

#### 安全与脱敏

- **FR-028**: 审批 payload（包括 ApprovalRequest 中的参数摘要、Event Store 中的审批事件、Approvals 面板展示内容）**MUST** 对标记为敏感的参数值进行脱敏处理（如密钥类参数替换为掩码 `***`），确保审批面板展示和事件记录中不暴露原始敏感值。脱敏规则 **MUST** 复用 Feature 004 ToolBroker 的 Sanitizer 机制。
  - 追踪: US-5, US-7（Constitution C5 Least Privilege + C8 Observability 脱敏要求）
  - 测试: 注册一个包含敏感参数（如 api_key）的工具调用，触发审批后验证 ApprovalRequest 参数摘要、Event payload 和 Approvals 面板展示中该参数均已脱敏。

### Key Entities

- **PolicyDecision**: 策略评估的决策结果。包含决策动作（allow/ask/deny）、来源标签（label）、原因说明、关联的工具名称和副作用级别。是策略管道的核心输出物。

- **ApprovalRequest**: 审批请求记录。包含唯一 approval_id、关联 task_id、工具名称、参数摘要（脱敏后）、风险说明、触发审批的策略层 label、过期时间。是 Two-Phase Approval 的注册产物。

- **ApprovalDecision**: 用户的审批决策。三种取值：allow-once（一次性允许）、allow-always（总是允许同类操作）、deny（拒绝）。由用户通过 Approvals 面板或 REST API 提交。

- **PolicyProfile**: 策略配置档案。定义不同场景下的策略规则（各 SideEffectLevel 对应的默认决策、白名单等）。M1 阶段为代码内静态配置，M2 支持动态变更。

- **PolicyStep**: 策略管道中的一个评估层。包含评估函数和来源标签。多个 PolicyStep 按顺序组成完整的 Policy Pipeline。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 所有 `irreversible` 工具调用在首次执行前触发审批流，通过率为 100%（无遗漏）。

- **SC-002**: 所有 `none` 和 `reversible` 工具调用在默认策略下直接执行，无不必要的审批打扰，通过率为 100%。

- **SC-003**: 审批请求从注册到用户可见（Approvals 面板展示或 API 可查询）的延迟不超过 3 秒。

- **SC-004**: 用户做出审批决策后，工具执行恢复或终止的延迟不超过 2 秒。

- **SC-005**: 审批超时（120 秒）到期后，系统在 5 秒内完成自动 deny 处理并记录过期事件。

- **SC-006**: 系统进程重启后，所有未完成的审批请求可在 30 秒内恢复为 pending 状态并重新可见。

- **SC-007**: 每条策略决策和审批事件均可通过 Event Store 查询追溯，事件覆盖率为 100%。

- **SC-008**: Chat UI 的 SSE 流式输出首字节显示延迟不超过 1 秒（从 Agent 开始生成回复到用户看到第一个字符）。

- **SC-009**: Approvals 面板在 SSE 连接正常时，新审批请求出现延迟不超过 3 秒；SSE 断线时轮询兜底间隔不超过 30 秒。

---

## Appendix: Scope Boundaries

### M1 范围内（本 spec 覆盖）

- Policy Pipeline 前 2 层（Profile 过滤 + Global 规则）
- Three-outcome 决策（allow/ask/deny）
- Two-Phase Approval（幂等注册 + 异步等待 + 原子消费 + 宽限期 + 超时）
- 审批状态持久化与启动恢复
- WAITING_APPROVAL 状态机扩展
- Approvals REST API（GET + POST）
- Web Approvals 面板（三按钮决策 + 实时更新）
- 基础 Chat UI（消息输入 + SSE 流式输出）
- 审批事件全链路记录

### M2+ 延伸（本 spec 不覆盖）

- Policy Pipeline Layer 3（Agent 级策略）+ Layer 4（Group 级策略）
- 策略配置动态 API（运行时增删改查策略规则）
- allow-always 白名单持久化（Safe Bins）
- Telegram 渠道审批（inline keyboard 交互）
- 消息级 Guard（rewrite 决策类型）
- 交互式审批 UI（M2 Feature 009 激活）
- 多用户/多租户策略隔离

### Resolved Ambiguities

- **[AUTO-RESOLVED: Pipeline 层数]**: M1 实现 2 层（Profile + Global），而非 Blueprint 规划的完整 4 层。理由：m1-feature-split 明确指定"M1 实现 Layer 1 + Layer 2"，Layer 3/4 留给 M2 扩展。
- **[AUTO-RESOLVED: 审批等待实现方式]**: PolicyCheckHook 在 hook 内部异步等待审批决策，而非由 ToolBroker 感知审批状态。理由：技术调研推荐此方案，减少对 Feature 004 契约的变更，OpenClaw 已验证此模式。

---

## Clarifications

### Session 2026-03-02

| # | 问题 | 自动选择 | 理由 |
| --- | ---- | ------- | ---- |
| 1 | EventType 命名：m1-feature-split 中使用 `APPROVED`/`REJECTED`（无前缀），spec 使用 `APPROVAL_APPROVED`/`APPROVAL_REJECTED`（有前缀），两者不一致 | 采用 spec 中的 `APPROVAL_APPROVED`/`APPROVAL_REJECTED` 带前缀版本 | 带前缀可避免与 Task 级别的 REJECTED 状态混淆（m1-feature-split 自身也注明"区别于 Task 级 REJECTED"），且 APPROVAL_EXPIRED 和 APPROVAL_REQUESTED 已使用前缀风格，保持一致性 |
| 2 | PolicyCheckHook 与 PolicyCheckpoint 的接口适配方式：Feature 004 定义的 `PolicyCheckpoint.check()` 返回 `CheckResult(allowed, reason, requires_approval)`，但 spec FR-015/FR-016 描述的 PolicyCheckHook 使用 Pipeline 的 `PolicyDecision(action: allow/ask/deny)` 作为决策模型，两者有语义差距 | PolicyCheckHook 内部使用 PolicyPipeline 产生 `PolicyDecision`，然后将其映射为 `CheckResult` 返回给 ToolBroker：`allow -> CheckResult(allowed=True)`，`ask -> CheckResult(allowed=False, requires_approval=True)` 并在内部等待审批，`deny -> CheckResult(allowed=False, requires_approval=False)` | PolicyCheckpoint Protocol 是 Feature 004 锁定契约，不可变更。PolicyCheckHook 作为适配器层负责语义映射。tech-research 8.4 节的代码示例已验证此模式可行 |
| 3 | allow-always 白名单在 M1 范围内的持久化策略：FR-008 定义了 `allow-always` 决策类型，EC-5 描述了该工具加入 allow-always 名单，但 Scope Boundaries 将"allow-always 白名单持久化（Safe Bins）"列为 M2+ 延伸 | M1 阶段 allow-always 仅保持在内存中（进程重启后失效），M2 实现持久化 | spec Scope Boundaries 明确排除持久化。M1 的 allow-always 通过 ApprovalManager 内存字典实现，进程重启后用户需重新审批。这是可接受的 MVP 限制 |
| 4 | Pipeline deny 是否短路返回：FR-003 规定"后续层只能收紧不能放松"，但未明确 deny 是否应短路跳过后续层 | Pipeline 遇到 deny 立即短路返回，不继续执行后续层 | deny 是最严格决策，后续层无法改变结果（只能收紧不能放松）。短路返回避免不必要计算。tech-research 反模式表（附录 B #4）明确建议"deny 应立即短路返回"。OpenClaw 实现也采用此模式 |
| 5 | Safe Bins 白名单在 M1 的处理：m1-feature-split 提及"Safe Bins 白名单：预置安全命令列表（git, python, npm 等）"，但 spec FR 中未覆盖此能力 | M1 不实现 Safe Bins 白名单，仅通过 SideEffectLevel 驱动默认策略 | Safe Bins 是 M2+ 的 allow-always 持久化机制的一部分。M1 的决策完全由 SideEffectLevel + ToolProfile 驱动，无需预置命令白名单。m1-feature-split 中 Safe Bins 仅作为"PolicyEngine 核心"的辅助说明，非验收标准 |
