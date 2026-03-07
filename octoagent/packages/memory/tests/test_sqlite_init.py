"""Memory SQLite schema 测试。"""

from datetime import UTC, datetime

import aiosqlite
import pytest
from octoagent.memory import EvidenceRef, MemoryPartition, SorRecord
from octoagent.memory.store import SqliteMemoryStore, verify_memory_tables


class TestMemorySqliteInit:
    async def test_tables_created(self, memory_conn: aiosqlite.Connection):
        assert await verify_memory_tables(memory_conn) is True

    async def test_current_unique_constraint(self, memory_conn: aiosqlite.Connection):
        store = SqliteMemoryStore(memory_conn)
        now = datetime.now(UTC)
        base_kwargs = {
            "scope_id": "work/project-x",
            "partition": MemoryPartition.WORK,
            "subject_key": "work.project-x.status",
            "content": "running",
            "version": 1,
            "evidence_refs": [EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
            "created_at": now,
            "updated_at": now,
        }
        await store.insert_sor(
            SorRecord(memory_id="01JSOR_000000000000000001", **base_kwargs)
        )
        with pytest.raises(Exception):
            await store.insert_sor(
                SorRecord(memory_id="01JSOR_000000000000000002", **base_kwargs)
            )
