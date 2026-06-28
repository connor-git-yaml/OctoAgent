"""F127 Phase B — MemoryConsolidationService 触发编排单测。

覆盖 `[@test]` 绑定（plan §Phase B / FR-A1~A6）：
- cron 注册（startup → add_job）
- consolidation_active=False → SKIPPED(disabled) 不 spawn
- spawn rejected（capacity）→ SKIPPED(capacity) 优雅退出
- 并发单飞 try-lock-skip → SKIPPED(already_running)
- spawn written → TRIGGERED + child_task_id
- ensure root Task+Work 幂等
- H1：巩固全程无 user-facing 对话（spawn child 是 SUBAGENT_INTERNAL，无用户通道）
- spawn launch raise → SKIPPED(spawn_error) 不崩（C6）

**关键**：用真 StoreGroup（SQLite）验证 ensure root 真持久化 + spawn_child 真拿到 task+work
对象（验证 §0.1.1 "必须传真对象不能 None"）；fake delegation_plane 只捕获 spawn_child 调用
参数（验证编排正确），不真启动 subagent（task_runner 不在 Phase B 范围）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest_asyncio
from octoagent.core.models.delegation import DelegationTargetKind
from octoagent.core.models.enums import EventType
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.delegation_plane import SpawnChildResult
from octoagent.gateway.services.memory_consolidation import (
    CONSOLIDATION_JOB_ID,
    CONSOLIDATION_ROOT_TASK_ID,
    CONSOLIDATION_ROOT_WORK_ID,
    MemoryConsolidationService,
)

# ============================================================
# Fakes
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
    """捕获 spawn_child 调用参数 + 返回可配置 SpawnChildResult。

    可配置：result（默认 written）或 raise_exc（模拟 launch raise）。
    """

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


# 启用巩固的 USER.md（active=true）
_USER_MD_ACTIVE = """# 用户档案

- **consolidation_active**: true
- **consolidation_time**: "03:30"
- **consolidation_window_days**: 14
- **consolidation_max_facts**: 80
"""

_USER_MD_DISABLED = """# 用户档案

- **consolidation_active**: false
"""


@pytest_asyncio.fixture
async def store_group(tmp_path: Path) -> StoreGroup:
    db_path = str(tmp_path / "test.db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    return await create_store_group(db_path, str(artifacts_dir))


def _build_service(
    store_group: StoreGroup,
    *,
    user_md: str = "",
    plane: _FakePlane | None = None,
    agent_context_store: Any = None,
) -> MemoryConsolidationService:
    return MemoryConsolidationService(
        scheduler=_FakeScheduler(),
        task_store=store_group.task_store,
        work_store=store_group.work_store,
        event_store=store_group.event_store,
        snapshot_store=_FakeSnapshotStore(user_md=user_md),
        delegation_plane=plane or _FakePlane(),  # type: ignore[arg-type]
        agent_context_store=agent_context_store,
    )


async def _events_of_type(
    store_group: StoreGroup, event_type: EventType
) -> list[Any]:
    events = await store_group.event_store.get_events_for_task(
        CONSOLIDATION_ROOT_TASK_ID
    )
    return [e for e in events if e.type == event_type]


# ============================================================
# Cron 注册 + ensure root
# ============================================================


class TestStartup:
    async def test_startup_registers_cron(self, store_group):
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE)
        await svc.startup()
        # add_job 被调用，id 正确
        add_job = svc._scheduler._scheduler.add_job
        add_job.assert_called_once()
        assert add_job.call_args.kwargs["id"] == CONSOLIDATION_JOB_ID

    async def test_startup_ensures_root_task_and_work(self, store_group):
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE)
        await svc.startup()
        task = await store_group.task_store.get_task(CONSOLIDATION_ROOT_TASK_ID)
        work = await store_group.work_store.get_work(CONSOLIDATION_ROOT_WORK_ID)
        assert task is not None
        assert work is not None
        assert work.task_id == CONSOLIDATION_ROOT_TASK_ID
        assert work.target_kind == DelegationTargetKind.SUBAGENT
        # root task 有显式 thread_id（子 thread 命名稳定）
        assert task.thread_id == "_memory_consolidation"
        assert task.requester.channel == "system"

    async def test_startup_idempotent(self, store_group):
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE)
        await svc.startup()
        await svc.startup()  # 第二次 _started 守卫直接 return，不重复 add_job
        assert svc._scheduler._scheduler.add_job.call_count == 1

    async def test_ensure_root_idempotent_no_duplicate(self, store_group):
        """重复 ensure root 不产生重复 Task/Work（幂等，沿用 F102 ensure 范式）。"""
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE)
        t1, w1 = await svc._ensure_consolidation_root()
        t2, w2 = await svc._ensure_consolidation_root()
        assert t1.task_id == t2.task_id == CONSOLIDATION_ROOT_TASK_ID
        assert w1.work_id == w2.work_id == CONSOLIDATION_ROOT_WORK_ID


# ============================================================
# 触发主流程（FR-A2~A6）
# ============================================================


class TestRunConsolidation:
    async def test_disabled_skips_no_spawn(self, store_group):
        """FR-A2：active=False → SKIPPED(disabled) 不 spawn。

        关键：**不**预先 ensure root——验证 _run_consolidation 内部先 ensure（FK 安全），
        SKIPPED 事件能持久化（events 表 FK(task_id) REFERENCES tasks，root 必须先在）。
        """
        plane = _FakePlane()
        svc = _build_service(store_group, user_md=_USER_MD_DISABLED, plane=plane)
        await svc._run_consolidation()
        assert plane.calls == []  # 未 spawn
        skipped = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_SKIPPED
        )
        assert len(skipped) == 1
        assert skipped[0].payload["reason"] == "disabled"
        # root task 确实被 in-flow ensure 出来了（FK 目标存在）
        assert (
            await store_group.task_store.get_task(CONSOLIDATION_ROOT_TASK_ID)
            is not None
        )

    async def test_written_emits_triggered(self, store_group):
        """FR-A3/A4：active=True + spawn written → TRIGGERED + child_task_id。"""
        plane = _FakePlane(
            result=SpawnChildResult(status="written", task_id="child-xyz")
        )
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)
        await svc._run_consolidation()
        # spawn 被调用一次，参数正确
        assert len(plane.calls) == 1
        call = plane.calls[0]
        assert call["target_kind"] == DelegationTargetKind.SUBAGENT.value
        assert call["callback_mode"] == "async"
        assert call["emit_audit_event"] is False
        assert call["spawned_by"] == "memory_consolidation"
        # NFR-3：受限 tool_profile（不能是被静默降级的未知串）
        assert call["tool_profile"] == "minimal"
        # parent_task / parent_work 是真对象（非 None）——§0.1.1 核心
        assert call["parent_task"] is not None
        assert call["parent_work"] is not None
        assert call["parent_task"].task_id == CONSOLIDATION_ROOT_TASK_ID
        assert call["parent_work"].work_id == CONSOLIDATION_ROOT_WORK_ID
        # TRIGGERED 事件
        triggered = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_TRIGGERED
        )
        assert len(triggered) == 1
        assert triggered[0].payload["child_task_id"] == "child-xyz"
        # config 透传：window_days/max_facts 进 payload
        assert triggered[0].payload["window_days"] == 14
        assert triggered[0].payload["max_facts"] == 80

    async def test_rejected_capacity_skips(self, store_group):
        """FR-A4：spawn rejected（capacity）→ SKIPPED(capacity) 优雅退出不报错。"""
        plane = _FakePlane(
            result=SpawnChildResult(
                status="rejected",
                error_code="CAPACITY_EXCEEDED",
                reason="too many children",
            )
        )
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)
        await svc._run_consolidation()  # 不应抛
        skipped = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_SKIPPED
        )
        assert len(skipped) == 1
        assert skipped[0].payload["reason"] == "capacity"
        # 未写 TRIGGERED
        triggered = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_TRIGGERED
        )
        assert triggered == []

    async def test_spawn_raise_skips_gracefully(self, store_group):
        """C6：spawn launch raise → SKIPPED(spawn_error) 不崩。"""
        plane = _FakePlane(raise_exc=RuntimeError("task runner not bound"))
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)
        await svc._run_consolidation()  # 不应抛
        skipped = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_SKIPPED
        )
        assert len(skipped) == 1
        assert skipped[0].payload["reason"] == "spawn_error"
        # 运行标志已复位（finally），可再次触发
        assert svc._running is False

    async def test_single_flight_skips_concurrent(self, store_group):
        """FR-A5：运行中再触发 → SKIPPED(already_running) 立即 return 不排队。"""
        # 用一个会阻塞的 plane 模拟"巩固运行中"
        release = asyncio.Event()

        class _BlockingPlane:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            async def spawn_child(self, **kwargs: Any) -> SpawnChildResult:
                self.calls.append(kwargs)
                await release.wait()  # 卡住，模拟巩固进行中
                return SpawnChildResult(status="written", task_id="child-1")

        plane = _BlockingPlane()
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)  # type: ignore[arg-type]

        # 第一次触发（卡在 spawn）
        first = asyncio.create_task(svc._run_consolidation())
        await asyncio.sleep(0.05)  # 让 first 进入 _running=True + spawn await
        assert svc._running is True

        # 第二次触发（应立即 skip）
        await svc._run_consolidation()
        skipped = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_SKIPPED
        )
        assert len(skipped) == 1
        assert skipped[0].payload["reason"] == "already_running"
        # 第二次没有 spawn（只有 first 的一次 call）
        assert len(plane.calls) == 1

        # 放行 first 完成
        release.set()
        await first
        assert svc._running is False

    async def test_active_child_work_skips_cross_tick(self, store_group):
        """FR-A5 跨 tick 补强（Codex review）：root Work 下存在非终态 child Work →
        即便进程内 _running=False（新 tick / 进程重启）也跳过，不并行起第二个巩固。

        模拟：进程重启后 _running 丢失，但上一轮巩固 child Work 仍 ASSIGNED（非终态）。
        """
        from octoagent.core.models.delegation import (
            DelegationTargetKind,
            Work,
            WorkStatus,
        )

        plane = _FakePlane()
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)
        # ensure root 就位
        _, root_work = await svc._ensure_consolidation_root()
        # 注入一个非终态 child Work（上一轮巩固仍在跑）
        active_child = Work(
            work_id="cons-active-child",
            task_id="cons-active-child-task",
            parent_work_id=root_work.work_id,
            title="上一轮巩固 child",
            status=WorkStatus.ASSIGNED,  # 非终态
            target_kind=DelegationTargetKind.SUBAGENT,
        )
        await store_group.work_store.save_work(active_child)
        await store_group.conn.commit()

        # _running 默认 False（模拟新 tick / 进程重启）
        assert svc._running is False
        await svc._run_consolidation()

        # 不 spawn（持久态 child 拦下）
        assert plane.calls == []
        skipped = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_SKIPPED
        )
        assert len(skipped) == 1
        assert skipped[0].payload["reason"] == "already_running"

    async def test_terminal_child_work_allows_new_run(self, store_group):
        """对照：root Work 下 child 全部终态（上一轮巩固已完成）→ 允许派新一轮。"""
        from octoagent.core.models.delegation import (
            DelegationTargetKind,
            Work,
            WorkStatus,
        )

        plane = _FakePlane(
            result=SpawnChildResult(status="written", task_id="child-new")
        )
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)
        _, root_work = await svc._ensure_consolidation_root()
        done_child = Work(
            work_id="cons-done-child",
            task_id="cons-done-child-task",
            parent_work_id=root_work.work_id,
            title="上一轮巩固已完成",
            status=WorkStatus.SUCCEEDED,  # 终态
            target_kind=DelegationTargetKind.SUBAGENT,
        )
        await store_group.work_store.save_work(done_child)
        await store_group.conn.commit()

        await svc._run_consolidation()
        # 终态 child 不拦，正常派新一轮
        assert len(plane.calls) == 1
        triggered = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_TRIGGERED
        )
        assert len(triggered) == 1


# ============================================================
# H1 守界
# ============================================================


class TestH1Boundary:
    async def test_spawn_targets_subagent_not_user(self, store_group):
        """H1：巩固派 SUBAGENT（后台 spawn-and-die），不向用户发起对话。

        验证 spawn target_kind=subagent + parent.requester.channel=system（非用户渠道），
        子 subagent 走 SUBAGENT_INTERNAL session 无 user-facing 通道。
        """
        plane = _FakePlane()
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE, plane=plane)
        await svc._run_consolidation()
        call = plane.calls[0]
        assert call["target_kind"] == DelegationTargetKind.SUBAGENT.value
        # parent task requester 是 system（非 telegram/web 用户渠道）
        assert call["parent_task"].requester.channel == "system"
        assert call["parent_task"].requester.sender_id == "memory_consolidation"

    async def test_no_notification_in_phase_b(self, store_group):
        """Phase B 不发通知（通知是 Phase E）——本服务无 notification_service 依赖。"""
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE)
        # 构造签名里根本没有 notification_service（H1 通知走 Phase E NotificationService）
        assert not hasattr(svc, "_notification_service")


class TestNFR3RestrictedToolProfile:
    """NFR-3 / C5 最小权限：巩固 subagent tool_profile 必须是 capability_pack 支持的
    合法受限值——传未知串会被 _coerce_tool_profile 静默降级成 standard，反而把标准工具面
    给后台巩固 subagent，破坏只读/人审安全边界（Codex review 抓出 readonly 这个 bug）。
    """

    def test_consolidation_tool_profile_is_coercion_stable_and_most_restricted(self):
        """守卫：CONSOLIDATION_TOOL_PROFILE 经 _coerce_tool_profile 不被改写（即合法），
        且是最受限等级（level 0），不会泄漏更高权限工具面。
        """
        from octoagent.gateway.services.capability_pack import (
            _PROFILE_LEVELS,
            CapabilityPackService,
        )
        from octoagent.gateway.services.memory_consolidation import (
            CONSOLIDATION_TOOL_PROFILE,
        )

        # 合法（coerce 不改写）——若传 "readonly" 会被改写成 "standard"，此断言会红
        assert (
            CapabilityPackService._coerce_tool_profile(CONSOLIDATION_TOOL_PROFILE)
            == CONSOLIDATION_TOOL_PROFILE
        )
        # 最受限等级（minimal=0 < standard=1 < privileged=2）
        assert CONSOLIDATION_TOOL_PROFILE in _PROFILE_LEVELS
        assert _PROFILE_LEVELS[CONSOLIDATION_TOOL_PROFILE] == min(
            _PROFILE_LEVELS.values()
        )


class TestFinding1NamespaceInjection:
    """finding-1：cron 后台合成 spawn 无执行上下文，须显式注入主 Agent MAIN runtime
    身份，让巩固 subagent 经 α 共享读到主 Agent AGENT_PRIVATE 记忆（要合并的目标）。

    本层验证 service 编排：解析 MAIN runtime → 经 extra_control_metadata 注入
    synthetic_caller_agent_runtime_id。capability_pack 注入回退 + task_runner namespace
    查询的端到端证明在 services/test_capability_pack_phase_d.py（test_f127_synthetic_*）。
    """

    async def _make_main_runtime(self, store_group: StoreGroup):
        from octoagent.core.models.agent_context import (
            AgentRuntime,
            AgentRuntimeRole,
        )

        rt = AgentRuntime(
            agent_runtime_id="main-runtime-f127",
            project_id="proj-main-f127",
            role=AgentRuntimeRole.MAIN,
        )
        await store_group.agent_context_store.save_agent_runtime(rt)
        await store_group.conn.commit()
        return rt

    async def test_main_runtime_injected_into_spawn(self, store_group):
        """active MAIN runtime 存在 → spawn_child 收到 synthetic_caller_agent_runtime_id。"""
        await self._make_main_runtime(store_group)
        plane = _FakePlane()
        svc = _build_service(
            store_group,
            user_md=_USER_MD_ACTIVE,
            plane=plane,
            agent_context_store=store_group.agent_context_store,
        )
        await svc._run_consolidation()
        assert len(plane.calls) == 1
        extra = plane.calls[0].get("extra_control_metadata")
        assert extra is not None, "应注入 extra_control_metadata"
        assert extra["synthetic_caller_agent_runtime_id"] == "main-runtime-f127"
        assert extra["synthetic_caller_project_id"] == "proj-main-f127"

    async def test_no_main_runtime_degrades_no_injection(self, store_group):
        """无 MAIN runtime（全新实例）→ 不注入（降级），spawn 仍进行（subagent 拿不到记忆但不崩）。"""
        plane = _FakePlane()
        svc = _build_service(
            store_group,
            user_md=_USER_MD_ACTIVE,
            plane=plane,
            agent_context_store=store_group.agent_context_store,
        )
        await svc._run_consolidation()
        assert len(plane.calls) == 1
        # 无 runtime → extra_control_metadata 为 None（spawn_child 默认）
        assert plane.calls[0].get("extra_control_metadata") is None

    async def test_no_agent_context_store_degrades_gracefully(self, store_group):
        """agent_context_store 未注入（None）→ _resolve_main_agent_runtime 返回空，不崩。"""
        plane = _FakePlane()
        svc = _build_service(
            store_group,
            user_md=_USER_MD_ACTIVE,
            plane=plane,
            agent_context_store=None,
        )
        await svc._run_consolidation()
        assert len(plane.calls) == 1
        assert plane.calls[0].get("extra_control_metadata") is None


class TestSystemWorkExclusion:
    """root Work 是系统占位，不应泄漏到用户可见委派视图（control_plane delegation）。"""

    def test_root_work_id_in_control_plane_exclusion_set(self):
        """漂移守卫：control_plane _base.SYSTEM_INTERNAL_WORK_IDS 必须含巩固 root work_id。

        两处用字面量（_base 避免拉 apscheduler 进 control_plane import 图），此断言保证
        它们不漂移（改一处忘改另一处会红）。控制台委派/Worker 视图都从此集合排除系统占位 Work。
        """
        from octoagent.gateway.services.control_plane._base import (
            SYSTEM_INTERNAL_WORK_IDS,
        )

        assert CONSOLIDATION_ROOT_WORK_ID in SYSTEM_INTERNAL_WORK_IDS


class TestFinding3DescendantExclusion:
    """finding-3：巩固 subagent 的 **child** Work（parent_work_id=巩固 root）也是系统内部
    执行步进，必须连同 root 的全部后代一并从用户可见委派/Worker 视图排除。

    根因：a40e205d 只过滤 root 本身（SYSTEM_INTERNAL_WORK_IDS 字面比较），但
    spawn_child 建的 child Work parent_work_id=巩固 root → 这些 child Work 会以
    "孤儿内部任务"泄漏（既非用户委派，又让普通用户看到后台巩固步进，违 H1 + UI 普通
    用户原则）。expand_internal_work_ids 用 BFS 把后代一并纳入排除集。
    """

    def _make_work(
        self, work_id: str, parent_work_id: str | None = None
    ):
        from octoagent.core.models.delegation import (
            DelegationTargetKind,
            Work,
            WorkStatus,
        )

        return Work(
            work_id=work_id,
            task_id=f"task-{work_id}",
            parent_work_id=parent_work_id,
            title=f"work {work_id}",
            status=WorkStatus.CREATED,
            target_kind=DelegationTargetKind.SUBAGENT,
        )

    def test_expand_includes_multi_level_descendants(self):
        """BFS：root → child → grandchild 全部纳入排除集；无关用户 Work 不受影响。"""
        from octoagent.gateway.services.control_plane._base import (
            expand_internal_work_ids,
        )

        works = [
            self._make_work(CONSOLIDATION_ROOT_WORK_ID),  # 系统 root
            self._make_work("cons-child-1", parent_work_id=CONSOLIDATION_ROOT_WORK_ID),
            self._make_work("cons-grandchild-1", parent_work_id="cons-child-1"),
            # 无关的用户委派树（不应被排除）
            self._make_work("user-root"),
            self._make_work("user-child", parent_work_id="user-root"),
        ]
        excluded = expand_internal_work_ids(works)
        assert CONSOLIDATION_ROOT_WORK_ID in excluded
        assert "cons-child-1" in excluded
        assert "cons-grandchild-1" in excluded
        # 用户委派树不能被误排
        assert "user-root" not in excluded
        assert "user-child" not in excluded

    def test_expand_handles_empty_and_root_only(self):
        """边界：空 works → 仅 root 字面集；只有 root 无 child → 仅 root。"""
        from octoagent.gateway.services.control_plane._base import (
            SYSTEM_INTERNAL_WORK_IDS,
            expand_internal_work_ids,
        )

        assert expand_internal_work_ids([]) == set(SYSTEM_INTERNAL_WORK_IDS)
        assert expand_internal_work_ids(
            [self._make_work(CONSOLIDATION_ROOT_WORK_ID)]
        ) == set(SYSTEM_INTERNAL_WORK_IDS)

    async def test_delegation_document_excludes_consolidation_child(
        self, store_group
    ):
        """集成：get_delegation_document 不暴露巩固 root 的 child Work（finding-3）。

        构造 [巩固 root, 巩固 child, 用户委派 Work] 三条，验证返回 document 的 works
        只含用户委派 Work——root + child 都被排除（不是仅排除 root）。
        """
        from pathlib import Path

        from octoagent.gateway.services.control_plane._base import (
            ControlPlaneContext,
        )
        from octoagent.gateway.services.control_plane.work_service import (
            WorkDomainService,
        )

        root_work = self._make_work(CONSOLIDATION_ROOT_WORK_ID)
        child_work = self._make_work(
            "cons-child-leak", parent_work_id=CONSOLIDATION_ROOT_WORK_ID
        )
        user_work = self._make_work("user-delegation-1")

        class _FakeDelegationPlane:
            async def list_works(self, *, task_id: str | None = None):
                return [root_work, child_work, user_work]

        ctx = ControlPlaneContext(
            project_root=Path("/tmp/f127-finding3"),
            store_group=store_group,
            delegation_plane_service=_FakeDelegationPlane(),
        )
        svc = WorkDomainService(ctx)
        doc = await svc.get_delegation_document()
        visible_ids = {item.work_id for item in doc.works}
        assert visible_ids == {"user-delegation-1"}
        # 巩固 root + child 都不在用户可见视图
        assert CONSOLIDATION_ROOT_WORK_ID not in visible_ids
        assert "cons-child-leak" not in visible_ids


class TestSystemTaskExclusion:
    """Codex 复审 finding-B：系统内部占位 Task（channel=="system"）不应泄漏进用户可见
    任务列表（/api/tasks）与 daily routine 统计。

    根因：a40e205d/finding-3 只过滤了 Work 视图；但 _ensure_consolidation_root 还建了
    系统 root Task（spawn_child 派的 child Task 继承 channel="system"），而 /api/tasks
    (TaskService.list_tasks) 与 daily routine (list_tasks_in_time_range) 都直读 tasks 表
    不过滤——用户会在任务列表看到"F127 记忆巩固根任务占位"等系统条目（违 H1 + UI 普通
    用户原则）。修复：store 两 accessor 加 exclude_internal（默认 False 保持忠实），
    用户可见消费方显式开启，按 channel="system" 过滤同时覆盖 root + 全部 child。
    """

    async def _create_task(
        self, store_group, task_id: str, channel: str, *, created_at=None, status=None
    ):
        from datetime import UTC, datetime

        from octoagent.core.models.enums import TaskStatus
        from octoagent.core.models.task import RequesterInfo
        from octoagent.core.models.task import Task as TaskModel

        now = created_at or datetime.now(UTC)
        task = TaskModel(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=status or TaskStatus.SUCCEEDED,
            title=f"task {task_id}",
            requester=RequesterInfo(channel=channel, sender_id="x"),
        )
        await store_group.task_store.create_task(task)
        await store_group.conn.commit()
        return task

    async def test_list_tasks_exclude_internal_filters_system_channel(
        self, store_group
    ):
        """store list_tasks(exclude_internal=True) 排除 channel=="system"；默认忠实保留。"""
        await self._create_task(store_group, "user-task-1", "telegram")
        await self._create_task(store_group, CONSOLIDATION_ROOT_TASK_ID, "system")
        await self._create_task(store_group, "cons-child-task", "system")

        # 默认忠实 accessor：全返回（含 system）
        faithful = await store_group.task_store.list_tasks()
        faithful_ids = {t.task_id for t in faithful}
        assert CONSOLIDATION_ROOT_TASK_ID in faithful_ids
        assert "cons-child-task" in faithful_ids

        # exclude_internal：只剩用户 task
        filtered = await store_group.task_store.list_tasks(exclude_internal=True)
        filtered_ids = {t.task_id for t in filtered}
        assert filtered_ids == {"user-task-1"}

    async def test_list_tasks_in_time_range_exclude_internal(self, store_group):
        """list_tasks_in_time_range(exclude_internal=True) 排除 system；默认保留。"""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        win_start = now - timedelta(hours=1)
        win_end = now + timedelta(hours=1)
        mid = now  # 落在窗内
        await self._create_task(
            store_group, "user-in-window", "web", created_at=mid
        )
        await self._create_task(
            store_group, CONSOLIDATION_ROOT_TASK_ID, "system", created_at=mid
        )

        faithful = await store_group.task_store.list_tasks_in_time_range(
            win_start, win_end
        )
        assert CONSOLIDATION_ROOT_TASK_ID in {t.task_id for t in faithful}

        filtered = await store_group.task_store.list_tasks_in_time_range(
            win_start, win_end, exclude_internal=True
        )
        assert {t.task_id for t in filtered} == {"user-in-window"}

    async def test_task_service_list_tasks_excludes_consolidation_root(
        self, store_group
    ):
        """集成：TaskService.list_tasks（/api/tasks 后端）不返回巩固 root Task。"""
        from octoagent.gateway.services.task_service import TaskService

        # ensure 巩固 root（channel="system"）+ 一条用户 task
        svc = _build_service(store_group, user_md=_USER_MD_ACTIVE)
        await svc._ensure_consolidation_root()
        await self._create_task(store_group, "real-user-task", "telegram")

        task_service = TaskService(store_group)
        tasks = await task_service.list_tasks()
        ids = {t.task_id for t in tasks}
        assert "real-user-task" in ids
        assert CONSOLIDATION_ROOT_TASK_ID not in ids

    async def test_list_tasks_by_statuses_exclude_internal(self, store_group):
        """finding-C：list_tasks_by_statuses(exclude_internal=True) 排除 system——
        watchdog/operator inbox/task journal 走此 accessor，失败/卡住的后台巩固 child 不该
        产生用户可见 drift/retry 告警（H1）。默认忠实保留。
        """
        from octoagent.core.models.enums import TaskStatus

        # 用户 FAILED task + 巩固 child FAILED task（channel="system"）
        await self._create_task(
            store_group, "user-failed", "telegram", status=TaskStatus.FAILED
        )
        await self._create_task(
            store_group, "cons-child-failed", "system", status=TaskStatus.FAILED
        )

        # 默认忠实：两条都在
        faithful = await store_group.task_store.list_tasks_by_statuses(
            [TaskStatus.FAILED]
        )
        assert {"user-failed", "cons-child-failed"} <= {t.task_id for t in faithful}

        # exclude_internal：只剩用户 FAILED
        filtered = await store_group.task_store.list_tasks_by_statuses(
            [TaskStatus.FAILED], exclude_internal=True
        )
        assert {t.task_id for t in filtered} == {"user-failed"}
