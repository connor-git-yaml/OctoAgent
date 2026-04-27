"""Feature 082 P3：UserMdRenderer 单元 + 集成测试。

覆盖：
- 默认 OwnerProfile（全空）→ 不渲染（fallback 到模板）
- 仅有 preferred_address → 渲染真实内容
- 完整 OwnerProfile → 含所有字段
- 列表字段（interaction_preferences / boundary_notes）正确格式化
- 历史伪默认 "你" 视为未填充
- write 原子写入 + 默认路径正确
- render_and_write 跳过未填充场景
- BootstrapSessionOrchestrator 集成：完成时写 USER.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from octoagent.core.models.agent_context import (
    AgentProfile,
    AgentProfileScope,
    BootstrapSession,
    BootstrapSessionStatus,
    OwnerProfile,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.bootstrap_orchestrator import BootstrapSessionOrchestrator
from octoagent.gateway.services.user_md_renderer import (
    RenderResult,
    UserMdRenderer,
    _default_template,
)


# ────────────── render() 单元测试 ──────────────


def test_render_none_profile_returns_template(tmp_path: Path) -> None:
    """OwnerProfile 为 None → 返回静态模板（fallback）。"""
    renderer = UserMdRenderer(tmp_path)
    result = renderer.render(None)
    assert result.is_filled is False
    assert result.fields_used == []
    assert "待引导时填写" in result.content


def test_render_default_profile_returns_template(tmp_path: Path) -> None:
    """全默认 OwnerProfile（preferred_address="" 等）→ fallback 模板。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(owner_profile_id="x")  # 全默认
    result = renderer.render(profile)
    assert result.is_filled is False
    assert result.fields_used == []


def test_render_legacy_pseudo_default_treated_as_unfilled(tmp_path: Path) -> None:
    """preferred_address='你' 历史伪默认应视为未填充 → fallback。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(owner_profile_id="x", preferred_address="你")
    result = renderer.render(profile)
    assert result.is_filled is False
    assert "preferred_address" not in result.fields_used


def test_render_real_address_produces_filled_content(tmp_path: Path) -> None:
    """preferred_address='Connor' → 真实内容。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(owner_profile_id="x-abcdef123456", preferred_address="Connor")
    result = renderer.render(profile)
    assert result.is_filled is True
    assert "preferred_address" in result.fields_used
    assert "Connor" in result.content
    assert "待引导时填写" not in result.content
    assert "OwnerProfile x-abcdef" in result.content


def test_render_full_profile_includes_all_sections(tmp_path: Path) -> None:
    """完整 OwnerProfile 各字段都进 markdown。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(
        owner_profile_id="x",
        preferred_address="Connor",
        timezone="Asia/Shanghai",
        locale="zh-CN",  # 虽然是默认值，但 timezone/preferred 已让 is_filled=True
        working_style="偏好直接结论，避免冗长背景",
        interaction_preferences=["回答前先对齐 project 事实", "用 markdown 列表"],
        boundary_notes=["不要主动提家庭话题"],
    )
    result = renderer.render(profile)
    assert result.is_filled is True
    assert set(result.fields_used) >= {
        "preferred_address",
        "timezone",
        "working_style",
        "interaction_preferences",
        "boundary_notes",
    }
    assert "Connor" in result.content
    assert "Asia/Shanghai" in result.content
    assert "偏好直接结论" in result.content
    assert "回答前先对齐 project 事实" in result.content
    assert "用 markdown 列表" in result.content
    assert "不要主动提家庭话题" in result.content


def test_render_empty_lists_show_unset_fallback(tmp_path: Path) -> None:
    """interaction_preferences/boundary_notes 为空 → 显示"未设置"。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(owner_profile_id="x", preferred_address="Connor")
    result = renderer.render(profile)
    assert result.is_filled is True
    # 模板里这些为空时会显示"（未设置）"
    assert "（未设置）" in result.content


# ────────────── write() 单元测试 ──────────────


def test_write_creates_target_file(tmp_path: Path) -> None:
    """write 原子写入到 ``<root>/behavior/system/USER.md``。"""
    renderer = UserMdRenderer(tmp_path)
    written_path = renderer.write("# hello world\n")
    assert written_path == tmp_path / "behavior" / "system" / "USER.md"
    assert written_path.exists()
    assert written_path.read_text(encoding="utf-8") == "# hello world\n"


def test_write_explicit_target(tmp_path: Path) -> None:
    """write 支持显式 target 路径。"""
    renderer = UserMdRenderer(tmp_path)
    custom_target = tmp_path / "custom" / "USER.md"
    written = renderer.write("custom content", target=custom_target)
    assert written == custom_target
    assert custom_target.read_text(encoding="utf-8") == "custom content"


def test_write_overwrites_existing_file(tmp_path: Path) -> None:
    """write 覆盖已有文件（原子替换）。"""
    target = tmp_path / "behavior" / "system" / "USER.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old content", encoding="utf-8")

    renderer = UserMdRenderer(tmp_path)
    renderer.write("new content")
    assert target.read_text(encoding="utf-8") == "new content"


# ────────────── render_and_write() 集成测试 ──────────────


def test_render_and_write_skips_unfilled_profile(tmp_path: Path) -> None:
    """全默认 profile → 不写文件（避免覆盖用户手工 USER.md）。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(owner_profile_id="x")  # 全默认
    result, written_path = renderer.render_and_write(profile)
    assert result.is_filled is False
    assert written_path is None
    assert not (tmp_path / "behavior" / "system" / "USER.md").exists()


def test_render_and_write_writes_when_filled(tmp_path: Path) -> None:
    """有真实数据 → 写文件并返回路径。"""
    renderer = UserMdRenderer(tmp_path)
    profile = OwnerProfile(owner_profile_id="x", preferred_address="Connor")
    result, written_path = renderer.render_and_write(profile)
    assert result.is_filled is True
    assert written_path is not None
    assert written_path == tmp_path / "behavior" / "system" / "USER.md"
    content = written_path.read_text(encoding="utf-8")
    assert "Connor" in content


# ────────────── BootstrapSessionOrchestrator 集成 USER.md 写入 ──────────────


@pytest.mark.asyncio
async def test_orchestrator_writes_user_md_on_complete(tmp_path: Path) -> None:
    """**P3 集成**：complete_bootstrap 时如果 OwnerProfile 被回填 → 自动写 USER.md。"""
    store_group = await create_store_group(
        str(tmp_path / "p3-orch.db"), str(tmp_path / "p3-orch-artifacts")
    )
    try:
        # Seed default profile + bootstrap session
        profile = OwnerProfile(owner_profile_id="owner-p3")
        await store_group.agent_context_store.save_owner_profile(profile)

        agent_profile = AgentProfile(
            profile_id="agent-p3",
            scope=AgentProfileScope.PROJECT,
            project_id="project-p3",
            name="P3 Agent",
        )
        await store_group.agent_context_store.save_agent_profile(agent_profile)

        session = BootstrapSession(
            bootstrap_id="bs-p3",
            project_id="project-p3",
            owner_profile_id="owner-p3",
            agent_profile_id=agent_profile.profile_id,
            status=BootstrapSessionStatus.PENDING,
        )
        await store_group.agent_context_store.save_bootstrap_session(session)
        await store_group.conn.commit()

        # 调 orchestrator
        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap(
            "bs-p3",
            profile_updates={
                "preferred_address": "Connor",
                "working_style": "偏好直接结论",
            },
        )
        await store_group.conn.commit()

        # 验证 USER.md 被写入
        assert result.user_md_written is True, (
            f"P3 期望 user_md_written=True；result={result.to_payload()}"
        )
        assert result.user_md_path is not None
        user_md_file = tmp_path / "behavior" / "system" / "USER.md"
        assert user_md_file.exists()
        content = user_md_file.read_text(encoding="utf-8")
        assert "Connor" in content
        assert "偏好直接结论" in content
        assert "待引导时填写" not in content
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_orchestrator_skips_user_md_when_profile_unchanged(tmp_path: Path) -> None:
    """profile 未被更新（如全部 fields_skipped）→ 不写 USER.md。"""
    store_group = await create_store_group(
        str(tmp_path / "p3-skip.db"), str(tmp_path / "p3-skip-artifacts")
    )
    try:
        # Seed profile 已有用户显式值
        profile = OwnerProfile(owner_profile_id="owner-skip", preferred_address="UserChosen")
        await store_group.agent_context_store.save_owner_profile(profile)

        agent_profile = AgentProfile(
            profile_id="agent-skip",
            scope=AgentProfileScope.PROJECT,
            project_id="project-skip",
            name="Skip",
        )
        await store_group.agent_context_store.save_agent_profile(agent_profile)

        session = BootstrapSession(
            bootstrap_id="bs-skip",
            project_id="project-skip",
            owner_profile_id="owner-skip",
            agent_profile_id=agent_profile.profile_id,
            status=BootstrapSessionStatus.PENDING,
        )
        await store_group.agent_context_store.save_bootstrap_session(session)
        await store_group.conn.commit()

        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap(
            "bs-skip",
            profile_updates={"preferred_address": "Override"},  # 用户已显式 → skipped
        )

        assert result.owner_profile_updated is False
        assert result.user_md_written is False, (
            "profile 未更新时不应写 USER.md（避免覆盖现有内容）"
        )
        assert result.user_md_path is None
    finally:
        await store_group.conn.close()


# ────────────── _default_template ──────────────


def test_default_template_contains_placeholders() -> None:
    """fallback 模板必须含占位符标识词（被 _user_md_is_filled 检测）。"""
    template = _default_template()
    assert "待引导时填写" in template
    assert "待了解后补充" in template
