"""graph_pipeline_tool.py：graph_pipeline 工具 broker handler。

设计目的：把 GraphPipelineTool 实例（main.py lifespan 内创建并挂到
``app.state.graph_pipeline_tool``）暴露为 ToolBroker 可发现的工具，让 LLM 能
直接 tool_call ``graph_pipeline``，不必绕 ``tool_search`` → orchestrator
promote → 下一轮 tool_call 的两跳链路。

CORE 待遇由 ``CoreToolSet.default()`` 的 tool_names 列表统一决定（本工具加进
该列表后，每轮 LLM call 的 tools schema 都包含完整 graph_pipeline 签名）。

late-binding：handler 通过 ``deps._graph_pipeline_tool`` 访问真实实例。
``GraphPipelineTool`` 在 lifespan 内 ``capability_pack_service.startup()``
（注册阶段）之后才创建并注入回 ToolDeps，因此 handler 调用时检查 None 兜底。

F088 followup security 加固（Codex adversarial review round 2 + 3）：
- ``approved`` 不暴露给 LLM：审批门必须由人类操作员通过 Web UI / Telegram /
  REST API 提交，LLM 不能自批 WAITING_APPROVAL。
- 跨 task run 操作必须先做归属校验：status / resume / cancel / retry 必须
  有 ``execution_context.task_id`` 且 run.task_id 对应的 child_task.parent_task_id
  等于当前 task（直接父子）。
- start 也必须有 task_id：subagent_lifecycle 走独立 SkillRunner、不绑定
  gateway execution_context，没有可信 task_id 时禁止启动后台不可逆 pipeline，
  避免 parent_task_id=None 的孤儿 child_task / pipeline run。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import structlog
from octoagent.core.models.enums import SideEffectLevel
from octoagent.core.models.tool_results import GraphPipelineResult
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.tooling import reflect_tool_schema, tool_contract
from pydantic import BaseModel

from ._deps import ToolDeps

log = structlog.get_logger(__name__)

# graph_pipeline 仅 agent_runtime 入口（与 delegate_task 同策略：Web UI / Telegram
# 不直接 tool_call orchestration 工具，应走专用 API / 命令）
_ENTRYPOINTS = frozenset({"agent_runtime"})

# 需要 task 上下文的 action 集合：start 需要 parent_task_id 防孤儿 child_task；
# status/resume/cancel/retry 需要 current_task_id 做 ownership 校验。
# list 是只读且不创建 / 操作 run，无需 task 上下文。
_TASK_REQUIRED_ACTIONS = frozenset({"start", "status", "resume", "cancel", "retry"})

# 需要 run_id + run 归属校验的 action（操作已有 run）
_RUN_REQUIRED_ACTIONS = frozenset({"status", "resume", "cancel", "retry"})

# action → GraphPipelineResult.action 字面量映射（拒绝路径占位）
_ACTION_FOR_RESULT = {
    "list": "start",
    "start": "start",
    "status": "start",
    "resume": "resume",
    "cancel": "cancel",
    "retry": "retry",
}


@dataclass
class _OwnershipCheck:
    ok: bool
    reason: str = ""


async def _verify_run_ownership(
    *,
    deps: ToolDeps,
    run_id: str,
    current_task_id: str,
) -> _OwnershipCheck:
    """校验 run 归属：run.task_id == current_task_id 或 child_task.parent_task_id == current_task_id。

    禁止跨 task 操作他人 pipeline run（防 LLM 拿到 leaked run_id 后越权）。
    单层 parent 校验作为最小可用防护；深嵌套场景未来可扩展为祖先链遍历。
    """
    tool = deps._graph_pipeline_tool
    engine = getattr(tool, "_engine", None) if tool is not None else None
    if engine is None:
        return _OwnershipCheck(ok=False, reason="pipeline engine 未就绪")

    try:
        run = await engine.get_pipeline_run(run_id)
    except Exception as exc:
        return _OwnershipCheck(ok=False, reason=f"load_run_failed: {exc}")

    if run is None:
        return _OwnershipCheck(ok=False, reason=f"pipeline run not found: '{run_id}'")

    child_task_id = getattr(run, "task_id", "") or ""
    if not child_task_id or child_task_id == "no-task":
        return _OwnershipCheck(
            ok=False,
            reason=f"run '{run_id}' 未绑定 task，无法校验归属",
        )

    # 直接归属：LLM 自己以 child task_id 起的 run（罕见路径，保留兼容）
    if child_task_id == current_task_id:
        return _OwnershipCheck(ok=True)

    task_store = getattr(getattr(deps, "stores", None), "task_store", None)
    if task_store is None:
        return _OwnershipCheck(ok=False, reason="task_store 未就绪")

    try:
        child_task = await task_store.get_task(child_task_id)
    except Exception as exc:
        return _OwnershipCheck(ok=False, reason=f"load_child_task_failed: {exc}")

    if child_task is None:
        return _OwnershipCheck(ok=False, reason=f"child task not found: '{child_task_id}'")

    parent_task_id = getattr(child_task, "parent_task_id", None) or ""
    if parent_task_id == current_task_id:
        return _OwnershipCheck(ok=True)

    return _OwnershipCheck(
        ok=False,
        reason=(
            f"run '{run_id}' 不属于当前 task '{current_task_id}' "
            f"(child.parent='{parent_task_id}')"
        ),
    )


# ---------------------------------------------------------------------------
# 注册入口
# ---------------------------------------------------------------------------


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 graph_pipeline 工具到 broker + ToolRegistry。

    handler 转发到 ``deps._graph_pipeline_tool.execute(...)``。注入时机由
    ``main.py`` lifespan 控制（GraphPipelineTool 创建后立即 ``_tool_deps
    ._graph_pipeline_tool = ...``）。
    """

    @tool_contract(
        name="graph_pipeline",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        tool_group="orchestration",
        produces_write=True,
        tags=["pipeline", "graph", "orchestration"],
        manifest_ref="builtin://graph_pipeline",
        metadata={
            "entrypoints": ["agent_runtime"],
        },
    )
    async def graph_pipeline_handler(
        action: Literal["list", "start", "status", "resume", "cancel", "retry"],
        pipeline_id: str = "",
        run_id: str = "",
        params: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> GraphPipelineResult:
        """发现、启动、监控和管理确定性 Pipeline 流程。

        Action 语义：
        - list: 列出可用 Pipeline 定义
        - start: 启动 pipeline，返回 run_id + task_id（后台运行）
        - status: 查询 run 状态（status / progress / 当前 step）
        - resume: 恢复 WAITING_INPUT 状态的 run（提供 input_data）
        - cancel: 取消运行中的 run
        - retry: 重试 FAILED 的 run

        Args:
            action: 操作类型。
            pipeline_id: start 必填，pipeline 定义 ID。
            run_id: start 之外的 action 必填，目标 run ID。
            params: start 的输入参数。
            input_data: resume 时提供的输入数据（仅 WAITING_INPUT 用）。

        Returns:
            GraphPipelineResult：包含 run_id、task_id、status 等字段。

        Note:
            WAITING_APPROVAL 节点的人工审批不通过本工具完成；审批必须由
            Web UI / Telegram / operator REST API 等真实人类入口提交，
            LLM 不允许自批 pipeline 的人工审批门（F088 followup security）。
        """
        result_action = _ACTION_FOR_RESULT.get(action, "start")
        graph_tool = deps._graph_pipeline_tool
        if graph_tool is None:
            return GraphPipelineResult(
                status="rejected",
                target=run_id or pipeline_id or "graph_pipeline",
                preview="graph_pipeline tool not initialized (pipeline_registry 未就绪)",
                reason="graph_pipeline_tool_unavailable",
                detail=(
                    "GraphPipelineTool 实例尚未注入到 ToolDeps；通常因 "
                    "pipeline_registry 在 lifespan startup 时创建失败。"
                    "请检查 gateway 启动日志中的 pipeline_registry_init_skipped / "
                    "graph_pipeline_tool_init_skipped 警告。"
                ),
                action=result_action,
            )

        # 从执行上下文取 task_id（用于 _handle_start 创建 child task FK 关联）
        current_task_id = ""
        try:
            from ..execution_context import get_current_execution_context
            ctx = get_current_execution_context()
            if ctx is not None:
                current_task_id = ctx.task_id or ""
        except Exception:
            pass

        # F088 followup security：start / 操作类 action 必须有 task_id。
        # 防 subagent_lifecycle 等不绑 gateway execution_context 的运行时
        # 创建 parent_task_id=None 的孤儿 pipeline run（同 runtime 后续无法
        # status / resume / cancel）。
        if action in _TASK_REQUIRED_ACTIONS and not current_task_id:
            msg = (
                f"action='{action}' 需要 task 上下文："
                "当前无 gateway execution_context.task_id（"
                "subagent runtime 等场景未绑定）。"
                "禁止创建/操作不可追踪的 pipeline run。"
            )
            log.warning(
                "graph_pipeline_handler_no_task_context",
                action=action,
                run_id=run_id,
            )
            return GraphPipelineResult(
                status="rejected",
                target=run_id or pipeline_id or "graph_pipeline",
                preview=msg[:200],
                reason=msg,
                detail=msg,
                action=result_action,
                run_id=run_id or None,
            )

        # F088 followup security：跨 task run 操作必须先校验归属
        if action in _RUN_REQUIRED_ACTIONS:
            if not run_id:
                msg = f"action='{action}' 需要 run_id"
                return GraphPipelineResult(
                    status="rejected",
                    target="graph_pipeline",
                    preview=msg,
                    reason=msg,
                    detail=msg,
                    action=result_action,
                )
            ownership = await _verify_run_ownership(
                deps=deps,
                run_id=run_id,
                current_task_id=current_task_id,
            )
            if not ownership.ok:
                log.warning(
                    "graph_pipeline_handler_ownership_denied",
                    action=action,
                    run_id=run_id,
                    current_task_id=current_task_id,
                    reason=ownership.reason,
                )
                return GraphPipelineResult(
                    status="rejected",
                    target=run_id,
                    preview=ownership.reason[:200],
                    reason=ownership.reason,
                    detail=ownership.reason,
                    action=result_action,
                    run_id=run_id,
                )

        # F088 followup security：approved 不暴露给 LLM。
        # WAITING_APPROVAL run 的 resume 会因 approved=None 在底层 execute 返回
        # "approved required" 错误 — 这是预期行为，强制人工审批走非 LLM 入口。
        return await graph_tool.execute(
            action=action,
            pipeline_id=pipeline_id,
            run_id=run_id,
            params=params,
            input_data=input_data,
            approved=None,
            task_id=current_task_id,
        )

    # 向 ToolBroker 注册（让 LLM 能 tool_call）
    await broker.try_register(
        reflect_tool_schema(graph_pipeline_handler),
        graph_pipeline_handler,
    )

    # 向全局 ToolRegistry 注册 ToolEntry（Feature 084 entrypoints 过滤）
    # schema=BaseModel 占位，实际入参 schema 由 @tool_contract + reflect_tool_schema
    # 在 ToolBroker 注册时反射；ToolRegistry 这里仅做 entrypoints 可见性过滤
    _registry_register(ToolEntry(
        name="graph_pipeline",
        entrypoints=_ENTRYPOINTS,
        toolset="orchestration",
        handler=graph_pipeline_handler,
        schema=BaseModel,
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        description=(
            "发现、启动、监控和管理确定性 Pipeline 流程。"
            "支持 list / start / status / resume / cancel / retry 6 个 action。"
            "WAITING_APPROVAL 节点必须由人类操作员通过 Web UI / Telegram / "
            "REST API 审批，LLM 不能自批。"
            "与 subagents（探索性任务）互补——pipeline 适合可重复的固定步骤。"
        ),
    ))
