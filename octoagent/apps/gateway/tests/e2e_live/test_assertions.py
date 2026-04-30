"""F087 P2 T-P2-10 helpers/assertions 自身单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.gateway.tests.e2e_live.helpers.assertions import (
    assert_event_emitted,
    assert_file_contains,
    assert_no_threat_block,
    assert_tool_called,
    assert_writeresult_status,
)


pytestmark = [pytest.mark.e2e_live]


def test_assert_tool_called_pass_dict() -> None:
    events = [
        {"type": "task.start"},
        {"type": "tool.call", "name": "memory.write"},
    ]
    assert_tool_called(events, "memory.write")


def test_assert_tool_called_fail() -> None:
    events = [{"type": "tool.call", "name": "memory.read"}]
    with pytest.raises(AssertionError, match="memory.write"):
        assert_tool_called(events, "memory.write")


def test_assert_event_emitted_pass() -> None:
    events = [{"type": "task.start"}, {"type": "task.complete"}]
    assert_event_emitted(events, "task.complete")


def test_assert_event_emitted_fail() -> None:
    events = [{"type": "task.start"}]
    with pytest.raises(AssertionError, match="task.complete"):
        assert_event_emitted(events, "task.complete")


def test_assert_writeresult_status_pass_dict() -> None:
    assert_writeresult_status({"status": "written"}, "written")


def test_assert_writeresult_status_pass_obj() -> None:
    class R:
        status = "promoted"

    assert_writeresult_status(R(), "promoted")


def test_assert_writeresult_status_fail() -> None:
    with pytest.raises(AssertionError, match="status mismatch"):
        assert_writeresult_status({"status": "failed"}, "written")


def test_assert_file_contains_pass(tmp_path: Path) -> None:
    p = tmp_path / "u.md"
    p.write_text("hello Connor\n", encoding="utf-8")
    assert_file_contains(p, "Connor")


def test_assert_file_contains_fail_missing(tmp_path: Path) -> None:
    p = tmp_path / "missing.md"
    with pytest.raises(AssertionError, match="不存在"):
        assert_file_contains(p, "x")


def test_assert_file_contains_fail_substr(tmp_path: Path) -> None:
    p = tmp_path / "u.md"
    p.write_text("hello\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Connor"):
        assert_file_contains(p, "Connor")


def test_assert_no_threat_block_pass() -> None:
    events = [{"type": "tool.call"}, {"type": "task.complete"}]
    assert_no_threat_block(events)


def test_assert_no_threat_block_fail() -> None:
    events = [{"type": "threat.blocked", "payload": {"pattern": "_PI_001"}}]
    with pytest.raises(AssertionError, match="threat.blocked"):
        assert_no_threat_block(events)
