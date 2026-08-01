"""BuiltinMemUBridge 同步合同。"""

from __future__ import annotations

from datetime import UTC, datetime

from octoagent.gateway.services.memory.builtin_memu_bridge import (
    BuiltinMemUBridge,
    _ResolvedEmbeddingTarget,
)
from octoagent.memory import (
    MemoryBackendState,
    MemoryPartition,
    MemorySyncBatch,
    MemorySyncResult,
    SorRecord,
)


class _SqliteBackendStub:
    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        return MemorySyncResult(
            batch_id=batch.batch_id,
            synced_sor_records=len(batch.sor_records),
        )


async def test_sync_batch_uses_canonical_sor_content_and_string_tombstones() -> None:
    """SoR 投影只消费 canonical content，tombstone 保持字符串 ID。"""
    bridge = object.__new__(BuiltinMemUBridge)
    bridge._sqlite_backend = _SqliteBackendStub()
    upserted: list[dict[str, object]] = []
    deleted: list[str] = []

    async def _capture_upsert(records: list[dict[str, object]]) -> None:
        upserted.extend(records)

    async def _capture_delete(record_ids: list[str]) -> None:
        deleted.extend(record_ids)

    bridge._upsert_to_lancedb = _capture_upsert
    bridge._delete_from_lancedb = _capture_delete
    now = datetime.now(UTC)
    record = SorRecord(
        memory_id="01JMEMORYSYNC00000000000001",
        scope_id="scope-1",
        partition=MemoryPartition.WORK,
        subject_key="project:status",
        content="这是权威记忆正文",
        version=1,
        created_at=now,
        updated_at=now,
    )

    result = await bridge.sync_batch(
        MemorySyncBatch(
            batch_id="batch-1",
            scope_id="scope-1",
            sor_records=[record],
            tombstones=["retired-record-id"],
            created_at=now,
        )
    )

    assert result.backend_state is MemoryBackendState.HEALTHY
    assert upserted[0]["summary"] == record.content
    assert upserted[0]["content_text"] == "project:status: 这是权威记忆正文"
    assert deleted == ["retired-record-id"]


def test_lancedb_row_maps_to_current_memory_search_hit_contract() -> None:
    """LanceDB 行必须映射到统一 record_id/created_at 搜索合同。"""
    bridge = object.__new__(BuiltinMemUBridge)
    created_at = datetime.now(UTC)

    hit = bridge._row_to_search_hit(
        {
            "record_id": "01JMEMORYSEARCH000000000001",
            "layer": "sor",
            "scope_id": "scope-1",
            "partition": "work",
            "subject_key": "project:status",
            "summary": "这是权威记忆正文",
            "status": "current",
            "version": 2,
            "created_at": created_at.isoformat(),
            "_score": 0.75,
        },
        _ResolvedEmbeddingTarget(
            requested_target="engine-default",
            effective_target="engine-default",
            layer_id="lancedb-fts-bm25",
            mode="lancedb-fts-bm25-fallback",
        ),
        "engine-default",
    )

    assert hit.record_id == "01JMEMORYSEARCH000000000001"
    assert hit.created_at == created_at
    assert hit.version == 2
    assert hit.status == "current"
