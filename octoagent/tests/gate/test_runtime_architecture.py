"""F151 Phase0：runtime architecture gate 的黑盒契约测试。

本文件只描述 gate 的外部行为。Phase0 RED 时 checker 尚不存在，因此每个
slice 都必须以自己的唯一 ``*_MISSING`` oracle 失败；不得以 import/collection/
usage error 冒充 RED。T005 实现 checker 后，同一批 nodeids 会在临时仓库中运行
对应的正负场景。
"""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib.util
import inspect
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPO_ROOT / "repo-scripts" / "check-runtime-architecture.py"
FEATURE_REL = Path(".specify/features/151-runtime-boundary-architecture-truth")
FEATURE_ROOT = REPO_ROOT / FEATURE_REL
INVENTORY_REL = FEATURE_REL / "inventories"

IMPORT_ORACLE = "F151_IMPORT_DIRECTION_SCANNER_MISSING"
RETIRED_ORACLE = "F151_RETIRED_QUALITY_SCANNER_MISSING"
MANIFEST_ORACLE = "F151_MACHINE_MANIFEST_VALIDATOR_MISSING"
COMPLEXITY_ORACLE = "F151_COMPLEXITY_RATCHET_SCANNER_MISSING"
EVIDENCE_ORACLE = "F151_TDD_EVIDENCE_VERIFIER_MISSING"
JUNIT_ORACLE = "F151_PYTEST_JUNIT_AGGREGATION_FAIL_CLOSED_MISSING"
CORRECTIVE_EVIDENCE_ORACLE = "F151_T005_EVIDENCE_INTEGRITY_CONTRACT_MISSING"
CORRECTIVE_FINALIZE_ORACLE = "F151_FINALIZE_VERIFICATION_CONTRACT_MISSING"
T006_AMENDMENT_ORACLE = "F151_T006_INDEX_AMENDMENT_CONTRACT_MISSING"
POST_T006_FRONTIER_ORACLE = "F151_POST_T006_FORMAL_FRONTIER_MISSING"
FORMAL_RED_RECORDING_ORACLE = "F151_FORMAL_RED_RECORDING_MISSING"
JUNIT_RERUN_CROSSCHECK_ORACLE = "F151_JUNIT_RERUN_CROSSCHECK_MISSING"
QUARANTINE_NO_GROWTH_ORACLE = "F151_QUARANTINE_NO_GROWTH_MISSING"
EXISTING_RERUN_REPORTING_ORACLE = "F151_EXISTING_RERUN_REPORTING_MISSING"
DOCUMENTATION_AUTHORITY_ORACLE = "F151_DOCUMENTATION_AUTHORITY_DRIFT"
DEPENDENCY_SELECTOR_ORACLE = "F151_DEPENDENCY_SELECTOR_SEMANTIC_RESOLVER_MISSING"
REPOSITORY_COMPLEXITY_ORACLE = "F151_REPOSITORY_COMPLEXITY_SNAPSHOT_NOT_INSTALLED"
ATOMIC_SNAPSHOT_LIFECYCLE_ORACLE = "F151_ATOMIC_SNAPSHOT_LIFECYCLE_MISSING"
REPOSITORY_COMPLEXITY_SNAPSHOT = REPO_ROOT / "repo-scripts/runtime-architecture-ceiling.v1.json"
FORMAL_OFFLINE_ENV_KEY = "LITELLM_LOCAL_MODEL_COST_MAP"
FORMAL_ENV_ORDER = ("PYTHONNOUSERSITE", "PYTHONPATH", FORMAL_OFFLINE_ENV_KEY)
HISTORIC_FORMAL_PREFIX_COUNT = 26
HISTORIC_FORMAL_PREFIX_HEAD = "eff7628173addb61e57c3f668a24a2433de5107db392be985c6673fec3edf0ea"
T006_RED_SLICES = (
    "S006-committed-worktree-clean",
    "S006-index-amendment-integrity",
)
ATOMIC_SNAPSHOT_PATHS = (
    "local/atomic/S017-namespace-atomic/T017-BEFORE/atomic-namespace-before.v1.json",
    "local/atomic/S017-namespace-atomic/T029-AFTER/atomic-namespace-after.v1.json",
)

BOOTSTRAP_SLICES = (
    "S001-import-direction",
    "S002-retired-quality",
    "S002-manifest-integrity",
    "S003-complexity-checker",
    "S004-evidence-checker",
    "S004-junit-parser",
)
RUN_ARTIFACT_NAMES = (
    "junit.xml",
    "stdout.txt",
    "stderr.txt",
    "exit-code.txt",
    "invocation.json",
    "tree.json",
)
V2_TREE_FIELDS = (
    "version",
    "slice_id",
    "phase",
    "base_ref",
    "merge_base_sha",
    "head_sha",
    "head_tree_sha",
    "worktree_fingerprint",
    "fingerprint_scope",
    "fingerprint_files",
    "status_porcelain",
    "captured_utc",
)
V2_INVOCATION_FIELDS = (
    "version",
    "slice_id",
    "phase",
    "task_scope",
    "argv",
    "cwd",
    "env",
    "exact_command",
    "started_utc",
    "finished_utc",
    "exit_code",
)
V2_RECORD_FIELDS = (
    "producer_id",
    "lifecycle_type",
    "slice_id",
    "phase",
    "task_id",
    "cwd",
    "argv",
    "env",
    "mode",
    "base_ref",
    "base_sha",
    "head_sha",
    "head_tree_sha",
    "worktree_fingerprint",
    "fingerprint_scope",
    "fingerprint_files",
    "status_porcelain",
    "tree_captured_utc",
    "started_utc",
    "finished_utc",
    "exit_code",
    "expected_oracle_id",
    "expected_failing_nodeids",
    "observed_nodeids",
    "selected_count",
    "passed_count",
    "failed_count",
    "error_count",
    "skipped_count",
    "rerun_count",
    "artifact_paths",
    "artifact_sha256",
    "artifact_size_bytes",
    "artifact_aggregate_sha256",
    "previous_record_sha256",
    "record_sha256",
)
V2_INDEX_FIELDS = (
    "schema_version",
    "feature_id",
    "bootstrap_anchor_path",
    "bootstrap_anchor_sha256",
    "base_sha",
    "created_utc",
    "recovery",
    "records",
    "chain_head_sha256",
)
BOOTSTRAP_TASKS = {
    "S001-import-direction": "T001",
    "S002-manifest-integrity": "T002",
    "S002-retired-quality": "T002",
    "S003-complexity-checker": "T003",
    "S004-evidence-checker": "T004",
    "S004-junit-parser": "T004",
}


def _require_checker(oracle: str) -> None:
    if not CHECKER.is_file():
        pytest.fail(oracle, pytrace=False)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _complexity_hotspot_source() -> str:
    lines = ["def f(x):"]
    for value in range(11):
        lines.extend((f"    if x == {value}:", f"        return {value}"))
    lines.append("    return -1")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _replace_exact(text: str, old: str, new: str, *, expected_count: int) -> str:
    """执行可审计的fixture替换，拒绝命中数漂移或静默no-op。"""

    actual_count = text.count(old)
    assert actual_count == expected_count, (
        f"fixture replace count drift: expected={expected_count}, actual={actual_count}, "
        f"token={old!r}"
    )
    replaced = text.replace(old, new)
    assert replaced != text, f"fixture replace produced no observable delta: token={old!r}"
    return replaced


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(repo / ".home"),
        "GIT_AUTHOR_NAME": "F151 Test",
        "GIT_AUTHOR_EMAIL": "f151@example.invalid",
        "GIT_COMMITTER_NAME": "F151 Test",
        "GIT_COMMITTER_EMAIL": "f151@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_repo(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(FEATURE_ROOT, repo / FEATURE_REL)
    shutil.rmtree(repo / FEATURE_REL / "evidence" / "local", ignore_errors=True)
    (repo / FEATURE_REL / "verification-report.md").unlink(missing_ok=True)
    if REPOSITORY_COMPLEXITY_SNAPSHOT.is_file():
        target = repo / "repo-scripts/runtime-architecture-ceiling.v1.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_COMPLEXITY_SNAPSHOT, target)
    for relative in ("octoagent/pyproject.toml", "octoagent/uv.lock"):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    quarantine = repo / "octoagent/tests/quarantine.json"
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "octoagent/tests/quarantine.json", quarantine)
    (repo / ".specify/memory").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        REPO_ROOT / ".specify/memory/constitution.md",
        repo / ".specify/memory/constitution.md",
    )
    _write(
        repo / "octoagent/packages/provider/src/octoagent/provider/clean.py",
        "VALUE = 1\n",
    )
    _write(
        repo / "octoagent/packages/provider/src/octoagent/provider/provider_router.py",
        "VALUE = 1\n",
    )
    _write(
        repo / "octoagent/apps/gateway/src/octoagent/gateway/main.py",
        "def create_app():\n    return object()\n",
    )
    _write(repo / "docs/blueprint.md", "# Fixture Blueprint\n")
    (repo / ".home").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture baseline")
    return repo


def _seed_authority_documents(repo: Path) -> None:
    """把真实权威文档集合加入需要执行文档门禁的临时仓库。"""

    authority = _read_json(REPO_ROOT / INVENTORY_REL / "authority-docs.v1.json")
    paths = {row["path"] for row in authority["documents"]}
    paths.update(row["path"] for row in authority["index_derivation"]["root_candidates"])
    for relative in sorted(paths):
        source = REPO_ROOT / relative
        if source.is_file():
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if _git(repo, "status", "--porcelain", "--", "docs", ".specify/memory").stdout:
        _git(repo, "add", "docs", ".specify/memory")
        _git(repo, "commit", "-q", "-m", "fixture documentation authority")


def _seed_precommit_cross_role_repo(tmp_path: Path) -> Path:
    repo = _seed_repo(tmp_path)
    relative = INVENTORY_REL / "cross-role-edges.v1.json"
    _git(repo, "rm", "-q", "--cached", relative.as_posix())
    _git(repo, "commit", "-q", "-m", "inventory absent from pre-feature HEAD")
    source_tree = FEATURE_ROOT / "evidence/local/bootstrap/S002-manifest-integrity/RED/tree.json"
    target_tree = repo / source_tree.relative_to(REPO_ROOT)
    target_tree.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_tree, target_tree)
    return repo


def _manifest(repo: Path, name: str) -> Path:
    return repo / INVENTORY_REL / name


def _append_list_item(repo: Path, name: str, key: str, value: Any) -> None:
    path = _manifest(repo, name)
    data = _read_json(path)
    data[key].append(value)
    _write_json(path, data)


def _drop_key(repo: Path, name: str, key: str) -> None:
    path = _manifest(repo, name)
    data = _read_json(path)
    data.pop(key)
    _write_json(path, data)


def _f150_module_path(path: str) -> str:
    relative = path.split("/src/", 1)[1]
    return relative.removesuffix(".py").replace("/", ".")


def _f150_d03_modules(repo: Path) -> tuple[list[str], list[str]]:
    selected = {
        "project_migration",
        "service_manager",
        "telegram_pairing",
        "update_status_store",
        "update_service",
    }
    moves = _read_json(_manifest(repo, "namespace-migration.v1.json"))["moves"]
    rows = [item for item in moves if Path(item["source"]).stem in selected]
    assert len(rows) == len(selected)
    rows.sort(key=lambda item: Path(item["source"]).stem)
    return (
        [_f150_module_path(item["source"]) for item in rows],
        [_f150_module_path(item["target"]) for item in rows],
    )


def _f150_d03_text(modules: list[str], mutation: str = "") -> str:
    names = {
        "project_migration": "ProjectMigration",
        "service_manager": "ServiceManager",
        "telegram_pairing": "TelegramPairing",
        "update_service": "UpdateService",
        "update_status_store": "UpdateStatusStore",
    }
    imports = [f"from {module} import {names[module.rsplit('.', 1)[-1]]}" for module in modules[:3]]
    if mutation == "order":
        imports[0], imports[1] = imports[1], imports[0]
    if mutation == "alias":
        imports[0] += " as ChangedMigration"
    if mutation == "nonmanifest":
        imports.append("import os")
    lazy = [
        f"    from {module} import {names[module.rsplit('.', 1)[-1]]}" for module in modules[3:]
    ]
    body = "    return UpdateService, UpdateStatusStore"
    if mutation == "body":
        body = "    raise RuntimeError('semantic drift')"
    return "\n".join([*imports, "", "def current_update():", *lazy, body, ""])


def _prepare_f150_d03_case(repo: Path, case: str) -> None:
    source_modules, target_modules = _f150_d03_modules(repo)
    path = repo / "octoagent/apps/gateway/src/octoagent/gateway/main.py"
    _write(path, _f150_d03_text(source_modules))
    _git(repo, "add", str(path.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "freeze f150 d03 provider baseline")
    mutation = case.removeprefix("f150-d03-")
    mutation = "" if mutation == "import-only" else mutation
    _write(path, _f150_d03_text(target_modules, mutation))


def _prepare_atomic_contract_case(repo: Path, case: str) -> None:
    producer_case = case.startswith("atomic-producer-")
    name = "evidence-producers.v1.json" if producer_case else "artifact-lifecycle.v1.json"
    path = _manifest(repo, name)
    data = _read_json(path)
    rows = data["local_producers"] if producer_case else data["generated_local_types"]
    row = next(
        item
        for item in rows
        if item.get("lifecycle_type", item.get("type")) == "atomic-namespace-before"
    )
    prefix = "atomic-producer-" if producer_case else "atomic-lifecycle-"
    field = case.removeprefix(prefix)
    if field == "missing":
        rows.remove(row)
    elif field == "name":
        key = "exact_artifact_names" if producer_case else "required_artifact_names"
        row[key] = ["wrong-before.json"]
    else:
        row[field] = f"wrong-{field}"
    _write_json(path, data)


def _prepare_case(repo: Path, case: str) -> None:
    """把临时仓库改成一个真实的正/负 gate 输入，不写测试专用旁路。"""

    provider = repo / "octoagent/packages/provider/src/octoagent/provider/case.py"
    if case == "provider-static-type-checking":
        _write(
            provider,
            "from typing import TYPE_CHECKING\n"
            "from octoagent.gateway.main import create_app\n"
            "if TYPE_CHECKING:\n    from octoagent.gateway import main\n",
        )
    elif case == "provider-dynamic-module-string":
        _write(
            provider,
            "import importlib\n"
            "TARGET = 'octoagent.gateway.main'\n"
            "def load():\n    return importlib.import_module(TARGET)\n",
        )
    elif case == "provider-subprocess-monkeypatch-entrypoint":
        _write(provider, "COMMAND = ['python', '-m', 'octoagent.gateway']\n")
        _write(
            repo / "octoagent/packages/provider/pyproject.toml",
            "[project]\nname='fixture-provider'\n"
            "[project.scripts]\nfixture='octoagent.gateway.cli:main'\n",
        )
        _write(
            repo / "octoagent/packages/provider/tests/test_case.py",
            "TARGET = 'octoagent.gateway.main:create_app'\n",
        )
    elif case == "namespace-projection":
        data = _read_json(_manifest(repo, "namespace-migration.v1.json"))
        data["moves"][0]["target"] = "octoagent/apps/gateway/src/missing_projection.py"
        _write_json(_manifest(repo, "namespace-migration.v1.json"), data)
    elif case == "retired-history-only":
        _seed_authority_documents(repo)
        _write(repo / "docs/history.md", "Historical: LiteLLM Proxy was retired.\n")
    elif case == "retired-exception":
        _seed_authority_documents(repo)
        _write(repo / "docs/current-runtime.md", "LiteLLM Proxy is required in production.\n")
        _write(repo / "docs/history.md", "Historical: LiteLLM Proxy was retired.\n")
    elif case == "quality-buckets":
        _drop_key(repo, "artifact-lifecycle.v1.json", "content_policy")
    elif case == "cli-side-effect-growth":
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/cli/new_command.py",
            "import subprocess\ndef run():\n    return subprocess.run(['true'])\n",
        )
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/services/operations/bad.py",
            "from octoagent.gateway.cli import new_command\n",
        )
    elif case == "cross-role-equal-replacement":
        path = _manifest(repo, "cross-role-edges.v1.json")
        data = _read_json(path)
        data["calls"][0]["target"] = "octoagent.gateway.cli.replacement"
        _write_json(path, data)
    elif case == "changed-hunk-review":
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/services/operations/new_effect.py",
            "def run(client):\n    return client.send()\n",
        )
    elif case == "namespace-provider-map":
        path = _manifest(repo, "provider-test-rehome.v1.json")
        data = _read_json(path)
        data["moves"][1]["target"] = data["moves"][0]["target"]
        _write_json(path, data)
    elif case == "planned-owner-closure":
        _append_list_item(repo, "planned-diff.v1.json", "exact_additional_paths", "unowned/new.py")
    elif case == "constructor-owner":
        path = _manifest(repo, "runtime-test-behavior-owners.v1.json")
        data = _read_json(path)
        data["owners"].pop()
        _write_json(path, data)
    elif case == "test-owner":
        path = repo / INVENTORY_REL / "test-ownership.md"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n| fake.py | direct | missing.py::test_missing | mock-only |\n",
            encoding="utf-8",
        )
    elif case == "f150-unrelated-f151":
        _write(
            repo / "octoagent/packages/provider/src/octoagent/provider/provider_router.py",
            "VALUE = 2\n",
        )
    elif case.startswith("f150-d03-"):
        _prepare_f150_d03_case(repo, case)
    elif case == "f150-sibling":
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/main.py",
            "def create_app():\n    raise RuntimeError('semantic drift')\n",
        )
    elif case == "rgr-machine-complete":
        path = _manifest(repo, "rgr-slice-scopes.v1.json")
        data = _read_json(path)
        data["slices"].pop("S001-import-direction")
        _write_json(path, data)
    elif case == "declared-new-exists":
        path = _manifest(repo, "rgr-slice-scopes.v1.json")
        data = _read_json(path)
        existing = data["declared_new_paths"][0]
        _write(repo / existing, "already exists in base\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "declared path exists")
    elif case == "selector-grammar":
        path = _manifest(repo, "rgr-slice-scopes.v1.json")
        data = _read_json(path)
        data["symbol_partitions"][0]["members"][0]["ast_or_key_selectors"] = ["unknown:missing"]
        _write_json(path, data)
    elif case == "shared-members":
        path = _manifest(repo, "rgr-slice-scopes.v1.json")
        data = _read_json(path)
        data["overlap_groups"][0]["members"].pop()
        _write_json(path, data)
    elif case == "changed-test-zero-required":
        _write(
            repo / "octoagent/tests/gate/test_unowned_change.py", "def test_x():\n    assert True\n"
        )
    elif case == "phase2-no-future":
        _write(
            repo / "octoagent/frontend/src/domains/settings/SettingsPage.tsx",
            "export const x = 1;\n",
        )
    elif case == "phase3-no-future":
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py",
            "def select():\n    return 'inline'\n",
        )
    elif case == "declared-no-owner":
        path = _manifest(repo, "rgr-slice-scopes.v1.json")
        data = _read_json(path)
        data["declared_new_paths"].append("unowned/declared.py")
        _write_json(path, data)
        _write(repo / "unowned/declared.py", "VALUE = 1\n")
    elif case == "atomic-post-snapshot":
        _write(
            repo
            / "octoagent/apps/gateway/src/octoagent/gateway/services/operations/update_service.py",
            "def unauthorized_business_change():\n    return True\n",
        )
    elif case == "stage-future-selector":
        _append_list_item(
            repo,
            "stage-command-matrix.v1.json",
            "stages",
            {"stage_task": "T014", "sdk_state": "present", "commands": ["C24"]},
        )
    elif case == "coverage-path-count":
        path = _manifest(repo, "stage-command-matrix.v1.json")
        data = _read_json(path)
        data["profiles"]["post-sdk"]["pythonpath_components"].append("octoagent/packages/sdk/src")
        _write_json(path, data)
    elif case == "c03-zero-select":
        path = repo / INVENTORY_REL / "testing-matrix.md"
        path.write_text(
            _replace_exact(
                path.read_text(encoding="utf-8"),
                "test_from_yaml_rejects_exact_retired_runtime_keys_before_model_validation",
                "missing_node",
                expected_count=1,
            ),
            encoding="utf-8",
        )
    elif case == "final-stage-shape":
        path = _manifest(repo, "stage-command-matrix.v1.json")
        data = _read_json(path)
        for stage in data["stages"]:
            if stage["stage_task"] == "T121":
                stage["commands"] = ["C18", "C23"]
        _write_json(path, data)
    elif case == "final-stage-offline-env":
        path = repo / INVENTORY_REL / "testing-matrix.md"
        lines = path.read_text(encoding="utf-8").splitlines()
        matches = [index for index, line in enumerate(lines) if line.startswith("| C084 |")]
        assert len(matches) == 1
        lines[matches[0]] = _replace_exact(
            lines[matches[0]],
            f"{FORMAL_OFFLINE_ENV_KEY}=True ",
            "",
            expected_count=3,
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif case == "finalize-local-mode":
        path = repo / INVENTORY_REL / "testing-matrix.md"
        path.write_text(
            _replace_exact(
                path.read_text(encoding="utf-8"),
                "finalize-verification --mode local-working-tree",
                "finalize-verification --mode committed",
                expected_count=1,
            ),
            encoding="utf-8",
        )
    elif case == "coverage-parent":
        path = repo / INVENTORY_REL / "testing-matrix.md"
        path.write_text(
            _replace_exact(
                path.read_text(encoding="utf-8"),
                'mkdir -p "$report_parent"',
                ":",
                expected_count=2,
            ),
            encoding="utf-8",
        )
    elif case == "tree-delete-matcher":
        path = _manifest(repo, "tree-delete-expansion.v1.json")
        data = _read_json(path)
        data["expansions"][0]["matcher"] = "octoagent/**/*litellm*"
        _write_json(path, data)
    elif case == "other-feature-change":
        _write(repo / ".specify/features/149-forbidden/spec.md", "changed by F151\n")
    elif case == "lifecycle-transition":
        path = _manifest(repo, "artifact-lifecycle.v1.json")
        data = _read_json(path)
        data["committed_exact_paths"][2]["first_state"] = "Final"
        _write_json(path, data)
    elif case == "coverage-freshness":
        path = _manifest(repo, "stage-command-matrix.v1.json")
        data = _read_json(path)
        for stage in data["stages"]:
            if stage["stage_task"] == "T122":
                stage["freshness_binding"]["stage_task"] = "T105"
        _write_json(path, data)
    elif case == "producer-bijection":
        path = _manifest(repo, "evidence-producers.v1.json")
        data = _read_json(path)
        data["local_producers"][0]["exact_artifact_names"].remove("tree.json")
        _write_json(path, data)
    elif case.startswith(("atomic-lifecycle-", "atomic-producer-")):
        _prepare_atomic_contract_case(repo, case)
    elif case == "committed-producer":
        path = _manifest(repo, "evidence-producers.v1.json")
        data = _read_json(path)
        data["committed_producer_refs"].pop()
        _write_json(path, data)
    elif case == "finalize-extra-output":
        path = _manifest(repo, "artifact-lifecycle.v1.json")
        data = _read_json(path)
        data["committed_producer_commands"][-1]["exact_output_paths"].append("completion-report.md")
        _write_json(path, data)
    elif case == "superseded-owner":
        path = _manifest(repo, "active-artifacts.v1.json")
        data = _read_json(path)
        data["superseded"].pop()
        _write_json(path, data)
    elif case == "authority-index":
        path = _manifest(repo, "authority-docs.v1.json")
        data = _read_json(path)
        data["documents"].pop()
        _write_json(path, data)
    elif case == "complexity-fingerprint":
        path = repo / "repo-scripts/runtime-architecture-ceiling.v1.json"
        data = _read_json(path)
        data["ruff_version"] = "0.0.invalid"
        _write_json(path, data)
    elif case == "complexity-ceiling":
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/hotspot.py",
            _complexity_hotspot_source(),
        )
        path = repo / "repo-scripts/runtime-architecture-ceiling.v1.json"
        data = _read_json(path)
        data["total_by_rule"]["C901"] = 0
        _write_json(path, data)
    elif case == "complexity-merge-base":
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/hotspot.py",
            "def f(x):\n    return x\n",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "low water")
        _write(
            repo / "octoagent/apps/gateway/src/octoagent/gateway/hotspot.py",
            _complexity_hotspot_source(),
        )
    elif case == "complexity-write-up":
        path = repo / "repo-scripts/runtime-architecture-ceiling.v1.json"
        data = _read_json(path)
        data["total_by_rule"]["C901"] += 1
        _write_json(path, data)
    elif case.startswith("evidence-") or case.startswith("junit-"):
        _prepare_evidence_case(repo, case)
    elif case.startswith("bootstrap-"):
        _prepare_bootstrap_case(repo, case)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_artifact_sha256(hashes: dict[str, str]) -> str:
    """聚合六件套：UTF-8 canonical JSON(name→sha256)再做SHA-256。"""

    assert set(hashes) == set(RUN_ARTIFACT_NAMES)
    encoded = json.dumps(
        hashes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _junit(
    phase: str,
    *,
    framework: str = "pytest",
    variant: str = "valid",
    single_suite_root: bool = False,
) -> str:
    body = "" if phase != "RED" else '<failure message="EXPECTED_ORACLE">EXPECTED_ORACLE</failure>'
    failures = int(phase == "RED")
    errors = 0
    skipped = 0
    tests = 1
    extra = ""
    if variant == "skip":
        body = '<skipped message="not authorized" />'
        failures = 0
        skipped = 1
    elif variant == "error":
        body = '<error message="collection" />'
        failures = 0
        errors = 1
    elif variant == "rerun":
        body = '<rerunFailure message="flaky">flaky</rerunFailure>'
        failures = 0
    elif variant == "extra-failure":
        tests = 2
        failures = 2
        extra = (
            '<testcase classname="fixture" name="test_unexpected">'
            '<failure message="UNEXPECTED">UNEXPECTED</failure></testcase>'
        )
    suite = (
        f'<testsuite name="{framework}" tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped}">'
        f'<testcase classname="fixture" name="test_contract">{body}</testcase>{extra}'
        "</testsuite>"
    )
    if single_suite_root:
        return f'<?xml version="1.0"?>{suite}'
    return f'<?xml version="1.0"?><testsuites>{suite}</testsuites>'


def _refresh_evidence_record(index_path: Path, phase: str, *artifact_names: str) -> None:
    """刷新语义fixture改动的字节事实，避免被陈旧hash提前截断。"""

    data = _read_json(index_path)
    record = next(item for item in data["records"] if item["phase"] == phase)
    run = index_path.parent / "local" / "runs" / record["slice_id"] / phase
    names = artifact_names or RUN_ARTIFACT_NAMES
    for name in names:
        path = run / name
        if path.is_file():
            record["artifact_sha256"][name] = _sha(path)
            record["artifact_size_bytes"][name] = path.stat().st_size
    _write_json(index_path, data)


def _prepare_evidence_case(repo: Path, case: str) -> None:
    evidence_root = repo / FEATURE_REL / "evidence"
    index_path = evidence_root / "evidence-index.v1.json"
    records: list[dict[str, Any]] = []
    framework = "vitest" if "vitest" in case else "pytest"
    for phase in ("RED", "GREEN", "REFACTOR"):
        run = evidence_root / "local" / "runs" / "S004-evidence-checker" / phase
        run.mkdir(parents=True, exist_ok=True)
        exit_code = 1 if phase == "RED" else 0
        files = {
            "junit.xml": _junit(phase, framework=framework),
            "stdout.txt": f"{framework} {phase} fixture::test_contract\n",
            "stderr.txt": "",
            "exit-code.txt": f"{exit_code}\n",
            "invocation.json": json.dumps(
                {"argv": [framework, "fixture::test_contract"], "cwd": str(repo)}
            ),
            "tree.json": json.dumps(
                {"base_sha": "fixture", "tree_sha": "fixture", "worktree_fingerprint": "fixture"}
            ),
        }
        for name, text in files.items():
            _write(run / name, text)
        records.append(
            {
                "producer_id": f"formal-{framework}-rgr",
                "lifecycle_type": "formal-rgr",
                "slice_id": "S004-evidence-checker",
                "phase": phase,
                "task_id": "T004",
                "cwd": str(repo),
                "argv": [framework, "fixture::test_contract"],
                "env": {"PYTHONNOUSERSITE": "1"},
                "base_sha": "fixture",
                "head_sha": "fixture",
                "tree_sha": "fixture",
                "worktree_fingerprint": "fixture",
                "started_utc": "2026-07-21T00:00:00Z",
                "finished_utc": "2026-07-21T00:00:01Z",
                "expected_oracle_id": "EXPECTED_ORACLE" if phase == "RED" else None,
                "expected_failing_nodeids": ["fixture::test_contract"] if phase == "RED" else [],
                "observed_nodeids": ["fixture::test_contract"],
                "artifact_paths": sorted(str((run / name).relative_to(repo)) for name in files),
                "artifact_sha256": {name: _sha(run / name) for name in files},
                "artifact_size_bytes": {name: (run / name).stat().st_size for name in files},
            }
        )
    _write_json(index_path, {"version": 1, "records": records})

    red = evidence_root / "local/runs/S004-evidence-checker/RED"
    if case == "evidence-missing-artifact":
        (red / "tree.json").unlink()
    elif case in {"evidence-alias-root", "evidence-bin"}:
        (red / "stdout.bin").write_bytes((red / "stdout.txt").read_bytes())
    elif case == "evidence-run-json":
        _write(red / "run.json", "{}\n")
    elif case == "evidence-noncanonical-root":
        noncanonical = evidence_root / "runs" / "S004-evidence-checker" / "RED"
        shutil.copytree(red, noncanonical)
    elif case == "evidence-missing-invocation":
        (red / "invocation.json").unlink()
    elif case == "evidence-missing-tree":
        (red / "tree.json").unlink()
    elif case == "evidence-fake-hash":
        data = _read_json(index_path)
        data["records"][0]["artifact_sha256"]["stdout.txt"] = "0" * 64
        _write_json(index_path, data)
    elif case == "evidence-junit-disagrees":
        _write(red / "junit.xml", _junit("GREEN"))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "evidence-exit-node-mismatch":
        _write(red / "exit-code.txt", "0\n")
        _refresh_evidence_record(index_path, "RED", "exit-code.txt")
    elif case == "evidence-oracle-extra-failure":
        data = _read_json(index_path)
        data["records"][0]["expected_oracle_id"] = "OTHER_ORACLE"
        data["records"][0]["expected_failing_nodeids"].append("fixture::unexpected")
        _write_json(index_path, data)
    elif case == "evidence-wrong-assertion":
        _write(
            red / "junit.xml",
            _replace_exact(
                _junit("RED"),
                "EXPECTED_ORACLE",
                "WRONG_ASSERTION",
                expected_count=2,
            ),
        )
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "evidence-reordered":
        data = _read_json(index_path)
        data["records"] = [data["records"][1], data["records"][0], data["records"][2]]
        _write_json(index_path, data)
    elif case in {"evidence-selector-mismatch", "evidence-vitest-selector"}:
        _write(
            red / "invocation.json",
            json.dumps({"argv": [framework, "fixture::other"], "cwd": str(repo)}),
        )
        _refresh_evidence_record(index_path, "RED", "invocation.json")
    elif case == "evidence-collection-error":
        _write(red / "junit.xml", _junit("RED", variant="error"))
        _write(red / "exit-code.txt", "4\n")
        _refresh_evidence_record(index_path, "RED", "junit.xml", "exit-code.txt")
    elif case == "evidence-skip":
        _write(red / "junit.xml", _junit("RED", variant="skip"))
        _write(red / "exit-code.txt", "0\n")
        _refresh_evidence_record(index_path, "RED", "junit.xml", "exit-code.txt")
    elif case == "evidence-vitest-skip":
        _write(red / "junit.xml", _junit("RED", framework="vitest", variant="skip"))
        _write(red / "stdout.txt", "vitest selected fixture::test_contract then skipped\n")
        _write(red / "exit-code.txt", "0\n")
        _refresh_evidence_record(index_path, "RED", "junit.xml", "stdout.txt", "exit-code.txt")
    elif case == "evidence-vitest-extra-failure":
        _write(red / "junit.xml", _junit("RED", framework="vitest", variant="extra-failure"))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "evidence-vitest-unparseable-raw":
        _write(red / "stdout.txt", "not a parseable Vitest reporter stream\n")
        _refresh_evidence_record(index_path, "RED", "stdout.txt")
    elif case == "evidence-rerun":
        _write(red / "stdout.txt", "RERUN fixture::test_contract\n")
        _refresh_evidence_record(index_path, "RED", "stdout.txt")
    elif case == "evidence-blanket-rerun":
        _write(
            red / "invocation.json",
            json.dumps(
                {"argv": [framework, "--reruns", "1", "fixture::test_contract"], "cwd": str(repo)}
            ),
        )
        _refresh_evidence_record(index_path, "RED", "invocation.json")
    elif case == "evidence-dirty-zero":
        _write(repo / "octoagent/apps/gateway/src/octoagent/gateway/dirty.py", "VALUE = 2\n")
        _write_json(index_path, {"version": 1, "records": []})
    elif case == "evidence-unmapped-scope":
        _write(repo / "octoagent/apps/gateway/src/octoagent/gateway/unmapped.py", "VALUE = 2\n")
    elif case in {"evidence-lifecycle-escape", "evidence-lifecycle-unknown"}:
        _write(evidence_root / "local" / "unknown.bin", "unknown\n")
    elif case == "evidence-lifecycle-early":
        _write(evidence_root.parent / "verification-report.md", "premature\n")
    elif case == "evidence-lifecycle-wrong-writer":
        data = _read_json(index_path)
        data["records"][0]["task_id"] = "T999"
        _write_json(index_path, data)
    elif case == "evidence-lifecycle-gitignored":
        _write(evidence_root / "local" / "coverage" / "T999" / "coverage.lcov", "ignored escape\n")
    elif case == "junit-valid-single":
        _write(red / "junit.xml", _junit("RED", single_suite_root=True))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "junit-valid-nested":
        _write(red / "junit.xml", _junit("RED"))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "junit-failure":
        _write(
            red / "junit.xml",
            _replace_exact(
                _junit("RED"),
                'failures="1"',
                'failures="0"',
                expected_count=1,
            ),
        )
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "junit-error":
        _write(red / "junit.xml", _junit("RED", variant="error"))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "junit-skip":
        _write(red / "junit.xml", _junit("RED", variant="skip"))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "junit-rerun":
        _write(red / "junit.xml", _junit("RED", variant="rerun"))
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case == "junit-malformed":
        _write(red / "junit.xml", "<testsuites><testsuite>")
        _refresh_evidence_record(index_path, "RED", "junit.xml")
    elif case in {"junit-missing-suite", "junit-invalid-counts"}:
        _write(red / "junit.xml", "<?xml version='1.0'?><not-testsuite />")
        _refresh_evidence_record(index_path, "RED", "junit.xml")


def _bootstrap_contracts(repo: Path) -> dict[str, tuple[list[str], str]]:
    """从冻结RGR表解析6个bootstrap slice的exact selector与唯一oracle。"""

    manifest = (repo / INVENTORY_REL / "rgr-slices.md").read_text(encoding="utf-8")
    contracts: dict[str, tuple[list[str], str]] = {}
    for line in manifest.splitlines():
        if not line.startswith("| `S"):
            continue
        columns = [column.strip() for column in line.split("|")]
        slice_id = columns[1].split("`")[1]
        if slice_id not in BOOTSTRAP_SLICES:
            continue
        nodeids = columns[3].removeprefix("`").removesuffix("`").split()
        oracle = columns[4].removeprefix("`").removesuffix("`")
        assert slice_id not in contracts
        assert nodeids and all("::" in nodeid for nodeid in nodeids)
        assert oracle.endswith("_MISSING")
        contracts[slice_id] = (nodeids, oracle)
    assert tuple(contracts) == BOOTSTRAP_SLICES
    return contracts


def _bootstrap_junit(nodeids: list[str], oracle: str) -> str:
    cases = []
    for nodeid in nodeids:
        path, class_name, test_name = nodeid.split("::")
        module = path.removeprefix("octoagent/").removesuffix(".py").replace("/", ".")
        cases.append(
            f'<testcase classname="{module}.{class_name}" name="{test_name}" time="0.000">'
            f'<failure message="Failed: {oracle}">{oracle}</failure></testcase>'
        )
    count = len(nodeids)
    suite = (
        f'<testsuite name="pytest" errors="0" failures="{count}" skipped="0" '
        f'tests="{count}" time="0.001" timestamp="2026-07-21T00:00:00+00:00" '
        f'hostname="fixture.invalid">{"".join(cases)}</testsuite>'
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites name="pytest tests">{suite}</testsuites>'
    )


def _bootstrap_hashes(run: Path) -> dict[str, str]:
    assert {path.name for path in run.iterdir() if path.is_file()} == set(RUN_ARTIFACT_NAMES)
    return {name: _sha(run / name) for name in RUN_ARTIFACT_NAMES}


def _refresh_bootstrap_anchor(anchor: Path, slice_id: str, run: Path) -> None:
    """仅供schema负例：同步anchor，使缺字段成为唯一缺陷。"""

    data = _read_json(anchor)
    artifact = next(item for item in data["artifacts"] if item["slice_id"] == slice_id)
    hashes = _bootstrap_hashes(run)
    artifact.update(
        {
            "sha256": _canonical_artifact_sha256(hashes),
            "size_bytes": sum((run / name).stat().st_size for name in RUN_ARTIFACT_NAMES),
            "argv_sha256": hashes["invocation.json"],
            "tree_sha256": hashes["tree.json"],
            "junit_sha256": hashes["junit.xml"],
            "stdout_sha256": hashes["stdout.txt"],
            "stderr_sha256": hashes["stderr.txt"],
        }
    )
    _write_json(anchor, data)


def _prepare_bootstrap_case(repo: Path, case: str) -> None:
    evidence_root = repo / FEATURE_REL / "evidence"
    contracts = _bootstrap_contracts(repo)
    artifacts: list[dict[str, Any]] = []
    merge_base_sha = "1" * 40
    head_sha = "2" * 40
    head_tree_sha = "3" * 40
    worktree_fingerprint = "4" * 64
    pythonpath = ":".join(
        str(repo / relative)
        for relative in (
            "octoagent/packages/core/src",
            "octoagent/packages/provider/src",
            "octoagent/packages/protocol/src",
            "octoagent/packages/tooling/src",
            "octoagent/packages/skills/src",
            "octoagent/packages/policy/src",
            "octoagent/packages/memory/src",
            "octoagent/packages/sdk/src",
            "octoagent/apps/gateway/src",
        )
    )
    for slice_id in BOOTSTRAP_SLICES:
        nodeids, oracle = contracts[slice_id]
        run = evidence_root / "local" / "bootstrap" / slice_id / "RED"
        junit_path = run / "junit.xml"
        argv = [
            "uv",
            "run",
            "--project",
            "octoagent",
            "--no-sync",
            "python",
            "-m",
            "pytest",
            "-q",
            "-rA",
            *nodeids,
            f"--junitxml={junit_path.relative_to(repo)}",
        ]
        env = {"PYTHONNOUSERSITE": "1", "PYTHONPATH": pythonpath}
        exact_command = shlex.join(["env", "PYTHONNOUSERSITE=1", f"PYTHONPATH={pythonpath}", *argv])
        invocation = {
            "version": 1,
            "slice_id": slice_id,
            "phase": "RED",
            "task_scope": "T001-T004",
            "cwd": str(repo),
            "env": env,
            "argv": argv,
            "exact_command": exact_command,
            "exit_code": 1,
            "started_utc": "2026-07-21T00:00:00Z",
            "finished_utc": "2026-07-21T00:00:01Z",
        }
        tree = {
            "version": 1,
            "slice_id": slice_id,
            "phase": "RED",
            "base_ref": "origin/master",
            "merge_base_sha": merge_base_sha,
            "head_sha": head_sha,
            "head_tree_sha": head_tree_sha,
            "worktree_fingerprint": worktree_fingerprint,
            "fingerprint_scope": (
                "committed/staged/unstaged/untracked final files excluding evidence/local outputs"
            ),
            "fingerprint_files": [
                {
                    "kind": "file",
                    "path": "octoagent/tests/gate/test_runtime_architecture.py",
                    "sha256": "5" * 64,
                    "size_bytes": 1,
                }
            ],
            "status_porcelain": ["?? octoagent/tests/gate/test_runtime_architecture.py"],
            "captured_utc": "2026-07-21T00:00:01Z",
        }
        files = {
            "junit.xml": _bootstrap_junit(nodeids, oracle),
            "stdout.txt": f"{len(nodeids)} failed: {oracle}\n",
            "stderr.txt": "",
            "exit-code.txt": "1\n",
            "invocation.json": json.dumps(invocation, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            "tree.json": json.dumps(tree, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        }
        for name, text in files.items():
            _write(run / name, text)
        hashes = _bootstrap_hashes(run)
        artifacts.append(
            {
                "slice_id": slice_id,
                "phase": "RED",
                "relative_path": str(run.relative_to(repo)),
                "sha256": _canonical_artifact_sha256(hashes),
                "size_bytes": sum((run / name).stat().st_size for name in RUN_ARTIFACT_NAMES),
                "argv_sha256": hashes["invocation.json"],
                "tree_sha256": hashes["tree.json"],
                "junit_sha256": hashes["junit.xml"],
                "stdout_sha256": hashes["stdout.txt"],
                "stderr_sha256": hashes["stderr.txt"],
                "exit_code": 1,
            }
        )
    anchor = evidence_root / "bootstrap-anchor.v1.json"
    _write_json(
        anchor,
        {
            "version": 1,
            "feature_id": "F151",
            "base_sha": merge_base_sha,
            "worktree_fingerprint": worktree_fingerprint,
            "anchored_utc": "2026-07-21T00:00:02Z",
            "main_review_message_id": "fixture-main-review",
            "artifacts": artifacts,
        },
    )

    first_slice = BOOTSTRAP_SLICES[0]
    first_run = evidence_root / "local" / "bootstrap" / first_slice / "RED"
    if case == "bootstrap-missing":
        anchor.unlink()
    elif case == "bootstrap-malformed":
        _write_json(anchor, {"version": 1})
    elif case == "bootstrap-second-anchor":
        shutil.copy2(anchor, evidence_root / "bootstrap-anchor-second.v1.json")
    elif case == "bootstrap-missing-invocation-field":
        invocation = _read_json(first_run / "invocation.json")
        invocation.pop("exact_command")
        _write_json(first_run / "invocation.json", invocation)
        _refresh_bootstrap_anchor(anchor, first_slice, first_run)
    elif case == "bootstrap-missing-tree-field":
        tree = _read_json(first_run / "tree.json")
        tree.pop("head_sha")
        _write_json(first_run / "tree.json", tree)
        _refresh_bootstrap_anchor(anchor, first_slice, first_run)
    elif case == "bootstrap-replaced-artifact":
        _write(first_run / "exit-code.txt", "0\n")
    elif case == "bootstrap-mixed-base":
        tree = _read_json(first_run / "tree.json")
        tree["merge_base_sha"] = "6" * 40
        _write_json(first_run / "tree.json", tree)
    elif case == "bootstrap-mixed-head-sha":
        tree = _read_json(first_run / "tree.json")
        tree["head_sha"] = "7" * 40
        _write_json(first_run / "tree.json", tree)
    elif case == "bootstrap-mixed-head-tree":
        tree = _read_json(first_run / "tree.json")
        tree["head_tree_sha"] = "8" * 40
        _write_json(first_run / "tree.json", tree)
    elif case == "bootstrap-mixed-worktree-fingerprint":
        tree = _read_json(first_run / "tree.json")
        tree["worktree_fingerprint"] = "9" * 64
        _write_json(first_run / "tree.json", tree)
    elif case == "bootstrap-mixed-argv":
        invocation = _read_json(first_run / "invocation.json")
        invocation["argv"][-2] = "fixture::other"
        _write_json(first_run / "invocation.json", invocation)
    elif case == "bootstrap-mixed-junit":
        _write(first_run / "junit.xml", _junit("GREEN"))
    elif case == "bootstrap-mixed-stdout":
        _write(first_run / "stdout.txt", "other run\n")
    elif case == "bootstrap-mixed-stderr":
        _write(first_run / "stderr.txt", "other run\n")


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _corrective_transactions(repo: Path) -> dict[str, dict[str, Any]]:
    lifecycle = _read_json(_manifest(repo, "artifact-lifecycle.v1.json"))
    corrective = next(
        item for item in lifecycle["generated_local_types"] if item["type"] == "corrective-red"
    )
    transactions = {item["slice_id"]: item for item in corrective["transactions"]}
    assert set(transactions) == {"S004-evidence-checker", "S002-manifest-integrity"}
    return transactions


def _record_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    return _canonical_json_sha256(payload)


def _rehash_v2(index: dict[str, Any]) -> None:
    previous = _canonical_json_sha256(
        {
            "feature_id": index["feature_id"],
            "bootstrap_anchor_sha256": index["bootstrap_anchor_sha256"],
            "base_sha": index["base_sha"],
        }
    )
    seen: set[str] = set()
    for record in index["records"]:
        record["previous_record_sha256"] = previous
        record["record_sha256"] = _record_hash(record)
        assert record["record_sha256"] not in seen
        seen.add(record["record_sha256"])
        previous = record["record_sha256"]
    index["chain_head_sha256"] = previous


def _refresh_v2_artifact(index: dict[str, Any], repo: Path, ordinal: int, name: str) -> None:
    record = index["records"][ordinal]
    artifact_paths = {Path(path).name: repo / path for path in record["artifact_paths"]}
    path = artifact_paths[name]
    record["artifact_sha256"][name] = _sha(path)
    record["artifact_size_bytes"][name] = path.stat().st_size
    record["artifact_aggregate_sha256"] = _canonical_artifact_sha256(record["artifact_sha256"])
    _rehash_v2(index)


def _v2_junit(nodeids: list[str], oracle: str) -> str:
    return _bootstrap_junit(nodeids, oracle)


def _write_v2_run(
    repo: Path,
    run: Path,
    *,
    slice_id: str,
    task_id: str,
    nodeids: list[str],
    oracle: str,
    base_sha: str,
    head_tree_sha: str,
    lifecycle_type: str,
) -> dict[str, Any]:
    junit_relative = run / "junit.xml"
    argv = [
        "uv",
        "run",
        "--project",
        "octoagent",
        "--no-sync",
        "python",
        "-m",
        "pytest",
        "-q",
        "-rA",
        *nodeids,
        f"--junitxml={junit_relative.relative_to(repo)}",
    ]
    pythonpath = ":".join(
        str(repo / relative)
        for relative in (
            "octoagent/packages/core/src",
            "octoagent/packages/provider/src",
            "octoagent/packages/protocol/src",
            "octoagent/packages/tooling/src",
            "octoagent/packages/skills/src",
            "octoagent/packages/policy/src",
            "octoagent/packages/memory/src",
            "octoagent/packages/sdk/src",
            "octoagent/apps/gateway/src",
        )
    )
    env = {"PYTHONNOUSERSITE": "1", "PYTHONPATH": pythonpath}
    started = "2026-07-21T00:00:10Z"
    finished = "2026-07-21T00:00:11Z"
    fingerprint_files: list[dict[str, Any]] = []
    status_porcelain: list[str] = []
    fingerprint_scope = (
        "committed/staged/unstaged/untracked final files excluding evidence/local outputs"
    )
    worktree_fingerprint = _canonical_json_sha256(
        {"fingerprint_files": fingerprint_files, "status_porcelain": status_porcelain}
    )
    invocation = {
        "version": 1,
        "slice_id": slice_id,
        "phase": "RED",
        "task_scope": task_id,
        "argv": argv,
        "cwd": str(repo),
        "env": env,
        "exact_command": shlex.join(
            ["env", "PYTHONNOUSERSITE=1", f"PYTHONPATH={pythonpath}", *argv]
        ),
        "started_utc": started,
        "finished_utc": finished,
        "exit_code": 1,
    }
    tree = {
        "version": 1,
        "slice_id": slice_id,
        "phase": "RED",
        "base_ref": "HEAD",
        "merge_base_sha": base_sha,
        "head_sha": base_sha,
        "head_tree_sha": head_tree_sha,
        "worktree_fingerprint": worktree_fingerprint,
        "fingerprint_scope": fingerprint_scope,
        "fingerprint_files": fingerprint_files,
        "status_porcelain": status_porcelain,
        "captured_utc": finished,
    }
    assert set(invocation) == set(V2_INVOCATION_FIELDS)
    assert set(tree) == set(V2_TREE_FIELDS)
    files = {
        "junit.xml": _v2_junit(nodeids, oracle),
        "stdout.txt": f"{len(nodeids)} failed, oracle={oracle}\n",
        "stderr.txt": "",
        "exit-code.txt": "1\n",
        "invocation.json": json.dumps(invocation, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        "tree.json": json.dumps(tree, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    for name, text_value in files.items():
        _write(run / name, text_value)
    hashes = {name: _sha(run / name) for name in RUN_ARTIFACT_NAMES}
    count = len(nodeids)
    record = {
        "producer_id": (
            "phase0-pytest-bootstrap"
            if lifecycle_type == "phase0-bootstrap"
            else "t005-corrective-red-pytest"
        ),
        "lifecycle_type": lifecycle_type,
        "slice_id": slice_id,
        "phase": "RED",
        "task_id": task_id,
        "cwd": str(repo),
        "argv": argv,
        "env": env,
        "mode": "local-working-tree",
        "base_ref": "HEAD",
        "base_sha": base_sha,
        "head_sha": base_sha,
        "head_tree_sha": head_tree_sha,
        "worktree_fingerprint": worktree_fingerprint,
        "fingerprint_scope": fingerprint_scope,
        "fingerprint_files": fingerprint_files,
        "status_porcelain": status_porcelain,
        "tree_captured_utc": finished,
        "started_utc": started,
        "finished_utc": finished,
        "exit_code": 1,
        "expected_oracle_id": oracle,
        "expected_failing_nodeids": nodeids,
        "observed_nodeids": nodeids,
        "selected_count": count,
        "passed_count": 0,
        "failed_count": count,
        "error_count": 0,
        "skipped_count": 0,
        "rerun_count": 0,
        "artifact_paths": sorted(
            str((run / name).relative_to(repo)) for name in RUN_ARTIFACT_NAMES
        ),
        "artifact_sha256": hashes,
        "artifact_size_bytes": {name: (run / name).stat().st_size for name in RUN_ARTIFACT_NAMES},
        "artifact_aggregate_sha256": _canonical_artifact_sha256(hashes),
        "previous_record_sha256": "0" * 64,
        "record_sha256": "0" * 64,
    }
    assert set(record) == set(V2_RECORD_FIELDS)
    return record


def _prepare_v2_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = _seed_repo(tmp_path)
    evidence = repo / FEATURE_REL / "evidence"
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    head_tree_sha = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    contracts = _bootstrap_contracts(repo)
    transactions = _corrective_transactions(repo)
    records: list[dict[str, Any]] = []
    for slice_id in BOOTSTRAP_SLICES:
        nodeids, oracle = contracts[slice_id]
        run = evidence / "local" / "bootstrap" / slice_id / "RED"
        records.append(
            _write_v2_run(
                repo,
                run,
                slice_id=slice_id,
                task_id=BOOTSTRAP_TASKS[slice_id],
                nodeids=nodeids,
                oracle=oracle,
                base_sha=base_sha,
                head_tree_sha=head_tree_sha,
                lifecycle_type="phase0-bootstrap",
            )
        )
    corrective_aggregates: dict[str, str] = {}
    for slice_id in ("S004-evidence-checker", "S002-manifest-integrity"):
        transaction = transactions[slice_id]
        run = evidence / "local" / "corrective" / "T005-integrity-v2" / slice_id / "RED"
        record = _write_v2_run(
            repo,
            run,
            slice_id=slice_id,
            task_id="T005",
            nodeids=transaction["exact_nodeids"],
            oracle=transaction["expected_oracle_id"],
            base_sha=base_sha,
            head_tree_sha=head_tree_sha,
            lifecycle_type="corrective-red",
        )
        records.append(record)
        corrective_aggregates[slice_id] = record["artifact_aggregate_sha256"]

    anchor_artifacts = []
    for record in records[:6]:
        anchor_artifacts.append(
            {
                "slice_id": record["slice_id"],
                "phase": "RED",
                "relative_path": str(
                    (evidence / "local" / "bootstrap" / record["slice_id"] / "RED").relative_to(
                        repo
                    )
                ),
                "sha256": record["artifact_aggregate_sha256"],
                "size_bytes": sum(record["artifact_size_bytes"].values()),
                "argv_sha256": record["artifact_sha256"]["invocation.json"],
                "tree_sha256": record["artifact_sha256"]["tree.json"],
                "junit_sha256": record["artifact_sha256"]["junit.xml"],
                "stdout_sha256": record["artifact_sha256"]["stdout.txt"],
                "stderr_sha256": record["artifact_sha256"]["stderr.txt"],
                "exit_code": 1,
            }
        )
    anchor_path = evidence / "bootstrap-anchor.v1.json"
    _write_json(
        anchor_path,
        {
            "version": 1,
            "feature_id": "F151",
            "base_sha": base_sha,
            "worktree_fingerprint": records[0]["worktree_fingerprint"],
            "anchored_utc": "2026-07-21T00:00:12Z",
            "main_review_message_id": "fixture-main-phase0-review",
            "artifacts": anchor_artifacts,
        },
    )
    anchor_sha = _sha(anchor_path)
    review_id = "main-f151-t005-corrective-red-review-fixture"
    corrective_aggregate = _canonical_json_sha256(corrective_aggregates)
    recovery = {
        "rejected_index_sha256": "1ad740db7bb515c633e48c42c75da92dd310ad7bc1e1993cdd973c9c52023adb",
        "rejected_run_aggregate_sha256": (
            "a8f84281dd3749a5e80facf724fb40fb"
            # 保持固定哈希分行，避免超长行。
            "4ec20bdfb9e84c0a0edccb500501ba62"
        ),
        "quarantine_root": str((evidence / "local" / "rejected-t005-t006-v1").relative_to(repo)),
        "corrective_red_aggregate_sha256": corrective_aggregate,
        "main_review_message_id": review_id,
        "approval_binding_sha256": _canonical_json_sha256(
            {
                "main_review_message_id": review_id,
                "corrective_red_aggregate_sha256": corrective_aggregate,
            }
        ),
    }
    index = {
        "schema_version": 2,
        "feature_id": "F151",
        "bootstrap_anchor_path": str(anchor_path.relative_to(repo)),
        "bootstrap_anchor_sha256": anchor_sha,
        "base_sha": base_sha,
        "created_utc": records[-1]["finished_utc"],
        "recovery": recovery,
        "records": records,
        "chain_head_sha256": "0" * 64,
    }
    assert set(index) == set(V2_INDEX_FIELDS)
    _rehash_v2(index)
    index_path = evidence / "evidence-index.v2.json"
    _write_json(index_path, index)
    return repo, index_path


def _run_checker(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "HOME": str(repo / ".home"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
    }
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _verify_v2(
    repo: Path,
    index_path: Path,
    *,
    mode: str = "local-working-tree",
    base_ref: str = "HEAD",
    through_task: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        "tdd-evidence",
        "verify",
        "--mode",
        mode,
        "--base-ref",
        base_ref,
        "--evidence-index",
        str(index_path),
        "--repo-root",
        str(repo),
    ]
    if through_task is not None:
        args.extend(["--through-task", through_task])
    return _run_checker(repo, *args)


def _contract_accept(
    issues: list[str], label: str, result: subprocess.CompletedProcess[str]
) -> None:
    if result.returncode != 0:
        issues.append(f"{label}: expected accept, got {result.returncode}")


def _contract_reject(
    issues: list[str], label: str, result: subprocess.CompletedProcess[str]
) -> None:
    if result.returncode == 0:
        issues.append(f"{label}: expected fail-closed reject")


def _finish_corrective(issues: list[str], oracle: str) -> None:
    if issues:
        pytest.fail(oracle, pytrace=False)


def _mutate_index(
    repo: Path,
    index_path: Path,
    issues: list[str],
    label: str,
    mutator: Any,
    *,
    rehash: bool = True,
) -> None:
    original = index_path.read_bytes()
    index = _read_json(index_path)
    mutator(index)
    if rehash and isinstance(index.get("records"), list) and index["records"]:
        _rehash_v2(index)
    _write_json(index_path, index)
    _contract_reject(issues, label, _verify_v2(repo, index_path))
    index_path.write_bytes(original)


def _mutate_record_artifact(
    repo: Path,
    index_path: Path,
    issues: list[str],
    label: str,
    ordinal: int,
    artifact_name: str,
    mutator: Any,
) -> None:
    index_original = index_path.read_bytes()
    index = _read_json(index_path)
    record = index["records"][ordinal]
    artifact = next(
        repo / path for path in record["artifact_paths"] if Path(path).name == artifact_name
    )
    artifact_original = artifact.read_bytes()
    mutator(artifact)
    _refresh_v2_artifact(index, repo, ordinal, artifact_name)
    _write_json(index_path, index)
    _contract_reject(issues, label, _verify_v2(repo, index_path))
    artifact.write_bytes(artifact_original)
    index_path.write_bytes(index_original)


def _checker_has_command(repo: Path, *command_path: str) -> bool:
    prefix = list(command_path[:-1])
    result = _run_checker(repo, *prefix, "--help")
    return result.returncode == 0 and command_path[-1] in result.stdout


def _corrective_aggregate(repo: Path) -> str:
    root = repo / FEATURE_REL / "evidence/local/corrective/T005-integrity-v2"
    values: dict[str, str] = {}
    for slice_id in ("S004-evidence-checker", "S002-manifest-integrity"):
        run = root / slice_id / "RED"
        values[slice_id] = _canonical_artifact_sha256(
            {name: _sha(run / name) for name in RUN_ARTIFACT_NAMES}
        )
    return _canonical_json_sha256(values)


def _legacy_green_junit(nodeids: list[str]) -> str:
    cases = []
    for nodeid in nodeids:
        path, class_name, test_name = nodeid.split("::")
        module = path.removeprefix("octoagent/").removesuffix(".py").replace("/", ".")
        cases.append(
            f'<testcase classname="{module}.{class_name}" name="{test_name}" time="0.000" />'
        )
    count = len(nodeids)
    suite = (
        f'<testsuite name="pytest" errors="0" failures="0" skipped="0" '
        f'tests="{count}" time="0.001">{"".join(cases)}</testsuite>'
    )
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites>{suite}</testsuites>'


def _write_legacy_run(
    repo: Path,
    *,
    slice_id: str,
    phase: str,
    nodeids: list[str],
    base_sha: str,
    tree_sha: str,
) -> dict[str, Any]:
    run = repo / FEATURE_REL / "evidence/local/runs" / slice_id / phase
    task_id = "T005" if phase == "GREEN" else "T006"
    argv = ["python", "-m", "pytest", *nodeids]
    components = _read_json(_manifest(repo, "stage-command-matrix.v1.json"))["profiles"]["pre-sdk"][
        "pythonpath_components"
    ]
    pythonpath = ":".join(str(repo / component) for component in components)
    env = {"PYTHONNOUSERSITE": "1", "PYTHONPATH": pythonpath}
    started = "2026-07-21T00:00:20Z"
    finished = "2026-07-21T00:00:21Z"
    invocation = {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "task_scope": task_id,
        "argv": argv,
        "cwd": str(repo),
        "env": env,
        "exact_command": shlex.join(
            ["env", "PYTHONNOUSERSITE=1", f"PYTHONPATH={pythonpath}", *argv]
        ),
        "started_utc": started,
        "finished_utc": finished,
        "exit_code": 0,
    }
    tree = {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "base_ref": "HEAD",
        "merge_base_sha": base_sha,
        "head_sha": base_sha,
        "head_tree_sha": tree_sha,
        "worktree_fingerprint": "synthetic-rejected-v1",
        "fingerprint_scope": "synthetic tmp Git repository",
        "fingerprint_files": [],
        "status_porcelain": [],
        "captured_utc": finished,
    }
    assert set(invocation) == set(V2_INVOCATION_FIELDS)
    assert set(tree) == set(V2_TREE_FIELDS)
    files = {
        "junit.xml": _legacy_green_junit(nodeids),
        "stdout.txt": f"{len(nodeids)} passed\n",
        "stderr.txt": "",
        "exit-code.txt": "0\n",
        "invocation.json": json.dumps(invocation, sort_keys=True),
        "tree.json": json.dumps(tree, sort_keys=True),
    }
    for name, content in files.items():
        _write(run / name, content)
    record = {
        "producer_id": "formal-python-rgr",
        "lifecycle_type": "formal-rgr",
        "slice_id": slice_id,
        "phase": phase,
        "task_id": task_id,
        "cwd": str(repo),
        "argv": argv,
        "env": env,
        "base_sha": base_sha,
        "head_sha": base_sha,
        "tree_sha": tree_sha,
        "worktree_fingerprint": "synthetic-rejected-v1",
        "started_utc": started,
        "finished_utc": finished,
        "expected_oracle_id": None,
        "expected_failing_nodeids": [],
        "observed_nodeids": nodeids,
        "artifact_paths": sorted(
            str((run / name).relative_to(repo)) for name in RUN_ARTIFACT_NAMES
        ),
        "artifact_sha256": {name: _sha(run / name) for name in RUN_ARTIFACT_NAMES},
        "artifact_size_bytes": {name: (run / name).stat().st_size for name in RUN_ARTIFACT_NAMES},
    }
    record["record_sha256"] = _record_hash(record)
    assert set(record) == {
        "producer_id",
        "lifecycle_type",
        "slice_id",
        "phase",
        "task_id",
        "cwd",
        "argv",
        "env",
        "base_sha",
        "head_sha",
        "tree_sha",
        "worktree_fingerprint",
        "started_utc",
        "finished_utc",
        "expected_oracle_id",
        "expected_failing_nodeids",
        "observed_nodeids",
        "artifact_paths",
        "artifact_sha256",
        "artifact_size_bytes",
        "record_sha256",
    }
    return record


def _write_rejected_v1_fixture(repo: Path) -> tuple[Path, str]:
    evidence = repo / FEATURE_REL / "evidence"
    anchor = evidence / "bootstrap-anchor.v1.json"
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    tree_sha = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    contracts = _bootstrap_contracts(repo)
    formal_slices = (
        "S001-import-direction",
        "S002-manifest-integrity",
        "S002-retired-quality",
        "S003-complexity-checker",
        "S004-evidence-checker",
        "S004-junit-parser",
    )
    records = [
        _write_legacy_run(
            repo,
            slice_id=slice_id,
            phase=phase,
            nodeids=contracts[slice_id][0],
            base_sha=base_sha,
            tree_sha=tree_sha,
        )
        for phase in ("GREEN", "REFACTOR")
        for slice_id in formal_slices
    ]
    rejected = evidence / "evidence-index.v1.json"
    index = {
        "version": 1,
        "feature_id": "F151",
        "bootstrap_anchor_path": str(anchor.relative_to(repo)),
        "bootstrap_anchor_sha256": _sha(anchor),
        "created_utc": records[-1]["finished_utc"],
        "records": records,
        "chain_head_sha256": records[-1]["record_sha256"],
    }
    assert set(index) == {
        "version",
        "feature_id",
        "bootstrap_anchor_path",
        "bootstrap_anchor_sha256",
        "created_utc",
        "records",
        "chain_head_sha256",
    }
    assert len(records) == 12
    assert len({record["record_sha256"] for record in records}) == 12
    assert all(_record_hash(record) == record["record_sha256"] for record in records)
    _write_json(rejected, index)
    run_root = evidence / "local/runs"
    run_hashes = {
        str(path.relative_to(run_root)): _sha(path)
        for path in sorted(run_root.rglob("*"))
        if path.is_file()
    }
    leaf_runs = {
        path
        for path in run_root.rglob("*")
        if path.is_dir() and any(child.is_file() for child in path.iterdir())
    }
    assert len(leaf_runs) == 12
    assert all(
        {child.name for child in path.iterdir()} == set(RUN_ARTIFACT_NAMES) for path in leaf_runs
    )
    return rejected, _canonical_json_sha256(run_hashes)


def _prepare_recovery_fixture(tmp_path: Path) -> tuple[Path, list[str], bytes]:
    repo, index_path = _prepare_v2_fixture(tmp_path)
    evidence = repo / FEATURE_REL / "evidence"
    rejected_index, _ = _write_rejected_v1_fixture(repo)
    expected_index = _read_json(index_path)
    expected_index["recovery"]["rejected_index_sha256"] = _sha(rejected_index)
    _write_json(index_path, expected_index)
    expected_v2 = index_path.read_bytes()
    index_path.unlink()
    anchor = evidence / "bootstrap-anchor.v1.json"
    args = [
        "tdd-evidence",
        "recover-index",
        "--bootstrap-anchor-file",
        str(anchor),
        "--bootstrap-anchor-sha256",
        _sha(anchor),
        "--rejected-index",
        str(rejected_index),
        "--rejected-index-sha256",
        _sha(rejected_index),
        "--corrective-red-root",
        str(evidence / "local/corrective/T005-integrity-v2"),
        "--corrective-red-aggregate-sha256",
        _corrective_aggregate(repo),
        "--main-review-message-id",
        "main-f151-t005-corrective-red-review-fixture",
        "--output",
        str(index_path),
        "--repo-root",
        str(repo),
    ]
    return repo, args, expected_v2


def _recovery_paths(repo: Path) -> dict[str, Path]:
    evidence = repo / FEATURE_REL / "evidence"
    return {
        "source_index": evidence / "evidence-index.v1.json",
        "source_runs": evidence / "local/runs",
        "quarantine": evidence / "local/rejected-t005-t006-v1",
        "quarantine_index": (evidence / "local/rejected-t005-t006-v1/evidence-index.v1.json"),
        "temp": evidence / ".evidence-index.v2.json.recovering",
        "final": evidence / "evidence-index.v2.json",
    }


def _set_recovery_state(repo: Path, state: str, expected_v2: bytes) -> None:
    paths = _recovery_paths(repo)
    if state in {"R1", "R2", "R2_PARTIAL", "R3", "R4"}:
        paths["source_runs"].rename(paths["quarantine"])
    if state in {"R2", "R2_PARTIAL", "R3", "R4"}:
        paths["source_index"].rename(paths["quarantine_index"])
    if state == "R2_PARTIAL":
        paths["temp"].write_bytes(b"partial-v2")
    elif state == "R3":
        paths["temp"].write_bytes(expected_v2)
    elif state == "R4":
        paths["final"].write_bytes(expected_v2)


def _evidence_byte_map(repo: Path) -> dict[str, str]:
    evidence = repo / FEATURE_REL / "evidence"
    return {
        str(path.relative_to(evidence)): _sha(path)
        for path in sorted(evidence.rglob("*"))
        if path.is_file()
    }


def _mark_finalize_inputs(repo: Path, *, missing: str | None = None) -> None:
    tasks = repo / FEATURE_REL / "tasks.md"
    text_value = tasks.read_text(encoding="utf-8")
    for task_id in ("T120", "T121", "T122", "T123"):
        unchecked = f"- [ ] **{task_id}"
        checked = f"- [x] **{task_id}"
        assert (unchecked in text_value) != (checked in text_value)
        if task_id == missing and checked in text_value:
            text_value = _replace_exact(
                text_value,
                checked,
                unchecked,
                expected_count=1,
            )
        elif task_id != missing and unchecked in text_value:
            text_value = _replace_exact(
                text_value,
                unchecked,
                checked,
                expected_count=1,
            )
    tasks.write_text(text_value, encoding="utf-8")
    status = _git(repo, "status", "--porcelain", "--", str(tasks.relative_to(repo))).stdout
    if status:
        _git(repo, "add", str(tasks.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", "freeze finalize prerequisites")
    router = repo / "octoagent/packages/provider/src/octoagent/provider/provider_router.py"
    router.write_text(
        router.read_text(encoding="utf-8") + "# final local-working-tree input\n",
        encoding="utf-8",
    )


def _invoke(repo: Path, oracle: str, command: str, case: str) -> subprocess.CompletedProcess[str]:
    _require_checker(oracle)
    _prepare_case(repo, case)
    args = [sys.executable, str(CHECKER), command]
    if command == "complexity":
        args.extend(["--repo-root", str(repo), "--base-ref", "HEAD"])
        if case in {"complexity-write-stable", "complexity-write-up"}:
            args.append("--write-snapshot")
    elif command == "tdd-evidence":
        if case.startswith("bootstrap-"):
            anchor = repo / FEATURE_REL / "evidence/bootstrap-anchor.v1.json"
            expected_sha = _sha(anchor) if anchor.is_file() else "0" * 64
            if case == "bootstrap-hash-mismatch":
                expected_sha = "f" * 64
            args.extend(
                [
                    "verify-bootstrap",
                    "--bootstrap-anchor-file",
                    str(anchor),
                    "--bootstrap-anchor-sha256",
                    expected_sha,
                    "--repo-root",
                    str(repo),
                ]
            )
        else:
            args.extend(
                [
                    "verify",
                    "--mode",
                    "local-working-tree",
                    "--base-ref",
                    "HEAD",
                    "--evidence-index",
                    str(repo / FEATURE_REL / "evidence/evidence-index.v1.json"),
                    "--repo-root",
                    str(repo),
                ]
            )
    else:
        args.extend(["--repo-root", str(repo)])
    env = {
        **os.environ,
        "HOME": str(repo / ".home"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
    }
    return subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True)


def _expect_rejects(
    tmp_path: Path,
    oracle: str,
    command: str,
    case: str,
    diagnostic: str,
    *,
    accept_case: str | None = None,
) -> None:
    _expect_rejects_many(
        tmp_path,
        oracle,
        command,
        [(case, diagnostic)],
        accept_case=accept_case,
    )


def _expect_rejects_many(
    tmp_path: Path,
    oracle: str,
    command: str,
    cases: list[tuple[str, str]],
    *,
    accept_case: str | None = None,
) -> None:
    """先证明允许路径可达，再让每个独立fixture只携带一个目标缺陷。"""

    _require_checker(oracle)
    if accept_case is None:
        first_case = cases[0][0]
        if first_case.startswith("bootstrap-"):
            accept_case = "bootstrap-valid"
        elif command == "tdd-evidence":
            accept_case = (
                "evidence-valid-vitest" if "vitest" in first_case else "evidence-valid-pytest"
            )
        else:
            accept_case = "clean"
    _expect_accepts(tmp_path / "accept", oracle, command, accept_case)
    for ordinal, (case, diagnostic) in enumerate(cases):
        repo = _seed_repo(tmp_path / f"reject-{ordinal:02d}-{case}")
        result = _invoke(repo, oracle, command, case)
        output = result.stdout + result.stderr
        assert result.returncode != 0, case
        assert diagnostic in output, case


def _write_atomic_snapshot(repo: Path, relative: str) -> Path:
    path = repo / FEATURE_REL / "evidence" / relative
    _write(path, "{}\n")
    return path


def _atomic_wrong_paths(relative: str) -> tuple[str, ...]:
    path = Path(relative)
    name = path.name
    return (
        f"local/atomic/S999-wrong/{path.parent.name}/{name}",
        f"local/atomic/S017-namespace-atomic/T999-BEFORE/{name}",
        f"local/atomic/S017-namespace-atomic/T017-WRONG/{name}",
        f"{path.parent.as_posix()}/wrong-{name}",
        f"{path.parent.as_posix()}/extra.json",
    )


def _assert_atomic_snapshot_path_controls(tmp_path: Path) -> None:
    issues: list[str] = []
    for ordinal, relative in enumerate(ATOMIC_SNAPSHOT_PATHS):
        repo, index_path = _prepare_v2_fixture(tmp_path / f"accept-{ordinal}")
        _write_atomic_snapshot(repo, relative)
        _contract_accept(issues, f"atomic exact path {ordinal}", _verify_v2(repo, index_path))
        for mutation, wrong in enumerate(_atomic_wrong_paths(relative)):
            reject_repo, reject_index = _prepare_v2_fixture(
                tmp_path / f"reject-{ordinal}-{mutation}"
            )
            _write_atomic_snapshot(reject_repo, relative)
            _contract_accept(
                issues,
                f"atomic reject baseline {ordinal}-{mutation}",
                _verify_v2(reject_repo, reject_index),
            )
            _write_atomic_snapshot(reject_repo, wrong)
            before = _evidence_byte_map(reject_repo)
            _contract_reject(
                issues,
                f"atomic wrong path {ordinal}-{mutation}",
                _verify_v2(reject_repo, reject_index),
            )
            if before != _evidence_byte_map(reject_repo):
                issues.append(f"atomic wrong path {ordinal}-{mutation} changed fixture bytes")
    _finish_corrective(issues, ATOMIC_SNAPSHOT_LIFECYCLE_ORACLE)


def _expect_accepts(tmp_path: Path, oracle: str, command: str, case: str) -> None:
    _require_checker(oracle)
    repo = _seed_repo(tmp_path)
    result = _invoke(repo, oracle, command, case)
    assert result.returncode == 0, result.stdout + result.stderr


def _assert_precommit_cross_role_contract(tmp_path: Path) -> None:
    repo = _seed_precommit_cross_role_repo(tmp_path)
    accepted = _invoke(repo, MANIFEST_ORACLE, "quality-smells", "clean")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    _prepare_case(repo, "cross-role-equal-replacement")
    before = _manifest(repo, "cross-role-edges.v1.json").read_bytes()
    rejected = _invoke(repo, MANIFEST_ORACLE, "quality-smells", "clean")
    after = _manifest(repo, "cross-role-edges.v1.json").read_bytes()
    assert rejected.returncode != 0
    assert "CROSS_ROLE_EDGE_REPLACED" in rejected.stdout + rejected.stderr
    assert before == after


def _assert_machine_expansion_owner_contract(tmp_path: Path) -> None:
    target = "octoagent/apps/gateway/tests/services/operations/test_memory_backend_resolver.py"
    accepted_repo = _seed_repo(tmp_path / "accept")
    _write(accepted_repo / target, "def test_placeholder():\n    assert True\n")
    accepted = _invoke(accepted_repo, MANIFEST_ORACLE, "quality-smells", "clean")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    rejected_repo = _seed_repo(tmp_path / "reject")
    _write(rejected_repo / target, "def test_placeholder():\n    assert True\n")
    scope_path = _manifest(rejected_repo, "rgr-slice-scopes.v1.json")
    scope = _read_json(scope_path)
    expansions = scope["slices"]["S017-namespace-atomic"]["machine_expansions"]
    expansion = next(
        item for item in expansions if item["source"] == "inventories/provider-test-rehome.v1.json"
    )
    expansion["fields"][1] = "moves.missing"
    _write_json(scope_path, scope)
    before = scope_path.read_bytes()
    rejected = _invoke(rejected_repo, MANIFEST_ORACLE, "quality-smells", "clean")
    assert rejected.returncode != 0
    assert "MACHINE_EXPANSION_INVALID" in rejected.stdout + rejected.stderr
    assert scope_path.read_bytes() == before


def _assert_s084_owned_test_path_contract(tmp_path: Path) -> None:
    target = "octoagent/apps/gateway/tests/test_capability_pack_tools.py"
    accepted_repo = _seed_repo(tmp_path / "accept")
    _write(accepted_repo / target, "def test_placeholder():\n    assert True\n")
    accepted = _invoke(accepted_repo, MANIFEST_ORACLE, "quality-smells", "clean")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr

    rejected_repo = _seed_repo(tmp_path / "reject")
    _write(rejected_repo / target, "def test_placeholder():\n    assert True\n")
    scope_path = _manifest(rejected_repo, "rgr-slice-scopes.v1.json")
    scope = _read_json(scope_path)
    scope["slices"]["S084-test-constructors"].pop("owned_test_paths")
    _write_json(scope_path, scope)
    before = scope_path.read_bytes()
    rejected = _invoke(rejected_repo, MANIFEST_ORACLE, "quality-smells", "clean")
    assert rejected.returncode != 0
    assert "EVIDENCE_REQUIRED_SLICE_MISSING" in rejected.stdout + rejected.stderr
    assert scope_path.read_bytes() == before


def _t006_formal_record_core(
    repo: Path,
    source: dict[str, Any],
    invocation: dict[str, Any],
    tree: dict[str, Any],
) -> dict[str, Any]:
    count = len(source["observed_nodeids"])
    return {
        "producer_id": "formal-python-rgr",
        "lifecycle_type": "formal-rgr",
        "slice_id": source["slice_id"],
        "phase": source["phase"],
        "task_id": source["task_id"],
        "cwd": str(repo),
        "argv": invocation["argv"],
        "env": invocation["env"],
        "mode": "local-working-tree",
        "base_ref": tree["base_ref"],
        "base_sha": tree["merge_base_sha"],
        "head_sha": tree["head_sha"],
        "head_tree_sha": tree["head_tree_sha"],
        "worktree_fingerprint": tree["worktree_fingerprint"],
        "fingerprint_scope": tree["fingerprint_scope"],
        "fingerprint_files": tree["fingerprint_files"],
        "status_porcelain": tree["status_porcelain"],
        "tree_captured_utc": tree["captured_utc"],
        "started_utc": invocation["started_utc"],
        "finished_utc": invocation["finished_utc"],
        "exit_code": 0,
        "expected_oracle_id": None,
        "expected_failing_nodeids": [],
        "observed_nodeids": source["observed_nodeids"],
        "selected_count": count,
        "passed_count": count,
        "failed_count": 0,
        "error_count": 0,
        "skipped_count": 0,
        "rerun_count": 0,
        "previous_record_sha256": "0" * 64,
        "record_sha256": "0" * 64,
    }


def _t006_formal_record(repo: Path, source: dict[str, Any], run: Path) -> dict[str, Any]:
    invocation = _read_json(run / "invocation.json")
    tree = _read_json(run / "tree.json")
    hashes = {name: _sha(run / name) for name in RUN_ARTIFACT_NAMES}
    record = _t006_formal_record_core(repo, source, invocation, tree)
    record.update(
        {
            "artifact_paths": sorted(
                str((run / name).relative_to(repo)) for name in RUN_ARTIFACT_NAMES
            ),
            "artifact_sha256": hashes,
            "artifact_size_bytes": {
                name: (run / name).stat().st_size for name in RUN_ARTIFACT_NAMES
            },
            "artifact_aggregate_sha256": _canonical_artifact_sha256(hashes),
        }
    )
    assert set(record) == set(V2_RECORD_FIELDS)
    return record


def _t006_add_formal_records(repo: Path, index_path: Path) -> None:
    rejected_path, _ = _write_rejected_v1_fixture(repo)
    rejected = _read_json(rejected_path)
    index = _read_json(index_path)
    for source in rejected["records"]:
        run = repo / FEATURE_REL / "evidence/local/runs" / source["slice_id"] / source["phase"]
        index["records"].append(_t006_formal_record(repo, source, run))
    _rehash_v2(index)
    _write_json(index_path, index)
    rejected_path.unlink()
    assert len(index["records"]) == 20


def _t006_amendment_contracts(repo: Path) -> dict[str, dict[str, Any]]:
    lifecycle = _read_json(_manifest(repo, "artifact-lifecycle.v1.json"))
    corrective = next(
        item for item in lifecycle["generated_local_types"] if item["type"] == "corrective-red"
    )
    contracts = {item["slice_id"]: item for item in corrective["amendment_transactions"]}
    assert tuple(contracts) == T006_RED_SLICES
    return contracts


def _t006_prepare_amendment_fixture(
    root: Path,
) -> tuple[Path, Path, Path, str, str]:
    repo, index_path = _prepare_v2_fixture(root)
    _t006_add_formal_records(repo, index_path)
    index = _read_json(index_path)
    head_tree_sha = _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    parent = repo / FEATURE_REL / "evidence/local/corrective/T006-committed-mode-v1"
    aggregates: dict[str, str] = {}
    contracts = _t006_amendment_contracts(repo)
    for slice_id in T006_RED_SLICES:
        contract = contracts[slice_id]
        record = _write_v2_run(
            repo,
            parent / slice_id / "RED",
            slice_id=slice_id,
            task_id="T006",
            nodeids=contract["exact_nodeids"],
            oracle=contract["expected_oracle_id"],
            base_sha=index["base_sha"],
            head_tree_sha=head_tree_sha,
            lifecycle_type="corrective-red",
        )
        aggregates[slice_id] = record["artifact_aggregate_sha256"]
    combined = _canonical_json_sha256(aggregates)
    review_id = "main-f151-t006-index-amendment-red-review-fixture"
    return repo, index_path, parent, combined, review_id


def _t006_adoption_args(
    repo: Path,
    index_path: Path,
    parent: Path,
    combined: str,
    review_id: str,
) -> list[str]:
    return [
        "tdd-evidence",
        "run",
        "--slice",
        "S006-index-amendment-integrity",
        "--phase",
        "RED",
        "--mode",
        "local-working-tree",
        "--base-ref",
        "HEAD",
        "--evidence-index",
        str(index_path),
        "--adopt-corrective-red-root",
        str(parent),
        "--corrective-red-aggregate-sha256",
        combined,
        "--main-review-message-id",
        review_id,
        "--repo-root",
        str(repo),
    ]


def _t006_formal_run_bytes(repo: Path) -> dict[str, str]:
    root = repo / FEATURE_REL / "evidence/local/runs"
    return {
        str(path.relative_to(root)): _sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _t006_formal_command(
    repo: Path, run: Path, nodeids: list[str]
) -> tuple[list[str], dict[str, str]]:
    junit = run / "junit.xml"
    argv = [
        "uv",
        "run",
        "--project",
        "octoagent",
        "--no-sync",
        "python",
        "-m",
        "pytest",
        "-q",
        "-rA",
        *nodeids,
        f"--junitxml={junit.relative_to(repo)}",
    ]
    components = _read_json(_manifest(repo, "stage-command-matrix.v1.json"))["profiles"]["pre-sdk"][
        "pythonpath_components"
    ]
    pythonpath = ":".join(str(repo / item) for item in components)
    return argv, {"PYTHONNOUSERSITE": "1", "PYTHONPATH": pythonpath}


def _t006_formal_invocation(
    repo: Path,
    slice_id: str,
    phase: str,
    ordinal: int,
    argv: list[str],
    env: dict[str, str],
) -> dict[str, Any]:
    started = f"2026-07-21T00:01:{ordinal:02d}Z"
    finished = f"2026-07-21T00:02:{ordinal:02d}Z"
    return {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "task_scope": "T006",
        "argv": argv,
        "cwd": str(repo),
        "env": env,
        "exact_command": shlex.join(
            [
                "env",
                "PYTHONNOUSERSITE=1",
                f"PYTHONPATH={env['PYTHONPATH']}",
                *argv,
            ]
        ),
        "started_utc": started,
        "finished_utc": finished,
        "exit_code": 0,
    }


def _t006_formal_tree(
    repo: Path,
    index: dict[str, Any],
    slice_id: str,
    phase: str,
    finished: str,
) -> dict[str, Any]:
    fingerprint_files: list[dict[str, Any]] = []
    status_porcelain: list[str] = []
    fingerprint = _canonical_json_sha256(
        {
            "fingerprint_files": fingerprint_files,
            "status_porcelain": status_porcelain,
        }
    )
    return {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "base_ref": "HEAD",
        "merge_base_sha": index["base_sha"],
        "head_sha": index["base_sha"],
        "head_tree_sha": _git(repo, "rev-parse", "HEAD^{tree}").stdout.strip(),
        "worktree_fingerprint": fingerprint,
        "fingerprint_scope": (
            "committed/staged/unstaged/untracked final files excluding evidence/local outputs"
        ),
        "fingerprint_files": fingerprint_files,
        "status_porcelain": status_porcelain,
        "captured_utc": finished,
    }


def _t006_write_green_run(
    run: Path,
    nodeids: list[str],
    invocation: dict[str, Any],
    tree: dict[str, Any],
) -> dict[str, str]:
    files = {
        "junit.xml": _legacy_green_junit(nodeids),
        "stdout.txt": f"{len(nodeids)} passed\n",
        "stderr.txt": "",
        "exit-code.txt": "0\n",
        "invocation.json": json.dumps(invocation, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        "tree.json": json.dumps(tree, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    for name, content in files.items():
        _write(run / name, content)
    return {name: _sha(run / name) for name in RUN_ARTIFACT_NAMES}


def _t006_green_record_core(
    repo: Path,
    index: dict[str, Any],
    invocation: dict[str, Any],
    tree: dict[str, Any],
    nodeids: list[str],
) -> dict[str, Any]:
    count = len(nodeids)
    return {
        "producer_id": "formal-python-rgr",
        "lifecycle_type": "formal-rgr",
        "slice_id": invocation["slice_id"],
        "phase": invocation["phase"],
        "task_id": "T006",
        "cwd": str(repo),
        "argv": invocation["argv"],
        "env": invocation["env"],
        "mode": "local-working-tree",
        "base_ref": "HEAD",
        "base_sha": index["base_sha"],
        "head_sha": index["base_sha"],
        "head_tree_sha": tree["head_tree_sha"],
        "worktree_fingerprint": tree["worktree_fingerprint"],
        "fingerprint_scope": tree["fingerprint_scope"],
        "fingerprint_files": tree["fingerprint_files"],
        "status_porcelain": tree["status_porcelain"],
        "tree_captured_utc": invocation["finished_utc"],
        "started_utc": invocation["started_utc"],
        "finished_utc": invocation["finished_utc"],
        "exit_code": 0,
        "expected_oracle_id": None,
        "expected_failing_nodeids": [],
        "observed_nodeids": nodeids,
        "selected_count": count,
        "passed_count": count,
        "failed_count": 0,
        "error_count": 0,
        "skipped_count": 0,
        "rerun_count": 0,
        "previous_record_sha256": index["chain_head_sha256"],
        "record_sha256": "0" * 64,
    }


def _t006_green_record(
    repo: Path,
    index: dict[str, Any],
    run: Path,
    invocation: dict[str, Any],
    tree: dict[str, Any],
    nodeids: list[str],
    hashes: dict[str, str],
) -> dict[str, Any]:
    record = _t006_green_record_core(repo, index, invocation, tree, nodeids)
    record.update(
        {
            "artifact_paths": sorted(
                str((run / name).relative_to(repo)) for name in RUN_ARTIFACT_NAMES
            ),
            "artifact_sha256": hashes,
            "artifact_size_bytes": {
                name: (run / name).stat().st_size for name in RUN_ARTIFACT_NAMES
            },
            "artifact_aggregate_sha256": _canonical_artifact_sha256(hashes),
        }
    )
    assert set(record) == set(V2_RECORD_FIELDS)
    record["record_sha256"] = _record_hash(record)
    return record


def _t006_append_formal_fixture(
    repo: Path,
    index_path: Path,
    slice_id: str,
    phase: str,
    ordinal: int,
) -> None:
    contract = _t006_amendment_contracts(repo)[slice_id]
    nodeids = contract["exact_nodeids"]
    run = repo / FEATURE_REL / "evidence/local/runs" / slice_id / phase
    argv, env = _t006_formal_command(repo, run, nodeids)
    invocation = _t006_formal_invocation(repo, slice_id, phase, ordinal, argv, env)
    index = _read_json(index_path)
    tree = _t006_formal_tree(repo, index, slice_id, phase, invocation["finished_utc"])
    assert set(invocation) == set(V2_INVOCATION_FIELDS)
    assert set(tree) == set(V2_TREE_FIELDS)
    hashes = _t006_write_green_run(run, nodeids, invocation, tree)
    record = _t006_green_record(repo, index, run, invocation, tree, nodeids, hashes)
    index["records"].append(record)
    index["chain_head_sha256"] = record["record_sha256"]
    _write_json(index_path, index)


def _t006_require_capability(tmp_path: Path) -> None:
    repo = _seed_repo(tmp_path / "capability-probe")
    result = _run_checker(repo, "tdd-evidence", "run", "--help")
    required = (
        "--adopt-corrective-red-root",
        "--corrective-red-aggregate-sha256",
        "--main-review-message-id",
    )
    if result.returncode != 0 or not all(option in result.stdout for option in required):
        pytest.fail(T006_AMENDMENT_ORACLE, pytrace=False)


def _t006_validate_adoption(
    issues: list[str],
    index_path: Path,
    prior_records: list[dict[str, Any]],
    prior_hashes: list[str],
    prior_runs: dict[str, str],
) -> None:
    amended = _read_json(index_path)
    if amended["records"][:20] != prior_records:
        issues.append("two-RED adoption changed a prior record object")
    if [record["record_sha256"] for record in amended["records"][:20]] != prior_hashes:
        issues.append("two-RED adoption changed a prior record hash")
    if _t006_formal_run_bytes(index_path.parents[4]) != prior_runs:
        issues.append("two-RED adoption changed one of the 12 prior formal runs")
    identities = [
        tuple(record[key] for key in ("lifecycle_type", "task_id", "slice_id", "phase"))
        for record in amended["records"][20:]
    ]
    expected = [("corrective-red", "T006", slice_id, "RED") for slice_id in T006_RED_SLICES]
    if identities != expected:
        issues.append(f"two-RED adoption order mismatch: {identities!r}")


def _t006_complete_frontier(issues: list[str], repo: Path, index_path: Path) -> None:
    for ordinal, (phase, slice_id) in enumerate(
        (
            ("GREEN", T006_RED_SLICES[0]),
            ("GREEN", T006_RED_SLICES[1]),
            ("REFACTOR", T006_RED_SLICES[0]),
            ("REFACTOR", T006_RED_SLICES[1]),
        ),
        start=1,
    ):
        _t006_append_formal_fixture(repo, index_path, slice_id, phase, ordinal)
    _contract_accept(
        issues,
        "through-task T006 complete without future evidence",
        _verify_v2(repo, index_path, through_task="T006"),
    )
    _contract_reject(
        issues,
        "through-task T007 requires its own future evidence",
        _verify_v2(repo, index_path, through_task="T007"),
    )


def _t006_positive_scenario(tmp_path: Path, issues: list[str]) -> None:
    repo, index_path, parent, combined, review_id = _t006_prepare_amendment_fixture(
        tmp_path / "positive"
    )
    prior = _read_json(index_path)
    prior_records = _clone_json(prior["records"])
    prior_hashes = [record["record_sha256"] for record in prior_records]
    prior_runs = _t006_formal_run_bytes(repo)
    args = _t006_adoption_args(repo, index_path, parent, combined, review_id)
    adopted = _run_checker(repo, *args)
    _contract_accept(issues, "exact two-RED adoption", adopted)
    if adopted.returncode != 0:
        return
    _t006_validate_adoption(issues, index_path, prior_records, prior_hashes, prior_runs)
    before_reentry = index_path.read_bytes()
    _contract_accept(issues, "two-RED idempotent reentry", _run_checker(repo, *args))
    if index_path.read_bytes() != before_reentry:
        issues.append("two-RED reentry changed index bytes or duplicated a record")
    _contract_reject(
        issues,
        "through-task T006 before six-record tail",
        _verify_v2(repo, index_path, through_task="T006"),
    )
    _t006_complete_frontier(issues, repo, index_path)


def _t006_reject_case(tmp_path: Path, issues: list[str], label: str, mutator: Any) -> None:
    repo, index_path, parent, combined, review_id = _t006_prepare_amendment_fixture(
        tmp_path / f"reject-{label}"
    )
    args = _t006_adoption_args(repo, index_path, parent, combined, review_id)
    original_index = index_path.read_bytes()
    original_evidence = _evidence_byte_map(repo)
    _contract_accept(issues, f"{label} accept control", _run_checker(repo, *args))
    index_path.write_bytes(original_index)
    if _evidence_byte_map(repo) != original_evidence:
        issues.append(f"{label}: accept control changed bytes outside the index")
    baseline_args = list(args)
    mutator(repo, index_path, parent, args)
    mutated_evidence = _evidence_byte_map(repo)
    if mutated_evidence == original_evidence and args == baseline_args:
        issues.append(f"{label}: reject fixture has no observable delta")
    before_reject = _evidence_byte_map(repo)
    before_index = index_path.read_bytes()
    _contract_reject(issues, label, _run_checker(repo, *args))
    if _evidence_byte_map(repo) != before_reject:
        issues.append(f"{label}: rejected amendment changed evidence bytes")
    if index_path.read_bytes() != before_index:
        issues.append(f"{label}: rejected amendment changed index bytes")


def _t006_remove_index_red(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, index_path, args
    shutil.rmtree(parent / T006_RED_SLICES[1] / "RED")


def _t006_remove_tree(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, index_path, args
    (parent / T006_RED_SLICES[1] / "RED/tree.json").unlink()


def _t006_mutate_stdout(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, index_path, args
    path = parent / T006_RED_SLICES[1] / "RED/stdout.txt"
    path.write_bytes(path.read_bytes() + b"mutated\n")


def _t006_bad_combined(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, index_path, parent
    option = args.index("--corrective-red-aggregate-sha256")
    args[option + 1] = "0" * 64


def _t006_bad_review(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, index_path, parent
    option = args.index("--main-review-message-id")
    args[option + 1] = "not-main-approved"


def _t006_reused_review(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, parent
    option = args.index("--main-review-message-id")
    args[option + 1] = _read_json(index_path)["recovery"]["main_review_message_id"]


def _t006_corrupt_prior(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, parent, args
    index = _read_json(index_path)
    index["chain_head_sha256"] = "0" * 64
    _write_json(index_path, index)


def _t006_add_unindexed_run(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del index_path, parent, args
    source = repo / FEATURE_REL / "evidence/local/runs/S001-import-direction/GREEN"
    target = repo / FEATURE_REL / "evidence/local/runs/S999-extra/GREEN"
    shutil.copytree(source, target)


def _t006_duplicate_record(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, parent, args
    index = _read_json(index_path)
    index["records"].append(_clone_json(index["records"][-1]))
    _rehash_v2(index)
    _write_json(index_path, index)


def _t006_wrong_record_order(repo: Path, index_path: Path, parent: Path, args: list[str]) -> None:
    del repo, parent, args
    index = _read_json(index_path)
    index["records"][-1], index["records"][-2] = (
        index["records"][-2],
        index["records"][-1],
    )
    _rehash_v2(index)
    _write_json(index_path, index)


def _t006_reject_cases() -> tuple[tuple[str, Any], ...]:
    return (
        ("missing index RED", _t006_remove_index_red),
        ("partial index RED", _t006_remove_tree),
        ("mutated index RED artifact", _t006_mutate_stdout),
        ("bad combined aggregate", _t006_bad_combined),
        ("bad review id", _t006_bad_review),
        ("reused review id", _t006_reused_review),
        ("corrupt prior index", _t006_corrupt_prior),
        ("extra unindexed run", _t006_add_unindexed_run),
        ("duplicate prior record", _t006_duplicate_record),
        ("wrong prior record order", _t006_wrong_record_order),
    )


def _load_frontier_checker() -> Any:
    module_name = "f151_post_t006_frontier_checker"
    spec = importlib.util.spec_from_file_location(module_name, CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _frontier_contract(checker: Any) -> tuple[dict[str, Any], Any]:
    contracts = checker.parse_rgr(REPO_ROOT)
    frontier = getattr(checker, "manifest_record_candidates", None)
    expected_tasks = {
        "S013-complexity-snapshot": {
            "RED": "T013",
            "GREEN": "T013",
            "REFACTOR": "T013",
        },
        "S015-route-url": {"RED": "T015", "GREEN": "T015", "REFACTOR": "T015"},
        "S015-route-auth": {"RED": "T015", "GREEN": "T015", "REFACTOR": "T015"},
        "S088-shutdown": {"RED": "T088", "GREEN": "T088", "REFACTOR": "T089"},
        "S100-workflow": {"RED": "T100", "GREEN": "T101", "REFACTOR": "T102"},
    }
    issues = [
        slice_id
        for slice_id, tasks in expected_tasks.items()
        if contracts.get(slice_id, {}).get("tasks") != tasks
    ]
    if not callable(frontier) or issues:
        detail = "missing manifest frontier seam"
        if issues:
            detail += "; unsupported RGR task syntax rows=" + ",".join(issues)
        pytest.fail(f"{POST_T006_FRONTIER_ORACLE}: {detail}", pytrace=False)
    return contracts, frontier


def _formal_identity(task_id: str, slice_id: str, phase: str) -> tuple[str, ...]:
    return ("formal-rgr", task_id, slice_id, phase)


def _completed_phase_keys(records: list[dict[str, Any]]) -> set[tuple[str, ...]]:
    return {
        (str(record["task_id"]), str(record["slice_id"]), str(record["phase"]))
        for record in records
    }


def _required_phase_keys(contracts: dict[str, Any], before_task: int) -> set[tuple[str, ...]]:
    return {
        (str(task_id), slice_id, phase)
        for slice_id, contract in contracts.items()
        for phase, task_id in contract["tasks"].items()
        if int(str(task_id)[1:]) < before_task
    }


def _frontier_candidates(
    frontier: Any, contracts: dict[str, Any], completed: set[tuple[str, ...]]
) -> set[tuple[str, ...]]:
    return {tuple(item) for item in frontier(contracts, completed)}


def _assert_fixed_frontier_prefix(
    checker: Any, contracts: dict[str, Any], index: dict[str, Any], anchor: dict[str, Any]
) -> None:
    records = index["records"]
    for stop in range(8, len(records) + 1):
        partial = {**index, "records": [dict(item) for item in records[:stop]]}
        checker.validate_record_order(partial, anchor, contracts)
    reordered = {**index, "records": [dict(item) for item in records]}
    reordered["records"][0], reordered["records"][1] = (
        reordered["records"][1],
        reordered["records"][0],
    )
    with pytest.raises(checker.GateFailure):
        checker.validate_record_order(reordered, anchor, contracts)
    replaced = {**index, "records": [dict(item) for item in records]}
    replaced["records"][0]["slice_id"] = "S999-replaced-prefix"
    with pytest.raises(checker.GateFailure):
        checker.validate_record_order(replaced, anchor, contracts)


def _assert_post_t006_candidates(
    frontier: Any, contracts: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    completed = _completed_phase_keys(records)
    s007_red = _formal_identity("T007", "S007-local-coverage", "RED")
    s008_red = _formal_identity("T008", "S008-fresh-exempt", "RED")
    s007_green = _formal_identity("T009", "S007-local-coverage", "GREEN")
    s008_green = _formal_identity("T009", "S008-fresh-exempt", "GREEN")
    assert _frontier_candidates(frontier, contracts, completed) == {s007_red}
    assert s008_red not in _frontier_candidates(frontier, contracts, completed)
    assert s007_green not in _frontier_candidates(frontier, contracts, completed)
    completed.add(s007_red[1:])
    assert _frontier_candidates(frontier, contracts, completed) == {s008_red}
    assert s007_red not in _frontier_candidates(frontier, contracts, completed)
    assert _formal_identity("T007", "S999-unknown", "RED") not in (
        _frontier_candidates(frontier, contracts, completed)
    )
    assert _formal_identity("T007", "S007-local-coverage", "UNKNOWN") not in (
        _frontier_candidates(frontier, contracts, completed)
    )
    completed.add(s008_red[1:])
    assert _frontier_candidates(frontier, contracts, completed) == {
        s007_green,
        s008_green,
    }


def _expected_manifest_candidates(
    contracts: dict[str, Any], completed: set[tuple[str, ...]]
) -> set[tuple[str, ...]]:
    eligible: list[tuple[str, ...]] = []
    for slice_id, contract in contracts.items():
        for phase in ("RED", "GREEN", "REFACTOR"):
            task_id = contract["tasks"].get(phase)
            if task_id is not None and (task_id, slice_id, phase) not in completed:
                eligible.append(_formal_identity(task_id, slice_id, phase))
                break
    if not eligible:
        return set()
    minimum = min(int(identity[1][1:]) for identity in eligible)
    return {identity for identity in eligible if int(identity[1][1:]) == minimum}


def _assert_current_manifest_candidates(
    frontier: Any, contracts: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    completed = _completed_phase_keys(records)
    expected = _expected_manifest_candidates(contracts, completed)
    assert _frontier_candidates(frontier, contracts, completed) == expected


def _assert_same_task_interleaving(frontier: Any, contracts: dict[str, Any]) -> None:
    completed = _required_phase_keys(contracts, 15)
    url_red = _formal_identity("T015", "S015-route-url", "RED")
    url_green = _formal_identity("T015", "S015-route-url", "GREEN")
    url_refactor = _formal_identity("T015", "S015-route-url", "REFACTOR")
    auth_red = _formal_identity("T015", "S015-route-auth", "RED")
    assert _frontier_candidates(frontier, contracts, completed) == {url_red, auth_red}
    completed.add(url_red[1:])
    assert _frontier_candidates(frontier, contracts, completed) == {
        url_green,
        auth_red,
    }
    completed.add(url_green[1:])
    assert _frontier_candidates(frontier, contracts, completed) == {
        url_refactor,
        auth_red,
    }


def _assert_phase_task_sequence(
    frontier: Any,
    contracts: dict[str, Any],
    identities: tuple[tuple[str, ...], ...],
) -> None:
    first_task = int(identities[0][1][1:])
    completed = _required_phase_keys(contracts, first_task)
    for identity in identities:
        assert _frontier_candidates(frontier, contracts, completed) == {identity}
        completed.add(identity[1:])


def _assert_multi_task_rgr_syntaxes(frontier: Any, contracts: dict[str, Any]) -> None:
    _assert_phase_task_sequence(
        frontier,
        contracts,
        (
            _formal_identity("T088", "S088-shutdown", "RED"),
            _formal_identity("T088", "S088-shutdown", "GREEN"),
            _formal_identity("T089", "S088-shutdown", "REFACTOR"),
        ),
    )
    _assert_phase_task_sequence(
        frontier,
        contracts,
        (
            _formal_identity("T100", "S100-workflow", "RED"),
            _formal_identity("T101", "S100-workflow", "GREEN"),
            _formal_identity("T102", "S100-workflow", "REFACTOR"),
        ),
    )


def _assert_non_rgr_rows_are_excluded(contracts: dict[str, Any]) -> None:
    excluded = (
        "S017-namespace-atomic",
        "S046-bench",
        "S047-retirement-atomic",
        "S080-inline-reply",
    )
    assert all(contracts[slice_id]["tasks"] == {} for slice_id in excluded)


def _assert_through_task_semantics(
    checker: Any, contracts: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    historic = records[:HISTORIC_FORMAL_PREFIX_COUNT]
    checker.validate_through_task(historic, "T006", contracts)
    with pytest.raises(checker.GateFailure):
        checker.validate_through_task(historic[:-1], "T006", contracts)
    reordered = [dict(item) for item in historic]
    reordered[-1], reordered[-2] = reordered[-2], reordered[-1]
    with pytest.raises(checker.GateFailure):
        checker.validate_through_task(reordered, "T006", contracts)
    prior = [record for record in records if int(str(record["task_id"])[1:]) <= 14]
    completed = _completed_phase_keys(prior)
    missing = sorted(_required_phase_keys(contracts, 15) - completed)
    prior = [*prior, *(_identity_record(item) for item in missing)]
    checker.validate_through_task(prior, "T014", contracts)
    with pytest.raises(checker.GateFailure):
        checker.validate_through_task(prior[:-1], "T014", contracts)
    later = _identity_record(("T015", "S015-route-url", "RED"))
    with pytest.raises(checker.GateFailure):
        checker.validate_through_task([*prior, later], "T014", contracts)


def _identity_record(identity: tuple[str, ...]) -> dict[str, str]:
    task_id, slice_id, phase = identity
    return {
        "lifecycle_type": "formal-rgr",
        "task_id": task_id,
        "slice_id": slice_id,
        "phase": phase,
    }


def _formal_recording_junit(
    nodeids: list[str],
    failing_nodeids: list[str],
    oracle: str,
    *,
    error_node: str | None = None,
    skipped_node: str | None = None,
    rerun_node: str | None = None,
) -> str:
    cases: list[str] = []
    for nodeid in nodeids:
        test_name = nodeid.rsplit("::", 1)[-1]
        body = ""
        if nodeid in failing_nodeids:
            body = f'<failure message="{oracle}">{oracle}</failure>'
        if nodeid == error_node:
            body = '<error message="fixture error">fixture error</error>'
        if nodeid == skipped_node:
            body = '<skipped message="fixture skip" />'
        if nodeid == rerun_node:
            body += '<rerunFailure message="fixture rerun">fixture rerun</rerunFailure>'
        cases.append(f'<testcase classname="fixture" name="{test_name}">{body}</testcase>')
    failures = (
        len(failing_nodeids)
        - int(error_node in failing_nodeids)
        - int(skipped_node in failing_nodeids)
    )
    suite = (
        f'<testsuite name="pytest" tests="{len(nodeids)}" failures="{failures}" '
        f'errors="{int(error_node is not None)}" skipped="{int(skipped_node is not None)}">'
        f"{''.join(cases)}</testsuite>"
    )
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites>{suite}</testsuites>'


def _formal_recording_fixture(
    root: Path, checker: Any, phase: str
) -> tuple[Path, Path, dict[str, Any], list[str]]:
    repo = _seed_repo(root)
    contract = checker.parse_rgr(repo)["S007-local-coverage"]
    nodeids = list(contract["nodeids"])
    run = repo / FEATURE_REL / "evidence/local/runs/S007-local-coverage" / phase
    run.mkdir(parents=True)
    argv, pythonpath = checker.formal_command(repo, run, nodeids)
    exit_code = 1 if phase == "RED" else 0
    invocation, tree = checker.formal_metadata(
        repo,
        "S007-local-coverage",
        phase,
        "HEAD",
        contract,
        argv,
        pythonpath,
        "2026-07-21T00:03:00Z",
        "2026-07-21T00:03:01Z",
        exit_code,
    )
    _require_formal_offline_invocation(invocation, argv, pythonpath)
    completed = subprocess.CompletedProcess(argv, exit_code, b"formal fixture\n", b"")
    checker.write_formal_artifacts(run, completed, invocation, tree)
    failing = nodeids if phase == "RED" else []
    _write(run / "junit.xml", _formal_recording_junit(nodeids, failing, contract["oracle"]))
    return repo, run, contract, nodeids


def _formal_exact_command(env: dict[str, str], argv: list[str]) -> str:
    assignments = [f"{key}={env[key]}" for key in FORMAL_ENV_ORDER if key in env]
    return shlex.join(["env", *assignments, *argv])


def _require_formal_offline_invocation(
    invocation: dict[str, Any], argv: list[str], pythonpath: str
) -> None:
    expected_env = {
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": pythonpath,
        FORMAL_OFFLINE_ENV_KEY: "True",
    }
    if invocation.get("env") != expected_env or invocation.get(
        "exact_command"
    ) != _formal_exact_command(expected_env, argv):
        pytest.fail(FORMAL_RED_RECORDING_ORACLE, pytrace=False)


def _assert_historic_formal_invocations_readable(checker: Any) -> None:
    index = _read_json(FEATURE_ROOT / "evidence/evidence-index.v2.json")
    anchor = _read_json(FEATURE_ROOT / "evidence/bootstrap-anchor.v1.json")
    contracts = checker.parse_rgr(REPO_ROOT)
    _assert_formal_invocation_versions(checker, REPO_ROOT, index, anchor, contracts)


def _assert_formal_invocation_versions(
    checker: Any,
    repo: Path,
    index: dict[str, Any],
    anchor: dict[str, Any],
    contracts: dict[str, Any],
) -> None:
    records = index["records"]
    fixed = checker.fixed_record_identities(anchor)
    assert len(fixed) == HISTORIC_FORMAL_PREFIX_COUNT
    assert len(records) >= HISTORIC_FORMAL_PREFIX_COUNT
    prefix = records[:HISTORIC_FORMAL_PREFIX_COUNT]
    identities = [
        tuple(record[key] for key in ("lifecycle_type", "task_id", "slice_id", "phase"))
        for record in prefix
    ]
    assert tuple(identities) == fixed
    assert prefix[-1]["record_sha256"] == HISTORIC_FORMAL_PREFIX_HEAD
    checker.validate_record_order(index, anchor, contracts)
    historic_keys = {"PYTHONNOUSERSITE", "PYTHONPATH"}
    for record in prefix:
        invocation_path = _formal_invocation_path(repo, record)
        assert set(record["env"]) == historic_keys
        assert set(_read_json(invocation_path)["env"]) == historic_keys
        checker.validate_v2_record(repo, record)
    for record in records[HISTORIC_FORMAL_PREFIX_COUNT:]:
        if record["lifecycle_type"] == "formal-rgr":
            _assert_new_formal_invocation(repo, record)
        checker.validate_v2_record(repo, record)
    checker.validate_record_chain(index)


def _formal_invocation_path(repo: Path, record: dict[str, Any]) -> Path:
    return next(
        repo / path for path in record["artifact_paths"] if Path(path).name == "invocation.json"
    )


def _assert_new_formal_invocation(repo: Path, record: dict[str, Any]) -> None:
    invocation = _read_json(_formal_invocation_path(repo, record))
    pythonpath = record["env"].get("PYTHONPATH")
    assert isinstance(pythonpath, str) and pythonpath
    expected_env = {
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": pythonpath,
        FORMAL_OFFLINE_ENV_KEY: "True",
    }
    assert record["env"] == expected_env
    assert invocation["env"] == expected_env
    assert invocation["exact_command"] == _formal_exact_command(expected_env, invocation["argv"])


def _formal_recording_or_missing(
    checker: Any,
    fixture: tuple[Path, Path, dict[str, Any], list[str]],
) -> dict[str, Any]:
    try:
        return _formal_recording_record(checker, fixture)
    except checker.GateFailure as exc:
        missing_tokens = ("EVIDENCE_EXIT_OR_NODE_MISMATCH", "FORMAL_RUN_NOT_GREEN")
        if not any(token in str(exc) for token in missing_tokens):
            raise
        pytest.fail(FORMAL_RED_RECORDING_ORACLE, pytrace=False)


def _historic_prefix_repo(root: Path) -> Path:
    repo = _seed_repo(root)
    live_index = _read_json(FEATURE_ROOT / "evidence/evidence-index.v2.json")
    prefix = _clone_json(live_index["records"][:HISTORIC_FORMAL_PREFIX_COUNT])
    historic_index = {**_clone_json(live_index), "records": prefix}
    historic_index["chain_head_sha256"] = prefix[-1]["record_sha256"]
    _write_json(repo / FEATURE_REL / "evidence/evidence-index.v2.json", historic_index)
    for record in prefix:
        for relative in record["artifact_paths"]:
            source = REPO_ROOT / relative
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return repo


def _formal_prefix_with_tail(
    root: Path, checker: Any
) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    repo = _historic_prefix_repo(root / "prefix")
    fixture = _formal_recording_fixture(root / "tail", checker, "RED")
    record = _formal_recording_or_missing(checker, fixture)
    relative_run = fixture[1].relative_to(fixture[0])
    shutil.copytree(fixture[1], repo / relative_run)
    index_path = repo / FEATURE_REL / "evidence/evidence-index.v2.json"
    index = _read_json(index_path)
    record["previous_record_sha256"] = index["chain_head_sha256"]
    record["record_sha256"] = checker.record_hash(record)
    index["records"].append(record)
    index["chain_head_sha256"] = record["record_sha256"]
    anchor = _read_json(repo / FEATURE_REL / "evidence/bootstrap-anchor.v1.json")
    return repo, index, anchor, checker.parse_rgr(repo)


def _assert_synthetic_formal_tail_controls(root: Path, checker: Any) -> None:
    repo, index, anchor, contracts = _formal_prefix_with_tail(root, checker)
    _assert_formal_invocation_versions(checker, repo, index, anchor, contracts)
    for label, value in (("missing", None), ("false", "False")):
        mutated = _clone_json(index)
        tail_env = mutated["records"][-1]["env"]
        if value is None:
            tail_env.pop(FORMAL_OFFLINE_ENV_KEY)
        else:
            tail_env[FORMAL_OFFLINE_ENV_KEY] = value
        assert mutated != index, f"{label} tail env fixture has no observable delta"
        with pytest.raises(AssertionError):
            _assert_formal_invocation_versions(checker, repo, mutated, anchor, contracts)


def _formal_recording_record(
    checker: Any,
    fixture: tuple[Path, Path, dict[str, Any], list[str]],
) -> dict[str, Any]:
    repo, run, contract, nodeids = fixture
    return checker.formal_record_from_run(
        repo,
        run,
        "S007-local-coverage",
        run.name,
        "HEAD",
        contract,
        nodeids,
    )


def _assert_formal_recording_record(
    record: dict[str, Any], phase: str, nodeids: list[str], oracle: str
) -> None:
    is_red = phase == "RED"
    assert record["lifecycle_type"] == "formal-rgr"
    assert record["phase"] == phase
    assert record["exit_code"] == int(is_red)
    assert record["expected_oracle_id"] == (oracle if is_red else None)
    assert record["expected_failing_nodeids"] == (nodeids if is_red else [])
    assert record["observed_nodeids"] == nodeids
    assert record["selected_count"] == len(nodeids)
    assert record["passed_count"] == (0 if is_red else len(nodeids))
    assert record["failed_count"] == (len(nodeids) if is_red else 0)
    assert record["error_count"] == record["skipped_count"] == record["rerun_count"] == 0


def _formal_recording_set_exit(run: Path, exit_code: int) -> None:
    invocation = _read_json(run / "invocation.json")
    invocation["exit_code"] = exit_code
    _write_json(run / "invocation.json", invocation)
    _write(run / "exit-code.txt", f"{exit_code}\n")


def _formal_red_exit_zero(run: Path, nodeids: list[str], oracle: str) -> None:
    del nodeids, oracle
    _formal_recording_set_exit(run, 0)


def _formal_red_exit_two(run: Path, nodeids: list[str], oracle: str) -> None:
    del nodeids, oracle
    _formal_recording_set_exit(run, 2)


def _formal_red_partial_failure(run: Path, nodeids: list[str], oracle: str) -> None:
    _write(run / "junit.xml", _formal_recording_junit(nodeids, nodeids[:-1], oracle))


def _formal_red_extra_failure(run: Path, nodeids: list[str], oracle: str) -> None:
    extra = "octoagent/tests/gate/test_extra.py::TestExtra::test_unexpected"
    _write(run / "junit.xml", _formal_recording_junit([*nodeids, extra], [*nodeids, extra], oracle))


def _formal_red_missing_oracle(run: Path, nodeids: list[str], oracle: str) -> None:
    del oracle
    _write(run / "junit.xml", _formal_recording_junit(nodeids, nodeids, ""))


def _formal_red_wrong_oracle(run: Path, nodeids: list[str], oracle: str) -> None:
    del oracle
    _write(run / "junit.xml", _formal_recording_junit(nodeids, nodeids, "WRONG_ORACLE"))


def _formal_red_error(run: Path, nodeids: list[str], oracle: str) -> None:
    _write(
        run / "junit.xml",
        _formal_recording_junit(nodeids, nodeids, oracle, error_node=nodeids[0]),
    )


def _formal_red_skip(run: Path, nodeids: list[str], oracle: str) -> None:
    _write(
        run / "junit.xml",
        _formal_recording_junit(nodeids, nodeids, oracle, skipped_node=nodeids[0]),
    )


def _formal_red_rerun(run: Path, nodeids: list[str], oracle: str) -> None:
    _write(
        run / "junit.xml",
        _formal_recording_junit(nodeids, nodeids, oracle, rerun_node=nodeids[0]),
    )


def _formal_red_stderr(run: Path, nodeids: list[str], oracle: str) -> None:
    del nodeids, oracle
    _write(run / "stderr.txt", "unexpected stderr\n")


def _formal_env_missing(run: Path, nodeids: list[str], oracle: str) -> None:
    del nodeids, oracle
    invocation = _read_json(run / "invocation.json")
    invocation["env"].pop(FORMAL_OFFLINE_ENV_KEY)
    invocation["exact_command"] = _formal_exact_command(invocation["env"], invocation["argv"])
    _write_json(run / "invocation.json", invocation)


def _formal_env_false(run: Path, nodeids: list[str], oracle: str) -> None:
    del nodeids, oracle
    invocation = _read_json(run / "invocation.json")
    invocation["env"][FORMAL_OFFLINE_ENV_KEY] = "False"
    invocation["exact_command"] = _formal_exact_command(invocation["env"], invocation["argv"])
    _write_json(run / "invocation.json", invocation)


def _formal_env_command_mismatch(run: Path, nodeids: list[str], oracle: str) -> None:
    del nodeids, oracle
    invocation = _read_json(run / "invocation.json")
    invocation["exact_command"] = _replace_exact(
        invocation["exact_command"],
        f"{FORMAL_OFFLINE_ENV_KEY}=True",
        f"{FORMAL_OFFLINE_ENV_KEY}=False",
        expected_count=1,
    )
    _write_json(run / "invocation.json", invocation)


def _formal_green_failure(run: Path, nodeids: list[str], oracle: str) -> None:
    _write(run / "junit.xml", _formal_recording_junit(nodeids, nodeids, oracle))


def _formal_recording_reject_cases() -> tuple[tuple[str, str, Any], ...]:
    return (
        ("red-exit-zero", "RED", _formal_red_exit_zero),
        ("red-exit-two", "RED", _formal_red_exit_two),
        ("red-partial-failure", "RED", _formal_red_partial_failure),
        ("red-extra-failure", "RED", _formal_red_extra_failure),
        ("red-missing-oracle", "RED", _formal_red_missing_oracle),
        ("red-wrong-oracle", "RED", _formal_red_wrong_oracle),
        ("red-error", "RED", _formal_red_error),
        ("red-skip", "RED", _formal_red_skip),
        ("red-rerun", "RED", _formal_red_rerun),
        ("red-stderr", "RED", _formal_red_stderr),
        ("formal-env-missing", "RED", _formal_env_missing),
        ("formal-env-false", "RED", _formal_env_false),
        ("formal-env-command-mismatch", "RED", _formal_env_command_mismatch),
        ("green-failure", "GREEN", _formal_green_failure),
    )


def _formal_recording_reject_case(
    root: Path, checker: Any, label: str, phase: str, mutator: Any
) -> None:
    accept = _formal_recording_fixture(root / "accept", checker, phase)
    accepted = _formal_recording_record(checker, accept)
    _assert_formal_recording_record(accepted, phase, accept[3], accept[2]["oracle"])
    reject = _formal_recording_fixture(root / "reject", checker, phase)
    mutator(reject[1], reject[3], reject[2]["oracle"])
    before = {name: _sha(reject[1] / name) for name in RUN_ARTIFACT_NAMES}
    with pytest.raises(checker.GateFailure):
        _formal_recording_record(checker, reject)
    after = {name: _sha(reject[1] / name) for name in RUN_ARTIFACT_NAMES}
    assert before == after, f"{label}: rejected formal record mutated run bytes"


DEPENDENCY_HATCHLING = "dependency:root-dev-build-scaffold/key:hatchling==1.29.0"
DEPENDENCY_LOCK_HATCHLING = "dependency:lock-dev-build-scaffold/key:hatchling==1.29.0"
DEPENDENCY_SDK = "dependency:root-sdk-retirement/key:octoagent-sdk"
DEPENDENCY_LOCK_SDK = "dependency:lock-sdk-retirement/key:octoagent-sdk"
DEPENDENCY_LOCK_PROXY = "dependency:lock-provider-gateway-rehome/key:litellm-proxy-closure"
DEPENDENCY_PROXY_PACKAGES = (
    "azure-core",
    "azure-identity",
    "azure-storage-blob",
    "backoff",
    "boto3",
    "botocore",
    "croniter",
    "dnspython",
    "email-validator",
    "fastapi-sso",
    "gunicorn",
    "isodate",
    "jmespath",
    "litellm-enterprise",
    "litellm-proxy-extras",
    "msal",
    "msal-extensions",
    "oauthlib",
    "orjson",
    "polars",
    "polars-runtime-32",
    "pynacl",
    "pyroscope-io",
    "pytz",
    "redis",
    "rq",
    "s3transfer",
    "soundfile",
    "uvloop",
    "websockets",
)


def _dependency_pyproject(*, hatchling: bool, sdk: bool) -> str:
    dev = [
        item
        for item, enabled in (("octoagent-sdk", sdk), ("hatchling==1.29.0", hatchling))
        if enabled
    ]
    quoted = ", ".join(json.dumps(item) for item in dev)
    sources = "octoagent-sdk = { workspace = true }\n" if sdk else ""
    members = '["packages/sdk"]' if sdk else "[]"
    return (
        '[project]\nname = "octoagent"\nversion = "0.1.0"\n'
        f"dependencies = []\n\n[dependency-groups]\ndev = [{quoted}]\n\n"
        f"[tool.uv.sources]\n{sources}\n[tool.uv.workspace]\nmembers = {members}\n"
    )


def _dependency_lock(
    *,
    hatchling: bool,
    sdk: bool,
    proxy: bool = True,
    hatchling_version: str = "1.29.0",
) -> str:
    members = ["octoagent", *(("octoagent-sdk",) if sdk else ())]
    dev_names = [
        item for item, enabled in (("octoagent-sdk", sdk), ("hatchling", hatchling)) if enabled
    ]
    member_text = ", ".join(json.dumps(item) for item in members)
    dev_text = ", ".join(f'{{ name = "{item}" }}' for item in dev_names)
    metadata = []
    if sdk:
        metadata.append('{ name = "octoagent-sdk", editable = "packages/sdk" }')
    if hatchling:
        metadata.append(f'{{ name = "hatchling", specifier = "=={hatchling_version}" }}')
    metadata_text = ", ".join(metadata)
    packages = []
    if sdk:
        packages.append(
            '[[package]]\nname = "octoagent-sdk"\nversion = "0.1.0"\n'
            'source = { editable = "packages/sdk" }\n'
        )
    if hatchling:
        packages.append(
            f'[[package]]\nname = "hatchling"\nversion = "{hatchling_version}"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
            'dependencies = [{ name = "packaging" }, { name = "pathspec" }, '
            '{ name = "pluggy" }, { name = "trove-classifiers" }]\n'
        )
        for name, version in (
            ("packaging", "25.0"),
            ("pathspec", "0.12.1"),
            ("pluggy", "1.6.0"),
            ("trove-classifiers", "2026.1.14.14"),
        ):
            packages.append(
                f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
                'source = { registry = "https://pypi.org/simple" }\n'
            )
    if proxy:
        packages.extend(
            f'[[package]]\nname = "{name}"\nversion = "1.0.0"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
            for name in DEPENDENCY_PROXY_PACKAGES
        )
    return (
        'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n\n'
        f"[manifest]\nmembers = [{member_text}]\n\n"
        '[[package]]\nname = "octoagent"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
        f"[package.dev-dependencies]\ndev = [{dev_text}]\n"
        f"[package.metadata]\nrequires-dev = {{ dev = [{metadata_text}] }}\n\n"
        + "\n".join(packages)
    )


def _dependency_scope() -> dict[str, Any]:
    return {
        "selector_grammar": {"anchor_kinds": ["function", "dependency"]},
        "symbol_partitions": [
            _dependency_root_partition(),
            _dependency_lock_partition(),
        ],
    }


def _dependency_root_partition() -> dict[str, Any]:
    return {
        "path": "octoagent/pyproject.toml",
        "members": [
            {
                "slice": "S012-standard-backend-scaffold",
                "ast_or_key_selectors": [DEPENDENCY_HATCHLING],
            },
            {
                "slice": "S047-retirement-atomic",
                "ast_or_key_selectors": [DEPENDENCY_SDK],
            },
        ],
        "declared_states": {
            "pre_T012": {
                "required_nonempty_selectors": [DEPENDENCY_SDK],
                "required_absent_selectors": [DEPENDENCY_HATCHLING],
            },
            "T012_target": {
                "required_nonempty_selectors": [DEPENDENCY_HATCHLING, DEPENDENCY_SDK],
                "required_absent_selectors": [],
            },
            "T048_target": {
                "required_nonempty_selectors": [DEPENDENCY_HATCHLING],
                "required_absent_selectors": [DEPENDENCY_SDK],
            },
        },
        "transitions": [
            {
                "from": "pre_T012",
                "to": "T012_target",
                "required_add_selectors": [DEPENDENCY_HATCHLING],
                "required_delete_selectors": [],
            },
            {
                "from": "T012_target",
                "to": "T048_target",
                "required_add_selectors": [],
                "required_delete_selectors": [DEPENDENCY_SDK],
            },
        ],
    }


def _dependency_lock_partition() -> dict[str, Any]:
    return {
        "path": "octoagent/uv.lock",
        "members": [
            {
                "slice": "S012-standard-backend-scaffold",
                "ast_or_key_selectors": [DEPENDENCY_LOCK_HATCHLING],
            },
            {
                "slice": "S017-namespace-atomic",
                "ast_or_key_selectors": [DEPENDENCY_LOCK_PROXY],
            },
            {
                "slice": "S047-retirement-atomic",
                "ast_or_key_selectors": [DEPENDENCY_LOCK_SDK],
            },
        ],
        "declared_states": {
            "pre_T012": {
                "required_nonempty_selectors": [DEPENDENCY_LOCK_SDK, DEPENDENCY_LOCK_PROXY],
                "required_absent_selectors": [DEPENDENCY_LOCK_HATCHLING],
            },
            "T012_target": {
                "required_nonempty_selectors": [
                    DEPENDENCY_LOCK_HATCHLING,
                    DEPENDENCY_LOCK_SDK,
                    DEPENDENCY_LOCK_PROXY,
                ],
                "required_absent_selectors": [],
            },
            "T023_target": {
                "required_nonempty_selectors": [DEPENDENCY_LOCK_HATCHLING, DEPENDENCY_LOCK_SDK],
                "required_absent_selectors": [DEPENDENCY_LOCK_PROXY],
            },
            "T048_target": {
                "required_nonempty_selectors": [DEPENDENCY_LOCK_HATCHLING],
                "required_absent_selectors": [DEPENDENCY_LOCK_SDK, DEPENDENCY_LOCK_PROXY],
            },
        },
        "transitions": [
            {
                "from": "pre_T012",
                "to": "T012_target",
                "required_add_selectors": [DEPENDENCY_LOCK_HATCHLING],
                "required_delete_selectors": [],
            },
            {
                "from": "T012_target",
                "to": "T023_target",
                "required_add_selectors": [],
                "required_delete_selectors": [DEPENDENCY_LOCK_PROXY],
            },
            {
                "from": "T023_target",
                "to": "T048_target",
                "required_add_selectors": [],
                "required_delete_selectors": [DEPENDENCY_LOCK_SDK],
            },
        ],
    }


def _dependency_write_state(repo: Path, *, hatchling: bool, sdk: bool, proxy: bool) -> None:
    _write(
        repo / "octoagent/pyproject.toml",
        _dependency_pyproject(hatchling=hatchling, sdk=sdk),
    )
    _write(
        repo / "octoagent/uv.lock",
        _dependency_lock(hatchling=hatchling, sdk=sdk, proxy=proxy),
    )


def _dependency_fixture(root: Path, transition: str = "add") -> tuple[Path, Path]:
    repo = _seed_repo(root)
    states = {
        "add": ((False, True, True), (True, True, True)),
        "proxy": ((True, True, True), (True, True, False)),
        "delete": ((True, True, False), (True, False, False)),
    }
    before, after = states[transition]
    _dependency_write_state(repo, hatchling=before[0], sdk=before[1], proxy=before[2])
    scope_path = repo / "dependency-scope.json"
    _write_json(scope_path, _dependency_scope())
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", f"dependency {transition} base")
    _dependency_write_state(repo, hatchling=after[0], sdk=after[1], proxy=after[2])
    return repo, scope_path


def _dependency_cumulative_fixture(root: Path) -> tuple[Path, Path]:
    repo = _seed_repo(root)
    _dependency_write_state(repo, hatchling=False, sdk=True, proxy=True)
    scope_path = repo / "dependency-scope.json"
    _write_json(scope_path, _dependency_scope())
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "dependency cumulative pre-T012 base")
    _dependency_write_state(repo, hatchling=True, sdk=True, proxy=False)
    return repo, scope_path


def _dependency_checker_accepts(checker: Any, repo: Path, scope_path: Path) -> bool:
    scope = _read_json(scope_path)
    parameters = inspect.signature(checker.check_rgr_selectors).parameters
    try:
        if len(parameters) == 1:
            checker.check_rgr_selectors(scope)
        elif len(parameters) == 2:
            checker.check_rgr_selectors(repo, scope)
        else:
            return False
    except checker.GateFailure:
        return False
    return True


def _dependency_clean_control(checker: Any, root: Path) -> bool:
    repo = _seed_repo(root)
    _write(repo / "fixture.py", "def target() -> None:\n    return None\n")
    scope_path = repo / "dependency-scope.json"
    _write_json(
        scope_path,
        {
            "selector_grammar": {"anchor_kinds": ["function", "dependency"]},
            "symbol_partitions": [
                {
                    "path": "fixture.py",
                    "members": [{"slice": "S-clean", "ast_or_key_selectors": ["function:target"]}],
                }
            ],
        },
    )
    return _dependency_checker_accepts(checker, repo, scope_path)


def _dependency_replace(path: Path, old: str, new: str, *, count: int = 1) -> None:
    original = path.read_text(encoding="utf-8")
    changed = _replace_exact(original, old, new, expected_count=count)
    path.write_text(changed, encoding="utf-8")


def _dependency_bad_group(repo: Path, scope: Path) -> None:
    del scope
    path = repo / "octoagent/pyproject.toml"
    _dependency_replace(path, "dependencies = []", 'dependencies = ["hatchling==1.29.0"]')
    _dependency_replace(
        path,
        'dev = ["octoagent-sdk", "hatchling==1.29.0"]',
        'dev = ["octoagent-sdk"]',
    )


def _dependency_bad_pin(repo: Path, scope: Path) -> None:
    del scope
    _dependency_replace(repo / "octoagent/pyproject.toml", "hatchling==1.29.0", "hatchling==1.28.0")


def _dependency_lock_missing(repo: Path, scope: Path) -> None:
    del scope
    path = repo / "octoagent/uv.lock"
    text_value = path.read_text(encoding="utf-8")
    start = text_value.index('[[package]]\nname = "hatchling"')
    end = text_value.index('[[package]]\nname = "packaging"', start)
    changed = text_value[:start] + text_value[end:]
    assert changed != text_value
    path.write_text(changed, encoding="utf-8")


def _dependency_lock_drift(repo: Path, scope: Path) -> None:
    del scope
    _dependency_replace(repo / "octoagent/uv.lock", 'version = "1.29.0"', 'version = "1.28.0"')


def _dependency_closure_missing(repo: Path, scope: Path) -> None:
    del scope
    path = repo / "octoagent/uv.lock"
    _dependency_replace(path, ', { name = "pathspec" }', "")


def _dependency_duplicate_owner(repo: Path, scope: Path) -> None:
    del repo
    payload = _read_json(scope)
    payload["symbol_partitions"][0]["members"].append(
        {"slice": "S999-duplicate", "ast_or_key_selectors": [DEPENDENCY_HATCHLING]}
    )
    _write_json(scope, payload)


def _dependency_alias_selector(repo: Path, scope: Path) -> None:
    del repo
    _dependency_replace(
        scope,
        DEPENDENCY_HATCHLING,
        DEPENDENCY_HATCHLING.replace("hatchling", "Hatchling"),
        count=5,
    )


def _dependency_unknown_selector(repo: Path, scope: Path) -> None:
    del repo
    _dependency_replace(scope, "root-dev-build-scaffold", "unknown-build-scaffold", count=5)


def _dependency_inverted_transition(repo: Path, scope: Path) -> None:
    del repo
    payload = _read_json(scope)
    transition = payload["symbol_partitions"][0]["transitions"][0]
    transition["from"], transition["to"] = transition["to"], transition["from"]
    _write_json(scope, payload)


def _dependency_unowned_key(repo: Path, scope: Path) -> None:
    del scope
    pyproject = repo / "octoagent/pyproject.toml"
    _dependency_replace(pyproject, '"hatchling==1.29.0"]', '"hatchling==1.29.0", "build==1.2.2"]')
    lock = repo / "octoagent/uv.lock"
    _dependency_replace(
        lock,
        '{ name = "hatchling" }]',
        '{ name = "hatchling" }, { name = "build" }]',
    )
    _dependency_replace(
        lock,
        '{ name = "hatchling", specifier = "==1.29.0" }] }',
        '{ name = "hatchling", specifier = "==1.29.0" }, '
        '{ name = "build", specifier = "==1.2.2" }] }',
    )
    addition = (
        '\n[[package]]\nname = "build"\nversion = "1.2.2"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    lock.write_text(lock.read_text(encoding="utf-8") + addition, encoding="utf-8")


def _dependency_delete_keeps_pyproject_sdk(repo: Path, scope: Path) -> None:
    del scope
    _write(
        repo / "octoagent/pyproject.toml",
        _dependency_pyproject(hatchling=True, sdk=True),
    )


def _dependency_delete_keeps_lock_sdk(repo: Path, scope: Path) -> None:
    del scope
    _write(
        repo / "octoagent/uv.lock",
        _dependency_lock(hatchling=True, sdk=True, proxy=False),
    )


def _dependency_delete_removes_hatchling_instead(repo: Path, scope: Path) -> None:
    del scope
    _dependency_write_state(repo, hatchling=False, sdk=True, proxy=False)


def _dependency_proxy_partial_delete(repo: Path, scope: Path) -> None:
    del scope
    path = repo / "octoagent/uv.lock"
    addition = (
        f'\n[[package]]\nname = "{DEPENDENCY_PROXY_PACKAGES[0]}"\n'
        'version = "1.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    )
    path.write_text(path.read_text(encoding="utf-8") + addition, encoding="utf-8")


def _dependency_proxy_wrong_closure(repo: Path, scope: Path) -> None:
    del repo
    payload = _read_json(scope)
    partition = payload["symbol_partitions"][1]
    replacement = DEPENDENCY_LOCK_PROXY.replace("litellm-proxy-closure", "litellm-proxy-partial")
    for member in partition["members"]:
        member["ast_or_key_selectors"] = [
            replacement if item == DEPENDENCY_LOCK_PROXY else item
            for item in member["ast_or_key_selectors"]
        ]
    for state in partition["declared_states"].values():
        for key in ("required_nonempty_selectors", "required_absent_selectors"):
            state[key] = [
                replacement if item == DEPENDENCY_LOCK_PROXY else item for item in state[key]
            ]
    for transition in partition["transitions"]:
        for key in ("required_add_selectors", "required_delete_selectors"):
            transition[key] = [
                replacement if item == DEPENDENCY_LOCK_PROXY else item for item in transition[key]
            ]
    _write_json(scope, payload)


def _dependency_proxy_extra_unowned(repo: Path, scope: Path) -> None:
    del scope
    path = repo / "octoagent/uv.lock"
    addition = (
        '\n[[package]]\nname = "proxy-lookalike"\nversion = "1.0.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    path.write_text(path.read_text(encoding="utf-8") + addition, encoding="utf-8")


def _dependency_proxy_wrong_owner(repo: Path, scope: Path) -> None:
    del repo
    payload = _read_json(scope)
    payload["symbol_partitions"][1]["members"][1]["slice"] = "S047-retirement-atomic"
    _write_json(scope, payload)


def _dependency_proxy_wrong_transition_order(repo: Path, scope: Path) -> None:
    del repo
    payload = _read_json(scope)
    transitions = payload["symbol_partitions"][1]["transitions"]
    transitions[1], transitions[2] = transitions[2], transitions[1]
    _write_json(scope, payload)


def _dependency_proxy_wrong_state(repo: Path, scope: Path) -> None:
    del repo
    payload = _read_json(scope)
    target = payload["symbol_partitions"][1]["declared_states"]["T023_target"]
    target["required_absent_selectors"].remove(DEPENDENCY_LOCK_PROXY)
    target["required_nonempty_selectors"].append(DEPENDENCY_LOCK_PROXY)
    _write_json(scope, payload)


def _dependency_reject_cases() -> tuple[tuple[str, str, Any], ...]:
    return (
        ("add", "wrong dependency group/runtime leak", _dependency_bad_group),
        ("add", "wrong Hatchling pin", _dependency_bad_pin),
        ("add", "lock package missing", _dependency_lock_missing),
        ("add", "lock version drift", _dependency_lock_drift),
        ("add", "Hatchling closure missing", _dependency_closure_missing),
        ("add", "same semantic key double owner", _dependency_duplicate_owner),
        ("add", "alias selector", _dependency_alias_selector),
        ("add", "unknown dependency selector", _dependency_unknown_selector),
        ("add", "base/final inversion", _dependency_inverted_transition),
        ("add", "changed semantic key unowned", _dependency_unowned_key),
        ("delete", "final pyproject retains SDK", _dependency_delete_keeps_pyproject_sdk),
        ("delete", "final lock retains SDK", _dependency_delete_keeps_lock_sdk),
        (
            "delete",
            "wrongly deletes Hatchling while retaining SDK",
            _dependency_delete_removes_hatchling_instead,
        ),
        ("proxy", "partial proxy closure delete", _dependency_proxy_partial_delete),
        ("proxy", "wrong proxy closure selector", _dependency_proxy_wrong_closure),
        ("proxy", "extra unowned proxy package", _dependency_proxy_extra_unowned),
        ("proxy", "wrong proxy closure owner", _dependency_proxy_wrong_owner),
        (
            "proxy",
            "wrong proxy transition order",
            _dependency_proxy_wrong_transition_order,
        ),
        ("proxy", "wrong proxy target state", _dependency_proxy_wrong_state),
    )


def _dependency_fixture_bytes(repo: Path, scope: Path) -> dict[str, str]:
    paths = (repo / "octoagent/pyproject.toml", repo / "octoagent/uv.lock", scope)
    return {str(path.relative_to(repo)): _sha(path) for path in paths}


def _dependency_reject_case(
    root: Path,
    checker: Any,
    transition: str,
    label: str,
    mutator: Any,
) -> str | None:
    repo, scope = _dependency_fixture(root, transition)
    if not _dependency_checker_accepts(checker, repo, scope):
        return f"{label}: valid accept control rejected"
    before = _dependency_fixture_bytes(repo, scope)
    mutator(repo, scope)
    after = _dependency_fixture_bytes(repo, scope)
    if before == after:
        return f"{label}: reject fixture has no observable semantic delta"
    accepted = _dependency_checker_accepts(checker, repo, scope)
    if _dependency_fixture_bytes(repo, scope) != after:
        return f"{label}: validation changed fixture bytes"
    if accepted:
        return f"{label}: invalid dependency transition accepted"
    return None


def _assert_atomic_source_delete_is_read_safe(root: Path) -> None:
    repo = _seed_repo(root)
    source = repo / "octoagent/packages/provider/src/octoagent/provider/dx/update_service.py"
    _write(source, "def legacy_update() -> None:\n    return None\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "atomic source before deletion")
    source.unlink()
    result = _invoke(repo, MANIFEST_ORACLE, "quality-smells", "clean")
    assert result.returncode == 0, result.stderr


def _t103_capabilities(checker: Any, oracle: str, *names: str) -> tuple[Any, ...]:
    capabilities = tuple(getattr(checker, name, None) for name in names)
    if not all(callable(capability) for capability in capabilities):
        pytest.fail(oracle, pytrace=False)
    return capabilities


def _t103_rerun_junit(path: Path, nodeid: str) -> None:
    test_name = nodeid.rsplit("::", 1)[-1]
    _write(
        path,
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="1" failures="0" '
        'errors="0" skipped="0">'
        f'<testcase classname="fixture" name="{test_name}">'
        '<rerunFailure message="fixture rerun">fixture rerun</rerunFailure>'
        "</testcase></testsuite></testsuites>",
    )


def _t103_quarantine_entry(identifier: str, path: str) -> dict[str, str]:
    return {
        "id": identifier,
        "path": path,
        "reason": "deterministic fixture reason",
        "owner": "f151-test",
        "review_after": "2026-08-13",
        "exit_criteria": "remove after deterministic root-cause fix",
    }


def _t103_machine_truth() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    index = _read_json(FEATURE_ROOT / "evidence/evidence-index.v2.json")
    lifecycle = _read_json(_manifest(REPO_ROOT, "artifact-lifecycle.v1.json"))
    producers = _read_json(_manifest(REPO_ROOT, "evidence-producers.v1.json"))
    return index, lifecycle, producers


def _documentation_fixture(root: Path) -> tuple[Path, dict[str, Any]]:
    repo = root / "repo"
    authority = _read_json(_manifest(REPO_ROOT, "authority-docs.v1.json"))
    paths = {row["path"] for row in authority["documents"]}
    paths.update(row["path"] for row in authority["index_derivation"]["root_candidates"])
    paths.add(str(INVENTORY_REL / "authority-docs.v1.json"))
    for relative in sorted(paths):
        source = REPO_ROOT / relative
        if source.is_file():
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return repo, authority


def _documentation_byte_map(repo: Path) -> dict[str, str]:
    return {
        str(path.relative_to(repo)): _sha(path)
        for path in sorted(repo.rglob("*"))
        if path.is_file()
    }


def _assert_documentation_rejects(
    checker: Any,
    validate: Any,
    root: Path,
    label: str,
    mutator: Any,
) -> None:
    repo, authority = _documentation_fixture(root / label)
    before = _documentation_byte_map(repo)
    mutator(repo, authority)
    after = _documentation_byte_map(repo)
    assert before != after, f"{label}: fixture has no observable delta"
    with pytest.raises(checker.GateFailure, match="DOCUMENTATION_AUTHORITY_DRIFT"):
        validate(repo, authority)
    assert _documentation_byte_map(repo) == after, f"{label}: validator changed bytes"


def _append_documentation(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    _write(path, path.read_text(encoding="utf-8") + text)


def _documentation_current_proxy(repo: Path, _authority: dict[str, Any]) -> None:
    _append_documentation(
        repo,
        "docs/blueprint/requirements.md",
        "\n## Current runtime\nLiteLLM Proxy is a required production component.\n",
    )


def _documentation_checked_retired_row(repo: Path, _authority: dict[str, Any]) -> None:
    _append_documentation(
        repo,
        "docs/blueprint/milestones.md",
        "\n| status | required runtime |\n|---|---|\n| ✅ | LiteLLM Proxy current |\n",
    )


def _documentation_current_mermaid(repo: Path, _authority: dict[str, Any]) -> None:
    _append_documentation(
        repo,
        "docs/blueprint/architecture-overview.md",
        "\n## Current production runtime\n```mermaid\ngraph LR\nGateway --> LiteLLMProxy\n```\n",
    )


def _documentation_unlisted_authority(repo: Path, _authority: dict[str, Any]) -> None:
    _append_documentation(
        repo,
        "docs/blueprint.md",
        "\n[Unlisted runtime truth](blueprint/unlisted-runtime.md)\n",
    )
    _write(
        repo / "docs/blueprint/unlisted-runtime.md",
        "# Current runtime\nGateway is the production application host.\n",
    )


class TestAtomicNamespaceRelocation:
    def test_manifest_accepts_exact_before_or_after_relocation_state(self) -> None:
        manifest = _read_json(FEATURE_ROOT / "inventories/namespace-migration.v1.json")
        moves = manifest["moves"]
        deletes = manifest["deletes"]
        source_paths = [REPO_ROOT / item["source"] for item in moves + deletes]
        target_paths = [REPO_ROOT / item["target"] for item in moves]
        source_count = sum(path.is_file() for path in source_paths)
        target_count = sum(path.is_file() for path in target_paths)
        before = source_count == 51 and target_count == 0
        relocated = source_count == 0 and target_count == 49
        if not (before or relocated):
            pytest.fail(
                "F151_ATOMIC_NAMESPACE_STATE_MISMATCH: "
                f"source={source_count}/51 target={target_count}/49",
                pytrace=False,
            )
        if before:
            return

        before_path = (
            FEATURE_ROOT / "evidence/local/atomic/S017-namespace-atomic/T017-BEFORE/"
            "atomic-namespace-before.v1.json"
        )
        snapshot = _read_json(before_path)
        assert snapshot["base_sha"] == manifest["base_sha"]
        assert snapshot["approved_hash_exceptions"] == manifest["approved_hash_exceptions"]
        assert len(snapshot["source_files"]) == 51
        for item in snapshot["source_files"]:
            content = base64.b64decode(item["content_b64"])
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            ast.parse(content.decode("utf-8"))
        for path in target_paths:
            text = path.read_text(encoding="utf-8")
            ast.parse(text)
            assert "octoagent.provider.dx" not in text
        assert all(not path.exists() for path in source_paths)


def test_retired_runtime_sdk_proxy_and_manifest_surfaces_are_absent() -> None:
    absent_paths = (
        REPO_ROOT / "octoagent/packages/sdk",
        REPO_ROOT / "octoagent/.env.litellm.example",
        REPO_ROOT / "skills/llm-config",
    )
    assert all(not path.exists() for path in absent_paths)

    checker = _load_frontier_checker()
    exact_paths = checker.scope_owned_paths(REPO_ROOT)
    owned_globs = checker.scope_owned_globs(REPO_ROOT)
    for relative_path in (
        "octoagent/packages/sdk/src/octoagent_sdk/_agent.py",
        "skills/llm-config/SKILL.md",
    ):
        assert checker.path_owned_by_scope(relative_path, exact_paths, owned_globs)

    project_path = REPO_ROOT / "octoagent/pyproject.toml"
    project_text = project_path.read_text(encoding="utf-8")
    project = tomllib.loads(project_text)
    dev_dependencies = project["dependency-groups"]["dev"]
    uv_config = project["tool"]["uv"]
    assert not any(item.partition("[")[0].startswith("octoagent-sdk") for item in dev_dependencies)
    assert "octoagent-sdk" not in uv_config["sources"]
    assert "packages/sdk" not in uv_config["workspace"]["members"]
    assert "packages/sdk/src" not in project["tool"]["coverage"]["run"]["source"]
    assert "octoagent-sdk" not in project_text

    lock_text = (REPO_ROOT / "octoagent/uv.lock").read_text(encoding="utf-8")
    lock = tomllib.loads(lock_text)
    assert "octoagent-sdk" not in lock["manifest"]["members"]
    assert all(package["name"] != "octoagent-sdk" for package in lock["package"])
    assert "packages/sdk" not in lock_text

    clean_wheel_text = (REPO_ROOT / "repo-scripts/check-clean-wheel.py").read_text(encoding="utf-8")
    assert "octoagent-sdk" not in clean_wheel_text
    assert '"sdk"' not in clean_wheel_text

    active_runtime_readers = (
        "octoagent/scripts/run-octo-home.sh",
        "octoagent/scripts/doctor-octo-home.sh",
        "repo-scripts/worktree-shared-paths.txt",
        "octoagent/apps/gateway/src/octoagent/gateway/cli/install_bootstrap.py",
        "octoagent/apps/gateway/src/octoagent/gateway/services/config/dotenv_loader.py",
        "octoagent/apps/gateway/src/octoagent/gateway/services/frontdoor_exposure.py",
        "octoagent/apps/gateway/src/octoagent/gateway/cli/behavior_commands.py",
        "octoagent/apps/gateway/src/octoagent/gateway/services/operations/secret_service.py",
        "octoagent/apps/gateway/src/octoagent/gateway/services/operations/project_migration.py",
        "octoagent/apps/gateway/src/octoagent/gateway/services/operations/doctor_remediation.py",
    )
    for relative_path in active_runtime_readers:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert ".env.litellm" not in text, relative_path

    schema_path = (
        REPO_ROOT / "octoagent/apps/gateway/src/octoagent/gateway/services/config/config_schema.py"
    )
    schema_tree = ast.parse(schema_path.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "RuntimeConfig" for node in schema_tree.body
    )
    root_config = next(
        node
        for node in schema_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OctoAgentConfig"
    )
    root_fields = {
        node.target.id
        for node in root_config.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "runtime" not in root_fields

    tombstone_text = (
        REPO_ROOT
        / "octoagent/apps/gateway/src/octoagent/gateway/services/config/config_bootstrap.py"
    ).read_text(encoding="utf-8")
    assert '(".env.litellm", "LEGACY_LITELLM_ENV_FILE_FOUND")' in tombstone_text
    assert '("litellm-config.yaml", "LEGACY_LITELLM_CONFIG_FOUND")' in tombstone_text
    assert "litellm-config.yaml" in (REPO_ROOT / "octoagent/.gitignore").read_text(encoding="utf-8")


class TestImportDirection:
    def test_rejects_provider_gateway_static_and_type_checking_imports(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            IMPORT_ORACLE,
            "import-direction",
            "provider-static-type-checking",
            "PROVIDER_GATEWAY_IMPORT_FORBIDDEN",
        )

    def test_rejects_constant_dynamic_and_module_string_imports(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            IMPORT_ORACLE,
            "import-direction",
            "provider-dynamic-module-string",
            "PROVIDER_GATEWAY_IMPORT_FORBIDDEN",
        )

    def test_rejects_subprocess_monkeypatch_entrypoint_and_update_worker_strings(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            IMPORT_ORACLE,
            "import-direction",
            "provider-subprocess-monkeypatch-entrypoint",
            "PROVIDER_GATEWAY_IMPORT_FORBIDDEN",
        )

    def test_source_manifest_projection_prevents_missing_target_false_green(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            IMPORT_ORACLE,
            "import-direction",
            "namespace-projection",
            "NAMESPACE_TARGET_PROJECTION_INVALID",
        )


class TestRetiredAndQualitySmells:
    def test_rejects_unscoped_retired_term_and_accepts_exact_purpose_exception(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            RETIRED_ORACLE,
            "retired-terms",
            "retired-exception",
            "UNSCOPED_RETIRED_TERM",
            accept_case="retired-history-only",
        )

    def test_reports_must_fix_ratchet_and_follow_up_separately(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            RETIRED_ORACLE,
            "quality-smells",
            "quality-buckets",
            "QUALITY_CLASSIFICATION_INCOMPLETE",
        )

    def test_rejects_cli_side_effect_growth_or_wrong_operations_layer(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            RETIRED_ORACLE,
            "quality-smells",
            "cli-side-effect-growth",
            "LEGACY_EDGE_GROWTH",
        )


class TestManifestIntegrity:
    def test_cross_role_import_and_direct_name_ceiling_counts_unique_identities_and_rejects_equal_replacement(  # noqa: E501
        self, tmp_path: Path
    ) -> None:
        try:
            _expect_rejects(
                tmp_path,
                MANIFEST_ORACLE,
                "quality-smells",
                "cross-role-equal-replacement",
                "CROSS_ROLE_EDGE_REPLACED",
            )
            _assert_precommit_cross_role_contract(tmp_path / "precommit")
        except AssertionError as exc:
            pytest.fail(f"{MANIFEST_ORACLE}: {exc}", pytrace=False)

    def test_changed_operations_hunks_require_attribute_call_and_adversarial_responsibility_review(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "changed-hunk-review",
            "RESPONSIBILITY_REVIEW_MISSING",
        )

    def test_namespace_and_provider_test_maps_are_exact_unique_and_project_final_paths(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "namespace-provider-map",
            "SOURCE_TARGET_MAP_INVALID",
        )

    def test_planned_diff_closure_rejects_unowned_plan_or_owner_outside_plan(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "planned-owner-closure",
            "PLANNED_OWNER_CLOSURE_INVALID",
        )

    def test_constructor_behavior_owner_map_matches_44_paths_and_never_uses_helper_qualname_as_node(
        self, tmp_path: Path
    ) -> None:
        try:
            _expect_rejects(
                tmp_path / "owner-map",
                MANIFEST_ORACLE,
                "quality-smells",
                "constructor-owner",
                "CONSTRUCTOR_OWNER_CLOSURE_INVALID",
            )
            _assert_s084_owned_test_path_contract(tmp_path / "owned-test-path")
        except AssertionError as exc:
            pytest.fail(f"{MANIFEST_ORACLE}: {exc}", pytrace=False)

    def test_test_owner_rejects_nonexistent_uncollected_fake_or_planned_owner(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path, MANIFEST_ORACLE, "quality-smells", "test-owner", "TEST_OWNER_INVALID"
        )

    def test_f150_scope_allows_unrelated_f151_diff_but_rejects_protected_or_handler_sibling_change(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "f150-sibling",
            "F150_PROTECTED_SEMANTIC_DRIFT",
            accept_case="f150-unrelated-f151",
        )

    def test_f150_scope_allows_d03_import_only_but_rejects_body_change(
        self, tmp_path: Path
    ) -> None:
        try:
            _expect_rejects_many(
                tmp_path,
                MANIFEST_ORACLE,
                "quality-smells",
                [
                    ("f150-d03-body", "F150_D03_NON_IMPORT_CHANGE"),
                    ("f150-d03-alias", "F150_D03_NON_IMPORT_CHANGE"),
                    ("f150-d03-order", "F150_D03_NON_IMPORT_CHANGE"),
                    ("f150-d03-nonmanifest", "F150_D03_NON_IMPORT_CHANGE"),
                ],
                accept_case="f150-d03-import-only",
            )
        except AssertionError as exc:
            pytest.fail(f"{MANIFEST_ORACLE}: {exc}", pytrace=False)

    def test_rgr_scope_manifest_ids_refs_paths_and_declared_states_are_machine_complete(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "rgr-machine-complete",
            "RGR_SCOPE_MANIFEST_INCOMPLETE",
        )

    def test_declared_new_paths_are_absent_in_base_tree(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "declared-new-exists",
            "DECLARED_NEW_EXISTS_IN_BASE",
        )

    def test_selector_grammar_resolves_nonempty_disjoint_hunks_and_rejects_duplicate_or_missing_selector(  # noqa: E501
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "selector-grammar",
            "SELECTOR_RESOLUTION_INVALID",
        )

    def test_dependency_selectors_resolve_toml_lock_transitions_and_disjoint_hunks(
        self, tmp_path: Path
    ) -> None:
        checker = _load_frontier_checker()
        issues: list[str] = []
        if not _dependency_clean_control(checker, tmp_path / "clean-nondependency"):
            issues.append("legal nondependency manifest baseline rejected")
        for transition in ("add", "proxy", "delete"):
            repo, scope = _dependency_fixture(tmp_path / f"valid-{transition}", transition)
            if not _dependency_checker_accepts(checker, repo, scope):
                issues.append(f"exact {transition} transition rejected")
        cumulative_repo, cumulative_scope = _dependency_cumulative_fixture(
            tmp_path / "valid-cumulative"
        )
        if not _dependency_checker_accepts(checker, cumulative_repo, cumulative_scope):
            issues.append("exact cumulative pre-T012 to T023 transition rejected")
        for ordinal, (transition, label, mutator) in enumerate(_dependency_reject_cases()):
            issue = _dependency_reject_case(
                tmp_path / f"reject-{ordinal:02d}",
                checker,
                transition,
                label,
                mutator,
            )
            if issue is not None:
                issues.append(issue)
        if issues:
            pytest.fail(
                f"{DEPENDENCY_SELECTOR_ORACLE}: " + "; ".join(issues),
                pytrace=False,
            )

    def test_rgr_scope_requires_all_shared_members_or_disjoint_symbols(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "shared-members",
            "SHARED_SCOPE_MEMBER_MISSING",
        )

    def test_rgr_scope_derives_changed_tests_and_rejects_nonempty_zero_required(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "changed-test-zero-required",
            "CHANGED_PATH_WITHOUT_REQUIRED_SLICE",
        )

    def test_phase2_scope_never_requires_phase3_or_phase4_evidence_with_real_diff(
        self, tmp_path: Path
    ) -> None:
        _expect_accepts(tmp_path, MANIFEST_ORACLE, "quality-smells", "phase2-no-future")

    def test_phase3_scope_never_requires_phase4_evidence_with_real_diff(
        self, tmp_path: Path
    ) -> None:
        _expect_accepts(tmp_path, MANIFEST_ORACLE, "quality-smells", "phase3-no-future")

    def test_declared_new_path_without_slice_owner_still_fails(self, tmp_path: Path) -> None:
        try:
            _expect_rejects(
                tmp_path / "declared-new",
                MANIFEST_ORACLE,
                "quality-smells",
                "declared-no-owner",
                "DECLARED_NEW_WITHOUT_OWNER",
            )
            _assert_machine_expansion_owner_contract(tmp_path / "machine-expansion")
        except AssertionError as exc:
            pytest.fail(f"{MANIFEST_ORACLE}: {exc}", pytrace=False)

    def test_atomic_namespace_snapshot_rejects_t029_business_change_and_requires_later_slice_evidence(  # noqa: E501
        self, tmp_path: Path
    ) -> None:
        try:
            _assert_atomic_source_delete_is_read_safe(tmp_path / "source-delete")
            _expect_rejects(
                tmp_path / "business-change",
                MANIFEST_ORACLE,
                "quality-smells",
                "atomic-post-snapshot",
                "POST_ATOMIC_CHANGE_UNAUTHORIZED",
            )
        except AssertionError as exc:
            pytest.fail(f"{MANIFEST_ORACLE}: {exc}", pytrace=False)

    def test_tree_delete_expansions_use_reproducible_tree_or_exact_matchers_including_root_dotfiles(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "tree-delete-matcher",
            "TREE_DELETE_MATCHER_INVALID",
        )

    def test_changed_path_scope_allows_only_exact_preexisting_user_patch_and_rejects_other_feature_or_design_changes(  # noqa: E501
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "other-feature-change",
            "CHANGED_PATH_OUTSIDE_F151",
        )

    def test_artifact_lifecycle_fields_paths_and_phase_transitions_are_exact(
        self, tmp_path: Path
    ) -> None:
        try:
            _expect_rejects(
                tmp_path / "legacy",
                MANIFEST_ORACLE,
                "quality-smells",
                "lifecycle-transition",
                "ARTIFACT_LIFECYCLE_INVALID",
            )
            _expect_rejects_many(
                tmp_path / "atomic",
                MANIFEST_ORACLE,
                "quality-smells",
                [
                    ("atomic-lifecycle-exact_relative_path", "ARTIFACT_LIFECYCLE_INVALID"),
                    ("atomic-lifecycle-task_id", "ARTIFACT_LIFECYCLE_INVALID"),
                    ("atomic-lifecycle-phase", "ARTIFACT_LIFECYCLE_INVALID"),
                    ("atomic-lifecycle-slice_id", "ARTIFACT_LIFECYCLE_INVALID"),
                    ("atomic-lifecycle-name", "ARTIFACT_LIFECYCLE_INVALID"),
                ],
            )
        except AssertionError as exc:
            pytest.fail(f"{ATOMIC_SNAPSHOT_LIFECYCLE_ORACLE}: {exc}", pytrace=False)

    def test_evidence_producer_paths_names_and_lifecycle_sets_are_bijective(
        self, tmp_path: Path
    ) -> None:
        try:
            _expect_rejects(
                tmp_path / "legacy",
                MANIFEST_ORACLE,
                "quality-smells",
                "producer-bijection",
                "EVIDENCE_PRODUCER_SET_MISMATCH",
            )
            _expect_rejects_many(
                tmp_path / "atomic",
                MANIFEST_ORACLE,
                "quality-smells",
                [
                    ("atomic-producer-missing", "EVIDENCE_PRODUCER_SET_MISMATCH"),
                    (
                        "atomic-producer-exact_relative_path",
                        "EVIDENCE_PRODUCER_SET_MISMATCH",
                    ),
                    ("atomic-producer-command_owner", "EVIDENCE_PRODUCER_SET_MISMATCH"),
                    ("atomic-producer-task_id", "EVIDENCE_PRODUCER_SET_MISMATCH"),
                    ("atomic-producer-phase", "EVIDENCE_PRODUCER_SET_MISMATCH"),
                    ("atomic-producer-slice_id", "EVIDENCE_PRODUCER_SET_MISMATCH"),
                    ("atomic-producer-name", "EVIDENCE_PRODUCER_SET_MISMATCH"),
                ],
            )
        except AssertionError as exc:
            pytest.fail(f"{ATOMIC_SNAPSHOT_LIFECYCLE_ORACLE}: {exc}", pytrace=False)

    def test_committed_artifacts_have_reachable_first_writers_and_producer_commands(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "committed-producer",
            "COMMITTED_PRODUCER_MISSING",
        )

    def test_finalize_verification_writes_only_required_report_after_all_gates(
        self, tmp_path: Path
    ) -> None:
        issues: list[str] = []
        probe_repo = _seed_repo(tmp_path / "finalize-command-probe")
        if not _checker_has_command(probe_repo, "finalize-verification"):
            issues.append("finalize-verification command is missing")
            _finish_corrective(issues, CORRECTIVE_FINALIZE_ORACLE)
            return

        repo, index_path = _prepare_v2_fixture(tmp_path / "finalize-positive")
        _mark_finalize_inputs(repo)
        base_ref = _read_json(index_path)["base_sha"]
        output = repo / FEATURE_REL / "verification-report.md"
        result = _run_checker(
            repo,
            "finalize-verification",
            "--mode",
            "local-working-tree",
            "--base-ref",
            base_ref,
            "--evidence-index",
            str(index_path),
            "--output",
            str(output),
            "--repo-root",
            str(repo),
        )
        _contract_accept(issues, "T120-T123 complete without T124 self-dependency", result)
        if result.returncode == 0 and not output.is_file():
            issues.append("finalize success did not create the exact report")
        if result.returncode == 0:
            _contract_accept(
                issues,
                "T124 final report is a governed committed artifact",
                _run_checker(repo, "quality-smells", "--repo-root", str(repo)),
            )

        for task_id in ("T120", "T121", "T122", "T123"):
            reject_repo, reject_index = _prepare_v2_fixture(
                tmp_path / f"finalize-missing-{task_id}"
            )
            _mark_finalize_inputs(reject_repo, missing=task_id)
            reject_base_ref = _read_json(reject_index)["base_sha"]
            reject_output = reject_repo / FEATURE_REL / "verification-report.md"
            before = _evidence_byte_map(reject_repo)
            rejected = _run_checker(
                reject_repo,
                "finalize-verification",
                "--mode",
                "local-working-tree",
                "--base-ref",
                reject_base_ref,
                "--evidence-index",
                str(reject_index),
                "--output",
                str(reject_output),
                "--repo-root",
                str(reject_repo),
            )
            _contract_reject(issues, f"missing {task_id}", rejected)
            if reject_output.exists() or before != _evidence_byte_map(reject_repo):
                issues.append(f"missing {task_id}: failure was not zero-write")

        existing_repo, existing_index = _prepare_v2_fixture(tmp_path / "finalize-existing-output")
        _mark_finalize_inputs(existing_repo, missing="T122")
        existing_base_ref = _read_json(existing_index)["base_sha"]
        existing_output = existing_repo / FEATURE_REL / "verification-report.md"
        existing_output.write_bytes(b"existing-report\n")
        before_output = existing_output.read_bytes()
        rejected = _run_checker(
            existing_repo,
            "finalize-verification",
            "--mode",
            "local-working-tree",
            "--base-ref",
            existing_base_ref,
            "--evidence-index",
            str(existing_index),
            "--output",
            str(existing_output),
            "--repo-root",
            str(existing_repo),
        )
        _contract_reject(issues, "existing output on failed finalize", rejected)
        if existing_output.read_bytes() != before_output:
            issues.append("failed finalize changed existing output bytes")
        _finish_corrective(issues, CORRECTIVE_FINALIZE_ORACLE)

    def test_superseded_history_is_planned_and_s002_owned(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "superseded-owner",
            "SUPERSEDED_HISTORY_OWNER_MISSING",
        )

    def test_authority_index_derivation_includes_all_runtime_truth_candidates(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "authority-index",
            "AUTHORITY_INDEX_INCOMPLETE",
        )


class TestStageCommandManifest:
    def test_each_selector_is_produced_before_stage_and_collects_nonzero(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "stage-future-selector",
            "STAGE_SELECTOR_NOT_AVAILABLE",
        )

    def test_pre_sdk_coverage_has_ten_paths_and_post_sdk_has_nine(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "coverage-path-count",
            "SDK_RETIREMENT_COMMAND_INVALID",
        )

    def test_c03_uses_exact_nodes_and_cannot_deselect_zero(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "c03-zero-select",
            "STAGE_SELECTOR_SELECTED_ZERO",
        )

    def test_t120_t121_t122_t123_t124_and_c084_have_frozen_env_paths_markers_outputs_and_counts(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects_many(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            [
                ("final-stage-shape", "AUTOMATIC_VERIFY_COMMAND_INVALID"),
                ("final-stage-offline-env", "AUTOMATIC_VERIFY_COMMAND_INVALID"),
                ("finalize-local-mode", "AUTOMATIC_VERIFY_COMMAND_INVALID"),
            ],
        )

    def test_coverage_transaction_creates_report_parent_before_checker(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "coverage-parent",
            "COVERAGE_REPORT_PARENT_MISSING",
        )

    def test_t122_coverage_is_fresh_for_its_own_start_tree_and_cannot_reuse_t105(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            MANIFEST_ORACLE,
            "quality-smells",
            "coverage-freshness",
            "COVERAGE_FRESHNESS_BINDING_INVALID",
        )


class TestComplexityRatchet:
    def test_rejects_scanner_or_ruff_fingerprint_mismatch(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            COMPLEXITY_ORACLE,
            "complexity",
            "complexity-fingerprint",
            "COMPLEXITY_SCANNER_FINGERPRINT_MISMATCH",
        )

    def test_rejects_current_above_committed_ceiling(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            COMPLEXITY_ORACLE,
            "complexity",
            "complexity-ceiling",
            "COMPLEXITY_CEILING_EXCEEDED",
        )

    def test_rejects_current_above_merge_base_actual(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            COMPLEXITY_ORACLE,
            "complexity",
            "complexity-merge-base",
            "COMPLEXITY_LOW_WATER_EXCEEDED",
        )

    def test_write_snapshot_refuses_numeric_increase(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            COMPLEXITY_ORACLE,
            "complexity",
            "complexity-write-up",
            "COMPLEXITY_SNAPSHOT_INCREASE_FORBIDDEN",
            accept_case="complexity-write-stable",
        )


def _require_repository_complexity_snapshot() -> None:
    if not REPOSITORY_COMPLEXITY_SNAPSHOT.is_file():
        pytest.fail(REPOSITORY_COMPLEXITY_ORACLE, pytrace=False)
    frozen = FEATURE_ROOT / "inventories/complexity-ceiling.v1.json"
    if REPOSITORY_COMPLEXITY_SNAPSHOT.read_bytes() != frozen.read_bytes():
        pytest.fail(REPOSITORY_COMPLEXITY_ORACLE, pytrace=False)


def _install_fixture_complexity_snapshot(repo: Path, *, commit: bool = True) -> Path:
    target = repo / "repo-scripts/runtime-architecture-ceiling.v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file() or target.read_bytes() != REPOSITORY_COMPLEXITY_SNAPSHOT.read_bytes():
        shutil.copy2(REPOSITORY_COMPLEXITY_SNAPSHOT, target)
    status = _git(repo, "status", "--porcelain", "--", str(target.relative_to(repo))).stdout
    if commit and status:
        _git(repo, "add", str(target.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", "install complexity snapshot")
    return target


def _run_repository_complexity(
    repo: Path, *, write_snapshot: bool = False
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(CHECKER),
        "complexity",
        "--repo-root",
        str(repo),
        "--base-ref",
        "HEAD",
    ]
    if write_snapshot:
        args.append("--write-snapshot")
    return subprocess.run(args, cwd=repo, capture_output=True, text=True)


def _assert_repository_complexity_rejects(
    result: subprocess.CompletedProcess[str], diagnostic: str
) -> None:
    if result.returncode == 0 or diagnostic not in result.stdout + result.stderr:
        pytest.fail(REPOSITORY_COMPLEXITY_ORACLE, pytrace=False)


class TestComplexitySnapshotInstall:
    def test_repository_snapshot_matches_frozen_feature_inventory_before_production_edits(
        self, tmp_path: Path
    ) -> None:
        _require_repository_complexity_snapshot()
        repo = _seed_repo(tmp_path)
        _install_fixture_complexity_snapshot(repo)
        if _run_repository_complexity(repo).returncode != 0:
            pytest.fail(REPOSITORY_COMPLEXITY_ORACLE, pytrace=False)

    def test_rejects_scanner_version_or_config_fingerprint_drift(self, tmp_path: Path) -> None:
        _require_repository_complexity_snapshot()
        repo = _seed_repo(tmp_path)
        snapshot = _install_fixture_complexity_snapshot(repo)
        data = _read_json(snapshot)
        data["scanner_version"] = "f151-drift"
        _write_json(snapshot, data)
        _assert_repository_complexity_rejects(
            _run_repository_complexity(repo),
            "COMPLEXITY_SCANNER_FINGERPRINT_MISMATCH",
        )

    def test_rejects_numeric_upward_snapshot_write(self, tmp_path: Path) -> None:
        _require_repository_complexity_snapshot()
        repo = _seed_repo(tmp_path)
        snapshot = _install_fixture_complexity_snapshot(repo)
        data = _read_json(snapshot)
        data["total_by_rule"]["C901"] += 1
        _write_json(snapshot, data)
        _assert_repository_complexity_rejects(
            _run_repository_complexity(repo, write_snapshot=True),
            "COMPLEXITY_SNAPSHOT_INCREASE_FORBIDDEN",
        )

    def test_merge_base_low_water_mark_is_enforced(self, tmp_path: Path) -> None:
        _require_repository_complexity_snapshot()
        repo = _seed_repo(tmp_path)
        _install_fixture_complexity_snapshot(repo)
        hotspot = repo / "octoagent/apps/gateway/src/octoagent/gateway/hotspot.py"
        _write(hotspot, "def f(x):\n    return x\n")
        _git(repo, "add", str(hotspot.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", "freeze low water")
        _write(hotspot, _complexity_hotspot_source())
        _assert_repository_complexity_rejects(
            _run_repository_complexity(repo),
            "COMPLEXITY_LOW_WATER_EXCEEDED",
        )


class TestTddEvidence:
    def test_accepts_complete_pytest_red_green_refactor_artifacts(self, tmp_path: Path) -> None:
        _expect_accepts(
            tmp_path / "legacy-positive",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-valid-pytest",
        )
        issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "v2-positive")
        index = _read_json(index_path)
        if set(index) != set(V2_INDEX_FIELDS):
            issues.append("top-level index schema is not exact")
        if any(set(record) != set(V2_RECORD_FIELDS) for record in index["records"]):
            issues.append("record schema is not exact")
        _contract_accept(
            issues, "canonical v2 schema/chain/run closure", _verify_v2(repo, index_path)
        )
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_accepts_complete_vitest_red_green_refactor_artifacts(self, tmp_path: Path) -> None:
        _expect_accepts(tmp_path, EVIDENCE_ORACLE, "tdd-evidence", "evidence-valid-vitest")

    def test_rejects_vitest_junit_selector_skip_extra_failure_or_unparseable_raw(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects_many(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            [
                ("evidence-vitest-selector", "VITEST_EVIDENCE_INVALID"),
                ("evidence-vitest-skip", "VITEST_EVIDENCE_INVALID"),
                ("evidence-vitest-extra-failure", "VITEST_EVIDENCE_INVALID"),
                ("evidence-vitest-unparseable-raw", "VITEST_EVIDENCE_INVALID"),
            ],
            accept_case="evidence-valid-vitest",
        )

    def test_rejects_missing_runner_artifact(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path / "legacy-missing",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-missing-artifact",
            "EVIDENCE_ARTIFACT_MISSING",
        )
        issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "v2-bijection")
        _contract_accept(issues, "run-index bijection positive", _verify_v2(repo, index_path))

        index = _read_json(index_path)
        indexed_run = (repo / index["records"][0]["artifact_paths"][0]).parent
        parked = indexed_run.with_name("RED.parked")
        indexed_run.rename(parked)
        _contract_reject(issues, "indexed run missing on disk", _verify_v2(repo, index_path))
        parked.rename(indexed_run)

        extra = repo / FEATURE_REL / "evidence/local/corrective/T005-integrity-v2/S999-extra/RED"
        source = (
            repo
            / FEATURE_REL
            / "evidence/local/corrective/T005-integrity-v2/S004-evidence-checker/RED"
        )
        shutil.copytree(source, extra)
        _contract_reject(issues, "canonical run absent from index", _verify_v2(repo, index_path))
        shutil.rmtree(extra)

        _mutate_record_artifact(
            repo,
            index_path,
            issues,
            "tree missing field",
            0,
            "tree.json",
            lambda path: _write_json(
                path,
                {key: value for key, value in _read_json(path).items() if key != "captured_utc"},
            ),
        )
        _mutate_record_artifact(
            repo,
            index_path,
            issues,
            "tree extra field",
            0,
            "tree.json",
            lambda path: _write_json(path, {**_read_json(path), "extra": True}),
        )
        _mutate_record_artifact(
            repo,
            index_path,
            issues,
            "invocation missing field",
            0,
            "invocation.json",
            lambda path: _write_json(
                path,
                {key: value for key, value in _read_json(path).items() if key != "exact_command"},
            ),
        )
        _mutate_index(
            repo,
            index_path,
            issues,
            "record missing required tree cross-field",
            lambda data: data["records"][0].pop("tree_captured_utc"),
        )
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_rejects_formal_bin_run_json_noncanonical_root_and_missing_invocation_or_tree(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects_many(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            [
                ("evidence-bin", "EVIDENCE_PATH_OR_NAME_INVALID"),
                ("evidence-run-json", "EVIDENCE_PATH_OR_NAME_INVALID"),
                ("evidence-noncanonical-root", "EVIDENCE_PATH_OR_NAME_INVALID"),
                ("evidence-missing-invocation", "EVIDENCE_PATH_OR_NAME_INVALID"),
                ("evidence-missing-tree", "EVIDENCE_PATH_OR_NAME_INVALID"),
            ],
        )

    def test_rejects_fake_artifact_hash(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path / "legacy-hash",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-fake-hash",
            "EVIDENCE_HASH_MISMATCH",
        )
        issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "v2-hashes")
        _contract_accept(issues, "required unique hashes positive", _verify_v2(repo, index_path))
        _mutate_index(
            repo,
            index_path,
            issues,
            "missing record_sha256",
            lambda data: data["records"][0].pop("record_sha256"),
            rehash=False,
        )
        _mutate_index(
            repo,
            index_path,
            issues,
            "duplicate record_sha256",
            lambda data: data["records"][1].update(
                {"record_sha256": data["records"][0]["record_sha256"]}
            ),
            rehash=False,
        )
        _mutate_index(
            repo,
            index_path,
            issues,
            "record hash mismatch",
            lambda data: data["records"][0].update({"record_sha256": "0" * 64}),
            rehash=False,
        )
        _mutate_index(
            repo,
            index_path,
            issues,
            "artifact hash mismatch",
            lambda data: data["records"][0]["artifact_sha256"].update({"stdout.txt": "0" * 64}),
            rehash=False,
        )
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_rejects_matching_fake_jsonl_and_raw_when_junit_disagrees(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-junit-disagrees",
            "EVIDENCE_JUNIT_DISAGREES",
        )

    def test_rejects_junit_nodeids_or_exit_mismatch(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-exit-node-mismatch",
            "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        )

    def test_rejects_oracle_expected_failure_set_or_extra_failure_mismatch(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-oracle-extra-failure",
            "EVIDENCE_EXPECTED_FAILURE_MISMATCH",
        )

    def test_rejects_wrong_assertion_failure_on_expected_node(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-wrong-assertion",
            "EVIDENCE_ORACLE_MISMATCH",
        )

    def test_rejects_reordered_rgr_steps(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path / "legacy-order",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-reordered",
            "EVIDENCE_RGR_ORDER_INVALID",
        )
        issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "v2-prefix")
        _contract_accept(issues, "eight-record prefix positive", _verify_v2(repo, index_path))
        _mutate_index(
            repo,
            index_path,
            issues,
            "prefix prior record deleted",
            lambda data: data["records"].pop(1),
        )

        def insert_record(data: dict[str, Any]) -> None:
            inserted = _clone_json(data["records"][1])
            inserted["slice_id"] = "S999-inserted"
            data["records"].insert(2, inserted)

        _mutate_index(repo, index_path, issues, "prefix record inserted", insert_record)

        def reorder_records(data: dict[str, Any]) -> None:
            data["records"][1], data["records"][2] = data["records"][2], data["records"][1]

        _mutate_index(repo, index_path, issues, "prefix records reordered", reorder_records)
        _mutate_index(
            repo,
            index_path,
            issues,
            "prefix identity replaced",
            lambda data: data["records"][3].update({"task_id": "T999"}),
        )
        _mutate_index(
            repo,
            index_path,
            issues,
            "rejected v1 record injected",
            lambda data: data["records"].insert(
                0,
                {
                    **_clone_json(data["records"][0]),
                    "lifecycle_type": "formal-rgr",
                    "task_id": "T006",
                },
            ),
        )
        _mutate_index(
            repo,
            index_path,
            issues,
            "chain head mismatch",
            lambda data: data.update({"chain_head_sha256": "0" * 64}),
            rehash=False,
        )
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_rejects_selector_mismatch(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path / "legacy-selector",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-selector-mismatch",
            "EVIDENCE_SELECTOR_MISMATCH",
        )
        issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "v2-cross-fields")
        _contract_accept(
            issues, "invocation/tree cross-fields positive", _verify_v2(repo, index_path)
        )
        _mutate_record_artifact(
            repo,
            index_path,
            issues,
            "invocation extra field",
            0,
            "invocation.json",
            lambda path: _write_json(path, {**_read_json(path), "extra": True}),
        )
        _mutate_record_artifact(
            repo,
            index_path,
            issues,
            "invocation argv mismatch",
            0,
            "invocation.json",
            lambda path: _write_json(
                path,
                {
                    **_read_json(path),
                    "argv": [*_read_json(path)["argv"], "unexpected::selector"],
                },
            ),
        )
        cross_fields = (
            "slice_id",
            "phase",
            "base_ref",
            "merge_base_sha",
            "head_sha",
            "head_tree_sha",
            "worktree_fingerprint",
            "fingerprint_scope",
            "fingerprint_files",
            "status_porcelain",
            "captured_utc",
        )
        for field in cross_fields:

            def mutate_tree(path: Path, *, target: str = field) -> None:
                tree = _read_json(path)
                tree[target] = ["mismatch"] if isinstance(tree[target], list) else "mismatch"
                _write_json(path, tree)

            _mutate_record_artifact(
                repo,
                index_path,
                issues,
                f"tree/record cross-field {field}",
                0,
                "tree.json",
                mutate_tree,
            )
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_rejects_collection_or_usage_error_as_red(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-collection-error",
            "EVIDENCE_RED_NOT_ASSERTION_FAILURE",
        )

    def test_rejects_skipped_selected_node(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-skip",
            "EVIDENCE_SELECTED_NODE_SKIPPED",
        )

    def test_rejects_f151_node_rerun(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path, EVIDENCE_ORACLE, "tdd-evidence", "evidence-rerun", "EVIDENCE_RERUN_FORBIDDEN"
        )

    def test_rejects_f151_node_rerun_from_junit_even_when_jsonl_claims_pass(
        self, tmp_path: Path
    ) -> None:
        checker = _load_frontier_checker()
        junit_reruns, classify = _t103_capabilities(
            checker,
            JUNIT_RERUN_CROSSCHECK_ORACLE,
            "junit_rerun_nodeids",
            "classify_quarantine_reruns",
        )
        nodeid = (
            "octoagent/tests/gate/test_runtime_architecture.py::"
            "TestTddEvidence::test_rejects_f151_node_rerun_from_junit_even_when_jsonl_claims_pass"
        )
        index = {
            "records": [
                {
                    "lifecycle_type": "formal-rgr",
                    "observed_nodeids": [nodeid],
                    "rerun_count": 0,
                }
            ]
        }
        clean = tmp_path / "clean.xml"
        _write(clean, _legacy_green_junit([nodeid]))
        assert junit_reruns(clean, [nodeid]) == ()
        assert classify(index, {"quarantined": []}, ()) == ()

        rerun = tmp_path / "rerun.xml"
        _t103_rerun_junit(rerun, nodeid)
        observed = junit_reruns(rerun, [nodeid])
        assert observed == (nodeid,)
        with pytest.raises(checker.GateFailure, match="EVIDENCE_RERUN_FORBIDDEN"):
            classify(index, {"quarantined": []}, observed)

    def test_quarantine_manifest_has_no_new_or_expanded_entry_against_merge_base(
        self, tmp_path: Path
    ) -> None:
        checker = _load_frontier_checker()
        at_ref, validate = _t103_capabilities(
            checker,
            QUARANTINE_NO_GROWTH_ORACLE,
            "quarantine_manifest_at_ref",
            "validate_quarantine_no_growth",
        )
        repo = _seed_repo(tmp_path)
        baseline = at_ref(repo, "HEAD")
        current = _read_json(repo / "octoagent/tests/quarantine.json")
        validate(current, baseline)

        metadata_only = _clone_json(current)
        metadata_only["quarantined"][0]["review_after"] = "2026-08-20"
        validate(metadata_only, baseline)
        removed = _clone_json(current)
        removed["quarantined"] = removed["quarantined"][:-1]
        validate(removed, baseline)

        added = _clone_json(current)
        added["quarantined"].append(
            _t103_quarantine_entry(
                "f151-new-quarantine",
                "tests/gate/test_runtime_architecture.py",
            )
        )
        with pytest.raises(checker.GateFailure, match="QUARANTINE_GROWTH_FORBIDDEN"):
            validate(added, baseline)

        expanded = _clone_json(current)
        expanded["quarantined"][0]["path"] = "apps/gateway/tests"
        with pytest.raises(checker.GateFailure, match="QUARANTINE_GROWTH_FORBIDDEN"):
            validate(expanded, baseline)

    def test_existing_quarantine_rerun_is_reported_without_failing_unrelated_f151_nodes(
        self,
    ) -> None:
        checker = _load_frontier_checker()
        classify, validate_record33, build_report = _t103_capabilities(
            checker,
            EXISTING_RERUN_REPORTING_ORACLE,
            "classify_quarantine_reruns",
            "validate_record33_release_exclusion",
            "build_quarantine_report",
        )
        if not isinstance(getattr(checker, "QuarantineReport", None), type):
            pytest.fail(EXISTING_RERUN_REPORTING_ORACLE, pytrace=False)
        index, lifecycle, producers = _t103_machine_truth()
        validate_record33(index, lifecycle, producers)
        report = build_report(REPO_ROOT, index, "origin/master")
        assert isinstance(report, checker.QuarantineReport)
        assert report.record33_release_excluded is True
        assert report.existing_reruns == ()

        quarantine = _read_json(REPO_ROOT / "octoagent/tests/quarantine.json")
        entry = quarantine["quarantined"][0]
        reported = classify(index, quarantine, (entry["path"],))
        assert reported == ((entry["id"], entry["path"], entry["review_after"]),)
        with pytest.raises(checker.GateFailure, match="EVIDENCE_RERUN_UNREGISTERED"):
            classify(index, quarantine, ("tests/unregistered.py::test_flaky",))

        release_eligible = _clone_json(lifecycle)
        release_eligible["superseded_contract_evidence"]["release_eligible"] = True
        with pytest.raises(checker.GateFailure, match="RECORD33_RELEASE_EXCLUSION_INVALID"):
            validate_record33(index, release_eligible, producers)

        changed_binding = _clone_json(producers)
        protocol = changed_binding["main_owned_direct_corrective_protocols"][0]
        protocol["accepted_binding"]["artifact_aggregate_sha256"] = "0" * 64
        with pytest.raises(checker.GateFailure, match="RECORD33_RELEASE_EXCLUSION_INVALID"):
            validate_record33(index, lifecycle, changed_binding)

    def test_rejects_blanket_rerun_argument(self, tmp_path: Path) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-blanket-rerun",
            "EVIDENCE_BLANKET_RERUN_FORBIDDEN",
        )

    def test_dirty_behavior_change_requires_slice_when_head_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path / "legacy-dirty",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-dirty-zero",
            "EVIDENCE_REQUIRED_SLICE_MISSING",
        )

        control_issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "committed-clean")
        base_sha = _read_json(index_path)["base_sha"]
        _contract_accept(
            control_issues,
            "local-working-tree valid base",
            _verify_v2(repo, index_path, mode="local-working-tree", base_ref="HEAD"),
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "freeze synthetic v2 evidence")
        assert not _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        _contract_accept(
            control_issues,
            "clean committed valid base",
            _verify_v2(repo, index_path, mode="committed", base_ref=base_sha),
        )
        _contract_reject(
            control_issues,
            "unresolvable base-ref",
            _verify_v2(repo, index_path, base_ref="refs/heads/does-not-exist"),
        )
        _contract_reject(
            control_issues,
            "unknown through-task",
            _verify_v2(repo, index_path, through_task="T000"),
        )
        _finish_corrective(control_issues, CORRECTIVE_EVIDENCE_ORACLE)

        evidence_repo, evidence_index = _prepare_v2_fixture(tmp_path / "evidence-only")
        evidence_base = _read_json(evidence_index)["base_sha"]
        _git(evidence_repo, "add", ".")
        _git(evidence_repo, "commit", "-q", "-m", "freeze synthetic v2 evidence")
        coverage = evidence_repo / FEATURE_REL / "evidence/local/coverage/T029"
        evidence_files = {
            "coverage.lcov": "TN:\n",
            "coverage-metadata.json": "{}\n",
            "stdout.txt": "coverage fixture\n",
            "stderr.txt": "",
            "exit-code.txt": "0\n",
        }
        for name, content in evidence_files.items():
            _write(coverage / name, content)
        _git(evidence_repo, "add", "-f", *[str(coverage / name) for name in evidence_files])
        evidence_status = _git(
            evidence_repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ).stdout
        assert evidence_status
        evidence_root = str(coverage.relative_to(evidence_repo))
        assert all(evidence_root in row for row in evidence_status.split("\0") if row)
        _contract_accept(
            control_issues,
            "lifecycle exact evidence-only dirty committed state",
            _verify_v2(
                evidence_repo,
                evidence_index,
                mode="committed",
                base_ref=evidence_base,
            ),
        )
        _finish_corrective(control_issues, CORRECTIVE_EVIDENCE_ORACLE)

        incorrectly_accepted: list[str] = []

        staged_repo, staged_index = _prepare_v2_fixture(tmp_path / "staged-relevant")
        staged_base = _read_json(staged_index)["base_sha"]
        _git(staged_repo, "add", ".")
        _git(staged_repo, "commit", "-q", "-m", "freeze synthetic v2 evidence")
        staged_path = (
            staged_repo / "octoagent/packages/provider/src/octoagent/provider/provider_router.py"
        )
        _write(staged_path, "VALUE = 2\n")
        _git(staged_repo, "add", str(staged_path))
        _contract_reject(
            incorrectly_accepted,
            "staged relevant change was incorrectly accepted",
            _verify_v2(staged_repo, staged_index, mode="committed", base_ref=staged_base),
        )

        unstaged_repo, unstaged_index = _prepare_v2_fixture(tmp_path / "unstaged-relevant")
        unstaged_base = _read_json(unstaged_index)["base_sha"]
        _git(unstaged_repo, "add", ".")
        _git(unstaged_repo, "commit", "-q", "-m", "freeze synthetic v2 evidence")
        unstaged_path = (
            unstaged_repo / "octoagent/packages/provider/src/octoagent/provider/provider_router.py"
        )
        _write(unstaged_path, "VALUE = 2\n")
        _contract_reject(
            incorrectly_accepted,
            "unstaged relevant change was incorrectly accepted",
            _verify_v2(
                unstaged_repo,
                unstaged_index,
                mode="committed",
                base_ref=unstaged_base,
            ),
        )

        untracked_repo, untracked_index = _prepare_v2_fixture(tmp_path / "untracked-relevant")
        untracked_base = _read_json(untracked_index)["base_sha"]
        _git(untracked_repo, "add", ".")
        _git(untracked_repo, "commit", "-q", "-m", "freeze synthetic v2 evidence")
        _write(
            untracked_repo / "octoagent/packages/provider/src/octoagent/provider/untracked.py",
            "VALUE = 1\n",
        )
        _contract_reject(
            incorrectly_accepted,
            "untracked relevant change was incorrectly accepted",
            _verify_v2(
                untracked_repo,
                untracked_index,
                mode="committed",
                base_ref=untracked_base,
            ),
        )

        if incorrectly_accepted:
            pytest.fail(
                "F151_COMMITTED_MODE_DIRTY_WORKTREE_ACCEPTED: " + "; ".join(incorrectly_accepted),
                pytrace=False,
            )

    def test_t006_index_amendment_adopts_exact_two_reds_and_preserves_prior_chain(
        self, tmp_path: Path
    ) -> None:
        _t006_require_capability(tmp_path)
        issues: list[str] = []
        _t006_positive_scenario(tmp_path, issues)
        for label, mutator in _t006_reject_cases():
            _t006_reject_case(tmp_path, issues, label, mutator)
        _finish_corrective(issues, T006_AMENDMENT_ORACLE)

    def test_post_t006_formal_frontier_uses_manifest_partial_order(self) -> None:
        checker = _load_frontier_checker()
        contracts, frontier = _frontier_contract(checker)
        index = _read_json(FEATURE_ROOT / "evidence/evidence-index.v2.json")
        anchor = _read_json(FEATURE_ROOT / "evidence/bootstrap-anchor.v1.json")
        records = index["records"]
        _assert_fixed_frontier_prefix(checker, contracts, index, anchor)
        historic = records[:HISTORIC_FORMAL_PREFIX_COUNT]
        _assert_post_t006_candidates(frontier, contracts, historic)
        _assert_current_manifest_candidates(frontier, contracts, records)
        _assert_same_task_interleaving(frontier, contracts)
        _assert_multi_task_rgr_syntaxes(frontier, contracts)
        _assert_non_rgr_rows_are_excluded(contracts)
        _assert_through_task_semantics(checker, contracts, records)

    def test_formal_runner_records_exact_red_oracle_before_green(self, tmp_path: Path) -> None:
        checker = _load_frontier_checker()
        _assert_historic_formal_invocations_readable(checker)
        _assert_synthetic_formal_tail_controls(tmp_path / "historic-prefix-tail", checker)
        fixture = _formal_recording_fixture(tmp_path / "red-capability", checker, "RED")
        red_record = _formal_recording_or_missing(checker, fixture)
        _assert_formal_recording_record(
            red_record,
            "RED",
            fixture[3],
            fixture[2]["oracle"],
        )
        for phase in ("GREEN", "REFACTOR"):
            control = _formal_recording_fixture(tmp_path / phase.lower(), checker, phase)
            record = _formal_recording_record(checker, control)
            _assert_formal_recording_record(record, phase, control[3], control[2]["oracle"])
        for label, phase, mutator in _formal_recording_reject_cases():
            _formal_recording_reject_case(
                tmp_path / label,
                checker,
                label,
                phase,
                mutator,
            )

    def test_rejects_unmapped_free_text_scope_or_unpartitioned_overlap(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects(
            tmp_path,
            EVIDENCE_ORACLE,
            "tdd-evidence",
            "evidence-unmapped-scope",
            "EVIDENCE_SCOPE_UNMAPPED",
        )

    def test_bootstrap_anchor_rejects_missing_malformed_hash_mismatch_or_second_anchor(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects_many(
            tmp_path / "legacy-bootstrap-schema",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            [
                ("bootstrap-missing", "BOOTSTRAP_ANCHOR_INVALID"),
                ("bootstrap-malformed", "BOOTSTRAP_ANCHOR_INVALID"),
                ("bootstrap-hash-mismatch", "BOOTSTRAP_ANCHOR_INVALID"),
                ("bootstrap-second-anchor", "BOOTSTRAP_ANCHOR_INVALID"),
                ("bootstrap-missing-invocation-field", "BOOTSTRAP_ANCHOR_INVALID"),
                ("bootstrap-missing-tree-field", "BOOTSTRAP_ANCHOR_INVALID"),
            ],
            accept_case="bootstrap-valid",
        )
        issues: list[str] = []
        repo = _seed_repo(tmp_path / "same-anchor-corrupted-index")
        _prepare_bootstrap_case(repo, "bootstrap-valid")
        evidence = repo / FEATURE_REL / "evidence"
        anchor = evidence / "bootstrap-anchor.v1.json"
        index = evidence / "evidence-index.v1.json"
        index.unlink(missing_ok=True)
        args = [
            "tdd-evidence",
            "verify-bootstrap",
            "--bootstrap-anchor-file",
            str(anchor),
            "--bootstrap-anchor-sha256",
            _sha(anchor),
            "--repo-root",
            str(repo),
        ]
        _contract_accept(issues, "first bootstrap validation", _run_checker(repo, *args))
        if index.is_file():
            corrupted = _read_json(index)
            corrupted["records"] = []
            corrupted["chain_head_sha256"] = "0" * 64
            _write_json(index, corrupted)
            _contract_reject(
                issues,
                "same-anchor corrupted existing index",
                _run_checker(repo, *args),
            )
        else:
            issues.append("bootstrap verifier did not create its canonical index")
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_bootstrap_anchor_rejects_replaced_or_mixed_base_tree_argv_junit_and_raw(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects_many(
            tmp_path / "legacy-bootstrap-mixed",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            [
                ("bootstrap-replaced-artifact", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-base", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-head-sha", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-head-tree", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-worktree-fingerprint", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-argv", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-junit", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-stdout", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
                ("bootstrap-mixed-stderr", "BOOTSTRAP_ANCHOR_MIXED_RUN"),
            ],
            accept_case="bootstrap-valid",
        )
        issues: list[str] = []
        repo, index_path = _prepare_v2_fixture(tmp_path / "v2-bootstrap-cross")
        _contract_accept(issues, "bootstrap cross-field positive", _verify_v2(repo, index_path))
        cross_map = {
            "slice_id": "slice_id",
            "phase": "phase",
            "base_ref": "base_ref",
            "merge_base_sha": "base_sha",
            "head_sha": "head_sha",
            "head_tree_sha": "head_tree_sha",
            "worktree_fingerprint": "worktree_fingerprint",
            "fingerprint_scope": "fingerprint_scope",
            "fingerprint_files": "fingerprint_files",
            "status_porcelain": "status_porcelain",
            "captured_utc": "tree_captured_utc",
        }
        for tree_field, record_field in cross_map.items():

            def mismatch_record(data: dict[str, Any], *, target: str = record_field) -> None:
                current = data["records"][0][target]
                data["records"][0][target] = (
                    ["record-mismatch"] if isinstance(current, list) else "record-mismatch"
                )

            _mutate_index(
                repo,
                index_path,
                issues,
                f"bootstrap tree cross mismatch {tree_field}",
                mismatch_record,
            )
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_artifact_lifecycle_rejects_unknown_early_wrong_writer_or_gitignored_escape(
        self, tmp_path: Path
    ) -> None:
        _assert_atomic_snapshot_path_controls(tmp_path / "atomic-snapshots")
        _expect_rejects_many(
            tmp_path / "legacy-lifecycle",
            EVIDENCE_ORACLE,
            "tdd-evidence",
            [
                ("evidence-lifecycle-unknown", "ARTIFACT_LIFECYCLE_ESCAPE"),
                ("evidence-lifecycle-early", "ARTIFACT_LIFECYCLE_ESCAPE"),
                ("evidence-lifecycle-wrong-writer", "ARTIFACT_LIFECYCLE_ESCAPE"),
                ("evidence-lifecycle-gitignored", "ARTIFACT_LIFECYCLE_ESCAPE"),
            ],
        )
        issues: list[str] = []
        probe_repo = _seed_repo(tmp_path / "command-probe")
        _seed_authority_documents(probe_repo)
        if not _checker_has_command(probe_repo, "all"):
            issues.append("architecture all command is missing")
        else:
            _contract_accept(
                issues,
                "architecture all resolvable base-ref",
                _run_checker(
                    probe_repo, "all", "--base-ref", "HEAD", "--repo-root", str(probe_repo)
                ),
            )
            _contract_reject(
                issues,
                "architecture all missing base-ref",
                _run_checker(probe_repo, "all", "--repo-root", str(probe_repo)),
            )
            _contract_reject(
                issues,
                "architecture all unresolvable base-ref",
                _run_checker(
                    probe_repo,
                    "all",
                    "--base-ref",
                    "refs/heads/does-not-exist",
                    "--repo-root",
                    str(probe_repo),
                ),
            )

        if not _checker_has_command(probe_repo, "tdd-evidence", "recover-index"):
            issues.append("tdd-evidence recover-index command is missing")
            _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)
            return

        for state in ("R0", "R1", "R2", "R2_PARTIAL", "R3", "R4"):
            repo, args, expected_v2 = _prepare_recovery_fixture(
                tmp_path / f"recovery-{state.lower()}"
            )
            _set_recovery_state(repo, state, expected_v2)
            result = _run_checker(repo, *args)
            _contract_accept(issues, f"recovery resumes from {state}", result)
            if result.returncode == 0:
                final = _recovery_paths(repo)["final"]
                if not final.is_file() or final.read_bytes() != expected_v2:
                    issues.append(f"recovery {state} did not converge to deterministic v2")
                before_repeat = _evidence_byte_map(repo)
                repeated = _run_checker(repo, *args)
                _contract_accept(issues, f"recovery {state} idempotent re-entry", repeated)
                if before_repeat != _evidence_byte_map(repo):
                    issues.append(f"recovery {state} changed bytes on idempotent re-entry")

        invalid_ids = {
            "missing": None,
            "empty": "",
            "unknown": "not-main-approved",
            "anchor-reuse": "fixture-main-phase0-review",
            "t006-reuse": "main-f151-t006-rejected-review",
        }
        for label, value in invalid_ids.items():
            repo, args, _ = _prepare_recovery_fixture(tmp_path / f"review-id-{label}")
            option = args.index("--main-review-message-id")
            if value is None:
                del args[option : option + 2]
            else:
                args[option + 1] = value
            before = _evidence_byte_map(repo)
            _contract_reject(issues, f"review id {label}", _run_checker(repo, *args))
            if before != _evidence_byte_map(repo):
                issues.append(f"review id {label} failure changed bytes")

        mixed_repo, mixed_args, _ = _prepare_recovery_fixture(tmp_path / "recovery-mixed")
        mixed_paths = _recovery_paths(mixed_repo)
        mixed_paths["quarantine"].mkdir(parents=True)
        _write(mixed_paths["quarantine"] / "unexpected.txt", "mixed\n")
        before_mixed = _evidence_byte_map(mixed_repo)
        _contract_reject(
            issues,
            "unrecognized mixed recovery state",
            _run_checker(mixed_repo, *mixed_args),
        )
        if before_mixed != _evidence_byte_map(mixed_repo):
            issues.append("unrecognized mixed recovery state changed bytes")
        _finish_corrective(issues, CORRECTIVE_EVIDENCE_ORACLE)

    def test_accepts_pytest9_testsuites_and_single_testsuite_junit(self, tmp_path: Path) -> None:
        _expect_accepts(tmp_path / "single", JUNIT_ORACLE, "tdd-evidence", "junit-valid-single")
        _expect_accepts(tmp_path / "nested", JUNIT_ORACLE, "tdd-evidence", "junit-valid-nested")

    def test_rejects_pytest_junit_failure_error_skip_rerun_malformed_or_missing_suite(
        self, tmp_path: Path
    ) -> None:
        _expect_rejects_many(
            tmp_path,
            JUNIT_ORACLE,
            "tdd-evidence",
            [
                ("junit-failure", "PYTEST_JUNIT_AGGREGATION_INVALID"),
                ("junit-error", "PYTEST_JUNIT_AGGREGATION_INVALID"),
                ("junit-skip", "PYTEST_JUNIT_AGGREGATION_INVALID"),
                ("junit-rerun", "PYTEST_JUNIT_AGGREGATION_INVALID"),
                ("junit-malformed", "PYTEST_JUNIT_AGGREGATION_INVALID"),
                ("junit-missing-suite", "PYTEST_JUNIT_AGGREGATION_INVALID"),
            ],
            accept_case="junit-valid-nested",
        )


class TestDocumentationAuthority:
    def test_f151_authority_retired_terms_and_runtime_boundaries_match_machine_manifests(
        self, tmp_path: Path
    ) -> None:
        checker = _load_frontier_checker()
        (validate,) = _t103_capabilities(
            checker,
            DOCUMENTATION_AUTHORITY_ORACLE,
            "validate_documentation_authority",
        )
        authority = _read_json(_manifest(REPO_ROOT, "authority-docs.v1.json"))
        try:
            validate(REPO_ROOT, authority)
        except checker.GateFailure as exc:
            pytest.fail(f"{DOCUMENTATION_AUTHORITY_ORACLE}: {exc}", pytrace=False)

        history_repo, history_authority = _documentation_fixture(tmp_path / "history")
        _append_documentation(
            history_repo,
            "docs/blueprint/requirements.md",
            "\n## Historical retired architecture\n"
            "LiteLLM Proxy was retired and is no longer active.\n",
        )
        validate(history_repo, history_authority)
        for label, mutator in (
            ("current-proxy", _documentation_current_proxy),
            ("checked-retired-row", _documentation_checked_retired_row),
            ("current-mermaid", _documentation_current_mermaid),
            ("unlisted-authority", _documentation_unlisted_authority),
        ):
            _assert_documentation_rejects(checker, validate, tmp_path, label, mutator)


class TestProductionStartupInventory:
    def test_active_service_paths_use_only_canonical_module_entry(self) -> None:
        oracle = "F151_SINGLE_PRODUCTION_STARTUP_ENTRY_MISSING"
        entry = REPO_ROOT / "octoagent/apps/gateway/src/octoagent/gateway/__main__.py"
        run_script = REPO_ROOT / "octoagent/scripts/run-octo-home.sh"
        bootstrap = (
            REPO_ROOT / "octoagent/apps/gateway/src/octoagent/gateway/cli/install_bootstrap.py"
        )
        issues: list[str] = []
        if not entry.is_file():
            issues.append("canonical module entry is absent")

        run_text = run_script.read_text(encoding="utf-8")
        if "python -m octoagent.gateway" not in run_text:
            issues.append("run-octo-home.sh does not use the canonical module entry")
        if "uvicorn octoagent.gateway.main:app" in run_text:
            issues.append("run-octo-home.sh still executes the ASGI import target")

        bootstrap_text = bootstrap.read_text(encoding="utf-8")
        if '"python",\n            "-m",\n            "octoagent.gateway"' not in bootstrap_text:
            issues.append("managed descriptor does not use the canonical module entry")
        if '"uvicorn",\n            "octoagent.gateway.main:app"' in bootstrap_text:
            issues.append("managed descriptor still executes the ASGI import target")
        if issues:
            pytest.fail(f"{oracle}: {'; '.join(issues)}", pytrace=False)
