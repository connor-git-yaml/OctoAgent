"""Memory 持久性测试。"""

from pathlib import Path

import aiosqlite
from octoagent.memory import EvidenceRef, MemoryPartition, MemoryService, WriteAction
from octoagent.memory.store import SqliteMemoryStore, init_memory_db


class TestMemoryDurability:
    async def test_memory_survives_restart(self, tmp_path: Path):
        db_path = tmp_path / "memory_durability.db"

        conn1 = await aiosqlite.connect(str(db_path))
        conn1.row_factory = aiosqlite.Row
        await init_memory_db(conn1)
        service1 = MemoryService(conn1)
        proposal = await service1.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action=WriteAction.ADD,
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        await service1.validate_proposal(proposal.proposal_id)
        await service1.commit_memory(proposal.proposal_id)
        await conn1.close()

        conn2 = await aiosqlite.connect(str(db_path))
        conn2.row_factory = aiosqlite.Row
        await init_memory_db(conn2)
        store = SqliteMemoryStore(conn2)
        current = await store.get_current_sor("work/project-x", "work.project-x.status")
        assert current is not None
        assert current.content == "running"
        await conn2.close()
