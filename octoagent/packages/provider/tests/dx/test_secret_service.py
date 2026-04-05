from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from octoagent.core.models import ManagedRuntimeDescriptor, SecretRefSourceType, utc_now
from octoagent.gateway.services.config.config_schema import (
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.provider.dx.project_selector import ProjectSelectorService
from octoagent.provider.dx.secret_service import SecretService
from octoagent.provider.dx.update_status_store import UpdateStatusStore


def _write_secret_test_config(project_root: Path) -> None:
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


async def test_secret_service_configure_audit_apply_and_unmanaged_reload(tmp_path: Path) -> None:
    _write_secret_test_config(tmp_path)
    service = SecretService(
        tmp_path,
        environ={
            "OPENROUTER_SOURCE": "provider-secret",
            "MASTER_KEY_SOURCE": "master-secret",
        },
    )

    provider_summary = await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_SOURCE"},
        target_keys=["providers.openrouter.api_key_env"],
    )
    runtime_summary = await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "MASTER_KEY_SOURCE"},
        target_keys=["runtime.master_key_env"],
    )

    assert provider_summary.configured_targets == ["providers.openrouter.api_key_env"]
    assert runtime_summary.configured_targets == ["runtime.master_key_env"]

    report = await service.audit()
    assert report.missing_targets == []
    assert report.overall_status == "action_required"
    assert report.reload_required is True

    dry_run = await service.apply(dry_run=True)
    assert dry_run.status == "dry_run"
    assert dry_run.applied_binding_ids == []
    assert dry_run.materialization_summary["resolved_env_names"] == [
        "LITELLM_MASTER_KEY",
        "OPENROUTER_API_KEY",
    ]

    applied = await service.apply()
    assert applied.status == "applied"
    assert len(applied.applied_binding_ids) == 2
    assert applied.reload_required is True

    reloaded = await service.reload()
    assert reloaded.overall_status == "action_required"
    assert reloaded.materialization.delivery_mode == "unmanaged_manual"
    assert reloaded.materialization.resolved_env_names == [
        "LITELLM_MASTER_KEY",
        "OPENROUTER_API_KEY",
    ]

    post_reload_report = await service.audit()
    assert post_reload_report.overall_status == "ready"
    assert post_reload_report.reload_required is False


async def test_secret_service_reload_managed_runtime_and_redaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_secret_test_config(tmp_path)
    secret_value = "super-secret-value"
    service = SecretService(
        tmp_path,
        environ={
            "OPENROUTER_SOURCE": secret_value,
            "MASTER_KEY_SOURCE": "master-secret",
        },
    )

    await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_SOURCE"},
        target_keys=["providers.openrouter.api_key_env"],
    )
    await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "MASTER_KEY_SOURCE"},
        target_keys=["runtime.master_key_env"],
    )
    await service.apply()

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

    result = await service.reload()
    assert result.overall_status == "completed"
    assert result.materialization.delivery_mode == "managed_restart_verify"
    assert result.materialization.resolved_targets == [
        "providers.openrouter.api_key_env",
        "runtime.master_key_env",
    ]

    apply_payload = json.loads(
        (
            tmp_path
            / "data"
            / "ops"
            / "projects"
            / "project-default"
            / "secret-apply.json"
        ).read_text()
    )
    materialization_payload = json.loads(
        (
            tmp_path
            / "data"
            / "ops"
            / "projects"
            / "project-default"
            / "secret-materialization.json"
        ).read_text()
    )
    assert secret_value not in json.dumps(apply_payload, ensure_ascii=False)
    assert secret_value not in json.dumps(materialization_payload, ensure_ascii=False)
    assert "OPENROUTER_API_KEY" in materialization_payload["resolved_env_names"]


async def test_secret_service_rotate_requires_reapply(tmp_path: Path) -> None:
    _write_secret_test_config(tmp_path)
    service = SecretService(
        tmp_path,
        environ={
            "OPENROUTER_SOURCE": "provider-secret",
            "OPENROUTER_ROTATED": "provider-secret-rotated",
            "MASTER_KEY_SOURCE": "master-secret",
        },
    )

    await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_SOURCE"},
        target_keys=["providers.openrouter.api_key_env"],
    )
    await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "MASTER_KEY_SOURCE"},
        target_keys=["runtime.master_key_env"],
    )
    await service.apply()
    await service.reload()

    rotated = await service.rotate(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_ROTATED"},
        target_keys=["providers.openrouter.api_key_env"],
    )

    assert rotated.configured_targets == ["providers.openrouter.api_key_env"]
    report = await service.audit()
    assert report.overall_status == "action_required"
    assert report.reload_required is True


async def test_secret_service_skips_disabled_provider_targets(tmp_path: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-03-08",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=True,
                ),
                ProviderEntry(
                    id="disabled-provider",
                    name="Disabled",
                    auth_type="api_key",
                    api_key_env="DISABLED_API_KEY",
                    enabled=False,
                ),
            ],
            model_aliases={
                "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
                "cheap": ModelAlias(provider="openrouter", model="openrouter/auto"),
            },
            runtime=RuntimeConfig(
                llm_mode="echo",
                litellm_proxy_url="http://localhost:4000",
                master_key_env="LITELLM_MASTER_KEY",
            ),
        ),
        tmp_path,
    )
    service = SecretService(
        tmp_path,
        environ={"OPENROUTER_SOURCE": "provider-secret"},
    )

    summary = await service.configure(
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_SOURCE"},
    )

    assert summary.configured_targets == ["providers.openrouter.api_key_env"]
    report = await service.audit()
    assert "providers.disabled-provider.api_key_env" not in report.missing_targets


async def test_secret_service_warning_only_bridge_reports_ready(tmp_path: Path) -> None:
    _write_secret_test_config(tmp_path)
    (tmp_path / ".env.litellm").write_text(
        "OPENROUTER_API_KEY=provider-secret\nLITELLM_MASTER_KEY=master-secret\n",
        encoding="utf-8",
    )
    service = SecretService(tmp_path, environ={})

    report = await service.audit()

    assert report.overall_status == "ready"
    assert report.missing_targets == []
    assert report.reload_required is False
    assert len(report.warnings) == 2


async def test_project_inspect_uses_project_scoped_secret_status(tmp_path: Path) -> None:
    _write_secret_test_config(tmp_path)
    selector = ProjectSelectorService(tmp_path)
    alpha, _, _ = await selector.create_project(name="Alpha", slug="alpha", set_active=False)
    beta, _, _ = await selector.create_project(name="Beta", slug="beta", set_active=False)
    service = SecretService(
        tmp_path,
        environ={
            "OPENROUTER_ALPHA": "alpha-secret",
            "MASTER_ALPHA": "alpha-master",
            "OPENROUTER_BETA": "beta-secret",
            "MASTER_BETA": "beta-master",
        },
    )

    await service.configure(
        project_ref=alpha.slug,
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_ALPHA"},
        target_keys=["providers.openrouter.api_key_env"],
    )
    await service.configure(
        project_ref=alpha.slug,
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "MASTER_ALPHA"},
        target_keys=["runtime.master_key_env"],
    )
    await service.apply(project_ref=alpha.slug)

    await service.configure(
        project_ref=beta.slug,
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "OPENROUTER_BETA"},
        target_keys=["providers.openrouter.api_key_env"],
    )
    await service.configure(
        project_ref=beta.slug,
        source_type=SecretRefSourceType.ENV,
        locator={"env_name": "MASTER_BETA"},
        target_keys=["runtime.master_key_env"],
    )
    await service.apply(project_ref=beta.slug)
    await service.reload(project_ref=beta.slug)

    inspect_alpha = await selector.inspect_project(alpha.slug)
    inspect_beta = await selector.inspect_project(beta.slug)

    assert inspect_alpha.secret_runtime_summary["status"] == "action_required"
    assert inspect_beta.secret_runtime_summary["status"] == "ready"
    assert (
        tmp_path / "data" / "ops" / "projects" / alpha.project_id / "secret-apply.json"
    ).exists()
    assert (
        tmp_path / "data" / "ops" / "projects" / beta.project_id / "secret-materialization.json"
    ).exists()
