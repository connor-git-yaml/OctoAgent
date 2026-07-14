"""F111 Phase B — BehaviorCompactConfig USER.md 解析测试（FR-14）。

照 F127 consolidation_config 测试关注面：默认值 / 合法解析 / 左边界锚定
（previous_ 前缀不误匹配）/ HTML 注释块剥离 / 非法值 fallback / crontab 转换。
"""

from __future__ import annotations

from octoagent.gateway.services.behavior_compact_config import (
    DEFAULT_COMPACT_ACTIVE,
    DEFAULT_COMPACT_TIME,
    BehaviorCompactConfig,
    extract_compact_active_from_user_md,
    extract_compact_time_from_user_md,
)


class TestDefaults:
    def test_none_content_all_defaults(self):
        cfg = BehaviorCompactConfig.from_user_md(None)
        assert cfg.compact_active is False  # 保守默认关
        assert cfg.compact_time == "03:30"  # 与 F127 03:00 错峰
        assert DEFAULT_COMPACT_ACTIVE is False
        assert DEFAULT_COMPACT_TIME == "03:30"

    def test_empty_content_defaults(self):
        cfg = BehaviorCompactConfig.from_user_md("")
        assert cfg.compact_active is False
        assert cfg.compact_time == "03:30"


class TestActiveParsing:
    def test_parses_true_variants(self):
        assert extract_compact_active_from_user_md("compact_active: true") is True
        assert (
            extract_compact_active_from_user_md("- **compact_active**: True") is True
        )
        assert extract_compact_active_from_user_md('compact_active: "true"') is True

    def test_parses_false(self):
        assert extract_compact_active_from_user_md("compact_active: false") is False

    def test_left_boundary_anchor_blocks_prefixed_keys(self):
        """previous_/last_ 说明性字段不得误匹配（F127 round3 修复范式）。"""
        assert (
            extract_compact_active_from_user_md("previous_compact_active: true")
            is False
        )
        assert (
            extract_compact_active_from_user_md("last_compact_active: true") is False
        )

    def test_html_comment_block_stripped(self):
        """多行注释块内示例配置不得生效（F127 finding-G）。"""
        content = "<!--\n示例：\ncompact_active: true\n-->\n# USER\n"
        assert extract_compact_active_from_user_md(content) is False

    def test_invalid_value_falls_back(self):
        assert extract_compact_active_from_user_md("compact_active: truee") is False


class TestTimeParsing:
    def test_parses_valid_time(self):
        assert extract_compact_time_from_user_md("compact_time: 02:15") == "02:15"
        assert extract_compact_time_from_user_md('compact_time: "23:59"') == "23:59"

    def test_invalid_time_falls_back(self):
        assert extract_compact_time_from_user_md("compact_time: 25:00") == "03:30"
        assert extract_compact_time_from_user_md("compact_time: 3:300") == "03:30"

    def test_crontab_conversion(self):
        cfg = BehaviorCompactConfig(compact_active=True, compact_time="03:30")
        assert cfg.to_crontab() == "30 3 * * *"


class TestReusedExtractors:
    def test_timezone_and_channels_reused_from_f102(self):
        content = (
            "compact_active: true\n"
            "user_timezone: Asia/Shanghai\n"
            'summary_channels: "telegram"\n'
        )
        cfg = BehaviorCompactConfig.from_user_md(content)
        assert cfg.compact_active is True
        assert cfg.user_timezone == "Asia/Shanghai"
        assert cfg.summary_channels == frozenset({"telegram"})
