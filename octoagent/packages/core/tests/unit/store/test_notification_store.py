"""SqliteNotificationStore 单元测试 -- F116。

覆盖 dismiss / active 落盘 + 读取 + INSERT OR REPLACE 去重 + payload JSON round-trip。
"""

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.store.notification_store import SqliteNotificationStore
from octoagent.core.store.sqlite_init import init_db


@pytest_asyncio.fixture
async def store():
    """内存 SQLite + 已初始化 schema 的 notification store。"""
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    yield SqliteNotificationStore(conn)
    await conn.close()


@pytest.mark.asyncio
async def test_record_and_list_dismissed(store) -> None:
    await store.record_dismissal("notif-1", source="web")
    await store.record_dismissal("notif-2", source="telegram")
    dismissed = await store.list_dismissed()
    assert dismissed == {"notif-1", "notif-2"}


@pytest.mark.asyncio
async def test_dismissal_idempotent_replace(store) -> None:
    """同一 id 重复 dismiss → INSERT OR REPLACE 不报错，集合仍只一条。"""
    await store.record_dismissal("notif-1", source="web")
    await store.record_dismissal("notif-1", source="telegram")
    dismissed = await store.list_dismissed()
    assert dismissed == {"notif-1"}


@pytest.mark.asyncio
async def test_list_dismissed_empty(store) -> None:
    assert await store.list_dismissed() == set()


@pytest.mark.asyncio
async def test_record_and_list_active_roundtrip(store) -> None:
    """active 落盘 + 按 session 聚合 + payload JSON round-trip。"""
    await store.record_active(
        {
            "notification_id": "n-a",
            "session_id": "sess-1",
            "task_id": "task-1",
            "notification_type": "TASK_COMPLETED",
            "priority": "worker_completed",
            "payload": {"to_status": "SUCCEEDED", "中文": "值"},
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    await store.record_active(
        {
            "notification_id": "n-b",
            "session_id": "sess-1",
            "task_id": "task-2",
            "notification_type": "TASK_COMPLETED",
            "priority": "worker_completed",
            "payload": {},
            "created_at": "2026-01-01T00:01:00+00:00",
        }
    )
    result = await store.list_active_all()
    assert set(result.keys()) == {"sess-1"}
    entries = result["sess-1"]
    assert [e["notification_id"] for e in entries] == ["n-a", "n-b"]  # created_at 升序
    assert entries[0]["payload"] == {"to_status": "SUCCEEDED", "中文": "值"}
    # entry 形状与内存 _active_notifications 元素一致（不含 session_id 键）
    assert "session_id" not in entries[0]
    assert set(entries[0].keys()) == {
        "notification_id",
        "task_id",
        "notification_type",
        "priority",
        "payload",
        "created_at",
    }


@pytest.mark.asyncio
async def test_record_active_replace_dedup(store) -> None:
    """同一 notification_id 二次 record → PK 去重（list 只一条）。"""
    base = {
        "notification_id": "dup",
        "session_id": "s",
        "task_id": "t",
        "notification_type": "X",
        "priority": "worker_completed",
        "payload": {"v": 1},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    await store.record_active(base)
    await store.record_active({**base, "payload": {"v": 2}})
    result = await store.list_active_all()
    assert len(result["s"]) == 1
    assert result["s"][0]["payload"] == {"v": 2}


@pytest.mark.asyncio
async def test_delete_by_task_ids_clears_active_and_dismissals(store) -> None:
    """Codex M1：delete_by_task_ids 清 active + 关联 dismissals，不残留。"""
    await store.record_active(
        {
            "notification_id": "n-del",
            "session_id": "s",
            "task_id": "task-del",
            "notification_type": "X",
            "priority": "worker_completed",
            "payload": {"task_title": "敏感标题"},
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    await store.record_dismissal("n-del", source="web")
    # 另一无关 task 不应被误删
    await store.record_active(
        {
            "notification_id": "n-keep",
            "session_id": "s",
            "task_id": "task-keep",
            "notification_type": "X",
            "priority": "worker_completed",
            "payload": {},
            "created_at": "2026-01-01T00:00:01+00:00",
        }
    )

    deleted = await store.delete_by_task_ids(["task-del"])
    assert deleted == 1
    active = await store.list_active_all()
    assert "n-del" not in {e["notification_id"] for es in active.values() for e in es}
    assert "n-keep" in {e["notification_id"] for es in active.values() for e in es}
    # 关联 dismissal 也清掉（无 task_id 列，靠 active 行 id 反查）
    assert await store.list_dismissed() == set()


@pytest.mark.asyncio
async def test_delete_by_task_ids_empty_noop(store) -> None:
    assert await store.delete_by_task_ids([]) == 0


@pytest.mark.asyncio
async def test_active_grouped_by_session(store) -> None:
    for sess in ("s1", "s2"):
        await store.record_active(
            {
                "notification_id": f"n-{sess}",
                "session_id": sess,
                "task_id": "t",
                "notification_type": "X",
                "priority": "worker_completed",
                "payload": {},
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
    result = await store.list_active_all()
    assert set(result.keys()) == {"s1", "s2"}
