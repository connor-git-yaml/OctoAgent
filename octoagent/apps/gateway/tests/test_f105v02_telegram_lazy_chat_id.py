"""F105 v0.2 Phase E: Telegram 通知 chat_id 惰性解析（L1 修复，FR-E1/E2/E3）。

覆盖 US-5 AC-1（配对后即刻生效，无需重建实例/重启）/ AC-2（resolver
None/异常降级）；AC-3（既有静态构造 0 修改全绿）由 test_notification.py /
test_f101_notification.py 机械校验。
"""

from __future__ import annotations

import pytest
from octoagent.gateway.channels.telegram_adapter import TelegramChannelAdapter
from octoagent.gateway.services.notification import TelegramNotificationChannel


class _SendRecorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, chat_id: str, text: str, reply_markup=None) -> None:
        self.sent.append((chat_id, text))


def _payload() -> dict[str, object]:
    return {"task_title": "任务", "to_status": "SUCCEEDED"}


@pytest.mark.asyncio
async def test_resolver_lazy_after_pairing() -> None:
    """US-5 AC-1：构造时未配对（resolver→None），配对后同一实例通知即可达。"""
    state = {"chat_id": None}
    send = _SendRecorder()
    channel = TelegramNotificationChannel(
        send_message_fn=send,
        chat_id_resolver=lambda: state["chat_id"],
    )

    assert await channel.notify("t1", "E", _payload()) is False  # 未配对静默
    state["chat_id"] = "999"  # 模拟运行中完成配对
    assert await channel.notify("t2", "E", _payload()) is True
    assert send.sent[0][0] == "999"

    # send_approval_request 同路径
    assert await channel.send_approval_request("t3", "tool", "原因", {}) is True
    assert send.sent[1][0] == "999"


@pytest.mark.asyncio
async def test_resolver_none_or_raises_degrades() -> None:
    """US-5 AC-2：resolver 持续 None / 抛异常 → False 降级（与 baseline
    chat_id=None 行为一致，Constitution #6）。"""
    send = _SendRecorder()

    none_channel = TelegramNotificationChannel(
        send_message_fn=send, chat_id_resolver=lambda: None
    )
    assert await none_channel.notify("t", "E", _payload()) is False

    def _broken() -> str | None:
        raise RuntimeError("state store 损坏")

    broken_channel = TelegramNotificationChannel(
        send_message_fn=send, chat_id_resolver=_broken
    )
    assert await broken_channel.notify("t", "E", _payload()) is False
    assert await broken_channel.send_approval_request("t", "x", "y", {}) is False
    assert send.sent == []


@pytest.mark.asyncio
async def test_static_chat_id_still_works() -> None:
    """兼容形态：静态 chat_id（无 resolver）行为与 baseline 等价。"""
    send = _SendRecorder()
    channel = TelegramNotificationChannel(send_message_fn=send, chat_id="123")
    assert await channel.notify("t", "E", _payload()) is True
    assert send.sent[0][0] == "123"


class _FakeApprovedUser:
    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id


class _FakeStateStore:
    def __init__(self) -> None:
        self.approved: _FakeApprovedUser | None = None

    def first_approved_user(self) -> _FakeApprovedUser | None:
        return self.approved


class _FakeBotClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((str(chat_id), text))


class _FakeServiceShell:
    """携带 _bot_client/_state_store 的最小 service 壳（adapter 取属性用）。"""

    def __init__(self, bot_client, state_store) -> None:
        self._bot_client = bot_client
        self._state_store = state_store


@pytest.mark.asyncio
async def test_adapter_channel_resolves_per_notify() -> None:
    """FR-E2 集成形态：adapter 构造的渠道经 state_store 每次现查——
    bootstrap 时无 approved user 不再导致永久静默（L1 本体修复）。"""
    state_store = _FakeStateStore()
    bot = _FakeBotClient()
    adapter = TelegramChannelAdapter(_FakeServiceShell(bot, state_store))
    channel = adapter.notification_channel()
    assert channel is not None

    assert await channel.notify("t1", "E", _payload()) is False  # 启动时未配对
    state_store.approved = _FakeApprovedUser("777")  # 运行中配对
    assert await channel.notify("t2", "E", _payload()) is True
    assert bot.sent[0][0] == "777"


def test_adapter_no_bot_client_returns_none() -> None:
    """bot_client None → 不注册渠道（baseline 语义不变）。"""
    adapter = TelegramChannelAdapter(_FakeServiceShell(None, _FakeStateStore()))
    assert adapter.notification_channel() is None
