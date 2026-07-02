"""F127 Phase D — ConsolidationApprovalService 破坏性 MERGE 人审单测。

★ C4/C7 红线 Phase。覆盖 `[@test]` 绑定（plan §Phase D / FR-C1~C6 + AC-3/AC-4）：
- no-self-commit（红线）：发现端只产 PENDING；合并 commit 唯一入口是 accept（人审触发）
- accept → atomic claim → MERGE commit → 源 SUPERSEDED + 新权威事实 CURRENT + APPLIED
- accept 后 emit MEMORY_CONSOLIDATION_APPROVED（new_sor_id + superseded_count）
- reject → REJECTED + emit REJECTED + **不碰 SOR**（源仍 CURRENT）
- atomic claim 防双 accept（第二次 accept 同候选 → conflict，不重复 commit MERGE）
- claim 后 commit 失败 → 回滚 APPLYING→PENDING（候选可重审）
- 软删可回滚（AC-4）：MERGE 后源是 SUPERSEDED（无物理删），可经历史恢复
- 敏感分区候选 v0.1 不可 commit（NFR-3 codex P1 修复后：发现端不产 + 审批端最后闸
  拦存量/伪装候选 → CONFLICT 终态，敏感合并推 v0.2 vault-aware MERGE）
- P2 源新鲜度：accept 前验证全部源 SOR 仍 current——pending 期间源被更新/删除/被
  共享源候选合并 → conflict 409 + SOR 零触碰 + CONFLICT 终态 + emit CONFLICTED

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
# NFR-3（codex P1 修复后）：敏感分区候选 v0.1 不可 commit——三层防御最后闸
# ============================================================


class TestSensitiveBlockedAtApproval:
    async def test_discovery_no_longer_produces_sensitive_candidates(
        self, memory_conn, store_group
    ):
        """P1 根治验证：HEALTH 事实经完整发现端管道产 **0 候选**（窗口排除）。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(
            memory,
            subject_key="h.a",
            content="血压 120",
            partition=MemoryPartition.HEALTH,
        )
        id_b = await _seed_fact(
            memory,
            subject_key="h.b",
            content="血压 正常",
            partition=MemoryPartition.HEALTH,
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
                            "merged_content": "用户血压正常约 120（权威）",
                            "subject_key": "bp",
                            "rationale": "同指血压",
                            "confidence": 0.9,
                        }
                    ]
                )
            ),
        )
        await discovery.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []
        # 源不受任何影响
        for sid in (id_a, id_b):
            assert await _sor_status(memory_conn, sid) == "current"

    async def test_legacy_sensitive_candidate_accept_blocked_conflict(
        self, memory_conn, store_group
    ):
        """★ P1 第三层最后闸：存量敏感候选（发现端修复前产出/数据侧插入）accept →
        conflict 409 + **SOR 零触碰**（不 commit——敏感 MERGE commit 会被
        _safe_sor_content 毁内容）+ 候选 CONFLICT 终态 + emit CONFLICTED。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(
            memory,
            subject_key="h.a",
            content="血压 120",
            partition=MemoryPartition.HEALTH,
        )
        id_b = await _seed_fact(
            memory,
            subject_key="h.b",
            content="血压 正常",
            partition=MemoryPartition.HEALTH,
        )
        consol_store = ConsolidationStore(memory_conn)
        # 直接插入存量敏感候选（模拟修复前产出的历史数据）
        from octoagent.memory.models import ConsolidationCandidate

        await consol_store.insert_candidate(
            ConsolidationCandidate(
                candidate_id="legacy-sensitive-1",
                run_id="run-legacy",
                scope_id=_SCOPE,
                partition=MemoryPartition.HEALTH,
                subject_key="bp",
                source_sor_ids=[id_a, id_b],
                merged_content="用户血压正常约 120（权威）",
                rationale="同指血压",
                proposal_id="prop-legacy-1",
                confidence=0.9,
                is_sensitive=True,
                status=ConsolidationCandidateStatus.PENDING,
                content_hash="deadbeef",
                created_at=datetime.now(UTC),
            )
        )
        await memory_conn.commit()

        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept("legacy-sensitive-1")
        assert result.ok is False
        assert result.status == "conflict"
        assert "敏感" in result.detail
        # SOR 零触碰（源仍 CURRENT，无新 SOR 产生）
        for sid in (id_a, id_b):
            assert await _sor_status(memory_conn, sid) == "current"
        # 候选 CONFLICT 终态（非回滚 PENDING——重审也不能 commit）
        cand = await consol_store.get_candidate("legacy-sensitive-1")
        assert cand.status == ConsolidationCandidateStatus.CONFLICT
        # emit CONFLICTED（actor=SYSTEM 系统检测）且无 APPROVED
        conflicted = await _events(
            store_group, EventType.MEMORY_CONSOLIDATION_CONFLICTED
        )
        assert len(conflicted) == 1
        assert conflicted[0].payload["reason"] == "sensitive_partition"
        assert await _events(store_group, EventType.MEMORY_CONSOLIDATION_APPROVED) == []

    async def test_sensitive_source_sor_blocked_even_if_candidate_fields_clean(
        self, memory_conn, store_group
    ):
        """源级敏感防御：候选字段伪装非敏感（is_sensitive=False + partition=PROFILE）
        但 source_sor_ids 指向 HEALTH 源 → 仍被源级检查拦截（SOR 层 partition 是事实）。"""
        memory = MemoryService(memory_conn)
        id_h = await _seed_fact(
            memory,
            subject_key="h.a",
            content="血压 120",
            partition=MemoryPartition.HEALTH,
        )
        id_p = await _seed_fact(memory, subject_key="tz.a", content="时区 上海")
        consol_store = ConsolidationStore(memory_conn)
        from octoagent.memory.models import ConsolidationCandidate

        await consol_store.insert_candidate(
            ConsolidationCandidate(
                candidate_id="disguised-1",
                run_id="run-x",
                scope_id=_SCOPE,
                partition=MemoryPartition.PROFILE,  # 伪装非敏感
                subject_key="mixed",
                source_sor_ids=[id_p, id_h],  # 但含 HEALTH 源
                merged_content="混合内容",
                rationale="r",
                proposal_id="prop-x",
                confidence=0.5,
                is_sensitive=False,  # 伪装非敏感
                status=ConsolidationCandidateStatus.PENDING,
                content_hash="cafebabe",
                created_at=datetime.now(UTC),
            )
        )
        await memory_conn.commit()
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept("disguised-1")
        assert result.ok is False
        assert result.status == "conflict"
        for sid in (id_h, id_p):
            assert await _sor_status(memory_conn, sid) == "current"
        cand = await consol_store.get_candidate("disguised-1")
        assert cand.status == ConsolidationCandidateStatus.CONFLICT


# ============================================================
# P2：accept 前源新鲜度验证（pending 期间源被更新/删除/共享源已被合并）
# ============================================================


class TestStaleSourcesConflict:
    async def test_source_updated_while_pending_accept_conflicts_zero_sor_touch(
        self, memory_conn, store_group
    ):
        """★ P2-①：候选 pending 期间源被 UPDATE（旧行 SUPERSEDED + 新行 CURRENT）→
        accept → conflict + **SOR 零触碰**（不 supersede、不产合并事实——绝不静默
        用旧内容 commit）+ 候选 CONFLICT 终态 + emit CONFLICTED(stale_sources)。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        id_a, id_b = source_ids
        # pending 期间用户经 memory 工具 UPDATE 源 A（旧 A SUPERSEDED + 新行 CURRENT）
        cursor = await memory_conn.execute(
            "SELECT subject_key FROM memory_sor WHERE memory_id = ?", (id_a,)
        )
        subject_a = (await cursor.fetchone())["subject_key"]
        updated = await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.PROFILE,
            action=WriteAction.UPDATE,
            subject_key=subject_a,
            content="时区 更新为 Asia/Tokyo",
            confidence=1.0,
        )
        assert updated.sor_id
        assert await _sor_status(memory_conn, id_a) == "superseded"

        # SOR 快照（验证 accept 失败后零变化）
        cursor = await memory_conn.execute(
            "SELECT COUNT(*) AS n FROM memory_sor WHERE scope_id = ?", (_SCOPE,)
        )
        sor_count_before = (await cursor.fetchone())["n"]

        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        assert result.ok is False
        assert result.status == "conflict"
        assert id_a in result.detail  # detail 指明过期源

        # SOR 零触碰：行数不变（无合并事实产生）+ 新 current 不受影响 + B 仍 current
        cursor = await memory_conn.execute(
            "SELECT COUNT(*) AS n FROM memory_sor WHERE scope_id = ?", (_SCOPE,)
        )
        assert (await cursor.fetchone())["n"] == sor_count_before
        assert await _sor_status(memory_conn, updated.sor_id) == "current"
        assert await _sor_status(memory_conn, id_b) == "current"
        # 候选 CONFLICT 终态
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.CONFLICT
        assert cand.decided_at is not None
        # 事件：CONFLICTED(stale_sources, stale_sor_ids=[id_a]) 且无 APPROVED
        conflicted = await _events(
            store_group, EventType.MEMORY_CONSOLIDATION_CONFLICTED
        )
        assert len(conflicted) == 1
        assert conflicted[0].payload["reason"] == "stale_sources"
        assert conflicted[0].payload["stale_sor_ids"] == [id_a]
        assert await _events(store_group, EventType.MEMORY_CONSOLIDATION_APPROVED) == []
        # proposal 未被 commit（仍 validated）
        cursor = await memory_conn.execute(
            "SELECT status FROM memory_write_proposals WHERE proposal_id = ?",
            (cand.proposal_id,),
        )
        row = await cursor.fetchone()
        assert row is not None and row["status"] == "validated"

    async def test_source_deleted_while_pending_accept_conflicts(
        self, memory_conn, store_group
    ):
        """P2：pending 期间源被 DELETE（软删 status=deleted）→ accept → conflict。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        id_a, id_b = source_ids
        await memory_conn.execute(
            "UPDATE memory_sor SET status = 'deleted' WHERE memory_id = ?", (id_a,)
        )
        await memory_conn.commit()
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        assert result.ok is False
        assert result.status == "conflict"
        assert await _sor_status(memory_conn, id_b) == "current"
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.CONFLICT

    async def test_shared_source_second_candidate_conflicts_after_first_applied(
        self, memory_conn, store_group
    ):
        """★ P2-②：同批两候选共享源——第一个 accept 成功（共享源 SUPERSEDED）后，
        第二个 accept → conflict（共享源已非 current），不产生重复/并存合并事实。"""
        memory = MemoryService(memory_conn)
        # 三条事实：A/B 组成候选 1；B/C 组成候选 2（B 共享）
        id_a = await _seed_fact(memory, subject_key="tz.a", content="时区 上海")
        id_b = await _seed_fact(memory, subject_key="tz.b", content="时区 Asia/Shanghai")
        id_c = await _seed_fact(memory, subject_key="tz.c", content="时区 GMT+8")
        consol_store = ConsolidationStore(memory_conn)

        async def _discover(groups: list[dict[str, Any]], run_id: str) -> None:
            discovery = ConsolidationDiscoveryService(
                memory_service=memory,
                memory_store=memory._store,  # type: ignore[attr-defined]
                consolidation_store=consol_store,
                event_store=store_group.event_store,
                llm_client=_FakeLLM(content=_groups_json(groups)),
            )
            await discovery.discover_and_propose(
                run_id=run_id, scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
            )

        await _discover(
            [
                {
                    "source_ids": [id_a, id_b],
                    "merged_content": "时区上海（候选1）",
                    "subject_key": "tz.merged.1",
                    "rationale": "r1",
                    "confidence": 0.9,
                }
            ],
            "run-1",
        )
        await _discover(
            [
                {
                    "source_ids": [id_b, id_c],
                    "merged_content": "时区上海（候选2）",
                    "subject_key": "tz.merged.2",
                    "rationale": "r2",
                    "confidence": 0.9,
                }
            ],
            "run-2",
        )
        cands = await consol_store.list_candidates(scope_id=_SCOPE)
        assert len(cands) == 2
        by_run = {c.run_id: c for c in cands}
        cand1, cand2 = by_run["run-1"], by_run["run-2"]

        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        first = await approval.accept(cand1.candidate_id)
        assert first.ok is True
        assert await _sor_status(memory_conn, id_b) == "superseded"  # 共享源已合并

        second = await approval.accept(cand2.candidate_id)
        assert second.ok is False
        assert second.status == "conflict"
        assert id_b in second.detail
        # C 未被误 supersede（候选 2 未 commit）
        assert await _sor_status(memory_conn, id_c) == "current"
        # 候选 2 CONFLICT 终态；只有 1 个 APPROVED（候选 1）
        assert (
            await consol_store.get_candidate(cand2.candidate_id)
        ).status == ConsolidationCandidateStatus.CONFLICT
        approved = await _events(store_group, EventType.MEMORY_CONSOLIDATION_APPROVED)
        assert len(approved) == 1
        assert approved[0].payload["candidate_id"] == cand1.candidate_id

    async def test_conflict_candidate_does_not_block_reproposal(
        self, memory_conn, store_group
    ):
        """★ codex 复审 round2 P2：conflict 候选不得吞掉下次巩固的同内容重新提议——
        409 引导"等下次巩固重新提议"的恢复主流程必须真能走通：
        源更新 → accept 409(conflict) → 新一轮发现端基于新 current 源提同内容 →
        新 PENDING 候选产生（幂等账本放行）→ 新候选可正常 accept。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        id_a, id_b = source_ids
        # 源 A 被 UPDATE → accept → conflict（同 P2-① 场景）
        cursor = await memory_conn.execute(
            "SELECT subject_key FROM memory_sor WHERE memory_id = ?", (id_a,)
        )
        subject_a = (await cursor.fetchone())["subject_key"]
        updated = await memory.fast_commit(
            scope_id=_SCOPE,
            partition=MemoryPartition.PROFILE,
            action=WriteAction.UPDATE,
            subject_key=subject_a,
            content="时区 上海（更新）",
            confidence=1.0,
        )
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        first = await approval.accept(cand_id)
        assert first.status == "conflict"

        # 下次巩固：LLM 基于新 current 源（updated.sor_id + id_b）提**相同 merged_content**
        old_cand = await consol_store.get_candidate(cand_id)
        discovery = ConsolidationDiscoveryService(
            memory_service=memory,
            memory_store=memory._store,  # type: ignore[attr-defined]
            consolidation_store=consol_store,
            event_store=store_group.event_store,
            llm_client=_FakeLLM(
                content=_groups_json(
                    [
                        {
                            "source_ids": [updated.sor_id, id_b],
                            "merged_content": old_cand.merged_content,  # 同内容
                            "subject_key": "timezone.v2",
                            "rationale": "基于最新源重新提议",
                            "confidence": 0.9,
                        }
                    ]
                )
            ),
        )
        outcome = await discovery.discover_and_propose(
            run_id="run-2", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        # conflict 候选不阻断：新候选产生（旧黑名单逻辑会在此吞掉提议 → 0）
        assert outcome.proposals_made == 1
        pending = await consol_store.list_candidates(
            scope_id=_SCOPE, status=ConsolidationCandidateStatus.PENDING
        )
        assert len(pending) == 1
        # 恢复闭环：新候选可正常 accept（新源全 current）
        second = await approval.accept(pending[0].candidate_id)
        assert second.ok is True
        assert await _sor_status(memory_conn, updated.sor_id) == "superseded"
        assert await _sor_status(memory_conn, id_b) == "superseded"

    async def test_conflict_is_terminal_no_reclaim_no_reject(
        self, memory_conn, store_group
    ):
        """CONFLICT 终态与既有 CAS 不打架：conflict 后再 accept → claim 失败 conflict
        （不重复验证/commit）；再 reject → conflict（PENDING→REJECTED CAS 失败）。"""
        memory = MemoryService(memory_conn)
        cand_id, source_ids, consol_store = await _make_pending_candidate(
            memory, memory_conn, store_group
        )
        id_a, _ = source_ids
        await memory_conn.execute(
            "UPDATE memory_sor SET status = 'deleted' WHERE memory_id = ?", (id_a,)
        )
        await memory_conn.commit()
        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        first = await approval.accept(cand_id)
        assert first.status == "conflict"
        # 再 accept：claim 失败（status=conflict 非 pending）
        again = await approval.accept(cand_id)
        assert again.ok is False
        assert again.status == "conflict"
        # 再 reject：CAS 失败（同样 conflict，不覆写终态）
        rej = await approval.reject(cand_id)
        assert rej.ok is False
        assert rej.status == "conflict"
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.CONFLICT
        # 只 emit 1 次 CONFLICTED（重入不重复 emit）
        conflicted = await _events(
            store_group, EventType.MEMORY_CONSOLIDATION_CONFLICTED
        )
        assert len(conflicted) == 1


# ============================================================
# 边界：MERGE 目标 subject_key 与既有 CURRENT 冲突 → 优雅回滚（C4 安全不破）
# ============================================================


class TestCommitCollisionGracefulRollback:
    """LLM 选的合并 subject_key 撞已有 CURRENT 事实（UNIQUE idx_memory_sor_current_unique）→
    commit_memory 报 UNIQUE → approval 回滚 APPLYING→PENDING（候选可重审，无数据损坏）。

    根因：write_service MERGE commit 先 _commit_add（插新 CURRENT）再标源 SUPERSEDED；
    若新事实 subject_key 撞一个仍 CURRENT 的事实（含某个源事实自身的 key），插入违 UNIQUE。
    v0.1 默认路径不踩此坑（发现端 subject_key 缺省兜底用 consolidated_{首源id} 新 key），
    本测试覆盖 LLM 显式复用源 key 的对抗场景——核心断言：**C4 安全不破**（源不被 supersede，
    候选回 PENDING 可重审，不静默损坏记忆）。
    """

    async def test_subject_key_collision_rolls_back_to_pending_no_corruption(
        self, memory_conn, store_group
    ):
        memory = MemoryService(memory_conn)
        # 源事实 A/B，且 LLM 把合并 subject_key 设成 A 的 key（撞 A 仍 CURRENT）
        id_a = await _seed_fact(memory, subject_key="tz", content="时区 上海")
        id_b = await _seed_fact(memory, subject_key="tz.alt", content="时区 Asia/Shanghai")
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
                            "merged_content": "用户时区上海（权威）",
                            "subject_key": "tz",  # 撞源 A 的 key（仍 CURRENT）
                            "rationale": "同指",
                            "confidence": 0.9,
                        }
                    ]
                )
            ),
        )
        await discovery.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        cand_id = (await consol_store.list_candidates(scope_id=_SCOPE))[0].candidate_id

        approval = _build_approval(
            memory=memory, consol_store=consol_store, store_group=store_group
        )
        result = await approval.accept(cand_id)
        # commit 撞 UNIQUE → 回滚到 pending（不报 ok）
        assert result.ok is False
        assert result.status == "pending"
        # 候选回 PENDING（可重审）
        cand = await consol_store.get_candidate(cand_id)
        assert cand.status == ConsolidationCandidateStatus.PENDING
        # ★ C4 安全：源事实仍 CURRENT（未被 supersede，无数据损坏）
        assert await _sor_status(memory_conn, id_a) == "current"
        assert await _sor_status(memory_conn, id_b) == "current"
