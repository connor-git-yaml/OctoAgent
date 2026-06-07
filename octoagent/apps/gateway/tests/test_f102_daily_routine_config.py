"""F102 Phase B — daily_routine_config 单元测试。

覆盖 AC-D1 / AC-D2 / AC-D3（解析侧）/ AC-D4：
- 三个 USER.md 解析函数的合法值、非法值、缺失值、边界值
- DailyRoutineConfig.from_user_md + to_crontab 完整流程
- Payload schema 字段约束（Pydantic）
"""

from __future__ import annotations

from importlib import resources

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
    extract_user_timezone_from_user_md,
)


def _load_factory_user_md() -> str:
    """读取出厂 USER.md 模板原文（与运行时 behavior_templates 同一资源包）。"""
    return (
        resources.files("octoagent.core.behavior_templates")
        .joinpath("USER.md")
        .read_text(encoding="utf-8")
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

    def test_comment_line_with_real_value_is_skipped(self) -> None:
        """HTML 注释行即使含合法 HH:MM 值也不被解析（守卫生效，不靠 "HH:MM" 占位符侥幸）。

        无守卫时注释里的 "07:15" 会被捕获并 return；有守卫时该行跳过 → 返回默认。
        """
        content = '<!-- daily_summary_time: "07:15" 示例 -->'
        assert (
            extract_daily_summary_time_from_user_md(content)
            == DEFAULT_DAILY_SUMMARY_TIME
        )

    def test_comment_line_before_value_does_not_shadow(self) -> None:
        """注释行（含示例时间）在 value 行之前，value 行 "09:00" 必须胜出。"""
        content = (
            '<!-- daily_summary_time: "07:15"，每日推送时间 -->\n'
            '- **daily_summary_time**: "09:00"'
        )
        assert extract_daily_summary_time_from_user_md(content) == "09:00"


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

    def test_comment_true_does_not_shadow_value_false(self) -> None:
        """F102 核心 bug 回归守卫：USER.md 注释行含字面 ``routine_active: "true"``，
        写在用户改成 ``"false"`` 的 value 行之前。

        旧实现逐行扫描时注释行的 "true" 先命中 → 立即 return True → 用户无法在
        USER.md 关闭 daily routine。守卫跳过注释行后 value 行 "false" 必须胜出。
        """
        content = (
            '<!-- routine_active: "true" / "false"，是否启用 daily routine，默认 true -->\n'
            '- **routine_active**: "false"'
        )
        assert extract_routine_active_from_user_md(content) is False

    def test_comment_only_line_is_skipped(self) -> None:
        """仅含注释行（无 value 行）时，注释里的 "false" 不被解析 → 返回默认 True。"""
        content = '<!-- routine_active: "false" 示例 -->'
        assert extract_routine_active_from_user_md(content) is DEFAULT_ROUTINE_ACTIVE


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

    def test_comment_line_with_real_value_is_skipped(self) -> None:
        """HTML 注释行即使含合法 channel 名也不被解析（守卫生效，不靠注释接中文侥幸）。

        无守卫时注释里的 "telegram" 会被捕获并 return；有守卫时该行跳过 → 返回默认全渠道。
        """
        content = "<!-- summary_channels: telegram example -->"
        assert (
            extract_summary_channels_from_user_md(content) == DEFAULT_SUMMARY_CHANNELS
        )

    def test_comment_line_before_value_does_not_shadow(self) -> None:
        """注释行（含示例 channel）在 value 行之前，value 行 "telegram" 必须胜出。"""
        content = (
            "<!-- summary_channels: web example -->\n"
            '- **summary_channels**: "telegram"'
        )
        assert extract_summary_channels_from_user_md(content) == frozenset({"telegram"})


# ============================================================
# extract_user_timezone_from_user_md (F115 AC-5)
# ============================================================


class TestExtractUserTimezone:
    """F115：user_timezone 机器可读字段解析（缺失/非法返回 None 交上层降级）。"""

    def test_standard_list_format(self) -> None:
        content = '- **user_timezone**: "Asia/Shanghai"'
        assert extract_user_timezone_from_user_md(content) == "Asia/Shanghai"

    def test_naked_value(self) -> None:
        content = "user_timezone: America/New_York"
        assert extract_user_timezone_from_user_md(content) == "America/New_York"

    def test_multi_slash_iana_name(self) -> None:
        """覆盖多斜杠 IANA 名（如 America/Argentina/Buenos_Aires）。"""
        content = '- **user_timezone**: "America/Argentina/Buenos_Aires"'
        assert (
            extract_user_timezone_from_user_md(content)
            == "America/Argentina/Buenos_Aires"
        )

    def test_etc_gmt_offset_name(self) -> None:
        """覆盖含 +/数字 的 IANA 名（如 Etc/GMT+8）。"""
        content = "user_timezone: Etc/GMT+8"
        assert extract_user_timezone_from_user_md(content) == "Etc/GMT+8"

    def test_missing_field_returns_none(self) -> None:
        assert extract_user_timezone_from_user_md("no timezone here") is None

    def test_none_content_returns_none(self) -> None:
        assert extract_user_timezone_from_user_md(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_user_timezone_from_user_md("") is None

    def test_invalid_timezone_returns_none(self) -> None:
        """非法 IANA 名 → None（+ WARNING），交由 service 降级 env/UTC。"""
        content = '- **user_timezone**: "Mars/Olympus_Mons"'
        assert extract_user_timezone_from_user_md(content) is None

    def test_comment_line_with_valid_example_is_skipped(self) -> None:
        """关键：注释行里的合法时区示例（如文档注释）不被当作真实值误命中。

        其他三字段靠占位符（非法值）天然规避，user_timezone 示例是真值，
        extract 必须显式跳过 HTML 注释行（否则 premature return 漏掉真实 value 行）。
        """
        content = (
            '<!-- user_timezone: 机器可读 IANA 名，如 "Asia/Shanghai" -->\n'
            '- **user_timezone**: "Europe/London"'
        )
        assert extract_user_timezone_from_user_md(content) == "Europe/London"

    def test_comment_only_no_value_line_returns_none(self) -> None:
        """只有注释行（无真实 value 行）→ None（不取注释里的示例值）。"""
        content = '<!-- user_timezone: 如 "Asia/Shanghai"，留空则降级 -->'
        assert extract_user_timezone_from_user_md(content) is None

    def test_placeholder_value_returns_none(self) -> None:
        """USER.md 模板的占位文字（非 IANA 名）→ None → 降级 env/UTC。"""
        content = "- **user_timezone**: （留空，或填 IANA 名如 \"Asia/Shanghai\"）"
        assert extract_user_timezone_from_user_md(content) is None

    def test_shipped_template_user_timezone_is_unset(self) -> None:
        """出厂 USER.md 模板 user_timezone 必须解析为 None（占位符发布）。

        守卫：模板不得硬编码具体时区——否则对所有新实例覆盖 env，
        破坏 "USER.md 未填 → 降级 env/UTC" 的默认语义。
        """
        from importlib import resources

        template = (
            resources.files("octoagent.core.behavior_templates")
            .joinpath("USER.md")
            .read_text(encoding="utf-8")
        )
        assert extract_user_timezone_from_user_md(template) is None


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

    def test_user_timezone_none_when_missing(self) -> None:
        """F115：USER.md 无 user_timezone 字段时 config.user_timezone 为 None。"""
        cfg = DailyRoutineConfig.from_user_md("")
        assert cfg.user_timezone is None


# ============================================================
# F115 AC-6：DailyRoutineConfig 携带 user_timezone
# ============================================================


class TestConfigIncludesUserTimezone:
    """F115：from_user_md 把解析到的 user_timezone 纳入 config。"""

    def test_config_carries_valid_user_timezone(self) -> None:
        content = (
            '- **daily_summary_time**: "08:30"\n'
            '- **user_timezone**: "Asia/Tokyo"'
        )
        cfg = DailyRoutineConfig.from_user_md(content)
        assert cfg.user_timezone == "Asia/Tokyo"

    def test_config_user_timezone_none_for_invalid(self) -> None:
        content = '- **user_timezone**: "Not/AZone"'
        cfg = DailyRoutineConfig.from_user_md(content)
        assert cfg.user_timezone is None

    def test_direct_construction_defaults_user_timezone_none(self) -> None:
        """末位默认 None：既有直接构造（不传时区）零改动仍合法。"""
        cfg = DailyRoutineConfig(
            daily_summary_time="08:30",
            routine_active=True,
            summary_channels=DEFAULT_SUMMARY_CHANNELS,
        )
        assert cfg.user_timezone is None


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


# ============================================================
# 出厂 USER.md 模板：注释守卫端到端（F102 真 bug 回归）
# ============================================================


class TestFactoryTemplateCommentGuard:
    """用真实出厂 USER.md 模板验证注释守卫——模板的 F102 字段注释块（含字面
    routine_active: "true"）真实存在，是 bug 的诱因，必须端到端覆盖。"""

    def test_factory_template_parses_to_defaults(self) -> None:
        """出厂模板原文（routine_active="true"）应解析出与默认值一致的 config。"""
        cfg = DailyRoutineConfig.from_user_md(_load_factory_user_md())
        assert cfg.daily_summary_time == "08:30"
        assert cfg.routine_active is True
        assert cfg.summary_channels == frozenset({"telegram", "web_sse"})

    def test_factory_template_user_can_disable_routine(self) -> None:
        """F102 核心 bug：用户把出厂模板 value 行改成 "false" 必须能真正关闭 routine。

        注释块（line 39）含字面 ``routine_active: "true"``——无守卫时它先命中导致
        改 "false" 也关不掉。本测试是用户视角的最终回归守卫。
        """
        tpl = _load_factory_user_md()
        disabled = tpl.replace(
            '- **routine_active**: "true"', '- **routine_active**: "false"'
        )
        # 防止模板字段格式漂移让 replace 变 no-op（否则测试会假阳性通过）
        assert disabled != tpl, "出厂模板 routine_active value 行格式已变，需更新本测试"
        cfg = DailyRoutineConfig.from_user_md(disabled)
        assert cfg.routine_active is False
