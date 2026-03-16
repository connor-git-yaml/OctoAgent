"""Skill 多轮对话持续生效集成测试 -- Feature 057 T057

测试内容:
1. 在 session 中加载 2 个 Skill，验证 system prompt 注入正确
2. 连续 3 轮 system prompt 均包含两个 Skill 的 content
3. 新 session 的 metadata 不继承已加载的 Skill
4. 上下文预算超出时返回警告
5. unload 操作后 system prompt 不再包含该 Skill
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from octoagent.skills import SkillDiscovery
from octoagent.skills.tools import SkillsTool


def _create_test_skills(tmp_path: Path) -> SkillDiscovery:
    """创建测试用的 SkillDiscovery，包含 2 个测试 Skill。"""
    skills_dir = tmp_path / "skills"

    # Skill A
    skill_a_dir = skills_dir / "test-skill-a"
    skill_a_dir.mkdir(parents=True)
    (skill_a_dir / "SKILL.md").write_text(
        "---\n"
        "name: test-skill-a\n"
        "description: Test Skill A for integration test\n"
        "version: '1.0.0'\n"
        "tags:\n"
        "  - test\n"
        "---\n\n"
        "# Test Skill A\n\n"
        "This is the instruction body for Skill A.\n"
        "It should appear in the system prompt when loaded.\n",
        encoding="utf-8",
    )

    # Skill B
    skill_b_dir = skills_dir / "test-skill-b"
    skill_b_dir.mkdir(parents=True)
    (skill_b_dir / "SKILL.md").write_text(
        "---\n"
        "name: test-skill-b\n"
        "description: Test Skill B for integration test\n"
        "version: '2.0.0'\n"
        "tags:\n"
        "  - test\n"
        "  - integration\n"
        "---\n\n"
        "# Test Skill B\n\n"
        "This is the instruction body for Skill B.\n"
        "Different content from Skill A.\n",
        encoding="utf-8",
    )

    discovery = SkillDiscovery(builtin_dir=skills_dir)
    discovery.scan()
    return discovery


def _simulate_system_prompt_build(
    discovery: SkillDiscovery,
    metadata: dict[str, Any],
) -> str:
    """模拟 LLMService._build_loaded_skills_context 的行为。

    从 metadata["loaded_skill_names"] 读取已加载 Skill，
    构建注入 system prompt 的文本。
    """
    loaded_names = metadata.get("loaded_skill_names", [])
    if not isinstance(loaded_names, list) or not loaded_names:
        return ""

    sections: list[str] = []
    for name in loaded_names:
        entry = discovery.get(name)
        if entry is None:
            continue
        sections.append(
            f"--- Skill: {entry.name} ---\n{entry.content}\n--- End Skill: {entry.name} ---"
        )

    if not sections:
        return ""
    return "\n\n".join(sections)


class TestSkillSessionPersistence:
    """Skill 在 session 内多轮持续生效的集成测试。"""

    @pytest.mark.asyncio
    async def test_loaded_skills_persist_across_turns(self, tmp_path: Path):
        """加载 2 个 Skill 后，连续 3 轮 system prompt 均应包含两个 Skill 的 content。"""
        discovery = _create_test_skills(tmp_path)
        tool = SkillsTool(discovery)

        # 模拟一个 session 的 metadata
        session_metadata: dict[str, Any] = {}

        # 加载 Skill A
        result_a = await tool.execute(
            action="load", name="test-skill-a", session_metadata=session_metadata
        )
        assert "Loaded skill 'test-skill-a'" in result_a

        # 加载 Skill B
        result_b = await tool.execute(
            action="load", name="test-skill-b", session_metadata=session_metadata
        )
        assert "Loaded skill 'test-skill-b'" in result_b

        # 验证 metadata 中有两个 Skill
        assert session_metadata["loaded_skill_names"] == ["test-skill-a", "test-skill-b"]

        # 模拟连续 3 轮 system prompt 构建
        for turn in range(3):
            prompt = _simulate_system_prompt_build(discovery, session_metadata)
            assert "Test Skill A" in prompt, f"Turn {turn}: Skill A content missing"
            assert "Test Skill B" in prompt, f"Turn {turn}: Skill B content missing"
            assert prompt.index("Skill: test-skill-a") < prompt.index(
                "Skill: test-skill-b"
            ), f"Turn {turn}: Skill order incorrect"

    @pytest.mark.asyncio
    async def test_new_session_has_empty_loaded_skills(self, tmp_path: Path):
        """新 session 的 metadata 不应继承上一 session 的 loaded_skill_names。"""
        discovery = _create_test_skills(tmp_path)
        tool = SkillsTool(discovery)

        # Session 1: 加载 Skill
        session1_metadata: dict[str, Any] = {}
        await tool.execute(
            action="load", name="test-skill-a", session_metadata=session1_metadata
        )
        assert "test-skill-a" in session1_metadata.get("loaded_skill_names", [])

        # Session 2: 新 session（空 metadata）
        session2_metadata: dict[str, Any] = {}
        prompt = _simulate_system_prompt_build(discovery, session2_metadata)
        assert prompt == "", "新 session 不应有已加载的 Skill"
        assert session2_metadata.get("loaded_skill_names", []) == []

    @pytest.mark.asyncio
    async def test_unload_removes_skill_from_prompt(self, tmp_path: Path):
        """unload 操作后 system prompt 不再包含该 Skill。"""
        discovery = _create_test_skills(tmp_path)
        tool = SkillsTool(discovery)

        session_metadata: dict[str, Any] = {}

        # 加载两个 Skill
        await tool.execute(
            action="load", name="test-skill-a", session_metadata=session_metadata
        )
        await tool.execute(
            action="load", name="test-skill-b", session_metadata=session_metadata
        )

        # 验证两个都在
        prompt = _simulate_system_prompt_build(discovery, session_metadata)
        assert "Test Skill A" in prompt
        assert "Test Skill B" in prompt

        # unload Skill A
        result = await tool.execute(
            action="unload", name="test-skill-a", session_metadata=session_metadata
        )
        assert "unloaded" in result.lower()

        # 验证 Skill A 不在了，Skill B 还在
        prompt = _simulate_system_prompt_build(discovery, session_metadata)
        assert "Test Skill A" not in prompt
        assert "Test Skill B" in prompt

    @pytest.mark.asyncio
    async def test_context_budget_warning(self, tmp_path: Path):
        """超出上下文预算阈值时应返回警告。"""
        # 创建一个 body 非常大的 Skill
        skills_dir = tmp_path / "skills"
        large_skill_dir = skills_dir / "large-skill"
        large_skill_dir.mkdir(parents=True)
        # 创建一个 55KB 的 SKILL.md body
        large_body = "x" * 55_000
        (large_skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: large-skill\n"
            "description: A very large skill for testing\n"
            "---\n\n"
            f"{large_body}\n",
            encoding="utf-8",
        )

        discovery = SkillDiscovery(builtin_dir=skills_dir)
        discovery.scan()
        tool = SkillsTool(discovery)

        session_metadata: dict[str, Any] = {}

        result = await tool.execute(
            action="load", name="large-skill", session_metadata=session_metadata
        )
        # 55KB > 50KB 预算，应返回警告
        assert "warning" in result.lower() or "exceed" in result.lower()
        # 不应被添加到 loaded_skill_names
        assert "large-skill" not in session_metadata.get("loaded_skill_names", [])

    @pytest.mark.asyncio
    async def test_idempotent_load(self, tmp_path: Path):
        """重复 load 同一个 Skill 不应导致重复。"""
        discovery = _create_test_skills(tmp_path)
        tool = SkillsTool(discovery)

        session_metadata: dict[str, Any] = {}

        # 加载两次
        await tool.execute(
            action="load", name="test-skill-a", session_metadata=session_metadata
        )
        result = await tool.execute(
            action="load", name="test-skill-a", session_metadata=session_metadata
        )

        assert "already loaded" in result.lower()
        assert session_metadata["loaded_skill_names"].count("test-skill-a") == 1
