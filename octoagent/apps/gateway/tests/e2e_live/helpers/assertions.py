"""F087 P2 T-P2-10：e2e_live 断言 helper 工具集。

5 个 helper：

1. ``assert_tool_called(events, name)``
2. ``assert_event_emitted(events, type)``
3. ``assert_writeresult_status(result, expected)``
4. ``assert_file_contains(path, substr)``
5. ``assert_no_threat_block(events)``

每个 helper 用清晰的 AssertionError 抛错；assertion 信息含可观测的副作用快照
（events 中匹配的 type/name 列表 / 文件实际内容前 200 字 / 等）。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any


def assert_tool_called(events: Iterable[Any], name: str) -> None:
    """断言 events 流中至少出现一次 type 为 ``tool.call`` 且 ``name`` 匹配的事件。

    支持 events 元素是 dict（``{"type": "tool.call", "name": "..."}``）或
    带属性的对象（``event.type`` / ``event.name``）。
    """
    matched: list[str] = []
    for ev in events:
        ev_type = _get(ev, "type", "")
        ev_name = _get(ev, "name", "")
        # 也兼容 ev["payload"]["name"] 这种嵌套
        if not ev_name:
            payload = _get(ev, "payload", {}) or {}
            ev_name = payload.get("name", "") if isinstance(payload, dict) else ""
        if ev_type in {"tool.call", "tool.invoked", "tool.start"}:
            matched.append(ev_name)

    if name not in matched:
        raise AssertionError(
            f"assert_tool_called: tool.call name={name!r} not found. "
            f"Observed tool calls: {matched[:20]}"
        )


def assert_event_emitted(events: Iterable[Any], type_: str) -> None:
    """断言 events 流中至少出现一次 ``type == type_`` 的事件。"""
    types_seen: list[str] = []
    for ev in events:
        ev_type = _get(ev, "type", "")
        types_seen.append(ev_type)
        if ev_type == type_:
            return

    raise AssertionError(
        f"assert_event_emitted: type={type_!r} not in events stream. "
        f"Observed types (first 30): {types_seen[:30]}"
    )


def assert_writeresult_status(result: Any, expected: str) -> None:
    """断言 ``result.status`` (或 dict["status"]) == expected。

    支持 WriteResult Pydantic model 或 plain dict 形态。
    """
    actual = _get(result, "status", None)
    if actual != expected:
        raise AssertionError(
            f"assert_writeresult_status: status mismatch (expected={expected!r}, "
            f"actual={actual!r}). Result: {result!r}"
        )


def assert_file_contains(path: Path | str, substr: str) -> None:
    """断言文件内容包含 substr。文件不存在或 empty 时直接 fail。"""
    p = Path(path)
    if not p.exists():
        raise AssertionError(
            f"assert_file_contains: path={p} 不存在；substr={substr!r}"
        )
    content = p.read_text(encoding="utf-8")
    if substr not in content:
        # 仅 dump 前 200 字避免污染输出
        preview = content[:200].replace("\n", "\\n")
        raise AssertionError(
            f"assert_file_contains: substr={substr!r} not in {p}. "
            f"Content preview (200 chars): {preview!r}"
        )


def assert_no_threat_block(events: Iterable[Any]) -> None:
    """断言 events 流中**没有**任何 ``threat.blocked`` / ``threat.block`` 事件。

    用于"正常 path 应该不被 ThreatScanner 拦"的场景（例如域 #2 的 USER.md update
    是合法操作）。
    """
    blocks: list[dict[str, Any]] = []
    for ev in events:
        ev_type = _get(ev, "type", "")
        if ev_type in {"threat.blocked", "threat.block"}:
            blocks.append({
                "type": ev_type,
                "payload": _get(ev, "payload", None),
            })
    if blocks:
        raise AssertionError(
            f"assert_no_threat_block: 期望无 threat.blocked，实际有 {len(blocks)} 条："
            f" {blocks[:3]}"
        )


# ---------------------------------------------------------------------------
# 内部 helper
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """同时支持 dict 和带属性对象的属性提取。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


__all__ = [
    "assert_tool_called",
    "assert_event_emitted",
    "assert_writeresult_status",
    "assert_file_contains",
    "assert_no_threat_block",
]
