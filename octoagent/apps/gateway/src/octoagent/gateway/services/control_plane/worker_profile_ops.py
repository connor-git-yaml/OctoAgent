"""F108a W4：WorkerProfileDomainService 的 worker profile 操作职责簇 mixin。

职责边界：worker profile 的派生与生命周期写入辅助——model alias 校验、
label / summary / snapshot_id / 工具选择派生、AgentProfile 镜像构建与同步、
默认绑定、动态上下文构建、profile id 生成、内建 source / scope 解析、
draft review / save / publish。新增"profile 派生 / 生命周期"类方法放这里，
防止职责堆回 worker_service.py。

依赖约定（由继承类 WorkerProfileDomainService 提供，经 MRO 解析）：
- ``self._ctx`` / ``self._stores`` / ``self._resolve_selection`` /
  ``self._param_str`` / ``self._normalize_dict`` /
  ``self._resolve_effective_policy_profile`` / ``self._tool_profile_allowed``
  （DomainServiceBase）
- ``self._get_capability_pack_document``（worker_service 主文件 D 簇）
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from octoagent.core.behavior_workspace import (
    materialize_agent_behavior_files,
    normalize_behavior_agent_slug,
)
from octoagent.core.models import (
    AgentProfile,
    AgentProfileDynamicContext,
    AgentProfileOriginKind,
    AgentProfileRevision,
    AgentProfileScope,
    AgentProfileStatus,
    ControlPlaneCapability,
    ControlPlaneSupportStatus,
    DynamicToolSelection,
    Work,
)
from octoagent.gateway.services.agent_context_helpers import (
    build_worker_agent_profile,
)
from octoagent.gateway.services.agent_decision import is_worker_behavior_profile
from octoagent.gateway.services.config.config_wizard import load_config
from ulid import ULID

from ._base import ControlPlaneActionError


class WorkerProfileOpsMixin:
    """Worker profile 操作职责簇：见模块 docstring。

    本 mixin 不持有状态，所有依赖（self._ctx / self._stores 等）由继承类
    WorkerProfileDomainService 提供。方法签名、返回值与副作用与拆分前完全
    等价（F108a 行为零变更）。
    """

    # ══════════════════════════════════════════════════════════════
    #  Model Alias Helpers
    # ══════════════════════════════════════════════════════════════

    def _list_available_model_aliases(self) -> list[str]:
        try:
            config = load_config(self._ctx.project_root)
        except Exception:
            return ["main"]
        if config is None or not config.model_aliases:
            return ["main"]
        aliases = sorted(alias for alias in config.model_aliases.keys() if alias.strip())
        return aliases or ["main"]

    def _validate_model_alias(self, model_alias: str) -> tuple[bool, list[str]]:
        available_aliases = self._list_available_model_aliases()
        return model_alias.strip() in available_aliases, available_aliases

    # ══════════════════════════════════════════════════════════════
    #  Worker Profile Helpers
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def _worker_profile_label(worker_type: str) -> str:
        labels = {
            "general": "Main Root Agent",
            "ops": "Ops Root Agent",
            "research": "Research Root Agent",
            "dev": "Dev Root Agent",
        }
        return labels.get(worker_type, worker_type)

    @staticmethod
    def _worker_profile_summary(capabilities: list[str], tool_groups: list[str]) -> str:
        capability_summary = "、".join(capabilities[:2]) if capabilities else "通用协调"
        tool_summary = "、".join(tool_groups[:2]) if tool_groups else "基础工具"
        return f"静态配置面向 {capability_summary}，当前默认能力组为 {tool_summary}。"

    @staticmethod
    def _worker_snapshot_id(profile_id: str, revision: int | None) -> str:
        resolved_revision = revision or 1
        return f"worker-snapshot:{profile_id}:{resolved_revision}"

    @staticmethod
    def _tool_selection_from_work(work: Work | None) -> DynamicToolSelection | None:
        if work is None:
            return None
        raw = work.metadata.get("tool_selection", {})
        if not isinstance(raw, dict):
            return None
        try:
            return DynamicToolSelection.model_validate(raw)
        except Exception:
            return None

    async def _sync_worker_profile_agent_profile(
        self,
        profile: AgentProfile,
    ) -> AgentProfile:
        # F117 Wave 2c-2a：authoring 持久化镜像统一走 canonical builder
        # （build_worker_agent_profile），产出**完整** worker 行——含运行时读的
        # instruction_overlays + context_budget_policy.memory_recall。W4-2a 已删
        # materialize-on-read（运行时读路径直接信任此持久化镜像）；W4-2b 已删旧
        # incomplete builder（instruction_overlays=[] / 无 memory_recall）→ canonical
        # builder 成为 worker 镜像的唯一 SoT（authoring 写 + listing 文档展示共用）。
        existing = await self._stores.agent_context_store.get_agent_profile(profile.profile_id)
        mirrored = build_worker_agent_profile(
            profile, existing_profile=existing, include_user_metadata=True
        )
        await self._stores.agent_context_store.save_agent_profile(mirrored)
        # 同步时确保 agent-private 行为文件存在。slug 直接 name-based（canonical 镜像不带
        # behavior_agent_slug metadata），与 baseline resolve_behavior_agent_slug(old_mirror)
        # 候选 #1 结果一致（normalize 幂等）。
        _slug = normalize_behavior_agent_slug(profile.name or profile.profile_id)
        materialize_agent_behavior_files(
            self._ctx.project_root,
            agent_slug=_slug,
            agent_name=profile.name,
            is_worker_profile=True,
        )
        return mirrored

    async def _bind_worker_profile_as_default(
        self,
        *,
        profile: AgentProfile,
    ) -> bool:
        if profile.scope != AgentProfileScope.PROJECT or not profile.project_id:
            return False
        project = await self._stores.project_store.get_project(profile.project_id)
        if project is None:
            return False
        if project.default_agent_profile_id == profile.profile_id:
            return False
        await self._stores.project_store.save_project(
            project.model_copy(
                update={
                    "default_agent_profile_id": profile.profile_id,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        return True

    def _build_worker_dynamic_context(
        self,
        works: list[Work],
        *,
        fallback_tools: list[str],
        fallback_project_id: str = "",
        fallback_workspace_id: str = "",
    ) -> AgentProfileDynamicContext:
        active_statuses = {
            "created",
            "assigned",
            "running",
            "waiting_input",
            "waiting_approval",
            "paused",
            "escalated",
        }
        running_statuses = {"created", "assigned", "running"}
        attention_statuses = {"waiting_input", "waiting_approval", "paused", "escalated", "failed"}
        latest = works[0] if works else None
        selection = self._tool_selection_from_work(latest)
        active_works = [item for item in works if item.status.value in active_statuses]
        attention_works = [item for item in works if item.status.value in attention_statuses]
        return AgentProfileDynamicContext(
            active_project_id=(
                latest.project_id if latest is not None else fallback_project_id
            ),
            active_workspace_id=(
                ""
            ),
            active_work_count=len(active_works),
            running_work_count=len(
                [item for item in active_works if item.status.value in running_statuses]
            ),
            attention_work_count=len(attention_works),
            latest_work_id=latest.work_id if latest is not None else "",
            latest_task_id=latest.task_id if latest is not None else "",
            latest_work_title=latest.title if latest is not None else "",
            latest_work_status=latest.status.value if latest is not None else "",
            latest_target_kind=latest.target_kind.value if latest is not None else "",
            current_selected_tools=(
                list(selection.effective_tool_universe.selected_tools)
                if selection is not None and selection.effective_tool_universe is not None
                else list(latest.selected_tools)
                if latest is not None and latest.selected_tools
                else list(fallback_tools)
            ),
            current_tool_resolution_mode=(
                selection.resolution_mode if selection is not None else ""
            ),
            current_tool_warnings=list(selection.warnings) if selection is not None else [],
            current_mounted_tools=(
                list(selection.mounted_tools) if selection is not None else []
            ),
            current_blocked_tools=(
                list(selection.blocked_tools) if selection is not None else []
            ),
            current_discovery_entrypoints=(
                list(selection.effective_tool_universe.discovery_entrypoints)
                if selection is not None and selection.effective_tool_universe is not None
                else []
            ),
            updated_at=latest.updated_at if latest is not None else None,
        )

    def _worker_profile_control_capabilities(
        self,
        status: AgentProfileStatus,
        *,
        builtin: bool = False,
    ) -> list[ControlPlaneCapability]:
        if builtin:
            return [
                ControlPlaneCapability(
                    capability_id="worker_profile.clone",
                    label="Fork 成自定义 Root Agent",
                    action_id="worker_profile.clone",
                ),
                ControlPlaneCapability(
                    capability_id="worker.spawn_from_profile",
                    label="按这个 Root Agent 启动",
                    action_id="worker.spawn_from_profile",
                ),
            ]
        is_archived = status == AgentProfileStatus.ARCHIVED
        return [
            ControlPlaneCapability(
                capability_id="worker_profile.review",
                label="检查 Profile",
                action_id="worker_profile.review",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能继续修改或发布。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.apply",
                label="保存草稿",
                action_id="worker_profile.apply",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能继续修改或发布。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.publish",
                label="发布 Revision",
                action_id="worker_profile.publish",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能继续发布 revision。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.bind_default",
                label="设为聊天默认",
                action_id="worker_profile.bind_default",
                enabled=not is_archived and status == AgentProfileStatus.ACTIVE,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if (not is_archived and status == AgentProfileStatus.ACTIVE)
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason=(
                    ""
                    if (not is_archived and status == AgentProfileStatus.ACTIVE)
                    else "只有已发布且未归档的 Root Agent 才能绑定为当前聊天默认。"
                ),
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.clone",
                label="复制为新 Profile",
                action_id="worker_profile.clone",
            ),
            ControlPlaneCapability(
                capability_id="worker.spawn_from_profile",
                label="按这个 Root Agent 启动",
                action_id="worker.spawn_from_profile",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="归档后不能再用于启动新任务。" if is_archived else "",
            ),
            ControlPlaneCapability(
                capability_id="worker_profile.archive",
                label="归档",
                action_id="worker_profile.archive",
                enabled=not is_archived,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if not is_archived
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason="当前 profile 已归档。" if is_archived else "",
            ),
        ]

    @staticmethod
    def _normalize_string_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _slugify_worker_profile_token(value: str) -> str:
        lowered = value.strip().lower()
        if not lowered:
            return "worker"
        chars: list[str] = []
        previous_dash = False
        for char in lowered:
            if char.isascii() and char.isalnum():
                chars.append(char)
                previous_dash = False
                continue
            if previous_dash:
                continue
            chars.append("-")
            previous_dash = True
        token = "".join(chars).strip("-")
        return token or "worker"

    async def _generate_worker_profile_id(
        self,
        *,
        name: str,
        project_id: str,
        scope: str,
        existing_profile_id: str = "",
    ) -> str:
        seed = self._slugify_worker_profile_token(name)
        scope_prefix = project_id or "system" if scope == "project" else "system"
        candidate = f"worker-profile-{scope_prefix}-{seed}"
        if existing_profile_id and existing_profile_id == candidate:
            return candidate
        existing = await self._get_worker_profile_via_mirror(candidate)
        if existing is None or existing.profile_id == existing_profile_id:
            return candidate
        return f"{candidate}-{str(ULID()).lower()[-6:]}"

    async def _resolve_builtin_worker_source(
        self,
        profile_id: str,
    ) -> AgentProfile | None:
        if not profile_id.startswith("singleton:"):
            return None
        worker_type = profile_id.split(":", 1)[1]
        capability_pack = await self._get_capability_pack_document()
        builtin = next(
            (
                item
                for item in capability_pack.pack.worker_profiles
                if item.worker_type == worker_type
            ),
            None,
        )
        if builtin is None:
            return None
        return AgentProfile(
            profile_id=profile_id,
            kind="worker",
            scope=AgentProfileScope.SYSTEM,
            project_id="",
            name=self._worker_profile_label(worker_type),
            summary=self._worker_profile_summary(
                list(builtin.capabilities),
                list(builtin.default_tool_groups),
            ),
            model_alias=builtin.default_model_alias,
            tool_profile=builtin.default_tool_profile,
            default_tool_groups=list(builtin.default_tool_groups),
            selected_tools=[],
            runtime_kinds=[item.value for item in builtin.runtime_kinds],
            metadata={},
            status=AgentProfileStatus.ACTIVE,
            origin_kind=AgentProfileOriginKind.BUILTIN,
            draft_revision=1,
            active_revision=1,
        )

    async def _get_worker_profile_via_mirror(self, profile_id: str) -> AgentProfile | None:
        """F117：authoring 读统一 agent_profiles(kind=worker) 行（worker 镜像即权威 profile）。

        镜像由 authoring 写路径（_sync / _save_draft）保持 current。非 worker 行视为不存在
        （保 baseline"只有 worker"语义）。W4-1 id-收口后镜像统一 bare id（agent_service/
        _coordinator 程序化创建已收口）。W4-3：直接返回统一 AgentProfile——authoring 不再
        round-trip 旧 DTO 类型（类型 cascade 已收口），source_* 标记常驻 metadata。
        """
        mirror = await self._stores.agent_context_store.get_agent_profile(profile_id)
        if mirror is None or not is_worker_behavior_profile(mirror):
            return None
        return mirror

    async def _get_worker_profile_in_scope(
        self,
        profile_id: str,
    ) -> AgentProfile:
        _, selected_project, _, _ = await self._resolve_selection()
        builtin = await self._resolve_builtin_worker_source(profile_id)
        if builtin is not None:
            return builtin
        profile = await self._get_worker_profile_via_mirror(profile_id)
        if profile is None:
            raise ControlPlaneActionError("WORKER_PROFILE_NOT_FOUND", "Root Agent profile 不存在")
        if (
            profile.scope == AgentProfileScope.PROJECT
            and selected_project is not None
            and profile.project_id
            and profile.project_id != selected_project.project_id
        ):
            raise ControlPlaneActionError(
                "WORKER_PROFILE_NOT_IN_SCOPE",
                "当前 project 不能操作这个 Root Agent profile。",
            )
        return profile

    async def _get_work_in_scope(self, work_id: str) -> Work:
        work = await self._stores.work_store.get_work(work_id)
        if work is None:
            raise ControlPlaneActionError("WORK_NOT_FOUND", "work 不存在")
        return work

    async def _review_worker_profile_draft(
        self,
        *,
        raw: Mapping[str, Any],
        mode: str,
        existing: AgentProfile | None = None,
        source_profile: AgentProfile | None = None,
        selected_project: Any | None,
        origin_kind: AgentProfileOriginKind | None = None,
    ) -> dict[str, Any]:
        capability_pack = await self._get_capability_pack_document()
        builtin_defaults = {
            item.worker_type: item for item in capability_pack.pack.worker_profiles
        }
        available_tool_groups = sorted(
            {
                tool.tool_group
                for tool in capability_pack.pack.tools
                if str(tool.tool_group).strip()
            }
        )
        available_tools = {
            tool.tool_name: tool for tool in capability_pack.pack.tools if str(tool.tool_name).strip()
        }
        valid_runtime_kinds = {"worker", "subagent", "acp_runtime", "graph_agent"}
        valid_tool_profiles = {"minimal", "standard", "privileged"}

        existing_data = existing.model_dump(mode="json") if existing is not None else {}
        source_data = source_profile.model_dump(mode="json") if source_profile is not None else {}
        scope = (
            self._param_str(raw, "scope")
            or str(existing_data.get("scope", ""))
            or str(source_data.get("scope", ""))
            or ("project" if selected_project is not None else "system")
        ).lower()
        project_id = (
            self._param_str(raw, "project_id")
            or str(existing_data.get("project_id", ""))
            or str(source_data.get("project_id", ""))
            or (selected_project.project_id if selected_project is not None else "")
        )
        name = (
            self._param_str(raw, "name")
            or str(existing_data.get("name", ""))
            or str(source_data.get("name", ""))
        )
        summary = (
            self._param_str(raw, "summary")
            or str(existing_data.get("summary", ""))
            or str(source_data.get("summary", ""))
        )
        builtin = builtin_defaults.get("general")
        default_tool_groups = self._normalize_string_list(raw.get("default_tool_groups"))
        if not default_tool_groups:
            default_tool_groups = (
                self._normalize_string_list(existing_data.get("default_tool_groups"))
                or self._normalize_string_list(source_data.get("default_tool_groups"))
                or (list(builtin.default_tool_groups) if builtin is not None else [])
            )
        selected_tools = self._normalize_string_list(raw.get("selected_tools"))
        if not selected_tools:
            selected_tools = self._normalize_string_list(existing_data.get("selected_tools")) or self._normalize_string_list(source_data.get("selected_tools"))
        runtime_kinds = self._normalize_string_list(raw.get("runtime_kinds"))
        if not runtime_kinds:
            runtime_kinds = (
                self._normalize_string_list(existing_data.get("runtime_kinds"))
                or self._normalize_string_list(source_data.get("runtime_kinds"))
                or ([item.value for item in builtin.runtime_kinds] if builtin is not None else ["worker"])
            )
        model_alias = (
            self._param_str(raw, "model_alias", default="")
            or str(existing_data.get("model_alias", ""))
            or str(source_data.get("model_alias", ""))
            or (builtin.default_model_alias if builtin is not None else "main")
        )
        tool_profile = (
            self._param_str(raw, "tool_profile", default="")
            or str(existing_data.get("tool_profile", ""))
            or str(source_data.get("tool_profile", ""))
            or (builtin.default_tool_profile if builtin is not None else "minimal")
        )
        metadata = self._normalize_dict(raw.get("metadata"))
        if not metadata:
            metadata = self._normalize_dict(existing_data.get("metadata")) or self._normalize_dict(source_data.get("metadata"))
        resource_limits = self._normalize_dict(raw.get("resource_limits"))
        if not resource_limits:
            resource_limits = self._normalize_dict(existing_data.get("resource_limits")) or self._normalize_dict(source_data.get("resource_limits"))

        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            profile_id = str(existing_data.get("profile_id", "")) or str(source_data.get("profile_id", ""))
        if not profile_id or profile_id.startswith("singleton:"):
            profile_id = await self._generate_worker_profile_id(
                name=name or "",
                project_id=project_id,
                scope=scope,
                existing_profile_id=existing.profile_id if existing is not None else "",
            )

        normalized = {
            "profile_id": profile_id,
            "scope": scope,
            "project_id": project_id if scope == "project" else "",
            "name": name,
            "summary": summary
            or self._worker_profile_summary(default_tool_groups, default_tool_groups),
            "model_alias": model_alias or "main",
            "tool_profile": tool_profile or "minimal",
            "default_tool_groups": default_tool_groups,
            "selected_tools": selected_tools,
            "runtime_kinds": runtime_kinds,
            "metadata": metadata,
            "resource_limits": resource_limits,
            "origin_kind": (
                origin_kind.value
                if origin_kind is not None
                else (
                    existing.origin_kind.value
                    if existing is not None
                    else (
                        source_profile.origin_kind.value
                        if source_profile is not None
                        and source_profile.origin_kind != AgentProfileOriginKind.BUILTIN
                        else AgentProfileOriginKind.CUSTOM.value
                    )
                )
            ),
        }

        save_errors: list[str] = []
        blocking_reasons: list[str] = []
        warnings: list[str] = []
        if scope not in {"system", "project"}:
            save_errors.append("scope 只支持 system / project。")
        if not name:
            save_errors.append("name 不能为空。")
        if scope == "project" and not project_id:
            save_errors.append("project scope 的 Root Agent 需要 project_id。")
        model_alias_valid, available_aliases = self._validate_model_alias(model_alias or "main")
        if not model_alias_valid:
            save_errors.append(
                "model_alias 必须引用已存在的模型别名。"
                f" 当前为 '{model_alias or 'main'}'，可选：{', '.join(available_aliases)}。"
            )
        if tool_profile not in valid_tool_profiles:
            save_errors.append("tool_profile 只支持 minimal / standard / privileged。")
        invalid_runtime_kinds = [item for item in runtime_kinds if item not in valid_runtime_kinds]
        if invalid_runtime_kinds:
            save_errors.append(
                f"runtime_kinds 含无效值：{'、'.join(invalid_runtime_kinds)}。"
            )
        missing_tool_groups = [
            item for item in default_tool_groups if item not in available_tool_groups
        ]
        if missing_tool_groups:
            blocking_reasons.append(
                f"默认工具组不存在：{'、'.join(missing_tool_groups)}。"
            )
        missing_tools = [item for item in selected_tools if item not in available_tools]
        if missing_tools:
            blocking_reasons.append(f"选中的工具不存在：{'、'.join(missing_tools)}。")
        unavailable_tools = [
            item
            for item in selected_tools
            if item in available_tools
            and available_tools[item].availability.value != "available"
        ]
        if unavailable_tools:
            warnings.append(
                f"这些工具当前不是 available：{'、'.join(unavailable_tools)}。"
            )
        if not default_tool_groups and not selected_tools:
            warnings.append("当前没有默认工具组和固定工具，运行时会更依赖动态 tool index。")
        if not summary:
            warnings.append("建议补一段 summary，方便主 Agent 和 Control Plane 解释这个 Root Agent。")
        if selected_project is not None:
            policy_profile_id, policy_profile = self._resolve_effective_policy_profile(
                selected_project
            )
            if not self._tool_profile_allowed(tool_profile, policy_profile.allowed_tool_profile):
                warnings.append(
                    "当前 profile 的 tool_profile 高于当前 project policy，运行时可能被降级或要求审批。"
                )
        if existing is not None and existing.status == AgentProfileStatus.ARCHIVED:
            save_errors.append("归档后的 Root Agent 不能直接更新，请先 clone 一个新 profile。")

        snapshot_fields = (
            "name",
            "summary",
            "model_alias",
            "tool_profile",
            "default_tool_groups",
            "selected_tools",
            "runtime_kinds",
        )
        diff_items: list[dict[str, Any]] = []
        before_payload = existing_data or source_data
        for field in snapshot_fields:
            before_value = before_payload.get(field)
            after_value = normalized.get(field)
            if before_value != after_value:
                diff_items.append(
                    {
                        "field": field,
                        "before": before_value,
                        "after": after_value,
                    }
                )

        next_actions: list[str] = []
        if save_errors:
            next_actions.append("先补齐必填字段，再保存或发布这个 Root Agent。")
        elif blocking_reasons:
            next_actions.append("先处理工具组或工具可用性问题，再发布 revision。")
        else:
            next_actions.append("检查通过，可以保存草稿或直接发布 revision。")
        if not selected_tools:
            next_actions.append("如果你希望行为更稳定，建议至少 pin 1-3 个核心工具。")

        return {
            "mode": mode,
            "can_save": not save_errors,
            "ready": not save_errors and not blocking_reasons,
            "warnings": warnings,
            "save_errors": save_errors,
            "blocking_reasons": blocking_reasons,
            "next_actions": next_actions,
            "profile": normalized,
            "existing_profile": existing_data,
            "source_profile": source_data,
            "diff": {
                "has_changes": bool(diff_items),
                "changed_fields": diff_items,
            },
            "catalog": {
                "tool_group_count": len(available_tool_groups),
                "tool_count": len(available_tools),
                "available_tool_groups": available_tool_groups,
            },
            "dynamic_context_hint": {
                "project_id": selected_project.project_id if selected_project is not None else "",
                "workspace_id": "",
            },
        }

    def _worker_profile_snapshot_payload(self, profile: AgentProfile) -> dict[str, Any]:
        return {
            "profile_id": profile.profile_id,
            "scope": profile.scope.value,
            "project_id": profile.project_id,
            "name": profile.name,
            "summary": profile.summary,
            "model_alias": profile.model_alias,
            "tool_profile": profile.tool_profile,
            "default_tool_groups": list(profile.default_tool_groups),
            "selected_tools": list(profile.selected_tools),
            "runtime_kinds": list(profile.runtime_kinds),
            "metadata": dict(profile.metadata),
            "resource_limits": dict(profile.resource_limits),
            "origin_kind": profile.origin_kind.value,
        }

    async def _save_worker_profile_draft(
        self,
        *,
        normalized_profile: Mapping[str, Any],
        existing: AgentProfile | None,
        origin_kind: AgentProfileOriginKind | None = None,
    ) -> AgentProfile:
        now = datetime.now(tz=UTC)
        resolved_origin = (
            origin_kind
            if origin_kind is not None
            else (
                existing.origin_kind
                if existing is not None
                else AgentProfileOriginKind(
                    str(normalized_profile.get("origin_kind", AgentProfileOriginKind.CUSTOM.value))
                )
            )
        )
        if existing is None:
            status = AgentProfileStatus.DRAFT
            draft_revision = 1
            active_revision = 0
            created_at = now
        else:
            status = (
                AgentProfileStatus.ACTIVE
                if existing.active_revision > 0 and existing.status != AgentProfileStatus.ARCHIVED
                else AgentProfileStatus.DRAFT
            )
            draft_revision = (
                max(existing.draft_revision, existing.active_revision + 1)
                if existing.active_revision > 0
                else max(existing.draft_revision, 1)
            )
            active_revision = existing.active_revision
            created_at = existing.created_at
        # F117 Wave 4-3：停写 worker_profiles——构造 in-memory worker AgentProfile 承载 status/
        # revision 派生，再经 canonical builder 规范化为持久化镜像（agent_profiles 单写，Option B）。
        working = AgentProfile(
            profile_id=str(normalized_profile.get("profile_id", "")),
            kind="worker",
            scope=AgentProfileScope(str(normalized_profile.get("scope", "project"))),
            project_id=str(normalized_profile.get("project_id", "")),
            name=str(normalized_profile.get("name", "")),
            summary=str(normalized_profile.get("summary", "")),
            model_alias=str(normalized_profile.get("model_alias", "main")),
            tool_profile=str(normalized_profile.get("tool_profile", "minimal")),
            default_tool_groups=self._normalize_string_list(
                normalized_profile.get("default_tool_groups")
            ),
            selected_tools=self._normalize_string_list(
                normalized_profile.get("selected_tools")
            ),
            runtime_kinds=self._normalize_string_list(
                normalized_profile.get("runtime_kinds")
            ),
            metadata=self._normalize_dict(normalized_profile.get("metadata")),
            resource_limits=self._normalize_dict(normalized_profile.get("resource_limits")),
            status=status,
            origin_kind=resolved_origin,
            draft_revision=draft_revision,
            active_revision=active_revision,
            created_at=created_at,
            updated_at=now,
            archived_at=None,
        )
        # canonical builder 补齐运行时字段（instruction_overlays + memory_recall +
        # bootstrap_template_ids + source_* 标记），与 publish/bind 的 _sync 路径同 builder。
        # 草稿写入即刷新同 id 镜像（用户拍板"草稿即时生效"，read-switch 后未发布 worker 也立即
        # 被运行时解析）；不 materialize 行为文件（避免给频繁保存引入副作用，publish/bind 走 _sync 含 materialize）。
        existing_mirror = await self._stores.agent_context_store.get_agent_profile(
            working.profile_id
        )
        mirror = build_worker_agent_profile(
            working, existing_profile=existing_mirror, include_user_metadata=True
        )
        await self._stores.agent_context_store.save_agent_profile(mirror)
        await self._stores.conn.commit()
        # 返回镜像本身（含 source_* 标记），与读路径 _get_worker_profile_via_mirror 形状一致——
        # apply+publish（喂 saved）与独立 publish（喂读到的 existing）的 snapshot_payload 守恒。
        return mirror

    async def _publish_worker_profile_revision(
        self,
        *,
        profile: AgentProfile,
        change_summary: str,
        actor: str,
    ) -> tuple[AgentProfile, AgentProfileRevision, bool]:
        revisions = await self._stores.agent_context_store.list_agent_profile_revisions(
            profile.profile_id
        )
        snapshot_payload = self._worker_profile_snapshot_payload(profile)
        latest = revisions[0] if revisions else None
        if (
            latest is not None
            and latest.snapshot_payload == snapshot_payload
            and latest.revision == profile.active_revision
            and profile.status == AgentProfileStatus.ACTIVE
        ):
            return profile, latest, False

        next_revision = profile.draft_revision or profile.active_revision or 1
        if next_revision <= profile.active_revision:
            next_revision = profile.active_revision + 1
        # F117 Wave 2c-2c-W：revision 写统一 agent_profile_revisions（FK→agent_profiles，与停写
        # worker_profiles 一致；worker_profile_revisions FK→worker_profiles 会因停写违约 500）。
        revision = await self._stores.agent_context_store.save_agent_profile_revision(
            AgentProfileRevision(
                revision_id=self._worker_snapshot_id(profile.profile_id, next_revision),
                profile_id=profile.profile_id,
                revision=next_revision,
                change_summary=change_summary,
                snapshot_payload=snapshot_payload,
                created_by=actor,
                created_at=datetime.now(tz=UTC),
            )
        )
        # F117 Wave 2c-2c-W：停写 worker_profiles——in-memory model_copy；镜像由调用方 handler 的
        # _sync_worker_profile_agent_profile 写。删 _publish 内部 commit → revision + 镜像 + bind 由
        # handler 单事务原子提交（根除 Codex 双评审 [3] commit-between 陈旧镜像窗口）。
        updated = profile.model_copy(
            update={
                "status": AgentProfileStatus.ACTIVE,
                "active_revision": next_revision,
                "draft_revision": next_revision,
                "updated_at": datetime.now(tz=UTC),
                "archived_at": None,
            }
        )
        return updated, revision, True
