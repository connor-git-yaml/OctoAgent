"""F127 Phase E — 巩固完成"待确认"通知单测（FR-E1~E4 / AC-7）。

覆盖 `[@test]` 绑定（spec §FR-E）：
- FR-E1：proposals>0 → 发**一条** MEDIUM `MEMORY_CONSOLIDATION_PENDING_REVIEW` 通知
  （payload summary="整理了 K 条记忆，N 条合并建议待确认" + 计数 + run_id）
- FR-E2：无提议（0 提议 / 空运行 fallback）→ **不发**（无噪声）
- FR-E3：channels 读 USER.md summary_channels（复用 F102 字段）；quiet hours 由
  NotificationService 自身处理（MEDIUM quiet 内 discard + 审计，真服务集成验证）
- FR-E4：notification_id 幂等（state_transition_event_id=run_id → 同 run 重放不双发）

**与 finding-E（codex round4）的调和验证**：finding-E 压掉的是 channel=="system" 后台
Task 的**通用**完成/失败/状态推送（TaskRunner._notify_completion / audit_worker_error /
orchestrator._notify_state_change）——Phase E 是**专用直调** notify_task_state_change
（同 F102 daily routine 范式，finding-E commit 显式注明不受抑制）。本文件验证：
①失败路径（FAILED）不发通知（静默，事件已审计）；②skip 路径不发；③通用抑制路径
不因 Phase E 恢复（TestSystemTaskNotificationSuppression 在 trigger 测试守着，此处
不重复）。

**H1**：通知是系统级（NotificationService 渠道推送），非 Agent 对话——无 session、
无对话通道。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest_asyncio
from octoagent.core.models.enums import EventType
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.consolidation_discovery import DiscoveryOutcome
from octoagent.gateway.services.delegation_plane import SpawnChildResult
from octoagent.gateway.services.memory_consolidation import (
    CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE,
    CONSOLIDATION_ROOT_TASK_ID,
    MemoryConsolidationService,
)
from octoagent.gateway.services.notification import (
    NotificationPriority,
    NotificationService,
)

# ============================================================
# Fakes（与 trigger 测试同范式，各文件自足）
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
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def spawn_child(self, **kwargs: Any) -> SpawnChildResult:
        self.calls.append(kwargs)
        return SpawnChildResult(status="written", task_id="child-task-notify")


class _RecordingRunner:
    """返回可配置 DiscoveryOutcome（或抛异常）的发现端 runner。"""

    def __init__(
        self, *, outcome: DiscoveryOutcome | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcome = outcome or DiscoveryOutcome(
            facts_reviewed=5, proposals_made=2, candidate_ids=["c-1", "c-2"]
        )
        self._raise_exc = raise_exc

    async def __call__(self, **kwargs: Any) -> DiscoveryOutcome:
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._outcome


class _CapturingNotificationService:
    """捕获 notify_task_state_change kwargs 的 fake（可配置抛异常）。"""

    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise_exc = raise_exc

    async def notify_task_state_change(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc


class _FakeChannel:
    """真 NotificationService 集成用 channel stub。"""

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


_USER_MD_ACTIVE = """# 用户档案

- **consolidation_active**: true
- **consolidation_time**: "03:30"
- **consolidation_window_days**: 14
- **consolidation_max_facts**: 80
"""

_USER_MD_ACTIVE_TELEGRAM_ONLY = _USER_MD_ACTIVE + """
- **summary_channels**: "telegram"
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


async def _seed_main_runtime_with_namespace(
    store_group: StoreGroup,
    *,
    runtime_id: str = "main-rt-notify",
    project_id: str = "proj-main-notify",
    scope_id: str = "agent-private/main",
) -> None:
    """种主 Agent MAIN runtime + AGENT_PRIVATE namespace（发现端 scope 解析前置）。"""
    from octoagent.core.models.agent_context import (
        AgentRuntime,
        AgentRuntimeRole,
        MemoryNamespace,
        MemoryNamespaceKind,
    )

    await store_group.agent_context_store.save_agent_runtime(
        AgentRuntime(
            agent_runtime_id=runtime_id,
            project_id=project_id,
            role=AgentRuntimeRole.MAIN,
        )
    )
    await store_group.agent_context_store.save_memory_namespace(
        MemoryNamespace(
            namespace_id="ns-main-notify",
            project_id=project_id,
            agent_runtime_id=runtime_id,
            kind=MemoryNamespaceKind.AGENT_PRIVATE,
            memory_scope_ids=[scope_id],
        )
    )
    await store_group.conn.commit()


#: F146 件①：默认构造用「盘上无 USER.md」哨兵 root——用例走 live state 路径不变
_NO_DISK_ROOT = Path("/nonexistent/f146-no-disk")


def _build_service(
    store_group: StoreGroup,
    *,
    user_md: str = _USER_MD_ACTIVE,
    runner: Any = None,
    notification_service: Any = None,
) -> MemoryConsolidationService:
    return MemoryConsolidationService(
        scheduler=_FakeScheduler(),
        task_store=store_group.task_store,
        work_store=store_group.work_store,
        event_store=store_group.event_store,
        snapshot_store=_FakeSnapshotStore(user_md=user_md),
        delegation_plane=_FakePlane(),  # type: ignore[arg-type]
        project_root=_NO_DISK_ROOT,
        agent_context_store=store_group.agent_context_store,
        discovery_runner=runner,
        notification_service=notification_service,
    )


async def _events_of_type(
    store_group: StoreGroup, event_type: EventType
) -> list[Any]:
    events = await store_group.event_store.get_events_for_task(
        CONSOLIDATION_ROOT_TASK_ID
    )
    return [e for e in events if e.type == event_type]


# ============================================================
# FR-E1：有提议 → 一条 MEDIUM 通知
# ============================================================


class TestPendingReviewNotification:
    async def test_proposals_positive_sends_one_medium_notification(
        self, store_group
    ):
        """FR-E1：proposals>0 → 恰好一条通知，MEDIUM + 专用 event_type + 计数 payload。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif = _CapturingNotificationService()
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif
        )
        await svc._run_consolidation()

        assert len(notif.calls) == 1, "有提议应发且仅发一条通知"
        call = notif.calls[0]
        assert call["task_id"] == CONSOLIDATION_ROOT_TASK_ID
        assert call["event_type"] == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE
        assert call["priority"] == NotificationPriority.MEDIUM
        # payload：summary 文案 + 计数 + run_id（无敏感原文——合并内容不进 payload）
        payload = call["payload"]
        assert payload["facts_reviewed"] == 5
        assert payload["proposals_made"] == 2
        assert "5 条近期事实" in payload["summary"]
        assert "2 条合并建议待确认" in payload["summary"]
        assert payload["run_id"].startswith("mcons-")
        assert "merged_content" not in payload  # PII 防护：不含合并内容原文
        # FR-E4 幂等锚点：state_transition_event_id = run_id
        assert call["state_transition_event_id"] == payload["run_id"]
        # FR-E3：channels 默认全渠道（USER.md 未配 summary_channels）
        assert call["channels"] == frozenset({"telegram", "web_sse"})
        # Codex P2：session_id=""（全局通知桶）——None 会让 _record_active 跳过，
        # GET /api/notifications 查不到（Web-only 用户服务端死角）
        assert call["session_id"] == ""

    async def test_channels_read_from_user_md_summary_channels(self, store_group):
        """FR-E3：USER.md summary_channels: "telegram" → 通知只发 telegram。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif = _CapturingNotificationService()
        svc = _build_service(
            store_group,
            user_md=_USER_MD_ACTIVE_TELEGRAM_ONLY,
            runner=_RecordingRunner(),
            notification_service=notif,
        )
        await svc._run_consolidation()
        assert len(notif.calls) == 1
        assert notif.calls[0]["channels"] == frozenset({"telegram"})

    async def test_notification_sent_after_completed_event_persisted(
        self, store_group
    ):
        """审计先行：通知发出时 COMPLETED 事件必须已在 event_store（真查库，非 mock 自证）。"""
        await _seed_main_runtime_with_namespace(store_group)
        completed_at_notify_time: list[int] = []

        class _OrderProbeNotif:
            async def notify_task_state_change(self, **kwargs: Any) -> None:
                completed = await _events_of_type(
                    store_group, EventType.MEMORY_CONSOLIDATION_COMPLETED
                )
                completed_at_notify_time.append(len(completed))

        svc = _build_service(
            store_group,
            runner=_RecordingRunner(),
            notification_service=_OrderProbeNotif(),
        )
        await svc._run_consolidation()
        assert completed_at_notify_time == [1], (
            "notify 调用时 COMPLETED 已落盘（审计事件先于 best-effort 通知）"
        )


# ============================================================
# FR-E2 + finding-E 调和：不发通知的所有分支
# ============================================================


class TestNoNotificationBranches:
    async def test_zero_proposals_no_notification(self, store_group):
        """FR-E2：0 提议（事实已干净）→ COMPLETED 照写但不发通知（无噪声）。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif = _CapturingNotificationService()
        runner = _RecordingRunner(
            outcome=DiscoveryOutcome(facts_reviewed=7, proposals_made=0)
        )
        svc = _build_service(store_group, runner=runner, notification_service=notif)
        await svc._run_consolidation()

        completed = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_COMPLETED
        )
        assert len(completed) == 1  # 审计事件不受影响
        assert notif.calls == [], "0 提议不该发通知"

    async def test_empty_run_no_scope_no_notification(self, store_group):
        """FR-E2：无 scope 空运行（COMPLETED fallback proposals=0）→ 不发。"""
        # 不 seed namespace → scope 解析为空 → 空运行
        notif = _CapturingNotificationService()
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif
        )
        await svc._run_consolidation()
        completed = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_COMPLETED
        )
        assert len(completed) == 1
        assert completed[0].payload["fallback"] is True
        assert notif.calls == []

    async def test_discovery_failure_silent_no_notification(self, store_group):
        """finding-E 对齐：发现端失败 → FAILED 事件（审计）但**不**推用户通知（静默降级）。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif = _CapturingNotificationService()
        runner = _RecordingRunner(raise_exc=RuntimeError("discovery boom"))
        svc = _build_service(store_group, runner=runner, notification_service=notif)
        await svc._run_consolidation()

        failed = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_FAILED
        )
        assert len(failed) == 1  # 事件审计不可少（C2）
        assert notif.calls == [], "巩固失败不该打扰用户（finding-E 后台静默边界）"

    async def test_disabled_skip_no_notification(self, store_group):
        """skip 路径（disabled）→ SKIPPED 事件但不发通知。"""
        notif = _CapturingNotificationService()
        svc = _build_service(
            store_group,
            user_md=_USER_MD_DISABLED,
            runner=_RecordingRunner(),
            notification_service=notif,
        )
        await svc._run_consolidation()
        skipped = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_SKIPPED
        )
        assert len(skipped) == 1
        assert notif.calls == []

    async def test_no_notification_service_graceful(self, store_group):
        """C6：notification_service=None（未注入）→ 有提议也静默跳过，不崩。"""
        await _seed_main_runtime_with_namespace(store_group)
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=None
        )
        await svc._run_consolidation()  # 不应抛
        completed = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_COMPLETED
        )
        assert len(completed) == 1

    async def test_notify_exception_does_not_fail_run(self, store_group):
        """C6：通知抛异常 → 巩固运行不受影响（COMPLETED 已落盘 + _running 复位）。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif = _CapturingNotificationService(raise_exc=RuntimeError("channel down"))
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif
        )
        await svc._run_consolidation()  # 不应抛
        assert len(notif.calls) == 1  # 通知确实尝试过
        completed = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_COMPLETED
        )
        assert len(completed) == 1
        assert svc._running is False
        # FAILED 不该因通知失败而出现（通知失败 ≠ 巩固失败）
        failed = await _events_of_type(
            store_group, EventType.MEMORY_CONSOLIDATION_FAILED
        )
        assert failed == []


# ============================================================
# FR-E3/E4 真 NotificationService 集成（quiet hours + 幂等 + 审计链）
# ============================================================


class TestRealNotificationServiceIntegration:
    """用**真** NotificationService（fake 只到 channel 层）验证 quiet hours discard、
    sha256 幂等、NOTIFICATION_DISPATCHED 审计——都真查 event_store，非 mock 自证。
    """

    def _build_real_notif(
        self, store_group: StoreGroup, *, user_md: str = ""
    ) -> tuple[NotificationService, _FakeChannel, _FakeChannel]:
        svc = NotificationService(
            snapshot_store=_FakeSnapshotStore(user_md=user_md),
            event_store=store_group.event_store,
        )
        tg = _FakeChannel("telegram")
        web = _FakeChannel("web_sse")
        svc.register_channel(tg)
        svc.register_channel(web)
        return svc, tg, web

    async def test_pushes_channels_and_writes_dispatched_audit(self, store_group):
        """有提议 → 真服务推 channel + NOTIFICATION_DISPATCHED(filtered=False) 审计落盘。"""
        await _seed_main_runtime_with_namespace(store_group)
        # USER.md 无 active_hours → 全时段推送
        notif_svc, tg, web = self._build_real_notif(store_group)
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif_svc
        )
        await svc._run_consolidation()

        # 两个渠道各收到一条（默认全渠道）
        assert len(tg.calls) == 1
        assert len(web.calls) == 1
        task_id, event_type, payload = tg.calls[0]
        assert task_id == CONSOLIDATION_ROOT_TASK_ID
        assert event_type == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE
        assert "notification_id" in payload  # dismiss 按钮锚点
        # NOTIFICATION_DISPATCHED 审计事件真在 event_store（H4 链）
        dispatched = await _events_of_type(
            store_group, EventType.NOTIFICATION_DISPATCHED
        )
        assert len(dispatched) == 1
        assert dispatched[0].payload["filtered"] is False
        assert (
            dispatched[0].payload["notification_type"]
            == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE
        )
        assert dispatched[0].payload["priority"] == NotificationPriority.MEDIUM.value

    async def test_quiet_hours_discards_but_audits(self, store_group):
        """FR-E3：quiet hours 内 MEDIUM 被 discard（channel 0 推送）但审计仍落盘
        （NOTIFICATION_DISPATCHED filtered=True）——由 NotificationService 自身处理，
        巩固服务不重复实现时段判断。
        """
        await _seed_main_runtime_with_namespace(store_group)
        # 构造"现在必在 quiet"的 active_hours：active 窗 = [now+2h, now+3h)
        now = datetime.now(UTC)
        start = (now + timedelta(hours=2)).strftime("%H:%M")
        end = (now + timedelta(hours=3)).strftime("%H:%M")
        user_md_quiet = f'- **active_hours**: "{start}-{end}"\n'
        notif_svc, tg, web = self._build_real_notif(
            store_group, user_md=user_md_quiet
        )
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif_svc
        )
        await svc._run_consolidation()

        # channel 0 推送（quiet 内 MEDIUM discard）
        assert tg.calls == []
        assert web.calls == []
        # 但审计链保留（H4 discard 审计）
        dispatched = await _events_of_type(
            store_group, EventType.NOTIFICATION_DISPATCHED
        )
        assert len(dispatched) == 1
        assert dispatched[0].payload["filtered"] is True

    async def test_same_run_replay_dedup_by_notification_id(self, store_group):
        """FR-E4：同 run_id 重放（e.g. crash 重试）→ sha256 notification_id 去重，只推一次。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif_svc, tg, _web = self._build_real_notif(store_group)
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif_svc
        )
        # 直接调 Phase E 通知方法两次（同 run_id 模拟重放）
        config = svc._read_config()
        await svc._ensure_consolidation_root()
        for _ in range(2):
            await svc._notify_pending_review(
                run_id="mcons-replay-same",
                facts_reviewed=5,
                proposals_made=2,
                config=config,
            )
        assert len(tg.calls) == 1, "同 run_id 重放应被 notification_id 去重"

    async def test_different_runs_notify_separately(self, store_group):
        """对照：不同 run_id → 各发各的（每晚一条互不吞）。"""
        await _seed_main_runtime_with_namespace(store_group)
        notif_svc, tg, _web = self._build_real_notif(store_group)
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif_svc
        )
        config = svc._read_config()
        await svc._ensure_consolidation_root()
        for run_id in ("mcons-night-1", "mcons-night-2"):
            await svc._notify_pending_review(
                run_id=run_id, facts_reviewed=3, proposals_made=1, config=config
            )
        assert len(tg.calls) == 2

    async def test_visible_in_global_web_inbox(self, store_group):
        """Codex P2 修复验证：通知落 session_id="" 全局桶 → list_active("")
        （即 GET /api/notifications 默认查询）真能看到；Web-only 用户不再是服务端死角。
        """
        await _seed_main_runtime_with_namespace(store_group)
        notif_svc, _tg, _web = self._build_real_notif(store_group)
        svc = _build_service(
            store_group, runner=_RecordingRunner(), notification_service=notif_svc
        )
        await svc._run_consolidation()

        inbox = notif_svc.list_active("")  # notifications 路由默认 session_id=""
        assert len(inbox) == 1
        entry = inbox[0]
        assert entry["notification_type"] == CONSOLIDATION_PENDING_REVIEW_EVENT_TYPE
        assert entry["task_id"] == CONSOLIDATION_ROOT_TASK_ID
        assert entry["payload"]["proposals_made"] == 2
