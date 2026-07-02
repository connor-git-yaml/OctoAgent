"""F127 Phase C — ConsolidationDiscoveryService 发现端单测。

覆盖 `[@test]` 绑定（plan §Phase C / FR-B1~B6 + AC-2）：
- 窗口拉取（AGENT_PRIVATE CURRENT 事实，window_days→cutoff / max_facts→limit）
- LLM 提议 → WriteProposal[MERGE]（merge_source_ids 进 metadata，是 list 非 str）
- validate-no-commit（提议 validate 通过但 SOR 未被 commit——源仍 CURRENT）
- fallback 空运行（LLM=None / 异常 / 空响应 / 解析失败 → 0 提议 fallback=True）
- C4 红线：发现端绝不 commit 既有事实合并——源事实在发现端跑完后仍 CURRENT
- C9：无任何关键词/相似度硬规则判重（提议组完全来自 LLM 输出，grep 验证 + 行为验证）
- C2：每条候选 emit MEMORY_CONSOLIDATION_PROPOSED（payload 不含 merged_content 原文）
- NFR-4：content_hash 幂等账本——同 scope 重复内容候选去重
- NFR-3（codex P1-1/P1-2 修复后）：敏感分区（HEALTH/FINANCE）事实**不进巩固**——
  窗口排除（第一层）+ _propose_group any 语义纵深防御（第二层）；敏感合并推 v0.2

**关键**：用真 MemoryService（SQLite）+ 真 ConsolidationStore + 真 StoreGroup event_store，
注入 fake LLM client（返回固定 JSON）——验证确定性编排正确（窗口/propose/validate/写候选/
emit），LLM 判断力本身留强 model 验证（Phase Verify，AC-8）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import pytest_asyncio
from octoagent.core.models.enums import EventType, TaskStatus
from octoagent.core.models.task import RequesterInfo
from octoagent.core.models.task import Task as TaskModel
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.consolidation_discovery import (
    ConsolidationDiscoveryService,
)
from octoagent.memory import MemoryPartition, MemoryService, WriteAction
from octoagent.memory.models import ConsolidationCandidateStatus
from octoagent.memory.store import ConsolidationStore
from octoagent.memory.store.sqlite_init import init_memory_db

_ROOT_TASK_ID = "_memory_consolidation_root"
_SCOPE = "agent-private/main"


# ============================================================
# Fakes
# ============================================================


class _FakeLLM:
    """返回固定 .content（或抛异常）的巩固 LLM client（匹配 complete 签名）。"""

    def __init__(
        self, *, content: str = "", raise_exc: Exception | None = None
    ) -> None:
        self._content = content
        self._raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> Any:
        self.calls.append({"messages": messages, "model_alias": model_alias, **kwargs})
        if self._raise_exc is not None:
            raise self._raise_exc

        class _Result:
            content = self._content

        return _Result()


# ============================================================
# Fixtures
# ============================================================


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    db_path = str(tmp_path / "core.db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    sg = await create_store_group(db_path, str(artifacts_dir))
    # FK 占位 root task（事件 task_id 引用它）
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
    # 必须 yield + close——StoreGroup 持 2 个 aiosqlite 连接（主 + versionable），
    # 不关闭会跨 test 泄漏连接 + WAL 锁，最终 SQLite worker 线程死锁（本文件 20 test）。
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
    """种一条 CURRENT SOR 事实，返回 memory_id。"""
    result = await memory.fast_commit(
        scope_id=_SCOPE,
        partition=partition,
        action=WriteAction.ADD,
        subject_key=subject_key,
        content=content,
        confidence=1.0,
    )
    return result.sor_id or ""


def _build_discovery(
    *,
    memory: MemoryService,
    memory_conn: aiosqlite.Connection,
    store_group: StoreGroup,
    llm: _FakeLLM | None,
) -> tuple[ConsolidationDiscoveryService, ConsolidationStore]:
    consol_store = ConsolidationStore(memory_conn)
    svc = ConsolidationDiscoveryService(
        memory_service=memory,
        memory_store=memory._store,  # type: ignore[attr-defined]
        consolidation_store=consol_store,
        event_store=store_group.event_store,
        llm_client=llm,
    )
    return svc, consol_store


async def _events_proposed(store_group: StoreGroup) -> list[Any]:
    events = await store_group.event_store.get_events_for_task(_ROOT_TASK_ID)
    return [e for e in events if e.type == EventType.MEMORY_CONSOLIDATION_PROPOSED]


def _groups_json(groups: list[dict[str, Any]]) -> str:
    return json.dumps({"groups": groups}, ensure_ascii=False)


# ============================================================
# 窗口拉取（FR-B1）
# ============================================================


class TestWindowPull:
    async def test_pulls_current_facts(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="profile.tz.a", content="时区 上海")
        await _seed_fact(memory, subject_key="profile.tz.b", content="时区 Asia/Shanghai")
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=None
        )
        facts = await svc._pull_window(_SCOPE, window_days=7, max_facts=50)
        assert len(facts) == 2
        assert {f.subject_key for f in facts} == {"profile.tz.a", "profile.tz.b"}

    async def test_window_excludes_old_facts(self, memory_conn, store_group):
        """超 window_days 的事实不纳入（updated_after cutoff）。"""
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="recent", content="近期事实")
        # 手动把一条事实的 updated_at 改到 30 天前
        old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        await _seed_fact(memory, subject_key="old", content="旧事实")
        await memory_conn.execute(
            "UPDATE memory_sor SET updated_at = ? WHERE subject_key = ?",
            (old_ts, "old"),
        )
        await memory_conn.commit()
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=None
        )
        facts = await svc._pull_window(_SCOPE, window_days=7, max_facts=50)
        keys = {f.subject_key for f in facts}
        assert "recent" in keys
        assert "old" not in keys

    async def test_max_facts_limits(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        for i in range(5):
            await _seed_fact(memory, subject_key=f"k{i}", content=f"事实 {i}")
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=None
        )
        facts = await svc._pull_window(_SCOPE, window_days=7, max_facts=3)
        assert len(facts) == 3


# ============================================================
# LLM 提议 → WriteProposal[MERGE]（FR-B3/B4）
# ============================================================


class TestProposeAndValidate:
    async def test_llm_groups_produce_pending_candidate(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(memory, subject_key="tz.a", content="时区 上海")
        id_b = await _seed_fact(memory, subject_key="tz.b", content="时区 Asia/Shanghai")
        llm = _FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_a, id_b],
                        "merged_content": "用户时区是 Asia/Shanghai",
                        "subject_key": "timezone",
                        "rationale": "两条同指上海时区",
                        "confidence": 0.9,
                    }
                ]
            )
        )
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert outcome.facts_reviewed == 2
        assert outcome.proposals_made == 1
        assert not outcome.fallback
        cands = await consol_store.list_candidates(scope_id=_SCOPE)
        assert len(cands) == 1
        cand = cands[0]
        assert cand.status == ConsolidationCandidateStatus.PENDING
        assert set(cand.source_sor_ids) == {id_a, id_b}
        assert cand.merged_content == "用户时区是 Asia/Shanghai"
        assert cand.proposal_id  # 关联 write_service proposal

    async def test_merge_source_ids_stored_as_list_in_proposal(
        self, memory_conn, store_group
    ):
        """★ 关键回归：proposal.metadata['merge_source_ids'] 必须是 list（非 JSON 串）——
        否则 Phase D commit 时 `for src_id in ...` 会迭代字符。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(memory, subject_key="a", content="x")
        id_b = await _seed_fact(memory, subject_key="b", content="y")
        llm = _FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_a, id_b],
                        "merged_content": "merged",
                        "subject_key": "m",
                        "rationale": "r",
                        "confidence": 0.8,
                    }
                ]
            )
        )
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        cand = (await consol_store.list_candidates(scope_id=_SCOPE))[0]
        # 直接查 proposal 表，确认 metadata.merge_source_ids 是 JSON list
        cursor = await memory_conn.execute(
            "SELECT metadata FROM memory_write_proposals WHERE proposal_id = ?",
            (cand.proposal_id,),
        )
        row = await cursor.fetchone()
        meta = json.loads(row["metadata"])
        assert isinstance(meta["merge_source_ids"], list)
        assert set(meta["merge_source_ids"]) == {id_a, id_b}


# ============================================================
# C4 红线：validate-no-commit（发现端绝不 commit 既有事实合并）
# ============================================================


class TestNoCommitRedLine:
    async def test_sources_stay_current_after_discovery(self, memory_conn, store_group):
        """★ C4 红线：发现端跑完后，源事实仍 CURRENT（未被 MERGE commit 标 SUPERSEDED）。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(memory, subject_key="tz.a", content="时区 上海")
        id_b = await _seed_fact(memory, subject_key="tz.b", content="时区 沪")
        llm = _FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_a, id_b],
                        "merged_content": "用户时区上海",
                        "subject_key": "tz",
                        "rationale": "同指",
                        "confidence": 0.9,
                    }
                ]
            )
        )
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        # 源事实仍 CURRENT（发现端只提议不 commit）
        for sid in (id_a, id_b):
            cursor = await memory_conn.execute(
                "SELECT status FROM memory_sor WHERE memory_id = ?", (sid,)
            )
            row = await cursor.fetchone()
            assert row["status"] == "current", (
                f"源 {sid} 应仍 current（发现端不得 commit MERGE），实际 {row['status']}"
            )
        # 没有新增 CURRENT 的合并事实（合并目标尚未 commit）
        cursor = await memory_conn.execute(
            "SELECT COUNT(*) AS c FROM memory_sor WHERE scope_id = ? AND status = 'current'",
            (_SCOPE,),
        )
        row = await cursor.fetchone()
        assert row["c"] == 2, "发现端不应新增 CURRENT 事实（合并目标在 Phase D 才 commit）"


# ============================================================
# fallback（FR-B6）
# ============================================================


class TestFallback:
    async def test_llm_none_fallback(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="a", content="x")
        await _seed_fact(memory, subject_key="b", content="y")
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=None
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert outcome.fallback is True
        assert outcome.proposals_made == 0
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []

    async def test_llm_exception_fallback(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="a", content="x")
        await _seed_fact(memory, subject_key="b", content="y")
        llm = _FakeLLM(raise_exc=RuntimeError("provider down"))
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert outcome.fallback is True
        assert outcome.proposals_made == 0

    async def test_llm_empty_response_fallback(self, memory_conn, store_group):
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="a", content="x")
        await _seed_fact(memory, subject_key="b", content="y")
        llm = _FakeLLM(content="   ")
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert outcome.fallback is True

    async def test_malformed_json_no_proposals(self, memory_conn, store_group):
        """解析失败 → 0 提议（保守不产坏候选）。不标 fallback（区别于 LLM 不可用）。"""
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="a", content="x")
        await _seed_fact(memory, subject_key="b", content="y")
        llm = _FakeLLM(content="这不是 JSON，只是一段废话")
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert outcome.proposals_made == 0
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []

    async def test_too_few_facts_no_llm_call(self, memory_conn, store_group):
        """事实 < 2 → 不调 LLM（无合并空间），正常空运行非 fallback。"""
        memory = MemoryService(memory_conn)
        await _seed_fact(memory, subject_key="solo", content="孤独事实")
        llm = _FakeLLM(content=_groups_json([]))
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        assert outcome.facts_reviewed == 1
        assert outcome.proposals_made == 0
        assert llm.calls == [], "事实太少不应调 LLM"


# ============================================================
# C9：LLM 决策，无硬规则判重
# ============================================================


class TestLLMDrivenNoHardRules:
    async def test_hallucinated_ids_dropped(self, memory_conn, store_group):
        """LLM 给的 source_id 不在窗口内 → 该组被丢弃（防幻觉 id 误合并不存在事实）。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(memory, subject_key="a", content="x")
        await _seed_fact(memory, subject_key="b", content="y")
        llm = _FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_a, "01HALLUCINATED_NONEXISTENT"],
                        "merged_content": "merged",
                        "subject_key": "m",
                        "rationale": "r",
                        "confidence": 0.9,
                    }
                ]
            )
        )
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        # 清洗后只剩 1 个 valid id < MIN_GROUP_SOURCE_COUNT(2) → 组被丢
        assert outcome.proposals_made == 0
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []

    async def test_no_keyword_rules_in_source(self):
        """AC-2：发现端源码无关键词/相似度硬规则判重（冗余判断全交 LLM）。"""
        src = Path(
            "/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/"
            "F127-sleep-time/octoagent/apps/gateway/src/octoagent/gateway/services/"
            "consolidation_discovery.py"
        ).read_text(encoding="utf-8")
        # 不得出现相似度/编辑距离/关键词匹配判重的硬编码（C9）
        forbidden = ["difflib", "SequenceMatcher", "levenshtein", "jaccard", "cosine_sim"]
        for token in forbidden:
            assert token.lower() not in src.lower(), (
                f"发现端不得用 {token} 硬规则判重——冗余判断必须交 LLM（C9）"
            )


# ============================================================
# C2 事件 + NFR-3 敏感 + NFR-4 幂等
# ============================================================


class TestEventsSensitiveIdempotency:
    async def test_proposed_event_emitted_no_plaintext(self, memory_conn, store_group):
        """C2：每条候选 emit PROPOSED；payload 不含 merged_content 原文（PII 防护）。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(memory, subject_key="a", content="x")
        id_b = await _seed_fact(memory, subject_key="b", content="y")
        secret_content = "用户的私密时区合并事实ABCDEF"
        llm = _FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_a, id_b],
                        "merged_content": secret_content,
                        "subject_key": "tz",
                        "rationale": "r",
                        "confidence": 0.9,
                    }
                ]
            )
        )
        svc, _ = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        events = await _events_proposed(store_group)
        assert len(events) == 1
        payload_str = json.dumps(events[0].payload, ensure_ascii=False)
        assert secret_content not in payload_str, "PROPOSED payload 不得含 merged_content 原文"
        assert events[0].payload["source_count"] == 2
        assert events[0].payload["content_hash"]  # hash 引用而非原文

    async def test_sensitive_partition_excluded_from_window(
        self, memory_conn, store_group
    ):
        """★ NFR-3（codex P1-1/P1-2 修复）：敏感分区事实**不进巩固窗口**——
        HEALTH/FINANCE 事实不出现在 LLM prompt，不可能产生候选（v0.1 收窄，
        敏感合并推 v0.2 vault-aware MERGE）。"""
        memory = MemoryService(memory_conn)
        id_h1 = await _seed_fact(
            memory, subject_key="h.a", content="血压 120", partition=MemoryPartition.HEALTH
        )
        id_h2 = await _seed_fact(
            memory, subject_key="h.b", content="血压 正常", partition=MemoryPartition.HEALTH
        )
        id_f1 = await _seed_fact(
            memory,
            subject_key="fin.a",
            content="月供 8000",
            partition=MemoryPartition.FINANCE,
        )
        # 普通事实 ×2（保证 LLM 会被调用——非敏感事实数 ≥ MIN_GROUP_SOURCE_COUNT）
        id_p1 = await _seed_fact(memory, subject_key="tz.a", content="时区 上海")
        id_p2 = await _seed_fact(memory, subject_key="tz.b", content="时区 Asia/Shanghai")

        # 对抗性 LLM：无视窗口硬引用敏感 id 组队（模拟 LLM 幻觉/越权引用）
        llm = _FakeLLM(
            content=_groups_json(
                [
                    {
                        "source_ids": [id_h1, id_h2],
                        "merged_content": "用户血压正常约 120",
                        "subject_key": "bp",
                        "rationale": "同指血压",
                        "confidence": 0.9,
                    }
                ]
            )
        )
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=llm
        )
        outcome = await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        # facts_reviewed 只算非敏感（HEALTH×2 + FINANCE×1 被窗口排除）
        assert outcome.facts_reviewed == 2
        # LLM prompt 不含任何敏感事实 id（敏感内容零暴露给巩固 LLM）
        prompt = llm.calls[0]["messages"][0]["content"]
        for sensitive_id in (id_h1, id_h2, id_f1):
            assert sensitive_id not in prompt
        assert id_p1 in prompt and id_p2 in prompt
        # 敏感 id 不在 valid_ids 白名单 → 组被丢弃，0 候选 0 提议
        assert outcome.proposals_made == 0
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []
        assert await _events_proposed(store_group) == []

    async def test_sensitive_defense_in_depth_blocks_mixed_group(
        self, memory_conn, store_group
    ):
        """★ 纵深防御第二层（codex P1-1 any 语义）：直调 _propose_group 构造
        "敏感+普通混组"（绕过窗口，模拟窗口逻辑演化/数据侧漏网）——任一源敏感
        即拒绝产候选，即便众数/目标 partition 是非敏感。"""
        from octoagent.gateway.services.consolidation_discovery import _MergeGroup

        memory = MemoryService(memory_conn)
        # 2 普通 + 1 HEALTH：众数 partition=PROFILE（非敏感）——旧逻辑会以
        # is_sensitive=False 产候选（P1-1 降级 bug），新逻辑整组拒绝
        id_p1 = await _seed_fact(memory, subject_key="tz.a", content="时区 上海")
        id_p2 = await _seed_fact(memory, subject_key="tz.b", content="时区 SH")
        id_h = await _seed_fact(
            memory,
            subject_key="h.a",
            content="血压 120",
            partition=MemoryPartition.HEALTH,
        )
        svc, consol_store = _build_discovery(
            memory=memory, memory_conn=memory_conn, store_group=store_group, llm=None
        )
        facts_by_id = {
            sid: await memory._store.get_sor(sid)  # type: ignore[attr-defined]
            for sid in (id_p1, id_p2, id_h)
        }
        group = _MergeGroup(
            source_sor_ids=[id_p1, id_p2, id_h],
            merged_content="时区上海且血压120（混合泄漏）",
            subject_key="mixed",
            rationale="对抗混组",
            confidence=0.9,
        )
        candidate_id = await svc._propose_group(
            run_id="run-1",
            scope_id=_SCOPE,
            root_task_id=_ROOT_TASK_ID,
            group=group,
            facts_by_id=facts_by_id,
        )
        assert candidate_id is None, "任一源敏感的组必须被纵深防御拒绝"
        assert await consol_store.list_candidates(scope_id=_SCOPE) == []
        # 全敏感组（目标 partition 也敏感）同样拒绝
        group_all_h = _MergeGroup(
            source_sor_ids=[id_h, id_p1],
            merged_content="x",
            subject_key="h2",
            rationale="r",
            confidence=0.5,
        )
        assert (
            await svc._propose_group(
                run_id="run-1",
                scope_id=_SCOPE,
                root_task_id=_ROOT_TASK_ID,
                group=group_all_h,
                facts_by_id={id_h: facts_by_id[id_h]},
            )
            is None
        )

    async def test_duplicate_content_hash_deduped(self, memory_conn, store_group):
        """NFR-4：同 scope 重复内容候选去重（防 crash 重放产重复候选）。"""
        memory = MemoryService(memory_conn)
        id_a = await _seed_fact(memory, subject_key="a", content="x")
        id_b = await _seed_fact(memory, subject_key="b", content="y")
        groups = _groups_json(
            [
                {
                    "source_ids": [id_a, id_b],
                    "merged_content": "完全相同的合并内容",
                    "subject_key": "m",
                    "rationale": "r",
                    "confidence": 0.9,
                }
            ]
        )
        svc, consol_store = _build_discovery(
            memory=memory,
            memory_conn=memory_conn,
            store_group=store_group,
            llm=_FakeLLM(content=groups),
        )
        # 跑两次相同提议
        await svc.discover_and_propose(
            run_id="run-1", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        await svc.discover_and_propose(
            run_id="run-2", scope_id=_SCOPE, root_task_id=_ROOT_TASK_ID
        )
        cands = await consol_store.list_candidates(scope_id=_SCOPE)
        assert len(cands) == 1, "相同 content_hash 候选应去重（NFR-4 幂等）"
