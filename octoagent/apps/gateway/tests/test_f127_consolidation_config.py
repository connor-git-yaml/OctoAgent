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


class TestConfigKeyAnchoring:
    """Codex round3 finding-D：key 须左边界锚定——用户在 USER.md 写说明性字段
    （``previous_consolidation_active`` / ``last_consolidation_time`` 等，key 作为子串出现）
    不得被当真实配置，否则默认关的破坏性巩固会被误开（C4/C7）。
    """

    def test_prefixed_key_does_not_enable_active(self):
        """previous_consolidation_active: true → 不得误开（仍返回默认 False）。"""
        assert (
            extract_consolidation_active_from_user_md(
                "- previous_consolidation_active: true"
            )
            is False
        )

    def test_underscore_prefixed_active_not_matched(self):
        """my_consolidation_active: true → 不匹配（左边界挡标识符前缀）。"""
        assert (
            extract_consolidation_active_from_user_md(
                "记录：my_consolidation_active: true（仅说明）"
            )
            is False
        )

    def test_prefixed_time_not_matched(self):
        """last_consolidation_time: 02:00 → 不匹配，返回默认 03:00。"""
        assert (
            extract_consolidation_time_from_user_md(
                "- last_consolidation_time: 02:00"
            )
            == "03:00"
        )

    def test_prefixed_window_not_matched(self):
        assert (
            extract_consolidation_window_days_from_user_md(
                "- old_consolidation_window_days: 30"
            )
            == 7
        )

    def test_prefixed_max_facts_not_matched(self):
        assert (
            extract_consolidation_max_facts_from_user_md(
                "- prev_consolidation_max_facts: 999"
            )
            == 50
        )

    def test_legitimate_forms_still_match_after_anchoring(self):
        """锚定不能误伤合法形式：** 包裹 / 行首裸 key / 列表项 key 都仍生效。"""
        # ** 包裹
        assert (
            extract_consolidation_active_from_user_md(
                "- **consolidation_active**: true"
            )
            is True
        )
        # 行首裸 key（无列表前缀）
        assert (
            extract_consolidation_active_from_user_md("consolidation_active: true")
            is True
        )
        # 列表项裸 key
        assert (
            extract_consolidation_active_from_user_md("- consolidation_active: true")
            is True
        )

    def test_mixed_prefixed_and_real_line_picks_real(self):
        """同一 USER.md 既有说明性前缀行又有真实配置行 → 只采真实行。"""
        content = (
            "- previous_consolidation_active: true   # 历史说明，不应生效\n"
            "- consolidation_active: false           # 真实配置\n"
        )
        # 前缀行被锚定跳过；真实行 false → False
        assert extract_consolidation_active_from_user_md(content) is False


class TestBoolRightBoundary:
    """Codex round5 finding-F：布尔值须右边界——consolidation_active: truee / true_x 这类
    非法值不得匹配前缀 true 误开（默认关的破坏性巩固 C4/C7）。
    """

    def test_truee_does_not_enable(self):
        """consolidation_active: truee → 非法，fallback False（不匹配前缀 true）。"""
        assert (
            extract_consolidation_active_from_user_md(
                "- consolidation_active: truee"
            )
            is False
        )

    def test_true_underscore_suffix_does_not_enable(self):
        assert (
            extract_consolidation_active_from_user_md(
                "- consolidation_active: true_enabled"
            )
            is False
        )

    def test_falsey_suffix_does_not_match(self):
        """consolidation_active: falsey → 非法，fallback False。"""
        assert (
            extract_consolidation_active_from_user_md(
                "- consolidation_active: falsey"
            )
            is False
        )

    def test_quoted_true_still_works_with_right_boundary(self):
        """右边界不误伤合法引号形式 "true"。"""
        assert (
            extract_consolidation_active_from_user_md(
                '- consolidation_active: "true"'
            )
            is True
        )

    def test_time_trailing_digit_rejected(self):
        """consolidation_time: 3:300 → HH:MM 后紧跟数字，非法 fallback 03:00。"""
        assert (
            extract_consolidation_time_from_user_md(
                "- consolidation_time: 3:300"
            )
            == "03:00"
        )

    def test_window_trailing_word_rejected(self):
        """consolidation_window_days: 14x → 数字后紧贴字母，非法 fallback 7。"""
        assert (
            extract_consolidation_window_days_from_user_md(
                "- consolidation_window_days: 14x"
            )
            == 7
        )


class TestMultilineHtmlComment:
    """Codex round5 finding-G：多行 HTML 注释块内的示例配置不得被当真实配置——否则
    注释里写 consolidation_active: true 也会误开默认关闭的破坏性巩固。
    """

    def test_multiline_comment_block_config_ignored(self):
        """多行 <!-- ... --> 块内 consolidation_active: true → 不生效（仍默认 False）。"""
        content = (
            "<!--\n"
            "示例：开启巩固写\n"
            "- consolidation_active: true\n"
            "-->\n"
            "# 用户实际未开启\n"
        )
        assert extract_consolidation_active_from_user_md(content) is False

    def test_multiline_comment_then_real_config(self):
        """注释块内 true（示例）+ 块外 false（真实）→ 取真实 False。"""
        content = (
            "<!-- 示例：\n"
            "- consolidation_active: true\n"
            "-->\n"
            "- consolidation_active: false\n"
        )
        assert extract_consolidation_active_from_user_md(content) is False

    def test_inline_comment_block_on_one_line_ignored(self):
        """单行内联 <!-- consolidation_active: true --> → 不生效。"""
        content = "<!-- consolidation_active: true 是示例 -->\n# 实际未开\n"
        assert extract_consolidation_active_from_user_md(content) is False

    def test_real_config_after_inline_comment_same_line(self):
        """行内注释剥离后，同行真实配置仍生效（注释 + 真实在不同语义位置）。"""
        # 注释块在前被剥离，真实 key 在注释块之后
        content = "<!-- 旧值 true -->\n- consolidation_active: true\n"
        assert extract_consolidation_active_from_user_md(content) is True
