"""F108a W3：SetupDomainService 的 skill selection 职责簇 mixin。

职责边界：skill selection payload 归一化（含 allowed item 校验）、project 级
selection 解析（draft 优先于 project metadata）与对 governance items 的投影。
新增"skill 选择"类方法放这里，防止职责堆回 setup_service.py。

依赖约定（由继承类 SetupDomainService 提供，经 MRO 解析）：
- ``self._action_error``（DomainServiceBase）
- ``self.get_skill_governance_document``（setup_service 主文件 resource producer）
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from octoagent.core.models import SkillGovernanceItem


class SetupSkillSelectionMixin:
    """Skill selection 职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._action_error 等）由继承类
    SetupDomainService 提供。方法签名、返回值与副作用与拆分前完全等价
    （F108a 行为零变更）。
    """

    # ── skill selection ──────────────────────────────────────────

    def _normalize_skill_selection_payload(
        self,
        selection: Mapping[str, Any],
        *,
        allowed_item_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        selected_item_ids = {
            str(item).strip()
            for item in selection.get("selected_item_ids", [])
            if str(item).strip()
        }
        disabled_item_ids = {
            str(item).strip()
            for item in selection.get("disabled_item_ids", [])
            if str(item).strip()
        }
        overlap = sorted(selected_item_ids & disabled_item_ids)
        if overlap:
            raise self._action_error(
                "SKILL_SELECTION_CONFLICT",
                f"skill selection 同时出现在 enabled/disabled 列表：{overlap[0]}",
            )
        if allowed_item_ids is not None:
            unknown = sorted((selected_item_ids | disabled_item_ids) - allowed_item_ids)
            if unknown:
                raise self._action_error(
                    "SKILL_SELECTION_UNKNOWN_ITEM",
                    f"未知的 skill governance item: {unknown[0]}",
                )
        return {
            "selected_item_ids": sorted(selected_item_ids),
            "disabled_item_ids": sorted(disabled_item_ids),
            "updated_at": datetime.now(tz=UTC).isoformat(),
        }

    async def _normalize_skill_selection_for_scope(
        self,
        selection: Mapping[str, Any],
        *,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        if selected_project is None:
            raise self._action_error("PROJECT_REQUIRED", "当前没有可用 project")
        document = await self.get_skill_governance_document(
            selected_project=selected_project,
        )
        allowed_item_ids = {item.item_id for item in document.items}
        return self._normalize_skill_selection_payload(
            selection,
            allowed_item_ids=allowed_item_ids,
        )

    def _resolve_project_skill_selection(
        self,
        selected_project: Any | None,
        *,
        draft_selection: Mapping[str, Any] | None = None,
    ) -> tuple[set[str], set[str]]:
        selection = draft_selection
        if selection is None and selected_project is not None:
            metadata = (
                dict(selected_project.metadata)
                if isinstance(getattr(selected_project, "metadata", None), dict)
                else {}
            )
            raw = metadata.get("skill_selection")
            if isinstance(raw, Mapping):
                selection = raw
        if selection is None:
            return set(), set()
        selected_item_ids = {
            str(item).strip()
            for item in selection.get("selected_item_ids", [])
            if str(item).strip()
        }
        disabled_item_ids = {
            str(item).strip()
            for item in selection.get("disabled_item_ids", [])
            if str(item).strip()
        }
        return selected_item_ids, disabled_item_ids

    def _skill_item_selected(
        self,
        *,
        item_id: str,
        enabled_by_default: bool,
        selected_item_ids: set[str],
        disabled_item_ids: set[str],
    ) -> tuple[bool, str]:
        if item_id in selected_item_ids:
            return True, "project_override"
        if item_id in disabled_item_ids:
            return False, "project_override"
        return enabled_by_default, "default"

    def _apply_skill_selection_to_items(
        self,
        *,
        items: list[SkillGovernanceItem],
        selected_project: Any | None,
        draft_selection: Mapping[str, Any] | None = None,
    ) -> list[SkillGovernanceItem]:
        selected_item_ids, disabled_item_ids = self._resolve_project_skill_selection(
            selected_project,
            draft_selection=draft_selection,
        )
        projected: list[SkillGovernanceItem] = []
        for item in items:
            selected, selection_source = self._skill_item_selected(
                item_id=item.item_id,
                enabled_by_default=item.enabled_by_default,
                selected_item_ids=selected_item_ids,
                disabled_item_ids=disabled_item_ids,
            )
            projected.append(
                item.model_copy(
                    update={
                        "selected": selected,
                        "selection_source": selection_source,
                    }
                )
            )
        return projected
