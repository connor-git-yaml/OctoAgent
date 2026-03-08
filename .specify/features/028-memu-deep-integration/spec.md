---
feature_id: "028"
title: "MemU Deep Integration"
milestone: "M3"
status: "Implemented"
created: "2026-03-08"
updated: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/m3-feature-split.md Feature 028；docs/blueprint.md §8.7.4 / M3；Feature 020 Memory Core；Feature 025-B；Feature 026；Feature 027"
predecessor: "Feature 020 / Feature 025-B / Feature 026 / Feature 027"
---

# Feature Specification: MemU Deep Integration

**Feature Branch**: `028-memu-deep-integration`  
**Created**: 2026-03-08  
**Updated**: 2026-03-08  
**Status**: Implemented  
**Input**: 落实 M3 Feature 028：MemU Deep Integration。以上游为 `docs/m3-feature-split.md` 的 Feature 028、`docs/blueprint.md` 的 Memory 治理约束、Feature 020 Memory Core、当前仓库中的 `MemUBackend` 适配层，以及最新 master 已交付的 Feature 025-B 与 Feature 026 control plane。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

Feature 020 已经冻结了 Memory governance plane，但当前 `MemUBackend` 还只是一个非常薄的 adapter：

1. backend 只有 `search + sync_*`，没有结构化健康诊断、增量同步、maintenance、multimodal ingest 和 derived layer contract。
2. `search_memory()` 目前只有单向 degrade，没有标准化 failback、retry/backoff 和状态暴露。
3. `ChatImportProcessor` 虽然已经有窗口化与 artifact refs 输入结构，但尚未形成文本 / 图片 / 音频 / 文档统一 ingest 管线。
4. `Category / relation / entity / ToM` 等高级派生层仍停留在 blueprint/milestone 规划，没有稳定 backend contract。
5. Feature 027 已经交付 canonical Memory Console resource，但 028 仍缺少与之兼容的 engine-side query / evidence / diagnostics / integration contract；如果不补齐，后续深度集成很容易重新分叉 DTO 或旁路治理层。
6. Feature 025-B 与 Feature 026 已经提供了 project/secret/wizard 和 control-plane diagnostics/actions 的接缝，但 memory engine 还没有正式接进去。

本阶段目标不是重新设计 Memory Core，而是把 MemU 从“可选 adapter”升级为 OctoAgent memory 体系中的高级 engine plane，同时继续服从 020 的治理约束。

## Product Goal

交付一个可产品化的高级 memory engine contract：

- MemU 成为首选高级检索/索引/backend，而不是只做薄适配
- 多模态输入经 `artifact -> extractor/sidecar -> evidence -> derivation -> proposal` 路径进入 memory 体系
- derived layers 以 Category / relation / entity / ToM 形式可被查询和追溯
- consolidation / compaction / flush 形成可审计执行链
- MemU unavailable 时自动降级到核心 Memory 能力，并在恢复后自动回切
- 以兼容扩展方式补齐 Feature 027 所消费的 query/projection/integration contract
- 为 Feature 026 提供 diagnostics / action hook，而不是 detailed memory UI

## Architecture Boundary

### Governance Plane（继续由 OctoAgent 控制）

- `propose_write()`
- `validate_proposal()`
- `commit_memory()`
- SoR current 唯一约束
- Vault default deny
- `before_compaction_flush()` 草案入口

### Engine Plane（本 Feature 深度集成 MemU）

- retrieval / indexing
- incremental sync / replay
- multimodal ingest
- Category / relation / entity / ToM derived layers
- maintenance / consolidation / compaction / flush execution
- health diagnostics / degrade / failback

### Product / Surface Plane（本 Feature 不实现新 UI）

- Feature 027 继续作为 canonical Memory Console / Vault retrieval 产品面，并消费 028 补齐的 engine contract
- Feature 026 消费 diagnostics / action hooks
- Web 控制台、Memory Console、Vault 面板不在本 Feature 范围

---

## User Scenarios & Testing

### User Story 1 - 系统可以把 MemU 作为主 memory engine，但不会破坏治理事实源 (Priority: P1)

作为系统 owner，我希望在 MemU 可用时优先使用它进行更强的检索、索引和增量同步，但任何权威事实仍然只能经由 `WriteProposal -> validate -> commit` 进入 SoR/Vault。

**Why this priority**: 这是 028 的核心目标；如果 MemU 只是薄 adapter，本 Feature 没有成立；如果 MemU 直接写 SoR，本 Feature 就违反 blueprint。

**Independent Test**: 在 MemU healthy 时执行 search / sync / derived generation，验证 backend_used=memu；当有候选事实产生时，只返回 `WriteProposalDraft`，不直接改 SoR。

**Acceptance Scenarios**:

1. **Given** MemU bridge healthy，**When** 执行 `search_memory()`，**Then** 系统优先走 MemU backend，并返回可追溯的 hit / evidence 摘要。
2. **Given** derived 层识别出新的长期事实候选，**When** pipeline 完成，**Then** 系统只产出 `WriteProposalDraft`，而不是直接 upsert SoR。
3. **Given** MemU bridge 返回高级结果，**When** 027 查询 projection，**Then** 结果中仍能区分 SoR / Fragment / Derived，不混淆权威事实和派生视角。

---

### User Story 2 - 多模态材料进入统一证据链，而不是散落成不可解释的隐式记忆 (Priority: P1)

作为系统 owner，我希望文本、图片、音频、文档都能进入统一记忆流程，并且最终能追到 artifact、fragment 和 proposal 之间的关系。

**Why this priority**: M3 的高级记忆价值首先体现在 multimodal ingest。

**Independent Test**: 给系统输入文本、图片、音频、文档四类材料，验证 ingest 先生成 artifact 与必要的 extractor/sidecar 输出，再输出 fragment refs、derived refs 和可选 proposal drafts，且无 direct SoR write。

**Acceptance Scenarios**:

1. **Given** 输入一批聊天文本和文档，**When** ingest 完成，**Then** 结果包含 artifact refs、fragment refs 和 derived refs。
2. **Given** 输入图片或音频材料，**When** ingest 完成，**Then** 系统先产生标准 artifact refs 和 extractor/sidecar 输出，再进入 fragments / derived layers。
3. **Given** 某个多模态结果触发长期事实候选，**When** pipeline 结束，**Then** 候选事实以 `WriteProposalDraft` 形式返回，带完整 `evidence_refs`。

---

### User Story 3 - 高级派生层可以被浏览和解释，但不会被误当成事实层 (Priority: P1)

作为 027 的 consumer，我希望能够查询 Category / relation / entity / ToM 等派生结果，并看到它们的来源、置信度和关联 proposal，但这些结果不能自动冒充 SoR current。

**Why this priority**: 没有稳定派生层 contract，027 无法实现可信的 Memory Console。

**Independent Test**: 调用派生层 query/projection contract，验证返回 `derived_type`、`summary`、`confidence`、`source_fragment_refs`、`source_artifact_refs` 和可选 `proposal_ref`。

**Acceptance Scenarios**:

1. **Given** backend 已生成 entity / relation / ToM 结果，**When** 027 查询 `MemoryDerivedProjection`，**Then** 它能读到结构化摘要和来源 refs。
2. **Given** 某条 derived result 没有形成事实候选，**When** 读取 projection，**Then** 它不会被标记为 current SoR。
3. **Given** 某条 derived result 形成了候选事实，**When** 查看 evidence chain，**Then** 系统会同时返回 `proposal_ref` 与其来源 refs。

---

### User Story 4 - compaction / consolidation / flush 是可审计执行链，而不是静默黑盒 (Priority: P1)

作为系统 owner，我希望在 context compaction、memory consolidation 或 flush 发生时，系统能产生结构化 run/status/audit，而不是悄悄修改记忆层。

**Why this priority**: 长期记忆和工作上下文的交界处最容易出现不可解释副作用。

**Independent Test**: 触发一次 maintenance 命令，验证系统生成 `MemoryMaintenanceRun`，记录 backend_used、输出 refs、错误摘要，并且 `before_compaction_flush()` 仍只产生 fragment/proposal 草案。

**Acceptance Scenarios**:

1. **Given** session 接近 compaction threshold，**When** 触发 flush，**Then** 系统生成 maintenance run 和 fragment/proposal 草案，而不是直接改 SoR。
2. **Given** 需要 reindex / replay 积压同步，**When** 触发 maintenance action，**Then** 系统返回结构化 run 状态和 backlog 变化。
3. **Given** maintenance 失败，**When** 027 或 026 查询 diagnostics，**Then** 能看到 failure_code、last_failure_at 和 retry/fallback 状态。

---

### User Story 5 - MemU 故障时自动降级，恢复后自动回切，并且状态对 027/026 可见 (Priority: P1)

作为系统 owner，我希望 MemU down 掉时系统仍然能提供最小可用 Memory 能力，并在恢复后自动回切到 primary backend。

**Why this priority**: Constitution 6 要求 graceful degradation；028 如果没有 failback，就只是“高配外挂”。

**Independent Test**: 模拟 bridge failure，验证 `search_memory()` 自动回退到 SQLite fallback；恢复后经过健康探测自动回切 MemU，并在 diagnostics projection 中体现状态变化。

**Acceptance Scenarios**:

1. **Given** MemU bridge 暂时不可用，**When** 调用 `search_memory()`，**Then** 系统自动走 SQLite fallback，不抛出全局不可用错误。
2. **Given** backend 进入 degraded/unavailable，**When** 读取 `MemoryBackendDiagnosticsProjection`，**Then** 可以看到结构化原因和 backlog。
3. **Given** MemU 恢复健康，**When** 下次健康探测成功，**Then** 系统自动回切 primary backend，并更新 diagnostics 状态。

## Edge Cases

- 某个 derived result 同时引用多个 fragment / artifact，且其中一部分后续被删除或 superseded 时，evidence chain 应如何保留？
- 同一 `subject_key` 在 MemU derived 层与 SoR current 冲突时，027 应如何同时展示“候选结论”和“当前权威事实”？
- 多模态 ingest 中，音频转写或图片抽取失败但 artifact 已落盘时，系统如何保持部分成功与幂等重试？
- 敏感分区命中的 advanced search / derived results 在 `allow_vault=False` 时如何只暴露安全摘要，而不泄漏 raw payload？
- backend 处于 `recovering` 时，search 是否全部回切 SQLite，还是允许受控 probe traffic？
- replay/reindex 积压很大时，026 control plane 应如何只展示 diagnostics/action hook，而不是误当成 detailed memory console？
- 如果 025-B 尚未配置 MemU bridge secrets，028 应如何把状态表达为 `not_configured` / `degraded`，而不是“backend 崩了”？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 保持 Feature 020 冻结的治理边界：`MemoryService`、`WriteProposal`、SoR / Vault / Fragments 规则仍为 canonical fact source。
- **FR-002**: `MemUBackend` / `MemUBridge` MUST NOT 直接创建、覆盖或删除 SoR / Vault 权威记录；任何事实变更 MUST 通过 `WriteProposal -> validate -> commit`。
- **FR-003**: 系统 MUST 将 `MemUBackend` 从薄 adapter 扩展为高级 engine contract，至少覆盖检索、索引、增量同步、健康诊断、多模态 ingest、派生层查询和 maintenance。
- **FR-004**: 扩展后的 backend contract MUST 暴露结构化 `MemoryBackendStatus`，至少包含 `healthy | degraded | unavailable | recovering`、`failure_code`、`last_success_at`、`last_failure_at`、`retry_after`、`sync_backlog`、`pending_replay_count`。
- **FR-005**: `search_memory()` MUST 在 backend healthy 时优先使用 MemU；当 MemU 不可用或不健康时 MUST 自动降级到 SQLite fallback。
- **FR-006**: 系统 MUST 支持自动 failback；当 MemU 恢复健康后，search/index/sync MUST 能回切 primary backend，而不是永久停留在 degraded 模式。
- **FR-007**: backend 同步 MUST 支持批量、幂等和 replay，至少覆盖 `FragmentRecord`、SoR 安全摘要、Vault skeleton 摘要和 tombstone/update replay。
- **FR-008**: 系统 MUST 提供多模态 ingest 管线，至少支持 `text | image | audio | document` 四类输入；对于非文本输入，系统 MUST 允许通过 extractor/sidecar text 或结构化 metadata handoff 进入 memory engine。
- **FR-009**: 多模态 ingest 的 canonical output MUST 是 `artifact refs`、`fragment refs`、`derived refs` 和可选 `WriteProposalDrafts`；它 MUST NOT 直接写 SoR。
- **FR-010**: 所有 ingest / derived / maintenance 输出 MUST 携带 `artifact_refs` 或 `fragment_refs` 组成的 evidence chain。
- **FR-011**: 系统 MUST 实现 Category / relation / entity / ToM 等高级派生层，并把它们建模为 derived layer，而不是权威事实层。
- **FR-012**: derived layer 如推断出长期事实候选，MUST 返回 `WriteProposalDraft`，包含 `subject_key`、`confidence`、`rationale` 和 `evidence_refs`。
- **FR-013**: derived layer query MUST 提供 `derived_type`、`summary`、`payload`、`confidence`、`source_fragment_refs`、`source_artifact_refs` 和可选 `proposal_ref`。
- **FR-014**: 系统 MUST 以兼容扩展方式补齐 Feature 027 可消费的 query/projection/integration contract，至少包含 `MemoryQueryRequest`、`MemoryQueryProjection`、`MemoryEvidenceProjection`、`MemoryDerivedProjection` 和 `MemoryBackendDiagnosticsProjection`。
- **FR-015**: Feature 027 MUST 继续以其已交付的 canonical memory resources 为产品事实面；028 只能通过兼容新增字段、integration hooks 和 backend-facing DTO 扩展能力，不得重定义 027 的 canonical DTO。
- **FR-016**: `MemoryQueryProjection` MUST 明确返回 `backend_used`、`backend_state`、`degraded_reason`、`evidence_refs`、`derived_refs` 和分页/游标信息。
- **FR-017**: `MemoryEvidenceProjection` MUST 能展开 fragment、artifact、proposal、maintenance run 和 derived refs，供 027 做可信解释。
- **FR-018**: 028 MUST 不实现 Memory Console 详细 UI、Vault 授权 UI 或 proposal audit UI；这些仍属于 Feature 027。
- **FR-019**: 系统 MUST 提供 consolidation / compaction / flush 的可审计执行链，至少包含 `MemoryMaintenanceCommand` 与 `MemoryMaintenanceRun`。
- **FR-020**: `before_compaction_flush()` MUST 继续保持“只生成 fragment/proposal 草案，不直接改 SoR”的语义；028 不得破坏该约束。
- **FR-021**: maintenance 执行 MUST 记录 `backend_used`、`status`、输出 refs、错误摘要和时间戳，并可被 diagnostics / 027 projection 消费。
- **FR-022**: 028 MUST 向 Feature 026 提供 control-plane integration hooks，但 MUST NOT 新增 control-plane canonical memory resource。
- **FR-023**: Feature 026 可消费的 hooks MUST 至少包含 memory diagnostics contribution 和可选 maintenance action definitions。
- **FR-024**: 推荐的 maintenance action ids SHOULD 至少覆盖 `memory.flush`、`memory.reindex`、`memory.bridge.reconnect`、`memory.sync.resume`。
- **FR-025**: 028 MUST 与 Feature 025-B 的 project/workspace/secret store 路径对齐，MemU bridge 配置 MUST 绑定到 project/workspace，而不是全局裸配置。
- **FR-026**: MemU bridge 配置与凭据 MUST 使用 secret refs / bridge refs，不得把 secret 实值写入 YAML、projection、diagnostics 或前端缓存。
- **FR-027**: 敏感分区在 advanced search / derived projection 中 MUST 继续遵守 Vault default deny；`allow_vault=False` 时不得泄漏 raw sensitive payload。
- **FR-028**: MemU unavailable、bridge timeout、index backlog、ingest partial failure、maintenance conflict 等错误 MUST 使用结构化错误码和诊断信息表达。
- **FR-029**: 系统 MUST 补齐 backend 协议测试、fallback/failback 测试、multimodal ingest 测试、derived layer 测试、maintenance/audit 测试和 027/026 integration contract 测试。
- **FR-030**: 本 Feature MUST 不重做 Memory Console detailed UI、session center UI、scheduler 面板或 runtime console 页面。

### Key Entities

- **MemoryBackendStatus**: backend 健康、降级与回切状态对象。
- **MemorySyncBatch / MemorySyncResult**: backend 批量同步与 replay 对象。
- **MemoryIngestBatch / MemoryIngestResult**: 多模态 ingest 请求与结果对象。
- **DerivedMemoryRecord**: Category / relation / entity / ToM 派生层记录。
- **WriteProposalDraft**: 由 derived/maintenance 产出的候选事实草案。
- **MemoryQueryRequest / MemoryQueryProjection**: 027 的 canonical 查询输入/输出。
- **MemoryEvidenceProjection**: 命中结果的证据链投影。
- **MemoryDerivedProjection**: 派生层聚合投影。
- **MemoryBackendDiagnosticsProjection**: 027 / 026 共享的 backend 诊断对象。
- **MemoryMaintenanceCommand / MemoryMaintenanceRun**: consolidation / compaction / flush / replay / reindex 的可审计执行链对象。

## Success Criteria

### Measurable Outcomes

- **SC-001**: `MemUBackend` / `MemUBridge` contract 被扩展为可支持 retrieval、indexing、sync、ingest、derived layers、maintenance 和 diagnostics 的高级 engine contract。
- **SC-002**: MemU healthy 时，`search_memory()` 可作为主检索 backend 工作；MemU unavailable 时，系统自动降级到 SQLite fallback，并在恢复后自动回切。
- **SC-003**: 文本 / 图片 / 音频 / 文档四类输入都能进入统一 ingest 管线，输出 artifact/fragment/derived/proposal evidence chain。
- **SC-004**: Category / relation / entity / ToM 派生层可被查询，并且任何可能影响权威事实的结果都通过 `WriteProposalDraft` 表达。
- **SC-005**: consolidation / compaction / flush / replay / reindex 至少有一套统一 `MemoryMaintenanceRun` 审计模型。
- **SC-006**: Feature 027 可以在不破坏既有 canonical resource 的前提下，直接消费 028 补齐的 query/projection/integration contract，而无需另起一套 memory DTO。
- **SC-007**: Feature 026 可以通过 diagnostics / action hooks 感知 memory engine 健康与 maintenance，但不需要引入新的 control-plane memory resource。
- **SC-008**: backend、integration 与降级路径测试矩阵完整覆盖关键场景并通过。

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | MemU 是否可以接管 SoR / Vault 事实写入？ | 否 | blueprint 与 020 已冻结 governance plane，本 Feature 只能扩展 engine plane |
| 2 | 028 是否实现 Memory Console / Vault 详细 UI？ | 否 | 用户明确要求只提供 027 可消费的 API、projection 和 integration hooks |
| 3 | 028 是否允许高级结果直接变成权威事实？ | 否 | 高级结果只能落为 `Fragments`、派生索引或 `WriteProposal` |
| 4 | 026 control plane 是否需要新增 memory canonical resource？ | 否 | 026 当前只消费 diagnostics / actions hook，详细 memory 浏览留给 027 |
| 5 | MemU unavailable 时是否必须自动 fallback / failback？ | 是 | Constitution 6 与 Feature 028 验收标准都要求 graceful degradation |
| 6 | MemU bridge 配置是否必须绑定 project/workspace 和 secret refs？ | 是 | 025-B 已提供上游能力，避免全局裸配置与 secret 泄漏 |

## Scope Boundaries

### In Scope

- 扩展 `MemoryBackend` / `MemUBridge` 为高级 engine contract
- retrieval / indexing / incremental sync / replay / health diagnostics
- 多模态 ingest：text / image / audio / document
- Category / relation / entity / ToM 派生层
- consolidation / compaction / flush 的可审计执行链
- 027 可消费的 query/projection/integration contract
- 026 可消费的 diagnostics / action hooks
- 025-B project/workspace/secret binding 对齐
- backend / integration / fallback / maintenance 测试要求

### Out of Scope

- Memory Console 详细 UI
- Vault 授权面板
- proposal audit UI
- 直接旁路 SoR / Vault 的事实写入
- 重新设计 020 Memory Core 治理契约
- 重新设计 026 control plane canonical resources
- scheduler / runtime console / session center 详细页面实现

## Risks & Design Notes

- 当前最大的设计风险不是“MemU 能不能接”，而是“接进来之后是否破坏治理边界”。因此 028 必须先把 `derived != fact`、`proposal required`、`evidence required` 写死。
- 025-B 目前还没正式覆盖 MemU bridge secret target；因此 028 设计必须明确 project-scoped bridge binding，但不在本阶段重做 wizard/secret UI。
- 026 当前没有 memory canonical resource；028 若想让控制台感知 memory，只能通过 diagnostics contribution 和 maintenance actions，而不能偷渡 detailed memory API。
- 027 若没有提前冻结 consumption contract，后续 UI 大概率会绕开 `search_memory()` / `get_memory()` 自造 DTO；因此本设计把 query/projection/integration contract 视为硬交付。
