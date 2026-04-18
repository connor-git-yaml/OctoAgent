"""builtin_tools 共享依赖容器与辅助函数。

所有内置工具 handler 通过 ToolDeps 获取运行时依赖，
避免直接持有 CapabilityPackService 的引用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from octoagent.core.behavior_workspace import project_root_dir
from octoagent.core.models import WORK_TERMINAL_STATUSES, ProjectBindingType

from ..execution_context import get_current_execution_context
from ..task_service import TaskService

_MEMORY_BINDING_TYPES = {
    ProjectBindingType.SCOPE,
    ProjectBindingType.MEMORY_SCOPE,
    ProjectBindingType.IMPORT_SCOPE,
}

# Work 终态值集合（delegation/supervision 工具共用）
# 从 core.WORK_TERMINAL_STATUSES 派生，保持单一事实源
WORK_TERMINAL_VALUES = {s.value for s in WORK_TERMINAL_STATUSES}


@dataclass
class ToolDeps:
    """工具 handler 的共享依赖容器。"""

    project_root: Path
    stores: Any
    tool_broker: Any
    tool_index: Any
    skill_discovery: Any
    memory_console_service: Any
    memory_runtime_service: Any
    browser_sessions: dict = field(default_factory=dict)
    # 延迟绑定
    _task_runner: Any = None
    _delegation_plane: Any = None
    _mcp_registry: Any = None
    _mcp_installer: Any = None
    _pack_service: Any = None  # 弱引用回 CapabilityPackService

    @property
    def task_runner(self):
        if self._task_runner is None:
            raise RuntimeError("task_runner not bound yet")
        return self._task_runner

    @property
    def delegation_plane(self):
        if self._delegation_plane is None:
            raise RuntimeError("delegation_plane not bound yet")
        return self._delegation_plane

    @property
    def mcp_registry(self):
        return self._mcp_registry

    @property
    def mcp_installer(self):
        return self._mcp_installer


# ---------------------------------------------------------------------------
# 共享辅助函数
# ---------------------------------------------------------------------------


async def current_parent(deps: ToolDeps) -> tuple[TaskService, Any, Any]:
    """返回 (TaskService, execution_context, task)。"""
    context = get_current_execution_context()
    task_service = TaskService(deps.stores, project_root=deps.project_root)
    task = await deps.stores.task_store.get_task(context.task_id)
    if task is None:
        raise RuntimeError("current task not found for builtin tool")
    return task_service, context, task


async def resolve_runtime_project_context(
    deps: ToolDeps,
    *,
    project_id: str = "",
) -> tuple[Any, Any, Any | None]:
    """解析运行时 project context。

    优先使用显式 project_id；若无则从当前 execution context 推断；
    最终 fallback 到 pack_service._resolve_project_context。
    """
    task = None
    if project_id.strip():
        project, workspace = await deps._pack_service._resolve_project_context(
            project_id=project_id.strip(),
        )
        return project, workspace, task
    try:
        task_service, _context, task = await current_parent(deps)
    except Exception:
        task = None
        task_service = TaskService(deps.stores, project_root=deps.project_root)
    if task is not None:
        project, workspace = await task_service._agent_context.resolve_project_scope(
            task=task,
            surface=task.requester.channel,
        )
        if project is not None or workspace is not None:
            return project, workspace, task
    project, workspace = await deps._pack_service._resolve_project_context(
        project_id="",
    )
    return project, workspace, task


async def resolve_memory_scope_ids(
    deps: ToolDeps,
    *,
    task: Any | None,
    project: Any,
    explicit_scope_id: str = "",
) -> list[str]:
    """解析记忆 scope ID 列表。

    explicit_scope_id 必须属于当前 task 或 project bindings 的白名单；
    越界时静默丢弃并记录告警——防止 LLM 传入其他 project 的 scope 造成越权。
    """
    # 先构建白名单：当前 task.scope_id + project memory bindings
    bindings: list[Any] = []
    allowed: set[str] = set()
    if task is not None and task.scope_id:
        allowed.add(task.scope_id)
    if project is not None:
        bindings = await deps.stores.project_store.list_bindings(project.project_id)
        for binding in bindings:
            if binding.binding_type not in _MEMORY_BINDING_TYPES:
                continue
            if binding.binding_key:
                allowed.add(binding.binding_key)

    scope_ids: list[str] = []
    if explicit_scope_id.strip():
        candidate = explicit_scope_id.strip()
        # 白名单为空通常是无 task / 无 project binding 的直连场景（测试、
        # 独立工具调用）——此时没有可参考的安全边界，放行 candidate；
        # 生产路径的 task runner 会 bind execution context、task.scope_id
        # 必然非空，落到严格分支。
        if not allowed or candidate in allowed:
            scope_ids.append(candidate)
        else:
            import structlog
            structlog.get_logger(__name__).warning(
                "memory_scope_outside_binding",
                explicit_scope_id=candidate,
                allowed_count=len(allowed),
                project_id=(project.project_id if project is not None else ""),
            )
    elif task is not None and task.scope_id:
        scope_ids.append(task.scope_id)

    for binding in bindings:
        if binding.binding_type not in _MEMORY_BINDING_TYPES:
            continue
        if binding.binding_key:
            scope_ids.append(binding.binding_key)
    return list(dict.fromkeys(item for item in scope_ids if item))


async def resolve_instance_root(
    deps: ToolDeps,
    *,
    project_id: str = "",
) -> tuple[Path, str]:
    """返回 (project 隔离目录, project_slug)。

    Agent 的 filesystem/terminal 工具以 project 目录为根。
    当无活跃 project 时 fallback 到 projects/_default/（而非整个 instance root）。
    """
    project, _workspace, _task = await resolve_runtime_project_context(
        deps,
        project_id=project_id,
    )
    slug = project.slug if project is not None and project.slug else ""
    if slug:
        agent_root = project_root_dir(deps.project_root, slug)
    else:
        # 无活跃 project → 使用 _default 沙箱，不暴露整个 instance root
        agent_root = deps.project_root / "projects" / "_default"
        slug = "_default"
    agent_root.mkdir(parents=True, exist_ok=True)
    return agent_root.resolve(), slug


def resolve_and_check_path(
    instance_root: Path,
    raw_path: str,
    global_instance_root: Path,
    current_project_slug: str,
) -> Path:
    """解析路径并执行 PathAccessPolicy 检查。

    所有 filesystem 工具统一使用此函数。
    白名单内自动放行，黑名单内直接拒绝，灰名单交给 permission check。
    纯函数，不需要 deps。
    """
    from octoagent.tooling.path_policy import (
        PathVerdict,
        check_path_access,
    )

    normalized = raw_path.strip()
    candidate = Path(normalized) if normalized else instance_root
    if str(candidate).startswith("~"):
        candidate = candidate.expanduser()
    if not candidate.is_absolute():
        candidate = instance_root / candidate
    resolved = candidate.resolve()

    # 路径访问策略检查（对 global instance root 执行）
    result = check_path_access(resolved, global_instance_root, current_project_slug)

    if result.verdict == PathVerdict.DENY:
        raise RuntimeError(
            f"访问被拒绝: {result.reason}。"
            f"该路径包含系统内部文件，Agent 不可访问。"
        )

    if result.verdict == PathVerdict.NEEDS_APPROVAL:
        # 灰名单路径（instance root 外）— 仍然允许访问，
        # 但 permission.py 会把 SideEffectLevel 升级为 IRREVERSIBLE 触发审批
        pass

    return resolved


def truncate_text(value: str, *, limit: int = 100_000) -> str:
    """截断过长文本并附加提示信息。纯函数，不需要 deps。"""
    text = value.strip()
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return (
        f"{text[:limit].rstrip()}\n\n"
        f"⚠️ [内容已截断：原文 {len(text)} 字符，已显示前 {limit} 字符，"
        f"省略 {omitted} 字符。如需完整内容请增大 max_chars 参数。]"
    )


async def current_work_context(deps: ToolDeps) -> tuple[Any, Any]:
    """返回 (execution_context, task)，要求 context 携带 work_id。"""
    _, context, task = await current_parent(deps)
    if not context.work_id:
        raise RuntimeError("current execution context does not carry work_id")
    return context, task


def coerce_objectives(objectives: list[str] | str) -> list[str]:
    """将 objectives 规范化为字符串列表。纯函数，不需要 deps。"""
    if isinstance(objectives, list):
        return [item.strip() for item in objectives if item and item.strip()]
    return [item.strip() for item in str(objectives).splitlines() if item.strip()]


async def launch_child(
    deps: ToolDeps,
    *,
    objective: str,
    worker_type: str,
    target_kind: str,
    tool_profile: str = "minimal",
    title: str = "",
) -> dict[str, Any]:
    """启动子任务并返回结果字典。"""
    context, parent_task = await current_work_context(deps)
    parent_work = await deps.stores.work_store.get_work(context.work_id)
    if parent_work is None:
        raise RuntimeError(f"current work not found: {context.work_id}")
    return await deps._pack_service._launch_child_task(
        parent_task=parent_task,
        parent_work=parent_work,
        objective=objective,
        worker_type=worker_type,
        target_kind=target_kind,
        tool_profile=tool_profile,
        title=title,
        spawned_by="builtin_tool",
    )


async def descendant_works_for_current_context(
    deps: ToolDeps,
) -> tuple[Any, list[Any]]:
    """返回 (execution_context, 排序后的后代 work 列表)。"""
    if deps._delegation_plane is None:
        raise RuntimeError("delegation plane is not bound for descendant work lookup")
    context, _task = await current_work_context(deps)
    descendants = await deps._delegation_plane.list_descendant_works(context.work_id)
    descendants.sort(key=lambda item: item.created_at)
    return context, descendants


async def resolve_child_work(
    deps: ToolDeps,
    *,
    task_id: str = "",
    work_id: str = "",
):
    """从后代 work 列表中按 task_id 或 work_id 定位目标 work。

    返回 (execution_context, target_work, descendants)。
    """
    context, descendants = await descendant_works_for_current_context(deps)
    if work_id.strip():
        target = next(
            (item for item in descendants if item.work_id == work_id.strip()),
            None,
        )
        if target is None:
            raise RuntimeError(f"descendant work not found: {work_id}")
        return context, target, descendants
    if task_id.strip():
        target = next(
            (item for item in descendants if item.task_id == task_id.strip()),
            None,
        )
        if target is None:
            raise RuntimeError(f"descendant task not found: {task_id}")
        return context, target, descendants
    raise RuntimeError("either task_id or work_id is required")
