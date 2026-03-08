"""Memory Store Protocol。"""

from typing import Protocol

from ..models import (
    FragmentRecord,
    SorRecord,
    VaultAccessGrantRecord,
    VaultAccessRequestRecord,
    VaultRecord,
    VaultRetrievalAuditRecord,
    WriteProposal,
)


class MemoryStore(Protocol):
    """Memory 持久化协议。"""

    async def save_proposal(self, proposal: WriteProposal) -> None: ...

    async def get_proposal(self, proposal_id: str) -> WriteProposal | None: ...

    async def list_proposals(
        self,
        *,
        scope_ids: list[str] | None = None,
        statuses: list[str] | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> list[WriteProposal]: ...

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

    async def create_vault_access_request(
        self,
        record: VaultAccessRequestRecord,
    ) -> None: ...

    async def get_vault_access_request(
        self,
        request_id: str,
    ) -> VaultAccessRequestRecord | None: ...

    async def replace_vault_access_request(
        self,
        record: VaultAccessRequestRecord,
    ) -> None: ...

    async def list_vault_access_requests(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessRequestRecord]: ...

    async def insert_vault_access_grant(
        self,
        record: VaultAccessGrantRecord,
    ) -> None: ...

    async def get_vault_access_grant(
        self,
        grant_id: str,
    ) -> VaultAccessGrantRecord | None: ...

    async def replace_vault_access_grant(
        self,
        record: VaultAccessGrantRecord,
    ) -> None: ...

    async def list_vault_access_grants(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 50,
    ) -> list[VaultAccessGrantRecord]: ...

    async def append_vault_retrieval_audit(
        self,
        record: VaultRetrievalAuditRecord,
    ) -> None: ...

    async def list_vault_retrieval_audits(
        self,
        *,
        project_id: str,
        workspace_id: str | None = None,
        scope_ids: list[str] | None = None,
        subject_key: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[VaultRetrievalAuditRecord]: ...
