"""F111 Phase C — BehaviorCompactApprovalService 人审单测（AC-8，C4/C7 红线）。

覆盖：
- accept 全链：claim → 新鲜度 → 覆写落盘 + record_behavior_version + cache invalidate
  + APPLIED（CAS）+ 事件（actor=USER）
- reject：文件零触碰 + REJECTED
- source_hash 失配（pending 期间被编辑 / 文件被删）→ CONFLICT 终态 + 不落盘 + 事件
  （actor=SYSTEM）+ CONFLICT 后同文件新提议不被幂等账本阻断
- 禁区第二层：候选 file_id 不在白名单（存量脏数据）→ CONFLICT(not_eligible)
- claim 竞态：双 accept 只落盘一次
- 落盘自身异常 → 回滚 PENDING（候选可重审，F127 handoff 坑 5）
- not_found / 重复 reject conflict
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from octoagent.core.behavior_workspace import resolve_write_path_by_file_id
from octoagent.core.models import RequesterInfo, Task
from octoagent.core.models.behavior_compact import (
    BehaviorCompactCandidate,
    BehaviorCompactCandidateStatus,
)
from octoagent.core.models.enums import ActorType, EventType, TaskStatus
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.behavior_compact_approval import (
    BehaviorCompactApprovalService,
)

_ROOT_TASK = "_behavior_compact_root"
_ORIGINAL = "# AGENTS\n\n- 规则 A（重复表述一）\n- 规则 A（重复表述二）\n- 规则 B\n"
_COMPACTED = "# AGENTS\n\n- 规则 A\n- 规则 B\n"


@pytest_asyncio.fixture
async def env(tmp_path: Path):
    store_group = await create_store_group(
        str(tmp_path / "test.db"), str(tmp_path / "artifacts")
    )
    project_root = tmp_path / "root"
    now = datetime.now(UTC)
    await store_group.task_store.create_task(
        Task(
            task_id=_ROOT_TASK,
            created_at=now,
            updated_at=now,
            status=TaskStatus.SUCCEEDED,
            title="F111 root",
            requester=RequesterInfo(channel="system", sender_id="test"),
        )
    )
    await store_group.conn.commit()
    yield store_group, project_root
    await store_group.close()


def _write_file(project_root: Path, file_id: str, content: str) -> Path:
    resolved = resolve_write_path_by_file_id(project_root, file_id)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return resolved


async def _insert_candidate(
    store_group: StoreGroup,
    *,
    candidate_id: str = "cand-1",
    file_id: str = "AGENTS.md",
    source_content: str = _ORIGINAL,
    compacted: str = _COMPACTED,
) -> BehaviorCompactCandidate:
    cand = BehaviorCompactCandidate(
        candidate_id=candidate_id,
        run_id="bcpt-run-1",
        file_id=file_id,
        source_hash=hashlib.sha256(source_content.encode("utf-8")).hexdigest(),
        compacted_content=compacted,
        rationale="合并重复规则",
        size_before=len(source_content),
        size_after=len(compacted),
        content_hash=hashlib.sha256(compacted.encode("utf-8")).hexdigest(),
        created_at=datetime.now(UTC),
    )
    await store_group.behavior_compact_store.insert_candidate(cand)
    await store_group.conn.commit()
    return cand


def _service(store_group: StoreGroup, project_root: Path) -> BehaviorCompactApprovalService:
    return BehaviorCompactApprovalService(
        project_root=project_root,
        compact_store=store_group.behavior_compact_store,
        event_store=store_group.event_store,
        stores=store_group,
        root_task_id=_ROOT_TASK,
    )


async def _events(store_group: StoreGroup, event_type: EventType) -> list[Any]:
    events = await store_group.event_store.get_events_for_task(_ROOT_TASK)
    return [e for e in events if e.type == event_type]


# ============================================================
# accept 主链
# ============================================================


@pytest.mark.asyncio
async def test_accept_applies_write_version_cache_event(env):
    store_group, project_root = env
    resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    with patch(
        "octoagent.gateway.services.agent_decision.invalidate_behavior_pack_cache"
    ) as invalidate:
        result = await svc.accept("cand-1")

    assert result.ok is True
    assert result.status == "applied"
    # 1. 覆写落盘
    assert resolved.read_text(encoding="utf-8") == _COMPACTED
    # 2. F107 版本记录（record-after + 首版 baseline → baseline + 新版两条）
    from octoagent.core.behavior_workspace import behavior_version_key_from_path

    key = behavior_version_key_from_path(project_root, resolved)
    versions = await store_group.behavior_version_store.list_versions(key)
    assert len(versions) == 2
    latest = await store_group.behavior_version_store.get_version_content(
        key, versions[0].version_no
    )
    assert latest is not None and latest.content == _COMPACTED
    baseline = await store_group.behavior_version_store.get_version_content(
        key, versions[-1].version_no
    )
    assert baseline is not None and baseline.content == _ORIGINAL
    # 3. 缓存失效被调
    invalidate.assert_called_once()
    # 4. 状态 APPLIED + decided_at
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.APPLIED
    assert cand.decided_at is not None
    # 5. 事件（actor=USER，人审动作）
    applied = await _events(store_group, EventType.BEHAVIOR_COMPACT_APPLIED)
    assert len(applied) == 1
    assert applied[0].actor == ActorType.USER
    assert applied[0].payload["file_id"] == "AGENTS.md"


@pytest.mark.asyncio
async def test_reject_leaves_file_untouched(env):
    store_group, project_root = env
    resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    result = await svc.reject("cand-1")

    assert result.ok is True
    assert result.status == "rejected"
    assert resolved.read_text(encoding="utf-8") == _ORIGINAL
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.REJECTED
    rejected = await _events(store_group, EventType.BEHAVIOR_COMPACT_REJECTED)
    assert len(rejected) == 1
    # 重复 reject → conflict（不重复拒绝）
    again = await svc.reject("cand-1")
    assert again.ok is False
    assert again.status == "conflict"


# ============================================================
# CONFLICT 新鲜度（spec §0.1.2 问题 3）
# ============================================================


@pytest.mark.asyncio
async def test_source_changed_conflicts_no_write(env):
    """pending 期间文件被编辑 → CONFLICT 终态 + 不落盘（US-4）。"""
    store_group, project_root = env
    resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    # 用户过夜编辑了文件
    edited = _ORIGINAL + "- 用户半夜新加的规则\n"
    resolved.write_text(edited, encoding="utf-8")
    svc = _service(store_group, project_root)

    result = await svc.accept("cand-1")

    assert result.ok is False
    assert result.status == "conflict"
    assert resolved.read_text(encoding="utf-8") == edited  # 零触碰
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.CONFLICT
    conflicted = await _events(store_group, EventType.BEHAVIOR_COMPACT_CONFLICTED)
    assert len(conflicted) == 1
    assert conflicted[0].actor == ActorType.SYSTEM  # 系统检测非用户决策
    assert conflicted[0].payload["reason"] == "source_changed"
    # CONFLICT 后同文件基于新源可重新提议（输入幂等账本不阻断，白名单式）
    assert (
        await store_group.behavior_compact_store.has_blocking_candidate(
            file_id="AGENTS.md",
            agent_slug="main",
            project_slug="default",
            source_hash=cand.source_hash,
        )
        is False
    )


@pytest.mark.asyncio
async def test_missing_file_conflicts(env):
    store_group, project_root = env
    await _insert_candidate(store_group)  # 盘上从未写文件
    svc = _service(store_group, project_root)
    result = await svc.accept("cand-1")
    assert result.status == "conflict"
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.CONFLICT


@pytest.mark.asyncio
async def test_protected_duplicated_in_candidate_conflicts(env):
    """Codex round3 P2 闭环：候选行被数据侧改写成重复 PROTECTED 区段——`in` 检查
    过但 exact-once 复验必须拦（CONFLICT，不落盘）。"""
    from octoagent.core.behavior_workspace import (
        PROTECTED_CLOSE_MARKER,
        PROTECTED_OPEN_MARKER,
    )

    store_group, project_root = env
    section = f"{PROTECTED_OPEN_MARKER}\n- 红线\n{PROTECTED_CLOSE_MARKER}"
    source = f"# AGENTS\n\n{section}\n\n- 冗余规则一\n- 冗余规则二\n"
    resolved = _write_file(project_root, "AGENTS.md", source)
    # 候选内容把 PROTECTED 区段重复了两次（模拟数据侧损坏）
    corrupted = f"# AGENTS\n\n{section}\n{section}\n"
    await _insert_candidate(
        store_group, source_content=source, compacted=corrupted
    )
    svc = _service(store_group, project_root)

    result = await svc.accept("cand-1")

    assert result.status == "conflict"
    assert resolved.read_text(encoding="utf-8") == source  # 零触碰
    conflicted = await _events(store_group, EventType.BEHAVIOR_COMPACT_CONFLICTED)
    assert conflicted[0].payload["reason"] == "protected_reverify_failed"


@pytest.mark.asyncio
async def test_not_eligible_candidate_conflicts(env):
    """禁区第二层（FR-6）：存量脏数据候选（SOUL.md）→ CONFLICT(not_eligible)。"""
    store_group, project_root = env
    _write_file(project_root, "SOUL.md", _ORIGINAL)
    await _insert_candidate(store_group, file_id="SOUL.md")
    svc = _service(store_group, project_root)

    result = await svc.accept("cand-1")

    assert result.status == "conflict"
    conflicted = await _events(store_group, EventType.BEHAVIOR_COMPACT_CONFLICTED)
    assert conflicted[0].payload["reason"] == "not_eligible"
    resolved = resolve_write_path_by_file_id(project_root, "SOUL.md")
    assert resolved.read_text(encoding="utf-8") == _ORIGINAL


# ============================================================
# claim 竞态 + 回滚
# ============================================================


@pytest.mark.asyncio
async def test_double_accept_only_writes_once(env):
    store_group, project_root = env
    resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    first = await svc.accept("cand-1")
    assert first.ok is True
    # 重放 accept：claim 失败 → conflict，不重复落盘
    second = await svc.accept("cand-1")
    assert second.ok is False
    assert second.status == "conflict"
    assert resolved.read_text(encoding="utf-8") == _COMPACTED
    # F107 版本仍只有一次 accept 的两条（baseline + 新版）
    from octoagent.core.behavior_workspace import behavior_version_key_from_path

    key = behavior_version_key_from_path(project_root, resolved)
    versions = await store_group.behavior_version_store.list_versions(key)
    assert len(versions) == 2


@pytest.mark.asyncio
async def test_write_failure_rolls_back_to_pending(env):
    """落盘自身异常 → 回滚 PENDING 可重试（handoff 坑 5：绝不卡死 APPLYING）。"""
    store_group, project_root = env
    _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    with patch(
        "octoagent.gateway.services.behavior_compact_approval.commit_behavior_file_write",
        side_effect=OSError("disk full"),
    ):
        result = await svc.accept("cand-1")

    assert result.ok is False
    assert result.status == "pending"
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.PENDING
    # 重试可成功
    retry = await svc.accept("cand-1")
    assert retry.ok is True


@pytest.mark.asyncio
async def test_verify_own_exception_rolls_back(env):
    """验证自身异常（临时 IO）→ 回滚 PENDING（判定失败与自身异常二分）。"""
    store_group, project_root = env
    _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    with patch.object(
        BehaviorCompactApprovalService,
        "_verify_for_apply",
        side_effect=RuntimeError("transient"),
    ):
        result = await svc.accept("cand-1")

    assert result.ok is False
    assert result.status == "pending"
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.PENDING


@pytest.mark.asyncio
async def test_not_found(env):
    store_group, project_root = env
    svc = _service(store_group, project_root)
    assert (await svc.accept("ghost")).status == "not_found"
    assert (await svc.reject("ghost")).status == "not_found"


@pytest.mark.asyncio
async def test_accept_commit_failure_honest_failure(env, monkeypatch):
    """Codex P1 闭环：状态提交失败 → 绝不报成功、绝不 emit APPLIED；
    候选补偿回 pending，重 accept 走 CONFLICT(source_changed) 确定性收敛。"""
    store_group, project_root = env
    resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    async def _fail_commit() -> bool:
        return False

    monkeypatch.setattr(svc, "_commit_tx", _fail_commit)
    result = await svc.accept("cand-1")

    assert result.ok is False
    assert result.status == "pending"
    assert "提交失败" in result.detail
    # 文件已覆写（durable 事实，诚实归档的固有窗口）
    assert resolved.read_text(encoding="utf-8") == _COMPACTED
    # 绝不 emit APPLIED（状态未 durable）
    assert await _events(store_group, EventType.BEHAVIOR_COMPACT_APPLIED) == []
    # 补偿回 pending（同连接可见）→ 重 accept 走 CONFLICT 收敛
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.PENDING
    monkeypatch.undo()
    retry = await svc.accept("cand-1")
    assert retry.status == "conflict"  # 盘上已是精简后内容，source_hash 失配


@pytest.mark.asyncio
async def test_conflict_commit_failure_honest_retry(env, monkeypatch):
    """Codex round6 P1 闭环：CONFLICT 终态提交失败 → 不宣称终态不 emit，
    候选补偿回 pending 可重试（验证判定确定性，重试收敛到 durable CONFLICT）。"""
    store_group, project_root = env
    resolved = _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    resolved.write_text(_ORIGINAL + "- 半夜新规则\n", encoding="utf-8")  # 源已变更
    svc = _service(store_group, project_root)

    async def _fail_commit() -> bool:
        return False

    monkeypatch.setattr(svc, "_commit_tx", _fail_commit)
    result = await svc.accept("cand-1")

    assert result.ok is False
    assert result.status == "pending"
    assert "提交失败" in result.detail
    assert await _events(store_group, EventType.BEHAVIOR_COMPACT_CONFLICTED) == []
    cand = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand is not None
    assert cand.status is BehaviorCompactCandidateStatus.PENDING
    # 恢复后重试收敛到 durable CONFLICT
    monkeypatch.undo()
    retry = await svc.accept("cand-1")
    assert retry.status == "conflict"
    cand2 = await store_group.behavior_compact_store.get_candidate("cand-1")
    assert cand2 is not None
    assert cand2.status is BehaviorCompactCandidateStatus.CONFLICT


@pytest.mark.asyncio
async def test_ensure_root_commit_failure_raises(env, monkeypatch):
    """Codex round6 P2 闭环：root 占位 commit 失败必须上抛（半初始化状态下运行
    会让事件 FK 静默丢 + spawn lineage 断——路由据此 500 保护 C2 不变量）。"""
    store_group, _ = env

    from octoagent.gateway.services.behavior_compact_root import (
        ensure_behavior_compact_root,
    )

    async def _boom() -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(store_group.conn, "commit", _boom)
    with pytest.raises(RuntimeError, match="disk full"):
        await ensure_behavior_compact_root(
            store_group.task_store, store_group.work_store
        )


@pytest.mark.asyncio
async def test_reject_commit_failure_honest_failure(env, monkeypatch):
    """Codex P1 同族：reject 状态提交失败 → 不报成功不 emit，候选仍 pending 可重试。"""
    store_group, project_root = env
    _write_file(project_root, "AGENTS.md", _ORIGINAL)
    await _insert_candidate(store_group)
    svc = _service(store_group, project_root)

    async def _fail_commit() -> bool:
        return False

    monkeypatch.setattr(svc, "_commit_tx", _fail_commit)
    result = await svc.reject("cand-1")

    assert result.ok is False
    assert result.status == "pending"
    assert await _events(store_group, EventType.BEHAVIOR_COMPACT_REJECTED) == []
    monkeypatch.undo()
    retry = await svc.reject("cand-1")
    assert retry.ok is True  # 恢复后可重拒
