"""Memory backend 抽象测试。"""

from datetime import UTC, datetime

from octoagent.memory import (
    EvidenceRef,
    FragmentRecord,
    MemoryAccessPolicy,
    MemoryLayer,
    MemoryPartition,
    MemorySearchHit,
    MemUBackend,
    SorRecord,
    VaultRecord,
)
from octoagent.memory.service import MemoryService


class TestMemUBackend:
    async def test_adapter_delegates_to_bridge(self):
        bridge = _FakeMemUBridge()
        backend = MemUBackend(bridge)
        now = datetime.now(UTC)
        fragment = FragmentRecord(
            fragment_id="01JFRAG_MEMU_0000000001",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            content="fragment summary",
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
        )
        sor = SorRecord(
            memory_id="01JSOR_MEMU_00000000001",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            subject_key="work.project-x.status",
            content="running",
            version=1,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
            updated_at=now,
        )
        vault = VaultRecord(
            vault_id="01JVAULT_MEMU_00000001",
            scope_id="profile/user",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            summary="health note updated",
            content_ref="vault://proposal/123",
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
        )

        await backend.sync_fragment(fragment)
        await backend.sync_sor(sor)
        await backend.sync_vault(vault)
        hits = await backend.search(
            "work/project-x",
            query="running",
            policy=MemoryAccessPolicy(),
            limit=10,
        )

        assert bridge.calls == [
            ("sync_fragment", fragment.fragment_id),
            ("sync_sor", sor.memory_id),
            ("sync_vault", vault.vault_id),
            ("search", "work/project-x"),
        ]
        assert len(hits) == 1


class TestMemoryServiceBackendFallback:
    async def test_backend_failure_does_not_break_commit_or_search(self, memory_conn):
        backend = _FailingBackend()
        service = MemoryService(memory_conn, backend=backend)
        proposal = await service.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action="add",
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        validation = await service.validate_proposal(proposal.proposal_id)
        assert validation.accepted is True
        result = await service.commit_memory(proposal.proposal_id)
        assert result.sor_id is not None

        hits = await service.search_memory(scope_id="work/project-x", query="running")
        assert len(hits) >= 1
        assert any(hit.layer is MemoryLayer.SOR for hit in hits)

    async def test_backend_fallback_keeps_sensitive_partitions_hidden(self, memory_conn):
        backend = _FailingBackend()
        service = MemoryService(memory_conn, backend=backend)
        proposal = await service.propose_write(
            scope_id="profile/user",
            partition=MemoryPartition.HEALTH,
            action="add",
            subject_key="profile.user.health.note",
            content="blood pressure raw data",
            rationale="health note updated",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        validation = await service.validate_proposal(proposal.proposal_id)
        assert validation.accepted is True
        await service.commit_memory(proposal.proposal_id)

        hits = await service.search_memory(scope_id="profile/user", query="health")
        assert hits == []


class _FakeMemUBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def is_available(self) -> bool:
        return True

    async def search(self, scope_id: str, *, query=None, policy=None, limit=10):
        self.calls.append(("search", scope_id))
        _ = query, policy, limit
        return [
            MemorySearchHit(
                record_id="memu-hit-1",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary="running",
                created_at=datetime.now(UTC),
            )
        ]

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        self.calls.append(("sync_fragment", fragment.fragment_id))

    async def sync_sor(self, record: SorRecord) -> None:
        self.calls.append(("sync_sor", record.memory_id))

    async def sync_vault(self, record: VaultRecord) -> None:
        self.calls.append(("sync_vault", record.vault_id))


class _FailingBackend:
    backend_id = "memu"

    async def is_available(self) -> bool:
        return True

    async def search(self, scope_id: str, *, query=None, policy=None, limit=10):
        raise RuntimeError("backend search unavailable")

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        raise RuntimeError("backend sync unavailable")

    async def sync_sor(self, record: SorRecord) -> None:
        raise RuntimeError("backend sync unavailable")

    async def sync_vault(self, record: VaultRecord) -> None:
        raise RuntimeError("backend sync unavailable")
