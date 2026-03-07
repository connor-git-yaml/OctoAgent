"""Memory backend 协议。

治理层（WriteProposal/SoR/Vault 约束）保留在 OctoAgent；
backend 负责检索、索引、外部 memory engine 同步等可替换能力。
"""

from typing import Protocol

from ..models import FragmentRecord, MemoryAccessPolicy, MemorySearchHit, SorRecord, VaultRecord


class MemoryBackend(Protocol):
    """可插拔 Memory backend 协议。"""

    backend_id: str

    async def is_available(self) -> bool:
        """backend 是否可用。"""
        ...

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]:
        """执行 memory search。"""
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
