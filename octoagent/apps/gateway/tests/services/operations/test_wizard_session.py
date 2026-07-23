from __future__ import annotations

import asyncio
from pathlib import Path

from octoagent.gateway.services.operations.control_plane_models import WizardSessionDocument
from octoagent.gateway.services.operations.project_selector import ProjectSelectorService
from octoagent.gateway.services.operations.wizard_session import WizardSessionService
from octoagent.gateway.services.operations.wizard_session_store import (
    WizardSessionRecord,
    WizardSessionStore,
)


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


def test_wizard_session_store_roundtrip_corruption_reset_and_backup(tmp_path: Path) -> None:
    first = WizardSessionStore(tmp_path)
    second = WizardSessionStore(tmp_path)
    record = WizardSessionRecord(
        session_id="session-1",
        project_id="project-1",
        document=WizardSessionDocument(
            resource_id="wizard:session-1",
            project_id="project-1",
        ),
    )

    first.save(record)
    assert second.load() == record

    first.path.write_text("{not-json", encoding="utf-8")
    assert second.load() is None
    assert first.path.with_suffix(".json.corrupted").read_text(encoding="utf-8") == ("{not-json")

    first.save(record)
    first.reset()
    assert second.load() is None
    backup = first.path.with_suffix(".json.bak")
    assert WizardSessionRecord.model_validate_json(backup.read_text(encoding="utf-8")) == record
    assert list(first.path.parent.glob("*.tmp")) == []


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

    from octoagent.gateway.services.operations import wizard_session as wizard_module

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


def test_wizard_session_schema_includes_base_url_and_memory_aliases(tmp_path: Path) -> None:
    project = _create_project(tmp_path)
    service = WizardSessionService(tmp_path)

    result = service.start_or_resume(project, interactive=False)

    assert "providers.0.base_url" in result.schema_document.ui_hints["fields"]
    assert "memory.reasoning_model_alias" in result.schema_document.ui_hints["fields"]
    assert "memory.embedding_model_alias" in result.schema_document.ui_hints["fields"]
