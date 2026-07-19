"""F127 Phase F — H1 守界 + 端到端链路贯通验证（AC-5 / AC-6 / AC-7 / NFR-5）。

全链：cron 触发（`_run_consolidation`）→ 合成 root spawn（编排层）→ 发现端产提议
（**真** ConsolidationDiscoveryService + 注入式 fake LLM）→ **REST** accept/reject
（真 FastAPI 路由 + ConsolidationApprovalService）→ MERGE commit（源 SUPERSEDED，
真 SQL 断言）→ 通知 emit（**真** NotificationService + channel stub）。

**断言原则（反 mock 自证）**：
- 事件链断言**真查 event_store**（`get_events_for_task` 按 task_seq 正序），断言
  MEMORY_CONSOLIDATION_* 精确序列（NFR-5：trigger→propose→complete→approve/reject
  全链可查）。
- SOR 状态断言**真查 memory_sor 表**（raw SQL，非 service 返回值自证）。
- 通知断言到 channel 层捕获 + NOTIFICATION_DISPATCHED 审计事件双重验证。

**H1 验证（AC-5）**：全链无 user-facing 对话输出——root task 事件宇宙 ⊆
{MEMORY_CONSOLIDATION_*, NOTIFICATION_DISPATCHED}（无 USER_MESSAGE / A2A_MESSAGE_* /
MODEL_CALL_* 等对话/执行事件）；用户感知仅 ①系统级通知（非 Agent 说话，无 session）
②候选列表主动审查（REST）。

组件真伪清单（e2e 保真度归档）：
- 真：StoreGroup（SQLite）/ MemoryService 写管道 / ConsolidationStore /
  ConsolidationDiscoveryService / ConsolidationApprovalService / REST 路由 /
  NotificationService / event_store 审计链
- 注入/stub：LLM（fake 返回固定合并组——LLM 判断力属强 model 验证域，Phase Verify）/
  DelegationPlane（捕获 spawn_child 契约，不真启 task_runner——spawn 编排契约已在
  trigger 测试 + capability_pack_phase_d 测试覆盖）/ 通知 channel（捕获推送）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import aiosqlite
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from octoagent.core.models.enums import EventType
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.routes import consolidation_candidates as route_mod
from octoagent.gateway.routes import notifications as notifications_route_mod
from octoagent.gateway.services.consolidation_discovery import (
    ConsolidationDiscoveryService,
)
from octoagent.gateway.services.delegation_plane import SpawnChildResult
from octoagent.gateway.services.memory_consolidation import (
    CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE,
    CONSOLIDATION_ROOT_TASK_ID,
    MemoryConsolidationService,
)
from octoagent.gateway.services.notification import NotificationService
from octoagent.memory import MemoryPartition, MemoryService, WriteAction
from octoagent.memory.store import ConsolidationStore
from octoagent.memory.store.sqlite_init import init_memory_db

_SCOPE = "agent-private/main"

_USER_MD_ACTIVE = """# 用户档案

- **consolidation_active**: true
- **consolidation_time**: "03:00"
"""


# ============================================================
# Fakes（channel / plane / LLM / snapshot / scheduler）
# ============================================================


class _FakeSnapshotStore:
    def __init__(self, user_md: str | None = None) -> None:
        self._user_md = user_md or ""

    def get_live_state(self, key: str) -> str | None:
        if key == "USER.md":
            return self._user_md
        return None


class _FakeScheduler:
    def __init__(self) -> None:
        self._scheduler = MagicMock()
        self._scheduler.add_job = MagicMock()
        self._scheduler.remove_job = MagicMock()


class _FakePlane:
    """捕获 spawn_child（H2 审计容器契约在 trigger/capability_pack 测试已覆盖）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def spawn_child(self, **kwargs: Any) -> SpawnChildResult:
        self.calls.append(kwargs)
        return SpawnChildResult(status="written", task_id="child-e2e")


class _FakeLLM:
    """注入式发现端 LLM（返回固定 JSON——LLM 判断力属强 model 验证域）。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: int = 0

    async def complete(
        self, messages: list[dict[str, str]], model_alias: str = "main", **kwargs: Any
    ) -> Any:
        self.calls += 1
        content = self._content

        class _R:
            pass

        r = _R()
        r.content = content
        return r


class _FakeChannel:
    def __init__(self, channel_name: str) -> None:
        self._channel_name = channel_name
        self.calls: list[tuple[str, str, dict]] = []

    @property
    def channel_name(self) -> str:
        return self._channel_name

    async def notify(self, task_id: str, event_type: str, payload: dict) -> None:
        self.calls.append((task_id, event_type, payload))

    async def dismiss(self, notification_id: str) -> None:
        return None


# ============================================================
# Fixtures / 链路装配
# ============================================================


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    sg = await create_store_group(
        db_path=str(tmp_path / "test.db"), artifacts_dir=str(artifacts_dir)
    )
    # memory 表（含 consolidation_candidates）加到同一连接（同 harness bootstrap）
    await init_memory_db(sg.conn)
    yield sg
    await sg.close()


@pytest_asyncio.fixture
def client(store_group: StoreGroup) -> TestClient:
    """真 REST 客户端（candidates + notifications 路由，同 production include_router）。

    app.state.notification_service 由测试在 _build_chain 后挂上（GET /api/notifications
    全局收件箱验证用，Codex P2）。
    """
    app = FastAPI()
    app.state.store_group = store_group
    app.include_router(route_mod.router)
    app.include_router(notifications_route_mod.router)
    return TestClient(app)


async def _seed_main_runtime_with_namespace(store_group: StoreGroup) -> None:
    from octoagent.core.models.agent_context import (
        AgentRuntime,
        AgentRuntimeRole,
        MemoryNamespace,
        MemoryNamespaceKind,
    )

    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id="main-rt-e2e",
            project_id="proj-e2e",
            role=AgentRuntimeRole.MAIN,
        )
    )
    await store_group.agent_context_store.save_memory_namespace(
        MemoryNamespace(
            namespace_id="ns-e2e",
            project_id="proj-e2e",
            agent_runtime_id="main-rt-e2e",
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            memory_scope_ids=[_SCOPE],
        )
    )
    await store_group.conn.commit()


async def _seed_redundant_facts(store_group: StoreGroup) -> list[str]:
    """种 3 条同主题冗余事实（时区），返回 sor_ids。"""
    memory = MemoryService(store_group.conn)
    sor_ids: list[str] = []
    for key, content in (
        ("tz.a", "用户时区是上海"),
        ("tz.b", "时区 Asia/Shanghai"),
        ("tz.c", "用户在中国上海（UTC+8）"),
    ):
        r = await memory.fast_commit(
            scope_id=_SCOPE, partition=MemoryPartition.PROFILE,
            action=WriteAction.ADD, subject_key=key, content=content,
            confidence=1.0,
        )
        sor_ids.append(r.sor_id)
    return sor_ids


def _merge_llm_json(sor_ids: list[str]) -> str:
    return json.dumps(
        {
            "groups": [
                {
                    "source_ids": sor_ids,
                    "merged_content": "用户时区 Asia/Shanghai（上海，UTC+8）",
                    "subject_key": "timezone",
                    "rationale": "三条同指用户时区",
                    "confidence": 0.95,
                }
            ]
        },
        ensure_ascii=False,
    )


def _build_chain(
    store_group: StoreGroup, *, llm_content: str
) -> tuple[
    MemoryConsolidationService,
    _FakeChannel,
    _FakeChannel,
    _FakePlane,
    NotificationService,
]:
    """装配全链服务（发现端 runner 装配镜像 harness `_consolidation_discovery_runner`）。"""
    llm = _FakeLLM(llm_content)

    async def _discovery_runner(
        *, run_id: str, scope_id: str, root_task_id: str,
        window_days: int, max_facts: int,
    ):
        memory_service = MemoryService(store_group.conn)
        discovery = ConsolidationDiscoveryService(
            memory_service=memory_service,
            memory_store=memory_service._store,  # type: ignore[attr-defined]
            consolidation_store=ConsolidationStore(store_group.conn),
            event_store=store_group.event_store,
            llm_client=llm,
        )
        return await discovery.discover_and_propose(
            run_id=run_id, scope_id=scope_id, root_task_id=root_task_id,
            window_days=window_days, max_facts=max_facts,
        )

    notif_svc = NotificationService(
        snapshot_store=_FakeSnapshotStore(user_md=_USER_MD_ACTIVE),
        event_store=store_group.event_store,
    )
    tg = _FakeChannel("telegram")
    web = _FakeChannel("web_sse")
    notif_svc.register_channel(tg)
    notif_svc.register_channel(web)

    plane = _FakePlane()
    svc = MemoryConsolidationService(
        scheduler=_FakeScheduler(),
        task_store=store_group.task_store,
        work_store=store_group.work_store,
        event_store=store_group.event_store,
        snapshot_store=_FakeSnapshotStore(user_md=_USER_MD_ACTIVE),
        delegation_plane=plane,  # type: ignore[arg-type]
        # F146 件①：e2e 走 live state 路径（盘上无 USER.md 哨兵 root）
        project_root=Path("/nonexistent/f146-no-disk"),
        agent_context_store=store_group.agent_context_store,
        discovery_runner=_discovery_runner,
        notification_service=notif_svc,
    )
    return svc, tg, web, plane, notif_svc


async def _root_events(store_group: StoreGroup) -> list[Any]:
    """root task 全事件（真查 event_store，按 task_seq 正序——链序即审计序）。"""
    return await store_group.event_store.get_events_for_task(
        CONSOLIDATION_ROOT_TASK_ID
    )


def _consolidation_chain(events: list[Any]) -> list[str]:
    """过滤出 MEMORY_CONSOLIDATION_* 审计链（保持 task_seq 序）。"""
    return [
        e.type.value
        for e in events
        if e.type.value.startswith("MEMORY_CONSOLIDATION_")
    ]


#: H1 允许出现在巩固 root task 上的事件宇宙：巩固审计 + 系统通知审计。
#: 任何对话/模型/执行事件（USER_MESSAGE / MODEL_CALL_* / A2A_MESSAGE_* / WORKER_*）
#: 出现即 H1 破界。
_H1_ALLOWED_EVENT_TYPES = frozenset(
    {
        EventType.MEMORY_CONSOLIDATION_TRIGGERED,
        EventType.MEMORY_CONSOLIDATION_COMPLETED,
        EventType.MEMORY_CONSOLIDATION_FAILED,
        EventType.MEMORY_CONSOLIDATION_SKIPPED,
        EventType.MEMORY_CONSOLIDATION_PROPOSED,
        EventType.MEMORY_CONSOLIDATION_APPROVED,
        EventType.MEMORY_CONSOLIDATION_REJECTED,
        EventType.NOTIFICATION_DISPATCHED,
    }
)


def _assert_h1_boundary(
    events: list[Any], channels: list[_FakeChannel], plane: _FakePlane
) -> None:
    """AC-5 H1：全链无 user-facing 对话输出。

    - root task 事件宇宙受限（无 USER_MESSAGE / MODEL_CALL_* / A2A_* 等）
    - channel 侧只允许系统级 pending-review 通知（非 Agent 对话——payload 是计数
      引导，无对话文本语义，且带 notification_id 系统锚点）
    - spawn 容器是 SUBAGENT + system requester（无用户渠道）
    """
    violations = [
        e.type.value for e in events if e.type not in _H1_ALLOWED_EVENT_TYPES
    ]
    assert violations == [], f"H1 破界：root task 出现对话/执行事件 {violations}"
    for ch in channels:
        for _task_id, event_type, payload in ch.calls:
            assert event_type == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE, (
                f"H1 破界：channel 收到非系统通知 {event_type}"
            )
            assert "notification_id" in payload  # 系统通知锚点（可 dismiss）
    for call in plane.calls:
        assert call["target_kind"] == "subagent"
        assert call["parent_task"].requester.channel == "system"


async def _sor_status(conn: aiosqlite.Connection, memory_id: str) -> str:
    cursor = await conn.execute(
        "SELECT status FROM memory_sor WHERE memory_id = ?", (memory_id,)
    )
    row = await cursor.fetchone()
    return row["status"] if row else "<missing>"


async def _current_facts(conn: aiosqlite.Connection) -> list[tuple[str, str]]:
    cursor = await conn.execute(
        "SELECT memory_id, content FROM memory_sor "
        "WHERE scope_id = ? AND status = 'current' ORDER BY created_at",
        (_SCOPE,),
    )
    rows = await cursor.fetchall()
    return [(row["memory_id"], row["content"]) for row in rows]


# ============================================================
# 分支 1：accept 全链（happy path）
# ============================================================


class TestAcceptChain:
    async def test_full_chain_trigger_to_merge_commit_with_audit(
        self, store_group, client
    ):
        """cron 触发 → spawn → 发现端提议 → 通知 → REST accept → MERGE（源 SUPERSEDED）
        → 全链 event_store 审计序列 + H1 零对话输出。
        """
        await _seed_main_runtime_with_namespace(store_group)
        sor_ids = await _seed_redundant_facts(store_group)
        svc, tg, web, plane, notif_svc = _build_chain(
            store_group, llm_content=_merge_llm_json(sor_ids)
        )
        client.app.state.notification_service = notif_svc

        # ---- 阶段 1：cron 触发（_run_consolidation 即 cron job 回调）----
        await svc._run_consolidation()

        # spawn 编排发生（合成 root 真父对象）
        assert len(plane.calls) == 1

        # 候选 PENDING（发现端真跑，fake LLM 提议 1 组）
        consol_store = ConsolidationStore(store_group.conn)
        pending = await consol_store.list_candidates(scope_id=_SCOPE)
        assert len(pending) == 1
        candidate_id = pending[0].candidate_id
        assert pending[0].status.value == "pending"
        assert set(pending[0].source_sor_ids) == set(sor_ids)

        # Phase E 通知已推（proposals=1 > 0）——两渠道 + payload 计数
        assert len(tg.calls) == 1
        assert len(web.calls) == 1
        _tid, ntype, npayload = tg.calls[0]
        assert ntype == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE
        assert npayload["proposals_made"] == 1
        assert npayload["facts_reviewed"] == 3

        # 此刻源事实仍全部 CURRENT（C4：发现端只提议不 commit）
        for sid in sor_ids:
            assert await _sor_status(store_group.conn, sid) == "current"

        # Codex P2：通知落全局收件箱——真 REST GET /api/notifications（默认
        # session_id=""）能查到（Web-only 用户不再是服务端死角）
        inbox = client.get("/api/notifications")
        assert inbox.status_code == 200
        inbox_items = inbox.json()["notifications"]
        assert len(inbox_items) == 1
        assert (
            inbox_items[0]["notification_type"]
            == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE
        )

        # ---- 阶段 2：用户经 REST 审查 + accept（C7 用户主动）----
        listed = client.get("/api/consolidation/candidates")
        assert listed.status_code == 200
        assert listed.json()["pending_count"] == 1

        resp = client.post(f"/api/consolidation/candidates/{candidate_id}/accept")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "applied"
        assert body["superseded_count"] == 3
        new_sor_id = body["new_sor_id"]
        assert new_sor_id

        # ---- 阶段 3：SOR 真相（raw SQL，非 service 自证）----
        # 源 3 条 → SUPERSEDED（软删可回滚，FR-C5）
        for sid in sor_ids:
            assert await _sor_status(store_group.conn, sid) == "superseded"
        # 新权威事实 CURRENT 且是 scope 内唯一 current
        current = await _current_facts(store_group.conn)
        assert len(current) == 1
        assert current[0][0] == new_sor_id
        assert "Asia/Shanghai" in current[0][1]

        # ---- 阶段 4：全链审计序列（NFR-5 / AC-6，真查 event_store 按 task_seq）----
        events = await _root_events(store_group)
        chain = _consolidation_chain(events)
        assert chain == [
            "MEMORY_CONSOLIDATION_TRIGGERED",
            "MEMORY_CONSOLIDATION_PROPOSED",
            "MEMORY_CONSOLIDATION_COMPLETED",
            "MEMORY_CONSOLIDATION_APPROVED",
        ], f"审计链序错：{chain}"
        # 通知审计也在链上（COMPLETED 与 APPROVED 之间）
        assert any(e.type == EventType.NOTIFICATION_DISPATCHED for e in events)
        # APPROVED payload 引用闭环（run→candidate→new_sor）
        approved = [
            e for e in events if e.type == EventType.MEMORY_CONSOLIDATION_APPROVED
        ][0]
        assert approved.payload["candidate_id"] == candidate_id
        assert approved.payload["new_sor_id"] == new_sor_id
        assert approved.payload["superseded_count"] == 3
        # PROPOSED payload PII 防护：hash/id 引用，无合并内容原文
        proposed = [
            e for e in events if e.type == EventType.MEMORY_CONSOLIDATION_PROPOSED
        ][0]
        assert "merged_content" not in proposed.payload
        assert proposed.payload["source_count"] == 3

        # ---- 阶段 5：H1（AC-5）----
        _assert_h1_boundary(events, [tg, web], plane)


# ============================================================
# 分支 2：reject（不碰 SOR）
# ============================================================


class TestRejectChain:
    async def test_reject_chain_sor_untouched(self, store_group, client):
        """全链到 REST reject：候选 rejected + SOR 完全不动 + REJECTED 审计（FR-C4/C7）。"""
        await _seed_main_runtime_with_namespace(store_group)
        sor_ids = await _seed_redundant_facts(store_group)
        svc, tg, web, plane, _notif_svc = _build_chain(
            store_group, llm_content=_merge_llm_json(sor_ids)
        )
        await svc._run_consolidation()

        consol_store = ConsolidationStore(store_group.conn)
        pending = await consol_store.list_candidates(scope_id=_SCOPE)
        candidate_id = pending[0].candidate_id
        # 通知照发（有提议待审——reject 是之后用户的决定）
        assert len(tg.calls) == 1

        resp = client.post(f"/api/consolidation/candidates/{candidate_id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

        # SOR 完全不动：3 条源仍 CURRENT，无新增权威事实
        current = await _current_facts(store_group.conn)
        assert {mid for mid, _ in current} == set(sor_ids)

        # 审计链：TRIGGERED → PROPOSED → COMPLETED → REJECTED
        events = await _root_events(store_group)
        chain = _consolidation_chain(events)
        assert chain == [
            "MEMORY_CONSOLIDATION_TRIGGERED",
            "MEMORY_CONSOLIDATION_PROPOSED",
            "MEMORY_CONSOLIDATION_COMPLETED",
            "MEMORY_CONSOLIDATION_REJECTED",
        ], f"审计链序错：{chain}"
        rejected = [
            e for e in events if e.type == EventType.MEMORY_CONSOLIDATION_REJECTED
        ][0]
        assert rejected.payload["candidate_id"] == candidate_id

        # H1 全链守界
        _assert_h1_boundary(events, [tg, web], plane)


# ============================================================
# 分支 3：0 提议（LLM 判无可合并 → 不通知）
# ============================================================


class TestZeroProposalChain:
    async def test_zero_proposal_chain_completes_silently(self, store_group, client):
        """LLM 判定无可合并组 → COMPLETED(proposals=0)、无候选、**零通知**（FR-E2）。"""
        await _seed_main_runtime_with_namespace(store_group)
        await _seed_redundant_facts(store_group)
        svc, tg, web, plane, notif_svc = _build_chain(
            store_group, llm_content='{"groups": []}'
        )
        client.app.state.notification_service = notif_svc
        await svc._run_consolidation()

        # 无候选
        consol_store = ConsolidationStore(store_group.conn)
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []
        assert client.get("/api/consolidation/candidates").json()["pending_count"] == 0

        # 零通知（channel 层 + NOTIFICATION_DISPATCHED 审计 + 全局收件箱三重否定）
        assert tg.calls == []
        assert web.calls == []
        events = await _root_events(store_group)
        assert not any(
            e.type == EventType.NOTIFICATION_DISPATCHED for e in events
        ), "0 提议不该有任何通知派发（含审计）"
        assert client.get("/api/notifications").json()["notifications"] == []

        # 审计链：TRIGGERED → COMPLETED（无 PROPOSED）
        chain = _consolidation_chain(events)
        assert chain == [
            "MEMORY_CONSOLIDATION_TRIGGERED",
            "MEMORY_CONSOLIDATION_COMPLETED",
        ], f"审计链序错：{chain}"
        completed = [
            e for e in events if e.type == EventType.MEMORY_CONSOLIDATION_COMPLETED
        ][0]
        assert completed.payload["proposals_made"] == 0
        assert completed.payload["facts_reviewed"] == 3
        assert completed.payload["fallback"] is False  # LLM 真判空 ≠ fallback

        # H1（0 提议路径同样零对话输出）
        _assert_h1_boundary(events, [tg, web], plane)
