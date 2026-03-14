"""Memory backend 协议。

治理层（WriteProposal/SoR/Vault 约束）保留在 OctoAgent；
backend 负责检索、索引、外部 memory engine 同步等可替换能力。
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


class MemoryBackend(Protocol):
    """可插拔 Memory backend 协议。"""

    backend_id: str
    memory_engine_contract_version: str

    async def is_available(self) -> bool:
        """backend 是否可用。"""
        ...

    async def get_status(self) -> MemoryBackendStatus:
        """返回结构化 backend 状态。"""
        ...

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options: MemorySearchOptions | None = None,
    ) -> list[MemorySearchHit]:
        """执行 memory search。"""
        ...

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        """同步一批 fragments / sor summaries / vault summaries。"""
        ...

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        """执行多模态 ingest。"""
        ...

    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        """查询派生层。"""
        ...

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        """解析证据链。"""
        ...

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        """执行 maintenance / compaction / replay / reindex。"""
        ...

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        """同步 fragment 到 backend。"""
        ...

    async def sync_sor(self, record: SorRecord) -> None:
        """同步 sor 记录到 backend。"""
        ...

    async def sync_vault(self, record: VaultRecord) -> None:
        """同步 vault skeleton 到 backend。"""
        ...
