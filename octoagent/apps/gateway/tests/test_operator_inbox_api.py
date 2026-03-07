from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    EventType,
    OperatorActionKind,
    OperatorActionOutcome,
    OperatorActionSource,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.operator_actions import OperatorActionService
from octoagent.gateway.services.operator_inbox import OperatorInboxService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.policy.approval_manager import ApprovalManager
from octoagent.policy.models import ApprovalRequest
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from octoagent.tooling.models import SideEffectLevel


@pytest_asyncio.fixture
async def operator_app(tmp_path: Path):
    os.environ["OCTOAGENT_DB_PATH"] = str(tmp_path / "data" / "sqlite" / "test.db")
    os.environ["OCTOAGENT_ARTIFACTS_DIR"] = str(tmp_path / "data" / "artifacts")
    os.environ["OCTOAGENT_PROJECT_ROOT"] = str(tmp_path)
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"

    from octoagent.gateway.main import create_app

    app = create_app()
    store_group = await create_store_group(
        str(tmp_path / "data" / "sqlite" / "test.db"),
        tmp_path / "data" / "artifacts",
    )
    approval_manager = ApprovalManager(event_store=store_group.event_store)
    state_store = TelegramStateStore(tmp_path)
    app.state.store_group = store_group
    app.state.sse_hub = SSEHub()
    app.state.approval_manager = approval_manager
    app.state.telegram_state_store = state_store
    app.state.operator_inbox_service = OperatorInboxService(
        store_group=store_group,
        approval_manager=approval_manager,
        telegram_state_store=state_store,
    )
    app.state.operator_action_service = OperatorActionService(
        store_group=store_group,
        sse_hub=app.state.sse_hub,
        approval_manager=approval_manager,
        telegram_state_store=state_store,
    )

    yield app, store_group, approval_manager, state_store

    await store_group.conn.close()
    for key in [
        "OCTOAGENT_DB_PATH",
        "OCTOAGENT_ARTIFACTS_DIR",
        "OCTOAGENT_PROJECT_ROOT",
        "LOGFIRE_SEND_TO_LOGFIRE",
    ]:
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def client(operator_app) -> AsyncClient:
    app, *_ = operator_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def _seed_approval(store_group, approval_manager) -> str:
    task_service = TaskService(store_group, SSEHub())
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="thread-approval",
            scope_id="scope-approval",
            sender_id="owner",
            sender_name="Owner",
            text="approval request",
            idempotency_key="operator-inbox-api-approval",
        )
    )
    assert created is True
    await approval_manager.register(
        ApprovalRequest(
            approval_id="ap-001",
            task_id=task_id,
            tool_name="filesystem.write",
            tool_args_summary="echo hello",
            risk_explanation="需要人工确认",
            policy_label="global.irreversible",
            side_effect_level=SideEffectLevel.IRREVERSIBLE,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )
    )
    return task_id


class TestOperatorInboxApi:
    async def test_get_operator_inbox(self, client: AsyncClient, operator_app) -> None:
        _, store_group, approval_manager, state_store = operator_app
        await _seed_approval(store_group, approval_manager)
        state_store.ensure_pairing_request(
            user_id="1001",
            chat_id="1001",
            username="guest",
            display_name="Guest",
            last_message_text="hello",
        )

        resp = await client.get("/api/operator/inbox")

        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_pending"] == 2
        assert {item["kind"] for item in data["items"]} == {"approval", "pairing_request"}

    async def test_post_operator_action_returns_structured_result(
        self,
        client: AsyncClient,
        operator_app,
    ) -> None:
        _, store_group, approval_manager, _ = operator_app
        task_id = await _seed_approval(store_group, approval_manager)

        resp = await client.post(
            "/api/operator/actions",
            json={
                "item_id": "approval:ap-001",
                "kind": OperatorActionKind.APPROVE_ONCE.value,
                "source": OperatorActionSource.WEB.value,
                "actor_id": "user:web",
                "actor_label": "owner",
            },
        )

        events = await store_group.event_store.get_events_for_task(task_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["outcome"] == OperatorActionOutcome.SUCCEEDED.value
        assert data["audit_event_id"] == events[-1].event_id
        assert events[-1].type == EventType.OPERATOR_ACTION_RECORDED
