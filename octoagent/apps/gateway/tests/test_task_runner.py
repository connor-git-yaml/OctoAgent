"""TaskRunner 测试"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from octoagent.core.models import (
    CheckpointSnapshot,
    CheckpointStatus,
    DispatchEnvelope,
    ExecutionBackend,
    ExecutionSessionState,
    WorkerExecutionStatus,
    WorkerResult,
)
from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.execution_context import get_current_execution_context
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.worker_runtime import WorkerRuntimeConfig
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalDecision
from octoagent.provider.models import ModelCallResult, TokenUsage


class SlowLLMService:
    def __init__(self, delay_s: float = 0.3) -> None:
        self._delay_s = delay_s

    async def call(self, prompt_or_messages, model_alias: str | None = None) -> ModelCallResult:
        await asyncio.sleep(self._delay_s)
        return ModelCallResult(
            content="slow",
            model_alias=model_alias or "main",
            model_name="mock",
            provider="mock",
            duration_ms=int(self._delay_s * 1000),
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class CancellableLLMService:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def call(self, prompt_or_messages, model_alias: str | None = None) -> ModelCallResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class InteractiveLLMService:
    def __init__(self, *, approval_required: bool = False) -> None:
        self._approval_required = approval_required

    async def call(self, prompt_or_messages, model_alias: str | None = None) -> ModelCallResult:
        ctx = get_current_execution_context()
        await ctx.emit_log("stdout", "interactive-start")
        human_input = await ctx.consume_resume_input()
        if human_input is None:
            human_input = await ctx.request_input(
                "请输入执行确认信息",
                approval_required=self._approval_required,
            )
        await ctx.emit_log("stdout", f"interactive-input:{human_input}")
        return ModelCallResult(
            content=f"interactive:{human_input}",
            model_alias=model_alias or "main",
            model_name="mock-interactive",
            provider="mock",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class TestTaskRunner:
    async def test_enqueue_runs_and_marks_job_succeeded(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        llm_service = LLMService()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="runner hello",
            idempotency_key="runner-001",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True

        await runner.enqueue(task_id, msg.text)
        await asyncio.sleep(0.4)

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED"
        assert job is not None
        assert job.status == "SUCCEEDED"

        await runner.shutdown()
        await store_group.conn.close()

    async def test_startup_recovery_marks_orphan_running_failed(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-recover.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="orphan running",
            idempotency_key="runner-002",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True

        # 先把 task 正常推进到 RUNNING
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
        )

        # 标记为 RUNNING job（模拟进程中断）
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)

        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner.startup()

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == "FAILED"
        assert job is not None
        assert job.status == "FAILED"

        await runner.shutdown()
        await store_group.conn.close()

    async def test_enqueue_notifies_completion(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-notify.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        notified: list[str] = []
        completed = asyncio.Event()

        async def notifier(task_id: str) -> None:
            notified.append(task_id)
            completed.set()

        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            completion_notifier=notifier,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="runner notify",
            idempotency_key="runner-notify-001",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True

        await runner.enqueue(task_id, msg.text)
        await asyncio.wait_for(completed.wait(), timeout=2.0)

        assert notified == [task_id]

        await runner.shutdown()
        await store_group.conn.close()

    async def test_cancel_running_job_marks_task_and_job_cancelled(
        self, tmp_path: Path
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-cancel.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=SlowLLMService(delay_s=0.5),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="runner cancel",
            idempotency_key="runner-cancel-001",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await runner.enqueue(task_id, msg.text)

        await asyncio.sleep(0.1)
        cancelled = await runner.cancel_task(task_id)
        assert cancelled is True

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == "CANCELLED"
        assert job is not None
        assert job.status == "CANCELLED"
        conversations = await store_group.a2a_store.list_conversations(task_id=task_id)
        assert len(conversations) == 1
        assert conversations[0].status.value == "cancelled"
        messages = await store_group.a2a_store.list_messages(
            a2a_conversation_id=conversations[0].a2a_conversation_id
        )
        assert [item.message_type for item in messages][-1] == "CANCEL"

        await runner.shutdown()
        await store_group.conn.close()

    async def test_cancel_running_job_cancels_underlying_llm_call(
        self,
        tmp_path: Path,
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-cancel-live.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        llm_service = CancellableLLMService()
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            worker_runtime_config=WorkerRuntimeConfig(docker_mode="disabled"),
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="runner cancel backend",
            idempotency_key="runner-cancel-live-001",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await runner.enqueue(task_id, msg.text)

        await asyncio.wait_for(llm_service.started.wait(), timeout=0.5)
        cancelled = await runner.cancel_task(task_id)
        assert cancelled is True
        await asyncio.wait_for(llm_service.cancelled.wait(), timeout=0.5)

        session = await runner.get_execution_session(task_id)
        assert session is not None
        assert session.state == ExecutionSessionState.CANCELLED

        task = await task_service.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED

        await runner.shutdown()
        await store_group.conn.close()

    @pytest.mark.parametrize(
        ("deferred_status", "job_status"),
        [
            (TaskStatus.WAITING_APPROVAL, "WAITING_APPROVAL"),
            (TaskStatus.PAUSED, "PAUSED"),
        ],
    )
    async def test_run_job_preserves_deferred_job_status(
        self,
        tmp_path: Path,
        deferred_status: TaskStatus,
        job_status: str,
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / f"runner-deferred-{deferred_status.value}.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )

        msg = NormalizedMessage(
            text=f"runner deferred {deferred_status.value}",
            idempotency_key=f"runner-deferred-{deferred_status.value}",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)

        async def fake_dispatch(**kwargs) -> WorkerResult:
            await task_service._write_state_transition(
                task_id=task_id,
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
                trace_id=f"trace-{task_id}",
            )
            await task_service._write_state_transition(
                task_id=task_id,
                from_status=TaskStatus.RUNNING,
                to_status=deferred_status,
                trace_id=f"trace-{task_id}",
                reason="delegation preflight deferred",
            )
            return WorkerResult(
                dispatch_id="dispatch-deferred",
                task_id=task_id,
                worker_id="worker.llm.default",
                status=WorkerExecutionStatus.FAILED,
                retryable=True,
                summary="delegation_deferred",
            )

        runner._orchestrator.dispatch = fake_dispatch  # type: ignore[method-assign]
        await runner._run_job(task_id=task_id, user_text=msg.text, model_alias="main")

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == deferred_status
        assert job is not None
        assert job.status == job_status

        await store_group.conn.close()

    @pytest.mark.parametrize(
        ("deferred_status", "mark_job"),
        [
            (TaskStatus.WAITING_APPROVAL, "WAITING_APPROVAL"),
            (TaskStatus.PAUSED, "PAUSED"),
        ],
    )
    async def test_cancel_deferred_job_without_live_runner_marks_cancelled(
        self,
        tmp_path: Path,
        deferred_status: TaskStatus,
        mark_job: str,
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / f"runner-cancel-{deferred_status.value}.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )

        msg = NormalizedMessage(
            text=f"cancel deferred {deferred_status.value}",
            idempotency_key=f"runner-cancel-deferred-{deferred_status.value}",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
        )
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.RUNNING,
            to_status=deferred_status,
            trace_id=f"trace-{task_id}",
            reason="delegation deferred",
        )
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)
        await store_group.task_job_store.mark_deferred(task_id, mark_job)

        cancelled = await runner.cancel_task(task_id)
        assert cancelled is True

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED
        assert job is not None
        assert job.status == "CANCELLED"

        await store_group.conn.close()

    async def test_schedule_dispatch_envelope_requeues_terminal_job_and_executes(
        self,
        tmp_path: Path,
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-prepared-dispatch.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )

        msg = NormalizedMessage(
            text="prepared dispatch retry",
            idempotency_key="runner-prepared-dispatch",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_failed(task_id, "previous_failed")

        dispatched = asyncio.Event()

        async def fake_dispatch_prepared(envelope: DispatchEnvelope) -> WorkerResult:
            await task_service._write_state_transition(
                task_id=task_id,
                from_status=TaskStatus.CREATED,
                to_status=TaskStatus.RUNNING,
                trace_id=envelope.trace_id,
            )
            await task_service._write_state_transition(
                task_id=task_id,
                from_status=TaskStatus.RUNNING,
                to_status=TaskStatus.SUCCEEDED,
                trace_id=envelope.trace_id,
                reason="prepared dispatch succeeded",
            )
            dispatched.set()
            return WorkerResult(
                dispatch_id=envelope.dispatch_id,
                task_id=task_id,
                worker_id="worker.llm.dev",
                status=WorkerExecutionStatus.SUCCEEDED,
                retryable=False,
                summary="prepared_dispatch_succeeded",
            )

        runner._orchestrator.dispatch_prepared = fake_dispatch_prepared  # type: ignore[method-assign]
        scheduled = await runner.schedule_dispatch_envelope(
            DispatchEnvelope(
                dispatch_id="dispatch-retry",
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                contract_version="1.0",
                route_reason="retry",
                worker_capability="dev",
                hop_count=1,
                max_hops=3,
                user_text=msg.text,
                model_alias="main",
                tool_profile="standard",
                metadata={"work_id": "work-001"},
            )
        )
        assert scheduled is True
        await asyncio.wait_for(dispatched.wait(), timeout=1.0)

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == TaskStatus.SUCCEEDED
        assert job is not None
        assert job.status == "SUCCEEDED"

        await store_group.conn.close()

    async def test_startup_recovery_resumes_from_checkpoint(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-resume.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="resume from checkpoint",
            idempotency_key="runner-003",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True

        # 模拟进程中断前：任务已在 RUNNING 且存在成功 checkpoint
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
        )
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)

        checkpoint = CheckpointSnapshot(
            checkpoint_id="cp-runner-003",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await store_group.checkpoint_store.save_checkpoint(checkpoint)
        await store_group.conn.commit()

        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner.startup()
        await asyncio.sleep(0.4)

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED"
        assert job is not None
        assert job.status == "SUCCEEDED"

        await runner.shutdown()
        await store_group.conn.close()

    @pytest.mark.parametrize(
        ("terminal_status", "expected_job_status"),
        [
            (TaskStatus.SUCCEEDED, "SUCCEEDED"),
            (TaskStatus.FAILED, "FAILED"),
        ],
    )
    async def test_startup_recovery_terminal_job_notifies_completion(
        self,
        tmp_path: Path,
        terminal_status: TaskStatus,
        expected_job_status: str,
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / f"runner-recover-{terminal_status.value}.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        notified: list[str] = []

        async def notifier(task_id: str) -> None:
            notified.append(task_id)

        msg = NormalizedMessage(
            text=f"recover notify {terminal_status.value}",
            idempotency_key=f"runner-recover-notify-{terminal_status.value}",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
        )
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.RUNNING,
            to_status=terminal_status,
            trace_id=f"trace-{task_id}",
        )
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)

        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            completion_notifier=notifier,
        )
        await runner.startup()

        job = await store_group.task_job_store.get_job(task_id)
        assert job is not None
        assert job.status == expected_job_status
        assert notified == [task_id]

        await runner.shutdown()
        await store_group.conn.close()

    async def test_recover_twice_side_effect_executes_only_once(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-double-resume.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="double resume without duplicated side effect",
            idempotency_key="runner-004",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True

        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
        )
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)

        checkpoint = CheckpointSnapshot(
            checkpoint_id="cp-runner-004-first",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await store_group.checkpoint_store.save_checkpoint(checkpoint)
        await store_group.conn.commit()

        class CountingLLMService:
            def __init__(self) -> None:
                self.calls = 0
                self._delegate = LLMService()

            async def call(self, prompt_or_messages, model_alias=None):
                self.calls += 1
                return await self._delegate.call(prompt_or_messages, model_alias=model_alias)

        llm_service = CountingLLMService()

        # 第一次恢复：正常执行一次 LLM
        runner1 = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner1.startup()
        await asyncio.sleep(0.45)
        await runner1.shutdown()
        assert llm_service.calls == 1

        # 模拟第二次恢复前的“任务仍 RUNNING + checkpoint 回退到 model_call_started”
        await store_group.conn.execute(
            "UPDATE tasks SET status = 'RUNNING' WHERE task_id = ?",
            (task_id,),
        )
        await store_group.conn.execute(
            "UPDATE task_jobs SET status = 'RUNNING' WHERE task_id = ?",
            (task_id,),
        )
        checkpoint_2 = CheckpointSnapshot(
            checkpoint_id="cp-runner-004-second",
            task_id=task_id,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await store_group.checkpoint_store.save_checkpoint(checkpoint_2)
        await store_group.conn.commit()

        # 第二次恢复：应复用结果，不再调用 LLM
        runner2 = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=llm_service,
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner2.startup()
        await asyncio.sleep(0.45)

        task = await task_service.get_task(task_id)
        job = await store_group.task_job_store.get_job(task_id)
        assert task is not None
        assert task.status == "SUCCEEDED"
        assert job is not None
        assert job.status == "SUCCEEDED"
        assert llm_service.calls == 1

        await runner2.shutdown()
        await store_group.conn.close()

    async def test_resume_event_chain_order(self, tmp_path: Path) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-event-chain.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)

        msg = NormalizedMessage(
            text="resume event chain",
            idempotency_key="runner-005",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await task_service._write_state_transition(
            task_id=task_id,
            from_status=TaskStatus.CREATED,
            to_status=TaskStatus.RUNNING,
            trace_id=f"trace-{task_id}",
        )
        await store_group.task_job_store.create_job(task_id, msg.text, "main")
        await store_group.task_job_store.mark_running(task_id)

        # 通过 TaskService 生成 CHECKPOINT_SAVED 事件与 checkpoint
        await task_service._write_checkpoint(
            task_id=task_id,
            node_id="model_call_started",
            trace_id=f"trace-{task_id}",
            state_snapshot={"next_node": "response_persisted"},
        )

        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=LLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
        )
        await runner.startup()
        await asyncio.sleep(0.45)

        events = await store_group.event_store.get_events_for_task(task_id)
        chain = [
            e.type.value
            for e in events
            if e.type.value
            in {
                "CHECKPOINT_SAVED",
                "RESUME_STARTED",
                "RESUME_SUCCEEDED",
                "RESUME_FAILED",
            }
        ]
        assert "CHECKPOINT_SAVED" in chain
        assert "RESUME_STARTED" in chain
        assert ("RESUME_SUCCEEDED" in chain) or ("RESUME_FAILED" in chain)

        checkpoint_idx = chain.index("CHECKPOINT_SAVED")
        started_idx = chain.index("RESUME_STARTED")
        assert checkpoint_idx < started_idx
        if "RESUME_SUCCEEDED" in chain:
            assert started_idx < chain.index("RESUME_SUCCEEDED")
        if "RESUME_FAILED" in chain:
            assert started_idx < chain.index("RESUME_FAILED")

        await runner.shutdown()
        await store_group.conn.close()

    async def test_attach_input_live_path_updates_session_and_job(
        self, tmp_path: Path
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-interactive.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=InteractiveLLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            docker_available_checker=lambda: True,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="need operator input",
            idempotency_key="runner-019-live",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await runner.enqueue(task_id, msg.text)

        for _ in range(20):
            task = await task_service.get_task(task_id)
            if task is not None and task.status == TaskStatus.WAITING_INPUT:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("task did not enter WAITING_INPUT")

        session = await runner.get_execution_session(task_id)
        assert session is not None
        assert session.state == ExecutionSessionState.WAITING_INPUT
        assert session.backend == ExecutionBackend.DOCKER
        assert session.can_attach_input is True

        job = await store_group.task_job_store.get_job(task_id)
        assert job is not None
        assert job.status == "WAITING_INPUT"

        result = await runner.attach_input(task_id, "live-confirmed")
        assert result.delivered_live is True

        for _ in range(20):
            task = await task_service.get_task(task_id)
            if task is not None and task.status == TaskStatus.SUCCEEDED:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("task did not reach SUCCEEDED")

        job = await store_group.task_job_store.get_job(task_id)
        assert job is not None
        assert job.status == "SUCCEEDED"

        for _ in range(20):
            events = await store_group.event_store.get_events_for_task(task_id)
            event_types = [event.type.value for event in events]
            if {
                "EXECUTION_INPUT_REQUESTED",
                "EXECUTION_INPUT_ATTACHED",
                "EXECUTION_STATUS_CHANGED",
                "A2A_MESSAGE_RECEIVED",
                "A2A_MESSAGE_SENT",
                "TASK_HEARTBEAT",
            }.issubset(set(event_types)):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(f"missing expected events: {event_types}")

        assert "EXECUTION_INPUT_REQUESTED" in event_types
        assert "EXECUTION_INPUT_ATTACHED" in event_types
        assert "EXECUTION_STATUS_CHANGED" in event_types
        assert "A2A_MESSAGE_RECEIVED" in event_types
        assert "A2A_MESSAGE_SENT" in event_types
        assert "TASK_HEARTBEAT" in event_types

        artifacts = await store_group.artifact_store.list_artifacts_for_task(task_id)
        artifact_names = [artifact.name for artifact in artifacts]
        assert "human-input" in artifact_names
        assert "llm-response" in artifact_names
        conversations = await store_group.a2a_store.list_conversations(task_id=task_id)
        assert len(conversations) == 1
        messages = await store_group.a2a_store.list_messages(
            a2a_conversation_id=conversations[0].a2a_conversation_id
        )
        message_types = [item.message_type for item in messages]
        assert "TASK" in message_types
        assert "HEARTBEAT" in message_types
        assert message_types.count("UPDATE") >= 2
        assert message_types[-1] == "RESULT"

        await runner.shutdown()
        await store_group.conn.close()

    async def test_attach_input_after_restart_resumes_waiting_task(
        self, tmp_path: Path
    ) -> None:
        db_path = str(tmp_path / "runner-interactive-restart.db")
        artifacts_dir = str(tmp_path / "artifacts")
        store_group = await create_store_group(db_path, artifacts_dir)
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=InteractiveLLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            docker_available_checker=lambda: True,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="restart for input",
            idempotency_key="runner-019-restart",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await runner.enqueue(task_id, msg.text)

        for _ in range(20):
            task = await task_service.get_task(task_id)
            if task is not None and task.status == TaskStatus.WAITING_INPUT:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("task did not enter WAITING_INPUT before restart")

        await runner.shutdown()
        await store_group.conn.close()

        store_group_2 = await create_store_group(db_path, artifacts_dir)
        sse_hub_2 = SSEHub()
        task_service_2 = TaskService(store_group_2, sse_hub_2)
        runner_2 = TaskRunner(
            store_group=store_group_2,
            sse_hub=sse_hub_2,
            llm_service=InteractiveLLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            docker_available_checker=lambda: True,
        )
        await runner_2.startup()

        session = await runner_2.get_execution_session(task_id)
        assert session is not None
        assert session.state == ExecutionSessionState.WAITING_INPUT
        assert session.live is False
        original_session_id = session.session_id

        result = await runner_2.attach_input(task_id, "restart-confirmed")
        assert result.delivered_live is False
        assert result.session_id == original_session_id

        for _ in range(20):
            task = await task_service_2.get_task(task_id)
            if task is not None and task.status == TaskStatus.SUCCEEDED:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("task did not resume to SUCCEEDED after restart")

        job = await store_group_2.task_job_store.get_job(task_id)
        assert job is not None
        assert job.status == "SUCCEEDED"
        final_session = await runner_2.get_execution_session(task_id)
        assert final_session is not None
        assert final_session.session_id == original_session_id

        await runner_2.shutdown()
        await store_group_2.conn.close()

    async def test_cancel_waiting_input_without_live_runner_marks_execution_cancelled(
        self, tmp_path: Path
    ) -> None:
        db_path = str(tmp_path / "runner-interactive-cancel.db")
        artifacts_dir = str(tmp_path / "artifacts")
        store_group = await create_store_group(db_path, artifacts_dir)
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=InteractiveLLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            docker_available_checker=lambda: True,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="restart cancel waiting input",
            idempotency_key="runner-019-cancel-waiting",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await runner.enqueue(task_id, msg.text)

        for _ in range(20):
            task = await task_service.get_task(task_id)
            if task is not None and task.status == TaskStatus.WAITING_INPUT:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("task did not enter WAITING_INPUT before shutdown")

        await runner.shutdown()
        await store_group.conn.close()

        store_group_2 = await create_store_group(db_path, artifacts_dir)
        sse_hub_2 = SSEHub()
        task_service_2 = TaskService(store_group_2, sse_hub_2)
        runner_2 = TaskRunner(
            store_group=store_group_2,
            sse_hub=sse_hub_2,
            llm_service=InteractiveLLMService(),
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            docker_available_checker=lambda: True,
        )
        await runner_2.startup()

        session = await runner_2.get_execution_session(task_id)
        assert session is not None
        assert session.state == ExecutionSessionState.WAITING_INPUT
        assert session.live is False

        cancelled = await runner_2.cancel_task(task_id)
        assert cancelled is True

        task = await task_service_2.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED

        job = await store_group_2.task_job_store.get_job(task_id)
        assert job is not None
        assert job.status == "CANCELLED"

        final_session = await runner_2.get_execution_session(task_id)
        assert final_session is not None
        assert final_session.state == ExecutionSessionState.CANCELLED

        events = await store_group_2.event_store.get_events_for_task(task_id)
        terminal_events = [
            event
            for event in events
            if event.type == EventType.EXECUTION_STATUS_CHANGED
        ]
        assert terminal_events
        assert terminal_events[-1].payload["status"] == ExecutionSessionState.CANCELLED.value
        conversations = await store_group_2.a2a_store.list_conversations(task_id=task_id)
        assert len(conversations) == 1
        assert conversations[0].status.value == "cancelled"
        messages = await store_group_2.a2a_store.list_messages(
            a2a_conversation_id=conversations[0].a2a_conversation_id
        )
        assert [item.message_type for item in messages][-1] == "CANCEL"

        await runner_2.shutdown()
        await store_group_2.conn.close()

    async def test_attach_input_requires_approval_when_requested(
        self, tmp_path: Path
    ) -> None:
        store_group = await create_store_group(
            str(tmp_path / "runner-approval.db"),
            str(tmp_path / "artifacts"),
        )
        sse_hub = SSEHub()
        approval_manager = ApprovalManager(event_store=store_group.event_store)
        task_service = TaskService(store_group, sse_hub)
        runner = TaskRunner(
            store_group=store_group,
            sse_hub=sse_hub,
            llm_service=InteractiveLLMService(approval_required=True),
            approval_manager=approval_manager,
            timeout_seconds=60,
            monitor_interval_seconds=0.05,
            docker_available_checker=lambda: True,
        )
        await runner.startup()

        msg = NormalizedMessage(
            text="approved input only",
            idempotency_key="runner-019-approval",
        )
        task_id, created = await task_service.create_task(msg)
        assert created is True
        await runner.enqueue(task_id, msg.text)

        for _ in range(20):
            session = await runner.get_execution_session(task_id)
            if session is not None and session.pending_approval_id:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("approval_id was not exposed for interactive input")

        approval_id = session.pending_approval_id
        assert approval_id is not None

        try:
            await runner.attach_input(task_id, "should-fail")
        except Exception as exc:
            assert "approval" in str(exc).lower()
        else:
            raise AssertionError("attach_input should require approval")

        resolved = await approval_manager.resolve(approval_id, ApprovalDecision.ALLOW_ONCE)
        assert resolved is True

        result = await runner.attach_input(
            task_id,
            "approved-live",
            approval_id=approval_id,
        )
        assert result.approval_id == approval_id

        for _ in range(20):
            task = await task_service.get_task(task_id)
            if task is not None and task.status == TaskStatus.SUCCEEDED:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("approved input did not resume task")

        final_session = await runner.get_execution_session(task_id)
        assert final_session is not None
        assert final_session.pending_approval_id is None

        await runner.shutdown()
        await store_group.conn.close()
