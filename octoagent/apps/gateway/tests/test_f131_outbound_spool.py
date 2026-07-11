"""F131 Telegram 出站补偿 spool（G3 主缺口修复）。

覆盖 AC-5~11：send 失败入队 / drain 重试成功清账 / 重启不丢 / 首发成功不入队 /
重试退避+上限 / 降级不崩。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import os

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.telegram import (
    _SPOOL_MAX_ATTEMPTS,
    TelegramGatewayService,
)


def _write_config(project_root: Path) -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-07-06",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(enabled=True, mode="polling")
            ),
        ),
        project_root,
    )


class _OKBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": str(chat_id), "text": text, **kwargs})
        return SimpleNamespace(message_id=555)


class _FailBot:
    def __init__(self) -> None:
        self.attempts = 0

    async def send_message(self, chat_id, text, **kwargs):
        self.attempts += 1
        raise RuntimeError("telegram transport broken")


class _FlipBot:
    """先失败 fail_count 次，之后成功（模拟网络恢复后 drain 补发成功）。"""

    def __init__(self, fail_count: int) -> None:
        self.fail_count = fail_count
        self.calls = 0
        self.sent: list[dict[str, object]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise RuntimeError("transient failure")
        self.sent.append({"chat_id": str(chat_id), "text": text, **kwargs})
        return SimpleNamespace(message_id=777)


# ---------------------------------------------------------------------------
# store 层直接测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spool_store_enqueue_list_mark(tmp_path: Path) -> None:
    """store 基本契约：enqueue → list_due 取出 → mark_sent 删行 / count_pending。"""
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    store = store_group.telegram_outbound_spool_store
    sid = await store.enqueue(
        chat_id="123", text="hi", created_at=100.0, next_retry_at=100.0
    )
    assert sid > 0
    assert await store.count_pending() == 1
    due = await store.list_due(now=100.0)
    assert len(due) == 1 and due[0].chat_id == "123" and due[0].text == "hi"
    # 未到期不取
    await store.enqueue(chat_id="9", text="later", created_at=100.0, next_retry_at=999.0)
    assert len(await store.list_due(now=100.0)) == 1
    await store.mark_sent(sid)
    assert await store.count_pending() == 1  # 只剩 later 那条
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-5：send 失败入队
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_failure_enqueues_to_spool(tmp_path: Path) -> None:
    """AC-5：_send_or_spool 发送失败 → 消息落 spool（含 chat_id/text/reply/thread）。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        bot_client=_FailBot(),
    )
    result = await service._send_or_spool(
        {"chat_id": "42", "reply_to_message_id": "7", "message_thread_id": "3"},
        "结果文本",
        task_id="task-x",
    )
    assert result is None  # 发送失败返回 None
    due = await store_group.telegram_outbound_spool_store.list_due(now=1e12)
    assert len(due) == 1
    item = due[0]
    assert item.chat_id == "42"
    assert item.text == "结果文本"
    assert item.reply_to_message_id == "7"
    assert item.message_thread_id == "3"
    assert item.task_id == "task-x"
    assert item.status == "pending"
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-6：drain 重试成功清账
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spool_drain_retries_and_clears_on_success(tmp_path: Path) -> None:
    """AC-6：入队后网络恢复，drain 补发成功 → 从表删除（不重复发）。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    # 先用失败 bot 入队
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        bot_client=_FailBot(),
    )
    await service._send_or_spool({"chat_id": "42"}, "补发我", task_id="t")
    assert await store_group.telegram_outbound_spool_store.count_pending() == 1

    # 换成功 bot，drain 补发
    ok_bot = _OKBot()
    service._bot_client = ok_bot
    await service._drain_outbound_spool()
    assert len(ok_bot.sent) == 1 and ok_bot.sent[0]["text"] == "补发我"
    assert await store_group.telegram_outbound_spool_store.count_pending() == 0
    # 再次 drain 不重复发（已删）
    await service._drain_outbound_spool()
    assert len(ok_bot.sent) == 1
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-7：重启不丢
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spool_survives_process_restart(tmp_path: Path) -> None:
    """AC-7：spool 写盘后新建 store 实例（模拟进程重启）→ 待发消息仍在，可 drain。"""
    _write_config(tmp_path)
    db_path = str(tmp_path / "g.db")
    artifacts = str(tmp_path / "artifacts")

    store_group_1 = await create_store_group(db_path, artifacts)
    service_1 = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group_1,
        sse_hub=SSEHub(),
        bot_client=_FailBot(),
    )
    await service_1._send_or_spool({"chat_id": "99"}, "重启前未送达", task_id="t")
    assert await store_group_1.telegram_outbound_spool_store.count_pending() == 1
    await store_group_1.close()  # 模拟进程退出

    # 新进程：新 store 实例读同一 db
    store_group_2 = await create_store_group(db_path, artifacts)
    assert await store_group_2.telegram_outbound_spool_store.count_pending() == 1
    ok_bot = _OKBot()
    service_2 = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group_2,
        sse_hub=SSEHub(),
        bot_client=ok_bot,
    )
    await service_2._drain_outbound_spool()
    assert ok_bot.sent[0]["text"] == "重启前未送达"
    assert await store_group_2.telegram_outbound_spool_store.count_pending() == 0
    await store_group_2.close()


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="时序/性能断言在 CI 共享 runner 不稳定——F137 首跑 triage 记欠账，F142 quarantine/根因治理；本地照跑",
)
@pytest.mark.asyncio
async def test_startup_drains_spool(tmp_path: Path) -> None:
    """AC-7 补充：startup() 重启后后台 drain 首轮立即补发（Codex P2：非阻塞，
    走独立 _spool_drain_loop 首轮，不在 startup 主路径同步等待）。"""
    import asyncio as _aio

    from octoagent.provider.dx.telegram_pairing import TelegramStateStore

    _write_config(tmp_path)
    db_path = str(tmp_path / "g.db")
    artifacts = str(tmp_path / "artifacts")
    store_group_1 = await create_store_group(db_path, artifacts)
    service_1 = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group_1, sse_hub=SSEHub(),
        bot_client=_FailBot(),
    )
    await service_1._send_or_spool({"chat_id": "1"}, "startup补发", task_id="t")
    await store_group_1.close()

    store_group_2 = await create_store_group(db_path, artifacts)
    ok_bot = _OKBot()
    service_2 = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group_2, sse_hub=SSEHub(),
        state_store=TelegramStateStore(tmp_path), bot_client=ok_bot,
    )
    await service_2.startup()  # 起后台 drain loop（首轮立即 drain）+ polling task
    # 让后台 drain 首轮跑完（非阻塞，故需 yield 给事件循环）
    await _aio.sleep(0.05)
    await service_2.shutdown()
    assert ok_bot.sent and ok_bot.sent[0]["text"] == "startup补发"
    await store_group_2.close()


# ---------------------------------------------------------------------------
# AC-8：首发成功不入队
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_send_does_not_spool(tmp_path: Path) -> None:
    """AC-8：send_message 首发成功 → spool 表为空（不引入无谓写盘）。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    ok_bot = _OKBot()
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        bot_client=ok_bot,
    )
    result = await service._send_or_spool({"chat_id": "42"}, "首发成功", task_id="t")
    assert result is not None and result.message_id == 555
    assert len(ok_bot.sent) == 1
    assert await store_group.telegram_outbound_spool_store.count_pending() == 0
    await store_group.close()


@pytest.mark.asyncio
async def test_send_or_spool_defaults_disable_notification_true(tmp_path: Path) -> None:
    """AC-11 回归锁：_send_or_spool 默认静音（disable_notification=True）——对齐
    baseline notify_task_result 省略参数用 client 默认 True 的行为，不被改成有声。
    并验证 spool 落盘也保留该 flag。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    ok_bot = _OKBot()
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        bot_client=ok_bot,
    )
    # 成功路径：默认静音
    await service._send_or_spool({"chat_id": "1"}, "静音结果")
    assert ok_bot.sent[0]["disable_notification"] is True

    # 失败路径 spool 落盘也保留静音 flag
    service._bot_client = _FailBot()
    await service._send_or_spool({"chat_id": "2"}, "静音入队")
    due = await store_group.telegram_outbound_spool_store.list_due(now=1e12)
    assert due[0].disable_notification is True
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-9：重试退避 + 上限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spool_retry_backoff_and_max_attempts(tmp_path: Path) -> None:
    """AC-9：drain 失败 → 记 attempts + 退避延后；超上限标 failed（不再 drain）。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    store = store_group.telegram_outbound_spool_store
    fail_bot = _FailBot()
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        bot_client=fail_bot,
    )
    # 入队一条（next_retry_at=0 立即到期）
    await store.enqueue(chat_id="42", text="重试我", created_at=0.0, next_retry_at=0.0)

    # 反复 drain 直至该条被标 failed；每轮把 next_retry_at 重置为 0 以强制立即再取
    import octoagent.core.store.telegram_outbound_spool_store as spool_mod  # noqa: F401

    for _ in range(_SPOOL_MAX_ATTEMPTS + 2):
        # 强制到期
        await store._conn.execute(
            "UPDATE telegram_outbound_spool SET next_retry_at=0 WHERE status='pending'"
        )
        await store._conn.commit()
        await service._drain_outbound_spool()

    # 已无 pending（被标 failed），但行仍在（保留供诊断）
    assert await store.count_pending() == 0
    cursor = await store._conn.execute(
        "SELECT status, attempts FROM telegram_outbound_spool"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "failed"
    assert int(row[1]) >= _SPOOL_MAX_ATTEMPTS
    await store_group.close()


@pytest.mark.asyncio
async def test_spool_retry_increments_attempts_before_max(tmp_path: Path) -> None:
    """AC-9 中间态：未到上限的失败 → status 保持 pending + attempts 递增 + 退避延后。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    store = store_group.telegram_outbound_spool_store
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        bot_client=_FailBot(),
    )
    await store.enqueue(chat_id="1", text="x", created_at=0.0, next_retry_at=0.0)
    await service._drain_outbound_spool()
    cursor = await store._conn.execute(
        "SELECT status, attempts, next_retry_at FROM telegram_outbound_spool"
    )
    row = await cursor.fetchone()
    assert row[0] == "pending"
    assert int(row[1]) == 1
    assert float(row[2]) > 0  # 退避延后（不再立即到期）
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-10：降级不崩
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spool_failure_degrades_gracefully(tmp_path: Path) -> None:
    """AC-10：spool store 缺失 → _send_or_spool 失败不崩（只 log drop）；
    drain 无 store / 无 bot → 静默返回不抛。"""
    _write_config(tmp_path)
    # store_group 无 telegram_outbound_spool_store 属性（用 SimpleNamespace 模拟缺失）
    fake_stores = SimpleNamespace(telegram_outbound_spool_store=None)
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=fake_stores, sse_hub=SSEHub(),
        bot_client=_FailBot(),
    )
    # 不抛（缺 spool store → drop + log）
    result = await service._send_or_spool({"chat_id": "1"}, "无处可存", task_id="t")
    assert result is None
    # drain 无 store 静默返回
    await service._drain_outbound_spool()

    # drain 时 list_due 抛异常也不崩
    class _BrokenSpool:
        async def list_due(self, now, **kwargs):
            raise RuntimeError("db down")

    service._stores = SimpleNamespace(telegram_outbound_spool_store=_BrokenSpool())
    await service._drain_outbound_spool()  # 不抛


@pytest.mark.asyncio
async def test_drain_reregisters_reply_thread_root(tmp_path: Path) -> None:
    """Codex P2：群聊 reply-thread 任务结果首发失败入队时保存 root id；
    drain 补发成功后登记新 message_id → root 映射（用户回复补发消息落回原线程）。"""
    _write_config(tmp_path)
    from octoagent.provider.dx.telegram_pairing import TelegramStateStore

    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    state_store = TelegramStateStore(tmp_path)
    # 首发失败 → 入队（带 reply_thread_root_id）
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        state_store=state_store, bot_client=_FailBot(),
    )
    await service._send_or_spool(
        {"chat_id": "-100", "reply_thread_root_id": "88"}, "群聊补发", task_id="t"
    )
    due = await store_group.telegram_outbound_spool_store.list_due(now=1e12)
    assert due[0].reply_thread_root_id == "88"

    # 换成功 bot drain（send_message 返回 message_id=555）
    service._bot_client = _OKBot()
    await service._drain_outbound_spool()
    # 补发消息 message_id=555 → root=88 映射已登记，用户回复 555 能解析回 88
    resolved = state_store.resolve_reply_thread_root(chat_id="-100", message_id="555")
    assert resolved == "88"
    await store_group.close()


@pytest.mark.asyncio
async def test_webhook_mode_background_drain_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1：webhook 模式起独立后台周期 drain 任务（不依赖 inbound、不占请求路径）。
    startup 拉起 _spool_drain_loop → 周期 drain 待发消息。"""
    import os

    from octoagent.gateway.services.config.config_schema import (
        ChannelsConfig,
        OctoAgentConfig,
        TelegramChannelConfig,
    )
    from octoagent.gateway.services.config.config_wizard import save_config
    from octoagent.provider.dx.telegram_pairing import TelegramStateStore

    save_config(
        OctoAgentConfig(
            updated_at="2026-07-06",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode="webhook",
                    webhook_url="https://example.com/api/telegram/webhook",
                )
            ),
        ),
        tmp_path,
    )
    os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
    try:
        store_group = await create_store_group(
            str(tmp_path / "g.db"), str(tmp_path / "artifacts")
        )
        # 用失败 bot 预置一条待发（startup 首轮 drain 发不出去，留给周期 loop）
        await store_group.telegram_outbound_spool_store.enqueue(
            chat_id="1", text="后台补发", created_at=0.0, next_retry_at=0.0
        )
        ok_bot = _OKBot()
        service = TelegramGatewayService(
            project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
            state_store=TelegramStateStore(tmp_path), bot_client=ok_bot,
        )
        service._spool_drain_interval_s = 0.02  # 加速周期
        await service.startup()  # webhook 模式 → 起后台 drain loop
        assert service._spool_drain_task is not None
        # 等后台 loop 至少跑一轮 drain
        import asyncio as _aio

        await _aio.sleep(0.1)
        await service.shutdown()
        assert any(m["text"] == "后台补发" for m in ok_bot.sent)
        assert await store_group.telegram_outbound_spool_store.count_pending() == 0
        await store_group.close()
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)


def test_backoff_no_overflow_on_long_streak() -> None:
    """Codex P2：持续断网/409 → failure_streak 巨大也不 OverflowError（exp 封顶）。"""
    from octoagent.gateway.services.telegram import (
        _POLL_BACKOFF_MAX_S,
        _SPOOL_RETRY_MAX_S,
        _compute_poll_backoff,
        _compute_spool_retry_delay,
    )

    # 远超 float 幂溢出阈值（~1025）的 streak 不抛，且封顶到 max
    for big in (2000, 100_000, 10**9):
        d = _compute_poll_backoff(big)
        assert d <= _POLL_BACKOFF_MAX_S * 1.2
        assert d >= 0.0
        s = _compute_spool_retry_delay(big)
        assert s <= _SPOOL_RETRY_MAX_S


@pytest.mark.asyncio
async def test_concurrent_drain_skips_when_locked(tmp_path: Path) -> None:
    """串行锁：drain 进行中再次调用 drain 直接跳过（不重复取同批行）。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        bot_client=_OKBot(),
    )
    # 手动持锁 → drain 应立即返回不动
    await service._spool_drain_lock.acquire()
    try:
        await store_group.telegram_outbound_spool_store.enqueue(
            chat_id="1", text="x", created_at=0.0, next_retry_at=0.0
        )
        await service._drain_outbound_spool()  # 锁被占 → 跳过
        assert await store_group.telegram_outbound_spool_store.count_pending() == 1
    finally:
        service._spool_drain_lock.release()
    await store_group.close()


@pytest.mark.asyncio
async def test_flip_bot_drain_recovers_after_transient_failures(tmp_path: Path) -> None:
    """端到端：入队 → 数次 drain 失败（退避）→ 恢复后 drain 成功清账。"""
    _write_config(tmp_path)
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    store = store_group.telegram_outbound_spool_store
    flip = _FlipBot(fail_count=2)
    service = TelegramGatewayService(
        project_root=tmp_path, store_group=store_group, sse_hub=SSEHub(),
        bot_client=flip,
    )
    await store.enqueue(chat_id="7", text="终会送达", created_at=0.0, next_retry_at=0.0)
    for _ in range(4):  # 前 2 次失败，第 3 次成功
        await store._conn.execute(
            "UPDATE telegram_outbound_spool SET next_retry_at=0 WHERE status='pending'"
        )
        await store._conn.commit()
        await service._drain_outbound_spool()
    assert flip.sent and flip.sent[0]["text"] == "终会送达"
    assert await store.count_pending() == 0
    await store_group.close()
