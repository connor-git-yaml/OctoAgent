"""SkillMdEntry / SkillListItem 模型单元测试。

验证 name 正则约束、description 长度、默认值、to_list_item() 投影。
"""

from __future__ import annotations

import pytest

from octoagent.skills.skill_models import SkillListItem, SkillMdEntry, SkillSource


class TestSkillMdEntry:
    """SkillMdEntry 模型测试。"""

    def test_valid_creation(self):
        """合法参数应成功创建。"""
        entry = SkillMdEntry(name="test-skill", description="A test skill")
        assert entry.name == "test-skill"
        assert entry.description == "A test skill"
        assert entry.source == SkillSource.BUILTIN
        assert entry.tags == []
        assert entry.content == ""
        assert entry.version == ""

    def test_name_lowercase_with_hyphens(self):
        """名称可包含小写字母、数字、连字符。"""
        entry = SkillMdEntry(name="coding-agent", description="Test")
        assert entry.name == "coding-agent"

    def test_name_numeric(self):
        """名称可包含纯数字部分。"""
        entry = SkillMdEntry(name="nano-pdf", description="Test")
        assert entry.name == "nano-pdf"

    def test_name_single_word(self):
        """名称可以是单个单词。"""
        entry = SkillMdEntry(name="weather", description="Test")
        assert entry.name == "weather"

    def test_name_with_numbers(self):
        """名称可包含数字。"""
        entry = SkillMdEntry(name="tool2", description="Test")
        assert entry.name == "tool2"

    def test_name_invalid_uppercase(self):
        """大写字母应被拒绝。"""
        with pytest.raises(ValueError, match="格式无效"):
            SkillMdEntry(name="TestSkill", description="Test")

    def test_name_invalid_underscore(self):
        """下划线应被拒绝。"""
        with pytest.raises(ValueError, match="格式无效"):
            SkillMdEntry(name="test_skill", description="Test")

    def test_name_invalid_starting_hyphen(self):
        """以连字符开头应被拒绝。"""
        with pytest.raises(ValueError, match="格式无效"):
            SkillMdEntry(name="-test", description="Test")

    def test_name_invalid_ending_hyphen(self):
        """以连字符结尾应被拒绝。"""
        with pytest.raises(ValueError, match="格式无效"):
            SkillMdEntry(name="test-", description="Test")

    def test_name_invalid_double_hyphen(self):
        """连续连字符应被拒绝。"""
        with pytest.raises(ValueError, match="格式无效"):
            SkillMdEntry(name="test--skill", description="Test")

    def test_name_empty(self):
        """空名称应被拒绝。"""
        with pytest.raises(ValueError):
            SkillMdEntry(name="", description="Test")

    def test_name_too_long(self):
        """超长名称应被拒绝。"""
        with pytest.raises(ValueError):
            SkillMdEntry(name="a" * 65, description="Test")

    def test_description_empty(self):
        """空描述应被拒绝。"""
        with pytest.raises(ValueError):
            SkillMdEntry(name="test", description="")

    def test_description_max_length(self):
        """描述不应超过 1024 字符。"""
        with pytest.raises(ValueError):
            SkillMdEntry(name="test", description="a" * 1025)

    def test_to_list_item(self):
        """to_list_item() 应正确投影。"""
        entry = SkillMdEntry(
            name="summarize",
            description="Summarize content",
            version="1.0.0",
            tags=["summarize", "url"],
            source=SkillSource.USER,
            content="# Full body content",
        )
        item = entry.to_list_item()
        assert isinstance(item, SkillListItem)
        assert item.name == "summarize"
        assert item.description == "Summarize content"
        assert item.version == "1.0.0"
        assert item.tags == ["summarize", "url"]
        assert item.source == SkillSource.USER

    def test_default_values(self):
        """默认值应正确设置。"""
        entry = SkillMdEntry(name="test", description="Test")
        assert entry.version == ""
        assert entry.author == ""
        assert entry.tags == []
        assert entry.trigger_patterns == []
        assert entry.tools_required == []
        assert entry.source == SkillSource.BUILTIN
        assert entry.source_path == ""
        assert entry.content == ""
        assert entry.raw_frontmatter == {}
        assert entry.metadata == {}

    def test_tools_required_with_values(self):
        """tools_required 正常列表应正确解析。"""
        entry = SkillMdEntry(
            name="coding-agent",
            description="A coding agent skill",
            tools_required=["filesystem.write_text", "terminal.exec", "docker.run"],
        )
        assert entry.tools_required == [
            "filesystem.write_text",
            "terminal.exec",
            "docker.run",
        ]

    def test_tools_required_empty_list(self):
        """tools_required 空列表应正确处理。"""
        entry = SkillMdEntry(
            name="summarize",
            description="Summarize content",
            tools_required=[],
        )
        assert entry.tools_required == []

    def test_tools_required_nonexistent_tool_names(self):
        """tools_required 包含不存在的工具名仍可解析（验证在运行时进行）。"""
        entry = SkillMdEntry(
            name="test-skill",
            description="Test",
            tools_required=["nonexistent.tool", "another.fake.tool"],
        )
        assert len(entry.tools_required) == 2
        assert "nonexistent.tool" in entry.tools_required


class TestSkillListItem:
    """SkillListItem 模型测试。"""

    def test_valid_creation(self):
        """合法参数应成功创建。"""
        item = SkillListItem(
            name="test",
            description="A test",
            source=SkillSource.BUILTIN,
        )
        assert item.name == "test"
        assert item.tags == []
        assert item.version == ""

    def test_with_all_fields(self):
        """所有字段应正确设置。"""
        item = SkillListItem(
            name="github",
            description="GitHub operations",
            tags=["git", "github"],
            source=SkillSource.USER,
            version="2.0.0",
        )
        assert item.name == "github"
        assert item.tags == ["git", "github"]
        assert item.source == SkillSource.USER
        assert item.version == "2.0.0"


class TestSkillSource:
    """SkillSource 枚举测试。"""

    def test_values(self):
        assert SkillSource.BUILTIN == "builtin"
        assert SkillSource.USER == "user"
        assert SkillSource.PROJECT == "project"

    def test_from_string(self):
        assert SkillSource("builtin") == SkillSource.BUILTIN
        assert SkillSource("user") == SkillSource.USER
        assert SkillSource("project") == SkillSource.PROJECT
