"""MemoryStore 测试。"""

from datetime import UTC, datetime

import aiosqlite
from octoagent.memory import (
    EvidenceRef,
    MemoryPartition,
    SorRecord,
    VaultRecord,
    WriteAction,
    WriteProposal,
)
from octoagent.memory.store import SqliteMemoryStore, init_memory_db


class TestSqliteMemoryStore:
    async def test_append_fragment_and_query(self, memory_store):
        now = datetime.now(UTC)
        fragment = await _seed_fragment(memory_store, now)
        found = await memory_store.get_fragment(fragment.fragment_id)
        assert found is not None
        assert found.content == "Project X kicked off"

        listing = await memory_store.list_fragments("work/project-x", query="kicked")
        assert len(listing) == 1

    async def test_sor_history(self, memory_store):
        now = datetime.now(UTC)
        current = SorRecord(
            memory_id="01JSOR_100000000000000001",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            subject_key="work.project-x.status",
            content="running",
            version=1,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
            updated_at=now,
        )
        await memory_store.insert_sor(current)
        await memory_store.update_sor_status(
            current.memory_id,
            status="superseded",
            updated_at=now.isoformat(),
        )
        await memory_store.insert_sor(
            current.model_copy(
                update={
                    "memory_id": "01JSOR_100000000000000002",
                    "content": "done",
                    "version": 2,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        )
        history = await memory_store.list_sor_history(
            "work/project-x",
            "work.project-x.status",
        )
        assert [item.version for item in history] == [2, 1]

    async def test_proposal_round_trip(self, memory_store):
        now = datetime.now(UTC)
        proposal = WriteProposal(
            proposal_id="01JPROP_10000000000000001",
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="sync current state",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
        )
        await memory_store.save_proposal(proposal)
        loaded = await memory_store.get_proposal(proposal.proposal_id)
        assert loaded is not None
        assert loaded.subject_key == proposal.subject_key

    async def test_store_sets_row_factory_for_named_column_access(self, tmp_path):
        db_path = tmp_path / "memory-store-row-factory.db"
        conn = await aiosqlite.connect(str(db_path))
        try:
            await init_memory_db(conn)
            store = SqliteMemoryStore(conn)
            proposal = WriteProposal(
                proposal_id="01JPROP_10000000000000002",
                scope_id="work/project-y",
                partition=MemoryPartition.WORK,
                action=WriteAction.ADD,
                subject_key="work.project-y.status",
                content="running",
                rationale="sync current state",
                confidence=0.9,
                evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
                created_at=datetime.now(UTC),
            )
            await store.save_proposal(proposal)
            await conn.commit()

            loaded = await store.get_proposal(proposal.proposal_id)
            assert loaded is not None
            assert loaded.subject_key == proposal.subject_key
        finally:
            await conn.close()

    async def test_vault_round_trip(self, memory_store):
        now = datetime.now(UTC)
        vault = VaultRecord(
            vault_id="01JVAULT_100000000000001",
            scope_id="profile/user",
            partition=MemoryPartition.HEALTH,
            subject_key="profile.user.health.note",
            summary="health note updated",
            content_ref="vault://proposal/123",
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            created_at=now,
        )
        await memory_store.insert_vault(vault)
        loaded = await memory_store.get_vault(vault.vault_id)
        assert loaded is not None
        assert loaded.partition == MemoryPartition.HEALTH


async def _seed_fragment(memory_store, now):
    from octoagent.memory import FragmentRecord

    fragment = FragmentRecord(
        fragment_id="01JFRAG_10000000000000001",
        scope_id="work/project-x",
        partition=MemoryPartition.WORK,
        content="Project X kicked off",
        evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        created_at=now,
    )
    await memory_store.append_fragment(fragment)
    return fragment
