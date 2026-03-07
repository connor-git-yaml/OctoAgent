---
feature_id: "020"
title: "Memory Core + WriteProposal + Vault Skeleton"
milestone: "M2"
status: "Implemented"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.7 / §9.9 / M2"
predecessor: "Feature 015（M2 DX 闭环，已交付）"
parallel_dependency: "Feature 021 将复用本 Feature 冻结的 Memory contract"
---

# Feature Specification: Memory Core + WriteProposal + Vault Skeleton

**Feature Branch**: `codex/feat-020-memory-core`
**Created**: 2026-03-07
**Status**: Implemented
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 020，交付最小记忆治理内核：`Fragments + SoR + WriteProposal + Vault skeleton`。
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

## Problem Statement

OctoAgent 在 M1.5 已具备任务、事件、checkpoint 和 Worker 基础能力，但长期记忆仍然缺少可治理内核：

1. 系统还没有“当前定稿”与“历史过程”分离的存储层，导致未来很容易把旧结论和新结论混在一起。
2. 模型尚无统一的记忆写入 contract，未来 Chat Import、Worker 记忆写入和 context flush 容易各自直写。
3. 敏感信息分区还没有 default deny 的骨架，`health` / `finance` 等分区没有可落盘但不可检索的承载点。
4. Feature 021/023 需要稳定的 Memory contract，但当前仓库还没有 `search/get`、proposal、arbitration 的统一接口。
5. 现有研究和资产已经表明 MemU 在长期记忆检索、增量更新和 chat import 侧有沉淀，M2 需要提前留出可插拔 backend 位，而不是等到 M3 再硬接。

Feature 020 要先冻结“长期记忆行为约束”，而不是一次性做完知识库、聊天导入、向量检索和 UI。

---

## User Scenarios & Testing

### User Story 1 - 当前定稿稳定可读 (Priority: P1)

作为依赖长期记忆做决策的系统，我希望对同一主题总能稳定读到最新定稿，而不是从多个历史版本里猜哪个是最新，这样后续 Worker 和导入链路才能基于一致事实继续执行。

**Why this priority**: 这是 020 的核心验收目标。如果 `SoR.current` 不稳定，Feature 020 就失去意义。

**Independent Test**: 连续对同一 `subject_key` 执行一次 ADD 和一次 UPDATE，然后查询 current 记录，验证只返回最新版本且旧版本变为 `superseded`。

**Acceptance Scenarios**:

1. **Given** 某个 `subject_key` 还没有 current 记录，**When** 提交合法的 `ADD` proposal 并 commit，**Then** 系统创建 version 1 的 current SoR 记录。
2. **Given** 某个 `subject_key` 已存在 current 记录，**When** 提交合法的 `UPDATE` proposal 并 commit，**Then** 旧记录转为 `superseded`，新记录成为唯一 current。
3. **Given** 同一 `scope_id + subject_key` 已有 current，**When** 直接尝试插入第二条 current，**Then** 数据库约束拒绝该写入。

---

### User Story 2 - 写入必须经过仲裁 (Priority: P1)

作为系统治理层，我希望任何长期记忆写入都必须先形成 `WriteProposal`，通过证据校验、action 合法性和冲突检测后才能提交，这样模型不会绕过规则直接污染 SoR。

**Why this priority**: 这是 constitution 对记忆写入的硬约束，也是 021/023 接入真实记忆前的阻塞条件。

**Independent Test**: 构造一条缺失 `evidence_refs` 或 action 非法的 proposal，验证 `validate_proposal()` 返回拒绝，且 `commit_memory()` 不会写入 SoR/Vault。

**Acceptance Scenarios**:

1. **Given** proposal 缺少证据引用或 `confidence` 超出范围，**When** 调用 `validate_proposal()`，**Then** 系统拒绝该 proposal 并给出原因。
2. **Given** proposal 已通过验证，**When** 调用 `commit_memory()`，**Then** 系统只按 proposal.action 允许的路径写入 Fragments/SoR/Vault。
3. **Given** proposal 未通过验证，**When** 直接调用 commit，**Then** 系统拒绝执行并保持存储不变。

---

### User Story 3 - 敏感分区默认不可检索 (Priority: P1)

作为系统操作者，我希望敏感记忆可以被安全落盘，但默认不会出现在普通搜索结果里，这样系统既能保留审计和恢复所需数据，也不会把隐私内容随手回灌给模型。

**Why this priority**: Vault default deny 是 blueprint 和 constitution 的明确要求，不能推迟到 UI 阶段再补。

**Independent Test**: 写入一条 `health` 或 `finance` 分区记录，再执行默认搜索，验证返回为空；只有显式授权的读取请求才能拿到详情。

**Acceptance Scenarios**:

1. **Given** proposal 被路由到 Vault 分区，**When** 默认执行 `search_memory()`，**Then** 返回结果不包含该记录。
2. **Given** Vault 记录存在，**When** 未授权调用 `get_memory()`，**Then** 系统拒绝返回详情。
3. **Given** Vault 记录存在且请求显式带授权，**When** 调用 `get_memory()`，**Then** 系统返回 skeleton 详情与证据引用。

---

### User Story 4 - 为未来 compaction 和导入留接口 (Priority: P2)

作为后续 Feature 021/Context Manager 的实现者，我希望 020 先冻结最小写入与读取 contract，并预留 compaction 前 flush 钩子，这样后续接入聊天导入、上下文压缩和向量检索时不需要推翻现有内核。

**Why this priority**: 020 不是终态，但必须成为后续并行开发的稳定 contract。

**Independent Test**: 调用 `before_compaction_flush()`，验证其只生成 proposal / fragment 结果，不直接改 SoR current。

**Acceptance Scenarios**:

1. **Given** 有待压缩的上下文摘要输入，**When** 调用 `before_compaction_flush()`，**Then** 返回 fragment/proposal 草案而不直接写入 SoR。
2. **Given** Feature 021 需要接入 Memory contract，**When** 调用 `search_memory()` / `get_memory()`，**Then** 能使用统一接口而不是直接访问 SQLite 表。

---

### Edge Cases

- 当 `UPDATE` proposal 指向不存在的 `subject_key` current 记录时，系统应如何处理？
- 当 `DELETE` proposal 与当前版本不匹配时，系统如何阻止误删？
- 当同一 `subject_key` 在短时间内重复提交多个 proposal 时，系统如何保证最终只有一个 current？
- 当 proposal 指向敏感分区，但 evidence 中包含普通 fragment 引用时，系统如何避免原文泄漏？
- 当 `before_compaction_flush()` 产生的 proposal 需要后续人工/规则复核时，系统如何保留审计状态？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `FragmentRecord`、`SorRecord`、`WriteProposal` 和 `VaultRecord` 的强类型数据模型。
- **FR-002**: 系统 MUST 将 `layer/type` 与 `partition/scope` 区分建模，避免把业务分区与存储层角色混在同一字段。
- **FR-003**: 系统 MUST 支持 `WriteProposal.action = ADD | UPDATE | DELETE | NONE`。
- **FR-004**: 系统 MUST 要求每个非 `NONE` proposal 包含 `subject_key`、`confidence`、`rationale` 和至少一个 `evidence_refs`。
- **FR-005**: 系统 MUST 提供 `validate_proposal()`，执行 action 合法性、证据存在性、当前版本匹配和基础冲突检测。
- **FR-006**: 系统 MUST 只允许已通过验证的 proposal 进入 `commit_memory()`。
- **FR-007**: 系统 MUST 在 SoR 中保证同一 `scope_id + subject_key` 永远只有一条 `status=current` 记录。
- **FR-008**: 系统 MUST 在 `UPDATE` 成功后将旧版 SoR 标记为 `superseded`，并写入递增 version 的新 current 记录。
- **FR-009**: 系统 MUST 将 `Fragments` 设计为 append-only，不提供覆盖更新接口。
- **FR-010**: 系统 MUST 支持默认排除 Vault 的 `search_memory()`。
- **FR-011**: 系统 MUST 在未显式授权时拒绝 `get_memory()` 对 Vault 记录的读取。
- **FR-012**: 系统 MUST 提供 `get_memory()` 读取单条 SoR/Fragment/Vault 记录详情。
- **FR-013**: 系统 SHOULD 为未来 compaction / context flush 提供 `before_compaction_flush()` 钩子，但该钩子 MUST NOT 直接写入 SoR。
- **FR-014**: 系统 MUST 为每次 proposal 验证和提交保留可审计状态，包括验证结果、拒绝原因和提交时间。
- **FR-015**: 系统 MUST 提供可插拔 `MemoryBackend` 协议，使 M2 可接入 `MemUBackend` 承担检索、索引和增量同步等 memory engine 工作，同时不改变治理层 contract。
- **FR-016**: 系统 MUST 在 backend 不可用时自动降级回本地 SQLite metadata 路径，不得阻塞 SoR/Vault 治理写入。
- **FR-017**: 系统 MUST NOT 在 Feature 020 中直接实现 Chat Import Core、微信/Telegram adapter、Vault 授权审批流程；这些能力可由 `MemUBackend` 在后续 Feature 中承接。

### Key Entities

- **FragmentRecord**: 过程性记忆对象，append-only，保存摘要、过程证据或上下文 flush 结果。
- **SorRecord**: 权威记忆对象，按 `subject_key` 版本化，区分 `current` 与 `superseded`。
- **WriteProposal**: 记忆写入提案，描述目标分区、action、证据、置信度和预期版本。
- **VaultRecord**: 敏感分区对象，默认不可被普通检索访问，保留摘要和证据引用。
- **MemorySearchHit**: 检索结果摘要对象，用于 search/get 两段式读取。
- **MemoryAccessPolicy**: 读取策略对象，控制是否允许访问 Vault 或历史版本。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 对同一 `subject_key` 连续执行 ADD + UPDATE 后，系统始终只保留 1 条 current SoR 记录。
- **SC-002**: 所有非法或缺证据的 proposal 都会在 `validate_proposal()` 阶段被拒绝，且不会写入 SoR/Vault。
- **SC-003**: 默认 `search_memory()` 不返回任何 Vault 记录。
- **SC-004**: `before_compaction_flush()` 在测试中不会直接改写 SoR current，只返回 proposal / fragment 草案。
- **SC-005**: Memory package 提供的单元和集成测试可稳定通过，覆盖唯一 current、冲突写入、Vault default deny 三类关键场景。

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 020 是否实现 Chat Import Core？ | 否 | blueprint 已将其划入 Feature 021 |
| 2 | 020 是否实现工作上下文 GC / compaction 引擎？ | 否，只保留钩子 | 避免把长期记忆和上下文压缩耦合 |
| 3 | Vault 是否在 020 就开放授权检索？ | 否，只做 skeleton + default deny | 授权检索属于 M3 |
| 4 | 是否必须本次就引入向量库写路径？ | 否，但要保留 backend 位 | 先冻结治理契约和 SQLite 约束，再让 MemUBackend 在 M2 接大部分 engine 工作 |

## Scope Boundaries

### In Scope

- `packages/memory` 基础包
- SoR / Fragments / Vault skeleton 数据模型
- `WriteProposal -> validate -> commit` 服务闭环
- `MemoryBackend` / `MemUBackend` adapter 位
- SQLite 持久化和 current 唯一约束
- `search_memory()` / `get_memory()` / `before_compaction_flush()` 基础接口
- 单元与集成测试

### Out of Scope

- 直接内嵌 MemU 业务逻辑实现
- Chat Import Core 本体
- 微信/Telegram adapter
- Vault 授权审批与浏览 UI
- 工作上下文 GC / auto-compaction 运行时
