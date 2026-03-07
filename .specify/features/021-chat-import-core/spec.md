---
feature_id: "021"
title: "Chat Import Core"
milestone: "M2"
status: "Draft"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.7.5 / §11.6 / M2, docs/m2-feature-split.md Feature 021"
predecessor: "Feature 020（Memory Core + WriteProposal）、Feature 022（Backup/Export 闭环）"
parallel_dependency: "Feature 023 将消费本 Feature 的 CLI/导入报告/记忆写入结果；021 不回改 020 contract"
---

# Feature Specification: Chat Import Core

**Feature Branch**: `codex/feat-021-chat-import-core`
**Created**: 2026-03-07
**Status**: Draft
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 021，交付聊天导入通用内核，支持增量去重、窗口化摘要、按 chat scope 写入记忆，并补齐 M2 的用户可触达入口。
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

OctoAgent 在 M2 已经具备 Memory Core、会话导出、operator control 和多渠道运行时基础，但聊天导入仍停留在 blueprint 层：

1. 020 已经冻结了 `WriteProposal -> validate -> commit` 的记忆治理 contract，但还没有任何导入路径把历史聊天安全地接进来。
2. `blueprint` 与 `m2-feature-split` 都要求 M2 的导入链路“可用”，但当前 021 拆解只覆盖导入内核，没有覆盖用户入口、dry-run 预览和导入报告。
3. 没有 dedupe ledger / import cursor / provenance artifact，用户无法信任重复执行是否安全，也无法回看导入依据。
4. 如果导入直接把原文写进主上下文或 live session，就会污染实时对话，破坏 scope 隔离和后续检索质量。
5. 如果导入事实不经过 020 的 proposal 仲裁，错误信息会直接污染 SoR，后续难以审计和修正。

Feature 021 要解决的是：

- 让用户有一条稳定入口把历史聊天导入系统；
- 让重复执行安全、导入结果可回看、失败原因可定位；
- 让原文、摘要、事实写入在统一 durability / audit 体系下协同，而不是各走旁路。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 020：Memory Core + WriteProposal | 已交付 | 021 必须复用既有 memory contract，不得旁路写 SoR |
| Feature 022：Backup/Export | 已交付 | 021 需要与现有 CLI / artifact / bundle 路径保持兼容 |
| `NormalizedMessage` 模型 | 已交付 | 导入内核需要复用统一消息语义，而不是发明新消息格式 |
| Event Store / Artifact Store | 已交付 | 021 通过 operational task 写生命周期事件和原文审计 artifact |

前置约束：

- 021 不得重写 020 的 `search_memory()` / `get_memory()` / proposal contract。
- 021 必须补齐用户入口；只做库层不满足当前 M2 目标。
- 021 不交付具体微信 / Slack adapter；它交付的是 generic import core 和统一 source contract。
- 021 需要在设计批准后回写 `docs/blueprint.md` 与 `docs/m2-feature-split.md`，补入“CLI 入口 / dry-run / 导入报告”这三个当前遗漏的需求点。

---

## User Scenarios & Testing

### User Story 1 - 先预览，再安全导入历史聊天 (Priority: P1)

作为 owner，我希望先通过 dry-run 看到本次导入会写入多少消息、跳过多少重复、生成哪些 scope 和窗口摘要，然后再决定是否执行真正导入，这样我不会把一批不可信或重复的数据直接塞进系统。

**Why this priority**: 没有入口和 dry-run，导入行为对用户就是黑箱，M2 的“可用入口”实际上不成立。

**Independent Test**: 准备一份包含 20 条消息的导入文件，其中 5 条与历史批次重复。先执行 `octo import chats --dry-run`，验证输出的新增/重复/窗口数量准确且没有落盘写入；再执行真实导入，验证只写入新增消息。

**Acceptance Scenarios**:

1. **Given** 用户提供一份可识别的聊天导入源，**When** 执行 `octo import chats --dry-run`，**Then** 系统返回新增数、重复数、scope、窗口数和 warnings，且不创建 memory / artifact 写入。

2. **Given** 同一份导入源已执行过一次，**When** 用户再次执行真实导入，**Then** 系统只处理新增消息，不重复写入已导入内容。

3. **Given** 导入源存在格式问题或缺少必填字段，**When** 用户执行 dry-run 或真实导入，**Then** 系统明确返回错误原因，而不是部分静默失败。

---

### User Story 2 - 导入后的聊天必须隔离且可审计 (Priority: P1)

作为 owner，我希望导入的历史聊天进入独立 chat scope，并保留原文引用与窗口摘要，而不是直接混进当前实时会话，这样我之后检索和排障时能分清“这是历史导入内容”还是“这是实时聊天上下文”。

**Why this priority**: scope 隔离与 provenance 是导入可信度的基础；没有它，导入会直接破坏后续使用体验。

**Independent Test**: 导入一个 thread 的历史聊天后，检查写入结果，验证 scope 为 `chat:<channel>:<thread_id>` 或等价 chat scope，原文作为 artifact 保留，窗口摘要进入 fragment，且未污染其他 scope。

**Acceptance Scenarios**:

1. **Given** 一个外部聊天 thread 被导入，**When** 导入完成，**Then** 系统将其写入独立 chat scope，而不是当前 live thread 默认 scope。

2. **Given** 导入窗口包含较长原文，**When** 导入完成，**Then** 原始聊天文本保留为 artifact 引用，fragment 中只保留窗口摘要或精简摘要。

3. **Given** 用户后续检索该导入范围，**When** 查看导入报告或 memory 记录，**Then** 能定位到对应 artifact ref，而不是只看到脱离来源的摘要文本。

---

### User Story 3 - 导入必须支持中断恢复和增量继续 (Priority: P1)

作为 owner，我希望导入在中断后可以从上次 cursor 继续，并且在多次增量同步时保持稳定去重，这样我不需要每次都重头导入，也不用担心系统越导越乱。

**Why this priority**: 历史聊天导入天然是批量、耗时且可能重复执行的任务，没有 cursor / resume 就不具备日常可用性。

**Independent Test**: 模拟一批导入在处理中断后重跑，验证系统读取上次 cursor，跳过已写入消息，并最终生成正确的导入报告。

**Acceptance Scenarios**:

1. **Given** 某次导入在处理到一半时中断，**When** 用户重新执行同一导入，**Then** 系统能从最近的 cursor 或 dedupe ledger 继续，而不是重写整批数据。

2. **Given** 用户周期性导入同一聊天源的新消息，**When** 执行增量导入，**Then** 系统只处理 cursor 之后或 dedupe ledger 未命中的新消息。

3. **Given** 某条消息没有原始 message id，**When** 参与增量导入，**Then** 系统使用规范化 hash 去重，仍然保持重复执行安全。

---

### User Story 4 - 事实提取必须继续受 Memory 治理约束 (Priority: P2)

作为 owner，我希望从聊天窗口提炼出的稳定事实仍然通过 020 的 proposal 仲裁链进入 SoR，而不是在导入时直接落成 current，这样错误摘要不会直接污染长期记忆。

**Why this priority**: 021 的价值在于把历史聊天接进治理体系，而不是绕过治理体系。

**Independent Test**: 导入一个含明确项目状态的窗口，验证系统先生成 proposal，再由 validate / commit 写入 SoR；当证据或置信度不足时，系统只保留 fragment，不直接产生 current SoR。

**Acceptance Scenarios**:

1. **Given** 某个窗口能提炼出明确事实，**When** 导入流程处理该窗口，**Then** 系统通过 `WriteProposal -> validate -> commit` 写入 SoR，而不是直接插入 current 记录。

2. **Given** 某个窗口只包含过程性聊天或证据不足，**When** 导入流程处理该窗口，**Then** 系统只写 fragment 或记录 `NONE` 型 proposal，不污染 SoR。

3. **Given** 导入过程中某个 proposal 验证失败，**When** 导入完成，**Then** 最终报告会记录失败原因和受影响窗口，而不是吞掉错误。

---

### Edge Cases

- 同一导入批次被用户重复执行多次时，系统如何保证零重复写入？
- 导入源没有稳定消息 ID 时，hash 去重如何避免因为轻微文本差异导致误判？
- 导入窗口很大、原文超过 artifact inline 阈值时，系统如何保留引用同时避免大文本进入上下文？
- 导入过程中部分窗口成功、部分窗口失败时，用户如何看到精确结果？
- 当 memory backend 或摘要生成能力临时不可用时，系统是否仍能降级完成 dry-run 或 fragment-only 导入？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供用户可触达的最小 CLI 入口 `octo import chats`，用于触发聊天导入。

- **FR-002**: 系统 MUST 支持 `--dry-run` 预览模式，展示导入规模、重复数量、目标 scope、窗口数和 warnings，且 dry-run MUST NOT 写入 memory / artifact / event 副作用。

- **FR-003**: 系统 MUST 提供 `ImportBatch`、`ImportCursor`、`ImportWindow`、`ImportSummary` 和 `ImportReport` 等强类型模型，用于持久化批次状态和执行结果。

- **FR-004**: 系统 MUST 支持增量去重，优先使用源消息 ID；当源消息 ID 不存在时，MUST 回退到 `hash(sender_id + timestamp + normalized_text)` 或等价稳定 hash。

- **FR-005**: 系统 MUST 持久化 dedupe ledger，使同一消息在多次导入或恢复执行时不会重复写入。

- **FR-006**: 系统 MUST 持久化 import cursor，使导入中断后能够从最近位点继续，而不是要求整批重跑。

- **FR-007**: 系统 MUST 将导入内容映射到独立 chat scope，遵循 `scope_id=chat:<channel>:<thread_id>` 或等价 chat scope 规则。

- **FR-008**: 系统 MUST 将原始聊天窗口保存为可审计 artifact 引用，而不是把长原文直接塞入主上下文或 fragment 正文。

- **FR-009**: 系统 MUST 为每个导入窗口生成摘要性 fragment，使导入结果可被后续 memory search / retrieval 消费。

- **FR-010**: 当导入窗口提炼出稳定事实时，系统 MUST 通过 020 的 `WriteProposal -> validate -> commit` contract 写入 SoR；系统 MUST NOT 在 021 中直接旁路写 SoR current。

- **FR-011**: 当窗口证据不足、冲突不明或置信度不足时，系统 MUST 支持 fragment-only 或 `NONE` 型 proposal 路径，而不是强制写入 SoR。

- **FR-012**: 系统 MUST 在导入完成后产出持久化 `ImportReport`，至少包含：新增数、重复数、窗口数、proposal 结果、warnings、errors、cursor、scope 和 artifact refs。

- **FR-013**: 系统 MUST 为导入生命周期保留统一审计链，至少包含 started / completed / failed 三类事件。

- **FR-014**: 若导入生命周期事件或 artifact 需要依附 `task_id`，系统 MUST 使用 dedicated operational task 承载，而不是新建旁路日志源。

- **FR-015**: 系统 MUST 优先复用项目主 SQLite、既有 artifacts 目录和 Event Store，避免为 021 新建独立的生产数据孤岛。

- **FR-016**: 系统 SHOULD 为 future source adapter 保留统一 source contract，但 Feature 021 MUST NOT 直接交付微信 / Slack / Telegram 历史解析 adapter。

- **FR-017**: 系统 MUST 在部分能力不可用时优雅降级：例如摘要 / backend 不可用时，仍能完成 dry-run 或生成受限报告，而不是整体不可用。

- **FR-018**: 系统 MUST 让用户明确看到导入结果与失败原因，不允许出现“命令成功退出但用户不知道写入了什么”的黑箱行为。

### Key Entities

- **Import Batch**: 一次聊天导入执行的批次对象，记录来源、scope、状态、cursor 和结果摘要。
- **Import Cursor**: 增量位点对象，用于恢复执行和周期性增量同步。
- **Import Window**: 按时间或消息数量切分的原始聊天窗口，关联 artifact ref 与摘要。
- **Import Summary**: 本次执行的统计摘要，包含新增、重复、窗口、proposal 和 warnings。
- **Import Report**: 面向用户的持久化报告，告诉用户本次导入实际发生了什么。
- **Import Dedupe Entry**: 用于保证重复执行安全的去重账本记录。
- **Operational Import Audit Task**: 承载导入事件和 artifact 的 dedicated operational task。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以通过 `octo import chats` 完成一次 dry-run 或真实导入，不需要调用内部脚本或手写 Python 代码。

- **SC-002**: 对同一输入重复执行导入时，系统不会重复写入已存在消息；测试中重复执行的新增数为 0。

- **SC-003**: 导入结果始终落入独立 chat scope，不污染不相关 live session scope。

- **SC-004**: 每次导入都会产生持久化报告，包含 counts、cursor、warnings、artifact refs 和失败原因。

- **SC-005**: 原始聊天窗口可通过 artifact ref 回看，窗口摘要可通过 memory 读路径消费。

- **SC-006**: 任何 SoR 写入都经过 proposal 验证链，测试中不存在 021 直接插入 current SoR 的旁路写入。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 021 是否只做导入库层，不做用户入口？ | 否 | M2 已要求 Chat Import 有用户可触达入口；只做库层不满足目标 |
| 2 | 021 是否交付具体微信 / Slack adapter？ | 否 | 021 交付通用内核和 source contract，具体 adapter 后续实现 |
| 3 | 021 是否必须支持 dry-run？ | 是 | 导入属于高不确定性批量副作用，必须先预览再执行 |
| 4 | 导入原文是否可以直接进入 fragment / 主上下文？ | 否 | 原文必须 artifact 化，fragment 仅保留摘要 |
| 5 | 021 是否允许直接写 SoR？ | 否 | 必须走 020 proposal contract |
| 6 | 021 是否应复用项目主 SQLite？ | 是 | 这样 022 backup/export 才能自然覆盖导入元数据与 memory 写入 |
| 7 | 021 是否本轮同时做 Web 导入面板？ | 否 | CLI first 已足够满足 M2 最小可用入口 |

---

## Scope Boundaries

### In Scope

- 通用聊天导入内核
- `octo import chats` CLI 入口
- `--dry-run` 预览
- import batch / cursor / dedupe / report 持久化
- 原文 artifact + 窗口摘要 fragment
- proposal 驱动的 SoR 写入
- 导入生命周期审计事件
- 单元 / 集成测试

### Out of Scope

- 微信 / Slack / Telegram 历史具体 adapter
- Web 导入面板或批次管理后台
- 导入批次回滚 / 删除
- Vault 敏感数据治理增强
- 自动定时导入与订阅机制
