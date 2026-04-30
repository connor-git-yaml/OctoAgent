"""F087 P2 T-P2-11 helpers/state_diff 自身单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.gateway.tests.e2e_live.helpers.state_diff import (
    module_singletons_snapshot,
    sha256_dir,
    sha256_file,
    snapshot_to_json,
)


pytestmark = [pytest.mark.e2e_live]


def test_sha256_file_basic(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    h1 = sha256_file(p)
    assert len(h1) == 64
    # 内容相同 → hash 相同
    p.write_text("hello", encoding="utf-8")
    assert sha256_file(p) == h1
    # 内容不同 → hash 不同
    p.write_text("world", encoding="utf-8")
    assert sha256_file(p) != h1


def test_sha256_file_missing(tmp_path: Path) -> None:
    assert sha256_file(tmp_path / "no.txt") == ""


def test_sha256_dir_detects_added_file(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    h1 = sha256_dir(d)

    (d / "b.txt").write_text("y", encoding="utf-8")
    h2 = sha256_dir(d)
    assert h1 != h2


def test_sha256_dir_detects_renamed_file(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    h1 = sha256_dir(d)
    (d / "a.txt").rename(d / "b.txt")
    h2 = sha256_dir(d)
    assert h1 != h2


def test_sha256_dir_unchanged_for_idempotent(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "b.txt").write_text("y", encoding="utf-8")
    assert sha256_dir(d) == sha256_dir(d)


def test_sha256_dir_missing(tmp_path: Path) -> None:
    assert sha256_dir(tmp_path / "no") == ""


def test_module_singletons_snapshot_keys() -> None:
    """快照含 5 个 expected keys。"""
    snap = module_singletons_snapshot()
    assert "tool_registry_count" in snap
    assert "agent_context_llm_set" in snap
    assert "agent_context_router_set" in snap
    assert "execution_context_var" in snap
    assert "tiktoken_encoder_set" in snap


def test_module_singletons_snapshot_after_reset() -> None:
    """_reset_module_state 跑过后所有 stateful 都应处于 default 状态。"""
    snap = module_singletons_snapshot()
    # _reset_module_state autouse 已运行
    assert snap["tool_registry_count"] == 0
    assert snap["agent_context_llm_set"] is False
    assert snap["agent_context_router_set"] is False
    assert snap["execution_context_var"] == "None"
    assert snap["tiktoken_encoder_set"] is False


def test_snapshot_to_json_serializable() -> None:
    snap = module_singletons_snapshot()
    s = snapshot_to_json(snap)
    assert s.startswith("{") and s.endswith("}")
