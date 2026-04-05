"""Feature 064 P1: SubagentExecutor 单元测试 + 集成测试。

验证 Subagent 独立执行循环的核心行为：
- spawn → execute → SUCCEEDED 全流程
- spawn → execute → 异常 → FAILED
- spawn → cancel → CANCELLED
- SSEHub 双路广播
- Orchestrator SubagentResultQueue
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from octoagent.core.models import (
    A2AConversation,
    A2AConversationStatus,
    ActorType,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
    AgentSession,
    AgentSessionKind,
    AgentSessionStatus,
    Event,
    EventType,
    TaskStatus,
)
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import (
    SkillExecutionContext,
    SkillOutputEnvelope,
    SkillRunResult,
    SkillRunStatus,
    UsageLimits,
)

from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.subagent_lifecycle import (
    SubagentExecutor,
    SubagentOutcome,
    SubagentSpawnContext,
    SubagentSpawnParams,
    kill_subagent,
    list_active_subagents,
    spawn_subagent,
)


# ============================================================
# Fixtures
# ============================================================


def _make_dummy_manifest() -> SkillManifest:
    """构造最小 SkillManifest。"""

    class DummyInput:
        pass

    class DummyOutput:
        pass

    from pydantic import BaseModel

    class MinInput(BaseModel):
        pass

    class MinOutput(BaseModel):
        pass

    return SkillManifest(
        skill_id="test-skill",
        input_model=MinInput,
        output_model=MinOutput,
        model_alias="main",
        tools_allowed=[],
        heartbeat_interval_steps=2,
        max_concurrent_subagents=5,
    )


@pytest_asyncio.fixture
async def store_group(tmp_path: Path) -> StoreGroup:
    """创建测试用 StoreGroup。"""
    db_path = str(tmp_path / "test.db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    sg = await create_store_group(db_path, str(artifacts_dir))
    return sg


@pytest_asyncio.fixture
async def parent_runtime(store_group: StoreGroup) -> AgentRuntime:
    """创建父 Worker Runtime。"""
    now = datetime.now(tz=UTC)
    runtime = AgentRuntime(
        agent_runtime_id="worker-parent-001",
        project_id="project-001",

        agent_profile_id="profile-001",
        worker_profile_id="worker-profile-001",
        role=AgentRuntimeRole.WORKER,
        name="Test Parent Worker",
        status=AgentRuntimeStatus.ACTIVE,
        metadata={},
        created_at=now,
        updated_at=now,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)
    await store_group.conn.commit()
    return runtime


@pytest_asyncio.fixture
async def parent_task(store_group: StoreGroup) -> Task:
    """创建父 Task。"""
    now = datetime.now(tz=UTC)
    task = Task(
        task_id="task-parent-001",
        created_at=now,
        updated_at=now,
        status=TaskStatus.RUNNING,
        title="Parent task",
        requester=RequesterInfo(channel="test", sender_id="user-001"),
        trace_id="trace-parent-001",
    )
    await store_group.task_store.create_task(task)
    await store_group.conn.commit()
    return task


# ============================================================
# Task 模型 parent_task_id 测试
# ============================================================


class TestTaskParentTaskId:
    """验证 Task 模型新增 parent_task_id 字段。"""

    async def test_task_default_parent_task_id_is_none(self) -> None:
        """默认 parent_task_id 为 None。"""
        task = Task(
            task_id="task-001",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            status=TaskStatus.CREATED,
            title="Test",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
        )
        assert task.parent_task_id is None

    async def test_task_with_parent_task_id(self) -> None:
        """可以设置 parent_task_id。"""
        task = Task(
            task_id="task-002",
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
            status=TaskStatus.CREATED,
            title="Child",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
            parent_task_id="task-parent-001",
        )
        assert task.parent_task_id == "task-parent-001"

    @pytest.mark.asyncio
    async def test_task_persist_parent_task_id(self, store_group: StoreGroup) -> None:
        """parent_task_id 正确持久化和读取。"""
        now = datetime.now(tz=UTC)
        task = Task(
            task_id="task-persist-001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Persisted child",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
            parent_task_id="task-parent-persist",
        )
        await store_group.task_store.create_task(task)
        await store_group.conn.commit()

        loaded = await store_group.task_store.get_task("task-persist-001")
        assert loaded is not None
        assert loaded.parent_task_id == "task-parent-persist"

    @pytest.mark.asyncio
    async def test_task_persist_null_parent_task_id(self, store_group: StoreGroup) -> None:
        """parent_task_id=None 正确持久化。"""
        now = datetime.now(tz=UTC)
        task = Task(
            task_id="task-persist-002",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Top level task",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
        )
        await store_group.task_store.create_task(task)
        await store_group.conn.commit()

        loaded = await store_group.task_store.get_task("task-persist-002")
        assert loaded is not None
        assert loaded.parent_task_id is None

    @pytest.mark.asyncio
    async def test_list_child_tasks(self, store_group: StoreGroup) -> None:
        """list_child_tasks 按 parent_task_id 查询并按 created_at 正序。"""
        now = datetime.now(tz=UTC)
        parent_id = "task-parent-list"

        # 创建父 Task
        parent = Task(
            task_id=parent_id,
            created_at=now,
            updated_at=now,
            status=TaskStatus.RUNNING,
            title="Parent",
            requester=RequesterInfo(channel="test", sender_id="user-001"),
        )
        await store_group.task_store.create_task(parent)

        # 创建 2 个子 Task
        for i in range(2):
            child = Task(
                task_id=f"task-child-{i}",
                created_at=now,
                updated_at=now,
                status=TaskStatus.CREATED,
                title=f"Child {i}",
                requester=RequesterInfo(channel="subagent", sender_id=f"subagent-{i}"),
                parent_task_id=parent_id,
            )
            await store_group.task_store.create_task(child)

        await store_group.conn.commit()

        children = await store_group.task_store.list_child_tasks(parent_id)
        assert len(children) == 2
        assert all(c.parent_task_id == parent_id for c in children)

    @pytest.mark.asyncio
    async def test_list_child_tasks_empty(self, store_group: StoreGroup) -> None:
        """无子 Task 时返回空列表。"""
        children = await store_group.task_store.list_child_tasks("nonexistent-parent")
        assert children == []


# ============================================================
# SkillExecutionContext / SkillManifest 扩展测试
# ============================================================


class TestModelExtensions:
    """验证模型扩展字段。"""

    def test_execution_context_parent_task_id_default(self) -> None:
        ctx = SkillExecutionContext(
            task_id="t1",
            trace_id="tr1",
            usage_limits=UsageLimits(),
        )
        assert ctx.parent_task_id is None

    def test_execution_context_parent_task_id_set(self) -> None:
        ctx = SkillExecutionContext(
            task_id="t1",
            trace_id="tr1",
            parent_task_id="parent-t",
            usage_limits=UsageLimits(),
        )
        assert ctx.parent_task_id == "parent-t"

    def test_manifest_heartbeat_interval_default(self) -> None:
        m = _make_dummy_manifest()
        assert m.heartbeat_interval_steps == 2  # 构造时设置为 2

    def test_manifest_max_concurrent_subagents_default(self) -> None:
        m = _make_dummy_manifest()
        assert m.max_concurrent_subagents == 5


# ============================================================
# SSEHub 双路广播测试
# ============================================================


class TestSSEHubDualBroadcast:
    """验证 SSEHub broadcast() 双路广播。"""

    @pytest.mark.asyncio
    async def test_broadcast_without_parent_task_id(self) -> None:
        """parent_task_id=None 时行为不变。"""
        hub = SSEHub()
        queue = await hub.subscribe("task-1")
        event = Event(
            event_id="evt-1",
            task_id="task-1",
            task_seq=1,
            ts=datetime.now(tz=UTC),
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload={},
            trace_id="trace-test",
        )
        await hub.broadcast("task-1", event)
        assert not queue.empty()
        got = queue.get_nowait()
        assert got.event_id == "evt-1"

    @pytest.mark.asyncio
    async def test_broadcast_with_parent_task_id(self) -> None:
        """parent_task_id 非 None 时事件同时广播到父子 Task 订阅者。"""
        hub = SSEHub()
        child_queue = await hub.subscribe("child-task")
        parent_queue = await hub.subscribe("parent-task")

        event = Event(
            event_id="evt-child-1",
            task_id="child-task",
            task_seq=1,
            ts=datetime.now(tz=UTC),
            type=EventType.STATE_TRANSITION,
            actor=ActorType.WORKER,
            payload={"to_status": "SUCCEEDED"},
            trace_id="trace-test",
        )

        await hub.broadcast("child-task", event, parent_task_id="parent-task")

        # 子 Task 订阅者收到
        assert not child_queue.empty()
        assert child_queue.get_nowait().event_id == "evt-child-1"

        # 父 Task 订阅者也收到（事件冒泡）
        assert not parent_queue.empty()
        assert parent_queue.get_nowait().event_id == "evt-child-1"

    @pytest.mark.asyncio
    async def test_broadcast_parent_same_as_child(self) -> None:
        """parent_task_id 与 task_id 相同时不重复广播。"""
        hub = SSEHub()
        queue = await hub.subscribe("task-same")

        event = Event(
            event_id="evt-same",
            task_id="task-same",
            task_seq=1,
            ts=datetime.now(tz=UTC),
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload={},
            trace_id="trace-test",
        )

        await hub.broadcast("task-same", event, parent_task_id="task-same")

        # 只收到一次
        assert queue.qsize() == 1


# ============================================================
# spawn_subagent 向后兼容测试
# ============================================================


class TestSpawnSubagentBackcompat:
    """验证 spawn_subagent 在不提供新参数时向后兼容。"""

    @pytest.mark.asyncio
    async def test_spawn_returns_tuple2_without_executor_deps(
        self, store_group: StoreGroup, parent_runtime: AgentRuntime
    ) -> None:
        """不提供 model_client 等新参数时返回 (runtime, session)。"""
        result = await spawn_subagent(
            store_group=store_group,
            parent_worker_runtime_id=parent_runtime.agent_runtime_id,
            params=SubagentSpawnParams(name="compat-subagent"),
            ctx=SubagentSpawnContext(),
        )
        assert len(result) == 2
        runtime, session = result
        assert runtime.agent_runtime_id.startswith("subagent-")
        assert session.kind == AgentSessionKind.SUBAGENT_INTERNAL
        assert runtime.metadata.get("is_subagent") is True

    @pytest.mark.asyncio
    async def test_kill_subagent_backcompat(
        self, store_group: StoreGroup, parent_runtime: AgentRuntime
    ) -> None:
        """kill_subagent 保持向后兼容。"""
        result = await spawn_subagent(
            store_group=store_group,
            parent_worker_runtime_id=parent_runtime.agent_runtime_id,
            params=SubagentSpawnParams(),
            ctx=SubagentSpawnContext(),
        )
        runtime, session = result
        ok = await kill_subagent(
            store_group=store_group,
            subagent_runtime_id=runtime.agent_runtime_id,
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_kill_nonexistent_returns_false(self, store_group: StoreGroup) -> None:
        """kill_subagent 对不存在的 runtime 返回 False。"""
        ok = await kill_subagent(
            store_group=store_group,
            subagent_runtime_id="nonexistent-runtime",
        )
        assert ok is False


# ============================================================
# spawn_subagent with SubagentExecutor 测试
# ============================================================


class TestSpawnSubagentWithExecutor:
    """验证 spawn_subagent 提供全量参数时创建 SubagentExecutor。"""

    @pytest.mark.asyncio
    async def test_spawn_returns_tuple3_with_executor(
        self,
        store_group: StoreGroup,
        parent_runtime: AgentRuntime,
        parent_task: Task,
    ) -> None:
        """提供完整依赖时返回 (runtime, session, executor)。"""
        manifest = _make_dummy_manifest()

        # 构造 mock 依赖
        mock_model_client = MagicMock()
        # make generate() return a completed output
        mock_model_client.generate = AsyncMock(
            return_value=SkillOutputEnvelope(
                content="Done!",
                complete=True,
            )
        )

        mock_tool_broker = MagicMock()
        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=2)
        mock_event_store.append_event = AsyncMock()

        result = await spawn_subagent(
            store_group=store_group,
            parent_worker_runtime_id=parent_runtime.agent_runtime_id,
            params=SubagentSpawnParams(task_description="Test task for subagent"),
            ctx=SubagentSpawnContext(
                parent_task_id=parent_task.task_id,
                model_client=mock_model_client,
                tool_broker=mock_tool_broker,
                event_store=mock_event_store,
                parent_manifest=manifest,
            ),
        )

        assert len(result) == 3
        runtime, session, executor = result
        assert isinstance(executor, SubagentExecutor)
        assert executor.is_running
        assert executor.child_task_id.startswith("task-")

        # 验证 Child Task 创建
        child_task = await store_group.task_store.get_task(executor.child_task_id)
        assert child_task is not None
        assert child_task.parent_task_id == parent_task.task_id
        assert child_task.status in (TaskStatus.CREATED, TaskStatus.RUNNING)

        # 验证 A2AConversation 创建
        conversations = await store_group.a2a_store.list_conversations(
            target_agent_runtime_id=runtime.agent_runtime_id,
        )
        assert len(conversations) >= 1
        conv = conversations[0]
        assert conv.status == A2AConversationStatus.ACTIVE

        # 等待执行完成
        await executor.wait()
        assert not executor.is_running

    @pytest.mark.asyncio
    async def test_spawn_child_task_inherits_parent_project(
        self,
        store_group: StoreGroup,
        parent_runtime: AgentRuntime,
        parent_task: Task,
    ) -> None:
        """Child Task 继承父 Runtime 的 project_id。"""
        manifest = _make_dummy_manifest()
        mock_model_client = MagicMock()
        mock_model_client.generate = AsyncMock(
            return_value=SkillOutputEnvelope(content="OK", complete=True)
        )

        result = await spawn_subagent(
            store_group=store_group,
            parent_worker_runtime_id=parent_runtime.agent_runtime_id,
            params=SubagentSpawnParams(task_description="Inherit test"),
            ctx=SubagentSpawnContext(
                parent_task_id=parent_task.task_id,
                model_client=mock_model_client,
                tool_broker=MagicMock(),
                event_store=AsyncMock(
                    get_next_task_seq=AsyncMock(return_value=2),
                    append_event=AsyncMock(),
                ),
                parent_manifest=manifest,
            ),
        )

        _, _, executor = result
        child = await store_group.task_store.get_task(executor.child_task_id)
        assert child is not None
        assert child.scope_id == parent_runtime.project_id

        await executor.wait()


# ============================================================
# SubagentExecutor 生命周期测试
# ============================================================


class TestSubagentExecutorLifecycle:
    """测试 SubagentExecutor 的生命周期管理。"""

    @pytest.mark.asyncio
    async def test_executor_succeeded_flow(self, store_group: StoreGroup) -> None:
        """正常完成 → Child Task 流转到 SUCCEEDED。"""
        now = datetime.now(tz=UTC)

        # 创建 Parent Runtime
        parent_rt = AgentRuntime(
            agent_runtime_id="worker-life-001",
            project_id="proj-1",
    
            agent_profile_id="ap-1",
            worker_profile_id="wp-1",
            role=AgentRuntimeRole.WORKER,
            name="Parent",
            status=AgentRuntimeStatus.ACTIVE,
            metadata={},
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_runtime(parent_rt)

        # 创建 Subagent Runtime + Session
        sub_rt = AgentRuntime(
            agent_runtime_id="subagent-life-001",
            project_id="proj-1",
    
            agent_profile_id="ap-1",
            worker_profile_id="wp-1",
            role=AgentRuntimeRole.WORKER,
            name="Subagent",
            status=AgentRuntimeStatus.ACTIVE,
            metadata={"is_subagent": True, "parent_worker_runtime_id": "worker-life-001"},
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_runtime(sub_rt)

        sub_session = AgentSession(
            agent_session_id="session-life-001",
            agent_runtime_id="subagent-life-001",
            kind=AgentSessionKind.SUBAGENT_INTERNAL,
            status=AgentSessionStatus.ACTIVE,
            project_id="proj-1",
    
            parent_worker_runtime_id="worker-life-001",
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_session(sub_session)

        # 创建 Child Task
        child_task = Task(
            task_id="task-child-life-001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Test subagent task",
            requester=RequesterInfo(channel="subagent", sender_id="subagent-life-001"),
            parent_task_id="task-parent-life-001",
        )
        await store_group.task_store.create_task(child_task)
        await store_group.conn.commit()

        # Mock SkillRunner
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(
            return_value=SkillRunResult(
                status=SkillRunStatus.SUCCEEDED,
                output=SkillOutputEnvelope(content="Task completed!", complete=True),
                steps=3,
                duration_ms=1500,
            )
        )

        manifest = _make_dummy_manifest()
        ctx = SkillExecutionContext(
            task_id="task-child-life-001",
            trace_id="trace-life-001",
            agent_runtime_id="subagent-life-001",
            agent_session_id="session-life-001",
            parent_task_id="task-parent-life-001",
            metadata={"task_description": "Do something"},
            usage_limits=UsageLimits(max_steps=30),
        )

        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=2)
        mock_event_store.append_event = AsyncMock()

        # 创建 A2A conversation
        conv = A2AConversation(
            a2a_conversation_id="a2a-conv-life-001",
            task_id="task-child-life-001",
            source_agent="agent://workers/worker-life-001",
            target_agent="agent://workers/worker-life-001/subagents/subagent-life-001",
            status=A2AConversationStatus.ACTIVE,
        )
        await store_group.a2a_store.save_conversation(conv)
        await store_group.conn.commit()

        # 结果回调 mock
        callback_called = []

        async def result_callback(**kwargs):
            callback_called.append(kwargs)

        executor = SubagentExecutor(
            child_task=child_task,
            skill_runner=mock_runner,
            manifest=manifest,
            execution_context=ctx,
            a2a_conversation_id="a2a-conv-life-001",
            parent_agent_uri="agent://workers/worker-life-001",
            subagent_agent_uri="agent://workers/worker-life-001/subagents/subagent-life-001",
            event_store=mock_event_store,
            store_group=store_group,
            heartbeat_interval=2,
            result_callback=result_callback,
        )

        await executor.start()
        assert executor.is_running

        await executor.wait()
        assert not executor.is_running

        # 验证 Child Task 状态
        updated_task = await store_group.task_store.get_task("task-child-life-001")
        assert updated_task is not None
        assert updated_task.status == TaskStatus.SUCCEEDED

        # 验证回调被调用
        assert len(callback_called) == 1
        assert callback_called[0]["status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_executor_cancelled_flow(self, store_group: StoreGroup) -> None:
        """cancel() → Child Task 流转到 CANCELLED。"""
        now = datetime.now(tz=UTC)

        parent_rt = AgentRuntime(
            agent_runtime_id="worker-cancel-001",
            project_id="proj-1",
    
            agent_profile_id="ap-1",
            worker_profile_id="wp-1",
            role=AgentRuntimeRole.WORKER,
            name="Parent",
            status=AgentRuntimeStatus.ACTIVE,
            metadata={},
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_runtime(parent_rt)

        sub_rt = AgentRuntime(
            agent_runtime_id="subagent-cancel-001",
            project_id="proj-1",
    
            agent_profile_id="ap-1",
            worker_profile_id="wp-1",
            role=AgentRuntimeRole.WORKER,
            name="Subagent",
            status=AgentRuntimeStatus.ACTIVE,
            metadata={"is_subagent": True, "parent_worker_runtime_id": "worker-cancel-001"},
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_runtime(sub_rt)

        sub_session = AgentSession(
            agent_session_id="session-cancel-001",
            agent_runtime_id="subagent-cancel-001",
            kind=AgentSessionKind.SUBAGENT_INTERNAL,
            status=AgentSessionStatus.ACTIVE,
            project_id="proj-1",
    
            parent_worker_runtime_id="worker-cancel-001",
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_session(sub_session)

        child_task = Task(
            task_id="task-child-cancel-001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Cancellable task",
            requester=RequesterInfo(channel="subagent", sender_id="subagent-cancel-001"),
            parent_task_id="task-parent-cancel-001",
        )
        await store_group.task_store.create_task(child_task)

        conv = A2AConversation(
            a2a_conversation_id="a2a-conv-cancel-001",
            task_id="task-child-cancel-001",
            source_agent="agent://workers/worker-cancel-001",
            target_agent="agent://workers/worker-cancel-001/subagents/subagent-cancel-001",
            status=A2AConversationStatus.ACTIVE,
        )
        await store_group.a2a_store.save_conversation(conv)
        await store_group.conn.commit()

        # 模拟一个会 hang 住的 SkillRunner
        hang_event = asyncio.Event()
        mock_runner = MagicMock()

        async def slow_run(**kwargs):
            await hang_event.wait()  # 会一直等待，直到被 cancel
            return SkillRunResult(status=SkillRunStatus.SUCCEEDED, steps=1, duration_ms=100)

        mock_runner.run = slow_run

        manifest = _make_dummy_manifest()
        ctx = SkillExecutionContext(
            task_id="task-child-cancel-001",
            trace_id="trace-cancel-001",
            agent_runtime_id="subagent-cancel-001",
            agent_session_id="session-cancel-001",
            parent_task_id="task-parent-cancel-001",
            metadata={"task_description": "Slow task"},
            usage_limits=UsageLimits(max_steps=30),
        )

        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=2)
        mock_event_store.append_event = AsyncMock()

        callback_called = []

        async def result_callback(**kwargs):
            callback_called.append(kwargs)

        executor = SubagentExecutor(
            child_task=child_task,
            skill_runner=mock_runner,
            manifest=manifest,
            execution_context=ctx,
            a2a_conversation_id="a2a-conv-cancel-001",
            parent_agent_uri="agent://workers/worker-cancel-001",
            subagent_agent_uri="agent://workers/worker-cancel-001/subagents/subagent-cancel-001",
            event_store=mock_event_store,
            store_group=store_group,
            result_callback=result_callback,
        )

        await executor.start()
        assert executor.is_running

        # 短暂等待确保 _run_loop 已经开始
        await asyncio.sleep(0.05)

        # 取消
        await executor.cancel()
        await executor.wait()

        assert not executor.is_running

        # 验证 Child Task 状态
        updated = await store_group.task_store.get_task("task-child-cancel-001")
        assert updated is not None
        assert updated.status == TaskStatus.CANCELLED

        # 验证回调被调用
        assert len(callback_called) == 1
        assert callback_called[0]["status"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_executor_failed_on_exception(self, store_group: StoreGroup) -> None:
        """SkillRunner 抛出异常 → Child Task 流转到 FAILED。"""
        now = datetime.now(tz=UTC)

        parent_rt = AgentRuntime(
            agent_runtime_id="worker-fail-001",
            project_id="proj-1",
    
            agent_profile_id="ap-1",
            worker_profile_id="wp-1",
            role=AgentRuntimeRole.WORKER,
            name="Parent",
            status=AgentRuntimeStatus.ACTIVE,
            metadata={},
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_runtime(parent_rt)

        sub_rt = AgentRuntime(
            agent_runtime_id="subagent-fail-001",
            project_id="proj-1",
    
            agent_profile_id="ap-1",
            worker_profile_id="wp-1",
            role=AgentRuntimeRole.WORKER,
            name="Subagent",
            status=AgentRuntimeStatus.ACTIVE,
            metadata={"is_subagent": True, "parent_worker_runtime_id": "worker-fail-001"},
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_runtime(sub_rt)

        sub_session = AgentSession(
            agent_session_id="session-fail-001",
            agent_runtime_id="subagent-fail-001",
            kind=AgentSessionKind.SUBAGENT_INTERNAL,
            status=AgentSessionStatus.ACTIVE,
            project_id="proj-1",
    
            parent_worker_runtime_id="worker-fail-001",
            created_at=now,
            updated_at=now,
        )
        await store_group.agent_context_store.save_agent_session(sub_session)

        child_task = Task(
            task_id="task-child-fail-001",
            created_at=now,
            updated_at=now,
            status=TaskStatus.CREATED,
            title="Failing task",
            requester=RequesterInfo(channel="subagent", sender_id="subagent-fail-001"),
            parent_task_id="task-parent-fail-001",
        )
        await store_group.task_store.create_task(child_task)

        conv = A2AConversation(
            a2a_conversation_id="a2a-conv-fail-001",
            task_id="task-child-fail-001",
            source_agent="agent://workers/worker-fail-001",
            target_agent="agent://workers/worker-fail-001/subagents/subagent-fail-001",
            status=A2AConversationStatus.ACTIVE,
        )
        await store_group.a2a_store.save_conversation(conv)
        await store_group.conn.commit()

        # Mock runner 会抛出异常
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(side_effect=RuntimeError("Boom!"))

        manifest = _make_dummy_manifest()
        ctx = SkillExecutionContext(
            task_id="task-child-fail-001",
            trace_id="trace-fail-001",
            agent_runtime_id="subagent-fail-001",
            agent_session_id="session-fail-001",
            parent_task_id="task-parent-fail-001",
            metadata={"task_description": "Fail task"},
            usage_limits=UsageLimits(max_steps=30),
        )

        mock_event_store = AsyncMock()
        mock_event_store.get_next_task_seq = AsyncMock(return_value=2)
        mock_event_store.append_event = AsyncMock()

        executor = SubagentExecutor(
            child_task=child_task,
            skill_runner=mock_runner,
            manifest=manifest,
            execution_context=ctx,
            a2a_conversation_id="a2a-conv-fail-001",
            parent_agent_uri="agent://workers/worker-fail-001",
            subagent_agent_uri="agent://workers/worker-fail-001/subagents/subagent-fail-001",
            event_store=mock_event_store,
            store_group=store_group,
        )

        await executor.start()
        await executor.wait()

        assert not executor.is_running

        # 验证 Child Task 状态
        updated = await store_group.task_store.get_task("task-child-fail-001")
        assert updated is not None
        assert updated.status == TaskStatus.FAILED


# ============================================================
# Orchestrator SubagentResultQueue 测试
# ============================================================


class TestSubagentResultQueue:
    """验证 Orchestrator SubagentResultQueue 功能。"""

    @pytest.mark.asyncio
    async def test_drain_empty_queue(self) -> None:
        """空队列返回空列表。"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        # 创建一个最小 mock OrchestratorService 来测试 drain
        orch = MagicMock(spec=OrchestratorService)
        orch._subagent_result_queues = {}
        orch.drain_subagent_results = OrchestratorService.drain_subagent_results.__get__(
            orch, OrchestratorService
        )

        results = orch.drain_subagent_results("nonexistent-task")
        assert results == []

    @pytest.mark.asyncio
    async def test_enqueue_and_drain(self) -> None:
        """enqueue 后 drain 返回结果。"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        orch = MagicMock(spec=OrchestratorService)
        orch._subagent_result_queues = {}
        orch._stores = MagicMock()
        orch._stores.event_store = AsyncMock()
        orch._stores.event_store.get_next_task_seq = AsyncMock(return_value=5)
        orch._stores.event_store.append_event = AsyncMock()
        orch._stores.conn = AsyncMock()
        orch._sse_hub = MagicMock()
        orch._sse_hub.broadcast = AsyncMock()

        # 绑定真实方法
        orch.enqueue_subagent_result = OrchestratorService.enqueue_subagent_result.__get__(
            orch, OrchestratorService
        )
        orch.drain_subagent_results = OrchestratorService.drain_subagent_results.__get__(
            orch, OrchestratorService
        )

        await orch.enqueue_subagent_result(
            parent_task_id="parent-001",
            child_task_id="child-001",
            subagent_name="TestSub",
            status="SUCCEEDED",
            summary="All done",
            artifact_count=0,
        )

        results = orch.drain_subagent_results("parent-001")
        assert len(results) == 1
        assert "[Subagent Result]" in results[0]
        assert "TestSub" in results[0]
        assert "All done" in results[0]

        # 再次 drain 应该为空
        results2 = orch.drain_subagent_results("parent-001")
        assert results2 == []

    @pytest.mark.asyncio
    async def test_multiple_subagent_results_fifo(self) -> None:
        """多个 Subagent 结果按 FIFO 顺序消费。"""
        from octoagent.gateway.services.orchestrator import OrchestratorService

        orch = MagicMock(spec=OrchestratorService)
        orch._subagent_result_queues = {}
        orch._stores = MagicMock()
        orch._stores.event_store = AsyncMock()
        orch._stores.event_store.get_next_task_seq = AsyncMock(return_value=5)
        orch._stores.event_store.append_event = AsyncMock()
        orch._stores.conn = AsyncMock()
        orch._sse_hub = MagicMock()
        orch._sse_hub.broadcast = AsyncMock()

        orch.enqueue_subagent_result = OrchestratorService.enqueue_subagent_result.__get__(
            orch, OrchestratorService
        )
        orch.drain_subagent_results = OrchestratorService.drain_subagent_results.__get__(
            orch, OrchestratorService
        )

        # 入队 3 个结果
        for i in range(3):
            await orch.enqueue_subagent_result(
                parent_task_id="parent-fifo",
                child_task_id=f"child-{i}",
                subagent_name=f"Sub-{i}",
                status="SUCCEEDED",
                summary=f"Result {i}",
                artifact_count=0,
            )

        results = orch.drain_subagent_results("parent-fifo")
        assert len(results) == 3
        assert "Sub-0" in results[0]
        assert "Sub-1" in results[1]
        assert "Sub-2" in results[2]
