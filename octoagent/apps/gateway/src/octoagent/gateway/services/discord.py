"""Discord Interactions 渠道 service（F105 v0.2 FR-C3）。

HTTP-only 可达面 = Interactions Endpoint（slash command）；普通频道消息
监听需 WS Gateway + privileged intent，显式 v0.2 范围外（spec §2.2/D6）。

与 Slack 的关键差异：
- 验签是 Ed25519（cryptography），**验签失败 route 必映射 401**——Discord
  注册 Interactions Endpoint 时主动发探测请求并要求 401-on-bad-signature。
- 交互必须即时应答：受理/拒绝都通过 ``response_payload``（type 4）回包，
  未授权用 ephemeral（flags=64）文案而非传输层 4xx（4xx 会显示
  "interaction failed" 且暴露存在性差异）。
- 完成回复走 REST channel message（不存 interaction token——15min 有效期
  小于长任务时长，spec D6）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from octoagent.core.models import TaskStatus
from octoagent.core.models.message import NormalizedMessage

from .channel_reply import build_task_result_text
from .config.config_wizard import load_config
from .task_service import TaskService

logger = logging.getLogger(__name__)

# Discord interaction types / response types（recon §6）
_INTERACTION_PING = 1
_INTERACTION_APPLICATION_COMMAND = 2
_RESPONSE_PONG = 1
_RESPONSE_CHANNEL_MESSAGE = 4
_FLAG_EPHEMERAL = 64


@dataclass(slots=True)
class DiscordIngestResult:
    """Discord inbound 处理结果（route 据此映射 HTTP status + 回包体）。"""

    status: str
    detail: str = ""
    task_id: str | None = None
    created: bool = False
    response_payload: dict[str, Any] | None = field(default=None)


def _ephemeral_message(content: str) -> dict[str, Any]:
    return {
        "type": _RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": content, "flags": _FLAG_EPHEMERAL},
    }


class DiscordGatewayService:
    """Discord 渠道 service（FR-C3）。"""

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
    # 配置
    # ------------------------------------------------------------------

    def _get_discord_config(self) -> Any | None:
        try:
            cfg = load_config(self._project_root)
        except Exception:
            logger.warning("discord_config_load_failed", exc_info=True)
            return None
        if cfg is None:
            return None
        return getattr(getattr(cfg, "channels", None), "discord", None)

    @property
    def enabled(self) -> bool:
        config = self._get_discord_config()
        return bool(getattr(config, "enabled", False))

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
    # Inbound：Ed25519 验签 + interaction 分流
    # ------------------------------------------------------------------

    async def handle_interaction_request(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> DiscordIngestResult:
        if not self.enabled:
            return DiscordIngestResult(status="disabled", detail="discord_disabled")

        config = self._get_discord_config()
        public_key = str(getattr(config, "public_key", "") or "").strip()
        if not public_key:
            return DiscordIngestResult(
                status="blocked", detail="discord_public_key_unavailable"
            )

        if not self._verify_signature(raw_body, headers, public_key):
            return DiscordIngestResult(
                status="signature_invalid", detail="ed25519_verification_failed"
            )

        try:
            interaction = json.loads(raw_body)
        except ValueError:
            return DiscordIngestResult(status="ignored", detail="invalid_json")
        if not isinstance(interaction, dict):
            return DiscordIngestResult(status="ignored", detail="non_object_payload")

        interaction_type = interaction.get("type")
        if interaction_type == _INTERACTION_PING:
            return DiscordIngestResult(
                status="pong", response_payload={"type": _RESPONSE_PONG}
            )
        if interaction_type != _INTERACTION_APPLICATION_COMMAND:
            return DiscordIngestResult(
                status="unsupported",
                detail="unsupported_interaction_type",
                response_payload=_ephemeral_message("暂不支持该交互类型。"),
            )

        return await self._ingest_command(interaction)

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        value = headers.get(name)
        if value is None:
            value = headers.get(name.lower())
        return str(value or "")

    def _verify_signature(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        public_key_hex: str,
    ) -> bool:
        signature = self._header(headers, "X-Signature-Ed25519")
        timestamp = self._header(headers, "X-Signature-Timestamp")
        if not signature or not timestamp:
            return False
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
            key.verify(bytes.fromhex(signature), timestamp.encode() + raw_body)
            return True
        except (ValueError, InvalidSignature):
            return False

    async def _ingest_command(
        self, interaction: dict[str, Any]
    ) -> DiscordIngestResult:
        config = self._get_discord_config()

        guild_id = str(interaction.get("guild_id", "") or "")
        channel_id = str(interaction.get("channel_id", "") or "")
        user_obj = interaction.get("user")
        if not isinstance(user_obj, dict):
            member = interaction.get("member")
            user_obj = member.get("user") if isinstance(member, dict) else None
        if not isinstance(user_obj, dict):
            return DiscordIngestResult(
                status="ignored", detail="missing_user",
                response_payload=_ephemeral_message("无法识别用户。"),
            )
        sender = str(user_obj.get("id", "") or "")
        sender_name = str(
            user_obj.get("global_name") or user_obj.get("username") or sender
        )
        interaction_id = str(interaction.get("id", "") or "")
        if not channel_id or not sender or not interaction_id:
            return DiscordIngestResult(
                status="ignored", detail="missing_required_fields",
                response_payload=_ephemeral_message("交互数据不完整。"),
            )

        # 授权（spec D5 修订版：DM 看 allow_users；guild 双条件，空 allowlist = 拒）
        allow_users = {
            str(item) for item in (getattr(config, "allow_users", []) or [])
        }
        if sender not in allow_users:
            return DiscordIngestResult(
                status="unauthorized",
                detail="user_not_allowed",
                response_payload=_ephemeral_message("未授权使用 OctoAgent。"),
            )
        if guild_id:
            allowed_channels = {
                str(item) for item in (getattr(config, "allowed_channels", []) or [])
            }
            if channel_id not in allowed_channels:
                return DiscordIngestResult(
                    status="unauthorized",
                    detail="channel_not_allowed",
                    response_payload=_ephemeral_message(
                        "此频道未被授权使用 OctoAgent。"
                    ),
                )

        data = interaction.get("data")
        options = data.get("options") if isinstance(data, dict) else None
        text_parts: list[str] = []
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict) and option.get("value") is not None:
                    text_parts.append(str(option["value"]))
        text = " ".join(text_parts).strip()
        if not text:
            return DiscordIngestResult(
                status="ignored",
                detail="empty_command_text",
                response_payload=_ephemeral_message("请输入内容。"),
            )

        scope_id = f"chat:discord:{channel_id}"
        metadata: dict[str, str] = {
            "discord_interaction_id": interaction_id,
            "discord_channel_id": channel_id,
            "discord_user_id": sender,
        }
        if guild_id:
            metadata["discord_guild_id"] = guild_id

        message = NormalizedMessage(
            channel="discord",
            thread_id=f"discord:{channel_id}",
            scope_id=scope_id,
            sender_id=sender,
            sender_name=sender_name,
            text=text,
            metadata=metadata,
            idempotency_key=f"discord:{interaction_id}",
        )

        service = TaskService(self._stores, self._sse_hub)
        task_id, created = await service.create_task(message)
        await self._maybe_enqueue(task_id, text, created)
        await self._record_conversation_binding(
            channel_id, scope_id, "guild" if guild_id else "dm"
        )
        return DiscordIngestResult(
            status="accepted" if created else "duplicate",
            task_id=task_id,
            created=created,
            response_payload={
                "type": _RESPONSE_CHANNEL_MESSAGE,
                "data": {
                    "content": f"已受理，任务 {task_id} 处理中。完成后我会在此频道回复。"
                },
            },
        )

    async def _maybe_enqueue(self, task_id: str, text: str, created: bool) -> None:
        """spec D17a（与 slack 同语义）：duplicate 且 task 仍 CREATED → 补入队。"""
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
        channel_id: str,
        scope_id: str,
        conversation_type: str,
    ) -> None:
        binding_store = getattr(self._stores, "conversation_binding_store", None)
        if binding_store is None:
            return
        try:
            await binding_store.upsert_runtime_binding(
                "discord",
                channel_id,
                scope_id=scope_id,
                project_id="",
                metadata={"conversation_type": conversation_type},
            )
        except Exception:
            logger.warning(
                "discord_conversation_binding_failed channel_id=%s",
                channel_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Outbound：任务完成回复（task 锚定，spec D8）
    # ------------------------------------------------------------------

    async def notify_task_result(self, task_id: str) -> None:
        if self._api_client is None:
            return
        task = await self._stores.task_store.get_task(task_id)
        if task is None or task.requester.channel != "discord":
            return
        channel_id = await self._resolve_reply_channel(task_id)
        if channel_id is None:
            return
        events = await self._stores.event_store.get_events_for_task(task_id)
        status_value = str(getattr(task.status, "value", task.status))
        text = build_task_result_text(status_value, events)
        await self._api_client.create_message(channel_id, text)

    async def _resolve_reply_channel(self, task_id: str) -> str | None:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in events:
            event_type = getattr(event, "type", "")
            if str(getattr(event_type, "value", event_type)) != "USER_MESSAGE":
                continue
            metadata = event.payload.get("metadata", {})
            if not isinstance(metadata, Mapping):
                continue
            channel_id = str(metadata.get("discord_channel_id", "")).strip()
            if channel_id:
                return channel_id
        return None

    # ------------------------------------------------------------------
    # 生命周期（Interactions webhook 无常驻连接，no-op）
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
