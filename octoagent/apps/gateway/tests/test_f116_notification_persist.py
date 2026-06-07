"""F116：NotificationService dismiss/active 跨重启持久化测试。

用真实文件 SQLite + 两个独立 NotificationService 实例（共享同一 DB 文件）
模拟「进程重启」：实例 A 写入 → 实例 B rehydrate → 状态不丢、已 dismiss 不重现。
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio

from octoagent.core.store.notification_store import SqliteNotificationStore
from octoagent.core.store.sqlite_init import init_db
from octoagent.gateway.services.notification import (
    NotificationPriority,
    NotificationService,
    generate_notification_id,
)


@pytest_asyncio.fixture
async def db_file(tmp_path):
    """文件型 SQLite 路径（跨连接共享，可模拟重启）。"""
    return str(tmp_path / "f116.db")


async def _open_store(db_file: str) -> tuple[aiosqlite.Connection, SqliteNotificationStore]:
    conn = await aiosqlite.connect(db_file)
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    return conn, SqliteNotificationStore(conn)


# ============================================================
# AC-1：dismiss 跨重启不重现
# ============================================================


@pytest.mark.asyncio
async def test_dismiss_survives_restart(db_file) -> None:
    conn1, store1 = await _open_store(db_file)
    svc1 = NotificationService(notification_store=store1)
    await svc1.dismiss("notif-keep", source="web")
    assert svc1.is_dismissed("notif-keep") is True
    await conn1.close()  # 模拟进程退出

    # 新进程：新实例 + 新连接 + rehydrate
    conn2, store2 = await _open_store(db_file)
    svc2 = NotificationService(notification_store=store2)
    assert svc2.is_dismissed("notif-keep") is False  # rehydrate 前内存为空
    await svc2.rehydrate()
    assert svc2.is_dismissed("notif-keep") is True  # rehydrate 后恢复
    await conn2.close()


# ============================================================
# AC-2：active 跨重启 + 已 dismiss 在 rehydrate 后被过滤
# ============================================================


@pytest.mark.asyncio
async def test_active_survives_restart(db_file) -> None:
    conn1, store1 = await _open_store(db_file)
    svc1 = NotificationService(notification_store=store1)
    session_id = "sess-1"
    await svc1.notify_task_state_change(
        task_id="task-1",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-1",
        session_id=session_id,
    )
    active_before = svc1.list_active(session_id)
    assert len(active_before) == 1
    await conn1.close()

    conn2, store2 = await _open_store(db_file)
    svc2 = NotificationService(notification_store=store2)
    await svc2.rehydrate()
    active_after = svc2.list_active(session_id)
    assert len(active_after) == 1
    assert active_after[0]["notification_id"] == active_before[0]["notification_id"]
    assert active_after[0]["payload"] == {"to_status": "SUCCEEDED"}
    await conn2.close()


@pytest.mark.asyncio
async def test_dismissed_filtered_after_rehydrate(db_file) -> None:
    """active 已落盘但用户已 dismiss → 重启 rehydrate 后 list_active 不返回（核心 bug 修复）。"""
    conn1, store1 = await _open_store(db_file)
    svc1 = NotificationService(notification_store=store1)
    session_id = "sess-1"
    await svc1.notify_task_state_change(
        task_id="task-1",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-1",
        session_id=session_id,
    )
    nid = svc1.list_active(session_id)[0]["notification_id"]
    await svc1.dismiss(nid, source="web")
    assert svc1.list_active(session_id) == []
    await conn1.close()

    # 重启：active + dismiss 都已落盘 → rehydrate 后仍不重现
    conn2, store2 = await _open_store(db_file)
    svc2 = NotificationService(notification_store=store2)
    await svc2.rehydrate()
    assert svc2.is_dismissed(nid) is True
    assert svc2.list_active(session_id) == [], "已 dismiss 的通知重启后不应重现"
    await conn2.close()


# ============================================================
# AC-3：quiet hours 过滤路径仍写 event，但不落 active
# ============================================================


@pytest.mark.asyncio
async def test_quiet_hours_still_writes_event_no_active(db_file) -> None:
    conn1, store1 = await _open_store(db_file)
    mock_event_store = MagicMock()
    mock_event_store.append_event_committed = AsyncMock()
    svc = NotificationService(notification_store=store1, event_store=mock_event_store)

    mock_snapshot = MagicMock()
    mock_snapshot.get_live_state.return_value = '- **active_hours**: "09:00-23:00"\n'
    svc.bind_snapshot_store(mock_snapshot)

    fixed_time = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    with patch("octoagent.gateway.services.notification.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_time
        await svc.notify_task_state_change(
            task_id="task-q",
            event_type="TASK_COMPLETED",
            payload={"to_status": "SUCCEEDED"},
            priority=NotificationPriority.LOW,
            state_transition_event_id="evt-q",
            session_id="sess-q",
        )

    # event 写了（filtered=True）
    assert mock_event_store.append_event_committed.call_count == 1
    assert mock_event_store.append_event_committed.call_args[0][0].payload["filtered"] is True
    # active 未落盘（filtered 路径在 _record_active 之前 return）
    assert await store1.list_active_all() == {}
    await conn1.close()


# ============================================================
# AC-4：notification_store=None 全降级不抛
# ============================================================


@pytest.mark.asyncio
async def test_store_none_degrades() -> None:
    svc = NotificationService()  # 无 store
    await svc.dismiss("x", source="web")  # 不抛
    assert svc.is_dismissed("x") is True
    await svc._record_active(
        session_id="s",
        notification_id="n",
        task_id="t",
        notification_type="X",
        priority=NotificationPriority.LOW,
        payload={},
    )
    assert len(svc.list_active("s")) == 1
    await svc.rehydrate()  # 无 store → no-op 不抛


@pytest.mark.asyncio
async def test_rehydrate_failure_degrades(db_file) -> None:
    """store.list_dismissed 抛异常 → rehydrate 降级为空，不阻断。"""
    conn1, store1 = await _open_store(db_file)
    svc = NotificationService(notification_store=store1)
    store1.list_dismissed = AsyncMock(side_effect=RuntimeError("boom"))
    await svc.rehydrate()  # 不抛
    assert svc.is_dismissed("anything") is False
    await conn1.close()


# ============================================================
# Codex H1：dismiss 返回 persisted 真实状态（不谎报 durable）
# ============================================================


@pytest.mark.asyncio
async def test_dismiss_returns_persisted_status(db_file) -> None:
    conn1, store1 = await _open_store(db_file)
    svc = NotificationService(notification_store=store1)
    assert await svc.dismiss("n1", source="web") is True  # 落盘成功
    await conn1.close()


@pytest.mark.asyncio
async def test_dismiss_returns_false_without_store() -> None:
    svc = NotificationService()  # 无持久层
    assert await svc.dismiss("n1") is False  # 仅内存，不 durable
    assert svc.is_dismissed("n1") is True  # 但内存语义仍生效


@pytest.mark.asyncio
async def test_dismiss_returns_false_on_persist_error(db_file) -> None:
    conn1, store1 = await _open_store(db_file)
    svc = NotificationService(notification_store=store1)
    store1.record_dismissal = AsyncMock(side_effect=RuntimeError("db locked"))
    assert await svc.dismiss("n1", source="web") is False  # 落盘失败 → False
    assert svc.is_dismissed("n1") is True  # 内存仍生效（降级不 crash）
    await conn1.close()


# ============================================================
# Codex H2：重启后不重复推送已派发/已 dismiss 的状态变更通知
# ============================================================


class _RecordingChannel:
    def __init__(self) -> None:
        self.notify_calls: list[str] = []

    @property
    def channel_name(self) -> str:
        return "rec"

    async def notify(self, task_id, event_type, payload) -> bool:
        self.notify_calls.append(payload.get("notification_id", ""))
        return True

    async def send_approval_request(self, task_id, tool_name, ask_reason, payload) -> bool:
        return False


@pytest.mark.asyncio
async def test_no_repush_after_restart_for_sent_notification(db_file) -> None:
    """已派发的状态变更通知，重启 rehydrate 后再次 notify 不重复推 channel。"""
    conn1, store1 = await _open_store(db_file)
    svc1 = NotificationService(notification_store=store1)
    ch1 = _RecordingChannel()
    svc1.register_channel(ch1)
    kwargs = dict(
        task_id="task-1",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-1",
        session_id="sess-1",
    )
    await svc1.notify_task_state_change(**kwargs)
    assert len(ch1.notify_calls) == 1
    await conn1.close()

    # 重启：新实例 rehydrate（seed _notified_set）后同一 task/event 再次 notify
    conn2, store2 = await _open_store(db_file)
    svc2 = NotificationService(notification_store=store2)
    ch2 = _RecordingChannel()
    svc2.register_channel(ch2)
    await svc2.rehydrate()
    await svc2.notify_task_state_change(**kwargs)
    assert ch2.notify_calls == [], "重启后重放同一通知不应再次推送"
    # active 也不应翻倍
    assert len(svc2.list_active("sess-1")) == 1
    await conn2.close()


@pytest.mark.asyncio
async def test_dismissed_notification_never_dispatched(db_file) -> None:
    """已 dismiss 的状态变更通知再次 notify → guard 拦截，不推 channel。"""
    conn1, store1 = await _open_store(db_file)
    svc = NotificationService(notification_store=store1)
    ch = _RecordingChannel()
    svc.register_channel(ch)
    nid = generate_notification_id("task-2", "TASK_COMPLETED", "evt-2")
    await svc.dismiss(nid, source="web")
    await svc.notify_task_state_change(
        task_id="task-2",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-2",
        session_id="sess-2",
    )
    assert ch.notify_calls == [], "已 dismiss 的通知不应被派发"
    assert svc.list_active("sess-2") == []
    await conn1.close()


# ============================================================
# Codex M1：删除 task 后重启不复活 stale 通知
# ============================================================


@pytest.mark.asyncio
async def test_deleted_task_not_resurrected_after_restart(db_file) -> None:
    conn1, store1 = await _open_store(db_file)
    svc1 = NotificationService(notification_store=store1)
    await svc1.notify_task_state_change(
        task_id="task-del",
        event_type="TASK_COMPLETED",
        payload={"to_status": "SUCCEEDED", "task_title": "敏感"},
        priority=NotificationPriority.LOW,
        state_transition_event_id="evt-del",
        session_id="sess-del",
    )
    nid = svc1.list_active("sess-del")[0]["notification_id"]
    await svc1.dismiss(nid, source="web")
    # 模拟 session/task 级联删除清理该 task 的通知
    await store1.delete_by_task_ids(["task-del"])
    await conn1.commit()
    await conn1.close()

    conn2, store2 = await _open_store(db_file)
    svc2 = NotificationService(notification_store=store2)
    await svc2.rehydrate()
    assert svc2.list_active("sess-del") == [], "已删除 task 的通知不应在重启后复活"
    assert svc2.is_dismissed(nid) is False, "关联 dismissal 也应被清理"
    await conn2.close()
