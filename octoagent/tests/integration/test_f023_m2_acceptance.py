from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    EventType,
    ManagedRuntimeDescriptor,
    OperatorActionKind,
    OperatorActionSource,
    OrchestratorRequest,
    SecretRefSourceType,
    TaskStatus,
    utc_now,
)
from octoagent.core.models.message import NormalizedMessage
from octoagent.core.store import create_store_group
from octoagent.gateway.services.operator_actions import encode_telegram_operator_action
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_service import TaskService
from octoagent.gateway.services.worker_runtime import WorkerRuntimeConfig
from octoagent.protocol import (
    build_error_message,
    build_result_message,
    build_task_message,
    dispatch_envelope_from_task_message,
)
from octoagent.provider.config import ProviderConfig
from octoagent.provider.dx.channel_verifier import ChannelVerifierRegistry
from octoagent.provider.dx.chat_import_service import ChatImportService
from octoagent.provider.dx.cli import main as provider_cli
from octoagent.provider.dx.doctor import DoctorRunner
from octoagent.provider.dx.models import CheckStatus, DoctorReport
from octoagent.provider.dx.onboarding_models import OnboardingStep
from octoagent.provider.dx.secret_service import SecretService
from octoagent.provider.dx.telegram_verifier import TelegramOnboardingVerifier
from octoagent.provider.dx.update_status_store import UpdateStatusStore
from octoagent.provider.models import ModelCallResult, TokenUsage


class AcceptanceBotClient:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str]] = []
        self.answered_callbacks: list[tuple[str, str, bool]] = []
        self.edited_messages: list[tuple[str, str | int, str]] = []

    async def get_me(self):
        return type(
            "TelegramIdentity",
            (),
            {"id": "1", "username": "octobot"},
        )()

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        **_: object,
    ):
        self.sent_messages.append((str(chat_id), text))
        return type("TelegramSendResult", (), {"message_id": 9001})()

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_s: int,
    ) -> list[dict[str, object]]:
        _ = offset, timeout_s
        return []

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> bool:
        self.answered_callbacks.append((callback_query_id, text, show_alert))
        return True

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: str | int,
        text: str,
        reply_markup=None,
    ):
        _ = reply_markup
        self.edited_messages.append((str(chat_id), message_id, text))
        return type("TelegramEditResult", (), {"message_id": message_id})()


@dataclass
class PassingDoctorRunner:
    report: DoctorReport

    async def run_all_checks(self, live: bool = False) -> DoctorReport:
        assert live is True
        return self.report


class InstantLLMService:
    async def call(self, prompt_or_messages, model_alias: str | None = None) -> ModelCallResult:
        _ = prompt_or_messages
        return ModelCallResult(
            content="acceptance-ok",
            model_alias=model_alias or "main",
            model_name="mock-acceptance",
            provider="mock",
            duration_ms=3,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class SlowLLMService:
    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def call(self, prompt_or_messages, model_alias: str | None = None) -> ModelCallResult:
        _ = prompt_or_messages
        await asyncio.sleep(self._delay_s)
        return ModelCallResult(
            content="acceptance-slow",
            model_alias=model_alias or "main",
            model_name="mock-acceptance-slow",
            provider="mock",
            duration_ms=int(self._delay_s * 1000),
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


def _ready_report() -> DoctorReport:
    return DoctorReport(
        checks=[],
        overall_status=CheckStatus.PASS,
        timestamp=datetime.now(tz=UTC),
    )


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )


def _build_import_rows(now: datetime) -> list[dict[str, object]]:
    return [
        {
            "source_message_id": "m1",
            "source_cursor": "c1",
            "channel": "wechat_import",
            "thread_id": "project-alpha",
            "sender_id": "alice",
            "sender_name": "Alice",
            "timestamp": now.isoformat(),
            "text": "Project Alpha kickoff complete",
        },
        {
            "source_message_id": "m2",
            "source_cursor": "c2",
            "channel": "wechat_import",
            "thread_id": "project-alpha",
            "sender_id": "bob",
            "sender_name": "Bob",
            "timestamp": (now + timedelta(minutes=1)).isoformat(),
            "text": "Project Alpha now in development",
            "fact_hints": [
                {
                    "subject_key": "project.alpha.status",
                    "content": "development",
                    "confidence": 0.9,
                }
            ],
        },
    ]


@pytest.mark.asyncio
async def test_first_use_acceptance_config_gateway_pairing_and_onboarding_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    config_result = runner.invoke(
        provider_cli,
        [
            "config",
            "init",
            "--enable-telegram",
            "--telegram-mode",
            "webhook",
            "--telegram-webhook-url",
            "https://example.com/api/telegram/webhook",
        ],
        input="openrouter\nOpenRouter\nOPENROUTER_API_KEY\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )
    assert config_result.exit_code == 0

    doctor = DoctorRunner(project_root=tmp_path)
    env_check = await doctor.check_env_file()
    litellm_env_check = await doctor.check_env_litellm_file()
    assert env_check.status == CheckStatus.SKIP
    assert litellm_env_check.status == CheckStatus.SKIP

    status_store = UpdateStatusStore(tmp_path)
    status_store.save_runtime_descriptor(
        ManagedRuntimeDescriptor(
            project_root=str(tmp_path),
            start_command=["uv", "run", "uvicorn", "octoagent.gateway.main:app"],
            verify_url="http://127.0.0.1:8000/ready?profile=core",
            workspace_sync_command=["uv", "sync"],
            frontend_build_command=["npm", "run", "build"],
            environment_overrides={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    async def _fake_restart(self, *, trigger_source: str):
        assert trigger_source == "cli"
        return SimpleNamespace(overall_status="SUCCEEDED")

    async def _fake_verify(self, *, trigger_source: str):
        assert trigger_source == "cli"
        return SimpleNamespace(overall_status="SUCCEEDED")

    monkeypatch.setattr(
        "octoagent.provider.dx.secret_service.UpdateService.restart",
        _fake_restart,
    )
    monkeypatch.setattr(
        "octoagent.provider.dx.secret_service.UpdateService.verify",
        _fake_verify,
    )

    secret_service = SecretService(
        tmp_path,
        environ={
            "OPENROUTER_SOURCE": "provider-secret",
            "MASTER_KEY_SOURCE": "master-secret",
            "TELEGRAM_BOT_TOKEN_SOURCE": "test-token",
        },
    )
    await secret_service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_SOURCE"},
        target_keys=["providers.openrouter.api_key_env"],
    )
    await secret_service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "MASTER_KEY_SOURCE"},
        target_keys=["runtime.master_key_env"],
    )
    await secret_service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "TELEGRAM_BOT_TOKEN_SOURCE"},
        target_keys=["channels.telegram.bot_token_env"],
    )
    await secret_service.apply()
    await secret_service.reload()

    bot_client = AcceptanceBotClient()
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "octoagent.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_SOURCE", "test-token")
    monkeypatch.setenv("OPENROUTER_SOURCE", "provider-secret")
    monkeypatch.setenv("MASTER_KEY_SOURCE", "master-secret")

    from octoagent.gateway import main as gateway_main
    from octoagent.provider.dx.onboarding_service import OnboardingService

    monkeypatch.setattr(gateway_main, "TelegramBotClient", lambda _root: bot_client)
    monkeypatch.setattr(
        gateway_main,
        "load_provider_config",
        lambda: ProviderConfig(llm_mode="echo", config_source="env"),
    )

    app: FastAPI = gateway_main.create_app()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
            first = await client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 101,
                    "message": {
                        "message_id": 7,
                        "text": "/start",
                        "chat": {"id": 42, "type": "private"},
                        "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                    },
                },
            )
            assert first.status_code == 202
            assert first.json()["status"] == "pairing_required"

            inbox = await client.get("/api/operator/inbox")
            assert inbox.status_code == 200
            items = inbox.json()["items"]
            assert any(item["item_id"] == "pairing:42" for item in items)

            approve = await client.post(
                "/api/operator/actions",
                json={
                    "item_id": "pairing:42",
                    "kind": OperatorActionKind.APPROVE_PAIRING.value,
                    "source": OperatorActionSource.WEB.value,
                    "actor_id": "user:web",
                    "actor_label": "owner",
                },
            )
            assert approve.status_code == 200
            assert approve.json()["outcome"] == "succeeded"

            accepted = await client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 102,
                    "message": {
                        "message_id": 8,
                        "text": "run acceptance task",
                        "chat": {"id": 42, "type": "private"},
                        "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                    },
                },
            )
            assert accepted.status_code == 200
            payload = accepted.json()
            assert payload["status"] == "accepted"
            accepted_task_id = payload["task_id"]

            verifier = TelegramOnboardingVerifier(
                environ={"TELEGRAM_BOT_TOKEN": "test-token"},
                client_factory=lambda _root: bot_client,
            )
            registry = ChannelVerifierRegistry()
            registry.register(verifier)
            onboarding = OnboardingService(
                tmp_path,
                channel="telegram",
                doctor_factory=lambda _root: PassingDoctorRunner(_ready_report()),
                registry=registry,
            )
            onboarding_result = await onboarding.run()

    assert onboarding_result.exit_code == 0
    assert onboarding_result.session is not None
    assert (
        onboarding_result.session.steps[OnboardingStep.FIRST_MESSAGE].status.value == "completed"
    )
    assert accepted_task_id in onboarding_result.session.steps[OnboardingStep.FIRST_MESSAGE].summary


@pytest.mark.asyncio
async def test_a2a_task_message_can_drive_worker_runtime_and_return_result(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "a2a.db"),
        tmp_path / "artifacts",
    )
    try:
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        message = NormalizedMessage(
            text="a2a acceptance",
            idempotency_key="f023-a2a-001",
        )
        task_id, created = await task_service.create_task(message)
        assert created is True

        from octoagent.gateway.services.orchestrator import LLMWorkerAdapter, SingleWorkerRouter

        request = OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text=message.text,
        )
        envelope = SingleWorkerRouter().route(request)
        task_message = build_task_message(envelope, context_id="f023-a2a")
        restored = dispatch_envelope_from_task_message(task_message)

        worker = LLMWorkerAdapter(
            store_group,
            sse_hub,
            InstantLLMService(),
            runtime_config=WorkerRuntimeConfig(docker_mode="disabled"),
            docker_available_checker=lambda: False,
        )
        result = await worker.handle(restored)
        result_message = build_result_message(
            result,
            context_id=task_message.context_id,
            trace_id=restored.trace_id,
        )

        task = await task_service.get_task(task_id)
        assert restored.task_id == task_id
        assert restored.user_text == "a2a acceptance"
        assert result.status.value == "SUCCEEDED"
        assert result_message.payload.state == "completed"
        assert task is not None
        assert task.status == TaskStatus.SUCCEEDED
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_operator_parity_acceptance_web_and_telegram_share_same_pairing_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    config_result = runner.invoke(
        provider_cli,
        [
            "config",
            "init",
            "--enable-telegram",
            "--telegram-mode",
            "webhook",
            "--telegram-webhook-url",
            "https://example.com/api/telegram/webhook",
        ],
        input="openrouter\nOpenRouter\nOPENROUTER_API_KEY\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )
    assert config_result.exit_code == 0

    bot_client = AcceptanceBotClient()
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("OCTOAGENT_DB_PATH", str(tmp_path / "data" / "sqlite" / "octoagent.db"))
    monkeypatch.setenv("OCTOAGENT_ARTIFACTS_DIR", str(tmp_path / "data" / "artifacts"))
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")

    from octoagent.gateway import main as gateway_main

    monkeypatch.setattr(gateway_main, "TelegramBotClient", lambda _root: bot_client)
    monkeypatch.setattr(
        gateway_main,
        "load_provider_config",
        lambda: ProviderConfig(llm_mode="echo", config_source="env"),
    )

    app: FastAPI = gateway_main.create_app()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
            first = await client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 201,
                    "message": {
                        "message_id": 17,
                        "text": "/start",
                        "chat": {"id": 42, "type": "private"},
                        "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                    },
                },
            )
            assert first.status_code == 202
            assert first.json()["status"] == "pairing_required"

            approve = await client.post(
                "/api/operator/actions",
                json={
                    "item_id": "pairing:42",
                    "kind": OperatorActionKind.APPROVE_PAIRING.value,
                    "source": OperatorActionSource.WEB.value,
                    "actor_id": "user:web",
                    "actor_label": "owner",
                },
            )
            assert approve.status_code == 200
            assert approve.json()["outcome"] == "succeeded"

            callback = await client.post(
                "/api/telegram/webhook",
                json={
                    "update_id": 202,
                    "callback_query": {
                        "id": "cb-acceptance-001",
                        "data": encode_telegram_operator_action(
                            "pairing:42",
                            OperatorActionKind.APPROVE_PAIRING,
                        ),
                        "from": {"id": 42, "username": "owner", "first_name": "Owner"},
                        "message": {
                            "message_id": 17,
                            "text": "pairing card",
                            "chat": {"id": 42, "type": "private"},
                            "from": {"id": 999, "username": "octobot", "first_name": "Octo"},
                        },
                    },
                },
            )
            assert callback.status_code == 200
            assert callback.json()["status"] == "operator_action"
            assert callback.json()["detail"] == "already_handled"
            assert bot_client.answered_callbacks[-1][1] == "已被处理"
            assert "结果: already_handled" in bot_client.edited_messages[-1][2]

            events = await app.state.store_group.event_store.get_events_for_task(
                "ops-operator-inbox"
            )

    audit_events = [
        event
        for event in events
        if event.type == EventType.OPERATOR_ACTION_RECORDED
        and event.payload.get("item_id") == "pairing:42"
    ]
    assert len(audit_events) == 2
    assert [event.payload["source"] for event in audit_events] == ["web", "telegram"]
    assert [event.payload["outcome"] for event in audit_events] == [
        "succeeded",
        "already_handled",
    ]


@pytest.mark.asyncio
async def test_chat_import_memory_backup_restore_acceptance(tmp_path: Path) -> None:
    input_path = tmp_path / "messages.jsonl"
    _write_jsonl(input_path, _build_import_rows(datetime.now(tz=UTC)))

    import_service = ChatImportService(tmp_path)
    report = await import_service.import_chats(input_path=input_path)
    assert report.summary.committed_count == 1

    from octoagent.provider.dx.backup_service import BackupService

    backup_service = BackupService(tmp_path)
    bundle = await backup_service.create_bundle(label="after-chat-import")
    export_manifest = await backup_service.export_chats(task_id="ops-chat-import")
    restore_plan = await backup_service.plan_restore(
        bundle=bundle.output_path,
        target_root=tmp_path / "restore-preview",
    )
    recovery_summary = backup_service.get_recovery_summary()

    assert Path(bundle.output_path).exists()
    assert Path(export_manifest.output_path).exists()
    assert export_manifest.tasks[0].task_id == "ops-chat-import"
    assert restore_plan.compatible is True
    assert recovery_summary.ready_for_restore is True
    assert recovery_summary.latest_backup is not None
    assert recovery_summary.latest_backup.bundle_id == bundle.bundle_id
    assert recovery_summary.latest_recovery_drill is not None
    assert recovery_summary.latest_recovery_drill.bundle_path == bundle.output_path


@pytest.mark.asyncio
async def test_a2a_task_message_timeout_maps_to_error_and_failed_state(
    tmp_path: Path,
) -> None:
    store_group = await create_store_group(
        str(tmp_path / "a2a-timeout.db"),
        tmp_path / "artifacts-timeout",
    )
    try:
        sse_hub = SSEHub()
        task_service = TaskService(store_group, sse_hub)
        message = NormalizedMessage(
            text="a2a timeout acceptance",
            idempotency_key="f023-a2a-timeout-001",
        )
        task_id, created = await task_service.create_task(message)
        assert created is True

        from octoagent.gateway.services.orchestrator import LLMWorkerAdapter, SingleWorkerRouter

        request = OrchestratorRequest(
            task_id=task_id,
            trace_id=f"trace-{task_id}",
            user_text=message.text,
        )
        envelope = SingleWorkerRouter().route(request)
        task_message = build_task_message(envelope, context_id="f023-a2a-timeout")
        restored = dispatch_envelope_from_task_message(task_message)

        worker = LLMWorkerAdapter(
            store_group,
            sse_hub,
            SlowLLMService(delay_s=0.3),
            runtime_config=WorkerRuntimeConfig(
                docker_mode="disabled",
                max_execution_timeout_seconds=0.05,
            ),
            docker_available_checker=lambda: False,
        )
        result = await worker.handle(restored)
        error_message = build_error_message(
            result,
            context_id=task_message.context_id,
            trace_id=restored.trace_id,
        )

        task = await task_service.get_task(task_id)
        assert result.status.value == "FAILED"
        assert result.summary == "worker_runtime_timeout:max_exec"
        assert error_message.payload.state == "failed"
        assert error_message.payload.error_type == "WorkerRuntimeTimeoutError"
        assert task is not None
        assert task.status == TaskStatus.FAILED
    finally:
        await store_group.conn.close()
