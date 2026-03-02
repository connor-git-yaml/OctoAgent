"""TaskJobStore 测试"""

from datetime import UTC, datetime

from octoagent.core.models import RequesterInfo, Task, TaskStatus
from octoagent.core.store.task_job_store import SqliteTaskJobStore
from octoagent.core.store.task_store import SqliteTaskStore


async def _create_task(conn, task_id: str) -> None:
    task_store = SqliteTaskStore(conn)
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        status=TaskStatus.CREATED,
        title="job task",
        thread_id="default",
        scope_id="chat:web:default",
        requester=RequesterInfo(channel="web", sender_id="owner"),
    )
    await task_store.create_task(task)
    await conn.commit()


class TestTaskJobStore:
    async def test_create_and_mark_lifecycle(self, core_db):
        await _create_task(core_db, "job-task-001")
        store = SqliteTaskJobStore(core_db)
        created = await store.create_job(
            task_id="job-task-001",
            user_text="hello",
            model_alias="main",
        )
        assert created is True

        started = await store.mark_running("job-task-001")
        assert started is True

        await store.mark_succeeded("job-task-001")
        job = await store.get_job("job-task-001")
        assert job is not None
        assert job.status == "SUCCEEDED"

    async def test_list_jobs_by_status(self, core_db):
        await _create_task(core_db, "job-task-002")
        await _create_task(core_db, "job-task-003")
        store = SqliteTaskJobStore(core_db)
        await store.create_job("job-task-002", "a")
        await store.create_job("job-task-003", "b")
        await store.mark_running("job-task-003")
        await store.mark_failed("job-task-003", "boom")

        queued = await store.list_jobs(["QUEUED"])
        failed = await store.list_jobs(["FAILED"])

        assert len(queued) == 1
        assert queued[0].task_id == "job-task-002"
        assert len(failed) == 1
        assert failed[0].task_id == "job-task-003"
