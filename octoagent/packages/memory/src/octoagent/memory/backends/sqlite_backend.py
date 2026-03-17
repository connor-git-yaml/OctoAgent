"""SQLite metadata backend。

这是默认降级路径：直接复用本地 governance store 做 search，
并在 MemU 不可用时提供最小可用的 ingest / derived / evidence / maintenance 能力。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..enums import SENSITIVE_PARTITIONS, MemoryLayer, MemoryPartition
from ..models import (
    DerivedMemoryQuery,
    DerivedMemoryRecord,
    EvidenceRef,
    FragmentRecord,
    MemoryAccessPolicy,
    MemoryBackendState,
    MemoryBackendStatus,
    MemoryDerivedProjection,
    MemoryEvidenceProjection,
    MemoryEvidenceQuery,
    MemoryIngestBatch,
    MemoryIngestItem,
    MemoryIngestResult,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRun,
    MemoryMaintenanceRunStatus,
    MemorySearchHit,
    MemorySyncBatch,
    MemorySyncResult,
    SorRecord,
    VaultRecord,
    WriteProposalDraft,
)
from ..store.memory_store import SqliteMemoryStore
from .protocols import MemoryBackend


class SqliteMemoryBackend(MemoryBackend):
    """基于本地 SQLite metadata 的默认 backend。"""

    backend_id = "sqlite-metadata"
    memory_engine_contract_version = "1.0.0"

    def __init__(self, store: SqliteMemoryStore) -> None:
        self._store = store

    async def is_available(self) -> bool:
        return True

    async def get_status(self) -> MemoryBackendStatus:
        pending_backlog = await self._store.count_pending_sync_backlog()
        latest_ingest_at = await self._store.get_latest_ingest_at()
        latest_maintenance_at = await self._store.get_latest_maintenance_at()
        return MemoryBackendStatus(
            backend_id=self.backend_id,
            memory_engine_contract_version=self.memory_engine_contract_version,
            state=MemoryBackendState.HEALTHY,
            active_backend=self.backend_id,
            message="SQLite metadata fallback backend",
            retry_after=None,
            sync_backlog=pending_backlog,
            pending_replay_count=pending_backlog,
            last_ingest_at=(
                datetime.fromisoformat(latest_ingest_at) if latest_ingest_at else None
            ),
            last_maintenance_at=(
                datetime.fromisoformat(latest_maintenance_at)
                if latest_maintenance_at
                else None
            ),
            index_health={
                "mode": "metadata-only",
                "derived_layers": "local-fallback",
                "sync_backlog": pending_backlog,
            },
        )

    async def search(
        self,
        scope_id: str,
        *,
        query: str | None = None,
        policy: MemoryAccessPolicy | None = None,
        limit: int = 10,
        search_options=None,
    ) -> list[MemorySearchHit]:
        _ = search_options
        policy = policy or MemoryAccessPolicy()
        sor_records = await self._store.search_sor(
            scope_id,
            query=query,
            include_history=policy.include_history,
            limit=limit,
        )
        fragment_records = await self._store.list_fragments(scope_id, query=query, limit=limit)
        if not policy.allow_vault:
            sor_records = [
                item for item in sor_records if item.partition not in SENSITIVE_PARTITIONS
            ]
            fragment_records = [
                item for item in fragment_records if item.partition not in SENSITIVE_PARTITIONS
            ]
        vault_records: list[VaultRecord] = []
        if policy.allow_vault:
            vault_records = await self._store.search_vault(scope_id, query=query, limit=limit)

        hits = [self._to_sor_hit(item) for item in sor_records]
        hits.extend(self._to_fragment_hit(item) for item in fragment_records)
        hits.extend(self._to_vault_hit(item) for item in vault_records)
        hits.sort(key=lambda item: item.created_at, reverse=True)
        return hits[:limit]

    async def sync_fragment(self, fragment: FragmentRecord) -> None:
        _ = fragment

    async def sync_sor(self, record: SorRecord) -> None:
        _ = record

    async def sync_vault(self, record: VaultRecord) -> None:
        _ = record

    async def sync_batch(self, batch: MemorySyncBatch) -> MemorySyncResult:
        return MemorySyncResult(
            batch_id=batch.batch_id,
            synced_fragments=len(batch.fragments),
            synced_sor_records=len(batch.sor_records),
            synced_vault_records=len(batch.vault_records),
            replayed_tombstones=len(batch.tombstones),
            backend_state=MemoryBackendState.HEALTHY,
        )

    async def ingest_batch(self, batch: MemoryIngestBatch) -> MemoryIngestResult:
        existing = await self._store.get_ingest_result(
            ingest_id=batch.ingest_id,
            scope_id=batch.scope_id,
            partition=batch.partition.value,
            idempotency_key=batch.idempotency_key,
        )
        if existing is not None:
            return existing[0]

        now = datetime.now(UTC)
        artifact_refs: list[str] = []
        fragment_refs: list[str] = []
        derived_refs: list[str] = []
        proposal_drafts: list[WriteProposalDraft] = []
        warnings: list[str] = []
        errors: list[str] = []

        for item in batch.items:
            item_artifacts = [item.artifact_ref]
            sidecar_artifact_ref = self._metadata_str(item, "sidecar_artifact_ref")
            if sidecar_artifact_ref:
                item_artifacts.append(sidecar_artifact_ref)
            artifact_refs.extend(item_artifacts)

            content = self._extract_item_text(item)
            if not content:
                warnings.append(
                    f"{item.item_id}: {item.modality} 缺少 extractor/sidecar 文本，"
                    "已退化为 artifact 引用摘要。"
                )
                content = (
                    self._metadata_str(item, "summary")
                    or self._metadata_str(item, "caption")
                    or f"{item.modality} artifact {item.artifact_ref}"
                )

            fragment_id = f"fragment:{batch.ingest_id}:{item.item_id}"
            if await self._store.get_fragment(fragment_id) is None:
                fragment = FragmentRecord(
                    fragment_id=fragment_id,
                    scope_id=batch.scope_id,
                    partition=batch.partition,
                    content=content[:2000],
                    metadata={
                        "source": "memory_ingest",
                        "ingest_id": batch.ingest_id,
                        "item_id": item.item_id,
                        "modality": item.modality,
                        "artifact_ref": item.artifact_ref,
                        "content_ref": item.content_ref,
                        "project_id": batch.project_id,
                        "workspace_id": batch.workspace_id,
                    },
                    evidence_refs=[
                        EvidenceRef(
                            ref_id=artifact_ref,
                            ref_type="artifact",
                            snippet=content[:120],
                        )
                        for artifact_ref in item_artifacts
                    ],
                    created_at=now,
                )
                await self._store.append_fragment(fragment)
            fragment_refs.append(fragment_id)

            item_derived = self._build_derived_records(
                batch=batch,
                item=item,
                fragment_id=fragment_id,
                item_artifacts=item_artifacts,
                content=content,
                created_at=now,
            )
            for record in item_derived:
                await self._store.insert_derived_record(record)
                derived_refs.append(record.derived_id)

            draft = self._build_proposal_draft(
                batch=batch,
                item=item,
                fragment_id=fragment_id,
                item_artifacts=item_artifacts,
                content=content,
                derived_ids=[record.derived_id for record in item_derived],
            )
            if draft is not None:
                proposal_drafts.append(draft)

        result = MemoryIngestResult(
            ingest_id=batch.ingest_id,
            artifact_refs=sorted(set(artifact_refs)),
            fragment_refs=sorted(set(fragment_refs)),
            derived_refs=sorted(set(derived_refs)),
            proposal_drafts=proposal_drafts,
            warnings=warnings,
            errors=errors,
            backend_state=MemoryBackendState.DEGRADED,
        )
        await self._store.save_ingest_result(
            batch_id=batch.ingest_id,
            scope_id=batch.scope_id,
            partition=batch.partition.value,
            idempotency_key=batch.idempotency_key,
            result=result,
            created_at=now.isoformat(),
        )
        return result

    async def list_derivations(
        self,
        query: DerivedMemoryQuery,
    ) -> MemoryDerivedProjection:
        items = await self._store.list_derived_records(query)
        next_cursor = items[-1].created_at.isoformat() if len(items) == query.limit else ""
        return MemoryDerivedProjection(
            backend_used=self.backend_id,
            backend_state=MemoryBackendState.DEGRADED,
            items=items,
            next_cursor=next_cursor,
            degraded_reason="sqlite fallback derived layer",
        )

    async def resolve_evidence(
        self,
        query: MemoryEvidenceQuery,
    ) -> MemoryEvidenceProjection:
        projection = MemoryEvidenceProjection(record_id=query.record_id)
        if query.proposal_id:
            proposal = await self._store.get_proposal(query.proposal_id)
            if proposal is not None:
                projection.proposal_refs.append(proposal.proposal_id)
                for ref in proposal.evidence_refs:
                    if ref.ref_type == "artifact":
                        projection.artifact_refs.append(ref.ref_id)
                    if ref.ref_type == "fragment":
                        projection.fragment_refs.append(ref.ref_id)
            return projection

        if query.derived_id:
            derived = await self._store.get_derived_record(query.derived_id)
            if derived is not None:
                projection.derived_refs.append(derived.derived_id)
                projection.fragment_refs.extend(derived.source_fragment_refs)
                projection.artifact_refs.extend(derived.source_artifact_refs)
                if derived.proposal_ref:
                    projection.proposal_refs.append(derived.proposal_ref)
            return projection

        if query.layer is MemoryLayer.FRAGMENT:
            fragment = await self._store.get_fragment(query.record_id)
            if fragment is not None:
                projection.fragment_refs.append(fragment.fragment_id)
                projection.artifact_refs.extend(
                    ref.ref_id for ref in fragment.evidence_refs if ref.ref_type == "artifact"
                )
                if fragment.metadata.get("proposal_id"):
                    projection.proposal_refs.append(str(fragment.metadata["proposal_id"]))
        elif query.layer is MemoryLayer.SOR:
            record = await self._store.get_sor(query.record_id)
            if record is not None:
                projection.artifact_refs.extend(
                    ref.ref_id for ref in record.evidence_refs if ref.ref_type == "artifact"
                )
                projection.fragment_refs.extend(
                    ref.ref_id for ref in record.evidence_refs if ref.ref_type == "fragment"
                )
                if record.metadata.get("proposal_id"):
                    projection.proposal_refs.append(str(record.metadata["proposal_id"]))
        elif query.layer is MemoryLayer.VAULT:
            record = await self._store.get_vault(query.record_id)
            if record is not None:
                projection.artifact_refs.extend(
                    ref.ref_id for ref in record.evidence_refs if ref.ref_type == "artifact"
                )
                projection.fragment_refs.extend(
                    ref.ref_id for ref in record.evidence_refs if ref.ref_type == "fragment"
                )
        else:
            maintenance = await self._store.get_maintenance_run(query.record_id)
            if maintenance is not None:
                projection.maintenance_run_refs.append(maintenance.run_id)
                projection.fragment_refs.extend(maintenance.fragment_refs)
                projection.proposal_refs.extend(maintenance.proposal_refs)
                projection.derived_refs.extend(maintenance.derived_refs)
        return projection

    async def run_maintenance(
        self,
        command: MemoryMaintenanceCommand,
    ) -> MemoryMaintenanceRun:
        now = datetime.now(UTC)
        pending_backlog = await self._store.count_pending_sync_backlog()
        status = MemoryMaintenanceRunStatus.DEGRADED
        error_summary = "sqlite backend 不支持高级 maintenance，仅保留本地 fallback 能力"
        metadata: dict[str, Any] = {"pending_backlog": pending_backlog}

        if command.kind is MemoryMaintenanceCommandKind.REINDEX:
            status = MemoryMaintenanceRunStatus.COMPLETED
            error_summary = ""
            metadata["reindex_mode"] = "metadata-only"
        elif command.kind in {
            MemoryMaintenanceCommandKind.REPLAY,
            MemoryMaintenanceCommandKind.SYNC_RESUME,
        }:
            metadata["replay_required"] = pending_backlog > 0

        return MemoryMaintenanceRun(
            run_id=f"sqlite-maintenance:{command.command_id}",
            command_id=command.command_id,
            kind=command.kind,
            scope_id=command.scope_id,
            partition=command.partition,
            status=status,
            backend_used=self.backend_id,
            error_summary=error_summary,
            metadata=metadata,
            started_at=now,
            finished_at=now,
            backend_state=(
                MemoryBackendState.HEALTHY
                if status is MemoryMaintenanceRunStatus.COMPLETED
                else MemoryBackendState.DEGRADED
            ),
        )

    @staticmethod
    def _metadata_str(item: MemoryIngestItem, key: str) -> str:
        value = item.metadata.get(key, "")
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        return str(value).strip()

    def _extract_item_text(self, item: MemoryIngestItem) -> str:
        for key in (
            "text",
            "extractor_text",
            "ocr_text",
            "transcript",
            "summary",
            "caption",
            "content",
        ):
            value = self._metadata_str(item, key)
            if value:
                return value
        snippets = item.metadata.get("snippets")
        if isinstance(snippets, list):
            joined = " ".join(str(part).strip() for part in snippets if str(part).strip())
            if joined:
                return joined
        if item.modality == "text" and item.content_ref:
            return item.content_ref
        return ""

    def _build_derived_records(
        self,
        *,
        batch: MemoryIngestBatch,
        item: MemoryIngestItem,
        fragment_id: str,
        item_artifacts: list[str],
        content: str,
        created_at: datetime,
    ) -> list[DerivedMemoryRecord]:
        subject_key = self._metadata_str(item, "subject_key")
        records: list[DerivedMemoryRecord] = []
        category = self._metadata_str(item, "category") or batch.partition.value
        records.append(
            DerivedMemoryRecord(
                derived_id=f"derived:{batch.ingest_id}:{item.item_id}:category",
                scope_id=batch.scope_id,
                partition=batch.partition,
                derived_type="category",
                subject_key=subject_key,
                summary=f"{item.modality} -> {category}",
                payload={
                    "category": category,
                    "modality": item.modality,
                    "ingest_id": batch.ingest_id,
                },
                confidence=0.55,
                source_fragment_refs=[fragment_id],
                source_artifact_refs=item_artifacts,
                created_at=created_at,
            )
        )

        entity = self._metadata_str(item, "entity") or subject_key
        if entity:
            records.append(
                DerivedMemoryRecord(
                    derived_id=f"derived:{batch.ingest_id}:{item.item_id}:entity",
                    scope_id=batch.scope_id,
                    partition=batch.partition,
                    derived_type="entity",
                    subject_key=subject_key or entity,
                    summary=f"entity: {entity}",
                    payload={"entity": entity, "excerpt": content[:240]},
                    confidence=0.68,
                    source_fragment_refs=[fragment_id],
                    source_artifact_refs=item_artifacts,
                    created_at=created_at,
                )
            )

        relation = self._metadata_str(item, "relation")
        target = self._metadata_str(item, "relation_target")
        if relation and target:
            records.append(
                DerivedMemoryRecord(
                    derived_id=f"derived:{batch.ingest_id}:{item.item_id}:relation",
                    scope_id=batch.scope_id,
                    partition=batch.partition,
                    derived_type="relation",
                    subject_key=subject_key,
                    summary=f"{subject_key or item.item_id} {relation} {target}",
                    payload={
                        "relation": relation,
                        "target": target,
                    },
                    confidence=0.62,
                    source_fragment_refs=[fragment_id],
                    source_artifact_refs=item_artifacts,
                    created_at=created_at,
                )
            )

        tom_summary = (
            self._metadata_str(item, "tom_summary")
            or self._metadata_str(item, "intent")
            or self._metadata_str(item, "speaker_state")
        )
        if tom_summary:
            records.append(
                DerivedMemoryRecord(
                    derived_id=f"derived:{batch.ingest_id}:{item.item_id}:tom",
                    scope_id=batch.scope_id,
                    partition=batch.partition,
                    derived_type="tom",
                    subject_key=subject_key,
                    summary=tom_summary[:240],
                    payload={
                        "intent": self._metadata_str(item, "intent"),
                        "speaker_state": self._metadata_str(item, "speaker_state"),
                        "belief": self._metadata_str(item, "belief"),
                    },
                    confidence=0.58,
                    source_fragment_refs=[fragment_id],
                    source_artifact_refs=item_artifacts,
                    created_at=created_at,
                )
            )
        return records

    def _build_proposal_draft(
        self,
        *,
        batch: MemoryIngestBatch,
        item: MemoryIngestItem,
        fragment_id: str,
        item_artifacts: list[str],
        content: str,
        derived_ids: list[str],
    ) -> WriteProposalDraft | None:
        subject_key = (
            self._metadata_str(item, "proposal_subject_key")
            or self._metadata_str(item, "subject_key")
        )
        if not subject_key:
            return None
        partition_name = (
            self._metadata_str(item, "proposal_partition") or batch.partition.value
        )
        try:
            partition = MemoryPartition(partition_name)
        except ValueError:
            partition = batch.partition
        rationale = (
            self._metadata_str(item, "proposal_rationale")
            or f"{item.modality} ingest derived candidate"
        )
        confidence_raw = item.metadata.get("proposal_confidence", 0.65)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.65
        return WriteProposalDraft(
            subject_key=subject_key,
            partition=partition,
            content=self._metadata_str(item, "proposal_content") or content[:1000],
            rationale=rationale,
            confidence=max(0.0, min(1.0, confidence)),
            evidence_refs=[
                *[
                    EvidenceRef(
                        ref_id=artifact_ref,
                        ref_type="artifact",
                        snippet=content[:120],
                    )
                    for artifact_ref in item_artifacts
                ],
                EvidenceRef(
                    ref_id=fragment_id,
                    ref_type="fragment",
                    snippet=content[:120],
                ),
            ],
            metadata={
                "ingest_id": batch.ingest_id,
                "item_id": item.item_id,
                "modality": item.modality,
                "derived_refs": derived_ids,
                "project_id": batch.project_id,
                "workspace_id": batch.workspace_id,
                "candidate_engine": "builtin-memory-engine",
                "candidate_contract_version": "1.0.0",
                "candidate_kind": (
                    "vault_candidate"
                    if partition in SENSITIVE_PARTITIONS
                    else "fact_candidate"
                ),
            },
        )

    @staticmethod
    def _to_sor_hit(record: SorRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.memory_id,
            layer=MemoryLayer.SOR,
            scope_id=record.scope_id,
            partition=record.partition,
            subject_key=record.subject_key,
            summary=record.content[:160],
            version=record.version,
            status=record.status.value,
            created_at=record.updated_at,
            metadata={"schema_version": record.schema_version},
        )

    @staticmethod
    def _to_fragment_hit(record: FragmentRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.fragment_id,
            layer=MemoryLayer.FRAGMENT,
            scope_id=record.scope_id,
            partition=record.partition,
            summary=record.content[:160],
            created_at=record.created_at,
            metadata={"schema_version": record.schema_version},
        )

    @staticmethod
    def _to_vault_hit(record: VaultRecord) -> MemorySearchHit:
        return MemorySearchHit(
            record_id=record.vault_id,
            layer=MemoryLayer.VAULT,
            scope_id=record.scope_id,
            partition=record.partition,
            subject_key=record.subject_key,
            summary=record.summary[:160],
            created_at=record.created_at,
            metadata={"schema_version": record.schema_version},
        )
