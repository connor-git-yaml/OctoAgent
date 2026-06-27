"""WorkerProfileDomainService -- Worker profile 领域服务。

从 control_plane.py 拆分：
- get_worker_profiles_document / get_worker_profile_revisions_document
- _handle_worker_profile_review / create / update / clone / archive / apply / publish / bind_default
- _handle_worker_spawn_from_profile / _handle_worker_extract_profile_from_runtime
- _handle_behavior_read_file / _handle_behavior_write_file
- _sync_worker_profile_agent_profile
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
    commit_behavior_file_write,
    materialize_agent_behavior_files,
    prepare_behavior_file_write,
    read_behavior_file_content,
    resolve_behavior_agent_slug,
    validate_behavior_file_path,
)
from octoagent.core.models import (
    ActionRequestEnvelope,
    ActionResultEnvelope,
    AgentProfile,
    AgentProfileOriginKind,
    AgentProfileRevisionItem,
    AgentProfileRevisionsDocument,
    AgentProfileScope,
    AgentProfileStaticConfig,
    AgentProfileStatus,
    AgentProfileViewItem,
    CapabilityPackDocument,
    ControlPlaneCapability,
    ControlPlaneDegradedState,
    ControlPlaneSupportStatus,
    ControlPlaneTargetRef,
    NormalizedMessage,
    TurnExecutorKind,
    Work,
    WorkerProfilesDocument,
)
from ulid import ULID

from ..agent_decision import build_behavior_system_summary, is_worker_behavior_profile
from ..task_service import TaskService
from ._base import (
    SYSTEM_INTERNAL_WORK_IDS,
    ControlPlaneActionError,
    ControlPlaneContext,
    DomainServiceBase,
)
from .worker_profile_ops import WorkerProfileOpsMixin

log = structlog.get_logger()


class WorkerProfileDomainService(WorkerProfileOpsMixin, DomainServiceBase):
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
            "behavior.restore_version": self._handle_behavior_restore_version,
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
        # F117：listing 读统一 agent_profiles(kind=worker) 行（worker 镜像即权威 profile）。
        # 全局不过滤项目（管理页看全部）。W4-1 id-收口后镜像统一 bare id（agent_service/
        # _coordinator 程序化创建已收口），profile_id 是 PK 天然去重。include_archived 隐含
        # （agent_profiles 含 status=ARCHIVED 行）。W4-3：直接用 AgentProfile（不再反构 DTO）。
        all_agent_profiles = await self._stores.agent_context_store.list_agent_profiles()
        stored_profiles = [
            mirror
            for mirror in all_agent_profiles
            if is_worker_behavior_profile(mirror)
        ]
        project_works: list[Work] = []
        if self._ctx.delegation_plane_service is not None:
            project_works = await self._ctx.delegation_plane_service.list_works()
            # F127：排除系统内部占位 Work（巩固 root Work 不代表用户委派，不应污染
            # Worker profile 的 dynamic_context / active_project 解析）。
            project_works = [
                w for w in project_works if w.work_id not in SYSTEM_INTERNAL_WORK_IDS
            ]
        worker_profile_ids = {profile.profile_id for profile in stored_profiles}
        works_by_profile_id: dict[str, list[Work]] = defaultdict(list)
        legacy_works_by_type: dict[str, list[Work]] = defaultdict(list)
        for work in project_works:
            if work.requested_agent_profile_id:
                works_by_profile_id[work.requested_agent_profile_id].append(work)
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

        items: list[AgentProfileViewItem] = []
        for profile in stored_profiles:
            matched_works = sorted(
                works_by_profile_id.get(profile.profile_id, []),
                key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            latest = matched_works[0] if matched_works else None
            warnings: list[str] = []
            if profile.status == AgentProfileStatus.ARCHIVED:
                warnings.append("当前 profile 已归档，只保留审计、复制和历史追溯。")
            elif profile.active_revision == 0:
                warnings.append("当前 profile 还是草稿，还没有已发布 revision。")
            elif profile.draft_revision > profile.active_revision:
                warnings.append(
                    f"存在未发布草稿 revision {profile.draft_revision}，当前线上版本是 {profile.active_revision}。"
                )
            if latest is None:
                warnings.append("当前还没有绑定到这个 profile 的运行中 work。")

            # 为自定义 worker profile 构建 behavior_system。W4-3：profile 即统一 agent_profiles
            # (kind=worker) 镜像（运行时 SoT）→ 直接使用，展示的 slug / bootstrap_template_ids /
            # behavior_system 与运行时实际解析一致（不再经任何 incomplete builder 重建）。
            agent_profile_mirror = profile
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
                AgentProfileViewItem(
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
                        latest.effective_profile_snapshot_id
                        if latest is not None
                        else self._worker_snapshot_id(
                            profile.profile_id,
                            profile.active_revision or profile.draft_revision,
                        )
                    ),
                    editable=profile.origin_kind != AgentProfileOriginKind.BUILTIN,
                    summary=profile.summary
                    or self._worker_profile_summary(
                        list(profile.default_tool_groups),
                        list(profile.default_tool_groups),
                    ),
                    static_config=AgentProfileStaticConfig(
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
                AgentProfileViewItem(
                    profile_id=f"singleton:{worker_type}",
                    name=self._worker_profile_label(worker_type),
                    scope="system",
                    project_id="",
                    mode="singleton",
                    origin_kind=AgentProfileOriginKind.BUILTIN,
                    status=AgentProfileStatus.ACTIVE,
                    active_revision=1,
                    draft_revision=1,
                    effective_snapshot_id=self._worker_snapshot_id(f"singleton:{worker_type}", 1),
                    editable=False,
                    summary=summary,
                    static_config=AgentProfileStaticConfig(
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
                        AgentProfileStatus.ACTIVE,
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
        # default_profile_id 可能指向主 Agent profile（不在 worker 列表 items 中）。
        # 当 worker 列表查不到时，额外查 agent_profiles 取正确名称（Bug 075）。
        default_profile_name = default_profile.name if default_profile is not None else ""
        default_profile_scope = default_profile.scope if default_profile is not None else ""
        if not default_profile_name and default_profile_id:
            agent_profile_fallback = await self._stores.agent_context_store.get_agent_profile(
                default_profile_id
            )
            if agent_profile_fallback is not None:
                default_profile_name = agent_profile_fallback.name or ""
                default_profile_scope = str(agent_profile_fallback.scope) if agent_profile_fallback.scope else ""

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
                        if item.origin_kind == AgentProfileOriginKind.BUILTIN
                    ]
                ),
                "custom_count": len(
                    [
                        item
                        for item in items
                        if item.origin_kind != AgentProfileOriginKind.BUILTIN
                    ]
                ),
                "published_count": len(
                    [item for item in items if item.status == AgentProfileStatus.ACTIVE]
                ),
                "draft_count": len(
                    [item for item in items if item.status == AgentProfileStatus.DRAFT]
                ),
                "archived_count": len(
                    [item for item in items if item.status == AgentProfileStatus.ARCHIVED]
                ),
                "active_count": len(
                    [item for item in items if item.dynamic_context.active_work_count > 0]
                ),
                "attention_count": len(
                    [item for item in items if item.dynamic_context.attention_work_count > 0]
                ),
                "default_profile_id": default_profile_id,
                "default_profile_name": default_profile_name,
                "default_profile_scope": default_profile_scope,
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
    ) -> AgentProfileRevisionsDocument:
        _, selected_project, _, _ = await self._resolve_selection()
        stored_profile = await self._get_worker_profile_via_mirror(profile_id)
        items: list[AgentProfileRevisionItem] = []
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
            revisions = await self._stores.agent_context_store.list_agent_profile_revisions(
                profile_id
            )
            items = [
                AgentProfileRevisionItem(
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
                    AgentProfileRevisionItem(
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

        return AgentProfileRevisionsDocument(
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
                content, _exists, budget_chars = read_behavior_file_content(
                    self._ctx.project_root, file_path,
                )
            except Exception:
                content, _exists, budget_chars = "", False, 0
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

        agent_slug = str(request.params.get("agent_slug", "main")).strip()
        project_slug = str(request.params.get("project_slug", "default")).strip()

        try:
            pending = prepare_behavior_file_write(
                self._ctx.project_root,
                file_id,
                content,
                agent_slug=agent_slug,
                project_slug=project_slug,
            )
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_FILE_ID", str(exc)) from exc
        resolved = pending.resolved

        # 字符预算检查
        budget_result = pending.budget
        if not budget_result["within_budget"]:
            raise ControlPlaneActionError(
                "BUDGET_EXCEEDED",
                f"内容超出字符预算 {budget_result['exceeded_by']} 字符"
                f"（当前 {budget_result['current_chars']}/"
                f"预算 {budget_result['budget_chars']}），请精简后重试",
            )

        # F107 W1：写盘前读旧内容作版本 baseline（record-after + 首版 baseline）
        from octoagent.gateway.services.behavior_versioning import (
            read_disk_content,
            record_behavior_version,
        )

        old_content = read_disk_content(resolved)

        try:
            commit_behavior_file_write(pending, content)
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

        # F107 W1：record-after 版本记录（best-effort，不阻断写）。control_plane UI 写常无 task
        # 上下文 → 事件按需跳过，版本仍记录（durable 版本是主，事件是补充审计）。
        await record_behavior_version(
            stores=self._stores,
            project_root=self._ctx.project_root,
            resolved_path=resolved,
            new_content=content,
            old_content=old_content,
            task_id="",
            source="control_plane",
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

    async def _handle_behavior_restore_version(
        self, request: ActionRequestEnvelope
    ) -> ActionResultEnvelope:
        """F107 W1-C：恢复 behavior 文件到某一历史版本（SD-6）。

        Two-Phase（REVIEW_REQUIRED 风格）：confirmed=false → 返回 proposal（不写盘）；
        confirmed=true → 走现有 commit_behavior_file_write 写入，并由 record_behavior_version
        自动记为**新版本**（append-only，不改写历史；恢复本身也产生一条新版）。
        """
        from octoagent.core.behavior_workspace import behavior_version_key_for
        from octoagent.gateway.services.behavior_versioning import (
            read_disk_content,
            record_behavior_version,
        )

        file_id = str(request.params.get("file_id", "")).strip()
        if not file_id:
            raise ControlPlaneActionError("MISSING_PARAM", "file_id 不能为空")
        raw_version = request.params.get("target_version")
        try:
            target_version = int(raw_version)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ControlPlaneActionError(
                "INVALID_PARAM", "target_version 必须是整数版本号"
            ) from None
        confirmed = bool(request.params.get("confirmed", False))
        agent_slug = str(request.params.get("agent_slug", "main")).strip()
        project_slug = str(request.params.get("project_slug", "default")).strip()

        try:
            key = behavior_version_key_for(
                file_id, agent_slug=agent_slug, project_slug=project_slug
            )
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_FILE_ID", str(exc)) from exc

        version = await self._stores.behavior_version_store.get_version_content(
            key, target_version
        )
        if version is None or version.content is None:
            raise ControlPlaneActionError(
                "VERSION_NOT_FOUND", f"版本 {target_version} 不存在或内容不可用"
            )
        target_content = version.content

        # Phase Plan：proposal（confirmed=false）→ 不写盘，要求用户确认（SD-6 守 #4/#7）
        if not confirmed:
            return self._completed_result(
                request=request,
                code="BEHAVIOR_RESTORE_PROPOSAL",
                message=(
                    f"将把 {file_id} 恢复到版本 {target_version}，"
                    f"确认后写入并记为新版本"
                ),
                data={
                    "file_id": file_id,
                    "target_version": target_version,
                    "proposal": True,
                    "preview": target_content[:200],
                },
            )

        # Phase Execute：confirmed=true → 写盘 + record-after 记新版
        try:
            pending = prepare_behavior_file_write(
                self._ctx.project_root,
                file_id,
                target_content,
                agent_slug=agent_slug,
                project_slug=project_slug,
            )
        except ValueError as exc:
            raise ControlPlaneActionError("INVALID_FILE_ID", str(exc)) from exc
        if not pending.budget["within_budget"]:
            raise ControlPlaneActionError(
                "BUDGET_EXCEEDED",
                f"恢复内容超出字符预算 {pending.budget['exceeded_by']} 字符",
            )
        old_content = read_disk_content(pending.resolved)
        try:
            commit_behavior_file_write(pending, target_content)
        except Exception as exc:
            raise ControlPlaneActionError(
                "FILE_WRITE_ERROR", f"恢复写入失败: {exc}"
            ) from exc

        await record_behavior_version(
            stores=self._stores,
            project_root=self._ctx.project_root,
            resolved_path=pending.resolved,
            new_content=target_content,
            old_content=old_content,
            task_id="",
            source="restore",
        )
        # F107 Opus M1：恢复写盘后失效 behavior pack 缓存，让运行中 agent 立即用上恢复内容
        from octoagent.gateway.services.agent_decision import (
            invalidate_behavior_pack_cache,
        )

        invalidate_behavior_pack_cache(project_root=self._ctx.project_root)
        return self._completed_result(
            request=request,
            code="BEHAVIOR_RESTORED",
            message=f"已恢复 {file_id} 到版本 {target_version}（记为新版本）",
            data={"file_id": file_id, "restored_from_version": target_version},
        )

    async def _handle_worker_profile_review(
        self,
        request: ActionRequestEnvelope,
    ) -> ActionResultEnvelope:
        draft = request.params.get("draft")
        raw = draft if isinstance(draft, Mapping) else request.params
        _, selected_project, _, _ = await self._resolve_selection()

        source_profile: AgentProfile | None = None
        existing: AgentProfile | None = None
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
            if existing is not None and existing.origin_kind != AgentProfileOriginKind.BUILTIN:
                mode = "update"

        review = await self._review_worker_profile_draft(
            raw=raw,
            mode=mode,
            existing=existing,
            source_profile=source_profile,
            selected_project=selected_project,
            origin_kind=(
                AgentProfileOriginKind.CLONED if mode == "clone" else None
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
            existing = await self._get_worker_profile_via_mirror(profile_id)
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
            origin_kind=AgentProfileOriginKind.CUSTOM,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_CREATE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=AgentProfileOriginKind.CUSTOM,
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
        if existing.origin_kind == AgentProfileOriginKind.BUILTIN:
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
        raw["origin_kind"] = AgentProfileOriginKind.CLONED.value
        _, selected_project, _, _ = await self._resolve_selection()
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="clone",
            source_profile=source_profile,
            selected_project=selected_project,
            origin_kind=AgentProfileOriginKind.CLONED,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "克隆后的 Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_CLONE_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=AgentProfileOriginKind.CLONED,
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
        if existing.origin_kind == AgentProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BUILTIN_READONLY",
                "内建 archetype 不能归档。",
            )
        # F117 Wave 2c-2c-W：停写 worker_profiles——in-memory model_copy；镜像 status 由下方 archive-sync 写。
        archived = existing.model_copy(
            update={
                "status": AgentProfileStatus.ARCHIVED,
                "archived_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
        )
        # F117 Wave 2bc（archive-sync gate，Wave 1 评审 MEDIUM-1）：archive 后同步镜像
        # status=ARCHIVED——否则 read-switch 后 capability_pack/chat/session 读 mirror.status
        # 仍见旧 active，archived worker 仍可派发。直接改镜像 status（不走 _sync 全量重建 +
        # materialize 行为文件，避免给 archived worker 创建行为文件的多余副作用）。
        mirror = await self._stores.agent_context_store.get_agent_profile(archived.profile_id)
        if mirror is not None:
            await self._stores.agent_context_store.save_agent_profile(
                mirror.model_copy(
                    update={
                        "status": AgentProfileStatus.ARCHIVED,
                        "archived_at": archived.archived_at,
                        "updated_at": archived.updated_at,
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
        existing: AgentProfile | None = None
        mode = "create"
        if profile_id:
            existing = await self._get_worker_profile_via_mirror(profile_id)
            if existing is not None:
                if existing.origin_kind == AgentProfileOriginKind.BUILTIN:
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
            await self._sync_worker_profile_agent_profile(published)
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
        if existing.origin_kind == AgentProfileOriginKind.BUILTIN:
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
        await self._sync_worker_profile_agent_profile(published)
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
        if existing.origin_kind == AgentProfileOriginKind.BUILTIN:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_BIND_UNSUPPORTED",
                "当前只支持把已发布的自定义 Root Agent 绑定为聊天默认。",
            )
        if existing.status != AgentProfileStatus.ACTIVE:
            raise ControlPlaneActionError(
                "WORKER_PROFILE_NOT_PUBLISHED",
                "请先发布 revision，再绑定为默认聊天 Agent。",
            )
        revision = existing.active_revision or existing.draft_revision or 1
        await self._sync_worker_profile_agent_profile(existing)
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
        if profile.status == AgentProfileStatus.ARCHIVED:
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
                "requested_agent_profile_id": profile.profile_id,
                "requested_agent_profile_version": requested_revision,
                "effective_profile_snapshot_id": self._worker_snapshot_id(
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
                "requested_agent_profile_version": requested_revision,
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
                "source_snapshot_id": work.effective_profile_snapshot_id,
            },
            "profile_id": "",
            "project_id": work.project_id or (selected_project.project_id if selected_project is not None else ""),
        }
        review = await self._review_worker_profile_draft(
            raw=raw,
            mode="extract",
            selected_project=selected_project,
            origin_kind=AgentProfileOriginKind.EXTRACTED,
        )
        if not bool(review.get("can_save")):
            message = "；".join(review.get("save_errors", [])) or "提炼后的 Root Agent 草稿不能保存。"
            raise ControlPlaneActionError("WORKER_PROFILE_EXTRACT_INVALID", message)
        saved = await self._save_worker_profile_draft(
            normalized_profile=review["profile"],
            existing=None,
            origin_kind=AgentProfileOriginKind.EXTRACTED,
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
