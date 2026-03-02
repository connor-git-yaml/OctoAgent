"""TaskRunner 测试"""

from __future__ import annotations

import asyncio
from pathlib import Path

from octoagent.core.models.enums import TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService


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
