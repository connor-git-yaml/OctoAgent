# Data Model: Feature 027 — Memory Console + Vault Authorized Retrieval

## 1. Shared Control-Plane Documents

### 1.1 `MemoryConsoleDocument`

**Purpose**: Memory 总览 canonical resource，供 Control Plane 和 Web 消费。

**Fields**:

- `resource_type = "memory_console"`
- `resource_id = "memory:overview"`
- `active_project_id: str`
- `active_workspace_id: str`
- `filters: MemoryQueryFilter`
- `summary: MemoryConsoleSummary`
- `records: list[MemoryRecordProjection]`
- `subject_refs: list[str]`
- `proposal_refs: list[str]`
- `authorization_refs: list[str]`
- `capabilities`
- `warnings`
- `degraded`
- `refs`

### 1.2 `MemorySubjectHistoryDocument`

**Purpose**: 针对单个 `subject_key` 的 current / superseded 历史视图。

**Fields**:

- `resource_type = "memory_subject_history"`
- `resource_id = "memory-subject:{subject_key}"`
- `subject_key: str`
- `scope_id: str`
- `project_id: str | None`
- `workspace_id: str | None`
- `current_record: MemoryRecordProjection | None`
- `history: list[MemoryRecordProjection]`
- `latest_proposal_refs: list[str]`
- `warnings`
- `degraded`

### 1.3 `WriteProposalAuditDocument`

**Purpose**: 面向 operator 的 proposal 审计视图。

**Fields**:

- `resource_type = "memory_proposal_audit"`
- `resource_id = "memory-proposals:overview"`
- `filters: MemoryQueryFilter`
- `items: list[WriteProposalAuditItem]`
- `summary: dict[str, int | str]`
- `warnings`
- `degraded`

### 1.4 `VaultAuthorizationDocument`

**Purpose**: Vault 授权与检索状态视图。

**Fields**:

- `resource_type = "vault_authorization"`
- `resource_id = "vault:authorization"`
- `active_requests: list[VaultAccessRequest]`
- `active_grants: list[VaultAccessGrant]`
- `recent_retrievals: list[VaultRetrievalAudit]`
- `warnings`
- `degraded`

## 2. Query / Projection Objects

### 2.1 `MemoryQueryFilter`

**Purpose**: 统一 query/projection 过滤条件。

**Fields**:

- `project_id: str | None`
- `workspace_id: str | None`
- `scope_id: str | None`
- `partition: MemoryPartition | None`
- `layer: MemoryLayer | None`
- `query: str | None`
- `include_history: bool`
- `include_vault_refs: bool`
- `limit: int`
- `cursor: str | None`

**Constraints**:

- `limit` 默认小分页，避免把大体量 Memory 原样塞进 snapshot
- `include_vault_refs=true` 也只影响是否返回 Vault skeleton/ref，不代表允许看原文

### 2.2 `MemoryConsoleSummary`

**Purpose**: 总览统计。

**Fields**:

- `sor_current_count: int`
- `sor_history_count: int`
- `fragment_count: int`
- `vault_ref_count: int`
- `proposal_pending_count: int`
- `proposal_rejected_count: int`
- `unresolved_scope_count: int`
- `authorization_pending_count: int`

### 2.3 `MemoryRecordProjection`

**Purpose**: SoR / Fragment / Vault skeleton 的统一摘要对象。

**Fields**:

- `record_id: str`
- `layer: MemoryLayer`
- `scope_id: str`
- `project_id: str | None`
- `workspace_id: str | None`
- `partition: MemoryPartition`
- `subject_key: str | None`
- `summary: str`
- `version: int | None`
- `status: str | None`
- `is_sensitive: bool`
- `is_redacted: bool`
- `evidence_refs: list[EvidenceRef]`
- `metadata: dict[str, Any]`
- `created_at: datetime`
- `updated_at: datetime | None`

**Constraints**:

- `summary` 必须是 UI-safe 内容
- 未授权 Vault 记录 `is_redacted=true`
- `project_id/workspace_id` 来自 scope bridge，不修改底层权威记录

### 2.4 `ScopeOwnershipProjection`

**Purpose**: 把底层 `scope_id` 桥接到 project/workspace/operator 心智。

**Fields**:

- `scope_id: str`
- `project_id: str | None`
- `workspace_id: str | None`
- `binding_type: ProjectBindingType | None`
- `binding_key: str | None`
- `resolved: bool`
- `warnings: list[str]`

**Constraints**:

- 不解析失败时必须显式标记 `resolved=false`
- 不允许 UI 侧自行猜测 project/workspace

## 3. Vault Authorization Domain

### 3.1 `VaultAccessRequest`

**Purpose**: 一次敏感内容访问申请。

**Fields**:

- `request_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `scope_id: str | None`
- `partition: MemoryPartition | None`
- `subject_key: str | None`
- `requester_actor_id: str`
- `requester_actor_label: str`
- `reason: str`
- `status: VaultAccessRequestStatus`
- `created_at: datetime`
- `resolved_at: datetime | None`
- `grant_id: str | None`
- `metadata: dict[str, Any]`

### 3.2 `VaultAccessGrant`

**Purpose**: 一次授权决议及其有效期。

**Fields**:

- `grant_id: str`
- `request_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `scope_id: str | None`
- `partition: MemoryPartition | None`
- `subject_key: str | None`
- `decision: VaultAccessDecision`
- `granted_to_actor_id: str`
- `granted_by_actor_id: str`
- `expires_at: datetime | None`
- `revoked_at: datetime | None`
- `created_at: datetime`
- `metadata: dict[str, Any]`

**Constraints**:

- `decision=approved` 时才可被检索消费
- grant 必须可过期或撤销
- grant 范围不得比 request 更宽

### 3.3 `VaultRetrievalAudit`

**Purpose**: 一次 Vault 检索执行审计。

**Fields**:

- `retrieval_id: str`
- `request_id: str`
- `grant_id: str | None`
- `actor_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `scope_id: str | None`
- `partition: MemoryPartition | None`
- `subject_key: str | None`
- `query: str | None`
- `result_code: str`
- `result_count: int`
- `redacted: bool`
- `evidence_refs: list[EvidenceRef]`
- `created_at: datetime`
- `metadata: dict[str, Any]`

**Constraints**:

- 未授权时也要留下 audit，`grant_id` 可为空
- `evidence_refs` 只保存安全引用，不保存敏感原文

## 4. Proposal Audit Domain

### 4.1 `WriteProposalAuditItem`

**Purpose**: proposal 的 operator-facing 聚合视图。

**Fields**:

- `proposal_id: str`
- `project_id: str | None`
- `workspace_id: str | None`
- `scope_id: str`
- `partition: MemoryPartition`
- `action: WriteAction`
- `subject_key: str | None`
- `source: str`
- `status: ProposalStatus`
- `validation_errors: list[str]`
- `expected_version: int | None`
- `fragment_id: str | None`
- `sor_id: str | None`
- `vault_id: str | None`
- `evidence_refs: list[EvidenceRef]`
- `created_at: datetime`
- `validated_at: datetime | None`
- `committed_at: datetime | None`

**Constraints**:

- `source` 来自 metadata / import / compaction / worker 等，必须安全可展示
- 必须能区分“validated 但未 committed”与 `rejected`

## 5. Permission Model

### 5.1 `MemoryPermissionAction`

- `memory.view_summary`
- `memory.view_history`
- `vault.access.request`
- `vault.access.resolve`
- `vault.retrieve`
- `memory.export.inspect`
- `memory.restore.verify`
- `memory.proposal.inspect`

### 5.2 `MemoryPermissionDecision`

**Fields**:

- `actor_id: str`
- `action: MemoryPermissionAction`
- `project_id: str | None`
- `workspace_id: str | None`
- `scope_id: str | None`
- `allowed: bool`
- `reason_code: str`
- `message: str`

**Constraint**:

- Vault 相关动作必须默认 deny，只有显式 grant 或 operator policy 允许时才放行

## 6. Export / Restore Inspection

### 6.1 `MemoryExportInspection`

**Purpose**: export inspect 的结构化结果。

**Fields**:

- `inspection_id: str`
- `project_id: str`
- `workspace_id: str | None`
- `scope_ids: list[str]`
- `counts: dict[str, int]`
- `sensitive_partitions: list[str]`
- `warnings: list[str]`
- `blocking_issues: list[str]`
- `generated_at: datetime`

### 6.2 `MemoryRestoreVerification`

**Purpose**: restore verify 的结构化结果。

**Fields**:

- `verification_id: str`
- `project_id: str`
- `snapshot_ref: str`
- `schema_ok: bool`
- `subject_conflicts: list[str]`
- `grant_conflicts: list[str]`
- `scope_conflicts: list[str]`
- `warnings: list[str]`
- `blocking_issues: list[str]`
- `generated_at: datetime`

**Constraints**:

- 只做 verify，不做 destructive restore apply
- 必须显式检查 current 唯一约束与 Vault 授权边界

## 7. Enums

- `VaultAccessRequestStatus = pending | approved | rejected | expired | revoked`
- `VaultAccessDecision = approved | rejected`
- `MemoryPermissionAction` 如上

## 8. Persistence Mapping

### Existing tables (source of truth)

- `memory_fragments`
- `memory_sor`
- `memory_write_proposals`
- `memory_vault`

### New tables (027)

- `memory_vault_access_requests`
- `memory_vault_access_grants`
- `memory_vault_retrieval_audits`

### Existing bridge source

- `project_bindings` with `binding_type in (scope, memory_scope, import_scope)`
- `project_selector_state`

### Audit / event source

- control-plane action events via existing event store / `ControlPlaneAuditPayload`

## 9. Invariants

1. 020 的 `scope_id + subject_key` 只允许一条 `status=current` 不变。
2. 未授权 Vault 原文不能出现在 canonical resources。
3. `MemoryRecordProjection.project_id/workspace_id` 只能来自 scope bridge，不能反向改写底层 memory 记录。
4. Vault 检索未授权时必须 fail-closed，但仍保留 retrieval audit。
5. export inspect / restore verify 不得直接执行权威写入。
6. 028 如需接入高级检索，只能通过 integration fields 扩展 projection，不得绕过上述不变量。
