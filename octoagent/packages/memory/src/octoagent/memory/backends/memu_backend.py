"""MemU backend adapter。

MemU 作为 memory engine 承担检索、索引和增量更新的大部分工作；
治理层 contract（proposal/arbitration/Vault policy）仍保留在 OctoAgent。
"""

from typing import Protocol

from ..models import (
    DerivedMemoryQuery,
    FragmentRecord,
    MemoryAccessPolicy,
    MemoryBackendStatus,
    MemoryDerivedProjection,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryIngestBatch,
    MemoryIngestResult,
    MemoryMaintenanceCommand,
    MemoryMaintenanceRun,
    MemorySearchHit,
    MemorySearchOptions,
    MemorySyncBatch,
    MemorySyncResult,
    SorRecord,
    VaultRecord,
)
from .protocols import MemoryBackend


class MemUBridge(Protocol):
    """外部 MemU bridge 协议。

    真实接入时可由 HTTP client / local process bridge / plugin adapter 实现。
    """

    async def is_available(self) -> bool: ...

    async def get_status(self) -> MemoryBackendStatus: ...

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
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

    async def sync_fragment(self, fragment: FragmentRecord) -> None: ...

    async def sync_sor(self, record: SorRecord) -> None: ...

    async def sync_vault(self, record: VaultRecord) -> None: ...


class MemUBackend(MemoryBackend):
    """MemU backend adapter。"""

    backend_id = "memu"
    memory_engine_contract_version = "1.0.0"

    def __init__(self, bridge: MemUBridge) -> None:
        self._bridge = bridge

    async def is_available(self) -> bool:
        return await self._bridge.is_available()

    async def get_status(self) -> MemoryBackendStatus:
        return await self._bridge.get_status()

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        return await self._bridge.search(
            scope_id,
            query=query,
            policy=policy,
            limit=limit,
            search_options=search_options,
        )

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        return await self._bridge.sync_batch(batch)

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        return await self._bridge.ingest_batch(batch)

    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        return await self._bridge.list_derivations(query)

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        return await self._bridge.resolve_evidence(query)

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        return await self._bridge.run_maintenance(command)

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        await self._bridge.sync_fragment(fragment)

    async def sync_sor(self, record: SorRecord) -> None:
        await self._bridge.sync_sor(record)

    async def sync_vault(self, record: VaultRecord) -> None:
        await self._bridge.sync_vault(record)
