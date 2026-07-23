"""F151 execution model retirement contract."""

from __future__ import annotations

import ast
from pathlib import Path

import octoagent.core.models as public_models
import octoagent.core.models.execution as execution_models
import pytest

_RETIRED_MODELS = {"ExecutionRuntimeRecord", "JobSpec"}


def _class_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def _execution_import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "execution":
            names.update(alias.name for alias in node.names)
    return names


def test_unimplemented_container_job_models_are_absent_from_definitions_and_exports() -> None:
    core_root = Path(__file__).resolve().parents[1]
    model_root = core_root / "src/octoagent/core/models"
    issues: list[str] = []
    defined = _class_names(model_root / "execution.py") & _RETIRED_MODELS
    imported = _execution_import_names(model_root / "__init__.py") & _RETIRED_MODELS
    exported = set(public_models.__all__) & _RETIRED_MODELS
    runtime_attrs = {
        name
        for name in _RETIRED_MODELS
        if hasattr(execution_models, name) or hasattr(public_models, name)
    }
    stale_tests = {
        name
        for name in _RETIRED_MODELS
        if name in (core_root / "tests/test_models.py").read_text(encoding="utf-8")
    }
    for label, values in (
        ("definitions", defined),
        ("package imports", imported),
        ("public exports", exported),
        ("runtime attributes", runtime_attrs),
        ("legacy model tests", stale_tests),
    ):
        if values:
            issues.append(f"{label} still contain {sorted(values)}")
    if issues:
        pytest.fail(
            f"F151_DEAD_EXECUTION_MODELS_STILL_EXPORTED: {'; '.join(issues)}",
            pytrace=False,
        )
