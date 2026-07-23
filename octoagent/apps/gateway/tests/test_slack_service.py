"""F105 v0.2 Phase B: SlackGatewayService 测试。

覆盖 spec US-2 AC-1（url_verification）/ AC-2（DM 全链路 + binding）/
AC-3（event_id 幂等）/ AC-5（bot/subtype/未授权拒绝语义）/ AC-6（完成回复
进原 thread + 他渠道 no-op）/ AC-8（D17a 重试恢复 + 终态不重入队）+
D5 授权矩阵（非 DM 双条件 / 空 allowed_channels 拒 / team_id 边界）。
签名全部本地现算（不依赖外部网络）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from octoagent.core.models import TaskStatus
from octoagent.core.store import create_store_group
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    OctoAgentConfig,
    SlackChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from octoagent.gateway.services.slack import SlackGatewayService
from octoagent.gateway.services.sse_hub import SSEHub

_SECRET = "test-signing-secret"
_ENVIRON = {"SLACK_SIGNING_SECRET": _SECRET, "SLACK_BOT_TOKEN": "xoxb-test"}


def _write_config(project_root: Path, **slack_overrides: object) -> None:
    slack_config: dict[str, object] = {
        "enabled": True,
        "allow_users": ["U_OWNER"],
    }
    slack_config.update(slack_overrides)
    save_config(
        OctoAgentConfig(
            updated_at="2026-06-12",
            channels=ChannelsConfig(slack=SlackChannelConfig(**slack_config)),
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
            raise RuntimeError("模拟 enqueue 失败（落盘未入队窗口）")
        self.enqueued.append((task_id, user_text))


class FakeSlackApiClient:
    def __init__(self) -> None:
        self.posted: list[tuple[str, str, str | None]] = []

    def load_bot_token(self) -> str | None:
        return "xoxb-test"

    async def post_message(
        self, channel: str, text: str, *, thread_ts: str | None = None
    ) -> dict[str, object]:
        self.posted.append((channel, text, thread_ts))
        return {"ok": True}


def _sign(body: bytes, ts: str) -> str:
    base = b"v0:" + ts.encode() + b":" + body
    return "v0=" + hmac.new(_SECRET.encode(), base, hashlib.sha256).hexdigest()


def _signed_headers(body: bytes, ts: str | None = None) -> dict[str, str]:
    ts = ts if ts is not None else str(int(time.time()))
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": _sign(body, ts),
    }


def _message_envelope(
    *,
    event_id: str = "Ev001",
    channel: str = "D_DM1",
    user: str = "U_OWNER",
    text: str = "帮我查一下天气",
    channel_type: str = "im",
    ts: str = "1718000000.000100",
    thread_ts: str = "",
    team_id: str = "T_TEAM",
    extra_event: dict[str, object] | None = None,
) -> bytes:
    event: dict[str, object] = {
        "type": "message",
        "channel": channel,
        "user": user,
        "text": text,
        "ts": ts,
        "channel_type": channel_type,
    }
    if thread_ts:
        event["thread_ts"] = thread_ts
    if extra_event:
        event.update(extra_event)
    return json.dumps(
        {
            "type": "event_callback",
            "team_id": team_id,
            "event_id": event_id,
            "event": event,
        }
    ).encode()


async def _build_service(
    tmp_path: Path,
    *,
    task_runner: FakeTaskRunner | None = None,
    api_client: FakeSlackApiClient | None = None,
    **config_overrides: object,
):
    _write_config(tmp_path, **config_overrides)
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    service = SlackGatewayService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        api_client=api_client,
        task_runner=task_runner,
        environ=_ENVIRON,
    )
    return service, store_group


@pytest.mark.asyncio
async def test_url_verification_challenge(tmp_path: Path) -> None:
    """US-2 AC-1：合法签名的 url_verification 返回 challenge。"""
    service, store_group = await _build_service(tmp_path)
    body = json.dumps({"type": "url_verification", "challenge": "ch-123"}).encode()
    result = await service.handle_event_request(body, _signed_headers(body))
    assert result.status == "url_verification"
    assert result.challenge == "ch-123"
    await store_group.close()


@pytest.mark.asyncio
async def test_dm_message_creates_task_and_binding(tmp_path: Path) -> None:
    """US-2 AC-2：allowlisted DM → task（字段约定）+ enqueue + runtime binding。"""
    runner = FakeTaskRunner()
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _message_envelope()
    result = await service.handle_event_request(body, _signed_headers(body))

    assert result.status == "accepted"
    assert result.created is True
    assert result.task_id is not None
    assert [t for t, _ in runner.enqueued] == [result.task_id]

    task = await store_group.task_store.get_task(result.task_id)
    assert task is not None
    assert task.requester.channel == "slack"
    assert task.scope_id == "chat:slack:D_DM1"

    events = await store_group.event_store.get_events_for_task(result.task_id)
    user_messages = [e for e in events if str(getattr(e.type, "value", e.type)) == "USER_MESSAGE"]
    assert user_messages, "应有 USER_MESSAGE 事件"
    metadata = user_messages[0].payload.get("metadata", {})
    assert metadata.get("slack_channel_id") == "D_DM1"
    assert metadata.get("slack_user_id") == "U_OWNER"
    assert metadata.get("slack_event_id") == "Ev001"
    assert metadata.get("slack_ts") == "1718000000.000100"

    binding = await store_group.conversation_binding_store.get("slack", "D_DM1")
    assert binding is not None
    assert binding.agent_profile_id == ""  # H1
    assert binding.scope_id == "chat:slack:D_DM1"
    assert binding.metadata.get("conversation_type") == "im"
    await store_group.close()


@pytest.mark.asyncio
async def test_event_id_idempotent_on_retry(tmp_path: Path) -> None:
    """US-2 AC-3：同 event_id 重投不重复建 task（且正常路径不重复 enqueue）。"""
    runner = FakeTaskRunner()
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _message_envelope()
    first = await service.handle_event_request(body, _signed_headers(body))
    second = await service.handle_event_request(body, _signed_headers(body))

    assert first.status == "accepted"
    assert second.status == "duplicate"
    assert second.task_id == first.task_id
    # 正常完成 enqueue 后任务已离开 CREATED（job QUEUED 由 FakeRunner 模拟不了
    # 状态推进，此处验证 service 层不因 duplicate 重复调用 enqueue——
    # task 仍 CREATED 时 D17a 会补调，幂等去重由真实 create_job 保证，
    # 见 test_retry_recovers_unenqueued_task / test_late_retry_after_success）
    assert len(runner.enqueued) == 2  # created 1 次 + D17a 补调 1 次（task 仍 CREATED）
    await store_group.close()


@pytest.mark.asyncio
async def test_retry_recovers_unenqueued_task(tmp_path: Path) -> None:
    """US-2 AC-8（D17a 核心）：首投 enqueue 失败 → 重投 duplicate 且补 enqueue。"""
    runner = FakeTaskRunner()
    runner.fail_next = True
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _message_envelope()

    with pytest.raises(RuntimeError):
        await service.handle_event_request(body, _signed_headers(body))
    assert runner.enqueued == []  # 落盘未入队窗口

    result = await service.handle_event_request(body, _signed_headers(body))
    assert result.status == "duplicate"
    assert result.task_id is not None
    assert [t for t, _ in runner.enqueued] == [result.task_id]  # 补入队恢复
    await store_group.close()


@pytest.mark.asyncio
async def test_late_retry_after_success_no_requeue(tmp_path: Path) -> None:
    """US-2 AC-8 反向：task 已 SUCCEEDED 的晚到重投不再入队（状态守卫）。"""
    runner = FakeTaskRunner()
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    body = _message_envelope()
    first = await service.handle_event_request(body, _signed_headers(body))
    assert first.task_id is not None
    await store_group.task_store.update_task_status(
        first.task_id,
        TaskStatus.SUCCEEDED.value,
        datetime.now(UTC).isoformat(),
        "evt-test",
    )

    result = await service.handle_event_request(body, _signed_headers(body))
    assert result.status == "duplicate"
    assert len(runner.enqueued) == 1  # 不重入队
    await store_group.close()


@pytest.mark.asyncio
async def test_signature_and_timestamp_rejections(tmp_path: Path) -> None:
    """US-2 AC-4 服务层：签名不符 / 时间戳超窗 / 缺 header。"""
    service, store_group = await _build_service(tmp_path)
    body = _message_envelope()

    bad_sig = dict(_signed_headers(body))
    bad_sig["X-Slack-Signature"] = "v0=" + "0" * 64
    assert (await service.handle_event_request(body, bad_sig)).status == ("signature_invalid")

    stale_ts = str(int(time.time()) - 3600)
    stale = _signed_headers(body, ts=stale_ts)
    assert (await service.handle_event_request(body, stale)).status == ("timestamp_stale")

    assert (await service.handle_event_request(body, {})).status == ("signature_invalid")
    await store_group.close()


@pytest.mark.asyncio
async def test_blocked_and_disabled(tmp_path: Path) -> None:
    """secret env 缺失 → blocked；enabled=False → disabled。"""
    service, store_group = await _build_service(tmp_path)
    service._environ = {}  # 注入空 environ：SLACK_SIGNING_SECRET 不可解析
    body = _message_envelope()
    assert (await service.handle_event_request(body, _signed_headers(body))).status == ("blocked")
    await store_group.close()

    service2, store_group2 = await _build_service(tmp_path, enabled=False)
    assert (await service2.handle_event_request(body, _signed_headers(body))).status == "disabled"
    await store_group2.close()


@pytest.mark.asyncio
async def test_unauthorized_and_bot_and_subtype_ignored(tmp_path: Path) -> None:
    """US-2 AC-5：bot 消息 / subtype / 非 allowlist user → 不建 task。"""
    runner = FakeTaskRunner()
    service, store_group = await _build_service(tmp_path, task_runner=runner)

    bot = _message_envelope(extra_event={"bot_id": "B999"})
    assert (await service.handle_event_request(bot, _signed_headers(bot))).status == ("ignored")

    subtype = _message_envelope(extra_event={"subtype": "message_changed"})
    assert (
        await service.handle_event_request(subtype, _signed_headers(subtype))
    ).status == "ignored"

    stranger = _message_envelope(user="U_STRANGER")
    assert (
        await service.handle_event_request(stranger, _signed_headers(stranger))
    ).status == "unauthorized"

    assert runner.enqueued == []
    assert await store_group.task_store.list_tasks() == []
    await store_group.close()


@pytest.mark.asyncio
async def test_channel_authorization_matrix(tmp_path: Path) -> None:
    """D5（CODEX-M1）：非 DM 双条件；空 allowed_channels = 非 DM 一律拒。"""
    runner = FakeTaskRunner()
    # 默认 allowed_channels=[] → 频道消息即便 user allowlisted 也拒
    service, store_group = await _build_service(tmp_path, task_runner=runner)
    channel_msg = _message_envelope(channel="C_PUB", channel_type="channel")
    assert (
        await service.handle_event_request(channel_msg, _signed_headers(channel_msg))
    ).status == "unauthorized"
    await store_group.close()

    # allowed_channels 命中 + user 命中 → 接受
    service2, store_group2 = await _build_service(
        tmp_path, task_runner=runner, allowed_channels=["C_PUB"]
    )
    accepted = await service2.handle_event_request(channel_msg, _signed_headers(channel_msg))
    assert accepted.status == "accepted"
    binding = await store_group2.conversation_binding_store.get("slack", "C_PUB")
    assert binding is not None
    assert binding.metadata.get("conversation_type") == "channel"
    await store_group2.close()


@pytest.mark.asyncio
async def test_team_id_boundary(tmp_path: Path) -> None:
    """D5（CODEX-M1）：team_id 配置后，异 workspace event 拒绝。"""
    service, store_group = await _build_service(tmp_path, team_id="T_TEAM")
    ok_body = _message_envelope(team_id="T_TEAM")
    assert (
        await service.handle_event_request(ok_body, _signed_headers(ok_body))
    ).status == "accepted"

    foreign = _message_envelope(event_id="Ev002", team_id="T_EVIL")
    assert (
        await service.handle_event_request(foreign, _signed_headers(foreign))
    ).status == "unauthorized"
    await store_group.close()


@pytest.mark.asyncio
async def test_notify_task_result_replies_in_thread(tmp_path: Path) -> None:
    """US-2 AC-6：完成回复回原 channel 原 thread（thread_ts=原 ts）。"""
    runner = FakeTaskRunner()
    api = FakeSlackApiClient()
    service, store_group = await _build_service(tmp_path, task_runner=runner, api_client=api)
    body = _message_envelope()
    result = await service.handle_event_request(body, _signed_headers(body))
    assert result.task_id is not None
    await store_group.task_store.update_task_status(
        result.task_id,
        TaskStatus.SUCCEEDED.value,
        datetime.now(UTC).isoformat(),
        "evt-test",
    )

    await service.notify_task_result(result.task_id)
    assert len(api.posted) == 1
    channel, text, thread_ts = api.posted[0]
    assert channel == "D_DM1"
    assert thread_ts == "1718000000.000100"  # 顶层消息 → 以原 ts 开 thread
    assert text  # 终态文本非空（SUCCEEDED 默认文案或 summary）
    await store_group.close()


@pytest.mark.asyncio
async def test_foreign_channel_task_noop(tmp_path: Path) -> None:
    """US-2 AC-6 guard：非 slack 渠道 task 不发消息。"""
    api = FakeSlackApiClient()
    service, store_group = await _build_service(tmp_path, api_client=api)
    from octoagent.core.models.message import NormalizedMessage
    from octoagent.gateway.services.task_service import TaskService

    task_service = TaskService(store_group, SSEHub(), storage_only=True)
    task_id, _ = await task_service.create_task(
        NormalizedMessage(
            channel="web",
            thread_id="t-web",
            scope_id="chat:web:t-web",
            text="web 任务",
            idempotency_key="web:1",
        )
    )
    await service.notify_task_result(task_id)
    assert api.posted == []
    await store_group.close()


@pytest.mark.asyncio
async def test_malformed_signature_headers_rejected_not_500(tmp_path: Path) -> None:
    """Final CODEX-F-M1：公网入口对畸形认证头必须拒绝（401 语义）而非 500——
    inf/超大浮点时间戳（OverflowError 路径）与非 ASCII 签名
    （compare_digest TypeError 路径）全部走 signature_invalid。"""
    service, store_group = await _build_service(tmp_path)
    body = _message_envelope()

    for bad_ts in ("inf", "1e400", "nan", "-1", "12.5", "9" * 13, "abc"):
        headers = {
            "X-Slack-Request-Timestamp": bad_ts,
            "X-Slack-Signature": _sign(body, bad_ts),
        }
        result = await service.handle_event_request(body, headers)
        assert result.status == "signature_invalid", f"ts={bad_ts!r} 应拒绝"

    ts = str(int(time.time()))
    for bad_sig in ("v0=ümlaut" + "0" * 57, "v1=" + "0" * 64, "v0=" + "0" * 63, "v0=" + "G" * 64):
        headers = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": bad_sig}
        result = await service.handle_event_request(body, headers)
        assert result.status == "signature_invalid", f"sig={bad_sig!r} 应拒绝"

    assert await store_group.task_store.list_tasks() == []  # 全部未触达 ingest
    await store_group.close()
