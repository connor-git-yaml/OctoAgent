"""F105 Phase B: SqliteConversationBindingStore + resolve_outbound_route 测试。

覆盖 spec US-3 AC-4（三级策略）+ FR-E1/E2（upsert/touch/三元组唯一）
+ H1 构造性保证（upsert 后 agent_profile_id 恒 ''）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest
from octoagent.core.models.conversation_binding import (
    ConversationBinding,
    ConversationBindingKind,
)
from octoagent.core.store.conversation_binding_store import (
    SqliteConversationBindingStore,
    resolve_outbound_route,
)
from octoagent.core.store.sqlite_init import init_db


@pytest.fixture
async def store(tmp_path):
    conn = await aiosqlite.connect(tmp_path / "test.db")
    conn.row_factory = aiosqlite.Row
    await init_db(conn)
    yield SqliteConversationBindingStore(conn)
    await conn.close()


async def test_runtime_upsert_creates_binding(store) -> None:
    """首次 upsert 新建 runtime 绑定（US-3 AC-1 的 store 层）。"""
    binding = await store.upsert_runtime_binding(
        "telegram",
        "123",
        scope_id="chat:telegram:123",
        project_id="",
    )
    assert binding.platform == "telegram"
    assert binding.account_id == "default"
    assert binding.conversation_id == "123"
    assert binding.scope_id == "chat:telegram:123"
    assert binding.binding_kind is ConversationBindingKind.RUNTIME
    # H1 构造性保证：runtime 写入恒主 Agent
    assert binding.agent_profile_id == ""


async def test_runtime_upsert_touches_existing(store) -> None:
    """同三元组再 upsert 不新增行，touch last_active_at（US-3 AC-2 的 store 层）。"""
    first = await store.upsert_runtime_binding("telegram", "123", scope_id="s1")
    await asyncio.sleep(0.01)
    second = await store.upsert_runtime_binding("telegram", "123", scope_id="s2")

    assert second.binding_id == first.binding_id  # 未新建行
    assert second.last_active_at > first.last_active_at
    assert second.scope_id == "s2"
    assert second.created_at == first.created_at

    rows = await store.list_by_platform("telegram")
    assert len(rows) == 1


async def test_unique_key_distinguishes_conversations(store) -> None:
    """不同 conversation_id / platform 各自成行。"""
    await store.upsert_runtime_binding("telegram", "123")
    await store.upsert_runtime_binding("telegram", "456")
    await store.upsert_runtime_binding("web", "123")

    assert len(await store.list_by_platform("telegram")) == 2
    assert len(await store.list_by_platform("web")) == 1
    assert len(await store.list_recent()) == 3


async def test_same_thread_across_projects_not_collide(store) -> None:
    """Codex pre-impl H3：web 同名 thread 跨 project 各自成行，不互相覆盖。"""
    a = await store.upsert_runtime_binding(
        "web", "default", scope_id="project:proj-a:chat:web:default", project_id="proj-a"
    )
    b = await store.upsert_runtime_binding(
        "web", "default", scope_id="project:proj-b:chat:web:default", project_id="proj-b"
    )

    assert a.binding_id != b.binding_id
    rows = await store.list_by_platform("web")
    assert len(rows) == 2
    got_a = await store.get("web", "default", project_id="proj-a")
    got_b = await store.get("web", "default", project_id="proj-b")
    assert got_a is not None and got_a.scope_id == "project:proj-a:chat:web:default"
    assert got_b is not None and got_b.scope_id == "project:proj-b:chat:web:default"


async def test_runtime_upsert_metadata_refreshes(store) -> None:
    """telegram thread 维度经 metadata 滚动记录（Codex H3 metadata 路线）。"""
    await store.upsert_runtime_binding(
        "telegram", "123", metadata={"last_message_thread_id": "7"}
    )
    refreshed = await store.upsert_runtime_binding(
        "telegram", "123", metadata={"last_message_thread_id": "9"}
    )
    assert refreshed.metadata == {"last_message_thread_id": "9"}


async def test_get_missing_returns_none(store) -> None:
    assert await store.get("telegram", "missing") is None


def _make_binding(
    platform: str,
    conversation_id: str,
    *,
    kind: ConversationBindingKind = ConversationBindingKind.RUNTIME,
    last_active_offset_s: float = 0.0,
) -> ConversationBinding:
    base = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    ts = base + timedelta(seconds=last_active_offset_s)
    return ConversationBinding(
        binding_id=f"convb-{platform}-{conversation_id}",
        platform=platform,
        conversation_id=conversation_id,
        binding_kind=kind,
        last_active_at=ts,
        created_at=ts,
        updated_at=ts,
    )


def test_resolve_outbound_route_three_tiers() -> None:
    """US-3 AC-4：explicit → last_active(runtime) → single-configured → None。"""
    runtime_old = _make_binding("telegram", "111", last_active_offset_s=0)
    runtime_new = _make_binding("web", "t-1", last_active_offset_s=10)
    configured = _make_binding(
        "telegram",
        "999",
        kind=ConversationBindingKind.CONFIGURED,
        last_active_offset_s=99,  # 配置时间更新，但不该压过真实 runtime 活跃
    )
    bindings = [runtime_old, runtime_new, configured]

    # 1) explicit 精确命中
    explicit_hit = resolve_outbound_route(
        bindings, explicit=("telegram", "111")
    )
    assert explicit_hit is runtime_old

    # explicit 未命中 → 落到下一级
    fallback = resolve_outbound_route(bindings, explicit=("slack", "x"))
    assert fallback is runtime_new

    # 2) 无 explicit → runtime 中 last_active 最新（configured 时间戳不参与）
    assert resolve_outbound_route(bindings) is runtime_new

    # 3) 无 runtime → 唯一 configured
    assert resolve_outbound_route([configured]) is configured

    # 3') configured 多条歧义 → None
    configured_2 = _make_binding(
        "web", "t-9", kind=ConversationBindingKind.CONFIGURED
    )
    assert resolve_outbound_route([configured, configured_2]) is None

    # 4) 空集 → None
    assert resolve_outbound_route([]) is None


# ---------------------------------------------------------------------------
# F105 v0.2 Phase D：CONFIGURED 写入面 + 活跃信号解耦 + resolver v2
# ---------------------------------------------------------------------------


async def test_configured_upsert_h1_rejects_agent_profile(store) -> None:
    """US-4 AC-4（D13 H1 单点校验）：agent_profile_id 非空必 raise。"""
    with pytest.raises(ValueError, match="H1"):
        await store.upsert_configured_binding(
            "slack", "C1", agent_profile_id="wkr-x"
        )
    assert await store.get("slack", "C1") is None  # 未写入


async def test_configured_upsert_creates_and_is_idempotent(store) -> None:
    """US-4 AC-1 store 层：新建 CONFIGURED 行（活跃证据 NULL）+ 重复幂等。"""
    first = await store.upsert_configured_binding(
        "slack", "C1", scope_id="chat:slack:C1"
    )
    assert first.binding_kind is ConversationBindingKind.CONFIGURED
    assert first.agent_profile_id == ""
    assert first.last_runtime_active_at is None  # 配置不伪造活跃证据

    second = await store.upsert_configured_binding(
        "slack", "C1", scope_id="chat:slack:C1"
    )
    assert second.binding_id == first.binding_id  # 同行 upsert
    rows = await store.list_by_platform("slack")
    assert len(rows) == 1


async def test_configured_upgrades_runtime_not_reverse(store) -> None:
    """R7 棘轮：runtime → configured 单向升级；runtime upsert 不降级。"""
    await store.upsert_runtime_binding("slack", "D1", scope_id="chat:slack:D1")
    upgraded = await store.upsert_configured_binding(
        "slack", "D1", scope_id="chat:slack:D1"
    )
    assert upgraded.binding_kind is ConversationBindingKind.CONFIGURED
    # D17b：升级保留 runtime 活跃证据
    assert upgraded.last_runtime_active_at is not None

    touched = await store.upsert_runtime_binding(
        "slack", "D1", scope_id="chat:slack:D1"
    )
    assert touched.binding_kind is ConversationBindingKind.CONFIGURED  # 不降级
    assert touched.last_runtime_active_at is not None  # runtime 活跃证据刷新


async def test_configured_upgrade_keeps_runtime_activity_rank(store) -> None:
    """CODEX-H3 回归：活跃会话被配置升级后仍赢得 last-route 排序——
    不再被平台残留的旧 runtime 行压过。"""
    await store.upsert_runtime_binding("slack", "D_OLD", scope_id="chat:slack:D_OLD")
    await asyncio.sleep(0.01)
    await store.upsert_runtime_binding("slack", "D_HOT", scope_id="chat:slack:D_HOT")
    # 把"最活跃"的会话配置为 default notify → kind 升级
    await store.upsert_configured_binding("slack", "D_HOT", scope_id="chat:slack:D_HOT")

    bindings = await store.list_by_platform("slack")
    route = resolve_outbound_route(bindings)
    assert route is not None
    assert route.conversation_id == "D_HOT"  # v0.1 语义下这里会错选 D_OLD


async def test_configured_tier_and_runtime_precedence(store) -> None:
    """US-4 AC-2/AC-3：仅 CONFIGURED → tier 3 命中；runtime 活跃证据出现后
    tier 2 优先（"用户最后私聊的地方"压配置兜底）。"""
    await store.upsert_configured_binding("slack", "C_CFG", scope_id="chat:slack:C_CFG")
    bindings = await store.list_by_platform("slack")
    assert resolve_outbound_route(bindings).conversation_id == "C_CFG"

    await asyncio.sleep(0.01)
    await store.upsert_runtime_binding("slack", "D_DM", scope_id="chat:slack:D_DM")
    bindings = await store.list_by_platform("slack")
    assert resolve_outbound_route(bindings).conversation_id == "D_DM"


def test_resolver_v2_runtime_only_and_configured_only_unchanged() -> None:
    """FR-D6 ⑤：纯 runtime / 纯 configured 集合的行为与 v0.1 等价
    （手工构造行无新列值——RUNTIME 走 last_active_at 兜底）。"""
    runtime_old = _make_binding("telegram", "111", last_active_offset_s=0)
    runtime_new = _make_binding("web", "t-1", last_active_offset_s=10)
    assert resolve_outbound_route([runtime_old, runtime_new]) is runtime_new

    configured = _make_binding(
        "telegram", "999", kind=ConversationBindingKind.CONFIGURED
    )
    assert resolve_outbound_route([configured]) is configured
    assert resolve_outbound_route([]) is None


# ---------------------------------------------------------------------------
# F105 v0.2 Final CODEX-F-H1：config-managed CONFIGURED reconcile
# ---------------------------------------------------------------------------


async def test_reconcile_replaces_stale_config_target(store) -> None:
    """配置 C1 → 改 C2：旧 config-managed 行被撤销（无活跃证据 → 删除），
    不再出现双 CONFIGURED 歧义。"""
    await store.reconcile_config_managed_binding(
        "slack", "C1", scope_id="chat:slack:C1"
    )
    await store.reconcile_config_managed_binding(
        "slack", "C2", scope_id="chat:slack:C2"
    )
    rows = await store.list_by_platform("slack")
    assert [r.conversation_id for r in rows] == ["C2"]
    assert rows[0].metadata.get("source") == "default_notify_config"

    route = resolve_outbound_route(rows)
    assert route is not None and route.conversation_id == "C2"


async def test_reconcile_demotes_active_stale_target(store) -> None:
    """旧 config 目标有 runtime 活跃证据 → 降级回 RUNTIME（last-route 数据
    保留、剥离 source 标记），不再以 CONFIGURED 身份接收通知。"""
    await store.upsert_runtime_binding(
        "slack", "C1", scope_id="chat:slack:C1",
        metadata={"conversation_type": "channel"},
    )
    await store.reconcile_config_managed_binding(
        "slack", "C1", scope_id="chat:slack:C1"
    )
    await store.reconcile_config_managed_binding(
        "slack", "C2", scope_id="chat:slack:C2"
    )

    old = await store.get("slack", "C1")
    assert old is not None
    assert old.binding_kind is ConversationBindingKind.RUNTIME  # 降级
    assert "source" not in old.metadata  # 标记剥离
    assert old.metadata.get("conversation_type") == "channel"  # ingest 数据保留
    new = await store.get("slack", "C2")
    assert new is not None
    assert new.binding_kind is ConversationBindingKind.CONFIGURED


async def test_reconcile_empty_target_revokes(store) -> None:
    """配置清空（target=""）→ 仅撤销旧 config-managed 行，不写新行。"""
    await store.reconcile_config_managed_binding(
        "slack", "C1", scope_id="chat:slack:C1"
    )
    result = await store.reconcile_config_managed_binding("slack", "")
    assert result is None
    assert await store.list_by_platform("slack") == []


async def test_reconcile_merges_metadata_on_existing_runtime(store) -> None:
    """目标行已有 runtime 痕迹 → metadata merge（不丢 conversation_type）+
    活跃证据保留（D17b 不变量贯穿 reconcile）。"""
    await store.upsert_runtime_binding(
        "slack", "D1", scope_id="chat:slack:D1",
        metadata={"conversation_type": "im"},
    )
    upgraded = await store.reconcile_config_managed_binding(
        "slack", "D1", scope_id="chat:slack:D1"
    )
    assert upgraded is not None
    assert upgraded.binding_kind is ConversationBindingKind.CONFIGURED
    assert upgraded.metadata.get("conversation_type") == "im"  # merge 保留
    assert upgraded.metadata.get("source") == "default_notify_config"
    assert upgraded.last_runtime_active_at is not None  # 活跃证据保留
