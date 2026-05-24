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


def _build_service(
    store_group: StoreGroup,
    notification_service: NotificationService,
    user_md: str = "",
    llm_return: str | Exception | None = None,
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
