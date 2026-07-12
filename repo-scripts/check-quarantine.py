#!/usr/bin/env python3
"""F141 件2：flaky quarantine manifest 校验器（stdlib-only，pre-commit hook 可直调）。

manifest = ``octoagent/tests/quarantine.json``，六字段条目（cc-haha quarantine.ts 范式）：
    id / path / reason / owner / review_after / exit_criteria

校验规则：
- 顶层必须是 ``{"quarantined": [...]}``；
- 每条目六字段全非空字符串；``review_after`` 必须是合法 ``YYYY-MM-DD``；
- ``id`` 与 ``path`` 各自全局唯一（重复 = 治理账本腐化）；
- ``--enforce-review-date``：``review_after`` < 今天（或 ``--as-of``）→ **过期即 FAIL**
  （exit 1）——隔离不允许烂尾，到期必须复查：要么治好删条目，要么带新证据续期。

exit code：0 = 校验通过；1 = 校验失败/过期；2 = 参数错误。

用法：
    python3 repo-scripts/check-quarantine.py                       # 仅 schema 校验
    python3 repo-scripts/check-quarantine.py --enforce-review-date # gate 模式（CI / lane）
    python3 repo-scripts/check-quarantine.py --as-of 2026-08-01 --enforce-review-date

三分处置边界（详见 octoagent/tests/AGENTS.md）：本 manifest 只收「真 flaky（时序/环境间歇）」；
环境永久不适用（如绝对时长性能断言）走测试内 ``skipif`` + 理由；真 LLM 固有变异性走
e2e_live conftest 的 e2e_full 专属 rerun 政策。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

REQUIRED_FIELDS = ("id", "path", "reason", "owner", "review_after", "exit_criteria")

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parent.parent / "octoagent" / "tests" / "quarantine.json"
)


class QuarantineError(Exception):
    """manifest 校验失败。"""


def parse_date(value: str, field_ctx: str) -> _dt.date:
    """严格解析 YYYY-MM-DD；失败抛 QuarantineError。"""
    try:
        return _dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise QuarantineError(f"{field_ctx}: review_after 不是合法 YYYY-MM-DD: {value!r}") from exc


def load_manifest(path: Path) -> dict:
    """读取 + schema 校验（六字段/唯一性/日期格式）；失败抛 QuarantineError。"""
    if not path.is_file():
        raise QuarantineError(f"quarantine manifest 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QuarantineError(f"quarantine manifest 不是合法 JSON: {path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("quarantined"), list):
        raise QuarantineError('quarantine manifest 顶层必须是 {"quarantined": [...]}')

    ids: set[str] = set()
    paths: set[str] = set()
    for i, entry in enumerate(data["quarantined"]):
        ctx = f"quarantined[{i}]"
        if not isinstance(entry, dict):
            raise QuarantineError(f"{ctx}: 条目必须是 object")
        for field in REQUIRED_FIELDS:
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise QuarantineError(f"{ctx}: 缺失或空字段 {field!r}（六字段全必填）")
        parse_date(entry["review_after"], ctx)
        if entry["id"] in ids:
            raise QuarantineError(f"{ctx}: 重复 id: {entry['id']!r}")
        if entry["path"] in paths:
            raise QuarantineError(f"{ctx}: 重复 path: {entry['path']!r}")
        ids.add(entry["id"])
        paths.add(entry["path"])
    return data


def expired_entries(manifest: dict, as_of: _dt.date) -> list[dict]:
    """返回 review_after < as_of 的过期条目（当天不算过期）。"""
    out = []
    for entry in manifest["quarantined"]:
        if _dt.date.fromisoformat(entry["review_after"]) < as_of:
            out.append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F141 quarantine manifest 校验")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--enforce-review-date", action="store_true",
                        help="过期条目即 FAIL（gate 模式）")
    parser.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD（默认今天）")
    args = parser.parse_args(argv)

    try:
        as_of = _dt.date.fromisoformat(args.as_of) if args.as_of else _dt.date.today()
    except ValueError:
        print(f"[quarantine] --as-of 不是合法 YYYY-MM-DD: {args.as_of!r}", file=sys.stderr)
        return 2

    try:
        manifest = load_manifest(args.manifest)
    except QuarantineError as exc:
        print(f"[quarantine FAIL] {exc}", file=sys.stderr)
        return 1

    entries = manifest["quarantined"]
    expired = expired_entries(manifest, as_of)
    print(f"[quarantine] 条目 {len(entries)} / 过期 {len(expired)} (as_of={as_of})")

    if expired and args.enforce_review_date:
        for entry in expired:
            print(
                f"[quarantine FAIL] 过期条目需复查: {entry['id']} "
                f"(path={entry['path']}, review_after={entry['review_after']}, "
                f"owner={entry['owner']})\n"
                f"  exit_criteria: {entry['exit_criteria']}",
                file=sys.stderr,
            )
        print("[quarantine FAIL] 复查后：治好删条目，或带新证据更新 review_after", file=sys.stderr)
        return 1
    if expired:
        # 非 gate 模式只提示
        for entry in expired:
            print(
                f"[quarantine WARN] 过期条目: {entry['id']}"
                f" (review_after={entry['review_after']})"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
