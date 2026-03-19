"""Feature 065 Phase 2: GraphPipelineTool 单元测试 + 集成测试。

覆盖：
- T-065-011: handler 缺失 → FAILED 终态
- T-065-012: 节点级超时
- T-065-013: FAILED metadata 统一
- T-065-014~021: 6 个 action 正常/异常路径
- T-065-022: @tool_contract 装饰
- T-065-023: Task/Work 终态同步
- T-065-024: definition 快照
- T-065-025: __init__.py 导出
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from octoagent.core.models import (
    EventType,
    PipelineNodeType,
    PipelineRunStatus,
    RequesterInfo,
    SkillPipelineDefinition,
    SkillPipelineNode,
    Task,
    Work,
)
from octoagent.core.store import create_store_group
from octoagent.core.store.task_store import TaskPointers
from octoagent.skills.pipeline import PipelineNodeOutcome, SkillPipelineEngine
from octoagent.skills.pipeline_handlers import BUILTIN_HANDLERS
from octoagent.skills.pipeline_models import (
    PipelineInputField,
    PipelineManifest,
    PipelineListItem,
    PipelineSource,
)
from octoagent.skills.pipeline_registry import PipelineRegistry
from octoagent.skills.pipeline_tool import GraphPipelineTool


# ============================================================
# 测试 Fixtures
# ============================================================


def _simple_definition() -> SkillPipelineDefinition:
    """3 节点线性 Pipeline: step-a → step-b → step-c。"""
    return SkillPipelineDefinition(
        pipeline_id="test-pipeline",
        label="Test Pipeline",
        version="1.0.0",
        entry_node_id="step-a",
        nodes=[
            SkillPipelineNode(
                node_id="step-a",
                label="步骤 A",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
                next_node_id="step-b",
            ),
            SkillPipelineNode(
                node_id="step-b",
                label="步骤 B",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
                next_node_id="step-c",
            ),
            SkillPipelineNode(
                node_id="step-c",
                label="步骤 C",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
            ),
        ],
    )


def _gate_definition() -> SkillPipelineDefinition:
    """含审批门禁的 Pipeline: step-a → gate → step-c。"""
    return SkillPipelineDefinition(
        pipeline_id="gate-pipeline",
        label="Gate Pipeline",
        version="1.0.0",
        entry_node_id="step-a",
        nodes=[
            SkillPipelineNode(
                node_id="step-a",
                label="步骤 A",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
                next_node_id="gate",
            ),
            SkillPipelineNode(
                node_id="gate",
                label="审批门禁",
                node_type=PipelineNodeType.GATE,
                handler_id="approval_gate",
                next_node_id="step-c",
            ),
            SkillPipelineNode(
                node_id="step-c",
                label="步骤 C",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
            ),
        ],
    )


def _input_gate_definition() -> SkillPipelineDefinition:
    """含用户输入门禁的 Pipeline: step-a → input-gate → step-c。"""
    return SkillPipelineDefinition(
        pipeline_id="input-pipeline",
        label="Input Pipeline",
        version="1.0.0",
        entry_node_id="step-a",
        nodes=[
            SkillPipelineNode(
                node_id="step-a",
                label="步骤 A",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
                next_node_id="input-gate",
            ),
            SkillPipelineNode(
                node_id="input-gate",
                label="用户输入",
                node_type=PipelineNodeType.GATE,
                handler_id="input_gate",
                next_node_id="step-c",
            ),
            SkillPipelineNode(
                node_id="step-c",
                label="步骤 C",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
            ),
        ],
    )


def _failing_definition() -> SkillPipelineDefinition:
    """节点 handler 会失败的 Pipeline。"""
    return SkillPipelineDefinition(
        pipeline_id="fail-pipeline",
        label="Fail Pipeline",
        version="1.0.0",
        entry_node_id="step-a",
        nodes=[
            SkillPipelineNode(
                node_id="step-a",
                label="步骤 A",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="transform.passthrough",
                next_node_id="step-fail",
            ),
            SkillPipelineNode(
                node_id="step-fail",
                label="必定失败的步骤",
                node_type=PipelineNodeType.TOOL,
                handler_id="always_fail",
            ),
        ],
    )


def _timeout_definition() -> SkillPipelineDefinition:
    """带超时的 Pipeline。"""
    return SkillPipelineDefinition(
        pipeline_id="timeout-pipeline",
        label="Timeout Pipeline",
        version="1.0.0",
        entry_node_id="slow-step",
        nodes=[
            SkillPipelineNode(
                node_id="slow-step",
                label="慢步骤",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="slow_handler",
                timeout_seconds=0.1,  # 100ms 超时
            ),
        ],
    )


def _handler_missing_definition() -> SkillPipelineDefinition:
    """handler 不存在的 Pipeline。"""
    return SkillPipelineDefinition(
        pipeline_id="missing-handler-pipeline",
        label="Missing Handler Pipeline",
        version="1.0.0",
        entry_node_id="step-a",
        nodes=[
            SkillPipelineNode(
                node_id="step-a",
                label="步骤 A",
                node_type=PipelineNodeType.TRANSFORM,
                handler_id="nonexistent.handler",
            ),
        ],
    )


def _build_manifest(
    definition: SkillPipelineDefinition,
    input_schema: dict[str, PipelineInputField] | None = None,
) -> PipelineManifest:
    return PipelineManifest(
        pipeline_id=definition.pipeline_id,
        description=f"Test: {definition.label}",
        version=definition.version,
        tags=["test"],
        trigger_hint="测试用途",
        input_schema=input_schema or {},
        source=PipelineSource.BUILTIN,
        source_path="/test/PIPELINE.md",
        definition=definition,
    )


class MockPipelineRegistry:
    """模拟 PipelineRegistry，用于单元测试。"""

    def __init__(self, manifests: list[PipelineManifest] | None = None) -> None:
        self._cache: dict[str, PipelineManifest] = {}
        for m in manifests or []:
            self._cache[m.pipeline_id] = m

    def get(self, pipeline_id: str) -> PipelineManifest | None:
        return self._cache.get(pipeline_id)

    def list_items(self) -> list[PipelineListItem]:
        items = [m.to_list_item() for m in self._cache.values()]
        items.sort(key=lambda x: x.pipeline_id)
        return items

    def refresh(self) -> list[PipelineManifest]:
        return list(self._cache.values())


async def _seed_task_and_work(store_group, *, task_id: str, work_id: str) -> None:
    now = datetime.now(UTC)
    await store_group.task_store.create_task(
        Task(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            title="pipeline test task",
            requester=RequesterInfo(channel="test", sender_id="test"),
            pointers=TaskPointers(),
            trace_id=f"trace-{task_id}",
        )
    )
    await store_group.work_store.save_work(
        Work(work_id=work_id, task_id=task_id, title="pipeline test work")
    )
    await store_group.conn.commit()


async def _create_tool(
    tmp_path: Path,
    manifests: list[PipelineManifest] | None = None,
    max_concurrent_runs: int = 10,
) -> tuple[GraphPipelineTool, Any]:
    """创建测试用 GraphPipelineTool + StoreGroup。"""
    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )

    registry = MockPipelineRegistry(manifests)
    events: list[tuple[EventType, dict[str, object]]] = []

    async def event_recorder(task_id, event_type, payload):
        events.append((event_type, payload))

    tool = GraphPipelineTool(
        registry=registry,  # type: ignore[arg-type]
        store_group=store_group,
        event_recorder=event_recorder,
        max_concurrent_runs=max_concurrent_runs,
    )

    return tool, store_group


# ============================================================
# T-065-011: handler 缺失 → FAILED 终态
# ============================================================


async def test_handler_missing_sets_failed_status(tmp_path: Path) -> None:
    """handler 不存在时 Pipeline 进入 FAILED 终态（不抛异常）。"""
    defn = _handler_missing_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    result = await tool.execute(action="start", pipeline_id="missing-handler-pipeline")
    assert "started successfully" in result

    # 等待后台任务完成
    await asyncio.sleep(0.2)

    # 检查 run 状态
    run_id = result.split("run_id: ")[1].split("\n")[0].strip()
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.FAILED
    assert run.metadata.get("failure_category") == "handler_missing"
    assert run.metadata.get("failed_node_id") == "step-a"
    assert "recovery_hint" in run.metadata
    assert "error_message" in run.metadata

    await store_group.conn.close()


# ============================================================
# T-065-012: 节点级超时
# ============================================================


async def test_node_timeout_sets_failed_status(tmp_path: Path) -> None:
    """节点执行超时时 Pipeline 进入 FAILED 终态。"""
    defn = _timeout_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    # 注册一个慢 handler（超过 timeout_seconds）
    async def slow_handler(*, run, node, state):
        await asyncio.sleep(5)  # 5 秒，超过 100ms 超时
        return PipelineNodeOutcome(summary="should not reach")

    tool.engine.register_handler("slow_handler", slow_handler)

    result = await tool.execute(action="start", pipeline_id="timeout-pipeline")
    assert "started successfully" in result

    # 等待后台任务完成
    await asyncio.sleep(0.5)

    run_id = result.split("run_id: ")[1].split("\n")[0].strip()
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.FAILED
    assert run.metadata.get("failure_category") == "timeout"
    assert run.metadata.get("failed_node_id") == "slow-step"

    await store_group.conn.close()


# ============================================================
# T-065-013: FAILED metadata 统一
# ============================================================


async def test_failed_outcome_has_unified_metadata(tmp_path: Path) -> None:
    """handler 返回 FAILED 时 metadata 包含统一的失败信息。"""
    defn = _failing_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    async def always_fail_handler(*, run, node, state):
        return PipelineNodeOutcome(
            status=PipelineRunStatus.FAILED,
            summary="exit code 1: permission denied",
        )

    tool.engine.register_handler("always_fail", always_fail_handler)

    result = await tool.execute(action="start", pipeline_id="fail-pipeline")
    assert "started successfully" in result

    await asyncio.sleep(0.2)

    run_id = result.split("run_id: ")[1].split("\n")[0].strip()
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.FAILED
    assert "failure_category" in run.metadata
    assert "failed_node_id" in run.metadata
    assert run.metadata["failed_node_id"] == "step-fail"
    assert "recovery_hint" in run.metadata
    assert "error_message" in run.metadata

    await store_group.conn.close()


# ============================================================
# T-065-014: list action
# ============================================================


async def test_list_returns_available_pipelines(tmp_path: Path) -> None:
    """list action 返回 LLM 可读的 Pipeline 列表。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    result = await tool.execute(action="list")
    assert "Available Pipelines (1):" in result
    assert "test-pipeline" in result
    assert "Tags: test" in result
    assert "Trigger:" in result
    assert 'graph_pipeline(action="start"' in result

    await store_group.conn.close()


async def test_list_empty_registry(tmp_path: Path) -> None:
    """Registry 为空时返回提示。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="list")
    assert "No pipelines available" in result

    await store_group.conn.close()


async def test_list_with_input_schema(tmp_path: Path) -> None:
    """list action 包含 input_schema 信息。"""
    defn = _simple_definition()
    input_schema = {
        "branch": PipelineInputField(type="string", description="分支名", required=True),
        "skip_tests": PipelineInputField(type="boolean", default=False),
    }
    manifest = _build_manifest(defn, input_schema=input_schema)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    result = await tool.execute(action="list")
    assert "branch (string, required)" in result
    assert "skip_tests" in result

    await store_group.conn.close()


# ============================================================
# T-065-015: start action
# ============================================================


async def test_start_creates_run_and_returns_run_id(tmp_path: Path) -> None:
    """start action 创建 Pipeline run 并返回 run_id。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    result = await tool.execute(
        action="start",
        pipeline_id="test-pipeline",
        params={"key": "value"},
    )
    assert "started successfully" in result
    assert "run_id:" in result
    assert "task_id:" in result
    assert "status" in result.lower() or "background" in result.lower()

    # 等待后台完成
    await asyncio.sleep(0.3)

    run_id = result.split("run_id: ")[1].split("\n")[0].strip()
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.SUCCEEDED  # 3 个 passthrough 应该成功

    await store_group.conn.close()


# ============================================================
# T-065-016: 输入参数验证
# ============================================================


async def test_start_pipeline_not_found(tmp_path: Path) -> None:
    """pipeline_id 不存在时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="start", pipeline_id="nonexistent")
    assert "Error: pipeline not found" in result
    assert "nonexistent" in result

    await store_group.conn.close()


async def test_start_missing_pipeline_id(tmp_path: Path) -> None:
    """pipeline_id 为空时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="start")
    assert "Error:" in result

    await store_group.conn.close()


async def test_start_missing_required_params(tmp_path: Path) -> None:
    """缺少 required 参数时返回错误。"""
    defn = _simple_definition()
    input_schema = {
        "branch": PipelineInputField(type="string", required=True),
    }
    manifest = _build_manifest(defn, input_schema=input_schema)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    result = await tool.execute(
        action="start",
        pipeline_id="test-pipeline",
        params={},
    )
    assert "Error: invalid params" in result
    assert "branch" in result

    await store_group.conn.close()


async def test_start_with_valid_required_params(tmp_path: Path) -> None:
    """提供 required 参数时正常启动。"""
    defn = _simple_definition()
    input_schema = {
        "branch": PipelineInputField(type="string", required=True),
    }
    manifest = _build_manifest(defn, input_schema=input_schema)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    result = await tool.execute(
        action="start",
        pipeline_id="test-pipeline",
        params={"branch": "main"},
    )
    assert "started successfully" in result

    await asyncio.sleep(0.3)
    await store_group.conn.close()


# ============================================================
# T-065-017: 并发上限
# ============================================================


async def test_concurrent_run_limit(tmp_path: Path) -> None:
    """并发 run 数量达到上限时拒绝新的 start。"""
    defn = _gate_definition()  # 会在 gate 节点暂停，不释放计数
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest], max_concurrent_runs=2)

    # 启动 2 个 run（会暂停在 gate 节点）
    result1 = await tool.execute(action="start", pipeline_id="gate-pipeline")
    assert "started successfully" in result1
    await asyncio.sleep(0.2)

    result2 = await tool.execute(action="start", pipeline_id="gate-pipeline")
    assert "started successfully" in result2
    await asyncio.sleep(0.2)

    # 第 3 个应被拒绝
    result3 = await tool.execute(action="start", pipeline_id="gate-pipeline")
    assert "Error: maximum concurrent pipeline runs reached" in result3

    await store_group.conn.close()


# ============================================================
# T-065-018: status action
# ============================================================


async def test_status_returns_run_info(tmp_path: Path) -> None:
    """status action 返回 run 的详细信息。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="status", run_id=run_id)
    assert "Pipeline Run Status:" in result
    assert run_id in result
    assert "gate-pipeline" in result
    assert "WAITING_APPROVAL" in result
    assert "waiting_for:" in result
    assert "resume_command:" in result

    await store_group.conn.close()


async def test_status_run_not_found(tmp_path: Path) -> None:
    """run_id 不存在时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="status", run_id="nonexistent-run-id")
    assert "Error: pipeline run not found" in result

    await store_group.conn.close()


async def test_status_missing_run_id(tmp_path: Path) -> None:
    """run_id 为空时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="status")
    assert "Error:" in result

    await store_group.conn.close()


async def test_status_failed_run_includes_failure_info(tmp_path: Path) -> None:
    """FAILED run 的 status 包含失败信息。"""
    defn = _failing_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    async def always_fail_handler(*, run, node, state):
        return PipelineNodeOutcome(
            status=PipelineRunStatus.FAILED,
            summary="something broke",
        )

    tool.engine.register_handler("always_fail", always_fail_handler)

    start_result = await tool.execute(action="start", pipeline_id="fail-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="status", run_id=run_id)
    assert "FAILED" in result
    assert "failure_category:" in result
    assert "failed_node:" in result

    await store_group.conn.close()


# ============================================================
# T-065-019: resume action (WAITING_APPROVAL)
# ============================================================


async def test_resume_approval_approved(tmp_path: Path) -> None:
    """resume with approved=true 恢复 Pipeline。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    # 确认暂停
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.WAITING_APPROVAL

    # 恢复
    result = await tool.execute(action="resume", run_id=run_id, approved=True)
    assert "resumed successfully" in result

    await asyncio.sleep(0.3)

    # 确认完成
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.SUCCEEDED

    await store_group.conn.close()


async def test_resume_approval_denied(tmp_path: Path) -> None:
    """resume with approved=false 取消 Pipeline。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="resume", run_id=run_id, approved=False)
    assert "cancelled" in result.lower()
    assert "denied" in result.lower()

    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.CANCELLED

    await store_group.conn.close()


async def test_resume_approval_missing_approved(tmp_path: Path) -> None:
    """WAITING_APPROVAL 时不提供 approved 参数返回错误。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="resume", run_id=run_id)
    assert "Error:" in result
    assert "approved" in result.lower()

    await store_group.conn.close()


# ============================================================
# T-065-019: resume action (WAITING_INPUT)
# ============================================================


async def test_resume_input_with_data(tmp_path: Path) -> None:
    """resume with input_data 恢复 WAITING_INPUT 的 Pipeline。"""
    defn = _input_gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="input-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.WAITING_INPUT

    result = await tool.execute(
        action="resume",
        run_id=run_id,
        input_data={"user_name": "Connor"},
    )
    assert "resumed successfully" in result

    await asyncio.sleep(0.3)

    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.SUCCEEDED

    await store_group.conn.close()


async def test_resume_input_missing_data(tmp_path: Path) -> None:
    """WAITING_INPUT 时不提供 input_data 返回错误。"""
    defn = _input_gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="input-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="resume", run_id=run_id)
    assert "Error:" in result
    assert "input_data" in result

    await store_group.conn.close()


async def test_resume_not_in_resumable_state(tmp_path: Path) -> None:
    """run 不在可恢复状态时返回错误。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="test-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    # 此时应该已经 SUCCEEDED
    result = await tool.execute(action="resume", run_id=run_id)
    assert "Error: cannot resume" in result

    await store_group.conn.close()


async def test_resume_run_not_found(tmp_path: Path) -> None:
    """run_id 不存在时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="resume", run_id="nonexistent")
    assert "Error: pipeline run not found" in result

    await store_group.conn.close()


# ============================================================
# T-065-020: cancel action
# ============================================================


async def test_cancel_running_pipeline(tmp_path: Path) -> None:
    """cancel 取消暂停中的 Pipeline。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="cancel", run_id=run_id)
    assert "cancelled" in result.lower()
    assert "Side effects" in result

    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.CANCELLED

    await store_group.conn.close()


async def test_cancel_terminal_pipeline(tmp_path: Path) -> None:
    """cancel 已终态的 Pipeline 返回错误。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="test-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="cancel", run_id=run_id)
    assert "Error: cannot cancel" in result
    assert "SUCCEEDED" in result

    await store_group.conn.close()


async def test_cancel_run_not_found(tmp_path: Path) -> None:
    """run_id 不存在时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="cancel", run_id="nonexistent")
    assert "Error: pipeline run not found" in result

    await store_group.conn.close()


# ============================================================
# T-065-021: retry action
# ============================================================


async def test_retry_failed_pipeline(tmp_path: Path) -> None:
    """retry 重试失败的 Pipeline。"""
    defn = _failing_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    call_count = 0

    async def sometimes_fail_handler(*, run, node, state):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return PipelineNodeOutcome(
                status=PipelineRunStatus.FAILED,
                summary="first attempt failed",
            )
        return PipelineNodeOutcome(summary="retry succeeded")

    tool.engine.register_handler("always_fail", sometimes_fail_handler)

    start_result = await tool.execute(action="start", pipeline_id="fail-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    # 确认失败
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.FAILED

    # 重试
    result = await tool.execute(action="retry", run_id=run_id)
    assert "Retrying" in result
    assert "step-fail" in result

    await asyncio.sleep(0.3)

    # 确认成功
    run = await tool.engine.get_pipeline_run(run_id)
    assert run is not None
    assert run.status == PipelineRunStatus.SUCCEEDED

    await store_group.conn.close()


async def test_retry_non_failed_pipeline(tmp_path: Path) -> None:
    """retry 非 FAILED 状态的 Pipeline 返回错误。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    result = await tool.execute(action="retry", run_id=run_id)
    assert "Error: cannot retry" in result
    assert "WAITING_APPROVAL" in result

    await store_group.conn.close()


async def test_retry_run_not_found(tmp_path: Path) -> None:
    """run_id 不存在时返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="retry", run_id="nonexistent")
    assert "Error: pipeline run not found" in result

    await store_group.conn.close()


# ============================================================
# T-065-022: @tool_contract 装饰
# ============================================================


def test_tool_contract_metadata() -> None:
    """execute 方法有正确的 @tool_contract 元数据。"""
    meta = getattr(GraphPipelineTool.execute, "_tool_meta", None)
    assert meta is not None
    assert meta["side_effect_level"] == "irreversible"
    assert meta["tool_group"] == "orchestration"
    assert meta["name"] == "graph_pipeline"
    assert "pipeline" in meta["tags"]


# ============================================================
# T-065-023: Task/Work 终态同步
# ============================================================


async def test_terminal_state_sync_on_success(tmp_path: Path) -> None:
    """Pipeline SUCCEEDED 时 Task + Work 同步到终态。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="test-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()
    task_id = start_result.split("task_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.5)

    # 验证 Task 状态
    task = await store_group.task_store.get_task(task_id)
    assert task is not None
    assert task.status.value in ("SUCCEEDED",)

    await store_group.conn.close()


# ============================================================
# T-065-024: definition 快照
# ============================================================


async def test_definition_snapshot_cached(tmp_path: Path) -> None:
    """start 后 definition 被缓存到 _run_definitions。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    start_result = await tool.execute(action="start", pipeline_id="test-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    # 验证 definition 在内存中
    assert run_id in tool._run_definitions
    assert tool._run_definitions[run_id].pipeline_id == "test-pipeline"

    await asyncio.sleep(0.3)
    await store_group.conn.close()


# ============================================================
# T-065-025: __init__.py 导出
# ============================================================


def test_init_exports() -> None:
    """验证 __init__.py 正确导出 Phase 2 模块。"""
    from octoagent.skills import (
        GraphPipelineTool,
        PipelineListItem,
        PipelineManifest,
        PipelineParseError,
        PipelineRegistry,
        PipelineSource,
        PIPELINE_BUILTIN_HANDLERS,
    )
    assert GraphPipelineTool is not None
    assert PipelineManifest is not None
    assert PipelineListItem is not None
    assert PipelineParseError is not None
    assert PipelineRegistry is not None
    assert PipelineSource is not None
    assert PIPELINE_BUILTIN_HANDLERS is not None
    assert "transform.passthrough" in PIPELINE_BUILTIN_HANDLERS


# ============================================================
# 未知 action
# ============================================================


async def test_unknown_action(tmp_path: Path) -> None:
    """未知 action 返回错误。"""
    tool, store_group = await _create_tool(tmp_path, [])

    result = await tool.execute(action="invalid_action")
    assert "Error: unknown action" in result
    assert "invalid_action" in result

    await store_group.conn.close()


# ============================================================
# 集成测试：端到端 Pipeline 执行
# ============================================================


async def test_e2e_pipeline_execution(tmp_path: Path) -> None:
    """端到端 Pipeline 执行：start → 逐节点执行 → SUCCEEDED。"""
    defn = _simple_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    # 1. list
    list_result = await tool.execute(action="list")
    assert "test-pipeline" in list_result

    # 2. start
    start_result = await tool.execute(
        action="start",
        pipeline_id="test-pipeline",
        params={"input_key": "input_value"},
    )
    assert "started successfully" in start_result
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    # 3. 等待完成
    await asyncio.sleep(0.5)

    # 4. status
    status_result = await tool.execute(action="status", run_id=run_id)
    assert "SUCCEEDED" in status_result

    # 5. 验证 checkpoints
    checkpoints = await store_group.work_store.list_pipeline_checkpoints(run_id)
    assert len(checkpoints) == 3  # 3 个节点
    assert all(cp.status == PipelineRunStatus.RUNNING for cp in checkpoints)

    await store_group.conn.close()


async def test_e2e_hitl_approval_flow(tmp_path: Path) -> None:
    """端到端 HITL 审批流：start → gate → WAITING → resume(approved) → SUCCEEDED。"""
    defn = _gate_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    # 1. start
    start_result = await tool.execute(action="start", pipeline_id="gate-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)

    # 2. status - 应该暂停
    status_result = await tool.execute(action="status", run_id=run_id)
    assert "WAITING_APPROVAL" in status_result

    # 3. resume - 批准
    resume_result = await tool.execute(action="resume", run_id=run_id, approved=True)
    assert "resumed successfully" in resume_result

    await asyncio.sleep(0.3)

    # 4. status - 应该成功
    status_result = await tool.execute(action="status", run_id=run_id)
    assert "SUCCEEDED" in status_result

    await store_group.conn.close()


async def test_e2e_failure_and_retry(tmp_path: Path) -> None:
    """端到端失败 + 重试流：start → FAILED → retry → SUCCEEDED。"""
    defn = _failing_definition()
    manifest = _build_manifest(defn)
    tool, store_group = await _create_tool(tmp_path, [manifest])

    call_count = 0

    async def retry_handler(*, run, node, state):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return PipelineNodeOutcome(
                status=PipelineRunStatus.FAILED,
                summary="first attempt failed",
            )
        return PipelineNodeOutcome(summary="retry succeeded")

    tool.engine.register_handler("always_fail", retry_handler)

    # 1. start → FAILED
    start_result = await tool.execute(action="start", pipeline_id="fail-pipeline")
    run_id = start_result.split("run_id: ")[1].split("\n")[0].strip()

    await asyncio.sleep(0.3)
    status_result = await tool.execute(action="status", run_id=run_id)
    assert "FAILED" in status_result

    # 2. retry → SUCCEEDED
    retry_result = await tool.execute(action="retry", run_id=run_id)
    assert "Retrying" in retry_result

    await asyncio.sleep(0.3)
    status_result = await tool.execute(action="status", run_id=run_id)
    assert "SUCCEEDED" in status_result

    await store_group.conn.close()
