"""Memory Console 只读查询子服务。

包含 get_overview、get_subject_history、browse_memory、get_proposal_audit
等不产生副作用的查询方法。
"""

from __future__ import annotations

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
)
from octoagent.memory import (
    DerivedMemoryQuery,
    MemoryPartition,
    ProposalStatus,
)

from ._memory_console_base import MemoryConsoleBase
from .memory_retrieval_profile import load_memory_retrieval_profile


class MemoryConsoleView:
    """Memory Console 只读查询——overview / subject history / browse / proposal audit。"""

    def __init__(self, base: MemoryConsoleBase) -> None:
        self._base = base

    async def get_overview(
        self,
        *,
        active_project_id: str,
        project_id: str = "",
        scope_id: str = "",
        partition: str = "",
        layer: str = "",
        query: str = "",
        include_history: bool = False,
        include_vault_refs: bool = False,
        limit: int = 50,
        derived_type: str = "",
        status: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> MemoryConsoleDocument:
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
        )
        memory = await self._base.memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        retrieval_profile = load_memory_retrieval_profile(
            self._base._project_root,
            backend_status=backend_status,
        )
        records: list[MemoryRecordProjection] = []
        summary = MemoryConsoleSummary(scope_count=len(context.selected_scope_ids))
        for scope in context.selected_scope_ids:
            bound = context.scope_bindings.get(scope)
            if layer in {"", "fragment"}:
                fragments = await self._base._memory_store.list_fragments(
                    scope,
                    query=query or None,
                    limit=limit,
                )
                for fragment in fragments:
                    if partition and fragment.partition.value != partition:
                        continue
                    records.append(
                        self._base.fragment_projection(
                            fragment=fragment,
                            project_id=context.project.project_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )
                    summary.fragment_count += 1
                    if not fragment.metadata.get("consolidated_at"):
                        summary.pending_consolidation_count += 1
            if layer in {"", "sor"}:
                sor_records = await self._base._memory_store.search_sor(
                    scope,
                    query=query or None,
                    include_history=include_history,
                    limit=limit,
                    partition=partition,
                    status=status,
                    derived_type=derived_type,
                    updated_after=updated_after,
                    updated_before=updated_before,
                )
                for sor in sor_records:
                    if partition and sor.partition.value != partition:
                        continue
                    if sor.status == "current":
                        summary.sor_current_count += 1
                    else:
                        summary.sor_history_count += 1
                    records.append(
                        self._base.sor_projection(
                            sor=sor,
                            project_id=context.project.project_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )
                # 用独立 COUNT 查询获取准确的用户可读 SoR 数量（不受 limit 截断）
                summary.sor_readable_count += await self._base._memory_store.count_sor_readable(scope)
            if include_vault_refs and layer in {"", "vault"}:
                vault_records = await self._base._memory_store.search_vault(
                    scope,
                    query=query or None,
                    limit=limit,
                )
                for vault in vault_records:
                    if partition and vault.partition.value != partition:
                        continue
                    summary.vault_ref_count += 1
                    records.append(
                        self._base.vault_projection(
                            vault=vault,
                            project_id=context.project.project_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )
            if layer in {"", "derived"}:
                derived_result = await memory.list_derived_memory(
                    DerivedMemoryQuery(
                        scope_id=scope,
                        partition=MemoryPartition(partition) if partition else None,
                        limit=limit,
                    )
                )
                for derived in derived_result.items:
                    if query and not self._base.derived_matches_query(derived, query):
                        continue
                    records.append(
                        self._base.derived_projection(
                            derived=derived,
                            project_id=context.project.project_id,
                            retrieval_backend=backend_status.active_backend,
                        )
                    )

        proposals = await memory.list_proposals(
            scope_ids=context.selected_scope_ids,
            limit=limit,
        )
        summary.proposal_count = len(proposals)
        summary.pending_replay_count = backend_status.pending_replay_count
        # 从 AutomationScheduler 获取下次 consolidate 执行时间
        try:
            consolidate_job = self._base._stores.automation_store.get_job("system:memory-consolidate")
            if consolidate_job and consolidate_job.next_run_at:
                summary.next_consolidation_at = consolidate_job.next_run_at.isoformat()
        except Exception:
            pass  # scheduler 不可用时静默
        records.sort(key=self._base.projection_sort_key, reverse=True)
        records = records[:limit]
        available_partitions = sorted({item.partition for item in records})
        available_layers = sorted({item.layer for item in records})
        warnings = list(context.warnings)
        doc_status = "ready" if not context.blocking_issues else "degraded"
        if backend_status.state.value != "healthy":
            warnings.append(
                backend_status.message
                or f"memory backend 当前状态为 {backend_status.state.value}"
            )
            doc_status = "degraded"
        warnings.extend(context.blocking_issues)
        return MemoryConsoleDocument(
            status=doc_status,
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
            backend_id=backend_status.backend_id,
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            index_health=self._base.backend_index_health(backend_status),
            retrieval_profile=retrieval_profile,
            filters=MemoryConsoleFilter(
                project_id=context.project.project_id,
                scope_id=scope_id,
                partition=partition,
                layer=layer,
                query=query,
                include_history=include_history,
                include_vault_refs=include_vault_refs,
                limit=limit,
                derived_type=derived_type,
                status=doc_status,
                updated_after=updated_after,
                updated_before=updated_before,
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
        project_id: str = "",
        scope_id: str = "",
    ) -> MemorySubjectHistoryDocument:
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
        )
        memory = await self._base.memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        history: list[MemoryRecordProjection] = []
        current_record: MemoryRecordProjection | None = None
        warnings = list(context.warnings)
        latest_proposal_refs: list[str] = []
        for scope in context.selected_scope_ids:
            bound = context.scope_bindings.get(scope)
            sor_history = await self._base._memory_store.list_sor_history(scope, subject_key)
            for sor in sor_history:
                projection = self._base.sor_projection(
                    sor=sor,
                    project_id=context.project.project_id,
                    retrieval_backend=backend_status.active_backend,
                )
                history.append(projection)
                if sor.status == "current" and current_record is None:
                    current_record = projection
                latest_proposal_refs.extend(projection.proposal_refs)
        history.sort(key=self._base.projection_sort_key, reverse=True)
        if len({item.scope_id for item in history}) > 1:
            warnings.append("subject_key 命中了多个 scope，已合并显示历史。")
        return MemorySubjectHistoryDocument(
            resource_id=f"memory-subject:{subject_key}",
            active_project_id=context.project.project_id,
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            index_health=self._base.backend_index_health(backend_status),
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

    async def browse_memory(
        self,
        *,
        project_id: str = "",
        scope_id: str = "",
        prefix: str = "",
        partition: str = "",
        group_by: str = "partition",
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """浏览 SoR 记忆目录——返回分组统计和条目摘要。"""
        from octoagent.memory import BrowseResult

        context = await self._base.resolve_context(
            active_project_id=project_id or "",
            project_id=project_id or "",
            scope_id=scope_id or "",
        )
        if context.blocking_issues:
            return BrowseResult().model_dump(mode="json")

        # 限制 limit
        limit = max(1, min(limit, 100))

        # 合并所有 scope 的 browse 结果
        from octoagent.memory import BrowseGroup

        merged_groups: dict[str, BrowseGroup] = {}
        total = 0
        has_more = False

        for sid in context.selected_scope_ids:
            result = await self._base._memory_store.browse_sor(
                sid,
                prefix=prefix,
                partition=partition,
                status="current",
                group_by=group_by,
                offset=offset,
                limit=limit,
            )
            total += result.total_count
            if result.has_more:
                has_more = True
            for g in result.groups:
                if g.key in merged_groups:
                    existing = merged_groups[g.key]
                    merged_groups[g.key] = BrowseGroup(
                        key=g.key,
                        count=existing.count + g.count,
                        items=existing.items + g.items,
                        latest_updated_at=(
                            max(existing.latest_updated_at, g.latest_updated_at)
                            if existing.latest_updated_at and g.latest_updated_at
                            else existing.latest_updated_at or g.latest_updated_at
                        ),
                    )
                else:
                    merged_groups[g.key] = g

        final = BrowseResult(
            groups=list(merged_groups.values()),
            total_count=total,
            has_more=has_more,
            offset=offset,
            limit=limit,
        )
        return final.model_dump(mode="json")

    async def get_proposal_audit(
        self,
        *,
        active_project_id: str = "",
        project_id: str = "",
        scope_id: str = "",
        status: ProposalStatus | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> MemoryProposalAuditDocument:
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
        )
        memory = await self._base.memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        statuses = [status] if status else None
        proposals = await memory.list_proposals(
            scope_ids=context.selected_scope_ids,
            statuses=statuses,
            source=source or None,
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
