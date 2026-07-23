"""F105 v0.2 Phase C: DiscordGatewayService 测试。

覆盖 spec US-3 AC-1（PING/PONG）/ AC-2（验签失败）/ AC-3（command 全链路 +
幂等）/ AC-4（未授权 ephemeral）/ AC-5（完成回复 REST）+ D5 授权矩阵
（guild 空 allowed_channels 拒 / DM 仅 allow_users）+ D17a 重试恢复。
Ed25519 用测试密钥对现签（不依赖外部网络）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from octoagent.core.models import TaskStatus
from octoagent.core.store import create_store_group
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    DiscordChannelConfig,
    OctoAgentConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.discord import DiscordGatewayService
from octoagent.gateway.services.sse_hub import SSEHub

_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY_HEX = _PRIVATE_KEY.public_key().public_bytes_raw().hex()
_ENVIRON = {"DISCORD_BOT_TOKEN": "bot-test-token"}


def _write_config(project_root: Path, **discord_overrides: object) -> None:
    discord_config: dict[str, object] = {
        "enabled": True,
        "public_key": _PUBLIC_KEY_HEX,
        "allow_users": ["U_OWNER"],
    }
    discord_config.update(discord_overrides)
    save_config(
        OctoAgentConfig(
            updated_at="2026-06-12",
            channels=ChannelsConfig(discord=DiscordChannelConfig(**discord_config)),
        ),
        project_root,
    )


class FakeTaskRunner:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, str]] = []
        self.fail_next: bool = False

    async def enqueue(self, task_id: str, user_text: str, model_alias: str | None = None) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("模拟 enqueue 失败")
        self.enqueued.append((task_id, user_text))


class FakeDiscordApiClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def load_bot_token(self) -> str | None:
        return "bot-test-token"

    async def create_message(self, channel_id: str, content: str):
        self.messages.append((channel_id, content))
        return {"id": "m1"}


def _signed_headers(body: bytes, ts: str = "1718000000") -> dict[str, str]:
    signature = _PRIVATE_KEY.sign(ts.encode() + body).hex()
    return {
        "X-Signature-Ed25519": signature,
        "X-Signature-Timestamp": ts,
    }


def _command_body(
    *,
    interaction_id: str = "I001",
    channel_id: str = "CH1",
    user_id: str = "U_OWNER",
    guild_id: str = "",
    text: str = "帮我查一下天气",
) -> bytes:
    interaction: dict[str, object] = {
        "type": 2,
        "id": interaction_id,
        "channel_id": channel_id,
        "data": {"name": "octo", "options": [{"name": "prompt", "value": text}]},
    }
    user = {"id": user_id, "username": "owner"}
    if guild_id:
        interaction["guild_id"] = guild_id
        interaction["member"] = {"user": user}
    else:
        interaction["user"] = user
    return json.dumps(interaction).encode()


async def _build_service(
    tmp_path: Path,
    *,
    task_runner: FakeTaskRunner | None = None,
    api_client: FakeDiscordApiClient | None = None,
    **config_overrides: object,
):
    _write_config(tmp_path, **config_overrides)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    service = DiscordGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        api_client=api_client,
        task_runner=task_runner,
        environ=_ENVIRON,
    )
    return service, store_group


@pytest.mark.asyncio
async def test_ping_pong(tmp_path: Path) -> None:
    """US-3 AC-1：合法签名 PING → PONG。"""
    service, store_group = await _build_service(tmp_path)
    body = json.dumps({"type": 1, "id": "I0"}).encode()
    result = await service.handle_interaction_request(body, _signed_headers(body))
    assert result.status == "pong"
    assert result.response_payload == {"type": 1}
    await store_group.close()


@pytest.mark.asyncio
async def test_invalid_signature_rejected(tmp_path: Path) -> None:
    """US-3 AC-2 服务层：签名不合法 → signature_invalid（route 映射 401）。"""
    service, store_group = await _build_service(tmp_path)
    body = _command_body()
    wrong_key = Ed25519PrivateKey.generate()
    bad_headers = {
        "X-Signature-Ed25519": wrong_key.sign(b"1718000000" + body).hex(),
        "X-Signature-Timestamp": "1718000000",
    }
    result = await service.handle_interaction_request(body, bad_headers)
    assert result.status == "signature_invalid"

    # 缺 header / 非 hex 同样拒绝
    assert (await service.handle_interaction_request(body, {})).status == "signature_invalid"
    garbled = {"X-Signature-Ed25519": "zz-not-hex", "X-Signature-Timestamp": "1"}
    assert (await service.handle_interaction_request(body, garbled)).status == "signature_invalid"
    await store_group.close()


@pytest.mark.asyncio
async def test_blocked_and_disabled(tmp_path: Path) -> None:
    """public_key 未配置 → blocked；enabled=False → disabled。"""
    service, store_group = await _build_service(tmp_path, public_key="")
    body = _command_body()
    assert (
        await service.handle_interaction_request(body, _signed_headers(body))
    ).status == "blocked"
    await store_group.close()

    service2, store_group2 = await _build_service(tmp_path, enabled=False)
    assert (
        await service2.handle_interaction_request(body, _signed_headers(body))
    ).status == "disabled"
    await store_group2.close()


@pytest.mark.asyncio
async def test_command_creates_task_idempotent(tmp_path: Path) -> None:
    """US-3 AC-3：DM slash command → task（字段约定）+ 受理回包；同
    interaction_id 重投不重复建 task。"""
    runner = FakeTaskRunner()
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _command_body()
    first = await service.handle_interaction_request(body, _signed_headers(body))

    assert first.status == "accepted"
    assert first.created is True
    assert first.task_id is not None
    assert first.response_payload is not None
    assert first.response_payload["type"] == 4
    assert first.task_id in first.response_payload["data"]["content"]

    task = await store_group.task_store.get_task(first.task_id)
    assert task is not None
    assert task.requester.channel == "discord"
    assert task.scope_id == "chat:discord:CH1"

    events = await store_group.event_store.get_events_for_task(first.task_id)
    user_messages = [e for e in events if str(getattr(e.type, "value", e.type)) == "USER_MESSAGE"]
    metadata = user_messages[0].payload.get("metadata", {})
    assert metadata.get("discord_interaction_id") == "I001"
    assert metadata.get("discord_channel_id") == "CH1"
    assert metadata.get("discord_user_id") == "U_OWNER"

    binding = await store_group.conversation_binding_store.get("discord", "CH1")
    assert binding is not None
    assert binding.agent_profile_id == ""  # H1
    assert binding.metadata.get("conversation_type") == "dm"

    second = await service.handle_interaction_request(body, _signed_headers(body))
    assert second.status == "duplicate"
    assert second.task_id == first.task_id
    await store_group.close()


@pytest.mark.asyncio
async def test_retry_recovers_unenqueued_task(tmp_path: Path) -> None:
    """D17a：首投 enqueue 失败 → 重投补入队；终态后晚到重投不入队。"""
    runner = FakeTaskRunner()
    runner.fail_next = True
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _command_body()

    with pytest.raises(RuntimeError):
        await service.handle_interaction_request(body, _signed_headers(body))
    assert runner.enqueued == []

    recovered = await service.handle_interaction_request(body, _signed_headers(body))
    assert recovered.status == "duplicate"
    assert recovered.task_id is not None
    assert [t for t, _ in runner.enqueued] == [recovered.task_id]

    await store_group.task_store.update_task_status(
        recovered.task_id,
        TaskStatus.SUCCEEDED.value,
        datetime.now(UTC).isoformat(),
        "evt-test",
    )
    late = await service.handle_interaction_request(body, _signed_headers(body))
    assert late.status == "duplicate"
    assert len(runner.enqueued) == 1  # 终态不重入队
    await store_group.close()


@pytest.mark.asyncio
async def test_unauthorized_user_ephemeral_rejection(tmp_path: Path) -> None:
    """US-3 AC-4：非 allowlist user → 不建 task + type 4 ephemeral（flags=64）。"""
    runner = FakeTaskRunner()
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _command_body(user_id="U_STRANGER")
    result = await service.handle_interaction_request(body, _signed_headers(body))

    assert result.status == "unauthorized"
    assert result.task_id is None
    assert result.response_payload is not None
    assert result.response_payload["type"] == 4
    assert result.response_payload["data"]["flags"] == 64
    assert runner.enqueued == []
    assert await store_group.task_store.list_tasks() == []
    await store_group.close()


@pytest.mark.asyncio
async def test_guild_channel_authorization_matrix(tmp_path: Path) -> None:
    """D5（CODEX-M1）：guild interaction 要求 allowed_channels 命中；
    空 allowed_channels = guild 一律拒；DM 不受 allowed_channels 限制。"""
    runner = FakeTaskRunner()
    # 空 allowed_channels：guild 拒
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    guild_body = _command_body(guild_id="G1", channel_id="CH_G")
    result = await service.handle_interaction_request(guild_body, _signed_headers(guild_body))
    assert result.status == "unauthorized"
    assert result.response_payload["data"]["flags"] == 64
    await store_group.close()

    # allowed_channels 命中：guild 接受，binding conversation_type=guild
    service2, store_group2 = await _build_service(
        tmp_path, task_runner=runner, allowed_channels=["CH_G"]
    )
    accepted = await service2.handle_interaction_request(guild_body, _signed_headers(guild_body))
    assert accepted.status == "accepted"
    binding = await store_group2.conversation_binding_store.get("discord", "CH_G")
    assert binding is not None
    assert binding.metadata.get("conversation_type") == "guild"
    await store_group2.close()


@pytest.mark.asyncio
async def test_unsupported_type_and_empty_text(tmp_path: Path) -> None:
    """非 command 交互 → unsupported ephemeral；空 options 文本 → ignored。"""
    service, store_group = await _build_service(tmp_path)
    component = json.dumps({"type": 3, "id": "I9"}).encode()
    result = await service.handle_interaction_request(component, _signed_headers(component))
    assert result.status == "unsupported"
    assert result.response_payload["data"]["flags"] == 64

    empty = json.dumps(
        {
            "type": 2,
            "id": "I010",
            "channel_id": "CH1",
            "user": {"id": "U_OWNER", "username": "owner"},
            "data": {"name": "octo", "options": []},
        }
    ).encode()
    result2 = await service.handle_interaction_request(empty, _signed_headers(empty))
    assert result2.status == "ignored"
    await store_group.close()


@pytest.mark.asyncio
async def test_notify_task_result_rest_message(tmp_path: Path) -> None:
    """US-3 AC-5：discord 来源 task 完成 → REST create_message；他渠道 no-op。"""
    runner = FakeTaskRunner()
    api = FakeDiscordApiClient()
    service, store_group = await _build_service(tmp_path, task_runner=runner, api_client=api)
    body = _command_body()
    result = await service.handle_interaction_request(body, _signed_headers(body))
    assert result.task_id is not None
    await store_group.task_store.update_task_status(
        result.task_id,
        TaskStatus.SUCCEEDED.value,
        datetime.now(UTC).isoformat(),
        "evt-test",
    )
    await service.notify_task_result(result.task_id)
    assert len(api.messages) == 1
    assert api.messages[0][0] == "CH1"

    # 他渠道 task no-op
    from octoagent.core.models.message import NormalizedMessage
    from octoagent.gateway.services.task_service import TaskService

    task_service = TaskService(store_group, SSEHub(), storage_only=True)
    web_task_id, _ = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="t-web",
            scope_id="chat:web:t-web",
            text="web 任务",
            idempotency_key="web:9",
        )
    )
    await service.notify_task_result(web_task_id)
    assert len(api.messages) == 1
    await store_group.close()
