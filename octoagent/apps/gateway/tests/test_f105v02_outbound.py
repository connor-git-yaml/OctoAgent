"""F105 v0.2: 出站路由接线测试（FR-D2 通知渠道 eligibility + CONFIGURED 消费）。

Phase B 落 Slack 部分（US-2 AC-7：DM last-route + 频道-only 不投递）；
Phase D 追加 CONFIGURED 幂等 / H1 v02 / resolver v2 交互用例。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.notification import (
    DiscordNotificationChannel,
    SlackNotificationChannel,
)


class _SendRecorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, conversation_id: str, text: str) -> None:
        self.sent.append((conversation_id, text))


def _payload() -> dict[str, object]:
    return {
        "task_title": "测试任务",
        "to_status": "SUCCEEDED",
        "duration_ms": 1200,
        "notification_id": "abc123",
    }


@pytest.mark.asyncio
async def test_slack_notification_resolves_dm_last_route(tmp_path: Path) -> None:
    """US-2 AC-7 正向：DM 类 runtime binding（conversation_type=im）可作通知目标，
    last-route 取活跃最新。"""
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    binding_store = store_group.conversation_binding_store
    await binding_store.upsert_runtime_binding(
        "slack", "D_OLD", scope_id="chat:slack:D_OLD",
        metadata={"conversation_type": "im"},
    )
    await binding_store.upsert_runtime_binding(
        "slack", "D_NEW", scope_id="chat:slack:D_NEW",
        metadata={"conversation_type": "im"},
    )

    send = _SendRecorder()
    channel = SlackNotificationChannel(send_fn=send, binding_store=binding_store)
    assert channel.channel_name == "slack"
    ok = await channel.notify("task-1", "TASK_STATE_CHANGED", _payload())
    assert ok is True
    assert [c for c, _ in send.sent] == ["D_NEW"]  # last_active 最新的 DM
    text = send.sent[0][1]
    assert "测试任务" in text and "已完成" in text
    await store_group.close()


@pytest.mark.asyncio
async def test_channel_only_runtime_binding_not_notified(tmp_path: Path) -> None:
    """US-2 AC-7 反向（CODEX-H2）：仅存在多人频道 runtime binding（无 DM、
    无 configured）→ 不投递（频道发言不构成通知同意）。"""
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    binding_store = store_group.conversation_binding_store
    await binding_store.upsert_runtime_binding(
        "slack", "C_PUBLIC", scope_id="chat:slack:C_PUBLIC",
        metadata={"conversation_type": "channel"},
    )

    send = _SendRecorder()
    channel = SlackNotificationChannel(send_fn=send, binding_store=binding_store)
    ok = await channel.notify("task-1", "TASK_STATE_CHANGED", _payload())
    assert ok is False
    assert send.sent == []
    await store_group.close()


@pytest.mark.asyncio
async def test_notification_degrades_without_store_or_send_fn(tmp_path: Path) -> None:
    """Constitution #6：binding_store/send_fn 缺失或异常 → False 降级不抛。"""
    send = _SendRecorder()
    assert (
        await SlackNotificationChannel(send_fn=send, binding_store=None).notify(
            "t", "E", _payload()
        )
        is False
    )

    class _BrokenStore:
        async def list_by_platform(self, platform: str):
            raise RuntimeError("db down")

    assert (
        await SlackNotificationChannel(
            send_fn=send, binding_store=_BrokenStore()
        ).notify("t", "E", _payload())
        is False
    )
    assert send.sent == []


@pytest.mark.asyncio
async def test_send_approval_request_unsupported(tmp_path: Path) -> None:
    """spec §2.2：无交互组件 → 审批推送恒 False（审批走 Web/Telegram）。"""
    channel = DiscordNotificationChannel(send_fn=_SendRecorder(), binding_store=None)
    assert (
        await channel.send_approval_request("t", "tool", "reason", {}) is False
    )
