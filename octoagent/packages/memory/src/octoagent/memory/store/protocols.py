"""Memory Store Protocol。"""

from typing import Protocol

from ..models import FragmentRecord, SorRecord, VaultRecord, WriteProposal


class MemoryStore(Protocol):
    """Memory 持久化协议。"""

    async def save_proposal(self, proposal: WriteProposal) -> None: ...

    async def get_proposal(self, proposal_id: str) -> WriteProposal | None: ...

    async def replace_proposal(self, proposal: WriteProposal) -> None: ...

    async def append_fragment(self, fragment: FragmentRecord) -> None: ...

    async def get_fragment(self, fragment_id: str) -> FragmentRecord | None: ...

    async def list_fragments(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> list[FragmentRecord]: ...

    async def insert_sor(self, record: SorRecord) -> None: ...

    async def get_sor(self, memory_id: str) -> SorRecord | None: ...

    async def get_current_sor(self, scope_id: str, subject_key: str) -> SorRecord | None: ...

    async def list_sor_history(self, scope_id: str, subject_key: str) -> list[SorRecord]: ...

    async def update_sor_status(
        self,
        memory_id: str,
        *,
        status: str,
        updated_at: str,
    ) -> None: ...

    async def get_next_sor_version(self, scope_id: str, subject_key: str) -> int: ...

    async def insert_vault(self, record: VaultRecord) -> None: ...

    async def get_vault(self, vault_id: str) -> VaultRecord | None: ...

    async def search_sor(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        include_history: bool = False,
        limit: int = 10,
    ) -> list[SorRecord]: ...

    async def search_vault(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> list[VaultRecord]: ...
