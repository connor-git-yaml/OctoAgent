"""F127 — ConsolidationConfig USER.md 解析单测（FR-F）。

复用 F102 解析范式：强制 key prefix + 跳过 HTML 注释行 + 非法值 fallback 默认。
v0.1 关键默认：consolidation_active **False**（保守默认关）。
"""

from __future__ import annotations

from octoagent.gateway.services.consolidation_config import (
    DEFAULT_CONSOLIDATION_ACTIVE,
    DEFAULT_CONSOLIDATION_MAX_FACTS,
    DEFAULT_CONSOLIDATION_TIME,
    DEFAULT_CONSOLIDATION_WINDOW_DAYS,
    ConsolidationConfig,
    extract_consolidation_active_from_user_md,
    extract_consolidation_max_facts_from_user_md,
    extract_consolidation_time_from_user_md,
    extract_consolidation_window_days_from_user_md,
)


class TestDefaults:
    def test_active_default_false(self):
        """v0.1 保守：缺失 → False（用户必须显式开）。"""
        assert DEFAULT_CONSOLIDATION_ACTIVE is False
        assert extract_consolidation_active_from_user_md(None) is False
        assert extract_consolidation_active_from_user_md("") is False
        assert extract_consolidation_active_from_user_md("# 无相关字段") is False

    def test_time_default(self):
        assert DEFAULT_CONSOLIDATION_TIME == "03:00"
        assert extract_consolidation_time_from_user_md(None) == "03:00"

    def test_window_default(self):
        assert DEFAULT_CONSOLIDATION_WINDOW_DAYS == 7
        assert extract_consolidation_window_days_from_user_md(None) == 7

    def test_max_facts_default(self):
        assert DEFAULT_CONSOLIDATION_MAX_FACTS == 50
        assert extract_consolidation_max_facts_from_user_md(None) == 50


class TestActiveParsing:
    def test_true(self):
        assert extract_consolidation_active_from_user_md(
            '- **consolidation_active**: true'
        ) is True

    def test_false_explicit(self):
        assert extract_consolidation_active_from_user_md(
            '- **consolidation_active**: false'
        ) is False

    def test_quoted_true(self):
        assert extract_consolidation_active_from_user_md(
            '- **consolidation_active**: "true"'
        ) is True

    def test_skips_html_comment(self):
        """注释行的字面值不应被误命中（F102 H1 BLOCKER 同范式）。"""
        content = (
            '<!-- consolidation_active: "true" 是示例 -->\n'
            '- **consolidation_active**: false'
        )
        assert extract_consolidation_active_from_user_md(content) is False


class TestTimeParsing:
    def test_valid(self):
        assert extract_consolidation_time_from_user_md(
            '- **consolidation_time**: "03:30"'
        ) == "03:30"

    def test_single_digit_hour(self):
        assert extract_consolidation_time_from_user_md(
            '- **consolidation_time**: 3:30'
        ) == "3:30"

    def test_invalid_falls_back(self):
        # 25:99 非法 → 默认
        assert extract_consolidation_time_from_user_md(
            '- **consolidation_time**: 25:99'
        ) == "03:00"


class TestWindowParsing:
    def test_valid(self):
        assert extract_consolidation_window_days_from_user_md(
            '- **consolidation_window_days**: 14'
        ) == 14

    def test_zero_out_of_range_falls_back(self):
        assert extract_consolidation_window_days_from_user_md(
            '- **consolidation_window_days**: 0'
        ) == 7

    def test_over_cap_falls_back(self):
        assert extract_consolidation_window_days_from_user_md(
            '- **consolidation_window_days**: 9999'
        ) == 7


class TestMaxFactsParsing:
    def test_valid(self):
        assert extract_consolidation_max_facts_from_user_md(
            '- **consolidation_max_facts**: 120'
        ) == 120

    def test_over_cap_falls_back(self):
        assert extract_consolidation_max_facts_from_user_md(
            '- **consolidation_max_facts**: 99999'
        ) == 50


class TestConfigAggregate:
    def test_from_user_md_full(self):
        content = (
            '- **consolidation_active**: true\n'
            '- **consolidation_time**: "02:15"\n'
            '- **consolidation_window_days**: 30\n'
            '- **consolidation_max_facts**: 100\n'
            '- **user_timezone**: "Asia/Shanghai"\n'
        )
        cfg = ConsolidationConfig.from_user_md(content)
        assert cfg.consolidation_active is True
        assert cfg.consolidation_time == "02:15"
        assert cfg.consolidation_window_days == 30
        assert cfg.consolidation_max_facts == 100
        assert cfg.user_timezone == "Asia/Shanghai"

    def test_from_user_md_empty_all_defaults(self):
        cfg = ConsolidationConfig.from_user_md("")
        assert cfg.consolidation_active is False
        assert cfg.consolidation_time == "03:00"
        assert cfg.consolidation_window_days == 7
        assert cfg.consolidation_max_facts == 50
        assert cfg.user_timezone is None

    def test_to_crontab(self):
        cfg = ConsolidationConfig.from_user_md(
            '- **consolidation_time**: "03:05"'
        )
        # "MM HH * * *"
        assert cfg.to_crontab() == "5 3 * * *"

    def test_to_crontab_strips_leading_zero(self):
        cfg = ConsolidationConfig.from_user_md(
            '- **consolidation_time**: "09:00"'
        )
        assert cfg.to_crontab() == "0 9 * * *"
