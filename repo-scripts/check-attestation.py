#!/usr/bin/env python3
"""F141 件1：attestation 清单校验器（消费 F144 的 attestation-checklist.md）。

数据源 = ``docs/codebase-architecture/attestation-checklist.md`` 的**第一个** ```yaml
fenced block（F144 约定的机器可读源），每项 7 字段：
    id / source_ac / why_physical / action / frequency / last_attested / optional

两种模式：
- 默认（解析校验）：yaml block 可提取、schema 完整、id 唯一 → exit 0；
  供 CI / pre-commit（staged 触发）防清单格式腐化。
- ``--require-signed``（release gate 模式）：对每个 ``optional: false`` 且
  ``frequency: release`` 的项，要求 ``last_attested`` 非 null 且距 ``--as-of``
  不超过 ``--attest-max-age`` 天（默认 90）——否则 exit 1。
  90 天默认值的取舍：attestation 项（如「重启 Mac 验开机自启」）只在相关子系统改动时
  才真正需要重验，时间窗是唯一可机械化的 proxy；过短逼出例行盖章（反狼来了），过长等于
  永不复验。可用 ``--attest-max-age`` 按 release 节奏调。

依赖 PyYAML（provider/skills 传导依赖，项目 venv 恒有）——须以
``uv run --project octoagent --no-sync python repo-scripts/check-attestation.py`` 调用。

exit code：0 = 通过；1 = 校验失败/未签署；2 = 参数错误/依赖缺失。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

REQUIRED_FIELDS = (
    "id", "source_ac", "why_physical", "action", "frequency", "last_attested", "optional",
)

DEFAULT_CHECKLIST = (
    Path(__file__).resolve().parent.parent
    / "docs" / "codebase-architecture" / "attestation-checklist.md"
)

_YAML_BLOCK_RE = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)


class AttestationError(Exception):
    """清单解析/校验失败。"""


def extract_yaml_block(md_text: str) -> str:
    """提取第一个 ```yaml fenced block（F144 约定：解析器取第一个）。"""
    match = _YAML_BLOCK_RE.search(md_text)
    if not match:
        raise AttestationError("attestation-checklist.md 中找不到 ```yaml fenced block")
    return match.group(1)


def load_checklist(path: Path) -> list[dict]:
    """读取 + 解析 + schema 校验；返回 attestations 列表。"""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 依赖缺失护栏，venv 内恒可 import
        raise AttestationError(
            "PyYAML 不可用——请用 `uv run --project octoagent --no-sync python` 调用本脚本"
        ) from exc

    if not path.is_file():
        raise AttestationError(f"attestation 清单不存在: {path}")
    block = extract_yaml_block(path.read_text(encoding="utf-8"))
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise AttestationError(f"yaml block 解析失败: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("attestations"), list):
        raise AttestationError("yaml block 顶层必须是 {attestations: [...]}")

    ids: set[str] = set()
    items: list[dict] = []
    for i, item in enumerate(data["attestations"]):
        ctx = f"attestations[{i}]"
        if not isinstance(item, dict):
            raise AttestationError(f"{ctx}: 条目必须是 mapping")
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            raise AttestationError(f"{ctx}: 缺字段 {missing}")
        if not isinstance(item["id"], str) or not item["id"].strip():
            raise AttestationError(f"{ctx}: id 必须是非空字符串")
        if item["id"] in ids:
            raise AttestationError(f"{ctx}: 重复 id: {item['id']!r}")
        ids.add(item["id"])
        if not isinstance(item["optional"], bool):
            raise AttestationError(f"{ctx}: optional 必须是 bool")
        la = item["last_attested"]
        if la is not None:
            # yaml 会把裸 2026-07-12 解析成 date；带引号则是 str——两种都接受
            if isinstance(la, str):
                try:
                    _dt.date.fromisoformat(la)
                except ValueError as exc:
                    raise AttestationError(
                        f"{ctx}: last_attested 不是合法 YYYY-MM-DD: {la!r}"
                    ) from exc
            elif not isinstance(la, _dt.date):
                raise AttestationError(f"{ctx}: last_attested 必须是 null 或 YYYY-MM-DD")
        items.append(item)
    return items


def _as_date(value) -> _dt.date:
    return value if isinstance(value, _dt.date) else _dt.date.fromisoformat(value)


def unsigned_release_items(
    items: list[dict], as_of: _dt.date, max_age_days: int
) -> list[tuple[dict, str]]:
    """返回 release gate 视角未签署的 (item, 原因) 列表。"""
    out: list[tuple[dict, str]] = []
    for item in items:
        if item["optional"] or item["frequency"] != "release":
            continue
        la = item["last_attested"]
        if la is None:
            out.append((item, "last_attested 为 null（从未签署）"))
            continue
        age = (as_of - _as_date(la)).days
        if age > max_age_days:
            out.append(
                (item, f"签署已过期 {age} 天 > 上限 {max_age_days} 天（last_attested={la}）")
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F141 attestation 清单校验")
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    parser.add_argument("--require-signed", action="store_true",
                        help="release gate 模式：非 optional 的 release 项必须已签署且未超龄")
    parser.add_argument("--attest-max-age", type=int, default=90, help="签署有效天数（默认 90）")
    parser.add_argument("--as-of", type=str, default=None, help="YYYY-MM-DD（默认今天）")
    args = parser.parse_args(argv)

    try:
        as_of = _dt.date.fromisoformat(args.as_of) if args.as_of else _dt.date.today()
    except ValueError:
        print(f"[attestation] --as-of 不是合法 YYYY-MM-DD: {args.as_of!r}", file=sys.stderr)
        return 2

    try:
        items = load_checklist(args.checklist)
    except AttestationError as exc:
        print(f"[attestation FAIL] {exc}", file=sys.stderr)
        return 1

    required = [i for i in items if not i["optional"] and i["frequency"] == "release"]
    print(f"[attestation] 清单 {len(items)} 项 / release 必签 {len(required)} 项 (as_of={as_of})")

    if not args.require_signed:
        return 0

    unsigned = unsigned_release_items(items, as_of, args.attest_max_age)
    if unsigned:
        for item, reason in unsigned:
            print(
                f"[attestation FAIL] 未签署: {item['id']} —— {reason}\n"
                f"  action: {item['action']}\n"
                f"  签署方式: 人工执行 action 后在 attestation-checklist.md 回填 last_attested"
                f"（lane 只核对不代签，Constitution #7）",
                file=sys.stderr,
            )
        return 1
    for item in items:
        if item["optional"]:
            print(
                f"[attestation] optional 项记录: {item['id']}"
                f" (last_attested={item['last_attested']})"
            )
    print("[attestation] release 必签项全部有效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
