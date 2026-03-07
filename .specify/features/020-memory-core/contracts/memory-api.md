# Feature 020 Contracts: Memory API

## `propose_write()`

```python
async def propose_write(
    *,
    scope_id: str,
    partition: MemoryPartition,
    action: WriteAction,
    subject_key: str | None,
    content: str | None,
    rationale: str,
    confidence: float,
    evidence_refs: list[EvidenceRef],
    expected_version: int | None = None,
    is_sensitive: bool = False,
    metadata: dict[str, str | int | float | bool | None] | None = None,
) -> WriteProposal
```

**约束**:

- 返回 `status=pending`
- 不写 SoR / Vault
- 不做副作用

## `validate_proposal()`

```python
async def validate_proposal(proposal_id: str) -> ProposalValidation
```

**校验项**:

- action 是否合法
- `confidence` 是否在 0~1
- `subject_key` / `content` / `evidence_refs` 是否满足 action 要求
- `UPDATE` / `DELETE` 是否命中 current 记录
- 敏感分区是否需路由 Vault

## `commit_memory()`

```python
async def commit_memory(proposal_id: str) -> CommitResult
```

**行为**:

- `ADD`: 新建 current SoR；若命中敏感分区则额外写入 Vault skeleton
- `UPDATE`: 旧 current -> superseded，新记录 -> current；若命中敏感分区则额外写入 Vault skeleton
- `DELETE`: 当前记录 -> deleted
- `NONE`: 不写 SoR/Vault，只保留审计状态

## `search_memory()`

```python
async def search_memory(
    *,
    scope_id: str,
    query: str | None = None,
    policy: MemoryAccessPolicy | None = None,
    limit: int = 10,
) -> list[MemorySearchHit]
```

**默认行为**:

- `policy=None` 时只查 `SoR.current + Fragments`
- `policy.allow_vault=False` 时默认排除 Vault
- 返回摘要对象，不返回大正文

## `get_memory()`

```python
async def get_memory(
    record_id: str,
    *,
    layer: MemoryLayer,
    policy: MemoryAccessPolicy | None = None,
) -> FragmentRecord | SorRecord | VaultRecord | None
```

**默认行为**:

- Vault 没有授权时拒绝
- 普通 SoR/Fragment 正常返回详情

## `before_compaction_flush()`

```python
async def before_compaction_flush(
    *,
    scope_id: str,
    summary: str,
    evidence_refs: list[EvidenceRef],
    partition: MemoryPartition = MemoryPartition.WORK,
    subject_key: str | None = None,
) -> CompactionFlushResult
```

**行为**:

- 生成 1 条 Fragment 草案
- 可选生成 1 条 `WriteProposal`
- 不直接 commit SoR

## `MemoryBackend`

```python
class MemoryBackend(Protocol):
    backend_id: str

    async def is_available(self) -> bool: ...
    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]: ...
    async def sync_fragment(self, fragment: FragmentRecord) -> None: ...
    async def sync_sor(self, record: SorRecord) -> None: ...
    async def sync_vault(self, record: VaultRecord) -> None: ...
```

约束：

- governance 仍以 SQLite + arbitration 为事实源
- backend 承担检索、索引、外部 memory engine 同步
- backend 失败时必须允许服务降级到本地 SQLite search

## `MemUBackend`

```python
class MemUBridge(Protocol):
    async def is_available(self) -> bool: ...
    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]: ...
    async def sync_fragment(self, fragment: FragmentRecord) -> None: ...
    async def sync_sor(self, record: SorRecord) -> None: ...
    async def sync_vault(self, record: VaultRecord) -> None: ...
```

说明：

- `MemUBackend` 是 adapter，不接管 governance
- M2 即可让 MemU 承担大部分 memory engine 工作：search/index/sync
- Chat Import / knowledge update 等增强能力后续可继续从 `MemUBridge` 扩展
