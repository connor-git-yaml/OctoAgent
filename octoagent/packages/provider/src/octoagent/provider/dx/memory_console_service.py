"""Feature 027: Memory Console / Vault 授权桥接服务。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from octoagent.core.models import (
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    MemoryConsoleDocument,
    MemoryConsoleFilter,
    MemoryConsoleSummary,
    MemoryProposalAuditDocument,
    MemoryProposalAuditItem,
    MemoryProposalSummary,
    MemoryRecordProjection,
    MemorySubjectHistoryDocument,
    Project,
    ProjectBindingType,
    VaultAccessGrantItem,
    VaultAccessRequestItem,
    VaultAuthorizationDocument,
    VaultRetrievalAuditItem,
    Workspace,
)
from octoagent.memory import (
    SENSITIVE_PARTITIONS,
    DerivedMemoryQuery,
    MemoryBackendStatus,
    MemoryLayer,
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRun,
    MemoryPartition,
    MemoryService,
    ProposalStatus,
    SqliteMemoryStore,
    VaultAccessDecision,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
    init_memory_db,
)
from ulid import ULID

from .backup_service import resolve_project_root
from .memory_backend_resolver import MemoryBackendResolver

_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}


@dataclass(slots=True)
class _BoundScope:
    scope_id: str
    workspace_id: str | None
    binding_type: ProjectBindingType


@dataclass(slots=True)
class _MemoryContext:
    project: Project
    workspace: Workspace | None
    scope_bindings: dict[str, _BoundScope]
    selected_scope_ids: list[str]
    warnings: list[str]
    blocking_issues: list[str]


@dataclass(slots=True)
class MemoryPermissionDecision:
    allowed: bool
    reason_code: str
    message: str
    project_id: str = ""
    workspace_id: str = ""
    scope_id: str = ""


class MemoryConsoleError(RuntimeError):
    """Memory Console 结构化错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MemoryConsoleService:
    """基于 Project/Workspace 绑定产出 Memory Console 文档与动作结果。"""

    def __init__(self, project_root: Path, *, store_group) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._memory_store = SqliteMemoryStore(store_group.conn)
        self._memory = MemoryService(store_group.conn, store=self._memory_store)
        self._backend_resolver = MemoryBackendResolver(
            self._project_root,
            store_group=store_group,
        )

    async def ensure_ready(self) -> None:
        await init_memory_db(self._stores.conn)

    async def get_backend_status(
        self,
        *,
        project_id: str = "",
        workspace_id: str | None = None,
    ) -> MemoryBackendStatus:
        """返回底层 memory backend 状态。"""
        context = await self._resolve_context(
            active_project_id=project_id or "",
            active_workspace_id=workspace_id or "",
            project_id=project_id or "",
            workspace_id=workspace_id or "",
        )
        memory = await self._memory_service_for_context(context)
        return await memory.get_backend_status()

    async def get_memory_console(
        self,
        *,
        project_id: str = "",
        workspace_id: str | None = None,
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        layer: MemoryLayer | None = None,
        query: str | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
        limit: int = 50,
    ) -> MemoryConsoleDocument:
        return await self.get_overview(
            active_project_id=project_id or "",
            active_workspace_id=workspace_id or "",
            project_id=project_id or "",
            workspace_id=workspace_id or "",
            scope_id=scope_id or "",
            partition=partition.value if partition else "",
            layer=layer.value if layer else "",
            query=query or "",
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
        )

    async def get_memory_subject_history(
        self,
        subject_key: str,
        *,
        project_id: str = "",
        workspace_id: str | None = None,
        scope_id: str | None = None,
    ) -> MemorySubjectHistoryDocument:
        return await self.get_subject_history(
            subject_key=subject_key,
            active_project_id=project_id or "",
            active_workspace_id=workspace_id or "",
            project_id=project_id or "",
            workspace_id=workspace_id or "",
            scope_id=scope_id or "",
        )

    async def run_maintenance(
        self,
        *,
        kind: MemoryMaintenanceCommandKind,
        project_id: str = "",
        workspace_id: str | None = None,
        scope_id: str = "",
        partition: MemoryPartition | None = None,
        reason: str = "",
        summary: str = "",
        requested_by: str = "",
        evidence_refs=None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryMaintenanceRun:
        """执行 project/workspace 绑定后的 memory maintenance。"""

        context = await self._resolve_context(
            active_project_id=project_id or "",
            active_workspace_id=workspace_id or "",
            project_id=project_id or "",
            workspace_id=workspace_id or "",
            scope_id=scope_id,
        )
        memory = await self._memory_service_for_context(context)
        resolved_scope_id = scope_id or (
            context.selected_scope_ids[0] if context.selected_scope_ids else ""
        )
        return await memory.run_memory_maintenance(
            MemoryMaintenanceCommand(
                command_id=str(ULID()),
                kind=kind,
                scope_id=resolved_scope_id,
                partition=partition,
                reason=reason,
                requested_by=requested_by,
                summary=summary,
                evidence_refs=list(evidence_refs or []),
                metadata=metadata or {},
            )
        )

    async def get_overview(
        self,
        *,
        active_project_id: str,
        active_workspace_id: str,
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str = "",
        partition: str = "",
        layer: str = "",
        query: str = "",
        include_history: bool = False,
        include_vault_refs: bool = False,
        limit: int = 50,
    ) -> MemoryConsoleDocument:
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
        memory = await self._memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        records: list[MemoryRecordProjection] = []
        summary = MemoryConsoleSummary(scope_count=len(context.selected_scope_ids))
        for scope in context.selected_scope_ids:
            bound = context.scope_bindings.get(scope)
            record_workspace_id = (
                bound.workspace_id or (context.workspace.workspace_id if context.workspace else "")
            )
            if layer in {"", "fragment"}:
                fragments = await self._memory_store.list_fragments(
                    scope,
                    query=query or None,
                    limit=limit,
                )
                for fragment in fragments:
                    if partition and fragment.partition.value != partition:
                        continue
                    records.append(
                        self._fragment_projection(
                            fragment=fragment,
                            project_id=context.project.project_id,
                            workspace_id=record_workspace_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )
                    summary.fragment_count += 1
            if layer in {"", "sor"}:
                sor_records = await self._memory_store.search_sor(
                    scope,
                    query=query or None,
                    include_history=include_history,
                    limit=limit,
                )
                for sor in sor_records:
                    if partition and sor.partition.value != partition:
                        continue
                    if sor.status == "current":
                        summary.sor_current_count += 1
                    else:
                        summary.sor_history_count += 1
                    records.append(
                        self._sor_projection(
                            sor=sor,
                            project_id=context.project.project_id,
                            workspace_id=record_workspace_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )
            if include_vault_refs and layer in {"", "vault"}:
                vault_records = await self._memory_store.search_vault(
                    scope,
                    query=query or None,
                    limit=limit,
                )
                for vault in vault_records:
                    if partition and vault.partition.value != partition:
                        continue
                    summary.vault_ref_count += 1
                    records.append(
                        self._vault_projection(
                            vault=vault,
                            project_id=context.project.project_id,
                            workspace_id=record_workspace_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )
            if layer in {"", "derived"}:
                derived_projection = await memory.list_derived_memory(
                    DerivedMemoryQuery(
                        scope_id=scope,
                        partition=MemoryPartition(partition) if partition else None,
                        limit=limit,
                    )
                )
                for derived in derived_projection.items:
                    if query and not self._derived_matches_query(derived, query):
                        continue
                    records.append(
                        self._derived_projection(
                            derived=derived,
                            project_id=context.project.project_id,
                            workspace_id=record_workspace_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )

        proposals = await memory.list_proposals(
            scope_ids=context.selected_scope_ids,
            limit=limit,
        )
        summary.proposal_count = len(proposals)
        summary.pending_replay_count = backend_status.pending_replay_count
        records.sort(key=self._projection_sort_key, reverse=True)
        records = records[:limit]
        available_partitions = sorted({item.partition for item in records})
        available_layers = sorted({item.layer for item in records})
        warnings = list(context.warnings)
        status = "ready" if not context.blocking_issues else "degraded"
        if backend_status.state.value != "healthy":
            warnings.append(
                backend_status.message
                or f"memory backend 当前状态为 {backend_status.state.value}"
            )
            status = "degraded"
        warnings.extend(context.blocking_issues)
        return MemoryConsoleDocument(
            status=status,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(
                    context.warnings
                    or context.blocking_issues
                    or backend_status.state.value != "healthy"
                ),
                reasons=(
                    context.blocking_issues
                    or context.warnings
                    or (
                        [backend_status.state.value]
                        if backend_status.state.value != "healthy"
                        else []
                    )
                ),
                unavailable_sections=[],
            ),
            warnings=warnings,
            active_project_id=context.project.project_id,
            active_workspace_id=context.workspace.workspace_id if context.workspace else "",
            backend_id=backend_status.backend_id,
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            index_health=self._backend_index_health(backend_status),
            filters=MemoryConsoleFilter(
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else "",
                scope_id=scope_id,
                partition=partition,
                layer=layer,
                query=query,
                include_history=include_history,
                include_vault_refs=include_vault_refs,
                limit=limit,
            ),
            summary=summary,
            records=records,
            available_scopes=context.selected_scope_ids,
            available_partitions=available_partitions,
            available_layers=available_layers or ["fragment", "sor", "vault", "derived"],
            advanced_refs={
                "backend_diagnostics": "/api/control/resources/diagnostics",
                "memory_console": "/api/control/resources/memory",
                "maintenance_actions": "/api/control/actions",
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="memory.query",
                    label="查询 Memory",
                    action_id="memory.query",
                ),
                ControlPlaneCapability(
                    capability_id="memory.export.inspect",
                    label="检查导出范围",
                    action_id="memory.export.inspect",
                ),
            ],
            refs={
                "subject_history": "/api/control/resources/memory-subjects/{subject_key}",
                "proposal_audit": "/api/control/resources/memory-proposals",
                "vault_authorization": "/api/control/resources/vault-authorization",
            },
        )

    async def get_subject_history(
        self,
        *,
        subject_key: str,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str = "",
    ) -> MemorySubjectHistoryDocument:
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
        memory = await self._memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        history: list[MemoryRecordProjection] = []
        current_record: MemoryRecordProjection | None = None
        warnings = list(context.warnings)
        latest_proposal_refs: list[str] = []
        for scope in context.selected_scope_ids:
            bound = context.scope_bindings.get(scope)
            record_workspace_id = (
                bound.workspace_id or (context.workspace.workspace_id if context.workspace else "")
            )
            sor_history = await self._memory_store.list_sor_history(scope, subject_key)
            for sor in sor_history:
                projection = self._sor_projection(
                    sor=sor,
                    project_id=context.project.project_id,
                    workspace_id=record_workspace_id,
                    retrieval_backend=backend_status.active_backend,
                )
                history.append(projection)
                if sor.status == "current" and current_record is None:
                    current_record = projection
                latest_proposal_refs.extend(projection.proposal_refs)
        history.sort(key=self._projection_sort_key, reverse=True)
        if len({item.scope_id for item in history}) > 1:
            warnings.append("subject_key 命中了多个 scope，已合并显示历史。")
        return MemorySubjectHistoryDocument(
            resource_id=f"memory-subject:{subject_key}",
            active_project_id=context.project.project_id,
            active_workspace_id=context.workspace.workspace_id if context.workspace else "",
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            index_health=self._backend_index_health(backend_status),
            scope_id=scope_id,
            subject_key=subject_key,
            current_record=current_record,
            history=history,
            latest_proposal_refs=sorted(set(latest_proposal_refs)),
            warnings=warnings + context.blocking_issues,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(warnings or context.blocking_issues),
                reasons=warnings + context.blocking_issues,
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="memory.subject.inspect",
                    label="查看 Subject 历史",
                    action_id="memory.subject.inspect",
                )
            ],
        )

    async def get_proposal_audit(
        self,
        *,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str = "",
        status: ProposalStatus | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> MemoryProposalAuditDocument:
        _ = source
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
        memory = await self._memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        statuses = [status] if status else None
        proposals = await memory.list_proposals(
            scope_ids=context.selected_scope_ids,
            statuses=statuses,
            limit=limit,
        )
        summary = MemoryProposalSummary()
        items: list[MemoryProposalAuditItem] = []
        for proposal in proposals:
            setattr(summary, proposal.status.value, getattr(summary, proposal.status.value) + 1)
            items.append(
                MemoryProposalAuditItem(
                    proposal_id=proposal.proposal_id,
                    scope_id=proposal.scope_id,
                    partition=proposal.partition.value,
                    action=proposal.action.value,
                    subject_key=proposal.subject_key or "",
                    status=proposal.status.value,
                    confidence=proposal.confidence,
                    rationale=proposal.rationale,
                    is_sensitive=proposal.is_sensitive,
                    evidence_refs=[
                        item.model_dump(mode="json") for item in proposal.evidence_refs
                    ],
                    created_at=proposal.created_at,
                    validated_at=proposal.validated_at,
                    committed_at=proposal.committed_at,
                    metadata=proposal.metadata,
                )
            )
        return MemoryProposalAuditDocument(
            active_project_id=context.project.project_id,
            active_workspace_id=context.workspace.workspace_id if context.workspace else "",
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            summary=summary,
            items=items,
            warnings=context.warnings + context.blocking_issues,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(context.warnings or context.blocking_issues),
                reasons=context.warnings + context.blocking_issues,
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="memory.proposal.inspect",
                    label="查看 WriteProposal 审计",
                    action_id="memory.proposal.inspect",
                )
            ],
        )

    async def get_vault_authorization(
        self,
        *,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str = "",
        subject_key: str = "",
    ) -> VaultAuthorizationDocument:
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
        memory = await self._memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        requests = await memory.list_vault_access_requests(
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else None,
            scope_ids=context.selected_scope_ids,
            subject_key=subject_key or None,
            limit=50,
        )
        grants = await memory.list_vault_access_grants(
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else None,
            scope_ids=context.selected_scope_ids,
            subject_key=subject_key or None,
            limit=50,
        )
        active_grants = [await self._normalize_grant(item) for item in grants]
        retrievals = await memory.list_vault_retrieval_audits(
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else None,
            scope_ids=context.selected_scope_ids,
            subject_key=subject_key or None,
            limit=50,
        )
        return VaultAuthorizationDocument(
            active_project_id=context.project.project_id,
            active_workspace_id=context.workspace.workspace_id if context.workspace else "",
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            active_requests=[self._request_item(item) for item in requests],
            active_grants=[
                self._grant_item(item)
                for item in active_grants
                if item.status is VaultAccessGrantStatus.ACTIVE
            ],
            recent_retrievals=[self._retrieval_item(item) for item in retrievals],
            warnings=context.warnings + context.blocking_issues,
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(context.warnings or context.blocking_issues),
                reasons=context.warnings + context.blocking_issues,
            ),
            capabilities=[
                ControlPlaneCapability(
                    capability_id="vault.access.request",
                    label="申请 Vault 授权",
                    action_id="vault.access.request",
                ),
                ControlPlaneCapability(
                    capability_id="vault.access.resolve",
                    label="审批 Vault 授权",
                    action_id="vault.access.resolve",
                ),
                ControlPlaneCapability(
                    capability_id="vault.retrieve",
                    label="检索 Vault",
                    action_id="vault.retrieve",
                ),
            ],
        )

    async def request_vault_access(
        self,
        *,
        actor_id: str,
        actor_label: str,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str,
        partition: str = "",
        subject_key: str = "",
        reason: str = "",
    ):
        if not scope_id:
            return None, MemoryPermissionDecision(
                allowed=False,
                reason_code="MEMORY_PERMISSION_SCOPE_UNBOUND",
                message="vault.access.request 需要明确 scope_id。",
            )
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
        decision = self._decide_project_scope_action(
            action_id="vault.access.request",
            actor_id=actor_id,
            context=context,
            required_scope_id=scope_id,
        )
        if not decision.allowed:
            return None, decision
        request = await self._memory.create_vault_access_request(
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else None,
            scope_id=scope_id,
            partition=MemoryPartition(partition) if partition else None,
            subject_key=subject_key or None,
            requester_actor_id=actor_id,
            requester_actor_label=actor_label,
            reason=reason,
        )
        return request, decision

    async def resolve_vault_access(
        self,
        *,
        actor_id: str,
        request_id: str,
        decision: VaultAccessDecision,
        actor_label: str = "",
        expires_in_seconds: int = 0,
    ):
        request = await self._memory_store.get_vault_access_request(request_id)
        if request is None:
            raise MemoryConsoleError(
                "VAULT_ACCESS_REQUEST_NOT_FOUND",
                "Vault 授权申请不存在。",
            )
        context = await self._resolve_context(
            active_project_id=request.project_id,
            active_workspace_id=request.workspace_id or "",
            project_id=request.project_id,
            workspace_id=request.workspace_id or "",
            scope_id=request.scope_id,
        )
        permission = self._decide_operator_only(
            action_id="vault.access.resolve",
            actor_id=actor_id,
            context=context,
            required_scope_id=request.scope_id,
        )
        if not permission.allowed:
            raise MemoryConsoleError("VAULT_ACCESS_RESOLVE_NOT_ALLOWED", permission.message)
        if request.status is not VaultAccessRequestStatus.PENDING:
            raise MemoryConsoleError(
                "VAULT_ACCESS_REQUEST_ALREADY_RESOLVED",
                "Vault 授权申请已经处理过。",
            )
        resolved_request, grant = await self._memory.resolve_vault_access_request(
            request_id,
            decision=decision,
            granted_by_actor_id=actor_id,
            granted_by_actor_label=actor_label or actor_id,
            expires_at=(
                datetime.now(tz=UTC) + timedelta(seconds=expires_in_seconds)
                if expires_in_seconds > 0
                else None
            ),
        )
        return resolved_request, grant

    async def retrieve_vault(
        self,
        *,
        actor_id: str,
        actor_label: str,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str,
        partition: str = "",
        subject_key: str = "",
        query: str = "",
        grant_id: str = "",
        limit: int = 20,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        if not scope_id:
            return (
                "VAULT_AUTHORIZATION_SCOPE_MISMATCH",
                {},
                MemoryPermissionDecision(
                    allowed=False,
                    reason_code="MEMORY_PERMISSION_SCOPE_UNBOUND",
                    message="vault.retrieve 需要明确 scope_id。",
                ),
            )
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
        )
        decision = self._decide_project_scope_action(
            action_id="vault.retrieve",
            actor_id=actor_id,
            context=context,
            required_scope_id=scope_id,
        )
        if not decision.allowed:
            await self._memory.record_vault_retrieval_audit(
                actor_id=actor_id,
                actor_label=actor_label,
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else None,
                scope_id=scope_id,
                partition=MemoryPartition(partition) if partition else None,
                subject_key=subject_key or None,
                query=query or None,
                reason_code=decision.reason_code,
                authorized=False,
            )
            return "VAULT_RETRIEVE_NOT_ALLOWED", {}, decision

        grant, grant_code, grant_message = await self._resolve_grant_for_retrieval(
            actor_id=actor_id,
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else None,
            scope_id=scope_id,
            partition=MemoryPartition(partition) if partition else None,
            subject_key=subject_key or None,
            grant_id=grant_id or None,
        )
        if grant is None:
            await self._memory.record_vault_retrieval_audit(
                actor_id=actor_id,
                actor_label=actor_label,
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else None,
                scope_id=scope_id,
                partition=MemoryPartition(partition) if partition else None,
                subject_key=subject_key or None,
                query=query or None,
                reason_code=grant_code,
                authorized=False,
            )
            denied = MemoryPermissionDecision(
                allowed=False,
                reason_code=grant_code,
                message=grant_message,
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else "",
                scope_id=scope_id,
            )
            return grant_code, {}, denied

        vault_records = await self._memory_store.search_vault(
            scope_id,
            query=query or subject_key or None,
            limit=limit,
        )
        results = []
        matched_vault_ids: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        for vault in vault_records:
            if partition and vault.partition.value != partition:
                continue
            if subject_key and vault.subject_key != subject_key:
                continue
            matched_vault_ids.append(vault.vault_id)
            evidence_refs.extend([item.model_dump(mode="json") for item in vault.evidence_refs])
            results.append(
                {
                    "vault_id": vault.vault_id,
                    "scope_id": vault.scope_id,
                    "partition": vault.partition.value,
                    "subject_key": vault.subject_key,
                    "summary": vault.summary,
                    "content_ref": vault.content_ref,
                    "evidence_refs": [
                        item.model_dump(mode="json") for item in vault.evidence_refs
                    ],
                    "metadata": vault.metadata,
                }
            )
        await self._memory.record_vault_retrieval_audit(
            actor_id=actor_id,
            actor_label=actor_label,
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else None,
            scope_id=scope_id,
            partition=MemoryPartition(partition) if partition else None,
            subject_key=subject_key or None,
            query=query or None,
            grant_id=grant.grant_id,
            reason_code="MEMORY_PERMISSION_ALLOWED",
            authorized=True,
            result_count=len(results),
            retrieved_vault_ids=matched_vault_ids,
            evidence_refs=[],
        )
        return (
            "VAULT_RETRIEVE_AUTHORIZED",
            {"results": results, "grant_id": grant.grant_id},
            decision,
        )

    async def inspect_export(
        self,
        *,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        scope_ids: list[str] | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        decision = self._decide_project_scope_action(
            action_id="memory.export.inspect",
            actor_id="system:memory-export",
            context=context,
            required_scope_id=(scope_ids or [None])[0],
            bypass_actor_check=True,
        )
        if not decision.allowed:
            return "MEMORY_EXPORT_INSPECTION_NOT_ALLOWED", {}, decision
        selected_scope_ids = scope_ids or context.selected_scope_ids
        counts = {
            "fragments": 0,
            "sor_current": 0,
            "sor_history": 0,
            "vault_refs": 0,
            "proposals": 0,
        }
        sensitive_partitions: set[str] = set()
        for scope in selected_scope_ids:
            fragments = await self._memory_store.list_fragments(scope, limit=200)
            counts["fragments"] += len(fragments)
            sor_records = await self._memory_store.search_sor(
                scope,
                include_history=include_history,
                limit=200,
            )
            for sor in sor_records:
                if sor.status == "current":
                    counts["sor_current"] += 1
                else:
                    counts["sor_history"] += 1
                if sor.partition in SENSITIVE_PARTITIONS:
                    sensitive_partitions.add(sor.partition.value)
            if include_vault_refs:
                vault_records = await self._memory_store.search_vault(scope, limit=200)
                counts["vault_refs"] += len(vault_records)
                for vault in vault_records:
                    if vault.partition in SENSITIVE_PARTITIONS:
                        sensitive_partitions.add(vault.partition.value)
        counts["proposals"] = len(
            await self._memory.list_proposals(scope_ids=selected_scope_ids, limit=200)
        )
        payload = {
            "inspection_id": str(ULID()),
            "counts": counts,
            "sensitive_partitions": sorted(sensitive_partitions),
            "warnings": context.warnings,
            "blocking_issues": context.blocking_issues,
            "export_refs": [
                {
                    "project_id": context.project.project_id,
                    "workspace_id": context.scope_bindings.get(scope_id).workspace_id
                    if context.scope_bindings.get(scope_id) is not None
                    else "",
                    "scope_id": scope_id,
                }
                for scope_id in selected_scope_ids
            ],
        }
        code = (
            "MEMORY_EXPORT_INSPECTION_BLOCKED"
            if payload["blocking_issues"]
            else "MEMORY_EXPORT_INSPECTION_READY"
        )
        return code, payload, decision

    async def verify_restore(
        self,
        *,
        actor_id: str,
        active_project_id: str = "",
        active_workspace_id: str = "",
        project_id: str = "",
        workspace_id: str = "",
        snapshot_ref: str,
        target_scope_mode: str = "current_project",
        scope_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        context = await self._resolve_context(
            active_project_id=active_project_id,
            active_workspace_id=active_workspace_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        permission = self._decide_operator_only(
            action_id="memory.restore.verify",
            actor_id=actor_id,
            context=context,
        )
        if not permission.allowed:
            return "MEMORY_RESTORE_VERIFICATION_NOT_ALLOWED", {}, permission

        snapshot_path = Path(snapshot_ref).expanduser()
        if not snapshot_path.is_absolute():
            snapshot_path = (self._project_root / snapshot_path).resolve()
        else:
            snapshot_path = snapshot_path.resolve()

        warnings: list[str] = list(context.warnings)
        blocking_issues: list[str] = list(context.blocking_issues)
        schema_ok = False
        snapshot_payload: dict[str, Any] = {}
        if not snapshot_path.exists():
            blocking_issues.append(f"snapshot 不存在: {snapshot_path}")
        elif snapshot_path.suffix.lower() == ".json":
            snapshot_payload, schema_ok, parse_warning = self._load_memory_snapshot_json(
                snapshot_path
            )
            if parse_warning:
                warnings.append(parse_warning)
        elif snapshot_path.suffix.lower() == ".zip":
            warnings.append("bundle 校验仅做 manifest/entries 检查，未发现专用 memory snapshot。")
            schema_ok = self._bundle_contains_memory_refs(snapshot_path)
            if not schema_ok:
                blocking_issues.append("bundle 未包含可识别的 memory snapshot/manifest。")
        else:
            blocking_issues.append("仅支持 .json 或 .zip 的 memory snapshot/bundle 校验。")

        target_scopes = scope_ids or context.selected_scope_ids
        scope_conflicts: list[str] = []
        if target_scope_mode == "current_project":
            bound_scope_ids = set(context.scope_bindings.keys())
            for scope in target_scopes:
                if scope not in bound_scope_ids:
                    scope_conflicts.append(f"scope 未绑定到当前 project: {scope}")

        subject_conflicts: list[str] = []
        grant_conflicts: list[str] = []
        for item in snapshot_payload.get("records", []):
            if item.get("layer") != "sor" or item.get("status") != "current":
                continue
            item_scope_id = str(item.get("scope_id", ""))
            item_subject = str(item.get("subject_key", ""))
            if not item_scope_id or not item_subject:
                continue
            current = await self._memory_store.get_current_sor(item_scope_id, item_subject)
            if current is not None:
                subject_conflicts.append(
                    f"{item_scope_id}:{item_subject} 已存在 current version={current.version}"
                )
        for item in snapshot_payload.get("grants", []):
            item_scope_id = str(item.get("scope_id", ""))
            item_subject = str(item.get("subject_key", ""))
            item_actor_id = str(item.get("granted_to_actor_id", ""))
            if not item_scope_id or not item_actor_id:
                continue
            existing = await self._memory.list_vault_access_grants(
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else None,
                scope_ids=[item_scope_id],
                subject_key=item_subject or None,
                actor_id=item_actor_id,
                statuses=[VaultAccessGrantStatus.ACTIVE],
                limit=10,
            )
            if existing:
                grant_conflicts.append(
                    f"{item_actor_id}:{item_scope_id}:{item_subject or '*'} 已存在 active grant"
                )

        payload = {
            "verification_id": str(ULID()),
            "schema_ok": schema_ok,
            "subject_conflicts": subject_conflicts,
            "grant_conflicts": grant_conflicts,
            "scope_conflicts": scope_conflicts,
            "warnings": warnings,
            "blocking_issues": blocking_issues,
        }
        code = (
            "MEMORY_RESTORE_VERIFICATION_BLOCKED"
            if (
                not schema_ok
                or subject_conflicts
                or grant_conflicts
                or scope_conflicts
                or blocking_issues
            )
            else "MEMORY_RESTORE_VERIFICATION_READY"
        )
        return code, payload, permission

    async def _resolve_context(
        self,
        *,
        active_project_id: str,
        active_workspace_id: str,
        project_id: str = "",
        workspace_id: str = "",
        scope_id: str = "",
    ) -> _MemoryContext:
        project_ref = project_id or active_project_id
        project = (
            await self._stores.project_store.get_project(project_ref)
            if project_ref
            else await self._stores.project_store.get_default_project()
        )
        if project is None:
            raise RuntimeError("当前没有可用 project。")
        workspace_ref = workspace_id or active_workspace_id
        workspace = (
            await self._stores.project_store.get_workspace(workspace_ref)
            if workspace_ref
            else await self._stores.project_store.get_primary_workspace(project.project_id)
        )
        if workspace is not None and workspace.project_id != project.project_id:
            workspace = await self._stores.project_store.get_primary_workspace(project.project_id)

        bindings = await self._stores.project_store.list_bindings(project.project_id)
        scope_bindings: dict[str, _BoundScope] = {}
        for binding in bindings:
            if binding.binding_type not in _MEMORY_BINDING_TYPES:
                continue
            if workspace is not None and binding.workspace_id not in {None, workspace.workspace_id}:
                continue
            scope_bindings[binding.binding_key] = _BoundScope(
                scope_id=binding.binding_key,
                workspace_id=binding.workspace_id,
                binding_type=binding.binding_type,
            )
        warnings: list[str] = []
        blocking_issues: list[str] = []
        if scope_id:
            if scope_id in scope_bindings:
                selected_scope_ids = [scope_id]
            else:
                selected_scope_ids = [scope_id]
                warnings.append(
                    f"scope {scope_id} 未绑定到当前 project，将按 orphan scope 只读显示。"
                )
        else:
            selected_scope_ids = sorted(scope_bindings.keys())
        if not selected_scope_ids:
            blocking_issues.append("当前 project/workspace 没有可用的 memory scope 绑定。")
        return _MemoryContext(
            project=project,
            workspace=workspace,
            scope_bindings=scope_bindings,
            selected_scope_ids=selected_scope_ids,
            warnings=warnings,
            blocking_issues=blocking_issues,
        )

    async def _memory_service_for_context(self, context: _MemoryContext) -> MemoryService:
        backend = await self._backend_resolver.resolve_backend(
            project=context.project,
            workspace=context.workspace,
        )
        return MemoryService(
            self._stores.conn,
            store=self._memory_store,
            backend=backend,
        )

    @staticmethod
    def _backend_index_health(backend_status: MemoryBackendStatus) -> dict[str, Any]:
        index_health = dict(backend_status.index_health)
        if backend_status.project_binding:
            index_health.setdefault("project_binding", backend_status.project_binding)
        if backend_status.last_ingest_at is not None:
            index_health.setdefault(
                "last_ingest_at",
                backend_status.last_ingest_at.isoformat(),
            )
        if backend_status.last_maintenance_at is not None:
            index_health.setdefault(
                "last_maintenance_at",
                backend_status.last_maintenance_at.isoformat(),
            )
        if backend_status.retry_after is not None:
            index_health.setdefault("retry_after", backend_status.retry_after.isoformat())
        return index_health

    def _decide_project_scope_action(
        self,
        *,
        action_id: str,
        actor_id: str,
        context: _MemoryContext,
        required_scope_id: str | None,
        bypass_actor_check: bool = False,
    ) -> MemoryPermissionDecision:
        if not context.project.project_id:
            return MemoryPermissionDecision(
                allowed=False,
                reason_code="MEMORY_PERMISSION_PROJECT_REQUIRED",
                message="memory 操作需要 project 上下文。",
            )
        if required_scope_id and required_scope_id not in context.scope_bindings:
            return MemoryPermissionDecision(
                allowed=False,
                reason_code="MEMORY_PERMISSION_SCOPE_UNBOUND",
                message=f"{action_id} 目标 scope 未绑定到当前 project。",
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else "",
                scope_id=required_scope_id,
            )
        if bypass_actor_check:
            return MemoryPermissionDecision(
                allowed=True,
                reason_code="MEMORY_PERMISSION_ALLOWED",
                message="允许访问。",
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else "",
                scope_id=required_scope_id or "",
            )
        if not actor_id:
            return MemoryPermissionDecision(
                allowed=False,
                reason_code="MEMORY_PERMISSION_OPERATOR_REQUIRED",
                message="缺少 actor 上下文。",
                project_id=context.project.project_id,
            )
        return MemoryPermissionDecision(
            allowed=True,
            reason_code="MEMORY_PERMISSION_ALLOWED",
            message="允许访问。",
            project_id=context.project.project_id,
            workspace_id=context.workspace.workspace_id if context.workspace else "",
            scope_id=required_scope_id or "",
        )

    def _decide_operator_only(
        self,
        *,
        action_id: str,
        actor_id: str,
        context: _MemoryContext,
        required_scope_id: str | None = None,
    ) -> MemoryPermissionDecision:
        decision = self._decide_project_scope_action(
            action_id=action_id,
            actor_id=actor_id,
            context=context,
            required_scope_id=required_scope_id,
        )
        if not decision.allowed:
            return decision
        if not (
            actor_id.startswith("user:")
            or actor_id.startswith("system:")
            or actor_id.startswith("cli:")
        ):
            return MemoryPermissionDecision(
                allowed=False,
                reason_code="MEMORY_PERMISSION_OPERATOR_REQUIRED",
                message=f"{action_id} 仅允许 owner/operator surface。",
                project_id=context.project.project_id,
                workspace_id=context.workspace.workspace_id if context.workspace else "",
                scope_id=required_scope_id or "",
            )
        return decision

    async def _resolve_grant_for_retrieval(
        self,
        *,
        actor_id: str,
        project_id: str,
        workspace_id: str | None,
        scope_id: str,
        partition: MemoryPartition | None,
        subject_key: str | None,
        grant_id: str | None,
    ):
        if grant_id:
            grant = await self._memory.get_vault_access_grant(grant_id)
            if grant is None:
                return None, "VAULT_AUTHORIZATION_REQUIRED", "未找到指定的 Vault grant。"
            normalized = await self._normalize_grant(grant)
            if normalized.status is VaultAccessGrantStatus.EXPIRED:
                return None, "VAULT_AUTHORIZATION_EXPIRED", "Vault grant 已过期。"
            if normalized.project_id != project_id or normalized.scope_id != scope_id:
                return (
                    None,
                    "VAULT_AUTHORIZATION_SCOPE_MISMATCH",
                    "Vault grant 与当前 scope 不匹配。",
                )
            if partition is not None and normalized.partition not in {None, partition}:
                return (
                    None,
                    "VAULT_AUTHORIZATION_SCOPE_MISMATCH",
                    "Vault grant 与当前 partition 不匹配。",
                )
            if subject_key and normalized.subject_key not in {"", subject_key}:
                return (
                    None,
                    "VAULT_AUTHORIZATION_SCOPE_MISMATCH",
                    "Vault grant 与当前 subject 不匹配。",
                )
            return normalized, "VAULT_RETRIEVE_AUTHORIZED", ""
        grant = await self._memory.get_latest_valid_vault_grant(
            actor_id=actor_id,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
        )
        if grant is None:
            grants = await self._memory.list_vault_access_grants(
                project_id=project_id,
                workspace_id=workspace_id,
                scope_ids=[scope_id],
                subject_key=subject_key,
                actor_id=actor_id,
                limit=20,
            )
            if any(
                item.expires_at is not None and item.expires_at <= datetime.now(tz=UTC)
                for item in grants
            ):
                return None, "VAULT_AUTHORIZATION_EXPIRED", "Vault grant 已过期。"
            return None, "VAULT_AUTHORIZATION_REQUIRED", "当前 actor 缺少有效 Vault grant。"
        return grant, "VAULT_RETRIEVE_AUTHORIZED", ""

    async def _normalize_grant(self, grant):
        if (
            grant.status is VaultAccessGrantStatus.ACTIVE
            and grant.expires_at is not None
            and grant.expires_at <= datetime.now(tz=UTC)
        ):
            expired = grant.model_copy(update={"status": VaultAccessGrantStatus.EXPIRED})
            await self._memory_store.replace_vault_access_grant(expired)
            await self._stores.conn.commit()
            return expired
        return grant

    def _fragment_projection(
        self,
        *,
        fragment,
        project_id: str,
        workspace_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=fragment.fragment_id,
            layer="fragment",
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=fragment.scope_id,
            partition=fragment.partition.value,
            summary=fragment.content[:240],
            created_at=fragment.created_at,
            evidence_refs=[item.model_dump(mode="json") for item in fragment.evidence_refs],
            metadata=fragment.metadata,
            retrieval_backend=retrieval_backend,
        )

    def _derived_projection(
        self,
        *,
        derived,
        project_id: str,
        workspace_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=derived.derived_id,
            layer="derived",
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=derived.scope_id,
            partition=derived.partition.value,
            subject_key=derived.subject_key,
            summary=derived.summary,
            status="derived",
            created_at=derived.created_at,
            evidence_refs=[
                {"ref_id": ref_id, "ref_type": "fragment"}
                for ref_id in derived.source_fragment_refs
            ]
            + [
                {"ref_id": ref_id, "ref_type": "artifact"}
                for ref_id in derived.source_artifact_refs
            ],
            derived_refs=[derived.derived_id],
            proposal_refs=[derived.proposal_ref] if derived.proposal_ref else [],
            metadata={
                "derived_type": derived.derived_type,
                "confidence": derived.confidence,
                **derived.payload,
            },
            retrieval_backend=retrieval_backend,
        )

    def _derived_matches_query(self, derived, query: str) -> bool:
        normalized = query.strip().lower()
        if not normalized:
            return True
        haystacks = [
            derived.derived_type,
            derived.subject_key,
            derived.summary,
            json.dumps(derived.payload, ensure_ascii=False),
        ]
        return any(normalized in str(item).lower() for item in haystacks if item)

    def _sor_projection(
        self,
        *,
        sor,
        project_id: str,
        workspace_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=sor.memory_id,
            layer="sor",
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=sor.scope_id,
            partition=sor.partition.value,
            subject_key=sor.subject_key,
            summary=sor.content[:240],
            status=sor.status.value if hasattr(sor.status, "value") else str(sor.status),
            version=sor.version,
            created_at=sor.created_at,
            updated_at=sor.updated_at,
            evidence_refs=[item.model_dump(mode="json") for item in sor.evidence_refs],
            metadata=sor.metadata,
            proposal_refs=(
                [str(sor.metadata.get("proposal_id"))]
                if sor.metadata.get("proposal_id")
                else []
            ),
            retrieval_backend=retrieval_backend,
        )

    def _vault_projection(
        self,
        *,
        vault,
        project_id: str,
        workspace_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=vault.vault_id,
            layer="vault",
            project_id=project_id,
            workspace_id=workspace_id,
            scope_id=vault.scope_id,
            partition=vault.partition.value,
            subject_key=vault.subject_key,
            summary=vault.summary,
            created_at=vault.created_at,
            evidence_refs=[item.model_dump(mode="json") for item in vault.evidence_refs],
            metadata=vault.metadata,
            requires_vault_authorization=True,
            retrieval_backend=retrieval_backend,
        )

    def _request_item(self, item) -> VaultAccessRequestItem:
        return VaultAccessRequestItem(
            request_id=item.request_id,
            project_id=item.project_id,
            workspace_id=item.workspace_id or "",
            scope_id=item.scope_id,
            partition=item.partition.value if item.partition else "",
            subject_key=item.subject_key,
            reason=item.reason,
            requester_actor_id=item.requester_actor_id,
            requester_actor_label=item.requester_actor_label,
            status=item.status.value if hasattr(item.status, "value") else str(item.status),
            decision=item.decision.value if item.decision else "",
            requested_at=item.requested_at,
            resolved_at=item.resolved_at,
            resolver_actor_id=item.resolver_actor_id,
            resolver_actor_label=item.resolver_actor_label,
        )

    def _grant_item(self, item) -> VaultAccessGrantItem:
        return VaultAccessGrantItem(
            grant_id=item.grant_id,
            request_id=item.request_id,
            project_id=item.project_id,
            workspace_id=item.workspace_id or "",
            scope_id=item.scope_id,
            partition=item.partition.value if item.partition else "",
            subject_key=item.subject_key,
            granted_to_actor_id=item.granted_to_actor_id,
            granted_to_actor_label=item.granted_to_actor_label,
            granted_by_actor_id=item.granted_by_actor_id,
            granted_by_actor_label=item.granted_by_actor_label,
            granted_at=item.granted_at,
            expires_at=item.expires_at,
            status=item.status.value if hasattr(item.status, "value") else str(item.status),
        )

    def _retrieval_item(self, item) -> VaultRetrievalAuditItem:
        return VaultRetrievalAuditItem(
            retrieval_id=item.retrieval_id,
            project_id=item.project_id,
            workspace_id=item.workspace_id or "",
            scope_id=item.scope_id,
            partition=item.partition.value if item.partition else "",
            subject_key=item.subject_key,
            query=item.query,
            grant_id=item.grant_id,
            actor_id=item.actor_id,
            actor_label=item.actor_label,
            authorized=item.authorized,
            reason_code=item.reason_code,
            result_count=item.result_count,
            retrieved_vault_ids=item.retrieved_vault_ids,
            evidence_refs=[item_ref.model_dump(mode="json") for item_ref in item.evidence_refs],
            created_at=item.created_at,
        )

    def _projection_sort_key(self, item: MemoryRecordProjection) -> datetime:
        return item.updated_at or item.created_at

    def _load_memory_snapshot_json(self, snapshot_path: Path) -> tuple[dict[str, Any], bool, str]:
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {}, False, f"snapshot 解析失败: {exc}"
        if not isinstance(payload, dict):
            return {}, False, "snapshot 顶层必须是 object。"
        schema_ok = any(key in payload for key in ("records", "manifest", "grants"))
        if "records" not in payload:
            payload["records"] = []
        if "grants" not in payload:
            payload["grants"] = []
        return payload, schema_ok, ""

    def _bundle_contains_memory_refs(self, bundle_path: Path) -> bool:
        try:
            with zipfile.ZipFile(bundle_path) as archive:
                names = set(archive.namelist())
        except Exception:
            return False
        return any("memory" in name for name in names)
