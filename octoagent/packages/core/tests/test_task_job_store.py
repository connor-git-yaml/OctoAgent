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

    async def test_create_job_requeues_terminal_job(self, core_db):
        await _create_task(core_db, "job-task-004")
        store = SqliteTaskJobStore(core_db)

        created = await store.create_job("job-task-004", "first")
        assert created is True
        await store.mark_running("job-task-004")
        await store.mark_succeeded("job-task-004")

        requeued = await store.create_job("job-task-004", "second", model_alias="main")
        assert requeued is True

        job = await store.get_job("job-task-004")
        assert job is not None
        assert job.status == "QUEUED"
        assert job.user_text == "second"
        assert job.model_alias == "main"
        assert job.started_at is None
        assert job.finished_at is None

    async def test_create_job_noop_on_live_job(self, core_db):
        """D17a 双执行安全底座：create_job 对仍在 QUEUED/RUNNING 的 job 是 no-op
        （返 False，不重置内容/不重入队）。

        这是平台 duplicate 重投补 enqueue（telegram/slack/discord `_maybe_enqueue`
        的 CREATED 守卫之外）的最后一道幂等保证——live job 不会被二次启动。
        与 test_create_job_requeues_terminal_job（终态可重入队）互补，覆盖
        UPDATE 的 `status IN (终态)` 条件在非终态下不命中的分支。
        """
        await _create_task(core_db, "job-task-005")
        store = SqliteTaskJobStore(core_db)

        assert (
            await store.create_job("job-task-005", "first", model_alias="main") is True
        )

        # QUEUED 时重复 create_job → no-op（返 False），内容不被覆盖
        requeued = await store.create_job("job-task-005", "second", model_alias="alt")
        assert requeued is False
        job = await store.get_job("job-task-005")
        assert job is not None
        assert job.status == "QUEUED"
        assert job.user_text == "first"
        assert job.model_alias == "main"

        # RUNNING 时重复 create_job → no-op（返 False），不重置 started_at
        assert await store.mark_running("job-task-005") is True
        running = await store.get_job("job-task-005")
        assert running is not None and running.status == "RUNNING"
        started_at = running.started_at

        again = await store.create_job("job-task-005", "third")
        assert again is False
        job2 = await store.get_job("job-task-005")
        assert job2 is not None
        assert job2.status == "RUNNING"
        assert job2.user_text == "first"
        assert job2.started_at == started_at
