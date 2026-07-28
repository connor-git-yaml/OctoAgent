from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from octoagent.core.store import create_store_group
from pydantic import ValidationError

ORACLE = "F152_PRIVACY_AUDIT_CONTRACT_MISSING"
DELETION_ORACLE = "F152_PRIVACY_DELETION_CASCADE_MISSING"


def _contracts() -> tuple[Any, Any]:
    models = importlib.import_module("octoagent.core.models.privacy_ingestion")
    try:
        store = importlib.import_module("octoagent.core.store.privacy_ingestion_store")
    except ModuleNotFoundError:
        pytest.fail(f"{ORACLE}: durable audit store is absent", pytrace=False)
    required_models = {"PrivacyAuditEvent"}
    required_store = {"SqlitePrivacyIngestionStore"}
    missing = sorted((required_models - set(vars(models))) | (required_store - set(vars(store))))
    if missing:
        pytest.fail(f"{ORACLE}: missing symbols={','.join(missing)}", pytrace=False)
    return models, store


def _deletion_contracts() -> tuple[Any, Any]:
    models, store = _contracts()
    required_models = {"DeletionReceipt", "DeletionStatus"}
    required_methods = {
        "delete_source_chain",
        "get_deletion_receipt",
        "list_stage_object_hashes",
        "put_analysis_result",
        "put_approved_packet",
        "put_memory_candidate",
        "put_review_bundle",
    }
    missing = sorted(required_models - set(vars(models)))
    store_class = store.SqlitePrivacyIngestionStore
    missing.extend(sorted(required_methods - set(vars(store_class))))
    if missing:
        pytest.fail(
            f"{DELETION_ORACLE}: missing symbols={','.join(missing)}",
            pytrace=False,
        )
    return models, store


def _audit_payload() -> dict[str, object]:
    return {
        "event_id": "audit-1",
        "event_type": "consent_approved",
        "schema_version": 1,
        "owner_hash": "1" * 64,
        "device_hash": "2" * 64,
        "object_hash": "3" * 64,
        "count": 2,
        "data_types": ["step_count", "heart_rate"],
        "capabilities": ["device.profile.read"],
        "decision": "allow",
        "result": "success",
        "reason_code": "CONSENT_APPROVED",
        "occurred_at": datetime(2026, 7, 28, 10, tzinfo=UTC),
    }


def _chain(models: Any) -> tuple[Any, Any, Any, Any]:
    provenance = models.Provenance.model_validate(
        {
            "source_kind": "device_profile",
            "source_object_hash": "4" * 64,
            "owner_id": "owner-1",
            "device_id": "device-1",
            "captured_at": datetime(2026, 7, 28, 10, tzinfo=UTC),
            "time_range": {
                "start": datetime(2026, 7, 28, 9, tzinfo=UTC),
                "end": datetime(2026, 7, 28, 10, tzinfo=UTC),
            },
            "data_types": ["activity_summary"],
        }
    )
    bundle = models.ReviewBundle.model_validate(
        {
            "bundle_id": "bundle-1",
            "purpose": "summarize recent wellness pattern",
            "provenance": [provenance],
            "fact_count": 1,
            "field_manifest": ["activity_summary"],
            "preview_hash": "5" * 64,
            "retention": {
                "stage": "review_bundle",
                "created_at": datetime(2026, 7, 28, 10, tzinfo=UTC),
                "expires_at": datetime(2026, 7, 28, 11, tzinfo=UTC),
            },
        }
    )
    packet = models.ApprovedAnalysisPacket.model_validate(
        {
            "packet_id": "packet-1",
            "purpose": bundle.purpose,
            "facts_sha256": "6" * 64,
            "provenance": bundle.provenance,
            "consent_id": "consent-1",
            "retention": {
                "stage": "approved_analysis_packet",
                "created_at": datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
                "expires_at": datetime(2026, 7, 28, 10, 12, tzinfo=UTC),
            },
        }
    )
    result = models.AnalysisResult.model_validate(
        {
            "result_id": "result-1",
            "packet_id": packet.packet_id,
            "result_sha256": "7" * 64,
            "created_at": datetime(2026, 7, 28, 10, 4, tzinfo=UTC),
            "retention_state": "ephemeral",
        }
    )
    candidate = models.OptionalMemoryCandidate.model_validate(
        {
            "candidate_id": "candidate-1",
            "result_id": result.result_id,
            "packet_sha256": models.canonical_sha256(packet),
            "provenance": packet.provenance,
            "user_selected_text": "F152-sensitive-candidate-marker",
            "review_state": "pending",
        }
    )
    return bundle, packet, result, candidate


async def _persist_chain(group: Any, models: Any) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    bundle, packet, result, candidate = _chain(models)
    hashes = (
        await group.privacy_ingestion_store.put_review_bundle(bundle),
        await group.privacy_ingestion_store.put_approved_packet(
            models.canonical_sha256(bundle),
            packet,
        ),
        await group.privacy_ingestion_store.put_analysis_result(
            models.canonical_sha256(packet),
            result,
        ),
        await group.privacy_ingestion_store.put_memory_candidate(
            models.canonical_sha256(result),
            candidate,
        ),
    )
    return (bundle, packet, result, candidate), hashes


def test_audit_event_has_exact_non_sensitive_metadata() -> None:
    models, _ = _contracts()
    event = models.PrivacyAuditEvent.model_validate(_audit_payload())
    assert set(models.PrivacyAuditEvent.model_fields) == {
        "event_id",
        "event_type",
        "schema_version",
        "owner_hash",
        "device_hash",
        "object_hash",
        "count",
        "data_types",
        "capabilities",
        "decision",
        "result",
        "reason_code",
        "occurred_at",
    }, ORACLE
    assert event.data_types == ("heart_rate", "step_count"), ORACLE
    assert event.capabilities == (models.DeviceCapability.DEVICE_PROFILE_READ,), ORACLE

    for forbidden in (
        "raw_sample",
        "normalized_fact",
        "packet_text",
        "result_text",
        "token",
        "signature",
        "nonce",
        "email",
        "calendar_id",
        "health_sample_id",
    ):
        with pytest.raises(ValidationError):
            models.PrivacyAuditEvent.model_validate(
                _audit_payload() | {forbidden: f"forbidden-{forbidden}"}
            )


@pytest.mark.asyncio
async def test_audit_is_durable_append_only_and_store_group_owned(tmp_path: Path) -> None:
    models, store_module = _contracts()
    db_path = tmp_path / "octo.db"
    artifacts = tmp_path / "artifacts"
    event = models.PrivacyAuditEvent.model_validate(_audit_payload())

    first = await create_store_group(str(db_path), artifacts)
    try:
        assert isinstance(
            first.privacy_ingestion_store,
            store_module.SqlitePrivacyIngestionStore,
        ), ORACLE
        await first.privacy_ingestion_store.append_audit(event)
        with pytest.raises(aiosqlite.IntegrityError):
            await first.privacy_ingestion_store.append_audit(event)
    finally:
        await first.close()

    reopened = await create_store_group(str(db_path), artifacts)
    try:
        rows = await reopened.privacy_ingestion_store.list_audit(
            owner_hash="1" * 64,
            device_hash="2" * 64,
        )
        assert rows == [event], ORACLE
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_audit_schema_and_bytes_cannot_store_sensitive_body(tmp_path: Path) -> None:
    models, _ = _contracts()
    db_path = tmp_path / "octo.db"
    group = await create_store_group(str(db_path), tmp_path / "artifacts")
    try:
        event = models.PrivacyAuditEvent.model_validate(_audit_payload())
        await group.privacy_ingestion_store.append_audit(event)
        cursor = await group.conn.execute("PRAGMA table_info(privacy_ingestion_audit)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        assert columns == set(models.PrivacyAuditEvent.model_fields), ORACLE
    finally:
        await group.close()

    stored = db_path.read_bytes()
    for forbidden in (
        b"forbidden-raw-body",
        b"service-token-secret",
        b"owner-email-secret",
        b"calendar-system-id",
        b"health-system-id",
    ):
        assert forbidden not in stored, ORACLE


def test_deletion_receipt_has_exact_recoverable_states() -> None:
    models, _ = _deletion_contracts()
    started = datetime(2026, 7, 28, 10, tzinfo=UTC)
    receipt = models.DeletionReceipt.model_validate(
        {
            "request_id": "delete-1",
            "source_hash": "4" * 64,
            "deleted_object_hashes": [],
            "retained_audit_hashes": [],
            "status": "started",
            "started_at": started,
            "finished_at": None,
            "failure_reason": "",
        }
    )
    assert receipt.status is models.DeletionStatus.STARTED, DELETION_ORACLE
    assert set(models.DeletionReceipt.model_fields) == {
        "request_id",
        "source_hash",
        "deleted_object_hashes",
        "retained_audit_hashes",
        "status",
        "started_at",
        "finished_at",
        "failure_reason",
    }, DELETION_ORACLE
    for patch in (
        {"status": "completed", "finished_at": None},
        {"status": "failed", "finished_at": started, "failure_reason": ""},
        {"status": "started", "failure_reason": "hidden failure"},
        {"deleted_object_hashes": ["a" * 64, "a" * 64]},
    ):
        with pytest.raises(ValidationError):
            models.DeletionReceipt.model_validate(receipt.model_dump() | patch)


@pytest.mark.asyncio
async def test_deletion_cascades_once_and_retains_only_audit_hash(
    tmp_path: Path,
) -> None:
    models, _ = _deletion_contracts()
    db_path = tmp_path / "octo.db"
    group = await create_store_group(str(db_path), tmp_path / "artifacts")
    try:
        _, hashes = await _persist_chain(group, models)
        event = models.PrivacyAuditEvent.model_validate(
            _audit_payload() | {"event_id": "audit-delete", "object_hash": hashes[1]}
        )
        await group.privacy_ingestion_store.append_audit(event)
        assert await group.privacy_ingestion_store.list_stage_object_hashes(hashes[0]) == hashes, (
            DELETION_ORACLE
        )

        receipt = await group.privacy_ingestion_store.delete_source_chain(
            request_id="delete-1",
            source_hash=hashes[0],
            started_at=datetime(2026, 7, 28, 10, 5, tzinfo=UTC),
        )
        assert receipt.status is models.DeletionStatus.COMPLETED, DELETION_ORACLE
        assert receipt.deleted_object_hashes == tuple(sorted(hashes)), DELETION_ORACLE
        assert receipt.retained_audit_hashes == (models.canonical_sha256(event),), DELETION_ORACLE
        assert await group.privacy_ingestion_store.list_stage_object_hashes(hashes[0]) == (), (
            DELETION_ORACLE
        )
        assert await group.privacy_ingestion_store.get_deletion_receipt("delete-1") == receipt, (
            DELETION_ORACLE
        )
        assert (
            await group.privacy_ingestion_store.delete_source_chain(
                request_id="delete-1",
                source_hash=hashes[0],
                started_at=datetime(2026, 7, 28, 10, 6, tzinfo=UTC),
            )
            == receipt
        ), DELETION_ORACLE
    finally:
        await group.close()

    assert b"F152-sensitive-candidate-marker" not in db_path.read_bytes(), DELETION_ORACLE
    wal_path = db_path.with_name(f"{db_path.name}-wal")
    if wal_path.exists():
        assert b"F152-sensitive-candidate-marker" not in wal_path.read_bytes(), DELETION_ORACLE


@pytest.mark.asyncio
async def test_deletion_failure_rolls_back_and_same_request_recovers(
    tmp_path: Path,
) -> None:
    models, _ = _deletion_contracts()
    group = await create_store_group(str(tmp_path / "octo.db"), tmp_path / "artifacts")
    try:
        _, hashes = await _persist_chain(group, models)
        await group.conn.execute(
            """
            CREATE TRIGGER fail_f152_candidate_delete
            BEFORE DELETE ON privacy_memory_candidates
            BEGIN
                SELECT RAISE(ABORT, 'injected deletion failure');
            END;
            """
        )
        await group.conn.commit()
        failed = await group.privacy_ingestion_store.delete_source_chain(
            request_id="delete-retry",
            source_hash=hashes[0],
            started_at=datetime(2026, 7, 28, 10, 5, tzinfo=UTC),
        )
        assert failed.status is models.DeletionStatus.FAILED, DELETION_ORACLE
        assert await group.privacy_ingestion_store.list_stage_object_hashes(hashes[0]) == hashes, (
            DELETION_ORACLE
        )

        await group.conn.execute("DROP TRIGGER fail_f152_candidate_delete")
        await group.conn.commit()
        recovered = await group.privacy_ingestion_store.delete_source_chain(
            request_id="delete-retry",
            source_hash=hashes[0],
            started_at=datetime(2026, 7, 28, 10, 6, tzinfo=UTC),
        )
        assert recovered.status is models.DeletionStatus.COMPLETED, DELETION_ORACLE
        assert recovered.started_at == failed.started_at, DELETION_ORACLE
        assert await group.privacy_ingestion_store.list_stage_object_hashes(hashes[0]) == (), (
            DELETION_ORACLE
        )
    finally:
        await group.close()
