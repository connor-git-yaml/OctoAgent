"""SQLite metadata backend。

这是默认降级路径：直接复用本地 governance store 做 search，
不承担 embedding/vector 索引职责，但保证最小功能可用。
"""

from ..enums import SENSITIVE_PARTITIONS, MemoryLayer
from ..models import FragmentRecord, MemoryAccessPolicy, MemorySearchHit, SorRecord, VaultRecord
from ..store.memory_store import SqliteMemoryStore
from .protocols import MemoryBackend


class SqliteMemoryBackend(MemoryBackend):
    """基于本地 SQLite metadata 的默认 backend。"""

    backend_id = "sqlite-metadata"

    def __init__(self, store: SqliteMemoryStore) -> None:
        self._store = store

    async def is_available(self) -> bool:
        return True

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]:
        policy = policy or MemoryAccessPolicy()
        sor_records = await self._store.search_sor(
            scope_id,
            query=query,
            include_history=policy.include_history,
            limit=limit,
        )
        fragment_records = await self._store.list_fragments(scope_id, query=query, limit=limit)
        if not policy.allow_vault:
            sor_records = [
                item for item in sor_records if item.partition not in SENSITIVE_PARTITIONS
            ]
            fragment_records = [
                item
                for item in fragment_records
                if item.partition not in SENSITIVE_PARTITIONS
            ]
        vault_records = []
        if policy.allow_vault:
            vault_records = await self._store.search_vault(scope_id, query=query, limit=limit)

        hits = [self._to_sor_hit(item) for item in sor_records]
        hits.extend(self._to_fragment_hit(item) for item in fragment_records)
        hits.extend(self._to_vault_hit(item) for item in vault_records)
        hits.sort(key=lambda item: item.created_at, reverse=True)
        return hits[:limit]

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        _ = fragment

    async def sync_sor(self, record: SorRecord) -> None:
        _ = record

    async def sync_vault(self, record: VaultRecord) -> None:
        _ = record

    @staticmethod
    def _to_sor_hit(record: SorRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.memory_id,
            layer=MemoryLayer.SOR,
            scope_id=record.scope_id,
            partition=record.partition,
            subject_key=record.subject_key,
            summary=record.content[:160],
            version=record.version,
            status=record.status.value,
            created_at=record.updated_at,
            metadata={"schema_version": record.schema_version},
        )

    @staticmethod
    def _to_fragment_hit(record: FragmentRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.fragment_id,
            layer=MemoryLayer.FRAGMENT,
            scope_id=record.scope_id,
            partition=record.partition,
            summary=record.content[:160],
            created_at=record.created_at,
            metadata={"schema_version": record.schema_version},
        )

    @staticmethod
    def _to_vault_hit(record: VaultRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.vault_id,
            layer=MemoryLayer.VAULT,
            scope_id=record.scope_id,
            partition=record.partition,
            subject_key=record.subject_key,
            summary=record.summary[:160],
            created_at=record.created_at,
            metadata={"schema_version": record.schema_version},
        )
