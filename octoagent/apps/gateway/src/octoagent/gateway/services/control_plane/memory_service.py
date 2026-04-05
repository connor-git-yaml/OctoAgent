"""MemoryDomainService -- 记忆 / Vault / Retrieval 领域服务。

从 control_plane.py 拆分：
- _handle_memory_* / _handle_vault_* / _handle_retrieval_*  (action handlers)
- get_memory_console / get_retrieval_platform_document / get_memory_subject_history
- get_memory_proposal_audit / get_vault_authorization  (document producers)
- _resolve_memory_action_context / _parse_memory_partition / _parse_memory_layer
- _check_sensitive_partition / _get_memory_store / _memory_target_refs
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    ControlPlaneTargetRef,
    CorpusKind,
    MemoryConsoleDocument,
    MemoryProposalAuditDocument,
    MemorySubjectHistoryDocument,
    RetrievalPlatformDocument,
    VaultAuthorizationDocument,
)
from octoagent.memory import (
    EvidenceRef,
    MemoryLayer,
    MemoryMaintenanceCommandKind,
    MemoryPartition,
    ProposalStatus,
    SENSITIVE_PARTITIONS,
)
from octoagent.gateway.services.memory.memory_console_service import (
    MemoryConsoleError,
    MemoryConsoleService,
)
from octoagent.gateway.services.memory.memory_retrieval_profile import load_memory_retrieval_profile
from octoagent.gateway.services.memory.retrieval_platform_service import (
    RetrievalPlatformService,
)

from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class MemoryDomainService(DomainServiceBase):
    """记忆 / Vault / Retrieval 的全部 action / document 逻辑。"""

    def __init__(
        self,
        ctx: ControlPlaneContext,
        *,
        memory_console_service: MemoryConsoleService | None = None,
        retrieval_platform_service: RetrievalPlatformService | None = None,
    ) -> None:
        super().__init__(ctx)
        self._memory_console_service: MemoryConsoleService = (
            memory_console_service
            or ctx.memory_console_service
            or MemoryConsoleService(ctx.project_root, store_group=ctx.store_group)
        )
        self._retrieval_platform_service: RetrievalPlatformService = (
            retrieval_platform_service
            or ctx.retrieval_platform_service
            or RetrievalPlatformService(ctx.project_root, store_group=ctx.store_group)
        )

    # ------------------------------------------------------------------
    # action / document 路由
    # ------------------------------------------------------------------

    def action_routes(self) -> dict[str, Callable[..., Coroutine[Any, Any, ActionResultEnvelope]]]:
        return {
            "memory.query": self._handle_memory_query,
            "memory.subject.inspect": self._handle_memory_subject_inspect,
            "memory.proposal.inspect": self._handle_memory_proposal_inspect,
            "memory.flush": lambda req: self._handle_memory_maintenance(
                req,
                kind=MemoryMaintenanceCommandKind.FLUSH,
                success_code="MEMORY_FLUSH_COMPLETED",
                success_message="已执行 Memory flush。",
            ),
            "memory.reindex": lambda req: self._handle_memory_maintenance(
                req,
                kind=MemoryMaintenanceCommandKind.REINDEX,
                success_code="MEMORY_REINDEX_COMPLETED",
                success_message="已执行 Memory reindex。",
            ),
            "memory.sync.resume": lambda req: self._handle_memory_maintenance(
                req,
                kind=MemoryMaintenanceCommandKind.SYNC_RESUME,
                success_code="MEMORY_SYNC_RESUME_COMPLETED",
                success_message="已执行 Memory sync.resume。",
            ),
            "memory.consolidate": self._handle_memory_consolidate,
            "memory.profile_generate": self._handle_memory_profile_generate,
            "memory.sor.edit": self._handle_memory_sor_edit,
            "memory.sor.archive": self._handle_memory_sor_archive,
            "memory.sor.restore": self._handle_memory_sor_restore,
            "memory.browse": self._handle_memory_browse,
            "vault.access.request": self._handle_vault_access_request,
            "vault.access.resolve": self._handle_vault_access_resolve,
            "vault.retrieve": self._handle_vault_retrieve,
            "memory.export.inspect": self._handle_memory_export_inspect,
            "memory.restore.verify": self._handle_memory_restore_verify,
            "retrieval.index.start": self._handle_retrieval_index_start,
            "retrieval.index.cancel": self._handle_retrieval_index_cancel,
            "retrieval.index.cutover": self._handle_retrieval_index_cutover,
            "retrieval.index.rollback": self._handle_retrieval_index_rollback,
        }

    def document_routes(self) -> dict[str, Callable[..., Coroutine[Any, Any, Any]]]:
        return {
            "memory": self.get_memory_console,
            "retrieval_platform": self.get_retrieval_platform_document,
        }

    # ------------------------------------------------------------------
    # Document producers
    # ------------------------------------------------------------------

    async def get_memory_console(
        self,
        *,
        project_id: str | None = None,
        scope_id: str | None = None,
        partition: str | None = None,
        layer: str | None = None,
        query: str | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
        limit: int = 50,
        derived_type: str | None = None,
        status: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
    ) -> MemoryConsoleDocument:
        _, selected_project, _, fallback_reason = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        resolved_project = (
            await self._stores.project_store.get_project(resolved_project_id)
            if resolved_project_id
            else selected_project
        )
        backend_status = await self._memory_console_service.get_backend_status(
            project_id=resolved_project_id,
        )
        active_embedding_target, requested_embedding_target = (
            await self._retrieval_platform_service.get_memory_embedding_targets(
                project=resolved_project,
                backend_status=backend_status,
            )
        )
        document = await self._memory_console_service.get_memory_console(
            project_id=resolved_project_id,
            scope_id=scope_id,
            partition=self._parse_memory_partition(partition),
            layer=self._parse_memory_layer(layer),
            query=query,
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
            derived_type=derived_type or "",
            status=status or "",
            updated_after=updated_after or "",
            updated_before=updated_before or "",
        )
        document.retrieval_profile = load_memory_retrieval_profile(
            self._ctx.project_root,
            backend_status=backend_status,
            active_embedding_target=active_embedding_target,
            requested_embedding_target=requested_embedding_target,
        )
        if fallback_reason:
            document.warnings.append(fallback_reason)
            document.degraded.is_degraded = True
            if fallback_reason not in document.degraded.reasons:
                document.degraded.reasons.append(fallback_reason)
        return document

    async def get_retrieval_platform_document(
        self,
        *,
        project_id: str | None = None,
    ) -> RetrievalPlatformDocument:
        _, selected_project, _, fallback_reason = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        backend_status = await self._memory_console_service.get_backend_status(
            project_id=resolved_project_id,
        )
        document = await self._retrieval_platform_service.get_document(
            active_project_id=resolved_project_id,
            backend_status=backend_status,
        )
        if fallback_reason:
            document.warnings.append(fallback_reason)
            document.degraded.is_degraded = True
            if fallback_reason not in document.degraded.reasons:
                document.degraded.reasons.append(fallback_reason)
        return document

    async def get_memory_subject_history(
        self,
        subject_key: str,
        *,
        project_id: str | None = None,
        scope_id: str | None = None,
    ) -> MemorySubjectHistoryDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        return await self._memory_console_service.get_memory_subject_history(
            subject_key=subject_key,
            project_id=resolved_project_id,
            scope_id=scope_id,
        )

    async def get_memory_proposal_audit(
        self,
        *,
        project_id: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> MemoryProposalAuditDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        proposal_status = ProposalStatus(status) if status else None
        return await self._memory_console_service.get_proposal_audit(
            project_id=resolved_project_id,
            scope_id=scope_id,
            status=proposal_status,
            source=source,
            limit=limit,
        )

    async def get_vault_authorization(
        self,
        *,
        project_id: str | None = None,
        scope_id: str | None = None,
        subject_key: str | None = None,
    ) -> VaultAuthorizationDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        resolved_project_id = project_id or (
            selected_project.project_id if selected_project is not None else ""
        )
        return await self._memory_console_service.get_vault_authorization(
            project_id=resolved_project_id,
            scope_id=scope_id,
            subject_key=subject_key,
        )

    # ------------------------------------------------------------------
    # Action handlers — Memory
    # ------------------------------------------------------------------

    async def _handle_memory_query(self, request: ActionRequestEnvelope) -> ActionResultEnvelope:
        project_id = await self._resolve_memory_action_context(request)
        document = await self.get_memory_console(
            project_id=project_id or None,
            scope_id=self._param_str(request.params, "scope_id") or None,
            partition=self._param_str(request.params, "partition") or None,
            layer=self._param_str(request.params, "layer") or None,
            query=self._param_str(request.params, "query") or None,
            include_history=self._param_bool(request.params, "include_history"),
            include_vault_refs=self._param_bool(request.params, "include_vault_refs"),
            limit=self._param_int(request.params, "limit", default=50),
            derived_type=self._param_str(request.params, "derived_type") or "",
            status=self._param_str(request.params, "status") or "",
            updated_after=self._param_str(request.params, "updated_after") or "",
            updated_before=self._param_str(request.params, "updated_before") or "",
        )
        return self._completed_result(
            request=request,
            code="MEMORY_QUERY_COMPLETED",
            message="已刷新 Memory 总览。",
            data={
                "record_count": len(document.records),
                "active_project_id": document.active_project_id,
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_subject_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        subject_key = self._param_str(request.params, "subject_key")
        if not subject_key:
            raise ControlPlaneActionError("SUBJECT_KEY_REQUIRED", "subject_key 不能为空")
        project_id = await self._resolve_memory_action_context(request)
        document = await self.get_memory_subject_history(
            subject_key,
            project_id=project_id or None,
            scope_id=self._param_str(request.params, "scope_id") or None,
        )
        return self._completed_result(
            request=request,
            code="MEMORY_SUBJECT_HISTORY_READY",
            message="已加载 Subject 历史。",
            data={
                "subject_key": subject_key,
                "history_count": len(document.history),
                "scope_id": document.scope_id,
            },
            resource_refs=[
                self._resource_ref(
                    "memory_subject_history",
                    f"memory-subject:{subject_key}",
                )
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="memory_subject",
                    target_id=subject_key,
                    label=subject_key,
                )
            ],
        )

    async def _handle_memory_proposal_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id = await self._resolve_memory_action_context(request)
        document = await self.get_memory_proposal_audit(
            project_id=project_id or None,
            scope_id=self._param_str(request.params, "scope_id") or None,
            status=self._param_str(request.params, "status") or None,
            source=self._param_str(request.params, "source") or None,
            limit=self._param_int(request.params, "limit", default=50),
        )
        return self._completed_result(
            request=request,
            code="MEMORY_PROPOSAL_AUDIT_READY",
            message="已加载 Memory Proposal 审计视图。",
            data={"item_count": len(document.items)},
            resource_refs=[
                self._resource_ref(
                    "memory_proposal_audit",
                    "memory-proposals:overview",
                )
            ],
        )

    async def _handle_memory_maintenance(
        self,
        request: ActionRequestEnvelope,
        *,
        kind: MemoryMaintenanceCommandKind,
        success_code: str,
        success_message: str,
    ) -> ActionResultEnvelope:
        project_id = await self._resolve_memory_action_context(request)
        partition_value = self._param_str(request.params, "partition")
        partition = self._parse_memory_partition(partition_value) if partition_value else None
        raw_evidence_refs = request.params.get("evidence_refs", [])
        evidence_refs = (
            [EvidenceRef.model_validate(item) for item in raw_evidence_refs]
            if isinstance(raw_evidence_refs, list)
            else []
        )
        run = await self._memory_console_service.run_maintenance(
            kind=kind,
            project_id=project_id or "",
            scope_id=self._param_str(request.params, "scope_id"),
            partition=partition,
            reason=self._param_str(request.params, "reason"),
            summary=self._param_str(request.params, "summary"),
            requested_by=request.actor.actor_id,
            evidence_refs=evidence_refs,
            metadata={
                "actor_id": request.actor.actor_id,
                "actor_label": request.actor.actor_label,
            },
        )
        return self._completed_result(
            request=request,
            code=success_code,
            message=success_message,
            data={
                "run_id": run.run_id,
                "status": run.status.value,
                "backend_used": run.backend_used,
                "error_summary": run.error_summary,
                "metadata": run.metadata,
            },
            resource_refs=[
                self._resource_ref("memory_console", "memory:overview"),
                self._resource_ref("diagnostics_summary", "diagnostics:runtime"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_consolidate(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """使用 LLM 将待整理 fragment 整合为 SoR 现行事实。"""
        project_id = await self._resolve_memory_action_context(request)
        try:
            result = await self._memory_console_service.run_consolidate(
                project_id=project_id or "",
            )
        except MemoryConsoleError as exc:
            return self._rejected_result(
                request=request,
                code=exc.code,
                message=exc.message,
            )
        return self._completed_result(
            request=request,
            code="MEMORY_CONSOLIDATE_COMPLETED",
            message=result.get("message", "记忆整理完成"),
            data=result,
            resource_refs=[
                self._resource_ref("memory_console", "memory:overview"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_profile_generate(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """定期聚合生成用户画像（Feature 065 Phase 3, US-9）。"""
        project_id = await self._resolve_memory_action_context(request)

        # 复用 MemoryConsoleService 内部的 context/memory 解析模式
        try:
            context = await self._memory_console_service._resolve_context(
                active_project_id=project_id or "",
                project_id=project_id or "",
            )
            if not context.selected_scope_ids:
                return self._completed_result(
                    request=request,
                    code="PROFILE_GENERATE_NO_SCOPE",
                    message="没有可用的 scope",
                    data={"dimensions_generated": 0, "dimensions_updated": 0},
                    resource_refs=[],
                    target_refs=self._memory_target_refs(request),
                )
            memory = await self._memory_console_service._memory_service_for_context(context)
        except Exception as exc:
            return self._rejected_result(
                request=request,
                code="MEMORY_SERVICE_UNAVAILABLE",
                message=f"Memory 服务不可用: {exc}",
            )

        # 延迟创建 ProfileGeneratorService
        try:
            from octoagent.memory import SqliteMemoryStore
            from octoagent.gateway.services.inference.profile_generator_service import ProfileGeneratorService

            memory_store = SqliteMemoryStore(self._stores.conn)
            llm_service = (
                getattr(self._stores, "llm_service", None)
                or self._memory_console_service._llm_service
            )
            profile_service = ProfileGeneratorService(
                memory_store=memory_store,
                llm_service=llm_service,
                project_root=self._ctx.project_root,
            )
        except Exception as exc:
            return self._rejected_result(
                request=request,
                code="PROFILE_SERVICE_UNAVAILABLE",
                message=f"画像服务初始化失败: {exc}",
            )

        total_generated = 0
        total_updated = 0
        all_errors: list[str] = []

        for scope_id in context.selected_scope_ids:
            try:
                result = await profile_service.generate_profile(
                    memory=memory,
                    scope_id=scope_id,
                )
                total_generated += result.dimensions_generated
                total_updated += result.dimensions_updated
                all_errors.extend(result.errors)
            except Exception as exc:
                all_errors.append(f"scope {scope_id} 画像生成失败: {exc}")
                log.warning(
                    "profile_generate_scope_failed",
                    scope_id=scope_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        return self._completed_result(
            request=request,
            code="PROFILE_GENERATE_COMPLETED",
            message=f"画像生成完成：{total_generated} 新增, {total_updated} 更新",
            data={
                "dimensions_generated": total_generated,
                "dimensions_updated": total_updated,
                "errors": all_errors[:10],
            },
            resource_refs=[
                self._resource_ref("memory_console", "memory:overview"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_sor_edit(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T023: 用户编辑 SoR 记忆——乐观锁 + Proposal 流程 + 审计事件。"""
        project_id = await self._resolve_memory_action_context(request)
        scope_id = self._param_str(request.params, "scope_id")
        subject_key = self._param_str(request.params, "subject_key")
        content = self._param_str(request.params, "content")
        new_subject_key = self._param_str(request.params, "new_subject_key")
        expected_version = self._param_int(request.params, "expected_version", default=0)
        edit_summary = self._param_str(request.params, "edit_summary")

        if not scope_id or not subject_key or not content or expected_version < 1:
            return self._rejected_result(
                request=request,
                code="INVALID_PARAMS",
                message="scope_id、subject_key、content、expected_version 均为必填项。",
            )

        store = self._get_memory_store()
        current = await store.get_current_sor(scope_id, subject_key)
        if current is None:
            return self._rejected_result(
                request=request,
                code="SOR_NOT_FOUND",
                message=f"未找到 scope={scope_id} subject_key={subject_key} 的 current SoR 记录。",
            )
        if current.version != expected_version:
            return self._rejected_result(
                request=request,
                code="VERSION_CONFLICT",
                message=f"版本冲突：期望版本 {expected_version}，当前版本 {current.version}。请刷新后重试。",
            )
        if self._check_sensitive_partition(current.partition):
            return self._rejected_result(
                request=request,
                code="VAULT_AUTHORIZATION_REQUIRED",
                message="此记忆属于敏感分区，编辑需要额外的 Vault 授权确认。",
            )

        # 走 propose-validate-commit 流程
        from octoagent.memory import MemoryService, WriteAction, WriteProposal
        from ulid import ULID

        target_subject_key = new_subject_key if new_subject_key else subject_key
        memory_service = await self._memory_console_service._memory_service_for_context(
            await self._memory_console_service._resolve_context(
                active_project_id=project_id or "",
                project_id=project_id or "",
                scope_id=scope_id,
            )
        )
        now = datetime.now(UTC)
        proposal = WriteProposal(
            proposal_id=f"01JPROP_{ULID()}",
            scope_id=scope_id,
            partition=current.partition,
            action=WriteAction.UPDATE,
            subject_key=target_subject_key,
            content=content,
            rationale=edit_summary or "用户手动编辑",
            confidence=1.0,
            evidence_refs=current.evidence_refs or [EvidenceRef(ref_id="user_edit", ref_type="user")],
            expected_version=current.version,
            metadata={"source": "user_edit", "edit_summary": edit_summary},
            created_at=now,
        )

        await memory_service.propose_write(proposal)
        validation = await memory_service.validate_proposal(proposal.proposal_id)
        if validation.errors:
            return self._rejected_result(
                request=request,
                code="VALIDATION_FAILED",
                message=f"编辑验证失败: {'; '.join(validation.errors)}",
            )

        result = await memory_service.commit_memory(proposal.proposal_id)

        # 审计事件
        log.info(
            "memory.sor.edit.completed",
            scope_id=scope_id,
            subject_key=subject_key,
            new_subject_key=target_subject_key,
            old_version=current.version,
            new_version=result.version if result else expected_version + 1,
            edit_summary=edit_summary,
            actor="user:web",
        )

        return self._completed_result(
            request=request,
            code="MEMORY_SOR_EDIT_COMPLETED",
            message="记忆已更新",
            data={
                "memory_id": result.memory_id if result else "",
                "subject_key": target_subject_key,
                "version": result.version if result else expected_version + 1,
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_sor_archive(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T034: 归档 SoR 记忆。"""
        project_id = await self._resolve_memory_action_context(request)
        scope_id = self._param_str(request.params, "scope_id")
        memory_id = self._param_str(request.params, "memory_id")
        expected_version = self._param_int(request.params, "expected_version", default=0)

        if not scope_id or not memory_id or expected_version < 1:
            return self._rejected_result(
                request=request,
                code="INVALID_PARAMS",
                message="scope_id、memory_id、expected_version 均为必填项。",
            )

        store = self._get_memory_store()
        current = await store.get_sor(memory_id)
        if current is None or current.scope_id != scope_id:
            return self._rejected_result(
                request=request, code="SOR_NOT_FOUND",
                message=f"未找到 memory_id={memory_id} 的 SoR 记录。",
            )
        if current.status != "current":
            return self._rejected_result(
                request=request, code="INVALID_STATUS",
                message=f"记忆状态为 {current.status}，只有 current 状态的记忆可以归档。",
            )
        if current.version != expected_version:
            return self._rejected_result(
                request=request, code="VERSION_CONFLICT",
                message=f"版本冲突：期望版本 {expected_version}，当前版本 {current.version}。请刷新后重试。",
            )
        if self._check_sensitive_partition(current.partition):
            return self._rejected_result(
                request=request, code="VAULT_AUTHORIZATION_REQUIRED",
                message="此记忆属于敏感分区，归档需要额外的 Vault 授权确认。",
            )

        now_str = datetime.now(UTC).isoformat()
        await store.update_sor_status(memory_id, status="archived", updated_at=now_str)

        # 审计事件
        log.info(
            "memory.sor.archive.completed",
            scope_id=scope_id,
            memory_id=memory_id,
            subject_key=current.subject_key,
            actor="user:web",
        )

        return self._completed_result(
            request=request,
            code="MEMORY_SOR_ARCHIVE_COMPLETED",
            message="记忆已归档",
            data={
                "memory_id": memory_id,
                "subject_key": current.subject_key,
                "new_status": "archived",
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_sor_restore(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T035: 恢复已归档的 SoR 记忆。"""
        project_id = await self._resolve_memory_action_context(request)
        scope_id = self._param_str(request.params, "scope_id")
        memory_id = self._param_str(request.params, "memory_id")

        if not scope_id or not memory_id:
            return self._rejected_result(
                request=request,
                code="INVALID_PARAMS",
                message="scope_id 和 memory_id 均为必填项。",
            )

        store = self._get_memory_store()
        record = await store.get_sor(memory_id)
        if record is None or record.scope_id != scope_id:
            return self._rejected_result(
                request=request, code="SOR_NOT_FOUND",
                message=f"未找到 memory_id={memory_id} 的 SoR 记录。",
            )
        if record.status != "archived":
            return self._rejected_result(
                request=request,
                code="INVALID_STATUS",
                message=f"记忆状态为 {record.status}，只有 archived 状态的记忆可以恢复。",
            )

        # 检查同 subject_key 下是否已有 current 记录
        existing_current = await store.get_current_sor(scope_id, record.subject_key)
        if existing_current is not None:
            return self._rejected_result(
                request=request,
                code="SUBJECT_KEY_CONFLICT",
                message=(
                    f"同 subject_key ({record.subject_key}) 下已存在 current 记录 "
                    f"(memory_id={existing_current.memory_id})，无法恢复。"
                    f"请先归档或编辑现有记录。"
                ),
            )

        now_str = datetime.now(UTC).isoformat()
        await store.update_sor_status(memory_id, status="current", updated_at=now_str)

        # 审计事件
        log.info(
            "memory.sor.restore.completed",
            scope_id=scope_id,
            memory_id=memory_id,
            subject_key=record.subject_key,
            actor="user:web",
        )

        return self._completed_result(
            request=request,
            code="MEMORY_SOR_RESTORE_COMPLETED",
            message="记忆已恢复",
            data={
                "memory_id": memory_id,
                "subject_key": record.subject_key,
                "new_status": "current",
            },
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    async def _handle_memory_browse(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """T062: 前端 Memory UI 的 browse 查询。"""
        project_id = await self._resolve_memory_action_context(request)
        result = await self._memory_console_service.browse_memory(
            project_id=project_id or "",
            scope_id=self._param_str(request.params, "scope_id") or "",
            prefix=self._param_str(request.params, "prefix"),
            partition=self._param_str(request.params, "partition"),
            group_by=self._param_str(request.params, "group_by") or "partition",
            offset=self._param_int(request.params, "offset", default=0),
            limit=self._param_int(request.params, "limit", default=20),
        )
        return self._completed_result(
            request=request,
            code="MEMORY_BROWSE_COMPLETED",
            message="已获取记忆目录。",
            data=result,
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
        )

    # ------------------------------------------------------------------
    # Action handlers — Retrieval
    # ------------------------------------------------------------------

    async def _handle_retrieval_index_start(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id = await self._resolve_memory_action_context(request)
        document = await self._retrieval_platform_service.start_memory_generation_build(
            actor_id=request.actor.actor_id,
            actor_label=request.actor.actor_label,
            project_id=project_id or "",
        )
        memory_state = next(
            (
                item
                for item in document.corpora
                if item.corpus_kind == CorpusKind.MEMORY
            ),
            None,
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_BUILD_STARTED",
            message="已开始准备新的 embedding 索引。",
            data={
                "corpus_kind": CorpusKind.MEMORY.value,
                "state": memory_state.state if memory_state is not None else "unknown",
                "pending_generation_id": (
                    memory_state.pending_generation_id if memory_state is not None else ""
                ),
            },
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_retrieval_index_cancel(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        generation_id = self._param_str(request.params, "generation_id")
        if not generation_id:
            raise ControlPlaneActionError(
                "GENERATION_ID_REQUIRED",
                "generation_id 不能为空",
            )
        project_id = await self._resolve_memory_action_context(request)
        await self._retrieval_platform_service.cancel_generation(
            generation_id=generation_id,
            project_id=project_id or "",
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_BUILD_CANCELLED",
            message="已取消新的 embedding 迁移，系统继续使用旧索引。",
            data={"generation_id": generation_id},
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_retrieval_index_cutover(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        generation_id = self._param_str(request.params, "generation_id")
        if not generation_id:
            raise ControlPlaneActionError(
                "GENERATION_ID_REQUIRED",
                "generation_id 不能为空",
            )
        project_id = await self._resolve_memory_action_context(request)
        await self._retrieval_platform_service.cutover_generation(
            generation_id=generation_id,
            project_id=project_id or "",
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_CUTOVER_COMPLETED",
            message="已切换到新的 embedding 索引。",
            data={"generation_id": generation_id},
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    async def _handle_retrieval_index_rollback(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        generation_id = self._param_str(request.params, "generation_id")
        if not generation_id:
            raise ControlPlaneActionError(
                "GENERATION_ID_REQUIRED",
                "generation_id 不能为空",
            )
        project_id = await self._resolve_memory_action_context(request)
        await self._retrieval_platform_service.rollback_generation(
            generation_id=generation_id,
            project_id=project_id or "",
        )
        return self._completed_result(
            request=request,
            code="RETRIEVAL_ROLLBACK_COMPLETED",
            message="已回滚到上一版 embedding 索引。",
            data={"generation_id": generation_id},
            resource_refs=[
                self._resource_ref("retrieval_platform", "retrieval:platform"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
        )

    # ------------------------------------------------------------------
    # Action handlers — Vault
    # ------------------------------------------------------------------

    async def _handle_vault_access_request(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id = await self._resolve_memory_action_context(request)
        vault_request, decision = await self._memory_console_service.request_vault_access(
            actor_id=request.actor.actor_id,
            actor_label=request.actor.actor_label,
            active_project_id=project_id,
            project_id=project_id,
            scope_id=self._param_str(request.params, "scope_id") or None,
            partition=self._param_str(request.params, "partition"),
            subject_key=self._param_str(request.params, "subject_key") or None,
            reason=self._param_str(request.params, "reason"),
        )
        if not decision.allowed or vault_request is None:
            return self._rejected_result(
                request=request,
                code=decision.reason_code,
                message=decision.message,
            )
        return self._completed_result(
            request=request,
            code="VAULT_ACCESS_REQUEST_CREATED",
            message="已创建 Vault 授权申请。",
            data={"request_id": vault_request.request_id},
            resource_refs=[
                self._resource_ref("vault_authorization", "vault:authorization"),
                self._resource_ref("memory_console", "memory:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="vault_request",
                    target_id=vault_request.request_id,
                )
            ],
        )

    async def _handle_vault_access_resolve(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        request_id = self._param_str(request.params, "request_id")
        decision_raw = self._param_str(request.params, "decision").lower()
        if not request_id:
            raise ControlPlaneActionError("REQUEST_ID_REQUIRED", "request_id 不能为空")
        if decision_raw not in {"approve", "reject"}:
            raise ControlPlaneActionError(
                "VAULT_ACCESS_DECISION_INVALID",
                "decision 必须是 approve/reject",
            )
        try:
            resolved_request, grant = await self._memory_console_service.resolve_vault_access(
                request_id=request_id,
                approved=decision_raw == "approve",
                actor_id=request.actor.actor_id,
                actor_label=request.actor.actor_label,
                expires_in_seconds=self._param_int(
                    request.params,
                    "expires_in_seconds",
                    default=0,
                ),
            )
        except MemoryConsoleError as exc:
            return self._rejected_result(
                request=request,
                code=exc.code,
                message=str(exc),
            )
        code = (
            "VAULT_ACCESS_APPROVED"
            if resolved_request.status is not None and resolved_request.status.value == "approved"
            else "VAULT_ACCESS_REJECTED"
        )
        message = (
            "已批准 Vault 授权申请。"
            if code == "VAULT_ACCESS_APPROVED"
            else "已拒绝 Vault 授权申请。"
        )
        return self._completed_result(
            request=request,
            code=code,
            message=message,
            data={
                "request_id": resolved_request.request_id,
                "grant_id": grant.grant_id if grant is not None else "",
            },
            resource_refs=[
                self._resource_ref("vault_authorization", "vault:authorization"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="vault_request",
                    target_id=resolved_request.request_id,
                )
            ],
        )

    async def _handle_vault_retrieve(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        project_id = await self._resolve_memory_action_context(request)
        code, payload, decision = await self._memory_console_service.retrieve_vault(
            actor_id=request.actor.actor_id,
            actor_label=request.actor.actor_label,
            active_project_id=project_id,
            project_id=project_id,
            scope_id=self._param_str(request.params, "scope_id") or None,
            partition=self._param_str(request.params, "partition"),
            subject_key=self._param_str(request.params, "subject_key") or None,
            query=self._param_str(request.params, "query") or None,
            grant_id=self._param_str(request.params, "grant_id") or None,
        )
        if code != "VAULT_RETRIEVE_AUTHORIZED":
            return self._rejected_result(
                request=request,
                code=code if decision.allowed else decision.reason_code,
                message=("当前没有可用的 Vault 授权。" if decision.allowed else decision.message),
                target_refs=self._memory_target_refs(request),
            )
        return self._completed_result(
            request=request,
            code=code,
            message="已返回授权范围内的 Vault 检索结果。",
            data=payload,
            resource_refs=[
                self._resource_ref("vault_authorization", "vault:authorization"),
            ],
            target_refs=self._memory_target_refs(request),
        )

    # ------------------------------------------------------------------
    # Action handlers — Export / Restore
    # ------------------------------------------------------------------

    async def _handle_memory_export_inspect(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        scope_ids = self._param_list(request.params, "scope_ids")
        project_id = await self._resolve_memory_action_context(request)
        code, payload, decision = await self._memory_console_service.inspect_export(
            active_project_id=project_id,
            project_id=project_id,
            scope_ids=scope_ids or None,
            include_history=self._param_bool(request.params, "include_history"),
            include_vault_refs=self._param_bool(request.params, "include_vault_refs"),
        )
        if code != "MEMORY_EXPORT_INSPECTION_READY":
            return self._rejected_result(
                request=request,
                code=code if decision.allowed else decision.reason_code,
                message=("Memory 导出检查存在阻塞项。" if decision.allowed else decision.message),
                target_refs=self._memory_target_refs(request),
            )
        return self._completed_result(
            request=request,
            code=code,
            message="Memory 导出检查已就绪。",
            data=payload,
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
            target_refs=self._memory_target_refs(request),
        )

    async def _handle_memory_restore_verify(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        snapshot_ref = self._param_str(request.params, "snapshot_ref")
        if not snapshot_ref:
            raise ControlPlaneActionError("SNAPSHOT_REF_REQUIRED", "snapshot_ref 不能为空")
        project_id = await self._resolve_memory_action_context(request)
        code, payload, decision = await self._memory_console_service.verify_restore(
            actor_id=request.actor.actor_id,
            active_project_id=project_id,
            project_id=project_id,
            snapshot_ref=snapshot_ref,
            target_scope_mode=self._param_str(
                request.params,
                "target_scope_mode",
                default="current_project",
            ),
            scope_ids=self._param_list(request.params, "scope_ids") or None,
        )
        if code != "MEMORY_RESTORE_VERIFICATION_READY":
            return self._rejected_result(
                request=request,
                code=code if decision.allowed else decision.reason_code,
                message=("Memory 恢复校验存在阻塞项。" if decision.allowed else decision.message),
                target_refs=self._memory_target_refs(request),
            )
        return self._completed_result(
            request=request,
            code=code,
            message="Memory 恢复校验已通过。",
            data=payload,
            resource_refs=[self._resource_ref("memory_console", "memory:overview")],
            target_refs=self._memory_target_refs(request),
        )

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _resolve_memory_action_context(
        self,
        request: ActionRequestEnvelope,
    ) -> str:
        """解析 memory action 公共上下文：返回 project_id。"""
        _, selected_project, _, _ = await self._resolve_selection()
        project_id = self._param_str(request.params, "project_id") or (
            selected_project.project_id if selected_project is not None else ""
        )
        return project_id

    def _parse_memory_partition(self, value: str | None) -> MemoryPartition | None:
        if not value:
            return None
        try:
            return MemoryPartition(value)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MEMORY_PARTITION_INVALID",
                f"不支持的 partition: {value}",
            ) from exc

    def _parse_memory_layer(self, value: str | None) -> MemoryLayer | None:
        if not value:
            return None
        try:
            return MemoryLayer(value)
        except ValueError as exc:
            raise ControlPlaneActionError(
                "MEMORY_LAYER_INVALID",
                f"不支持的 layer: {value}",
            ) from exc

    @staticmethod
    def _check_sensitive_partition(partition_str: str) -> bool:
        """检查 partition 是否属于敏感分区（HEALTH/FINANCE）。"""
        try:
            partition_enum = MemoryPartition(partition_str)
        except ValueError:
            return False
        return partition_enum in SENSITIVE_PARTITIONS

    def _get_memory_store(self):
        """获取 Memory Store 实例（避免多处直接穿透 _memory_console_service）。"""
        return self._memory_console_service._memory_store

    def _memory_target_refs(self, request: ActionRequestEnvelope) -> list[ControlPlaneTargetRef]:
        targets: list[ControlPlaneTargetRef] = []
        for key, target_type in (
            ("project_id", "project"),
            ("scope_id", "scope"),
            ("subject_key", "memory_subject"),
        ):
            value = self._param_str(request.params, key)
            if value:
                targets.append(
                    ControlPlaneTargetRef(
                        target_type=target_type,
                        target_id=value,
                        label=value,
                    )
                )
        return targets
