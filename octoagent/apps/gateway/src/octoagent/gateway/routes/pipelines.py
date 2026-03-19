"""Pipeline 管理 REST API -- Feature 065 Phase 4

对齐 contracts/pipeline-rest-api.md。
提供 Pipeline 定义列表/详情/刷新 + Pipeline run 列表/详情 共 5 个端点。

端点:
  GET    /api/pipelines              -- 列出所有已注册 Pipeline
  GET    /api/pipelines/{pipeline_id} -- 获取指定 Pipeline 详情
  POST   /api/pipelines/refresh       -- 触发 PipelineRegistry 重新扫描
  GET    /api/pipeline-runs           -- 列出 Pipeline run（支持分页+筛选）
  GET    /api/pipeline-runs/{run_id}  -- 获取指定 run 详情
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


# ============================================================
# 依赖注入
# ============================================================


def _get_pipeline_registry(request: Request):
    """从 app.state 获取 PipelineRegistry 实例。

    Feature 065: PipelineRegistry 可能尚未挂载到 app.state，
    此时返回 None（端点会返回 503）。
    """
    return getattr(request.app.state, "pipeline_registry", None)


def _get_pipeline_tool(request: Request):
    """从 app.state 获取 GraphPipelineTool 实例。

    Feature 065: GraphPipelineTool 可能尚未挂载到 app.state，
    此时返回 None（端点会返回 503）。
    """
    return getattr(request.app.state, "graph_pipeline_tool", None)


def _get_store_group(request: Request):
    """从 app.state 获取 StoreGroup 实例。"""
    return getattr(request.app.state, "store_group", None)


# ============================================================
# 响应模型（对齐 contracts/pipeline-rest-api.md）
# ============================================================


class PipelineNodeResponse(BaseModel):
    """Pipeline 节点详情。"""

    node_id: str
    label: str = ""
    node_type: str  # skill / tool / transform / gate
    handler_id: str
    next_node_id: str | None = None
    retry_limit: int = 0
    timeout_seconds: float | None = None


class PipelineItemResponse(BaseModel):
    """GET /api/pipelines 列表元素。"""

    pipeline_id: str
    description: str = ""
    version: str = ""
    tags: list[str] = Field(default_factory=list)
    trigger_hint: str = ""
    source: str = ""  # "builtin" / "user" / "project"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class PipelineListResponse(BaseModel):
    """GET /api/pipelines 响应体。"""

    items: list[PipelineItemResponse]
    total: int


class PipelineDetailResponse(PipelineItemResponse):
    """GET /api/pipelines/{pipeline_id} 响应体。"""

    author: str = ""
    source_path: str = ""
    content: str = ""  # PIPELINE.md Markdown body
    nodes: list[PipelineNodeResponse] = Field(default_factory=list)
    entry_node: str = ""


class PipelineRunItemResponse(BaseModel):
    """GET /api/pipeline-runs 列表元素。"""

    run_id: str
    pipeline_id: str = ""
    task_id: str = ""
    work_id: str = ""
    status: str = ""  # created / running / waiting_input / waiting_approval / ...
    current_node_id: str = ""
    pause_reason: str = ""
    created_at: str = ""  # ISO 8601
    updated_at: str = ""
    completed_at: str | None = None


class PipelineRunListResponse(BaseModel):
    """GET /api/pipeline-runs 响应体。"""

    items: list[PipelineRunItemResponse]
    total: int
    page: int
    page_size: int


class PipelineCheckpointResponse(BaseModel):
    """Checkpoint 详情。"""

    checkpoint_id: str
    node_id: str = ""
    status: str = ""
    replay_summary: str = ""
    retry_count: int = 0
    created_at: str = ""


class PipelineRunDetailResponse(PipelineRunItemResponse):
    """GET /api/pipeline-runs/{run_id} 响应体。"""

    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    input_request: dict[str, Any] = Field(default_factory=dict)
    approval_request: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    retry_cursor: dict[str, int] = Field(default_factory=dict)
    checkpoints: list[PipelineCheckpointResponse] = Field(default_factory=list)


# ============================================================
# 路由
# ============================================================

# 分别创建两个 router：/api/pipelines 和 /api/pipeline-runs
pipelines_router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])
pipeline_runs_router = APIRouter(prefix="/api/pipeline-runs", tags=["pipeline-runs"])


def _require_registry(registry):
    """如果 PipelineRegistry 不可用，抛出 503。"""
    if registry is None:
        raise HTTPException(
            status_code=503,
            detail="Pipeline 服务不可用：PipelineRegistry 尚未初始化",
        )
    return registry


def _require_store(store_group):
    """如果 StoreGroup 不可用，抛出 503。"""
    if store_group is None:
        raise HTTPException(
            status_code=503,
            detail="Pipeline 服务不可用：StoreGroup 尚未初始化",
        )
    return store_group


# ============================================================
# GET /api/pipelines
# ============================================================


@pipelines_router.get("", response_model=PipelineListResponse)
async def list_pipelines(
    registry=Depends(_get_pipeline_registry),
) -> PipelineListResponse:
    """列出所有已注册 Pipeline。"""
    registry = _require_registry(registry)

    items: list[PipelineItemResponse] = []
    for entry in registry.list_items():
        items.append(
            PipelineItemResponse(
                pipeline_id=entry.pipeline_id,
                description=entry.description,
                version=entry.version,
                tags=entry.tags,
                trigger_hint=entry.trigger_hint,
                source=str(entry.source),
                input_schema={
                    k: v.model_dump() for k, v in entry.input_schema.items()
                },
                output_schema={},
            )
        )

    return PipelineListResponse(items=items, total=len(items))


# ============================================================
# POST /api/pipelines/refresh（必须在 /{pipeline_id} 之前注册）
# ============================================================


@pipelines_router.post("/refresh", response_model=PipelineListResponse)
async def refresh_pipelines(
    registry=Depends(_get_pipeline_registry),
) -> PipelineListResponse:
    """触发 PipelineRegistry 重新扫描文件系统并返回更新后的列表。"""
    registry = _require_registry(registry)

    registry.refresh()

    items: list[PipelineItemResponse] = []
    for entry in registry.list_items():
        items.append(
            PipelineItemResponse(
                pipeline_id=entry.pipeline_id,
                description=entry.description,
                version=entry.version,
                tags=entry.tags,
                trigger_hint=entry.trigger_hint,
                source=str(entry.source),
                input_schema={
                    k: v.model_dump() for k, v in entry.input_schema.items()
                },
                output_schema={},
            )
        )

    return PipelineListResponse(items=items, total=len(items))


# ============================================================
# GET /api/pipelines/{pipeline_id}
# ============================================================


@pipelines_router.get("/{pipeline_id}", response_model=PipelineDetailResponse)
async def get_pipeline(
    pipeline_id: str,
    registry=Depends(_get_pipeline_registry),
) -> PipelineDetailResponse:
    """获取指定 Pipeline 详情（含完整节点拓扑）。"""
    registry = _require_registry(registry)

    manifest = registry.get(pipeline_id)
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail=f"pipeline not found: '{pipeline_id}'",
        )

    # 构建节点列表
    nodes: list[PipelineNodeResponse] = []
    for node in manifest.definition.nodes:
        nodes.append(
            PipelineNodeResponse(
                node_id=node.node_id,
                label=node.label,
                node_type=str(node.node_type),
                handler_id=node.handler_id,
                next_node_id=node.next_node_id,
                retry_limit=node.retry_limit,
                timeout_seconds=node.timeout_seconds,
            )
        )

    return PipelineDetailResponse(
        pipeline_id=manifest.pipeline_id,
        description=manifest.description,
        version=manifest.version,
        tags=manifest.tags,
        trigger_hint=manifest.trigger_hint,
        source=str(manifest.source),
        input_schema={
            k: v.model_dump() for k, v in manifest.input_schema.items()
        },
        output_schema={
            k: v.model_dump() for k, v in manifest.output_schema.items()
        },
        author=manifest.author,
        source_path="",  # 不暴露服务器绝对路径
        content=manifest.content,
        nodes=nodes,
        entry_node=manifest.definition.entry_node_id,
    )


# ============================================================
# GET /api/pipeline-runs
# ============================================================


@pipeline_runs_router.get("", response_model=PipelineRunListResponse)
async def list_pipeline_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    pipeline_id: str = Query(default=""),
    status: str = Query(default=""),
    task_id: str = Query(default=""),
    store_group=Depends(_get_store_group),
) -> PipelineRunListResponse:
    """列出 Pipeline run（支持分页 + 按 pipeline_id / status / task_id 筛选）。"""
    store_group = _require_store(store_group)

    # 查询总数
    total = await store_group.work_store.count_pipeline_runs(
        pipeline_id=pipeline_id or None,
        status=status or None,
        task_id=task_id or None,
    )

    # 查询分页数据
    runs = await store_group.work_store.list_pipeline_runs(
        pipeline_id=pipeline_id or None,
        status=status or None,
        task_id=task_id or None,
        page=page,
        page_size=page_size,
    )

    items: list[PipelineRunItemResponse] = []
    for run in runs:
        items.append(_run_to_item_response(run))

    return PipelineRunListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ============================================================
# GET /api/pipeline-runs/{run_id}
# ============================================================


@pipeline_runs_router.get("/{run_id}", response_model=PipelineRunDetailResponse)
async def get_pipeline_run(
    run_id: str,
    store_group=Depends(_get_store_group),
) -> PipelineRunDetailResponse:
    """获取指定 Pipeline run 详情（含 checkpoint 历史）。"""
    store_group = _require_store(store_group)

    run = await store_group.work_store.get_pipeline_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"pipeline run not found: '{run_id}'",
        )

    # 获取 checkpoint 历史
    checkpoints = await store_group.work_store.list_pipeline_checkpoints(run_id)
    checkpoint_responses: list[PipelineCheckpointResponse] = []
    for cp in checkpoints:
        checkpoint_responses.append(
            PipelineCheckpointResponse(
                checkpoint_id=cp.checkpoint_id,
                node_id=cp.node_id,
                status=str(cp.status),
                replay_summary=cp.replay_summary,
                retry_count=cp.retry_count,
                created_at=cp.created_at.isoformat() if cp.created_at else "",
            )
        )

    return PipelineRunDetailResponse(
        run_id=run.run_id,
        pipeline_id=run.pipeline_id,
        task_id=run.task_id,
        work_id=run.work_id,
        status=str(run.status),
        current_node_id=run.current_node_id or "",
        pause_reason=run.pause_reason or "",
        created_at=run.created_at.isoformat() if run.created_at else "",
        updated_at=run.updated_at.isoformat() if run.updated_at else "",
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        state_snapshot=run.state_snapshot or {},
        input_request=run.input_request or {},
        approval_request=run.approval_request or {},
        metadata=run.metadata or {},
        retry_cursor=run.retry_cursor or {},
        checkpoints=checkpoint_responses,
    )


# ============================================================
# POST /api/pipeline-runs/{run_id}/approve  (T-065-038: 渠道审批桥接)
# ============================================================


class PipelineApprovalRequest(BaseModel):
    """渠道审批请求体。"""

    approved: bool = Field(description="true=批准，false=拒绝")
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="WAITING_INPUT 时提供的用户输入数据",
    )


class PipelineApprovalResponse(BaseModel):
    """渠道审批响应体。"""

    run_id: str
    status: str
    message: str


@pipeline_runs_router.post(
    "/{run_id}/approve",
    response_model=PipelineApprovalResponse,
)
async def approve_pipeline_run(
    run_id: str,
    body: PipelineApprovalRequest,
    pipeline_tool=Depends(_get_pipeline_tool),
    store_group=Depends(_get_store_group),
) -> PipelineApprovalResponse:
    """T-065-038: 渠道审批桥接 — Web UI / Telegram 审批按钮调用此端点。

    效果等同于调用 graph_pipeline(action="resume", run_id=..., approved=...)。
    """
    if pipeline_tool is None:
        raise HTTPException(
            status_code=503,
            detail="Pipeline 服务不可用：GraphPipelineTool 尚未初始化",
        )

    # 通过 GraphPipelineTool.execute 调用 resume action
    result = await pipeline_tool.execute(
        action="resume",
        run_id=run_id,
        approved=body.approved,
        input_data=body.input_data,
    )

    # 判断结果是否是错误
    is_error = result.startswith("Error:")
    if is_error:
        raise HTTPException(
            status_code=400,
            detail=result,
        )

    # 确定最终状态
    if not body.approved:
        final_status = "cancelled"
    else:
        final_status = "resumed"

    return PipelineApprovalResponse(
        run_id=run_id,
        status=final_status,
        message=result,
    )


# ============================================================
# 辅助函数
# ============================================================


def _run_to_item_response(run) -> PipelineRunItemResponse:
    """将 SkillPipelineRun 转为列表元素响应模型。"""
    return PipelineRunItemResponse(
        run_id=run.run_id,
        pipeline_id=run.pipeline_id,
        task_id=run.task_id,
        work_id=run.work_id,
        status=str(run.status),
        current_node_id=run.current_node_id or "",
        pause_reason=run.pause_reason or "",
        created_at=run.created_at.isoformat() if run.created_at else "",
        updated_at=run.updated_at.isoformat() if run.updated_at else "",
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
    )


# 聚合 router 供 main.py 使用
# 由于 pipelines 和 pipeline-runs 有不同前缀，提供 include 辅助
def include_pipeline_routers(app, **kwargs):
    """将 pipelines + pipeline-runs 两个 router 同时挂载到 FastAPI app。"""
    app.include_router(pipelines_router, **kwargs)
    app.include_router(pipeline_runs_router, **kwargs)
