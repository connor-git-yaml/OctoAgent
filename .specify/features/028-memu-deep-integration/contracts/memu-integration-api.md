# Contract: MemU Integration API

## 1. Contract Scope

本 contract 冻结 Feature 028 对外提供的 backend integration surface，供已交付的 Feature 027（Memory Console / Vault authorized retrieval）与 Feature 026（Control Plane diagnostics/actions hook）兼容消费。

硬规则：

- governance 仍由 `MemoryService` 控制
- `MemUBackend` 不能直接写 SoR / Vault
- 高级结果只能落为 `Fragments`、派生索引或 `WriteProposal`
- 所有高级结果必须带 evidence / artifact refs

## 2. Compatibility Rules

- `memory_engine_contract_version` 使用 SemVer
- 新增可选字段、可选派生层、可选 metrics 属于 minor-compatible
- 改变 query/result 语义、改变 evidence 结构、改变 maintenance 状态机属于 major-breaking
- 027 / 026 consumer 必须忽略未知可选字段

## 3. Extended Backend Protocol

```python
class MemoryBackend(Protocol):
    backend_id: str
    memory_engine_contract_version: str

    async def is_available(self) -> bool: ...
    async def get_status(self) -> MemoryBackendStatus: ...
    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]: ...
    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult: ...
    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult: ...
    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection: ...
    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection: ...
    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun: ...
```

说明：

- `search()` 仍是 020 canonical read gate 的高级 engine 路径
- `sync_batch()` 用于 fragment / SoR summary / vault skeleton / tombstone replay
- `ingest_batch()` 只产出 `artifact_refs` / ingest / derived / proposal 结果，不改 SoR
- `list_derivations()` / `resolve_evidence()` 供 027 做浏览/解释
- `run_maintenance()` 用于 consolidation / compaction / flush / replay / reindex / reconnect

## 4. MemU Bridge Contract

`MemUBridge` 是 `MemUBackend` 的 transport adapter，可由 HTTP client、local process bridge 或 plugin adapter 实现。

桥接层必须满足：

- 支持 project/workspace scoped binding
- 支持 secret refs，而不是明文 secrets
- 支持 idempotency key
- 支持结构化错误码
- 支持 backlog / retry_after / replay cursor 暴露

当前仓库内的推荐落点：

- `ProjectBindingType.MEMORY_BRIDGE` 负责 bridge endpoint / profile / timeout 等非敏感配置
- `SecretTargetKind.MEMORY` 负责 MemU bridge 的 API key / credential ref

最小错误类别：

- `UNAVAILABLE`
- `AUTH_FAILED`
- `TIMEOUT`
- `INDEX_OUT_OF_SYNC`
- `INGEST_REJECTED`
- `MAINTENANCE_CONFLICT`

## 5. Query / Projection Compatibility for Feature 027

### 5.1 Query Request

```python
class MemoryQueryRequest(BaseModel):
    scope_id: str | None
    partition: MemoryPartition | None
    layers: list[MemoryLayer]
    query: str | None
    subject_key: str | None
    include_derived: bool = False
    include_evidence: bool = False
    include_superseded: bool = False
    policy: MemoryAccessPolicy | None
    limit: int = 20
    cursor: str | None = None
```

### 5.2 Query Projection

`MemoryQueryProjection` 是 028 对 027 canonical list/search projection 的兼容扩展，必须至少补齐：

- `backend_used`: `memu | sqlite`
- `backend_state`: `healthy | degraded | unavailable | recovering`
- `items`: SoR / Fragment / Vault summary / derived hit 摘要
- `evidence_refs`
- `derived_refs`
- `degraded_reason`
- `next_cursor`

### 5.3 Evidence Projection

`MemoryEvidenceProjection` 必须展开：

- `fragment_refs`
- `artifact_refs`
- `proposal_refs`
- `maintenance_run_refs`
- `derived_refs`

027 使用它来展示“为什么命中 / 为什么形成这个派生结论”，而不是直接读取 raw backend payload。028 可以补充 evidence / diagnostics / derived 字段，但不得重定义 027 已冻结的产品对象语义。

### 5.4 Derived Projection

`MemoryDerivedProjection` 用于查询 Category / relation / entity / ToM 派生层，必须包含：

- `derived_type`
- `subject_key`
- `summary`
- `confidence`
- `source_fragment_refs`
- `source_artifact_refs`
- `proposal_ref`（若该派生结果产生了事实候选）

### 5.5 Diagnostics Projection

`MemoryBackendDiagnosticsProjection` 供 027 与 026 共享，必须至少包含：

- `active_backend`
- `state`
- `failure_code`
- `last_success_at`
- `last_failure_at`
- `retry_after`
- `sync_backlog`
- `pending_replay_count`
- `last_ingest_at`
- `last_maintenance_at`
- `project_binding`

## 6. Control Plane Integration Hooks for Feature 026

028 不新增新的 control-plane canonical resource。  
026 只消费以下 hook：

- `DiagnosticsSummaryDocument` 中的 memory subsystem summary
- `ActionRegistryDocument` 中的可选 memory maintenance actions
- `ControlPlaneEvent` 中与 memory maintenance 相关的 action execution 结果

推荐 action ids：

- `memory.flush`
- `memory.reindex`
- `memory.bridge.reconnect`
- `memory.sync.resume`

维护命令最小字段：

- `kind`: `flush | consolidate | compact | reindex | replay | sync_resume | bridge_reconnect`
- `scope_id`
- `partition`
- `reason`
- `summary`
- `evidence_refs`
- `requested_by`

维护运行最小字段：

- `run_id`
- `command_id`
- `kind`
- `scope_id`
- `partition`
- `status`
- `backend_used`
- `fragment_refs`
- `proposal_refs`
- `derived_refs`
- `diagnostic_refs`
- `metadata`
- `error_summary`

说明：

- 这些 actions 是 maintenance / diagnostics hook，不是 detailed memory browse API
- detailed memory browsing、Vault retrieval、proposal audit 仍属于 027

## 7. Audit & Governance Rules

- `MemUBackend` 不能直接创建或覆盖 `SorRecord`
- `MemUBackend` 不能直接暴露 Vault 原文
- `ingest_batch()` / `run_maintenance()` 如产生候选事实，必须返回 `WriteProposalDraft`
- 任何 `WriteProposalDraft` 都必须带 `evidence_refs`
- `before_compaction_flush()` 仍然是 flush draft 入口；backend 不得绕过该治理入口静默更新事实层

## 8. Consumer Rules

- 027 必须以已交付 canonical memory resources 为事实产品面，并以本 contract 作为 backend integration 扩展面，不得另起一套 canonical DTO
- 026 只能使用 diagnostics/actions hook，不得直接抓取 raw memory rows 作为控制台 canonical state
- frontend / Telegram / CLI 都不得绕过 backend service 直接读取 MemU bridge 私有 payload
