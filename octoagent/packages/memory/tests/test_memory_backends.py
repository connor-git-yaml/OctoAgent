"""Memory backend 抽象测试。"""

from datetime import UTC, datetime

from octoagent.memory import (
    DerivedMemoryQuery,
    EvidenceRef,
    FragmentRecord,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryIngestBatch,
    MemoryIngestItem,
    MemoryLayer,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRunStatus,
    MemoryPartition,
    MemorySearchHit,
    MemorySyncBatch,
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
        status = await backend.get_status()
        sync_result = await backend.sync_batch(
            MemorySyncBatch(
                batch_id="batch-1",
                scope_id="work/project-x",
                fragments=[fragment],
                sor_records=[sor],
                vault_records=[vault],
                created_at=now,
            )
        )
        ingest_result = await backend.ingest_batch(
            MemoryIngestBatch(
                ingest_id="ingest-1",
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                items=[
                    MemoryIngestItem(
                        item_id="item-1",
                        modality="text",
                        artifact_ref="artifact-1",
                    )
                ],
            )
        )
        derived = await backend.list_derivations(DerivedMemoryQuery(scope_id="work/project-x"))
        evidence = await backend.resolve_evidence(
            MemoryEvidenceQuery(record_id=fragment.fragment_id, layer=MemoryLayer.FRAGMENT)
        )
        maintenance = await backend.run_maintenance(
            MemoryMaintenanceCommand(
                command_id="command-1",
                kind=MemoryMaintenanceCommandKind.FLUSH,
                scope_id="work/project-x",
            )
        )
        hits = await backend.search(
            "work/project-x",
            query="running",
            policy=MemoryAccessPolicy(),
            limit=10,
        )

        assert status.backend_id == "memu"
        assert sync_result.synced_fragments == 1
        assert ingest_result.ingest_id == "ingest-1"
        assert derived.backend_used == "memu"
        assert evidence.record_id == fragment.fragment_id
        assert maintenance.status is MemoryMaintenanceRunStatus.COMPLETED
        assert bridge.calls == [
            ("sync_fragment", fragment.fragment_id),
            ("sync_sor", sor.memory_id),
            ("sync_vault", vault.vault_id),
            ("get_status", "memu"),
            ("sync_batch", "batch-1"),
            ("ingest_batch", "ingest-1"),
            ("list_derivations", "work/project-x"),
            ("resolve_evidence", fragment.fragment_id),
            ("run_maintenance", "command-1"),
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

    async def test_backend_can_failback_after_recovery(self, memory_conn):
        backend = _FlakyBackend()
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
        await service.validate_proposal(proposal.proposal_id)
        await service.commit_memory(proposal.proposal_id)

        fallback_hits = await service.search_memory(scope_id="work/project-x", query="running")
        assert any(hit.layer is MemoryLayer.SOR for hit in fallback_hits)
        degraded_status = await service.get_backend_status()
        assert degraded_status.active_backend == "sqlite-metadata"
        assert degraded_status.state in {
            MemoryBackendState.DEGRADED,
            MemoryBackendState.RECOVERING,
            MemoryBackendState.UNAVAILABLE,
        }

        backend.fail_search = False
        await service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id="memory-sync-resume-flaky",
                kind=MemoryMaintenanceCommandKind.SYNC_RESUME,
                scope_id="work/project-x",
            )
        )
        recovered_hits = await service.search_memory(
            scope_id="work/project-x",
            query="running",
        )
        assert recovered_hits[0].record_id == "memu-hit-recovered"
        recovered_status = await service.get_backend_status()
        assert recovered_status.active_backend == "memu"
        assert recovered_status.state is MemoryBackendState.HEALTHY

    async def test_pending_backlog_keeps_fallback_active_until_sync_resume(self, memory_conn):
        backend = _RecoverableSyncBackend()
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
        await service.validate_proposal(proposal.proposal_id)
        await service.commit_memory(proposal.proposal_id)

        backend.fail_sync = False
        stale_hits = await service.search_memory(scope_id="work/project-x", query="running")
        assert any(hit.record_id != "memu-hit-recovered" for hit in stale_hits)
        status_before_replay = await service.get_backend_status()
        assert status_before_replay.active_backend == "sqlite-metadata"
        assert status_before_replay.state is MemoryBackendState.RECOVERING

        await service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id="memory-sync-resume-recovery",
                kind=MemoryMaintenanceCommandKind.SYNC_RESUME,
                scope_id="work/project-x",
            )
        )
        recovered_hits = await service.search_memory(scope_id="work/project-x", query="running")
        assert recovered_hits[0].record_id == "memu-hit-recovered"

    async def test_fresh_service_reports_recovering_when_persisted_backlog_exists(
        self,
        memory_conn,
    ):
        backend = _RecoverableSyncBackend()
        writer = MemoryService(memory_conn, backend=backend)
        proposal = await writer.propose_write(
            scope_id="work/project-x",
            partition=MemoryPartition.WORK,
            action="add",
            subject_key="work.project-x.status",
            content="running",
            rationale="initial sync",
            confidence=0.9,
            evidence_refs=[EvidenceRef(ref_id="artifact-1", ref_type="artifact")],
        )
        await writer.validate_proposal(proposal.proposal_id)
        await writer.commit_memory(proposal.proposal_id)

        backend.fail_sync = False
        fresh_service = MemoryService(memory_conn, backend=backend)
        status = await fresh_service.get_backend_status()
        assert status.pending_replay_count == 1
        assert status.active_backend == "sqlite-metadata"
        assert status.state is MemoryBackendState.RECOVERING

        hits = await fresh_service.search_memory(scope_id="work/project-x", query="running")
        assert any(hit.record_id != "memu-hit-recovered" for hit in hits)

    async def test_fallback_ingest_persists_fragments_derived_and_proposal_drafts(
        self,
        memory_conn,
    ):
        backend = _FailingBackend()
        service = MemoryService(memory_conn, backend=backend)

        result = await service.ingest_memory_batch(
            MemoryIngestBatch(
                ingest_id="ingest-fallback-1",
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                idempotency_key="ingest-fallback-key",
                items=[
                    MemoryIngestItem(
                        item_id="item-text",
                        modality="text",
                        artifact_ref="artifact-text-1",
                        metadata={
                            "text": "Connor 正在推进 project x 交付",
                            "subject_key": "work.project-x.status",
                            "proposal_rationale": "文本提取到了稳定项目状态",
                            "entity": "Connor",
                            "relation": "owns",
                            "relation_target": "project-x",
                            "tom_summary": "Owner 认为项目处于推进阶段",
                        },
                    ),
                    MemoryIngestItem(
                        item_id="item-audio",
                        modality="audio",
                        artifact_ref="artifact-audio-1",
                        metadata={
                            "transcript": "会议录音提到 project x 需要补充测试。",
                            "sidecar_artifact_ref": "artifact-audio-sidecar-1",
                            "category": "meeting",
                        },
                    ),
                ],
            )
        )

        assert sorted(result.artifact_refs) == [
            "artifact-audio-1",
            "artifact-audio-sidecar-1",
            "artifact-text-1",
        ]
        assert len(result.fragment_refs) == 2
        assert any(draft.subject_key == "work.project-x.status" for draft in result.proposal_drafts)

        derived = await service.list_derived_memory(
            DerivedMemoryQuery(scope_id="work/project-x", limit=20)
        )
        assert derived.items
        assert any(item.derived_type == "category" for item in derived.items)
        assert any(item.derived_type == "entity" for item in derived.items)
        assert any(item.derived_type == "relation" for item in derived.items)
        assert any(item.derived_type == "tom" for item in derived.items)

        evidence = await service.resolve_memory_evidence(
            MemoryEvidenceQuery(
                record_id=result.fragment_refs[0],
                derived_id=result.derived_refs[0],
            )
        )
        assert evidence.fragment_refs
        assert evidence.artifact_refs

        repeated = await service.ingest_memory_batch(
            MemoryIngestBatch(
                ingest_id="ingest-fallback-1",
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                idempotency_key="ingest-fallback-key",
                items=[],
            )
        )
        assert repeated.fragment_refs == result.fragment_refs
        assert repeated.derived_refs == result.derived_refs

    async def test_fallback_ingest_idempotency_is_scoped_by_scope_and_partition(
        self,
        memory_conn,
    ):
        backend = _FailingBackend()
        service = MemoryService(memory_conn, backend=backend)

        first = await service.ingest_memory_batch(
            MemoryIngestBatch(
                ingest_id="ingest-scope-a",
                scope_id="work/project-a",
                partition=MemoryPartition.WORK,
                idempotency_key="shared-external-key",
                items=[
                    MemoryIngestItem(
                        item_id="item-a",
                        modality="text",
                        artifact_ref="artifact-a",
                        metadata={"text": "project a status"},
                    )
                ],
            )
        )
        second = await service.ingest_memory_batch(
            MemoryIngestBatch(
                ingest_id="ingest-scope-b",
                scope_id="profile/user-b",
                partition=MemoryPartition.PROFILE,
                idempotency_key="shared-external-key",
                items=[
                    MemoryIngestItem(
                        item_id="item-b",
                        modality="text",
                        artifact_ref="artifact-b",
                        metadata={"text": "user b profile"},
                    )
                ],
            )
        )

        assert first.fragment_refs != second.fragment_refs
        assert first.derived_refs != second.derived_refs
        derived_scope_b = await service.list_derived_memory(
            DerivedMemoryQuery(scope_id="profile/user-b", limit=10)
        )
        assert derived_scope_b.items

    async def test_sync_resume_replays_persisted_backlog(self, memory_conn):
        backend = _RecoverableSyncBackend()
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
        await service.validate_proposal(proposal.proposal_id)
        await service.commit_memory(proposal.proposal_id)

        degraded_status = await service.get_backend_status()
        assert degraded_status.pending_replay_count == 1

        backend.fail_sync = False
        run = await service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id="memory-sync-resume-1",
                kind=MemoryMaintenanceCommandKind.SYNC_RESUME,
                scope_id="work/project-x",
            )
        )
        assert run.status is MemoryMaintenanceRunStatus.COMPLETED
        assert run.metadata["replayed_batches"] == 1
        recovered_status = await service.get_backend_status()
        assert recovered_status.pending_replay_count == 0
        assert backend.synced_batches

    async def test_flush_maintenance_persists_fragment_and_proposal_draft(
        self,
        memory_conn,
        memory_store,
    ):
        service = MemoryService(memory_conn)
        run = await service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id="memory-flush-1",
                kind=MemoryMaintenanceCommandKind.FLUSH,
                scope_id="work/project-x",
                partition=MemoryPartition.WORK,
                summary="最近对话聚焦在 project x 的交付和测试。",
                evidence_refs=[EvidenceRef(ref_id="artifact-9", ref_type="artifact")],
                metadata={"subject_key": "work.project-x.summary"},
            )
        )

        assert run.status is MemoryMaintenanceRunStatus.COMPLETED
        assert run.fragment_refs
        assert run.proposal_refs
        fragment = await service.get_memory(run.fragment_refs[0], layer=MemoryLayer.FRAGMENT)
        assert fragment is not None
        proposals = await service.list_proposals(scope_ids=["work/project-x"], limit=20)
        assert any(item.proposal_id == run.proposal_refs[0] for item in proposals)
        current = await memory_store.get_current_sor("work/project-x", "work.project-x.summary")
        assert current is None

    async def test_bridge_reconnect_invokes_backend_maintenance(self, memory_conn):
        backend = _ReconnectBackend()
        service = MemoryService(memory_conn, backend=backend)

        run = await service.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id="memory-bridge-reconnect-1",
                kind=MemoryMaintenanceCommandKind.BRIDGE_RECONNECT,
                scope_id="work/project-x",
            )
        )

        assert backend.maintenance_calls == ["memory-bridge-reconnect-1"]
        assert run.backend_used == "memu"
        assert run.metadata["active_backend"] == "memu"


class _FakeMemUBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def is_available(self) -> bool:
        return True

    async def get_status(self) -> MemoryBackendStatus:
        self.calls.append(("get_status", "memu"))
        return MemoryBackendStatus(
            backend_id="memu",
            state=MemoryBackendState.HEALTHY,
            active_backend="memu",
        )

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

    async def sync_batch(self, batch: MemorySyncBatch):
        self.calls.append(("sync_batch", batch.batch_id))
        return type(
            "_SyncResult",
            (),
            {
                "synced_fragments": len(batch.fragments),
                "synced_sor_records": len(batch.sor_records),
                "synced_vault_records": len(batch.vault_records),
                "replayed_tombstones": len(batch.tombstones),
                "backend_state": MemoryBackendState.HEALTHY,
            },
        )()

    async def ingest_batch(self, batch: MemoryIngestBatch):
        self.calls.append(("ingest_batch", batch.ingest_id))
        return type(
            "_IngestResult",
            (),
            {
                "ingest_id": batch.ingest_id,
                "fragment_refs": [],
                "derived_refs": [],
                "proposal_drafts": [],
                "warnings": [],
                "errors": [],
                "backend_state": MemoryBackendState.HEALTHY,
            },
        )()

    async def list_derivations(self, query: DerivedMemoryQuery):
        self.calls.append(("list_derivations", query.scope_id))
        return type(
            "_DerivedProjection",
            (),
            {
                "backend_used": "memu",
                "backend_state": MemoryBackendState.HEALTHY,
                "items": [],
                "next_cursor": "",
                "degraded_reason": "",
            },
        )()

    async def resolve_evidence(self, query: MemoryEvidenceQuery):
        self.calls.append(("resolve_evidence", query.record_id))
        return MemoryEvidenceProjection(record_id=query.record_id)

    async def run_maintenance(self, command: MemoryMaintenanceCommand):
        self.calls.append(("run_maintenance", command.command_id))
        return type(
            "_MaintenanceRun",
            (),
            {
                "status": MemoryMaintenanceRunStatus.COMPLETED,
            },
        )()

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        self.calls.append(("sync_fragment", fragment.fragment_id))

    async def sync_sor(self, record: SorRecord) -> None:
        self.calls.append(("sync_sor", record.memory_id))

    async def sync_vault(self, record: VaultRecord) -> None:
        self.calls.append(("sync_vault", record.vault_id))


class _FailingBackend:
    backend_id = "memu"
    memory_engine_contract_version = "1.0.0"

    async def is_available(self) -> bool:
        return True

    async def get_status(self) -> MemoryBackendStatus:
        return MemoryBackendStatus(
            backend_id="memu",
            state=MemoryBackendState.UNAVAILABLE,
            active_backend="sqlite-metadata",
            failure_code="TEST_BACKEND_FAILED",
        )

    async def search(self, scope_id: str, *, query=None, policy=None, limit=10):
        raise RuntimeError("backend search unavailable")

    async def sync_batch(self, batch: MemorySyncBatch):
        raise RuntimeError("backend sync unavailable")

    async def ingest_batch(self, batch: MemoryIngestBatch):
        raise RuntimeError("backend ingest unavailable")

    async def list_derivations(self, query: DerivedMemoryQuery):
        raise RuntimeError("backend derived unavailable")

    async def resolve_evidence(self, query: MemoryEvidenceQuery):
        raise RuntimeError("backend evidence unavailable")

    async def run_maintenance(self, command: MemoryMaintenanceCommand):
        raise RuntimeError("backend maintenance unavailable")

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        raise RuntimeError("backend sync unavailable")

    async def sync_sor(self, record: SorRecord) -> None:
        raise RuntimeError("backend sync unavailable")

    async def sync_vault(self, record: VaultRecord) -> None:
        raise RuntimeError("backend sync unavailable")


class _FlakyBackend(_FailingBackend):
    def __init__(self) -> None:
        self.fail_search = True

    async def is_available(self) -> bool:
        return True

    async def get_status(self) -> MemoryBackendStatus:
        return MemoryBackendStatus(
            backend_id="memu",
            state=(
                MemoryBackendState.DEGRADED
                if self.fail_search
                else MemoryBackendState.HEALTHY
            ),
            active_backend="memu" if not self.fail_search else "sqlite-metadata",
            failure_code="TEST_BACKEND_FAILED" if self.fail_search else "",
        )

    async def search(self, scope_id: str, *, query=None, policy=None, limit=10):
        _ = query, policy, limit
        if self.fail_search:
            raise RuntimeError("backend search unavailable")
        return [
            MemorySearchHit(
                record_id="memu-hit-recovered",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary="running",
                created_at=datetime.now(UTC),
            )
        ]

    async def sync_batch(self, batch: MemorySyncBatch):
        _ = batch
        return type(
            "_SyncResult",
            (),
            {
                "synced_fragments": 0,
                "synced_sor_records": 0,
                "synced_vault_records": 0,
                "replayed_tombstones": 0,
                "backend_state": (
                    MemoryBackendState.DEGRADED
                    if self.fail_search
                    else MemoryBackendState.HEALTHY
                ),
            },
        )()


class _RecoverableSyncBackend(_FailingBackend):
    def __init__(self) -> None:
        self.fail_sync = True
        self.synced_batches: list[str] = []

    async def get_status(self) -> MemoryBackendStatus:
        return MemoryBackendStatus(
            backend_id="memu",
            state=(
                MemoryBackendState.DEGRADED
                if self.fail_sync
                else MemoryBackendState.HEALTHY
            ),
            active_backend="sqlite-metadata" if self.fail_sync else "memu",
            failure_code="TEST_BACKEND_FAILED" if self.fail_sync else "",
        )

    async def search(self, scope_id: str, *, query=None, policy=None, limit=10):
        _ = query, policy, limit
        return [
            MemorySearchHit(
                record_id="memu-hit-recovered",
                layer=MemoryLayer.SOR,
                scope_id=scope_id,
                partition=MemoryPartition.WORK,
                summary="running",
                created_at=datetime.now(UTC),
            )
        ]

    async def sync_batch(self, batch: MemorySyncBatch):
        if self.fail_sync:
            raise RuntimeError("backend sync unavailable")
        self.synced_batches.append(batch.batch_id)
        return type(
            "_SyncResult",
            (),
            {
                "synced_fragments": len(batch.fragments),
                "synced_sor_records": len(batch.sor_records),
                "synced_vault_records": len(batch.vault_records),
                "replayed_tombstones": len(batch.tombstones),
                "backend_state": MemoryBackendState.HEALTHY,
            },
        )()


class _ReconnectBackend(_FailingBackend):
    def __init__(self) -> None:
        self.maintenance_calls: list[str] = []

    async def get_status(self) -> MemoryBackendStatus:
        return MemoryBackendStatus(
            backend_id="memu",
            state=MemoryBackendState.HEALTHY,
            active_backend="memu",
        )

    async def run_maintenance(self, command: MemoryMaintenanceCommand):
        self.maintenance_calls.append(command.command_id)
        now = datetime.now(UTC)
        return type(
            "_ReconnectRun",
            (),
            {
                "run_id": f"reconnect:{command.command_id}",
                "command_id": command.command_id,
                "kind": command.kind,
                "scope_id": command.scope_id,
                "partition": command.partition,
                "status": MemoryMaintenanceRunStatus.COMPLETED,
                "backend_used": "memu",
                "fragment_refs": [],
                "proposal_refs": [],
                "derived_refs": [],
                "diagnostic_refs": ["memory:backend-status"],
                "error_summary": "",
                "metadata": {"reconnected": True},
                "started_at": now,
                "finished_at": now,
                "backend_state": MemoryBackendState.HEALTHY,
            },
        )()
