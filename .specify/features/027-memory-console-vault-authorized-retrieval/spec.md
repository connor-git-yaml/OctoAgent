---
feature_id: "027"
title: "Memory Console + Vault Authorized Retrieval"
milestone: "M3"
status: "Implemented"
created: "2026-03-08"
updated: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/m3-feature-split.md Feature 027；docs/blueprint.md M3 产品化约束；.specify/features/020-memory-core/spec.md；.specify/features/025-project-workspace-migration/spec.md；.specify/features/026-control-plane-contract/spec.md"
predecessor: "Feature 020（Memory Core，已交付） / Feature 025-B（Project + Secret + Wizard，已交付） / Feature 026（Control Plane，已交付）"
parallel_dependency: "Feature 028 只消费本 Feature 交付的 Memory/Vault control-plane contract 与授权记录，不得反向重定义 027 语义"
---

# Feature Specification: Memory Console + Vault Authorized Retrieval

**Feature Branch**: `codex/feat-027-memory-console-vault`  
**Created**: 2026-03-08  
**Status**: Implemented  
**Input**: 落实 M3 Feature 027：Memory Console + Vault Authorized Retrieval。范围包含 Memory Console query/projection contract、按 `project/workspace/partition/scope/layer` 浏览 SoR/Fragments/Vault 引用、`subject_key` 历史与 evidence refs、Vault 授权申请与授权检索证据链、WriteProposal 审计视图、memory 权限模型、Memory export/inspect/restore 校验入口，并把详细 Memory/Vault 领域视图接到现有 control plane。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

Feature 020 已经交付可治理的 Memory Core，Feature 025-B 已经把 project/workspace/secret/wizard 变成正式主路径，Feature 026 已经交付统一 control plane；但对 operator 来说，Memory 仍缺少正式产品面：

1. 020 只有 `SoR / Fragments / Vault / WriteProposal` 内核与 service/store contract，没有 operator-facing 的浏览、审计、授权和校验视图。
2. 026 的 Control Plane 目前没有 Memory canonical resource，Memory/Vault 仍只是规划中的入口位。
3. Vault 现在只有 default deny skeleton，没有“申请授权 -> 决议记录 -> 检索执行 -> 证据链”的正式闭环。
4. 用户无法从 project/workspace 视角理解某条记忆属于谁、为什么是 current、为何被 superseded、凭什么可以或不可以查看敏感内容。
5. `WriteProposal` 目前更多停留在底层治理表；operator 还看不到 proposal 来源、validate 结果、commit 状态和关联 evidence。

因此，027 的目标不是做“记忆搜索功能”，而是把 Memory 从系统内部能力提升为可理解、可授权、可审计、可校验的产品对象，同时不破坏 020 已冻结的治理边界。

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 020：Memory Core + WriteProposal + Vault Skeleton | 已交付 | 继续作为唯一权威治理内核，027 不得旁路写 SoR/Vault |
| Feature 025-B：Secret Store + Unified Config Wizard | 已交付 | 027 必须复用 `project/workspace` 与 active project 语义 |
| Feature 026：Control Plane | 已交付 | 027 必须接入现有 canonical resources / actions / events 与 Web 控制台 |

前置约束：

- 027 **必须** 复用 `packages/memory` 现有 `WriteProposal -> validate -> commit_memory()` 写入闭环，不得直接写权威事实表。
- 027 **必须** 复用现有 control-plane backend 与 Web 控制台，不得新造第二套 console framework 或平行 canonical API。
- 027 **必须** 默认保护 Vault 敏感内容；未经授权的资源视图不得暴露原文。
- 027 **不得** 把 MemU 深度引擎、高级 recall、多模态 ingest、consolidation pipeline 偷带进来；只能预留 integration points。

## Scope Boundaries

### In Scope

- Memory Console query/projection contract
- 按 `project / workspace / partition / scope / layer` 浏览 SoR、Fragments、Vault 引用
- `subject_key` 的 current / superseded 历史与 evidence refs
- Vault 授权申请、授权记录、授权检索结果与证据链
- WriteProposal 审计视图
- memory 相关权限模型
- Memory export / inspect / restore 校验入口
- 将 detailed Memory/Vault 领域视图接入现有 control plane

### Out of Scope

- MemU 深度集成与高级 recall
- 多模态 ingest、Category、ToM、consolidation pipeline
- 旁路写 SoR/Vault 的“直接编辑记忆”能力
- 重做 control plane 框架或单独新建 memory 管理台
- 将 Vault 敏感原文默认暴露给 Web/Telegram/CLI

## User Scenarios & Testing

### User Story 1 - 我可以按 project/workspace 读懂系统当前记忆状态 (Priority: P1)

作为 operator，我希望在 Control Plane 中按 project/workspace/partition/scope/layer 浏览 SoR、Fragments 和 Vault 引用，并查看某个 `subject_key` 的 current 与 superseded 历史，这样我能知道系统当前记住了什么、这些结论来自哪里。

**Why this priority**: 这是把 Memory 从“内部能力”提升为“可理解产品对象”的最小闭环；没有这层浏览和历史解释，后续 Vault 授权与 proposal 审计都缺少基础上下文。

**Independent Test**: 准备包含多个 project/scope/partition 的 Memory 数据，打开 Control Plane Memory 视图，验证可以筛选、查看 SoR current/superseded 历史、显示 evidence refs，并明确标记 Vault 引用与 redacted 状态。

**Acceptance Scenarios**:

1. **Given** 当前 active project 下存在 SoR、Fragments 与 Vault skeleton，**When** operator 打开 Memory Console，**Then** 系统按 `project/workspace/partition/scope/layer` 提供可筛选的正式视图，而不是底层表快照。
2. **Given** 某个 `subject_key` 有 1 条 current 与多条 superseded 记录，**When** operator 查看该 subject，**Then** 系统能展示 current/superseded 历史、version、evidence refs 与更新时间。
3. **Given** 某条记录属于 Vault 或敏感分区，**When** 未授权 operator 浏览列表，**Then** 只返回 redacted 摘要与授权状态，不暴露原文。

---

### User Story 2 - 我可以通过正式授权链检索 Vault 内容 (Priority: P1)

作为需要查看敏感记忆的操作者，我希望能在控制台发起 Vault 授权申请、看到授权决议和生效范围，并在授权后执行带审计的检索，这样系统既能保护隐私，也不会让我在排障时完全失明。

**Why this priority**: Vault default deny 是 020 的硬约束，但没有正式授权链，Vault 就无法成为可用的产品能力。

**Independent Test**: 在含敏感 Vault 记录的项目上，发起一次授权申请并完成批准，然后执行检索；验证系统产生授权记录、检索执行记录和证据链，并只在授权生效时返回允许的明细内容。

**Acceptance Scenarios**:

1. **Given** operator 尚未获得某个 Vault 范围的访问授权，**When** 发起检索，**Then** 系统返回 `authorization_required` 结果，并给出可追踪的授权申请记录。
2. **Given** 授权申请已被批准且仍在有效期内，**When** operator 执行 Vault 检索，**Then** 系统返回授权范围内的结果，并记录 request/decision/result/evidence 的审计链。
3. **Given** 授权已过期或范围不匹配，**When** operator 再次检索，**Then** 系统拒绝访问并明确说明是 `expired`、`scope_mismatch` 或 `not_granted`。

---

### User Story 3 - 我可以审计每条 WriteProposal 是如何变成事实的 (Priority: P1)

作为 operator，我希望在控制台查看 WriteProposal 的来源、验证结果、commit 状态、关联 fragment/sor/vault 结果和 evidence refs，这样我能解释“为什么系统最终记住了这件事”。

**Why this priority**: Memory 可信度来自治理链，而不是检索 UI；没有 proposal 审计，用户仍无法判断 current SoR 背后的推理与证据。

**Independent Test**: 构造通过与拒绝的 proposal 数据，打开 proposal 审计视图；验证系统能正确区分 validate rejected / validated / committed 状态，并关联 evidence refs 与最终落盘对象。

**Acceptance Scenarios**:

1. **Given** 某条 proposal 因缺失 evidence 被拒绝，**When** operator 查看 proposal 审计，**Then** 系统明确展示拒绝原因且不声称已写入 SoR/Vault。
2. **Given** 某条 proposal 已提交并产生 fragment + sor + vault skeleton，**When** operator 查看 proposal 审计，**Then** 系统展示 validate/commit 状态、关联 record refs 与 evidence refs。
3. **Given** proposal 来自 import、worker 或 compaction flush，**When** operator 查看来源信息，**Then** 系统能明确区分 proposal source 与相关 project/scope。

---

### User Story 4 - 我可以在 Control Plane 中校验 Memory export/restore 风险 (Priority: P2)

作为 operator，我希望通过正式入口检查 Memory export、inspect 与 restore 的影响和一致性，而不是直接对数据库做盲操作，这样我能在恢复或迁移前先验证风险。

**Why this priority**: 这让 Memory 管理进入完整的 operator 生命周期，也为后续恢复/迁移场景提供统一入口。

**Independent Test**: 对一个包含 SoR/Vault/Proposal 历史的项目执行 export inspect 与 restore verify，验证系统输出一致性检查、权限提示、受影响对象摘要与 blocking issues，而不直接执行权威写入。

**Acceptance Scenarios**:

1. **Given** 当前项目存在可导出的 Memory 数据，**When** operator 执行 export inspect，**Then** 系统返回导出范围、分区摘要、敏感数据提示和校验结论。
2. **Given** operator 提供一个 restore payload 或 snapshot，**When** 执行 restore verify，**Then** 系统返回 schema/subject/version/authorization 风险检查结果，而不是直接写入事实表。
3. **Given** restore verify 检测到会破坏 current 唯一约束或 Vault 授权边界，**When** operator 查看结果，**Then** 系统明确阻断并给出 remediation 建议。

---

### Edge Cases

- 当某个 `scope_id` 无法映射到当前 project/workspace 时，Memory Console 应如何表达 `orphan_scope` / `unbound_scope`？
- 当 `subject_key` 历史包含已损坏或缺失的 evidence refs 时，系统如何在不隐藏问题的前提下保持浏览可用？
- 当 Vault 授权申请已批准，但检索请求超出授权分区/subject/scope 时，系统如何部分放行或完全拒绝？
- 当 proposal 已 validate 但 commit 失败，审计视图如何区分“被拒绝”和“验证通过但未提交”？
- 当 export/restore 检查面对包含 superseded 历史与 current 冲突的快照时，系统如何展示会覆盖或冲突的对象而不直接执行恢复？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 在现有 control plane 中新增 Memory 相关 canonical resources / actions / events，而 MUST NOT 新造平行控制台框架或绕开 `/api/control/*`。
- **FR-002**: 系统 MUST 复用 Feature 020 的 `WriteProposal -> validate_proposal() -> commit_memory()` 治理内核；任何权威事实写入 MUST NOT 旁路该路径。
- **FR-003**: 系统 MUST 提供 Memory Console query/projection contract，用于按 `project_id / workspace_id / partition / scope_id / layer` 浏览 SoR、Fragments、Vault 引用与 Proposal 摘要。
- **FR-004**: Memory Console MUST 能从 025-B 已交付的 project/workspace 语义派生过滤条件；当底层 `scope_id` 无法解析到 project/workspace 时，系统 MUST 明示 `orphan_scope` 或等价 degraded 状态。
- **FR-005**: 系统 MUST 提供 `subject_key` 历史视图，展示 current / superseded 记录、version、created/updated 时间、关联 evidence refs 与最新 proposal refs。
- **FR-006**: 系统 MUST 在未授权时对 Vault 记录返回 redacted 视图；canonical resource MUST NOT 暴露 Vault 敏感原文。
- **FR-007**: 系统 MUST 提供 Vault 授权申请能力，并持久化申请记录、目标范围、申请 actor、理由、状态与时间戳。
- **FR-008**: 系统 MUST 提供 Vault 授权决议记录，表达批准/拒绝、授权 actor、生效范围、时效、撤销/过期状态与审计 refs。
- **FR-009**: 系统 MUST 提供 Vault 检索结果记录，至少包含 request、命中对象摘要、是否 redacted、使用的授权记录、evidence chain 与结果码。
- **FR-010**: Vault 检索 action MUST 默认 fail-closed；当授权不存在、过期、范围不匹配或 actor 不被允许时，系统 MUST 返回明确的拒绝结果码与下一步建议。
- **FR-011**: 系统 MUST 提供 WriteProposal 审计视图，展示 proposal source、action、validation status、validation errors、commit status、关联 fragment/sor/vault refs 与 evidence refs。
- **FR-012**: WriteProposal 审计视图 MUST 明确区分 `pending`、`validated`、`rejected`、`committed` 以及“验证通过但提交失败/未提交”的状态。
- **FR-013**: 系统 MUST 定义 memory 相关权限模型，至少覆盖：查看 SoR 摘要、查看 superseded/history、申请 Vault、批准 Vault、执行 Vault retrieve、执行 export inspect、执行 restore verify。
- **FR-014**: memory 权限模型 MUST 保持 surface-agnostic，并可被 Web/Telegram/CLI 共用语义消费；Telegram/CLI 只能作为 alias/consumer，不得定义独立授权语义。
- **FR-015**: 系统 MUST 提供 Memory export inspect 入口，用于输出导出范围、对象摘要、敏感分区提示、一致性与权限检查结果。
- **FR-016**: 系统 MUST 提供 Memory restore verify 入口，用于校验 schema、subject history、current 唯一约束、Vault 权限边界与潜在冲突；该入口 MUST NOT 直接执行权威恢复写入。
- **FR-017**: 所有 Vault 授权、Vault 检索、proposal 审计读取、export inspect、restore verify 动作 MUST 进入 control-plane audit/event 链，并满足 `Durability First` 与 `Everything is an Event`。
- **FR-018**: Memory/Vault resource documents MUST 提供 `degraded / warnings / capabilities / refs`，并在 backend 不可用、evidence 缺失或 scope 解析失败时保持可降级浏览。
- **FR-019**: 系统 MUST 为 Feature 028 预留 integration points（如 `backend_id`、`retrieval_backend`、`index_health`、advanced refs），但 MUST NOT 在 027 中引入 MemU 深度引擎语义或自动事实写入。
- **FR-020**: Web Control Plane MUST 提供正式 Memory 领域视图，并继续只消费 canonical control-plane resources / actions / events，不得前端自行拼接 memory 表结构。
- **FR-021**: 027 MUST 补齐 memory canonical resource 测试、Vault 授权与检索集成测试、proposal audit 测试、control-plane API 测试、Web integration 测试与关键 e2e 测试。
- **FR-022**: 027 MUST 明确与 Feature 028 的边界：028 只能复用 027 暴露的 query/projection/authorization/integration points，而不得反向重定义 Memory Console、Vault 授权或 proposal 审计语义。

### Key Entities

- **MemoryConsoleDocument**: 面向 Control Plane 的 Memory 总览文档，表达 filters、summary、records、warnings、capabilities 与 degraded 状态。
- **MemoryRecordProjection**: SoR / Fragment / Vault 引用的统一摘要对象，包含 `project/workspace/partition/scope/layer`、subject、version、redaction 与 evidence refs。
- **MemorySubjectHistoryDocument**: 针对单个 `subject_key` 的 current / superseded 历史、版本信息、evidence refs 与相关 proposal refs。
- **VaultAccessRequest**: Vault 授权申请记录，描述 actor、目标范围、理由、状态、时间戳与 refs。
- **VaultAccessGrant**: Vault 授权决议/生效记录，描述 grant scope、decision、effective window、revocation/expiration 与审计信息。
- **VaultRetrievalAudit**: 单次 Vault 检索执行记录，描述请求参数、命中摘要、是否 redacted、使用的 grant 与 evidence chain。
- **WriteProposalAuditDocument**: 面向 operator 的 proposal 审计投影，聚合 proposal、validate、commit 与落盘对象 refs。
- **MemoryPermissionDecision**: memory 相关动作的权限判断摘要，表达 actor、action、scope、decision 与阻断原因。
- **MemoryExportInspection**: export inspect 的结果对象，表达导出范围、对象计数、敏感分区提示与一致性结论。
- **MemoryRestoreVerification**: restore verify 的结果对象，表达冲突、阻断项、风险提示与 remediation 建议。

## Success Criteria

### Measurable Outcomes

- **SC-001**: Control Plane 中存在正式 Memory 资源与页面，用户可以按 `project/workspace/partition/scope/layer` 浏览 Memory，而不需要直接访问底层数据库或日志。
- **SC-002**: 对任意存在 current/superseded 历史的 `subject_key`，系统都能在 UI/API 中正确显示 current 版本、历史版本和 evidence refs。
- **SC-003**: Vault 默认仍不可检索；未授权时详细原文不暴露，授权后检索会留下完整 request/decision/result 审计链。
- **SC-004**: operator 能在 proposal 审计视图中解释至少一条 accepted/committed proposal 与一条 rejected proposal 的来源、验证和落盘结果。
- **SC-005**: export inspect / restore verify 能在不直接写入事实表的前提下输出阻断项、风险提示和一致性检查结论。
- **SC-006**: 027 的单元、API、integration、Web integration 与关键 e2e 测试通过，且不会破坏 020 已有的 current 唯一约束与 Vault default deny 行为。
