"""SnapshotStore 并发写基准测试（T073）。

Feature 084 Phase 5 验收：
- test_concurrent_writes_no_data_loss：10 协程并发 write_through，文件内容完整（atomic rename 保证）
- test_prefix_cache_hit_rate_preserved：同 session 多 turn，冻结快照不变（prefix cache 保护）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.gateway.harness.snapshot_store import SnapshotStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_conn(tmp_path: Path):
    """创建内存 SQLite 连接（含完整 schema）。"""
    from octoagent.core.store.sqlite_init import init_db
    db_path = str(tmp_path / "concurrent_test.db")
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield conn
    await conn.close()


@pytest.fixture
def user_md_file(tmp_path: Path) -> Path:
    """创建临时 USER.md 文件。"""
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    user_md.write_text("§ 初始内容：timezone: Asia/Shanghai\n", encoding="utf-8")
    return user_md


# ---------------------------------------------------------------------------
# T073-1：10 协程并发写 write_through，最终文件内容完整
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writes_no_data_loss(db_conn, user_md_file: Path) -> None:
    """10 个协程并发调用 write_through，atomic rename 保证最终文件完整无损坏。

    设计：
    - 10 个协程同时写入不同内容（不同 content_N 标记）
    - 最终文件必须是合法的 UTF-8 文本（不损坏）
    - 文件存在且不为空
    - 最终内容来自某一次写入（原子性保证，非混合写入）
    """
    store = SnapshotStore(conn=db_conn)
    await store.load_snapshot(
        session_id="concurrent-test-session",
        files={"USER.md": user_md_file},
    )

    NUM_COROUTINES = 10
    # 记录每次写入的内容
    written_contents = []
    errors: list[Exception] = []

    async def write_one(index: int) -> None:
        """单个协程的写入任务。"""
        content = f"§ 并发写入 coroutine_{index:02d}：timestamp=T{index * 100}\n"
        written_contents.append(content)
        try:
            await store.write_through(
                file_path=user_md_file,
                new_content=content,
                live_state_key="USER.md",
            )
        except Exception as exc:
            errors.append(exc)

    # 10 个协程并发运行
    tasks = [write_one(i) for i in range(NUM_COROUTINES)]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 断言：没有抛出错误
    assert not errors, (
        f"并发写入期间发生 {len(errors)} 个错误：\n"
        + "\n".join(f"  {type(e).__name__}: {e}" for e in errors[:5])
    )

    # 断言：文件存在
    assert user_md_file.exists(), "并发写入后 USER.md 应存在"

    # 断言：文件内容是合法的 UTF-8（不损坏）
    try:
        final_content = user_md_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"并发写入后文件内容损坏（无法解码为 UTF-8）: {exc}")

    # 断言：文件不为空
    assert final_content.strip(), "并发写入后 USER.md 不应为空"

    # 断言：最终内容来自某一次写入（不是多次写入的混合）
    # atomic rename 保证任何时刻文件内容都是完整的某次写入
    final_is_valid = any(final_content == c for c in written_contents)
    assert final_is_valid, (
        f"最终文件内容应来自某一次写入（原子性保证）。\n"
        f"实际内容: {final_content!r}\n"
        f"期望之一: {written_contents[-1]!r}"
    )


# ---------------------------------------------------------------------------
# T073-2：同 session 多 turn，prefix cache 保护（冻结快照不变）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefix_cache_hit_rate_preserved(db_conn, tmp_path: Path) -> None:
    """同 session 多 turn 写入，format_for_system_prompt() 返回冻结副本不变（SC-011）。

    设计验证：
    - 模拟 LLM provider cache 的 token 序列一致性
    - format_for_system_prompt() 在 session 内永远返回初始快照
    - 即使经过多次 write_through（mid-session 更新），冻结副本不变
    - 只有 get_live_state() 会反映最新写入

    这保证了 LLM provider cache 命中率不降（prefix token 序列一致）。
    """
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)

    # session 开始时的原始内容
    original_content = "§ 姓名: Connor\n§ 时区: Asia/Shanghai\n§ 语言: zh-CN\n"
    user_md.write_text(original_content, encoding="utf-8")

    store = SnapshotStore(conn=db_conn)
    await store.load_snapshot(
        session_id="prefix-cache-test-session",
        files={"USER.md": user_md},
    )

    # 冻结快照初始值
    initial_snapshot = store.format_for_system_prompt()
    assert initial_snapshot["USER.md"] == original_content, (
        "初始快照应与磁盘内容一致"
    )

    # 模拟 5 次 mid-session 写入（turn 1 ~ turn 5）
    turns = [
        "§ 职业: AI 工程师\n",
        "§ 项目: OctoAgent\n",
        "§ 工作风格: 技术深入、工程化优先\n",
        "§ 沟通: 中文优先\n",
        "§ 值班: 非 996\n",
    ]

    for i, turn_content in enumerate(turns):
        await store.write_through(
            file_path=user_md,
            new_content=original_content + turn_content,
            live_state_key="USER.md",
        )

        # 核心断言：冻结快照不变（prefix cache 保护）
        snapshot_after = store.format_for_system_prompt()
        assert snapshot_after["USER.md"] == original_content, (
            f"Turn {i + 1} 后冻结快照不应改变（prefix cache 保护）：\n"
            f"  期望: {original_content!r}\n"
            f"  实际: {snapshot_after['USER.md']!r}"
        )

        # live state 应反映最新写入
        live_state = store.get_live_state("USER.md")
        assert live_state is not None, f"Turn {i + 1} 后 live state 不应为 None"
        assert turn_content.strip() in live_state, (
            f"Turn {i + 1} 后 live state 应包含最新写入内容"
        )

    # 最终验证：5 次写入后冻结快照仍是原始内容（prefix token 序列一致性保证）
    final_snapshot = store.format_for_system_prompt()
    assert final_snapshot["USER.md"] == original_content, (
        "5 次写入后冻结快照仍应是 session 开始时的原始内容\n"
        "（这保证了 LLM provider prefix cache 命中率不降）"
    )


# ---------------------------------------------------------------------------
# T073 补充：高并发下无孤立临时文件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_no_orphan_tmp_files(db_conn, user_md_file: Path) -> None:
    """高并发写入结束后，目录内无孤立的 .tmp 临时文件。

    atomic rename 正常完成时，临时文件应在 rename 后消失。
    如果有孤立临时文件，说明原子写入未正确清理。
    """
    store = SnapshotStore(conn=db_conn)
    await store.load_snapshot(
        session_id="orphan-tmp-test-session",
        files={"USER.md": user_md_file},
    )

    parent_dir = user_md_file.parent
    NUM_COROUTINES = 20

    async def write_one(index: int) -> None:
        content = f"§ 写入 {index}：内容\n"
        await store.write_through(
            file_path=user_md_file,
            new_content=content,
            live_state_key="USER.md",
        )

    tasks = [write_one(i) for i in range(NUM_COROUTINES)]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 检查目录内无孤立 .tmp 文件
    tmp_files = list(parent_dir.glob("*.tmp")) + list(parent_dir.glob("tmp*"))
    assert not tmp_files, (
        f"并发写入后发现 {len(tmp_files)} 个孤立临时文件：\n"
        + "\n".join(f"  {f}" for f in tmp_files[:10])
    )
