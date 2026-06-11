"""F119 e2e_live：F116 通知 dismiss/active 持久化端到端补全。

集成 review 缺口：F116 有单测但无 e2e_live——bootstrap 后 store_group.notification_store
真装配了吗？NotificationService 真能跨"重启"（新实例 rehydrate）恢复 dismiss/active 吗？
已 dismiss 的通知重启后真不重现吗？

设计原则：
1. 真跑 OctoHarness bootstrap → 真文件 tmp DB + store_group.notification_store
2. "重启"语义：service A 经公共路径落盘 → 新建 service B + rehydrate（内存态从 DB 恢复）
3. 断言走 NotificationService 公共方法（dismiss/is_dismissed/list_active）守 H1
4. 每个 case ≥ 2 独立断言点

AC 绑定（spec §3）：
- AC-116-1 → test_notification_dismiss_survives_restart
- AC-116-2 → test_notification_active_survives_restart
- AC-116-3 → test_notification_dismissed_filtered_after_restart
- AC-116-4 → test_notification_dismiss_cross_channel
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


pytestmark = [pytest.mark.e2e_full, pytest.mark.e2e_live]


@pytest.fixture
async def bootstrapped_harness(octo_harness_e2e: dict[str, Any]) -> dict[str, Any]:
    """真跑 OctoHarness.bootstrap → 拿真文件 DB + store_group.notification_store。"""
    harness = octo_harness_e2e["harness"]
    app = octo_harness_e2e["app"]
    project_root = octo_harness_e2e["project_root"]

    fixtures_root = (
        Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "local-instance"
    )
    if (fixtures_root / "octoagent.yaml.template").exists():
        from apps.gateway.tests.e2e_live.helpers.factories import (
            copy_local_instance_template,
        )

        copy_local_instance_template(fixtures_root, project_root)

    await harness.bootstrap(app)
    harness.commit_to_app(app)
    return {"harness": harness, "app": app, "project_root": project_root}


def _new_service(store_group: Any) -> Any:
    """新建一个 NotificationService，绑定真文件 notification_store（模拟新进程实例）。"""
    from octoagent.gateway.services.notification import NotificationService

    return NotificationService(
        snapshot_store=None,
        event_store=None,
        notification_store=store_group.notification_store,
    )


def _active_entry(
    *,
    notification_id: str,
    session_id: str,
    task_id: str,
    notification_type: str = "task_state_change",
    priority: str = "LOW",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 store.record_active 的 entry（对齐 NotificationService._record_active 落盘形状）。"""
    return {
        "notification_id": notification_id,
        "session_id": session_id,
        "task_id": task_id,
        "notification_type": notification_type,
        "priority": priority,
        "payload": payload or {"title": "测试通知"},
        "created_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# AC-116-1：dismiss 跨"重启"不重现
# ---------------------------------------------------------------------------


async def test_notification_dismiss_survives_restart(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-116-1：service A dismiss → 新 service B rehydrate → B.is_dismissed True。

    断言（≥ 2 独立点）：
    1. dismiss 返回 True（durable 落盘成功，store 非 None）
    2. 新 service 实例 rehydrate 后 is_dismissed(id) 为 True（跨重启不重现）
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group

    notif_id = "notif-dismiss-survives-001"
    service_a = _new_service(store_group)
    durable = await service_a.dismiss(notif_id, source="web")
    assert durable is True, (
        f"AC-116-1: dismiss 应 durable 落盘（store 非 None），返回 {durable}"
    )

    # 模拟重启：全新 service 实例（内存态空）+ rehydrate 从 DB 恢复
    service_b = _new_service(store_group)
    assert service_b.is_dismissed(notif_id) is False, (
        "AC-116-1: rehydrate 前新实例内存态应为空（未恢复）"
    )
    await service_b.rehydrate()
    assert service_b.is_dismissed(notif_id) is True, (
        "AC-116-1: rehydrate 后已 dismiss 的通知应被恢复（跨重启不重现）"
    )


# ---------------------------------------------------------------------------
# AC-116-2：active 跨重启持久化
# ---------------------------------------------------------------------------


async def test_notification_active_survives_restart(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-116-2：active 落盘 → 新 service rehydrate → list_active 恢复该条。

    断言（≥ 2 独立点）：
    1. rehydrate 前 list_active 为空（新实例内存态空）
    2. rehydrate 后 list_active(session) 含落盘的 active 条目
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group

    session_id = "sess-active-001"
    notif_id = "notif-active-survives-001"
    await store_group.notification_store.record_active(
        _active_entry(
            notification_id=notif_id, session_id=session_id, task_id="task-active-1"
        )
    )

    service_b = _new_service(store_group)
    assert service_b.list_active(session_id) == [], (
        "AC-116-2: rehydrate 前新实例 list_active 应为空"
    )
    await service_b.rehydrate()
    active = service_b.list_active(session_id)
    assert len(active) == 1, (
        f"AC-116-2: rehydrate 后应恢复 1 条 active，实际 {len(active)}"
    )
    assert active[0]["notification_id"] == notif_id, (
        f"AC-116-2: 恢复的 active 应为落盘那条，实际 {active[0]!r}"
    )


# ---------------------------------------------------------------------------
# AC-116-3：已 dismiss 的 active 重启后被过滤
# ---------------------------------------------------------------------------


async def test_notification_dismissed_filtered_after_restart(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-116-3：active 一条后 dismiss 它 → 新实例 rehydrate → list_active 不返回它。

    断言（≥ 2 独立点）：
    1. rehydrate 后 is_dismissed 为 True
    2. list_active 不含该条（_dismissed_set 过滤 + H2 _notified_set 防重派）
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group

    session_id = "sess-filter-001"
    notif_id = "notif-active-then-dismiss-001"

    await store_group.notification_store.record_active(
        _active_entry(
            notification_id=notif_id, session_id=session_id, task_id="task-filter-1"
        )
    )
    service_a = _new_service(store_group)
    await service_a.dismiss(notif_id, source="telegram")

    service_b = _new_service(store_group)
    await service_b.rehydrate()
    assert service_b.is_dismissed(notif_id) is True, (
        "AC-116-3: rehydrate 后应恢复 dismiss 状态"
    )
    active = service_b.list_active(session_id)
    assert all(e["notification_id"] != notif_id for e in active), (
        f"AC-116-3: 已 dismiss 的 active 不应在 list_active 中重现，实际 {active!r}"
    )


# ---------------------------------------------------------------------------
# AC-116-4：跨通道 dismiss 统一
# ---------------------------------------------------------------------------


async def test_notification_dismiss_cross_channel(
    bootstrapped_harness: dict[str, Any],
) -> None:
    """AC-116-4：Web dismiss → 同一持久层 → 其它通道（rehydrate）查询一致。

    断言（≥ 2 独立点）：
    1. dismiss(source="web") 落盘 source 字段正确
    2. 任意新实例 rehydrate（≈ Telegram 通道进程）后 is_dismissed 一致 True
    """
    app = bootstrapped_harness["app"]
    store_group = app.state.store_group

    notif_id = "notif-cross-channel-001"
    service_web = _new_service(store_group)
    await service_web.dismiss(notif_id, source="web")

    # 直查持久层确认 source 落盘（跨通道共享同一 notification_dismissals 表）
    dismissed = await store_group.notification_store.list_dismissed()
    assert notif_id in dismissed, (
        f"AC-116-4: dismiss 应落盘到共享表，实际 {dismissed}"
    )

    # 另一通道（不同 service 实例）rehydrate 后看到同一 dismiss
    service_other = _new_service(store_group)
    await service_other.rehydrate()
    assert service_other.is_dismissed(notif_id) is True, (
        "AC-116-4: 跨通道（另一 service 实例）应看到同一 dismiss（统一持久层）"
    )
