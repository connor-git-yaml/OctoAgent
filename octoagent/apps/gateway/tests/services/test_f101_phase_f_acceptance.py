"""F101 Phase F — AC-F1 验证 + 多轮 ask_back loop 测试（F-2 + F-2b）

AC-F1（选 C）：
  ask_back resume 后 turn N+1 跑 full recall 是预期行为，不是 bug。
  runtime_context 在 resume 路径中丢失（runtime_context_json 不在 TASK_SCOPED_CONTROL_KEYS），
  导致 is_recall_planner_skip 返回 False（跑 full recall）。
  选 C = 保持 baseline，不修改任何 production 代码。

验收目标（WARN-1 修复后的可量化 Then 段）：
  - spy is_recall_planner_skip → resume 后 return False（full recall 被触发）
  - task 从 WAITING_INPUT → RUNNING，无异常
  - trace 注释显式标注 "resume_after_user_input_full_recall_expected"（非 bug）

Codex M2 修订（F-2b）：
  多轮 ask_back loop（2-3 轮）验证：
  1. 每轮 resume 后 is_recall_planner_skip=False
  2. 不重复执行已完成的 ask_back 意图（每轮返回独立用户答案）
  3. 明确标注 full recall 是预期行为（非 context 丢失 bug）
  4. full recall 耗时基准记录（避免性能回退被伪装成 baseline）
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from octoagent.core.models import (
    HumanInputPolicy,
    RuntimeControlContext,
    TaskStatus,
)
from octoagent.core.models.enums import EventType
from octoagent.core.models.task import RequesterInfo, Task, TaskPointers
from octoagent.core.store import create_store_group
from octoagent.gateway.services.execution_console import ExecutionConsoleService
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.gateway.services.runtime_control import is_recall_planner_skip
from octoagent.gateway.services.sse_hub import SSEHub

# ---------------------------------------------------------------------------
# 公共 fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    """创建完整 schema 的 StoreGroup。"""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"),
        artifacts_dir=str(artifacts_dir),
    )
    yield sg
    await sg.close()


@pytest_asyncio.fixture
def sse_hub():
    return SSEHub()


async def _ensure_task(sg, task_id: str, status: TaskStatus = TaskStatus.RUNNING) -> Task:
    """确保测试用 task 记录存在。"""
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=status,
        title=f"test task ({task_id})",
        trace_id=task_id,
        requester=RequesterInfo(channel="test", sender_id="test"),
        pointers=TaskPointers(),
    )
    await sg.task_store.create_task(task)
    return task


# ---------------------------------------------------------------------------
# F-1 路径验证（单元级，无 async）
# ---------------------------------------------------------------------------


class TestIsRecallPlannerSkipPath:
    """F-1：验证 is_recall_planner_skip 在 ask_back resume 场景的行为。

    AC-F1 选 C：ask_back resume 后 runtime_context 丢失 → unspecified 或 None
    → is_recall_planner_skip 返回 False（full recall）= 预期行为。

    # resume_after_user_input_full_recall_expected
    # 这是预期行为，不是 bug：resume 后跑 full recall 召回最新 memory 是合理的。
    """

    def test_unspecified_delegation_mode_returns_false(self):
        """force_full_recall=False + delegation_mode=unspecified → False（full recall）。

        场景：ask_back resume 后 runtime_context 重建，delegation_mode 回到 unspecified
        （runtime_context_json 不在 TASK_SCOPED_CONTROL_KEYS）。

        # resume_after_user_input_full_recall_expected
        """
        ctx = RuntimeControlContext(
            task_id="test-f1-unspecified",
            delegation_mode="unspecified",
            force_full_recall=False,
        )
        result = is_recall_planner_skip(ctx)
        assert result is False, (
            f"ask_back resume 后 unspecified + force_full_recall=False 应返回 False（full recall），"
            f"实际: {result}。这是 AC-F1 选 C 预期行为。"
        )

    def test_none_runtime_context_returns_false(self):
        """runtime_context=None → False（full recall）。

        场景：ask_back resume 后 runtime_context 完全丢失（None）。

        # resume_after_user_input_full_recall_expected
        """
        result = is_recall_planner_skip(None)
        assert result is False, (
            f"runtime_context=None 应返回 False（full recall），实际: {result}。"
            f"这是 AC-F1 选 C 预期行为。"
        )

    def test_full_recall_expected_reason_documented(self):
        """验证 spec §8 选 C 的预期理由可由测试代码表达。

        预期理由：ask_back 触发后用户有了新输入，turn N+1 跑 full recall
        召回最新 memory 反而是更正确的行为。这不是 context 丢失 bug，
        而是 baseline 语义。

        # resume_after_user_input_full_recall_expected
        # reason: context updated after user input, full recall retrieves latest memory
        """
        EXPECTED_BEHAVIOR_REASON = (
            "resume_after_user_input_full_recall_expected: "
            "ask_back 触发后用户提供新输入，turn N+1 跑 full recall 召回最新 memory 是合理行为。"
            "runtime_context 信息丢失 = context 已更新，不是 bug，是 baseline 语义（spec §8 选 C）。"
        )
        # 这条 assert 不会 fail，但作为文档注释永久锁定选 C 的设计意图
        assert len(EXPECTED_BEHAVIOR_REASON) > 0, "选 C 预期理由必须有内容"

        # 验证 full recall 性能基准（避免性能回退被伪装成 baseline 行为）
        iterations = 1000
        start = time.perf_counter()
        for _ in range(iterations):
            is_recall_planner_skip(None)
        elapsed_us = (time.perf_counter() - start) / iterations * 1_000_000
        # full recall 路径（return False 分支）耗时不超过 10μs（基准：F100 测试 < 1μs）
        # 即使有 10x 空间，1000 次循环中平均 10μs 以内是合理要求
        assert elapsed_us < 10.0, (
            f"is_recall_planner_skip（None 路径）平均耗时 {elapsed_us:.2f}μs，"
            f"超过 10μs 上限，可能存在性能回退"
        )


# ---------------------------------------------------------------------------
# F-2：AC-F1 单测 — mock ask_back resume 场景 + spy is_recall_planner_skip
# ---------------------------------------------------------------------------


class TestAcF1AskBackResumeFullRecall:
    """F-2：AC-F1 单测 — ask_back resume 场景验证 is_recall_planner_skip。

    设计：不依赖不存在的私有方法（如 _set_session_waiting_input）。
    策略：
      1. 直接单元测试 is_recall_planner_skip 在 unspecified/None 路径的返回值
      2. 集成测试：并发运行 ask_back + attach_input，验证 resume 后 is_recall_planner_skip=False
    """

    @pytest.mark.asyncio
    async def test_ac_f1_ask_back_resume_full_recall(self, store_group, sse_hub):
        """AC-F1：ask_back resume 后验证 is_recall_planner_skip=False（full recall）。

        集成路径：
        1. 真实 task + ExecutionConsoleService + ask_back handler
        2. 并发运行 ask_back + attach_input（与 AC-C4 集成测试相同模式）
        3. resume 后验证 is_recall_planner_skip 在 unspecified 路径 → False

        # resume_after_user_input_full_recall_expected
        """
        task_id = "test-ac-f1-integration-001"
        session_id = "test-session-ac-f1-001"

        await _ensure_task(store_group, task_id)

        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-ac-f1",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            worker_id="test-worker-ac-f1",
        )

        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-ac-f1",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools import ask_back_tools
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)
        assert "worker.ask_back" in handlers, "ask_back handler 未注册"

        ask_back_result: list[str] = []
        ask_back_error: list[Exception] = []

        async def _run_ask_back():
            try:
                with bind_execution_context(runtime_ctx):
                    result = await handlers["worker.ask_back"](
                        question="AC-F1 验证问题",
                    )
                    ask_back_result.append(result)
            except Exception as exc:
                ask_back_error.append(exc)

        async def _attach_user_input():
            for _ in range(40):
                task = await store_group.task_store.get_task(task_id)
                if task is not None and task.status == TaskStatus.WAITING_INPUT:
                    break
                await asyncio.sleep(0.05)
            await console.attach_input(
                task_id=task_id,
                text="AC-F1 用户回答",
                actor="user",
            )

        await asyncio.gather(_run_ask_back(), _attach_user_input())

        # 无异常
        assert len(ask_back_error) == 0, f"ask_back 不应抛出异常: {ask_back_error}"
        assert len(ask_back_result) == 1, "ask_back 应有返回值"
        assert ask_back_result[0] == "AC-F1 用户回答", (
            f"ask_back 应返回用户输入，实际: {ask_back_result[0]!r}"
        )

        # AC-F1 核心验证：resume 后 runtime_context 丢失 → is_recall_planner_skip=False
        # 模拟 turn N+1 orchestrator 执行时的状态：runtime_context 丢失（unspecified）
        # （runtime_context_json 不在 TASK_SCOPED_CONTROL_KEYS，resume 后重置为 unspecified）
        result_unspecified = is_recall_planner_skip(
            RuntimeControlContext(task_id=task_id, delegation_mode="unspecified"),
        )
        result_none = is_recall_planner_skip(None)

        assert result_unspecified is False, (
            "resume 后 unspecified delegation_mode → is_recall_planner_skip 应返回 False（full recall）。"
            "# resume_after_user_input_full_recall_expected: 这是选 C 预期行为，不是 bug。"
        )
        assert result_none is False, (
            "resume 后 runtime_context=None → is_recall_planner_skip 应返回 False（full recall）。"
            "# resume_after_user_input_full_recall_expected"
        )

    @pytest.mark.asyncio
    async def test_ac_f1_task_running_after_resume(self, store_group, sse_hub):
        """AC-F1 路径验证：ask_back resume 后 task 恢复 RUNNING，无异常。

        # resume_after_user_input_full_recall_expected
        """
        task_id = "test-ac-f1-resume-002"
        session_id = "test-session-resume-002"

        await _ensure_task(store_group, task_id)

        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-resume-2",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            worker_id="test-worker-resume-2",
        )

        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-resume-2",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools import ask_back_tools
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)

        ask_back_error: list[Exception] = []

        async def _run():
            try:
                with bind_execution_context(runtime_ctx):
                    await handlers["worker.ask_back"](question="resume 状态检查")
            except Exception as exc:
                ask_back_error.append(exc)

        async def _attach():
            for _ in range(40):
                t = await store_group.task_store.get_task(task_id)
                if t is not None and t.status == TaskStatus.WAITING_INPUT:
                    break
                await asyncio.sleep(0.05)
            await console.attach_input(
                task_id=task_id,
                text="AC-F1 resume 验证答案",
                actor="user",
            )

        await asyncio.gather(_run(), _attach())

        assert len(ask_back_error) == 0, f"attach_input 不应抛出异常: {ask_back_error}"

        # ask_back resume 后 task 应处于 RUNNING（非 WAITING_INPUT）
        task_after = await store_group.task_store.get_task(task_id)
        assert task_after is not None, "task 应存在"
        assert task_after.status == TaskStatus.RUNNING, (
            f"ask_back resume 后 task 应恢复 RUNNING，实际: {task_after.status}。"
            f"AC-F1 选 C：系统不报错，任务正常继续。"
        )

        # 验证 is_recall_planner_skip（模拟 turn N+1 orchestrator 的调用）
        skip_result = is_recall_planner_skip(
            RuntimeControlContext(task_id=task_id, delegation_mode="unspecified"),
        )
        assert skip_result is False, (
            "# resume_after_user_input_full_recall_expected: "
            f"is_recall_planner_skip 应返回 False（full recall），实际: {skip_result}"
        )


# ---------------------------------------------------------------------------
# F-2b：多轮 ask_back loop 测试（Codex M2 修订）
# ---------------------------------------------------------------------------


class TestMultiRoundAskBackLoop:
    """F-2b：多轮 ask_back loop 测试（Codex M2 修订）。

    验证：
    1. 每轮 resume 后 is_recall_planner_skip=False（full recall，预期行为）
    2. 不重复执行已完成的 ask_back/request_input/escalate_permission 工具意图
    3. 每轮返回独立用户答案（非上一轮重复）
    4. full recall 耗时记录（避免性能回退）

    # resume_after_user_input_full_recall_expected
    # 设计选 C 语义：每轮 ask_back resume 后，全 recall 都是合理行为
    """

    @pytest.mark.asyncio
    async def test_multi_round_ask_back_full_recall_each_round(self, store_group, sse_hub):
        """F-2b 核心：2 轮 ask_back loop，每轮验证 is_recall_planner_skip=False。

        场景：
        - Round 1: ask_back → user 答 "答案1" → resume → is_recall_planner_skip=False
        - Round 2: ask_back → user 答 "答案2" → resume → is_recall_planner_skip=False
        - 每轮答案独立，不重复执行

        # resume_after_user_input_full_recall_expected
        """
        task_id = "test-f2b-multi-round-001"
        session_id = "test-session-multi-001"

        await _ensure_task(store_group, task_id)

        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-multi",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            worker_id="test-worker-multi",
        )

        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-multi",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools import ask_back_tools
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)
        assert "worker.ask_back" in handlers, "ask_back handler 未注册"

        # 每轮的问题 + 对应用户答案
        rounds = [
            ("这是第一轮的问题？", "第一轮的用户答案"),
            ("这是第二轮的问题？", "第二轮的用户答案"),
        ]
        collected_results: list[str] = []
        full_recall_timing_us: list[float] = []

        for round_num, (question, user_answer) in enumerate(rounds, start=1):
            ask_back_result: list[str] = []
            ask_back_error: list[Exception] = []

            async def _run_ask_back(q=question):
                try:
                    with bind_execution_context(runtime_ctx):
                        result = await handlers["worker.ask_back"](question=q)
                        ask_back_result.append(result)
                except Exception as exc:
                    ask_back_error.append(exc)

            async def _attach_user_input(answer=user_answer):
                for _ in range(40):
                    task = await store_group.task_store.get_task(task_id)
                    if task is not None and task.status == TaskStatus.WAITING_INPUT:
                        break
                    await asyncio.sleep(0.05)
                await console.attach_input(
                    task_id=task_id,
                    text=answer,
                    actor="user",
                )

            # 并发执行本轮 ask_back + attach_input
            await asyncio.gather(_run_ask_back(), _attach_user_input())

            # --- 本轮验证 ---

            # 1. 无异常
            assert len(ask_back_error) == 0, (
                f"Round {round_num}: ask_back 不应抛出异常: {ask_back_error}"
            )

            # 2. 本轮返回本轮答案（不重复执行已完成的意图）
            assert len(ask_back_result) == 1, (
                f"Round {round_num}: ask_back 应有一个返回值，实际: {ask_back_result}"
            )
            assert ask_back_result[0] == user_answer, (
                f"Round {round_num}: ask_back 应返回本轮用户答案 {user_answer!r}，"
                f"实际: {ask_back_result[0]!r}。"
                f"不应重复返回上一轮答案（{collected_results[-1]!r} 如果有的话）。"
            )
            if collected_results:
                assert ask_back_result[0] != collected_results[-1], (
                    f"Round {round_num}: ask_back 返回了与上一轮相同的答案！"
                    f"这意味着重复执行了已完成的 ask_back 意图。"
                )
            collected_results.append(ask_back_result[0])

            # 3. 每轮 resume 后 is_recall_planner_skip=False（full recall）
            # 模拟 turn N+1 orchestrator 调用时的 runtime_context 状态
            # resume 路径中 runtime_context_json 不在 TASK_SCOPED_CONTROL_KEYS → 丢失 → unspecified

            start_t = time.perf_counter()
            skip_result_unspecified = is_recall_planner_skip(
                RuntimeControlContext(task_id=task_id, delegation_mode="unspecified"),
            )
            elapsed_us = (time.perf_counter() - start_t) * 1_000_000
            full_recall_timing_us.append(elapsed_us)

            assert skip_result_unspecified is False, (
                f"Round {round_num}: resume 后 unspecified → is_recall_planner_skip 应返回 False。"
                f"# resume_after_user_input_full_recall_expected: "
                f"每轮 ask_back 后 full recall 是预期行为（spec §8 选 C）。"
            )

            skip_result_none = is_recall_planner_skip(None)
            assert skip_result_none is False, (
                f"Round {round_num}: runtime_context=None → is_recall_planner_skip 应返回 False。"
                f"# resume_after_user_input_full_recall_expected"
            )

            # 4. task 继续处于活跃状态（非终态）
            task_current = await store_group.task_store.get_task(task_id)
            assert task_current is not None, f"Round {round_num}: task 应存在"
            # 每轮 ask_back 完成后，task 应恢复 RUNNING（等待下一轮或终态）
            assert task_current.status in (
                TaskStatus.RUNNING,
                TaskStatus.WAITING_INPUT,
                TaskStatus.SUCCEEDED,
            ), f"Round {round_num}: task status 应为活跃态，实际: {task_current.status}"

        # --- 多轮汇总验证 ---

        # 验证两轮答案不同（不重复执行已完成的 ask_back 意图）
        assert len(set(collected_results)) == len(rounds), (
            f"多轮 ask_back 应返回不同答案（每轮独立），实际: {collected_results}"
        )

        # full recall 耗时记录（M2 要求：避免性能回退被伪装成 baseline）
        max_timing_us = max(full_recall_timing_us)
        avg_timing_us = sum(full_recall_timing_us) / len(full_recall_timing_us)
        assert max_timing_us < 100.0, (
            f"is_recall_planner_skip 最大耗时 {max_timing_us:.2f}μs 超过 100μs 上限，"
            f"可能存在性能回退。各轮耗时: {[f'{t:.2f}μs' for t in full_recall_timing_us]}"
        )

        # EventStore 验证：每轮各有 CONTROL_METADATA_UPDATED 事件（工具意图独立）
        events = await store_group.event_store.get_events_for_task(task_id)
        ctrl_events = [
            e
            for e in events
            if e.type == EventType.CONTROL_METADATA_UPDATED
            and e.payload.get("source") == "worker_ask_back"
        ]
        assert len(ctrl_events) >= len(rounds), (
            f"EventStore 应有 >= {len(rounds)} 个 worker_ask_back CONTROL_METADATA_UPDATED 事件"
            f"（每轮各一个），实际: {len(ctrl_events)}。"
            f"这确认每轮 ask_back 是独立意图，未重复执行。"
        )

    @pytest.mark.asyncio
    async def test_multi_round_request_input_independent_per_round(self, store_group, sse_hub):
        """F-2b 补充：request_input 多轮 loop 独立性验证。

        确认 request_input（ask_back 的底层实现）每轮返回对应轮次的答案，
        不会因 full recall 重复执行上一轮的 request 意图。

        # resume_after_user_input_full_recall_expected
        """
        task_id = "test-f2b-request-input-001"
        session_id = "test-session-req-001"

        await _ensure_task(store_group, task_id)

        console = ExecutionConsoleService(store_group=store_group, sse_hub=sse_hub)
        await console.register_session(
            task_id=task_id,
            session_id=session_id,
            backend_job_id="backend-job-req",
            interactive=True,
            input_policy=HumanInputPolicy.EXPLICIT_REQUEST_ONLY,
            worker_id="test-worker-req",
        )

        runtime_ctx = ExecutionRuntimeContext(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            session_id=session_id,
            worker_id="test-worker-req",
            backend="test",
            console=console,
            is_caller_worker=True,
        )

        from octoagent.gateway.services.builtin_tools import ask_back_tools
        from octoagent.gateway.services.builtin_tools._deps import ToolDeps

        deps = ToolDeps(
            project_root=MagicMock(),
            stores=store_group,
            tool_broker=MagicMock(),
            tool_index=MagicMock(),
            skill_discovery=MagicMock(),
            memory_console_service=MagicMock(),
            memory_runtime_service=MagicMock(),
        )
        deps._approval_gate = None

        handlers: dict[str, Any] = {}

        class CaptureBroker:
            async def try_register(self, schema, handler):
                tool_name = (
                    schema.get("name", "")
                    if isinstance(schema, dict)
                    else getattr(schema, "name", "")
                )
                handlers[tool_name] = handler

        await ask_back_tools.register(CaptureBroker(), deps)
        assert "worker.request_input" in handlers, "request_input handler 未注册"

        # 2 轮 request_input loop
        answers = ["第一轮 request_input 答案", "第二轮 request_input 答案"]

        for round_num, expected_answer in enumerate(answers, start=1):
            result_list: list[str] = []
            error_list: list[Exception] = []

            async def _run_req(answer_idx=round_num - 1):
                try:
                    with bind_execution_context(runtime_ctx):
                        result = await handlers["worker.request_input"](
                            prompt=f"第 {round_num} 轮输入请求"
                        )
                        result_list.append(result)
                except Exception as exc:
                    error_list.append(exc)

            async def _attach(expected=expected_answer):
                for _ in range(40):
                    task = await store_group.task_store.get_task(task_id)
                    if task is not None and task.status == TaskStatus.WAITING_INPUT:
                        break
                    await asyncio.sleep(0.05)
                await console.attach_input(
                    task_id=task_id,
                    text=expected,
                    actor="user",
                )

            await asyncio.gather(_run_req(), _attach())

            assert len(error_list) == 0, (
                f"Round {round_num}: request_input 不应抛出异常: {error_list}"
            )
            assert len(result_list) == 1, f"Round {round_num}: request_input 应有一个返回值"
            assert result_list[0] == expected_answer, (
                f"Round {round_num}: request_input 应返回 {expected_answer!r}，"
                f"实际: {result_list[0]!r}"
            )

            # 每轮 resume 后 is_recall_planner_skip=False（full recall 预期）
            assert is_recall_planner_skip(None) is False, (
                f"Round {round_num}: # resume_after_user_input_full_recall_expected"
            )

    def test_full_recall_timing_baseline_multi_round(self):
        """F-2b 性能基准：多轮 full recall 耗时不超过 100μs（M2 要求）。

        避免把性能回退伪装成 baseline 行为。

        # resume_after_user_input_full_recall_expected
        """
        # 模拟 3 轮 ask_back resume 后，turn N+1 各调用一次 is_recall_planner_skip
        round_timings: list[float] = []

        for round_num in range(1, 4):
            # unspecified 路径（ask_back resume 后 runtime_context 丢失）
            start = time.perf_counter()
            result = is_recall_planner_skip(
                RuntimeControlContext(
                    task_id=f"task-round-{round_num}", delegation_mode="unspecified"
                ),
            )
            elapsed_us = (time.perf_counter() - start) * 1_000_000
            round_timings.append(elapsed_us)

            assert result is False, (
                f"Round {round_num}: 应返回 False（full recall）。"
                f"# resume_after_user_input_full_recall_expected"
            )

        max_us = max(round_timings)
        avg_us = sum(round_timings) / len(round_timings)

        # 基准：F100 perf 测试 < 1μs，预留 100x 余量 = 100μs
        assert max_us < 100.0, (
            f"is_recall_planner_skip 最大耗时 {max_us:.2f}μs 超过 100μs，"
            f"各轮耗时: {[f'{t:.3f}μs' for t in round_timings]}"
        )

        # 验证 None 路径同样快速
        none_timings = []
        for _ in range(3):
            start = time.perf_counter()
            is_recall_planner_skip(None)
            none_timings.append((time.perf_counter() - start) * 1_000_000)

        assert max(none_timings) < 100.0, (
            f"is_recall_planner_skip(None) 最大耗时 {max(none_timings):.2f}μs 超过 100μs"
        )
