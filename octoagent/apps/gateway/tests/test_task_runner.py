"""TaskRunner 测试"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from octoagent.core.models import CheckpointSnapshot, CheckpointStatus
from octoagent.core.models.enums import TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
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

        await runner.shutdown()
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
