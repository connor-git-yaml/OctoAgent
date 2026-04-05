"""Feature 027: Memory Console / Vault 授权桥接服务（Facade）。

本模块是 MemoryConsoleService 的外观层，将实际逻辑委托给四个子服务：
- MemoryConsoleView — 只读查询（overview / subject history / browse / proposal audit）
- MemoryVaultBridge — Vault 授权（查看 / 申请 / 审批 / 检索）
- MemoryExportService — 导出/导入（inspect export / verify restore）
- MemoryMaintenanceBridge — 维护操作（maintenance / consolidate）

共享基础设施（context 解析、权限判定、projection 构造）位于 _memory_console_base.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octoagent.core.models import (
    MemoryConsoleDocument,
    MemoryProposalAuditDocument,
    MemorySubjectHistoryDocument,
    VaultAuthorizationDocument,
)
from octoagent.memory import (
    MemoryBackendStatus,
    MemoryLayer,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRun,
    MemoryPartition,
    ProposalStatus,
    init_memory_db,
)

# 从 base 模块重新导出，保持外部 import 兼容
from ._memory_console_base import (  # noqa: F401
    MemoryConsoleBase,
    MemoryConsoleError,
    MemoryPermissionDecision,
)
from .memory_console_view import MemoryConsoleView
from .memory_export_service import MemoryExportService
from .memory_maintenance_bridge import MemoryMaintenanceBridge
from .memory_vault_bridge import MemoryVaultBridge


class MemoryConsoleService:
    """基于 Project 绑定产出 Memory Console 文档与动作结果。

    Facade 层——所有公共方法签名与旧实现完全一致，委托给对应的子服务。
    """

    def __init__(
        self,
        project_root: Path,
        *,
        store_group,
        llm_service=None,
        consolidation_service=None,
    ) -> None:
        self._base = MemoryConsoleBase(project_root, store_group=store_group)
        self._view = MemoryConsoleView(self._base)
        self._vault = MemoryVaultBridge(self._base)
        self._export = MemoryExportService(self._base)
        self._maintenance = MemoryMaintenanceBridge(
            self._base,
            llm_service=llm_service,
            consolidation_service=consolidation_service,
        )

    async def ensure_ready(self) -> None:
        await init_memory_db(self._base._stores.conn)

    # ------------------------------------------------------------------
    # 只读查询（委托 MemoryConsoleView）
    # ------------------------------------------------------------------

    async def get_backend_status(
        self,
        *,
        project_id: str = "",
    ) -> MemoryBackendStatus:
        """返回底层 memory backend 状态。"""
        context = await self._base.resolve_context(
            active_project_id=project_id or "",
            project_id=project_id or "",
        )
        memory = await self._base.memory_service_for_context(context)
        return await memory.get_backend_status()

    async def get_memory_console(
        self,
        *,
        project_id: str = "",
        scope_id: str | None = None,
        partition: MemoryPartition | None = None,
        layer: MemoryLayer | None = None,
        query: str | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
        limit: int = 50,
        derived_type: str = "",
        status: str = "",
        updated_after: str = "",
        updated_before: str = "",
    ) -> MemoryConsoleDocument:
        return await self.get_overview(
            active_project_id=project_id or "",
            project_id=project_id or "",
            scope_id=scope_id or "",
            partition=partition.value if partition else "",
            layer=layer.value if layer else "",
            query=query or "",
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
            derived_type=derived_type,
            status=status,
            updated_after=updated_after,
            updated_before=updated_before,
        )

    async def get_memory_subject_history(
        self,
        subject_key: str,
        *,
        project_id: str = "",
        scope_id: str | None = None,
    ) -> MemorySubjectHistoryDocument:
        return await self.get_subject_history(
            subject_key=subject_key,
            active_project_id=project_id or "",
            project_id=project_id or "",
            scope_id=scope_id or "",
        )

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
        return await self._view.get_overview(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            layer=layer,
            query=query,
            include_history=include_history,
            include_vault_refs=include_vault_refs,
            limit=limit,
            derived_type=derived_type,
            status=status,
            updated_after=updated_after,
            updated_before=updated_before,
        )

    async def get_subject_history(
        self,
        *,
        subject_key: str,
        active_project_id: str = "",
        project_id: str = "",
        scope_id: str = "",
    ) -> MemorySubjectHistoryDocument:
        return await self._view.get_subject_history(
            subject_key=subject_key,
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
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
        return await self._view.browse_memory(
            project_id=project_id,
            scope_id=scope_id,
            prefix=prefix,
            partition=partition,
            group_by=group_by,
            offset=offset,
            limit=limit,
        )

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
        return await self._view.get_proposal_audit(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
            status=status,
            source=source,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Vault 授权（委托 MemoryVaultBridge）
    # ------------------------------------------------------------------

    async def get_vault_authorization(
        self,
        *,
        active_project_id: str = "",
        project_id: str = "",
        scope_id: str = "",
        subject_key: str = "",
    ) -> VaultAuthorizationDocument:
        return await self._vault.get_vault_authorization(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
            subject_key=subject_key,
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
        return await self._vault.request_vault_access(
            actor_id=actor_id,
            actor_label=actor_label,
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
            reason=reason,
        )

    async def resolve_vault_access(
        self,
        *,
        actor_id: str,
        request_id: str,
        approved: bool,
        actor_label: str = "",
        expires_in_seconds: int = 0,
    ):
        return await self._vault.resolve_vault_access(
            actor_id=actor_id,
            request_id=request_id,
            approved=approved,
            actor_label=actor_label,
            expires_in_seconds=expires_in_seconds,
        )

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
        return await self._vault.retrieve_vault(
            actor_id=actor_id,
            actor_label=actor_label,
            active_project_id=active_project_id,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            subject_key=subject_key,
            query=query,
            grant_id=grant_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # 导出/导入（委托 MemoryExportService）
    # ------------------------------------------------------------------

    async def inspect_export(
        self,
        *,
        active_project_id: str = "",
        project_id: str = "",
        scope_ids: list[str] | None = None,
        include_history: bool = False,
        include_vault_refs: bool = False,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        return await self._export.inspect_export(
            active_project_id=active_project_id,
            project_id=project_id,
            scope_ids=scope_ids,
            include_history=include_history,
            include_vault_refs=include_vault_refs,
        )

    async def verify_restore(
        self,
        *,
        actor_id: str,
        active_project_id: str = "",
        project_id: str = "",
        snapshot_ref: str,
        target_scope_mode: str = "current_project",
        scope_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any], MemoryPermissionDecision]:
        return await self._export.verify_restore(
            actor_id=actor_id,
            active_project_id=active_project_id,
            project_id=project_id,
            snapshot_ref=snapshot_ref,
            target_scope_mode=target_scope_mode,
            scope_ids=scope_ids,
        )

    # ------------------------------------------------------------------
    # 维护操作（委托 MemoryMaintenanceBridge）
    # ------------------------------------------------------------------

    async def run_maintenance(
        self,
        *,
        kind: MemoryMaintenanceCommandKind,
        project_id: str = "",
        scope_id: str = "",
        partition: MemoryPartition | None = None,
        reason: str = "",
        summary: str = "",
        requested_by: str = "",
        evidence_refs=None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryMaintenanceRun:
        return await self._maintenance.run_maintenance(
            kind=kind,
            project_id=project_id,
            scope_id=scope_id,
            partition=partition,
            reason=reason,
            summary=summary,
            requested_by=requested_by,
            evidence_refs=evidence_refs,
            metadata=metadata,
        )

    async def run_consolidate(
        self,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        return await self._maintenance.run_consolidate(
            project_id=project_id,
        )
