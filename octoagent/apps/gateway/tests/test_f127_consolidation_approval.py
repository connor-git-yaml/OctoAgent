"""F127 Phase D — ConsolidationApprovalService 破坏性 MERGE 人审单测。

★ C4/C7 红线 Phase。覆盖 `[@test]` 绑定（plan §Phase D / FR-C1~C6 + AC-3/AC-4）：
- no-self-commit（红线）：发现端只产 PENDING；合并 commit 唯一入口是 accept（人审触发）
- accept → atomic claim → MERGE commit → 源 SUPERSEDED + 新权威事实 CURRENT + APPLIED
- accept 后 emit MEMORY_CONSOLIDATION_APPROVED（new_sor_id + superseded_count）
- reject → REJECTED + emit REJECTED + **不碰 SOR**（源仍 CURRENT）
- atomic claim 防双 accept（第二次 accept 同候选 → conflict，不重复 commit MERGE）
- claim 后 commit 失败 → 回滚 APPLYING→PENDING（候选可重审）
- 软删可回滚（AC-4）：MERGE 后源是 SUPERSEDED（无物理删），可经历史恢复
- 敏感分区候选同样走人审（NFR-3：v0.1 无自动路径）

**关键**：用真 MemoryService（SQLite）跑完整 Phase C 发现端造真 PENDING 候选 → 再测
Phase D accept/reject 对 SOR 的真实影响。用 ConsolidationDiscoveryService（注入 fake LLM）
造候选，保证候选关联的 proposal 真 VALIDATED 持久化（accept commit 才能成功）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest_asyncio
from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.core.models.task import RequesterInfo
from octoagent.core.models.task import Task as TaskModel
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.consolidation_approval import (
    ConsolidationApprovalService,
)
from octoagent.gateway.services.consolidation_discovery import (
    ConsolidationDiscoveryService,
)
from octoagent.memory import MemoryPartition, MemoryService, WriteAction
from octoagent.memory.models import ConsolidationCandidateStatus
from octoagent.memory.store import ConsolidationStore
from octoagent.memory.store.sqlite_init import init_memory_db

_ROOT_TASK_ID = "_memory_consolidation_root"
_SCOPE = "agent-private/main"


class _FakeLLM:
    def __init__(self, *, content: str = "") -> None:
        self._content = content

    async def complete(
        self, messages: list[dict[str, str]], model_alias: str = "main", **kwargs: Any
    ) -> Any:
        class _R:
            content = self._content

        return _R()


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    db_path = str(tmp_path / "core.db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    sg = await create_store_group(db_path, str(artifacts_dir))
    now = datetime.now(UTC)
    await sg.task_store.create_task(
        TaskModel(
            task_id=_ROOT_TASK_ID,
            created_at=now,
            updated_at=now,
            status=TaskStatus.SUCCEEDED,
            title="F127 root",
            thread_id="_memory_consolidation",
            scope_id="",
            requester=RequesterInfo(channel="system", sender_id="memory_consolidation"),
        )
    )
    await sg.conn.commit()
    try:
        yield sg
    finally:
        await sg.close()


@pytest_asyncio.fixture
async def memory_conn(tmp_path: Path):
    conn = await aiosqlite.connect(str(tmp_path / "memory.db"))
    conn.row_factory = aiosqlite.Row
    await init_memory_db(conn)
    yield conn
    await conn.close()


async def _seed_fact(
    memory: MemoryService,
    *,
    subject_key: str,
    content: str,
    partition: MemoryPartition = MemoryPartition.PROFILE,
) -> str:
    result = await memory.fast_commit(
        scope_id=_SCOPE,
        partition=partition,
        action=WriteAction.ADD,
        subject_key=subject_key,
        content=content,
        confidence=1.0,
    )
    return result.sor_id or ""


def _groups_json(groups: list[dict[str, Any]]) -> str:
    return json.dumps({"groups": groups}, ensure_ascii=False)


async def _make_pending_candidate(
    memory: MemoryService,
    memory_conn: aiosqlite.Connection,
    store_group: StoreGroup,
    *,
    partition: MemoryPartition = MemoryPartition.PROFILE,
    merged_content: str = "用户时区是 Asia/Shanghai（权威）",
) -> tuple[str, list[str], ConsolidationStore]:
    """跑 Phase C 发现端造一条真 PENDING 候选。返回 (candidate_id, source_ids, store)。"""
    id_a = await _seed_fact(
        memory, subject_key="tz.a", content="时区 上海", partition=partition
    )
    id_b = await _seed_fact(
        memory, subject_key="tz.b", content="时区 Asia/Shanghai", partition=partition
    )
    consol_store = ConsolidationStore(memory_conn)
    discovery = ConsolidationDiscoveryService(
        memory_service=memory,
        memory_store=memory._store,  # type: ignore[attr-defined]
        consolidation_store=consol_store,
        event_store=store_group.event_store,
        llm_client=_FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_a, id_b],
                        "merged_content": merged_content,
                        "subject_key": "timezone",
                        "rationale": "两条同指上海时区",
                        "confidence": 0.9,
                    }
                ]
            )
        ),
    )
    await discovery.discover_and_propose(
        run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
    )
    cands = await consol_store.list_candidates(scope_id=_SCOPE)
    assert len(cands) == 1
    return cands[0].candidate_id, [id_a, id_b], consol_store


def _build_approval(
    *,
    memory: MemoryService,
    consol_store: ConsolidationStore,
    store_group: StoreGroup,
) -> ConsolidationApprovalService:
    return ConsolidationApprovalService(
        memory_service=memory,
        consolidation_store=consol_store,
        event_store=store_group.event_store,
        root_task_id=_ROOT_TASK_ID,
    )


async def _sor_status(memory_conn: aiosqlite.Connection, memory_id: str) -> str:
    cursor = await memory_conn.execute(
        "SELECT status FROM memory_sor WHERE memory_id = ?", (memory_id,)
    )
    row = await cursor.fetchone()
    return row["status"] if row else "<missing>"


async def _events(store_group: StoreGroup, event_type: EventType) -> list[Any]:
    events = await store_group.event_store.get_events_for_task(_ROOT_TASK_ID)
    return [e for e in events if e.type == event_type]


# ============================================================
# C4 红线：no-self-commit（发现端无 commit 路径，accept 是唯一入口）
# ============================================================


class TestNoSelfCommitRedLine:
    async def test_discovery_never_commits_only_approval_does(
        self, memory_conn, store_group
    ):
        """★ C4 红线：Phase C 发现端跑完源仍 CURRENT；只有 Phase D accept commit MERGE。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        # 发现端后：源仍 CURRENT（未 commit）
        for sid in source_ids:
            assert await _sor_status(memory_conn, sid) == "current"
        # 候选 PENDING
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.PENDING

        # accept（人审）→ 才 commit
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        assert result.ok is True
        # accept 后源 SUPERSEDED
        for sid in source_ids:
            assert await _sor_status(memory_conn, sid) == "superseded"

    async def test_approval_service_is_sole_commit_entry_no_autonomous_path(self):
        """★ AC-3 静态证明：commit_memory **调用**只出现在 approval（accept），发现端 +
        cron 编排服务**无调用**（docstring 提及不算——只查方法调用 `.commit_memory(`）。

        grep 源码：consolidation_discovery.py / memory_consolidation.py 不得有
        `.commit_memory(` 调用（只 propose+validate / 只编排 spawn+发现端 runner）。
        """
        base = (
            "/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/"
            "F127-sleep-time/octoagent/apps/gateway/src/octoagent/gateway/services/"
        )

        def _call_lines(src: str) -> list[str]:
            """返回含 `.commit_memory(` 调用的非注释/非 docstring 代码行。

            粗粒度排除：strip 后以 # 开头（行注释）或整行在三引号 docstring 内的不算。
            足够区分"调用"vs"docstring 提及"——调用形态是 `xxx.commit_memory(`。
            """
            hits = []
            in_doc = False
            for line in src.splitlines():
                stripped = line.strip()
                # 简易三引号 docstring 进/出（成对 """ 在同行不切换）
                triple = stripped.count('"""')
                if triple % 2 == 1:
                    in_doc = not in_doc
                    continue
                if in_doc or stripped.startswith("#"):
                    continue
                if ".commit_memory(" in line:
                    hits.append(line)
            return hits

        discovery_src = Path(base + "consolidation_discovery.py").read_text(
            encoding="utf-8"
        )
        assert _call_lines(discovery_src) == [], (
            "发现端不得**调用** commit_memory——合并 commit 唯一入口是 Phase D 人审（C4 红线）；"
            f"实际命中：{_call_lines(discovery_src)}"
        )
        consolidation_src = Path(base + "memory_consolidation.py").read_text(
            encoding="utf-8"
        )
        assert _call_lines(consolidation_src) == [], (
            "巩固编排服务不得**调用** commit_memory——破坏性合并必须人审；"
            f"实际命中：{_call_lines(consolidation_src)}"
        )
        # 正向：approval 服务确实有 commit_memory 调用（accept 路径）
        approval_src = Path(base + "consolidation_approval.py").read_text(
            encoding="utf-8"
        )
        assert _call_lines(approval_src), (
            "approval 服务应有 commit_memory 调用（accept 是唯一 commit 入口）"
        )

    async def test_approval_accept_is_only_commit_caller(self):
        """approval 服务里 commit_memory 只在 accept 方法体内（人审触发的唯一 commit）。"""
        src = Path(
            "/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/"
            "F127-sleep-time/octoagent/apps/gateway/src/octoagent/gateway/services/"
            "consolidation_approval.py"
        ).read_text(encoding="utf-8")
        # commit_memory 出现且仅在 accept 上下文（reject 路径不得 commit）
        assert "commit_memory" in src
        # reject 方法不得含 commit_memory（粗粒度：reject 方法体到下个方法之间无 commit_memory）
        reject_start = src.index("async def reject")
        # reject 之后的下一个 "async def" 或 "# ====" 段
        rest = src[reject_start:]
        next_method = rest.find("\n    async def ", 1)
        reject_body = rest[: next_method if next_method > 0 else len(rest)]
        assert "commit_memory" not in reject_body, "reject 路径不得 commit MERGE"


# ============================================================
# accept → MERGE commit → SUPERSEDED + APPROVED
# ============================================================


class TestAccept:
    async def test_accept_commits_merge_supersedes_sources(
        self, memory_conn, store_group
    ):
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        assert result.ok is True
        assert result.status == "applied"
        assert result.new_sor_id  # 新权威 SOR id
        assert result.superseded_count == 2
        # 源 SUPERSEDED
        for sid in source_ids:
            assert await _sor_status(memory_conn, sid) == "superseded"
        # 新权威事实 CURRENT
        assert await _sor_status(memory_conn, result.new_sor_id) == "current"
        # 候选 APPLIED
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.APPLIED
        assert cand.decided_at is not None

    async def test_accept_emits_approved_event(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        cand_id, _, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        events = await _events(store_group, EventType.MEMORY_CONSOLIDATION_APPROVED)
        assert len(events) == 1
        assert events[0].payload["candidate_id"] == cand_id
        assert events[0].payload["new_sor_id"] == result.new_sor_id
        assert events[0].payload["superseded_count"] == 2

    async def test_accept_nonexistent_candidate(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        consol_store = ConsolidationStore(memory_conn)
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept("nonexistent")
        assert result.ok is False
        assert result.status == "not_found"


# ============================================================
# atomic claim 防双 accept（C4 核心）
# ============================================================


class TestAtomicClaimDoubleAccept:
    async def test_double_accept_second_conflicts_no_double_merge(
        self, memory_conn, store_group
    ):
        """★ C4：同候选第二次 accept → conflict，不重复 commit MERGE（源不被二次 supersede）。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        first = await approval.accept(cand_id)
        assert first.ok is True
        # 第二次 accept 同候选 → claim 失败（已 applied 非 pending）→ conflict
        second = await approval.accept(cand_id)
        assert second.ok is False
        assert second.status == "conflict"
        # 只产生 1 个 APPROVED 事件（没二次 commit）
        approved = await _events(store_group, EventType.MEMORY_CONSOLIDATION_APPROVED)
        assert len(approved) == 1

    async def test_accept_after_reject_conflicts(self, memory_conn, store_group):
        """已 reject 的候选再 accept → claim 失败 conflict（不 commit 已拒绝的提议）。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        rej = await approval.reject(cand_id)
        assert rej.ok is True
        acc = await approval.accept(cand_id)
        assert acc.ok is False
        assert acc.status == "conflict"
        # 源仍 CURRENT（reject 不碰 SOR，后续 accept 也没 commit）
        for sid in source_ids:
            assert await _sor_status(memory_conn, sid) == "current"


# ============================================================
# reject → 不碰 SOR（C7）
# ============================================================


class TestReject:
    async def test_reject_does_not_touch_sor(self, memory_conn, store_group):
        """★ C7：reject → 源事实仍 CURRENT（不碰 SOR），候选 REJECTED。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.reject(cand_id)
        assert result.ok is True
        assert result.status == "rejected"
        for sid in source_ids:
            assert await _sor_status(memory_conn, sid) == "current"
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.REJECTED
        assert cand.decided_at is not None

    async def test_reject_emits_rejected_event(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        cand_id, _, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        await approval.reject(cand_id)
        events = await _events(store_group, EventType.MEMORY_CONSOLIDATION_REJECTED)
        assert len(events) == 1
        assert events[0].payload["candidate_id"] == cand_id

    async def test_double_reject_conflicts(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        cand_id, _, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        first = await approval.reject(cand_id)
        assert first.ok is True
        second = await approval.reject(cand_id)
        assert second.ok is False
        assert second.status == "conflict"


# ============================================================
# AC-4：软删可回滚（MERGE 源是 SUPERSEDED 无物理删）
# ============================================================


class TestSoftDeleteRollbackable:
    async def test_superseded_sources_recoverable_from_history(
        self, memory_conn, store_group
    ):
        """★ AC-4：accept 后源是 SUPERSEDED（非物理删），仍在表中可查 → 可恢复 CURRENT。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        await approval.accept(cand_id)
        # 源行仍在表中（无 DELETE FROM）——可经历史反推恢复
        for sid in source_ids:
            cursor = await memory_conn.execute(
                "SELECT memory_id, status, content FROM memory_sor WHERE memory_id = ?",
                (sid,),
            )
            row = await cursor.fetchone()
            assert row is not None, f"源 {sid} 应仍在表中（软删非物理删，AC-4 可回滚）"
            assert row["status"] == "superseded"
            assert row["content"]  # 内容仍保留


# ============================================================
# NFR-3：敏感分区候选同样走人审（v0.1 无自动路径）
# ============================================================


class TestSensitiveHumanReview:
    async def test_sensitive_candidate_same_human_review_path(
        self, memory_conn, store_group
    ):
        """NFR-3：HEALTH 分区候选走与普通候选**完全相同**的人审 accept 路径（无自动旁路）。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory,
            memory_conn,
            store_group,
            partition=MemoryPartition.HEALTH,
            merged_content="用户血压正常约 120（权威）",
        )
        cand = await consol_store.get_candidate(cand_id)
        assert cand.is_sensitive is True
        assert cand.status == ConsolidationCandidateStatus.PENDING  # 仍待人审，无自动 applied
        # 人审 accept 才生效（与普通候选同一入口，无自动旁路）
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        assert result.ok is True
        for sid in source_ids:
            assert await _sor_status(memory_conn, sid) == "superseded"
