"""Feature 010 集成测试：恢复幂等 + API 错误码语义"""

from datetime import UTC, datetime

from httpx import AsyncClient
from octoagent.core.models import CheckpointSnapshot, CheckpointStatus, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.task_service import TaskService


class CountingLLMService:
    """统计调用次数的 LLMService 包装器。"""

    def __init__(self) -> None:
        self.calls = 0
        self._delegate = LLMService()

    async def call(self, prompt_or_messages, model_alias=None):
        self.calls += 1
        return await self._delegate.call(prompt_or_messages, model_alias=model_alias)


class TestFeature010CheckpointResume:
    async def test_recovery_replays_no_side_effect(self, integration_app) -> None:
        sg = integration_app.state.store_group
        service = TaskService(sg, integration_app.state.sse_hub)

        msg = NormalizedMessage(
            text="Feature010 resume idempotency",
            idempotency_key="f010-idem-001",
        )
        task_id, _ = await service.create_task(msg)
        await service._write_state_transition(
            task_id,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_id}",
        )

        llm = CountingLLMService()

        # 第一次恢复执行：写入 ledger + artifact + success
        await service.process_task_with_llm(
            task_id=task_id,
            user_text=msg.text,
            llm_service=llm,
            model_alias="main",
            resume_from_node="model_call_started",
            resume_state_snapshot={"next_node": "response_persisted"},
        )
        assert llm.calls == 1

        # 模拟再次恢复：任务回到 RUNNING，resume 仍从 model_call_started
        await sg.conn.execute(
            "UPDATE tasks SET status = 'RUNNING' WHERE task_id = ?",
            (task_id,),
        )
        await sg.conn.commit()
        await service.process_task_with_llm(
            task_id=task_id,
            user_text=msg.text,
            llm_service=llm,
            model_alias="main",
            resume_from_node="model_call_started",
            resume_state_snapshot={"next_node": "response_persisted"},
        )

        # 第二次恢复应复用已存在 artifact，不再触发 LLM 调用
        assert llm.calls == 1
        artifacts = await sg.artifact_store.list_artifacts_for_task(task_id)
        assert len(artifacts) == 1

        ledger_entry = await sg.side_effect_ledger_store.get_entry(f"{task_id}:llm_call:main")
        assert ledger_entry is not None
        assert ledger_entry.result_ref == artifacts[0].artifact_id

    async def test_resume_api_error_code_semantics(
        self,
        client: AsyncClient,
        integration_app,
    ) -> None:
        sg = integration_app.state.store_group
        service = TaskService(sg, integration_app.state.sse_hub)

        # 404: task 不存在
        resp = await client.post("/api/tasks/01NONEXISTENT0000000000000/resume")
        assert resp.status_code == 404

        # 422: 无 checkpoint
        msg_422 = NormalizedMessage(
            text="resume 422",
            idempotency_key="f010-resume-422",
        )
        task_422, _ = await service.create_task(msg_422)
        await service._write_state_transition(
            task_422,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_422}",
        )
        resp = await client.post(f"/api/tasks/{task_422}/resume")
        assert resp.status_code == 422
        assert resp.json()["error"]["failure_type"] == "dependency_missing"

        # 409: 终态任务
        msg_409 = NormalizedMessage(
            text="resume 409",
            idempotency_key="f010-resume-409",
        )
        task_409, _ = await service.create_task(msg_409)
        await service._write_state_transition(
            task_409,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_409}",
        )
        await service._write_state_transition(
            task_409,
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            f"trace-{task_409}",
        )
        resp = await client.post(f"/api/tasks/{task_409}/resume")
        assert resp.status_code == 409
        assert resp.json()["error"]["failure_type"] == "terminal_task"

        # 200: 有可恢复 checkpoint
        msg_200 = NormalizedMessage(
            text="resume 200",
            idempotency_key="f010-resume-200",
        )
        task_200, _ = await service.create_task(msg_200)
        await service._write_state_transition(
            task_200,
            TaskStatus.CREATED,
            TaskStatus.RUNNING,
            f"trace-{task_200}",
        )
        cp = CheckpointSnapshot(
            checkpoint_id="cp-f010-resume-200",
            task_id=task_200,
            node_id="model_call_started",
            status=CheckpointStatus.SUCCESS,
            schema_version=1,
            state_snapshot={"next_node": "response_persisted"},
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await sg.checkpoint_store.save_checkpoint(cp)
        await sg.conn.commit()

        resp = await client.post(f"/api/tasks/{task_200}/resume")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["checkpoint_id"] == "cp-f010-resume-200"
        assert body["resumed_from_node"] == "model_call_started"
