"""CooldownRegistry 单元测试 -- Feature 011 T018

覆盖首次检测/cooldown 窗口内/cooldown 过期/重建逻辑场景。
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.gateway.services.watchdog.cooldown import CooldownRegistry


class TestCooldownRegistryBasic:
    """基础防抖逻辑测试"""

    def test_initially_not_in_cooldown(self):
        """初始状态：任何任务都不在 cooldown 中"""
        registry = CooldownRegistry()
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is False

    def test_record_and_check_cooldown(self):
        """记录 DRIFT 后立即进入 cooldown"""
        registry = CooldownRegistry()
        now = datetime.now(UTC)
        registry.record_drift("task-001", now)
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True

    def test_cooldown_expired(self):
        """cooldown 过期后不在 cooldown 中"""
        registry = CooldownRegistry()
        # 记录一个 61 秒前的 DRIFT 事件
        old_ts = datetime.now(UTC) - timedelta(seconds=61)
        registry.record_drift("task-001", old_ts)
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is False

    def test_cooldown_boundary_still_in(self):
        """cooldown 窗口边界内：仍在 cooldown"""
        registry = CooldownRegistry()
        # 记录 30 秒前的事件，cooldown=60，应仍在窗口内
        recent_ts = datetime.now(UTC) - timedelta(seconds=30)
        registry.record_drift("task-001", recent_ts)
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True

    def test_different_tasks_independent(self):
        """不同任务的 cooldown 状态独立"""
        registry = CooldownRegistry()
        now = datetime.now(UTC)
        registry.record_drift("task-001", now)

        # task-001 在 cooldown 中
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True
        # task-002 不在 cooldown 中
        assert registry.is_in_cooldown("task-002", cooldown_seconds=60) is False

    def test_record_drift_updates_timestamp(self):
        """更新 DRIFT 时间戳：以最新记录为准"""
        registry = CooldownRegistry()
        old_ts = datetime.now(UTC) - timedelta(seconds=61)
        registry.record_drift("task-001", old_ts)
        # 此时已过期
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is False

        # 记录新的 DRIFT
        registry.record_drift("task-001", datetime.now(UTC))
        # 现在重新进入 cooldown
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True


class TestCooldownRegistryRebuild:
    """跨重启重建逻辑测试"""

    @pytest.mark.asyncio
    async def test_rebuild_with_recent_drift_restores_cooldown(self):
        """从 EventStore 重建：有近期 DRIFT 事件，恢复 cooldown"""
        registry = CooldownRegistry()

        # 模拟 EventStore 返回近期 DRIFT 事件
        recent_ts = datetime.now(UTC) - timedelta(seconds=30)
        mock_event = MagicMock()
        mock_event.ts = recent_ts

        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = [mock_event]

        await registry.rebuild_from_store(
            event_store=mock_event_store,
            active_task_ids=["task-001"],
            cooldown_seconds=60,
        )

        # 重建后 task-001 应在 cooldown 中
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True

    @pytest.mark.asyncio
    async def test_rebuild_with_old_drift_does_not_restore_cooldown(self):
        """从 EventStore 重建：DRIFT 事件已过 cooldown 窗口，不恢复"""
        registry = CooldownRegistry()

        # 模拟 EventStore 返回空（cooldown 窗口外的事件不会被查询到）
        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = []

        await registry.rebuild_from_store(
            event_store=mock_event_store,
            active_task_ids=["task-001"],
            cooldown_seconds=60,
        )

        # 无近期 DRIFT，不在 cooldown 中
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is False

    @pytest.mark.asyncio
    async def test_rebuild_selects_latest_drift_ts(self):
        """重建时选取最近一次 DRIFT 事件时间戳"""
        registry = CooldownRegistry()

        ts1 = datetime.now(UTC) - timedelta(seconds=50)
        ts2 = datetime.now(UTC) - timedelta(seconds=10)  # 最新

        mock_event1 = MagicMock()
        mock_event1.ts = ts1
        mock_event2 = MagicMock()
        mock_event2.ts = ts2

        mock_event_store = AsyncMock()
        mock_event_store.get_events_by_types_since.return_value = [mock_event1, mock_event2]

        await registry.rebuild_from_store(
            event_store=mock_event_store,
            active_task_ids=["task-001"],
            cooldown_seconds=60,
        )

        # 以最新 ts2 为基准，仍在 cooldown 中
        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True

    @pytest.mark.asyncio
    async def test_rebuild_multiple_tasks(self):
        """重建多个任务的 cooldown 状态"""
        registry = CooldownRegistry()

        recent_ts = datetime.now(UTC) - timedelta(seconds=20)
        mock_event = MagicMock()
        mock_event.ts = recent_ts

        mock_event_store = AsyncMock()

        # task-001 有近期 DRIFT，task-002 没有
        def side_effect(task_id, event_types, since_ts):
            if task_id == "task-001":
                return [mock_event]
            return []

        mock_event_store.get_events_by_types_since.side_effect = side_effect

        await registry.rebuild_from_store(
            event_store=mock_event_store,
            active_task_ids=["task-001", "task-002"],
            cooldown_seconds=60,
        )

        assert registry.is_in_cooldown("task-001", cooldown_seconds=60) is True
        assert registry.is_in_cooldown("task-002", cooldown_seconds=60) is False

    @pytest.mark.asyncio
    async def test_rebuild_empty_task_list(self):
        """重建时活跃任务列表为空，无操作"""
        registry = CooldownRegistry()
        mock_event_store = AsyncMock()

        await registry.rebuild_from_store(
            event_store=mock_event_store,
            active_task_ids=[],
            cooldown_seconds=60,
        )

        # EventStore 未被调用
        mock_event_store.get_events_by_types_since.assert_not_called()
