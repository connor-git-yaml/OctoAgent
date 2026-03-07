"""Feature 019: execution API 测试。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from octoagent.core.models import TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.main import create_app
from octoagent.gateway.services.execution_context import get_current_execution_context
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalDecision
from octoagent.provider.models import ModelCallResult, TokenUsage


class InteractiveLLMService:
    """通过 execution context 驱动输入请求的测试 LLM。"""

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


@asynccontextmanager
async def execution_test_app(
    tmp_path: Path,
    llm_service,
) -> AsyncIterator[tuple]:
    """构造手动初始化的 gateway app。"""

    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
    app = create_app()
    store_group = await create_store_group(
        str(tmp_path / "execution-api.db"),
        str(tmp_path / "artifacts"),
    )
    sse_hub = SSEHub()
    approval_manager = ApprovalManager(event_store=store_group.event_store)
    runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=llm_service,
        approval_manager=approval_manager,
        timeout_seconds=60,
        monitor_interval_seconds=0.05,
        docker_available_checker=lambda: True,
    )
    await runner.startup()

    app.state.store_group = store_group
    app.state.sse_hub = sse_hub
    app.state.llm_service = llm_service
    app.state.approval_manager = approval_manager
    app.state.task_runner = runner
    app.state.execution_console = runner.execution_console

    try:
        yield app, TaskService(store_group, sse_hub), runner, approval_manager
    finally:
        await runner.shutdown()
        await store_group.conn.close()
        os.environ.pop("LOGFIRE_SEND_TO_LOGFIRE", None)


async def _wait_for_task_status(
    task_service: TaskService,
    task_id: str,
    expected: TaskStatus,
    *,
    attempts: int = 40,
) -> None:
    for _ in range(attempts):
        task = await task_service.get_task(task_id)
        if task is not None and task.status == expected:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} did not reach {expected}")


async def _create_waiting_input_task(
    task_service: TaskService,
    runner: TaskRunner,
    *,
    key: str,
) -> str:
    msg = NormalizedMessage(text="execution api", idempotency_key=key)
    task_id, created = await task_service.create_task(msg)
    assert created is True
    await runner.enqueue(task_id, msg.text)
    await _wait_for_task_status(task_service, task_id, TaskStatus.WAITING_INPUT)
    return task_id


class TestExecutionApi:
    async def test_get_execution_session_and_events(self, tmp_path: Path) -> None:
        async with execution_test_app(
            tmp_path,
            InteractiveLLMService(),
        ) as (app, task_service, runner, _):
            task_id = await _create_waiting_input_task(
                task_service,
                runner,
                key="execution-api-session-001",
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                session_resp = await client.get(f"/api/tasks/{task_id}/execution")
                assert session_resp.status_code == 200
                session = session_resp.json()["session"]
                assert session["task_id"] == task_id
                assert session["state"] == "WAITING_INPUT"
                assert session["backend"] == "docker"
                assert session["can_attach_input"] is True

                events_resp = await client.get(f"/api/tasks/{task_id}/execution/events")
                assert events_resp.status_code == 200
                body = events_resp.json()
                assert body["session_id"] == session["session_id"]
                kinds = [event["kind"] for event in body["events"]]
                assert "status" in kinds
                assert "step" in kinds
                assert "input_requested" in kinds

    async def test_attach_input_route_resumes_live_task(self, tmp_path: Path) -> None:
        async with execution_test_app(
            tmp_path,
            InteractiveLLMService(),
        ) as (app, task_service, runner, _):
            task_id = await _create_waiting_input_task(
                task_service,
                runner,
                key="execution-api-attach-001",
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                attach_resp = await client.post(
                    f"/api/tasks/{task_id}/execution/input",
                    json={"text": "api-confirmed"},
                )
                assert attach_resp.status_code == 200
                result = attach_resp.json()["result"]
                assert result["task_id"] == task_id
                assert result["delivered_live"] is True

                await _wait_for_task_status(task_service, task_id, TaskStatus.SUCCEEDED)

                session_resp = await client.get(f"/api/tasks/{task_id}/execution")
                assert session_resp.status_code == 200
                assert session_resp.json()["session"]["state"] == "SUCCEEDED"

                events_resp = await client.get(f"/api/tasks/{task_id}/execution/events")
                assert events_resp.status_code == 200
                kinds = [event["kind"] for event in events_resp.json()["events"]]
                assert "input_attached" in kinds
                assert "artifact" in kinds

    async def test_attach_input_route_requires_approval(self, tmp_path: Path) -> None:
        async with execution_test_app(
            tmp_path,
            InteractiveLLMService(approval_required=True),
        ) as (app, task_service, runner, approval_manager):
            task_id = await _create_waiting_input_task(
                task_service,
                runner,
                key="execution-api-approval-001",
            )
            session = await runner.get_execution_session(task_id)
            assert session is not None
            approval_id = session.pending_approval_id
            assert approval_id is not None

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                forbidden_resp = await client.post(
                    f"/api/tasks/{task_id}/execution/input",
                    json={"text": "forbidden-first"},
                )
                assert forbidden_resp.status_code == 403
                assert forbidden_resp.json()["error"]["code"] == "INPUT_APPROVAL_REQUIRED"
                assert forbidden_resp.json()["error"]["approval_id"] == approval_id

                resolved = await approval_manager.resolve(
                    approval_id,
                    ApprovalDecision.ALLOW_ONCE,
                )
                assert resolved is True

                attach_resp = await client.post(
                    f"/api/tasks/{task_id}/execution/input",
                    json={"text": "approved-input", "approval_id": approval_id},
                )
                assert attach_resp.status_code == 200

                await _wait_for_task_status(task_service, task_id, TaskStatus.SUCCEEDED)

    async def test_attach_input_route_returns_conflict_when_not_waiting(
        self,
        tmp_path: Path,
    ) -> None:
        async with execution_test_app(
            tmp_path,
            InteractiveLLMService(),
        ) as (app, task_service, _, _):
            msg = NormalizedMessage(
                text="not waiting yet",
                idempotency_key="execution-api-conflict-001",
            )
            task_id, created = await task_service.create_task(msg)
            assert created is True

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.post(
                    f"/api/tasks/{task_id}/execution/input",
                    json={"text": "too-early"},
                )
                assert resp.status_code == 409
                assert resp.json()["error"]["code"] == "TASK_NOT_WAITING_INPUT"

    async def test_get_execution_session_returns_404_for_missing_task(
        self,
        tmp_path: Path,
    ) -> None:
        async with execution_test_app(
            tmp_path,
            InteractiveLLMService(),
        ) as (app, _, _, _), AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/tasks/01NONEXISTENT0000000000000/execution")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "TASK_NOT_FOUND"
