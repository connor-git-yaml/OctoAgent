"""Memory Console Vault 授权桥接子服务。

包含 Vault 授权查看、申请、审批、检索等操作。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from octoagent.core.models import (
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    VaultAuthorizationDocument,
)
from octoagent.memory import (
    MemoryPartition,
    VaultAccessGrantStatus,
    VaultAccessRequestStatus,
)

from ._memory_console_base import (
    MemoryConsoleBase,
    MemoryConsoleError,
    MemoryPermissionDecision,
)


class MemoryVaultBridge:
    """Vault 授权管理——查看、申请、审批、检索。"""

    def __init__(self, base: MemoryConsoleBase) -> None:
        self._base = base

    async def get_vault_authorization(
        self,
        *,
        active_project_id: str = "",
        project_id: str = "",
        scope_id: str = "",
        subject_key: str = "",
    ) -> VaultAuthorizationDocument:
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
        )
        memory = await self._base.memory_service_for_context(context)
        backend_status = await memory.get_backend_status()
        requests = await memory.list_vault_access_requests(
            project_id=context.project.project_id,
            scope_ids=context.selected_scope_ids,
            subject_key=subject_key or None,
            limit=50,
        )
        grants = await memory.list_vault_access_grants(
            project_id=context.project.project_id,
            scope_ids=context.selected_scope_ids,
            subject_key=subject_key or None,
            limit=50,
        )
        active_grants = [await self._base.normalize_grant(item) for item in grants]
        retrievals = await memory.list_vault_retrieval_audits(
            project_id=context.project.project_id,
            scope_ids=context.selected_scope_ids,
            subject_key=subject_key or None,
            limit=50,
        )
        return VaultAuthorizationDocument(
            active_project_id=context.project.project_id,
            retrieval_backend=backend_status.active_backend,
            backend_state=backend_status.state.value,
            active_requests=[self._base.request_item(item) for item in requests],
            active_grants=[
                self._base.grant_item(item)
                for item in active_grants
                if item.status is VaultAccessGrantStatus.ACTIVE
            ],
            recent_retrievals=[self._base.retrieval_item(item) for item in retrievals],
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
        project_id: str = "",
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
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
        )
        decision = self._base.decide_project_scope_action(
            action_id="vault.access.request",
            actor_id=actor_id,
            context=context,
            required_scope_id=scope_id,
        )
        if not decision.allowed:
            return None, decision
        request = await self._base._memory.create_vault_access_request(
            project_id=context.project.project_id,
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
        approved: bool,
        actor_label: str = "",
        expires_in_seconds: int = 0,
    ):
        request = await self._base._memory_store.get_vault_access_request(request_id)
        if request is None:
            raise MemoryConsoleError(
                "VAULT_ACCESS_REQUEST_NOT_FOUND",
                "Vault 授权申请不存在。",
            )
        context = await self._base.resolve_context(
            active_project_id=request.project_id,
            project_id=request.project_id,
            scope_id=request.scope_id,
        )
        permission = self._base.decide_operator_only(
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
        from octoagent.memory import VaultAccessDecision
        decision = VaultAccessDecision.APPROVE if approved else VaultAccessDecision.REJECT
        resolved_request, grant = await self._base._memory.resolve_vault_access_request(
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
        project_id: str = "",
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
        context = await self._base.resolve_context(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
        )
        decision = self._base.decide_project_scope_action(
            action_id="vault.retrieve",
            actor_id=actor_id,
            context=context,
            required_scope_id=scope_id,
        )
        if not decision.allowed:
            await self._base._memory.record_vault_retrieval_audit(
                actor_id=actor_id,
                actor_label=actor_label,
                project_id=context.project.project_id,
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
            scope_id=scope_id,
            partition=MemoryPartition(partition) if partition else None,
            subject_key=subject_key or None,
            grant_id=grant_id or None,
        )
        if grant is None:
            await self._base._memory.record_vault_retrieval_audit(
                actor_id=actor_id,
                actor_label=actor_label,
                project_id=context.project.project_id,
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
                scope_id=scope_id,
            )
            return grant_code, {}, denied

        vault_records = await self._base._memory_store.search_vault(
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
        await self._base._memory.record_vault_retrieval_audit(
            actor_id=actor_id,
            actor_label=actor_label,
            project_id=context.project.project_id,
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

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _resolve_grant_for_retrieval(
        self,
        *,
        actor_id: str,
        project_id: str,
        scope_id: str,
        partition: MemoryPartition | None,
        subject_key: str | None,
        grant_id: str | None,
    ):
        if grant_id:
            grant = await self._base._memory.get_vault_access_grant(grant_id)
            if grant is None:
                return None, "VAULT_AUTHORIZATION_REQUIRED", "未找到指定的 Vault grant。"
            normalized = await self._base.normalize_grant(grant)
            if normalized.status is VaultAccessGrantStatus.EXPIRED:
                return None, "VAULT_AUTHORIZATION_EXPIRED", "Vault grant 已过期。"
            if normalized.granted_to_actor_id != actor_id:
                return (
                    None,
                    "VAULT_AUTHORIZATION_NOT_ALLOWED",
                    "指定的 Vault grant 不属于当前 actor。",
                )
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
        grant = await self._base._memory.get_latest_valid_vault_grant(
            actor_id=actor_id,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
        )
        if grant is None:
            grants = await self._base._memory.list_vault_access_grants(
                project_id=project_id,
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
