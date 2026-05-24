"""F102 Phase B — daily_routine_config 单元测试。

覆盖 AC-D1 / AC-D2 / AC-D3（解析侧）/ AC-D4：
- 三个 USER.md 解析函数的合法值、非法值、缺失值、边界值
- DailyRoutineConfig.from_user_md + to_crontab 完整流程
- Payload schema 字段约束（Pydantic）
"""

from __future__ import annotations

import pytest

from octoagent.gateway.services.daily_routine_config import (
    DEFAULT_DAILY_SUMMARY_TIME,
    DEFAULT_ROUTINE_ACTIVE,
    DEFAULT_SUMMARY_CHANNELS,
    DailyRoutineConfig,
    RoutineCompletedPayload,
    RoutineFailedPayload,
    RoutineSkippedPayload,
    RoutineTriggeredPayload,
    extract_daily_summary_time_from_user_md,
    extract_routine_active_from_user_md,
    extract_summary_channels_from_user_md,
)


# ============================================================
# extract_daily_summary_time_from_user_md (AC-D1)
# ============================================================


class TestDailySummaryTimeParsing:
    """AC-D1 daily_summary_time 字段解析。"""

    def test_standard_user_md_list_format(self) -> None:
        content = '- **daily_summary_time**: "09:00"'
        assert extract_daily_summary_time_from_user_md(content) == "09:00"

    def test_naked_value(self) -> None:
        content = "daily_summary_time: 08:30"
        assert extract_daily_summary_time_from_user_md(content) == "08:30"

    def test_single_digit_hour(self) -> None:
        """允许 H:MM 格式（不强制 HH:MM 前导零）。"""
        content = '- **daily_summary_time**: "8:30"'
        assert extract_daily_summary_time_from_user_md(content) == "8:30"

    def test_missing_field_returns_default(self) -> None:
        content = "## 用户画像\n- **active_hours**: \"09:00-23:00\""
        assert (
            extract_daily_summary_time_from_user_md(content)
            == DEFAULT_DAILY_SUMMARY_TIME
        )

    def test_none_content_returns_default(self) -> None:
        assert (
            extract_daily_summary_time_from_user_md(None) == DEFAULT_DAILY_SUMMARY_TIME
        )

    def test_empty_string_returns_default(self) -> None:
        assert extract_daily_summary_time_from_user_md("") == DEFAULT_DAILY_SUMMARY_TIME

    def test_invalid_hour_falls_back_to_default(self) -> None:
        """HH=25 非法（> 23），fallback 到默认。"""
        content = '- **daily_summary_time**: "25:00"'
        assert (
            extract_daily_summary_time_from_user_md(content)
            == DEFAULT_DAILY_SUMMARY_TIME
        )

    def test_invalid_minute_falls_back_to_default(self) -> None:
        """MM=60 非法（> 59），fallback 到默认。"""
        content = '- **daily_summary_time**: "08:60"'
        assert (
            extract_daily_summary_time_from_user_md(content)
            == DEFAULT_DAILY_SUMMARY_TIME
        )

    def test_boundary_values(self) -> None:
        for time_str in ["0:00", "23:59", "12:00"]:
            content = f'- **daily_summary_time**: "{time_str}"'
            assert extract_daily_summary_time_from_user_md(content) == time_str


# ============================================================
# extract_routine_active_from_user_md (AC-D2)
# ============================================================


class TestRoutineActiveParsing:
    """AC-D2 routine_active 字段解析。"""

    def test_true_with_quotes(self) -> None:
        content = '- **routine_active**: "true"'
        assert extract_routine_active_from_user_md(content) is True

    def test_false_with_quotes(self) -> None:
        content = '- **routine_active**: "false"'
        assert extract_routine_active_from_user_md(content) is False

    def test_naked_true(self) -> None:
        content = "routine_active: true"
        assert extract_routine_active_from_user_md(content) is True

    def test_case_insensitive_True(self) -> None:
        content = '- **routine_active**: "True"'
        assert extract_routine_active_from_user_md(content) is True

    def test_missing_field_returns_default(self) -> None:
        content = "## 用户画像"
        assert (
            extract_routine_active_from_user_md(content) is DEFAULT_ROUTINE_ACTIVE
        )

    def test_none_returns_default(self) -> None:
        assert extract_routine_active_from_user_md(None) is DEFAULT_ROUTINE_ACTIVE


# ============================================================
# extract_summary_channels_from_user_md (AC-D3 解析侧)
# ============================================================


class TestSummaryChannelsParsing:
    """AC-D3 summary_channels 字段解析 + "web" → "web_sse" 映射（SD-6 / plan A-8）。"""

    def test_both_channels_default(self) -> None:
        content = '- **summary_channels**: "telegram,web"'
        result = extract_summary_channels_from_user_md(content)
        assert result == frozenset({"telegram", "web_sse"})

    def test_only_telegram(self) -> None:
        content = '- **summary_channels**: "telegram"'
        assert extract_summary_channels_from_user_md(content) == frozenset({"telegram"})

    def test_only_web_maps_to_web_sse(self) -> None:
        """SD-6 + plan A-8 关键映射：USER.md "web" → 内部 "web_sse"。"""
        content = '- **summary_channels**: "web"'
        assert extract_summary_channels_from_user_md(content) == frozenset({"web_sse"})

    def test_with_spaces(self) -> None:
        content = '- **summary_channels**: "telegram, web"'
        result = extract_summary_channels_from_user_md(content)
        assert result == frozenset({"telegram", "web_sse"})

    def test_missing_field_returns_default(self) -> None:
        content = "## 用户画像"
        assert (
            extract_summary_channels_from_user_md(content) == DEFAULT_SUMMARY_CHANNELS
        )

    def test_none_returns_default(self) -> None:
        assert (
            extract_summary_channels_from_user_md(None) == DEFAULT_SUMMARY_CHANNELS
        )

    def test_unknown_channel_falls_back_to_default(self) -> None:
        """未知 channel name 时 fallback 到全渠道（Constitution C6）。"""
        content = '- **summary_channels**: "telegram,slack"'
        assert (
            extract_summary_channels_from_user_md(content) == DEFAULT_SUMMARY_CHANNELS
        )

    def test_internal_value_web_sse_accepted(self) -> None:
        """Codex M3 修复：开发者直接写内部值 "web_sse" 也接受（不再 fallback）。"""
        content = '- **summary_channels**: "web_sse"'
        assert extract_summary_channels_from_user_md(content) == frozenset({"web_sse"})

    def test_key_string_in_value_position_does_not_match_itself(self) -> None:
        """Codex H1 BLOCKER 修复：value 部分包含 "summary_channels" 字面值
        不应被当作 channel name 提取——key prefix MUST 存在才提取 value。
        """
        # 用户随便写了个 invalid value："summary_channels: telegram_channels"
        # 旧实现：捕获 "telegram_channels" → 未知 channel → fallback 全渠道
        # 新实现：捕获 "telegram_channels" → 未知 channel → fallback 全渠道（同行为）
        # 关键测试：当 key 名出现在 value 部分时，不应误捕获
        # 即裸字符串 "summary_channels" 不带 ":" 前缀时不应被解析
        content = "随便提及 summary_channels 这个词但没赋值"
        # 该行不含 ":" 模式，应该跳过该行解析回到默认
        assert (
            extract_summary_channels_from_user_md(content) == DEFAULT_SUMMARY_CHANNELS
        )

    def test_naked_value_without_list_marker(self) -> None:
        """spec SD-1 / FR-D1 支持的写法：裸 key:value（无 **bold** 列表标记）。"""
        content = "summary_channels: telegram"
        assert extract_summary_channels_from_user_md(content) == frozenset({"telegram"})


# ============================================================
# AC-D4：全部字段缺失时整体行为
# ============================================================


class TestAcD4AllFieldsMissing:
    """AC-D4：USER.md 无任何 F102 新增字段时，全字段默认值生效。"""

    def test_empty_user_md(self) -> None:
        """完全空的 USER.md。"""
        cfg = DailyRoutineConfig.from_user_md("")
        assert cfg.daily_summary_time == DEFAULT_DAILY_SUMMARY_TIME
        assert cfg.routine_active is DEFAULT_ROUTINE_ACTIVE
        assert cfg.summary_channels == DEFAULT_SUMMARY_CHANNELS

    def test_only_active_hours_present(self) -> None:
        """有 F101 active_hours 但无 F102 字段。"""
        content = '- **active_hours**: "09:00-23:00"'
        cfg = DailyRoutineConfig.from_user_md(content)
        assert cfg.daily_summary_time == DEFAULT_DAILY_SUMMARY_TIME
        assert cfg.routine_active is DEFAULT_ROUTINE_ACTIVE
        assert cfg.summary_channels == DEFAULT_SUMMARY_CHANNELS


# ============================================================
# DailyRoutineConfig.to_crontab
# ============================================================


class TestCrontabConversion:
    """daily_summary_time → cron 表达式转换（FR-B1）。"""

    def test_08_30(self) -> None:
        cfg = DailyRoutineConfig(
            daily_summary_time="08:30",
            routine_active=True,
            summary_channels=DEFAULT_SUMMARY_CHANNELS,
        )
        assert cfg.to_crontab() == "30 8 * * *"

    def test_09_00(self) -> None:
        cfg = DailyRoutineConfig(
            daily_summary_time="09:00",
            routine_active=True,
            summary_channels=DEFAULT_SUMMARY_CHANNELS,
        )
        assert cfg.to_crontab() == "0 9 * * *"

    def test_23_59(self) -> None:
        cfg = DailyRoutineConfig(
            daily_summary_time="23:59",
            routine_active=True,
            summary_channels=DEFAULT_SUMMARY_CHANNELS,
        )
        assert cfg.to_crontab() == "59 23 * * *"

    def test_00_00(self) -> None:
        cfg = DailyRoutineConfig(
            daily_summary_time="0:00",
            routine_active=True,
            summary_channels=DEFAULT_SUMMARY_CHANNELS,
        )
        assert cfg.to_crontab() == "0 0 * * *"


# ============================================================
# Payload Schema 字段约束 (FR-E2 / FR-E3)
# ============================================================


class TestPayloadSchemas:
    """4 个 Routine event payload schema 字段约束。"""

    def test_routine_triggered_minimal(self) -> None:
        p = RoutineTriggeredPayload(trigger_ts="2026-05-25T08:30:00Z")
        assert p.routine_type == "daily"

    def test_routine_completed_full_fields(self) -> None:
        p = RoutineCompletedPayload(
            date="2026-05-24",
            worker_count=5,
            failed_count=1,
            attention_count=2,
            elapsed_ms=1500,
            llm_elapsed_ms=800,
            fallback=False,
            summary_length=120,
            channels=["telegram"],
        )
        assert p.routine_type == "daily"
        assert p.fallback is False
        assert p.channels == ["telegram"]

    def test_routine_completed_fallback_default(self) -> None:
        """Codex L5 修复：llm_elapsed_ms 默认 None（区分 fallback 路径与 LLM 真 0ms）。"""
        p = RoutineCompletedPayload(
            date="2026-05-24",
            worker_count=0,
            failed_count=0,
            attention_count=0,
            elapsed_ms=10,
            summary_length=0,
        )
        assert p.fallback is False
        assert p.llm_elapsed_ms is None
        assert p.channels is None

    def test_routine_completed_llm_path_sets_elapsed(self) -> None:
        """LLM 路径成功时 MUST 设置 llm_elapsed_ms 具体值。"""
        p = RoutineCompletedPayload(
            date="2026-05-24",
            worker_count=3,
            failed_count=0,
            attention_count=0,
            elapsed_ms=1500,
            llm_elapsed_ms=800,
            fallback=False,
            summary_length=120,
        )
        assert p.llm_elapsed_ms == 800
        assert p.fallback is False

    def test_routine_completed_rejects_negative_counts(self) -> None:
        with pytest.raises(ValueError):
            RoutineCompletedPayload(
                date="2026-05-24",
                worker_count=-1,
                failed_count=0,
                attention_count=0,
                elapsed_ms=10,
                summary_length=0,
            )

    def test_routine_failed_minimum(self) -> None:
        p = RoutineFailedPayload(error_type="TimeoutError", error_msg="cron timeout")
        assert p.routine_type == "daily"

    def test_routine_skipped_with_reason(self) -> None:
        p = RoutineSkippedPayload(reason="routine_disabled")
        assert p.reason == "routine_disabled"
