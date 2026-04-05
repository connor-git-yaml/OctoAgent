"""WorkerProfileDomainService -- Worker profile 领域服务。

从 control_plane.py 拆分：
- get_worker_profiles_document / get_worker_profile_revisions_document
- _handle_worker_profile_review / create / update / clone / archive / apply / publish / bind_default
- _handle_worker_spawn_from_profile / _handle_worker_extract_profile_from_runtime
- _handle_behavior_read_file / _handle_behavior_write_file
- _build_agent_profile_from_worker_profile / _sync_worker_profile_agent_profile
- _bind_worker_profile_as_default / _build_worker_dynamic_context / _worker_snapshot_id
- _get_worker_profile_in_scope / _resolve_builtin_worker_source / _review_worker_profile_draft
- _save_worker_profile_draft / _publish_worker_profile_revision
- _worker_profile_control_capabilities / _worker_profile_label / _worker_profile_summary
- _worker_profile_snapshot_payload / _generate_worker_profile_id / _slugify_worker_profile_token
- _normalize_text_list / _normalize_string_list / _normalize_dict / _tool_selection_from_work
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog
from octoagent.core.behavior_workspace import (
    check_behavior_file_budget,
    materialize_agent_behavior_files,
    read_behavior_file_content,
    resolve_behavior_agent_slug,
    resolve_write_path_by_file_id,
    validate_behavior_file_path,
)
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    AgentProfile,
    AgentProfileScope,
    CapabilityPackDocument,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneResourceRef,
    ControlPlaneSupportStatus,
    ControlPlaneTargetRef,
    DynamicToolSelection,
    NormalizedMessage,
    TurnExecutorKind,
    Work,
    WorkerProfile,
    WorkerProfileDynamicContext,
    WorkerProfileOriginKind,
    WorkerProfileRevision,
    WorkerProfileRevisionItem,
    WorkerProfileRevisionsDocument,
    WorkerProfilesDocument,
    WorkerProfileStaticConfig,
    WorkerProfileStatus,
    WorkerProfileViewItem,
)
from octoagent.gateway.services.config.config_wizard import load_config
from ulid import ULID

from ..agent_decision import build_behavior_system_summary
from ..task_service import TaskService
from ._base import ControlPlaneActionError, ControlPlaneContext, DomainServiceBase

log = structlog.get_logger()


class WorkerProfileDomainService(DomainServiceBase):
    """Worker profile 的全部 action / document / lifecycle 逻辑。"""

    def __init__(self, ctx: ControlPlaneContext) -> None:
        super().__init__(ctx)

    # ══════════════════════════════════════════════════════════════
    #  Action / Document Routes
    # ══════════════════════════════════════════════════════════════

    def action_routes(self) -> dict[str, Any]:
        return {
            "worker_profile.review": self._handle_worker_profile_review,
            "worker_profile.create": self._handle_worker_profile_create,
            "worker_profile.update": self._handle_worker_profile_update,
            "worker_profile.clone": self._handle_worker_profile_clone,
            "worker_profile.archive": self._handle_worker_profile_archive,
            "worker_profile.apply": self._handle_worker_profile_apply,
            "worker_profile.publish": self._handle_worker_profile_publish,
            "worker_profile.bind_default": self._handle_worker_profile_bind_default,
            "worker.spawn_from_profile": self._handle_worker_spawn_from_profile,
            "worker.extract_profile_from_runtime": self._handle_worker_extract_profile_from_runtime,
            "behavior.read_file": self._handle_behavior_read_file,
            "behavior.write_file": self._handle_behavior_write_file,
        }

    def document_routes(self) -> dict[str, Any]:
        return {
            "worker_profiles": self.get_worker_profiles_document,
            "worker_profile_revisions": self.get_worker_profile_revisions_document,
        }

    # ══════════════════════════════════════════════════════════════
    #  Resource Producers
    # ══════════════════════════════════════════════════════════════

    async def get_worker_profiles_document(self) -> WorkerProfilesDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        capability_pack = await self._get_capability_pack_document()
        # 全局加载所有 worker profiles，不按项目过滤——Agents 管理页面需要看到全部
        stored_profiles = await self._stores.agent_context_store.list_worker_profiles(
            include_archived=True,
        )
        project_works: list[Work] = []
        if self._ctx.delegation_plane_service is not None:
            project_works = await self._ctx.delegation_plane_service.list_works()
        worker_profile_ids = {profile.profile_id for profile in stored_profiles}
        works_by_profile_id: dict[str, list[Work]] = defaultdict(list)
        legacy_works_by_type: dict[str, list[Work]] = defaultdict(list)
        for work in project_works:
            if work.requested_worker_profile_id:
                works_by_profile_id[work.requested_worker_profile_id].append(work)
                continue
            if (
                work.turn_executor_kind is TurnExecutorKind.WORKER
                and work.session_owner_profile_id in worker_profile_ids
                and not work.delegation_target_profile_id
            ):
                works_by_profile_id[work.session_owner_profile_id].append(work)
                continue
            legacy_works_by_type[work.selected_worker_type].append(work)

        # 预备 behavior_system 构建所需的项目/工作区上下文
        _bs_project_name = selected_project.name if selected_project is not None else ""
        _bs_project_slug = selected_project.slug if selected_project is not None else ""

        items: list[WorkerProfileViewItem] = []
        for profile in stored_profiles:
            matched_works = sorted(
                works_by_profile_id.get(profile.profile_id, []),
                key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            latest = matched_works[0] if matched_works else None
            warnings: list[str] = []
            if profile.status == WorkerProfileStatus.ARCHIVED:
                warnings.append("当前 profile 已归档，只保留审计、复制和历史追溯。")
            elif profile.active_revision == 0:
                warnings.append("当前 profile 还是草稿，还没有已发布 revision。")
            elif profile.draft_revision > profile.active_revision:
                warnings.append(
                    f"存在未发布草稿 revision {profile.draft_revision}，当前线上版本是 {profile.active_revision}。"
                )
            if latest is None:
                warnings.append("当前还没有绑定到这个 profile 的运行中 work。")

            # 为自定义 worker profile 构建 behavior_system
            agent_profile_mirror = self._build_agent_profile_from_worker_profile(
                profile=profile,
                revision=profile.active_revision or profile.draft_revision,
            )
            # 确保 agent-private 行为文件存在（lazy materialization）
            _agent_slug = resolve_behavior_agent_slug(agent_profile_mirror)
            materialize_agent_behavior_files(
                self._ctx.project_root,
                agent_slug=_agent_slug,
                agent_name=profile.name,
                is_worker_profile=True,
            )
            behavior_sys = build_behavior_system_summary(
                agent_profile=agent_profile_mirror,
                project_name=_bs_project_name,
                project_slug=_bs_project_slug,
                project_root=self._ctx.project_root,
            )

            items.append(
                WorkerProfileViewItem(
                    profile_id=profile.profile_id,
                    name=profile.name,
                    scope=profile.scope.value,
                    project_id=profile.project_id,
                    mode="singleton",
                    origin_kind=profile.origin_kind,
                    status=profile.status,
                    active_revision=profile.active_revision,
                    draft_revision=profile.draft_revision,
                    effective_snapshot_id=(
                        latest.effective_worker_snapshot_id
                        if latest is not None
                        else self._worker_snapshot_id(
                            profile.profile_id,
                            profile.active_revision or profile.draft_revision,
                        )
                    ),
                    editable=profile.origin_kind != WorkerProfileOriginKind.BUILTIN,
                    summary=profile.summary
                    or self._worker_profile_summary(
                        list(profile.default_tool_groups),
                        list(profile.default_tool_groups),
                    ),
                    static_config=WorkerProfileStaticConfig(
                        summary=profile.summary,
                        model_alias=profile.model_alias,
                        tool_profile=profile.tool_profile,
                        default_tool_groups=list(profile.default_tool_groups),
                        selected_tools=list(profile.selected_tools),
                        runtime_kinds=list(profile.runtime_kinds),
                        capabilities=[],
                        metadata=dict(profile.metadata),
                        resource_limits=dict(profile.resource_limits),
                    ),
                    dynamic_context=self._build_worker_dynamic_context(
                        matched_works,
                        fallback_tools=profile.selected_tools or profile.default_tool_groups,
                        fallback_project_id=(
                            selected_project.project_id if selected_project is not None else ""
                        ),
                        fallback_workspace_id="",
                    ),
                    behavior_system=behavior_sys,
                    warnings=warnings,
                    capabilities=self._worker_profile_control_capabilities(profile.status),
                )
            )

        for profile in capability_pack.pack.worker_profiles:
            worker_type = profile.worker_type
            matched_works = sorted(
                [
                    *works_by_profile_id.get(f"singleton:{worker_type}", []),
                    *legacy_works_by_type.get(worker_type, []),
                ],
                key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            summary = self._worker_profile_summary(profile.capabilities, profile.default_tool_groups)
            builtin_latest = matched_works[0] if matched_works else None

            # 为 builtin profile 构建 behavior_system（合成临时 AgentProfile）
            builtin_agent_profile = AgentProfile(
                profile_id=f"singleton:{worker_type}",
                scope=AgentProfileScope.SYSTEM,
                name=self._worker_profile_label(worker_type),
                model_alias=profile.default_model_alias,
                tool_profile=profile.default_tool_profile,
                metadata={},
            )
            # 确保 agent-private 行为文件存在（lazy materialization）
            _builtin_slug = resolve_behavior_agent_slug(builtin_agent_profile)
            materialize_agent_behavior_files(
                self._ctx.project_root,
                agent_slug=_builtin_slug,
                agent_name=self._worker_profile_label(worker_type),
                is_worker_profile=True,
            )
            builtin_behavior_sys = build_behavior_system_summary(
                agent_profile=builtin_agent_profile,
                project_name=_bs_project_name,
                project_slug=_bs_project_slug,
                project_root=self._ctx.project_root,
            )

            items.append(
                WorkerProfileViewItem(
                    profile_id=f"singleton:{worker_type}",
                    name=self._worker_profile_label(worker_type),
                    scope="system",
                    project_id="",
                    mode="singleton",
                    origin_kind=WorkerProfileOriginKind.BUILTIN,
                    status=WorkerProfileStatus.ACTIVE,
                    active_revision=1,
                    draft_revision=1,
                    effective_snapshot_id=self._worker_snapshot_id(f"singleton:{worker_type}", 1),
                    editable=False,
                    summary=summary,
                    static_config=WorkerProfileStaticConfig(
                        summary=summary,
                        model_alias=profile.default_model_alias,
                        tool_profile=profile.default_tool_profile,
                        default_tool_groups=list(profile.default_tool_groups),
                        selected_tools=[],
                        runtime_kinds=[item.value for item in profile.runtime_kinds],
                        capabilities=list(profile.capabilities),
                        metadata={},
                    ),
                    dynamic_context=self._build_worker_dynamic_context(
                        matched_works,
                        fallback_tools=profile.default_tool_groups,
                        fallback_project_id=(
                            selected_project.project_id if selected_project is not None else ""
                        ),
                        fallback_workspace_id="",
                    ),
                    behavior_system=builtin_behavior_sys,
                    warnings=[] if builtin_latest is not None else ["当前还没有运行中的 work。"],
                    capabilities=self._worker_profile_control_capabilities(
                        WorkerProfileStatus.ACTIVE,
                        builtin=True,
                    ),
                )
            )

        # 收集所有项目的 default_agent_profile_id，标记每个 profile 是否为其所属项目的默认
        all_projects = await self._stores.project_store.list_projects()
        default_profile_id_set: set[str] = set()
        primary_default_profile_id = ""
        for project in all_projects:
            pid = str(project.default_agent_profile_id or "").strip()
            if pid:
                default_profile_id_set.add(pid)
                if project.is_default:
                    primary_default_profile_id = pid
        for item in items:
            if item.profile_id in default_profile_id_set:
                item.is_default_for_project = True
        # summary 中的 default_profile_id 优先用默认项目的，退化到 selected_project
        default_profile_id = (
            primary_default_profile_id
            or (selected_project.default_agent_profile_id if selected_project is not None else "")
        )
        default_profile = next(
            (item for item in items if item.profile_id == default_profile_id),
            None,
        )

        return WorkerProfilesDocument(
            active_project_id=selected_project.project_id if selected_project is not None else "",
            active_workspace_id="",
            profiles=items,
            summary={
                "profile_count": len(items),
                "singleton_count": len(
                    [
                        item
                        for item in items
                        if item.origin_kind == WorkerProfileOriginKind.BUILTIN
                    ]
                ),
                "custom_count": len(
                    [
                        item
                        for item in items
                        if item.origin_kind != WorkerProfileOriginKind.BUILTIN
                    ]
                ),
                "published_count": len(
                    [item for item in items if item.status == WorkerProfileStatus.ACTIVE]
                ),
                "draft_count": len(
                    [item for item in items if item.status == WorkerProfileStatus.DRAFT]
                ),
                "archived_count": len(
                    [item for item in items if item.status == WorkerProfileStatus.ARCHIVED]
                ),
                "active_count": len(
                    [item for item in items if item.dynamic_context.active_work_count > 0]
                ),
                "attention_count": len(
                    [item for item in items if item.dynamic_context.attention_work_count > 0]
                ),
                "default_profile_id": default_profile_id,
                "default_profile_name": default_profile.name if default_profile is not None else "",
                "default_profile_scope": default_profile.scope if default_profile is not None else "",
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="worker_profile.create",
                    label="新建 Root Agent",
                    action_id="worker_profile.create",
                )
            ],
            refs={
                "revisions_base": "/api/control/resources/worker-profile-revisions/{profile_id}"
            },
            warnings=[] if items else ["当前没有可见的 Root Agent profiles。"],
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["worker_profiles_empty"] if not items else [],
            ),
        )

    async def get_worker_profile_revisions_document(
        self,
        profile_id: str,
    ) -> WorkerProfileRevisionsDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        stored_profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
        items: list[WorkerProfileRevisionItem] = []
        warnings: list[str] = []

        if stored_profile is not None:
            if (
                stored_profile.scope.value == "project"
                and selected_project is not None
                and stored_profile.project_id
                and stored_profile.project_id != selected_project.project_id
            ):
                raise ControlPlaneActionError(
                    "WORKER_PROFILE_NOT_IN_SCOPE",
                    "当前 project 不能查看这个 Root Agent profile。",
                )
            revisions = await self._stores.agent_context_store.list_worker_profile_revisions(
                profile_id
            )
            items = [
                WorkerProfileRevisionItem(
                    revision_id=item.revision_id,
                    profile_id=item.profile_id,
                    revision=item.revision,
                    change_summary=item.change_summary,
                    created_by=item.created_by,
                    created_at=item.created_at,
                    snapshot_payload=item.snapshot_payload,
                )
                for item in revisions
            ]
            if not items:
                warnings.append("当前 profile 还没有已发布 revision。")
        elif profile_id.startswith("singleton:"):
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
            if builtin is not None:
                summary = self._worker_profile_summary(
                    builtin.capabilities,
                    builtin.default_tool_groups,
                )
                items = [
                    WorkerProfileRevisionItem(
                        revision_id=self._worker_snapshot_id(profile_id, 1),
                        profile_id=profile_id,
                        revision=1,
                        change_summary="内建 archetype singleton snapshot",
                        created_by="system",
                        created_at=capability_pack.generated_at,
                        snapshot_payload={
                            "profile_id": profile_id,
                            "name": self._worker_profile_label(worker_type),
                            "summary": summary,
                            "model_alias": builtin.default_model_alias,
                            "tool_profile": builtin.default_tool_profile,
                            "default_tool_groups": list(builtin.default_tool_groups),
                            "runtime_kinds": [item.value for item in builtin.runtime_kinds],
                            "capabilities": list(builtin.capabilities),
                        },
                    )
                ]
            else:
                warnings.append("当前找不到对应的内建 singleton Root Agent。")
        else:
            warnings.append("当前找不到对应的 Root Agent profile。")

        return WorkerProfileRevisionsDocument(
            resource_id=f"worker-profile-revisions:{profile_id}",
            profile_id=profile_id,
            revisions=items,
            summary={
                "profile_id": profile_id,
                "revision_count": len(items),
                "latest_revision": items[0].revision if items else 0,
            },
            capabilities=[
                ControlPlaneCapability(
                    capability_id="worker_profile.publish",
                    label="发布 Revision",
                    action_id="worker_profile.publish",
                    enabled=not profile_id.startswith("singleton:"),
                    support_status=(
                        ControlPlaneSupportStatus.SUPPORTED
                        if not profile_id.startswith("singleton:")
                        else ControlPlaneSupportStatus.DEGRADED
                    ),
                    reason="内建 archetype 不能直接发布 revision。"
                    if profile_id.startswith("singleton:")
                    else "",
                )
            ],
            warnings=warnings,
            degraded=ControlPlaneDegradedState(
                is_degraded=not bool(items),
                reasons=["worker_profile_revisions_empty"] if not items else [],
            ),
        )

    # ══════════════════════════════════════════════════════════════
    #  Action Handlers
    # ══════════════════════════════════════════════════════════════

    async def _handle_behavior_read_file(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        """读取行为文件的内容（从磁盘）。供 Web UI 行为文件面板使用。"""
        file_path = str(request.params.get("file_path", "")).strip()
        if not file_path:
            raise ControlPlaneActionError("MISSING_PARAM", "file_path 不能为空")

        try:
            resolved = validate_behavior_file_path(self._ctx.project_root, file_path)
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_PATH", str(exc)) from exc

        if not resolved.exists():
            # 文件不存在时 fallback 到默认模板
            try:
                content, exists, budget_chars = read_behavior_file_content(
                    self._ctx.project_root, file_path,
                )
            except Exception:
                content, exists, budget_chars = "", False, 0
            return self._completed_result(
                request=request,
                code="BEHAVIOR_FILE_READ",
                message="文件尚未创建，返回默认模板",
                data={"file_path": file_path, "content": content, "exists": False,
                      "budget_chars": budget_chars},
            )

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as exc:
            raise ControlPlaneActionError(
                "FILE_READ_ERROR", f"读取文件失败: {exc}"
            ) from exc

        return self._completed_result(
            request=request,
            code="BEHAVIOR_FILE_READ",
            message="已读取行为文件",
            data={"file_path": file_path, "content": content, "exists": True},
        )

    async def _handle_behavior_write_file(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        """写入行为文件内容（到磁盘）。"""
        # 优先使用 file_id，兼容旧的 file_path
        file_id = str(request.params.get("file_id", "") or request.params.get("file_path", "")).strip()
        content = str(request.params.get("content", ""))
        if not file_id:
            raise ControlPlaneActionError("MISSING_PARAM", "file_id 不能为空")

        agent_slug = str(request.params.get("agent_slug", "butler")).strip()
        project_slug = str(request.params.get("project_slug", "default")).strip()

        try:
            resolved = resolve_write_path_by_file_id(
                self._ctx.project_root,
                file_id,
                agent_slug=agent_slug,
                project_slug=project_slug,
            )
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_FILE_ID", str(exc)) from exc

        # 字符预算检查
        budget_result = check_behavior_file_budget(file_id, content)
        if not budget_result["within_budget"]:
            raise ControlPlaneActionError(
                "BUDGET_EXCEEDED",
                f"内容超出字符预算 {budget_result['exceeded_by']} 字符"
                f"（当前 {budget_result['current_chars']}/"
                f"预算 {budget_result['budget_chars']}），请精简后重试",
            )

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except Exception as exc:
            raise ControlPlaneActionError(
                "FILE_WRITE_ERROR", f"写入文件失败: {exc}"
            ) from exc

        # 记录事件（FR-018）
        _log = structlog.get_logger("control_plane.behavior")
        _log.info(
            "behavior_file_written",
            source="control_plane",
            file_id=file_id,
            resolved_path=str(resolved),
            chars_written=len(content),
        )

        return self._completed_result(
            request=request,
            code="BEHAVIOR_FILE_WRITTEN",
            message="已保存行为文件",
            data={"file_id": file_id, "resolved_path": str(resolved)},
            resource_refs=[
                self._resource_ref("agent_profiles", "agent:profiles"),
            ],
        )

    async def _handle_worker_profile_review(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        _, selected_project, _, _ = await self._resolve_selection()

        source_profile: WorkerProfile | None = None
        existing: WorkerProfile | None = None
        mode = "create"
        profile_id = self._param_str(raw, "profile_id")
        source_profile_id = self._param_str(raw, "source_profile_id")
        if source_profile_id:
            source_profile = await self._get_worker_profile_in_scope(source_profile_id)
            mode = "clone"
        if profile_id and not source_profile_id:
            try:
                existing = await self._get_worker_profile_in_scope(profile_id)
            except ControlPlaneActionError:
                existing = None
            if existing is not None and existing.origin_kind != WorkerProfileOriginKind.BUILTIN:
                mode = "update"

        review = await self._review_worker_profile_draft(
            raw=raw,
            mode=mode,
            existing=existing,
            source_profile=source_profile,
            selected_project=selected_project,
            origin_kind=(
                WorkerProfileOriginKind.CLONED if mode == "clone" else None
            ),
        )
        target_profile_id = str(review["profile"].get("profile_id", "")).strip()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_REVIEW_READY",
            message="Root Agent profile 检查已完成。",
            data={"review": review},
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{target_profile_id}",
                ),
            ],
            target_refs=(
                [
                    ControlPlaneTargetRef(
                        target_type="worker_profile",
                        target_id=target_profile_id,
                        label=str(review["profile"].get("name", target_profile_id)),
                    )
                ]
                if target_profile_id
                else []
            ),
        )

    async def _handle_worker_profile_create(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        profile_id = self._param_str(raw, "profile_id")
        if profile_id:
            existing = await self._stores.agent_context_store.get_worker_profile(profile_id)
            if existing is not None:
                raise ControlPlaneActionError(
                    "WORKER_PROFILE_ALREADY_EXISTS",
                    "同名 Root Agent profile 已存在，请改名或使用 clone/update。",
                )
        _, selected_project, _, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="create",
            selected_project=selected_project,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_CREATE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=WorkerProfileOriginKind.CUSTOM,
        )
        # 为新 Agent 创建 agent-private 行为文件
        materialize_agent_behavior_files(
            self._ctx.project_root,
            agent_slug=saved.name or saved.profile_id,
            agent_name=saved.name,
            is_worker_profile=True,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_CREATED",
            message="已创建 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "status": saved.status.value,
                "draft_revision": saved.draft_revision,
                "active_revision": saved.active_revision,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_update(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        profile_id = self._param_str(raw, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能直接修改，请先 clone 一个新的 Root Agent。",
            )
        _, selected_project, _, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="update",
            existing=existing,
            selected_project=selected_project,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_UPDATE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=existing,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_UPDATED",
            message="已更新 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "status": saved.status.value,
                "draft_revision": saved.draft_revision,
                "active_revision": saved.active_revision,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_clone(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        source_profile_id = self._param_str(request.params, "source_profile_id")
        if not source_profile_id:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_SOURCE_REQUIRED",
                "source_profile_id 不能为空。",
            )
        source_profile = await self._get_worker_profile_in_scope(source_profile_id)
        raw: dict[str, Any] = {
            **self._worker_profile_snapshot_payload(source_profile),
            "source_profile_id": source_profile_id,
        }
        if name := self._param_str(request.params, "name"):
            raw["name"] = name
        raw["profile_id"] = ""
        raw["origin_kind"] = WorkerProfileOriginKind.CLONED.value
        _, selected_project, _, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="clone",
            source_profile=source_profile,
            selected_project=selected_project,
            origin_kind=WorkerProfileOriginKind.CLONED,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "克隆后的 Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_CLONE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=WorkerProfileOriginKind.CLONED,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_CLONED",
            message="已复制为新的 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "source_profile_id": source_profile_id,
                "status": saved.status.value,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_archive(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能归档。",
            )
        archived = await self._stores.agent_context_store.save_worker_profile(
            existing.model_copy(
                update={
                    "status": WorkerProfileStatus.ARCHIVED,
                    "archived_at": datetime.now(tz=UTC),
                    "updated_at": datetime.now(tz=UTC),
                }
            )
        )
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_ARCHIVED",
            message="已归档 Root Agent profile。",
            data={
                "profile_id": archived.profile_id,
                "status": archived.status.value,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{archived.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=archived.profile_id,
                    label=archived.name,
                )
            ],
        )

    async def _handle_worker_profile_apply(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        publish = self._param_bool(request.params, "publish")
        profile_id = self._param_str(raw, "profile_id")
        existing: WorkerProfile | None = None
        mode = "create"
        if profile_id:
            existing = await self._stores.agent_context_store.get_worker_profile(profile_id)
            if existing is not None:
                if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
                    raise ControlPlaneActionError(
                        "WORKER_PROFILE_BUILTIN_READONLY",
                        "内建 archetype 不能直接 apply，请先 clone 一个新的 Root Agent。",
                    )
                mode = "update"
        _, selected_project, _, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode=mode,
            existing=existing,
            selected_project=selected_project,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_APPLY_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=existing,
        )
        data: dict[str, Any] = {
            "profile_id": saved.profile_id,
            "status": saved.status.value,
            "draft_revision": saved.draft_revision,
            "active_revision": saved.active_revision,
            "review": review,
        }
        message = "已保存 Root Agent 草稿。"
        if publish:
            if not bool(review.get("ready")):
                blocking = "；".join(review.get("blocking_reasons", [])) or "当前 review 未通过。"
                raise ControlPlaneActionError("WORKER_PROFILE_REVIEW_BLOCKED", blocking)
            published, revision, changed = await self._publish_worker_profile_revision(
                profile=saved,
                change_summary=(
                    self._param_str(request.params, "change_summary")
                    or "通过 Profile Studio apply 并发布"
                ),
                actor=request.actor.actor_id,
            )
            await self._sync_worker_profile_agent_profile(
                published,
                revision=revision.revision,
            )
            bound_as_default = False
            should_bind_default = (
                self._param_bool(request.params, "set_as_default")
                if "set_as_default" in request.params
                else bool(
                    published.scope == AgentProfileScope.PROJECT
                    and selected_project is not None
                    and not selected_project.default_agent_profile_id
                )
            )
            if should_bind_default:
                bound_as_default = await self._bind_worker_profile_as_default(profile=published)
            data["published_revision"] = revision.revision
            data["published"] = changed
            data["status"] = published.status.value
            data["active_revision"] = published.active_revision
            data["draft_revision"] = published.draft_revision
            data["bound_as_default"] = bound_as_default
            message = "已保存草稿并发布 Root Agent revision。"
            await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_APPLIED",
            message=message,
            data=data,
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
                self._resource_ref("delegation_plane", "delegation:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                )
            ],
        )

    async def _handle_worker_profile_publish(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        if isinstance(draft, Mapping):
            return await self._handle_worker_profile_apply(
                request.model_copy(update={"action_id": "worker_profile.apply", "params": {**request.params, "publish": True}})
            )

        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能直接发布 revision。",
            )
        _, selected_project, _, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=existing.model_dump(mode="python"),
            mode="publish",
            existing=existing,
            selected_project=selected_project,
        )
        if not bool(review.get("ready")):
            blocking = "；".join(review.get("blocking_reasons", [])) or "当前 review 未通过。"
            raise ControlPlaneActionError("WORKER_PROFILE_REVIEW_BLOCKED", blocking)
        published, revision, changed = await self._publish_worker_profile_revision(
            profile=existing,
            change_summary=(
                self._param_str(request.params, "change_summary")
                or "通过 Profile Studio 发布"
            ),
            actor=request.actor.actor_id,
        )
        await self._sync_worker_profile_agent_profile(
            published,
            revision=revision.revision,
        )
        _, selected_project, _, _ = await self._resolve_selection()
        should_bind_default = (
            self._param_bool(request.params, "set_as_default")
            if "set_as_default" in request.params
            else bool(
                published.scope == AgentProfileScope.PROJECT
                and selected_project is not None
                and not selected_project.default_agent_profile_id
            )
        )
        bound_as_default = False
        if should_bind_default:
            bound_as_default = await self._bind_worker_profile_as_default(profile=published)
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_PUBLISHED",
            message="已发布 Root Agent revision。",
            data={
                "profile_id": published.profile_id,
                "revision": revision.revision,
                "published": changed,
                "bound_as_default": bound_as_default,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{published.profile_id}",
                ),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=published.profile_id,
                    label=published.name,
                )
            ],
        )

    async def _handle_worker_profile_bind_default(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        existing = await self._get_worker_profile_in_scope(profile_id)
        if existing.origin_kind == WorkerProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BIND_UNSUPPORTED",
                "当前只支持把已发布的自定义 Root Agent 绑定为聊天默认。",
            )
        if existing.status != WorkerProfileStatus.ACTIVE:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_NOT_PUBLISHED",
                "请先发布 revision，再绑定为默认聊天 Agent。",
            )
        revision = existing.active_revision or existing.draft_revision or 1
        await self._sync_worker_profile_agent_profile(existing, revision=revision)
        bound = await self._bind_worker_profile_as_default(profile=existing)
        await self._stores.conn.commit()
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_BOUND_DEFAULT",
            message="已绑定为当前 project 的默认聊天 Agent。",
            data={
                "profile_id": existing.profile_id,
                "bound": bound,
                "revision": revision,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref("agent_profiles", "agent-profiles:overview"),
                self._resource_ref("setup_governance", "setup:governance"),
            ],
            target_refs=[
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=existing.profile_id,
                    label=existing.name,
                )
            ],
        )

    async def _handle_worker_spawn_from_profile(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        profile_id = self._param_str(request.params, "profile_id")
        if not profile_id:
            raise ControlPlaneActionError("WORKER_PROFILE_REQUIRED", "profile_id 不能为空。")
        profile = await self._get_worker_profile_in_scope(profile_id)
        if profile.status == WorkerProfileStatus.ARCHIVED:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_ARCHIVED",
                "归档后的 Root Agent 不能再启动新任务。",
            )
        objective = self._param_str(request.params, "objective") or self._param_str(
            request.params, "message"
        )
        if not objective:
            raise ControlPlaneActionError("OBJECTIVE_REQUIRED", "objective 不能为空。")
        _, selected_project, _, _ = await self._resolve_selection()
        project_id = (
            profile.project_id
            or (selected_project.project_id if selected_project is not None else "")
        )
        workspace_id = ""
        requested_revision = profile.active_revision or profile.draft_revision or 1
        message = NormalizedMessage(
            channel="web",
            thread_id=f"worker-profile:{profile.profile_id}",
            scope_id=project_id or f"worker-profile:{profile.profile_id}",
            sender_id="owner",
            sender_name=request.actor.actor_label or "Owner",
            text=objective,
            idempotency_key=f"spawn:{profile.profile_id}:{objective}:{ULID()}",
            control_metadata={
                "requested_worker_profile_id": profile.profile_id,
                "requested_worker_profile_version": requested_revision,
                "effective_worker_snapshot_id": self._worker_snapshot_id(
                    profile.profile_id,
                    requested_revision,
                ),
                "requested_worker_type": "general",
                "tool_profile": profile.tool_profile,
                "target_kind": self._param_str(request.params, "target_kind", default="worker")
                or "worker",
                "project_id": project_id,
                "workspace_id": workspace_id,
            },
        )
        if self._ctx.task_runner is not None:
            task_id, created = await self._ctx.task_runner.launch_child_task(
                message,
                model_alias=profile.model_alias,
            )
        else:
            task_id, created = await TaskService(self._stores, self._ctx.sse_hub).create_task(message)
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_SPAWNED",
            message="已按 Root Agent profile 创建任务。",
            data={
                "task_id": task_id,
                "created": created,
                "profile_id": profile.profile_id,
                "requested_worker_profile_version": requested_revision,
            },
            resource_refs=[
                self._resource_ref("session_projection", "sessions:overview"),
                self._resource_ref("delegation_plane", "delegation:overview"),
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(target_type="task", target_id=task_id, label=objective[:48]),
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=profile.profile_id,
                    label=profile.name,
                ),
            ],
        )

    async def _handle_worker_extract_profile_from_runtime(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        work_id = self._param_str(request.params, "work_id")
        if not work_id:
            raise ControlPlaneActionError("WORK_ID_REQUIRED", "work_id 不能为空。")
        work = await self._get_work_in_scope(work_id)
        _, selected_project, _, _ = await self._resolve_selection()
        raw: dict[str, Any] = {
            "name": self._param_str(request.params, "name")
            or f"{self._worker_profile_label(work.selected_worker_type)} 提炼草稿",
            "summary": self._param_str(request.params, "summary")
            or (work.title or "从运行中的 Work 提炼而来。"),
            "tool_profile": str(work.metadata.get("requested_tool_profile", "minimal")),
            "selected_tools": list(work.selected_tools),
            "runtime_kinds": [work.target_kind.value]
            if work.target_kind.value in {"worker", "subagent", "acp_runtime", "graph_agent"}
            else ["worker"],
            "tags": [work.selected_worker_type, "runtime-extract"],
            "metadata": {
                "source_work_id": work.work_id,
                "source_task_id": work.task_id,
                "source_snapshot_id": work.effective_worker_snapshot_id,
            },
            "profile_id": "",
            "project_id": work.project_id or (selected_project.project_id if selected_project is not None else ""),
        }
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="extract",
            selected_project=selected_project,
            origin_kind=WorkerProfileOriginKind.EXTRACTED,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "提炼后的 Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_EXTRACT_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=WorkerProfileOriginKind.EXTRACTED,
        )
        return self._completed_result(
            request=request,
            code="WORKER_PROFILE_EXTRACTED",
            message="已从运行中的 Work 提炼出 Root Agent 草稿。",
            data={
                "profile_id": saved.profile_id,
                "source_work_id": work.work_id,
                "review": review,
            },
            resource_refs=[
                self._resource_ref("worker_profiles", "worker-profiles:overview"),
                self._resource_ref(
                    "worker_profile_revisions",
                    f"worker-profile-revisions:{saved.profile_id}",
                ),
                self._resource_ref("delegation_plane", "delegation:overview"),
            ],
            target_refs=[
                ControlPlaneTargetRef(target_type="work", target_id=work.work_id, label=work.title),
                ControlPlaneTargetRef(
                    target_type="worker_profile",
                    target_id=saved.profile_id,
                    label=saved.name,
                ),
            ],
        )

    # ══════════════════════════════════════════════════════════════
    #  Capability Pack（本地副本，与 setup_service.py 独立）
    # ══════════════════════════════════════════════════════════════

    async def _get_capability_pack_document(self) -> CapabilityPackDocument:
        """获取能力包文档。各 domain service 独立持有自己的副本。"""
        if self._ctx.capability_pack_service is None:
            return CapabilityPackDocument(
                selected_project_id="",
                selected_workspace_id="",
                degraded=ControlPlaneDegradedState(
                    is_degraded=True,
                    reasons=["capability_pack_unavailable"],
                ),
                warnings=["capability pack service unavailable"],
            )
        pack = await self._ctx.capability_pack_service.get_pack(
            project_id="",
        )
        return CapabilityPackDocument(
            pack=pack,
            selected_project_id="",
            selected_workspace_id="",
            degraded=ControlPlaneDegradedState(
                is_degraded=bool(pack.degraded_reason),
                reasons=[pack.degraded_reason] if pack.degraded_reason else [],
            ),
        )

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
            "general": "Butler Root Agent",
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

    def _build_agent_profile_from_worker_profile(
        self,
        *,
        profile: WorkerProfile,
        revision: int,
        existing: AgentProfile | None = None,
    ) -> AgentProfile:
        metadata = dict(existing.metadata) if existing is not None else {}
        metadata.update(dict(profile.metadata))
        from octoagent.core.behavior_workspace import normalize_behavior_agent_slug
        metadata.update(
            {
                "source_kind": "worker_profile_mirror",
                "behavior_agent_slug": normalize_behavior_agent_slug(
                    profile.name or profile.profile_id
                ),
                "worker_profile_id": profile.profile_id,
                "worker_profile_revision": revision,
                "worker_profile_status": profile.status.value,
            }
        )
        return AgentProfile(
            profile_id=profile.profile_id,
            scope=profile.scope,
            project_id=profile.project_id,
            name=profile.name,
            persona_summary=profile.summary,
            model_alias=profile.model_alias,
            tool_profile=profile.tool_profile,
            metadata=metadata,
            version=max(existing.version if existing is not None else 1, revision or 1),
            created_at=existing.created_at if existing is not None else profile.created_at,
            updated_at=datetime.now(tz=UTC),
        )

    async def _sync_worker_profile_agent_profile(
        self,
        profile: WorkerProfile,
        *,
        revision: int,
    ) -> AgentProfile:
        existing = await self._stores.agent_context_store.get_agent_profile(profile.profile_id)
        mirrored = self._build_agent_profile_from_worker_profile(
            profile=profile,
            revision=revision,
            existing=existing,
        )
        await self._stores.agent_context_store.save_agent_profile(mirrored)
        # 同步时确保 agent-private 行为文件存在
        _slug = resolve_behavior_agent_slug(mirrored)
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
        profile: WorkerProfile,
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
    ) -> WorkerProfileDynamicContext:
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
        return WorkerProfileDynamicContext(
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
        status: WorkerProfileStatus,
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
        is_archived = status == WorkerProfileStatus.ARCHIVED
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
                enabled=not is_archived and status == WorkerProfileStatus.ACTIVE,
                support_status=(
                    ControlPlaneSupportStatus.SUPPORTED
                    if (not is_archived and status == WorkerProfileStatus.ACTIVE)
                    else ControlPlaneSupportStatus.DEGRADED
                ),
                reason=(
                    ""
                    if (not is_archived and status == WorkerProfileStatus.ACTIVE)
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
        existing = await self._stores.agent_context_store.get_worker_profile(candidate)
        if existing is None or existing.profile_id == existing_profile_id:
            return candidate
        return f"{candidate}-{str(ULID()).lower()[-6:]}"

    async def _resolve_builtin_worker_source(
        self,
        profile_id: str,
    ) -> WorkerProfile | None:
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
        return WorkerProfile(
            profile_id=profile_id,
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
            status=WorkerProfileStatus.ACTIVE,
            origin_kind=WorkerProfileOriginKind.BUILTIN,
            draft_revision=1,
            active_revision=1,
        )

    async def _get_worker_profile_in_scope(
        self,
        profile_id: str,
    ) -> WorkerProfile:
        _, selected_project, _, _ = await self._resolve_selection()
        builtin = await self._resolve_builtin_worker_source(profile_id)
        if builtin is not None:
            return builtin
        profile = await self._stores.agent_context_store.get_worker_profile(profile_id)
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
        existing: WorkerProfile | None = None,
        source_profile: WorkerProfile | None = None,
        selected_project: Any | None,
        origin_kind: WorkerProfileOriginKind | None = None,
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
                        and source_profile.origin_kind != WorkerProfileOriginKind.BUILTIN
                        else WorkerProfileOriginKind.CUSTOM.value
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
            warnings.append("建议补一段 summary，方便 Butler 和 Control Plane 解释这个 Root Agent。")
        if selected_project is not None:
            policy_profile_id, policy_profile = self._resolve_effective_policy_profile(
                selected_project
            )
            if not self._tool_profile_allowed(tool_profile, policy_profile.allowed_tool_profile):
                warnings.append(
                    "当前 profile 的 tool_profile 高于当前 project policy，运行时可能被降级或要求审批。"
                )
        if existing is not None and existing.status == WorkerProfileStatus.ARCHIVED:
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

    def _worker_profile_snapshot_payload(self, profile: WorkerProfile) -> dict[str, Any]:
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
        existing: WorkerProfile | None,
        origin_kind: WorkerProfileOriginKind | None = None,
    ) -> WorkerProfile:
        now = datetime.now(tz=UTC)
        resolved_origin = (
            origin_kind
            if origin_kind is not None
            else (
                existing.origin_kind
                if existing is not None
                else WorkerProfileOriginKind(
                    str(normalized_profile.get("origin_kind", WorkerProfileOriginKind.CUSTOM.value))
                )
            )
        )
        if existing is None:
            status = WorkerProfileStatus.DRAFT
            draft_revision = 1
            active_revision = 0
            created_at = now
        else:
            status = (
                WorkerProfileStatus.ACTIVE
                if existing.active_revision > 0 and existing.status != WorkerProfileStatus.ARCHIVED
                else WorkerProfileStatus.DRAFT
            )
            draft_revision = (
                max(existing.draft_revision, existing.active_revision + 1)
                if existing.active_revision > 0
                else max(existing.draft_revision, 1)
            )
            active_revision = existing.active_revision
            created_at = existing.created_at
        saved = await self._stores.agent_context_store.save_worker_profile(
            WorkerProfile(
                profile_id=str(normalized_profile.get("profile_id", "")),
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
        )
        await self._stores.conn.commit()
        return saved

    async def _publish_worker_profile_revision(
        self,
        *,
        profile: WorkerProfile,
        change_summary: str,
        actor: str,
    ) -> tuple[WorkerProfile, WorkerProfileRevision, bool]:
        revisions = await self._stores.agent_context_store.list_worker_profile_revisions(
            profile.profile_id
        )
        snapshot_payload = self._worker_profile_snapshot_payload(profile)
        latest = revisions[0] if revisions else None
        if (
            latest is not None
            and latest.snapshot_payload == snapshot_payload
            and latest.revision == profile.active_revision
            and profile.status == WorkerProfileStatus.ACTIVE
        ):
            return profile, latest, False

        next_revision = profile.draft_revision or profile.active_revision or 1
        if next_revision <= profile.active_revision:
            next_revision = profile.active_revision + 1
        revision = await self._stores.agent_context_store.save_worker_profile_revision(
            WorkerProfileRevision(
                revision_id=self._worker_snapshot_id(profile.profile_id, next_revision),
                profile_id=profile.profile_id,
                revision=next_revision,
                change_summary=change_summary,
                snapshot_payload=snapshot_payload,
                created_by=actor,
                created_at=datetime.now(tz=UTC),
            )
        )
        updated = await self._stores.agent_context_store.save_worker_profile(
            profile.model_copy(
                update={
                    "status": WorkerProfileStatus.ACTIVE,
                    "active_revision": next_revision,
                    "draft_revision": next_revision,
                    "updated_at": datetime.now(tz=UTC),
                    "archived_at": None,
                }
            )
        )
        await self._stores.conn.commit()
        return updated, revision, True
