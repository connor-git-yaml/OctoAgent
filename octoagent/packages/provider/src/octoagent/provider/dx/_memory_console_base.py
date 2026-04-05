"""Memory Console 子服务共享基础设施。

包含 context 解析、权限判定、projection 构造等所有子服务共享的逻辑。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from octoagent.core.models import (
    MemoryRecordProjection,
    Project,
    ProjectBindingType,
    VaultAccessGrantItem,
    VaultAccessRequestItem,
    VaultRetrievalAuditItem,
)
from octoagent.memory import (
    MemoryBackendStatus,
    MemoryService,
    SqliteMemoryStore,
    VaultAccessGrantStatus,
)

from .backup_service import resolve_project_root
from .memory_backend_resolver import MemoryBackendResolver

_log = structlog.get_logger()

_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}


@dataclass(slots=True)
class _BoundScope:
    scope_id: str
    binding_type: ProjectBindingType


@dataclass(slots=True)
class _MemoryContext:
    project: Project
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
    scope_id: str = ""


class MemoryConsoleError(RuntimeError):
    """Memory Console 结构化错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class MemoryConsoleBase:
    """所有 Memory Console 子服务的共享基础设施。

    提供 context 解析、权限判定、projection 构造等能力。
    子服务通过构造函数接收同一个 base 实例来共享状态。
    """

    def __init__(
        self,
        project_root: Path,
        *,
        store_group,
    ) -> None:
        self._project_root = resolve_project_root(project_root).resolve()
        self._stores = store_group
        self._memory_store = SqliteMemoryStore(store_group.conn)
        self._memory = MemoryService(store_group.conn, store=self._memory_store)
        self._backend_resolver = MemoryBackendResolver(
            self._project_root,
            store_group=store_group,
        )

    # ------------------------------------------------------------------
    # Context 解析
    # ------------------------------------------------------------------

    async def resolve_context(
        self,
        *,
        active_project_id: str,
        project_id: str = "",
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

        bindings = await self._stores.project_store.list_bindings(project.project_id)
        scope_bindings: dict[str, _BoundScope] = {}
        for binding in bindings:
            if binding.binding_type not in _MEMORY_BINDING_TYPES:
                continue
            scope_bindings[binding.binding_key] = _BoundScope(
                scope_id=binding.binding_key,
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
        # 当 project 级别没有 memory_scope binding 时，直接查 DB 里所有存在数据的 scope
        if not selected_scope_ids:
            try:
                store = self._stores.memory_store if hasattr(self._stores, "memory_store") else None
                if store is None:
                    from octoagent.memory.store.memory_store import SqliteMemoryStore as _Store
                    store = _Store(self._stores.conn)
                all_scopes = await store.list_scope_ids()
                if all_scopes:
                    selected_scope_ids = sorted(all_scopes)
                    for sid in all_scopes:
                        scope_bindings[sid] = _BoundScope(
                            scope_id=sid,
                            binding_type=ProjectBindingType.MEMORY_SCOPE,
                        )
            except Exception:
                pass
        if not selected_scope_ids:
            blocking_issues.append("还没有记忆数据。去 Chat 对话后，系统会自动提取并存储记忆。")
        return _MemoryContext(
            project=project,
            scope_bindings=scope_bindings,
            selected_scope_ids=selected_scope_ids,
            warnings=warnings,
            blocking_issues=blocking_issues,
        )

    async def memory_service_for_context(self, context: _MemoryContext) -> MemoryService:
        backend = await self._backend_resolver.resolve_backend(
            project=context.project,
        )
        return MemoryService(
            self._stores.conn,
            store=self._memory_store,
            backend=backend,
        )

    # ------------------------------------------------------------------
    # 权限判定
    # ------------------------------------------------------------------

    def decide_project_scope_action(
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
                scope_id=required_scope_id,
            )
        if bypass_actor_check:
            return MemoryPermissionDecision(
                allowed=True,
                reason_code="MEMORY_PERMISSION_ALLOWED",
                message="允许访问。",
                project_id=context.project.project_id,
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
            scope_id=required_scope_id or "",
        )

    def decide_operator_only(
        self,
        *,
        action_id: str,
        actor_id: str,
        context: _MemoryContext,
        required_scope_id: str | None = None,
    ) -> MemoryPermissionDecision:
        decision = self.decide_project_scope_action(
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
                scope_id=required_scope_id or "",
            )
        return decision

    def decide_scope_list_bound(
        self,
        *,
        action_id: str,
        context: _MemoryContext,
        scope_ids: list[str],
    ) -> MemoryPermissionDecision | None:
        invalid_scope_ids = [
            scope_id for scope_id in scope_ids if scope_id not in context.scope_bindings
        ]
        if not invalid_scope_ids:
            return None
        return MemoryPermissionDecision(
            allowed=False,
            reason_code="MEMORY_PERMISSION_SCOPE_UNBOUND",
            message=(
                f"{action_id} 包含未绑定到当前 project 的 scope: "
                f"{', '.join(invalid_scope_ids)}"
            ),
            project_id=context.project.project_id,
            scope_id=invalid_scope_ids[0],
        )

    # ------------------------------------------------------------------
    # Projection 构造
    # ------------------------------------------------------------------

    @staticmethod
    def backend_index_health(backend_status: MemoryBackendStatus) -> dict[str, Any]:
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

    def fragment_projection(
        self,
        *,
        fragment,
        project_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=fragment.fragment_id,
            layer="fragment",
            project_id=project_id,
            scope_id=fragment.scope_id,
            partition=fragment.partition.value,
            summary=fragment.content[:240],
            created_at=fragment.created_at,
            evidence_refs=[item.model_dump(mode="json") for item in fragment.evidence_refs],
            metadata=fragment.metadata,
            retrieval_backend=retrieval_backend,
        )

    def derived_projection(
        self,
        *,
        derived,
        project_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=derived.derived_id,
            layer="derived",
            project_id=project_id,
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

    def derived_matches_query(self, derived, query: str) -> bool:
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

    def sor_projection(
        self,
        *,
        sor,
        project_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=sor.memory_id,
            layer="sor",
            project_id=project_id,
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

    def vault_projection(
        self,
        *,
        vault,
        project_id: str,
        retrieval_backend: str = "",
    ) -> MemoryRecordProjection:
        return MemoryRecordProjection(
            record_id=vault.vault_id,
            layer="vault",
            project_id=project_id,
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

    def projection_sort_key(self, item: MemoryRecordProjection) -> datetime:
        return item.updated_at or item.created_at

    # ------------------------------------------------------------------
    # Vault item 转换
    # ------------------------------------------------------------------

    def request_item(self, item) -> VaultAccessRequestItem:
        return VaultAccessRequestItem(
            request_id=item.request_id,
            project_id=item.project_id,
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

    def grant_item(self, item) -> VaultAccessGrantItem:
        return VaultAccessGrantItem(
            grant_id=item.grant_id,
            request_id=item.request_id,
            project_id=item.project_id,
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

    def retrieval_item(self, item) -> VaultRetrievalAuditItem:
        return VaultRetrievalAuditItem(
            retrieval_id=item.retrieval_id,
            project_id=item.project_id,
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

    async def normalize_grant(self, grant):
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
