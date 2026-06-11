"""Slack Events API 渠道 service（F105 v0.2 FR-B3）。

形态镜像 TelegramGatewayService：inbound（webhook 验签 + 事件解析 →
NormalizedMessage → create_task → enqueue → binding upsert）+ outbound
（任务完成回复 task 锚定 USER_MESSAGE metadata）。差异：

- 验签是 Slack v0 HMAC（raw body + 时间戳防重放 + constant-time compare），
  signing secret **必须**可解析（公网 webhook deny-by-default，与 telegram
  的"可选 secret"不同——Slack 所有请求恒签名，无"未配置即跳过"语义）。
- 授权是静态 allowlist（spec D5：DM 看 allow_users；非 DM 要求
  allowed_channels∋channel 且 allow_users∋sender，空 allowlist = 拒）。
- enqueue 走 spec D17a：duplicate 且 task 仍 CREATED → 补 enqueue
  （平台 retry 是"落盘未入队"窗口的唯一恢复机会；enqueue 幂等由
  create_job INSERT OR IGNORE + _start_job CAS 保证）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octoagent.core.models import TaskStatus
from octoagent.core.models.message import NormalizedMessage

from .channel_reply import build_task_result_text
from .config.config_wizard import load_config
from .task_service import TaskService

logger = logging.getLogger(__name__)

_SIGNATURE_VERSION = "v0"
_TIMESTAMP_TOLERANCE_S = 300  # Slack 官方防重放窗口（recon §6）


@dataclass(slots=True)
class SlackIngestResult:
    """Slack inbound 处理结果（route 据此映射 HTTP status，FR-B4）。"""

    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False
    challenge: str | None = None


class SlackGatewayService:
    """Slack 渠道 service（FR-B3）。"""

    def __init__(
        self,
        *,
        project_root: Path,
        store_group: Any,
        sse_hub: Any,
        api_client: Any | None = None,
        task_runner: Any | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._project_root = project_root
        self._stores = store_group
        self._sse_hub = sse_hub
        self._api_client = api_client
        self._task_runner = task_runner
        self._environ = environ if environ is not None else os.environ

    def bind_task_runner(self, task_runner: Any) -> None:
        self._task_runner = task_runner

    # ------------------------------------------------------------------
    # 配置解析（telegram 同款惰性 per-call 读取）
    # ------------------------------------------------------------------

    def _get_slack_config(self) -> Any | None:
        try:
            cfg = load_config(self._project_root)
        except Exception:
            logger.warning("slack_config_load_failed", exc_info=True)
            return None
        if cfg is None:
            return None
        return getattr(getattr(cfg, "channels", None), "slack", None)

    @property
    def enabled(self) -> bool:
        config = self._get_slack_config()
        return bool(getattr(config, "enabled", False))

    def _resolve_signing_secret(self) -> str:
        config = self._get_slack_config()
        secret_env = str(getattr(config, "signing_secret_env", "") or "").strip()
        if not secret_env:
            return ""
        return self._environ.get(secret_env, "")

    def notification_send_available(self) -> bool:
        """通知通道可用性（adapter D10 gate）：enabled 且 bot token 可解析。"""
        if not self.enabled or self._api_client is None:
            return False
        load_token = getattr(self._api_client, "load_bot_token", None)
        if not callable(load_token):
            return False
        try:
            return load_token() is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Inbound：验签 + 分流 + 事件解析
    # ------------------------------------------------------------------

    async def handle_event_request(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> SlackIngestResult:
        if not self.enabled:
            return SlackIngestResult(status="disabled", detail="slack_disabled")

        secret = self._resolve_signing_secret()
        if not secret:
            return SlackIngestResult(
                status="blocked", detail="slack_signing_secret_unavailable"
            )

        verify_error = self._verify_signature(raw_body, headers, secret)
        if verify_error is not None:
            return verify_error

        try:
            envelope = json.loads(raw_body)
        except ValueError:
            return SlackIngestResult(status="ignored", detail="invalid_json")
        if not isinstance(envelope, dict):
            return SlackIngestResult(status="ignored", detail="non_object_payload")

        envelope_type = str(envelope.get("type", ""))
        if envelope_type == "url_verification":
            return SlackIngestResult(
                status="url_verification",
                challenge=str(envelope.get("challenge", "")),
            )
        if envelope_type != "event_callback":
            return SlackIngestResult(status="ignored", detail="unsupported_envelope")

        return await self._ingest_event(envelope)

    def _verify_signature(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        secret: str,
    ) -> SlackIngestResult | None:
        """v0 HMAC 验签（recon §6）。返回 None = 通过。"""
        timestamp = self._header(headers, "X-Slack-Request-Timestamp")
        signature = self._header(headers, "X-Slack-Signature")
        if not timestamp or not signature:
            return SlackIngestResult(
                status="signature_invalid", detail="missing_signature_headers"
            )
        try:
            ts_value = int(float(timestamp))
        except ValueError:
            return SlackIngestResult(
                status="signature_invalid", detail="malformed_timestamp"
            )
        if abs(time.time() - ts_value) > _TIMESTAMP_TOLERANCE_S:
            return SlackIngestResult(
                status="timestamp_stale", detail="timestamp_outside_tolerance"
            )
        base = b"%s:%s:%s" % (
            _SIGNATURE_VERSION.encode(),
            timestamp.encode(),
            raw_body,
        )
        expected = (
            _SIGNATURE_VERSION
            + "="
            + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            return SlackIngestResult(
                status="signature_invalid", detail="signature_mismatch"
            )
        return None

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        """大小写不敏感取 header（starlette Headers 天然支持；普通 dict 兜底）。"""
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        return str(value or "")

    async def _ingest_event(self, envelope: dict[str, Any]) -> SlackIngestResult:
        config = self._get_slack_config()

        configured_team = str(getattr(config, "team_id", "") or "").strip()
        if configured_team:
            envelope_team = str(envelope.get("team_id", "") or "").strip()
            if envelope_team != configured_team:
                return SlackIngestResult(status="unauthorized", detail="team_mismatch")

        event = envelope.get("event")
        if not isinstance(event, dict):
            return SlackIngestResult(status="ignored", detail="missing_event")
        if str(event.get("type", "")) != "message":
            return SlackIngestResult(status="ignored", detail="unsupported_event_type")
        if event.get("bot_id"):
            return SlackIngestResult(status="ignored", detail="bot_message")
        if event.get("subtype"):
            return SlackIngestResult(status="ignored", detail="message_subtype")

        channel = str(event.get("channel", "") or "")
        sender = str(event.get("user", "") or "")
        text = str(event.get("text", "") or "")
        ts = str(event.get("ts", "") or "")
        thread_ts = str(event.get("thread_ts", "") or "")
        channel_type = str(event.get("channel_type", "") or "")
        if not channel or not sender or not text.strip():
            return SlackIngestResult(status="ignored", detail="empty_or_partial_event")

        # 授权（spec D5：deny-all 默认；非 DM 双条件，空 allowlist = 拒）
        allow_users = {
            str(item) for item in (getattr(config, "allow_users", []) or [])
        }
        if sender not in allow_users:
            return SlackIngestResult(status="unauthorized", detail="user_not_allowed")
        if channel_type != "im":
            allowed_channels = {
                str(item) for item in (getattr(config, "allowed_channels", []) or [])
            }
            if channel not in allowed_channels:
                return SlackIngestResult(
                    status="unauthorized", detail="channel_not_allowed"
                )

        scope_id = f"chat:slack:{channel}"
        thread_id = (
            f"slack:{channel}:thread:{thread_ts}" if thread_ts else f"slack:{channel}"
        )
        event_id = str(envelope.get("event_id", "") or "")
        idempotency_key = (
            f"slack:{event_id}" if event_id else f"slack:{channel}:{ts}"
        )
        metadata: dict[str, str] = {
            "slack_event_id": event_id,
            "slack_channel_id": channel,
            "slack_user_id": sender,
            "slack_ts": ts,
        }
        if thread_ts:
            metadata["slack_thread_ts"] = thread_ts
        if channel_type:
            metadata["slack_channel_type"] = channel_type

        message = NormalizedMessage(
            channel="slack",
            thread_id=thread_id,
            scope_id=scope_id,
            sender_id=sender,
            sender_name=sender,
            text=text,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )

        service = TaskService(self._stores, self._sse_hub)
        task_id, created = await service.create_task(message)
        await self._maybe_enqueue(task_id, text, created)
        await self._record_conversation_binding(channel, scope_id, channel_type)
        return SlackIngestResult(
            status="accepted" if created else "duplicate",
            task_id=task_id,
            created=created,
        )

    async def _maybe_enqueue(self, task_id: str, text: str, created: bool) -> None:
        """spec D17a：created 直接入队；duplicate 仅当 task 仍 CREATED 时补入队。

        补入队覆盖"create_task 落盘后 enqueue 前失败"的窗口——平台 retry
        是唯一恢复机会（CODEX-H1）。状态守卫防止晚到 retry 触发 create_job
        的终态重入队语义（task_job_store L60-76）。
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

    async def _record_conversation_binding(
        self,
        channel: str,
        scope_id: str,
        channel_type: str,
    ) -> None:
        """runtime binding upsert（accepted/duplicate 都 touch，telegram 同构）。

        metadata.conversation_type 是 D9 通知 eligibility 的判定依据
        （仅 "im" 的 runtime binding 可作通用通知目标，CODEX-H2）。
        """
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is None:
            return
        try:
            await binding_store.upsert_runtime_binding(
                "slack",
                channel,
                scope_id=scope_id,
                project_id="",
                metadata={"conversation_type": channel_type or ""},
            )
        except Exception:
            logger.warning(
                "slack_conversation_binding_failed channel=%s",
                channel,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Outbound：任务完成回复（task 锚定，spec D8）
    # ------------------------------------------------------------------

    async def notify_task_result(self, task_id: str) -> None:
        if self._api_client is None:
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.requester.channel != "slack":
            return
        target = await self._resolve_reply_target(task_id)
        if target is None:
            return
        events = await self._stores.event_store.get_events_for_task(task_id)
        status_value = str(getattr(task.status, "value", task.status))
        text = build_task_result_text(status_value, events)
        await self._api_client.post_message(
            target["channel"],
            text,
            thread_ts=target.get("thread_ts") or None,
        )

    async def _resolve_reply_target(self, task_id: str) -> dict[str, str] | None:
        """扫 USER_MESSAGE 事件 metadata 解析回复目标（telegram 同构）。

        回复进原 thread：thread_ts = 原 slack_thread_ts（已在 thread 内）
        或原 slack_ts（对顶层消息开 thread 回复）。
        """
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in events:
            event_type = getattr(event, "type", "")
            if str(getattr(event_type, "value", event_type)) != "USER_MESSAGE":
                continue
            metadata = event.payload.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            channel = str(metadata.get("slack_channel_id", "")).strip()
            if not channel:
                continue
            thread_ts = str(metadata.get("slack_thread_ts", "")).strip() or str(
                metadata.get("slack_ts", "")
            ).strip()
            target = {"channel": channel}
            if thread_ts:
                target["thread_ts"] = thread_ts
            return target
        return None

    # ------------------------------------------------------------------
    # 生命周期（Slack Events API 无常驻连接，no-op）
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
