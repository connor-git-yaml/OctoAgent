"""Memory Console 维护操作子服务。

包含 run_maintenance、run_consolidate 等维护性操作。
"""

from __future__ import annotations

from typing import Any

from octoagent.memory import (
    MemoryMaintenanceCommand,
    MemoryMaintenanceCommandKind,
    MemoryMaintenanceRun,
    MemoryPartition,
)
from ulid import ULID

from ._memory_console_base import (
    MemoryConsoleBase,
    MemoryConsoleError,
)
from octoagent.gateway.services.config.config_wizard import load_config


class MemoryMaintenanceBridge:
    """Memory 维护操作——maintenance / consolidate。"""

    def __init__(
        self,
        base: MemoryConsoleBase,
        *,
        llm_service=None,
        consolidation_service=None,
    ) -> None:
        self._base = base
        self._llm_service = llm_service
        # Feature 065: ConsolidationService 注入（可选，为 None 时退化为旧路径报错）
        if consolidation_service is not None:
            self._consolidation_service = consolidation_service
        else:
            from octoagent.gateway.services.inference.consolidation_service import (
                ConsolidationService,
            )
            self._consolidation_service = ConsolidationService(
                memory_store=self._base._memory_store,
                llm_service=llm_service,
                project_root=self._base._project_root,
            )

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
        """执行 project 绑定后的 memory maintenance。"""

        context = await self._base.resolve_context(
            active_project_id=project_id or "",
            project_id=project_id or "",
            scope_id=scope_id,
        )
        memory = await self._base.memory_service_for_context(context)
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

    async def run_consolidate(
        self,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        """使用 LLM 将待整理 fragment 整合为 SoR 现行事实。

        委托 ConsolidationService.consolidate_all_pending 执行实际逻辑。

        Returns:
            包含 consolidated_count, skipped_count, errors 等统计信息的字典。
        """
        if self._llm_service is None:
            raise MemoryConsoleError(
                "CONSOLIDATE_NO_LLM",
                "记忆整理需要 LLM 服务，但当前未配置。请在 Settings 中配置模型。",
            )

        # 1. 解析 context 和 scope
        context = await self._base.resolve_context(
            active_project_id=project_id or "",
            project_id=project_id or "",
        )
        if not context.selected_scope_ids:
            return {"consolidated_count": 0, "skipped_count": 0, "errors": [], "message": "没有可用的 scope"}

        memory = await self._base.memory_service_for_context(context)

        # 2. 解析模型别名
        config = load_config(self._base._project_root)
        model_alias = (config.memory.reasoning_model_alias if config else "") or "main"

        # 3. 委托 ConsolidationService 逐 scope 处理
        result = await self._consolidation_service.consolidate_all_pending(
            memory=memory,
            scope_ids=context.selected_scope_ids,
            model_alias=model_alias,
        )

        return {
            "consolidated_count": result.total_consolidated,
            "skipped_count": result.total_skipped,
            "errors": result.all_errors,
            "model_alias": model_alias,
            "message": f"已整理 {result.total_consolidated} 条事实"
            if result.total_consolidated
            else "没有可提取的新事实",
        }
