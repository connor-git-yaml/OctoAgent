"""MemU backend adapter。

MemU 作为 memory engine 承担检索、索引和增量更新的大部分工作；
治理层 contract（proposal/arbitration/Vault policy）仍保留在 OctoAgent。
"""

from typing import Protocol

from ..models import FragmentRecord, MemoryAccessPolicy, MemorySearchHit, SorRecord, VaultRecord
from .protocols import MemoryBackend


class MemUBridge(Protocol):
    """外部 MemU bridge 协议。

    真实接入时可由 HTTP client / local process bridge / plugin adapter 实现。
    """

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


class MemUBackend(MemoryBackend):
    """MemU backend adapter。"""

    backend_id = "memu"

    def __init__(self, bridge: MemUBridge) -> None:
        self._bridge = bridge

    async def is_available(self) -> bool:
        return await self._bridge.is_available()

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
    ) -> list[MemorySearchHit]:
        return await self._bridge.search(
            scope_id,
            query=query,
            policy=policy,
            limit=limit,
        )

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        await self._bridge.sync_fragment(fragment)

    async def sync_sor(self, record: SorRecord) -> None:
        await self._bridge.sync_sor(record)

    async def sync_vault(self, record: VaultRecord) -> None:
        await self._bridge.sync_vault(record)
