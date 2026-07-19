"""F102 Phase C — DailyRoutineService 主体单测/集成测试。

覆盖 AC-B1 / AC-B2 / AC-B5 / AC-B6 / AC-B7 / AC-E1 / AC-E3 / AC-E4 / AC-F1：
- routine 触发完整 9 步流程（含 ROUTINE_TRIGGERED → COMPLETED → NOTIFICATION_DISPATCHED）
- routine_active=False 跳过路径（写 ROUTINE_SKIPPED 不推送）
- 空数据不推送（SD-8：仍写 ROUTINE_COMPLETED(worker_count=0)）
- attention_count 算法（SD-7 校正：4 个 TaskStatus 值）
- attention_count > 0 时 priority 升 MEDIUM
- CancelledError 显式 re-raise（FR-B6 / Constitution C6 / M-1 broad-catch）
- LLM fallback 路径（mock provider_router）
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from octoagent.core.models import TaskStatus
from octoagent.core.models.enums import EventType
from octoagent.core.models.task import RequesterInfo, Task
from octoagent.core.store import StoreGroup, create_store_group
from octoagent.gateway.services.daily_routine import (
    ATTENTION_TASK_STATUSES,
    DAILY_ROUTINE_AUDIT_TASK_ID,
    DAILY_ROUTINE_JOB_ID,
    DailyRoutineService,
)
from octoagent.gateway.services.notification import (
    NotificationPriority,
    NotificationService,
)


# ============================================================
# Fixtures
# ============================================================


class _FakeChannel:
    def __init__(self, name: str) -> None:
        self._name = name
        self.calls: list[tuple[str, str, dict]] = []

    @property
    def channel_name(self) -> str:
        return self._name

    async def notify(self, task_id: str, event_type: str, payload: dict) -> None:
        self.calls.append((task_id, event_type, payload))

    async def dismiss(self, notification_id: str) -> None:
        return None


class _FakeSnapshotStore:
    def __init__(self, user_md: str | None = None) -> None:
        self._user_md = user_md or ""

    def get_live_state(self, key: str) -> str | None:
        if key == "USER.md":
            return self._user_md
        return None


class _FakeScheduler:
    """Fake AutomationSchedulerService — 仅暴露 _scheduler.add_job / remove_job。"""

    def __init__(self) -> None:
        self._scheduler = MagicMock()
        self._scheduler.add_job = MagicMock()
        self._scheduler.remove_job = MagicMock()


@pytest_asyncio.fixture
async def store_group(tmp_path: Path) -> StoreGroup:
    db_path = str(tmp_path / "test.db")
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    return await create_store_group(db_path, str(artifacts_dir))


@pytest.fixture
def notification_service() -> NotificationService:
    svc = NotificationService(
        snapshot_store=_FakeSnapshotStore(),
        event_store=None,  # 单独用 store_group.event_store
    )
    svc.register_channel(_FakeChannel("telegram"))
    svc.register_channel(_FakeChannel("web_sse"))
    return svc


#: F146 件①：默认构造用「盘上无 USER.md」哨兵 root（纯路径拼接不做 I/O）——
#: 既有用例继续走 live state 路径（语义 = 盘缺失兜底），盘优先用例显式传 tmp_path root。
_NO_DISK_ROOT = Path("/nonexistent/f146-no-disk")


def _build_service(
    store_group: StoreGroup,
    notification_service: NotificationService,
    user_md: str = "",
    llm_return: str | Exception | None = None,
    project_root: Path | None = None,
) -> DailyRoutineService:
    """构造 DailyRoutineService 实例 + mock provider_router。"""
    notification_service._event_store = store_group.event_store

    provider_router = MagicMock()
    if isinstance(llm_return, Exception):
        provider_router.complete = AsyncMock(side_effect=llm_return)
    else:
        provider_router.complete = AsyncMock(
            return_value=llm_return if llm_return is not None else ""
        )

    snapshot_store = _FakeSnapshotStore(user_md=user_md)
    notification_service._snapshot_store = snapshot_store

    return DailyRoutineService(
        scheduler=_FakeScheduler(),
        task_store=store_group.task_store,
        event_store=store_group.event_store,
        notification_service=notification_service,
        snapshot_store=snapshot_store,
        provider_router=provider_router,
        project_root=project_root or _NO_DISK_ROOT,
    )


async def _create_task(
    store_group: StoreGroup,
    task_id: str,
    created_at: datetime,
    status: TaskStatus = TaskStatus.SUCCEEDED,
    title: str = "Test task",
) -> None:
    task = Task(
        task_id=task_id,
        created_at=created_at,
        updated_at=created_at,
        status=status,
        title=title,
        requester=RequesterInfo(channel="test", sender_id="user-1"),
    )
    await store_group.task_store.create_task(task)


# ============================================================
# AC-B6 cron 注册 + audit task 占位
# ============================================================


class TestStartupShutdown:
    @pytest.mark.asyncio
    async def test_startup_registers_cron_and_audit_task(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-B6：startup 注册 cron job + ensure audit task 占位。"""
        svc = _build_service(store_group, notification_service)
        await svc.startup()

        # cron 注册被调用
        svc._scheduler._scheduler.add_job.assert_called_once()
        call = svc._scheduler._scheduler.add_job.call_args
        assert call.kwargs["id"] == DAILY_ROUTINE_JOB_ID
        assert call.kwargs["replace_existing"] is True
        assert call.kwargs["misfire_grace_time"] == 30

        # audit task 占位已创建
        audit = await store_group.task_store.get_task(DAILY_ROUTINE_AUDIT_TASK_ID)
        assert audit is not None
        assert audit.title.startswith("F102")

    @pytest.mark.asyncio
    async def test_shutdown_removes_cron(
        self, store_group: StoreGroup, notification_service: NotificationService
    ) -> None:
        svc = _build_service(store_group, notification_service)
        await svc.startup()
        await svc.shutdown()
        svc._scheduler._scheduler.remove_job.assert_called_once_with(
            DAILY_ROUTINE_JOB_ID
        )

    @pytest.mark.asyncio
    async def test_startup_idempotent(
        self, store_group: StoreGroup, notification_service: NotificationService
    ) -> None:
        svc = _build_service(store_group, notification_service)
        await svc.startup()
        await svc.startup()
        assert svc._scheduler._scheduler.add_job.call_count == 1


# ============================================================
# AC-B2 routine_active=False skipped
# ============================================================


class TestRoutineDisabled:
    @pytest.mark.asyncio
    async def test_routine_active_false_writes_skipped_no_push(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-B2：routine_active=False → 写 ROUTINE_SKIPPED 不推送通知。"""
        user_md = '- **routine_active**: "false"'
        svc = _build_service(store_group, notification_service, user_md=user_md)
        await svc.startup()

        await svc._run_daily_summary()

        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        event_types = [e.type for e in events]
        assert EventType.ROUTINE_TRIGGERED in event_types
        assert EventType.ROUTINE_SKIPPED in event_types
        assert EventType.ROUTINE_COMPLETED not in event_types

        # 不推送 channel
        telegram_ch, web_ch = notification_service._channels
        assert telegram_ch.calls == []
        assert web_ch.calls == []


# ============================================================
# AC-B5 / SD-8 空数据不推送
# ============================================================


class TestEmptyData:
    @pytest.mark.asyncio
    async def test_empty_yesterday_writes_completed_no_push(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-B5 + SD-8：昨日无 task → 写 ROUTINE_COMPLETED(worker_count=0) 不推送。"""
        svc = _build_service(store_group, notification_service)
        await svc.startup()
        await svc._run_daily_summary()

        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        completed_events = [
            e for e in events if e.type == EventType.ROUTINE_COMPLETED
        ]
        assert len(completed_events) == 1
        payload = completed_events[0].payload
        assert payload["worker_count"] == 0
        assert payload["failed_count"] == 0
        assert payload["attention_count"] == 0
        assert payload["fallback"] is False
        assert payload["summary_length"] == 0

        # 不推送
        telegram_ch, web_ch = notification_service._channels
        assert telegram_ch.calls == []
        assert web_ch.calls == []


# ============================================================
# AC-E4 attention_count 算法（SD-7 校正：4 个状态）
# ============================================================


class TestAttentionCountAlgorithm:
    @pytest.mark.asyncio
    async def test_attention_count_excludes_succeeded(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-E4：5 个 task / 4 个 status / attention_count 应是 attention_statuses ∩ tasks 的数量。

        构造：
          - 1 × SUCCEEDED  (非 attention)
          - 1 × FAILED      (attention)
          - 1 × WAITING_INPUT (attention)
          - 1 × WAITING_APPROVAL (attention)
          - 1 × RUNNING     (非 attention)
        attention_count 应 = 3（FAILED + WAITING_INPUT + WAITING_APPROVAL）
        """
        # 创建昨日范围内的 5 个 task（按 UTC 计算，确保落入 _compute_yesterday_range_utc）
        now_utc = datetime.now(UTC)
        from datetime import timedelta
        yesterday_noon = now_utc - timedelta(days=1)
        yesterday_noon = yesterday_noon.replace(hour=12, minute=0, second=0, microsecond=0)

        await _create_task(store_group, "t-succ", yesterday_noon, TaskStatus.SUCCEEDED)
        await _create_task(store_group, "t-fail", yesterday_noon, TaskStatus.FAILED)
        await _create_task(store_group, "t-wi", yesterday_noon, TaskStatus.WAITING_INPUT)
        await _create_task(store_group, "t-wa", yesterday_noon, TaskStatus.WAITING_APPROVAL)
        await _create_task(store_group, "t-run", yesterday_noon, TaskStatus.RUNNING)
        await store_group.conn.commit()

        svc = _build_service(
            store_group, notification_service, llm_return="测试摘要内容。"
        )
        await svc.startup()
        await svc._run_daily_summary()

        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        completed = [e for e in events if e.type == EventType.ROUTINE_COMPLETED]
        assert len(completed) == 1
        payload = completed[0].payload
        assert payload["worker_count"] == 5
        assert payload["failed_count"] == 1
        assert payload["attention_count"] == 3  # FAILED + WAITING_INPUT + WAITING_APPROVAL

    def test_attention_statuses_set_definition(self) -> None:
        """SD-7 校正实证：attention_statuses 4 个 TaskStatus 值，无 'escalated'。"""
        assert ATTENTION_TASK_STATUSES == frozenset({
            TaskStatus.WAITING_INPUT,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.PAUSED,
            TaskStatus.FAILED,
        })


# ============================================================
# AC-B7 attention > 0 → priority MEDIUM
# ============================================================


class TestPriorityElevation:
    @pytest.mark.asyncio
    async def test_attention_count_triggers_medium_priority(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-B7：attention_count > 0 时 priority=MEDIUM；否则 LOW。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        await _create_task(store_group, "t-fail", yesterday_noon, TaskStatus.FAILED)
        await store_group.conn.commit()

        svc = _build_service(
            store_group, notification_service, llm_return="昨日 1 任务失败。"
        )
        await svc.startup()
        await svc._run_daily_summary()

        # 通知应已推送到 channel（attention > 0）
        telegram_ch, web_ch = notification_service._channels
        assert len(telegram_ch.calls) == 1
        # priority 通过 NOTIFICATION_DISPATCHED event 验证
        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        notif_events = [
            e for e in events if e.type == EventType.NOTIFICATION_DISPATCHED
        ]
        assert len(notif_events) >= 1
        assert notif_events[-1].payload["priority"] == NotificationPriority.MEDIUM.value


# ============================================================
# AC-E1 + AC-F1 完整事件链
# ============================================================


class TestEventChain:
    @pytest.mark.asyncio
    async def test_full_event_chain_triggered_completed_dispatched(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-E1 + AC-F1：完整流程 ROUTINE_TRIGGERED → ROUTINE_COMPLETED + NOTIFICATION_DISPATCHED。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        await _create_task(store_group, "t-1", yesterday_noon, TaskStatus.SUCCEEDED)
        await store_group.conn.commit()

        svc = _build_service(
            store_group, notification_service, llm_return="昨日完成 1 个任务。"
        )
        await svc.startup()
        await svc._run_daily_summary()

        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        event_types_ordered = [e.type for e in events]

        # 必须含 TRIGGERED + COMPLETED + DISPATCHED
        assert EventType.ROUTINE_TRIGGERED in event_types_ordered
        assert EventType.ROUTINE_COMPLETED in event_types_ordered
        assert EventType.NOTIFICATION_DISPATCHED in event_types_ordered

        # ROUTINE_COMPLETED.elapsed_ms 应 > 0
        completed = next(
            e for e in events if e.type == EventType.ROUTINE_COMPLETED
        )
        assert completed.payload["elapsed_ms"] >= 0
        assert completed.payload["worker_count"] == 1

        # NOTIFICATION_DISPATCHED 含 channels 字段（FR-B8）
        notif = next(
            e for e in events if e.type == EventType.NOTIFICATION_DISPATCHED
        )
        # 默认 summary_channels = ["telegram", "web_sse"]
        assert sorted(notif.payload.get("channels", [])) == ["telegram", "web_sse"]


# ============================================================
# AC-B3 / AC-E2 LLM fallback 路径
# ============================================================


class TestLLMFallback:
    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_deterministic_template(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-B3 + AC-E2：LLM 抛异常时 fallback；ROUTINE_COMPLETED.fallback=True。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        await _create_task(store_group, "t-1", yesterday_noon, TaskStatus.SUCCEEDED)
        await store_group.conn.commit()

        svc = _build_service(
            store_group,
            notification_service,
            llm_return=TimeoutError("network down"),
        )
        await svc.startup()
        await svc._run_daily_summary()

        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        completed = next(
            e for e in events if e.type == EventType.ROUTINE_COMPLETED
        )
        assert completed.payload["fallback"] is True
        assert completed.payload["llm_elapsed_ms"] is None
        # fallback 模板含 "昨日 Worker 摘要" 关键词
        telegram_ch, _ = notification_service._channels
        assert len(telegram_ch.calls) == 1
        pushed_payload = telegram_ch.calls[0][2]
        assert "昨日" in pushed_payload["summary"]

    @pytest.mark.asyncio
    async def test_llm_empty_response_falls_back(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """LLM 返回空字符串也走 fallback（Codex L5 边界场景）。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        await _create_task(store_group, "t-1", yesterday_noon, TaskStatus.SUCCEEDED)
        await store_group.conn.commit()

        svc = _build_service(store_group, notification_service, llm_return="   ")
        await svc.startup()
        await svc._run_daily_summary()

        events = await store_group.event_store.get_events_for_task(
            DAILY_ROUTINE_AUDIT_TASK_ID
        )
        completed = next(
            e for e in events if e.type == EventType.ROUTINE_COMPLETED
        )
        assert completed.payload["fallback"] is True


# ============================================================
# AC-E3 CancelledError 显式 re-raise（Constitution C6）
# ============================================================


class TestLLMPromptTokenBudget:
    """Phase E：SD-9 LLM prompt 截断策略测试。"""

    @pytest.mark.asyncio
    async def test_prompt_includes_attention_section_when_tasks_exist(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """prompt 含 [待关注 / 失败任务] section 当有 attention task 时。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        await _create_task(
            store_group, "t-fail", yesterday_noon, TaskStatus.FAILED, title="重要任务失败"
        )
        await _create_task(
            store_group, "t-succ", yesterday_noon, TaskStatus.SUCCEEDED, title="日常任务"
        )
        await store_group.conn.commit()

        # 捕获 LLM 调用 prompt
        captured: dict[str, Any] = {}

        async def _capture(model_alias: str, messages: list, max_tokens: int) -> str:
            captured["prompt"] = messages[0]["content"]
            captured["max_tokens"] = max_tokens
            captured["model"] = model_alias
            return "测试摘要"

        svc = _build_service(store_group, notification_service)
        svc._provider_router.complete = _capture
        await svc.startup()
        await svc._run_daily_summary()

        prompt = captured["prompt"]
        assert "[待关注 / 失败任务]" in prompt
        assert "重要任务失败" in prompt
        assert "FAILED" in prompt
        assert "[完成任务（仅 title）]" in prompt
        assert "日常任务" in prompt
        assert captured["max_tokens"] == 512  # LLM_OUTPUT_TOKEN_BUDGET
        assert captured["model"] == "cheap"

    @pytest.mark.asyncio
    async def test_prompt_truncates_when_too_many_succeeded_tasks(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """SD-9：大量 task 时 prompt 截断，回退到 "... 以及 N 个其他完成任务" 概括。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        # 创建 200 个 succeeded task，title 50 chars each → 总 char ~ 200 * 50 = 10000，
        # 远超 LLM_INPUT_CHAR_BUDGET=2000
        long_title = "这是一个相当长的任务标题用来测试 token budget 截断策略是否正确生效啊"
        for i in range(200):
            await _create_task(
                store_group,
                f"t-{i:03d}",
                yesterday_noon,
                TaskStatus.SUCCEEDED,
                title=f"{long_title}-{i}",
            )
        await store_group.conn.commit()

        captured_prompt: dict[str, str] = {}

        async def _capture(model_alias: str, messages: list, max_tokens: int) -> str:
            captured_prompt["text"] = messages[0]["content"]
            return "截断测试摘要"

        svc = _build_service(store_group, notification_service)
        svc._provider_router.complete = _capture
        await svc.startup()
        await svc._run_daily_summary()

        prompt = captured_prompt["text"]
        # 截断标记应出现
        assert "以及" in prompt and "其他完成任务" in prompt
        # 总 prompt 长度应小于 budget + 头尾固定开销（~400 char）
        # 即使头尾 + body 截断后 prompt 仍 < 3000 char（充裕余量）
        assert len(prompt) < 3000

    @pytest.mark.asyncio
    async def test_prompt_attention_priority_when_budget_tight(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """SD-9：当 attention task 多 + budget 紧张时，attention 详情优先于 succeeded。"""
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        # 50 个 attention task（FAILED）每个 title 100 char ≈ 5000 char ≫ budget
        long_title = "一个相当长的失败任务标题用来测试 attention task 优先级是否生效啊不行还得再长一点"
        for i in range(50):
            await _create_task(
                store_group,
                f"t-fail-{i:02d}",
                yesterday_noon,
                TaskStatus.FAILED,
                title=f"{long_title}-{i}",
            )
        await store_group.conn.commit()

        captured_prompt: dict[str, str] = {}

        async def _capture(model_alias: str, messages: list, max_tokens: int) -> str:
            captured_prompt["text"] = messages[0]["content"]
            return "测试"

        svc = _build_service(store_group, notification_service)
        svc._provider_router.complete = _capture
        await svc.startup()
        await svc._run_daily_summary()

        prompt = captured_prompt["text"]
        # attention section 应至少含部分 task，截断标记出现
        assert "[待关注 / 失败任务]" in prompt
        assert "FAILED" in prompt
        assert "个待关注任务未列出" in prompt


class TestUserTimezoneResolver:
    """F115：_resolve_user_timezone 降级链 USER.md → env → UTC。

    spec NFR-3 / SD-10：用户本地时区影响 cron 触发时刻和"昨日"窗口边界。
    无参调用（user_md_tz=None）退化为原 env → UTC 行为（向后兼容）。
    """

    # --- 原 env → UTC 行为（无参调用，向后兼容）---

    def test_default_fallback_to_utc(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("OCTOAGENT_USER_TIMEZONE", raising=False)
        assert DailyRoutineService._resolve_user_timezone() == "UTC"

    def test_environment_variable_overrides(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("OCTOAGENT_USER_TIMEZONE", "Asia/Shanghai")
        assert DailyRoutineService._resolve_user_timezone() == "Asia/Shanghai"

    def test_invalid_timezone_falls_back_to_utc(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("OCTOAGENT_USER_TIMEZONE", "Mars/Olympus_Mons")
        assert DailyRoutineService._resolve_user_timezone() == "UTC"

    def test_empty_string_falls_back_to_utc(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("OCTOAGENT_USER_TIMEZONE", "   ")
        assert DailyRoutineService._resolve_user_timezone() == "UTC"

    # --- F115 新增：USER.md 优先级（AC-1 / AC-2 / AC-4）---

    def test_user_md_overrides_env(self, monkeypatch: Any) -> None:
        """AC-1：USER.md 有效时区优先于 env（USER.md is SoT）。"""
        monkeypatch.setenv("OCTOAGENT_USER_TIMEZONE", "Asia/Shanghai")
        assert (
            DailyRoutineService._resolve_user_timezone("America/New_York")
            == "America/New_York"
        )

    def test_user_md_overrides_env_even_when_env_absent(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.delenv("OCTOAGENT_USER_TIMEZONE", raising=False)
        assert (
            DailyRoutineService._resolve_user_timezone("Europe/London")
            == "Europe/London"
        )

    def test_env_fallback_when_user_md_none(self, monkeypatch: Any) -> None:
        """AC-2：USER.md 未提供（None）时降级到 env。"""
        monkeypatch.setenv("OCTOAGENT_USER_TIMEZONE", "Asia/Shanghai")
        assert (
            DailyRoutineService._resolve_user_timezone(None) == "Asia/Shanghai"
        )

    def test_utc_when_both_absent(self, monkeypatch: Any) -> None:
        """AC-3：USER.md + env 均缺 → UTC。"""
        monkeypatch.delenv("OCTOAGENT_USER_TIMEZONE", raising=False)
        assert DailyRoutineService._resolve_user_timezone(None) == "UTC"


class TestUserMdTimezoneAffectsYesterdayWindow:
    """F115 AC-7：USER.md 机器可读时区真实影响"昨日"窗口边界（service 集成）。

    这是 F115 的核心修复——baseline 的 self._user_timezone 是 __init__ env-only
    缓存，USER.md 改时区对昨日窗口零影响；修复后窗口从重读的 config 派生。
    """

    def test_user_md_timezone_changes_yesterday_date(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
        monkeypatch: Any,
    ) -> None:
        # env 清空，确保 UTC 分支不被部署 env 污染
        monkeypatch.delenv("OCTOAGENT_USER_TIMEZONE", raising=False)
        # now_utc 选在 UTC 当日 18:00：Asia/Shanghai(UTC+8) 已跨入次日凌晨
        # → Shanghai 的"昨日" = UTC 的"今日"，与 UTC 的"昨日"差一天，断言清晰
        now_utc = datetime(2026, 6, 8, 18, 0, tzinfo=UTC)

        # USER.md 配 Asia/Shanghai：昨日 = 2026-06-08
        svc_sh = _build_service(
            store_group,
            notification_service,
            user_md='- **user_timezone**: "Asia/Shanghai"',
        )
        config_sh = svc_sh._read_config()
        tz_sh = svc_sh._resolve_user_timezone(config_sh.user_timezone)
        assert tz_sh == "Asia/Shanghai"
        _, _, date_sh = svc_sh._compute_yesterday_range_utc(now_utc, tz_sh)
        assert date_sh == "2026-06-08"

        # USER.md 无时区字段 + env 空 → UTC：昨日 = 2026-06-07
        svc_utc = _build_service(store_group, notification_service, user_md="")
        config_utc = svc_utc._read_config()
        tz_utc = svc_utc._resolve_user_timezone(config_utc.user_timezone)
        assert tz_utc == "UTC"
        _, _, date_utc = svc_utc._compute_yesterday_range_utc(now_utc, tz_utc)
        assert date_utc == "2026-06-07"

        # 同一 now_utc，时区不同 → 昨日日期不同（核心证明）
        assert date_sh != date_utc


class TestCancelledErrorRespected:
    @pytest.mark.asyncio
    async def test_cancelled_error_during_llm_call_propagates(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
    ) -> None:
        """AC-E3 / FR-B6：LLM 路径中的 CancelledError MUST 显式 re-raise，不被吞掉。

        注意：AsyncMock side_effect=CancelledError() 实例会被 AsyncMock 内部
        return None（CancelledError 是 BaseException 不是 Exception），所以这里
        直接 monkeypatch provider_router.complete 为真异步函数手动 raise。
        """
        from datetime import timedelta

        now_utc = datetime.now(UTC)
        yesterday_noon = (now_utc - timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        await _create_task(store_group, "t-1", yesterday_noon, TaskStatus.SUCCEEDED)
        await store_group.conn.commit()

        svc = _build_service(store_group, notification_service)
        await svc.startup()

        async def _raise_cancelled(**kwargs: Any) -> str:
            raise asyncio.CancelledError("simulated cancellation")

        svc._provider_router.complete = _raise_cancelled

        with pytest.raises(asyncio.CancelledError):
            await svc._run_daily_summary()


# ============================================================
# F146 件①：USER.md 盘优先（F111 修法推广，TestConfigDiskFirst 同款锚）
# ============================================================


class TestConfigDiskFirst:
    async def test_disk_user_md_wins_over_stale_snapshot(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
        tmp_path: Path,
    ) -> None:
        """盘上 USER.md（routine_active=false）优先于 stale snapshot live state
        （默认 true）——盘外编辑对 cron 即时可见（F146 件①行为变更锚）。"""
        from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

        project_root = tmp_path / "root"
        user_md = resolve_write_path_by_file_id(project_root, "USER.md")
        user_md.parent.mkdir(parents=True, exist_ok=True)
        user_md.write_text('- **routine_active**: "false"', encoding="utf-8")
        svc = _build_service(
            store_group,
            notification_service,
            user_md='- **routine_active**: "true"',  # stale snapshot
            project_root=project_root,
        )
        config = svc._read_config()
        assert config.routine_active is False  # 盘赢

    async def test_snapshot_fallback_when_disk_missing(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
        tmp_path: Path,
    ) -> None:
        """盘上无 USER.md → snapshot live state 兜底（#6 降级链原样）。"""
        svc = _build_service(
            store_group,
            notification_service,
            user_md='- **routine_active**: "false"',
            project_root=tmp_path / "empty-root",
        )
        config = svc._read_config()
        assert config.routine_active is False  # live state 兜底生效


# ============================================================
# F146 件③：cron 时间热重载（下一次已排定 tick 读盘生效，无需重启）
# ============================================================


class TestCronHotReload:
    def _write_user_md(self, project_root: Path, time_value: str) -> None:
        from octoagent.core.behavior_workspace import resolve_write_path_by_file_id

        user_md = resolve_write_path_by_file_id(project_root, "USER.md")
        user_md.parent.mkdir(parents=True, exist_ok=True)
        user_md.write_text(
            f'- **routine_active**: "false"\n- **daily_summary_time**: "{time_value}"\n',
            encoding="utf-8",
        )

    async def test_time_change_reschedules_on_next_tick(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """改 USER.md daily_summary_time 后下一次 tick 重注册 cron（无需重启）。"""
        monkeypatch.delenv("OCTOAGENT_USER_TIMEZONE", raising=False)
        project_root = tmp_path / "root"
        self._write_user_md(project_root, "08:30")
        svc = _build_service(
            store_group, notification_service, project_root=project_root
        )
        await svc.startup()
        assert svc._registered_cron_key == ("30 8 * * *", "UTC")

        self._write_user_md(project_root, "07:00")  # 盘外编辑改时间
        await svc._run_daily_summary()  # 下一次 tick（disabled 短路在 reconcile 之后）
        assert svc._scheduler._scheduler.add_job.call_count == 2
        assert svc._registered_cron_key == ("0 7 * * *", "UTC")

    async def test_unchanged_time_does_not_reregister(
        self,
        store_group: StoreGroup,
        notification_service: NotificationService,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """时间未变 → tick 不重注册（幂等，无调度抖动）。"""
        monkeypatch.delenv("OCTOAGENT_USER_TIMEZONE", raising=False)
        project_root = tmp_path / "root"
        self._write_user_md(project_root, "08:30")
        svc = _build_service(
            store_group, notification_service, project_root=project_root
        )
        await svc.startup()
        await svc._run_daily_summary()
        assert svc._scheduler._scheduler.add_job.call_count == 1  # 仅 startup 一次
