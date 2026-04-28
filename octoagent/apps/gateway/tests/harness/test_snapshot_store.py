"""SnapshotStore 单元测试（T036）。

Feature 084 Phase 2 — 验证冻结快照不可变性、live state 更新语义、原子写入。
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import patch

import pytest

from octoagent.gateway.harness.snapshot_store import SnapshotStore


# ---------------------------------------------------------------------------
# 辅助 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_user_md(tmp_path: Path) -> Path:
    """创建临时 USER.md。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    user_md.write_text("§ 初始条目：timezone: UTC\n", encoding="utf-8")
    return user_md


@pytest.fixture
def tmp_memory_md(tmp_path: Path) -> Path:
    """创建临时 MEMORY.md。"""
    memory_md = tmp_path / "behavior" / "system" / "MEMORY.md"
    memory_md.parent.mkdir(parents=True, exist_ok=True)
    memory_md.write_text("# 记忆文件\n", encoding="utf-8")
    return memory_md


# ---------------------------------------------------------------------------
# test_snapshot_store_prefix_cache_immutable（T036 核心验收）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_store_prefix_cache_immutable(
    tmp_user_md: Path,
    tmp_memory_md: Path,
) -> None:
    """load_snapshot 后 write_through，format_for_system_prompt() 仍返回原始内容（SC-011）。

    设计：冻结副本在 session 整个生命周期不变，mid-session 写入不影响系统提示。
    """
    store = SnapshotStore(conn=None)  # 测试场景，conn=None 降级
    original_content = tmp_user_md.read_text(encoding="utf-8")

    await store.load_snapshot(
        session_id="test-session-001",
        files={
            "USER.md": tmp_user_md,
            "MEMORY.md": tmp_memory_md,
        },
    )

    # 验证初始冻结快照内容正确
    snapshot = store.format_for_system_prompt()
    assert snapshot["USER.md"] == original_content

    # 模拟 mid-session 写入 USER.md
    new_content = original_content + "§ 新增条目：locale: zh-CN\n"
    await store.write_through(
        file_path=tmp_user_md,
        new_content=new_content,
        live_state_key="USER.md",
    )

    # 核心断言：冻结副本仍是原始内容（不受 write_through 影响）
    snapshot_after_write = store.format_for_system_prompt()
    assert snapshot_after_write["USER.md"] == original_content, (
        "write_through 不应改变冻结快照（SC-011 / prefix cache 不可变性）"
    )

    # live state 应已更新
    assert store.get_live_state("USER.md") == new_content


# ---------------------------------------------------------------------------
# test_snapshot_store_live_state_updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_store_live_state_updated(tmp_user_md: Path) -> None:
    """write_through 后 get_live_state() 返回新内容。"""
    store = SnapshotStore(conn=None)
    await store.load_snapshot(
        session_id="test-session-002",
        files={"USER.md": tmp_user_md},
    )

    new_content = "§ 更新后的内容\n"
    await store.write_through(
        file_path=tmp_user_md,
        new_content=new_content,
        live_state_key="USER.md",
    )

    assert store.get_live_state("USER.md") == new_content


# ---------------------------------------------------------------------------
# test_snapshot_store_atomic_write（模拟异常，原始文件保持完整）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_store_atomic_write(tmp_user_md: Path) -> None:
    """write_through 中途模拟异常，原始文件完整性不受损。

    原子写入通过 tempfile + os.replace 保证：即使写入到一半失败，
    目标文件也不会变为空文件或损坏状态。
    """
    store = SnapshotStore(conn=None)
    original_content = tmp_user_md.read_text(encoding="utf-8")

    await store.load_snapshot(
        session_id="test-session-003",
        files={"USER.md": tmp_user_md},
    )

    import os

    original_replace = os.replace

    def failing_replace(src: str, dst: str) -> None:
        """模拟 os.replace 失败（磁盘满 / 权限错误等）。"""
        raise OSError("模拟 os.replace 失败（测试用）")

    with patch("os.replace", side_effect=failing_replace):
        with pytest.raises(OSError):
            await store.write_through(
                file_path=tmp_user_md,
                new_content="新内容（不应写入）",
                live_state_key="USER.md",
            )

    # 原始文件内容应完整（atomic write 保证）
    assert tmp_user_md.read_text(encoding="utf-8") == original_content
