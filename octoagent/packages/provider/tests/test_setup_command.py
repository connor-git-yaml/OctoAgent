from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
from octoagent.core.models import ControlPlaneActionStatus
from octoagent.provider.dx.cli import main


def test_setup_command_runs_quick_connect_flow(tmp_path: Path, monkeypatch) -> None:
    import octoagent.provider.dx.cli as cli_module
    import octoagent.provider.dx.setup_governance_adapter as adapter_module

    class FakeAdapter:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        async def prepare_wizard_draft(self, draft):
            return dict(draft)

        async def quick_connect(self, draft):
            assert draft["config"]["providers"][0]["id"] == "openrouter"
            assert draft["secret_values"]["OPENROUTER_API_KEY"] == "sk-provider-value"
            assert draft["secret_values"]["LITELLM_MASTER_KEY"] == "sk-local-test-key"
            return SimpleNamespace(
                data={
                    "review": {
                        "ready": True,
                        "risk_level": "low",
                        "blocking_reasons": [],
                        "next_actions": [],
                    },
                    "activation": {
                        "proxy_url": "http://localhost:4000",
                        "source_root": str(self.project_root / "app" / "octoagent"),
                        "runtime_reload_mode": "managed_restart_completed",
                        "runtime_reload_message": "已自动重启托管实例。",
                    },
                }
            )

        async def connect_openai_codex_oauth(self, **_kwargs):
            raise AssertionError("openrouter flow 不应触发 OAuth")

    monkeypatch.setattr(cli_module, "_generate_local_proxy_key", lambda: "sk-local-test-key")
    monkeypatch.setattr(adapter_module, "LocalSetupGovernanceAdapter", FakeAdapter)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--provider", "openrouter", "--skip-live-verify"],
        input="\n\nsk-provider-value\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "已自动生成本地 LiteLLM Proxy Key" in result.output
    assert "Runtime Activation" in result.output
    assert "managed_restart_completed" in result.output


def test_setup_command_fails_when_quick_connect_rejected(tmp_path: Path, monkeypatch) -> None:
    import octoagent.provider.dx.setup_governance_adapter as adapter_module

    class FakeAdapter:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        async def prepare_wizard_draft(self, draft):
            return dict(draft)

        async def quick_connect(self, draft):
            return SimpleNamespace(
                status=ControlPlaneActionStatus.REJECTED,
                code="ACTION_EXECUTION_FAILED",
                message="no such table: memory_sync_backlog",
                data={},
            )

        async def connect_openai_codex_oauth(self, **_kwargs):
            return SimpleNamespace(
                status=ControlPlaneActionStatus.COMPLETED,
                code="OPENAI_OAUTH_CONNECTED",
                message="ok",
                data={},
            )

    monkeypatch.setattr(adapter_module, "LocalSetupGovernanceAdapter", FakeAdapter)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "setup",
            "--provider",
            "openai-codex",
            "--master-key",
            "sk-local-test-key",
            "--skip-live-verify",
        ],
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 1
    assert "setup.quick_connect 失败" in result.output
    assert "memory_sync_backlog" in result.output


def test_setup_command_supports_custom_provider_with_base_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import octoagent.provider.dx.cli as cli_module
    import octoagent.provider.dx.setup_governance_adapter as adapter_module

    class FakeAdapter:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        async def prepare_wizard_draft(self, draft):
            return dict(draft)

        async def quick_connect(self, draft):
            provider = draft["config"]["providers"][0]
            assert provider["id"] == "siliconflow"
            assert provider["base_url"] == "https://api.siliconflow.cn/v1"
            assert draft["config"]["model_aliases"]["main"]["model"] == "Qwen/Qwen3-32B"
            assert draft["config"]["model_aliases"]["cheap"]["model"] == "Qwen/Qwen3-14B"
            assert draft["secret_values"]["SILICONFLOW_API_KEY"] == "sk-siliconflow"
            assert draft["secret_values"]["LITELLM_MASTER_KEY"] == "sk-local-test-key"
            return SimpleNamespace(
                data={
                    "review": {
                        "ready": True,
                        "risk_level": "low",
                        "blocking_reasons": [],
                        "next_actions": [],
                    },
                    "activation": {
                        "proxy_url": "http://localhost:4000",
                        "source_root": str(self.project_root / "app" / "octoagent"),
                        "runtime_reload_mode": "managed_restart_completed",
                        "runtime_reload_message": "已自动重启托管实例。",
                    },
                }
            )

        async def connect_openai_codex_oauth(self, **_kwargs):
            raise AssertionError("custom provider flow 不应触发 OAuth")

    monkeypatch.setattr(cli_module, "_generate_local_proxy_key", lambda: "sk-local-test-key")
    monkeypatch.setattr(adapter_module, "LocalSetupGovernanceAdapter", FakeAdapter)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "setup",
            "--provider",
            "custom",
            "--provider-id",
            "siliconflow",
            "--provider-name",
            "SiliconFlow",
            "--api-key-env",
            "SILICONFLOW_API_KEY",
            "--base-url",
            "https://api.siliconflow.cn/v1",
            "--main-model",
            "Qwen/Qwen3-32B",
            "--cheap-model",
            "Qwen/Qwen3-14B",
            "--skip-live-verify",
        ],
        input="sk-siliconflow\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "Runtime Activation" in result.output
