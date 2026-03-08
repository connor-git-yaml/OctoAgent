from __future__ import annotations

import asyncio
from pathlib import Path

from octoagent.provider.dx.project_selector import ProjectSelectorService
from octoagent.provider.dx.wizard_session import WizardSessionService


def _create_project(tmp_path: Path):
    async def _run():
        service = ProjectSelectorService(tmp_path)
        project, _, _ = await service.create_project(
            name="Wizard Demo",
            slug="wizard-demo",
            set_active=True,
        )
        return project

    return asyncio.run(_run())


def test_wizard_session_start_resume_status_cancel(tmp_path: Path) -> None:
    project = _create_project(tmp_path)
    service = WizardSessionService(tmp_path)

    started = service.start_or_resume(project, interactive=False)
    assert started.resumed is False
    assert started.record.status == "pending"
    assert started.record.document.current_step == "provider"

    resumed = service.start_or_resume(project, interactive=False)
    assert resumed.resumed is True
    assert resumed.record.session_id == started.record.session_id

    status = service.load_status(project.project_id)
    assert status is not None
    assert status.record.session_id == started.record.session_id

    cancelled = service.cancel(project.project_id)
    assert cancelled is not None
    assert cancelled.record.status == "cancelled"
    assert cancelled.record.document.status == "cancelled"


def test_wizard_session_tolerates_unknown_ui_hints(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _create_project(tmp_path)

    from octoagent.provider.dx import wizard_session as wizard_module

    original_builder = wizard_module.build_config_schema_document

    def _patched_builder(config):
        document = original_builder(config)
        document.ui_hints["fields"]["providers.0.id"]["unknown_cli_hint"] = {
            "widget": "future-ui",
        }
        return document

    monkeypatch.setattr(wizard_module, "build_config_schema_document", _patched_builder)
    service = WizardSessionService(tmp_path)

    result = service.start_or_resume(project, interactive=False)

    assert result.record.status == "pending"
    assert "unknown_cli_hint" in result.schema_document.ui_hints["fields"]["providers.0.id"]
