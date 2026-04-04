"""AgentProfileDomainService -- Agent profile / policy profile 领域服务。

从 control_plane.py 拆分：
- get_agent_profiles_document / get_owner_profile_document / get_policy_profiles_document
- _handle_agent_profile_save / _handle_update_resource_limits / _handle_policy_profile_select
- _handle_agent_list_models / _handle_agent_list_archetypes / _handle_agent_list_tool_profiles
- _handle_agent_create_worker_with_project
- _resolve_active_agent_profile_payload / _merge_agent_profile_payload
- _policy_catalog / _policy_profile_by_id / _resolve_effective_policy_profile
- _sync_policy_engine_for_project / _describe_policy_approval / _tool_profile_allowed
- _list_available_model_aliases / _validate_model_alias
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.behavior_workspace import (
    ensure_filesystem_skeleton,
    materialize_agent_behavior_files,
    resolve_behavior_agent_slug,
)
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    AgentProfile,
    AgentProfileItem,
    AgentProfileScope,
    AgentProfilesDocument,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneResourceRef,
    ControlPlaneTargetRef,
    OwnerProfileDocument,
    PolicyProfileItem,
    PolicyProfilesDocument,
    Project,
    WorkerProfile,
    WorkerProfileOriginKind,
    WorkerProfileStatus,
)
from octoagent.core.models.agent_context import DEFAULT_PERMISSION_PRESET

from octoagent.provider.dx.config_wizard import load_config
from ulid import ULID

from ..agent_decision import build_behavior_system_summary
from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class AgentProfileDomainService(DomainServiceBase):
    """Agent profile / owner profile / policy profile 的全部 action / document 逻辑。"""

    def __init__(self, ctx: ControlPlaneContext) -> None:
        super().__init__(ctx)

    # ══════════════════════════════════════════════════════════════
    #  Action / Document Routes
    # ══════════════════════════════════════════════════════════════

    def action_routes(self) -> dict[str, Any]:
        return {
            "agent_profile.save": self._handle_agent_profile_save,
            "agent_profile.update_resource_limits": self._handle_update_resource_limits,
            "policy_profile.select": self._handle_policy_profile_select,
            "agent.list_available_models": self._handle_agent_list_models,
            "agent.list_worker_archetypes": self._handle_agent_list_archetypes,
            "agent.list_tool_profiles": self._handle_agent_list_tool_profiles,
            "agent.create_worker_with_project": self._handle_agent_create_worker_with_project,
        }

    def document_routes(self) -> dict[str, Any]:
        return {
            "agent_profiles": self.get_agent_profiles_document,
            "owner_profile": self.get_owner_profile_document,
            "policy_profiles": self.get_policy_profiles_document,
        }

    # ══════════════════════════════════════════════════════════════
    #  Resource Producers
    # ══════════════════════════════════════════════════════════════

    async def get_agent_profiles_document(self) -> AgentProfilesDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        # 全局加载所有 agent profiles，不按项目过滤——管理页面需要看到全部
        profiles = await self._stores.agent_context_store.list_agent_profiles()
        items = [
            AgentProfileItem(
                profile_id=profile.profile_id,
                scope=profile.scope.value,
                project_id=profile.project_id,
                name=profile.name,
                persona_summary=profile.persona_summary,
                model_alias=profile.model_alias,
                tool_profile=profile.tool_profile,
                memory_access_policy=dict(profile.memory_access_policy),
                context_budget_policy=dict(profile.context_budget_policy),
                bootstrap_template_ids=list(profile.bootstrap_template_ids),
                behavior_system=build_behavior_system_summary(
                    agent_profile=profile,
                    project_name=selected_project.name if selected_project is not None else "",
                    project_slug=selected_project.slug if selected_project is not None else "",
                    project_root=self._ctx.project_root,
                ),
                metadata=dict(profile.metadata),
                resource_limits=dict(profile.resource_limits),
                updated_at=profile.updated_at,
            )
            for profile in profiles
        ]
        return AgentProfilesDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            profiles=items,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="agent_profile.refresh",
                    label="刷新 Agent Profiles",
                    action_id="agent_profile.refresh",
                )
            ],
            warnings=[] if items else ["当前作用域没有可见的 agent profiles。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["agent_profiles_empty"] if not items else [],
            ),
        )

    async def get_owner_profile_document(self) -> OwnerProfileDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        profiles = await self._stores.agent_context_store.list_owner_profiles()
        profile = next(
            (item for item in profiles if item.owner_profile_id == "owner-profile-default"),
            profiles[0] if profiles else None,
        )
        overlays = (
            await self._stores.agent_context_store.list_owner_overlays(
                project_id=selected_project.project_id if selected_project is not None else None
            )
            if selected_project is not None
            else []
        )
        return OwnerProfileDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            profile=profile.model_dump(mode="json") if profile is not None else {},
            overlays=[item.model_dump(mode="json") for item in overlays],
            warnings=[] if profile is not None else ["尚未创建 owner profile。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=profile is None,
                reasons=["owner_profile_missing"] if profile is None else [],
            ),
        )

    async def get_policy_profiles_document(self) -> PolicyProfilesDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        active_profile_id, _ = self._resolve_effective_policy_profile(selected_project)
        profiles = [
            PolicyProfileItem(
                profile_id=profile_id,
                label=label,
                description=profile.description,
                allowed_tool_profile=profile.allowed_tool_profile.value,
                approval_policy=self._describe_policy_approval(profile),
                risk_level=risk_level,
                recommended_for=recommended_for,
                is_active=profile_id == active_profile_id,
            )
            for profile_id, label, profile, risk_level, recommended_for in self._policy_catalog()
        ]
        return PolicyProfilesDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            active_profile_id=active_profile_id,
            profiles=profiles,
            capabilities=[
                ControlPlaneCapability(
                    capability_id="policy_profile.select",
                    label="切换安全等级",
                    action_id="policy_profile.select",
                )
            ],
        )

    # ══════════════════════════════════════════════════════════════
    #  Action Handlers
    # ══════════════════════════════════════════════════════════════

    async def _handle_agent_profile_save(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        payload = request.params.get("profile")
        raw = payload if isinstance(payload, dict) else request.params
        _, selected_project, _, _ = await self._resolve_selection()
        scope = self._param_str(raw, "scope", default="project").lower()
        if scope not in {"system", "project"}:
            raise ControlPlaneActionError(
                "AGENT_PROFILE_SCOPE_INVALID", "scope 必须是 system/project"
            )
        project_id = self._param_str(raw, "project_id")
        if scope == "project" and not project_id:
            if selected_project is None:
                raise ControlPlaneActionError(
                    "PROJECT_REQUIRED",
                    "project scope 的 agent profile 需要 project_id",
                )
            project_id = selected_project.project_id
        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            profile_id = (
                f"agent-profile-{project_id or 'system-default'}"
                if scope == "project"
                else "agent-profile-system-default"
            )
        existing = await self._stores.agent_context_store.get_agent_profile(profile_id)
        profile = AgentProfileItem.model_validate(
            {
                "profile_id": profile_id,
                "scope": scope,
                "project_id": project_id,
                "name": self._param_str(raw, "name") or (existing.name if existing else ""),
                "persona_summary": self._param_str(raw, "persona_summary")
                or (existing.persona_summary if existing else ""),
                "model_alias": self._param_str(raw, "model_alias", default="main")
                or (existing.model_alias if existing else "main"),
                "tool_profile": self._param_str(raw, "tool_profile", default="standard")
                or (existing.tool_profile if existing else "standard"),
            }
        )
        if not profile.name:
            raise ControlPlaneActionError("AGENT_PROFILE_NAME_REQUIRED", "name 不能为空")
        model_alias_valid, available_aliases = self._validate_model_alias(profile.model_alias)
        if not model_alias_valid:
            raise ControlPlaneActionError(
                "AGENT_PROFILE_MODEL_ALIAS_INVALID",
                f"模型别名 '{profile.model_alias}' 不存在，可选：{', '.join(available_aliases)}",
            )
        saved = await self._stores.agent_context_store.save_agent_profile(
            AgentProfile(
                profile_id=profile.profile_id,
                scope=AgentProfileScope(profile.scope),
                project_id=profile.project_id,
                name=profile.name,
                persona_summary=profile.persona_summary,
                model_alias=profile.model_alias,
                tool_profile=profile.tool_profile,
                memory_access_policy=(
                    dict(raw.get("memory_access_policy", {}))
                    if isinstance(raw.get("memory_access_policy"), dict)
                    else {}
                ),
                context_budget_policy=(
                    dict(raw.get("context_budget_policy", {}))
                    if isinstance(raw.get("context_budget_policy"), dict)
                    else {}
                ),
                bootstrap_template_ids=[str(item) for item in raw.get("bootstrap_template_ids", [])]
                if isinstance(raw.get("bootstrap_template_ids"), list)
                else [],
                metadata=dict(raw.get("metadata", {}))
                if isinstance(raw.get("metadata"), dict)
                else {},
                resource_limits=dict(raw.get("resource_limits", {}))
                if isinstance(raw.get("resource_limits"), dict)
                else (dict(existing.resource_limits) if existing else {}),
                version=(existing.version if existing is not None else 1),
                created_at=(existing.created_at if existing is not None else datetime.now(tz=UTC)),
                updated_at=datetime.now(tz=UTC),
            )
        )
        set_as_default = (
            True
            if scope == "project" and "set_as_default" not in raw
            else self._param_bool(raw, "set_as_default")
        )
        target_project = None
        if scope == "project":
            target_project = await self._stores.project_store.get_project(project_id)
            if target_project is None:
                raise ControlPlaneActionError(
                    "PROJECT_NOT_FOUND",
                    "project_id 对应的 project 不存在",
                )
        if scope == "project" and target_project is not None and set_as_default:
            await self._stores.project_store.save_project(
                target_project.model_copy(
                    update={
                        "default_agent_profile_id": saved.profile_id,
                        "updated_at": datetime.now(tz=UTC),
                    }
                )
            )
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="AGENT_PROFILE_SAVED",
            message="主 Agent 设置已保存。",
            data={
                "profile_id": saved.profile_id,
                "project_id": saved.project_id,
                "scope": saved.scope.value,
                "set_as_default": scope == "project" and set_as_default,
            },
            resource_refs=[
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
                self._resource_ref("setup_governance", "setup:governance"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="agent_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_update_resource_limits(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """更新 Agent Profile 或 Worker Profile 的 resource_limits 字段。

        支持两种 target_type:
          - "agent_profile": 更新 AgentProfile.resource_limits
          - "worker_profile": 更新 WorkerProfile.resource_limits
        """
        raw = request.params
        target_type = self._param_str(raw, "target_type", default="agent_profile")
        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError(
                "PROFILE_ID_REQUIRED", "profile_id 不能为空。"
            )
        resource_limits = raw.get("resource_limits")
        if not isinstance(resource_limits, dict):
            raise ControlPlaneActionError(
                "RESOURCE_LIMITS_INVALID",
                "resource_limits 必须是 dict 类型。",
            )
        # 白名单校验：只允许 UsageLimits 已知字段
        allowed_keys = {
            "max_steps", "max_request_tokens", "max_response_tokens",
            "max_tool_calls", "max_budget_usd", "max_duration_seconds",
            "repeat_signature_threshold",
        }
        sanitized: dict[str, Any] = {}
        for key, value in resource_limits.items():
            if key in allowed_keys and value is not None:
                sanitized[key] = value

        resource_refs: list[ControlPlaneResourceRef] = []
        target_label = ""

        if target_type == "worker_profile":
            existing_wp = await self._stores.agent_context_store.get_worker_profile(profile_id)
            if existing_wp is None:
                raise ControlPlaneActionError(
                    "WORKER_PROFILE_NOT_FOUND",
                    f"找不到 worker profile: {profile_id}",
                )
            updated_wp = existing_wp.model_copy(
                update={
                    "resource_limits": sanitized,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            await self._stores.agent_context_store.save_worker_profile(updated_wp)
            resource_refs = [
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
            ]
            target_label = existing_wp.name
        else:
            existing_agent = await self._stores.agent_context_store.get_agent_profile(
                profile_id
            )
            if existing_agent is None:
                raise ControlPlaneActionError(
                    "AGENT_PROFILE_NOT_FOUND",
                    f"找不到 agent profile: {profile_id}",
                )
            updated_agent = existing_agent.model_copy(
                update={
                    "resource_limits": sanitized,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
            await self._stores.agent_context_store.save_agent_profile(updated_agent)
            resource_refs = [
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
            ]
            target_label = existing_agent.name

        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="RESOURCE_LIMITS_UPDATED",
            message="资源限制已更新。",
            data={
                "profile_id": profile_id,
                "target_type": target_type,
                "resource_limits": sanitized,
            },
            resource_refs=resource_refs,
            target_refs=[
                ControlPlaneTargetRef(
                    target_type=target_type,
                    target_id=profile_id,
                    label=target_label,
                )
            ],
        )

    async def _handle_policy_profile_select(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id").lower()
        if not profile_id:
            raise ControlPlaneActionError("POLICY_PROFILE_REQUIRED", "profile_id 不能为空")
        profile = self._policy_profile_by_id(profile_id)
        if profile is None:
            raise ControlPlaneActionError("POLICY_PROFILE_INVALID", "不支持的 policy profile")
        _, selected_project, _, _ = await self._resolve_selection()
        if selected_project is None:
            raise ControlPlaneActionError("PROJECT_REQUIRED", "当前没有可用 project")
        metadata = dict(selected_project.metadata)
        metadata["policy_profile_id"] = profile_id
        await self._stores.project_store.save_project(
            selected_project.model_copy(
                update={
                    "metadata": metadata,
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._sync_policy_engine_for_project(
            selected_project.model_copy(update={"metadata": metadata})
        )
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="POLICY_PROFILE_SELECTED",
            message="安全等级已更新。",
            data={
                "profile_id": profile_id,
                "allowed_tool_profile": profile.allowed_tool_profile.value,
                "approval_policy": self._describe_policy_approval(profile),
            },
            resource_refs=[
                self._resource_ref("policy_profiles", "policy:profiles"),
                self._resource_ref("setup_governance", "setup:governance"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="policy_profile",
                    target_id=profile_id,
                    label=profile_id,
                )
            ],
        )

    async def _handle_agent_list_models(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """返回已配置的模型别名列表，供主 Agent 选择。"""
        config = load_config(self._ctx.project_root)
        if config is None:
            return self._completed_result(
                request=request,
                code="AGENT_MODELS_LISTED",
                message="尚未配置 octoagent.yaml",
                data={"model_aliases": {}},
            )
        aliases: dict[str, dict[str, str]] = {}
        for alias_key, alias_val in config.model_aliases.items():
            aliases[alias_key] = {
                "provider": alias_val.provider,
                "model": alias_val.model,
                "description": alias_val.description,
            }
        return self._completed_result(
            request=request,
            code="AGENT_MODELS_LISTED",
            message=f"共 {len(aliases)} 个模型别名",
            data={"model_aliases": aliases},
        )

    async def _handle_agent_list_archetypes(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """返回内建 Worker archetype 列表。"""
        archetypes = [
            {
                "type": "general",
                "label": "通用",
                "description": "通用 Worker，适合多数场景",
            },
            {
                "type": "ops",
                "label": "运维",
                "description": "侧重运维操作（文件系统、Docker、监控）",
            },
            {
                "type": "research",
                "label": "调研",
                "description": "侧重信息搜集、网络检索、文档分析",
            },
            {
                "type": "dev",
                "label": "开发",
                "description": "侧重代码编写、测试、构建流程",
            },
        ]
        return self._completed_result(
            request=request,
            code="AGENT_ARCHETYPES_LISTED",
            message=f"共 {len(archetypes)} 个内建 archetype",
            data={"archetypes": archetypes},
        )

    async def _handle_agent_list_tool_profiles(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """返回工具权限等级列表。"""
        profiles = [
            {
                "profile": "minimal",
                "label": "最小",
                "description": "只读工具（查询、检索）",
            },
            {
                "profile": "standard",
                "label": "标准",
                "description": "读写工具（文件操作、记忆写入）",
            },
            {
                "profile": "privileged",
                "label": "特权",
                "description": "外部 API、Docker 执行、shell 命令",
            },
        ]
        return self._completed_result(
            request=request,
            code="AGENT_TOOL_PROFILES_LISTED",
            message=f"共 {len(profiles)} 个权限等级",
            data={"tool_profiles": profiles},
        )

    async def _handle_agent_create_worker_with_project(
        self, request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        """主 Agent 创建 Worker + Project + Session + 行为文件。"""
        # 参数解析
        worker_name = str(request.params.get("worker_name", "")).strip()
        project_name = str(request.params.get("project_name", "")).strip()
        model_alias = str(request.params.get("model_alias", "main")).strip()
        tool_profile = str(request.params.get("tool_profile", "minimal")).strip()
        project_goal = str(request.params.get("project_goal", "")).strip()
        # Feature 061 T-030: permission_preset + role_card 参数
        permission_preset = str(request.params.get("permission_preset", DEFAULT_PERMISSION_PRESET)).strip().lower()
        role_card = str(request.params.get("role_card", "")).strip()

        if not worker_name:
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_MISSING_NAME",
                message="请为新 Worker 输入名称。",
            )
        if not project_name:
            project_name = worker_name

        # 验证 tool_profile
        valid_profiles = {"minimal", "standard", "privileged"}
        if tool_profile not in valid_profiles:
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_INVALID_TOOL_PROFILE",
                message=f"tool_profile 必须是 {', '.join(sorted(valid_profiles))} 之一。",
            )

        # Feature 061 T-030: 验证 permission_preset
        valid_presets = {"minimal", "normal", "full"}
        if permission_preset not in valid_presets:
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_INVALID_PRESET",
                message=f"permission_preset 必须是 {', '.join(sorted(valid_presets))} 之一。",
            )

        # 验证 model_alias 存在
        model_alias_valid, available_aliases = self._validate_model_alias(model_alias)
        if not model_alias_valid:
            available = ", ".join(available_aliases)
            return self._rejected_result(
                request=request,
                code="WORKER_CREATE_INVALID_MODEL",
                message=f"模型别名 '{model_alias}' 不存在，可选：{available}",
            )

        now = datetime.now(tz=UTC)

        # 创建 WorkerProfile
        worker_profile_id = f"worker-profile-{str(ULID())}"
        worker_profile = WorkerProfile(
            profile_id=worker_profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="",  # 后面回填
            name=worker_name,
            summary=project_goal or f"{worker_name} Worker",
            model_alias=model_alias,
            tool_profile=tool_profile,
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_worker_profile(worker_profile)

        # 从 WorkerProfile 同步生成 AgentProfile
        agent_profile_id = f"agent-profile-{worker_profile_id}"
        agent_profile = AgentProfile(
            profile_id=agent_profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="",
            name=worker_name,
            persona_summary=project_goal,
            model_alias=model_alias,
            tool_profile=tool_profile,
        )
        await self._stores.agent_context_store.save_agent_profile(agent_profile)

        # 创建 Project
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", project_name.lower()).strip("-") or "worker"
        if not re.search(r"[a-z0-9]", slug):
            slug = f"worker-{str(ULID())[-6:]}"

        # 避免 slug 冲突
        existing = await self._stores.project_store.get_project_by_slug(slug)
        if existing is not None:
            slug = f"{slug}-{str(ULID())[-6:]}"

        project_id = f"project-{str(ULID())}"
        project = Project(
            project_id=project_id,
            slug=slug,
            name=project_name,
            description=project_goal or f"Worker「{worker_name}」的工作空间",
            status="active",
            is_default=False,
            default_agent_profile_id=agent_profile_id,
            primary_agent_id="",  # Worker 的 runtime_id 创建后回填
            created_at=now,
            updated_at=now,
        )
        await self._stores.project_store.create_project(project)

        # 回填 WorkerProfile 的 project_id
        worker_profile.project_id = project_id
        await self._stores.agent_context_store.save_worker_profile(worker_profile)
        agent_profile.project_id = project_id
        await self._stores.agent_context_store.save_agent_profile(agent_profile)

        # 创建行为文件骨架
        ensure_filesystem_skeleton(
            self._ctx.project_root,
            project_slug=slug,
        )
        agent_slug = resolve_behavior_agent_slug(agent_profile)
        materialize_agent_behavior_files(
            self._ctx.project_root,
            agent_slug=agent_slug,
            agent_name=worker_name,
            is_worker_profile=True,
        )

        # 创建 Worker AgentRuntime（FK 约束要求 agent_runtime_id 必须存在）
        runtime_id = f"runtime-{str(ULID())}"
        worker_runtime = AgentRuntime(
            agent_runtime_id=runtime_id,
            project_id=project_id,
            workspace_id="",
            agent_profile_id=agent_profile_id,
            worker_profile_id=worker_profile_id,
            role=AgentRuntimeRole.WORKER,
            name=worker_name,
            persona_summary=project_goal,
            status=AgentRuntimeStatus.ACTIVE,
            permission_preset=permission_preset,
            role_card=role_card,
            metadata={},
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_runtime(worker_runtime)

        # 创建 AgentSession（Direct Worker，确保会话列表可见）
        session_id = f"session-{str(ULID())}"
        session = AgentSession(
            agent_session_id=session_id,
            agent_runtime_id=runtime_id,
            project_id=project_id,
            workspace_id="",
            kind=AgentSessionKind.DIRECT_WORKER,
            status=AgentSessionStatus.ACTIVE,
            surface="chat",
            created_at=now,
            updated_at=now,
        )
        await self._stores.agent_context_store.save_agent_session(session)

        # 回填 Project 的 primary_agent_id
        await self._stores.project_store.set_primary_agent(project_id, runtime_id)

        await self._stores.conn.commit()

        return self._completed_result(
            request=request,
            code="WORKER_CREATED_WITH_PROJECT",
            message=f"已创建 Worker「{worker_name}」+ 项目「{project_name}」",
            data={
                "worker_profile_id": worker_profile_id,
                "agent_profile_id": agent_profile_id,
                "project_id": project_id,
                "workspace_id": "",
                "session_id": session_id,
                "runtime_id": runtime_id,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker_profiles:overview"),
                self._resource_ref("session_projection", "sessions:overview"),
            ],
        )

    # ══════════════════════════════════════════════════════════════
    #  Agent Profile Helpers
    # ══════════════════════════════════════════════════════════════

    def _list_available_model_aliases(self) -> list[str]:
        """返回当前配置中可用于 Agent / Worker 的模型别名集合。"""
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

    def _resolve_active_agent_profile_payload(
        self,
        *,
        agent_profiles: AgentProfilesDocument,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        if not agent_profiles.profiles:
            return {}
        if selected_project is not None and selected_project.default_agent_profile_id:
            matched = next(
                (
                    item
                    for item in agent_profiles.profiles
                    if item.profile_id == selected_project.default_agent_profile_id
                ),
                None,
            )
            if matched is not None:
                return matched.model_dump(mode="json")
        return agent_profiles.profiles[0].model_dump(mode="json")

    def _merge_agent_profile_payload(
        self,
        base: dict[str, Any],
        patch: dict[str, Any],
        *,
        selected_project: Any | None,
    ) -> dict[str, Any]:
        merged = self._deep_merge_dicts(base, patch)
        if (
            str(merged.get("scope", "")).strip().lower() == "project"
            and selected_project is not None
        ):
            merged.setdefault("project_id", selected_project.project_id)
        return merged

    def _deep_merge_dicts(
        self,
        base: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged
