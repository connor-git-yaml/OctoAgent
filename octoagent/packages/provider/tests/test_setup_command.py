from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner
from octoagent.provider.dx.cli import main


def test_setup_command_runs_quick_connect_flow(tmp_path: Path, monkeypatch) -> None:
    import octoagent.provider.dx.setup_governance_adapter as adapter_module

    class FakeAdapter:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        async def prepare_wizard_draft(self, draft):
            return dict(draft)

        async def quick_connect(self, draft):
            assert draft["config"]["providers"][0]["id"] == "openrouter"
            assert draft["secret_values"]["OPENROUTER_API_KEY"] == "sk-provider-value"
            assert draft["secret_values"]["LITELLM_MASTER_KEY"] == "sk-master-value"
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

    monkeypatch.setattr(adapter_module, "LocalSetupGovernanceAdapter", FakeAdapter)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--provider", "openrouter", "--skip-live-verify"],
        input="\n\nsk-provider-value\nsk-master-value\n",
        env={"OCTOAGENT_PROJECT_ROOT": str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "Runtime Activation" in result.output
    assert "managed_restart_completed" in result.output
