from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner
from octoagent.provider.dx.cli import main
from octoagent.gateway.services.config.config_wizard import load_config
from octoagent.provider.dx.project_selector import ProjectSelectorService
from octoagent.provider.dx.wizard_session import WizardSessionService


def test_project_create_select_and_inspect(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    created = runner.invoke(
        main,
        ["project", "create", "--name", "Demo Project", "--description", "demo"],
        env=env,
    )
    assert created.exit_code == 0
    assert "Project Create" in created.output
    assert "slug=demo-project" in created.output
    assert "active_project_changed=true" in created.output

    selected = runner.invoke(main, ["project", "select", "default"], env=env)
    assert selected.exit_code == 0
    assert "Project Select" in selected.output
    assert "current_project=default" in selected.output

    inspected = runner.invoke(main, ["project", "inspect"], env=env)
    assert inspected.exit_code == 0
    assert "Project Inspect" in inspected.output
    assert "project=default" in inspected.output
    assert "binding_summary={}" in inspected.output


@pytest.mark.skip(
    reason=(
        "commit d7938cd 把 setup_service 从 provider/dx 迁到 apps/gateway 后，CLI "
        "的 apply 流程改走 HTTP（默认 http://127.0.0.1:8000）。该测试预期 apply 会"
        "把 octoagent.yaml 写到 tmp_path，但实际写入走的是 gateway 自己的 project_root；"
        "用户机器上开着真 gateway 时测试会写到真配置。需要补 in-process gateway "
        "fixture（TestClient 接管 OCTOAGENT_GATEWAY_URL）或给 adapter 加本地 fallback "
        "才能恢复。"
    )
)
def test_project_edit_wizard_status_and_apply(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    created = runner.invoke(main, ["project", "create", "--name", "Wizard Demo"], env=env)
    assert created.exit_code == 0

    wizard_input = "\n".join(
        [
            "siliconflow",
            "SiliconFlow",
            "api_key",
            "SILICONFLOW_API_KEY",
            "https://api.siliconflow.cn/v1",
            "Qwen/Qwen3.5-32B",
            "Qwen/Qwen3.5-14B",
            "main",
            "cheap",
            "cheap",
            "cheap",
            "litellm",
            "http://localhost:4000",
            "LITELLM_MASTER_KEY",
            "n",
        ]
    )
    started = runner.invoke(
        main,
        ["project", "edit", "--wizard"],
        env=env,
        input=wizard_input,
    )
    assert started.exit_code == 0
    assert "Wizard Session" in started.output
    assert "status=ready_for_apply" in started.output
    assert "draft_secret_targets=2" in started.output

    status = runner.invoke(main, ["project", "edit", "--wizard-status"], env=env)
    assert status.exit_code == 0
    assert "status=ready_for_apply" in status.output

    applied = runner.invoke(main, ["project", "edit", "--apply-wizard"], env=env)
    # setup.review 已通过，apply 成功执行，exit_code 为 0
    assert applied.exit_code == 0
    assert "Setup Apply" in applied.output
    assert "SETUP_APPLIED" in applied.output
    # 配置已应用，octoagent.yaml 应被创建
    assert (tmp_path / "octoagent.yaml").exists()
    config = load_config(tmp_path)
    assert config is not None
    assert config.providers[0].base_url == "https://api.siliconflow.cn/v1"
    assert config.memory.reasoning_model_alias == "main"
    assert config.memory.embedding_model_alias == "cheap"


def test_wizard_build_setup_draft_does_not_force_agent_profile(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    created = runner.invoke(main, ["project", "create", "--name", "Draft Demo"], env=env)
    assert created.exit_code == 0

    wizard_input = "\n".join(
        [
            "openrouter",
            "OpenRouter",
            "api_key",
            "OPENROUTER_API_KEY",
            "",
            "openrouter/auto",
            "openrouter/auto",
            "",
            "",
            "",
            "",
            "litellm",
            "http://localhost:4000",
            "LITELLM_MASTER_KEY",
            "n",
        ]
    )
    started = runner.invoke(
        main,
        ["project", "edit", "--wizard"],
        env=env,
        input=wizard_input,
    )
    assert started.exit_code == 0

    async def _active_project_id() -> str:
        project, _ = await ProjectSelectorService(tmp_path).get_active_project()
        return project.project_id

    project_id = asyncio.run(_active_project_id())
    draft = WizardSessionService(tmp_path).build_setup_draft(project_id)

    assert "config" in draft
    assert "agent_profile" not in draft


def test_config_provider_add_preserves_existing_base_url_on_update(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"OCTOAGENT_PROJECT_ROOT": str(tmp_path)}

    created = runner.invoke(
        main,
        [
            "config",
            "provider",
            "add",
            "siliconflow",
            "--auth-type",
            "api_key",
            "--api-key-env",
            "SILICONFLOW_API_KEY",
            "--name",
            "SiliconFlow",
            "--base-url",
            "https://api.siliconflow.cn/v1",
            "--no-credential",
        ],
        env=env,
        input="",
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        main,
        [
            "config",
            "provider",
            "add",
            "siliconflow",
            "--auth-type",
            "api_key",
            "--api-key-env",
            "SILICONFLOW_API_KEY",
            "--name",
            "SiliconFlow CN",
            "--no-credential",
        ],
        env=env,
        input="u\n\n",
    )
    assert updated.exit_code == 0

    config = load_config(tmp_path)
    assert config is not None
    provider = config.get_provider("siliconflow")
    assert provider is not None
    assert provider.name == "SiliconFlow CN"
    assert provider.base_url == "https://api.siliconflow.cn/v1"


def test_provider_package_declares_gateway_dependency(tmp_path: Path) -> None:
    """Provider 包不再依赖 gateway（循环依赖已打断）。"""
    _ = tmp_path
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    assert '"octoagent-gateway"' not in content
