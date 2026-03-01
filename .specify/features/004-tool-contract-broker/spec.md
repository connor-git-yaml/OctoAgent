# Feature Specification: Tool Contract + ToolBroker

**Feature Branch**: `feat/004-tool-contract-broker`
**Created**: 2026-03-01
**Status**: Draft
**Blueprint FR**: FR-TOOL-1（工具契约化）、FR-TOOL-2（工具调用结构化）
**Track**: A（基础设施）— 与 Feature 005/006 三轨并行
**Input**: 建立工具治理基础设施——工具可声明、可反射、可执行、大输出可裁切；同时输出接口契约供 005/006 并行开发引用。
**调研基础**: `research/tech-research.md`（技术调研，推荐方案 A: Pydantic-Native 融合方案）

---

## User Scenarios & Testing

### User Story 1 — 工具契约声明（Priority: P1）

作为**工具开发者**，我希望通过在函数上添加声明性标注，即可完成工具的元数据定义（名称、描述、副作用等级、权限 Profile、工具分组），系统自动从函数签名和类型注解生成参数的 JSON Schema，确保代码即契约（code = schema），无需手动编写或同步 schema 文件。

**Why this priority**: 这是整个工具治理体系的基石。Constitution 原则 3 要求"工具 schema 必须与代码签名一致（单一事实源）"，且 Feature 005（Skill Runner）和 Feature 006（Policy Engine）均依赖 ToolMeta 模型。没有契约声明能力，后续的注册、发现、执行、门禁都无从谈起。

**Independent Test**: 可独立测试——编写一个带类型注解和 docstring 的 Python 函数，附加声明性标注，验证系统自动生成的 ToolMeta 中 JSON Schema 与函数签名完全一致，side_effect_level 正确填充。

**Acceptance Scenarios**:

1. **Given** 一个带完整类型注解和函数文档注释的 Python 函数，且标注了 side_effect_level=none、tool_profile=minimal、tool_group="system"，**When** 系统对该函数执行 Schema 反射，**Then** 生成的 ToolMeta 中 JSON Schema 参数名称、类型、描述与函数签名完全一致，side_effect_level / tool_profile / tool_group 字段正确填充。
2. **Given** 一个带嵌套对象参数的异步函数，**When** 系统对该函数执行 Schema 反射，**Then** 生成的 JSON Schema 正确包含嵌套对象的结构定义，且标记该工具为异步工具。
3. **Given** 一个函数未标注 side_effect_level（即缺失必要的副作用声明），**When** 系统尝试对该函数执行 Schema 反射，**Then** 系统拒绝注册并给出明确的错误信息，指明缺少 side_effect_level 声明。

---

### User Story 2 — 工具注册与发现（Priority: P1）

作为**系统编排层（Orchestrator/Worker）**，我希望通过统一的中介（ToolBroker）注册、发现和查询可用工具，并能按权限 Profile 和逻辑分组过滤工具集，使得不同权限等级的执行上下文只能看到被授权的工具子集。

**Why this priority**: 工具发现和过滤是工具执行的前置条件。Feature 006（Policy Engine）依赖 ToolBroker 的 Profile 过滤作为门禁的第一道防线。没有集中的注册和发现机制，工具管理将变得碎片化。

**Independent Test**: 可独立测试——注册若干不同 Profile 和 Group 的工具，然后以不同 Profile 查询，验证返回的工具集符合过滤规则。

**Acceptance Scenarios**:

1. **Given** ToolBroker 中已注册 3 个工具（1 个 minimal + 1 个 standard + 1 个 privileged），**When** 以 profile=minimal 查询可用工具，**Then** 仅返回 1 个 minimal 级别的工具。
2. **Given** ToolBroker 中已注册多个不同 tool_group 的工具（如 "system" 和 "filesystem"），**When** 按 group="filesystem" 查询，**Then** 仅返回属于 "filesystem" 分组的工具。
3. **Given** 尝试注册两个同名工具（相同 name），**When** 第二个工具注册时，**Then** 系统拒绝注册并给出明确的名称冲突错误信息。
4. **Given** ToolBroker 中无任何已注册工具，**When** 查询可用工具，**Then** 返回空列表，不抛出异常。

---

### User Story 3 — 工具执行与事件追踪（Priority: P1）

作为**系统编排层**，我希望通过 ToolBroker 执行工具调用，执行过程自动生成完整的事件链（开始、完成/失败），支持声明式超时控制，使得每次工具调用都可追溯、可审计。

**Why this priority**: 工具执行是 ToolBroker 的核心职能。Constitution 原则 2 要求"工具调用必须生成事件记录"，原则 8 要求"每个任务可看到已执行步骤、消耗、失败原因"。Feature 007 集成阶段的端到端流程（LLM -> Skill -> ToolBroker -> 结果回灌）依赖此能力。

**Independent Test**: 可独立测试——通过 ToolBroker 执行一个已注册的工具，验证返回结构化的 ToolResult，且 EventStore 中生成了 TOOL_CALL_STARTED + TOOL_CALL_COMPLETED 事件。

**Acceptance Scenarios**:

1. **Given** 一个 side_effect_level=none 的工具已注册到 ToolBroker，**When** 通过 ToolBroker 执行该工具并传入合法参数，**Then** 返回结构化的 ToolResult（含 output、is_error=false、duration），且 EventStore 中按序记录了 TOOL_CALL_STARTED 和 TOOL_CALL_COMPLETED 事件。
2. **Given** 一个工具声明了 timeout_seconds=5，**When** 该工具执行耗时超过 5 秒，**Then** ToolBroker 取消执行，返回 ToolResult（is_error=true、error 包含超时信息），且 EventStore 中记录 TOOL_CALL_FAILED 事件。
3. **Given** 一个工具在执行过程中抛出异常，**When** ToolBroker 捕获到该异常，**Then** 返回 ToolResult（is_error=true、error 包含异常分类和消息），且 EventStore 中记录 TOOL_CALL_FAILED 事件（含错误分类和可恢复性标记）。
4. **Given** 一个同步函数注册为工具，**When** 通过 ToolBroker 执行，**Then** 系统自动将同步调用包装为异步执行，不阻塞事件循环，返回结果与异步工具一致。

---

### User Story 4 — 大输出自动裁切（Priority: P1）

作为**系统编排层**，我希望当工具输出超过指定阈值时，系统自动将完整输出存入 Artifact Store 并在上下文中保留精简的引用摘要，使得 LLM 上下文不被大量无关内容污染（对齐 Constitution 原则 11: Context Hygiene），同时完整结果仍可按需检索。

**Why this priority**: 大输出裁切直接影响 LLM 的推理质量和 token 成本。Constitution 原则 11 明确禁止"把长日志/大文件原文直接塞进主上下文"。此能力对工具开发者零侵入（工具无需感知裁切逻辑），是 ToolBroker hook 机制的关键验证场景。

**Independent Test**: 可独立测试——执行一个返回超长字符串（>500 char）的工具，验证 ToolResult 中包含 artifact 引用而非完整输出，且 ArtifactStore 中可检索到完整内容。

**Acceptance Scenarios**:

1. **Given** 一个工具返回了 800 字符的输出（超过默认阈值 500 字符），**When** ToolBroker 执行完成后的后处理阶段，**Then** ToolResult 中的 output 被替换为精简的引用信息（包含 artifact ID 和内容摘要/前缀），完整输出已存入 ArtifactStore。
2. **Given** 一个工具返回了 300 字符的输出（未超过阈值），**When** ToolBroker 执行完成后的后处理阶段，**Then** ToolResult 中的 output 保持完整原文，不触发裁切和 Artifact 存储。
3. **Given** 裁切阈值已在全局或特定工具级别被配置为 1000 字符，**When** 工具返回 800 字符的输出，**Then** 不触发裁切（因未超过自定义阈值）。

---

### User Story 5 — Hook 扩展机制（Priority: P1）

作为**系统开发者**，我希望 ToolBroker 提供 before/after 扩展点（Hook），使得横切关注点（可观测性记录、策略门禁预检、输出裁切、审计日志）可以通过独立的 Hook 模块插入执行管线，而不修改工具本身或 Broker 核心逻辑。

**Why this priority**: Hook 机制是 ToolBroker 的可扩展性核心。大输出裁切（US-4）通过 after hook 实现；Feature 006 的 PolicyCheckpoint 通过 before hook 接入。没有 Hook 机制，这些横切关注点将侵入 Broker 核心逻辑，违反单一职责原则。

**Independent Test**: 可独立测试——注册一个自定义 before hook（如参数记录）和一个 after hook（如结果日志），执行工具后验证两个 hook 均按预期顺序执行。

**Acceptance Scenarios**:

1. **Given** 注册了 3 个 before hook（优先级分别为 10、20、30），**When** 执行一个工具，**Then** before hook 按优先级从低到高（10 -> 20 -> 30）依次执行。
2. **Given** 注册了一个 before hook 返回"拒绝执行"的信号（模拟 Policy 门禁拒绝），**When** 执行一个工具，**Then** 工具不被执行，ToolResult 标记为 is_error=true 且包含拒绝原因。
3. **Given** 一个 after hook 在执行过程中抛出异常，**When** 该异常发生，**Then** ToolBroker 记录错误日志但不影响工具执行结果的返回（降级策略：log-and-continue）。[AUTO-RESOLVED: after hook 失败采用 log-and-continue 策略而非 fail-fast，原因是 after hook 不应影响已完成的工具执行结果，与 Constitution 原则 6（Degrade Gracefully）一致]

---

### User Story 6 — 接口契约输出（Priority: P1）

作为 **Feature 005/006 的开发者**，我希望 Feature 004 输出稳定的接口契约文档和 Protocol 定义（ToolBrokerProtocol、ToolMeta、ToolResult、PolicyCheckpoint Protocol），使得我可以在并行开发阶段基于这些契约编写 mock 实现，后续集成时无缝替换为真实依赖。

**Why this priority**: Feature 004 是 Track A 基础设施，005（Skill Runner）和 006（Policy Engine）的并行开发依赖这些契约作为 mock 基础。接口契约的稳定性直接决定了 007 集成阶段的风险。

**Independent Test**: 可独立测试——基于输出的 Protocol 定义编写一个 mock ToolBroker 实现，验证其满足 ToolBrokerProtocol 的类型检查。

**Acceptance Scenarios**:

1. **Given** Feature 004 开发完成，**When** 查阅 `contracts/tooling-api.md`，**Then** 文档中包含 ToolBrokerProtocol、ToolMeta、ToolResult、PolicyCheckpoint Protocol 四个核心接口的完整定义（方法签名、参数类型、返回类型、行为约定）。
2. **Given** 一个第三方模块基于 ToolBrokerProtocol 编写了 mock 实现，**When** 对该 mock 进行静态类型检查，**Then** 类型检查通过，无缺失方法或类型不匹配。
3. **Given** PolicyCheckpoint Protocol 已定义，**When** Feature 006 基于此 Protocol 实现 PolicyCheckHook，**Then** 该 Hook 可注册到 ToolBroker 的 before hook 链中，无需修改 Broker 代码。

---

### User Story 7 — 示例工具验证（Priority: P2）

作为**验收测试人员**，我希望系统提供至少 2 个不同 side_effect_level 的示例工具（如只读工具和不可逆工具），作为端到端验证的 fixture，同时为工具开发者提供最佳实践参考。

**Why this priority**: 示例工具是验收标准的验证载体，同时为工具开发者提供声明和注册的范例。但示例工具本身不承载核心业务逻辑，优先级略低于基础设施能力。

**Independent Test**: 可独立测试——运行示例工具的端到端测试，验证从声明、注册、发现、执行、事件生成的完整链路。

**Acceptance Scenarios**:

1. **Given** 示例工具 A（side_effect_level=none，如文件读取），**When** 通过 ToolBroker 执行，**Then** 返回预期结果，事件链完整，且 Profile 过滤时归属 minimal 或 standard 级别。
2. **Given** 示例工具 B（side_effect_level=irreversible，如文件写入），**When** 通过 ToolBroker 执行，**Then** 返回预期结果，事件链完整，且 ToolMeta 中 side_effect_level 正确标记为 irreversible。

---

### Edge Cases

- **EC-1（Schema 反射降级）**: 函数参数缺少类型注解时，系统如何处理？预期：拒绝注册并给出明确错误提示（不允许无类型注解的工具），而非生成不完整的 schema。关联 FR-005。
- **EC-2（工具执行并发）**: 同一工具被多个 Worker 并发调用时，ToolBroker 是否正确处理？预期：每次调用独立执行，共享注册表但不共享执行上下文。关联 FR-008。
- **EC-3（Artifact 存储失败）**: 大输出裁切时 ArtifactStore 不可用，如何降级？预期：保留原始输出（不裁切），记录降级警告事件。关联 FR-010、Constitution C6。
- **EC-4（Hook 执行超时）**: before hook 自身执行超时，如何处理？预期：before hook 有独立超时限制，超时后跳过该 hook 并记录警告（对 before hook 执行应用 fail-open 还是 fail-closed 需根据 hook 类型决定）。关联 FR-012。
- **EC-5（空参数工具）**: 工具函数无参数（零参数函数），Schema 反射是否正常？预期：生成空参数的 JSON Schema，正常注册和执行。关联 FR-003。
- **EC-6（超大输出）**: 工具输出超过 100KB 时，ArtifactStore 存储是否有效率问题？预期：ArtifactStore 正常处理，裁切后的引用摘要保留输出的前缀（头部信息）以辅助 LLM 理解。关联 FR-009。
- **EC-7（重复注册覆盖）**: 同一工具名重复注册时行为是否明确？预期：拒绝重复注册（不默默覆盖），需先显式注销再重新注册。关联 FR-006。

---

## Requirements

### Functional Requirements

#### 工具契约声明（Tool Contract Declaration）

- **FR-001**: 系统 **MUST** 支持工具开发者通过声明性标注定义工具元数据，包含以下必填字段：name（工具名称）、description（工具描述）、side_effect_level（副作用等级：none / reversible / irreversible）、tool_profile（权限 Profile：minimal / standard / privileged）、tool_group（逻辑分组，如 "system"、"filesystem"）。
  _追踪_: US-1 | _Constitution_: C3（Tools are Contracts）

- **FR-002**: 系统 **MUST** 强制要求每个工具声明 side_effect_level，不允许存在未声明副作用等级的工具。缺失 side_effect_level 声明的工具在注册阶段被拒绝。
  _追踪_: US-1 场景 3 | _Constitution_: C3

- **FR-003**: 系统 **MUST** 从 Python 函数签名（参数名、类型注解）和 docstring 自动生成工具参数的 JSON Schema，保证 code = schema 单一事实源。工具开发者无需手动编写或维护独立的 schema 文件。
  _追踪_: US-1 场景 1, 2 | _Constitution_: C3

- **FR-004**: 系统 **MUST** 支持可选的工具元数据字段：version（版本号）、timeout_seconds（声明式超时）。
  _追踪_: US-1, US-3 场景 2

- **FR-005**: 系统 **MUST** 在 Schema 反射时，对缺少类型注解的函数参数拒绝注册，给出明确的错误信息指明哪些参数缺少类型注解。
  _追踪_: US-1, EC-1

#### 工具注册与发现（Tool Registration & Discovery）

- **FR-006**: 系统 **MUST** 提供集中的工具注册机制，支持将带有 ToolMeta 的工具处理函数注册到 ToolBroker。注册时进行名称唯一性检查，重复名称注册被拒绝并返回冲突错误。
  _追踪_: US-2 场景 3, EC-7

- **FR-007**: 系统 **MUST** 支持按 tool_profile 过滤工具集——查询时指定 Profile 级别，仅返回该级别及以下的工具。即 minimal 查询仅返回 minimal 工具；standard 查询返回 minimal + standard 工具；privileged 查询返回所有工具。
  _追踪_: US-2 场景 1 | _Constitution_: C5（Least Privilege）

- **FR-008**: 系统 **MUST** 支持按 tool_group 过滤工具集——查询时指定分组名称，仅返回该分组内的工具。
  _追踪_: US-2 场景 2

- **FR-009**: 系统 **SHOULD** 支持工具注销（unregister），以便在运行时移除不再需要的工具。
  _追踪_: EC-7

#### 工具执行（Tool Execution）

- **FR-010**: 系统 **MUST** 通过 ToolBroker 统一执行工具调用。所有工具调用必须经过 Broker，确保 hook 链路完整执行，禁止绕过 Broker 直接调用工具函数。
  _追踪_: US-3 | _Constitution_: C4（Two-Phase 强制）

- **FR-010a**: 系统 **MUST** 在没有任何注册的 PolicyCheckpoint hook 时，拒绝执行 `side_effect_level=irreversible` 的工具，返回明确的拒绝原因（"no policy checkpoint registered for irreversible tool"）。这是 Two-Phase 的最小强制契约——不可逆操作在无门禁保护时默认被阻止（safe by default）。`side_effect_level=none` 和 `reversible` 的工具不受此限制。
  _追踪_: US-3, US-5 | _Constitution_: C4（Side-effect Must be Two-Phase）, C7（User-in-Control, safe by default）

- **FR-011**: 系统 **MUST** 为每次工具执行生成结构化的 ToolResult，包含：output（输出内容或 artifact 引用）、is_error（是否错误）、error（错误信息，仅错误时）、duration（执行耗时）、artifact_ref（artifact 引用，仅裁切时）。
  _追踪_: US-3 场景 1, 3

- **FR-012**: 系统 **MUST** 支持声明式超时控制——读取 ToolMeta 中的 timeout_seconds，执行超时后自动取消并返回超时错误。
  _追踪_: US-3 场景 2

- **FR-013**: 系统 **MUST** 对同步函数工具自动包装为异步执行（不阻塞事件循环），对调用方透明。
  _追踪_: US-3 场景 4

#### 事件生成（Event Generation）

- **FR-014**: 系统 **MUST** 在工具执行的关键节点生成事件：TOOL_CALL_STARTED（执行开始，含 tool_id、参数摘要）、TOOL_CALL_COMPLETED（执行完成，含结果摘要、耗时、artifact_ref）、TOOL_CALL_FAILED（执行失败，含错误分类、可恢复性标记）。[AUTO-CLARIFIED: TOOL_CALL_STARTED / TOOL_CALL_COMPLETED / TOOL_CALL_FAILED 三个事件类型需在 Feature 004 实现阶段添加到 `octoagent.core.models.enums.EventType` 枚举中，与现有 MODEL_CALL_STARTED/COMPLETED/FAILED 模式保持一致。这是对 core 包的向前兼容扩展，不影响现有事件消费者]
  _追踪_: US-3 场景 1, 2, 3 | _Constitution_: C2（Everything is an Event）, C8（Observability）

- **FR-015**: 系统 **MUST** 在事件中对敏感数据进行脱敏处理。脱敏规则包括：(a) 文件路径中的 `$HOME` 或用户目录部分替换为 `~`；(b) 环境变量值替换为 `[ENV:VAR_NAME]`；(c) 匹配常见凭证模式（`token=*`、`password=*`、`secret=*`、`key=*`）的参数值替换为 `[REDACTED]`。脱敏逻辑由 ToolBroker 在事件生成前统一执行，工具开发者无需在工具代码中处理脱敏。
  _追踪_: _Constitution_: C8（敏感原文不写入 Event payload）

#### 大输出处理（Large Output Handling）

- **FR-016**: 系统 **MUST** 在工具执行完成后自动检测输出长度，超过阈值（默认 500 字符）时将完整输出存入 ArtifactStore，并在 ToolResult 中替换为包含 artifact ID 和内容前缀摘要的引用信息。
  _追踪_: US-4 场景 1 | _Constitution_: C11（Context Hygiene）

- **FR-017**: 系统 **MUST** 支持裁切阈值的配置——支持全局默认阈值和工具级别自定义阈值（工具级别优先于全局）。
  _追踪_: US-4 场景 3

- **FR-018**: 系统 **MUST** 在 ArtifactStore 不可用时降级处理——保留原始输出不裁切，并记录降级警告事件。
  _追踪_: EC-3 | _Constitution_: C6（Degrade Gracefully）

#### Hook 扩展机制（Hook Extension）

- **FR-019**: 系统 **MUST** 支持 before hook 和 after hook 两类扩展点。before hook 在工具执行前运行，可修改参数或拒绝执行；after hook 在工具执行后运行，可修改结果。每个 Hook **MUST** 声明 `fail_mode`（"closed" 或 "open"），决定 hook 自身异常/超时时的行为：`fail_mode="closed"` 时拒绝工具执行（安全类 hook 如 PolicyCheckpoint 必须使用此模式）；`fail_mode="open"` 时记录警告并继续执行（可观测类 hook 使用此模式）。
  _追踪_: US-5 | _Constitution_: C4（安全类 fail-closed）, C6（可观测类 fail-open）

- **FR-020**: 系统 **MUST** 支持 Hook 的优先级排序——每个 Hook 声明一个数值优先级，按优先级从低到高顺序执行。
  _追踪_: US-5 场景 1

- **FR-021**: 系统 **MUST** 支持 before hook 返回"拒绝执行"信号，此时工具不执行，直接返回包含拒绝原因的 ToolResult。
  _追踪_: US-5 场景 2 | _Constitution_: C4（Two-Phase 预留接入点）

- **FR-022**: 系统 **SHOULD** 对 after hook 执行异常采用 log-and-continue 降级策略——记录错误但不影响工具执行结果的返回。
  _追踪_: US-5 场景 3 | _Constitution_: C6（Degrade Gracefully）

#### 接口契约（Interface Contracts）

- **FR-023**: 系统 **MUST** 输出 ToolBrokerProtocol 接口定义，包含工具注册（register）、发现（discover）、执行（execute）、Hook 管理（add_hook）的方法签名和行为约定。
  _追踪_: US-6 场景 1

- **FR-024**: 系统 **MUST** 输出 PolicyCheckpoint Protocol 接口定义，作为 Feature 006 PolicyEngine 接入 ToolBroker before hook 的契约。
  _追踪_: US-6 场景 3

- **FR-025**: 系统 **MUST** 将接口契约文档输出到 `contracts/tooling-api.md`，包含四个核心 Protocol（ToolBrokerProtocol、ToolMeta、ToolResult、PolicyCheckpoint）的完整定义。
  _追踪_: US-6 场景 1

- **FR-025a**: 接口契约 **MUST** 锁定以下稳定项，变更需经 005/006 利益方评审：
  - **枚举值**: SideEffectLevel（none / reversible / irreversible），ToolProfile（minimal / standard / privileged）
  - **默认值**: 大输出裁切阈值 500 字符，hook 默认 fail_mode="open"（PolicyCheckpoint 强制 fail_mode="closed"）
  - **Protocol 方法签名**: ToolBrokerProtocol.execute(tool_name, params, context) → ToolResult；PolicyCheckpoint.check(tool_meta, params, context) → CheckResult
  - **ToolResult 必含字段**: output, is_error, error, duration, artifact_ref
  _追踪_: US-6 | 三轨并行依赖稳定性

#### 示例工具（Example Tools）

- **FR-026**: 系统 **MUST** 提供至少 2 个示例工具，覆盖不同的 side_effect_level：至少 1 个 none 级别工具（只读）和至少 1 个 irreversible 级别工具（不可逆写操作）。
  _追踪_: US-7 | 需求描述第 5 点

- **FR-027**: 示例工具 **MUST** 使用标准的声明性标注方式定义，作为工具开发者的最佳实践参考。
  _追踪_: US-7

### Key Entities

- **ToolMeta**: 工具的完整元数据描述，包含名称、描述、参数 JSON Schema、副作用等级、权限 Profile、逻辑分组、版本、超时设置。ToolMeta 是工具在系统中的"身份证"，由 Schema 反射自动生成。
- **ToolResult**: 工具执行的结构化结果，包含输出内容（或 artifact 引用）、错误状态、执行耗时。是 ToolBroker 执行的标准返回格式，也是 Feature 005 SkillRunner 结果回灌的输入。
- **ToolCall**: 工具调用请求，包含工具名称、调用参数、执行上下文。是 ToolBroker execute 方法的输入。
- **SideEffectLevel**: 工具副作用等级枚举（none / reversible / irreversible）。驱动 Feature 006 Policy Engine 的门禁决策。
- **ToolProfile**: 工具权限 Profile 枚举（minimal / standard / privileged）。用于工具集的分级过滤，是最小权限原则的实现载体。[AUTO-CLARIFIED: spec 使用 `privileged` 命名而非 Blueprint 的 `full`，原因是 `privileged` 语义更精确地表达"特权操作"含义，与 Constitution C5（Least Privilege）术语一致；M1 阶段仅实现 minimal + standard 两级（Blueprint §8.5.4），`privileged` 在 M1.5 Docker 执行就绪后激活。Blueprint 的 `full` 与 spec 的 `privileged` 为同一概念的不同命名，后续需在 Blueprint 中统一]
- **ExecutionContext**: 工具执行上下文，包含 task_id（关联任务 ID）、trace_id（追踪标识）、caller（调用者标识，如 Worker ID）、profile（当前 ToolProfile）。作为 ToolBroker.execute() 和 Hook 的上下文参数传递，用于事件生成和 Policy 决策。[AUTO-CLARIFIED: 调研文档中引用但 spec Key Entities 未定义，补充为正式实体。字段设计对齐现有 Event 模型的 trace_id/task_id 字段（core.models.event），确保事件生成时上下文信息完整]
- **ToolHook**: Hook 扩展点的抽象，分为 BeforeHook（执行前）和 AfterHook（执行后）。PolicyCheckpoint Protocol 是 BeforeHook 的特化形式。
- **PolicyCheckpoint**: Feature 006 PolicyEngine 接入 ToolBroker 的契约接口。作为 before hook 运行，对 irreversible 工具触发审批流。Feature 004 定义 Protocol，Feature 006 提供实现。[AUTO-RESOLVED: PolicyCheckpoint 在 004 中仅定义 Protocol 接口，不实现具体门禁逻辑。原因：004 聚焦工具基础设施，门禁逻辑属于 006 的职责范围，与三轨并行策略一致]

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 工具开发者为一个函数添加声明性标注后，系统自动生成的 ToolMeta 中 JSON Schema 与函数签名 100% 一致（通过 contract test 自动验证），无需手动编写任何 schema 文件。
- **SC-002**: 通过 ToolBroker 执行工具后，EventStore 中可查询到完整的事件链（TOOL_CALL_STARTED -> TOOL_CALL_COMPLETED 或 TOOL_CALL_FAILED），每次调用的执行耗时、参数摘要、结果摘要均可追溯。
- **SC-003**: 工具输出超过阈值时，LLM 上下文中仅保留精简的 artifact 引用（而非完整输出），完整内容通过 ArtifactStore 可检索。工具开发者无需在工具代码中处理裁切逻辑（零侵入验证）。
- **SC-004**: Feature 005 和 Feature 006 的开发者可基于 `contracts/tooling-api.md` 和 Protocol 定义编写 mock 实现，mock 通过静态类型检查，且在 Feature 007 集成阶段可无缝替换为真实依赖。
- **SC-005**: 以 profile=minimal 查询 ToolBroker 时，返回的工具集中不包含任何 standard 或 privileged 级别的工具（最小权限过滤验证）。
- **SC-006**: 所有已注册工具的 side_effect_level 均已声明（无"未声明"状态的工具存在于注册表中），覆盖率 100%。

---

## Constraints & Out of Scope

### Architecture Notes

- **ToolBroker 注册表为进程内存态**: 进程重启后注册表清空，需由应用启动流程重新注册工具。历史工具调用事件通过 EventStore 持久化不丢失（对齐 Constitution C1）。此设计决策的依据：工具注册信息是静态声明，可在启动时确定性重建；不需要额外的持久化开销。
- **FR-010 "禁止绕过 Broker"为架构约定**: 由于 @tool_contract 装饰的函数本质是普通 Python callable，技术上无法阻止直接调用。FR-010 通过以下机制强制执行：(1) 代码审查规范；(2) 仅通过 ToolBroker.discover() 暴露工具给 LLM toolset，不暴露原始函数引用；(3) 事件缺失检测——缺少 TOOL_CALL_STARTED 事件的工具执行结果应触发告警。

### In Scope（MVP 边界）

- ToolMeta 数据模型（全部必填字段 + 可选字段）
- Schema 反射引擎（从函数签名 + type hints + docstring 生成 JSON Schema）
- ToolBroker 核心（register / discover / execute / add_hook）
- Large Output Handler（after hook 实现，阈值可配）
- before/after Hook 链（优先级排序 + 降级策略）
- 事件生成（TOOL_CALL_STARTED / COMPLETED / FAILED）
- 示例工具（至少 2 个不同 side_effect_level）
- 接口契约文档（contracts/tooling-api.md）+ Protocol 定义
- PolicyCheckpoint Protocol（仅接口定义，不含实现）

### Out of Scope（不在 Feature 004 范围内）

- PolicyEngine 具体实现（Feature 006）
- Skill Runner 集成（Feature 005）
- 工具热加载/动态注册（M2 考虑）
- MCP 工具协议兼容（M2 考虑）
- 工具输出 summarizer 压缩（依赖 Feature 005 SkillRunner，可选在 007 激活）
- 工具调用审批流的完整实现（Feature 006）
- Chat UI / Approvals 面板（Feature 006）
- 多 Worker 并发调度（Feature 007 集成验证）

### Constitution 对齐

| Constitution 原则 | Feature 004 中的体现 |
|---|---|
| C2: Everything is an Event | TOOL_CALL_STARTED / COMPLETED / FAILED 事件自动生成 |
| C3: Tools are Contracts | Schema 反射保证 code=schema 单一事实源；强制声明 side_effect_level |
| C4: Side-effect Two-Phase | FR-010a: irreversible 工具在无 PolicyCheckpoint 时被强制拒绝（safe by default）；before hook + fail_mode="closed" 确保安全门禁不可绕过 |
| C5: Least Privilege | ToolProfile 分级过滤（minimal / standard / privileged） |
| C6: Degrade Gracefully | after hook 异常 log-and-continue；ArtifactStore 不可用时保留原始输出 |
| C8: Observability | 完整事件链 + 结构化日志；敏感数据脱敏 |
| C11: Context Hygiene | 大输出自动裁切 + artifact 引用模式 |
| C13: 失败必须可解释 | TOOL_CALL_FAILED 事件含错误分类和可恢复性标记 |

---

## Clarifications

### Session 2026-03-01

#### 自动解决的澄清

##### CLR-001: ToolProfile 命名 — `privileged` vs Blueprint `full`

- **问题**: spec 使用 `privileged` 而 Blueprint §8.5.4 使用 `full`，可能导致 005/006 并行开发时术语混乱
- **决策**: 保持 spec 使用 `privileged`
- **理由**: `privileged` 语义更精确（表达"特权操作"），与 Constitution C5（Least Privilege）术语一致。Blueprint `full` 与 spec `privileged` 为同义映射，M1 阶段仅实现 minimal + standard 两级，`privileged` 在 M1.5 激活。后续需在 Blueprint 中统一命名
- **影响范围**: Key Entities > ToolProfile、FR-001、FR-007

##### CLR-002: ExecutionContext 实体缺失

- **问题**: 调研文档 tech-research.md 中 ToolBrokerProtocol.execute() 和 Hook 的方法签名引用了 `ExecutionContext` 参数，但 spec Key Entities 中未定义该实体
- **决策**: 补充 `ExecutionContext` 为正式 Key Entity
- **理由**: ExecutionContext 是 ToolBroker 执行管线的核心上下文参数，承载 task_id、trace_id、caller、profile 等信息，直接影响事件生成（FR-014）和 Policy 决策。字段设计对齐现有 Event 模型的 trace_id/task_id
- **影响范围**: Key Entities 新增、ToolBrokerProtocol 方法签名、contracts/tooling-api.md

##### CLR-003: EventType 枚举扩展方式

- **问题**: FR-014 要求生成 TOOL_CALL_STARTED/COMPLETED/FAILED 事件，但现有 `core.models.enums.EventType` 枚举中不含这些类型，需明确扩展方式
- **决策**: Feature 004 实现阶段直接在 core 包的 EventType 枚举中添加三个新类型
- **理由**: 与现有 MODEL_CALL_STARTED/COMPLETED/FAILED 模式保持一致，是向前兼容的枚举扩展，不影响现有事件消费者。core 包作为共享基础设施包，承载所有事件类型定义是其设计职责
- **影响范围**: FR-014、octoagent.core.models.enums.EventType

#### CRITICAL 问题（需用户决策）

##### CLR-004: [RESOLVED] 大输出裁切阈值 — 500 字符

- **问题**: spec FR-016/US-4 定义默认阈值 500 字符（Agent Zero），Blueprint §8.5.5 默认 4000 字符，两者 8 倍差异
- **决策**: 采用 **500 字符**（用户选择）
- **理由**: 沿用 Agent Zero 验证过的实践，激进裁切有利于保持 LLM 上下文精简（对齐 Constitution C11 Context Hygiene）。FR-017 已支持工具级自定义阈值覆盖，特定工具可按需放宽
- **影响范围**: FR-016 默认阈值确认为 500 字符，Blueprint §8.5.5 的 `max_inline_chars` 后续需同步更新

##### CLR-005: [RESOLVED] before hook 超时策略 — 按类型区分

- **问题**: before hook 超时时 fail-open 会绕过安全门禁（违反 C4），fail-closed 会阻塞所有工具调用（违反 C6）
- **决策**: 采用 **按类型区分**（Hook 声明 `fail_mode`）
- **理由**: 安全类 hook（如 PolicyCheckpoint）声明 `fail_mode="closed"`，超时即拒绝执行，确保不可逆操作不被放行；非安全类 hook（如日志、metrics）声明 `fail_mode="open"`，超时仅记录警告。兼顾安全性（C4）和可用性（C6）
- **影响范围**: Hook Protocol 新增 `fail_mode` 字段，EC-4 边界场景测试需覆盖两种模式，contracts/tooling-api.md 需包含 fail_mode 定义
