from __future__ import annotations

import json
import signal
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient
from octoagent.core.models import (
    ActorType,
    Artifact,
    ArtifactPart,
    Event,
    EventCausality,
    EventType,
    ManagedRuntimeDescriptor,
    NormalizedMessage,
    OrchestratorRequest,
    PartType,
    ProjectBindingType,
    RequesterInfo,
    RuntimeManagementMode,
    RuntimeStateSnapshot,
    SecretRefSourceType,
    Task,
    TaskCreatedPayload,
    UpdateOverallStatus,
    UserMessagePayload,
    utc_now,
)
from octoagent.core.store import create_store_group
from octoagent.core.store.transaction import create_task_with_initial_events
from octoagent.gateway.services.task_service import TaskService
from octoagent.provider.dx.backup_service import BackupService
from octoagent.provider.dx.cli import main as provider_cli
from octoagent.provider.dx.config_schema import (
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
)
from octoagent.provider.dx.config_wizard import save_config
from octoagent.provider.dx.doctor import DoctorRunner
from octoagent.provider.dx.models import CheckLevel, CheckResult, CheckStatus, DoctorReport
from octoagent.provider.dx.project_selector import ProjectSelectorService
from octoagent.provider.dx.recovery_status_store import RecoveryStatusStore
from octoagent.provider.dx.secret_service import SecretService
from octoagent.provider.dx.update_service import UpdateService
from octoagent.provider.dx.update_status_store import UpdateStatusStore
from ulid import ULID


def _configure_runtime_env(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    monkeypatch.setenv("OCTOAGENT_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv(
        "OCTOAGENT_DB_PATH",
        str(project_root / "data" / "sqlite" / "octoagent.db"),
    )
    monkeypatch.setenv(
        "OCTOAGENT_ARTIFACTS_DIR",
        str(project_root / "data" / "artifacts"),
    )
    monkeypatch.setenv("OCTOAGENT_LLM_MODE", "echo")
    monkeypatch.setenv("OCTOAGENT_FRONTDOOR_MODE", "loopback")
    monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "false")


def _write_secret_runtime_config(project_root: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-08",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                )
            ],
            model_aliases={
                "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
                "cheap": ModelAlias(provider="openrouter", model="openrouter/auto"),
            },
            runtime=RuntimeConfig(
                llm_mode="litellm",
                litellm_proxy_url="http://localhost:4000",
                master_key_env="LITELLM_MASTER_KEY",
            ),
        ),
        project_root,
    )


def _write_wechat_export(path: Path, media_root: Path) -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "alpha.txt").write_text("alpha attachment", encoding="utf-8")
    payload = {
        "account": {"label": "Connor"},
        "conversations": [
            {
                "conversation_key": "team-alpha",
                "label": "Team Alpha",
                "messages": [
                    {
                        "id": "wx-1",
                        "cursor": "cursor-1",
                        "sender_id": "alice",
                        "sender_name": "Alice",
                        "timestamp": datetime.now(tz=UTC).isoformat(),
                        "text": "wechat import from Feature 031 acceptance",
                        "attachments": [
                            {
                                "path": "alpha.txt",
                                "filename": "alpha.txt",
                                "mime": "text/plain",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def _create_task(app, *, text: str, thread_id: str) -> str:
    task_service = TaskService(app.state.store_group, app.state.sse_hub)
    task_id, created = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id=thread_id,
            scope_id=f"chat:web:{thread_id}",
            sender_id="owner",
            sender_name="Owner",
            text=text,
            idempotency_key=f"f031:{thread_id}:{text}",
        )
    )
    assert created is True
    return task_id


async def _control_action(
    client: AsyncClient,
    action_id: str,
    params: dict[str, object],
) -> dict[str, object]:
    resp = await client.post(
        "/api/control/actions",
        json={
            "request_id": str(ULID()),
            "action_id": action_id,
            "surface": "web",
            "actor": {"actor_id": "user:web", "actor_label": "Owner"},
            "params": params,
        },
    )
    assert resp.status_code in {200, 202}, resp.text
    return resp.json()["result"]


class _PassingDoctorRunner:
    def __init__(self, report: DoctorReport) -> None:
        self._report = report

    async def run_all_checks(self, live: bool = False) -> DoctorReport:
        _ = live
        return self._report


def _report_with_status(status: CheckStatus) -> DoctorReport:
    return DoctorReport(
        checks=[
            CheckResult(
                name="python_version",
                status=status,
                level=CheckLevel.REQUIRED,
                message="python ok" if status == CheckStatus.PASS else "python fail",
                fix_hint="修复 Python",
            )
        ],
        overall_status=status,
        timestamp=utc_now(),
    )


def _runtime_descriptor(project_root: Path) -> ManagedRuntimeDescriptor:
    now = utc_now()
    return ManagedRuntimeDescriptor(
        project_root=str(project_root),
        runtime_mode=RuntimeManagementMode.MANAGED,
        start_command=["uv", "run", "uvicorn", "octoagent.gateway.main:app"],
        verify_url="http://127.0.0.1:8000/ready?profile=core",
        workspace_sync_command=["uv", "sync"],
        frontend_build_command=["npm", "run", "build"],
        created_at=now,
        updated_at=now,
    )


async def _seed_update_restore_fixture(project_root: Path) -> None:
    (project_root / "octoagent.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (project_root / "litellm-config.yaml").write_text("model_list: []\n", encoding="utf-8")
    (project_root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (project_root / ".env.litellm").write_text("SECRET=1\n", encoding="utf-8")

    store_group = await create_store_group(
        str(project_root / "data" / "sqlite" / "octoagent.db"),
        project_root / "data" / "artifacts",
    )
    now = datetime.now(tz=UTC)
    task_id = "task-031-update"
    task = Task(
        task_id=task_id,
        created_at=now,
        updated_at=now,
        title="hello release backup",
        thread_id="thread-031",
        requester=RequesterInfo(channel="web", sender_id="owner"),
        trace_id="trace-task-031-update",
    )
    events = [
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.USER,
            payload=TaskCreatedPayload(
                title=task.title,
                thread_id=task.thread_id,
                scope_id=task.scope_id,
                channel=task.requester.channel,
                sender_id=task.requester.sender_id,
            ).model_dump(mode="json"),
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key="seed-task-created"),
        ),
        Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=2,
            ts=now,
            type=EventType.USER_MESSAGE,
            actor=ActorType.USER,
            payload=UserMessagePayload(
                text_preview="hello release backup",
                text_length=20,
            ).model_dump(mode="json"),
            trace_id=task.trace_id,
            causality=EventCausality(idempotency_key="seed-user-message"),
        ),
    ]
    await create_task_with_initial_events(
        store_group.conn,
        store_group.task_store,
        store_group.event_store,
        task,
        events,
    )
    artifact = Artifact(
        artifact_id="artifact-031-update",
        task_id=task_id,
        ts=now,
        name="release-output",
        parts=[ArtifactPart(type=PartType.TEXT, mime="text/plain", content="ready")],
        size=0,
        hash="",
    )
    await store_group.artifact_store.put_artifact(artifact, content=b"ready")
    await store_group.conn.commit()
    await store_group.conn.close()


@pytest.mark.asyncio
async def test_m3_first_use_dashboard_and_trust_boundary_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
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
    assert result.exit_code == 0

    doctor = DoctorRunner(project_root=tmp_path)
    assert (await doctor.check_env_file()).status == CheckStatus.SKIP
    assert (await doctor.check_env_litellm_file()).status == CheckStatus.SKIP

    _configure_runtime_env(monkeypatch, tmp_path)

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as local_client:
            snapshot_resp = await local_client.get("/api/control/snapshot")
            assert snapshot_resp.status_code == 200
            snapshot = snapshot_resp.json()
            assert set(snapshot["resources"].keys()) >= {
                "wizard",
                "config",
                "project_selector",
                "sessions",
            }
            assert snapshot["resources"]["project_selector"]["default_project_id"]

            create_resp = await local_client.post(
                "/api/message",
                json={
                    "text": "Feature 031 first chat",
                    "idempotency_key": "f031-first-chat",
                },
            )
            assert create_resp.status_code == 201
            task_id = create_resp.json()["task_id"]

            sessions_resp = await local_client.get("/api/control/resources/sessions")
            assert sessions_resp.status_code == 200
            session_items = sessions_resp.json()["sessions"]
            assert any(item["task_id"] == task_id for item in session_items)

        async with AsyncClient(
            transport=ASGITransport(app=app, client=("203.0.113.10", 12345)),
            base_url="http://test",
        ) as remote_client:
            remote_resp = await remote_client.get("/api/control/snapshot")

        assert remote_resp.status_code == 403
        assert remote_resp.json()["detail"]["code"] == "FRONT_DOOR_LOOPBACK_ONLY"


@pytest.mark.asyncio
async def test_m3_project_isolation_secret_import_and_automation_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_secret_runtime_config(tmp_path)
    _configure_runtime_env(monkeypatch, tmp_path)

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        selector = ProjectSelectorService(
            tmp_path,
            surface="web",
            store_group=app.state.store_group,
        )
        alpha, _, _ = await selector.create_project(name="Alpha", slug="alpha", set_active=False)
        beta, _, _ = await selector.create_project(name="Beta", slug="beta", set_active=False)

        secret_service = SecretService(
            tmp_path,
            store_group=app.state.store_group,
            environ={
                "OPENROUTER_ALPHA": "alpha-secret",
                "MASTER_ALPHA": "alpha-master",
                "OPENROUTER_BETA": "beta-secret",
                "MASTER_BETA": "beta-master",
            },
        )
        for project_ref, provider_env, master_env in [
            (alpha.slug, "OPENROUTER_ALPHA", "MASTER_ALPHA"),
            (beta.slug, "OPENROUTER_BETA", "MASTER_BETA"),
        ]:
            await secret_service.configure(
                project_ref=project_ref,
                source_type=SecretRefSourceType.ENV,
                locator={"env_name": provider_env},
                target_keys=["providers.openrouter.api_key_env"],
            )
            await secret_service.configure(
                project_ref=project_ref,
                source_type=SecretRefSourceType.ENV,
                locator={"env_name": master_env},
                target_keys=["runtime.master_key_env"],
            )
            await secret_service.apply(project_ref=project_ref)
            await secret_service.reload(project_ref=project_ref)

        inspect_alpha = await selector.inspect_project(alpha.slug)
        inspect_beta = await selector.inspect_project(beta.slug)
        assert inspect_alpha.secret_runtime_summary["status"] == "ready"
        assert inspect_beta.secret_runtime_summary["status"] == "ready"

        alpha_bindings = await app.state.store_group.project_store.list_secret_bindings(
            alpha.project_id
        )
        beta_bindings = await app.state.store_group.project_store.list_secret_bindings(
            beta.project_id
        )
        assert {item.ref_locator["env_name"] for item in alpha_bindings} == {
            "OPENROUTER_ALPHA",
            "MASTER_ALPHA",
        }
        assert {item.ref_locator["env_name"] for item in beta_bindings} == {
            "OPENROUTER_BETA",
            "MASTER_BETA",
        }

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            select_alpha = await _control_action(
                client,
                "project.select",
                {"project_id": alpha.project_id},
            )
            assert select_alpha["code"] == "PROJECT_SELECTED"

            export_path = tmp_path / "wechat-export.json"
            media_root = tmp_path / "wechat-media"
            _write_wechat_export(export_path, media_root)

            detect_result = await _control_action(
                client,
                "import.source.detect",
                {
                    "source_type": "wechat",
                    "input_path": str(export_path),
                    "media_root": str(media_root),
                    "format_hint": "json",
                },
            )
            source_id = str(detect_result["data"]["source_id"])
            assert detect_result["data"]["active_project_id"] == alpha.project_id

            mapping_result = await _control_action(
                client,
                "import.mapping.save",
                {"source_id": source_id},
            )
            mapping_id = str(mapping_result["data"]["mapping_id"])

            run_result = await _control_action(
                client,
                "import.run",
                {
                    "source_id": source_id,
                    "mapping_id": mapping_id,
                },
            )
            assert run_result["code"] == "IMPORT_RUN_COMPLETED"
            assert run_result["data"]["active_project_id"] == alpha.project_id
            assert run_result["data"]["memory_effects"]["fragment_count"] >= 1

            alpha_project_bindings = await app.state.store_group.project_store.list_bindings(
                alpha.project_id
            )
            assert any(
                item.binding_type == ProjectBindingType.IMPORT_SCOPE
                for item in alpha_project_bindings
            )

            alpha_job_result = await _control_action(
                client,
                "automation.create",
                {
                    "name": "alpha-diag",
                    "action_id": "diagnostics.refresh",
                    "schedule_kind": "interval",
                    "schedule_expr": "3600",
                    "enabled": True,
                },
            )
            alpha_job_id = str(alpha_job_result["data"]["job_id"])

            automation_resp = await client.get("/api/control/resources/automation")
            assert automation_resp.status_code == 200
            automation_payload = automation_resp.json()
            alpha_job_item = next(
                item for item in automation_payload["jobs"] if item["job"]["job_id"] == alpha_job_id
            )
            assert alpha_job_item["job"]["project_id"] == alpha.project_id

            select_beta = await _control_action(
                client,
                "project.select",
                {"project_id": beta.project_id},
            )
            assert select_beta["code"] == "PROJECT_SELECTED"

            workbench_resp = await client.get("/api/control/resources/import-workbench")
            assert workbench_resp.status_code == 200
            workbench_payload = workbench_resp.json()
            assert workbench_payload["active_project_id"] == beta.project_id

            beta_project_bindings = await app.state.store_group.project_store.list_bindings(
                beta.project_id
            )
            assert not any(
                item.binding_type == ProjectBindingType.IMPORT_SCOPE
                for item in beta_project_bindings
            )

            beta_job_result = await _control_action(
                client,
                "automation.create",
                {
                    "name": "beta-diag",
                    "action_id": "diagnostics.refresh",
                    "schedule_kind": "interval",
                    "schedule_expr": "7200",
                    "enabled": True,
                },
            )
            beta_job_id = str(beta_job_result["data"]["job_id"])

            automation_resp = await client.get("/api/control/resources/automation")
            assert automation_resp.status_code == 200
            beta_job_item = next(
                item
                for item in automation_resp.json()["jobs"]
                if item["job"]["job_id"] == beta_job_id
            )
            assert beta_job_item["job"]["project_id"] == beta.project_id
            assert beta_job_item["job"]["project_id"] != alpha_job_item["job"]["project_id"]


@pytest.mark.asyncio
async def test_m3_project_selection_syncs_delegation_work_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_secret_runtime_config(tmp_path)
    _configure_runtime_env(monkeypatch, tmp_path)

    from octoagent.gateway.main import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        selector = ProjectSelectorService(
            tmp_path,
            surface="web",
            store_group=app.state.store_group,
        )
        alpha, _, _ = await selector.create_project(
            name="Alpha Delegation",
            slug="alpha-delegation",
            set_active=False,
        )
        _, alpha_workspace = await selector.resolve_project(alpha.project_id)
        assert alpha_workspace is not None

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            result = await _control_action(
                client,
                "project.select",
                {"project_id": alpha.project_id},
            )
            assert result["code"] == "PROJECT_SELECTED"

        web_selector = await app.state.store_group.project_store.get_selector_state("web")
        assert web_selector is not None
        assert web_selector.active_project_id == alpha.project_id
        assert web_selector.active_workspace_id == alpha_workspace.workspace_id

        task_id = await _create_task(
            app,
            text="请为 alpha delegation 项目补测试并总结风险",
            thread_id="thread-f031-delegation",
        )
        plan = await app.state.delegation_plane_service.prepare_dispatch(
            OrchestratorRequest(
                task_id=task_id,
                trace_id=f"trace-{task_id}",
                user_text="请为 alpha delegation 项目补测试并总结风险",
                worker_capability="llm_generation",
                metadata={},
            )
        )

        assert plan.work.project_id == alpha.project_id
        assert plan.work.workspace_id == alpha_workspace.workspace_id
        assert plan.work.pipeline_run_id
        assert plan.work.route_reason
        assert plan.tool_selection.selected_tools
        assert plan.dispatch_envelope is not None


@pytest.mark.asyncio
async def test_m3_update_backup_restore_drill_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_update_restore_fixture(tmp_path)
    backup_service = BackupService(tmp_path)
    bundle = await backup_service.create_bundle(label="before-m3-release")

    status_store = UpdateStatusStore(tmp_path)
    descriptor = _runtime_descriptor(tmp_path)
    status_store.save_runtime_descriptor(descriptor)
    status_store.save_runtime_state(
        RuntimeStateSnapshot(
            pid=4321,
            project_root=str(tmp_path),
            started_at=utc_now(),
            heartbeat_at=utc_now(),
            verify_url=descriptor.verify_url,
            management_mode=RuntimeManagementMode.MANAGED,
        )
    )

    commands: list[tuple[list[str], Path]] = []
    launched: list[list[str]] = []
    killed: list[tuple[int, int]] = []
    running_pids = {4321}

    async def fake_get(_self, _url: str):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"status": "ready"}

        return Response()

    def fake_kill(pid: int, sig: int) -> None:
        if sig == signal.SIGTERM:
            killed.append((pid, sig))
            running_pids.discard(pid)
            return
        if sig == 0 and pid in running_pids:
            return
        raise ProcessLookupError

    class DummyPopen:
        def __init__(self, command, **_kwargs) -> None:
            launched.append(command)

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr("octoagent.provider.dx.update_service.httpx.AsyncClient.get", fake_get)
    monkeypatch.setattr("octoagent.provider.dx.update_service.os.kill", fake_kill)
    monkeypatch.setattr("octoagent.provider.dx.update_service.subprocess.Popen", DummyPopen)

    service = UpdateService(
        tmp_path,
        status_store=status_store,
        doctor_factory=lambda _root: _PassingDoctorRunner(_report_with_status(CheckStatus.PASS)),
        command_runner=lambda command, cwd: commands.append((command, cwd)) or "ok",
    )

    preview = await service.preview(trigger_source="cli")
    assert preview.overall_status == UpdateOverallStatus.SUCCEEDED

    summary = await service.apply(trigger_source="cli", wait=True)
    assert summary.overall_status == UpdateOverallStatus.SUCCEEDED

    restore_plan = await backup_service.plan_restore(
        bundle=bundle.output_path,
        target_root=tmp_path / "restore-preview",
    )
    recovery_summary = RecoveryStatusStore(tmp_path).load_summary()
    latest_backup = RecoveryStatusStore(tmp_path).load_latest_backup()

    assert latest_backup is not None
    assert latest_backup.bundle_id == bundle.bundle_id
    assert restore_plan.compatible is True
    assert recovery_summary.ready_for_restore is True
    assert commands[0][0] == ["uv", "sync"]
    assert killed == [(4321, signal.SIGTERM)]
    assert launched
