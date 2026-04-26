"""Feature 082 P1：BootstrapIntegrityChecker 单元测试。

覆盖：
- 全新用户（无 state + 默认 profile + USER.md 占位）→ 未实质完成
- 误标用户（marker 但 profile/USER.md 全默认）→ 未实质完成（关键修复点）
- 真完成用户（marker + profile 实质填充）→ 实质完成
- 真完成用户（marker + USER.md 已填充）→ 实质完成
- owner_profile_is_filled 各种字段触发条件
- ``preferred_address: '你'`` 历史伪默认不算填充
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from octoagent.core.behavior_workspace import save_onboarding_state, OnboardingState
from octoagent.core.models.agent_context import OwnerProfile
from octoagent.gateway.services.bootstrap_integrity import (
    BootstrapIntegrityChecker,
    IntegrityReport,
    owner_profile_is_filled,
)


def _seed_completed_state(project_root: Path) -> None:
    """模拟 .onboarding-state.json 已标记 completed（不论真假）。"""
    state = OnboardingState(
        bootstrap_seeded_at=datetime.now(tz=UTC).isoformat(),
        onboarding_completed_at=datetime.now(tz=UTC).isoformat(),
    )
    save_onboarding_state(project_root, state)


def _write_filled_user_md(project_root: Path) -> None:
    """写入实质填充的 USER.md（无占位符）。"""
    user_md_dir = project_root / "behavior" / "system"
    user_md_dir.mkdir(parents=True, exist_ok=True)
    user_md_dir.joinpath("USER.md").write_text(
        "## 用户画像\n\n### 基本信息\n- **称呼**: Connor\n- **时区**: Asia/Shanghai\n",
        encoding="utf-8",
    )


def _write_placeholder_user_md(project_root: Path) -> None:
    """写入仍含占位符的 USER.md（模拟新装但未引导）。"""
    user_md_dir = project_root / "behavior" / "system"
    user_md_dir.mkdir(parents=True, exist_ok=True)
    user_md_dir.joinpath("USER.md").write_text(
        "## 用户画像\n- **称呼**: （待引导时填写——用户希望被称呼的名字或昵称）\n",
        encoding="utf-8",
    )


# ────────────── owner_profile_is_filled 单元测试 ──────────────


def test_owner_profile_filled_none_is_false() -> None:
    assert owner_profile_is_filled(None) is False


def test_owner_profile_filled_default_is_false() -> None:
    """Feature 082 P0 默认值：preferred_address='' / working_style='' → 未填充。"""
    p = OwnerProfile(owner_profile_id="x")
    assert owner_profile_is_filled(p) is False


def test_owner_profile_filled_legacy_pseudo_default_is_false() -> None:
    """``preferred_address='你'`` 是历史伪默认，不算实质填充。"""
    p = OwnerProfile(owner_profile_id="x", preferred_address="你")
    assert owner_profile_is_filled(p) is False


def test_owner_profile_filled_real_address_is_true() -> None:
    p = OwnerProfile(owner_profile_id="x", preferred_address="Connor")
    assert owner_profile_is_filled(p) is True


def test_owner_profile_filled_working_style_is_true() -> None:
    p = OwnerProfile(owner_profile_id="x", working_style="偏好直接结论")
    assert owner_profile_is_filled(p) is True


def test_owner_profile_filled_interaction_preferences_is_true() -> None:
    p = OwnerProfile(owner_profile_id="x", interaction_preferences=["回答前先对齐"])
    assert owner_profile_is_filled(p) is True


def test_owner_profile_filled_dict_compat() -> None:
    """兼容 dict 输入（来自 store row）。"""
    assert owner_profile_is_filled({"preferred_address": "Connor"}) is True
    assert owner_profile_is_filled({"preferred_address": "你"}) is False
    assert owner_profile_is_filled({}) is False


# ────────────── IntegrityReport.is_substantively_completed ──────────────


def test_report_no_marker_is_not_completed() -> None:
    r = IntegrityReport(has_onboarding_marker=False, owner_profile_filled=True, user_md_filled=True)
    assert r.is_substantively_completed is False, "marker 缺失即视为未完成"


def test_report_marker_only_is_not_completed() -> None:
    """Feature 082 P1 关键修复：仅 marker 不够（即历史误标场景）。"""
    r = IntegrityReport(has_onboarding_marker=True, owner_profile_filled=False, user_md_filled=False)
    assert r.is_substantively_completed is False, (
        "仅 onboarding_completed_at 时间戳不足以视为实质完成；"
        "Bootstrap 引导应重新跑（修复 _detect_legacy_onboarding_completion 误标）"
    )


def test_report_marker_plus_profile_is_completed() -> None:
    r = IntegrityReport(has_onboarding_marker=True, owner_profile_filled=True, user_md_filled=False)
    assert r.is_substantively_completed is True


def test_report_marker_plus_user_md_is_completed() -> None:
    r = IntegrityReport(has_onboarding_marker=True, owner_profile_filled=False, user_md_filled=True)
    assert r.is_substantively_completed is True


# ────────────── BootstrapIntegrityChecker 集成测试 ──────────────


@pytest.mark.asyncio
async def test_checker_fresh_install_not_completed(tmp_path: Path) -> None:
    """全新装：无 state、profile 默认、USER.md 未写入 → False。"""
    checker = BootstrapIntegrityChecker(tmp_path)
    report = await checker.build_report(owner_profile=OwnerProfile(owner_profile_id="x"))
    assert report.has_onboarding_marker is False
    assert report.owner_profile_filled is False
    assert report.user_md_filled is False
    assert report.is_substantively_completed is False


@pytest.mark.asyncio
async def test_checker_misflagged_user_detects_substantive_incompleteness(
    tmp_path: Path,
) -> None:
    """Feature 082 P1 核心修复场景：onboarding 被误标 + profile/USER.md 全默认。

    用户实测复现：preferred_address: '你' + USER.md 占位符 + state 标记 completed。
    P1 必须识别这种情况为"未实质完成"，让 Bootstrap 引导重新跑。
    """
    _seed_completed_state(tmp_path)
    _write_placeholder_user_md(tmp_path)

    checker = BootstrapIntegrityChecker(tmp_path)
    report = await checker.build_report(
        owner_profile=OwnerProfile(owner_profile_id="x", preferred_address="你"),
    )
    assert report.has_onboarding_marker is True, "marker 已被设置（误标）"
    assert report.owner_profile_filled is False, "preferred_address='你' 是伪默认"
    assert report.user_md_filled is False, "USER.md 仍含占位符"
    assert report.is_substantively_completed is False, (
        "P1 关键修复：误标 + 全默认 → 必须识别为未实质完成"
    )


@pytest.mark.asyncio
async def test_checker_real_completion_with_filled_profile(tmp_path: Path) -> None:
    """marker + profile 实质填充 → 视为已完成（即使 USER.md 还没生成）。"""
    _seed_completed_state(tmp_path)

    checker = BootstrapIntegrityChecker(tmp_path)
    report = await checker.build_report(
        owner_profile=OwnerProfile(owner_profile_id="x", preferred_address="Connor"),
    )
    assert report.is_substantively_completed is True


@pytest.mark.asyncio
async def test_checker_real_completion_with_filled_user_md(tmp_path: Path) -> None:
    """marker + USER.md 已填充（即使 profile 仍默认）→ 视为已完成。"""
    _seed_completed_state(tmp_path)
    _write_filled_user_md(tmp_path)

    checker = BootstrapIntegrityChecker(tmp_path)
    report = await checker.build_report(owner_profile=OwnerProfile(owner_profile_id="x"))
    assert report.is_substantively_completed is True


@pytest.mark.asyncio
async def test_checker_check_substantive_completion_shortcut(tmp_path: Path) -> None:
    """check_substantive_completion 是 build_report().is_substantively_completed 的快捷方式。"""
    _seed_completed_state(tmp_path)
    checker = BootstrapIntegrityChecker(tmp_path)

    # 默认 profile + 占位 USER.md → False
    assert await checker.check_substantive_completion(
        owner_profile=OwnerProfile(owner_profile_id="x")
    ) is False

    # 实质 profile → True
    assert await checker.check_substantive_completion(
        owner_profile=OwnerProfile(owner_profile_id="x", preferred_address="Connor")
    ) is True
