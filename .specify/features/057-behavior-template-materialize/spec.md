# Feature Specification: 行为文件模板落盘与 Agent 自主更新

**Feature Branch**: `057-behavior-template-materialize`
**Created**: 2026-03-16
**Status**: Draft
**Input**: 用户需求描述 — 实现行为文件（Behavior Files）的模板初始化写入磁盘、Agent LLM 工具读写行为文件、System Prompt 引导 Agent 自主判断何时更新行为文件、删除对不存在的 bootstrap.answer 工具的引用

[无调研基础] 本规范基于用户提供的内联调研结论（Agent Zero / OpenClaw 做法）和代码上下文生成，未使用 research-synthesis.md。

---

## User Scenarios & Testing

### User Story 1 - 首次启动时行为文件自动写入磁盘 (Priority: P1)

用户（或运维人员）在全新环境首次启动 OctoAgent 后，系统自动将所有默认行为文件模板（AGENTS.md、USER.md、PROJECT.md、KNOWLEDGE.md、TOOLS.md、BOOTSTRAP.md，以及高级文件 SOUL.md、IDENTITY.md、HEARTBEAT.md）写入对应的磁盘目录。用户无需手动操作即可获得完整的行为文件骨架，后续可在前端查看和编辑这些文件。

**Why this priority**: 这是所有后续功能的前提条件。如果文件不存在于磁盘上，Agent 无法读取到用户定制的内容，前端也只能展示内存中的默认模板。落盘是「耐久优先」宪法原则（原则 1）的直接要求。

**Independent Test**: 在一个干净的 data 目录启动系统，检查 behavior/system/、behavior/agents/butler/、projects/default/behavior/ 下是否自动生成了对应的 .md 文件，且内容与 `_default_content_for_file()` 返回的模板一致。

**Acceptance Scenarios**:

1. **Given** 全新安装环境（data 目录为空），**When** 系统启动并完成初始化，**Then** behavior/system/ 目录下存在 AGENTS.md、USER.md、TOOLS.md、BOOTSTRAP.md 四个文件，behavior/agents/butler/ 下存在 IDENTITY.md、SOUL.md、HEARTBEAT.md 三个文件，projects/default/behavior/ 下存在 PROJECT.md、KNOWLEDGE.md 两个文件，文件内容为对应的默认模板。
2. **Given** 已存在的环境中某些行为文件已被用户修改，**When** 系统重新启动，**Then** 已存在的文件内容不被覆盖（writeFileIfMissing 策略），仅缺失的文件被补写。
3. **Given** 全新安装环境，**When** 系统启动，**Then** instructions/README.md 仍然正常生成（现有逻辑不被破坏）。

---

### User Story 2 - Agent 通过 LLM 工具读写行为文件 (Priority: P1)

Agent（LLM）在对话过程中，能够通过专用工具读取和修改行为文件。当 Agent 判断需要更新行为文件时（例如用户表达了新的偏好、项目语境发生变化），Agent 可以先读取当前文件内容，然后提出修改请求。修改请求遵循治理规则（proposal_required 模式下需要用户确认）。

**Why this priority**: 这是让 Agent 具备「自主进化」能力的核心。没有 LLM 可调用的工具，行为文件只能通过前端手动编辑，Agent 无法在对话流程中自主适应用户需求。这也是宪法原则 3（工具即契约）的要求——工具 schema 必须与代码签名一致并可被 LLM 调用。

**Independent Test**: 在对话中向 Agent 提出修改行为偏好的请求（如「以后用英文回复我」），验证 Agent 调用了 behavior.read_file 读取当前 USER.md 内容，并调用 behavior.write_file 提出修改 proposal。

**Acceptance Scenarios**:

1. **Given** Agent 已注册 behavior.read_file 和 behavior.write_file 两个 LLM 工具，**When** Agent 在对话中判断需要读取 USER.md，**Then** Agent 调用 behavior.read_file 工具并获得文件当前内容。
2. **Given** Agent 判断需要修改 USER.md，**When** Agent 调用 behavior.write_file 工具，**Then** 修改遵循该文件的 editable_mode 和 review_mode 配置（proposal_required 模式下需要用户确认后才生效）。
3. **Given** Agent 尝试写入的文件路径超出 behavior 目录边界，**When** 调用 behavior.write_file，**Then** 系统拒绝操作并返回错误说明。
4. **Given** Agent 尝试写入的内容超出文件字符预算（BEHAVIOR_FILE_BUDGETS），**When** 调用 behavior.write_file，**Then** 系统拒绝写入并返回错误提示（含字符数/预算/超出量），Agent 自行精简后重试。

---

### User Story 3 - System Prompt 充分引导 Agent 理解行为文件用途 (Priority: P1)

Agent 的 system prompt 中包含每个行为文件的用途说明、当前内容摘要和修改时机建议。Agent 基于这些上下文自主判断何时需要更新行为文件，而不依赖硬编码的问答流程。例如：当用户说「我叫 Connor，时区是 UTC+8」时，Agent 理解应将用户偏好写入 USER.md（通过 behavior.write_file 工具），而非通过不存在的 bootstrap.answer 工具。

**Why this priority**: 与 P1-2 同等重要。工具能力和上下文引导缺一不可——Agent 有工具但不知道何时用、用来写什么，等于没有工具。这直接对应宪法原则 13A（优先提供上下文，而不是堆积硬策略）。

**Independent Test**: 检查 Agent 的 system prompt 输出，确认包含行为文件清单（file_id、用途、修改建议），且不包含对 bootstrap.answer 工具的引用。

**Acceptance Scenarios**:

1. **Given** Agent 进入对话，**When** 构建 system prompt，**Then** prompt 中包含一个「行为文件工具使用指南」block，至少包含：(a) 每个 file_id 的一行用途描述 (b) 修改时机建议（何时该写哪个文件） (c) behavior.read_file / behavior.write_file 的调用示例或参数说明 (d) 存储边界提示（事实 -> Memory、规则 -> behavior files、敏感值 -> SecretService）。
2. **Given** Bootstrap 处于 PENDING 状态，**When** 构建 system prompt，**Then** prompt 引导 Agent 通过对话收集信息，并使用 behavior.write_file 工具保存到对应的行为文件，而非引用 bootstrap.answer 工具。
3. **Given** 用户在对话中分享了新的偏好或项目信息，**When** Agent 处理消息，**Then** Agent 能够基于 system prompt 中的引导，自主判断该信息应写入哪个行为文件，并调用相应工具。

---

### User Story 4 - 删除 bootstrap.answer 幽灵引用 (Priority: P2)

系统提示词中不再引用不存在的 bootstrap.answer 工具。Bootstrap 引导流程改为引导 Agent 使用行为文件读写工具和记忆工具来保存初始化收集到的信息。

**Why this priority**: 这是一个 bug 修复——当前 system prompt 引用了不存在的工具，导致 Agent 在 bootstrap 阶段尝试调用幽灵工具而失败。但它不阻塞其他功能的实现，因此排在 P2。

**Independent Test**: 在 bootstrap PENDING 状态下启动对话，搜索 system prompt 输出中是否还包含 "bootstrap.answer" 字样，预期不包含。

**Acceptance Scenarios**:

1. **Given** Bootstrap session 状态为 PENDING，**When** 系统构建 Agent 上下文，**Then** 生成的 prompt 中不包含 "bootstrap.answer" 字样。
2. **Given** Bootstrap session 状态为 PENDING，**When** Agent 完成信息收集，**Then** Agent 使用 behavior.write_file 和/或 memory 工具保存答案，而非尝试调用 bootstrap.answer。

---

### Edge Cases

- 磁盘写入失败（权限不足、磁盘满）时，模板落盘应记录错误事件并降级运行（使用内存中的默认模板），不阻塞系统启动（原则 6：可降级）。
- Agent 并发写入同一行为文件时，系统应保证最终一致性（后写者覆盖），不需要悲观锁（单用户系统，并发概率极低）。
- 用户通过前端 UI 和 Agent LLM 工具同时修改同一行为文件时，应以最后写入者为准，并通过事件记录修改来源。
- Agent 尝试读取尚未 materialize 的文件时，应返回默认模板内容而非空内容或错误（复用 `_default_content_for_file()` fallback）。
- 行为文件内容被清空（空字符串）时，系统应视为有效操作但在 prompt 中标注该文件为空。
- 文件路径中包含特殊字符（如空格、中文、符号）时，路径校验和文件操作应正常工作。
- LLM 工具的 file_path 参数使用相对路径（相对于 project_root），不接受绝对路径。路径校验 MUST 防止 path traversal（`../`）。
- 文件存在但内容为空时，writeFileIfMissing 不覆盖（空文件可能是用户有意清空）；下次重启不会重新写入默认模板。

---

## Requirements

### Functional Requirements

**模板落盘（Materialize）**

- **FR-001**: 系统启动时 MUST 将所有 9 个默认行为文件模板写入磁盘对应目录（behavior/system/、behavior/agents/{agent_slug}/、projects/{project_slug}/behavior/）。[关联 US-1]
- **FR-002**: 模板写入 MUST 采用 writeFileIfMissing 策略——仅当目标文件不存在时（`not path.exists()`）才写入，已存在的文件内容 MUST NOT 被覆盖。文件存在但内容为空时视为「已存在」，不覆盖。[关联 US-1] [AUTO-CLARIFIED: 见 Clarifications Session 2026-03-16 #3]
- **FR-003**: 模板写入的内容 MUST 与 `_default_content_for_file()` 函数返回的内容一致（该函数是模板内容的单一事实源）。[关联 US-1]
- **FR-004**: 模板写入 SHOULD 在 `ensure_filesystem_skeleton()` 流程中完成，与目录创建在同一阶段执行。[关联 US-1]

**LLM 工具注册**

- **FR-005**: 系统 MUST 在 Agent 的 LLM 工具集中注册 behavior.read_file 工具，使 Agent 可以在对话中读取行为文件内容。[关联 US-2]
- **FR-006**: 系统 MUST 在 Agent 的 LLM 工具集中注册 behavior.write_file 工具，使 Agent 可以在对话中修改行为文件内容。[关联 US-2]
- **FR-007**: 两个 LLM 工具的 schema MUST 与现有 control plane action handler 的参数和行为保持一致（原则 3：工具即契约）。[关联 US-2]
- **FR-008**: behavior.write_file 工具 MUST 声明副作用等级为 `reversible`（修改行为文件会影响 Agent 后续行为，但可随时改回），behavior.read_file 声明为 `none`。[关联 US-2]
- **FR-009**: behavior.write_file 工具 MUST 遵循文件的 editable_mode 和 review_mode 配置——当 review_mode 为 REVIEW_REQUIRED 时，LLM 工具返回结果中 MUST 标注 `proposal: true`，并在对话中以自然语言向用户展示修改摘要，等待用户确认后再实际写入磁盘。当前阶段（MVP）的 proposal 确认通过对话交互实现（Agent 先展示 diff 摘要、用户回复确认、Agent 再执行写入），不依赖独立的 ApprovalService 审批流。[关联 US-2] [AUTO-CLARIFIED: 见 Clarifications Session 2026-03-16 #1]

**路径安全与边界**

- **FR-010**: LLM 工具的文件路径 MUST 限制在 behavior 目录体系内，不得读写任意文件系统路径。[关联 US-2]
- **FR-011**: LLM 工具 MUST 在写入前对内容执行字符预算检查（参照 BEHAVIOR_FILE_BUDGETS），超出预算时 MUST 拒绝写入并返回明确的错误提示（含当前字符数、预算上限、超出量），由 Agent 自行精简后重试。不执行静默截断。[关联 US-2] [AUTO-CLARIFIED: 见 Clarifications Session 2026-03-16 #2]

**System Prompt 引导**

- **FR-012**: Agent 上下文构建时 MUST 在 system prompt 中注入一个「行为文件工具使用指南」block（role: system），包含：(a) 每个 file_id 的一行用途描述 (b) 修改时机建议 (c) `behavior.read_file(file_path)` / `behavior.write_file(file_path, content)` 的参数说明 (d) review_mode 为 REVIEW_REQUIRED 时需先展示 diff 再写入的提示。该 block 从 `BehaviorWorkspace.files` 动态生成，随行为文件集变化而更新。[关联 US-3] [AUTO-CLARIFIED: 见 Clarifications Session 2026-03-16 #4]
- **FR-013**: System prompt MUST 包含存储边界提示：事实进 Memory、规则和人格进 behavior files、敏感值进 SecretService。该提示 SHOULD 复用现有 `StorageBoundaryHints` 模型的内容。[关联 US-3]
- **FR-014**: 行为文件工具使用指南 MUST 在 bootstrap PENDING 和 COMPLETED 两种状态下都注入，但 PENDING 状态下 SHOULD 额外强调初始化信息的存储路由（称呼/偏好 -> behavior.write_file USER.md、名称/性格 -> behavior.write_file IDENTITY.md/SOUL.md、事实 -> memory tools、敏感信息 -> SecretService）。[关联 US-3, US-4]

**删除幽灵引用**

- **FR-015**: 系统 MUST 从 bootstrap 引导 prompt 中移除所有对 bootstrap.answer 工具的引用。[关联 US-4]
- **FR-016**: Bootstrap PENDING 状态下的引导 prompt MUST 改为引导 Agent 通过 behavior.write_file 和 memory 工具保存初始化收集到的用户信息。[关联 US-4]

**降级与可观测**

- **FR-017**: 模板落盘过程中的写入失败 MUST 被记录为结构化事件（原则 8：可观测性），且 MUST NOT 阻塞系统启动（原则 6：可降级）。[关联 Edge Case]
- **FR-018**: 通过 LLM 工具修改行为文件的操作 MUST 生成事件记录，包含修改来源（LLM tool）、目标文件和修改摘要。[关联 Edge Case, 原则 2]

### Key Entities

- **BehaviorWorkspaceFile**: 行为文件的运行时表示，包含 file_id、content、scope、editable_mode、review_mode 等属性。已有模型定义。
- **BehaviorPackFile**: 行为文件在 behavior pack 中的表示，用于 system prompt 注入。已有模型定义。
- **_BehaviorFileTemplate**: 行为文件的元数据模板（title、layer、visibility、scope、editable_mode、review_mode），定义了每个文件的治理属性。已有数据类定义。
- **BEHAVIOR_FILE_BUDGETS**: 每个行为文件的字符预算上限映射表。已有常量定义。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 全新安装启动后，9 个默认行为文件全部存在于磁盘上，内容与默认模板一致。
- **SC-002**: Agent 在对话中可以成功调用 behavior.read_file 读取任一行为文件的当前内容。
- **SC-003**: Agent 在对话中可以成功调用 behavior.write_file 提出行为文件修改 proposal，且 proposal 遵循 review_mode 配置。
- **SC-004**: 系统重启后，用户之前通过前端或 Agent 修改的行为文件内容不丢失、不被覆盖。
- **SC-005**: Bootstrap PENDING 状态下的 system prompt 不包含 "bootstrap.answer" 字样，Agent 不再尝试调用该幽灵工具。
- **SC-006**: Agent 的 system prompt 中包含行为文件清单和使用指南，Agent 能基于上下文自主判断何时更新行为文件。

---

## Resolved Ambiguities

- **[AUTO-RESOLVED: 模板写入时机]**: 模板写入在 `ensure_filesystem_skeleton()` 中执行（与目录创建同阶段），而非在 `ensure_startup_records()` 中。理由：`ensure_filesystem_skeleton()` 已负责创建目录骨架和最小 scaffold 文件（如 README.md、secret-bindings.json），行为文件模板是同类制品，应在同一阶段写入，保持单一职责。
- **[AUTO-RESOLVED: LLM 工具复用现有实现]**: LLM 工具直接复用现有 control plane 中 `_handle_behavior_read_file` / `_handle_behavior_write_file` 的核心逻辑（路径校验、文件读写），而非重新实现。理由：现有实现已通过安全校验（路径边界检查），复用可避免重复代码和安全漏洞。

---

## Clarifications

### Session 2026-03-16

**#1 — FR-009 review_mode REVIEW_REQUIRED 的实现方式**

- **问题**: 所有 9 个行为文件的 review_mode 均为 `REVIEW_REQUIRED`，但当前 `_handle_behavior_write_file` 直接写入磁盘，不检查 review_mode。系统有 `ApprovalService` 和 `WAITING_APPROVAL` 状态机（蓝图 8.6.3），但该审批流主要面向不可逆工具调用，尚未与行为文件写入集成。如果 FR-009 要求走完整 Two-Phase Approval，则需要：(a) 在 LLM 工具层注册 approval 请求 (b) 等待用户在 Web/Telegram 审批面板操作 (c) 审批通过后才实际写入。这显著增大实现范围。
- **自动选择**: 对话内 proposal 确认（MVP 方案）—— LLM 工具检测到 `review_mode == REVIEW_REQUIRED` 时，返回 `{proposal: true, diff_summary: "..."}` 而非直接写入，由 Agent 在对话中向用户展示修改摘要并请求确认，用户回复确认后 Agent 再次调用 `behavior.write_file` 并携带 `confirmed: true` 标志。
- **理由**: (1) 行为文件修改是 reversible 操作（可随时改回），不属于蓝图要求的不可逆操作审批范畴。(2) 宪法原则 13A 要求优先通过上下文引导模型决策，对话内确认比正式审批流更轻量且更符合场景。(3) 后续 M2+ 可升级为走 ApprovalService，当前 MVP 不增加不必要的架构耦合。

**#2 — FR-011 字符预算超出时的行为：拒绝 vs 截断**

- **问题**: spec 原文写「写入被拒绝或截断（具体行为由治理策略决定）」，但未明确选择。现有 `_apply_behavior_budget` 函数对 prompt 注入执行静默截断，但 LLM 工具的写入语义不同——截断会导致用户/Agent 不知道内容被修改。
- **自动选择**: 拒绝写入 + 返回详细错误。
- **理由**: (1) 静默截断违反宪法原则 7（用户可控）——用户不知道内容被截断。(2) Agent 收到错误后可以自行精简内容重试，体现原则 10（Bias to Action）。(3) prompt 注入侧的截断是合理的（保护上下文窗口），但写入侧截断是数据丢失。两个场景行为应不同。

**#3 — FR-002 writeFileIfMissing 对空文件的处理**

- **问题**: `ensure_filesystem_skeleton` 使用 `if not path.exists()` 判断是否写入。如果文件存在但内容为空（0 字节），`path.exists()` 返回 True，不会写入默认模板。用户可能是有意清空，也可能是异常创建的空文件。
- **自动选择**: 空文件视为「已存在」，不覆盖。
- **理由**: (1) 无法区分「用户有意清空」和「异常空文件」，保守策略更安全（宪法原则 7 - 用户可控）。(2) 如果用户想恢复默认内容，可以删除文件后重启，或通过前端/CLI 重置。(3) `path.exists()` 语义清晰，增加空文件特判会增加代码复杂度且收益低。

**#4 — FR-012 system prompt 行为文件工具指南的内容格式**

- **问题**: spec 只说「包含用途说明和修改时机建议」，但未定义具体格式。当前 system prompt 中已有行为文件内容注入（通过 BehaviorPackFile），但没有「如何使用 behavior.read_file/write_file 工具」的使用指南。Agent 需要知道工具参数、调用时机和 review_mode 语义。
- **自动选择**: 新增一个独立的 system block「BehaviorToolGuide」，包含结构化的文件清单表（file_id | 用途 | 修改时机 | path_hint）+ 工具参数说明 + review_mode 行为说明 + 存储边界提示。从 `BehaviorWorkspace.files` 和 `StorageBoundaryHints` 动态生成。
- **理由**: (1) 独立 block 便于测试和维护，不污染已有的行为文件内容注入逻辑。(2) 动态生成确保文件集变化时指南自动更新。(3) 结构化表格对 LLM 的理解效果优于长段落叙述。
