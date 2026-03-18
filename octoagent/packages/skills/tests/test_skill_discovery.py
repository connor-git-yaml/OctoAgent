"""SkillDiscovery 单元测试。

覆盖：正常解析、frontmatter 缺失必填字段跳过、同名优先级覆盖、空目录处理、非 UTF-8 文件跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.skills.discovery import (
    SkillDiscovery,
    parse_frontmatter,
    split_frontmatter,
    validate_skill,
)
from octoagent.skills.skill_models import SkillSource


# ============================================================
# split_frontmatter 测试
# ============================================================


class TestSplitFrontmatter:
    """split_frontmatter() 工具函数测试。"""

    def test_normal_split(self):
        raw = "---\nname: test\n---\n# Body"
        fm, body = split_frontmatter(raw)
        assert fm == "name: test"
        assert body == "# Body"

    def test_no_frontmatter(self):
        raw = "# Just a markdown file"
        fm, body = split_frontmatter(raw)
        assert fm == ""
        assert body == "# Just a markdown file"

    def test_only_opening_separator(self):
        raw = "---\nname: test\n# No closing separator"
        fm, body = split_frontmatter(raw)
        assert fm == ""
        assert "name: test" in body

    def test_empty_body(self):
        raw = "---\nname: test\n---\n"
        fm, body = split_frontmatter(raw)
        assert fm == "name: test"
        assert body == ""

    def test_multiline_body(self):
        raw = "---\nname: test\n---\nLine 1\nLine 2\nLine 3"
        fm, body = split_frontmatter(raw)
        assert fm == "name: test"
        assert "Line 1" in body
        assert "Line 3" in body


# ============================================================
# parse_frontmatter 测试
# ============================================================


class TestParseFrontmatter:
    """parse_frontmatter() 工具函数测试。"""

    def test_valid_yaml(self):
        result = parse_frontmatter("name: test\ndescription: A test skill")
        assert result["name"] == "test"
        assert result["description"] == "A test skill"

    def test_empty_string(self):
        result = parse_frontmatter("")
        assert result == {}

    def test_yaml_with_list(self):
        result = parse_frontmatter("tags:\n  - code\n  - python")
        assert result["tags"] == ["code", "python"]

    def test_invalid_yaml_raises(self):
        with pytest.raises(Exception):
            parse_frontmatter("name: [invalid: yaml:")

    def test_non_dict_result_raises(self):
        with pytest.raises(ValueError, match="不是 dict"):
            parse_frontmatter("- item1\n- item2")


# ============================================================
# validate_skill 测试
# ============================================================


class TestValidateSkill:
    """validate_skill() 工具函数测试。"""

    def test_valid(self):
        is_valid, err = validate_skill({"name": "test", "description": "A test"})
        assert is_valid
        assert err == ""

    def test_missing_name(self):
        is_valid, err = validate_skill({"description": "A test"})
        assert not is_valid
        assert "name" in err

    def test_missing_description(self):
        is_valid, err = validate_skill({"name": "test"})
        assert not is_valid
        assert "description" in err

    def test_empty_name(self):
        is_valid, err = validate_skill({"name": "", "description": "A test"})
        assert not is_valid

    def test_empty_dict(self):
        is_valid, err = validate_skill({})
        assert not is_valid


# ============================================================
# SkillDiscovery 集成测试
# ============================================================


@pytest.fixture
def skill_dirs(tmp_path: Path) -> dict[str, Path]:
    """创建三级 Skill 测试目录。"""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    builtin.mkdir()
    user.mkdir()
    project.mkdir()
    return {"builtin": builtin, "user": user, "project": project}


def _write_skill(
    base_dir: Path, name: str, *, description: str = "", extra_fields: str = ""
) -> Path:
    """辅助函数：在指定目录下创建 SKILL.md 文件。"""
    if not description:
        description = f"A skill named {name}"
    skill_dir = base_dir / name
    skill_dir.mkdir(exist_ok=True)
    content = f"---\nname: {name}\ndescription: {description}\n{extra_fields}---\n\n# {name}\n\nSkill body content."
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


class TestSkillDiscovery:
    """SkillDiscovery 核心功能测试。"""

    def test_scan_empty_directories(self, skill_dirs):
        """空目录扫描应返回空列表。"""
        sd = SkillDiscovery(
            builtin_dir=skill_dirs["builtin"],
            user_dir=skill_dirs["user"],
            project_dir=skill_dirs["project"],
        )
        result = sd.scan()
        assert result == []

    def test_scan_builtin_skills(self, skill_dirs):
        """扫描内置目录应正确发现 Skill。"""
        _write_skill(skill_dirs["builtin"], "summarize")
        _write_skill(skill_dirs["builtin"], "github")

        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        result = sd.scan()
        assert len(result) == 2
        names = {e.name for e in result}
        assert names == {"summarize", "github"}
        for entry in result:
            assert entry.source == SkillSource.BUILTIN
            assert entry.content  # body 不为空

    def test_priority_override(self, skill_dirs):
        """用户级 Skill 应覆盖同名内置 Skill。"""
        _write_skill(skill_dirs["builtin"], "summarize", description="builtin version")
        _write_skill(skill_dirs["user"], "summarize", description="user version")

        sd = SkillDiscovery(
            builtin_dir=skill_dirs["builtin"],
            user_dir=skill_dirs["user"],
        )
        result = sd.scan()
        assert len(result) == 1
        assert result[0].description == "user version"
        assert result[0].source == SkillSource.USER

    def test_project_overrides_all(self, skill_dirs):
        """项目级 Skill 应覆盖用户级和内置。"""
        _write_skill(skill_dirs["builtin"], "summarize", description="builtin")
        _write_skill(skill_dirs["user"], "summarize", description="user")
        _write_skill(skill_dirs["project"], "summarize", description="project")

        sd = SkillDiscovery(
            builtin_dir=skill_dirs["builtin"],
            user_dir=skill_dirs["user"],
            project_dir=skill_dirs["project"],
        )
        result = sd.scan()
        assert len(result) == 1
        assert result[0].description == "project"
        assert result[0].source == SkillSource.PROJECT

    def test_skip_missing_required_fields(self, skill_dirs):
        """缺少必填字段的 SKILL.md 应被跳过。"""
        # 缺少 description
        bad_dir = skill_dirs["builtin"] / "bad-skill"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text(
            "---\nname: bad-skill\n---\n\n# Bad", encoding="utf-8"
        )

        _write_skill(skill_dirs["builtin"], "good-skill")

        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        result = sd.scan()
        assert len(result) == 1
        assert result[0].name == "good-skill"

    def test_skip_non_utf8_files(self, skill_dirs):
        """非 UTF-8 编码文件应被跳过。"""
        bad_dir = skill_dirs["builtin"] / "bad-encoding"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_bytes(b"\xff\xfe\x00\x01invalid")

        _write_skill(skill_dirs["builtin"], "good-skill")

        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        result = sd.scan()
        assert len(result) == 1
        assert result[0].name == "good-skill"

    def test_skip_invalid_yaml(self, skill_dirs):
        """YAML 语法错误的文件应被跳过。"""
        bad_dir = skill_dirs["builtin"] / "yaml-error"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_text(
            "---\nname: [invalid: yaml:\n---\n\n# Bad", encoding="utf-8"
        )

        _write_skill(skill_dirs["builtin"], "good-skill")

        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        result = sd.scan()
        assert len(result) == 1

    def test_get_by_name(self, skill_dirs):
        """get() 应从缓存返回指定 Skill。"""
        _write_skill(skill_dirs["builtin"], "summarize")
        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()

        entry = sd.get("summarize")
        assert entry is not None
        assert entry.name == "summarize"

        assert sd.get("nonexistent") is None

    def test_list_items(self, skill_dirs):
        """list_items() 应返回排序的摘要列表。"""
        _write_skill(skill_dirs["builtin"], "summarize")
        _write_skill(skill_dirs["builtin"], "github")

        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()

        items = sd.list_items()
        assert len(items) == 2
        assert items[0].name == "github"  # 按字母排序
        assert items[1].name == "summarize"

    def test_refresh_rescans(self, skill_dirs):
        """refresh() 应重新扫描并更新缓存。"""
        _write_skill(skill_dirs["builtin"], "summarize")
        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()
        assert len(sd.list_items()) == 1

        # 添加新 Skill 后 refresh
        _write_skill(skill_dirs["builtin"], "github")
        sd.refresh()
        assert len(sd.list_items()) == 2

    def test_none_directories(self):
        """所有目录为 None 时应正常工作。"""
        sd = SkillDiscovery()
        result = sd.scan()
        assert result == []

    def test_nonexistent_directory(self, tmp_path):
        """不存在的目录应被安全跳过。"""
        sd = SkillDiscovery(builtin_dir=tmp_path / "nonexistent")
        result = sd.scan()
        assert result == []

    def test_tags_and_optional_fields(self, skill_dirs):
        """可选字段（tags, version, author）应正确解析。"""
        _write_skill(
            skill_dirs["builtin"],
            "test-skill",
            extra_fields="version: 1.0.0\nauthor: Connor\ntags:\n  - code\n  - python\n",
        )
        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()

        entry = sd.get("test-skill")
        assert entry is not None
        assert entry.version == "1.0.0"
        assert entry.author == "Connor"
        assert entry.tags == ["code", "python"]

    def test_directory_without_skill_md(self, skill_dirs):
        """有子目录但没有 SKILL.md 的应被安全跳过。"""
        empty_skill_dir = skill_dirs["builtin"] / "empty-skill"
        empty_skill_dir.mkdir()

        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        result = sd.scan()
        assert result == []

    def test_tools_required_parsing(self, skill_dirs):
        """tools_required 字段应从 frontmatter 正确解析。"""
        _write_skill(
            skill_dirs["builtin"],
            "coding-agent",
            extra_fields="tools_required:\n  - docker.run\n  - terminal.exec\n",
        )
        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()

        entry = sd.get("coding-agent")
        assert entry is not None
        assert entry.tools_required == ["docker.run", "terminal.exec"]

    def test_tools_required_empty_or_missing(self, skill_dirs):
        """未声明 tools_required 的 Skill 应默认为空列表。"""
        _write_skill(skill_dirs["builtin"], "simple-skill")
        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()

        entry = sd.get("simple-skill")
        assert entry is not None
        assert entry.tools_required == []

    def test_tools_required_nonexistent_tools(self, skill_dirs):
        """tools_required 包含不存在的工具名仍可解析（运行时记录警告）。"""
        _write_skill(
            skill_dirs["builtin"],
            "test-skill",
            extra_fields="tools_required:\n  - nonexistent.tool\n  - another.fake\n",
        )
        sd = SkillDiscovery(builtin_dir=skill_dirs["builtin"])
        sd.scan()

        entry = sd.get("test-skill")
        assert entry is not None
        assert len(entry.tools_required) == 2
        assert "nonexistent.tool" in entry.tools_required
        assert "another.fake" in entry.tools_required
