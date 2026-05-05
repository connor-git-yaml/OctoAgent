"""Agent 决策与行为辅助模块。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from octoagent.core.behavior_workspace import (
    BEHAVIOR_FILE_BUDGETS,
    BEHAVIOR_OVERLAY_ORDER,
    BehaviorLoadProfile,
    get_profile_allowlist,
    resolve_behavior_workspace,
)
from octoagent.core.behavior_workspace import (
    build_default_behavior_pack_files as build_default_behavior_workspace_pack_files,
)
from octoagent.core.models import (
    AgentProfile,
    BehaviorLayer,
    BehaviorLayerKind,
    BehaviorPack,
    BehaviorPackFile,
    BehaviorSliceEnvelope,
    BehaviorWorkspace,
    AgentDecision,
    AgentDecisionMode,
    AgentLoopPlan,
    ClarificationAction,
    ClarificationDecision,
    DynamicToolSelection,
    RecallPlan,
    RecallPlanMode,
    RuntimeHintBundle,
    ToolUniverseHints,
)

# ---------------------------------------------------------------------------
# Feature 063 T2.4: Session 级 BehaviorPack 缓存 + mtime 脏检查热更新
# ---------------------------------------------------------------------------
# 缓存值 = (BehaviorPack, source_paths, cached_mtime_ns)
# source_paths: 构成该 pack 的磁盘文件路径列表
# cached_mtime_ns: 缓存时所有源文件的最大 mtime（纳秒）
# 命中时通过 _check_behavior_mtime() 校验文件是否被修改。
_behavior_pack_cache: dict[
    tuple[str, str, str, str],
    tuple[BehaviorPack, list[Path], int],
] = {}


def _collect_behavior_source_paths(pack: BehaviorPack, project_root: Path) -> list[Path]:
    """从 BehaviorPack.files 中收集实际磁盘路径。"""
    paths: list[Path] = []
    for f in pack.files:
        hint = f.path_hint.strip()
        if hint:
            p = Path(hint) if Path(hint).is_absolute() else project_root / hint
            if p.exists():
                paths.append(p.resolve())
    return paths


def _max_mtime_ns(paths: list[Path]) -> int:
    """返回路径列表中最大的 mtime（纳秒精度），空列表返回 0。"""
    if not paths:
        return 0
    return max((p.stat().st_mtime_ns for p in paths if p.exists()), default=0)


def _check_behavior_mtime(source_paths: list[Path], cached_mtime_ns: int) -> bool:
    """检查缓存是否仍然有效（文件未被修改）。"""
    if not source_paths:
        return True  # 无磁盘文件（来自 metadata/default templates），总是有效
    return _max_mtime_ns(source_paths) <= cached_mtime_ns


def invalidate_behavior_pack_cache(
    *,
    project_root: Path | str | None = None,
) -> int:
    """清除行为文件缓存。

    Args:
        project_root: 如果提供，仅清除该 project_root 相关的条目；
                      否则清除全部缓存。

    Returns:
        被清除的缓存条目数。
    """
    if project_root is None:
        count = len(_behavior_pack_cache)
        _behavior_pack_cache.clear()
        return count

    root_str = str(Path(project_root).resolve())
    keys_to_remove = [k for k in _behavior_pack_cache if k[2] == root_str]
    for k in keys_to_remove:
        del _behavior_pack_cache[k]
    return len(keys_to_remove)


def behavior_pack_cache_size() -> int:
    """返回当前缓存条目数（供测试和诊断使用）。"""
    return len(_behavior_pack_cache)


def is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
    """判断 AgentProfile 是否为 worker 镜像（来自 WorkerProfile）。

    Feature 090 D2: 优先读 ``agent_profile.kind == "worker"``（显式标记）；
    metadata 探测保留为兼容历史数据的 fallback。F107 完全合并 WorkerProfile
    后可移除 fallback 路径。
    """
    if agent_profile.kind == "worker":
        return True
    metadata = agent_profile.metadata
    return (
        str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
        or bool(str(metadata.get("source_worker_profile_id", "")).strip())
    )


def resolve_behavior_pack(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_root: Path | None = None,
    load_profile: BehaviorLoadProfile = BehaviorLoadProfile.FULL,
) -> BehaviorPack:
    # Feature 063 T2.4: 缓存命中检查 + mtime 脏检查热更新
    resolved_root = str((project_root or Path.cwd()).resolve())
    cache_key = (
        agent_profile.profile_id,
        project_slug,
        resolved_root,
        load_profile.value,
    )
    cached_entry = _behavior_pack_cache.get(cache_key)
    if cached_entry is not None:
        cached_pack, source_paths, cached_mtime_ns = cached_entry
        if _check_behavior_mtime(source_paths, cached_mtime_ns):
            return cached_pack
        # 文件已修改，清除旧缓存重新加载
        del _behavior_pack_cache[cache_key]

    metadata = dict(agent_profile.metadata)
    filesystem_pack = _resolve_filesystem_behavior_pack(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        project_root=project_root or Path.cwd(),
        load_profile=load_profile,
    )
    if filesystem_pack is not None:
        fs_paths = _collect_behavior_source_paths(filesystem_pack, project_root or Path.cwd())
        _behavior_pack_cache[cache_key] = (filesystem_pack, fs_paths, _max_mtime_ns(fs_paths))
        return filesystem_pack

    raw_pack = metadata.get("behavior_pack")
    if isinstance(raw_pack, dict):
        try:
            pack = BehaviorPack.model_validate(raw_pack)
            if not pack.layers:
                pack = pack.model_copy(
                    update={
                        "layers": build_behavior_layers(pack.files),
                        "source_chain": pack.source_chain
                        or ["agent_profile.metadata:behavior_pack"],
                    }
                )
            _behavior_pack_cache[cache_key] = (pack, [], 0)  # metadata 来源无磁盘文件
            return pack
        except Exception:
            pass

    all_files = build_default_behavior_pack_files(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
    )
    # Feature 063: 在 fallback 路径也应用 load_profile 过滤
    profile_allowlist = get_profile_allowlist(load_profile)
    files = [f for f in all_files if f.file_id in profile_allowlist]
    source_chain = ["default_behavior_templates"]
    if agent_profile.scope.value == "project" and project_slug:
        source_chain.append(f"project:{project_slug}")
    clarification_policy = {
        "max_clarification_turns": 2,
        "prefer_single_question": True,
        "fallback_requires_boundary_note": True,
        "delegate_after_clarification_for_realtime": True,
    }
    result = BehaviorPack(
        pack_id=f"behavior-pack:{agent_profile.profile_id}",
        profile_id=agent_profile.profile_id,
        scope=agent_profile.scope.value,
        source_chain=source_chain,
        files=files,
        layers=build_behavior_layers(files),
        clarification_policy=clarification_policy,
        metadata={
            "resolved_from": "default_templates",
            "overlay_order": list(BEHAVIOR_OVERLAY_ORDER),
            "file_budgets": dict(BEHAVIOR_FILE_BUDGETS),
        },
    )
    _behavior_pack_cache[cache_key] = (result, [], 0)  # default templates 无磁盘文件
    return result


def build_default_behavior_pack_files(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
) -> list[BehaviorPackFile]:
    return build_default_behavior_workspace_pack_files(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        include_advanced=False,
    )


def build_behavior_layers(files: list[BehaviorPackFile]) -> list[BehaviorLayer]:
    ordered_layers = [
        BehaviorLayerKind.ROLE,
        BehaviorLayerKind.COMMUNICATION,
        BehaviorLayerKind.SOLVING,
        BehaviorLayerKind.TOOL_BOUNDARY,
        BehaviorLayerKind.MEMORY_POLICY,
        BehaviorLayerKind.BOOTSTRAP,
    ]
    layers: list[BehaviorLayer] = []
    for layer_kind in ordered_layers:
        matching = [item for item in files if item.layer is layer_kind and item.content.strip()]
        if not matching:
            continue
        content = "\n".join(
            (
                f"[{item.file_id}"
                + (
                    f"; truncated {item.effective_char_count}/{item.original_char_count} chars"
                    if item.truncated
                    else ""
                )
                + f"] {item.content.strip()}"
            )
            for item in matching
            if item.content.strip()
        )
        layers.append(
            BehaviorLayer(
                layer=layer_kind,
                content=content,
                source_file_ids=[item.file_id for item in matching],
                original_char_count=sum(item.original_char_count for item in matching),
                effective_char_count=sum(item.effective_char_count for item in matching),
                truncated_file_ids=[item.file_id for item in matching if item.truncated],
                metadata={
                    "file_count": len(matching),
                    "truncated_file_ids": [
                        item.file_id for item in matching if item.truncated
                    ],
                },
            )
        )
    return layers


def build_tool_universe_hints(
    selection: DynamicToolSelection | None,
    *,
    scope: str = "main",
    note: str = "",
    tool_profile_fallback: str = "",
) -> ToolUniverseHints:
    if selection is None:
        return ToolUniverseHints(
            scope=scope,
            tool_profile=tool_profile_fallback.strip(),
            resolution_mode="unavailable",
            note=note,
        )
    effective = selection.effective_tool_universe
    return ToolUniverseHints(
        scope=scope,
        tool_profile=(
            effective.tool_profile
            if effective is not None and effective.tool_profile
            else tool_profile_fallback.strip()
        ),
        resolution_mode=selection.resolution_mode,
        selected_tools=(
            list(effective.selected_tools)
            if effective is not None and effective.selected_tools
            else list(selection.selected_tools)
        ),
        discovery_entrypoints=(
            list(effective.discovery_entrypoints)
            if effective is not None
            else []
        ),
        warnings=list(selection.warnings),
        mounted_tools=list(selection.mounted_tools),
        blocked_tools=list(selection.blocked_tools),
        note=note,
        metadata={
            "selection_id": selection.selection_id,
            "backend": selection.backend,
            "is_fallback": selection.is_fallback,
        },
    )


def build_behavior_slice_envelope(pack: BehaviorPack) -> BehaviorSliceEnvelope:
    """构建 Worker 行为切片信封。

    Feature 063 T2.6: 改用 BehaviorLoadProfile.WORKER 白名单过滤，
    替代原有的 ad-hoc share_with_workers 过滤。
    """
    worker_allowlist = get_profile_allowlist(BehaviorLoadProfile.WORKER)
    # 同时保持 share_with_workers 兼容：取交集
    shared_files = [
        item for item in pack.files
        if item.share_with_workers and item.file_id in worker_allowlist
    ]
    shared_ids = [item.file_id for item in shared_files]
    shared_layers = build_behavior_layers(shared_files)
    return BehaviorSliceEnvelope(
        summary="Worker 仅继承 WORKER profile 定义的行为子集，不继承主 Agent 私有偏好全集。",
        shared_file_ids=shared_ids,
        layers=shared_layers,
        metadata={
            "shared_file_count": len(shared_files),
            "private_file_count": len(pack.files) - len(shared_files),
            "load_profile": BehaviorLoadProfile.WORKER.value,
        },
    )


def _group_bootstrap_template_ids(template_ids: list[str]) -> dict[str, list[str]]:
    groups = {
        "shared": [],
        "agent_private": [],
        "project_shared": [],
        "project_agent": [],
    }
    for template_id in template_ids:
        if template_id.startswith("behavior:system:"):
            groups["shared"].append(template_id)
        elif template_id.startswith("behavior:agent:"):
            groups["agent_private"].append(template_id)
        elif template_id.startswith("behavior:project_agent:"):
            groups["project_agent"].append(template_id)
        elif template_id.startswith("behavior:project:"):
            groups["project_shared"].append(template_id)
    return groups


def _build_bootstrap_routes(
    *,
    path_manifest: dict[str, Any],
    storage_boundary_hints: dict[str, Any],
) -> dict[str, Any]:
    return {
        "facts": {
            "store": storage_boundary_hints.get("facts_store") or "MemoryService",
            "access": (
                storage_boundary_hints.get("facts_access")
                or "使用 MemoryService / memory tools 读取与写入稳定事实。"
            ),
            "summary": "用户稳定事实、长期偏好和已确认上下文写入 Memory，而不是 behavior files。",
        },
        "secrets": {
            "store": storage_boundary_hints.get("secrets_store") or "SecretService",
            "metadata_path": (
                storage_boundary_hints.get("secret_bindings_metadata_path")
                or path_manifest.get("secret_bindings_path")
                or ""
            ),
            "access": (
                storage_boundary_hints.get("secrets_access")
                or "使用 SecretService / secret bindings workflow 管理敏感值。"
            ),
            "summary": (
                "敏感值通过 SecretService / secret bindings workflow 管理；"
                "project.secret-bindings.json 只保存绑定元数据，不保存 secret 值。"
            ),
        },
        "assistant_identity": {
            "target": "IDENTITY.md",
            "summary": "Agent 名称、角色身份和自称放在 Agent private identity。",
        },
        "assistant_personality": {
            "target": "SOUL.md",
            "summary": "Agent 性格、语气和协作风格放在 Agent private soul。",
        },
        "project_instructions": {
            "target": "instructions/README.md",
            "summary": "Project 专属启动说明和协作约束放在 instructions README。",
        },
        "workspace_materials": {
            "roots": list(storage_boundary_hints.get("workspace_roots", [])),
            "summary": "代码、数据、笔记和产物进入 project workspace/data/notes/artifacts。",
        },
    }


def build_behavior_system_summary(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_root: Path | None = None,
) -> dict[str, Any]:
    pack = resolve_behavior_pack(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        project_root=project_root,
    )
    slice_envelope = build_behavior_slice_envelope(pack)
    path_manifest = dict(pack.metadata.get("path_manifest", {}))
    storage_boundary_hints = dict(pack.metadata.get("storage_boundary_hints", {}))
    bootstrap_template_ids = list(agent_profile.bootstrap_template_ids)
    return {
        "source_chain": list(pack.source_chain),
        "clarification_policy": dict(pack.clarification_policy),
        "decision_modes": [item.value for item in AgentDecisionMode],
        "runtime_hint_fields": [
            "can_delegate_research",
            "recent_worker_lane_worker_type",
            "recent_worker_lane_profile_id",
            "recent_worker_lane_topic",
            "recent_worker_lane_summary",
            "recent_clarification_category",
            "recent_clarification_source_text",
            "tool_universe",
        ],
        "files": [
            {
                "file_id": item.file_id,
                "title": item.title,
                "layer": item.layer.value,
                "visibility": item.visibility.value,
                "share_with_workers": item.share_with_workers,
                "source_kind": item.source_kind,
                "path_hint": item.path_hint,
                "is_advanced": bool(item.metadata.get("is_advanced", False)),
                "budget_chars": item.budget_chars,
                "original_char_count": item.original_char_count,
                "effective_char_count": item.effective_char_count,
                "truncated": item.truncated,
                "truncation_reason": item.truncation_reason,
            }
            for item in pack.files
        ],
        "layers": [
            {
                "layer": item.layer.value,
                "source_file_ids": list(item.source_file_ids),
                "truncated_file_ids": list(item.truncated_file_ids),
            }
            for item in pack.layers
        ],
        "worker_slice": {
            "shared_file_ids": list(slice_envelope.shared_file_ids),
            "layers": [item.layer.value for item in slice_envelope.layers],
        },
        "budget": {
            "overlay_order": list(pack.metadata.get("overlay_order", [])),
            "file_budgets": dict(pack.metadata.get("file_budgets", {})),
        },
        "path_manifest": path_manifest,
        "storage_boundary_hints": storage_boundary_hints,
        "bootstrap_template_ids": bootstrap_template_ids,
        "bootstrap_templates": _group_bootstrap_template_ids(bootstrap_template_ids),
        "bootstrap_routes": _build_bootstrap_routes(
            path_manifest=path_manifest,
            storage_boundary_hints=storage_boundary_hints,
        ),
    }


def render_behavior_system_block(
    *,
    agent_profile: AgentProfile,
    project_name: str = "",
    project_slug: str = "",
    project_root: Path | None = None,
    load_profile: BehaviorLoadProfile = BehaviorLoadProfile.FULL,
) -> str:
    pack = resolve_behavior_pack(
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        project_root=project_root,
        load_profile=load_profile,
    )
    effective_layers = (
        build_behavior_slice_envelope(pack).layers
        if load_profile == BehaviorLoadProfile.WORKER
        else build_behavior_layers(pack.files)
    )
    path_manifest = dict(pack.metadata.get("path_manifest", {}))
    storage_boundary_hints = dict(pack.metadata.get("storage_boundary_hints", {}))
    rendered_layers = []
    for layer in effective_layers:
        layer_header = layer.layer.value
        if layer.truncated_file_ids:
            layer_header = (
                f"{layer_header} [truncated files: {', '.join(layer.truncated_file_ids)}]"
            )
        rendered_layers.append(f"{layer_header}: {layer.content}")
    manifest_lines = []
    if path_manifest:
        manifest_lines.extend(
            [
                "ProjectPathManifest:",
                f"repository_root: {path_manifest.get('repository_root') or 'N/A'}",
                f"project_root: {path_manifest.get('project_root') or 'N/A'}",
                f"project_root_source: {path_manifest.get('project_root_source') or 'N/A'}",
                f"project_behavior_root: {path_manifest.get('project_behavior_root') or 'N/A'}",
                f"project_workspace_root: {path_manifest.get('project_workspace_root') or 'N/A'}",
                "project_workspace_root_source: "
                f"{path_manifest.get('project_workspace_root_source') or 'N/A'}",
                f"project_data_root: {path_manifest.get('project_data_root') or 'N/A'}",
                f"project_notes_root: {path_manifest.get('project_notes_root') or 'N/A'}",
                f"project_artifacts_root: {path_manifest.get('project_artifacts_root') or 'N/A'}",
                f"shared_behavior_root: {path_manifest.get('shared_behavior_root') or 'N/A'}",
                f"agent_behavior_root: {path_manifest.get('agent_behavior_root') or 'N/A'}",
                f"project_agent_behavior_root: {path_manifest.get('project_agent_behavior_root') or 'N/A'}",
                f"secret_bindings_path: {path_manifest.get('secret_bindings_path') or 'N/A'}",
            ]
        )
    storage_lines = []
    if storage_boundary_hints:
        workspace_roots = ", ".join(storage_boundary_hints.get("workspace_roots", [])) or "N/A"
        storage_lines.extend(
            [
                "StorageBoundaries:",
                f"facts_store: {storage_boundary_hints.get('facts_store') or 'MemoryService'}",
                f"facts_access: {storage_boundary_hints.get('facts_access') or 'N/A'}",
                f"secrets_store: {storage_boundary_hints.get('secrets_store') or 'SecretService'}",
                f"secrets_access: {storage_boundary_hints.get('secrets_access') or 'N/A'}",
                "secret_bindings_metadata_path: "
                f"{storage_boundary_hints.get('secret_bindings_metadata_path') or 'N/A'}",
                f"behavior_store: {storage_boundary_hints.get('behavior_store') or 'behavior_files'}",
                f"workspace_roots: {workspace_roots}",
                f"boundary_note: {storage_boundary_hints.get('note') or 'N/A'}",
            ]
        )
    return (
        "BehaviorSystem:\n"
        f"source_chain: {', '.join(pack.source_chain) or 'N/A'}\n"
        "budget_policy: per-file char budgets with explicit truncation metadata\n"
        "clarification_policy: "
        f"{pack.clarification_policy}\n"
        "decision_modes: "
        f"{', '.join(item.value for item in AgentDecisionMode)}\n"
        f"{chr(10).join(rendered_layers)}\n"
        f"{chr(10).join(manifest_lines)}\n"
        f"{chr(10).join(storage_lines)}"
    )


def build_behavior_tool_guide_block(
    *,
    workspace: BehaviorWorkspace,
    is_bootstrap_pending: bool = False,
) -> str:
    """生成行为文件工具使用指南 system block。"""
    _modification_hints = {
        "AGENTS.md": "调整 Agent 总体行为约束时",
        "USER.md": "用户表达新偏好时",
        "PROJECT.md": "项目语境变化时",
        "KNOWLEDGE.md": "需要更新知识入口时",
        "TOOLS.md": "调整工具使用策略时",
        "BOOTSTRAP.md": "修改初始化问卷流程时",
        "SOUL.md": "用户自定义语气/风格时",
        "IDENTITY.md": "用户自定义 Agent 名称/定位时",
        "HEARTBEAT.md": "调整运行节奏/自检策略时",
    }
    lines = [
        "[BehaviorToolGuide]",
        "行为文件内容已在上方 BehaviorSystem block 中按 [file_id] 标注展示。",
        "修改时直接基于已有内容生成新版本，调用 behavior.write_file 即可。",
        "",
        "| file_id | 用途 | 修改时机 |",
        "|---------|------|----------|",
    ]
    for f in workspace.files:
        hint = _modification_hints.get(f.file_id, "按需修改")
        lines.append(f"| {f.file_id} | {f.title} | {hint} |")

    lines.extend([
        "",
        "工具参数：",
        "- behavior.write_file(file_id, content, confirmed=false): 修改行为文件",
        "  - file_id: 上表中的文件名（如 USER.md），系统根据当前 session 自动解析磁盘路径",
        "  - content: 完整的新内容（基于上方 BehaviorSystem 中 [file_id] 标注的已有内容修改）",
        "  - confirmed: 默认 false 返回 proposal 供用户确认，确认后传 true 执行写入",
        "",
        "存储边界：",
        "- 稳定事实 -> MemoryService / memory tools",
        "- 规则、人格、偏好 -> behavior files（通过 behavior.write_file）",
        "- 敏感值 -> SecretService / secret bindings workflow",
        "- 代码/数据/文档 -> project workspace roots",
    ])

    if is_bootstrap_pending:
        lines.extend([
            "",
            "[BOOTSTRAP 存储路由]",
            "当前处于初始化阶段，收集到的信息请按以下路由存储：",
            "- 称呼/偏好 -> behavior.write_file(file_id=\"USER.md\", content=...)",
            "- Agent 名称/定位 -> behavior.write_file(file_id=\"IDENTITY.md\", content=...)",
            "- 性格/语气 -> behavior.write_file(file_id=\"SOUL.md\", content=...)",
            "- 稳定事实（时区/地点等） -> memory tools",
            "- 敏感信息 -> SecretService",
        ])

    return "\n".join(lines)


def render_runtime_hint_block(*, user_text: str, runtime_hints: RuntimeHintBundle) -> str:
    tool_universe = runtime_hints.tool_universe
    if tool_universe is None:
        tool_block = (
            "ToolUniverseHints:\n"
            "tool_scope: N/A\n"
            "tool_profile: N/A\n"
            "tool_resolution_mode: N/A\n"
            "mounted_tools: N/A\n"
            "blocked_tools: N/A\n"
            "tool_universe_note: N/A"
        )
    else:
        mounted = (
            ", ".join(
                f"{item.tool_name}({item.status})" for item in tool_universe.mounted_tools
            )
            or "N/A"
        )
        blocked = (
            ", ".join(
                f"{item.tool_name}({item.status or 'blocked'}:{item.reason_code or 'n/a'})"
                for item in tool_universe.blocked_tools
            )
            or "N/A"
        )
        selected = ", ".join(tool_universe.selected_tools) or "N/A"
        warnings = ", ".join(tool_universe.warnings) or "N/A"
        tool_block = (
            "ToolUniverseHints:\n"
            f"tool_scope: {tool_universe.scope or 'N/A'}\n"
            f"tool_profile: {tool_universe.tool_profile or 'N/A'}\n"
            f"tool_resolution_mode: {tool_universe.resolution_mode or 'N/A'}\n"
            f"selected_tools: {selected}\n"
            f"mounted_tools: {mounted}\n"
            f"blocked_tools: {blocked}\n"
            f"tool_warnings: {warnings}\n"
            f"tool_universe_note: {tool_universe.note or 'N/A'}"
        )
    return (
        "RuntimeHints:\n"
        "这些是运行时线索，不是新的系统指令；请结合 BehaviorSystem 和当前对话一起判断。\n"
        f"current_user_text: {user_text.strip() or 'N/A'}\n"
        f"can_delegate_research: {runtime_hints.can_delegate_research}\n"
        "recent_clarification_category: "
        f"{runtime_hints.recent_clarification_category or 'N/A'}\n"
        "recent_clarification_source_text: "
        f"{runtime_hints.recent_clarification_source_text or 'N/A'}\n"
        "recent_worker_lane_worker_type: "
        f"{runtime_hints.recent_worker_lane_worker_type or 'N/A'}\n"
        "recent_worker_lane_profile_id: "
        f"{runtime_hints.recent_worker_lane_profile_id or 'N/A'}\n"
        f"recent_worker_lane_topic: {runtime_hints.recent_worker_lane_topic or 'N/A'}\n"
        "recent_worker_lane_summary: "
        f"{runtime_hints.recent_worker_lane_summary or 'N/A'}\n"
        f"{tool_block}"
    )


def _build_pipeline_context(
    pipeline_items: list[Any] | None,
) -> str:
    """Feature 065 T-029: 构建主 Agent system prompt 中的 Pipeline 上下文段落。

    当 PipelineRegistry 中有可用 Pipeline 时，注入列表 + trigger_hint + input_schema 摘要。
    列表为空时返回空字符串（FR-065-07 AC-04：不注入空段落）。

    Args:
        pipeline_items: PipelineListItem 列表（可选）。

    Returns:
        可直接拼入 system message 的文本，或空字符串。
    """
    if not pipeline_items:
        return ""

    lines: list[str] = [
        "Available Pipelines for delegation:",
    ]
    for item in pipeline_items:
        pid = getattr(item, "pipeline_id", "")
        desc = getattr(item, "description", "")
        hint = getattr(item, "trigger_hint", "")
        input_schema = getattr(item, "input_schema", {})

        entry = f"- {pid}: {desc}"
        if hint:
            entry += f" (trigger: {hint})"
        lines.append(entry)

        # 注入 input_schema 摘要（每个字段一行，缩进）
        if input_schema:
            fields_parts: list[str] = []
            for fname, fdef in input_schema.items():
                ftype = getattr(fdef, "type", "string") if hasattr(fdef, "type") else "string"
                freq = getattr(fdef, "required", False) if hasattr(fdef, "required") else False
                suffix = ", required" if freq else ""
                fields_parts.append(f"{fname} ({ftype}{suffix})")
            lines.append(f"  input: {', '.join(fields_parts)}")

    return "\n".join(lines)


def build_agent_decision_messages(
    *,
    user_text: str,
    behavior_system_block: str,
    runtime_hint_block: str,
    conversation_context_block: str = "",
    project_name: str = "",
    project_slug: str = "",
) -> list[dict[str, str]]:
    # Feature 067: Pipeline 匹配已迁移到 decide_agent_routing() 规则决策层，
    # 此函数不再接收 pipeline_items 参数。
    mode_values = (
        "direct_answer | ask_once | delegate_research | delegate_dev | "
        "delegate_ops | best_effort_answer"
    )
    schema: dict[str, Any] = {
        "mode": mode_values,
        "category": "string",
        "rationale": "string",
        "missing_inputs": ["string"],
        "assumptions": ["string"],
        "tool_intent": "string",
        "target_worker_type": "string",
        "target_worker_profile_id": "string",
        "delegate_objective": "string",
        "continuity_topic": "string",
        "prefer_sticky_worker": "boolean",
        "user_visible_boundary_note": "string",
        "reply_prompt": "string",
    }

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是 OctoAgent 的 AgentDecision resolver。"
                "你的任务不是直接回答用户，而是先基于显式上下文判断下一步动作。"
                "你必须只返回一个 JSON object，不要输出 Markdown、解释或代码块。"
            ),
        },
        {
            "role": "system",
            "content": (
            "决策原则：优先信任 BehaviorSystem 和 RuntimeHints。"
            "除权限、审批、审计、loop guard 等硬边界外，不要退回僵硬硬编码。"
            "当当前挂载工具已经足够时，优先 direct_answer；"
            "不要为了形式上的多 Agent 结构强行委派。"
            "当问题会长期持续、跨多轮、跨权限、跨敏感边界，"
            "或明显更适合 specialist worker 时，再 delegate。"
            "当 delegate 时，不要把用户原话直接转发给 Worker；"
            "应输出 delegate_objective 作为 Worker 任务目标。"
            "当存在近期同题材 specialist lane 时，"
            "优先设置 prefer_sticky_worker=true 或指定 target_worker_profile_id。"
            "当缺关键信息时最多 ask_once；"
            "如果用户显式要求联网但关键事实仍缺失，可以给 best_effort_answer，"
            "但必须明确边界，不能假装已经完成准确查询。"
            ),
        },
        {
            "role": "system",
            "content": (
                f"ProjectDecisionContext:\nproject_name: {project_name or 'N/A'}\n"
                f"project_slug: {project_slug or 'N/A'}"
            ),
        },
        {
            "role": "system",
            "content": behavior_system_block,
        },
        {
            "role": "system",
            "content": runtime_hint_block,
        },
    ]

    if conversation_context_block.strip():
        messages.append({
            "role": "system",
            "content": conversation_context_block,
        })

    messages.append({
        "role": "user",
        "content": (
            f"当前用户消息：{user_text.strip() or 'N/A'}\n\n"
            "请优先输出一个 AgentLoopPlan JSON："
            "{\"decision\": <AgentDecision>, \"recall_plan\": <RecallPlan>}。"
            "如果你只能输出旧版 AgentDecision JSON，也允许。"
            "RecallPlan schema: "
            "{\"mode\":\"skip|recall\",\"query\":\"...\",\"rationale\":\"...\","
            "\"subject_hint\":\"...\",\"focus_terms\":[\"...\"],"
            "\"allow_vault\":false,\"limit\":4}\n"
            f"AgentDecision 字段模板：{json.dumps(schema, ensure_ascii=False)}"
        ),
    })

    return messages


def _parse_recall_plan_payload(payload: dict[str, Any]) -> RecallPlan | None:
    try:
        plan = RecallPlan.model_validate(payload)
    except Exception:
        return None
    if plan.mode is RecallPlanMode.RECALL and not plan.query.strip():
        return plan.model_copy(update={"mode": RecallPlanMode.SKIP})
    return plan


def _parse_agent_decision_payload(payload: dict[str, Any]) -> AgentDecision | None:
    normalized = dict(payload)
    if not normalized.get("target_worker_type") and normalized.get("mode") == "delegate_research":
        normalized["target_worker_type"] = "research"
    if not normalized.get("target_worker_type") and normalized.get("mode") == "delegate_dev":
        normalized["target_worker_type"] = "dev"
    if not normalized.get("target_worker_type") and normalized.get("mode") == "delegate_ops":
        normalized["target_worker_type"] = "ops"
    # Feature 065: delegate_graph 模式确保 pipeline_id 字段透传
    if normalized.get("mode") == "delegate_graph":
        # pipeline_id 和 pipeline_params 直接透传给 AgentDecision
        pass
    try:
        decision = AgentDecision.model_validate(normalized)
    except Exception:
        return None
    # reply_prompt 应由 LLM 在 AgentDecision 中直接生成；
    # 此处不再用硬编码模板补全。
    return decision


def parse_agent_decision_response(content: str) -> AgentDecision | None:
    loop_plan = parse_agent_loop_plan_response(content)
    if loop_plan is not None:
        return loop_plan.decision
    return None


def parse_agent_loop_plan_response(content: str) -> AgentLoopPlan | None:
    raw = content.strip()
    if not raw:
        return None
    candidates = [raw]
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())
    brace_match = re.search(r"\{[\s\S]*\}", raw)
    if brace_match is not None:
        candidates.append(brace_match.group(0).strip())

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "decision" in payload or "recall_plan" in payload:
            decision_payload = payload.get("decision", {})
            recall_payload = payload.get("recall_plan", {})
            if not isinstance(decision_payload, dict):
                decision_payload = {}
            if not isinstance(recall_payload, dict):
                recall_payload = {}
            decision = _parse_agent_decision_payload(decision_payload) or AgentDecision()
            recall_plan = _parse_recall_plan_payload(recall_payload) or RecallPlan()
            return AgentLoopPlan(
                decision=decision,
                recall_plan=recall_plan,
                metadata={
                    "loop_plan_source": "wrapped_json",
                },
            )
        decision = _parse_agent_decision_payload(payload)
        if decision is None:
            continue
        return AgentLoopPlan(
            decision=decision,
            recall_plan=RecallPlan(),
            metadata={
                "loop_plan_source": "legacy_main_decision",
            },
        )
    return None


def _resolve_filesystem_behavior_pack(
    *,
    agent_profile: AgentProfile,
    project_name: str,
    project_slug: str,
    project_root: Path | None,
    load_profile: BehaviorLoadProfile = BehaviorLoadProfile.FULL,
) -> BehaviorPack | None:
    if project_root is None:
        return None

    workspace = resolve_behavior_workspace(
        project_root=project_root,
        agent_profile=agent_profile,
        project_name=project_name,
        project_slug=project_slug,
        load_profile=load_profile,
    )

    return _build_behavior_pack_from_workspace(
        agent_profile=agent_profile,
        workspace=workspace,
    )


def _build_behavior_pack_from_workspace(
    *,
    agent_profile: AgentProfile,
    workspace: BehaviorWorkspace,
) -> BehaviorPack:
    files = [
        BehaviorPackFile(
            file_id=item.file_id,
            title=item.title,
            path_hint=item.path,
            layer=item.layer,
            content=item.content,
            visibility=item.visibility,
            share_with_workers=item.share_with_workers,
            source_kind=item.source_kind,
            budget_chars=item.budget_chars,
            original_char_count=item.original_char_count,
            effective_char_count=item.effective_char_count,
            truncated=item.truncated,
            truncation_reason=item.truncation_reason,
            metadata=dict(item.metadata),
        )
        for item in workspace.files
    ]
    clarification_policy = {
        "max_clarification_turns": 2,
        "prefer_single_question": True,
        "fallback_requires_boundary_note": True,
        "delegate_after_clarification_for_realtime": True,
    }
    return BehaviorPack(
        pack_id=f"behavior-pack:{agent_profile.profile_id}",
        profile_id=agent_profile.profile_id,
        scope=agent_profile.scope.value,
        source_chain=list(workspace.source_chain),
        files=files,
        layers=build_behavior_layers(files),
        clarification_policy=clarification_policy,
        metadata={
            "resolved_from": "filesystem_behavior_workspace",
            "project_slug": workspace.project_slug,
            "overlay_order": list(workspace.metadata.get("overlay_order", [])),
            "file_budgets": dict(workspace.metadata.get("file_budgets", {})),
            "path_manifest": (
                workspace.path_manifest.model_dump(mode="json")
                if workspace.path_manifest is not None
                else {}
            ),
            "storage_boundary_hints": (
                workspace.storage_boundary_hints.model_dump(mode="json")
                if workspace.storage_boundary_hints is not None
                else {}
            ),
        },
    )


def build_runtime_hint_bundle(
    *,
    user_text: str,
    surface: str = "",
    can_delegate_research: bool = False,
    recent_clarification_category: str = "",
    recent_clarification_source_text: str = "",
    recent_worker_lane_worker_type: str = "",
    recent_worker_lane_profile_id: str = "",
    recent_worker_lane_topic: str = "",
    recent_worker_lane_summary: str = "",
    tool_universe: ToolUniverseHints | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeHintBundle:
    return RuntimeHintBundle(
        surface=surface.strip(),
        can_delegate_research=can_delegate_research,
        recent_clarification_category=recent_clarification_category.strip(),
        recent_clarification_source_text=recent_clarification_source_text.strip(),
        recent_worker_lane_worker_type=recent_worker_lane_worker_type.strip(),
        recent_worker_lane_profile_id=recent_worker_lane_profile_id.strip(),
        recent_worker_lane_topic=recent_worker_lane_topic.strip(),
        recent_worker_lane_summary=recent_worker_lane_summary.strip(),
        tool_universe=tool_universe,
        metadata=dict(metadata or {}),
    )


# ---------------------------------------------------------------------------
# Phase 1 (Feature 064): 简单对话识别
# 仅作为性能优化（跳过 memory recall 等准备步骤），不影响主 Agent Execution Loop 的核心执行逻辑。
# 误判（false negative）不影响正确性——未被识别的请求正常进入完整流程。
# ---------------------------------------------------------------------------

# 纯问候语——匹配最常见的问候短语，避免误匹配"你好，帮我查一下..."等复合句
_TRIVIAL_GREETING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(你好|hello|hi|hey|嗨|哈喽|早|早上好|晚上好|下午好)\s*[!！。.？?]*$", re.I),
]

# 身份询问——"你是谁/你是什么模型"等直接身份问题
_TRIVIAL_IDENTITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(你是谁|你是什么|你叫什么|what are you|who are you|你是什么模型)\s*[?？。.]*$", re.I),
]

# 致谢/确认——简短的正向反馈，不含后续请求
_TRIVIAL_ACK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(谢谢|thanks|thank you|好的|ok|好|明白了|知道了|收到|了解)\s*[!！。.]*$", re.I),
]

# 简单元问题——关于 Agent 自身能力的简短提问
_TRIVIAL_META_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(你能做什么|你有什么功能|help|帮助)\s*[?？。.]*$", re.I),
]


def _is_trivial_direct_answer(user_text: str) -> bool:
    """判断用户消息是否为简单直答类型。

    保守匹配，仅覆盖最明确的几类：
    1. 纯问候（你好/hello/hi + 无实质问题）
    2. 身份询问（你是谁/什么模型）
    3. 致谢/确认（谢谢/好的/明白了）
    4. 简单元问题（你能做什么）

    该函数仅作为性能优化（跳过 memory recall 等准备步骤），
    不影响主 Agent Execution Loop 的核心执行逻辑。
    误判不影响正确性——未被识别的请求正常进入完整流程。
    """
    normalized = user_text.strip()
    if not normalized or len(normalized) > 30:
        return False

    for patterns in (
        _TRIVIAL_GREETING_PATTERNS,
        _TRIVIAL_IDENTITY_PATTERNS,
        _TRIVIAL_ACK_PATTERNS,
        _TRIVIAL_META_PATTERNS,
    ):
        for pattern in patterns:
            if pattern.match(normalized):
                return True
    return False


def _match_pipeline_trigger(
    user_text: str,
    pipeline_items: list[Any] | None,
) -> AgentDecision | None:
    """Feature 067: 当用户请求匹配 Pipeline trigger_hint 时返回 DELEGATE_GRAPH。

    简单的关键词包含匹配：将 trigger_hint 中的关键词与用户输入对比。
    匹配条件：用户输入包含 trigger_hint 中的所有非停用词（> 1 字符），
    或 pipeline_id 整体出现在用户输入中。

    Args:
        user_text: 用户输入（已 strip）
        pipeline_items: PipelineListItem 列表

    Returns:
        匹配成功返回 AgentDecision(DELEGATE_GRAPH)，否则 None
    """
    if not pipeline_items:
        return None

    lower_text = user_text.lower()

    for item in pipeline_items:
        pid = getattr(item, "pipeline_id", "")
        hint = getattr(item, "trigger_hint", "")
        tags = getattr(item, "tags", []) or []

        # 1. pipeline_id 整体出现在用户输入中
        if pid and pid.lower() in lower_text:
            return AgentDecision(
                mode=AgentDecisionMode.DELEGATE_GRAPH,
                pipeline_id=pid,
                rationale=f"用户输入中包含 pipeline_id '{pid}'，匹配确定性 Pipeline。",
                metadata={"pipeline_tags": list(tags), "match_type": "pipeline_id"},
            )

        # 2. trigger_hint 关键词匹配
        if hint:
            hint_words = [w for w in hint.lower().split() if len(w) > 1]
            if hint_words and all(w in lower_text for w in hint_words):
                return AgentDecision(
                    mode=AgentDecisionMode.DELEGATE_GRAPH,
                    pipeline_id=pid,
                    rationale=f"用户请求匹配 Pipeline '{pid}' 的 trigger_hint。",
                    metadata={"pipeline_tags": list(tags), "match_type": "trigger_hint"},
                )

    return None


def decide_agent_routing(
    user_text: str,
    *,
    runtime_hints: RuntimeHintBundle | None = None,
    pipeline_items: list[Any] | None = None,
) -> AgentDecision:
    """Agent 规则快速路径决策——判断直答 vs Pipeline 委派。

    天气等场景不再做专属分支，统一由 Agent Direct Execution + web.search 处理。
    """
    normalized = user_text.strip()
    if not normalized:
        return AgentDecision()

    # Pipeline trigger_hint 规则匹配
    pipeline_match = _match_pipeline_trigger(normalized, pipeline_items)
    if pipeline_match is not None:
        return pipeline_match

    return AgentDecision()


def decide_clarification(user_text: str) -> ClarificationDecision:
    decision = decide_agent_routing(user_text)
    if decision.mode is AgentDecisionMode.ASK_ONCE:
        return ClarificationDecision(
            action=ClarificationAction.CLARIFY,
            category=decision.category,
            rationale=decision.rationale,
            missing_inputs=list(decision.missing_inputs),
            followup_prompt=decision.reply_prompt,
            metadata=dict(decision.metadata),
        )
    if decision.mode is AgentDecisionMode.BEST_EFFORT_ANSWER:
        return ClarificationDecision(
            action=ClarificationAction.BEST_EFFORT_FALLBACK,
            category=decision.category,
            rationale=decision.rationale,
            missing_inputs=list(decision.missing_inputs),
            followup_prompt=decision.reply_prompt,
            fallback_hint=decision.user_visible_boundary_note,
            metadata=dict(decision.metadata),
        )
    return ClarificationDecision()


