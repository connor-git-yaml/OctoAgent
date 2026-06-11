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


# ---------------------------------------------------------------------------
# F105 v0.2 Phase D：CONFIGURED tier 消费 + bootstrap 写入 + H1 v02
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_fallback_when_no_dm_runtime(tmp_path: Path) -> None:
    """US-4 AC-2 渠道层：无 DM runtime 时 CONFIGURED 频道兜底可收通知
    （显式配置 = 通知同意，频道也行）。"""
    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    binding_store = store_group.conversation_binding_store
    await binding_store.upsert_configured_binding(
        "slack", "C_NOTIFY", scope_id="chat:slack:C_NOTIFY"
    )
    # 多人频道 runtime 行存在也不该被选（eligibility 过滤掉）
    await binding_store.upsert_runtime_binding(
        "slack", "C_PUBLIC", scope_id="chat:slack:C_PUBLIC",
        metadata={"conversation_type": "channel"},
    )

    send = _SendRecorder()
    channel = SlackNotificationChannel(send_fn=send, binding_store=binding_store)
    ok = await channel.notify("task-1", "TASK_STATE_CHANGED", _payload())
    assert ok is True
    assert [c for c, _ in send.sent] == ["C_NOTIFY"]
    await store_group.close()


@pytest.mark.asyncio
async def test_h1_no_agent_profile_write_path_v02(tmp_path: Path) -> None:
    """US-4 AC-5：v0.2 全部写入路径产出的行 agent_profile_id 恒 ''；
    runtime 入口签名仍无该参；configured 入口非空必 raise。"""
    import inspect

    from octoagent.core.store.conversation_binding_store import (
        SqliteConversationBindingStore,
    )

    runtime_sig = inspect.signature(
        SqliteConversationBindingStore.upsert_runtime_binding
    )
    assert "agent_profile_id" not in runtime_sig.parameters

    configured_sig = inspect.signature(
        SqliteConversationBindingStore.upsert_configured_binding
    )
    assert "agent_profile_id" in configured_sig.parameters  # 校验点物理存在

    store_group = await create_store_group(
        str(tmp_path / "g.db"), str(tmp_path / "artifacts")
    )
    binding_store = store_group.conversation_binding_store
    await binding_store.upsert_runtime_binding("slack", "D1", scope_id="s")
    await binding_store.upsert_configured_binding("discord", "C1", scope_id="s2")
    with pytest.raises(ValueError):
        await binding_store.upsert_configured_binding(
            "discord", "C2", agent_profile_id="wkr-1"
        )

    rows = await binding_store.list_recent()
    assert len(rows) == 2
    assert all(row.agent_profile_id == "" for row in rows)
    await store_group.close()


@pytest.mark.asyncio
async def test_bootstrap_writes_configured_binding_idempotent(tmp_path: Path) -> None:
    """US-4 AC-1：default_notify_channel 配置后，harness bootstrap 写入
    CONFIGURED binding（agent_profile_id=''）；重启（二次 bootstrap）幂等。"""
    import os

    from octoagent.gateway.services.config.config_schema import (
        ChannelsConfig,
        OctoAgentConfig,
        SlackChannelConfig,
    )
    from octoagent.gateway.services.config.config_wizard import save_config

    save_config(
        OctoAgentConfig(
            updated_at="2026-06-12",
            channels=ChannelsConfig(
                slack=SlackChannelConfig(
                    enabled=True,
                    default_notify_channel="C_BOOT",
                )
            ),
        ),
        tmp_path,
    )
    env_pairs = {
        "OCTOAGENT_DB_PATH": str(tmp_path / "data" / "sqlite" / "test.db"),
        "OCTOAGENT_ARTIFACTS_DIR": str(tmp_path / "data" / "artifacts"),
        "OCTOAGENT_PROJECT_ROOT": str(tmp_path),
        "OCTOAGENT_LLM_MODE": "echo",
        "LOGFIRE_SEND_TO_LOGFIRE": "false",
    }
    for key, value in env_pairs.items():
        os.environ[key] = value
    try:
        from octoagent.gateway.main import create_app

        for _boot in range(2):  # 二次 bootstrap = 重启幂等
            application = create_app()
            async with application.router.lifespan_context(application):
                pass

        store_group = await create_store_group(
            env_pairs["OCTOAGENT_DB_PATH"],
            env_pairs["OCTOAGENT_ARTIFACTS_DIR"],
        )
        rows = await store_group.conversation_binding_store.list_by_platform("slack")
        assert len(rows) == 1
        binding = rows[0]
        assert binding.conversation_id == "C_BOOT"
        assert binding.binding_kind.value == "configured"
        assert binding.agent_profile_id == ""  # H1
        assert binding.scope_id == "chat:slack:C_BOOT"
        assert binding.last_runtime_active_at is None  # 配置不伪造活跃证据
        await store_group.close()
    finally:
        for key in env_pairs:
            os.environ.pop(key, None)
