"""F151 T064：唯一 Gateway module entry 与静态启动失败边界。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _locked_pythonpath(repo_root: Path) -> str:
    relative = (
        "octoagent/packages/core/src",
        "octoagent/packages/provider/src",
        "octoagent/packages/protocol/src",
        "octoagent/packages/tooling/src",
        "octoagent/packages/skills/src",
        "octoagent/packages/policy/src",
        "octoagent/packages/memory/src",
        "octoagent/apps/gateway/src",
    )
    return os.pathsep.join(str(repo_root / item) for item in relative)


def _write_uvicorn_probe(root: Path) -> tuple[Path, Path]:
    shim = root / "shim"
    shim.mkdir()
    facts_path = root / "uvicorn-facts.json"
    (shim / "uvicorn.py").write_text(
        """from __future__ import annotations
import json
import os

def run(app, *, host, port):
    with open(os.environ["F151_UVICORN_FACTS"], "w", encoding="utf-8") as handle:
        json.dump({
            "app_module": type(app).__module__,
            "app_title": app.title,
            "host": host,
            "port": port,
        }, handle, sort_keys=True)
""",
        encoding="utf-8",
    )
    return shim, facts_path


def _write_lifespan_failure_probe(root: Path) -> tuple[Path, Path]:
    shim = root / "lifespan-shim"
    shim.mkdir()
    facts_path = root / "lifespan-facts.json"
    (shim / "uvicorn.py").write_text(
        """from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

def _workload_counts():
    counts = {"tasks": 0, "works": 0, "events": 0, "bootstrap_audit_tasks": 0}
    for database in Path(os.environ["OCTOAGENT_DATA_DIR"]).rglob("*.db"):
        connection = sqlite3.connect(database)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in counts:
                if table in {"works", "events"} and table in tables:
                    counts[table] += connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
            if "tasks" in tables:
                counts["tasks"] += connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE task_id != '_plugin_registry_audit'"
                ).fetchone()[0]
                counts["bootstrap_audit_tasks"] += connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE task_id = '_plugin_registry_audit'"
                ).fetchone()[0]
        finally:
            connection.close()
    return counts

def run(app, *, host, port):
    del host, port
    facts = {
        "task_runner_attempts": 0,
        "lifespan_entered": False,
        "readiness_requests": 0,
        "application_requests": 0,
        "backend_calls": 0,
    }

    async def exercise():
        from octoagent.gateway import main as gateway_main

        class FailingTaskRunner:
            def __init__(self, **kwargs):
                del kwargs
                facts["task_runner_attempts"] += 1
                raise RuntimeError("F151_RUNTIME_COMPOSITION_PROBE")

        gateway_main.TaskRunner = FailingTaskRunner
        try:
            async with app.router.lifespan_context(app):
                facts["lifespan_entered"] = True
        except Exception as exc:
            facts["error_type"] = type(exc).__name__
            facts["error"] = str(exc)
            facts["workload_counts"] = _workload_counts()
            Path(os.environ["F151_LIFESPAN_FACTS"]).write_text(
                json.dumps(facts, sort_keys=True),
                encoding="utf-8",
            )
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            os._exit(1)

    asyncio.run(exercise())
""",
        encoding="utf-8",
    )
    return shim, facts_path


def _run_module_entry(
    tmp_path: Path,
    *,
    config: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8017,
    front_door_mode: str | None = None,
) -> SimpleNamespace:
    root = tmp_path / "instance"
    root.mkdir()
    if config is not None:
        (root / "octoagent.yaml").write_text(config, encoding="utf-8")
    shim, facts_path = _write_uvicorn_probe(tmp_path)
    data_dir = root / "data"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
        "OCTOAGENT_PROJECT_ROOT": str(root),
        "OCTOAGENT_DATA_DIR": str(data_dir),
        "F151_UVICORN_FACTS": str(facts_path),
        "PYTHONPATH": os.pathsep.join((str(shim), _locked_pythonpath(_repo_root()))),
    }
    if front_door_mode is None:
        env.pop("OCTOAGENT_FRONTDOOR_MODE", None)
    else:
        env["OCTOAGENT_FRONTDOOR_MODE"] = front_door_mode
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "octoagent.gateway",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return SimpleNamespace(
        completed=completed,
        facts=(json.loads(facts_path.read_text()) if facts_path.exists() else None),
        data_files=(
            sorted(path.relative_to(data_dir) for path in data_dir.rglob("*"))
            if data_dir.exists()
            else []
        ),
    )


def _run_lifespan_failure(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "lifespan-instance"
    root.mkdir()
    shim, facts_path = _write_lifespan_failure_probe(tmp_path)
    data_dir = root / "data"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
        "TMPDIR": str(tmp_path / "tmp"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
        "OCTOAGENT_PROJECT_ROOT": str(root),
        "OCTOAGENT_DATA_DIR": str(data_dir),
        "OCTOAGENT_LLM_MODE": "echo",
        "F151_LIFESPAN_FACTS": str(facts_path),
        "PYTHONPATH": os.pathsep.join((str(shim), _locked_pythonpath(_repo_root()))),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "octoagent.gateway", "--host", "127.0.0.1"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return SimpleNamespace(
        completed=completed,
        facts=(json.loads(facts_path.read_text()) if facts_path.exists() else None),
    )


def _fail_if_issues(oracle: str, issues: list[str]) -> None:
    if issues:
        pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)


def _assert_static_rejection(
    outcome: SimpleNamespace,
    *,
    oracle: str,
    expected_code: str,
) -> None:
    issues: list[str] = []
    if outcome.completed.returncode != 78:
        issues.append(f"exit={outcome.completed.returncode}, expected 78")
    if expected_code not in outcome.completed.stderr:
        issues.append(f"stderr missing {expected_code}")
    if outcome.facts is not None:
        issues.append("static rejection reached uvicorn")
    if outcome.data_files:
        issues.append(f"static rejection wrote workload files: {outcome.data_files!r}")
    _fail_if_issues(oracle, issues)


def test_module_entry_maps_config_error_to_exit_78_before_uvicorn(tmp_path: Path) -> None:
    outcome = _run_module_entry(tmp_path, config="config_version: [\n")
    _assert_static_rejection(
        outcome,
        oracle="F151_CONFIG_EXIT_78_ENTRY_MISSING",
        expected_code="GATEWAY_RUNTIME_CONFIG_INVALID",
    )


def test_module_entry_maps_static_runtime_config_error_to_exit_78_before_uvicorn_even_with_front_door_env_override(  # noqa: E501
    tmp_path: Path,
) -> None:
    outcome = _run_module_entry(
        tmp_path,
        config="config_version: 1\nruntime:\n  llm_mode: docker\n",
        front_door_mode="bearer",
    )
    _assert_static_rejection(
        outcome,
        oracle="F151_STATIC_RUNTIME_CONFIG_BYPASSES_CANONICAL_PREFLIGHT",
        expected_code="GATEWAY_RUNTIME_CONFIG_INVALID",
    )


def test_module_entry_maps_existing_security_exposure_error_to_exit_78_before_uvicorn(
    tmp_path: Path,
) -> None:
    outcome = _run_module_entry(
        tmp_path,
        host="0.0.0.0",
        front_door_mode="loopback",
    )
    _assert_static_rejection(
        outcome,
        oracle="F151_SECURITY_EXIT_78_ENTRY_MISSING",
        expected_code="GATEWAY_SECURITY_CONFIG_INVALID",
    )


def test_module_entry_imports_main_app_once_create_app_preflights_once_and_uvicorn_uses_same_host_port(  # noqa: E501
    tmp_path: Path,
) -> None:
    outcome = _run_module_entry(tmp_path, host="127.0.0.1", port=8123)
    issues: list[str] = []
    if outcome.completed.returncode != 0:
        issues.append(f"exit={outcome.completed.returncode}: {outcome.completed.stderr.strip()}")
    expected = {
        "app_module": "fastapi.applications",
        "app_title": "OctoAgent Gateway",
        "host": "127.0.0.1",
        "port": 8123,
    }
    if outcome.facts != expected:
        issues.append(f"uvicorn facts={outcome.facts!r}, expected {expected!r}")
    if outcome.data_files:
        issues.append(f"module import wrote workload files: {outcome.data_files!r}")
    _fail_if_issues("F151_SINGLE_PRODUCTION_STARTUP_ENTRY_MISSING", issues)


def test_lifespan_runtime_composition_failure_serves_no_request_and_creates_no_workload_side_effects(  # noqa: E501
    tmp_path: Path,
) -> None:
    outcome = _run_lifespan_failure(tmp_path)
    issues: list[str] = []
    if outcome.completed.returncode in {0, 78}:
        issues.append(
            f"runtime composition exit={outcome.completed.returncode}, expected nonzero/non78"
        )
    expected = {
        "task_runner_attempts": 1,
        "lifespan_entered": False,
        "readiness_requests": 0,
        "application_requests": 0,
        "backend_calls": 0,
        "error_type": "RuntimeError",
        "error": "F151_RUNTIME_COMPOSITION_PROBE",
        "workload_counts": {
            "tasks": 0,
            "works": 0,
            "events": 0,
            "bootstrap_audit_tasks": 1,
        },
    }
    if outcome.facts != expected:
        issues.append(f"lifespan facts={outcome.facts!r}, expected {expected!r}")
    if "F151_RUNTIME_COMPOSITION_PROBE" not in outcome.completed.stderr:
        issues.append("runtime composition exception did not reach the process boundary")
    _fail_if_issues("F151_LIFESPAN_COMPOSITION_FAIL_CLOSED_MISSING", issues)
