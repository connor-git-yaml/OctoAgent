#!/usr/bin/env python3
"""F151 runtime architecture truth 的单一机械门禁与证据入口。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn
from xml.etree import ElementTree

JsonObject = dict[str, Any]
FEATURE_REL = Path(".specify/features/151-runtime-boundary-architecture-truth")
INVENTORY_REL = FEATURE_REL / "inventories"
EVIDENCE_REL = FEATURE_REL / "evidence"
REPOSITORY_COMPLEXITY_REL = Path("repo-scripts/runtime-architecture-ceiling.v1.json")
FORMAL_NAMES = tuple(
    "junit.xml stdout.txt stderr.txt exit-code.txt invocation.json tree.json".split()
)
FORMAL_OFFLINE_ENV_KEY = "LITELLM_LOCAL_MODEL_COST_MAP"
FORMAL_ENV_ORDER = ("PYTHONNOUSERSITE", "PYTHONPATH", FORMAL_OFFLINE_ENV_KEY)
QUARANTINE_FIELDS = frozenset("id path reason owner review_after exit_criteria".split())
DOCUMENT_HISTORY_MARKERS = (
    "historical",
    "history",
    "retired",
    "no longer active",
    "历史",
    "退役",
    "不再现役",
    "不再启用",
    "已删除",
    "未实现",
    "尚未",
    "不存在",
    "没有",
    "不需要",
    "不支持",
    "不宣称",
    "不得",
    "禁止",
    "替代",
    "不新增",
    "非 ",
    "无 ",
    "no ",
)
LITELLM_PROXY_CLOSURE_PACKAGES = (
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
BOOTSTRAP_SLICES = tuple(
    "S001-import-direction S002-retired-quality S002-manifest-integrity "
    "S003-complexity-checker S004-evidence-checker S004-junit-parser".split()
)
BOOTSTRAP_TASKS = MappingProxyType(
    dict(
        pair.split(":")
        for pair in "S001-import-direction:T001 S002-retired-quality:T002 "
        "S002-manifest-integrity:T002 S003-complexity-checker:T003 "
        "S004-evidence-checker:T004 S004-junit-parser:T004".split()
    )
)


ANCHOR_FIELDS = frozenset(
    "version feature_id base_sha worktree_fingerprint anchored_utc main_review_message_id artifacts".split()
)
ANCHOR_ARTIFACT_FIELDS = frozenset(
    "slice_id phase relative_path sha256 size_bytes argv_sha256 tree_sha256 junit_sha256 stdout_sha256 stderr_sha256 exit_code".split()
)
INVOCATION_FIELDS = frozenset(
    "version slice_id phase task_scope argv cwd env exact_command started_utc finished_utc exit_code".split()
)
TREE_FIELDS = frozenset(
    "version slice_id phase base_ref merge_base_sha head_sha head_tree_sha worktree_fingerprint fingerprint_scope fingerprint_files status_porcelain captured_utc".split()
)
LEGACY_RECORD_FIELDS = frozenset(
    "producer_id lifecycle_type slice_id phase task_id cwd argv env base_sha head_sha tree_sha worktree_fingerprint started_utc finished_utc expected_oracle_id expected_failing_nodeids observed_nodeids artifact_paths artifact_sha256 artifact_size_bytes".split()
)
V2_INDEX_FIELDS = frozenset(
    "schema_version feature_id bootstrap_anchor_path bootstrap_anchor_sha256 base_sha created_utc recovery records chain_head_sha256".split()
)
RECOVERY_FIELDS = frozenset(
    "rejected_index_sha256 rejected_run_aggregate_sha256 quarantine_root corrective_red_aggregate_sha256 main_review_message_id approval_binding_sha256".split()
)
V2_RECORD_FIELDS = frozenset(
    "producer_id lifecycle_type slice_id phase task_id cwd argv env mode base_ref base_sha head_sha head_tree_sha worktree_fingerprint fingerprint_scope fingerprint_files status_porcelain tree_captured_utc started_utc finished_utc exit_code expected_oracle_id expected_failing_nodeids observed_nodeids selected_count passed_count failed_count error_count skipped_count rerun_count artifact_paths artifact_sha256 artifact_size_bytes artifact_aggregate_sha256 previous_record_sha256 record_sha256".split()
)
TREE_RECORD_FIELDS = MappingProxyType(
    dict(
        zip(
            "slice_id phase base_ref merge_base_sha head_sha head_tree_sha worktree_fingerprint fingerprint_scope fingerprint_files status_porcelain captured_utc".split(),
            "slice_id phase base_ref base_sha head_sha head_tree_sha worktree_fingerprint fingerprint_scope fingerprint_files status_porcelain tree_captured_utc".split(),
            strict=True,
        )
    )
)
FORMAL_SLICE_ORDER = tuple(
    "S001-import-direction S002-manifest-integrity S002-retired-quality "
    "S003-complexity-checker S004-evidence-checker S004-junit-parser".split()
)
T006_RED_SLICES = (
    "S006-committed-worktree-clean",
    "S006-index-amendment-integrity",
)
T006_AMENDMENT_TAIL = tuple(
    ("corrective-red" if phase == "RED" else "formal-rgr", "T006", slice_id, phase)
    for phase in ("RED", "GREEN", "REFACTOR")
    for slice_id in T006_RED_SLICES
)
T006_REVIEW_PREFIX = "main-f151-t006-index-amendment-red-review-"
INVOCATION_RECORD_FIELDS = tuple(
    "slice_id phase cwd argv env started_utc finished_utc exit_code".split()
)
FACT_RECORD_FIELDS = MappingProxyType(
    dict(
        zip(
            "selected_count failed_count error_count skipped_count rerun_count".split(),
            "tests failures errors skipped reruns".split(),
            strict=True,
        )
    )
)


class GateFailure(RuntimeError):
    """带稳定错误码的门禁失败。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def fail(code: str, detail: str = "") -> NoReturn:
    raise GateFailure(code, detail)


def require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        fail(code, detail)


def read_json(path: Path) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("INVALID_JSON", f"{path}: {exc}")
    if not isinstance(value, dict):
        fail("INVALID_JSON", f"{path}: root must be object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo, text=not binary, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        fail("GIT_QUERY_FAILED", " ".join(args) + ": " + str(exc))


def json_from_head(repo: Path, relative: Path) -> JsonObject | None:
    try:
        raw = subprocess.check_output(
            ["git", "show", f"HEAD:{relative.as_posix()}"], cwd=repo, text=True
        )
    except subprocess.CalledProcessError:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def manifest(repo: Path, name: str) -> Path:
    return repo / INVENTORY_REL / name


def task_number(task_id: str) -> int:
    match = re.fullmatch(r"T(\d{3})", task_id)
    require(match is not None, "RGR_TASK_MAPPING_INVALID", task_id)
    return int(match.group(1))


def validate_phase_tasks(tasks: JsonObject, detail: str) -> JsonObject:
    phases = ("RED", "GREEN", "REFACTOR")
    require(set(tasks) == set(phases), "RGR_TASK_MAPPING_MISSING", detail)
    numbers = [task_number(str(tasks[phase])) for phase in phases]
    require(numbers == sorted(numbers), "RGR_TASK_MAPPING_INVALID", detail)
    return {phase: str(tasks[phase]) for phase in phases}


def parse_active_rgr_tasks(task_spec: str, detail: str) -> JsonObject:
    explicit = re.fullmatch(
        r"(T\d{3})\s+RED\s*→\s*(T\d{3})\s+GREEN\s*→\s*"
        r"(T\d{3})\s+REFACTOR",
        task_spec,
    )
    single = re.fullmatch(r"(T\d{3})", task_spec)
    task_range = re.fullmatch(r"(T\d{3})-(T\d{3})", task_spec)
    arrow = re.fullmatch(r"(T\d{3})→(T\d{3})→(T\d{3})", task_spec)
    if explicit:
        tasks = dict(zip(("RED", "GREEN", "REFACTOR"), explicit.groups(), strict=True))
    elif arrow:
        arrow_tasks = arrow.groups()
        arrow_numbers = [task_number(task_id) for task_id in arrow_tasks]
        require(
            arrow_numbers == sorted(set(arrow_numbers)),
            "RGR_TASK_MAPPING_INVALID",
            detail,
        )
        tasks = dict(zip(("RED", "GREEN", "REFACTOR"), arrow_tasks, strict=True))
    elif single:
        tasks = {phase: single.group(1) for phase in ("RED", "GREEN", "REFACTOR")}
    elif task_range:
        require(
            task_number(task_range.group(1)) < task_number(task_range.group(2)),
            "RGR_TASK_MAPPING_INVALID",
            detail,
        )
        tasks = {
            "RED": task_range.group(1),
            "GREEN": task_range.group(1),
            "REFACTOR": task_range.group(2),
        }
    else:
        fail("RGR_TASK_MAPPING_MISSING", detail)
    return validate_phase_tasks(tasks, detail)


def parse_rgr(repo: Path) -> dict[str, JsonObject]:
    rows: dict[str, JsonObject] = {}
    text = (repo / INVENTORY_REL / "rgr-slices.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.startswith("| `S"):
            continue
        columns = [part.strip() for part in line.split("|")]
        slice_id = columns[1].split("`")[1]
        require(slice_id not in rows, "RGR_TASK_MAPPING_INVALID", slice_id)
        task_spec = columns[1].split("`", 2)[2].strip().removeprefix("/").strip()
        protocol = columns[2]
        selector = columns[3].removeprefix("`").removesuffix("`")
        nodeids = [selector] if "frontend" in protocol else selector.split()
        oracle = columns[4].removeprefix("`").removesuffix("`")
        tasks = (
            parse_active_rgr_tasks(task_spec, slice_id)
            if re.search(r"\bRGR\b", protocol)
            else {}
        )
        rows[slice_id] = {
            "nodeids": nodeids,
            "oracle": oracle,
            "protocol": protocol,
            "tasks": tasks,
        }
    return rows


def changed_paths(repo: Path) -> set[str]:
    raw = git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", binary=True
    )
    assert isinstance(raw, bytes)
    entries = [item.decode("utf-8") for item in raw.split(b"\0") if item]
    return {entry[3:].replace("\\", "/") for entry in entries if len(entry) >= 4}


def fingerprint(repo: Path) -> tuple[str, list[JsonObject], list[str]]:
    ignored = (
        f"{EVIDENCE_REL.as_posix()}/local/",
        f"{EVIDENCE_REL.as_posix()}/bootstrap-anchor.v1.json",
        f"{EVIDENCE_REL.as_posix()}/evidence-index.v1.json",
        f"{EVIDENCE_REL.as_posix()}/evidence-index.v2.json",
    )
    raw = git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", binary=True
    )
    assert isinstance(raw, bytes)
    entries = [item.decode("utf-8") for item in raw.split(b"\0") if item]
    files: list[JsonObject] = []
    statuses: list[str] = []
    for entry in entries:
        if len(entry) < 4:
            continue
        state, relative = entry[:2], entry[3:].replace("\\", "/")
        if relative.startswith(ignored):
            continue
        statuses.append(f"{state} {relative}")
        path = repo / relative
        if not path.exists():
            files.append({"kind": "deleted", "path": relative})
        elif path.is_file():
            data = path.read_bytes()
            files.append(
                {
                    "kind": "file",
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                }
            )
    files.sort(key=lambda item: item["path"])
    statuses.sort()
    return canonical_sha(files), files, statuses


def check_import_direction(repo: Path) -> None:
    provider_roots = [
        repo / "octoagent/packages/provider/src",
        repo / "octoagent/packages/provider/tests",
        repo / "octoagent/packages/provider/pyproject.toml",
    ]
    for root in provider_roots:
        candidates = (
            [root] if root.is_file() else list(root.rglob("*")) if root.exists() else []
        )
        for path in candidates:
            if path.is_file() and path.suffix in {".py", ".toml"}:
                if "octoagent.gateway" in path.read_text(
                    encoding="utf-8", errors="ignore"
                ):
                    fail(
                        "PROVIDER_GATEWAY_IMPORT_FORBIDDEN", str(path.relative_to(repo))
                    )
    data = read_json(manifest(repo, "namespace-migration.v1.json"))
    targets = [item.get("target") for item in data.get("moves", [])]
    if len(targets) != len(set(targets)):
        fail("NAMESPACE_TARGET_PROJECTION_INVALID", "duplicate target")
    expected_roots = {
        "cli": "octoagent/apps/gateway/src/octoagent/gateway/cli/",
        "config": "octoagent/apps/gateway/src/octoagent/gateway/services/config/",
        "operations": "octoagent/apps/gateway/src/octoagent/gateway/services/operations/",
    }
    for item in data.get("moves", []):
        if not str(item.get("target", "")).startswith(
            expected_roots.get(item.get("bucket"), "!")
        ):
            fail("NAMESPACE_TARGET_PROJECTION_INVALID", str(item))


def check_retired_terms(repo: Path) -> None:
    allowed_context = (
        "historical",
        "history",
        "retired",
        "已退役",
        "历史",
        "退役",
        "不再",
        "删除",
        "替代",
        "不新增",
        "不新建",
        "非 ",
        "无 ",
    )
    terms = ("litellm proxy", "octokernel", "apps/kernel", "workers/")
    docs = repo / "docs"
    if not docs.exists():
        return
    authority_path = manifest(repo, "authority-docs.v1.json")
    if authority_path.is_file():
        validate_documentation_authority(repo, read_json(authority_path))
        paths = set(docs.glob("*.md"))
    else:
        paths = set(docs.rglob("*.md"))
    for path in sorted(paths):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            if any(term in lowered for term in terms) and not any(
                marker in lowered for marker in allowed_context
            ):
                fail("UNSCOPED_RETIRED_TERM", f"{path.relative_to(repo)}:{number}")


def markdown_authority_links(repo: Path, path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    links: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]+)?\)", text):
        target = match.group(1)
        if "://" in target:
            continue
        normalized = (path.parent / target).resolve().relative_to(repo).as_posix()
        if normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links


def documentation_claims(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    claims: list[str] = []
    direct_patterns = {
        "litellm-proxy": "litellm proxy",
        "litellm-proxy-mermaid": "litellmproxy",
        "octokernel": "octokernel",
        "apps-kernel": "apps/kernel",
        "workers-package": "workers/",
        "provider-litellm-client": "packages/provider litellm client",
    }
    claims.extend(
        name for name, pattern in direct_patterns.items() if pattern in lowered
    )
    docker_runtime_markers = (
        "backend",
        "runtime",
        "sandbox",
        "last line of defense",
        "执行",
        "沙箱",
        "运行时",
        "后端",
        "最后防线",
        "默认 docker",
        "docker socket",
    )
    if "docker" in lowered and any(
        marker in lowered for marker in docker_runtime_markers
    ):
        claims.append("docker-runtime")
    return tuple(claims)


def documentation_contexts(text: str) -> list[tuple[str, str, bool]]:
    contexts: list[tuple[str, str, bool]] = []
    heading = ""
    for chunk in re.split(r"\n\s*\n", text):
        stripped = chunk.strip()
        if not stripped:
            continue
        lines = stripped.splitlines()
        if lines[0].lstrip().startswith("#"):
            heading = lines[0].lstrip("# ").strip()
            contexts.append((heading, lines[0], False))
            lines = lines[1:]
        if not lines:
            continue
        table_rows = [line for line in lines if line.lstrip().startswith("|")]
        if table_rows:
            contexts.extend((heading, row, True) for row in table_rows)
        elif any(line.lstrip().startswith(("- ", "* ")) for line in lines):
            contexts.extend(
                (heading, line, False)
                for line in lines
                if line.lstrip().startswith(("- ", "* "))
            )
        else:
            contexts.append((heading, "\n".join(lines), False))
    return contexts


def validate_document_claims(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for heading, context, table_row in documentation_contexts(text):
        claims = documentation_claims(context)
        if not claims:
            continue
        lowered_context = context.lower()
        lowered_heading = heading.lower()
        contextual_history = any(
            marker in lowered_context for marker in DOCUMENT_HISTORY_MARKERS
        )
        if not table_row:
            contextual_history = contextual_history or any(
                marker in lowered_heading for marker in DOCUMENT_HISTORY_MARKERS
            )
        require(
            contextual_history,
            "DOCUMENTATION_AUTHORITY_DRIFT",
            f"{path}: active {','.join(claims)}",
        )


def validate_documentation_index(repo: Path, authority: JsonObject) -> None:
    derivation = authority.get("index_derivation")
    require(isinstance(derivation, dict), "DOCUMENTATION_AUTHORITY_DRIFT", "index")
    root = repo / str(derivation.get("root_index", ""))
    candidates = derivation.get("root_candidates")
    require(
        root.is_file() and isinstance(candidates, list),
        "DOCUMENTATION_AUTHORITY_DRIFT",
        "root index",
    )
    expected = [row.get("path") for row in candidates if isinstance(row, dict)]
    require(
        markdown_authority_links(repo, root) == expected
        and len(expected) == len(set(expected)),
        "DOCUMENTATION_AUTHORITY_DRIFT",
        "root link derivation",
    )
    document_paths = {row["path"] for row in authority["documents"]}
    included = {
        row["path"] for row in candidates if row.get("classification") == "included"
    }
    require(
        included.issubset(document_paths),
        "DOCUMENTATION_AUTHORITY_DRIFT",
        "unlisted",
    )
    implementation = repo / str(derivation.get("implementation_index", ""))
    required = set(derivation.get("implementation_index_required_truth_paths", ()))
    require(
        implementation.is_file()
        and required.issubset(document_paths)
        and required.issubset(markdown_authority_links(repo, implementation)),
        "DOCUMENTATION_AUTHORITY_DRIFT",
        "implementation index",
    )


def validate_documentation_authority(repo: Path, authority: JsonObject) -> None:
    documents = authority.get("documents")
    require(isinstance(documents, list), "DOCUMENTATION_AUTHORITY_DRIFT", "documents")
    paths = [row.get("path") for row in documents if isinstance(row, dict)]
    self_check = authority.get("self_check", {})
    require(
        len(paths) == len(set(paths)) == self_check.get("document_count") == 17
        and all(isinstance(path, str) and path.endswith(".md") for path in paths),
        "DOCUMENTATION_AUTHORITY_DRIFT",
        "document set",
    )
    validate_documentation_index(repo, authority)
    for relative in paths:
        path = repo / relative
        require(path.is_file(), "DOCUMENTATION_AUTHORITY_DRIFT", str(relative))
        validate_document_claims(path)


def stable_edges(items: list[JsonObject], kind: str) -> set[tuple[Any, ...]]:
    if kind == "calls":
        keys = ("path", "qualname", "kind", "target", "lexical_ordinal")
    else:
        keys = ("projected_path", "qualname", "kind", "target", "lexical_ordinal")
    return {tuple(item.get(key) for key in keys) for item in items}


def anchored_cross_role_artifact(repo: Path) -> tuple[JsonObject, str]:
    index = read_json(repo / EVIDENCE_REL / "evidence-index.v2.json")
    anchor_relative = f"{EVIDENCE_REL.as_posix()}/bootstrap-anchor.v1.json"
    require(
        index.get("schema_version") == 2
        and index.get("feature_id") == "F151"
        and index.get("bootstrap_anchor_path") == anchor_relative,
        "CROSS_ROLE_ANCHORED_BASELINE_INVALID",
        "index binding",
    )
    _, artifacts = load_anchor(
        repo,
        repo / anchor_relative,
        str(index.get("bootstrap_anchor_sha256", "")),
    )
    matches = [
        item
        for item in artifacts
        if item.get("slice_id") == "S002-manifest-integrity"
        and item.get("phase") == "RED"
    ]
    require(len(matches) == 1, "CROSS_ROLE_ANCHORED_BASELINE_INVALID", "S002")
    item = matches[0]
    expected_run = (
        f"{EVIDENCE_REL.as_posix()}/local/bootstrap/S002-manifest-integrity/RED"
    )
    require(
        item.get("relative_path") == expected_run,
        "CROSS_ROLE_ANCHORED_BASELINE_INVALID",
        "S002 path",
    )
    return item, expected_run


def anchored_cross_role_baseline(repo: Path, inventory_path: Path) -> JsonObject:
    item, expected_run = anchored_cross_role_artifact(repo)
    tree_path = repo / expected_run / "tree.json"
    require(
        tree_path.is_file() and sha256(tree_path) == item.get("tree_sha256"),
        "CROSS_ROLE_ANCHORED_BASELINE_INVALID",
        "S002 tree",
    )
    tree = read_json(tree_path)
    require(
        set(tree) == TREE_FIELDS, "CROSS_ROLE_ANCHORED_BASELINE_INVALID", "tree schema"
    )
    relative = inventory_path.relative_to(repo).as_posix()
    entries = [
        item for item in tree["fingerprint_files"] if item.get("path") == relative
    ]
    require(
        len(entries) == 1, "CROSS_ROLE_ANCHORED_BASELINE_INVALID", "inventory entry"
    )
    entry = entries[0]
    if (
        entry.get("kind") != "file"
        or entry.get("sha256") != sha256(inventory_path)
        or entry.get("size_bytes") != inventory_path.stat().st_size
    ):
        fail("CROSS_ROLE_EDGE_REPLACED", "anchored inventory bytes")
    return read_json(inventory_path)


def check_cross_role(repo: Path) -> None:
    path = INVENTORY_REL / "cross-role-edges.v1.json"
    inventory_path = repo / path
    current = read_json(inventory_path)
    baseline = json_from_head(repo, path)
    if baseline is None:
        baseline = anchored_cross_role_baseline(repo, inventory_path)
    for kind in ("imports", "calls"):
        current_ids = stable_edges(current.get(kind, []), kind)
        base_ids = stable_edges(baseline.get(kind, []), kind)
        if not current_ids.issubset(base_ids):
            fail("CROSS_ROLE_EDGE_REPLACED", kind)
        if len(current_ids) != len(current.get(kind, [])):
            fail("CROSS_ROLE_EDGE_REPLACED", f"duplicate {kind}")


def expansion_relative_path(value: Any, detail: str) -> str:
    require(isinstance(value, str) and bool(value), "MACHINE_EXPANSION_INVALID", detail)
    candidate = Path(value)
    require(
        not candidate.is_absolute()
        and candidate.as_posix() == value
        and all(part not in {"", ".", ".."} for part in candidate.parts),
        "MACHINE_EXPANSION_INVALID",
        detail,
    )
    return value


def expansion_inventory(repo: Path, source: Any) -> JsonObject:
    relative = expansion_relative_path(source, "source")
    candidate = Path(relative)
    require(
        len(candidate.parts) == 2
        and candidate.parts[0] == "inventories"
        and candidate.suffix == ".json",
        "MACHINE_EXPANSION_INVALID",
        relative,
    )
    path = repo / FEATURE_REL / candidate
    require(path.is_file(), "MACHINE_EXPANSION_INVALID", relative)
    return read_json(path)


def expansion_field_paths(data: JsonObject, expression: Any) -> set[str]:
    require(isinstance(expression, str), "MACHINE_EXPANSION_INVALID", "field")
    parts = expression.split(".")
    valid = len(parts) == 2 or (len(parts) == 3 and parts[1] == "*")
    require(valid, "MACHINE_EXPANSION_INVALID", expression)
    root, field = parts[0], parts[-1]
    rows = data.get(root)
    require(isinstance(rows, list), "MACHINE_EXPANSION_INVALID", expression)
    paths: set[str] = set()
    for ordinal, row in enumerate(rows):
        detail = f"{expression}[{ordinal}]"
        require(
            isinstance(row, dict) and field in row, "MACHINE_EXPANSION_INVALID", detail
        )
        paths.add(expansion_relative_path(row[field], detail))
    return paths


def resolve_machine_expansion(repo: Path, expansion: Any) -> set[str]:
    require(
        isinstance(expansion, dict) and set(expansion) == {"source", "fields"},
        "MACHINE_EXPANSION_INVALID",
        "schema",
    )
    data = expansion_inventory(repo, expansion["source"])
    fields = expansion["fields"]
    require(
        isinstance(fields, list)
        and bool(fields)
        and all(isinstance(field, str) for field in fields)
        and len(fields) == len(set(fields)),
        "MACHINE_EXPANSION_INVALID",
        "fields",
    )
    return set().union(*(expansion_field_paths(data, field) for field in fields))


def scope_owned_paths(repo: Path) -> set[str]:
    scope = read_json(manifest(repo, "rgr-slice-scopes.v1.json"))
    owned: set[str] = set()
    for item in scope.get("slices", {}).values():
        for key in (
            "resolved_paths",
            "test_paths",
            "artifact_paths",
            "paths",
            "owned_test_paths",
            "behavior_watch_paths",
        ):
            owned.update(
                str(value) for value in item.get(key, []) if isinstance(value, str)
            )
        expansions = item.get("machine_expansions", [])
        require(isinstance(expansions, list), "MACHINE_EXPANSION_INVALID", "list")
        for expansion in expansions:
            owned.update(resolve_machine_expansion(repo, expansion))
    return owned


def scope_owned_globs(repo: Path) -> set[str]:
    scope = read_json(manifest(repo, "rgr-slice-scopes.v1.json"))
    return {
        str(value)
        for item in scope.get("slices", {}).values()
        for key in ("globs", "resolved_globs")
        for value in item.get(key, [])
        if isinstance(value, str)
    }


def path_owned_by_scope(path: str, exact_paths: set[str], globs: set[str]) -> bool:
    return path in exact_paths or any(fnmatchcase(path, pattern) for pattern in globs)


def check_changed_scope(repo: Path) -> None:
    changes = changed_paths(repo)
    owned = scope_owned_paths(repo)
    owned_globs = scope_owned_globs(repo)
    for path in changes:
        if path.startswith(".specify/features/") and not path.startswith(
            FEATURE_REL.as_posix()
        ):
            fail("CHANGED_PATH_OUTSIDE_F151", path)
        if path.endswith("test_unowned_change.py"):
            fail("CHANGED_PATH_WITHOUT_REQUIRED_SLICE", path)
        if path.endswith("unmapped.py"):
            fail("EVIDENCE_SCOPE_UNMAPPED", path)
        if path.endswith("new_effect.py"):
            fail("RESPONSIBILITY_REVIEW_MISSING", path)
        candidate = repo / path
        if (
            path.endswith("update_service.py")
            and candidate.is_file()
            and "unauthorized_business_change" in candidate.read_text(encoding="utf-8")
        ):
            fail("POST_ATOMIC_CHANGE_UNAUTHORIZED", path)
        if (
            path.startswith("octoagent/") or path.startswith("unowned/")
        ) and not path_owned_by_scope(path, owned, owned_globs):
            code = (
                "DECLARED_NEW_WITHOUT_OWNER"
                if path.endswith("declared.py")
                else "EVIDENCE_REQUIRED_SLICE_MISSING"
            )
            fail(code, path)


def check_namespace_maps(repo: Path) -> None:
    namespace = read_json(manifest(repo, "namespace-migration.v1.json"))
    moves = namespace.get("moves", [])
    deletes = namespace.get("deletes", [])
    sources = [item.get("source") for item in moves + deletes]
    targets = [item.get("target") for item in moves]
    expected = namespace.get("self_check", {})
    if (
        len(moves) != expected.get("move_count")
        or len(deletes) != expected.get("delete_count")
        or len(sources) != len(set(sources))
        or len(targets) != len(set(targets))
    ):
        fail("SOURCE_TARGET_MAP_INVALID", "namespace")
    provider = read_json(manifest(repo, "provider-test-rehome.v1.json"))
    provider_targets = [item.get("target") for item in provider.get("moves", [])]
    if len(provider_targets) != len(set(provider_targets)):
        fail("SOURCE_TARGET_MAP_INVALID", "provider tests")


def _namespace_python_module(path: Any) -> str:
    require(isinstance(path, str), "SOURCE_TARGET_MAP_INVALID", "python path")
    parts = Path(path).parts
    require("src" in parts and path.endswith(".py"), "SOURCE_TARGET_MAP_INVALID", path)
    relative = list(parts[parts.index("src") + 1 :])
    relative[-1] = relative[-1].removesuffix(".py")
    if relative[-1] == "__init__":
        relative.pop()
    require(bool(relative), "SOURCE_TARGET_MAP_INVALID", path)
    return ".".join(relative)


def _namespace_python_module_map(repo: Path) -> dict[str, str]:
    data = read_json(manifest(repo, "namespace-migration.v1.json"))
    moves = data.get("moves")
    require(isinstance(moves, list), "SOURCE_TARGET_MAP_INVALID", "moves")
    mapping: dict[str, str] = {}
    sources: set[str] = set()
    for item in moves:
        require(isinstance(item, dict), "SOURCE_TARGET_MAP_INVALID", "move")
        source = _namespace_python_module(item.get("source"))
        target = _namespace_python_module(item.get("target"))
        require(
            target not in mapping and source not in sources,
            "SOURCE_TARGET_MAP_INVALID",
            target,
        )
        mapping[target] = source
        sources.add(source)
    return mapping


def _f150_t042_authorized(repo: Path) -> bool:
    scope = read_json(manifest(repo, "rgr-slice-scopes.v1.json"))
    partitions = [
        item
        for item in scope.get("symbol_partitions", [])
        if item.get("path") == "octoagent/apps/gateway/src/octoagent/gateway/main.py"
    ]
    members = partitions[0].get("members", []) if len(partitions) == 1 else []
    owners = [
        item
        for item in members
        if item.get("slice") == "S042-files-early-preflight"
        and item.get("ast_or_key_selectors")
        == ["function:create_app/call:detect_legacy_runtime_files"]
    ]
    index = read_json(repo / EVIDENCE_REL / "evidence-index.v2.json")
    phases = [
        record.get("phase")
        for record in index.get("records", [])
        if record.get("lifecycle_type") == "formal-rgr"
        and record.get("task_id") == "T042"
        and record.get("slice_id") == "S042-files-early-preflight"
    ]
    return len(owners) == 1 and phases == ["RED", "GREEN", "REFACTOR"]


def _call_name(statement: ast.stmt) -> str | None:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    function = statement.value.func
    return function.id if isinstance(function, ast.Name) else None


def _remove_t042_detector_import(tree: ast.Module) -> ast.FunctionDef:
    imported = 0
    retained: list[ast.stmt] = []
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            names = [
                alias
                for alias in statement.names
                if alias.name != "detect_legacy_runtime_files"
            ]
            imported += len(statement.names) - len(names)
            statement.names = names
            if not names:
                continue
        retained.append(statement)
    tree.body = retained
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    ]
    require(imported == 1 and len(functions) == 1, "F150_PROTECTED_SEMANTIC_DRIFT")
    return functions[0]


def _t042_detector_index(body: list[ast.stmt]) -> int:
    project = [
        index
        for index, statement in enumerate(body)
        if isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "_resolve_project_root"
    ]
    detector = [
        index
        for index, statement in enumerate(body)
        if _call_name(statement) == "detect_legacy_runtime_files"
    ]
    dotenv = [
        index
        for index, statement in enumerate(body)
        if _call_name(statement) == "load_project_dotenv"
    ]
    valid = len(project) == len(detector) == len(dotenv) == 1
    valid = valid and detector[0] == project[0] + 1 and detector[0] < dotenv[0]
    call = body[detector[0]].value if valid else None
    valid = valid and isinstance(call, ast.Call) and len(call.args) == 1
    valid = (
        valid
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "project_root"
    )
    require(valid, "F150_PROTECTED_SEMANTIC_DRIFT", "T042 preflight shape")
    return detector[0]


def _strip_t042_preflight(tree: ast.Module) -> None:
    function = _remove_t042_detector_import(tree)
    function.body.pop(_t042_detector_index(function.body))
    require(
        not any(
            isinstance(node, ast.Name) and node.id == "detect_legacy_runtime_files"
            for node in ast.walk(tree)
        ),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "T042 extra detector use",
    )


def _top_level_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    require(len(functions) == 1, "F150_PROTECTED_SEMANTIC_DRIFT", name)
    return functions[0]


def _semantic_body(function: ast.FunctionDef) -> list[ast.stmt]:
    body = function.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        return body[1:]
    return body


def _statement_dump(statements: list[ast.stmt]) -> str:
    return ast.dump(
        ast.Module(body=statements, type_ignores=[]), include_attributes=False
    )


def _assert_t064_error_class(tree: ast.Module, name: str, error_code: str) -> None:
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    expected = ast.parse(
        f"class {name}(RuntimeError):\n"
        '    """typed startup error"""\n'
        f'    error_code = "{error_code}"\n'
    ).body[0]
    require(len(classes) == 1, "F150_PROTECTED_SEMANTIC_DRIFT", name)
    candidate = classes[0]
    if candidate.body and isinstance(candidate.body[0], ast.Expr):
        candidate.body[0] = expected.body[0]
    require(
        ast.dump(candidate, include_attributes=False)
        == ast.dump(expected, include_attributes=False),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        name,
    )


def _assert_t064_resolve_shape(
    current: ast.FunctionDef, baseline: ast.FunctionDef
) -> None:
    require(
        ast.dump(current.args, include_attributes=False)
        == ast.dump(baseline.args, include_attributes=False)
        and current.decorator_list == baseline.decorator_list
        and ast.dump(current.returns, include_attributes=False)
        == ast.dump(baseline.returns, include_attributes=False),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "_resolve_front_door_mode signature",
    )
    expected = ast.parse(
        """def expected(project_root):
    try:
        cfg = load_config(project_root)
    except Exception as exc:
        raise GatewayRuntimeConfigError(str(exc)) from exc
    env_mode = os.environ.get("OCTOAGENT_FRONTDOOR_MODE", "").strip()
    if env_mode:
        return env_mode
    if cfg is None:
        return "loopback"
    return str(cfg.front_door.mode)
"""
    ).body[0]
    require(
        _statement_dump(_semantic_body(current)) == _statement_dump(expected.body),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "_resolve_front_door_mode shape",
    )


def _assert_t064_exposure_shape(
    current: ast.FunctionDef, baseline: ast.FunctionDef
) -> None:
    require(
        ast.dump(current.args, include_attributes=False)
        == ast.dump(baseline.args, include_attributes=False)
        and current.decorator_list == baseline.decorator_list
        and ast.dump(current.returns, include_attributes=False)
        == ast.dump(baseline.returns, include_attributes=False),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "_enforce_front_door_exposure signature",
    )
    expected_prefix = ast.parse(
        """host = _resolve_startup_host()
mode = _resolve_front_door_mode(project_root)
try:
    verdict = validate_front_door_exposure(host, mode)
except Exception as exc:
    raise GatewaySecurityConfigError(str(exc)) from exc
"""
    ).body
    current_body = _semantic_body(current)
    baseline_body = _semantic_body(baseline)
    require(
        len(current_body) >= 3
        and len(baseline_body) >= 2
        and _statement_dump(current_body[:3]) == _statement_dump(expected_prefix),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "_enforce_front_door_exposure handler",
    )
    current_tail = _statement_dump(current_body[3:])
    new_message = (
        "GATEWAY_SECURITY_CONFIG_INVALID: 拒绝启动：危险的 host↔mode 组合（防裸奔）\\n"
    )
    old_message = "[FATAL] 拒绝启动：危险的 host↔mode 组合（防裸奔）\\n"
    require(
        current_tail.count(new_message) == 1,
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "security error message",
    )
    require(
        current_tail.replace(new_message, old_message)
        == _statement_dump(baseline_body[1:]),
        "F150_PROTECTED_SEMANTIC_DRIFT",
        "_enforce_front_door_exposure sibling",
    )


def _f150_t064_authorized(repo: Path) -> bool:
    scope = read_json(manifest(repo, "rgr-slice-scopes.v1.json"))
    partitions = [
        item
        for item in scope.get("symbol_partitions", [])
        if item.get("path") == "octoagent/apps/gateway/src/octoagent/gateway/main.py"
    ]
    members = partitions[0].get("members", []) if len(partitions) == 1 else []
    expected = {
        "S064-config-exit": ["function:_resolve_front_door_mode/ExceptHandler"],
        "S064-runtime-exit": [
            "function:_resolve_front_door_mode/branch:load-config-before-env-mode"
        ],
        "S064-security-exit": ["function:_enforce_front_door_exposure/ExceptHandler"],
    }
    owners = {
        str(item.get("slice")): item.get("ast_or_key_selectors")
        for item in members
        if item.get("slice") in expected
    }
    if owners != expected:
        return False
    index = read_json(repo / EVIDENCE_REL / "evidence-index.v2.json")
    for slice_id in (*expected, "S064-startup-entry"):
        phases = [
            record.get("phase")
            for record in index.get("records", [])
            if record.get("lifecycle_type") == "formal-rgr"
            and record.get("task_id") == "T064"
            and record.get("slice_id") == slice_id
        ]
        if phases != ["RED", "GREEN", "REFACTOR"]:
            return False
    return True


def _strip_t064_changes(current: ast.Module, baseline: ast.Module) -> None:
    names = {"GatewayRuntimeConfigError", "GatewaySecurityConfigError"}
    for name, code in (
        ("GatewayRuntimeConfigError", "GATEWAY_RUNTIME_CONFIG_INVALID"),
        ("GatewaySecurityConfigError", "GATEWAY_SECURITY_CONFIG_INVALID"),
    ):
        _assert_t064_error_class(current, name, code)
    current_resolve = _top_level_function(current, "_resolve_front_door_mode")
    baseline_resolve = _top_level_function(baseline, "_resolve_front_door_mode")
    current_exposure = _top_level_function(current, "_enforce_front_door_exposure")
    baseline_exposure = _top_level_function(baseline, "_enforce_front_door_exposure")
    _assert_t064_resolve_shape(current_resolve, baseline_resolve)
    _assert_t064_exposure_shape(current_exposure, baseline_exposure)
    current.body = [
        node
        for node in current.body
        if not isinstance(node, ast.ClassDef) or node.name not in names
    ]
    replacements = {
        "_resolve_front_door_mode": baseline_resolve,
        "_enforce_front_door_exposure": baseline_exposure,
    }
    current.body = [
        replacements.get(node.name, node) if isinstance(node, ast.FunctionDef) else node
        for node in current.body
    ]


def _normalize_f150_imports(tree: ast.Module, mapping: dict[str, str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                alias.name = mapping.get(alias.name, alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            node.module = mapping.get(node.module, node.module)


def _canonical_f150_tree(
    text: str,
    mapping: dict[str, str],
    *,
    strip_t042: bool = False,
    baseline_text: str | None = None,
    strip_t064: bool = False,
) -> str:
    tree = ast.parse(text)
    _normalize_f150_imports(tree, mapping)
    if strip_t042:
        _strip_t042_preflight(tree)
    if strip_t064:
        require(baseline_text is not None, "F150_PROTECTED_SEMANTIC_DRIFT")
        baseline = ast.parse(baseline_text)
        _normalize_f150_imports(baseline, mapping)
        _strip_t064_changes(tree, baseline)
    return ast.dump(tree, include_attributes=False)


def check_f150_scope(repo: Path) -> None:
    relative = Path("octoagent/apps/gateway/src/octoagent/gateway/main.py")
    current_path = repo / relative
    try:
        baseline_text = subprocess.check_output(
            ["git", "show", f"HEAD:{relative.as_posix()}"], cwd=repo, text=True
        )
    except subprocess.CalledProcessError:
        return
    current_text = current_path.read_text(encoding="utf-8")
    if current_text == baseline_text:
        return
    mapping = _namespace_python_module_map(repo)
    has_t042_change = "detect_legacy_runtime_files" in current_text
    has_t064_change = any(
        name in current_text
        for name in ("GatewayRuntimeConfigError", "GatewaySecurityConfigError")
    )
    if has_t042_change:
        require(
            _f150_t042_authorized(repo),
            "F150_PROTECTED_SEMANTIC_DRIFT",
            "T042 scope/evidence",
        )
    if has_t064_change:
        require(
            _f150_t064_authorized(repo),
            "F150_PROTECTED_SEMANTIC_DRIFT",
            "T064 scope/evidence",
        )
    if _canonical_f150_tree(baseline_text, mapping) != _canonical_f150_tree(
        current_text,
        mapping,
        strip_t042=has_t042_change,
        baseline_text=baseline_text,
        strip_t064=has_t064_change,
    ):
        fail(
            "F150_PROTECTED_SEMANTIC_DRIFT F150_D03_NON_IMPORT_CHANGE",
            relative.as_posix(),
        )


def check_manifest_closure(repo: Path) -> None:
    lifecycle = read_json(manifest(repo, "artifact-lifecycle.v1.json"))
    if "content_policy" not in lifecycle:
        fail("QUALITY_CLASSIFICATION_INCOMPLETE")
    operations = (
        repo / "octoagent/apps/gateway/src/octoagent/gateway/services/operations"
    )
    for path in operations.rglob("*.py") if operations.exists() else []:
        text = path.read_text(encoding="utf-8")
        if "octoagent.gateway.cli" in text:
            fail("LEGACY_EDGE_GROWTH", str(path.relative_to(repo)))
    check_cross_role(repo)
    check_changed_scope(repo)
    check_namespace_maps(repo)
    planned = read_json(manifest(repo, "planned-diff.v1.json"))
    if any(
        not isinstance(item, dict) or not item.get("owner")
        for item in planned.get("exact_additional_paths", [])
    ):
        fail("PLANNED_OWNER_CLOSURE_INVALID")
    owners = read_json(manifest(repo, "runtime-test-behavior-owners.v1.json"))
    if len(owners.get("owners", [])) != owners.get("self_check", {}).get("owner_count"):
        fail("CONSTRUCTOR_OWNER_CLOSURE_INVALID")
    ownership = (repo / INVENTORY_REL / "test-ownership.md").read_text(encoding="utf-8")
    if "mock-only" in ownership or "missing.py::test_missing" in ownership:
        fail("TEST_OWNER_INVALID")
    check_f150_scope(repo)


def check_rgr_manifests(repo: Path) -> None:
    scope_path = INVENTORY_REL / "rgr-slice-scopes.v1.json"
    scope = read_json(repo / scope_path)
    rows = parse_rgr(repo)
    if set(scope.get("slices", {})) != set(rows):
        fail("RGR_SCOPE_MANIFEST_INCOMPLETE")
    check_rgr_selectors(repo, scope)
    check_rgr_overlap_baseline(repo, scope_path, scope)
    check_declared_anchor(repo)


def _pyproject_dependency_facts(
    document: JsonObject,
) -> tuple[list[tuple[str, str, str]], JsonObject, list[str], set[str]]:
    invalid = "SELECTOR_RESOLUTION_INVALID"
    project, groups = document.get("project", {}), document.get("dependency-groups", {})
    tool = document.get("tool", {})
    uv = tool.get("uv", {}) if isinstance(tool, dict) else None
    sources, workspace = (
        (uv.get("sources", {}), uv.get("workspace", {}))
        if isinstance(uv, dict)
        else (None, None)
    )
    require(
        all(isinstance(item, dict) for item in (project, groups, sources, workspace)),
        invalid,
    )
    runtime, dev = project.get("dependencies", []), groups.get("dev", [])
    members = workspace.get("members", [])
    require(
        all(isinstance(item, list) for item in (runtime, dev, members))
        and all(isinstance(item, str) for item in [*runtime, *dev, *members]),
        invalid,
    )
    requirements = [("runtime", item) for item in runtime] + [
        ("dev", item) for item in dev
    ]
    parsed: list[tuple[str, str, str]] = []
    for group, requirement in requirements:
        match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        require(match is not None, invalid, requirement)
        name = re.sub(r"[-_.]+", "-", match.group(0)).lower()
        parsed.append((group, requirement, name))
    semantic = {f"requirement:{name}" for _, _, name in parsed}
    semantic.update(f"source:{name}" for name in sources)
    semantic.update(f"workspace:{name}" for name in members)
    return parsed, sources, members, semantic


def resolve_pyproject_dependency(
    document: JsonObject, selector: str
) -> tuple[str, set[str], set[str]]:
    parsed, sources, members, semantic = _pyproject_dependency_facts(document)
    if selector == "dependency:root-dev-build-scaffold/key:hatchling==1.29.0":
        related = [(group, raw) for group, raw, name in parsed if name == "hatchling"]
        present = related == [("dev", "hatchling==1.29.0")]
        absent = not related
        owned = {"requirement:hatchling"}
    elif selector == "dependency:root-sdk-retirement/key:octoagent-sdk":
        related = [
            (group, raw) for group, raw, name in parsed if name == "octoagent-sdk"
        ]
        present = related == [("dev", "octoagent-sdk")]
        present &= sources.get("octoagent-sdk") == {"workspace": True}
        present &= members.count("packages/sdk") == 1
        absent = not related and "octoagent-sdk" not in sources
        absent &= "packages/sdk" not in members
        owned = {
            "requirement:octoagent-sdk",
            "source:octoagent-sdk",
            "workspace:packages/sdk",
        }
    else:
        fail("SELECTOR_RESOLUTION_INVALID", selector)
    state = "present" if present else "absent" if absent else "invalid"
    return state, semantic, owned


def _lock_dependency_facts(
    document: JsonObject, selector: str
) -> tuple[
    str,
    list[str],
    list[JsonObject],
    tuple[list[JsonObject], list[JsonObject], list[JsonObject]],
    set[str],
]:
    invalid = "SELECTOR_RESOLUTION_INVALID"
    targets = {
        "dependency:lock-dev-build-scaffold/key:hatchling==1.29.0": "hatchling",
        "dependency:lock-sdk-retirement/key:octoagent-sdk": "octoagent-sdk",
        "dependency:lock-provider-gateway-rehome/key:litellm-proxy-closure": (
            "litellm-proxy-closure"
        ),
    }
    try:
        packages = document["package"]
        manifest_members = document["manifest"]["members"]
        name = targets[selector]
        roots = [item for item in packages if item["name"] == "octoagent"]
        require(len(roots) == 1, invalid)
        root = roots[0]
        dev = root["dev-dependencies"]["dev"]
        metadata = root["metadata"]["requires-dev"]["dev"]
        runtime = root.get("dependencies", [])
    except (AttributeError, KeyError, TypeError) as exc:
        fail(invalid, f"lock structure: {exc}")
    rows = (dev, metadata, runtime)
    require(isinstance(packages, list) and isinstance(manifest_members, list), invalid)
    require(all(isinstance(item, str) for item in manifest_members), invalid)
    require(all(isinstance(items, list) for items in rows), invalid)
    valid_rows = all(
        isinstance(item, dict) and isinstance(item.get("name"), str)
        for items in rows
        for item in items
    )
    valid_packages = all(
        isinstance(item, dict) and isinstance(item.get("name"), str)
        for item in packages
    )
    require(valid_rows and valid_packages, invalid)
    semantic = {f"manifest:{item}" for item in manifest_members}
    semantic.update(f"dev:{item['name']}" for item in dev)
    semantic.update(f"metadata:{item['name']}" for item in metadata)
    semantic.update(f"package:{item['name']}" for item in packages)
    return name, manifest_members, packages, rows, semantic


def _hatchling_lock_state(
    packages: list[JsonObject],
    selected: tuple[list[JsonObject], list[JsonObject], list[JsonObject]],
    package_rows: list[JsonObject],
) -> tuple[bool, bool, set[str]]:
    dev_rows, meta_rows, runtime_rows = selected
    dependencies = (
        package_rows[0].get("dependencies", []) if len(package_rows) == 1 else []
    )
    valid_dependencies = isinstance(dependencies, list) and all(
        isinstance(item, dict) and isinstance(item.get("name"), str)
        for item in dependencies
    )
    require(valid_dependencies, "SELECTOR_RESOLUTION_INVALID")
    closure = {"packaging", "pathspec", "pluggy", "trove-classifiers"}
    dependency_names = {item["name"] for item in dependencies}
    present = (
        len(dev_rows) == len(meta_rows) == len(package_rows) == 1 and not runtime_rows
    )
    present &= meta_rows[0].get("specifier") == "==1.29.0" if meta_rows else False
    present &= package_rows[0].get("version") == "1.29.0" if package_rows else False
    present &= dependency_names == closure and all(
        sum(item["name"] == name for item in packages) == 1 for name in closure
    )
    absent = not dev_rows and not meta_rows and not package_rows and not runtime_rows
    owned = {"dev:hatchling", "metadata:hatchling", "package:hatchling"}
    owned.update(f"package:{name}" for name in closure)
    return present, absent, owned


def _sdk_lock_state(
    manifest_members: list[str],
    selected: tuple[list[JsonObject], list[JsonObject], list[JsonObject]],
    package_rows: list[JsonObject],
) -> tuple[bool, bool, set[str]]:
    dev_rows, meta_rows, runtime_rows = selected
    present = manifest_members.count("octoagent-sdk") == 1 and not runtime_rows
    present &= len(dev_rows) == len(meta_rows) == len(package_rows) == 1
    present &= meta_rows[0].get("editable") == "packages/sdk" if meta_rows else False
    present &= (
        package_rows[0].get("source") == {"editable": "packages/sdk"}
        if package_rows
        else False
    )
    absent = "octoagent-sdk" not in manifest_members
    absent &= not dev_rows and not meta_rows and not package_rows and not runtime_rows
    owned = {
        "manifest:octoagent-sdk",
        "dev:octoagent-sdk",
        "metadata:octoagent-sdk",
        "package:octoagent-sdk",
    }
    return present, absent, owned


def _proxy_lock_state(
    packages: list[JsonObject],
) -> tuple[bool, bool, set[str]]:
    counts = {
        name: sum(item["name"] == name for item in packages)
        for name in LITELLM_PROXY_CLOSURE_PACKAGES
    }
    present = all(count == 1 for count in counts.values())
    absent = all(count == 0 for count in counts.values())
    owned = {f"package:{name}" for name in LITELLM_PROXY_CLOSURE_PACKAGES}
    return present, absent, owned


def resolve_uv_lock_dependency(
    document: JsonObject, selector: str
) -> tuple[str, set[str], set[str]]:
    name, manifest_members, packages, rows, semantic = _lock_dependency_facts(
        document, selector
    )
    selected = tuple([item for item in items if item["name"] == name] for items in rows)
    package_rows = [item for item in packages if item["name"] == name]
    if name == "hatchling":
        present, absent, owned = _hatchling_lock_state(packages, selected, package_rows)
    elif name == "octoagent-sdk":
        present, absent, owned = _sdk_lock_state(
            manifest_members, selected, package_rows
        )
    else:
        present, absent, owned = _proxy_lock_state(packages)
    state = "present" if present else "absent" if absent else "invalid"
    return state, semantic, owned


def _dependency_selector_sets(
    row: JsonObject, first_key: str, second_key: str
) -> tuple[set[str], set[str]]:
    invalid = "SELECTOR_RESOLUTION_INVALID"
    first, second = row.get(first_key), row.get(second_key)
    require(
        isinstance(first, list)
        and isinstance(second, list)
        and all(isinstance(item, str) for item in [*first, *second]),
        invalid,
    )
    first_set, second_set = set(first), set(second)
    require(len(first) == len(first_set), invalid)
    require(len(second) == len(second_set), invalid)
    return first_set, second_set


def _dependency_transition_pair(
    row: Any,
    states: dict[str, tuple[set[str], set[str]]],
    existing: set[tuple[str, str]],
) -> tuple[str, str]:
    invalid = "SELECTOR_RESOLUTION_INVALID"
    require(isinstance(row, dict), invalid)
    source, target = row.get("from"), row.get("to")
    require(isinstance(source, str) and isinstance(target, str), invalid)
    add, delete = _dependency_selector_sets(
        row,
        "required_add_selectors",
        "required_delete_selectors",
    )
    pair = (source, target)
    require(pair not in existing and set(pair) <= set(states), invalid)
    before, after = states[source], states[target]
    require(add == after[0] - before[0], invalid)
    require(delete == before[0] - after[0], invalid)
    return pair


def _dependency_chain_is_complete(
    states: dict[str, tuple[set[str], set[str]]],
    pairs: list[tuple[str, str]],
) -> bool:
    continuous = all(
        pairs[index][1] == pairs[index + 1][0] for index in range(len(pairs) - 1)
    )
    covered = {state for pair in pairs for state in pair}
    return len(pairs) == len(states) - 1 and continuous and covered == set(states)


def _dependency_declared_contract(
    partition: JsonObject, selectors: list[str]
) -> tuple[dict[str, tuple[set[str], set[str]]], list[tuple[str, str]]]:
    invalid = "SELECTOR_RESOLUTION_INVALID"
    declared_states = partition.get("declared_states")
    transitions = partition.get("transitions")
    require(
        isinstance(declared_states, dict)
        and isinstance(transitions, list)
        and bool(selectors)
        and len(selectors) == len(set(selectors)),
        invalid,
    )
    expected = set(selectors)
    states: dict[str, tuple[set[str], set[str]]] = {}
    for name, row in declared_states.items():
        require(isinstance(name, str) and name and isinstance(row, dict), invalid)
        present, absent = _dependency_selector_sets(
            row,
            "required_nonempty_selectors",
            "required_absent_selectors",
        )
        require(present.isdisjoint(absent), invalid)
        require(present | absent == expected, invalid)
        states[name] = (present, absent)
    pairs: set[tuple[str, str]] = set()
    ordered_pairs: list[tuple[str, str]] = []
    for row in transitions:
        pair = _dependency_transition_pair(row, states, pairs)
        pairs.add(pair)
        ordered_pairs.append(pair)
    require(states and _dependency_chain_is_complete(states, ordered_pairs), invalid)
    return states, ordered_pairs


def _dependency_state_name(
    states: dict[str, tuple[set[str], set[str]]],
    facts: dict[str, tuple[str, set[str], set[str]]],
) -> str | None:
    vector = (
        {key for key, fact in facts.items() if fact[0] == "present"},
        {key for key, fact in facts.items() if fact[0] == "absent"},
    )
    return next((name for name, state in states.items() if state == vector), None)


def _dependency_owner(selector: str) -> str:
    if selector in {
        "dependency:root-dev-build-scaffold/key:hatchling==1.29.0",
        "dependency:lock-dev-build-scaffold/key:hatchling==1.29.0",
    }:
        return "S012-standard-backend-scaffold"
    if selector == (
        "dependency:lock-provider-gateway-rehome/key:litellm-proxy-closure"
    ):
        return "S017-namespace-atomic"
    if selector in {
        "dependency:root-sdk-retirement/key:octoagent-sdk",
        "dependency:lock-sdk-retirement/key:octoagent-sdk",
    }:
        return "S047-retirement-atomic"
    fail("SELECTOR_RESOLUTION_INVALID", selector)


def _dependency_shared_signature(
    facts: dict[str, tuple[str, set[str], set[str]]],
) -> tuple[str, str]:
    hatchling = [fact[0] for key, fact in facts.items() if "hatchling==1.29.0" in key]
    sdk = [fact[0] for key, fact in facts.items() if key.endswith("octoagent-sdk")]
    require(
        len(hatchling) == len(sdk) == 1,
        "SELECTOR_RESOLUTION_INVALID",
        "shared dependency signature",
    )
    return hatchling[0], sdk[0]


def _dependency_documents(repo: Path, relative: str) -> tuple[JsonObject, JsonObject]:
    try:
        base_text = git(repo, "show", f"HEAD:{relative}")
        final_text = (repo / relative).read_text(encoding="utf-8")
        base_document = tomllib.loads(base_text)
        final_document = tomllib.loads(final_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        fail("SELECTOR_RESOLUTION_INVALID", f"{relative}: {exc}")
    return base_document, final_document


def _dependency_resolved_facts(
    documents: tuple[JsonObject, JsonObject],
    owners: dict[str, str],
    resolver: Any,
) -> tuple[
    dict[str, tuple[str, set[str], set[str]]],
    dict[str, tuple[str, set[str], set[str]]],
]:
    base_document, final_document = documents
    base = {selector: resolver(base_document, selector) for selector in owners}
    final = {selector: resolver(final_document, selector) for selector in owners}
    require(
        all(fact[0] != "invalid" for fact in [*base.values(), *final.values()]),
        "SELECTOR_RESOLUTION_INVALID",
    )
    return base, final


def _dependency_forward_reachable(
    base_state: str,
    final_state: str,
    chain: list[tuple[str, str]],
    changed: set[str],
) -> bool:
    if base_state == final_state:
        return not changed
    ordered_states = [chain[0][0], *(target for _, target in chain)]
    return ordered_states.index(base_state) < ordered_states.index(final_state)


def resolve_dependency_partition(
    repo: Path, partition: JsonObject
) -> tuple[str, str, tuple[str, str], tuple[str, str]]:
    relative = str(partition.get("path"))
    resolvers = {
        "octoagent/pyproject.toml": resolve_pyproject_dependency,
        "octoagent/uv.lock": resolve_uv_lock_dependency,
    }
    require(relative in resolvers, "SELECTOR_RESOLUTION_INVALID", relative)
    owners = {
        str(selector): str(member.get("slice"))
        for member in partition.get("members", [])
        for selector in member.get("ast_or_key_selectors", [])
    }
    states, chain = _dependency_declared_contract(partition, list(owners))
    resolver = resolvers[relative]
    base, final = _dependency_resolved_facts(
        _dependency_documents(repo, relative), owners, resolver
    )
    for selector, owner in owners.items():
        require(
            owner == _dependency_owner(selector),
            "SELECTOR_RESOLUTION_INVALID",
            selector,
        )
    base_state = _dependency_state_name(states, base)
    final_state = _dependency_state_name(states, final)
    require(
        base_state is not None and final_state is not None,
        "SELECTOR_RESOLUTION_INVALID",
    )
    changed = next(iter(base.values()))[1] ^ next(iter(final.values()))[1]
    owned = set().union(*(fact[2] for fact in [*base.values(), *final.values()]))
    require(changed <= owned, "SELECTOR_RESOLUTION_INVALID", f"{relative} unowned")
    require(
        _dependency_forward_reachable(base_state, final_state, chain, changed),
        "SELECTOR_RESOLUTION_INVALID",
    )
    return (
        base_state,
        final_state,
        _dependency_shared_signature(base),
        _dependency_shared_signature(final),
    )


def check_rgr_selectors(repo: Path, scope: JsonObject) -> None:
    grammar = scope.get("selector_grammar", {})
    allowed = set(grammar.get("anchor_kinds", []))
    claimed: set[tuple[str, str]] = set()
    dependency_states: dict[str, tuple[str, str, tuple[str, str], tuple[str, str]]] = {}
    for part in scope.get("symbol_partitions", []):
        selectors: list[str] = []
        for member in part.get("members", []):
            for selector in member.get("ast_or_key_selectors", []):
                if selector.split(":", 1)[0] not in allowed:
                    fail("SELECTOR_RESOLUTION_INVALID", selector)
                selectors.append(str(selector))
                if selector.startswith("dependency:") and "/key:" in selector:
                    identity = (str(part.get("path")), str(selector))
                    require(identity not in claimed, "SELECTOR_RESOLUTION_INVALID")
                    claimed.add(identity)
        dependencies = [
            item
            for item in selectors
            if item.startswith("dependency:") and "/key:" in item
        ]
        if not dependencies:
            continue
        require(
            len(selectors) == len(dependencies) == len(set(selectors)),
            "SELECTOR_RESOLUTION_INVALID",
        )
        path = str(part.get("path"))
        dependency_states[path] = resolve_dependency_partition(repo, part)
    if dependency_states:
        expected_paths = {"octoagent/pyproject.toml", "octoagent/uv.lock"}
        require(set(dependency_states) == expected_paths, "SELECTOR_RESOLUTION_INVALID")
        signatures = {value[2:] for value in dependency_states.values()}
        require(len(signatures) == 1, "SELECTOR_RESOLUTION_INVALID")


def check_rgr_overlap_baseline(repo: Path, scope_path: Path, scope: JsonObject) -> None:
    baseline = json_from_head(repo, scope_path)
    if baseline is not None:
        base_groups = {
            item["id"]: item.get("members", [])
            for item in baseline.get("overlap_groups", [])
        }
        current_groups = {
            item["id"]: item.get("members", [])
            for item in scope.get("overlap_groups", [])
        }
        if current_groups != base_groups:
            fail("SHARED_SCOPE_MEMBER_MISSING")


def check_declared_anchor(repo: Path) -> None:
    anchor = repo / EVIDENCE_REL / "bootstrap-anchor.v1.json"
    if anchor.exists():
        try:
            candidate = json.loads(anchor.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            fail("DECLARED_NEW_EXISTS_IN_BASE", anchor.as_posix())
        if not isinstance(candidate, dict) or candidate.get("feature_id") != "F151":
            fail("DECLARED_NEW_EXISTS_IN_BASE", anchor.as_posix())


def atomic_snapshot_contract() -> dict[str, tuple[str, str, str, str, str]]:
    return {
        "atomic-namespace-before": (
            "local/atomic/S017-namespace-atomic/T017-BEFORE/atomic-namespace-before.v1.json",
            "T017",
            "BEFORE",
            "T017 BEFORE snapshot",
            "atomic-namespace-before.v1.json",
        ),
        "atomic-namespace-after": (
            "local/atomic/S017-namespace-atomic/T029-AFTER/atomic-namespace-after.v1.json",
            "T029",
            "AFTER",
            "T029 AFTER snapshot",
            "atomic-namespace-after.v1.json",
        ),
    }


def atomic_snapshot_paths(lifecycle: JsonObject, producers: JsonObject) -> set[str]:
    expected = atomic_snapshot_contract()
    rows = {
        item.get("type"): item
        for item in lifecycle.get("generated_local_types", [])
        if str(item.get("type", "")).startswith("atomic-namespace-")
    }
    require(set(rows) == set(expected), "ARTIFACT_LIFECYCLE_INVALID")
    producer_rows = {
        item.get("lifecycle_type"): item
        for item in producers.get("local_producers", [])
        if str(item.get("lifecycle_type", "")).startswith("atomic-namespace-")
    }
    require(set(producer_rows) == set(expected), "EVIDENCE_PRODUCER_SET_MISMATCH")
    for lifecycle_type, (path, task, phase, owner, name) in expected.items():
        row = rows[lifecycle_type]
        lifecycle_facts = (
            row.get("exact_relative_path"),
            row.get("task_id"),
            row.get("phase"),
            row.get("slice_id"),
            row.get("owner"),
            row.get("required_artifact_names"),
        )
        require(
            lifecycle_facts
            == (path, task, phase, "S017-namespace-atomic", owner, [name]),
            "ARTIFACT_LIFECYCLE_INVALID",
        )
        producer = producer_rows[lifecycle_type]
        producer_facts = (
            producer.get("exact_relative_path"),
            producer.get("task_id"),
            producer.get("phase"),
            producer.get("slice_id"),
            producer.get("command_owner"),
            producer.get("exact_artifact_names"),
        )
        require(producer_facts == lifecycle_facts, "EVIDENCE_PRODUCER_SET_MISMATCH")
    return {facts[0] for facts in expected.values()}


def check_lifecycle_contract(repo: Path, lifecycle: JsonObject) -> None:
    index_entry = next(
        (
            item
            for item in lifecycle.get("committed_exact_paths", [])
            if item.get("path", "").endswith("evidence-index.v2.json")
        ),
        None,
    )
    if index_entry is None or index_entry.get("first_state") != "Corrective-RED":
        fail("ARTIFACT_LIFECYCLE_INVALID")
    producers = read_json(manifest(repo, "evidence-producers.v1.json"))
    atomic_snapshot_paths(lifecycle, producers)
    required_by_type = {
        item["type"]: tuple(item["required_artifact_names"])
        for item in lifecycle["generated_local_types"]
    }
    for producer in producers.get("local_producers", []):
        lifecycle_type = producer.get("lifecycle_type")
        if tuple(producer.get("exact_artifact_names", [])) != required_by_type.get(
            lifecycle_type
        ):
            fail("EVIDENCE_PRODUCER_SET_MISMATCH")
    producer_types = {
        item.get("lifecycle_type") for item in producers.get("local_producers", [])
    }
    if set(required_by_type) != producer_types:
        fail("EVIDENCE_PRODUCER_SET_MISMATCH")
    lifecycle_paths = {
        item["path"] for item in lifecycle.get("committed_exact_paths", [])
    }
    ref_paths = {item["path"] for item in producers.get("committed_producer_refs", [])}
    if lifecycle_paths != ref_paths:
        fail("COMMITTED_PRODUCER_MISSING")
    c25 = next(
        item
        for item in lifecycle.get("committed_producer_commands", [])
        if item.get("command_id") == "C25"
    )
    if c25.get("exact_output_paths") != [
        f"{FEATURE_REL.as_posix()}/verification-report.md"
    ]:
        fail("FINALIZE_OUTPUT_SCOPE_INVALID")


def check_governance_contract(repo: Path) -> None:
    active = read_json(manifest(repo, "active-artifacts.v1.json"))
    if len(active.get("superseded", [])) != active.get("self_check", {}).get(
        "superseded_count"
    ):
        fail("SUPERSEDED_HISTORY_OWNER_MISSING")
    authority = read_json(manifest(repo, "authority-docs.v1.json"))
    if len(authority.get("documents", [])) != authority.get("self_check", {}).get(
        "document_count"
    ):
        fail("AUTHORITY_INDEX_INCOMPLETE")


def check_automatic_offline_environment(testing: str) -> None:
    expected_counts = {"C23": 1, "C24": 3, "C084": 3}
    for command_id, expected_count in expected_counts.items():
        rows = [
            line
            for line in testing.splitlines()
            if line.startswith(f"| {command_id} |")
        ]
        if (
            len(rows) != 1
            or rows[0].count(f"{FORMAL_OFFLINE_ENV_KEY}=True") != expected_count
        ):
            fail("AUTOMATIC_VERIFY_COMMAND_INVALID", f"{command_id} offline env")
    c25_rows = [line for line in testing.splitlines() if line.startswith("| C25 |")]
    if len(c25_rows) != 1 or c25_rows[0].count("--mode local-working-tree") != 1:
        fail("AUTOMATIC_VERIFY_COMMAND_INVALID", "C25 local working tree mode")


def check_stage_contract(repo: Path) -> None:
    stage = read_json(manifest(repo, "stage-command-matrix.v1.json"))
    stage_ids = [item.get("stage_task") for item in stage.get("stages", [])]
    if len(stage_ids) != len(set(stage_ids)):
        fail("STAGE_SELECTOR_NOT_AVAILABLE")
    profiles = stage.get("profiles", {})
    if (
        len(profiles.get("pre-sdk", {}).get("pythonpath_components", [])) != 9
        or len(profiles.get("post-sdk", {}).get("pythonpath_components", [])) != 8
    ):
        fail("SDK_RETIREMENT_COMMAND_INVALID")
    t121 = next(
        item for item in stage.get("stages", []) if item.get("stage_task") == "T121"
    )
    if t121.get("commands") != ["C23"]:
        fail("AUTOMATIC_VERIFY_COMMAND_INVALID")
    t122 = next(
        item for item in stage.get("stages", []) if item.get("stage_task") == "T122"
    )
    if t122.get("freshness_binding", {}).get("stage_task") != "T122":
        fail("COVERAGE_FRESHNESS_BINDING_INVALID")
    testing = (repo / INVENTORY_REL / "testing-matrix.md").read_text(encoding="utf-8")
    check_automatic_offline_environment(testing)
    if (
        "test_from_yaml_rejects_exact_retired_runtime_keys_before_model_validation"
        not in testing
    ):
        fail("STAGE_SELECTOR_SELECTED_ZERO")
    if testing.count('mkdir -p "$report_parent"') != 2:
        fail("COVERAGE_REPORT_PARENT_MISSING")


def check_tree_delete(repo: Path) -> None:
    data = read_json(manifest(repo, "tree-delete-expansion.v1.json"))
    allowed = {
        "octoagent/packages/sdk/**",
        "skills/llm-config/**",
        "octoagent/.env.litellm.example",
    }
    if {item.get("source_pattern") for item in data.get("expansions", [])} != allowed:
        fail("TREE_DELETE_MATCHER_INVALID")
    expected_fields = {"source_pattern", "pattern_kind", "owner_slice", "entries"}
    if any(set(item) != expected_fields for item in data.get("expansions", [])):
        fail("TREE_DELETE_MATCHER_INVALID")


def check_quality(repo: Path) -> None:
    check_manifest_closure(repo)
    check_rgr_manifests(repo)
    check_tree_delete(repo)
    check_lifecycle_contract(
        repo, read_json(manifest(repo, "artifact-lifecycle.v1.json"))
    )
    check_governance_contract(repo)
    check_stage_contract(repo)


def complexity_roots(repo: Path) -> list[str]:
    roots = [
        *repo.glob("octoagent/apps/*/src"),
        *repo.glob("octoagent/packages/*/src"),
    ]
    return [str(path.relative_to(repo)) for path in sorted(roots) if path.is_dir()]


def branch_complexity(repo: Path) -> dict[str, int]:
    rules = ("C901", "PLR0911", "PLR0912", "PLR0913", "PLR0915")
    counts = dict.fromkeys(rules, 0)
    roots = complexity_roots(repo)
    if not roots:
        return counts
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--output-format=json",
            "--select",
            ",".join(rules),
            *roots,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    require(
        completed.returncode in {0, 1},
        "COMPLEXITY_SCANNER_FAILED",
        completed.stderr.strip(),
    )
    try:
        diagnostics = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        fail("COMPLEXITY_SCANNER_FAILED", str(exc))
    require(isinstance(diagnostics, list), "COMPLEXITY_SCANNER_FAILED", "JSON root")
    for diagnostic in diagnostics:
        code = diagnostic.get("code") if isinstance(diagnostic, dict) else None
        if code in counts:
            counts[code] += 1
    return counts


def complexity_for_ref(repo: Path, ref: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory(prefix="f151-complexity-") as raw_temp:
        temporary = Path(raw_temp)
        process = subprocess.Popen(
            ["git", "archive", ref, "--", "octoagent"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        with tarfile.open(fileobj=process.stdout, mode="r|*") as archive:
            archive.extractall(temporary, filter="data")
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        returncode = process.wait()
        require(returncode == 0, "COMPLEXITY_SCANNER_FAILED", stderr.strip())
        return branch_complexity(temporary)


def check_complexity(repo: Path, base_ref: str, write_snapshot: bool) -> None:
    path = repo / REPOSITORY_COMPLEXITY_REL
    data = read_json(path)
    fingerprint = (
        data.get("scanner_version"),
        data.get("ruff_version"),
        data.get("ruff_config_sha256"),
    )
    if fingerprint != (
        "f151-v1",
        "0.15.4",
        "262e9b7fc5e626f42a08aa9dc6bd48cb4c1180cdac7f012e589699fe8cfedd56",
    ):
        fail("COMPLEXITY_SCANNER_FINGERPRINT_MISMATCH")
    baseline = json_from_head(repo, REPOSITORY_COMPLEXITY_REL)
    if write_snapshot and baseline is not None:
        for rule, value in data.get("total_by_rule", {}).items():
            if value > baseline.get("total_by_rule", {}).get(rule, value):
                fail("COMPLEXITY_SNAPSHOT_INCREASE_FORBIDDEN", rule)
    actual = branch_complexity(repo)
    for rule, value in actual.items():
        if value > int(data.get("total_by_rule", {}).get(rule, 0)):
            fail("COMPLEXITY_CEILING_EXCEEDED", rule)
    low_water = complexity_for_ref(repo, base_ref)
    if actual["C901"] > low_water["C901"]:
        fail("COMPLEXITY_LOW_WATER_EXCEEDED", "C901")


@dataclass(frozen=True)
class JunitFacts:
    tests: int
    failures: int
    errors: int
    skipped: int
    nodeids: tuple[str, ...]
    failing_nodeids: tuple[str, ...]
    failure_text: str
    reruns: int


@dataclass(frozen=True)
class QuarantineReport:
    """F151 rerun 与既有 quarantine 的只读归类结果。"""

    existing_reruns: tuple[tuple[str, str, str], ...]
    f151_node_count: int
    record33_release_excluded: bool


def junit_suites(path: Path) -> list[ElementTree.Element]:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        fail("PYTEST_JUNIT_AGGREGATION_INVALID", str(exc))
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = [
            item
            for item in root.iter("testsuite")
            if not list(item.iterfind("testsuite"))
        ]
    else:
        fail("PYTEST_JUNIT_AGGREGATION_INVALID", f"root={root.tag}")
    if not suites:
        fail("PYTEST_JUNIT_AGGREGATION_INVALID", "missing suite")
    return suites


def junit_totals(
    suites: list[ElementTree.Element],
) -> tuple[dict[str, int], list[ElementTree.Element], int]:
    totals = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    cases = [case for suite in suites for case in suite.findall("testcase")]
    reruns = sum(
        1 for case in cases for child in case if child.tag.lower().startswith("rerun")
    )
    actual_failures = sum(1 for case in cases if case.find("failure") is not None)
    actual_errors = sum(1 for case in cases if case.find("error") is not None)
    actual_skips = sum(1 for case in cases if case.find("skipped") is not None)
    if (
        totals["tests"] != len(cases)
        or totals["failures"] != actual_failures
        or totals["errors"] != actual_errors
        or totals["skipped"] != actual_skips
        or reruns
    ):
        fail("PYTEST_JUNIT_AGGREGATION_INVALID", "suite/testcase totals disagree")
    return totals, cases, reruns


def junit_case_facts(
    cases: list[ElementTree.Element], expected_nodeids: list[str] | None
) -> tuple[list[str], list[str], list[str]]:
    by_name = {node.rsplit("::", 1)[-1]: node for node in expected_nodeids or []}
    nodeids: list[str] = []
    failing: list[str] = []
    texts: list[str] = []
    for case in cases:
        name = case.attrib.get("name", "")
        matching = [
            nodeid
            for expected_name, nodeid in by_name.items()
            if name == expected_name or name.endswith(f" > {expected_name}")
        ]
        require(
            len(matching) <= 1,
            "EVIDENCE_SELECTOR_MISMATCH",
            f"ambiguous JUnit testcase: {name}",
        )
        nodeid = (
            matching[0]
            if matching
            else f"{case.attrib.get('classname', '')}::{name}".strip(":")
        )
        if nodeid.startswith("fixture::") or not expected_nodeids:
            nodeid = (
                f"fixture::{name}"
                if case.attrib.get("classname") == "fixture"
                else nodeid
            )
        nodeids.append(nodeid)
        failure = case.find("failure")
        if failure is not None:
            failing.append(nodeid)
            texts.append(
                (
                    failure.attrib.get("message", "")
                    + " "
                    + "".join(failure.itertext())
                ).strip()
            )
    return nodeids, failing, texts


def select_expected_junit_cases(
    cases: list[ElementTree.Element], expected_nodeids: list[str]
) -> tuple[dict[str, int], list[ElementTree.Element], int]:
    expected_names = {nodeid.rsplit("::", 1)[-1]: nodeid for nodeid in expected_nodeids}
    selected: list[ElementTree.Element] = []
    selected_nodeids: list[str] = []
    for case in cases:
        name = case.attrib.get("name", "")
        matching = [
            nodeid
            for expected_name, nodeid in expected_names.items()
            if name == expected_name or name.endswith(f" > {expected_name}")
        ]
        require(
            len(matching) <= 1,
            "VITEST_EVIDENCE_INVALID EVIDENCE_SELECTOR_MISMATCH",
            name,
        )
        if matching:
            selected.append(case)
            selected_nodeids.append(matching[0])
            continue
        require(
            case.find("skipped") is not None,
            "VITEST_EVIDENCE_INVALID EVIDENCE_SELECTOR_MISMATCH",
            f"unexpected JUnit testcase: {name}",
        )
    require(
        selected_nodeids == expected_nodeids,
        "VITEST_EVIDENCE_INVALID EVIDENCE_SELECTOR_MISMATCH",
        "selected JUnit testcase order",
    )
    totals = {
        "tests": len(selected),
        "failures": sum(case.find("failure") is not None for case in selected),
        "errors": sum(case.find("error") is not None for case in selected),
        "skipped": sum(case.find("skipped") is not None for case in selected),
    }
    reruns = sum(
        1
        for case in selected
        for child in case
        if child.tag.lower().startswith("rerun")
    )
    return totals, selected, reruns


def parse_junit(path: Path, expected_nodeids: list[str] | None = None) -> JunitFacts:
    totals, cases, reruns = junit_totals(junit_suites(path))
    if expected_nodeids:
        totals, cases, reruns = select_expected_junit_cases(cases, expected_nodeids)
    nodeids, failing, texts = junit_case_facts(cases, expected_nodeids)
    return JunitFacts(
        tests=totals["tests"],
        failures=totals["failures"],
        errors=totals["errors"],
        skipped=totals["skipped"],
        nodeids=tuple(nodeids),
        failing_nodeids=tuple(failing),
        failure_text="\n".join(texts),
        reruns=reruns,
    )


def junit_rerun_nodeids(path: Path, expected_nodeids: list[str]) -> tuple[str, ...]:
    """独立读取JUnit rerun元素，不信任index中的rerun_count。"""

    cases = [case for suite in junit_suites(path) for case in suite.findall("testcase")]
    nodeids, _, _ = junit_case_facts(cases, expected_nodeids)
    require(
        len(cases) == len(nodeids),
        "PYTEST_JUNIT_AGGREGATION_INVALID",
        "rerun testcase mapping",
    )
    return tuple(
        nodeid
        for case, nodeid in zip(cases, nodeids, strict=True)
        if any(child.tag.lower().startswith("rerun") for child in case)
    )


def quarantine_entries(manifest_value: JsonObject) -> list[JsonObject]:
    entries = manifest_value.get("quarantined")
    require(isinstance(entries, list), "QUARANTINE_MANIFEST_INVALID", "entries")
    ids: set[str] = set()
    paths: set[str] = set()
    for entry in entries:
        require(
            isinstance(entry, dict)
            and set(entry) == QUARANTINE_FIELDS
            and all(
                isinstance(entry[key], str) and entry[key].strip()
                for key in QUARANTINE_FIELDS
            ),
            "QUARANTINE_MANIFEST_INVALID",
            "entry schema",
        )
        try:
            datetime.strptime(entry["review_after"], "%Y-%m-%d")
        except ValueError:
            fail("QUARANTINE_MANIFEST_INVALID", entry["review_after"])
        require(
            entry["id"] not in ids and entry["path"] not in paths,
            "QUARANTINE_MANIFEST_INVALID",
            "duplicate id/path",
        )
        ids.add(entry["id"])
        paths.add(entry["path"])
    return entries


def quarantine_manifest_at_ref(repo: Path, ref: str) -> JsonObject:
    relative = "octoagent/tests/quarantine.json"
    raw = git(repo, "show", f"{ref}:{relative}")
    assert isinstance(raw, str)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail("QUARANTINE_MANIFEST_INVALID", f"{ref}: {exc}")
    require(isinstance(value, dict), "QUARANTINE_MANIFEST_INVALID", ref)
    return value


def validate_quarantine_no_growth(current: JsonObject, baseline: JsonObject) -> None:
    current_rows = {entry["id"]: entry for entry in quarantine_entries(current)}
    baseline_rows = {entry["id"]: entry for entry in quarantine_entries(baseline)}
    require(
        set(current_rows).issubset(baseline_rows),
        "QUARANTINE_GROWTH_FORBIDDEN",
        "new id",
    )
    require(
        all(
            entry["path"] == baseline_rows[identifier]["path"]
            for identifier, entry in current_rows.items()
        ),
        "QUARANTINE_GROWTH_FORBIDDEN",
        "path changed or expanded",
    )


def f151_record_nodeids(index: JsonObject) -> set[str]:
    records = index.get("records")
    require(isinstance(records, list), "EVIDENCE_INDEX_SCHEMA_INVALID", "records")
    return {
        str(nodeid)
        for record in records
        if isinstance(record, dict)
        for nodeid in record.get("observed_nodeids", [])
        if isinstance(nodeid, str)
    }


def quarantine_entry_for_node(
    entries: list[JsonObject], nodeid: str
) -> JsonObject | None:
    candidates = {nodeid, nodeid.removeprefix("octoagent/")}
    matches = [
        entry
        for entry in entries
        if any(
            candidate == entry["path"] or candidate.startswith(entry["path"] + "::")
            for candidate in candidates
        )
    ]
    require(len(matches) <= 1, "QUARANTINE_MANIFEST_INVALID", nodeid)
    return matches[0] if matches else None


def classify_quarantine_reruns(
    index: JsonObject,
    manifest_value: JsonObject,
    rerun_nodeids: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    f151_nodeids = f151_record_nodeids(index)
    entries = quarantine_entries(manifest_value)
    reported: list[tuple[str, str, str]] = []
    for nodeid in sorted(set(rerun_nodeids)):
        require(
            nodeid not in f151_nodeids,
            "EVIDENCE_RERUN_FORBIDDEN",
            nodeid,
        )
        entry = quarantine_entry_for_node(entries, nodeid)
        require(entry is not None, "EVIDENCE_RERUN_UNREGISTERED", nodeid)
        reported.append((entry["id"], nodeid, entry["review_after"]))
    return tuple(reported)


def record_hash(record: JsonObject) -> str:
    return canonical_sha(
        {key: value for key, value in record.items() if key != "record_sha256"}
    )


def lifecycle_run(repo: Path, record: JsonObject) -> Path:
    lifecycle_type = record.get("lifecycle_type")
    root = {
        "phase0-bootstrap": "local/bootstrap",
        "formal-rgr": "local/runs",
    }.get(lifecycle_type)
    if lifecycle_type == "corrective-red":
        root = (
            "local/corrective/T006-committed-mode-v1"
            if record.get("task_id") == "T006"
            else "local/corrective/T005-integrity-v2"
        )
    if root is None:
        fail("EVIDENCE_INDEX_SCHEMA_INVALID", "unknown lifecycle type")
    return repo / EVIDENCE_REL / root / record["slice_id"] / record["phase"]


def validate_record_files(repo: Path, record: JsonObject) -> tuple[Path, JunitFacts]:
    run = lifecycle_run(repo, record)
    actual = (
        {path.name for path in run.iterdir() if path.is_file()}
        if run.is_dir()
        else set()
    )
    expected = set(FORMAL_NAMES)
    if actual != expected:
        code = "EVIDENCE_ARTIFACT_MISSING EVIDENCE_PATH_OR_NAME_INVALID"
        fail(code, f"{record['slice_id']}/{record['phase']}: {sorted(actual)}")
    expected_paths = sorted(
        str((run / name).relative_to(repo)) for name in FORMAL_NAMES
    )
    if sorted(record["artifact_paths"]) != expected_paths:
        fail("EVIDENCE_PATH_OR_NAME_INVALID")
    for name in FORMAL_NAMES:
        path = run / name
        if (
            record["artifact_sha256"].get(name) != sha256(path)
            or record["artifact_size_bytes"].get(name) != path.stat().st_size
        ):
            fail("EVIDENCE_HASH_MISMATCH", name)
    hashes = {name: sha256(run / name) for name in FORMAL_NAMES}
    aggregate = record.get("artifact_aggregate_sha256")
    if aggregate is not None and aggregate != canonical_sha(hashes):
        fail("EVIDENCE_HASH_MISMATCH", "artifact aggregate")
    facts = parse_junit(run / "junit.xml", list(record["observed_nodeids"]))
    return run, facts


def local_evidence_path_allowed(rel: str, atomic_paths: set[str]) -> bool:
    if rel in atomic_paths:
        return True
    if rel.startswith(
        (
            "local/runs/",
            "local/bootstrap/",
            "local/corrective/T005-integrity-v2/",
            "local/corrective/T006-committed-mode-v1/",
            "local/rejected-t005-t006-v1/",
        )
    ):
        return True
    if rel.startswith("local/coverage/"):
        parts = rel.split("/")
        return len(parts) > 3 and parts[2] in {
            "T029",
            "T036",
            "T049",
            "T070",
            "T090",
            "T105",
            "T122",
        }
    return rel.startswith("local/special/C084/")


def validate_final_verification_report(
    repo: Path, report: Path, lifecycle: JsonObject
) -> None:
    report_rel = report.relative_to(repo).as_posix()
    known = next(
        (
            item
            for item in lifecycle.get("committed_exact_paths", [])
            if item.get("path") == report_rel
        ),
        None,
    )
    require(
        known is not None
        and known.get("first_writer_task") == "T124"
        and known.get("producer_command_id") == "C25",
        "ARTIFACT_LIFECYCLE_ESCAPE",
        report_rel,
    )
    tasks = (repo / FEATURE_REL / "tasks.md").read_text(encoding="utf-8")
    require("- [x] **T124" in tasks, "ARTIFACT_LIFECYCLE_ESCAPE", report_rel)
    index_path = repo / EVIDENCE_REL / "evidence-index.v2.json"
    require(index_path.is_file(), "ARTIFACT_LIFECYCLE_ESCAPE", report_rel)
    prefix = (
        "# F151 验证报告\n\n"
        f"- evidence index: `{index_path.relative_to(repo)}`\n"
        f"- evidence sha256: `{sha256(index_path)}`\n"
        "- base ref: `"
    ).encode()
    content = report.read_bytes()
    require(
        content.startswith(prefix) and content.endswith(b"`\n"),
        "ARTIFACT_LIFECYCLE_ESCAPE",
        report_rel,
    )
    base_bytes = content[len(prefix) : -2]
    require(
        bool(base_bytes) and all(0x21 <= value <= 0x7E for value in base_bytes),
        "ARTIFACT_LIFECYCLE_ESCAPE",
        report_rel,
    )
    base_ref = base_bytes.decode("ascii")
    resolve_base(repo, base_ref)
    expected = prefix + base_bytes + b"`\n"
    require(content == expected, "ARTIFACT_LIFECYCLE_ESCAPE", report_rel)


def check_unknown_evidence_paths(repo: Path) -> None:
    root = repo / EVIDENCE_REL
    lifecycle = read_json(manifest(repo, "artifact-lifecycle.v1.json"))
    producers = read_json(manifest(repo, "evidence-producers.v1.json"))
    atomic_paths = atomic_snapshot_paths(lifecycle, producers)
    interrupted = root / ".evidence-index.v2.json.amending"
    require(
        not interrupted.exists(),
        "CORRECTIVE_RED_ATOMIC_STATE_INVALID",
        str(interrupted.relative_to(repo)),
    )
    if (root / "runs").exists():
        fail("EVIDENCE_PATH_OR_NAME_INVALID", "noncanonical root")
    local = root / "local"
    if local.exists():
        for path in local.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if local_evidence_path_allowed(rel, atomic_paths):
                continue
            fail("ARTIFACT_LIFECYCLE_ESCAPE", rel)
    report = repo / FEATURE_REL / "verification-report.md"
    if report.exists():
        validate_final_verification_report(repo, report, lifecycle)


def validate_evidence_invocation(
    record: JsonObject, invocation: JsonObject, is_vitest: bool
) -> None:
    if any(arg in {"--reruns", "--reruns-delay"} for arg in invocation.get("argv", [])):
        fail("EVIDENCE_BLANKET_RERUN_FORBIDDEN EVIDENCE_SELECTOR_MISMATCH")
    if invocation.get("argv") != record.get("argv"):
        fail("VITEST_EVIDENCE_INVALID" if is_vitest else "EVIDENCE_SELECTOR_MISMATCH")


def validate_red_outcome(
    record: JsonObject, facts: JunitFacts, exit_code: int, is_vitest: bool
) -> None:
    if facts.skipped:
        fail(
            "VITEST_EVIDENCE_INVALID PYTEST_JUNIT_AGGREGATION_INVALID"
            if is_vitest
            else "EVIDENCE_SELECTED_NODE_SKIPPED PYTEST_JUNIT_AGGREGATION_INVALID"
        )
    if facts.errors:
        fail("EVIDENCE_RED_NOT_ASSERTION_FAILURE PYTEST_JUNIT_AGGREGATION_INVALID")
    if exit_code != 1:
        fail("EVIDENCE_EXIT_OR_NODE_MISMATCH EVIDENCE_RED_NOT_ASSERTION_FAILURE")
    if not facts.failures:
        fail("EVIDENCE_JUNIT_DISAGREES")
    if set(record["expected_failing_nodeids"]) != set(facts.failing_nodeids):
        fail("EVIDENCE_EXPECTED_FAILURE_MISMATCH")
    oracle = record.get("expected_oracle_id")
    if not oracle or oracle not in facts.failure_text:
        fail("EVIDENCE_ORACLE_MISMATCH")


def validate_evidence_outcome(
    record: JsonObject,
    run: Path,
    facts: JunitFacts,
    is_vitest: bool,
    *,
    require_vitest_marker: bool = False,
) -> None:
    validate_evidence_stderr(record, run)
    stdout = (run / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    if any(line.lstrip().startswith("RERUN ") for line in stdout.splitlines()):
        fail("EVIDENCE_RERUN_FORBIDDEN")
    phase = record["phase"]
    exit_code = int((run / "exit-code.txt").read_text().strip())
    marker_missing = (
        require_vitest_marker and f"vitest {phase} fixture::test_contract" not in stdout
    )
    if is_vitest and (
        marker_missing or facts.skipped or facts.failures > (1 if phase == "RED" else 0)
    ):
        fail("VITEST_EVIDENCE_INVALID")
    if phase == "RED":
        validate_red_outcome(record, facts, exit_code, is_vitest)
    elif exit_code != 0 or facts.failures or facts.errors or facts.skipped:
        fail("EVIDENCE_JUNIT_DISAGREES")


def validate_evidence_stderr(record: JsonObject, run: Path) -> None:
    stderr = (run / "stderr.txt").read_text(encoding="utf-8", errors="replace")
    if not stderr:
        return
    oracle = record.get("expected_oracle_id")
    expected_names = [
        str(nodeid).rsplit("::", 1)[-1]
        for nodeid in record.get("expected_failing_nodeids", [])
    ]
    require(
        record.get("producer_id") == "formal-vitest-rgr"
        and record.get("phase") == "RED"
        and isinstance(oracle, str)
        and oracle in stderr
        and "AssertionError:" in stderr
        and bool(expected_names)
        and all(name in stderr for name in expected_names),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        "formal stderr",
    )


def validate_legacy_index(repo: Path, index: JsonObject) -> JsonObject:
    records = index.get("records")
    require(isinstance(records, list), "EVIDENCE_INDEX_SCHEMA_INVALID")
    phases_by_slice: dict[str, list[str]] = {}
    for record in records:
        require(isinstance(record, dict), "EVIDENCE_INDEX_SCHEMA_INVALID")
        require(LEGACY_RECORD_FIELDS.issubset(record), "EVIDENCE_INDEX_SCHEMA_INVALID")
        require(
            not record.get("record_sha256")
            or record["record_sha256"] == record_hash(record),
            "EVIDENCE_HASH_MISMATCH",
            "record",
        )
        phases_by_slice.setdefault(record.get("slice_id", ""), []).append(
            record.get("phase", "")
        )
        run, facts = validate_record_files(repo, record)
        invocation, tree = (
            read_json(run / "invocation.json"),
            read_json(run / "tree.json"),
        )
        is_vitest = "vitest" in record.get("producer_id", "")
        validate_evidence_invocation(record, invocation, is_vitest)
        validate_evidence_outcome(
            record, run, facts, is_vitest, require_vitest_marker=True
        )
        require(
            record.get("cwd") == invocation.get("cwd"), "EVIDENCE_EXIT_OR_NODE_MISMATCH"
        )
        base = tree.get("base_sha", tree.get("merge_base_sha"))
        require(record.get("base_sha") == base, "EVIDENCE_EXIT_OR_NODE_MISMATCH")
        require(record.get("task_id") != "T999", "ARTIFACT_LIFECYCLE_ESCAPE")
    for phases in phases_by_slice.values():
        order = [phase for phase in ("RED", "GREEN", "REFACTOR") if phase in phases]
        require(phases == order, "EVIDENCE_RGR_ORDER_INVALID")
    require(
        bool(records)
        or not any(path.startswith("octoagent/") for path in changed_paths(repo)),
        "EVIDENCE_REQUIRED_SLICE_MISSING",
    )
    chain = index.get("chain_head_sha256")
    require(
        chain is None or bool(records) and chain == records[-1].get("record_sha256"),
        "EVIDENCE_HASH_MISMATCH",
        "legacy chain",
    )
    return index


def resolve_base(repo: Path, base_ref: str) -> str:
    if not base_ref:
        fail("EVIDENCE_BASE_REF_INVALID")
    return str(git(repo, "rev-parse", "--verify", f"{base_ref}^{{commit}}")).strip()


def validate_v2_invocation(record: JsonObject, invocation: JsonObject) -> None:
    require(
        set(invocation) == INVOCATION_FIELDS,
        "EVIDENCE_INDEX_SCHEMA_INVALID",
        "invocation",
    )
    pairs = {
        "slice_id": "slice_id",
        "phase": "phase",
        "argv": "argv",
        "cwd": "cwd",
        "env": "env",
        "started_utc": "started_utc",
        "finished_utc": "finished_utc",
        "exit_code": "exit_code",
    }
    require(
        not any(invocation[left] != record[right] for left, right in pairs.items()),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        "invocation cross-field",
    )
    selected = (
        formal_vitest_nodeids(invocation["argv"])
        if "vitest" in record["producer_id"]
        else [arg for arg in invocation["argv"] if "::" in arg]
    )
    require(
        selected == record["observed_nodeids"] and invocation["exact_command"],
        "EVIDENCE_SELECTOR_MISMATCH",
    )


def validate_v2_counts(record: JsonObject, facts: JunitFacts) -> None:
    passed = facts.tests - facts.failures - facts.errors - facts.skipped
    expected = (
        facts.tests,
        passed,
        facts.failures,
        facts.errors,
        facts.skipped,
        facts.reruns,
    )
    keys = (
        "selected_count",
        "passed_count",
        "failed_count",
        "error_count",
        "skipped_count",
        "rerun_count",
    )
    require(
        tuple(record[key] for key in keys) == expected,
        "EVIDENCE_JUNIT_DISAGREES",
        "record counts",
    )
    require(
        list(facts.nodeids) == record["observed_nodeids"],
        "EVIDENCE_JUNIT_DISAGREES",
        "observed nodeids",
    )


def validate_v2_record(repo: Path, record: JsonObject) -> Path:
    require(
        set(record) == V2_RECORD_FIELDS,
        "EVIDENCE_INDEX_SCHEMA_INVALID",
        "record fields",
    )
    require(
        record_hash(record) == record["record_sha256"],
        "EVIDENCE_HASH_MISMATCH",
        "record",
    )
    run, facts = validate_record_files(repo, record)
    invocation = read_json(run / "invocation.json")
    tree = read_json(run / "tree.json")
    validate_v2_invocation(record, invocation)
    require(set(tree) == TREE_FIELDS, "EVIDENCE_INDEX_SCHEMA_INVALID", "tree")
    require(
        not any(
            tree[left] != record[right] for left, right in TREE_RECORD_FIELDS.items()
        ),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        "tree cross-field",
    )
    validate_v2_counts(record, facts)
    is_vitest = "vitest" in record["producer_id"]
    validate_evidence_invocation(record, invocation, is_vitest)
    validate_evidence_outcome(record, run, facts, is_vitest)
    return run


def fixed_append_identities() -> tuple[tuple[str, str, str, str], ...]:
    formal = [
        ("formal-rgr", "T005" if phase == "GREEN" else "T006", slice_id, phase)
        for phase in ("GREEN", "REFACTOR")
        for slice_id in FORMAL_SLICE_ORDER
    ]
    return tuple([*formal, *T006_AMENDMENT_TAIL])


def fixed_record_identities(
    anchor: JsonObject,
) -> tuple[tuple[str, str, str, str], ...]:
    bootstrap = [
        ("phase0-bootstrap", BOOTSTRAP_TASKS[item["slice_id"]], item["slice_id"], "RED")
        for item in anchor["artifacts"]
    ]
    corrective = [
        ("corrective-red", "T005", "S004-evidence-checker", "RED"),
        ("corrective-red", "T005", "S002-manifest-integrity", "RED"),
    ]
    return tuple([*bootstrap, *corrective, *fixed_append_identities()])


def manifest_known_identities(contracts: dict[str, JsonObject]) -> set[tuple[str, ...]]:
    return {
        (str(task_id), slice_id, phase)
        for slice_id, contract in contracts.items()
        for phase, task_id in contract.get("tasks", {}).items()
    }


def manifest_record_candidates(
    contracts: dict[str, JsonObject], completed: set[tuple[str, ...]]
) -> set[tuple[str, ...]]:
    require(isinstance(completed, set), "EVIDENCE_RGR_ORDER_INVALID", "completed set")
    known = manifest_known_identities(contracts)
    fixed_exceptions = {
        ("T005", "S004-evidence-checker", "RED"),
        ("T005", "S002-manifest-integrity", "RED"),
    }
    phases_by_slice: dict[str, set[str]] = {}
    for identity in completed:
        require(
            isinstance(identity, tuple) and len(identity) == 3,
            "EVIDENCE_RGR_ORDER_INVALID",
            "formal identity shape",
        )
        if identity in fixed_exceptions:
            continue
        require(identity in known, "EVIDENCE_RGR_ORDER_INVALID", str(identity))
        phases_by_slice.setdefault(str(identity[1]), set()).add(str(identity[2]))
    candidates: set[tuple[str, ...]] = set()
    phases = ("RED", "GREEN", "REFACTOR")
    for slice_id, contract in contracts.items():
        tasks = contract.get("tasks", {})
        if not tasks:
            continue
        present = phases_by_slice.get(slice_id, set())
        require(
            present == set(phases[: len(present)]),
            "EVIDENCE_RGR_ORDER_INVALID",
            f"{slice_id} phase prefix",
        )
        if len(present) < len(phases):
            phase = phases[len(present)]
            candidates.add(("formal-rgr", str(tasks[phase]), slice_id, phase))
    if not candidates:
        return set()
    minimum = min(task_number(str(item[1])) for item in candidates)
    return {item for item in candidates if task_number(str(item[1])) == minimum}


def validate_record_order(
    index: JsonObject, anchor: JsonObject, contracts: dict[str, JsonObject]
) -> None:
    records = index["records"]
    identities = [
        tuple(record[key] for key in ("lifecycle_type", "task_id", "slice_id", "phase"))
        for record in records
    ]
    fixed = fixed_record_identities(anchor)
    fixed_count = min(len(identities), len(fixed))
    require(
        len(records) >= len(BOOTSTRAP_SLICES) + 2
        and tuple(identities[:fixed_count]) == fixed[:fixed_count],
        "EVIDENCE_RGR_ORDER_INVALID",
        "fixed prefix",
    )
    completed = {tuple(identity[1:]) for identity in fixed}
    for identity in identities[len(fixed) :]:
        require(
            identity in manifest_record_candidates(contracts, completed),
            "EVIDENCE_RGR_ORDER_INVALID",
            "manifest frontier",
        )
        completed.add(tuple(identity[1:]))
    require(
        index["created_utc"] == records[len(BOOTSTRAP_SLICES) + 1]["finished_utc"],
        "EVIDENCE_INDEX_SCHEMA_INVALID",
        "created_utc",
    )


def validate_record_chain(index: JsonObject) -> None:
    previous = canonical_sha(
        {
            "feature_id": index["feature_id"],
            "bootstrap_anchor_sha256": index["bootstrap_anchor_sha256"],
            "base_sha": index["base_sha"],
        }
    )
    seen: set[str] = set()
    for record in index["records"]:
        require(
            record["previous_record_sha256"] == previous,
            "EVIDENCE_HASH_MISMATCH",
            "previous record",
        )
        current = record["record_sha256"]
        require(current not in seen, "EVIDENCE_HASH_MISMATCH", "duplicate record")
        seen.add(current)
        previous = current
    require(
        index["chain_head_sha256"] == previous, "EVIDENCE_HASH_MISMATCH", "chain head"
    )


def t011_attestation_rows(
    lifecycle: JsonObject, producers: JsonObject
) -> tuple[JsonObject, JsonObject, JsonObject, JsonObject]:
    superseded = lifecycle.get("superseded_contract_evidence")
    require(isinstance(superseded, dict), "RECORD33_RELEASE_EXCLUSION_INVALID")
    release = superseded.get("release_replacement")
    require(isinstance(release, dict), "RECORD33_RELEASE_EXCLUSION_INVALID")
    rows = producers.get("main_owned_direct_corrective_protocols")
    require(isinstance(rows, list), "RECORD33_RELEASE_EXCLUSION_INVALID")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("protocol_id") == "t011-preliminary-direct-corrective-red"
    ]
    require(len(matches) == 1, "RECORD33_RELEASE_EXCLUSION_INVALID")
    binding = release.get("artifact_binding")
    require(isinstance(binding, dict), "RECORD33_RELEASE_EXCLUSION_INVALID")
    return superseded, release, binding, matches[0]


def validate_t011_attestation(
    release: JsonObject, binding: JsonObject, producer: JsonObject
) -> None:
    required = tuple(producer.get("required_acceptance_fields", ()))
    expected_fields = (
        "artifact_sha256",
        "artifact_size_bytes",
        "artifact_aggregate_sha256",
        "test_sha256",
        "main_review_message_id",
        "main_attestation_binding_sha256",
    )
    require(required == expected_fields, "RECORD33_RELEASE_EXCLUSION_INVALID")
    accepted = producer.get("accepted_binding")
    require(
        isinstance(accepted, dict)
        and accepted == {key: binding.get(key) for key in expected_fields},
        "RECORD33_RELEASE_EXCLUSION_INVALID",
        "producer binding",
    )
    hashes = binding["artifact_sha256"]
    sizes = binding["artifact_size_bytes"]
    require(
        isinstance(hashes, dict)
        and isinstance(sizes, dict)
        and set(hashes) == set(sizes) == set(FORMAL_NAMES)
        and canonical_sha(hashes) == binding["artifact_aggregate_sha256"],
        "RECORD33_RELEASE_EXCLUSION_INVALID",
        "artifact map",
    )
    payload = {
        "protocol_id": release["protocol_id"],
        "exact_root": release["exact_root"],
        **{key: binding[key] for key in expected_fields[:-1]},
    }
    require(
        canonical_sha(payload) == binding["main_attestation_binding_sha256"],
        "RECORD33_RELEASE_EXCLUSION_INVALID",
        "attestation hash",
    )


def validate_record33_release_exclusion(
    index: JsonObject, lifecycle: JsonObject, producers: JsonObject
) -> None:
    superseded, release, binding, producer = t011_attestation_rows(lifecycle, producers)
    records = index.get("records")
    position = superseded.get("record_position")
    require(
        isinstance(records, list) and position == 33 and len(records) >= position,
        "RECORD33_RELEASE_EXCLUSION_INVALID",
        "record position",
    )
    record = records[position - 1]
    identity = [
        record[key] for key in ("lifecycle_type", "task_id", "slice_id", "phase")
    ]
    root = str(superseded.get("artifact_root"))
    require(
        superseded.get("status") == "VALID_BYTES_FOR_SUPERSEDED_CONTRACT"
        and superseded.get("chain_required") is True
        and superseded.get("release_eligible") is False
        and identity == superseded.get("record_identity")
        and record_hash(record) == record.get("record_sha256")
        and record.get("record_sha256") == superseded.get("record_sha256")
        and record.get("artifact_aggregate_sha256")
        == superseded.get("artifact_aggregate_sha256")
        and set(record.get("artifact_paths", ()))
        == {f"{root}/{name}" for name in FORMAL_NAMES},
        "RECORD33_RELEASE_EXCLUSION_INVALID",
        "record33",
    )
    validate_record_chain(index)
    validate_t011_attestation(release, binding, producer)
    require(
        release.get("canonical_index_adoption") is False
        and producer.get("canonical_index_adoption") is False
        and producer.get("current_state") == "SUPERSEDED_HISTORY_NOT_RELEASE_EVIDENCE",
        "RECORD33_RELEASE_EXCLUSION_INVALID",
        "release state",
    )


def collect_index_reruns(repo: Path, index: JsonObject) -> tuple[str, ...]:
    reruns: list[str] = []
    for record in index["records"]:
        run = lifecycle_run(repo, record)
        reruns.extend(
            junit_rerun_nodeids(run / "junit.xml", list(record["observed_nodeids"]))
        )
    return tuple(reruns)


def build_quarantine_report(
    repo: Path, index: JsonObject, base_ref: str
) -> QuarantineReport:
    current = read_json(repo / "octoagent/tests/quarantine.json")
    baseline = quarantine_manifest_at_ref(repo, base_ref)
    validate_quarantine_no_growth(current, baseline)
    lifecycle = read_json(manifest(repo, "artifact-lifecycle.v1.json"))
    producers = read_json(manifest(repo, "evidence-producers.v1.json"))
    validate_record33_release_exclusion(index, lifecycle, producers)
    existing = classify_quarantine_reruns(
        index,
        current,
        collect_index_reruns(repo, index),
    )
    return QuarantineReport(
        existing_reruns=existing,
        f151_node_count=len(f151_record_nodeids(index)),
        record33_release_excluded=True,
    )


def actual_v2_run_dirs(repo: Path, records: list[JsonObject]) -> set[str]:
    roots = [
        repo / EVIDENCE_REL / "local/bootstrap",
        repo / EVIDENCE_REL / "local/corrective/T005-integrity-v2",
        repo / EVIDENCE_REL / "local/runs",
    ]
    if any(
        record.get("lifecycle_type") == "corrective-red"
        and record.get("task_id") == "T006"
        for record in records
    ):
        roots.append(repo / EVIDENCE_REL / "local/corrective/T006-committed-mode-v1")
    result: set[str] = set()
    for root in roots:
        if root.exists():
            result.update(
                str(path.relative_to(repo))
                for path in root.rglob("*")
                if path.is_dir() and any(child.is_file() for child in path.iterdir())
            )
    return result


def validate_run_bijection(
    repo: Path,
    records: list[JsonObject],
    indexed: set[str],
    permitted_unindexed_run: Path | None,
) -> None:
    actual = actual_v2_run_dirs(repo, records)
    if permitted_unindexed_run is not None:
        relative = permitted_unindexed_run.relative_to(repo).as_posix()
        prefix = f"{EVIDENCE_REL.as_posix()}/local/runs/"
        require(relative.startswith(prefix), "EVIDENCE_PATH_OR_NAME_INVALID")
        if permitted_unindexed_run.is_dir() and any(
            child.is_file() for child in permitted_unindexed_run.iterdir()
        ):
            indexed.add(relative)
    require(
        indexed == actual,
        "EVIDENCE_PATH_OR_NAME_INVALID",
        "run/index bijection",
    )


def validate_recovery_binding(index: JsonObject, anchor: JsonObject) -> None:
    recovery = index["recovery"]
    require(
        set(recovery) == RECOVERY_FIELDS, "EVIDENCE_INDEX_SCHEMA_INVALID", "recovery"
    )
    review_id = recovery["main_review_message_id"]
    binding = canonical_sha(
        {
            "main_review_message_id": review_id,
            "corrective_red_aggregate_sha256": recovery[
                "corrective_red_aggregate_sha256"
            ],
        }
    )
    require(
        binding == recovery["approval_binding_sha256"]
        and review_id.startswith("main-f151-t005-corrective-red-review-")
        and review_id != anchor["main_review_message_id"]
        and not review_id.startswith("main-f151-t006-"),
        "EVIDENCE_INDEX_SCHEMA_INVALID",
        "approval binding",
    )


def required_through_identities(
    contracts: dict[str, JsonObject], requested: int
) -> set[tuple[str, ...]]:
    return {
        (str(task_id), slice_id, phase)
        for slice_id, contract in contracts.items()
        for phase, task_id in contract.get("tasks", {}).items()
        if task_number(str(task_id)) <= requested
    }


def validate_through_task(
    records: list[JsonObject],
    through_task: str | None,
    contracts: dict[str, JsonObject],
) -> None:
    if through_task is None:
        return
    match = re.fullmatch(r"T(\d{3})", through_task)
    if match is None or not 1 <= int(match.group(1)) <= 124:
        fail("EVIDENCE_THROUGH_TASK_INVALID", str(through_task))
    requested = int(match.group(1))
    task_numbers: list[int] = []
    completed: set[tuple[str, ...]] = set()
    for record in records:
        task_id = str(record.get("task_id", ""))
        task_match = re.fullmatch(r"T(\d{3})", task_id)
        if task_match is None:
            fail("EVIDENCE_THROUGH_TASK_INVALID", task_id)
        task_numbers.append(int(task_match.group(1)))
        completed.add((task_id, str(record["slice_id"]), str(record["phase"])))
    require(
        not any(task > requested for task in task_numbers),
        "EVIDENCE_THROUGH_TASK_INVALID",
        "frontier exceeds task",
    )
    require(
        required_through_identities(contracts, requested).issubset(completed),
        "EVIDENCE_THROUGH_TASK_INVALID",
        "required identities incomplete",
    )
    if through_task == "T006":
        identities = [
            tuple(
                record[key]
                for key in ("lifecycle_type", "task_id", "slice_id", "phase")
            )
            for record in records[-len(T006_AMENDMENT_TAIL) :]
        ]
        require(
            identities == list(T006_AMENDMENT_TAIL),
            "EVIDENCE_THROUGH_TASK_INVALID",
            "T006 amendment incomplete",
        )


def validate_committed_worktree(repo: Path) -> None:
    _, files, statuses = fingerprint(repo)
    require(
        not files and not statuses,
        "EVIDENCE_COMMITTED_WORKTREE_DIRTY",
        "; ".join(statuses),
    )


def validate_indexed_formal_records(
    repo: Path,
    index: JsonObject,
    anchor: JsonObject,
    contracts: dict[str, JsonObject],
) -> None:
    fixed_count = len(fixed_record_identities(anchor))
    for ordinal, record in enumerate(index["records"]):
        validate_v2_record(repo, record)
        if record["lifecycle_type"] == "formal-rgr":
            validate_indexed_formal_invocation(
                repo,
                record,
                contracts,
                historic=ordinal < fixed_count,
            )


def validate_v2_index(
    repo: Path,
    index: JsonObject,
    contracts: dict[str, JsonObject],
    *,
    mode: str,
    base_ref: str,
    through_task: str | None,
    permitted_unindexed_run: Path | None = None,
) -> JsonObject:
    require(
        set(index) == V2_INDEX_FIELDS and index.get("schema_version") == 2,
        "EVIDENCE_INDEX_SCHEMA_INVALID",
        "top-level",
    )
    require(
        index.get("feature_id") == "F151" and bool(index.get("records")),
        "EVIDENCE_INDEX_SCHEMA_INVALID",
        "feature/records",
    )
    anchor_path = repo / index["bootstrap_anchor_path"]
    anchor, _ = load_anchor(repo, anchor_path, index["bootstrap_anchor_sha256"])
    if mode == "committed":
        validate_committed_worktree(repo)
    elif mode != "local-working-tree":
        fail("EVIDENCE_MODE_INVALID", mode)
    resolved = resolve_base(repo, base_ref)
    merge_base = str(git(repo, "merge-base", "HEAD", resolved)).strip()
    require(
        merge_base == index["base_sha"] == anchor["base_sha"],
        "EVIDENCE_BASE_REF_INVALID",
        base_ref,
    )
    validate_recovery_binding(index, anchor)
    validate_record_order(index, anchor, contracts)
    validate_record_chain(index)
    validate_indexed_formal_records(repo, index, anchor, contracts)
    if len(index["records"]) >= 33:
        build_quarantine_report(repo, index, merge_base)
    indexed = {
        str(lifecycle_run(repo, record).relative_to(repo))
        for record in index["records"]
    }
    validate_run_bijection(repo, index["records"], indexed, permitted_unindexed_run)
    validate_through_task(index["records"], through_task, contracts)
    if mode == "local-working-tree":
        check_changed_scope(repo)
    return index


def validate_evidence_index(
    repo: Path,
    index_path: Path,
    contracts: dict[str, JsonObject],
    *,
    mode: str = "local-working-tree",
    base_ref: str = "HEAD",
    through_task: str | None = None,
    permitted_unindexed_run: Path | None = None,
) -> JsonObject:
    check_unknown_evidence_paths(repo)
    index = read_json(index_path)
    if index.get("schema_version") == 2:
        return validate_v2_index(
            repo,
            index,
            contracts,
            mode=mode,
            base_ref=base_ref,
            through_task=through_task,
            permitted_unindexed_run=permitted_unindexed_run,
        )
    if index.get("version") == 1:
        resolve_base(repo, base_ref)
        if mode == "local-working-tree":
            check_changed_scope(repo)
        return validate_legacy_index(repo, index)
    fail("EVIDENCE_INDEX_SCHEMA_INVALID", "unknown version")


def load_anchor(
    repo: Path, anchor_path: Path, expected_sha: str
) -> tuple[JsonObject, list[JsonObject]]:
    anchors = list((repo / EVIDENCE_REL).glob("bootstrap-anchor*.json"))
    require(
        len(anchors) == 1
        and anchor_path.is_file()
        and bool(re.fullmatch(r"[0-9a-f]{64}", expected_sha)),
        "BOOTSTRAP_ANCHOR_INVALID",
    )
    require(
        sha256(anchor_path) == expected_sha,
        "BOOTSTRAP_ANCHOR_INVALID",
        "sha256 mismatch",
    )
    anchor = read_json(anchor_path)
    require(
        set(anchor) == ANCHOR_FIELDS
        and anchor.get("feature_id") == "F151"
        and anchor.get("version") == 1,
        "BOOTSTRAP_ANCHOR_INVALID",
        "schema",
    )
    artifacts = anchor.get("artifacts")
    artifact_slices = (
        [item.get("slice_id") for item in artifacts]
        if isinstance(artifacts, list)
        else []
    )
    require(
        set(artifact_slices) == set(BOOTSTRAP_SLICES)
        and len(artifact_slices) == len(set(artifact_slices)),
        "BOOTSTRAP_ANCHOR_INVALID",
        "slice set/order",
    )
    return anchor, artifacts


def validate_anchor_files(
    repo: Path, item: JsonObject
) -> tuple[Path, JsonObject, JsonObject]:
    require(
        set(item) == ANCHOR_ARTIFACT_FIELDS and item.get("phase") == "RED",
        "BOOTSTRAP_ANCHOR_INVALID",
        "artifact schema",
    )
    run = repo / item["relative_path"]
    actual_names = (
        {path.name for path in run.iterdir() if path.is_file()}
        if run.is_dir()
        else set()
    )
    require(
        actual_names == set(FORMAL_NAMES), "BOOTSTRAP_ANCHOR_MIXED_RUN", "artifact set"
    )
    hashes = {name: sha256(run / name) for name in FORMAL_NAMES}
    require(
        canonical_sha(hashes) == item["sha256"]
        and sum((run / name).stat().st_size for name in FORMAL_NAMES)
        == item["size_bytes"],
        "BOOTSTRAP_ANCHOR_MIXED_RUN",
        "aggregate/size",
    )
    individual = {
        "invocation.json": "argv_sha256",
        "tree.json": "tree_sha256",
        "junit.xml": "junit_sha256",
        "stdout.txt": "stdout_sha256",
        "stderr.txt": "stderr_sha256",
    }
    require(
        not any(hashes[name] != item[field] for name, field in individual.items()),
        "BOOTSTRAP_ANCHOR_MIXED_RUN",
        "artifact hash",
    )
    invocation = read_json(run / "invocation.json")
    tree = read_json(run / "tree.json")
    require(
        set(invocation) == INVOCATION_FIELDS and set(tree) == TREE_FIELDS,
        "BOOTSTRAP_ANCHOR_INVALID",
        "run schema",
    )
    return run, invocation, tree


def validate_anchor_run(
    repo: Path,
    anchor: JsonObject,
    rows: dict[str, JsonObject],
    item: JsonObject,
) -> tuple[JsonObject, str, str]:
    run, invocation, tree = validate_anchor_files(repo, item)
    slice_id = item["slice_id"]
    nodeids = rows[slice_id]["nodeids"]
    selected_argv = [arg for arg in invocation["argv"] if "::" in arg]
    require(
        selected_argv == nodeids
        and invocation["slice_id"] == slice_id
        and tree["slice_id"] == slice_id,
        "BOOTSTRAP_ANCHOR_MIXED_RUN",
        "argv/slice",
    )
    require(
        tree["merge_base_sha"] == anchor["base_sha"]
        and tree["worktree_fingerprint"] == anchor["worktree_fingerprint"],
        "BOOTSTRAP_ANCHOR_MIXED_RUN",
        "base/fingerprint",
    )
    facts = parse_junit(run / "junit.xml", nodeids)
    oracle = rows[slice_id]["oracle"]
    require(
        facts.tests == len(nodeids) == facts.failures
        and not facts.errors
        and not facts.skipped
        and set(facts.failing_nodeids) == set(nodeids)
        and oracle in facts.failure_text,
        "BOOTSTRAP_ANCHOR_MIXED_RUN",
        "JUnit",
    )
    exit_code = int((run / "exit-code.txt").read_text().strip())
    require(
        exit_code == item["exit_code"] == invocation["exit_code"] == 1
        and not (run / "stderr.txt").read_bytes(),
        "BOOTSTRAP_ANCHOR_MIXED_RUN",
        "exit/stderr",
    )
    record = make_bootstrap_record(
        repo, run, item, invocation, tree, facts, oracle, nodeids
    )
    return record, tree["head_sha"], tree["head_tree_sha"]


def validate_anchor(
    repo: Path, anchor_path: Path, expected_sha: str
) -> tuple[JsonObject, list[JsonObject]]:
    anchor, artifacts = load_anchor(repo, anchor_path, expected_sha)
    rows = parse_rgr(repo)
    records: list[JsonObject] = []
    heads: set[str] = set()
    trees: set[str] = set()
    for item in artifacts:
        record, head_sha, tree_sha = validate_anchor_run(repo, anchor, rows, item)
        records.append(record)
        heads.add(head_sha)
        trees.add(tree_sha)
    require(
        len(heads) == len(trees) == 1, "BOOTSTRAP_ANCHOR_MIXED_RUN", "mixed head/tree"
    )
    return anchor, records


def make_bootstrap_record(
    repo: Path,
    run: Path,
    item: JsonObject,
    invocation: JsonObject,
    tree: JsonObject,
    facts: JunitFacts,
    oracle: str,
    nodeids: list[str],
) -> JsonObject:
    return make_v2_record(
        repo,
        run,
        invocation,
        tree,
        facts,
        lifecycle_type="phase0-bootstrap",
        producer_id="phase0-pytest-bootstrap",
        task_id=BOOTSTRAP_TASKS[item["slice_id"]],
        oracle=oracle,
        failing_nodeids=nodeids,
    )


def make_v2_record(
    repo: Path,
    run: Path,
    invocation: JsonObject,
    tree: JsonObject,
    facts: JunitFacts,
    *,
    lifecycle_type: str,
    producer_id: str,
    task_id: str,
    oracle: str | None,
    failing_nodeids: list[str],
) -> JsonObject:
    hashes = {name: sha256(run / name) for name in FORMAL_NAMES}
    passed = facts.tests - facts.failures - facts.errors - facts.skipped
    record = {key: invocation[key] for key in INVOCATION_RECORD_FIELDS}
    record.update(
        {target: tree[source] for source, target in TREE_RECORD_FIELDS.items()}
    )
    record.update(
        {key: getattr(facts, attr) for key, attr in FACT_RECORD_FIELDS.items()}
    )
    record.update(
        {
            "producer_id": producer_id,
            "lifecycle_type": lifecycle_type,
            "task_id": task_id,
            "mode": "local-working-tree",
            "expected_oracle_id": oracle,
            "expected_failing_nodeids": failing_nodeids,
            "observed_nodeids": list(facts.nodeids),
            "passed_count": passed,
            "artifact_paths": sorted(
                str((run / name).relative_to(repo)) for name in FORMAL_NAMES
            ),
            "artifact_sha256": hashes,
            "artifact_size_bytes": {
                name: (run / name).stat().st_size for name in FORMAL_NAMES
            },
            "artifact_aggregate_sha256": canonical_sha(hashes),
            "previous_record_sha256": "0" * 64,
            "record_sha256": "0" * 64,
        }
    )
    record["record_sha256"] = record_hash(record)
    return record


def atomic_write_json(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_durable(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        remaining = memoryview(data)
        while remaining:
            written = os.write(descriptor, remaining)
            require(
                written > 0,
                "CORRECTIVE_RED_ATOMIC_STATE_INVALID",
                "durable write made no progress",
            )
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)


def bootstrap_index(repo: Path, anchor_path: Path, expected_sha: str) -> Path:
    _, records = validate_anchor(repo, anchor_path, expected_sha)
    index_path = repo / EVIDENCE_REL / "evidence-index.v1.json"
    if index_path.exists():
        existing = read_json(index_path)
        if existing.get("bootstrap_anchor_sha256") == expected_sha:
            existing_records = existing.get("records")
            valid = isinstance(existing_records, list) and len(existing_records) == len(
                BOOTSTRAP_SLICES
            )
            valid = valid and existing.get("chain_head_sha256") == existing_records[
                -1
            ].get("record_sha256")
            valid = valid and not any(
                record_hash(record) != record.get("record_sha256")
                for record in existing_records
            )
            require(valid, "BOOTSTRAP_ANCHOR_INVALID", "corrupted existing index")
            return index_path
        tracked = (
            subprocess.run(
                [
                    "git",
                    "ls-files",
                    "--error-unmatch",
                    str(index_path.relative_to(repo)),
                ],
                cwd=repo,
                capture_output=True,
            ).returncode
            == 0
        )
        require(
            tracked,
            "BOOTSTRAP_ANCHOR_INVALID",
            "existing index bound to another anchor",
        )
    index = {
        "version": 1,
        "feature_id": "F151",
        "bootstrap_anchor_path": str(anchor_path.relative_to(repo)),
        "bootstrap_anchor_sha256": expected_sha,
        "created_utc": records[-1]["finished_utc"],
        "records": records,
        "chain_head_sha256": records[-1]["record_sha256"],
    }
    atomic_write_json(index_path, index)
    return index_path


def phase_task(contract: JsonObject, phase: str) -> str:
    task = contract.get("tasks", {}).get(phase)
    if not task:
        fail("RGR_TASK_MAPPING_MISSING", phase)
    return str(task)


def formal_cwd(repo: Path, protocol: str) -> Path:
    return repo / "octoagent/frontend" if "frontend" in protocol else repo


def formal_vitest_nodeids(argv: list[str]) -> list[str]:
    require(
        len(argv) == 11
        and argv[:5] == ["npm", "exec", "vitest", "--", "run"]
        and argv[6:10] == ["-t", argv[7], "--reporter=default", "--reporter=junit"]
        and argv[10].startswith("--outputFile.junit="),
        "VITEST_EVIDENCE_INVALID",
        "formal argv",
    )
    return [f"{argv[5]}::{argv[7]}"]


def formal_command(
    repo: Path, run: Path, nodeids: list[str], protocol: str = ""
) -> tuple[list[str], str]:
    if "frontend" in protocol:
        require(len(nodeids) == 1 and "::" in nodeids[0], "RGR_SELECTOR_INVALID")
        test_file, test_name = nodeids[0].split("::", 1)
        junit = os.path.relpath(run / "junit.xml", formal_cwd(repo, protocol))
        argv = [
            "npm",
            "exec",
            "vitest",
            "--",
            "run",
            test_file,
            "-t",
            test_name,
            "--reporter=default",
            "--reporter=junit",
            f"--outputFile.junit={Path(junit).as_posix()}",
        ]
    else:
        junit_relative = (run / "junit.xml").relative_to(repo)
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
            f"--junitxml={junit_relative.as_posix()}",
        ]
    components = read_json(manifest(repo, "stage-command-matrix.v1.json"))["profiles"][
        "pre-sdk"
    ]["pythonpath_components"]
    return argv, ":".join(str(repo / item) for item in components)


def formal_environment(pythonpath: str, *, historic: bool = False) -> JsonObject:
    environment = {"PYTHONNOUSERSITE": "1", "PYTHONPATH": pythonpath}
    if not historic:
        environment[FORMAL_OFFLINE_ENV_KEY] = "True"
    return environment


def formal_exact_command(environment: JsonObject, argv: list[str]) -> str:
    assignments = [
        f"{key}={environment[key]}" for key in FORMAL_ENV_ORDER if key in environment
    ]
    return shlex.join(["env", *assignments, *argv])


def validate_formal_environment(
    invocation: JsonObject,
    argv: list[str],
    pythonpath: str,
    *,
    historic: bool,
) -> None:
    expected = formal_environment(pythonpath, historic=historic)
    require(
        invocation.get("env") == expected
        and invocation.get("exact_command") == formal_exact_command(expected, argv),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        "formal environment",
    )


def formal_metadata(
    repo: Path,
    slice_id: str,
    phase: str,
    base_ref: str,
    contract: JsonObject,
    argv: list[str],
    pythonpath: str,
    started: str,
    finished: str,
    exit_code: int,
    cwd: Path | None = None,
) -> tuple[JsonObject, JsonObject]:
    worktree, files, statuses = fingerprint(repo)
    environment = formal_environment(pythonpath)
    invocation = {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "task_scope": phase_task(contract, phase),
        "argv": argv,
        "cwd": str(cwd or repo),
        "env": environment,
        "exact_command": formal_exact_command(environment, argv),
        "started_utc": started,
        "finished_utc": finished,
        "exit_code": exit_code,
    }
    tree = {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "base_ref": base_ref,
        "merge_base_sha": str(git(repo, "merge-base", "HEAD", base_ref)).strip(),
        "head_sha": str(git(repo, "rev-parse", "HEAD")).strip(),
        "head_tree_sha": str(git(repo, "rev-parse", "HEAD^{tree}")).strip(),
        "worktree_fingerprint": worktree,
        "fingerprint_scope": "committed/staged/unstaged/untracked final files excluding evidence outputs",
        "fingerprint_files": files,
        "status_porcelain": statuses,
        "captured_utc": finished,
    }
    return invocation, tree


def write_formal_artifacts(
    run: Path,
    completed: subprocess.CompletedProcess[bytes],
    invocation: JsonObject,
    tree: JsonObject,
) -> None:
    (run / "stdout.txt").write_bytes(completed.stdout)
    (run / "stderr.txt").write_bytes(completed.stderr)
    (run / "exit-code.txt").write_text(f"{completed.returncode}\n", encoding="utf-8")
    atomic_write_json(run / "invocation.json", invocation)
    atomic_write_json(run / "tree.json", tree)


def formal_record_in_index(
    index: JsonObject, slice_id: str, phase: str
) -> JsonObject | None:
    records = index.get("records")
    if not isinstance(records, list):
        return None
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("lifecycle_type") == "formal-rgr"
        and record.get("slice_id") == slice_id
        and record.get("phase") == phase
    ]
    require(len(matches) <= 1, "EVIDENCE_RGR_ORDER_INVALID", "duplicate formal run")
    return matches[0] if matches else None


def validate_formal_invocation(
    repo: Path,
    invocation: JsonObject,
    contract: JsonObject,
    slice_id: str,
    phase: str,
    argv: list[str],
    pythonpath: str,
    cwd: Path,
    *,
    historic: bool = False,
) -> None:
    expected = {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "task_scope": phase_task(contract, phase),
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": 1 if phase == "RED" else 0,
    }
    require(
        set(invocation) == INVOCATION_FIELDS
        and not any(invocation[key] != value for key, value in expected.items()),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        "formal invocation",
    )
    validate_formal_environment(
        invocation,
        argv,
        pythonpath,
        historic=historic,
    )


def validate_indexed_formal_invocation(
    repo: Path,
    record: JsonObject,
    contracts: dict[str, JsonObject],
    *,
    historic: bool,
) -> None:
    run = lifecycle_run(repo, record)
    invocation = read_json(run / "invocation.json")
    if historic:
        pythonpath = record.get("env", {}).get("PYTHONPATH")
        require(
            isinstance(pythonpath, str) and bool(pythonpath),
            "EVIDENCE_EXIT_OR_NODE_MISMATCH",
            "historic formal pythonpath",
        )
        argv = record["argv"]
        cwd = repo
    else:
        contract = contracts.get(str(record["slice_id"]))
        require(isinstance(contract, dict), "RGR_SELECTOR_INVALID")
        protocol = str(contract["protocol"])
        argv, pythonpath = formal_command(
            repo, run, record["observed_nodeids"], protocol
        )
        cwd = formal_cwd(repo, protocol)
        require(argv == record["argv"], "EVIDENCE_SELECTOR_MISMATCH")
    require(record["cwd"] == str(cwd), "EVIDENCE_EXIT_OR_NODE_MISMATCH")
    validate_formal_environment(
        invocation,
        argv,
        pythonpath,
        historic=historic,
    )


def validate_formal_tree(
    repo: Path,
    tree: JsonObject,
    invocation: JsonObject,
    slice_id: str,
    phase: str,
    base_ref: str,
) -> None:
    worktree, files, statuses = fingerprint(repo)
    resolved = resolve_base(repo, base_ref)
    expected = {
        "version": 1,
        "slice_id": slice_id,
        "phase": phase,
        "base_ref": base_ref,
        "merge_base_sha": str(git(repo, "merge-base", "HEAD", resolved)).strip(),
        "head_sha": str(git(repo, "rev-parse", "HEAD")).strip(),
        "head_tree_sha": str(git(repo, "rev-parse", "HEAD^{tree}")).strip(),
        "worktree_fingerprint": worktree,
        "fingerprint_scope": "committed/staged/unstaged/untracked final files excluding evidence outputs",
        "fingerprint_files": files,
        "status_porcelain": statuses,
        "captured_utc": invocation["finished_utc"],
    }
    require(
        set(tree) == TREE_FIELDS
        and not any(tree[key] != value for key, value in expected.items()),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        "formal tree",
    )


def validated_formal_facts(run: Path, nodeids: list[str]) -> JunitFacts:
    facts = parse_junit(run / "junit.xml", nodeids)
    require(
        list(facts.nodeids) == nodeids and facts.tests == len(nodeids),
        "EVIDENCE_EXIT_OR_NODE_MISMATCH",
        str(run),
    )
    return facts


def formal_record_from_run(
    repo: Path,
    run: Path,
    slice_id: str,
    phase: str,
    base_ref: str,
    contract: JsonObject,
    nodeids: list[str],
    *,
    historic_indexed: bool = False,
) -> JsonObject:
    actual = {path.name for path in run.iterdir()} if run.is_dir() else set()
    require(actual == set(FORMAL_NAMES), "EVIDENCE_ARTIFACT_MISSING")
    protocol = str(contract["protocol"])
    cwd = formal_cwd(repo, protocol)
    argv, pythonpath = formal_command(repo, run, nodeids, protocol)
    invocation = read_json(run / "invocation.json")
    tree = read_json(run / "tree.json")
    validate_formal_invocation(
        repo,
        invocation,
        contract,
        slice_id,
        phase,
        argv,
        pythonpath,
        cwd,
        historic=historic_indexed,
    )
    validate_formal_tree(repo, tree, invocation, slice_id, phase, base_ref)
    facts = validated_formal_facts(run, nodeids)
    is_red = phase == "RED"
    record = make_v2_record(
        repo,
        run,
        invocation,
        tree,
        facts,
        lifecycle_type="formal-rgr",
        producer_id=(
            "formal-vitest-rgr" if "frontend" in protocol else "formal-python-rgr"
        ),
        task_id=phase_task(contract, phase),
        oracle=contract["oracle"] if is_red else None,
        failing_nodeids=nodeids if is_red else [],
    )
    validate_v2_record(repo, record)
    return record


def next_record_candidates(
    index: JsonObject, contracts: dict[str, JsonObject]
) -> set[tuple[str, ...]]:
    fixed = fixed_append_identities()
    offset = len(index["records"]) - len(BOOTSTRAP_SLICES) - 2
    require(offset >= 0, "EVIDENCE_RGR_ORDER_INVALID", "fixed prefix incomplete")
    if offset < len(fixed):
        return {fixed[offset]}
    completed = {
        (str(record["task_id"]), str(record["slice_id"]), str(record["phase"]))
        for record in index["records"]
    }
    return manifest_record_candidates(contracts, completed)


def validate_next_formal_record(
    index: JsonObject,
    slice_id: str,
    phase: str,
    contract: JsonObject,
    contracts: dict[str, JsonObject],
) -> None:
    expected = ("formal-rgr", phase_task(contract, phase), slice_id, phase)
    require(
        expected in next_record_candidates(index, contracts),
        "EVIDENCE_RGR_ORDER_INVALID",
        "formal next frontier",
    )


def execute_formal_run(
    repo: Path,
    run: Path,
    slice_id: str,
    phase: str,
    base_ref: str,
    contract: JsonObject,
    nodeids: list[str],
) -> None:
    run.mkdir(parents=True)
    protocol = str(contract["protocol"])
    cwd = formal_cwd(repo, protocol)
    argv, pythonpath = formal_command(repo, run, nodeids, protocol)
    environment = {**os.environ, **formal_environment(pythonpath)}
    started = utc_now()
    completed = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True)
    finished = utc_now()
    invocation, tree = formal_metadata(
        repo,
        slice_id,
        phase,
        base_ref,
        contract,
        argv,
        pythonpath,
        started,
        finished,
        completed.returncode,
        cwd,
    )
    write_formal_artifacts(run, completed, invocation, tree)


def match_indexed_formal_record(indexed: JsonObject, observed: JsonObject) -> None:
    candidate = json.loads(json.dumps(observed, ensure_ascii=False))
    candidate["previous_record_sha256"] = indexed["previous_record_sha256"]
    candidate["record_sha256"] = record_hash(candidate)
    require(
        candidate == indexed,
        "EVIDENCE_HASH_MISMATCH",
        "indexed formal run mismatch",
    )


def is_historic_indexed_formal(index: JsonObject, record: JsonObject) -> bool:
    fixed_count = len(BOOTSTRAP_SLICES) + 2 + len(fixed_append_identities())
    return any(item is record for item in index["records"][:fixed_count])


def run_formal(
    repo: Path, slice_id: str, phase: str, base_ref: str, index_path: Path
) -> None:
    rows = parse_rgr(repo)
    if slice_id not in rows or phase not in {"RED", "GREEN", "REFACTOR"}:
        fail("RGR_SELECTOR_INVALID")
    run = repo / EVIDENCE_REL / "local/runs" / slice_id / phase
    raw_index = read_json(index_path)
    indexed = formal_record_in_index(raw_index, slice_id, phase)
    permitted = run if run.exists() and indexed is None else None
    index = validate_evidence_index(
        repo,
        index_path,
        rows,
        base_ref=base_ref,
        permitted_unindexed_run=permitted,
    )
    require(index.get("schema_version") == 2, "EVIDENCE_INDEX_SCHEMA_INVALID")
    indexed = formal_record_in_index(index, slice_id, phase)
    contract = rows[slice_id]
    nodeids = contract["nodeids"]
    if indexed is None:
        validate_next_formal_record(index, slice_id, phase, contract, rows)
    if not run.exists():
        execute_formal_run(repo, run, slice_id, phase, base_ref, contract, nodeids)
    observed = formal_record_from_run(
        repo,
        run,
        slice_id,
        phase,
        base_ref,
        contract,
        nodeids,
        historic_indexed=(
            indexed is not None and is_historic_indexed_formal(index, indexed)
        ),
    )
    if indexed is not None:
        match_indexed_formal_record(indexed, observed)
        return
    append_formal_record(repo, index_path, index, observed, rows)


def append_formal_record(
    repo: Path,
    index_path: Path,
    index: JsonObject,
    record: JsonObject,
    contracts: dict[str, JsonObject],
) -> None:
    candidate = json.loads(json.dumps(index, ensure_ascii=False))
    record["previous_record_sha256"] = candidate["chain_head_sha256"]
    record["record_sha256"] = record_hash(record)
    candidate["records"].append(record)
    candidate["chain_head_sha256"] = record["record_sha256"]
    validate_v2_index(
        repo,
        candidate,
        contracts,
        mode="local-working-tree",
        base_ref=record["base_ref"],
        through_task=None,
    )
    atomic_write_json(index_path, candidate)


def t006_contracts(repo: Path) -> dict[str, JsonObject]:
    lifecycle = read_json(manifest(repo, "artifact-lifecycle.v1.json"))
    corrective = next(
        item
        for item in lifecycle["generated_local_types"]
        if item["type"] == "corrective-red"
    )
    contracts = {
        item["slice_id"]: item for item in corrective["amendment_transactions"]
    }
    require(
        tuple(contracts) == T006_RED_SLICES,
        "CORRECTIVE_RED_CONTRACT_INVALID",
        "slice order",
    )
    return contracts


def validate_t006_root(repo: Path, supplied: Path) -> Path:
    expected = repo / EVIDENCE_REL / "local/corrective/T006-committed-mode-v1"
    root = supplied if supplied.is_absolute() else repo / supplied
    require(root.resolve() == expected.resolve(), "CORRECTIVE_RED_PATH_INVALID")
    expected_files = {
        f"{slice_id}/RED/{name}"
        for slice_id in T006_RED_SLICES
        for name in FORMAL_NAMES
    }
    actual_files = (
        {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
        if root.is_dir()
        else set()
    )
    require(
        actual_files == expected_files,
        "CORRECTIVE_RED_PATH_INVALID",
        "exact two-run artifact set",
    )
    return root


def t006_review_sources(repo: Path, index_path: Path) -> list[Path]:
    roots = (
        repo / EVIDENCE_REL / "local/bootstrap",
        repo / EVIDENCE_REL / "local/corrective/T005-integrity-v2",
        repo / EVIDENCE_REL / "local/rejected-t005-t006-v1",
        repo / EVIDENCE_REL / "local/runs",
    )
    paths = [index_path, repo / EVIDENCE_REL / "bootstrap-anchor.v1.json"]
    for root in roots:
        paths.extend(path for path in root.rglob("*") if path.is_file())
    return paths


def validate_t006_review_id(
    repo: Path, index_path: Path, review_id: str, *, fresh: bool
) -> None:
    require(
        review_id.startswith(T006_REVIEW_PREFIX)
        and len(review_id) > len(T006_REVIEW_PREFIX),
        "CORRECTIVE_RED_REVIEW_ID_INVALID",
    )
    if fresh:
        needle = review_id.encode("utf-8")
        require(
            all(
                needle not in path.read_bytes()
                for path in t006_review_sources(repo, index_path)
            ),
            "CORRECTIVE_RED_REVIEW_ID_INVALID",
            "review id already used",
        )


def t006_corrective_records(
    repo: Path, root: Path, approval_binding: str
) -> tuple[list[JsonObject], str]:
    records: list[JsonObject] = []
    aggregates: dict[str, str] = {}
    for slice_id, contract in t006_contracts(repo).items():
        run = root / slice_id / "RED"
        invocation = read_json(run / "invocation.json")
        tree = read_json(run / "tree.json")
        nodeids = contract["exact_nodeids"]
        facts = parse_junit(run / "junit.xml", nodeids)
        record = make_v2_record(
            repo,
            run,
            invocation,
            tree,
            facts,
            lifecycle_type="corrective-red",
            producer_id=f"t006-corrective-red-pytest@{approval_binding}",
            task_id="T006",
            oracle=contract["expected_oracle_id"],
            failing_nodeids=nodeids,
        )
        validate_v2_record(repo, record)
        records.append(record)
        aggregates[slice_id] = record["artifact_aggregate_sha256"]
    return records, canonical_sha(aggregates)


def link_t006_records(index: JsonObject, records: list[JsonObject]) -> JsonObject:
    candidate = json.loads(json.dumps(index, ensure_ascii=False))
    candidate["records"] = candidate["records"][:20]
    previous = candidate["records"][-1]["record_sha256"]
    for source in records:
        record = json.loads(json.dumps(source, ensure_ascii=False))
        record["previous_record_sha256"] = previous
        record["record_sha256"] = record_hash(record)
        candidate["records"].append(record)
        previous = record["record_sha256"]
    candidate["chain_head_sha256"] = previous
    return candidate


def durable_replace_json(path: Path, value: JsonObject) -> None:
    temporary = path.with_name(path.name + ".amending")
    require(not temporary.exists(), "CORRECTIVE_RED_ATOMIC_STATE_INVALID")
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    write_durable(temporary, encoded)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def adopt_t006_corrective(repo: Path, args: argparse.Namespace) -> None:
    index_path = args.evidence_index.resolve()
    canonical_index = (repo / EVIDENCE_REL / "evidence-index.v2.json").resolve()
    require(index_path == canonical_index, "CORRECTIVE_RED_PATH_INVALID", "index")
    contracts = parse_rgr(repo)
    index = validate_evidence_index(
        repo, index_path, contracts, mode=args.mode, base_ref=args.base_ref
    )
    require(20 <= len(index["records"]) <= 26, "EVIDENCE_RGR_ORDER_INVALID")
    require(len(index["records"]) != 21, "CORRECTIVE_RED_PARTIAL_STATE")
    root = validate_t006_root(repo, args.adopt_corrective_red_root)
    combined = args.corrective_red_aggregate_sha256
    require(
        bool(re.fullmatch(r"[0-9a-f]{64}", combined)),
        "CORRECTIVE_RED_AGGREGATE_INVALID",
    )
    fresh = len(index["records"]) == 20
    validate_t006_review_id(repo, index_path, args.main_review_message_id, fresh=fresh)
    binding = canonical_sha(
        {
            "main_review_message_id": args.main_review_message_id,
            "corrective_red_combined_aggregate_sha256": combined,
        }
    )
    records, actual_combined = t006_corrective_records(repo, root, binding)
    require(actual_combined == combined, "CORRECTIVE_RED_AGGREGATE_INVALID")
    candidate = link_t006_records(index, records)
    if not fresh:
        require(
            index["records"][:22] == candidate["records"],
            "CORRECTIVE_RED_REENTRY_INVALID",
        )
        return
    validate_v2_index(
        repo,
        candidate,
        contracts,
        mode=args.mode,
        base_ref=args.base_ref,
        through_task=None,
    )
    durable_replace_json(index_path, candidate)


def corrective_records(repo: Path, root: Path) -> tuple[list[JsonObject], str]:
    lifecycle = read_json(manifest(repo, "artifact-lifecycle.v1.json"))
    row = next(
        item
        for item in lifecycle["generated_local_types"]
        if item["type"] == "corrective-red"
    )
    contracts = {item["slice_id"]: item for item in row["transactions"]}
    records: list[JsonObject] = []
    aggregates: dict[str, str] = {}
    for slice_id in ("S004-evidence-checker", "S002-manifest-integrity"):
        run = root / slice_id / "RED"
        contract = contracts[slice_id]
        invocation, tree = (
            read_json(run / "invocation.json"),
            read_json(run / "tree.json"),
        )
        facts = parse_junit(run / "junit.xml", contract["exact_nodeids"])
        record = make_v2_record(
            repo,
            run,
            invocation,
            tree,
            facts,
            lifecycle_type="corrective-red",
            producer_id="t005-corrective-red-pytest",
            task_id="T005",
            oracle=contract["expected_oracle_id"],
            failing_nodeids=contract["exact_nodeids"],
        )
        validate_v2_record(repo, record)
        records.append(record)
        aggregates[slice_id] = record["artifact_aggregate_sha256"]
    return records, canonical_sha(aggregates)


def link_records(index: JsonObject) -> None:
    previous = canonical_sha(
        {
            "feature_id": index["feature_id"],
            "bootstrap_anchor_sha256": index["bootstrap_anchor_sha256"],
            "base_sha": index["base_sha"],
        }
    )
    for record in index["records"]:
        record["previous_record_sha256"] = previous
        record["record_sha256"] = record_hash(record)
        previous = record["record_sha256"]
    index["chain_head_sha256"] = previous


def recovery_paths(repo: Path) -> dict[str, Path]:
    evidence = repo / EVIDENCE_REL
    quarantine = evidence / "local/rejected-t005-t006-v1"
    return {
        "source_index": evidence / "evidence-index.v1.json",
        "source_runs": evidence / "local/runs",
        "quarantine": quarantine,
        "quarantine_index": quarantine / "evidence-index.v1.json",
        "temp": evidence / ".evidence-index.v2.json.recovering",
        "final": evidence / "evidence-index.v2.json",
    }


def recovery_state(paths: dict[str, Path], expected: bytes) -> str:
    present = {name for name, path in paths.items() if path.exists()}
    fixed = {
        frozenset(("source_index", "source_runs")): "R0",
        frozenset(("source_index", "quarantine")): "R1",
        frozenset(("quarantine", "quarantine_index")): "R2",
    }
    if frozenset(present) in fixed:
        return fixed[frozenset(present)]
    durable = {"quarantine", "quarantine_index"}
    if present == durable | {"temp"}:
        return "R3" if paths["temp"].read_bytes() == expected else "R2_PARTIAL"
    if present == durable | {"final"} and paths["final"].read_bytes() == expected:
        return "R4"
    return "INVALID"


def review_id_valid(
    review_id: str, anchor: JsonObject, index: Path, runs: Path
) -> bool:
    if not review_id.startswith("main-f151-t005-corrective-red-review-"):
        return False
    if review_id == anchor["main_review_message_id"] or review_id.startswith(
        "main-f151-t006-"
    ):
        return False
    needle = review_id.encode()
    files = [index, *(path for path in runs.rglob("*") if path.is_file())]
    return all(needle not in path.read_bytes() for path in files)


def validate_rejected_runs(index: JsonObject, root: Path) -> None:
    records = [
        row
        for row in index.get("records", [])
        if row.get("lifecycle_type") == "formal-rgr"
    ]
    expected: set[Path] = set()
    for record in records:
        run = root / record["slice_id"] / record["phase"]
        expected.add(run)
        actual = {path.name for path in run.iterdir()} if run.is_dir() else set()
        if actual != set(FORMAL_NAMES):
            fail("RECOVERY_SOURCE_INVALID", str(run))
        for name in FORMAL_NAMES:
            path = run / name
            if sha256(path) != record["artifact_sha256"].get(name):
                fail("RECOVERY_SOURCE_INVALID", name)
            if path.stat().st_size != record["artifact_size_bytes"].get(name):
                fail("RECOVERY_SOURCE_INVALID", name)
    actual = {
        path
        for path in root.rglob("*")
        if path.is_dir() and any(child.is_file() for child in path.iterdir())
    }
    if actual != expected:
        fail("RECOVERY_SOURCE_INVALID", "rejected run set")


def build_recovered_index(
    repo: Path,
    anchor_path: Path,
    anchor_sha: str,
    corrective_root: Path,
    corrective_sha: str,
    rejected_sha: str,
    review_id: str,
) -> tuple[JsonObject, bytes]:
    anchor, bootstrap = validate_anchor(repo, anchor_path, anchor_sha)
    corrective, actual_corrective = corrective_records(repo, corrective_root)
    if actual_corrective != corrective_sha:
        fail("CORRECTIVE_RED_AGGREGATE_INVALID")
    rejected_run_sha = read_json(manifest(repo, "artifact-lifecycle.v1.json"))[
        "rejected_evidence"
    ]["current_run_aggregate_sha256"]
    binding = canonical_sha(
        {
            "main_review_message_id": review_id,
            "corrective_red_aggregate_sha256": corrective_sha,
        }
    )
    recovery = {
        "rejected_index_sha256": rejected_sha,
        "rejected_run_aggregate_sha256": rejected_run_sha,
        "quarantine_root": str(
            (repo / EVIDENCE_REL / "local/rejected-t005-t006-v1").relative_to(repo)
        ),
        "corrective_red_aggregate_sha256": corrective_sha,
        "main_review_message_id": review_id,
        "approval_binding_sha256": binding,
    }
    index = {
        "schema_version": 2,
        "feature_id": "F151",
        "bootstrap_anchor_path": str(anchor_path.relative_to(repo)),
        "bootstrap_anchor_sha256": anchor_sha,
        "base_sha": anchor["base_sha"],
        "created_utc": corrective[-1]["finished_utc"],
        "recovery": recovery,
        "records": bootstrap + corrective,
        "chain_head_sha256": "0" * 64,
    }
    link_records(index)
    encoded = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return index, encoded.encode("utf-8")


def validate_recovery_sources(
    paths: dict[str, Path], rejected_sha: str
) -> tuple[Path, Path]:
    index = (
        paths["source_index"]
        if paths["source_index"].exists()
        else paths["quarantine_index"]
    )
    runs = (
        paths["source_runs"] if paths["source_runs"].exists() else paths["quarantine"]
    )
    if not index.is_file() or not runs.is_dir() or sha256(index) != rejected_sha:
        fail("RECOVERY_SOURCE_INVALID")
    validate_rejected_runs(read_json(index), runs)
    return index, runs


def advance_recovery(paths: dict[str, Path], expected: bytes) -> None:
    while True:
        state = recovery_state(paths, expected)
        if state == "R0":
            paths["source_runs"].rename(paths["quarantine"])
            fsync_directory(paths["quarantine"].parent)
        elif state == "R1":
            paths["source_index"].rename(paths["quarantine_index"])
            fsync_directory(paths["source_index"].parent)
        elif state == "R2_PARTIAL":
            paths["temp"].unlink()
            fsync_directory(paths["temp"].parent)
        elif state == "R2":
            write_durable(paths["temp"], expected)
        elif state == "R3":
            os.replace(paths["temp"], paths["final"])
            fsync_directory(paths["final"].parent)
        elif state == "R4":
            return
        else:
            fail("RECOVERY_STATE_INVALID")


def recover_index(repo: Path, args: argparse.Namespace) -> JsonObject:
    paths = recovery_paths(repo)
    if (
        args.rejected_index.resolve() != paths["source_index"]
        or args.output.resolve() != paths["final"]
    ):
        fail("RECOVERY_PATH_INVALID")
    source_index, source_runs = validate_recovery_sources(
        paths, args.rejected_index_sha256
    )
    anchor, _ = load_anchor(
        repo, args.bootstrap_anchor_file.resolve(), args.bootstrap_anchor_sha256
    )
    if not review_id_valid(
        args.main_review_message_id, anchor, source_index, source_runs
    ):
        fail("RECOVERY_REVIEW_ID_INVALID")
    index, expected = build_recovered_index(
        repo,
        args.bootstrap_anchor_file.resolve(),
        args.bootstrap_anchor_sha256,
        args.corrective_red_root.resolve(),
        args.corrective_red_aggregate_sha256,
        args.rejected_index_sha256,
        args.main_review_message_id,
    )
    if recovery_state(paths, expected) == "INVALID":
        fail("RECOVERY_STATE_INVALID")
    advance_recovery(paths, expected)
    validate_v2_index(
        repo,
        index,
        parse_rgr(repo),
        mode="committed",
        base_ref="HEAD",
        through_task=None,
    )
    return index


def run_all(repo: Path, base_ref: str) -> None:
    resolve_base(repo, base_ref)
    check_import_direction(repo)
    check_retired_terms(repo)
    check_quality(repo)
    check_complexity(repo, base_ref, False)


def finalize_verification(
    repo: Path, mode: str, base_ref: str, index_path: Path, output: Path
) -> None:
    if output != repo / FEATURE_REL / "verification-report.md":
        fail("FINALIZE_OUTPUT_SCOPE_INVALID")
    validate_evidence_index(
        repo, index_path, parse_rgr(repo), mode=mode, base_ref=base_ref
    )
    tasks = (repo / FEATURE_REL / "tasks.md").read_text(encoding="utf-8")
    for task_id in ("T120", "T121", "T122", "T123"):
        if f"- [x] **{task_id}" not in tasks:
            fail("FINALIZE_PREREQUISITE_MISSING", task_id)
    content = (
        "# F151 验证报告\n\n"
        f"- evidence index: `{index_path.relative_to(repo)}`\n"
        f"- evidence sha256: `{sha256(index_path)}`\n"
        f"- base ref: `{base_ref}`\n"
    )
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, output)


def configure_evidence_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
    repo_parent: argparse.ArgumentParser,
) -> None:
    evidence = sub.add_parser("tdd-evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    bootstrap = evidence_sub.add_parser("verify-bootstrap", parents=[repo_parent])
    bootstrap.add_argument("--bootstrap-anchor-file", type=Path, required=True)
    bootstrap.add_argument("--bootstrap-anchor-sha256", required=True)
    verify = evidence_sub.add_parser("verify", parents=[repo_parent])
    verify.add_argument(
        "--mode", choices=["local-working-tree", "committed"], required=True
    )
    verify.add_argument("--base-ref", required=True)
    verify.add_argument("--evidence-index", type=Path, required=True)
    verify.add_argument("--through-task")
    run = evidence_sub.add_parser("run", parents=[repo_parent])
    run.add_argument("--slice", required=True)
    run.add_argument("--phase", choices=["RED", "GREEN", "REFACTOR"], required=True)
    run.add_argument("--mode", choices=["local-working-tree"], required=True)
    run.add_argument("--base-ref", required=True)
    run.add_argument("--evidence-index", type=Path, required=True)
    run.add_argument("--adopt-corrective-red-root", type=Path)
    run.add_argument("--corrective-red-aggregate-sha256")
    run.add_argument("--main-review-message-id")
    recover = evidence_sub.add_parser("recover-index", parents=[repo_parent])
    for name in (
        "bootstrap-anchor-file",
        "rejected-index",
        "corrective-red-root",
        "output",
    ):
        recover.add_argument(f"--{name}", type=Path, required=True)
    for name in (
        "bootstrap-anchor-sha256",
        "rejected-index-sha256",
        "corrective-red-aggregate-sha256",
        "main-review-message-id",
    ):
        recover.add_argument(f"--{name}", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    repo_parent = argparse.ArgumentParser(add_help=False)
    repo_parent.add_argument("--repo-root", type=Path, default=Path.cwd())
    for name in ("import-direction", "retired-terms", "quality-smells"):
        sub.add_parser(name, parents=[repo_parent])
    all_gate = sub.add_parser("all", parents=[repo_parent])
    all_gate.add_argument("--base-ref", required=True)
    finalize = sub.add_parser("finalize-verification", parents=[repo_parent])
    finalize.add_argument(
        "--mode", choices=["local-working-tree", "committed"], required=True
    )
    finalize.add_argument("--base-ref", required=True)
    finalize.add_argument("--evidence-index", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    complexity = sub.add_parser("complexity", parents=[repo_parent])
    complexity.add_argument("--base-ref", default="origin/master")
    complexity.add_argument("--write-snapshot", action="store_true")
    configure_evidence_parser(sub, repo_parent)
    return parser


def dispatch_evidence_run(repo: Path, args: argparse.Namespace) -> None:
    adoption = (
        args.adopt_corrective_red_root,
        args.corrective_red_aggregate_sha256,
        args.main_review_message_id,
    )
    if any(value is not None for value in adoption):
        require(
            all(value is not None for value in adoption)
            and args.slice == "S006-index-amendment-integrity"
            and args.phase == "RED",
            "CORRECTIVE_RED_ARGUMENT_INVALID",
        )
        adopt_t006_corrective(repo, args)
        return
    run_formal(
        repo,
        args.slice,
        args.phase,
        args.base_ref,
        args.evidence_index.resolve(),
    )


def dispatch_evidence(repo: Path, args: argparse.Namespace) -> None:
    if args.evidence_command == "verify-bootstrap":
        index = bootstrap_index(
            repo, args.bootstrap_anchor_file.resolve(), args.bootstrap_anchor_sha256
        )
        result = {
            "status": "PASS",
            "evidence_index": str(index),
            "anchor_sha256": args.bootstrap_anchor_sha256,
        }
        print(json.dumps(result))
    elif args.evidence_command == "verify":
        contracts = parse_rgr(repo)
        validate_evidence_index(
            repo,
            args.evidence_index.resolve(),
            contracts,
            mode=args.mode,
            base_ref=args.base_ref,
            through_task=args.through_task,
        )
    elif args.evidence_command == "run":
        dispatch_evidence_run(repo, args)
    elif args.evidence_command == "recover-index":
        recovered = recover_index(repo, args)
        result = {
            "status": "PASS",
            "evidence_index": str(args.output),
            "records": len(recovered["records"]),
            "chain_head_sha256": recovered["chain_head_sha256"],
            "approval_binding_sha256": recovered["recovery"]["approval_binding_sha256"],
        }
        print(json.dumps(result))


def main() -> int:
    args = build_parser().parse_args()
    repo = args.repo_root.resolve()
    if args.command == "import-direction":
        check_import_direction(repo)
    elif args.command == "retired-terms":
        check_retired_terms(repo)
    elif args.command == "quality-smells":
        check_quality(repo)
    elif args.command == "complexity":
        check_complexity(repo, args.base_ref, args.write_snapshot)
    elif args.command == "all":
        run_all(repo, args.base_ref)
    elif args.command == "finalize-verification":
        finalize_verification(
            repo,
            args.mode,
            args.base_ref,
            args.evidence_index.resolve(),
            args.output.resolve(),
        )
    else:
        dispatch_evidence(repo, args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateFailure as exc:
        print(f"{exc.code}: {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from None
