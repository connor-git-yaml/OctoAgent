"""F133 — voice 从 polling 剥离（语音处理异步化）专项测试。

核心主张：voice pipeline（下载+转写+降级回复）不再阻塞 ingest 热路径——
慢转写期间文字消息照常处理（AC-1 存在性证明 + polling 集成版）。
队列语义：全局 FIFO 串行（AC-2）/ shutdown 干净 cancel（AC-3）/ 失败不静默
（AC-4）/ 幂等（AC-5）/ worker 韧性（AC-6）/ H1 转写后同路（AC-7）。
全部 hermetic Fake，零真实模型加载。AC 绑定见 spec.md §2。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from octoagent.core.store import create_store_group
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.telegram import TelegramGatewayService
from octoagent.gateway.voice import SttResult
from octoagent.provider.dx.telegram_pairing import TelegramStateStore

from .test_telegram_voice import (
    FakeTaskRunner,
    FakeVoiceBotClient,
    _text_update,
    _voice_update,
)


def _write_config(project_root: Path, *, mode: str = "webhook") -> None:
    save_config(
        OctoAgentConfig(
            updated_at="2026-07-06",
            channels=ChannelsConfig(
                telegram=TelegramChannelConfig(
                    enabled=True,
                    mode=mode,
                    webhook_url="https://example.com/api/telegram/webhook",
                )
            ),
        ),
        project_root,
    )


class SlowControlledStt:
    """可控慢 STT fake：transcribe 挂起在 release 事件上，暴露转写进行中的窗口。

    - started：首次 transcribe 已开始（后台 worker 已取到该 voice）。
    - release：放行转写（返回 results 队列的下一个结果）。
    - filenames：按处理顺序记录 filename（voice_<message_id>.ogg → FIFO 断言）。
    """

    def __init__(self, results: list[SttResult] | None = None) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.filenames: list[str] = []
        self.transcribe_calls = 0
        self._results = list(results) if results else []

    def is_available(self) -> bool:
        return True

    async def transcribe(self, audio: bytes, *, mime: str, filename: str) -> SttResult:
        self.transcribe_calls += 1
        self.filenames.append(filename)
        self.started.set()
        await self.release.wait()
        if self._results:
            return self._results.pop(0)
        return SttResult(ok=True, text=f"转写#{self.transcribe_calls}", backend="fake")


async def _drain_voice(service: TelegramGatewayService) -> None:
    """等后台 voice worker 处理完队列中全部消息。"""
    await asyncio.wait_for(service._voice_queue.join(), timeout=5)


async def _build_service(
    tmp_path: Path,
    *,
    bot_client: object,
    stt_service: object,
    task_runner: FakeTaskRunner | None = None,
    mode: str = "webhook",
) -> tuple[TelegramGatewayService, object, TelegramStateStore]:
    _write_config(tmp_path, mode=mode)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"), str(tmp_path / "artifacts")
    )
    state_store = TelegramStateStore(tmp_path)
    state_store.upsert_approved_user(user_id="42", chat_id="42", username="owner")
    service = TelegramGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        task_runner=task_runner or FakeTaskRunner(),
        state_store=state_store,
        bot_client=bot_client,
        stt_service=stt_service,
    )
    return service, store_group, state_store


# ---------------------------------------------------------------------------
# AC-1：存在性证明——慢转写期间文字消息不被阻塞
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_not_blocked_by_slow_voice(tmp_path: Path) -> None:
    """AC-1：voice 转写挂起时发文字 update → 文字 task 在转写完成之前已建。

    这是 F133 的存在性证明：baseline 下 _ingest_update 内联等转写，
    文字消息必须排在转写之后；剥离后文字先落定。
    """
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt()
    runner = FakeTaskRunner()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, task_runner=runner
    )

    voice_result = await service.handle_webhook_update(_voice_update(update_id=801, message_id=801))
    assert voice_result.status == "accepted"
    assert voice_result.detail == "voice_queued"
    # 后台 worker 已开始转写并挂起
    await asyncio.wait_for(stt.started.wait(), timeout=2)

    # 转写仍挂起期间：文字 update 完整走完主链（建 task + enqueue）
    text_result = await service.handle_webhook_update(
        _text_update(update_id=802, message_id=802, text="文字不该等语音")
    )
    assert text_result.status == "accepted"
    assert text_result.task_id is not None
    assert not stt.release.is_set()  # 转写确实还没完成
    tasks = await store_group.task_store.list_tasks()
    assert len(tasks) == 1  # 只有文字 task；voice task 尚未建

    # 放行转写 → voice task 落定
    stt.release.set()
    await _drain_voice(service)
    tasks = await store_group.task_store.list_tasks()
    assert len(tasks) == 2
    assert [text for _, text in runner.enqueued] == ["文字不该等语音", "转写#1"]

    await service.shutdown()
    await store_group.close()


@pytest.mark.asyncio
async def test_polling_loop_not_blocked_by_slow_voice(tmp_path: Path) -> None:
    """AC-1 polling 集成版：同批 [voice, text]，转写挂起期间 polling 轮次完成——
    文字 task 已建 + offset 已先行确认（durability trade-off 的实证，spec §3）。
    """

    class ScriptedPollingBot(FakeVoiceBotClient):
        def __init__(self, batches: list[list[dict[str, object]]]) -> None:
            super().__init__()
            self._batches = list(batches)

        async def get_updates(
            self, *, offset: int | None = None, timeout_s: int
        ) -> list[object]:
            del offset, timeout_s
            if self._batches:
                return self._batches.pop(0)
            await asyncio.sleep(0.01)
            return []

    voice_update = _voice_update(update_id=901, message_id=901)
    text_update = _text_update(update_id=902, message_id=902, text="polling文字不等语音")
    bot = ScriptedPollingBot([[voice_update, text_update]])
    stt = SlowControlledStt()
    service, store_group, state_store = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, mode="polling"
    )

    await service.startup()
    try:
        await asyncio.wait_for(stt.started.wait(), timeout=2)

        # 转写挂起期间：同批文字消息已被处理（polling loop 未被 voice 卡住）
        async def _wait_text_task() -> None:
            while True:
                tasks = await store_group.task_store.list_tasks()
                if any(t.title == "polling文字不等语音" for t in tasks):
                    return
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_wait_text_task(), timeout=2)
        assert not stt.release.is_set()

        # offset 已先行确认（902+1）——"已确认未转写"窗口的实证
        async def _wait_offset() -> None:
            while state_store.get_polling_offset() != 903:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_wait_offset(), timeout=2)

        # 放行转写 → voice task 落定
        stt.release.set()
        await _drain_voice(service)
        tasks = await store_group.task_store.list_tasks()
        assert len(tasks) == 2
    finally:
        await service.shutdown()
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-2：全局 FIFO——多条 voice 排队全部最终处理且保序
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_voice_fifo_all_processed(tmp_path: Path) -> None:
    """AC-2：3 条 voice 排队 → 全部最终处理，转写顺序 = 入队顺序（同 chat FIFO）。"""
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt()
    runner = FakeTaskRunner()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, task_runner=runner
    )

    for i in (1, 2, 3):
        result = await service.handle_webhook_update(
            _voice_update(update_id=810 + i, message_id=810 + i)
        )
        assert result.detail == "voice_queued"
    # 3 条都已入队（第 1 条可能已被 worker 取走开始转写）
    assert stt.transcribe_calls <= 1

    stt.release.set()
    await _drain_voice(service)

    # 全部处理 + FIFO 保序（filename 含 message_id）
    assert stt.filenames == ["voice_811.ogg", "voice_812.ogg", "voice_813.ogg"]
    assert len(await store_group.task_store.list_tasks()) == 3
    assert [text for _, text in runner.enqueued] == ["转写#1", "转写#2", "转写#3"]

    await service.shutdown()
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-3：shutdown 干净 cancel——转写挂起时停机不留 orphan、pending 不再处理
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_cancels_voice_worker(tmp_path: Path) -> None:
    """AC-3：转写挂起 + 1 条 pending 时 shutdown() → worker 干净 cancel、
    pending 项不再处理（随进程丢弃，spec §3 归档窗口）。"""
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    await service.handle_webhook_update(_voice_update(update_id=821, message_id=821))
    await service.handle_webhook_update(_voice_update(update_id=822, message_id=822))
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    worker_task = service._voice_worker_task
    assert worker_task is not None and not worker_task.done()
    assert service._voice_queue.qsize() == 1  # 第 2 条还在排队

    await service.shutdown()

    # worker 已被 cancel 且引用清空（无 orphan task）
    assert worker_task.done()
    assert service._voice_worker_task is None
    # 第 1 条挂起在转写中被 cancel，第 2 条从未开始
    assert stt.transcribe_calls == 1
    assert await store_group.task_store.list_tasks() == []

    # shutdown 后新入站 voice：入队但 worker 守卫退出，不处理（进程退出语义）
    result = await service.handle_webhook_update(_voice_update(update_id=823, message_id=823))
    assert result.detail == "voice_queued"
    await asyncio.sleep(0.05)
    assert stt.transcribe_calls == 1

    await store_group.close()


# ---------------------------------------------------------------------------
# AC-4：失败不静默——慢转写队列中的失败仍发降级回复
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degrade_reply_in_async_path(tmp_path: Path) -> None:
    """AC-4：转写失败发生在后台 worker → 降级回复仍发出（Constitution #6）。"""
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt(
        results=[SttResult(ok=False, reason="transcribe_error", backend="fake")]
    )
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    result = await service.handle_webhook_update(_voice_update(update_id=831, message_id=831))
    assert result.detail == "voice_queued"
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    assert bot.sent == []  # 失败尚未发生（转写挂起中）

    stt.release.set()
    await _drain_voice(service)

    assert len(bot.sent) == 1
    assert "转写失败" in bot.sent[0].text
    assert await store_group.task_store.list_tasks() == []

    await service.shutdown()
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-5：幂等——同一 update 转写完成前重投两次，只建 1 task 只转写 1 次
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_voice_updates_single_task(tmp_path: Path) -> None:
    """AC-5：首条还在转写中时同 update 重投 → 两条都排队，串行 worker 处理
    第二条时幂等预检命中首条已建 task → duplicate 跳过。

    这正是 baseline（webhook 并发 ingest）存在的"转写前幂等窗口"（F109 已知
    limitation）——全局串行队列使其闭合。
    """
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt()
    service, store_group, _ = await _build_service(tmp_path, bot_client=bot, stt_service=stt)

    update = _voice_update(update_id=841, message_id=841)
    first = await service.handle_webhook_update(update)
    await asyncio.wait_for(stt.started.wait(), timeout=2)
    second = await service.handle_webhook_update(update)  # 首条转写挂起中重投
    assert first.detail == "voice_queued"
    assert second.detail == "voice_queued"

    stt.release.set()
    await _drain_voice(service)

    assert stt.transcribe_calls == 1  # 第二条被幂等预检拦截,未重复转写
    assert bot.download_calls == 1
    assert len(await store_group.task_store.list_tasks()) == 1

    await service.shutdown()
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-6：worker 韧性——转写成功后主链异常不杀 worker、用户可见降级
# ---------------------------------------------------------------------------


class ExplodingTaskRunner(FakeTaskRunner):
    """enqueue 对特定文本抛异常——经注入的 DI seam 模拟转写后主链故障。"""

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        if "炸" in user_text:
            raise RuntimeError("enqueue boom")
        await super().enqueue(task_id, user_text, model_alias)


@pytest.mark.asyncio
async def test_worker_survives_pipeline_error(tmp_path: Path) -> None:
    """AC-6：第 1 条转写成功但主链 enqueue 炸 → 用户收降级回复、worker 存活、
    第 2 条正常处理（单条失败不退出 loop，Constitution #6）。"""
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt(
        results=[
            SttResult(ok=True, text="这条会炸", backend="fake"),
            SttResult(ok=True, text="这条正常", backend="fake"),
        ]
    )
    runner = ExplodingTaskRunner()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, task_runner=runner
    )
    stt.release.set()  # 本测试不需要挂起窗口

    await service.handle_webhook_update(_voice_update(update_id=851, message_id=851))
    await service.handle_webhook_update(_voice_update(update_id=852, message_id=852))
    await _drain_voice(service)

    # 第 1 条：主链炸 → 降级回复（不静默）；worker 未死
    assert any("处理失败" in s.text for s in bot.sent)
    assert service._voice_worker_task is not None and not service._voice_worker_task.done()
    # 第 2 条：正常走完主链
    assert ("这条正常" in [text for _, text in runner.enqueued])
    assert stt.transcribe_calls == 2

    await service.shutdown()
    await store_group.close()


# ---------------------------------------------------------------------------
# AC-7：H1 同路——转写 task 与等价文字 task 走同一管道
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transcript_task_equals_text_task(tmp_path: Path) -> None:
    """AC-7：voice 转写建的 task 与直接发同文字建的 task 管道等价
    （title / requester.channel / metadata 键集 / enqueue 文本一致）。"""
    bot = FakeVoiceBotClient()
    stt = SlowControlledStt(results=[SttResult(ok=True, text="你好世界", backend="fake")])
    stt.release.set()
    runner = FakeTaskRunner()
    service, store_group, _ = await _build_service(
        tmp_path, bot_client=bot, stt_service=stt, task_runner=runner
    )

    await service.handle_webhook_update(_voice_update(update_id=861, message_id=861))
    await _drain_voice(service)
    text_result = await service.handle_webhook_update(
        _text_update(update_id=862, message_id=862, text="你好世界")
    )
    assert text_result.task_id is not None

    tasks = sorted(await store_group.task_store.list_tasks(), key=lambda t: t.task_id)
    assert len(tasks) == 2
    voice_task = next(t for t in tasks if t.task_id != text_result.task_id)
    text_task = next(t for t in tasks if t.task_id == text_result.task_id)
    assert voice_task.title == text_task.title == "你好世界"
    assert voice_task.requester.channel == text_task.requester.channel == "telegram"
    # enqueue 文本一致（H1:主 Agent 收到的输入无差别）
    assert [text for _, text in runner.enqueued] == ["你好世界", "你好世界"]

    await service.shutdown()
    await store_group.close()
