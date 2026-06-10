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
