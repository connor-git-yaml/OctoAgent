"""F111 Phase D — BehaviorCompactionService 触发编排单测（AC-10）。

覆盖（仿 test_f127_consolidation_trigger.py 结构）：
- startup 注册 cron + ensure root Task+Work（幂等）
- compact_active=False → SKIPPED(disabled) 不 spawn（cron 路径）
- 并发单飞 → SKIPPED(already_running)；跨 tick 非终态 child → 同
- spawn rejected（capacity）→ SKIPPED(capacity) 优雅退出
- spawn raise → SKIPPED(spawn_error) 不崩（C6）
- spawn written → TRIGGERED(trigger=cron) + 发现端跑（llm=None → fallback COMPLETED）
- cron 范围 = SHARED ∩ eligible 派生（AGENTS/TOOLS/USER）
- SYSTEM_INTERNAL_WORK_IDS 守卫（root work id 防漂移，F127 坑 3 一族）
- 路由字面量 root task id 守卫
- 通知决策表：proposals>0 才发 MEDIUM；0 提议静默
- run_manual：不受 active 门控 + 不 spawn + 返回 outcomes + 共享单飞
- H1：spawn 目标是 SUBAGENT + tool_profile=minimal
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from octoagent.core.behavior_workspace import resolve_write_path_by_file_id
from octoagent.core.models.delegation import DelegationTargetKind, Work, WorkStatus
from octoagent.core.models.enums import EventType
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.behavior_compaction import (
    BEHAVIOR_COMPACT_CRON_FILE_IDS,
    BEHAVIOR_COMPACT_JOB_ID,
    BEHAVIOR_COMPACT_ROOT_TASK_ID,
    BEHAVIOR_COMPACT_ROOT_WORK_ID,
    BEHAVIOR_COMPACT_TOOL_PROFILE,
    BehaviorCompactionService,
)
from octoagent.gateway.services.delegation_plane import SpawnChildResult

# ============================================================
# Fakes（同 F127 trigger 测试范式）
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
    def __init__(
        self,
        *,
        result: SpawnChildResult | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result or SpawnChildResult(
            status="written", task_id="child-task-1"
        )
        self._raise_exc = raise_exc

    async def spawn_child(self, **kwargs: Any) -> SpawnChildResult:
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


class _FakeNotification:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def notify_task_state_change(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _ScriptedLLM:
    """契约合规输出的发现端 stub（驱动真候选产出）。"""

    def __init__(self, compacted: str) -> None:
        self._compacted = compacted

    async def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        text = f"===COMPACTED===\n{self._compacted}\n===RATIONALE===\n合并重复"

        class _R:
            content = text

        return _R()


_USER_MD_ACTIVE = "# 用户档案\n\n- **compact_active**: true\n- **compact_time**: \"03:30\"\n"
_USER_MD_DISABLED = "# 用户档案\n\n- **compact_active**: false\n"

_ORIGINAL = "# AGENTS\n\n" + "- 规则 A 的语义重复表述（用于测试的填充行）\n" * 12
_COMPACTED = "# AGENTS\n\n- 规则 A\n"


@pytest_asyncio.fixture
async def store_group(tmp_path: Path):
    sg = await create_store_group(
        str(tmp_path / "test.db"), str(tmp_path / "artifacts")
    )
    yield sg
    await sg.close()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path / "root"


def _build_service(
    store_group: StoreGroup,
    project_root: Path,
    *,
    user_md: str = "",
    plane: _FakePlane | None = None,
    llm: Any = None,
    notification: Any = None,
) -> BehaviorCompactionService:
    return BehaviorCompactionService(
        scheduler=_FakeScheduler(),
        task_store=store_group.task_store,
        work_store=store_group.work_store,
        event_store=store_group.event_store,
        snapshot_store=_FakeSnapshotStore(user_md=user_md),
        delegation_plane=plane or _FakePlane(),  # type: ignore[arg-type]
        compact_store=store_group.behavior_compact_store,
        project_root=project_root,
        llm_client=llm,
        notification_service=notification,
    )


async def _events_of_type(store_group: StoreGroup, event_type: EventType) -> list[Any]:
    events = await store_group.event_store.get_events_for_task(
        BEHAVIOR_COMPACT_ROOT_TASK_ID
    )
    return [e for e in events if e.type == event_type]


def _write_shared_file(project_root: Path, file_id: str, content: str) -> Path:
    resolved = resolve_write_path_by_file_id(project_root, file_id)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return resolved


# ============================================================
# startup + ensure root
# ============================================================


class TestStartup:
    async def test_startup_registers_cron(self, store_group, project_root):
        svc = _build_service(store_group, project_root, user_md=_USER_MD_ACTIVE)
        await svc.startup()
        add_job = svc._scheduler._scheduler.add_job
        add_job.assert_called_once()
        assert add_job.call_args.kwargs["id"] == BEHAVIOR_COMPACT_JOB_ID

    async def test_startup_ensures_root_task_and_work(self, store_group, project_root):
        svc = _build_service(store_group, project_root)
        await svc.startup()
        task = await store_group.task_store.get_task(BEHAVIOR_COMPACT_ROOT_TASK_ID)
        assert task is not None
        assert task.requester.channel == "system"  # 通用系统任务抑制面覆盖
        work = await store_group.work_store.get_work(BEHAVIOR_COMPACT_ROOT_WORK_ID)
        assert work is not None

    async def test_ensure_root_idempotent(self, store_group, project_root):
        svc = _build_service(store_group, project_root)
        t1, w1 = await svc._ensure_compact_root()
        t2, w2 = await svc._ensure_compact_root()
        assert t1.task_id == t2.task_id
        assert w1.work_id == w2.work_id


# ============================================================
# cron 主流程
# ============================================================


class TestRunCompaction:
    async def test_disabled_skips_no_spawn(self, store_group, project_root):
        plane = _FakePlane()
        svc = _build_service(
            store_group, project_root, user_md=_USER_MD_DISABLED, plane=plane
        )
        await svc._run_compaction()
        assert plane.calls == []
        skipped = await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_SKIPPED)
        assert len(skipped) == 1
        assert skipped[0].payload["reason"] == "disabled"

    async def test_written_emits_triggered_and_runs_discovery(
        self, store_group, project_root
    ):
        _write_shared_file(project_root, "AGENTS.md", _ORIGINAL)
        plane = _FakePlane()
        svc = _build_service(
            store_group,
            project_root,
            user_md=_USER_MD_ACTIVE,
            plane=plane,
            llm=_ScriptedLLM(_COMPACTED),
        )
        await svc._run_compaction()

        # spawn 审计容器（H1：SUBAGENT + minimal）
        assert len(plane.calls) == 1
        assert plane.calls[0]["target_kind"] == DelegationTargetKind.SUBAGENT.value
        assert plane.calls[0]["tool_profile"] == BEHAVIOR_COMPACT_TOOL_PROFILE
        # TRIGGERED(trigger=cron) + 扫描集
        triggered = await _events_of_type(
            store_group, EventType.BEHAVIOR_COMPACT_TRIGGERED
        )
        assert len(triggered) == 1
        assert triggered[0].payload["trigger"] == "cron"
        assert triggered[0].payload["child_task_id"] == "child-task-1"
        assert triggered[0].payload["file_ids"] == list(BEHAVIOR_COMPACT_CRON_FILE_IDS)
        # 发现端真跑：AGENTS 产候选 + COMPLETED
        completed = await _events_of_type(
            store_group, EventType.BEHAVIOR_COMPACT_COMPLETED
        )
        assert len(completed) == 1
        assert completed[0].payload["files_reviewed"] == len(
            BEHAVIOR_COMPACT_CRON_FILE_IDS
        )
        assert completed[0].payload["proposals_made"] == 1
        assert len(await store_group.behavior_compact_store.list_candidates()) == 1

    async def test_rejected_capacity_skips(self, store_group, project_root):
        plane = _FakePlane(
            result=SpawnChildResult(status="rejected", error_code="CAPACITY")
        )
        svc = _build_service(
            store_group, project_root, user_md=_USER_MD_ACTIVE, plane=plane
        )
        await svc._run_compaction()
        skipped = await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_SKIPPED)
        assert [e.payload["reason"] for e in skipped] == ["capacity"]
        assert (
            await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_TRIGGERED)
            == []
        )

    async def test_spawn_raise_skips_gracefully(self, store_group, project_root):
        plane = _FakePlane(raise_exc=RuntimeError("launch boom"))
        svc = _build_service(
            store_group, project_root, user_md=_USER_MD_ACTIVE, plane=plane
        )
        await svc._run_compaction()  # 不崩
        skipped = await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_SKIPPED)
        assert [e.payload["reason"] for e in skipped] == ["spawn_error"]

    async def test_single_flight_skips_concurrent(self, store_group, project_root):
        release = asyncio.Event()

        class _BlockingPlane(_FakePlane):
            async def spawn_child(self, **kwargs: Any) -> SpawnChildResult:
                await release.wait()
                return await super().spawn_child(**kwargs)

        plane = _BlockingPlane()
        svc = _build_service(
            store_group, project_root, user_md=_USER_MD_ACTIVE, plane=plane
        )
        first = asyncio.create_task(svc._run_compaction())
        await asyncio.sleep(0.05)
        await svc._run_compaction()  # 并发触发 → already_running
        release.set()
        await first
        skipped = await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_SKIPPED)
        assert "already_running" in [e.payload["reason"] for e in skipped]

    async def test_active_child_work_skips_cross_tick(self, store_group, project_root):
        """跨 tick 单飞：root Work 下非终态 child → SKIPPED（bool 丢失场景补强）。"""
        plane = _FakePlane()
        svc = _build_service(
            store_group, project_root, user_md=_USER_MD_ACTIVE, plane=plane
        )
        _, root_work = await svc._ensure_compact_root()
        now = datetime.now(UTC)
        child = Work(
            work_id="bcpt-child-active",
            task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
            parent_work_id=root_work.work_id,
            title="上一轮审计容器",
            status=WorkStatus.RUNNING,  # 非终态
            target_kind=DelegationTargetKind.SUBAGENT,
            created_at=now,
            updated_at=now,
        )
        await store_group.work_store.save_work(child)
        await store_group.conn.commit()

        await svc._run_compaction()

        assert plane.calls == []
        skipped = await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_SKIPPED)
        assert [e.payload["reason"] for e in skipped] == ["already_running"]


# ============================================================
# run_manual（手动触发）
# ============================================================


class TestRunManual:
    async def test_manual_ignores_active_gate_and_no_spawn(
        self, store_group, project_root
    ):
        """DP-2：active=False 不拦手动；手动不 spawn 审计容器。"""
        _write_shared_file(project_root, "AGENTS.md", _ORIGINAL)
        plane = _FakePlane()
        svc = _build_service(
            store_group,
            project_root,
            user_md=_USER_MD_DISABLED,  # cron 被关
            plane=plane,
            llm=_ScriptedLLM(_COMPACTED),
        )
        result = await svc.run_manual(file_ids=["AGENTS.md"])

        assert result.skipped_reason == ""
        assert plane.calls == []  # 前台直调不 spawn
        assert len(result.outcomes) == 1
        assert result.outcomes[0].status == "proposed"
        triggered = await _events_of_type(
            store_group, EventType.BEHAVIOR_COMPACT_TRIGGERED
        )
        assert triggered[0].payload["trigger"] == "manual"
        assert triggered[0].payload["child_task_id"] == ""

    async def test_manual_default_targets_cron_set(self, store_group, project_root):
        svc = _build_service(store_group, project_root, llm=None)
        result = await svc.run_manual()
        assert [o.file_id for o in result.outcomes] == list(
            BEHAVIOR_COMPACT_CRON_FILE_IDS
        )

    async def test_manual_shares_single_flight(self, store_group, project_root):
        svc = _build_service(store_group, project_root)
        svc._running = True  # cron 在跑
        result = await svc.run_manual(file_ids=["AGENTS.md"])
        assert result.skipped_reason == "already_running"
        assert result.outcomes == []
        svc._running = False

    async def test_manual_discovery_failure_surfaces_error(
        self, store_group, project_root, monkeypatch
    ):
        """Codex round3 P2 闭环：发现端异常 → error 通道非空成功（REST 据此 500）。"""
        svc = _build_service(store_group, project_root)

        async def _boom(**kwargs: Any):
            raise RuntimeError("discovery crashed")

        monkeypatch.setattr(
            "octoagent.gateway.services.behavior_compact_discovery."
            "BehaviorCompactDiscoveryService.discover_files",
            _boom,
        )
        result = await svc.run_manual(file_ids=["AGENTS.md"])
        assert result.error  # 显式失败通道
        assert result.outcomes == []
        failed = await _events_of_type(store_group, EventType.BEHAVIOR_COMPACT_FAILED)
        assert len(failed) == 1

    async def test_manual_blocked_by_active_child(self, store_group, project_root):
        """Codex round2 P1 闭环：cron 审计 child 非终态（含重启后 _running 丢失
        场景）→ 手动同样 skip（'cron/manual 共享单飞'的持久半边）。"""
        svc = _build_service(store_group, project_root, llm=None)
        _, root_work = await svc._ensure_compact_root()
        now = datetime.now(UTC)
        await store_group.work_store.save_work(
            Work(
                work_id="bcpt-child-manual-block",
                task_id=BEHAVIOR_COMPACT_ROOT_TASK_ID,
                parent_work_id=root_work.work_id,
                title="上一轮审计容器",
                status=WorkStatus.RUNNING,  # 非终态
                target_kind=DelegationTargetKind.SUBAGENT,
                created_at=now,
                updated_at=now,
            )
        )
        await store_group.conn.commit()

        result = await svc.run_manual(file_ids=["AGENTS.md"])
        assert result.skipped_reason == "already_running"
        assert result.outcomes == []


# ============================================================
# 通知决策表（F127 同款）
# ============================================================


class TestNotification:
    async def test_notifies_only_when_proposals_made(self, store_group, project_root):
        _write_shared_file(project_root, "AGENTS.md", _ORIGINAL)
        notification = _FakeNotification()
        svc = _build_service(
            store_group,
            project_root,
            user_md=_USER_MD_ACTIVE,
            llm=_ScriptedLLM(_COMPACTED),
            notification=notification,
        )
        await svc._run_compaction()
        assert len(notification.calls) == 1
        call = notification.calls[0]
        assert call["session_id"] == ""  # 全局通知桶
        assert call["payload"]["proposals_made"] == 1
        assert "run_id" in call["payload"]

    async def test_zero_proposals_silent(self, store_group, project_root):
        # 无任何 behavior 文件盘上就位 → 0 提议
        notification = _FakeNotification()
        svc = _build_service(
            store_group,
            project_root,
            user_md=_USER_MD_ACTIVE,
            llm=None,
            notification=notification,
        )
        await svc._run_compaction()
        assert notification.calls == []

    async def test_manual_never_notifies(self, store_group, project_root):
        _write_shared_file(project_root, "AGENTS.md", _ORIGINAL)
        notification = _FakeNotification()
        svc = _build_service(
            store_group,
            project_root,
            llm=_ScriptedLLM(_COMPACTED),
            notification=notification,
        )
        result = await svc.run_manual(file_ids=["AGENTS.md"])
        assert result.outcomes[0].status == "proposed"
        assert notification.calls == []  # 用户在场，响应即结果


# ============================================================
# 守卫（防漂移，F127 坑 3 一族）
# ============================================================


class TestGuards:
    def test_root_work_id_in_control_plane_exclusion_set(self):
        """SYSTEM_INTERNAL_WORK_IDS 必须含 compact root work id（占位泄漏防御）。"""
        from octoagent.gateway.services.control_plane._base import (
            SYSTEM_INTERNAL_WORK_IDS,
        )

        assert BEHAVIOR_COMPACT_ROOT_WORK_ID in SYSTEM_INTERNAL_WORK_IDS

    def test_route_literal_root_task_id_matches(self):
        """路由字面量与服务常量一致（避免 import apscheduler 链的字面量防漂移）。"""
        from octoagent.gateway.routes.behavior_compact import (
            _BEHAVIOR_COMPACT_ROOT_TASK_ID,
        )

        assert _BEHAVIOR_COMPACT_ROOT_TASK_ID == BEHAVIOR_COMPACT_ROOT_TASK_ID

    def test_cron_file_ids_derived_shared_eligible(self):
        """cron 范围 = SHARED ∩ eligible（派生守卫，DP-6）。"""
        from octoagent.core.behavior_workspace import (
            COMPACT_ELIGIBLE_FILE_IDS,
            SHARED_BEHAVIOR_FILE_IDS,
        )

        assert set(BEHAVIOR_COMPACT_CRON_FILE_IDS) == set(
            COMPACT_ELIGIBLE_FILE_IDS
        ) & set(SHARED_BEHAVIOR_FILE_IDS)
        assert set(BEHAVIOR_COMPACT_CRON_FILE_IDS) == {
            "AGENTS.md",
            "TOOLS.md",
            "USER.md",
        }

    def test_tool_profile_is_coercion_stable_most_restricted(self):
        """tool_profile 必须是 _coerce_tool_profile 合法值（F127 Codex 抓过未知串
        被静默降级 standard 的坑）。"""
        assert BEHAVIOR_COMPACT_TOOL_PROFILE in {"minimal", "standard", "privileged"}
        assert BEHAVIOR_COMPACT_TOOL_PROFILE == "minimal"
