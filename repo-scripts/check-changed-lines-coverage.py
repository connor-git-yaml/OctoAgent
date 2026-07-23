#!/usr/bin/env python3
"""F141 件4：changed-lines coverage 门（stdlib-only，CI backend job 调用）。

机械规则（cc-haha coverage.ts ``evaluateChangedLineCoverage`` 范式，spec D5v2）：
1. ``git diff --unified=0 --no-ext-diff <base>...HEAD`` 解析 hunk 头，取**新增行号**；
2. 范围过滤：只计生产 python 源码
   ``octoagent/packages/*/src/**/*.py`` + ``octoagent/apps/gateway/src/**/*.py``
   （测试 / repo-scripts / frontend / 文档天然不计——存量不背债，脚本改动不背 coverage 债）；
3. lcov（pytest-cov ``--cov-report=lcov`` 产物，``relative_files=true`` + config source）
   的 ``SF:`` 路径是相对 ``octoagent/`` 的 ``packages/.../src/...``——补 ``octoagent/``
   前缀后与 git 路径对齐；``DA:<line>,<hits>`` 即可执行行集合与命中集合；
4. 判定：新增行 ∩ 可执行行 的覆盖率 < ``--min-percent``（默认 90）→ exit 1，并列出
   未覆盖 文件:行号；**范围内新文件在 lcov 无记录 → 该文件新增行全按可执行 0 覆盖计**
   （抓「加了模块没有任何测试 import」）；非可执行行（空行/注释/pragma 排除行）不计；
   新增可执行行数 = 0 → PASS（docs/测试/脚本改动天然过门）；
5. escape hatch：仅 committed 模式读取 HEAD ``[cov-exempt]``。legacy 保持 exit 0；完整
   C19 contract 写 ``EXEMPT``（不得冒充 ``PASS``），local-working-tree 不继承豁免。

exit code：0 = 过门/exempt/无新增可执行行；1 = 覆盖率不足或输入损坏；2 = 参数错误。

用法（CI）：
    python3 repo-scripts/check-changed-lines-coverage.py \
        --lcov octoagent/coverage.lcov --base <sha> [--min-percent 90]
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXEMPT_MARKER = "[cov-exempt]"

# 生产源码范围（相对 repo root）
_SCOPE_RE = re.compile(r"^octoagent/(?:packages/[^/]+/src/.+|apps/gateway/src/.+)\.py$")

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

_UTC_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z$")

_FULL_ARGUMENTS = (
    "mode",
    "fresh_after_utc",
    "expected_stage_task",
    "expected_head_sha",
    "expected_head_tree",
    "expected_worktree_fingerprint",
    "report_json",
)

_ATOMIC_SNAPSHOT_KEYS = frozenset(
    {
        "approved_hash_exceptions",
        "base_sha",
        "base_source_files",
        "before_artifact_sha256",
        "c15_pre_verdict",
        "captured_utc",
        "deletions",
        "expected_counts",
        "feature_id",
        "fingerprint_files",
        "fingerprint_scope",
        "head_sha",
        "head_tree_sha",
        "lifecycle_type",
        "manifest_path",
        "manifest_sha256",
        "phase",
        "projection_pairs",
        "provider_dx_import_occurrence_count",
        "slice_id",
        "source_absence_count",
        "target_files",
        "target_presence_count",
        "task_id",
        "version",
        "worktree_fingerprint",
    }
)
_SNAPSHOT_FILE_KEYS = frozenset(
    {
        "bucket",
        "content_b64",
        "normalized_ast_sha256",
        "sha256",
        "size_bytes",
        "source",
        "target",
    }
)
_PROJECTION_KEYS = frozenset(
    {
        "base_source_normalized_ast_sha256",
        "base_source_sha256",
        "source",
        "target",
        "target_normalized_ast_sha256",
        "target_sha256",
    }
)
_DELETION_KEYS = frozenset(
    {"absent", "base_source_normalized_ast_sha256", "base_source_sha256", "source"}
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


def _git_bytes(repo_root: Path, args: list[str]) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        check=True,
    )
    return result.stdout


def _git(repo_root: Path, args: list[str]) -> str:
    return _git_bytes(repo_root, args).decode()


def in_scope(path: str) -> bool:
    """是否属于 changed-lines 门的生产源码范围。"""
    return bool(_SCOPE_RE.match(path))


def parse_added_lines_from_diff(diff_text: str) -> dict[str, set[int]]:
    """解析 ``git diff --unified=0`` 输出 → {repo 相对路径: 新增行号集合}。

    只看 hunk 头的 ``+<start>,<count>``——unified=0 下新增行号即
    ``start..start+count-1``（count 缺省 = 1，count=0 = 纯删除无新增）。
    """
    added: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            elif target.startswith("b/"):
                current = target[2:]
            else:
                current = target
            continue
        match = _HUNK_RE.match(line)
        if match and current is not None:
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            if count > 0:
                added.setdefault(current, set()).update(range(start, start + count))
    return added


def _resolve_merge_base(repo_root: Path, base_ref: str) -> str:
    return _git(repo_root, ["merge-base", base_ref, "HEAD"]).strip()


def _nul_paths(repo_root: Path, args: list[str]) -> set[str]:
    output = _git_bytes(repo_root, args)
    return {item.decode() for item in output.split(b"\0") if item}


def _local_candidate_paths(repo_root: Path, merge_base: str) -> set[str]:
    commands = (
        ["diff", "--name-only", "-z", f"{merge_base}..HEAD", "--"],
        ["diff", "--cached", "--name-only", "-z", "--"],
        ["diff", "--name-only", "-z", "--"],
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
    )
    paths: set[str] = set()
    for command in commands:
        paths.update(_nul_paths(repo_root, command))
    return {path for path in paths if in_scope(path)}


def _base_blob(repo_root: Path, merge_base: str, path: str) -> bytes:
    object_name = f"{merge_base}:{path}"
    entry = _git_bytes(repo_root, ["ls-tree", "-z", merge_base, "--", path])
    if not entry:
        return b""
    return _git_bytes(repo_root, ["show", object_name])


def _final_added_lines(path: str, before: bytes, after: bytes) -> set[int]:
    before_lines = before.decode("utf-8").splitlines(keepends=True)
    after_lines = after.decode("utf-8").splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=0,
        )
    )
    return parse_added_lines_from_diff(diff).get(path, set())


def _collect_local_added_lines(
    repo_root: Path,
    merge_base: str,
    relocation_baselines: dict[str, bytes] | None = None,
) -> dict[str, set[int]]:
    added: dict[str, set[int]] = {}
    for path in sorted(_local_candidate_paths(repo_root, merge_base)):
        final_path = repo_root / path
        final = final_path.read_bytes() if final_path.is_file() else b""
        baseline = (relocation_baselines or {}).get(path)
        before = (
            baseline
            if baseline is not None
            else _base_blob(repo_root, merge_base, path)
        )
        lines = _final_added_lines(path, before, final)
        if lines:
            added[path] = lines
    return added


def collect_added_lines(
    repo_root: Path, base_ref: str, mode: str
) -> dict[str, set[int]]:
    """按 committed 或最终工作树坐标收集范围内新增行。"""
    if mode == "committed":
        diff = _git(
            repo_root,
            ["diff", "--unified=0", "--no-ext-diff", f"{base_ref}...HEAD", "--"],
        )
        return {
            path: lines
            for path, lines in parse_added_lines_from_diff(diff).items()
            if in_scope(path)
        }
    if mode == "local-working-tree":
        return _collect_local_added_lines(
            repo_root, _resolve_merge_base(repo_root, base_ref)
        )
    raise ValueError(f"unsupported coverage mode: {mode}")


def parse_lcov(
    lcov_text: str, path_prefix: str = "octoagent/"
) -> dict[str, dict[int, int]]:
    """解析 lcov → {repo 相对路径: {行号: 命中次数}}。

    SF 路径若非绝对且不带 ``octoagent/`` 前缀（relative_files=true 相对 cwd=octoagent/），
    补 ``path_prefix`` 归一到 repo 相对路径。
    """
    coverage: dict[str, dict[int, int]] = {}
    current: str | None = None
    for line in lcov_text.splitlines():
        if line.startswith("SF:"):
            raw = line[3:].strip()
            if raw.startswith("/"):
                # 绝对路径：截取 octoagent/ 之后部分再归一（防守——理论上 relative_files 不产）
                idx = raw.find("/octoagent/")
                raw = raw[idx + 1 :] if idx >= 0 else raw
            if not raw.startswith(path_prefix):
                raw = path_prefix + raw
            current = raw
            coverage.setdefault(current, {})
        elif line.startswith("DA:") and current is not None:
            payload = line[3:].split(",")
            if len(payload) >= 2:
                try:
                    lineno, hits = int(payload[0]), int(payload[1])
                except ValueError:
                    continue
                coverage[current][lineno] = hits
        elif line.strip() == "end_of_record":
            current = None
    return coverage


def evaluate(
    added: dict[str, set[int]],
    coverage: dict[str, dict[int, int]],
    min_percent: float,
) -> tuple[bool, list[str], int, int]:
    """核心判定。返回 (是否过门, 明细行, 覆盖行数, 可执行新增行数)。"""
    detail: list[str] = []
    total = 0
    covered = 0
    for path in sorted(added):
        if not in_scope(path):
            continue
        lines = added[path]
        file_cov = coverage.get(path)
        if file_cov is None:
            # 范围内新文件无任何 lcov 记录：全部新增行按可执行 0 覆盖计（spec D5 规则）
            total += len(lines)
            detail.append(
                f"  {path}: 0/{len(lines)} —— lcov 无该文件记录（新模块无任何测试 import？）"
            )
            continue
        executable = sorted(line for line in lines if line in file_cov)
        if not executable:
            continue  # 新增行全是非可执行行（空行/注释/被排除）
        hit = [line for line in executable if file_cov[line] > 0]
        missed = [line for line in executable if file_cov[line] == 0]
        total += len(executable)
        covered += len(hit)
        if missed:
            detail.append(
                f"  {path}: {len(hit)}/{len(executable)} 未覆盖行 {_fmt_lines(missed)}"
            )
    if total == 0:
        return True, detail, covered, total
    pct = covered / total * 100.0
    return pct >= min_percent, detail, covered, total


def _fmt_lines(lines: list[int], cap: int = 20) -> str:
    shown = ",".join(str(x) for x in lines[:cap])
    return shown + (f",…(+{len(lines) - cap})" if len(lines) > cap else "")


def head_is_exempt(repo_root: Path) -> tuple[bool, str]:
    """HEAD commit message 是否含 [cov-exempt]；返回 (exempt, 'sha subject')。"""
    out = _git(repo_root, ["log", "-1", "--format=%H%x00%s%x00%B"]).split("\x00", 2)
    sha, subject, body = out[0], out[1], out[2] if len(out) > 2 else ""
    return EXEMPT_MARKER in body, f"{sha[:12]} {subject}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="F141 changed-lines coverage 门")
    parser.add_argument("--lcov", type=Path, required=True)
    parser.add_argument("--base", type=str, required=True, help="diff 基准 commit sha")
    parser.add_argument("--min-percent", type=float, default=90.0)
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument("--mode", choices=("committed", "local-working-tree"))
    parser.add_argument("--fresh-after-utc")
    parser.add_argument("--expected-stage-task")
    parser.add_argument("--expected-head-sha")
    parser.add_argument("--expected-head-tree")
    parser.add_argument("--expected-worktree-fingerprint")
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--relocation-snapshot")
    return parser


def _full_requested(args: argparse.Namespace) -> bool:
    return args.relocation_snapshot is not None or any(
        getattr(args, name) is not None for name in _FULL_ARGUMENTS
    )


def _full_complete(args: argparse.Namespace) -> bool:
    return all(getattr(args, name) is not None for name in _FULL_ARGUMENTS)


def _parse_utc_ns(value: str) -> int:
    match = _UTC_RE.fullmatch(value)
    if match is None:
        raise ValueError("fresh-after-utc must be an exact UTC timestamp")
    moment = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    fraction = (match.group(2) or "").ljust(9, "0")
    return int(moment.timestamp()) * 1_000_000_000 + int(fraction or "0")


def _format_utc_ns(value: int) -> str:
    seconds, fraction = divmod(value, 1_000_000_000)
    prefix = datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{prefix}.{fraction:09d}Z" if fraction else f"{prefix}Z"


def _worktree_fingerprint(repo_root: Path) -> str:
    status = _git_bytes(
        repo_root, ["status", "--porcelain=v2", "-z", "--untracked-files=all"]
    )
    return hashlib.sha256(status).hexdigest()


def _repository_identity(repo_root: Path, base_ref: str) -> dict[str, str]:
    return {
        "resolved_base_sha": _resolve_merge_base(repo_root, base_ref),
        "head_sha": _git(repo_root, ["rev-parse", "HEAD"]).strip(),
        "head_tree_sha": _git(repo_root, ["rev-parse", "HEAD^{tree}"]).strip(),
        "worktree_fingerprint": _worktree_fingerprint(repo_root),
    }


def _exact_dict(value: Any, keys: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"relocation snapshot {label} schema is invalid")
    return value


def _snapshot_reference(repo_root: Path, raw: str) -> tuple[dict[str, Any], str, str]:
    relative, separator, expected_sha = raw.rpartition("@")
    path = Path(relative)
    if (
        separator != "@"
        or not relative
        or path.is_absolute()
        or ".." in path.parts
        or _HEX64_RE.fullmatch(expected_sha) is None
    ):
        raise ValueError("relocation snapshot must be repo-relative path@sha256")
    absolute = repo_root / path
    content = absolute.read_bytes()
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("relocation snapshot sha256 mismatch")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("relocation snapshot JSON is invalid") from exc
    return (
        _exact_dict(payload, _ATOMIC_SNAPSHOT_KEYS, "root"),
        path.as_posix(),
        actual_sha,
    )


def _snapshot_file(value: Any, label: str) -> tuple[dict[str, Any], bytes]:
    row = _exact_dict(value, _SNAPSHOT_FILE_KEYS, label)
    source = row["source"]
    target = row["target"]
    if not isinstance(source, str) or not source or not in_scope(source):
        raise ValueError(f"relocation snapshot {label} source is invalid")
    if target is not None and (
        not isinstance(target, str) or not target or not in_scope(target)
    ):
        raise ValueError(f"relocation snapshot {label} target is invalid")
    if not isinstance(row["size_bytes"], int) or row["size_bytes"] < 0:
        raise ValueError(f"relocation snapshot {label} size is invalid")
    hash_keys = ("sha256", "normalized_ast_sha256")
    if not all(
        isinstance(row[key], str) and _HEX64_RE.fullmatch(row[key]) for key in hash_keys
    ):
        raise ValueError(f"relocation snapshot {label} hash is invalid")
    try:
        content = base64.b64decode(row["content_b64"], validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"relocation snapshot {label} content is invalid") from exc
    if (
        len(content) != row["size_bytes"]
        or hashlib.sha256(content).hexdigest() != row["sha256"]
    ):
        raise ValueError(f"relocation snapshot {label} bytes mismatch")
    return row, content


def _snapshot_file_maps(
    payload: dict[str, Any],
) -> tuple[
    dict[str, tuple[dict[str, Any], bytes]], dict[str, tuple[dict[str, Any], bytes]]
]:
    source_rows = payload["base_source_files"]
    target_rows = payload["target_files"]
    if not isinstance(source_rows, list) or not isinstance(target_rows, list):
        raise ValueError("relocation snapshot file lists are invalid")
    sources: dict[str, tuple[dict[str, Any], bytes]] = {}
    targets: dict[str, tuple[dict[str, Any], bytes]] = {}
    for index, value in enumerate(source_rows):
        row, content = _snapshot_file(value, f"base_source_files[{index}]")
        if row["source"] in sources:
            raise ValueError("relocation snapshot duplicate source")
        sources[row["source"]] = (row, content)
    for index, value in enumerate(target_rows):
        row, content = _snapshot_file(value, f"target_files[{index}]")
        target = row["target"]
        if target is None or target in targets:
            raise ValueError("relocation snapshot duplicate or missing target")
        targets[target] = (row, content)
    return sources, targets


def _projection_baselines(
    payload: dict[str, Any],
    sources: dict[str, tuple[dict[str, Any], bytes]],
    targets: dict[str, tuple[dict[str, Any], bytes]],
) -> dict[str, bytes]:
    pairs = payload["projection_pairs"]
    if not isinstance(pairs, list):
        raise ValueError("relocation snapshot projection_pairs is invalid")
    baselines: dict[str, bytes] = {}
    seen_sources: set[str] = set()
    for index, value in enumerate(pairs):
        pair = _exact_dict(value, _PROJECTION_KEYS, f"projection_pairs[{index}]")
        source, target = pair["source"], pair["target"]
        hash_keys = (
            "base_source_sha256",
            "target_sha256",
            "base_source_normalized_ast_sha256",
            "target_normalized_ast_sha256",
        )
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or not all(
                isinstance(pair[key], str) and _HEX64_RE.fullmatch(pair[key])
                for key in hash_keys
            )
        ):
            raise ValueError("relocation snapshot projection value is invalid")
        if source in seen_sources or target in baselines:
            raise ValueError("relocation snapshot duplicate projection")
        if source not in sources or target not in targets:
            raise ValueError("relocation snapshot projection path is unknown")
        source_row, source_content = sources[source]
        target_row, _ = targets[target]
        expected = (
            source_row["target"] == target,
            target_row["source"] == source,
            pair["base_source_sha256"] == source_row["sha256"],
            pair["target_sha256"] == target_row["sha256"],
            pair["base_source_normalized_ast_sha256"]
            == source_row["normalized_ast_sha256"],
            pair["target_normalized_ast_sha256"] == target_row["normalized_ast_sha256"],
        )
        if not all(expected):
            raise ValueError("relocation snapshot projection facts mismatch")
        seen_sources.add(source)
        baselines[target] = source_content
    if set(targets) != set(baselines):
        raise ValueError("relocation snapshot target projection set mismatch")
    return baselines


def _snapshot_identity(payload: dict[str, Any], merge_base: str) -> None:
    expected = {
        "version": 1,
        "feature_id": "F151",
        "lifecycle_type": "atomic-namespace-after",
        "task_id": "T029",
        "phase": "AFTER",
        "slice_id": "S017-namespace-atomic",
        "base_sha": merge_base,
        "c15_pre_verdict": "PASS",
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("relocation snapshot identity mismatch")
    string_fields = (
        "before_artifact_sha256",
        "captured_utc",
        "fingerprint_scope",
        "head_sha",
        "head_tree_sha",
        "manifest_path",
        "manifest_sha256",
        "worktree_fingerprint",
    )
    if not all(
        isinstance(payload.get(key), str) and payload[key] for key in string_fields
    ):
        raise ValueError("relocation snapshot identity field is invalid")
    hashes_valid = all(
        _HEX64_RE.fullmatch(payload[key])
        for key in (
            "before_artifact_sha256",
            "manifest_sha256",
            "worktree_fingerprint",
        )
    ) and all(
        _HEX40_RE.fullmatch(payload[key]) for key in ("head_sha", "head_tree_sha")
    )
    if not hashes_valid:
        raise ValueError("relocation snapshot identity hash is invalid")
    list_fields = ("approved_hash_exceptions", "fingerprint_files")
    if not all(
        isinstance(payload[key], list)
        and len(payload[key]) == len(set(payload[key]))
        and all(isinstance(item, str) and item for item in payload[key])
        for key in list_fields
    ):
        raise ValueError("relocation snapshot list field is invalid")


def _snapshot_deletions(
    payload: dict[str, Any],
    sources: dict[str, tuple[dict[str, Any], bytes]],
    moved_sources: set[str],
) -> set[str]:
    values = payload["deletions"]
    if not isinstance(values, list):
        raise ValueError("relocation snapshot deletions is invalid")
    deletions: set[str] = set()
    for index, value in enumerate(values):
        row = _exact_dict(value, _DELETION_KEYS, f"deletions[{index}]")
        source = row["source"]
        hash_keys = ("base_source_sha256", "base_source_normalized_ast_sha256")
        if not isinstance(source, str) or not all(
            isinstance(row[key], str) and _HEX64_RE.fullmatch(row[key])
            for key in hash_keys
        ):
            raise ValueError("relocation snapshot deletion value is invalid")
        if source in deletions or source not in sources or row["absent"] is not True:
            raise ValueError("relocation snapshot deletion path is invalid")
        source_row, _ = sources[source]
        if source_row["target"] is not None or any(
            row[key] != source_row[source_key]
            for key, source_key in (
                ("base_source_sha256", "sha256"),
                ("base_source_normalized_ast_sha256", "normalized_ast_sha256"),
            )
        ):
            raise ValueError("relocation snapshot deletion facts mismatch")
        deletions.add(source)
    if moved_sources | deletions != set(sources) or moved_sources & deletions:
        raise ValueError("relocation snapshot source set mismatch")
    return deletions


def _snapshot_counts(
    payload: dict[str, Any], baselines: dict[str, bytes], deletions: set[str]
) -> None:
    counts = payload["expected_counts"]
    if not isinstance(counts, dict):
        raise ValueError("relocation snapshot expected_counts is invalid")
    expected = {
        "moves": len(baselines),
        "deletes": len(deletions),
        "source": 0,
        "target": len(baselines),
    }
    if any(
        type(counts.get(key)) is not int or counts[key] != value
        for key, value in expected.items()
    ):
        raise ValueError("relocation snapshot counts mismatch")
    if type(payload["source_absence_count"]) is not int or payload[
        "source_absence_count"
    ] != len(baselines) + len(deletions):
        raise ValueError("relocation snapshot source absence count mismatch")
    if type(payload["target_presence_count"]) is not int or payload[
        "target_presence_count"
    ] != len(baselines):
        raise ValueError("relocation snapshot target presence count mismatch")
    if not isinstance(counts.get("buckets"), dict) or not isinstance(
        counts.get("roles"), dict
    ):
        raise ValueError("relocation snapshot nested counts are invalid")


def _verify_snapshot_bytes(
    repo_root: Path,
    merge_base: str,
    sources: dict[str, tuple[dict[str, Any], bytes]],
    targets: dict[str, tuple[dict[str, Any], bytes]],
    deletions: set[str],
) -> None:
    for source, (_, expected) in sources.items():
        actual = _git_bytes(repo_root, ["show", f"{merge_base}:{source}"])
        if actual != expected:
            raise ValueError(f"relocation snapshot source baseline drift: {source}")
    for target in targets:
        path = repo_root / target
        if not path.is_file():
            raise ValueError(f"relocation snapshot target missing: {target}")
    for source in deletions:
        if (repo_root / source).exists():
            raise ValueError(f"relocation snapshot deleted source exists: {source}")


def _load_relocation_snapshot(
    repo_root: Path, merge_base: str, raw: str
) -> tuple[dict[str, bytes], str, str]:
    payload, relative, digest = _snapshot_reference(repo_root, raw)
    _snapshot_identity(payload, merge_base)
    sources, targets = _snapshot_file_maps(payload)
    baselines = _projection_baselines(payload, sources, targets)
    moved_sources = {row[0]["source"] for row in targets.values()}
    deletions = _snapshot_deletions(payload, sources, moved_sources)
    _snapshot_counts(payload, baselines, deletions)
    _verify_snapshot_bytes(repo_root, merge_base, sources, targets, deletions)
    return baselines, relative, digest


def _read_lcov_snapshot(
    path: Path, fresh_after_ns: int | None
) -> tuple[bytes | None, str | None, str | None, bool]:
    if not path.is_file():
        return None, None, None, False
    before = path.stat()
    content = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("lcov changed while reading")
    digest = hashlib.sha256(content).hexdigest()
    fresh = fresh_after_ns is not None and after.st_mtime_ns > fresh_after_ns
    return content, digest, _format_utc_ns(after.st_mtime_ns), fresh


def _lcov_snapshot_matches(
    path: Path,
    fresh_after_ns: int | None,
    snapshot: tuple[bytes | None, str | None, str | None, bool],
) -> bool:
    try:
        current = _read_lcov_snapshot(path, fresh_after_ns)
    except OSError:
        return False
    return current[1:] == snapshot[1:]


def _full_report(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], tuple[bytes | None, str | None, str | None, bool], int]:
    fresh_after_ns = _parse_utc_ns(args.fresh_after_utc)
    snapshot = _read_lcov_snapshot(args.lcov, fresh_after_ns)
    identity = _repository_identity(args.repo_root, args.base)
    report = {
        "status": "FAIL",
        "mode": args.mode,
        "base_ref": args.base,
        **identity,
        "fresh_after_utc": args.fresh_after_utc,
        "stage_task": os.environ.get("F151_COVERAGE_STAGE"),
        "lcov_sha256": snapshot[1],
        "lcov_mtime_utc": snapshot[2],
        "lcov_fresh": snapshot[3],
    }
    return report, snapshot, fresh_after_ns


def _contract_errors(args: argparse.Namespace, report: dict[str, Any]) -> list[str]:
    expected = {
        "stage_task": args.expected_stage_task,
        "head_sha": args.expected_head_sha,
        "head_tree_sha": args.expected_head_tree,
        "worktree_fingerprint": args.expected_worktree_fingerprint,
    }
    errors = [key for key, value in expected.items() if report.get(key) != value]
    if not args.expected_stage_task:
        errors.append("expected_stage_task")
    return errors


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("report write made no progress")
        remaining = remaining[written:]


def _atomic_write_report(path: Path, payload: dict[str, Any]) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise OSError(f"report parent does not exist: {parent}")
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temp = Path(temp_name)
    try:
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temp, path)
        parent_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except OSError:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise


def _emit_full(
    args: argparse.Namespace,
    report: dict[str, Any],
    snapshot: tuple[bytes | None, str | None, str | None, bool],
    fresh_after_ns: int | None,
    exit_code: int,
    message: str,
    *,
    status: str | None = None,
) -> int:
    if not _lcov_snapshot_matches(args.lcov, fresh_after_ns, snapshot):
        exit_code = 1
        status = "FAIL"
        message = "[changed-lines FAIL] lcov changed during transaction"
    report["status"] = status or ("PASS" if exit_code == 0 else "FAIL")
    try:
        _atomic_write_report(args.report_json, report)
    except OSError as exc:
        print(f"[changed-lines FAIL] JSON report 写入失败: {exc}", file=sys.stderr)
        return 1
    stream = sys.stdout if exit_code == 0 else sys.stderr
    print(message, file=stream)
    return exit_code


def _emit_exempt(
    args: argparse.Namespace,
    report: dict[str, Any],
    snapshot: tuple[bytes | None, str | None, str | None, bool],
    fresh_after_ns: int,
    head_desc: str,
) -> int:
    return _emit_full(
        args,
        report,
        snapshot,
        fresh_after_ns,
        0,
        f"[changed-lines EXEMPT] {head_desc} 显式声明 {EXEMPT_MARKER}",
        status="EXEMPT",
    )


def _failure_report(
    args: argparse.Namespace,
    snapshot: tuple[bytes | None, str | None, str | None, bool],
) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "mode": args.mode,
        "base_ref": args.base,
        "resolved_base_sha": None,
        "fresh_after_utc": args.fresh_after_utc,
        "stage_task": os.environ.get("F151_COVERAGE_STAGE"),
        "head_sha": None,
        "head_tree_sha": None,
        "worktree_fingerprint": None,
        "lcov_sha256": snapshot[1],
        "lcov_mtime_utc": snapshot[2],
        "lcov_fresh": snapshot[3],
    }


def _collect_full_added_lines(
    args: argparse.Namespace,
    report: dict[str, Any],
    relocation_baselines: dict[str, bytes] | None,
) -> dict[str, set[int]]:
    if args.mode == "local-working-tree":
        return _collect_local_added_lines(
            args.repo_root, report["resolved_base_sha"], relocation_baselines
        )
    return collect_added_lines(args.repo_root, args.base, "committed")


def _coverage_outcome(
    added: dict[str, set[int]],
    args: argparse.Namespace,
    snapshot: tuple[bytes | None, str | None, str | None, bool],
) -> tuple[bool, list[str], int, int]:
    content = snapshot[0]
    if content is None:
        raise OSError("lcov snapshot is missing")
    coverage = parse_lcov(content.decode("utf-8"))
    return evaluate(added, coverage, args.min_percent)


def _emit_failure(
    args: argparse.Namespace,
    report: dict[str, Any],
    snapshot: tuple[bytes | None, str | None, str | None, bool],
    fresh_after_ns: int | None,
    message: str,
) -> int:
    return _emit_full(args, report, snapshot, fresh_after_ns, 1, message)


def _failure_context(
    args: argparse.Namespace,
) -> tuple[
    dict[str, Any], tuple[bytes | None, str | None, str | None, bool], int | None
]:
    try:
        fresh_after_ns = _parse_utc_ns(args.fresh_after_utc)
    except ValueError:
        fresh_after_ns = None
    try:
        snapshot = _read_lcov_snapshot(args.lcov, fresh_after_ns)
    except OSError:
        snapshot = (None, None, None, False)
    return _failure_report(args, snapshot), snapshot, fresh_after_ns


def _evaluate_full_coverage(
    args: argparse.Namespace,
    report: dict[str, Any],
    snapshot: tuple[bytes | None, str | None, str | None, bool],
    fresh_after_ns: int,
    relocation_baselines: dict[str, bytes] | None,
) -> int:
    try:
        added = _collect_full_added_lines(args, report, relocation_baselines)
        current_identity = _repository_identity(args.repo_root, args.base)
        if any(report[key] != value for key, value in current_identity.items()):
            return _emit_failure(
                args,
                report,
                snapshot,
                fresh_after_ns,
                "[changed-lines FAIL] repository changed",
            )
        ok, detail, covered, total = _coverage_outcome(added, args, snapshot)
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        return _emit_failure(
            args, report, snapshot, fresh_after_ns, f"[changed-lines FAIL] {exc}"
        )
    report.update({"covered_lines": covered, "executable_added_lines": total})
    pct = f"{covered}/{total}" + (f" = {covered / total * 100.0:.1f}%" if total else "")
    message = f"[changed-lines {'PASS' if ok else 'FAIL'}] 新增可执行行覆盖 {pct}"
    if detail:
        message += "\n" + "\n".join(detail)
    return _emit_full(args, report, snapshot, fresh_after_ns, 0 if ok else 1, message)


def _relocation_context(
    args: argparse.Namespace, report: dict[str, Any]
) -> dict[str, bytes] | None:
    if args.relocation_snapshot is None:
        return None
    if args.mode != "local-working-tree":
        raise ValueError("relocation snapshot requires local-working-tree")
    baselines, relative, digest = _load_relocation_snapshot(
        args.repo_root, report["resolved_base_sha"], args.relocation_snapshot
    )
    report.update(
        {
            "relocation_snapshot_path": relative,
            "relocation_snapshot_sha256": digest,
            "relocation_mapping_count": len(baselines),
        }
    )
    return baselines


def _full_main(args: argparse.Namespace) -> int:
    try:
        report, snapshot, fresh_after_ns = _full_report(args)
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        failure, snapshot, fresh_after_ns = _failure_context(args)
        return _emit_failure(
            args, failure, snapshot, fresh_after_ns, f"[changed-lines FAIL] {exc}"
        )
    try:
        relocation_baselines = _relocation_context(args, report)
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[changed-lines FAIL] {exc}", file=sys.stderr)
        return 1
    errors = _contract_errors(args, report)
    if errors:
        detail = ", ".join(errors)
        return _emit_failure(
            args,
            report,
            snapshot,
            fresh_after_ns,
            f"[changed-lines FAIL] identity mismatch: {detail}",
        )
    if not report["lcov_fresh"]:
        return _emit_failure(
            args,
            report,
            snapshot,
            fresh_after_ns,
            "[changed-lines FAIL] lcov missing or stale",
        )
    try:
        exempt, head_desc = head_is_exempt(args.repo_root)
    except (UnicodeError, subprocess.CalledProcessError) as exc:
        return _emit_failure(
            args,
            report,
            snapshot,
            fresh_after_ns,
            f"[changed-lines FAIL] {exc}",
        )
    if args.mode == "committed" and exempt:
        return _emit_exempt(args, report, snapshot, fresh_after_ns, head_desc)
    return _evaluate_full_coverage(
        args, report, snapshot, fresh_after_ns, relocation_baselines
    )


def _legacy_coverage_result(
    args: argparse.Namespace, scoped: dict[str, set[int]]
) -> int:
    if not args.lcov.is_file():
        print(f"[changed-lines FAIL] lcov 文件不存在: {args.lcov}", file=sys.stderr)
        return 1
    coverage = parse_lcov(args.lcov.read_text(encoding="utf-8"))
    ok, detail, covered, total = evaluate(scoped, coverage, args.min_percent)
    pct_text = f"{covered}/{total}" + (
        f" = {covered / total * 100.0:.1f}%" if total else ""
    )
    if total == 0:
        print("[changed-lines PASS] 范围内新增行均非可执行行（注释/空行）")
        return 0
    if ok:
        print(
            f"[changed-lines PASS] 新增可执行行覆盖 {pct_text} ≥ {args.min_percent:.0f}%"
        )
        for line in detail:
            print(line)
        return 0
    print(
        f"[changed-lines FAIL] 新增可执行行覆盖 {pct_text} < {args.min_percent:.0f}%",
        file=sys.stderr,
    )
    for line in detail:
        print(line, file=sys.stderr)
    print(
        f"  修法：为新增行补测试；或（确属不可测例外）HEAD commit message 加 {EXEMPT_MARKER} 标记",
        file=sys.stderr,
    )
    return 1


def _legacy_main(args: argparse.Namespace) -> int:
    repo_root = args.repo_root

    exempt, head_desc = head_is_exempt(repo_root)
    if exempt:
        print("=" * 72)
        print(
            f"[changed-lines EXEMPT] HEAD commit 声明 {EXEMPT_MARKER} —— 本次 push 豁免覆盖门"
        )
        print(f"  HEAD: {head_desc}")
        print("  语义：与 SKIP_E2E 同级的显式 bypass；豁免记录留在 CI 日志")
        print("=" * 72)
        return 0

    try:
        scoped = collect_added_lines(repo_root, args.base, "committed")
    except subprocess.CalledProcessError as exc:
        print(
            f"[changed-lines FAIL] git diff 失败（base={args.base}）: {exc.stderr}",
            file=sys.stderr,
        )
        return 1

    if not scoped:
        print(
            "[changed-lines PASS] 本次 diff 无范围内生产源码新增行（0 changed executable lines）"
        )
        return 0
    return _legacy_coverage_result(args, scoped)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not _full_requested(args):
        return _legacy_main(args)
    if not _full_complete(args):
        print(
            "[changed-lines FAIL] C19 full-contract arguments must be complete",
            file=sys.stderr,
        )
        return 2
    return _full_main(args)


if __name__ == "__main__":
    sys.exit(main())
