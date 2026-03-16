"""SkillsTool 单元测试。

验证 list 返回格式、load 成功/失败（Skill 不存在）、重复 load 同一 Skill 不重复添加、unload。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.skills.discovery import SkillDiscovery
from octoagent.skills.tools import SkillsTool


def _create_skill_dir(base: Path, name: str, description: str = "") -> None:
    """辅助函数：在 base 下创建 SKILL.md。"""
    if not description:
        description = f"Description for {name}"
    skill_dir = base / name
    skill_dir.mkdir(exist_ok=True)
    content = (
        f"---\nname: {name}\n"
        f"description: {description}\n"
        f"version: 1.0.0\n"
        f"tags:\n  - test\n"
        f"---\n\n"
        f"# {name}\n\nThis is the body of {name}."
    )
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


@pytest.fixture
def discovery_with_skills(tmp_path: Path) -> SkillDiscovery:
    """创建包含 2 个 Skill 的 SkillDiscovery。"""
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    _create_skill_dir(builtin, "summarize", "Summarize text content")
    _create_skill_dir(builtin, "github", "GitHub operations via gh CLI")

    sd = SkillDiscovery(builtin_dir=builtin)
    sd.scan()
    return sd


class TestSkillsToolList:
    """list action 测试。"""

    @pytest.mark.asyncio
    async def test_list_returns_all_skills(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(action="list")
        assert "Available skills (2):" in result
        assert "summarize" in result
        assert "github" in result
        assert "Tip:" in result

    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path):
        sd = SkillDiscovery(builtin_dir=tmp_path / "empty")
        sd.scan()
        tool = SkillsTool(sd)
        result = await tool.execute(action="list")
        assert result == "No skills available."


class TestSkillsToolLoad:
    """load action 测试。"""

    @pytest.mark.asyncio
    async def test_load_success(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        metadata: dict = {}
        result = await tool.execute(
            action="load", name="summarize", session_metadata=metadata
        )
        assert "Loaded skill 'summarize' into current session." in result
        assert "summarize" in metadata["loaded_skill_names"]
        assert "This is the body of summarize" in result

    @pytest.mark.asyncio
    async def test_load_not_found(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(
            action="load", name="nonexistent", session_metadata={}
        )
        assert "Error: skill not found" in result
        assert "nonexistent" in result

    @pytest.mark.asyncio
    async def test_load_missing_name(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(action="load", name="", session_metadata={})
        assert "Error: 'name' is required" in result

    @pytest.mark.asyncio
    async def test_load_idempotent(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        metadata: dict = {"loaded_skill_names": ["summarize"]}
        result = await tool.execute(
            action="load", name="summarize", session_metadata=metadata
        )
        assert "already loaded" in result
        # 不重复添加
        assert metadata["loaded_skill_names"].count("summarize") == 1

    @pytest.mark.asyncio
    async def test_load_multiple_skills(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        metadata: dict = {}
        await tool.execute(action="load", name="summarize", session_metadata=metadata)
        await tool.execute(action="load", name="github", session_metadata=metadata)
        assert metadata["loaded_skill_names"] == ["summarize", "github"]

    @pytest.mark.asyncio
    async def test_load_none_metadata(self, discovery_with_skills):
        """session_metadata 为 None 时也应正常工作。"""
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(action="load", name="summarize", session_metadata=None)
        # 不应崩溃，但 metadata 不会被更新（无持久化）
        assert "Loaded skill 'summarize'" in result


class TestSkillsToolUnload:
    """unload action 测试。"""

    @pytest.mark.asyncio
    async def test_unload_success(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        metadata: dict = {"loaded_skill_names": ["summarize", "github"]}
        result = await tool.execute(
            action="unload", name="summarize", session_metadata=metadata
        )
        assert "unloaded" in result
        assert metadata["loaded_skill_names"] == ["github"]

    @pytest.mark.asyncio
    async def test_unload_not_loaded(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(
            action="unload", name="summarize", session_metadata={"loaded_skill_names": []}
        )
        assert "not loaded" in result

    @pytest.mark.asyncio
    async def test_unload_missing_name(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(action="unload", name="", session_metadata={})
        assert "Error: 'name' is required" in result


class TestSkillsToolUnknownAction:
    """未知 action 测试。"""

    @pytest.mark.asyncio
    async def test_unknown_action(self, discovery_with_skills):
        tool = SkillsTool(discovery_with_skills)
        result = await tool.execute(action="invalid")
        assert "Error: unknown action" in result
