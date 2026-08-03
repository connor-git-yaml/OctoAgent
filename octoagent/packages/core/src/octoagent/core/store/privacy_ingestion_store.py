"""F152 隐私采集 durable audit 的唯一 SQLite store。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import aiosqlite
from octoagent.core.models.privacy_ingestion import (
    AnalysisResult,
    ApprovedAnalysisPacket,
    DeletionReceipt,
    DeletionStatus,
    OptionalMemoryCandidate,
    PrivacyAuditEvent,
    ReviewBundle,
    canonical_json_bytes,
    canonical_sha256,
)


class SqlitePrivacyIngestionStore:
    """只写 non-sensitive metadata 的 append-only audit store。"""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def put_review_bundle(self, bundle: ReviewBundle) -> str:
        if not bundle.provenance:
            raise ValueError("review bundle provenance must not be empty")
        owner_ids = {item.owner_id for item in bundle.provenance}
        device_ids = {item.device_id for item in bundle.provenance}
        if len(owner_ids) != 1 or len(device_ids) != 1:
            raise ValueError("review bundle provenance scope must be unique")
        object_hash = canonical_sha256(bundle)
        await self._conn.execute(
            """
            INSERT INTO privacy_review_bundles (
                object_hash, owner_id, device_id, content, expires_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                object_hash,
                next(iter(owner_ids)),
                next(iter(device_ids)),
                canonical_json_bytes(bundle).decode(),
                bundle.retention.expires_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return object_hash

    async def put_approved_packet(
        self,
        source_hash: str,
        packet: ApprovedAnalysisPacket,
    ) -> str:
        source = await self.get_review_bundle(source_hash)
        if packet.purpose != source.purpose or canonical_json_bytes(
            packet.provenance
        ) != canonical_json_bytes(source.provenance):
            raise ValueError("approved packet does not match review bundle")
        object_hash = canonical_sha256(packet)
        await self._conn.execute(
            """
            INSERT INTO privacy_approved_packets (
                object_hash, source_hash, content, expires_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                object_hash,
                source_hash,
                canonical_json_bytes(packet).decode(),
                packet.retention.expires_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return object_hash

    async def put_analysis_result(
        self,
        packet_hash: str,
        result: AnalysisResult,
    ) -> str:
        packet = await self._load_analysis_packet(packet_hash)
        if result.packet_id != packet.packet_id:
            raise ValueError("analysis result does not match packet")
        object_hash = canonical_sha256(result)
        await self._conn.execute(
            """
            INSERT INTO privacy_analysis_results (
                object_hash, packet_hash, content, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                object_hash,
                packet_hash,
                canonical_json_bytes(result).decode(),
                result.created_at.isoformat(),
            ),
        )
        await self._conn.commit()
        return object_hash

    async def put_memory_candidate(
        self,
        result_hash: str,
        candidate: OptionalMemoryCandidate,
    ) -> str:
        result, packet_hash = await self._load_analysis_result(result_hash)
        if candidate.result_id != result.result_id or candidate.packet_sha256 != packet_hash:
            raise ValueError("memory candidate does not match analysis lineage")
        object_hash = canonical_sha256(candidate)
        await self._conn.execute(
            """
            INSERT INTO privacy_memory_candidates (
                object_hash, result_hash, content
            )
            VALUES (?, ?, ?)
            """,
            (
                object_hash,
                result_hash,
                canonical_json_bytes(candidate).decode(),
            ),
        )
        await self._conn.commit()
        return object_hash

    async def get_review_bundle(self, object_hash: str) -> ReviewBundle:
        """从唯一 F152 store 读取一个已持久化 review，不创建第二读取路径。"""

        cursor = await self._conn.execute(
            "SELECT content FROM privacy_review_bundles WHERE object_hash = ?",
            (object_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("unknown review bundle")
        return ReviewBundle.model_validate_json(row[0])

    async def _load_analysis_packet(
        self,
        object_hash: str,
    ) -> ApprovedAnalysisPacket:
        cursor = await self._conn.execute(
            "SELECT content FROM privacy_approved_packets WHERE object_hash = ?",
            (object_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("unknown approved packet")
        return ApprovedAnalysisPacket.model_validate_json(row[0])

    async def _load_analysis_result(
        self,
        object_hash: str,
    ) -> tuple[AnalysisResult, str]:
        cursor = await self._conn.execute(
            """
            SELECT content, packet_hash
            FROM privacy_analysis_results
            WHERE object_hash = ?
            """,
            (object_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("unknown analysis result")
        return AnalysisResult.model_validate_json(row[0]), str(row[1])

    async def append_audit(self, event: PrivacyAuditEvent) -> None:
        await self._conn.execute(
            """
            INSERT INTO privacy_ingestion_audit (
                event_id, event_type, schema_version, owner_hash, device_hash,
                object_hash, count, data_types, capabilities, decision, result,
                reason_code, occurred_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type.value,
                event.schema_version,
                event.owner_hash,
                event.device_hash,
                event.object_hash,
                event.count,
                json.dumps(event.data_types, separators=(",", ":")),
                json.dumps(
                    [capability.value for capability in event.capabilities],
                    separators=(",", ":"),
                ),
                event.decision.value,
                event.result.value,
                event.reason_code,
                event.occurred_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def list_audit(
        self,
        *,
        owner_hash: str,
        device_hash: str | None = None,
    ) -> list[PrivacyAuditEvent]:
        if device_hash is None:
            cursor = await self._conn.execute(
                """
                SELECT event_id, event_type, schema_version, owner_hash, device_hash,
                       object_hash, count, data_types, capabilities, decision, result,
                       reason_code, occurred_at
                FROM privacy_ingestion_audit
                WHERE owner_hash = ?
                ORDER BY occurred_at ASC, event_id ASC
                """,
                (owner_hash,),
            )
        else:
            cursor = await self._conn.execute(
                """
                SELECT event_id, event_type, schema_version, owner_hash, device_hash,
                       object_hash, count, data_types, capabilities, decision, result,
                       reason_code, occurred_at
                FROM privacy_ingestion_audit
                WHERE owner_hash = ? AND device_hash = ?
                ORDER BY occurred_at ASC, event_id ASC
                """,
                (owner_hash, device_hash),
            )
        return [self._row_to_event(row) for row in await cursor.fetchall()]

    async def list_stage_object_hashes(self, source_hash: str) -> tuple[str, ...]:
        groups = await self._stage_hash_groups(source_hash)
        return tuple(item for group in groups for item in group)

    async def _stage_hash_groups(
        self,
        source_hash: str,
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        review_cursor = await self._conn.execute(
            "SELECT object_hash FROM privacy_review_bundles WHERE object_hash = ?",
            (source_hash,),
        )
        review = [str(row[0]) for row in await review_cursor.fetchall()]
        packets = await self._child_hashes(
            "privacy_approved_packets",
            "source_hash",
            review,
        )
        results = await self._child_hashes(
            "privacy_analysis_results",
            "packet_hash",
            packets,
        )
        candidates = await self._child_hashes(
            "privacy_memory_candidates",
            "result_hash",
            results,
        )
        return review, packets, results, candidates

    async def _child_hashes(
        self,
        table: str,
        parent_column: str,
        parent_hashes: list[str],
    ) -> list[str]:
        if not parent_hashes:
            return []
        placeholders = ",".join("?" for _ in parent_hashes)
        cursor = await self._conn.execute(
            f"""
            SELECT object_hash FROM {table}
            WHERE {parent_column} IN ({placeholders})
            ORDER BY object_hash ASC
            """,
            tuple(parent_hashes),
        )
        return [str(row[0]) for row in await cursor.fetchall()]

    async def get_deletion_receipt(
        self,
        request_id: str,
    ) -> DeletionReceipt | None:
        cursor = await self._conn.execute(
            """
            SELECT request_id, source_hash, deleted_object_hashes,
                   retained_audit_hashes, status, started_at, finished_at,
                   failure_reason
            FROM privacy_deletion_receipts
            WHERE request_id = ?
            """,
            (request_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._row_to_deletion_receipt(row)

    async def delete_source_chain(
        self,
        *,
        request_id: str,
        source_hash: str,
        started_at: datetime,
    ) -> DeletionReceipt:
        existing = await self.get_deletion_receipt(request_id)
        if existing is not None:
            if existing.source_hash != source_hash:
                raise ValueError("deletion request id belongs to another source")
            if existing.status is DeletionStatus.COMPLETED:
                return existing
            started_at = existing.started_at
        started = DeletionReceipt(
            request_id=request_id,
            source_hash=source_hash,
            status=DeletionStatus.STARTED,
            started_at=started_at,
        )
        await self._write_deletion_receipt(started)
        await self._conn.commit()
        try:
            return await self._complete_deletion(started)
        except aiosqlite.DatabaseError as exc:
            await self._conn.rollback()
            failed = DeletionReceipt(
                request_id=request_id,
                source_hash=source_hash,
                status=DeletionStatus.FAILED,
                started_at=started_at,
                finished_at=self._finished_at(started_at),
                failure_reason=type(exc).__name__,
            )
            await self._write_deletion_receipt(failed)
            await self._conn.commit()
            return failed

    async def _complete_deletion(
        self,
        started: DeletionReceipt,
    ) -> DeletionReceipt:
        await self._conn.execute("BEGIN IMMEDIATE")
        groups = await self._stage_hash_groups(started.source_hash)
        object_hashes = tuple(item for group in groups for item in group)
        audit_hashes = await self._retained_audit_hashes(object_hashes)
        await self._delete_stage_rows(groups)
        completed = DeletionReceipt(
            request_id=started.request_id,
            source_hash=started.source_hash,
            deleted_object_hashes=object_hashes,
            retained_audit_hashes=audit_hashes,
            status=DeletionStatus.COMPLETED,
            started_at=started.started_at,
            finished_at=self._finished_at(started.started_at),
        )
        await self._write_deletion_receipt(completed)
        await self._conn.commit()
        await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return completed

    async def _retained_audit_hashes(
        self,
        object_hashes: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not object_hashes:
            return ()
        placeholders = ",".join("?" for _ in object_hashes)
        cursor = await self._conn.execute(
            f"""
            SELECT event_id, event_type, schema_version, owner_hash, device_hash,
                   object_hash, count, data_types, capabilities, decision, result,
                   reason_code, occurred_at
            FROM privacy_ingestion_audit
            WHERE object_hash IN ({placeholders})
            ORDER BY event_id ASC
            """,
            object_hashes,
        )
        events = [self._row_to_event(row) for row in await cursor.fetchall()]
        return tuple(sorted(canonical_sha256(event) for event in events))

    async def _delete_stage_rows(
        self,
        groups: tuple[list[str], list[str], list[str], list[str]],
    ) -> None:
        review, packets, results, candidates = groups
        for table, hashes in (
            ("privacy_memory_candidates", candidates),
            ("privacy_analysis_results", results),
            ("privacy_approved_packets", packets),
            ("privacy_review_bundles", review),
        ):
            if not hashes:
                continue
            placeholders = ",".join("?" for _ in hashes)
            await self._conn.execute(
                f"DELETE FROM {table} WHERE object_hash IN ({placeholders})",
                tuple(hashes),
            )

    async def _write_deletion_receipt(self, receipt: DeletionReceipt) -> None:
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO privacy_deletion_receipts (
                request_id, source_hash, deleted_object_hashes,
                retained_audit_hashes, status, started_at, finished_at,
                failure_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.request_id,
                receipt.source_hash,
                json.dumps(receipt.deleted_object_hashes, separators=(",", ":")),
                json.dumps(receipt.retained_audit_hashes, separators=(",", ":")),
                receipt.status.value,
                receipt.started_at.isoformat(),
                receipt.finished_at.isoformat() if receipt.finished_at else None,
                receipt.failure_reason,
            ),
        )

    @staticmethod
    def _finished_at(started_at: datetime) -> datetime:
        now = datetime.now(UTC).replace(microsecond=0)
        return max(now, started_at)

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> PrivacyAuditEvent:
        return PrivacyAuditEvent.model_validate(
            {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "schema_version": row["schema_version"],
                "owner_hash": row["owner_hash"],
                "device_hash": row["device_hash"],
                "object_hash": row["object_hash"],
                "count": row["count"],
                "data_types": json.loads(row["data_types"]),
                "capabilities": json.loads(row["capabilities"]),
                "decision": row["decision"],
                "result": row["result"],
                "reason_code": row["reason_code"],
                "occurred_at": row["occurred_at"],
            }
        )

    @staticmethod
    def _row_to_deletion_receipt(row: aiosqlite.Row) -> DeletionReceipt:
        return DeletionReceipt.model_validate(
            {
                "request_id": row["request_id"],
                "source_hash": row["source_hash"],
                "deleted_object_hashes": json.loads(row["deleted_object_hashes"]),
                "retained_audit_hashes": json.loads(row["retained_audit_hashes"]),
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "failure_reason": row["failure_reason"],
            }
        )


__all__ = ["SqlitePrivacyIngestionStore"]
