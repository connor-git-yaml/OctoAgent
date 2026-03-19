"""Pipeline REST API + HITL 集成测试 -- Feature 065 Phase 4

测试内容:
1. GET /api/pipelines 返回已注册 Pipeline 列表
2. GET /api/pipelines/{pipeline_id} 返回 Pipeline 详情
3. GET /api/pipelines/{pipeline_id} 不存在时返回 404
4. POST /api/pipelines/refresh 重新扫描后返回列表
5. GET /api/pipeline-runs 返回 run 列表（分页+筛选）
6. GET /api/pipeline-runs/{run_id} 返回 run 详情
7. GET /api/pipeline-runs/{run_id} 不存在时返回 404
8. POST /api/pipeline-runs/{run_id}/approve 渠道审批桥接
9. HITL: Pipeline WAITING → Task 状态同步
10. HITL: WAITING 状态不消耗 LLM token
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from octoagent.core.models import PipelineRunStatus


# ============================================================
# 辅助：创建最小 FastAPI app + mock 注入
# ============================================================


def _create_mock_registry():
    """创建 mock PipelineRegistry。"""
    mock = MagicMock()

    # 模拟一个 Pipeline 条目
    mock_item = MagicMock()
    mock_item.pipeline_id = "echo-test"
    mock_item.description = "Echo 测试 Pipeline"
    mock_item.version = "1.0.0"
    mock_item.tags = ["test"]
    mock_item.trigger_hint = "测试时使用"
    mock_item.source = MagicMock(value="builtin")
    mock_item.input_schema = {}
    mock_item.output_schema = {}

    mock.list_items.return_value = [mock_item]

    # get(pipeline_id) mock
    mock_manifest = MagicMock()
    mock_manifest.pipeline_id = "echo-test"
    mock_manifest.description = "Echo 测试 Pipeline"
    mock_manifest.version = "1.0.0"
    mock_manifest.tags = ["test"]
    mock_manifest.trigger_hint = "测试时使用"
    mock_manifest.source = MagicMock(value="builtin")
    mock_manifest.input_schema = {}
    mock_manifest.output_schema = {}
    mock_manifest.author = "OctoAgent"
    mock_manifest.source_path = "/pipelines/echo-test/PIPELINE.md"
    mock_manifest.content = "# Echo Test\n\n测试用 Pipeline。"

    # definition mock
    mock_def = MagicMock()
    mock_def.entry_node_id = "step-1"

    mock_node = MagicMock()
    mock_node.node_id = "step-1"
    mock_node.label = "步骤一"
    mock_node.node_type = MagicMock(value="tool")
    mock_node.handler_id = "transform.passthrough"
    mock_node.next_node_id = None
    mock_node.retry_limit = 0
    mock_node.timeout_seconds = None
    mock_def.nodes = [mock_node]
    mock_manifest.definition = mock_def

    def _get(pid):
        if pid == "echo-test":
            return mock_manifest
        return None

    mock.get.side_effect = _get
    mock.refresh.return_value = [mock_manifest]

    return mock


def _create_mock_store_group():
    """创建 mock StoreGroup。"""
    store_group = MagicMock()

    # work_store mock
    work_store = AsyncMock()

    # 模拟一个 pipeline run
    mock_run = MagicMock()
    mock_run.run_id = "run-001"
    mock_run.pipeline_id = "echo-test"
    mock_run.task_id = "task-001"
    mock_run.work_id = "work-001"
    mock_run.status = PipelineRunStatus.SUCCEEDED
    mock_run.current_node_id = "step-1"
    mock_run.pause_reason = ""
    mock_run.created_at = datetime(2026, 3, 19, 10, 0, 0, tzinfo=UTC)
    mock_run.updated_at = datetime(2026, 3, 19, 10, 1, 0, tzinfo=UTC)
    mock_run.completed_at = datetime(2026, 3, 19, 10, 1, 0, tzinfo=UTC)
    mock_run.state_snapshot = {"key": "value"}
    mock_run.input_request = {}
    mock_run.approval_request = {}
    mock_run.metadata = {}
    mock_run.retry_cursor = {}

    work_store.list_pipeline_runs.return_value = [mock_run]
    work_store.count_pipeline_runs.return_value = 1
    work_store.get_pipeline_run.return_value = mock_run

    # checkpoint mock
    mock_cp = MagicMock()
    mock_cp.checkpoint_id = "cp-001"
    mock_cp.node_id = "step-1"
    mock_cp.status = PipelineRunStatus.RUNNING
    mock_cp.replay_summary = "passthrough 完成"
    mock_cp.retry_count = 0
    mock_cp.created_at = datetime(2026, 3, 19, 10, 0, 30, tzinfo=UTC)
    work_store.list_pipeline_checkpoints.return_value = [mock_cp]

    store_group.work_store = work_store
    store_group.conn = AsyncMock()

    return store_group


def _create_test_app():
    """创建测试用 FastAPI app。"""
    from octoagent.gateway.routes.pipelines import (
        include_pipeline_routers,
    )

    app = FastAPI()

    registry = _create_mock_registry()
    store_group = _create_mock_store_group()

    app.state.pipeline_registry = registry
    app.state.store_group = store_group
    app.state.graph_pipeline_tool = None  # 大部分测试不需要真实的 tool

    include_pipeline_routers(app)

    return app, registry, store_group


# ============================================================
# 测试
# ============================================================


class TestListPipelines:
    """GET /api/pipelines"""

    @pytest.mark.asyncio
    async def test_list_returns_items(self):
        app, registry, _ = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipelines")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["pipeline_id"] == "echo-test"
        assert data["items"][0]["description"] == "Echo 测试 Pipeline"

    @pytest.mark.asyncio
    async def test_list_empty_when_no_pipelines(self):
        app, registry, _ = _create_test_app()
        registry.list_items.return_value = []
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipelines")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_list_503_when_registry_missing(self):
        from octoagent.gateway.routes.pipelines import include_pipeline_routers

        app = FastAPI()
        # 不设置 pipeline_registry
        include_pipeline_routers(app)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipelines")

        assert resp.status_code == 503


class TestGetPipeline:
    """GET /api/pipelines/{pipeline_id}"""

    @pytest.mark.asyncio
    async def test_get_existing_pipeline(self):
        app, _, _ = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipelines/echo-test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_id"] == "echo-test"
        assert data["author"] == "OctoAgent"
        assert data["entry_node"] == "step-1"
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["node_id"] == "step-1"
        assert data["nodes"][0]["handler_id"] == "transform.passthrough"

    @pytest.mark.asyncio
    async def test_get_nonexistent_pipeline_returns_404(self):
        app, _, _ = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipelines/nonexistent")

        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data["detail"]


class TestRefreshPipelines:
    """POST /api/pipelines/refresh"""

    @pytest.mark.asyncio
    async def test_refresh_triggers_rescan(self):
        app, registry, _ = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/pipelines/refresh")

        assert resp.status_code == 200
        registry.refresh.assert_called_once()
        data = resp.json()
        assert "items" in data
        assert "total" in data


class TestListPipelineRuns:
    """GET /api/pipeline-runs"""

    @pytest.mark.asyncio
    async def test_list_runs_returns_items(self):
        app, _, store = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipeline-runs")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert len(data["items"]) == 1
        assert data["items"][0]["run_id"] == "run-001"
        assert data["items"][0]["pipeline_id"] == "echo-test"

    @pytest.mark.asyncio
    async def test_list_runs_with_filters(self):
        app, _, store = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/pipeline-runs",
                params={"pipeline_id": "echo-test", "status": "succeeded"},
            )

        assert resp.status_code == 200
        # 验证 store 被调用时传入了筛选参数
        store.work_store.list_pipeline_runs.assert_called_once()
        call_kwargs = store.work_store.list_pipeline_runs.call_args
        assert call_kwargs.kwargs["pipeline_id"] == "echo-test"
        assert call_kwargs.kwargs["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_list_runs_pagination(self):
        app, _, store = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/pipeline-runs",
                params={"page": 2, "page_size": 5},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 5
        # 验证 store 被调用时传入了分页参数
        call_kwargs = store.work_store.list_pipeline_runs.call_args
        assert call_kwargs.kwargs["page"] == 2
        assert call_kwargs.kwargs["page_size"] == 5

    @pytest.mark.asyncio
    async def test_list_runs_503_when_store_missing(self):
        from octoagent.gateway.routes.pipelines import include_pipeline_routers

        app = FastAPI()
        # 不设置 store_group
        include_pipeline_routers(app)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipeline-runs")

        assert resp.status_code == 503


class TestGetPipelineRun:
    """GET /api/pipeline-runs/{run_id}"""

    @pytest.mark.asyncio
    async def test_get_existing_run(self):
        app, _, _ = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipeline-runs/run-001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-001"
        assert data["pipeline_id"] == "echo-test"
        assert data["status"] == "succeeded"
        assert len(data["checkpoints"]) == 1
        assert data["checkpoints"][0]["checkpoint_id"] == "cp-001"

    @pytest.mark.asyncio
    async def test_get_nonexistent_run_returns_404(self):
        app, _, store = _create_test_app()
        store.work_store.get_pipeline_run.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/pipeline-runs/nonexistent")

        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data["detail"]


class TestPipelineApproval:
    """POST /api/pipeline-runs/{run_id}/approve (T-065-038)"""

    @pytest.mark.asyncio
    async def test_approve_returns_503_when_tool_missing(self):
        app, _, _ = _create_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/pipeline-runs/run-001/approve",
                json={"approved": True},
            )

        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_approve_with_tool_success(self):
        app, _, _ = _create_test_app()

        # 设置 mock pipeline tool
        mock_tool = AsyncMock()
        mock_tool.execute.return_value = "Pipeline run resumed successfully."
        app.state.graph_pipeline_tool = mock_tool

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/pipeline-runs/run-001/approve",
                json={"approved": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-001"
        assert data["status"] == "resumed"

        # 验证 tool.execute 被正确调用
        mock_tool.execute.assert_called_once_with(
            action="resume",
            run_id="run-001",
            approved=True,
            input_data={},
        )

    @pytest.mark.asyncio
    async def test_reject_approval(self):
        """T-065-039: 拒绝审批 -> CANCELLED"""
        app, _, _ = _create_test_app()

        mock_tool = AsyncMock()
        mock_tool.execute.return_value = "Pipeline run cancelled."
        app.state.graph_pipeline_tool = mock_tool

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/pipeline-runs/run-001/approve",
                json={"approved": False},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_approve_returns_error_when_tool_fails(self):
        app, _, _ = _create_test_app()

        mock_tool = AsyncMock()
        mock_tool.execute.return_value = "Error: pipeline run not found: 'run-001'."
        app.state.graph_pipeline_tool = mock_tool

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/pipeline-runs/run-001/approve",
                json={"approved": True},
            )

        assert resp.status_code == 400


class TestHITLWaitingStateSync:
    """T-065-037: Pipeline WAITING → Task 状态同步"""

    @pytest.mark.asyncio
    async def test_waiting_approval_syncs_to_task(self):
        """Pipeline 进入 WAITING_APPROVAL 时，Task 状态应同步更新。"""
        from octoagent.skills.pipeline_tool import GraphPipelineTool

        registry = _create_mock_registry()
        store_group = _create_mock_store_group()

        tool = GraphPipelineTool(
            registry=registry,
            store_group=store_group,
        )

        # 模拟一个 WAITING_APPROVAL 状态的 run
        mock_run = MagicMock()
        mock_run.run_id = "run-w1"
        mock_run.pipeline_id = "echo-test"
        mock_run.status = PipelineRunStatus.WAITING_APPROVAL
        mock_run.task_id = "task-w1"
        mock_run.work_id = "work-w1"
        mock_run.input_request = {}
        mock_run.approval_request = {"question": "Deploy?"}

        await tool._sync_waiting_state(mock_run, "task-w1")

        # 验证 Task 状态被更新
        store_group.task_store.update_task_status.assert_called_once()
        call_kwargs = store_group.task_store.update_task_status.call_args
        assert call_kwargs.kwargs["task_id"] == "task-w1"
        assert call_kwargs.kwargs["status"] == "WAITING_APPROVAL"

    @pytest.mark.asyncio
    async def test_waiting_input_syncs_to_task(self):
        """Pipeline 进入 WAITING_INPUT 时，Task 状态应同步更新。"""
        from octoagent.skills.pipeline_tool import GraphPipelineTool

        registry = _create_mock_registry()
        store_group = _create_mock_store_group()

        tool = GraphPipelineTool(
            registry=registry,
            store_group=store_group,
        )

        mock_run = MagicMock()
        mock_run.run_id = "run-w2"
        mock_run.status = PipelineRunStatus.WAITING_INPUT
        mock_run.task_id = "task-w2"

        await tool._sync_waiting_state(mock_run, "task-w2")

        store_group.task_store.update_task_status.assert_called_once()
        call_kwargs = store_group.task_store.update_task_status.call_args
        assert call_kwargs.kwargs["status"] == "WAITING_INPUT"

    @pytest.mark.asyncio
    async def test_non_waiting_state_does_not_sync(self):
        """非 WAITING 状态不应触发 Task 状态同步。"""
        from octoagent.skills.pipeline_tool import GraphPipelineTool

        registry = _create_mock_registry()
        store_group = _create_mock_store_group()

        tool = GraphPipelineTool(
            registry=registry,
            store_group=store_group,
        )

        mock_run = MagicMock()
        mock_run.run_id = "run-r1"
        mock_run.status = PipelineRunStatus.RUNNING
        mock_run.task_id = "task-r1"

        await tool._sync_waiting_state(mock_run, "task-r1")

        store_group.task_store.update_task_status.assert_not_called()


class TestWaitingNoLLMTokenConsumption:
    """T-065-040: WAITING 状态下不消耗 LLM token"""

    @pytest.mark.asyncio
    async def test_waiting_does_not_invoke_llm(self):
        """验证 Engine._drive() 在 WAITING 状态返回，不进入 LLM 调用循环。

        Engine 的 _drive() 方法在遇到 WAITING_APPROVAL / WAITING_INPUT 后
        直接 return，不会继续循环。因此 WAITING 状态下不会触发任何 LLM 调用。
        此测试通过验证 Engine.start_run 返回的 run status 来确认。
        """
        from octoagent.skills.pipeline import (
            PipelineNodeOutcome,
            SkillPipelineEngine,
        )
        from octoagent.core.models.pipeline import (
            PipelineNodeType,
            SkillPipelineDefinition,
            SkillPipelineNode,
        )

        # 构建带 gate 节点的 definition
        nodes = [
            SkillPipelineNode(
                node_id="gate-1",
                label="审批门禁",
                node_type=PipelineNodeType.GATE,
                handler_id="approval_gate",
                next_node_id="step-2",
            ),
            SkillPipelineNode(
                node_id="step-2",
                label="后续步骤",
                node_type=PipelineNodeType.TOOL,
                handler_id="transform.passthrough",
            ),
        ]
        definition = SkillPipelineDefinition(
            pipeline_id="gate-test",
            label="Gate 测试",
            version="1.0.0",
            entry_node_id="gate-1",
            nodes=nodes,
        )

        # 创建 mock store group
        mock_store = MagicMock()
        mock_work_store = AsyncMock()
        mock_store.work_store = mock_work_store
        mock_store.conn = AsyncMock()
        mock_work_store.save_pipeline_run.return_value = None
        mock_work_store.save_pipeline_checkpoint.return_value = None

        engine = SkillPipelineEngine(store_group=mock_store)

        # 注册 approval_gate handler：返回 WAITING_APPROVAL
        async def _gate_handler(*, run, node, state):
            return PipelineNodeOutcome(
                status=PipelineRunStatus.WAITING_APPROVAL,
                summary="需要人工审批",
                approval_request={"question": "是否继续？"},
            )

        # 注册 passthrough handler
        async def _passthrough(*, run, node, state):
            return PipelineNodeOutcome(
                status=PipelineRunStatus.RUNNING,
                summary="透传完成",
            )

        engine.register_handler("approval_gate", _gate_handler)
        engine.register_handler("transform.passthrough", _passthrough)

        # 启动 Pipeline
        run = await engine.start_run(
            definition=definition,
            task_id="task-gate",
            work_id="work-gate",
            initial_state={},
        )

        # 验证：Pipeline 在 gate 节点暂停，状态为 WAITING_APPROVAL
        assert run.status == PipelineRunStatus.WAITING_APPROVAL

        # 验证：passthrough handler 没有被调用（因为 Pipeline 在 gate 暂停了）
        # 这意味着没有进一步的执行 = 没有 LLM token 消耗
        # save_pipeline_run 调用次数：
        # 1. initial created 状态
        # 2. gate-1 node running
        # 3. WAITING_APPROVAL 暂停
        assert mock_work_store.save_pipeline_run.call_count == 3

        # 验证：只有一个 checkpoint（gate-1）
        assert mock_work_store.save_pipeline_checkpoint.call_count == 1
