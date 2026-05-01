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
"""

from __future__ import annotations

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
        approved: bool | None = None,
    ) -> GraphPipelineResult:
        """发现、启动、监控和管理确定性 Pipeline 流程。

        Action 语义：
        - list: 列出可用 Pipeline 定义
        - start: 启动 pipeline，返回 run_id + task_id（后台运行）
        - status: 查询 run 状态（status / progress / 当前 step）
        - resume: 恢复 WAITING_INPUT / WAITING_APPROVAL 状态的 run
        - cancel: 取消运行中的 run
        - retry: 重试 FAILED 的 run

        Args:
            action: 操作类型。
            pipeline_id: start 必填，pipeline 定义 ID。
            run_id: start 之外的 action 必填，目标 run ID。
            params: start 的输入参数。
            input_data: resume 时提供的输入数据。
            approved: resume 时的审批决定（True/False）。

        Returns:
            GraphPipelineResult：包含 run_id、task_id、status 等字段。
        """
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
                action="start",
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

        return await graph_tool.execute(
            action=action,
            pipeline_id=pipeline_id,
            run_id=run_id,
            params=params,
            input_data=input_data,
            approved=approved,
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
            "与 subagents（探索性任务）互补——pipeline 适合可重复的固定步骤。"
        ),
    ))
