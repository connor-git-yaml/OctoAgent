"""Telegram 渠道接入服务。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from octoagent.core.models import OperatorActionOutcome, OperatorInboxItem, TaskStatus
from octoagent.core.models.message import NormalizedMessage
from octoagent.gateway.services.config.config_wizard import load_config
from octoagent.gateway.services.telegram_client import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    TelegramBotApiError,
)

from .operator_actions import decode_telegram_operator_action, encode_telegram_operator_action
from .task_service import TaskService

if TYPE_CHECKING:
    from ..voice import SpeechToTextService, TextToSpeechService

logger = logging.getLogger(__name__)

# F109 语音降级文案(Constitution #6:永不静默丢弃,给用户可理解回复)。
_VOICE_DEGRADE_UNAVAILABLE = "🎙️ 语音转写未启用,请改发文字消息。"
_VOICE_DEGRADE_TOO_LARGE = "🎙️ 语音过长,暂无法处理,请发更短的语音或文字。"
_VOICE_DEGRADE_DOWNLOAD = "🎙️ 语音下载失败,请重试或改发文字。"
_VOICE_DEGRADE_TRANSCRIBE = "🎙️ 语音转写失败,请重试或改发文字。"
_VOICE_DEGRADE_EMPTY = "🎙️ 未能识别语音内容,请重试或改发文字。"
# F133:后台 voice worker 意外异常(转写成功后主链失败等)的降级文案——offset 已
# 先行确认无重投机会,不静默,显式告知用户重试(#6;baseline 该异常经 polling 兜底重投)。
_VOICE_DEGRADE_PIPELINE = "🎙️ 语音消息处理失败,请重试或改发文字。"

# F109 语音大小/时长上限(防超长占用;Telegram getFile 本身亦有 20MB 上限)。
_VOICE_MAX_DURATION_S = int(os.environ.get("OCTOAGENT_STT_MAX_DURATION_S", "300"))
_VOICE_MAX_BYTES = int(os.environ.get("OCTOAGENT_STT_MAX_BYTES", str(20 * 1024 * 1024)))

# ------------------------------------------------------------------
# F131 Telegram 可靠性：polling 退避 + 409 识别 + 出站 spool 参数
# ------------------------------------------------------------------

# polling 失败恢复指数退避（替换扁平 sleep(1.0)，仿 OpenClaw
# TELEGRAM_POLL_RESTART_POLICY，收窄 base/max 适配单用户实例快恢复诉求）。
# 断网/双开时不 busy-loop 刷日志、不骚扰 Telegram API；成功一轮后 reset。
_POLL_BACKOFF_BASE_S = float(os.environ.get("OCTOAGENT_TG_POLL_BACKOFF_BASE_S", "2.0"))
_POLL_BACKOFF_MAX_S = float(os.environ.get("OCTOAGENT_TG_POLL_BACKOFF_MAX_S", "60.0"))
_POLL_BACKOFF_FACTOR = 2.0
_POLL_BACKOFF_JITTER = 0.2  # ±20%，避免与另一 poller 同步共振

# 409 双开诊断（用户可修：关掉另一个 poller/脚本，或切 webhook 模式）。
# 与普通网络错日志文案不同——运维一眼看出"是不是双开了"。
_TELEGRAM_409_CONFLICT_HINT = (
    "检测到同一 bot token 被多处 getUpdates 占用（双开冲突）："
    "请关闭另一个 poller/脚本，或将该账号切到 webhook 模式。"
)

# 出站 spool 重试退避（send 失败入队后 drain 重试的延后策略）。
_SPOOL_RETRY_BASE_S = float(os.environ.get("OCTOAGENT_TG_SPOOL_RETRY_BASE_S", "5.0"))
_SPOOL_RETRY_MAX_S = float(os.environ.get("OCTOAGENT_TG_SPOOL_RETRY_MAX_S", "600.0"))
_SPOOL_RETRY_FACTOR = 2.0
# 超此上限 → status=failed（不无限重试打爆 Telegram 429；保留行供诊断）。
_SPOOL_MAX_ATTEMPTS = int(os.environ.get("OCTOAGENT_TG_SPOOL_MAX_ATTEMPTS", "8"))


# 指数封顶——超此 exp 后退避已封顶到 max，无需再算大幂（防持续断网时 factor^exp
# 在 min() 生效前 OverflowError 崩 _polling_loop）。base=2/max=60 时 exp≈5 即到顶，
# 64 是极宽松的安全上界（2^64 远未溢出 float）。
_BACKOFF_EXP_CAP = 64


def _compute_poll_backoff(attempt: int) -> float:
    """指数退避 + jitter：base * factor^(attempt-1)，封顶 max。attempt 从 1 起。

    纯函数（局部 random，无外部依赖）。attempt<=0 视为首次失败（attempt=1）。
    exp 先封顶 _BACKOFF_EXP_CAP，防持续失败时大幂 OverflowError（Codex P2）。
    """
    import random

    exp = min(max(0, attempt - 1), _BACKOFF_EXP_CAP)
    raw = _POLL_BACKOFF_BASE_S * (_POLL_BACKOFF_FACTOR**exp)
    capped = min(raw, _POLL_BACKOFF_MAX_S)
    jitter = capped * _POLL_BACKOFF_JITTER
    return max(0.0, capped + random.uniform(-jitter, jitter))


def _compute_spool_retry_delay(attempts: int) -> float:
    """spool 重试退避：base * factor^attempts，封顶 max（无 jitter，串行 drain 无共振）。

    attempts 是"已尝试次数"（本次失败后的新值），从 1 起。
    exp 先封顶 _BACKOFF_EXP_CAP，防大幂 OverflowError（Codex P2；spool max_attempts=8
    虽本就不会触及，但纯函数防御性封顶，与 poll 退避一致）。
    """
    exp = min(max(0, attempts - 1), _BACKOFF_EXP_CAP)
    raw = _SPOOL_RETRY_BASE_S * (_SPOOL_RETRY_FACTOR**exp)
    return min(raw, _SPOOL_RETRY_MAX_S)


def _is_getupdates_conflict(exc: BaseException) -> bool:
    """判定 getUpdates 409 双开冲突（镜像 OpenClaw isGetUpdatesConflict）。

    双条件：error_code==409 且 描述含 "getupdates"/"conflict"——避免把偶发
    非 getUpdates 的 409 误判为双开。
    """
    if not isinstance(exc, TelegramBotApiError):
        return False
    if exc.status_code != 409:
        return False
    haystack = " ".join(
        part
        for part in (
            str(exc),
            str(exc.payload.get("description", "")) if isinstance(exc.payload, dict) else "",
        )
        if part
    ).lower()
    return "getupdates" in haystack or "conflict" in haystack


class TelegramPairingRequestLike(Protocol):
    code: str


class TelegramApprovedUserLike(Protocol):
    user_id: str
    chat_id: str


class TelegramStateStoreProtocol(Protocol):
    def is_user_allowed(self, user_id: str) -> bool:
        ...

    def is_group_allowed(self, chat_id: str, sender_id: str) -> bool:
        ...

    def ensure_pairing_request(
        self,
        *,
        user_id: str,
        username: str = "",
        chat_id: str,
        display_name: str = "",
        last_message_text: str = "",
    ) -> TelegramPairingRequestLike:
        ...

    def record_dm_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        username: str = "",
        display_name: str = "",
        message_id: int | None = None,
        text: str = "",
    ) -> None:
        ...

    def list_allowed_groups(self) -> list[str]:
        ...

    def list_group_allow_users(self) -> list[str]:
        ...

    def get_pending_pairing(self, user_id: str) -> TelegramPairingRequestLike | None:
        ...

    def list_pending_pairings(self) -> list[TelegramPairingRequestLike]:
        ...

    def upsert_approved_user(self, **kwargs: Any) -> TelegramApprovedUserLike:
        ...

    def delete_pending_pairing(self, user_id: str) -> None:
        ...

    def first_approved_user(self) -> TelegramApprovedUserLike | None:
        ...

    def resolve_reply_thread_root(
        self,
        *,
        chat_id: str,
        message_id: str,
    ) -> str | None:
        ...

    def remember_reply_thread_root(
        self,
        *,
        chat_id: str,
        message_id: str,
        root_message_id: str,
    ) -> str:
        ...

    def get_polling_offset(self) -> int | None:
        ...

    def set_polling_offset(self, offset: int | None) -> int | None:
        ...


class TelegramBotClientProtocol(Protocol):
    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        disable_notification: bool = False,
        reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None,
    ) -> Any:
        ...

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_s: int,
    ) -> list[Any]:
        ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str = "",
        show_alert: bool = False,
    ) -> Any:
        ...

    async def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: str | int,
        text: str,
        reply_markup: InlineKeyboardMarkup | dict[str, Any] | None = None,
    ) -> Any:
        ...

    async def get_file(self, file_id: str) -> dict[str, Any]:
        ...

    async def download_file_bytes(self, file_path: str, *, max_bytes: int = ...) -> bytes:
        ...

    async def send_voice(
        self,
        chat_id: str | int,
        voice: bytes,
        *,
        duration: int | None = None,
        reply_to_message_id: str | int | None = None,
        message_thread_id: str | int | None = None,
        disable_notification: bool = True,
    ) -> Any:
        """FIX-6：F110 TTS 出站——补全 DI 契约（Protocol 声明与 telegram_client.py 实现签名对齐）。"""
        ...


@dataclass(slots=True)
class TelegramVoiceRef:
    """F109:入站语音消息的音频引用(`message.voice` 提取)。"""

    file_id: str
    mime_type: str = "audio/ogg"
    duration: int = 0
    file_size: int = 0


@dataclass(slots=True)
class TelegramInboundContext:
    update_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    sender_name: str
    message_id: str
    text: str
    sender_username: str = ""
    reply_to_message_id: str = ""
    message_thread_id: str = ""
    callback_query_id: str = ""
    callback_data: str = ""
    is_callback: bool = False
    voice: TelegramVoiceRef | None = None


@dataclass(slots=True)
class TelegramIngestResult:
    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False


def _read_nested_attr(obj: object, *names: str) -> object | None:
    current = obj
    for name in names:
        if current is None:
            return None
        current = getattr(current, name, None)
    return current


class TelegramGatewayService:
    """桥接 Telegram update 与现有 Task/Event 管道。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group,
        sse_hub,
        task_runner=None,
        state_store: TelegramStateStoreProtocol | None = None,
        bot_client: TelegramBotClientProtocol | None = None,
        polling_timeout_s: int = 15,
        stt_service: SpeechToTextService | None = None,
        tts_service: TextToSpeechService | None = None,
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._sse_hub = sse_hub
        self._task_runner = task_runner
        self._state_store = state_store
        self._bot_client = bot_client
        self._polling_timeout_s = polling_timeout_s
        self._stt_service = stt_service  # F109:None = 语音转写未启用(优雅降级)
        self._tts_service = tts_service  # F110:None = TTS 未启用（优雅降级）
        self._polling_task: asyncio.Task[None] | None = None
        # F133：voice 处理剥离 ingest 热路径——全局 FIFO 队列 + 单 consumer 后台 worker
        # （并发上界=1：faster-whisper CPU-bound，串行防多语音并发打爆 CPU；全局 FIFO
        # 天然同 chat 保序）。item 是轻量 context（无音频字节），无界队列单用户可承受。
        self._voice_queue: asyncio.Queue[TelegramInboundContext] = asyncio.Queue()
        self._voice_worker_task: asyncio.Task[None] | None = None
        # F131：webhook 模式专用出站 spool 周期 drain 任务（polling 模式在其 loop 内 drain，
        # 不起此任务）。与入站请求解耦——不在 webhook 请求路径同步 drain（Codex P1）。
        self._spool_drain_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # F131：出站 spool drain 串行锁——webhook 周期 drain 与 startup drain 可能并发触发，
        # 单进程用锁串行化，避免两个 drain 各自 list_due 取到同一行重复发（polling
        # 模式单 loop 本就串行，锁在此路径无竞争、开销可忽略）。
        self._spool_drain_lock = asyncio.Lock()
        # webhook 模式周期 drain 间隔（秒）——比 polling timeout 略长，避免空转过频。
        self._spool_drain_interval_s = float(
            os.environ.get("OCTOAGENT_TG_SPOOL_DRAIN_INTERVAL_S", "30.0")
        )
        self._operator_inbox_service = None
        self._operator_action_service = None
        self._control_plane_service = None
        # F101 Phase C v2 H-3：NotificationService 引用，供 dismiss callback 使用
        self._notification_service = None

    @property
    def enabled(self) -> bool:
        config = self._get_telegram_config()
        return bool(getattr(config, "enabled", False))

    def bind_task_runner(self, task_runner: Any) -> None:
        self._task_runner = task_runner

    def bind_operator_services(
        self,
        operator_inbox_service: Any,
        operator_action_service: Any,
    ) -> None:
        self._operator_inbox_service = operator_inbox_service
        self._operator_action_service = operator_action_service

    def bind_control_plane_service(self, control_plane_service: Any) -> None:
        self._control_plane_service = control_plane_service

    def bind_notification_service(self, notification_service: Any) -> None:
        """绑定 NotificationService（F101 Phase C v2 H-3：Telegram dismiss callback）。"""
        self._notification_service = notification_service

    def _get_telegram_config(self):
        try:
            cfg = load_config(self._project_root)
        except Exception:
            logger.warning("telegram_config_load_failed", exc_info=True)
            return None
        return _read_nested_attr(cfg, "channels", "telegram")

    def _resolve_mode(self) -> str:
        config = self._get_telegram_config()
        mode = getattr(config, "mode", "webhook")
        return str(mode or "webhook")

    def _resolve_secret(self) -> str:
        config = self._get_telegram_config()
        secret_env = getattr(config, "webhook_secret_env", "")
        if not secret_env:
            return ""
        return os.environ.get(str(secret_env), "")

    def _has_configured_secret(self) -> bool:
        config = self._get_telegram_config()
        secret_env = str(getattr(config, "webhook_secret_env", "") or "").strip()
        return bool(secret_env)

    def _resolve_static_allow_users(self) -> set[str]:
        config = self._get_telegram_config()
        allow_users = getattr(config, "allow_users", []) or []
        return {str(item) for item in allow_users if str(item).strip()}

    def _resolve_static_allowed_groups(self) -> set[str]:
        config = self._get_telegram_config()
        allowed_groups = getattr(config, "allowed_groups", []) or []
        return {str(item) for item in allowed_groups if str(item).strip()}

    def _resolve_group_allow_users(self) -> set[str]:
        config = self._get_telegram_config()
        allow_users = getattr(config, "group_allow_users", []) or []
        return {str(item) for item in allow_users if str(item).strip()}

    def _resolve_dm_policy(self) -> str:
        config = self._get_telegram_config()
        return str(getattr(config, "dm_policy", "pairing") or "pairing")

    def _resolve_group_policy(self) -> str:
        config = self._get_telegram_config()
        return str(getattr(config, "group_policy", "allowlist") or "allowlist")

    async def startup(self) -> None:
        self._stop_event.clear()
        if not self.enabled or self._bot_client is None or self._state_store is None:
            return
        # Codex P2：出站 spool drain 一律走独立后台任务，绝不在 startup / polling 主路径
        # 同步等待（50 条 × 每条 10s send timeout 可拖住启动/收 update 数分钟）。
        # _spool_drain_loop 首轮立即 drain 一次（重启补偿 AC-7），随后周期 drain。
        # polling 与 webhook 模式都起此任务——drain 与 get_updates 关键路径彻底解耦。
        if self._spool_drain_task is None or self._spool_drain_task.done():
            self._spool_drain_task = asyncio.create_task(self._spool_drain_loop())
        if self._resolve_mode() == "polling":
            if self._polling_task is None or self._polling_task.done():
                self._polling_task = asyncio.create_task(self._polling_loop())

    async def shutdown(self) -> None:
        self._stop_event.set()
        # F133：_voice_worker_task 一并 cancel——打断 queue.get()/转写 await，不留 orphan
        # task。pending 队列项随进程丢弃（durability trade-off 见 spec §3：不发降级回复，
        # shutdown 时网络 send 不可靠且拖慢退出）。
        for attr in ("_polling_task", "_spool_drain_task", "_voice_worker_task"):
            task = getattr(self, attr)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                setattr(self, attr, None)

    async def _spool_drain_loop(self) -> None:
        """出站 spool 独立周期 drain 任务（Codex P1/P2）。

        首轮立即 drain（重启补偿），随后每 _spool_drain_interval_s 一次。与 polling
        get_updates 关键路径 + startup 主路径解耦——慢/超时的 send 不阻塞启动或收 update。
        退避 wait_for 期间响应 stop（shutdown 立即醒来）。drain 全降级；本 loop 亦兜底
        任何异常不退出。
        """
        first = True
        while not self._stop_event.is_set():
            if not first:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._spool_drain_interval_s
                    )
                if self._stop_event.is_set():
                    return
            first = False
            try:
                await self._drain_outbound_spool()
            except asyncio.CancelledError:
                raise
            except Exception:  # 防御性：drain 内已全降级，此处再兜底不退出 loop
                logger.warning("telegram_spool_drain_loop_failed", exc_info=True)

    async def handle_webhook_update(
        self,
        update: Mapping[str, Any],
        *,
        secret_token: str = "",
    ) -> TelegramIngestResult:
        if not self.enabled:
            return TelegramIngestResult(status="disabled", detail="telegram_disabled")
        if self._resolve_mode() != "webhook":
            return TelegramIngestResult(status="blocked", detail="telegram_not_in_webhook_mode")

        expected_secret = self._resolve_secret()
        if self._has_configured_secret():
            if not expected_secret:
                return TelegramIngestResult(
                    status="blocked",
                    detail="telegram_webhook_secret_unavailable",
                )
            if secret_token != expected_secret:
                return TelegramIngestResult(status="unauthorized", detail="invalid_webhook_secret")

        # Codex P1：drain 不在请求路径同步跑（避免慢 send 拖垮 webhook 响应 → Telegram
        # 超时重投）。webhook 模式的周期 drain 由独立 _spool_drain_loop 后台任务负责
        # （startup 拉起），与入站请求解耦。
        return await self._ingest_update(update)

    async def _polling_loop(self) -> None:
        # F131：连续失败次数——驱动指数退避（成功一轮后 reset），断网/双开时不 busy-loop。
        failure_streak = 0
        while not self._stop_event.is_set():
            assert self._state_store is not None
            assert self._bot_client is not None
            try:
                offset = self._state_store.get_polling_offset()
                updates = await self._bot_client.get_updates(
                    offset=offset,
                    timeout_s=self._polling_timeout_s,
                )
                next_offset = offset
                for update in updates:
                    await self._ingest_update(update)
                    payload = self._coerce_update(update)
                    if payload is None:
                        continue
                    try:
                        candidate = int(payload.get("update_id", 0)) + 1
                    except (TypeError, ValueError):
                        continue
                    next_offset = candidate if next_offset is None else max(next_offset, candidate)
                if next_offset != offset:
                    self._state_store.set_polling_offset(next_offset)
                # F131：成功一轮 → 退避重置。出站 spool drain 由独立 _spool_drain_loop
                # 负责（Codex P2：不在 get_updates 关键路径同步 drain，慢 send 不拖住收 update）。
                failure_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 防御性兜底：任何 get_updates/ingest 异常不崩 loop
                failure_streak += 1
                delay = _compute_poll_backoff(failure_streak)
                # F131 G2：409 双开与普通网络错区分——409 是用户可修的，给专属诊断日志。
                if _is_getupdates_conflict(exc):
                    logger.warning(
                        "telegram_polling_conflict_409 streak=%s retry_in_s=%.1f hint=%s error=%s",
                        failure_streak,
                        delay,
                        _TELEGRAM_409_CONFLICT_HINT,
                        str(exc),
                    )
                else:
                    logger.warning(
                        "telegram_polling_loop_failed streak=%s retry_in_s=%.1f",
                        failure_streak,
                        delay,
                        exc_info=True,
                    )
                # 退避 sleep 期间响应 stop（shutdown 立即醒来，不空等满一个 delay）。
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)

    # ------------------------------------------------------------------
    # F131 出站补偿 spool：send 失败入队 + 后台 drain 重试（进程重启不丢）
    # ------------------------------------------------------------------

    def _spool_store(self) -> Any | None:
        """取出站 spool store（缺失/未装配 → None，降级不崩，Constitution #6）。"""
        return getattr(self._stores, "telegram_outbound_spool_store", None)

    async def _send_or_spool(
        self,
        target: Mapping[str, str],
        text: str,
        *,
        disable_notification: bool = True,
        task_id: str = "",
    ) -> Any | None:
        """发送出站文字消息；失败入 spool 补偿队列（G3 主缺口修复）。

        disable_notification 默认 True：对齐 telegram_client.send_message 真实默认
        （baseline notify_task_result 省略该参数用 client 默认 True，approval 路径
        显式传 True）——两条 baseline 调用等价保持，静音不被改成有声（AC-11）。

        成功路径与 baseline `bot_client.send_message` 逐字节等价（返回 sent_message）；
        失败路径（网络抖动 / Telegram 5xx / 429）入队落盘，返回 None。调用方据此
        决定是否继续（如 _remember_outbound_reply_thread 仅在成功时有 message_id）。

        FR-B（H1）：spool 只补发主 Agent 已生成的结果文本，不抢话、不改决策环。
        """
        assert self._bot_client is not None  # 调用方已判 self._bot_client is not None
        chat_id = str(target.get("chat_id", "")).strip()
        reply_to = str(target.get("reply_to_message_id", "") or "").strip()
        thread_id = str(target.get("message_thread_id", "") or "").strip()
        # Codex P2：群聊 reply-thread 根 id——首发失败入队时一并持久化，drain 补发成功后
        # 据此登记新 message_id → root 映射，用户回复补发消息仍能落回原线程。
        reply_thread_root_id = str(target.get("reply_thread_root_id", "") or "").strip()
        try:
            sent_message = await self._bot_client.send_message(
                chat_id,
                text,
                reply_to_message_id=reply_to or None,
                message_thread_id=thread_id or None,
                disable_notification=disable_notification,
            )
            return sent_message
        except Exception as exc:
            await self._enqueue_outbound_spool(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to,
                message_thread_id=thread_id,
                reply_thread_root_id=reply_thread_root_id,
                disable_notification=disable_notification,
                task_id=task_id,
                error=str(exc),
            )
            return None

    async def _enqueue_outbound_spool(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str,
        message_thread_id: str,
        reply_thread_root_id: str = "",
        disable_notification: bool,
        task_id: str,
        error: str,
    ) -> None:
        """出站发送失败 → 落 spool 待发队列（AC-5）。spool 自身故障不级联（AC-10）。"""
        store = self._spool_store()
        if store is None:
            logger.warning(
                "telegram_outbound_dropped_no_spool chat_id=%s task_id=%s error=%s",
                chat_id,
                task_id,
                error,
            )
            return
        now = time.time()
        try:
            spool_id = await store.enqueue(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                reply_thread_root_id=reply_thread_root_id,
                disable_notification=disable_notification,
                task_id=task_id,
                created_at=now,
                next_retry_at=now,  # 立即可被下轮 drain 取出（首次重试无延后）
                last_error=error,
            )
            logger.warning(
                "telegram_outbound_spooled spool_id=%s chat_id=%s task_id=%s error=%s",
                spool_id,
                chat_id,
                task_id,
                error,
            )
        except Exception:
            logger.warning(
                "telegram_outbound_spool_enqueue_failed chat_id=%s task_id=%s",
                chat_id,
                task_id,
                exc_info=True,
            )

    async def _drain_outbound_spool(self) -> None:
        """取出到期待发消息逐条重发（AC-6/7/9）。全程降级不崩（AC-10）。

        成功 → mark_sent（删行，不重复发）+ 补登记 reply-thread 映射（Codex P2）；
        失败未超上限 → mark_retry（退避延后）；超 _SPOOL_MAX_ATTEMPTS → mark_failed。

        串行锁：webhook 模式并发 inbound 可能并发触发——若已有 drain 在跑就直接跳过
        （非阻塞 try-acquire），避免两个 drain 取到同一批行重复发。
        """
        store = self._spool_store()
        if store is None or self._bot_client is None:
            return
        if self._spool_drain_lock.locked():
            return  # 已有 drain 在跑，跳过（下轮/下次 inbound 会再触发）
        async with self._spool_drain_lock:
            try:
                due = await store.list_due(time.time())
            except Exception:
                logger.warning("telegram_outbound_spool_list_failed", exc_info=True)
                return
            for item in due:
                try:
                    sent_message = await self._bot_client.send_message(
                        item.chat_id,
                        item.text,
                        reply_to_message_id=item.reply_to_message_id or None,
                        message_thread_id=item.message_thread_id or None,
                        disable_notification=item.disable_notification,
                    )
                except Exception as exc:
                    attempts = item.attempts + 1
                    if attempts >= _SPOOL_MAX_ATTEMPTS:
                        with contextlib.suppress(Exception):
                            await store.mark_failed(
                                item.id, attempts=attempts, last_error=str(exc)
                            )
                        logger.warning(
                            "telegram_outbound_spool_failed_final spool_id=%s "
                            "chat_id=%s attempts=%s error=%s",
                            item.id,
                            item.chat_id,
                            attempts,
                            str(exc),
                        )
                    else:
                        next_retry_at = time.time() + _compute_spool_retry_delay(attempts)
                        with contextlib.suppress(Exception):
                            await store.mark_retry(
                                item.id,
                                attempts=attempts,
                                next_retry_at=next_retry_at,
                                last_error=str(exc),
                            )
                    continue
                # 成功 → 删行（不重复发）。
                with contextlib.suppress(Exception):
                    await store.mark_sent(item.id)
                    logger.info(
                        "telegram_outbound_spool_delivered spool_id=%s chat_id=%s attempts=%s",
                        item.id,
                        item.chat_id,
                        item.attempts,
                    )
                # Codex P2：补发成功后登记 reply-thread 映射——群聊 reply-thread 任务结果
                # 首发失败入队时保存了 reply_thread_root_id，补发成功后登记新 message_id，
                # 使用户回复补发消息时 resolve_reply_thread_root 仍能找到原线程根。
                if item.reply_thread_root_id:
                    self._remember_outbound_reply_thread(
                        {
                            "chat_id": item.chat_id,
                            "reply_thread_root_id": item.reply_thread_root_id,
                        },
                        sent_message,
                    )

    async def _ingest_update(self, update: Mapping[str, Any] | Any) -> TelegramIngestResult:
        payload = self._coerce_update(update)
        if payload is None:
            return TelegramIngestResult(status="ignored", detail="unsupported_update_payload")

        context = self._extract_context(payload)
        if context is None:
            return TelegramIngestResult(status="ignored", detail="unsupported_or_empty_update")

        if not self._is_allowed(context):
            return await self._handle_unauthorized(context)

        if context.is_callback:
            return await self._handle_callback_query(context)

        # F109:语音预处理(H1)。voice 且无文字 → 转写填 context.text,失败则降级回复。
        # F133:整条 voice pipeline(幂等预检+守卫+下载+转写+降级回复)剥离出 ingest
        # 热路径——入队后台串行 worker 立即返回,下载+STT(秒级~数十秒)与降级 send
        # (坏网下亦秒级)不再阻塞 polling get_updates / webhook 响应。转写成功后
        # worker 走 _ingest_text_context,与文字消息完全同路(H1 只挪"何时跑")。
        if context.voice is not None and not context.text.strip():
            return self._enqueue_voice_processing(context)

        return await self._ingest_text_context(context)

    def _enqueue_voice_processing(self, context: TelegramInboundContext) -> TelegramIngestResult:
        """F133:voice 消息入全局 FIFO 队列 + 确保后台 worker 存活(lazy spawn,自愈)。

        - lazy spawn:首条 voice 才拉起 worker(无 voice 用户零后台任务);worker 意外
          死亡(不应发生,loop 内有兜底)下条 voice 自动重拉。
        - 返回 accepted + voice_queued:route 层 accepted→200,Telegram 快速确认,
          webhook 超时重投概率反而下降。
        - shutdown 后(_stop_event set)入队的项不会被处理(worker loop 守卫退出),
          随进程丢弃——与 pending 项同一 durability 窗口(spec §3)。
        """
        self._voice_queue.put_nowait(context)
        if self._voice_worker_task is None or self._voice_worker_task.done():
            self._voice_worker_task = asyncio.create_task(self._voice_worker_loop())
        logger.info(
            "telegram_voice_queued chat_id=%s message_id=%s queue_depth=%s",
            context.chat_id,
            context.message_id,
            self._voice_queue.qsize(),
        )
        return TelegramIngestResult(status="accepted", detail="voice_queued")

    async def _voice_worker_loop(self) -> None:
        """F133:后台 voice 串行 worker——全局 FIFO 逐条处理(并发上界=1)。

        - 串行单 consumer:faster-whisper CPU-bound,防多条语音并发转写打爆 CPU;
          全局 FIFO 天然同 chat 保序。幂等预检在处理时点跑(串行 → webhook 重投的
          重复副本必然看到首条已建 task → duplicate 跳过,比 baseline 并发窗口更严)。
        - 单条失败不退出 loop(#6):意外异常(转写成功后主链 store 故障等)降级回复
          用户后继续下一条。baseline 该异常冒泡到 polling 兜底经 offset 重投;F133
          offset 已先行确认无重投机会 → 显式告知用户重试,不静默(spec §3 次级 delta)。
        - shutdown:task.cancel() 打断 queue.get()/处理中 await;finally 保证
          task_done 计数不漏(queue.join() 语义完整,测试依赖)。
        """
        while not self._stop_event.is_set():
            context = await self._voice_queue.get()
            try:
                outcome = await self._handle_voice_message(context)
                if not isinstance(outcome, TelegramIngestResult):
                    await self._ingest_text_context(outcome)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "telegram_voice_async_pipeline_failed chat_id=%s message_id=%s",
                    context.chat_id,
                    context.message_id,
                    exc_info=True,
                )
                await self._reply_voice_degrade(
                    context, _VOICE_DEGRADE_PIPELINE, reason="pipeline_error"
                )
            finally:
                self._voice_queue.task_done()

    async def _ingest_text_context(
        self, context: TelegramInboundContext
    ) -> TelegramIngestResult:
        """文字消息主链(baseline _ingest_update 后半段原样抽取,F133 零行为变更)。

        文字 update 直接走此路;voice 由后台 worker 转写成功回填 text 后走此路——
        create_task / enqueue / binding / reply-thread 与 baseline 完全同路(H1)。
        """
        if not context.text.strip():
            return TelegramIngestResult(status="ignored", detail="unsupported_or_empty_update")
        if control_result := await self._handle_control_command(context):
            return control_result

        self._record_authorized_private_dm(context)

        scope_id, thread_id, reply_thread_root_id = self._resolve_scope_thread(context)
        metadata = {
            "telegram_update_id": context.update_id,
            "telegram_chat_id": context.chat_id,
            "telegram_message_id": context.message_id,
        }
        if context.reply_to_message_id:
            metadata["telegram_reply_to_message_id"] = context.reply_to_message_id
        if context.message_thread_id:
            metadata["telegram_message_thread_id"] = context.message_thread_id
        if reply_thread_root_id:
            metadata["telegram_reply_thread_root_id"] = reply_thread_root_id

        message = NormalizedMessage(
            channel="telegram",
            thread_id=thread_id,
            scope_id=scope_id,
            sender_id=context.sender_id,
            sender_name=context.sender_name,
            text=context.text,
            metadata=metadata,
            idempotency_key=self._build_idempotency_key(context),
        )

        service = TaskService(self._stores, self._sse_hub)
        task_id, created = await service.create_task(message)
        # H-1/D17a：投递优先——enqueue 先于非关键路由 state 写入，确保"落盘未入队"
        # 窗口的补队不被 binding / reply-thread 写入异常阻断（对齐 slack/discord）
        await self._maybe_enqueue(task_id, context.text, created)
        # F105 FR-E3：accepted 与 duplicate 都 touch（幂等重投不丢 last-route）
        # F110 FIX-3：voice_mode 写入已前移到 _handle_voice_message 转写成功后（enqueue 之前），
        # 此处 _record_conversation_binding 仅维护 last_* 路由字段（不再传 set_voice_mode_if_unset）。
        await self._record_conversation_binding(
            context, scope_id, reply_thread_root_id,
        )
        self._remember_inbound_reply_thread(context, reply_thread_root_id)
        return TelegramIngestResult(
            status="accepted" if created else "duplicate",
            task_id=task_id,
            created=created,
        )

    async def _maybe_enqueue(self, task_id: str, text: str, created: bool) -> None:
        """spec D17a（与 slack/discord 同语义）：created 直接入队；
        duplicate 仅当 task 仍 CREATED 时补入队。

        补入队覆盖"create_task 落盘后 enqueue 前失败"的窗口——平台 retry
        （webhook 重投/polling 重读）是唯一恢复机会。enqueue 幂等由
        create_job INSERT OR IGNORE + _start_job CAS 保证。
        """
        if self._task_runner is None:
            return
        if created:
            await self._task_runner.enqueue(task_id, text)
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return
        status_value = str(getattr(task.status, "value", task.status))
        if status_value == TaskStatus.CREATED.value:
            await self._task_runner.enqueue(task_id, text)

    async def _handle_voice_message(
        self, context: TelegramInboundContext
    ) -> TelegramInboundContext | TelegramIngestResult:
        """F109:下载语音 → STT 转写 → 回填 context.text(成功)。

        返回更新后的 context(成功)或 TelegramIngestResult(降级/幂等,已回复用户)。
        全程不抛异常(FR-D3),失败一律降级。
        F133:本函数逻辑零改动,但调用点从 ingest 热路径挪到 _voice_worker_loop
        (后台串行)——幂等预检/守卫/下载/转写/降级回复全部不再阻塞 polling。
        """
        voice = context.voice
        assert voice is not None  # 调用方已判 context.voice is not None

        # ① 幂等预检:重投不重复转写(AC-3)。命中已存在 task → duplicate,不下载不转写。
        idempotency_key = self._build_idempotency_key(context)
        existing_task_id = await self._stores.event_store.check_idempotency_key(idempotency_key)
        if existing_task_id:
            return TelegramIngestResult(
                status="duplicate", task_id=existing_task_id, created=False
            )

        # ② STT 不可用 → 降级(AC-4)。
        if self._stt_service is None or not self._stt_service.is_available():
            await self._reply_voice_degrade(
                context, _VOICE_DEGRADE_UNAVAILABLE, reason="stt_unavailable"
            )
            return TelegramIngestResult(status="ignored", detail="voice_stt_unavailable")

        # ③ 时长/大小守卫 → 降级,不下载不转写(FR-C4)。
        if (voice.duration and voice.duration > _VOICE_MAX_DURATION_S) or (
            voice.file_size and voice.file_size > _VOICE_MAX_BYTES
        ):
            await self._reply_voice_degrade(
                context, _VOICE_DEGRADE_TOO_LARGE, reason="too_large"
            )
            return TelegramIngestResult(status="ignored", detail="voice_too_large")

        # ④ 下载音频 → 失败降级。
        if self._bot_client is None:
            await self._reply_voice_degrade(
                context, _VOICE_DEGRADE_UNAVAILABLE, reason="no_bot_client"
            )
            return TelegramIngestResult(status="ignored", detail="voice_no_bot_client")
        try:
            file_info = await self._bot_client.get_file(voice.file_id)
            file_path = str(file_info.get("file_path") or "")
            if not file_path:
                raise TelegramBotApiError("getFile 未返回 file_path")
            audio = await self._bot_client.download_file_bytes(
                file_path, max_bytes=_VOICE_MAX_BYTES
            )
        except Exception:
            logger.warning(
                "telegram_voice_download_failed chat_id=%s message_id=%s",
                context.chat_id,
                context.message_id,
                exc_info=True,
            )
            await self._reply_voice_degrade(
                context, _VOICE_DEGRADE_DOWNLOAD, reason="download_failed"
            )
            return TelegramIngestResult(status="ignored", detail="voice_download_failed")

        # ⑤ 转写(service 已兜底异常 + 判空)→ 失败/空降级。
        result = await self._stt_service.transcribe(
            audio,
            mime=voice.mime_type,
            filename=f"voice_{context.message_id}.ogg",
        )
        if not result.ok:
            degrade_text = (
                _VOICE_DEGRADE_EMPTY if result.reason == "empty" else _VOICE_DEGRADE_TRANSCRIBE
            )
            await self._reply_voice_degrade(
                context, degrade_text, reason=result.reason or "transcribe_failed"
            )
            return TelegramIngestResult(
                status="ignored", detail=f"voice_{result.reason or 'transcribe_failed'}"
            )

        # ⑥ 成功:回填 text,留观测日志(FR-D1,只记 len/duration/backend,不记转写原文/音频)。
        logger.info(
            "telegram_voice_transcribed backend=%s duration_s=%s transcript_len=%s",
            result.backend,
            voice.duration,
            len(result.text),
        )

        # FIX-3：voice_mode 写入在 enqueue 之前持久化，消除首轮竞态。
        # D2-C GATE：仅当 voice_mode 未设置（unset）时自动置 True；显式 False 不覆盖。
        await self._set_voice_mode_if_unset(context)

        return replace(context, text=result.text)

    async def _reply_voice_degrade(
        self, context: TelegramInboundContext, text: str, *, reason: str
    ) -> None:
        """F109:给用户发降级文字回复(#6)。发送失败 suppress,不阻断主链。"""
        logger.warning(
            "telegram_voice_degraded reason=%s chat_id=%s message_id=%s",
            reason,
            context.chat_id,
            context.message_id,
        )
        if self._bot_client is None:
            return
        with contextlib.suppress(Exception):
            await self._bot_client.send_message(
                context.chat_id,
                text,
                reply_to_message_id=context.message_id,
            )

    # ---- F110 voice_mode 状态机 helper（AC-D7/D1/D1b）----

    @staticmethod
    def _get_voice_mode(binding: Any) -> bool:
        """三态读取：key 缺失 → False；True → True；False → False。

        AC-D7：binding 不存在 / voice_mode key 缺失 → False（文字回复，不崩）。
        """
        if binding is None:
            return False
        return bool(getattr(binding, "metadata", {}).get("voice_mode", False))

    @staticmethod
    def _is_voice_mode_explicitly_disabled(binding: Any) -> bool:
        """区分「unset（key 缺失）」vs「显式 False（用户 /voice off 过）」。

        AC-D1b：显式 /voice off 后发 voice 消息，不自动重开 voice_mode。
        """
        if binding is None:
            return False
        md = getattr(binding, "metadata", {})
        return "voice_mode" in md and md["voice_mode"] is False

    async def _set_voice_mode_if_unset(self, context: TelegramInboundContext) -> None:
        """FIX-3：在 enqueue 之前将 voice_mode 置 True（仅当 unset 时）。

        D2-C GATE 裁决：入站 voice 转写成功后，若 voice_mode 未设置，自动置 True；
        显式 False（用户 /voice off 过）不覆盖。RMW：get → merge → upsert。
        失败 suppress，不阻断主链（Constitution #6）。
        """
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is None:
            return
        with contextlib.suppress(Exception):
            existing = await binding_store.get("telegram", context.chat_id, project_id="")
            if self._is_voice_mode_explicitly_disabled(existing):
                return  # 显式 False，不自动重开
            merged: dict[str, Any] = dict(getattr(existing, "metadata", {}) or {})
            if merged.get("voice_mode") is True:
                return  # 已是 True，幂等跳过
            merged["voice_mode"] = True
            existing_scope_id = str(getattr(existing, "scope_id", "") or "")
            await binding_store.upsert_runtime_binding(
                "telegram",
                context.chat_id,
                scope_id=existing_scope_id,
                project_id="",
                metadata=merged,
            )

    async def _record_conversation_binding(
        self,
        context: TelegramInboundContext,
        scope_id: str,
        reply_thread_root_id: str,
    ) -> None:
        """F105 FR-E3：登记/touch 渠道会话路由绑定（OC-2 + OC-6 last-route 状态）。

        FIX-3：voice_mode 写入已前移到 _set_voice_mode_if_unset（enqueue 之前），
        此方法只维护 last_* 路由字段和 scope_id，不再包含 voice_mode 逻辑。
        - 必须 read-modify-write（先 get → merge existing → upsert），防止全量替换
          清掉其他 metadata 字段（MEDIUM-3 最大风险，plan §C 说明）。

        - conversation_id=chat_id：出站寻址单元=chat 级（spec 已知 limitation L3：
          多 topic 群塌成一行，topic 维度滚动记录在 metadata.last_*）
        - project_id=''：telegram scope 解析不到 project（recon §4）
        - 失败 WARNING 降级，不阻断消息主链（Constitution #6）
        """
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is None:
            return

        # F110 MEDIUM-3：read-modify-write——先 get 现有 binding，merge 后再 upsert，
        # 防止 upsert 全量替换清掉其他字段（如 last_message_thread_id 等）。
        try:
            existing = await binding_store.get("telegram", context.chat_id, project_id="")
        except Exception:
            existing = None

        merged: dict[str, Any] = dict(getattr(existing, "metadata", {}) or {})
        if context.message_thread_id:
            merged["last_message_thread_id"] = context.message_thread_id
        if reply_thread_root_id:
            merged["last_reply_thread_root_id"] = reply_thread_root_id
        # FIX-3：voice_mode 不在此处写入（已前移到 _set_voice_mode_if_unset），
        # RMW 保留已持久化的 voice_mode 值（merged 从 existing.metadata 复制）。

        try:
            await binding_store.upsert_runtime_binding(
                "telegram",
                context.chat_id,
                scope_id=scope_id,
                project_id="",
                metadata=merged,
            )
        except Exception:
            logger.warning(
                "telegram_conversation_binding_failed chat_id=%s",
                context.chat_id,
                exc_info=True,
            )

    async def _handle_control_command(
        self,
        context: TelegramInboundContext,
    ) -> TelegramIngestResult | None:
        # F110 FR-D2：渠道层 voice 控制命令（Constitution #9：确定性渠道渲染开关，
        # 不经 Agent LLM 决策环）。优先于 control_plane 检测。
        text_stripped = context.text.strip().lower()
        if text_stripped in ("/voice on", "/voice off"):
            return await self._handle_voice_command(context, enable=(text_stripped == "/voice on"))

        if self._control_plane_service is None:
            return None
        request = self._control_plane_service.build_telegram_action_request(
            context.text,
            actor_id=f"user:telegram:{context.sender_id}",
            actor_label=context.sender_name,
        )
        if request is None:
            return None

        result = await self._control_plane_service.execute_action(request)
        if self._bot_client is not None:
            with contextlib.suppress(Exception):
                sent_message = await self._bot_client.send_message(
                    context.chat_id,
                    self._render_control_plane_result(result),
                    reply_to_message_id=context.message_id,
                    message_thread_id=context.message_thread_id or None,
                )
                self._remember_outbound_reply_thread(
                    {
                        "chat_id": context.chat_id,
                        "reply_thread_root_id": context.reply_to_message_id,
                    },
                    sent_message,
                )
        return TelegramIngestResult(
            status="control_action",
            detail=result.status.value,
            created=result.status.value == "completed",
        )

    async def _handle_voice_command(
        self,
        context: TelegramInboundContext,
        *,
        enable: bool,
    ) -> TelegramIngestResult:
        """F110 FR-D2：处理 /voice on|off 渠道控制命令。

        Constitution #9：/voice 是确定性渠道渲染开关，非 Agent LLM 决策。
        FR-B3：控制命令回复只发文字，不走 TTS（防循环）。
        执行 read-modify-write（get → merge existing metadata → upsert）。
        """
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is not None:
            with contextlib.suppress(Exception):
                existing = await binding_store.get("telegram", context.chat_id, project_id="")
                merged: dict[str, Any] = dict(getattr(existing, "metadata", {}) or {})
                merged["voice_mode"] = enable
                # 保留 existing.scope_id（不在此函数重算 scope）。
                # DEFER L1（主节点裁决）：若 existing is None（用户从未发过消息即发 /voice on），
                # scope_id 写入空字符串；下一条真实入站消息的 _record_conversation_binding RMW
                # 会补上 scope_id（自愈），v0.1 出站寻址不依赖 binding.scope_id。归 v0.2 通知路由。
                existing_scope_id = str(getattr(existing, "scope_id", "") or "")
                await binding_store.upsert_runtime_binding(
                    "telegram",
                    context.chat_id,
                    scope_id=existing_scope_id,
                    project_id="",
                    metadata=merged,
                )

        if self._bot_client is not None:
            reply_text = "语音模式已开启 🔊" if enable else "语音模式已关闭 💬"
            with contextlib.suppress(Exception):
                await self._bot_client.send_message(
                    context.chat_id,
                    reply_text,
                    reply_to_message_id=context.message_id,
                )

        detail = "voice_on" if enable else "voice_off"
        return TelegramIngestResult(status="control_action", detail=detail, created=False)

    def _is_allowed(self, context: TelegramInboundContext) -> bool:
        if self._state_store is None:
            return False

        if context.chat_type == "private":
            policy = self._resolve_dm_policy()
            if policy == "disabled":
                return False
            if policy == "open":
                return True
            static_allow = self._resolve_static_allow_users()
            if context.sender_id in static_allow:
                return True
            return self._state_store.is_user_allowed(context.sender_id)

        policy = self._resolve_group_policy()
        if policy == "disabled":
            return False
        if policy == "open":
            return True

        static_allowed_groups = self._resolve_static_allowed_groups()
        dynamic_allowed_groups = set(self._state_store.list_allowed_groups())
        chat_allowed = (
            "*" in static_allowed_groups
            or context.chat_id in static_allowed_groups
            or context.chat_id in dynamic_allowed_groups
        )
        if not chat_allowed:
            return False

        allowed_users = self._resolve_group_allow_users() | set(
            self._state_store.list_group_allow_users()
        )
        if not allowed_users:
            return True
        if context.sender_id in allowed_users:
            return True
        return self._state_store.is_group_allowed(context.chat_id, context.sender_id)

    def _record_authorized_private_dm(self, context: TelegramInboundContext) -> None:
        if context.chat_type != "private" or self._state_store is None:
            return
        if not self._state_store.is_user_allowed(context.sender_id):
            return
        self._state_store.record_dm_message(
            user_id=context.sender_id,
            chat_id=context.chat_id,
            username=context.sender_username,
            display_name=context.sender_name,
            message_id=int(context.message_id),
            text=context.text,
        )

    async def _handle_unauthorized(self, context: TelegramInboundContext) -> TelegramIngestResult:
        if context.chat_type != "private" or self._state_store is None or self._bot_client is None:
            return TelegramIngestResult(status="blocked", detail="telegram_sender_not_authorized")
        if context.is_callback:
            with contextlib.suppress(Exception):
                await self._bot_client.answer_callback_query(
                    context.callback_query_id,
                    text="当前账号没有 operator 权限",
                    show_alert=False,
                )
            return TelegramIngestResult(status="blocked", detail="telegram_sender_not_authorized")

        policy = self._resolve_dm_policy()
        if policy != "pairing":
            return TelegramIngestResult(status="blocked", detail="telegram_dm_not_authorized")

        request = self._state_store.ensure_pairing_request(
            user_id=context.sender_id,
            username=context.sender_username,
            chat_id=context.chat_id,
            display_name=context.sender_name,
            last_message_text=context.text,
        )
        text = (
            "当前 Telegram 私聊尚未授权。\n"
            f"Pairing Code: {request.code}\n"
            "请由 owner 审批后重新发送消息。"
        )
        try:
            await self._bot_client.send_message(context.chat_id, text)
        except Exception:
            logger.warning(
                "telegram_pairing_notice_failed chat_id=%s user_id=%s",
                context.chat_id,
                context.sender_id,
                exc_info=True,
            )
        await self._notify_pairing_request(context.sender_id)
        return TelegramIngestResult(status="pairing_required", detail=request.code)

    @staticmethod
    def _build_idempotency_key(context: TelegramInboundContext) -> str:
        return f"telegram:{context.update_id}:{context.chat_id}:{context.message_id}"

    def _resolve_scope_thread(self, context: TelegramInboundContext) -> tuple[str, str, str]:
        scope_id = f"chat:telegram:{context.chat_id}"
        if context.chat_type == "private":
            return scope_id, f"tg:{context.sender_id}", ""
        if context.message_thread_id:
            return (
                scope_id,
                f"tg_group:{context.chat_id}:topic:{context.message_thread_id}",
                "",
            )
        if context.reply_to_message_id:
            reply_thread_root_id = context.reply_to_message_id
            if self._state_store is not None:
                resolved = self._state_store.resolve_reply_thread_root(
                    chat_id=context.chat_id,
                    message_id=context.reply_to_message_id,
                )
                if resolved:
                    reply_thread_root_id = resolved
            return (
                scope_id,
                f"tg_group:{context.chat_id}:reply:{reply_thread_root_id}",
                reply_thread_root_id,
            )
        return scope_id, f"tg_group:{context.chat_id}", ""

    @staticmethod
    def _extract_context(update: Mapping[str, Any]) -> TelegramInboundContext | None:
        message = update.get("message")
        callback_query_id = ""
        callback_data = ""
        is_callback = False
        if not isinstance(message, Mapping):
            callback = update.get("callback_query")
            if isinstance(callback, Mapping):
                callback_query_id = str(callback.get("id") or "").strip()
                callback_data = str(callback.get("data") or "").strip()
                is_callback = True
                message = callback.get("message")
                sender = callback.get("from")
            else:
                sender = None
        else:
            sender = message.get("from")
        if not isinstance(message, Mapping):
            return None

        chat = message.get("chat")
        if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
            return None

        text = str(message.get("text") or "").strip()
        voice_ref = TelegramGatewayService._extract_voice_ref(message)
        username = str(sender.get("username") or "").strip()
        sender_name = (
            username
            or str(sender.get("first_name") or "").strip()
            or str(sender.get("id") or "").strip()
        )
        reply_to = message.get("reply_to_message")
        reply_to_message_id = ""
        if isinstance(reply_to, Mapping):
            value = reply_to.get("message_id")
            if value is not None:
                reply_to_message_id = str(value)

        message_thread_id = ""
        if message.get("message_thread_id") is not None:
            message_thread_id = str(message.get("message_thread_id"))

        update_id = update.get("update_id")
        message_id = message.get("message_id")
        chat_id = chat.get("id")
        sender_id = sender.get("id")
        if update_id is None or message_id is None or chat_id is None or sender_id is None:
            return None

        return TelegramInboundContext(
            update_id=str(update_id),
            chat_id=str(chat_id),
            chat_type=str(chat.get("type") or "private"),
            sender_id=str(sender_id),
            sender_name=sender_name or str(sender_id),
            message_id=str(message_id),
            text=text,
            sender_username=username,
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
            callback_query_id=callback_query_id,
            callback_data=callback_data,
            is_callback=is_callback,
            voice=voice_ref,
        )

    @staticmethod
    def _extract_voice_ref(message: Mapping[str, Any]) -> TelegramVoiceRef | None:
        """从 `message.voice` 提取音频引用(F109,AC-1)。photo/document 不在范围。"""
        raw = message.get("voice")
        if not isinstance(raw, Mapping):
            return None
        file_id = str(raw.get("file_id") or "").strip()
        if not file_id:
            return None
        try:
            duration = int(raw.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        try:
            file_size = int(raw.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        return TelegramVoiceRef(
            file_id=file_id,
            mime_type=str(raw.get("mime_type") or "audio/ogg"),
            duration=duration,
            file_size=file_size,
        )

    async def notify_task_result(self, task_id: str) -> None:
        if self._bot_client is None:
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.requester.channel != "telegram":
            return

        target = await self._resolve_reply_target(task_id)
        if target is None:
            return

        events = await self._stores.event_store.get_events_for_task(task_id)
        # FR-B2：_build_result_text / _resolve_reply_target 零修改（硬约束）
        text = self._build_result_text(self._status_value(task.status), events)

        # F110 FR-B1：TTS 出站分支（H1：在主 Agent 回复之后，渠道层后处理）
        if await self._try_send_voice_reply(target, text):
            return  # 语音发送成功，不再发文字

        # 原有文字路径（voice_mode=False / TTS 不可用 / 所有失败降级后到此）
        # F131 G3：send 失败 → 入 spool 补偿队列（返回 None），成功 → 记 reply-thread。
        sent_message = await self._send_or_spool(target, text, task_id=task_id)
        if sent_message is not None:
            self._remember_outbound_reply_thread(target, sent_message)

    async def _try_send_voice_reply(
        self, target: dict[str, str], text: str
    ) -> bool:
        """尝试 TTS + send_voice；任何失败记日志后返回 False（调用方降级文字）。

        Constitution #6 + FR-B4/B5：所有异常在此捕获，永不逃逸到 notify_task_result 调用方。
        FR-B3：控制命令回复不走此路径（仅 notify_task_result 调用，确认回复用 send_message）。
        AC-D7：voice_mode key 缺失 → False → 静默走文字（无日志噪声）。

        DEFER FINDING-3（主节点裁决，v0.2 通知幂等域）：
        notify_task_result 重入时可能重复发 voice（与 send_message 同样无去重机制，F110 未引入
        新风险）。去重逻辑属通知幂等范畴，归 v0.2；v0.1 维持 F109 基线行为不变。
        """
        try:
            if self._tts_service is None or not self._tts_service.is_available():
                logger.debug(
                    "tts_skip reason=tts_unavailable chat_id=%s", target.get("chat_id")
                )
                return False

            # 查 voice_mode
            binding_store = getattr(self._stores, "conversation_binding_store", None)
            if binding_store is None:
                return False
            binding = await binding_store.get("telegram", target["chat_id"], project_id="")
            if not self._get_voice_mode(binding):
                return False  # voice_mode=False/unset，静默走文字

            # TTS 合成（TextToSpeechService 内已有全面兜底，不会抛）
            tts_result = await self._tts_service.synthesize(text)
            if not tts_result.ok:
                logger.warning(
                    "tts_degrade reason=%s chat_id=%s text_len=%d",
                    tts_result.reason, target.get("chat_id"), len(text),
                )
                return False

            logger.info(
                "tts_success backend=%s duration_ms=%d text_len=%d",
                tts_result.backend, tts_result.duration_ms, len(text),
            )

            # send_voice（失败抛 TelegramBotApiError，此处捕获）
            sent_message = await self._bot_client.send_voice(
                target["chat_id"],
                tts_result.audio,
                reply_to_message_id=target.get("reply_to_message_id") or None,
                message_thread_id=target.get("message_thread_id") or None,
            )
            self._remember_outbound_reply_thread(target, sent_message)
            return True

        except Exception:
            logger.warning(
                "tts_degrade reason=send_voice_failed chat_id=%s",
                target.get("chat_id"),
                exc_info=True,
            )
            return False

    async def notify_approval_event(
        self,
        *,
        event_type: str,
        data: Mapping[str, Any],
        task_id: str | None,
    ) -> None:
        if self._bot_client is None or task_id is None:
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return

        target = await self._resolve_operator_target()
        if target is None:
            return

        if event_type == "approval:requested":
            approval_id = str(data.get("approval_id", "")).strip()
            item = None
            if approval_id and self._operator_inbox_service is not None:
                with contextlib.suppress(Exception):
                    item = await self._operator_inbox_service.get_item(f"approval:{approval_id}")
            text = (
                self._build_operator_item_text(item)
                if item is not None
                else (
                    "任务需要审批。\n"
                    f"Approval ID: {data.get('approval_id', '-')}\n"
                    f"Tool: {data.get('tool_name', '-')}"
                )
            )
            reply_markup = self._build_operator_item_markup(item)
        elif event_type == "approval:resolved":
            decision = str(data.get("decision", "unknown"))
            text = f"审批结果已更新：{decision}"
            reply_markup = None
        else:
            return

        # F131 G3：审批通知出站补偿——仅对**无 inline keyboard** 的纯文字通知
        # （如 approval:resolved 状态更新）走 spool；带按钮的 approval:requested
        # 不 spool——延后送达一个「按钮已失效」的审批卡片比丢弃更糟，且审批本身有
        # SSE/operator-inbox 的独立 durability。带按钮路径保持 baseline 行为
        # （直发；失败由 registry try/except 记 warning，与改动前一致）。
        if reply_markup is None:
            sent_message = await self._send_or_spool(
                target, text, disable_notification=True, task_id=task_id
            )
            if sent_message is not None:
                self._remember_outbound_reply_thread(target, sent_message)
        else:
            sent_message = await self._bot_client.send_message(
                target["chat_id"],
                text,
                disable_notification=True,
                reply_markup=reply_markup,
            )
            self._remember_outbound_reply_thread(target, sent_message)

    async def _handle_callback_query(self, context: TelegramInboundContext) -> TelegramIngestResult:
        if (
            self._bot_client is None
            or not context.callback_query_id
            or not context.callback_data
        ):
            return TelegramIngestResult(status="ignored", detail="operator_action_unavailable")

        # F101 Phase C v2 H-3：先检测 dismiss_notif 格式（优先于 operator action 解码）
        if context.callback_data.startswith("dismiss_notif:"):
            return await self._handle_dismiss_notification_callback(context)

        if self._operator_action_service is None:
            return TelegramIngestResult(status="ignored", detail="operator_action_unavailable")

        try:
            request = decode_telegram_operator_action(context.callback_data).model_copy(
                update={
                    "actor_id": f"user:telegram:{context.sender_id}",
                    "actor_label": context.sender_name,
                }
            )
        except ValueError as exc:
            await self._bot_client.answer_callback_query(
                context.callback_query_id,
                text=str(exc),
                show_alert=False,
            )
            return TelegramIngestResult(status="blocked", detail="invalid_operator_callback")

        result = await self._operator_action_service.execute(request)
        await self._bot_client.answer_callback_query(
            context.callback_query_id,
            text=self._callback_notice(result),
            show_alert=False,
        )
        with contextlib.suppress(Exception):
            await self._bot_client.edit_message_text(
                chat_id=context.chat_id,
                message_id=context.message_id,
                text=self._render_operator_result_text(result),
                reply_markup=None,
            )
        return TelegramIngestResult(
            status="operator_action",
            detail=result.outcome.value,
            task_id=result.task_id,
            created=result.outcome == OperatorActionOutcome.SUCCEEDED,
        )

    async def _handle_dismiss_notification_callback(
        self, context: TelegramInboundContext
    ) -> TelegramIngestResult:
        """F101 Phase C v2 H-3：处理通知 dismiss callback。

        callback_data 格式：``dismiss_notif:<notification_id>``
        识别后调用 notification_service.dismiss(notification_id, source="telegram")。
        不影响现有 operator action 路径。
        """
        # 解析 notification_id
        try:
            _, notification_id = context.callback_data.split(":", 1)
            notification_id = notification_id.strip()
        except ValueError:
            with contextlib.suppress(Exception):
                await self._bot_client.answer_callback_query(
                    context.callback_query_id,
                    text="无效的通知 ID",
                    show_alert=False,
                )
            return TelegramIngestResult(status="blocked", detail="invalid_dismiss_callback")

        # 调用 notification_service.dismiss（若可用）。F116：dismiss 改 async（落盘）。
        if self._notification_service is not None:
            with contextlib.suppress(Exception):
                await self._notification_service.dismiss(notification_id, source="telegram")

        # 应答 callback query（移除 inline keyboard）
        with contextlib.suppress(Exception):
            await self._bot_client.answer_callback_query(
                context.callback_query_id,
                text="通知已关闭",
                show_alert=False,
            )
        with contextlib.suppress(Exception):
            await self._bot_client.edit_message_text(
                chat_id=context.chat_id,
                message_id=context.message_id,
                text="[通知已关闭]",
                reply_markup=None,
            )
        logger.debug(
            "telegram_notification_dismissed",
            notification_id=notification_id,
            sender_id=context.sender_id,
        )
        return TelegramIngestResult(
            status="notification_dismissed",
            detail=notification_id,
        )

    async def _notify_pairing_request(self, user_id: str) -> None:
        if self._operator_inbox_service is None or self._bot_client is None:
            return
        item = None
        with contextlib.suppress(Exception):
            item = await self._operator_inbox_service.get_item(f"pairing:{user_id}")
        if item is None:
            return
        target = await self._resolve_operator_target()
        if target is None:
            return
        await self._bot_client.send_message(
            target["chat_id"],
            self._build_operator_item_text(item),
            disable_notification=True,
            reply_markup=self._build_operator_item_markup(item),
        )

    async def _resolve_operator_target(self) -> dict[str, str] | None:
        if self._state_store is None:
            return None
        approved = self._state_store.first_approved_user()
        if approved is None:
            return None
        return {"chat_id": str(approved.chat_id)}

    def _build_operator_item_text(self, item: OperatorInboxItem | None) -> str:
        if item is None:
            return "存在待处理 operator 工作项。"
        lines = [item.title]
        if item.summary:
            lines.append(item.summary)
        if item.expires_at is not None:
            lines.append(f"过期时间: {item.expires_at.isoformat()}")
        if item.task_id:
            lines.append(f"Task: {item.task_id}")
        code = item.metadata.get("code", "")
        if code:
            lines.append(f"Pairing Code: {code}")
        return "\n".join(lines)

    def _build_operator_item_markup(
        self,
        item: OperatorInboxItem | None,
    ) -> InlineKeyboardMarkup | None:
        if item is None:
            return None
        rows: list[list[InlineKeyboardButton]] = []
        current: list[InlineKeyboardButton] = []
        for action in item.quick_actions:
            if not action.enabled:
                continue
            try:
                callback_data = encode_telegram_operator_action(item.item_id, action.kind)
            except ValueError:
                continue
            current.append(
                InlineKeyboardButton(text=action.label, callback_data=callback_data)
            )
            if len(current) == 2:
                rows.append(current)
                current = []
        if current:
            rows.append(current)
        if not rows:
            return None
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _callback_notice(result) -> str:
        if result.outcome == OperatorActionOutcome.SUCCEEDED:
            return "已处理"
        if result.outcome == OperatorActionOutcome.ALREADY_HANDLED:
            return "已被处理"
        if result.outcome == OperatorActionOutcome.EXPIRED:
            return "已过期"
        if result.outcome == OperatorActionOutcome.STALE_STATE:
            return "状态已变化"
        if result.outcome == OperatorActionOutcome.NOT_ALLOWED:
            return "当前不可操作"
        if result.outcome == OperatorActionOutcome.NOT_FOUND:
            return "目标不存在"
        return "处理失败"

    @staticmethod
    def _render_operator_result_text(result) -> str:
        return (
            "Operator Action\n"
            f"结果: {result.outcome.value}\n"
            f"说明: {result.message}"
        )

    @staticmethod
    def _render_control_plane_result(result) -> str:
        lines = [
            f"Action: {result.action_id}",
            f"状态: {result.status.value}",
            f"代码: {result.code}",
            f"说明: {result.message}",
        ]
        if isinstance(result.data, Mapping):
            summary = result.data.get("overall_status") or result.data.get("project_id")
            if summary:
                lines.append(f"摘要: {summary}")
        return "\n".join(lines)

    async def _resolve_reply_target(self, task_id: str) -> dict[str, str] | None:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in events:
            if self._event_type_name(event) != "USER_MESSAGE":
                continue
            metadata = event.payload.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            chat_id = str(metadata.get("telegram_chat_id", "")).strip()
            if not chat_id:
                continue
            result = {"chat_id": chat_id}
            reply_to = str(metadata.get("telegram_message_id", "")).strip()
            if reply_to:
                result["reply_to_message_id"] = reply_to
            thread_id = str(metadata.get("telegram_message_thread_id", "")).strip()
            if thread_id:
                result["message_thread_id"] = thread_id
            reply_thread_root_id = str(
                metadata.get("telegram_reply_thread_root_id", "")
            ).strip()
            if reply_thread_root_id:
                result["reply_thread_root_id"] = reply_thread_root_id
            return result
        return None

    @staticmethod
    def _build_result_text(status: str, events: list[Any]) -> str:
        if status == TaskStatus.SUCCEEDED.value:
            for event in reversed(events):
                if TelegramGatewayService._event_type_name(event) == "MODEL_CALL_COMPLETED":
                    summary = str(event.payload.get("response_summary", "")).strip()
                    if summary:
                        return summary
            return "任务已成功完成。"

        if status == TaskStatus.FAILED.value:
            for event in reversed(events):
                if TelegramGatewayService._event_type_name(event) == "MODEL_CALL_FAILED":
                    message = str(event.payload.get("error_message", "")).strip()
                    if message:
                        return f"任务失败：{message}"
            return "任务失败，请查看系统日志。"

        if status == TaskStatus.CANCELLED.value:
            return "任务已取消。"
        if status == TaskStatus.REJECTED.value:
            return "任务已被拒绝。"
        return f"任务状态已更新：{status}"

    @staticmethod
    def _coerce_update(update: Mapping[str, Any] | Any) -> Mapping[str, Any] | None:
        if isinstance(update, Mapping):
            return update
        model_dump = getattr(update, "model_dump", None)
        if callable(model_dump):
            payload = model_dump(by_alias=True, exclude_none=True)
            if isinstance(payload, Mapping):
                return payload
        return None

    @staticmethod
    def _event_type_name(event: Any) -> str:
        event_type = getattr(event, "type", "")
        return str(getattr(event_type, "value", event_type))

    @staticmethod
    def _status_value(status: Any) -> str:
        return str(getattr(status, "value", status))

    def _remember_inbound_reply_thread(
        self,
        context: TelegramInboundContext,
        reply_thread_root_id: str,
    ) -> None:
        if (
            self._state_store is None
            or context.chat_type == "private"
            or not reply_thread_root_id
        ):
            return
        self._state_store.remember_reply_thread_root(
            chat_id=context.chat_id,
            message_id=context.message_id,
            root_message_id=reply_thread_root_id,
        )

    def _remember_outbound_reply_thread(
        self,
        target: Mapping[str, str],
        sent_message: Any,
    ) -> None:
        if self._state_store is None:
            return
        reply_thread_root_id = str(target.get("reply_thread_root_id", "")).strip()
        if not reply_thread_root_id:
            return
        message_id = self._extract_message_id(sent_message)
        if not message_id:
            return
        self._state_store.remember_reply_thread_root(
            chat_id=target["chat_id"],
            message_id=message_id,
            root_message_id=reply_thread_root_id,
        )

    @staticmethod
    def _extract_message_id(sent_message: Any) -> str:
        value = getattr(sent_message, "message_id", None)
        if value is None and isinstance(sent_message, Mapping):
            value = sent_message.get("message_id")
        return str(value).strip() if value is not None else ""


class CompositeApprovalBroadcaster:
    """同时广播 SSE 与 Telegram 提示。"""

    def __init__(self, *broadcasters) -> None:
        self._broadcasters = [item for item in broadcasters if item is not None]

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        for broadcaster in self._broadcasters:
            await broadcaster.broadcast(event_type, data, task_id=task_id)


class TelegramApprovalBroadcaster:
    """将审批状态同步到 Telegram 会话。"""

    def __init__(self, telegram_service: TelegramGatewayService) -> None:
        self._telegram_service = telegram_service

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> None:
        await self._telegram_service.notify_approval_event(
            event_type=event_type,
            data=data,
            task_id=task_id,
        )
