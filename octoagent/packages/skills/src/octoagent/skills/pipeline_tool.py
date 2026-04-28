"""Feature 065 Phase 2: GraphPipelineTool — LLM 工具层。

将 Pipeline 能力暴露给 LLM，支持 6 个 action:
list / start / status / resume / cancel / retry

设计决策（来自 plan.md）：
- GraphPipelineTool 持有独立的 SkillPipelineEngine 实例
- Pipeline 执行通过 asyncio.create_task 后台运行，execute() 立即返回
- 并发 run 计数由本类维护（非 Engine 层）
- start 时快照 definition 到 run.metadata["definition_snapshot"]
- Child Task / Work 创建是可选的（依赖 StoreGroup 是否注入）
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from ulid import ULID

from octoagent.core.models import (
    PipelineRunStatus,
    SkillPipelineDefinition,
    Task,
    Work,
)
from octoagent.core.models.delegation import (
    DelegationTargetKind,
    WorkKind,
    WorkStatus,
)
from octoagent.core.models.enums import TaskStatus
from octoagent.core.models.task import RequesterInfo, TaskPointers
from octoagent.core.store import StoreGroup
from octoagent.core.models.tool_results import GraphPipelineResult
from octoagent.tooling.decorators import tool_contract
from octoagent.tooling.models import SideEffectLevel, ToolTier

from .pipeline import EventRecorder, SkillPipelineEngine
from .pipeline_handlers import BUILTIN_HANDLERS
from .pipeline_models import PipelineManifest
from .pipeline_registry import PipelineRegistry

logger = structlog.get_logger(__name__)

# 支持的 action 列表
_SUPPORTED_ACTIONS = frozenset({"list", "start", "status", "resume", "cancel", "retry"})

# 默认最大并发 Pipeline run 数量
DEFAULT_MAX_CONCURRENT_RUNS = 10

# Pipeline run 终态集合
_TERMINAL_STATUSES = frozenset({
    PipelineRunStatus.SUCCEEDED,
    PipelineRunStatus.FAILED,
    PipelineRunStatus.CANCELLED,
})

# Pipeline run 可恢复状态集合
_RESUMABLE_STATUSES = frozenset({
    PipelineRunStatus.WAITING_INPUT,
    PipelineRunStatus.WAITING_APPROVAL,
})

# Pipeline -> Task/Work 状态映射（模块级常量）
_PIPELINE_TO_TASK_STATUS = {
    PipelineRunStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
    PipelineRunStatus.FAILED: TaskStatus.FAILED,
    PipelineRunStatus.CANCELLED: TaskStatus.CANCELLED,
}

_PIPELINE_TO_WORK_STATUS = {
    PipelineRunStatus.SUCCEEDED: WorkStatus.SUCCEEDED,
    PipelineRunStatus.FAILED: WorkStatus.FAILED,
    PipelineRunStatus.CANCELLED: WorkStatus.CANCELLED,
}

_PIPELINE_TO_WAITING_TASK = {
    PipelineRunStatus.WAITING_APPROVAL: TaskStatus.WAITING_APPROVAL,
    PipelineRunStatus.WAITING_INPUT: TaskStatus.WAITING_INPUT,
}


class GraphPipelineTool:
    """Graph Pipeline LLM 工具。

    封装 PipelineRegistry + SkillPipelineEngine，
    为 LLM 提供 list / start / status / resume / cancel / retry 6 个 action。

    依赖注入：
    - registry（必须）: PipelineRegistry 实例
    - store_group（可选）: 提供时创建 Child Task + Work
    - event_recorder（可选）: 事件记录器
    - max_concurrent_runs: 最大并发数，默认 10
    """

    def __init__(
        self,
        *,
        registry: PipelineRegistry,
        store_group: StoreGroup | None = None,
        event_recorder: EventRecorder | None = None,
        max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
    ) -> None:
        self._registry = registry
        self._store_group = store_group
        self._event_recorder = event_recorder
        self._max_concurrent_runs = max_concurrent_runs

        # 创建独立 Engine 实例（与 DelegationPlane 内部 Engine 分离）
        if store_group is not None:
            self._engine = SkillPipelineEngine(
                store_group=store_group,
                event_recorder=event_recorder,
            )
            # 注册内置 handler
            for handler_id, handler_fn in BUILTIN_HANDLERS.items():
                self._engine.register_handler(handler_id, handler_fn)
        else:
            self._engine = None  # type: ignore[assignment]

        # 并发 run 计数
        self._active_run_count = 0

        # 内存中跟踪活跃 run → definition 映射（用于 resume/retry 恢复 definition）
        self._run_definitions: dict[str, SkillPipelineDefinition] = {}
        # 内存中跟踪 run → 后台 task 映射
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}

    @property
    def engine(self) -> SkillPipelineEngine:
        """获取底层 Engine 实例（测试用）。"""
        return self._engine

    @property
    def active_run_count(self) -> int:
        return self._active_run_count

    # ============================================================
    # 工具入口
    # ============================================================

    @tool_contract(
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        tool_group="orchestration",
        tier=ToolTier.DEFERRED,
        tags=["pipeline", "graph", "orchestration"],
        name="graph_pipeline",
        version="1.0.0",
        produces_write=True,
    )
    async def execute(
        self,
        *,
        action: str,
        pipeline_id: str = "",
        run_id: str = "",
        params: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
        approved: bool | None = None,
        # 内部注入（不暴露给 LLM schema）
        task_id: str = "",
        session_metadata: dict[str, Any] | None = None,
    ) -> GraphPipelineResult:
        """发现、启动、监控和管理确定性 Pipeline 流程。"""
        # 校验 action（所有 action 统一走 WriteResult 契约）
        if action not in _SUPPORTED_ACTIONS:
            msg = f"Error: unknown action '{action}'. Supported actions: {', '.join(sorted(_SUPPORTED_ACTIONS))}."
            return GraphPipelineResult(
                status="rejected",
                target=run_id or pipeline_id or "pipeline",
                preview=msg[:200],
                reason=msg,
                detail=msg,
                action="start",  # 占位，表示意图失败
            )

        # list / status 是只读查询，包装成 preview + detail 返回（读写统一契约）
        if action == "list":
            text = self._handle_list()
            return GraphPipelineResult(
                status="written",
                target="pipeline_registry",
                preview=text[:200],
                detail=text,
                action="start",  # list 无对应写 action，以最常见写 action 代替
            )
        elif action == "status":
            text = await self._handle_status(run_id=run_id)
            is_error = text.startswith("Error:")
            return GraphPipelineResult(
                status="rejected" if is_error else "written",
                target=run_id,
                preview=text[:200],
                reason=text if is_error else None,
                detail=text,
                action="start",
                run_id=run_id,
            )
        elif action == "start":
            text = await self._handle_start(
                pipeline_id=pipeline_id,
                params=params or {},
                parent_task_id=task_id,
            )
            # _handle_start 返回错误时 text 以 "Error:" 开头
            if text.startswith("Error:"):
                return GraphPipelineResult(
                    status="rejected",
                    target=pipeline_id,
                    preview=text[:200],
                    reason=text,
                    detail=text,
                    action="start",
                )
            # 从返回文本中提取 run_id / task_id
            extracted_run_id = ""
            extracted_task_id = ""
            for line in text.splitlines():
                if line.startswith("run_id:"):
                    extracted_run_id = line.split(":", 1)[1].strip()
                elif line.startswith("task_id:"):
                    extracted_task_id = line.split(":", 1)[1].strip()
            return GraphPipelineResult(
                status="pending",
                target=pipeline_id,
                preview=f"Pipeline '{pipeline_id}' started, run_id={extracted_run_id}",
                reason=f"后台执行中，使用 graph_pipeline(action='status', run_id='{extracted_run_id}') 追踪",
                detail=text,
                action="start",
                run_id=extracted_run_id or None,
                task_id=extracted_task_id or None,
            )
        elif action == "resume":
            text = await self._handle_resume(
                run_id=run_id,
                input_data=input_data or {},
                approved=approved,
            )
            if text.startswith("Error:"):
                return GraphPipelineResult(
                    status="rejected",
                    target=run_id,
                    preview=text[:200],
                    reason=text,
                    detail=text,
                    action="resume",
                    run_id=run_id,
                )
            return GraphPipelineResult(
                status="written",
                target=run_id,
                preview=text[:200],
                detail=text,
                action="resume",
                run_id=run_id,
            )
        elif action == "cancel":
            text = await self._handle_cancel(run_id=run_id)
            if text.startswith("Error:"):
                return GraphPipelineResult(
                    status="rejected",
                    target=run_id,
                    preview=text[:200],
                    reason=text,
                    detail=text,
                    action="cancel",
                    run_id=run_id,
                )
            return GraphPipelineResult(
                status="written",
                target=run_id,
                preview=text[:200],
                detail=text,
                action="cancel",
                run_id=run_id,
            )
        elif action == "retry":
            text = await self._handle_retry(run_id=run_id)
            if text.startswith("Error:"):
                return GraphPipelineResult(
                    status="rejected",
                    target=run_id,
                    preview=text[:200],
                    reason=text,
                    detail=text,
                    action="retry",
                    run_id=run_id,
                )
            return GraphPipelineResult(
                status="written",
                target=run_id,
                preview=text[:200],
                detail=text,
                action="retry",
                run_id=run_id,
            )

        # 不应到达此处
        return GraphPipelineResult(
            status="rejected",
            target=run_id or pipeline_id or "pipeline",
            preview=f"unknown action '{action}'",
            reason=f"unknown action '{action}'",
            detail=f"unknown action '{action}'",
            action="start",
        )

    # ============================================================
    # action="list"  (T-065-014)
    # ============================================================

    def _handle_list(self) -> str:
        """返回可用 Pipeline 列表（LLM 可读格式）。"""
        items = self._registry.list_items()
        if not items:
            return "No pipelines available. Pipeline definitions are loaded from PIPELINE.md files."

        lines: list[str] = [f"Available Pipelines ({len(items)}):"]
        lines.append("")

        for idx, item in enumerate(items, start=1):
            lines.append(f"{idx}. {item.pipeline_id}")
            lines.append(f"   Description: {item.description}")
            if item.tags:
                lines.append(f"   Tags: {', '.join(item.tags)}")
            if item.trigger_hint:
                lines.append(f"   Trigger: {item.trigger_hint}")
            if item.input_schema:
                input_parts = []
                for field_name, field_def in item.input_schema.items():
                    req = ", required" if field_def.required else ""
                    default_str = (
                        f", default={field_def.default}"
                        if field_def.default is not None
                        else ""
                    )
                    input_parts.append(f"{field_name} ({field_def.type}{req}{default_str})")
                lines.append(f"   Input: {', '.join(input_parts)}")
            lines.append("")

        lines.append(
            'Use graph_pipeline(action="start", pipeline_id="<id>", params={{...}}) '
            "to start a pipeline."
        )
        return "\n".join(lines)

    # ============================================================
    # action="start"  (T-065-015, T-065-016, T-065-017, T-065-024)
    # ============================================================

    async def _handle_start(
        self,
        *,
        pipeline_id: str,
        params: dict[str, Any],
        parent_task_id: str,
    ) -> str:
        # T-065-016: pipeline_id 存在性验证
        if not pipeline_id:
            return (
                "Error: pipeline_id is required for start action. "
                "Use graph_pipeline(action='list') to see available pipelines."
            )

        manifest = self._registry.get(pipeline_id)
        if manifest is None:
            return (
                f"Error: pipeline not found: '{pipeline_id}'. "
                f"Use graph_pipeline(action='list') to see available pipelines."
            )

        # T-065-016: params 验证（required 字段必须提供）
        validation_error = self._validate_params(manifest, params)
        if validation_error:
            return validation_error

        # T-065-017: 并发上限检查
        if self._active_run_count >= self._max_concurrent_runs:
            return (
                f"Error: maximum concurrent pipeline runs reached "
                f"({self._max_concurrent_runs}). "
                f"Cancel or wait for existing runs to complete."
            )

        # Engine 必须可用
        if self._engine is None:
            return "Error: pipeline engine not available (store_group not provided)."

        run_id = str(ULID())
        child_task_id = ""
        work_id = ""

        # 创建 Child Task + Work（可选，依赖 StoreGroup）
        if self._store_group is not None:
            now = datetime.now(tz=UTC)
            child_task_id = str(ULID())
            work_id = str(ULID())

            child_task = Task(
                task_id=child_task_id,
                created_at=now,
                updated_at=now,
                title=f"Pipeline: {manifest.pipeline_id}",
                requester=RequesterInfo(channel="pipeline", sender_id="graph_pipeline_tool"),
                pointers=TaskPointers(),
                trace_id=f"trace-{child_task_id}",
                parent_task_id=parent_task_id or None,
            )
            await self._store_group.task_store.create_task(child_task)

            work = Work(
                work_id=work_id,
                task_id=child_task_id,
                title=f"Pipeline run: {manifest.pipeline_id}",
                kind=WorkKind.PIPELINE,
                target_kind=DelegationTargetKind.GRAPH_AGENT,
                pipeline_run_id=run_id,
                metadata={
                    "pipeline_id": manifest.pipeline_id,
                    "run_id": run_id,
                },
            )
            await self._store_group.work_store.save_work(work)
            await self._store_group.conn.commit()

        # T-065-024: 快照 definition 到内存（Engine 层的 run.metadata 中也存）
        definition = manifest.definition
        self._run_definitions[run_id] = definition

        # 递增并发计数
        self._active_run_count += 1

        # 后台执行 Pipeline（T-065-015 核心）
        bg_task = asyncio.create_task(
            self._execute_pipeline_run(
                definition=definition,
                task_id=child_task_id or "no-task",
                work_id=work_id or "no-work",
                initial_state=params,
                run_id=run_id,
                manifest=manifest,
            )
        )
        self._run_tasks[run_id] = bg_task

        result_lines = [
            f"Pipeline '{pipeline_id}' started successfully.",
            f"run_id: {run_id}",
        ]
        if child_task_id:
            result_lines.append(f"task_id: {child_task_id}")
        result_lines.append("")
        result_lines.append(
            f"The pipeline is running in the background. "
            f"Use graph_pipeline(action=\"status\", run_id=\"{run_id}\") to check progress."
        )
        return "\n".join(result_lines)

    async def _execute_pipeline_run(
        self,
        *,
        definition: SkillPipelineDefinition,
        task_id: str,
        work_id: str,
        initial_state: dict[str, Any],
        run_id: str,
        manifest: PipelineManifest,
    ) -> None:
        """后台执行 Pipeline run，完成后同步终态。"""
        try:
            # T-065-024: 在 Engine 的 initial_state 中嵌入 definition_snapshot
            enriched_state = {
                **initial_state,
            }

            run = await self._engine.start_run(
                definition=definition,
                task_id=task_id,
                work_id=work_id,
                initial_state=enriched_state,
                run_id=run_id,
            )

            # T-065-023: Pipeline 终态同步到 Task/Work
            await self._sync_terminal_state(run.status, task_id, work_id, run.run_id)

            # T-065-037: Pipeline WAITING 状态同步到 Child Task
            await self._sync_waiting_state(run, task_id)

        except Exception as exc:
            logger.error(
                "pipeline_run_background_error",
                run_id=run_id,
                pipeline_id=manifest.pipeline_id,
                error=str(exc),
            )
            # 尝试同步失败状态
            await self._sync_terminal_state(
                PipelineRunStatus.FAILED, task_id, work_id, run_id,
            )
        finally:
            # 仅在终态时释放资源
            # 如果 run 暂停（WAITING_INPUT / WAITING_APPROVAL），不释放
            try:
                if self._engine is not None:
                    final_run = await self._engine.get_pipeline_run(run_id)
                    if final_run and final_run.status in _TERMINAL_STATUSES:
                        self._active_run_count = max(0, self._active_run_count - 1)
                        self._run_tasks.pop(run_id, None)
                        self._run_definitions.pop(run_id, None)
            except Exception:
                # 安全释放，不 propagate
                self._active_run_count = max(0, self._active_run_count - 1)
                self._run_tasks.pop(run_id, None)
                self._run_definitions.pop(run_id, None)

    # ============================================================
    # action="status"  (T-065-018)
    # ============================================================

    async def _handle_status(self, *, run_id: str) -> str:
        if not run_id:
            return "Error: run_id is required for status action."

        if self._engine is None:
            return "Error: pipeline engine not available."

        run = await self._engine.get_pipeline_run(run_id)
        if run is None:
            return f"Error: pipeline run not found: '{run_id}'."

        # 获取 definition 用于节点信息
        definition = self._run_definitions.get(run_id)

        lines: list[str] = ["Pipeline Run Status:"]
        lines.append(f"  run_id: {run_id}")
        lines.append(f"  pipeline: {run.pipeline_id}")
        lines.append(f"  status: {run.status.value.upper()}")

        # 当前节点信息
        if run.current_node_id and definition:
            node = definition.get_node(run.current_node_id)
            label_part = f" ({node.label})" if node and node.label else ""
            lines.append(f"  current_node: {run.current_node_id}{label_part}")

        # 已完成 / 待处理节点
        if definition:
            checkpoints = await self._engine._stores.work_store.list_pipeline_checkpoints(run_id)
            completed_ids = []
            seen = set()
            for cp in checkpoints:
                if cp.node_id not in seen and cp.status == PipelineRunStatus.RUNNING:
                    completed_ids.append(cp.node_id)
                    seen.add(cp.node_id)

            all_node_ids = [n.node_id for n in definition.nodes]
            pending_ids = [
                nid for nid in all_node_ids
                if nid not in seen and nid != run.current_node_id
            ]

            if completed_ids:
                done = ", ".join(n + " \u2713" for n in completed_ids)
                lines.append(f"  completed_nodes: {done}")
            if pending_ids:
                lines.append(f"  pending_nodes: {', '.join(pending_ids)}")

        # 时间信息
        if run.created_at:
            lines.append(f"  started_at: {run.created_at.isoformat()}")
            elapsed = datetime.now(tz=UTC) - run.created_at
            total_seconds = int(elapsed.total_seconds())
            if total_seconds >= 60:
                minutes = total_seconds // 60
                secs = total_seconds % 60
                lines.append(f"  elapsed: {minutes}m {secs}s")
            else:
                lines.append(f"  elapsed: {total_seconds}s")

        # 暂停状态额外信息
        if run.status in _RESUMABLE_STATUSES:
            if run.status == PipelineRunStatus.WAITING_APPROVAL:
                lines.append("  waiting_for: Human approval required")
                lines.append(
                    f'  resume_command: graph_pipeline(action="resume", '
                    f'run_id="{run_id}", approved=true)'
                )
            elif run.status == PipelineRunStatus.WAITING_INPUT:
                lines.append("  waiting_for: User input required")
                if run.input_request:
                    fields = run.input_request.get("fields", {})
                    if fields:
                        lines.append(f"  required_input_fields: {', '.join(fields.keys())}")
                lines.append(
                    f'  resume_command: graph_pipeline(action="resume", '
                    f'run_id="{run_id}", input_data={{...}})'
                )

        # 失败信息
        if run.status == PipelineRunStatus.FAILED:
            failure_category = run.metadata.get("failure_category", "unknown")
            failed_node = run.metadata.get("failed_node_id", "")
            error_msg = run.metadata.get("error_message", "")
            recovery = run.metadata.get("recovery_hint", "")
            lines.append(f"  failure_category: {failure_category}")
            if failed_node:
                lines.append(f"  failed_node: {failed_node}")
            if error_msg:
                lines.append(f"  error: {error_msg[:200]}")
            if recovery:
                lines.append(f"  recovery_hint: {recovery}")

        return "\n".join(lines)

    # ============================================================
    # action="resume"  (T-065-019)
    # ============================================================

    async def _handle_resume(
        self,
        *,
        run_id: str,
        input_data: dict[str, Any],
        approved: bool | None,
    ) -> str:
        if not run_id:
            return "Error: run_id is required for resume action."

        if self._engine is None:
            return "Error: pipeline engine not available."

        run = await self._engine.get_pipeline_run(run_id)
        if run is None:
            return f"Error: pipeline run not found: '{run_id}'."

        if run.status not in _RESUMABLE_STATUSES:
            return (
                f"Error: cannot resume pipeline run '{run_id}': "
                f"current status is {run.status.value.upper()} "
                f"(expected WAITING_INPUT or WAITING_APPROVAL)."
            )

        # 获取 definition（从内存缓存或 Registry 回退）
        definition = self._get_definition_for_run(run)
        if definition is None:
            return (
                f"Error: cannot resume pipeline run '{run_id}': "
                f"pipeline definition not found. The pipeline may have been removed."
            )

        if run.status == PipelineRunStatus.WAITING_APPROVAL:
            if approved is None:
                return (
                    "Error: 'approved' parameter is required when resuming a "
                    "WAITING_APPROVAL pipeline run. "
                    "Use approved=true to approve or approved=false to reject."
                )
            if not approved:
                # 拒绝审批 → CANCELLED
                await self._engine.cancel_run(
                    run_id, reason=f"Approval denied for node '{run.current_node_id}'"
                )
                # 同步 Task/Work 终态
                await self._sync_terminal_state(
                    PipelineRunStatus.CANCELLED, run.task_id, run.work_id, run_id,
                )
                # 计数释放由后台 task 的 finally 块统一处理，避免双重释放
                node_label = run.current_node_id or "unknown"
                return (
                    f"Pipeline run cancelled. "
                    f"The approval for node '{node_label}' was denied."
                )
            # approved=true → 继续执行
            bg_task = asyncio.create_task(
                self._resume_pipeline_run(
                    definition=definition,
                    run_id=run_id,
                    state_patch={"approved": True},
                    task_id=run.task_id,
                    work_id=run.work_id,
                )
            )
            self._run_tasks[run_id] = bg_task
            next_node = run.metadata.get("resume_next_node_id", "next")
            return (
                f"Pipeline run resumed successfully.\n"
                f"run_id: {run_id}\n"
                f"The pipeline continues from node '{next_node}'. "
                f"Use graph_pipeline(action=\"status\", run_id=\"{run_id}\") to check progress."
            )

        if run.status == PipelineRunStatus.WAITING_INPUT:
            if not input_data:
                return (
                    "Error: 'input_data' parameter is required when resuming a "
                    "WAITING_INPUT pipeline run."
                )
            bg_task = asyncio.create_task(
                self._resume_pipeline_run(
                    definition=definition,
                    run_id=run_id,
                    state_patch=input_data,
                    task_id=run.task_id,
                    work_id=run.work_id,
                )
            )
            self._run_tasks[run_id] = bg_task
            next_node = run.metadata.get("resume_next_node_id", "next")
            return (
                f"Pipeline run resumed successfully.\n"
                f"run_id: {run_id}\n"
                f"The pipeline continues from node '{next_node}'. "
                f"Use graph_pipeline(action=\"status\", run_id=\"{run_id}\") to check progress."
            )

        return f"Error: unexpected state for resume: {run.status.value}"

    async def _resume_pipeline_run(
        self,
        *,
        definition: SkillPipelineDefinition,
        run_id: str,
        state_patch: dict[str, Any],
        task_id: str,
        work_id: str,
    ) -> None:
        """后台恢复 Pipeline run。"""
        try:
            run = await self._engine.resume_run(
                definition=definition,
                run_id=run_id,
                state_patch=state_patch,
            )
            await self._sync_terminal_state(run.status, task_id, work_id, run_id)

            # T-065-037: 恢复后如果再次 WAITING，同步到 Task
            await self._sync_waiting_state(run, task_id)
        except Exception as exc:
            logger.error(
                "pipeline_resume_background_error",
                run_id=run_id,
                error=str(exc),
            )
            await self._sync_terminal_state(
                PipelineRunStatus.FAILED, task_id, work_id, run_id,
            )
        finally:
            try:
                if self._engine is not None:
                    final_run = await self._engine.get_pipeline_run(run_id)
                    if final_run and final_run.status in _TERMINAL_STATUSES:
                        self._active_run_count = max(0, self._active_run_count - 1)
                        self._run_tasks.pop(run_id, None)
                        self._run_definitions.pop(run_id, None)
            except Exception:
                self._active_run_count = max(0, self._active_run_count - 1)
                self._run_tasks.pop(run_id, None)
                self._run_definitions.pop(run_id, None)

    # ============================================================
    # action="cancel"  (T-065-020)
    # ============================================================

    async def _handle_cancel(self, *, run_id: str) -> str:
        if not run_id:
            return "Error: run_id is required for cancel action."

        if self._engine is None:
            return "Error: pipeline engine not available."

        run = await self._engine.get_pipeline_run(run_id)
        if run is None:
            return f"Error: pipeline run not found: '{run_id}'."

        if run.status in _TERMINAL_STATUSES:
            return (
                f"Error: cannot cancel pipeline run '{run_id}': "
                f"already in terminal status {run.status.value.upper()}."
            )

        await self._engine.cancel_run(run_id, reason="cancelled by user")

        # T-065-023: 同步 Task/Work 终态
        await self._sync_terminal_state(
            PipelineRunStatus.CANCELLED, run.task_id, run.work_id, run_id,
        )

        # 计数释放由后台 task 的 finally 块统一处理

        return (
            f"Pipeline run cancelled.\n"
            f"run_id: {run_id}\n"
            f"Note: Side effects from already completed nodes are not reverted."
        )

    # ============================================================
    # action="retry"  (T-065-021)
    # ============================================================

    async def _handle_retry(self, *, run_id: str) -> str:
        if not run_id:
            return "Error: run_id is required for retry action."

        if self._engine is None:
            return "Error: pipeline engine not available."

        run = await self._engine.get_pipeline_run(run_id)
        if run is None:
            return f"Error: pipeline run not found: '{run_id}'."

        if run.status != PipelineRunStatus.FAILED:
            return (
                f"Error: cannot retry pipeline run '{run_id}': "
                f"current status is {run.status.value.upper()} (expected FAILED)."
            )

        # 获取 definition
        definition = self._get_definition_for_run(run)
        if definition is None:
            return (
                f"Error: cannot retry pipeline run '{run_id}': "
                f"pipeline definition not found."
            )

        failed_node = run.current_node_id or "unknown"

        # 递增并发计数（retry 相当于重新激活 run）
        self._active_run_count += 1

        bg_task = asyncio.create_task(
            self._retry_pipeline_run(
                definition=definition,
                run_id=run_id,
                task_id=run.task_id,
                work_id=run.work_id,
            )
        )
        self._run_tasks[run_id] = bg_task

        return (
            f"Retrying current node '{failed_node}' for pipeline run {run_id}.\n"
            f"The pipeline continues in the background. "
            f"Use graph_pipeline(action=\"status\", run_id=\"{run_id}\") to check progress."
        )

    async def _retry_pipeline_run(
        self,
        *,
        definition: SkillPipelineDefinition,
        run_id: str,
        task_id: str,
        work_id: str,
    ) -> None:
        """后台重试 Pipeline run。"""
        try:
            run = await self._engine.retry_current_node(
                definition=definition,
                run_id=run_id,
            )
            await self._sync_terminal_state(run.status, task_id, work_id, run_id)
        except Exception as exc:
            logger.error(
                "pipeline_retry_background_error",
                run_id=run_id,
                error=str(exc),
            )
            await self._sync_terminal_state(
                PipelineRunStatus.FAILED, task_id, work_id, run_id,
            )
        finally:
            try:
                if self._engine is not None:
                    final_run = await self._engine.get_pipeline_run(run_id)
                    if final_run and final_run.status in _TERMINAL_STATUSES:
                        self._active_run_count = max(0, self._active_run_count - 1)
                        self._run_tasks.pop(run_id, None)
                        self._run_definitions.pop(run_id, None)
            except Exception:
                self._active_run_count = max(0, self._active_run_count - 1)
                self._run_tasks.pop(run_id, None)
                self._run_definitions.pop(run_id, None)

    # ============================================================
    # 内部辅助
    # ============================================================

    def _validate_params(
        self,
        manifest: PipelineManifest,
        params: dict[str, Any],
    ) -> str:
        """验证 params 是否满足 input_schema 的 required 字段。

        Returns:
            验证通过返回空字符串，失败返回错误消息。
        """
        if not manifest.input_schema:
            return ""

        missing: list[str] = []
        for field_name, field_def in manifest.input_schema.items():
            if field_def.required and field_name not in params:
                missing.append(field_name)

        if missing:
            return (
                f"Error: invalid params for pipeline '{manifest.pipeline_id}': "
                f"missing required field{'s' if len(missing) > 1 else ''} "
                f"'{', '.join(missing)}'."
            )
        return ""

    def _get_definition_for_run(
        self,
        run: Any,  # SkillPipelineRun
    ) -> SkillPipelineDefinition | None:
        """获取 run 对应的 definition：优先内存缓存，回退到 Registry。"""
        # 优先从内存缓存获取（快照）
        definition = self._run_definitions.get(run.run_id)
        if definition is not None:
            return definition

        # 回退到 Registry
        manifest = self._registry.get(run.pipeline_id)
        if manifest is not None:
            return manifest.definition

        return None

    async def _sync_terminal_state(
        self,
        status: PipelineRunStatus,
        task_id: str,
        work_id: str,
        run_id: str,
    ) -> None:
        """T-065-023: Pipeline 终态同步到 Child Task 和 Work。"""
        if self._store_group is None:
            return

        if status not in _TERMINAL_STATUSES:
            return

        try:
            now = datetime.now(tz=UTC)

            # 更新 Task 状态
            if task_id and task_id != "no-task":
                task_status = _PIPELINE_TO_TASK_STATUS.get(status)
                if task_status is not None:
                    await self._store_group.task_store.update_task_status(
                        task_id=task_id,
                        status=task_status.value,
                        updated_at=now.isoformat(),
                        latest_event_id=f"pipeline-{run_id}",
                    )

            # 更新 Work 状态
            if work_id and work_id != "no-work":
                work_status = _PIPELINE_TO_WORK_STATUS.get(status)
                if work_status is not None:
                    work = await self._store_group.work_store.get_work(work_id)
                    if work is not None:
                        updated = work.model_copy(
                            update={
                                "status": work_status,
                                "completed_at": now,
                                "updated_at": now,
                            }
                        )
                        await self._store_group.work_store.save_work(updated)

            await self._store_group.conn.commit()
        except Exception as exc:
            logger.error(
                "pipeline_terminal_state_sync_error",
                run_id=run_id,
                task_id=task_id,
                work_id=work_id,
                error=str(exc),
            )

    async def _sync_waiting_state(
        self,
        run: Any,  # SkillPipelineRun
        task_id: str,
    ) -> None:
        """T-065-037: Pipeline WAITING 状态同步到 Child Task。

        当 Pipeline 进入 WAITING_APPROVAL 或 WAITING_INPUT 时，
        将 Child Task 状态同步更新，使渠道审批机制能感知到等待状态。
        """
        if self._store_group is None:
            return

        if run.status not in _RESUMABLE_STATUSES:
            return

        if not task_id or task_id == "no-task":
            return

        target_status = _PIPELINE_TO_WAITING_TASK.get(run.status)
        if target_status is None:
            return

        try:
            now = datetime.now(tz=UTC)
            await self._store_group.task_store.update_task_status(
                task_id=task_id,
                status=target_status.value,
                updated_at=now.isoformat(),
                latest_event_id=f"pipeline-waiting-{run.run_id}",
            )
            await self._store_group.conn.commit()
            logger.info(
                "pipeline_waiting_state_synced",
                run_id=run.run_id,
                task_id=task_id,
                pipeline_status=run.status.value,
                task_status=target_status.value,
            )
        except Exception as exc:
            logger.error(
                "pipeline_waiting_state_sync_error",
                run_id=run.run_id,
                task_id=task_id,
                error=str(exc),
            )
