"""builtin_tools 共享依赖容器与辅助函数。

所有内置工具 handler 通过 ToolDeps 获取运行时依赖，
避免直接持有 CapabilityPackService 的引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from octoagent.core.behavior_workspace import project_root_dir
from octoagent.core.models import (
    WORK_TERMINAL_STATUSES,
    MemoryNamespaceKind,
    ProjectBindingType,
)

from ..execution_context import get_current_execution_context
from ..task_service import TaskService


class WorkerMemoryNamespaceNotResolved(Exception):
    """F094 B3 (NFR-3): worker 默认 memory namespace 解析失败时显式 raise。

    场景：
    - 调用方未传 scope_id + agent_runtime_id 非空
    - MemoryNamespace 表未找到 (project_id, agent_runtime_id, AGENT_PRIVATE) 的
      active 记录（worker dispatch 路径应该已经创建过；缺失意味着上游 dispatch
      未跑或被破坏，需要显式 fail-fast 让上游可观测）
    """

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
    _snapshot_store: Any = None  # F084 Phase 2 T022-T025
    _graph_pipeline_tool: Any = None  # GraphPipelineTool 实例（lifespan 内 late-bind）

    @property
    def task_runner(self):
        if self._task_runner is None:
            raise RuntimeError("task_runner not bound yet")
        return self._task_runner

    @property
    def snapshot_store(self):
        if self._snapshot_store is None:
            raise RuntimeError("snapshot_store not bound yet (F084 Phase 2 T033 待接入)")
        return self._snapshot_store

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


async def resolve_worker_default_scope_id(
    deps: ToolDeps,
    *,
    project_id: str,
    agent_runtime_id: str,
) -> str:
    """F094 B3 (Codex plan HIGH-1 闭环): worker / main agent 默认写入 fact 时，
    按 (project_id, agent_runtime_id, AGENT_PRIVATE) 三元组解析 worker 私有
    namespace 的第一个 scope_id。

    与 Codex plan §0.1 锁定的注入点对齐：memory.write 工具不再走
    `session_memory_extractor._resolve_scope_id`（那是 SessionMemoryExtractor
    内部函数，仅 main_bootstrap 走），而是用此独立 helper。

    成功路径：返回 namespace.memory_scope_ids[0]（保留 baseline scope_id 形态
    `memory/private/{owner}/...`，spec §0 锁定不动 build_private_memory_scope_ids
    函数本身）。

    失败路径（NFR-3 显式 raise）：未命中 namespace 或 namespace 没有 scope_ids。
    """
    if not agent_runtime_id:
        raise WorkerMemoryNamespaceNotResolved(
            "agent_runtime_id is empty; cannot resolve AGENT_PRIVATE namespace"
        )
    namespaces = await deps.stores.agent_context_store.list_memory_namespaces(
        project_id=project_id or None,
        agent_runtime_id=agent_runtime_id,
        kind=MemoryNamespaceKind.AGENT_PRIVATE,
        # F094 C7b: 默认 active path（include_archived=False）；archived 数据
        # 不参与业务路径。
    )
    if not namespaces:
        raise WorkerMemoryNamespaceNotResolved(
            f"no AGENT_PRIVATE namespace for "
            f"(project_id={project_id!r}, agent_runtime_id={agent_runtime_id!r})"
        )
    if len(namespaces) > 1:
        # 三元组 partial unique index（C2）保证同三元组在 archived_at IS NULL
        # 路径上唯一；多于 1 条意味着 schema 约束失效——显式 raise。
        raise WorkerMemoryNamespaceNotResolved(
            f"multiple active AGENT_PRIVATE namespaces for "
            f"(project_id={project_id!r}, agent_runtime_id={agent_runtime_id!r}); "
            f"unique constraint violation"
        )
    namespace = namespaces[0]
    if not namespace.memory_scope_ids:
        raise WorkerMemoryNamespaceNotResolved(
            f"AGENT_PRIVATE namespace {namespace.namespace_id!r} has no scope_ids"
        )
    return namespace.memory_scope_ids[0]


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
