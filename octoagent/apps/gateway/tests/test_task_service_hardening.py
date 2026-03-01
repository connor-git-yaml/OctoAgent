"""TaskService 加固测试

覆盖修复项：
1. 幂等键并发冲突时返回已存在 task，不抛 500
2. task_seq 冲突时自动重试
3. MODEL_CALL_FAILED 事件不暴露底层异常细节
4. 失败事件落盘再次失败时，任务不会卡在 RUNNING
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest_asyncio
from octoagent.core.models import ActorType, Event, EventType, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService

import octoagent.gateway.services.task_service as task_service_module


@pytest_asyncio.fixture
async def service_with_store(tmp_path: Path):
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    service = TaskService(store_group, SSEHub())

    yield service, store_group

    await store_group.conn.close()
    os.environ.pop("OCTOAGENT_DB_PATH", None)
    os.environ.pop("OCTOAGENT_ARTIFACTS_DIR", None)
    os.environ.pop("LOGFIRE_SEND_TO_LOGFIRE", None)


class TestTaskServiceHardening:
    async def test_idempotency_integrity_conflict_returns_existing_task(
        self, service_with_store, monkeypatch
    ):
        service, store_group = service_with_store
        first_msg = NormalizedMessage(
            text="first",
            idempotency_key="idem-race-001",
        )
        existing_task_id, created = await service.create_task(first_msg)
        assert created is True

        original_check = store_group.event_store.check_idempotency_key
        call_count = 0

        async def race_like_check(key: str):
            nonlocal call_count
            call_count += 1
            # 第一次检查模拟“并发窗口”：尚未看到已存在幂等键
            if call_count == 1:
                return None
            return await original_check(key)

        monkeypatch.setattr(store_group.event_store, "check_idempotency_key", race_like_check)

        dup_msg = NormalizedMessage(
            text="duplicate",
            idempotency_key="idem-race-001",
        )
        task_id, created = await service.create_task(dup_msg)

        assert created is False
        assert task_id == existing_task_id
        tasks = await store_group.task_store.list_tasks()
        assert len(tasks) == 1

    async def test_append_event_retries_on_task_seq_conflict(
        self, service_with_store, monkeypatch
    ):
        service, store_group = service_with_store
        task_id, _ = await service.create_task(
            NormalizedMessage(
                text="seq-retry",
                idempotency_key="seq-retry-001",
            )
        )

        real_append_event_only = task_service_module.append_event_only
        call_count = 0

        async def flaky_append(conn, event_store, event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise aiosqlite.IntegrityError(
                    "UNIQUE constraint failed: events.task_id, events.task_seq"
                )
            return await real_append_event_only(conn, event_store, event)

        monkeypatch.setattr(task_service_module, "append_event_only", flaky_append)

        event = await service._append_event_only_with_retry(
            task_id=task_id,
            event_builder=lambda seq: Event(
                event_id="01JRETRY000000000000000001",
                task_id=task_id,
                task_seq=seq,
                ts=datetime.now(UTC),
                type=EventType.ERROR,
                actor=ActorType.SYSTEM,
                payload={"error_type": "system"},
                trace_id=f"trace-{task_id}",
            ),
        )

        assert call_count == 2
        assert event.task_seq >= 3
        events = await store_group.event_store.get_events_for_task(task_id)
        assert any(e.event_id == "01JRETRY000000000000000001" for e in events)

    async def test_llm_failure_event_hides_internal_error_detail(
        self, service_with_store
    ):
        service, store_group = service_with_store
        task_id, _ = await service.create_task(
            NormalizedMessage(
                text="fail-mask",
                idempotency_key="fail-mask-001",
            )
        )
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )

        await service._handle_llm_failure(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            model_alias="main",
            error=RuntimeError("api_key=secret-123 token=abc"),
        )

        events = await store_group.event_store.get_events_for_task(task_id)
        failed_events = [e for e in events if e.type == EventType.MODEL_CALL_FAILED]
        assert len(failed_events) == 1
        payload = failed_events[0].payload
        assert payload["error_type"] == "RuntimeError"
        assert payload["error_message"] == "LLM 调用失败，请查看服务端日志"
        assert "secret" not in payload["error_message"].lower()

        task = await store_group.task_store.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED

    async def test_force_failed_when_failure_event_write_fails(
        self, service_with_store, monkeypatch
    ):
        service, store_group = service_with_store
        task_id, _ = await service.create_task(
            NormalizedMessage(
                text="force-failed",
                idempotency_key="force-failed-001",
            )
        )
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )

        async def always_fail_append(*args, **kwargs):
            raise RuntimeError("append failed")

        monkeypatch.setattr(task_service_module, "append_event_only", always_fail_append)

        await service._handle_llm_failure(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            model_alias="main",
            error=RuntimeError("boom"),
        )

        task = await store_group.task_store.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED
