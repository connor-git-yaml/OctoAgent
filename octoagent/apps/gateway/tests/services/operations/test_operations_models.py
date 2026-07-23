from __future__ import annotations

import json

import pytest
from octoagent.gateway.services.operations.control_plane_models import (
    ConfigSchemaDocument,
    ProjectCandidate,
    ProjectSelectorDocument,
    WizardSessionDocument,
)
from pydantic import ValidationError


def test_control_plane_models_roundtrip_and_reject_invalid_payload() -> None:
    first_candidate = ProjectCandidate(
        project_id="project-alpha",
        slug="alpha",
        name="Alpha",
    )
    selector = ProjectSelectorDocument(
        current_project=first_candidate,
        candidate_projects=[first_candidate],
    )
    restored = ProjectSelectorDocument.model_validate_json(selector.model_dump_json())

    assert restored == selector
    assert json.loads(restored.model_dump_json())["current_project"]["project_id"] == (
        "project-alpha"
    )

    first_candidate.warnings.append("first-only")
    second_candidate = ProjectCandidate(
        project_id="project-beta",
        slug="beta",
        name="Beta",
    )
    assert second_candidate.warnings == []

    first_schema = ConfigSchemaDocument()
    first_schema.schema_payload["type"] = "object"
    assert ConfigSchemaDocument().schema_payload == {}

    with pytest.raises(ValidationError):
        WizardSessionDocument.model_validate({"project_id": "project-alpha"})
