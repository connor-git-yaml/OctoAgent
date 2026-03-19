# Feature Specification: Session 驱动统一记忆管线

**Feature Branch**: `claude/competent-pike`
**Created**: 2026-03-19
**Status**: Draft
**Input**: 将当前 6 条分散的记忆写入路径统一为一个 Session 级入口，解决 Fragment 碎片化问题

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 每次对话自动提取记忆 (Priority: P1)

用户与 Agent（Butler 或 Worker）进行对话后，系统在 Agent 响应完成时自动从本轮新增对话内容中提取值得长期记忆的信息，并写入 SoR（System of Record）。整个过程对用户完全透明，不阻塞对话响应，也不需要用户手动触发。

**Why this priority**: 这是整个 Feature 的核心价值——将记忆提取从分散、碎片化的多路径收敛为单一、可靠的 Session 级入口。只有这个 Story 实现后，系统才具备"每轮对话自动沉淀知识"的能力。

**Independent Test**: 发送一条包含明确个人偏好的消息（如"我喜欢用 Vim 编辑器"），等待 Agent 响应完成后，通过 Memory UI 查看是否自动出现对应的 SoR 记录。

**Acceptance Scenarios**:

1. **Given** 用户向 Butler 发送一条包含个人偏好的消息（如"以后回复我都用中文"），**When** Agent 完成响应, **Then** 系统在后台自动提取该偏好并写入 SoR，用户在 Memory 管理界面可以看到对应记录。
2. **Given** 用户与 Agent 进行了一段纯问答对话（如"Python 的 list 怎么排序？"），**When** Agent 完成响应, **Then** 系统判断无值得记忆的内容，不产生任何新的 Fragment 或 SoR 写入。
3. **Given** 用户在同一个 Session 中连续发送了 3 条消息, **When** 每次 Agent 响应完成, **Then** 每次只处理自上次提取以来的新增 turn，不会重复提取已处理的对话。
4. **Given** Agent 响应涉及了多次工具调用（如执行命令、搜索文件）, **When** 记忆提取触发, **Then** 工具调用内容以压缩摘要形式呈现给提取 LLM，不传入原始的完整输入输出。

---

### User Story 2 - 废弃碎片化写入路径 (Priority: P1)

系统移除当前分散在多处的记忆写入路径（响应后自动写入工具证据 Fragment、Compaction 流程中的碎片化写入、Compaction 后注入静默记忆提取 turn、Fragment 写入后自动触发 Consolidation），统一由 Session 级管线处理。消除因多路径并发写入导致的 Fragment 碎片化和重复记忆问题。

**Why this priority**: 与 US-1 同等重要——如果不废弃旧路径，新旧路径会同时写入导致重复和混乱。两者必须同步完成才能实现"统一入口"的目标。

**Independent Test**: 在废弃旧路径后执行一次完整的对话流程，验证 Compaction 不再触发静默记忆 turn，响应完成后不再单独写入工具证据 Fragment，且 Memory 中不会出现来自旧路径的新记录。

**Acceptance Scenarios**:

1. **Given** 系统完成升级后，用户触发了一次上下文压缩（Compaction）, **When** 压缩流程执行, **Then** 不再注入静默记忆提取 turn，也不再在压缩流程中执行碎片化 Fragment 写入。
2. **Given** Worker Agent 完成了一次响应, **When** 响应上下文记录流程执行, **Then** 不再单独写入工具证据 Fragment（记忆提取统一由 Session 级管线负责）。
3. **Given** 用户使用 `memory.write` 工具主动写入了一条记忆, **When** 写入完成, **Then** 该记忆正常写入（`memory.write` 工具作为 Agent 主动写入通道被保留）。

---

### User Story 3 - Session Cursor 保证增量处理与崩溃恢复 (Priority: P1)

系统通过 Session Cursor 机制追踪每个 Session 中记忆提取已处理到的位置。当进程意外中断时，重启后能够从 Cursor 记录的位置继续处理，不丢失未提取的对话内容，也不重复处理已提取的内容。

**Why this priority**: Cursor 是保证记忆管线可靠性的基础设施——没有它，增量处理和崩溃恢复都无法实现，直接影响 Constitution 要求的 Durability First 原则。

**Independent Test**: 模拟进程中断场景——在记忆提取 LLM 调用之后、Cursor 更新之前 kill 进程，重启后验证相同的 turn 会被重新处理。

**Acceptance Scenarios**:

1. **Given** 一个 Session 已经处理了前 5 个 turn 的记忆提取（Memory Cursor 指向第 5 个 turn）, **When** 用户继续对话产生第 6-8 个 turn 后触发记忆提取, **Then** 系统只读取 Cursor 之后的 turn，提取完成后 Cursor 更新为 8。
2. **Given** 记忆提取过程中进程崩溃（LLM 调用已完成但 Cursor 尚未更新）, **When** 进程重启后用户继续对话, **Then** 下一次记忆提取会重新包含上次未确认的 turn（Cursor 保持崩溃前的值），通过幂等写入保证不会产生重复记忆。
3. **Given** 一个新创建的 Session（Memory Cursor 默认指向起始位置）, **When** 用户发送第一条消息且 Agent 响应完成, **Then** 系统处理所有 turn 并更新 Cursor。

---

### User Story 4 - LLM 智能分片与一次性提取 (Priority: P2)

系统通过单次 LLM 调用完成所有类型记忆的提取——包括事实（facts）、解决方案（solutions）、实体关系（entities/relations）和 Theory of Mind（ToM）推理。LLM 按语义边界自行决定 Fragment 粒度，而非系统预设固定大小。

**Why this priority**: 单次 LLM 调用的设计减少了延迟和成本，LLM 自主分片比规则分片产出更高质量的记忆单元。但这属于提取质量的优化，在基础管线跑通后可以迭代调优。

**Independent Test**: 发送一段包含多种可提取信息的复杂消息（如"我刚搬到上海，在 XX 公司做后端工程师，项目用 Go 重写了原来的 Python 服务，发现性能提升了 3 倍"），检查一次 LLM 调用是否提取出多个不同类型的 SoR 记录。

**Acceptance Scenarios**:

1. **Given** 用户发送了一段包含个人事实和技术决策的消息, **When** 记忆提取 LLM 调用完成, **Then** 一次调用中同时提取出 fact 类型和 solution 类型的记忆，并按语义边界拆分为独立的 SoR 记录。
2. **Given** 用户发送了一段纯闲聊消息（如"今天天气不错"）, **When** 记忆提取 LLM 调用完成, **Then** LLM 返回空结果，系统不写入任何 Fragment 或 SoR。
3. **Given** 一段对话中涉及了实体关系（如"张三是我的同事，负责前端"）, **When** 记忆提取触发, **Then** 提取结果中包含实体（张三）和关系（同事、负责前端）的结构化信息。

---

### User Story 5 - Fragment 角色从载体转为溯源证据 (Priority: P2)

Fragment 不再作为记忆检索的主要目标，而是转变为 SoR 的溯源证据。每个 Fragment 记录提取时的原始对话段落，为 SoR 记录提供出处追溯。记忆检索主要面向 SoR 和 Derived 记录。

**Why this priority**: 角色转变是统一管线设计的自然产物——提取产出直接写入 SoR 后，Fragment 的意义从"待整合的碎片"变为"SoR 的来源凭证"。这对记忆质量有长期正面影响，但不阻塞核心管线的运行。

**Independent Test**: 发送一条值得记忆的消息，检查产出的 Fragment 是否关联到对应的 SoR 记录，且记忆检索（recall）优先返回 SoR 而非 Fragment。

**Acceptance Scenarios**:

1. **Given** 记忆提取管线处理了一段对话并产出 SoR 记录, **When** 查看产出的 Fragment, **Then** Fragment 中包含原始对话段落的引用，且与对应 SoR 记录通过 evidence_ref 关联。
2. **Given** 用户通过搜索查询某条记忆, **When** 记忆检索执行, **Then** 返回的主要结果是 SoR 和 Derived 记录，Fragment 作为溯源信息可供展开查看但不出现在首要结果中。

---

### User Story 6 - 保留兜底与手动记忆通道 (Priority: P3)

Scheduler 定期 Consolidation 作为兜底机制继续保留，在统一管线偶发失败时确保 Fragment 最终被整合。管理台手动 Consolidation 保留，供用户主动触发批量整合。`memory.write` 工具保留，供 Agent 在对话中主动写入特定记忆。

**Why this priority**: 兜底机制和手动通道是系统韧性的保障，但在正常运行下不会被触发。属于"保险性"需求，优先级低于核心管线。

**Independent Test**: 手动禁用统一管线触发点，发送几条对话后手动触发 Consolidation，验证 Scheduler 能处理遗留的未整合 Fragment。

**Acceptance Scenarios**:

1. **Given** 统一管线因 LLM 不可用而跳过了数次提取, **When** Scheduler 定期 Consolidation 触发, **Then** 积累的 Fragment（如有）被正常整合为 SoR。
2. **Given** 用户在管理台点击"手动整合", **When** Consolidation 执行, **Then** 目标 scope 下的未整合 Fragment 被批量处理。
3. **Given** Agent 在对话中调用 `memory.write` 工具, **When** 写入完成, **Then** 记忆通过 propose-validate-commit 治理流程正常写入 SoR。

---

### Edge Cases

- **LLM 提取服务不可用**: 记忆提取 LLM 调用失败时，管线静默跳过本次提取（不阻塞对话），cursor 不更新，下次触发时重新尝试。日志记录降级事件。
- **Session 在提取过程中被关闭**: 已触发的提取任务继续执行到完成（fire-and-forget），cursor 正常更新。Session 关闭不会中断正在执行的后台任务。
- **单次对话产出大量可提取信息**: LLM 单次提取的输出量上限由 LLM 的 max_tokens 参数约束。若 LLM 因上下文过长而截断，系统接受部分结果并推进 cursor，不重试整段。
- **Tool Call 压缩丢失关键信息**: 压缩摘要保留工具名和关键结果。若某个工具调用的结果对记忆提取至关重要（如搜索结果），压缩摘要中应包含足够的关键信息供 LLM 判断。
- **并发 Session 写入同一 Scope**: 多个 Session 的提取管线可能并发写入同一个 Memory Scope。通过 propose-validate-commit 的治理流程和 subject_key 去重保证一致性。
- **Memory Cursor 与实际 turn 数不一致**: 若 turn 记录因异常被部分写入，Cursor 可能指向一个不存在的序列号。提取时应查询 Cursor 之后的所有 turn，不假设序列号连续。
- **新旧路径过渡期**: 升级过程中可能存在旧路径已产出但未整合的 Fragment。Scheduler 定期 Consolidation 兜底处理这些历史遗留。
- **空 Session / 无新 turn**: cursor 等于最新 turn_seq 时，提取管线检测到无新内容后立即返回，不调用 LLM。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 在每次 Agent（Butler 或 Worker）响应完成后自动触发记忆提取管线，触发时机为响应上下文记录流程的末尾。
- **FR-002**: 记忆提取 MUST 以 fire-and-forget 方式异步执行，不阻塞 Agent 响应的返回。
- **FR-003**: AgentSession MUST 持久化一个记忆提取游标（Memory Cursor，整型，默认 0），记录记忆提取已处理到的 turn sequence。
- **FR-004**: 每次记忆提取 MUST 只读取 Memory Cursor 之后的新增 turn，不重复处理已提取内容。
- **FR-005**: Memory Cursor MUST 在 SoR 写入成功后（或 LLM 明确返回空结果后）才更新，确保崩溃恢复时未确认的 turn 可以被重新处理。
- **FR-006**: 系统 MUST 将 Tool Call 的原始输入/输出压缩为摘要格式后传入提取 LLM，摘要 MUST 保留工具名称和关键结果。
- **FR-007**: 系统 MUST 通过单次 LLM 调用完成所有类型记忆的提取（facts、solutions、entities/relations、ToM），不进行多轮分类调用。
- **FR-008**: 当 LLM 判断无值得记忆的内容时，系统 MUST 返回空结果且不写入任何 Fragment 或 SoR。
- **FR-009**: 系统 MUST 移除"响应完成后自动写入通用记忆 Fragment"的行为路径。Vault 安全证据写入（Private Tool Evidence）MUST 保留，其职责与记忆提取正交。
- **FR-010**: 系统 MUST 移除"上下文压缩（Compaction）流程中碎片化写入 Fragment"的行为路径。
- **FR-011**: 系统 MUST 移除"Compaction 后注入静默记忆提取 turn"的行为路径及其所有触发点。
- **FR-012**: 系统 MUST 移除"Fragment 写入后自动触发 Consolidation"的行为路径。
- **FR-013**: 系统 MUST 保留 `memory.write` 工具作为 Agent 主动写入通道。
- **FR-014**: 系统 MUST 保留 Scheduler 定期 Consolidation 作为兜底机制。
- **FR-015**: 系统 MUST 保留管理台手动 Consolidation 入口。
- **FR-016**: 记忆提取的产出 MUST 通过现有的 propose-validate-commit 治理流程写入 SoR。
- **FR-017**: Fragment SHOULD 记录提取时的原始对话段落作为 SoR 的溯源证据，并通过 evidence_ref 与 SoR 记录关联。
- **FR-018**: 记忆提取管线 MUST 全自动运行，不需要用户审批。
- **FR-019**: 当记忆提取 LLM 不可用时，系统 MUST 静默跳过本次提取并记录降级日志，不影响对话流程。
- **FR-020**: LLM SHOULD 按语义边界自行决定 Fragment 粒度，系统不预设固定的分片大小规则。
- **FR-021**: 记忆提取 LLM 调用 SHOULD 使用 `fast` alias（低成本、结构化输出足够），可通过配置切换。
- **FR-022**: 记忆提取管线 MUST 防止同一 Session 的并发提取（per-Session 互斥），后续触发在前一次提取尚未完成时 SHOULD 跳过（try-lock 语义）。
- **FR-023**: 记忆提取 MUST 仅对 `BUTLER_MAIN`、`WORKER_INTERNAL`、`DIRECT_WORKER` 三种 Session kind 触发，`SUBAGENT_INTERNAL` Session 不独立触发提取。
- **FR-024**: 记忆提取管线 MUST 从 AgentSession 关联的 project_id/workspace_id 推导目标 scope_id，复用现有 scope 计算逻辑。

### Key Entities

- **AgentSession**: 绑定到 AgentRuntime 的会话对象。新增 Memory Cursor 字段，追踪记忆提取进度。核心属性包括近期对话记录和滚动摘要。
- **AgentSessionTurn**: Session 的每个 turn 记录，包含 `turn_seq`（序列号）、`kind`（消息类型）、`role`、`tool_name`、`summary` 等字段。是记忆提取的输入数据源。
- **Fragment**: 从"记忆的主要载体"转变为"SoR 的溯源证据"。记录被提取的原始对话段落，为 SoR 提供出处追溯。
- **SoR (System of Record)**: 长期记忆的权威记录。由记忆提取管线通过 propose-validate-commit 流程产出。
- **Derived**: 从 SoR 派生的结构化信息（实体、关系等）。与 SoR 一同作为记忆检索的主要目标。
- **Memory Cursor**: AgentSession 上的整型游标，标记记忆提取已处理到的 turn 位置。Cursor 仅在写入成功后才推进，保证崩溃恢复的正确性。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Agent 每次响应完成后，记忆提取管线自动触发，用户无需任何手动操作即可实现对话知识的持久化。
- **SC-002**: 对话中包含的有价值信息（个人偏好、项目决策、关键事实等）能够在下次相关对话中被准确召回。
- **SC-003**: 记忆写入路径从当前的 6 条收敛为 1 条统一入口（加 1 条保留的 `memory.write` 主动通道），消除因多路径导致的重复和碎片化问题。
- **SC-004**: 记忆提取的延迟不影响用户感知——从用户发送消息到收到 Agent 响应的时间不因记忆管线而增加。
- **SC-005**: 进程意外中断后重启，未处理的对话内容能够在下次触发时被正确提取，不丢失也不重复。
- **SC-006**: 纯问答、闲聊等不含长期记忆价值的对话不产生任何存储写入，避免存储资源浪费。
- **SC-007**: 系统中不存在统一管线和 `memory.write` 之外的其他记忆写入路径——被废弃的 4 条旧路径（通用 Fragment 写入、Compaction 碎片化写入、静默记忆提取 turn 注入、写入后自动 Consolidation）的代码和调用点被完全移除，不留下死代码。

## Glossary

- **fire-and-forget**: 异步执行模式——调用方发起任务后立即返回，不等待任务完成。任务在后台独立运行，失败不影响调用方的主流程。
- **Memory Cursor（记忆游标）**: AgentSession 上持久化的整型值，标记记忆提取已处理到的 turn 位置。用于实现增量处理和崩溃恢复。
- **幂等性（Idempotency）**: 同一操作执行多次与执行一次产生相同结果的特性。本 spec 中指崩溃恢复后重新处理同一批 turn 时，不会产生重复的 SoR 记录（通过 subject_key 去重实现）。
- **try-lock 语义**: 尝试获取锁，若锁已被占用则立即放弃（而非排队等待）。用于避免同一 Session 的记忆提取任务堆积。
- **SoR (System of Record)**: 长期记忆的权威记录，是记忆检索的主要目标。
- **Fragment**: 记忆的溯源证据单元，记录提取时的原始对话段落，为 SoR 提供出处追溯。
- **Derived**: 从 SoR 派生的结构化信息（实体、关系等），与 SoR 一同作为记忆检索目标。
- **propose-validate-commit**: 记忆写入的三阶段治理流程——先提议写入内容，再验证合法性（去重、冲突检测等），最后确认写入。
- **Compaction（上下文压缩）**: 当 Agent 对话上下文超出窗口限制时，系统将历史 turn 压缩为摘要以释放上下文空间的过程。
- **scope_id**: 记忆空间的隔离标识，通常由 project_id 和 workspace_id 推导。不同 Agent/Project 的记忆存储在不同 scope 下。

## Clarifications

### Session 2026-03-19

#### Q1: Private Tool 安全证据写入的处置方式

**上下文**: 响应上下文记录流程末尾除了通用记忆 Fragment 写入（已明确废弃）外，还包含 Private Tool 安全证据写入。FR-009 明确废弃前者，但未提及后者。

**[AUTO-CLARIFIED: 保留 Private Tool 安全证据写入]** — 该行为处理的是 Private Tool（如 Vault 密码查询）的安全证据写入，属于合规/审计通道，与记忆提取管线的"知识沉淀"职责正交。Private Tool 证据不应进入 LLM 提取管线（避免敏感信息泄露到 memory），应继续走独立的 Vault 写入路径。

#### Q2: 记忆提取使用的 LLM Model Alias

**上下文**: FR-007 要求单次 LLM 调用完成提取，但未指定使用哪个 LiteLLM alias。系统当前有 `fast`（低延迟）、`default`（平衡）、`strong`（高质量）等多个 alias。

**[AUTO-CLARIFIED: 使用 `fast` alias]** — 记忆提取是后台异步任务，对延迟不敏感但对成本敏感。提取 prompt 结构化程度高（structured output），`fast` 级别模型足以胜任。若未来发现提取质量不足，可通过配置切换到 `default`，无需代码变更。

#### Q3: 记忆提取的 Memory Scope 确定方式

**上下文**: 记忆系统使用 scope_id 区分不同 Agent/Project 的记忆空间。spec 未说明提取管线如何确定目标 scope_id。当前通用 Fragment 写入路径中通过 Session 关联的 project/workspace 信息计算 scope。

**[AUTO-CLARIFIED: 复用现有 scope 推导逻辑]** — 提取管线从 AgentSession 关联的 AgentRuntime 获取 project_id，结合 workspace_id 推导 scope_id。逻辑与现有通用 Fragment 写入路径中的 scope 计算方式一致，确保提取结果写入正确的 memory scope。

#### Q4: 并发提取的防护机制

**上下文**: FR-002 要求 fire-and-forget 异步执行。若用户快速连续发送消息，可能在前一次提取尚未完成时触发下一次提取，导致同一 Session 两个并发提取任务读取重叠的 turn 范围。

**[AUTO-CLARIFIED: per-Session 互斥防护]** — 每个 AgentSession 维护一个互斥锁，提取任务启动时获取锁，完成后释放。后续触发在发现已有提取进行中时跳过（try-lock 语义），避免排队堆积。锁粒度为 Session 级别，不同 Session 的提取互不阻塞。

#### Q5: Subagent Session 是否触发记忆提取

**上下文**: AgentSession 有 4 种 kind：`BUTLER_MAIN`、`WORKER_INTERNAL`、`DIRECT_WORKER`、`SUBAGENT_INTERNAL`。spec 中 US-1 提到 "Butler 或 Worker" 响应时触发，未明确 Subagent 的情况。

**[AUTO-CLARIFIED: Subagent Session 不触发独立提取]** — Subagent 是 Worker 按需创建的临时智能体，共享 Worker 的 Project。Subagent 的对话内容会通过 Worker 的 Session turn 记录（Subagent 结果回传给 Worker 时记录为 tool_result turn），因此会在 Worker Session 的提取中被间接覆盖。独立为 Subagent 触发提取会导致重复提取和 scope 归属混乱。仅 `BUTLER_MAIN`、`WORKER_INTERNAL`、`DIRECT_WORKER` 三种 Session kind 触发记忆提取。
