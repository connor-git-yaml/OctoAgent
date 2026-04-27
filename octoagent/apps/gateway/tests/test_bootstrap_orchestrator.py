"""Feature 082 P2：BootstrapSessionOrchestrator 单元 + 集成测试。

覆盖：
- 字段冲突策略：默认 / 用户显式 / 用户在 sync 后改 / last_synced 锚点
- 完整 complete_bootstrap：标记 onboarding-state + 回填 OwnerProfile + 状态转 COMPLETED
- 已 COMPLETED 的 session 不重复处理
- 不存在的 bootstrap_id 安全降级
- profile_updates 为 None 时仅做状态机标记
- preferred_address: "你" 历史伪默认被覆盖
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
from octoagent.gateway.services.bootstrap_orchestrator import (
    BootstrapCompletionResult,
    BootstrapSessionOrchestrator,
    _apply_field_conflict_strategy,
    _is_pseudo_default_value,
)


# ────────────── _is_pseudo_default_value 单元测试 ──────────────


def test_pseudo_default_preferred_address_recognizes_legacy_you() -> None:
    """Feature 082 P2：'你' 是 P0 之前的伪默认，必须被识别为可覆盖。"""
    assert _is_pseudo_default_value("preferred_address", "你") is True
    assert _is_pseudo_default_value("preferred_address", "") is True
    assert _is_pseudo_default_value("preferred_address", "Connor") is False


def test_pseudo_default_geographic_defaults_are_overridable() -> None:
    """timezone="UTC" / locale="zh-CN" / display_name="Owner" 是兜底默认；
    引导第一次跑时应该可被用户答案覆盖。
    """
    assert _is_pseudo_default_value("timezone", "UTC") is True
    assert _is_pseudo_default_value("timezone", "Asia/Shanghai") is False
    assert _is_pseudo_default_value("locale", "zh-CN") is True
    assert _is_pseudo_default_value("locale", "en-US") is False
    assert _is_pseudo_default_value("display_name", "Owner") is True
    assert _is_pseudo_default_value("display_name", "Connor") is False


def test_pseudo_default_list_fields() -> None:
    assert _is_pseudo_default_value("interaction_preferences", []) is True
    assert _is_pseudo_default_value("interaction_preferences", ["x"]) is False


# ────────────── _apply_field_conflict_strategy 单元测试 ──────────────


def test_conflict_strategy_default_profile_overwritten() -> None:
    """全默认 profile + 新值 → 全部覆盖。"""
    p = OwnerProfile(owner_profile_id="x")
    apply, updated, skipped = _apply_field_conflict_strategy(
        p, {"preferred_address": "Connor", "working_style": "直接", "timezone": "Asia/Shanghai"}
    )
    assert apply == {
        "preferred_address": "Connor",
        "working_style": "直接",
        "timezone": "Asia/Shanghai",
    }
    assert set(updated) == {"preferred_address", "working_style", "timezone"}
    assert skipped == []


def test_conflict_strategy_user_explicit_value_preserved() -> None:
    """用户显式设置过且无 last_synced → 不覆盖。"""
    p = OwnerProfile(owner_profile_id="x", preferred_address="Connor")
    apply, updated, skipped = _apply_field_conflict_strategy(p, {"preferred_address": "Bob"})
    assert apply == {}
    assert skipped == ["preferred_address"]


def test_conflict_strategy_user_modified_after_sync_strictly_protected() -> None:
    """last_synced_from_profile_at 之后用户改过 updated_at → 严格保留所有字段。"""
    sync_time = datetime.now(UTC) - timedelta(hours=1)
    update_time = datetime.now(UTC)
    p = OwnerProfile(
        owner_profile_id="x",
        preferred_address="Connor",
        working_style="原值",
        last_synced_from_profile_at=sync_time,
        updated_at=update_time,
    )
    apply, updated, skipped = _apply_field_conflict_strategy(
        p, {"preferred_address": "Bob", "working_style": "新值"}
    )
    assert apply == {}
    assert set(skipped) == {"preferred_address", "working_style"}


def test_conflict_strategy_legacy_pseudo_default_overwritten() -> None:
    """preferred_address='你' 是 P0 之前的伪默认 → 应被覆盖。"""
    p = OwnerProfile(owner_profile_id="x", preferred_address="你")
    apply, updated, skipped = _apply_field_conflict_strategy(p, {"preferred_address": "Connor"})
    assert apply == {"preferred_address": "Connor"}
    assert updated == ["preferred_address"]


def test_conflict_strategy_empty_string_not_treated_as_update() -> None:
    """新值为空字符串 → 不算 update（避免清空已有数据）。"""
    p = OwnerProfile(owner_profile_id="x", preferred_address="Connor")
    apply, updated, skipped = _apply_field_conflict_strategy(p, {"preferred_address": ""})
    assert apply == {}
    assert updated == []


def test_conflict_strategy_unknown_field_ignored() -> None:
    """非 OwnerProfile 字段被忽略（防御性）。"""
    p = OwnerProfile(owner_profile_id="x")
    apply, _, _ = _apply_field_conflict_strategy(p, {"random_field": "value"})
    assert "random_field" not in apply


# ────────────── BootstrapSessionOrchestrator 集成测试 ──────────────


async def _seed_bootstrap_and_profile(
    store_group, bootstrap_id: str, owner_profile_id: str
) -> None:
    """创建一个 PENDING bootstrap session + 默认 OwnerProfile。"""
    profile = OwnerProfile(owner_profile_id=owner_profile_id)  # 全默认
    await store_group.agent_context_store.save_owner_profile(profile)

    agent_profile = AgentProfile(
        profile_id="agent-test",
        scope=AgentProfileScope.PROJECT,
        project_id="project-test",
        name="Test Agent",
    )
    await store_group.agent_context_store.save_agent_profile(agent_profile)

    session = BootstrapSession(
        bootstrap_id=bootstrap_id,
        project_id="project-test",
        owner_profile_id=owner_profile_id,
        agent_profile_id=agent_profile.profile_id,
        status=BootstrapSessionStatus.PENDING,
        steps=["owner_identity"],
        answers={},
    )
    await store_group.agent_context_store.save_bootstrap_session(session)
    await store_group.conn.commit()


@pytest.mark.asyncio
async def test_orchestrator_complete_bootstrap_full_flow(tmp_path: Path) -> None:
    """完整路径：默认 OwnerProfile → 调 complete_bootstrap → profile 回填 +
    onboarding-state 标记 + session.status=COMPLETED。
    """
    store_group = await create_store_group(
        str(tmp_path / "p2-orch.db"), str(tmp_path / "p2-orch-artifacts")
    )
    try:
        await _seed_bootstrap_and_profile(store_group, "bs-test", "owner-test")

        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result: BootstrapCompletionResult = await orch.complete_bootstrap(
            "bs-test",
            profile_updates={
                "preferred_address": "Connor",
                "working_style": "偏好直接结论",
                "timezone": "Asia/Shanghai",
                "interaction_preferences": ["回答前先对齐 project 事实"],
            },
        )
        await store_group.conn.commit()

        # 1. profile 回填成功
        assert result.owner_profile_updated is True
        assert set(result.fields_updated) == {
            "preferred_address",
            "working_style",
            "timezone",
            "interaction_preferences",
        }
        assert result.fields_skipped == []

        loaded_profile = await store_group.agent_context_store.get_owner_profile("owner-test")
        assert loaded_profile is not None
        assert loaded_profile.preferred_address == "Connor"
        assert loaded_profile.working_style == "偏好直接结论"
        assert loaded_profile.timezone == "Asia/Shanghai"
        assert loaded_profile.interaction_preferences == ["回答前先对齐 project 事实"]
        assert loaded_profile.last_synced_from_profile_at is not None
        assert loaded_profile.version == 2  # 1 → 2

        # 2. onboarding-state.json 已标记
        assert result.onboarding_completed_at is not None
        state_file = tmp_path / "behavior" / ".onboarding-state.json"
        assert state_file.exists()
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        assert state_data["onboarding_completed_at"] is not None

        # 3. session.status 转 COMPLETED
        loaded_session = await store_group.agent_context_store.get_bootstrap_session("bs-test")
        assert loaded_session is not None
        assert loaded_session.status == BootstrapSessionStatus.COMPLETED
        assert loaded_session.completed_at is not None
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_orchestrator_already_completed_session_no_op(tmp_path: Path) -> None:
    """已 COMPLETED 的 session → 返回 warning，不重复处理。"""
    store_group = await create_store_group(
        str(tmp_path / "p2-already.db"), str(tmp_path / "p2-already-artifacts")
    )
    try:
        await _seed_bootstrap_and_profile(store_group, "bs-done", "owner-done")
        # 手动标记 session 为 COMPLETED
        session = await store_group.agent_context_store.get_bootstrap_session("bs-done")
        completed_session = session.model_copy(
            update={"status": BootstrapSessionStatus.COMPLETED}
        )
        await store_group.agent_context_store.save_bootstrap_session(completed_session)
        await store_group.conn.commit()

        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap(
            "bs-done", profile_updates={"preferred_address": "Connor"}
        )

        assert result.owner_profile_updated is False
        assert any("已是 COMPLETED 状态" in w for w in result.warnings)
        # OwnerProfile 不应被覆盖
        loaded_profile = await store_group.agent_context_store.get_owner_profile("owner-done")
        assert loaded_profile.preferred_address == ""  # 仍是 P0 默认
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_orchestrator_unknown_bootstrap_id_safe_degrade(tmp_path: Path) -> None:
    """不存在的 bootstrap_id → 返回 warning，不抛异常。"""
    store_group = await create_store_group(
        str(tmp_path / "p2-unknown.db"), str(tmp_path / "p2-unknown-artifacts")
    )
    try:
        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap(
            "bs-nonexistent", profile_updates={"preferred_address": "Connor"}
        )
        assert result.owner_profile_updated is False
        assert any("不存在" in w for w in result.warnings)
        assert result.onboarding_completed_at is None
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_orchestrator_no_profile_updates_marks_state_only(tmp_path: Path) -> None:
    """profile_updates=None → 仅标记 .onboarding-state.json，不动 OwnerProfile。"""
    store_group = await create_store_group(
        str(tmp_path / "p2-no-updates.db"), str(tmp_path / "p2-no-updates-artifacts")
    )
    try:
        await _seed_bootstrap_and_profile(store_group, "bs-state-only", "owner-state-only")

        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap("bs-state-only", profile_updates=None)
        await store_group.conn.commit()

        assert result.owner_profile_updated is False
        assert result.onboarding_completed_at is not None
        # session 仍转 COMPLETED
        loaded_session = await store_group.agent_context_store.get_bootstrap_session(
            "bs-state-only"
        )
        assert loaded_session.status == BootstrapSessionStatus.COMPLETED
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_orchestrator_legacy_you_address_overwritten(tmp_path: Path) -> None:
    """**关键修复场景**：preferred_address='你' 历史伪默认被覆盖为真实值。"""
    store_group = await create_store_group(
        str(tmp_path / "p2-legacy.db"), str(tmp_path / "p2-legacy-artifacts")
    )
    try:
        # 模拟 P0 之前的老用户：preferred_address='你'
        legacy_profile = OwnerProfile(owner_profile_id="owner-legacy", preferred_address="你")
        await store_group.agent_context_store.save_owner_profile(legacy_profile)

        agent_profile = AgentProfile(
            profile_id="agent-legacy",
            scope=AgentProfileScope.PROJECT,
            project_id="project-legacy",
            name="Legacy Agent",
        )
        await store_group.agent_context_store.save_agent_profile(agent_profile)

        session = BootstrapSession(
            bootstrap_id="bs-legacy",
            project_id="project-legacy",
            owner_profile_id="owner-legacy",
            agent_profile_id=agent_profile.profile_id,
            status=BootstrapSessionStatus.PENDING,
        )
        await store_group.agent_context_store.save_bootstrap_session(session)
        await store_group.conn.commit()

        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap(
            "bs-legacy", profile_updates={"preferred_address": "Connor"}
        )
        await store_group.conn.commit()

        assert result.owner_profile_updated is True
        assert "preferred_address" in result.fields_updated
        loaded = await store_group.agent_context_store.get_owner_profile("owner-legacy")
        assert loaded.preferred_address == "Connor"  # 不再是 "你"
    finally:
        await store_group.conn.close()


@pytest.mark.asyncio
async def test_orchestrator_user_modified_after_sync_strictly_preserved(tmp_path: Path) -> None:
    """用户在 last_synced 之后改过 → 严格保护，新 profile_updates 全部 skipped。"""
    store_group = await create_store_group(
        str(tmp_path / "p2-protected.db"), str(tmp_path / "p2-protected-artifacts")
    )
    try:
        sync_time = datetime.now(UTC) - timedelta(hours=2)
        update_time = datetime.now(UTC) - timedelta(minutes=5)

        protected_profile = OwnerProfile(
            owner_profile_id="owner-protected",
            preferred_address="UserChosen",
            working_style="UserPicked",
            last_synced_from_profile_at=sync_time,
            updated_at=update_time,  # > sync_time → 用户已改
        )
        await store_group.agent_context_store.save_owner_profile(protected_profile)

        agent_profile = AgentProfile(
            profile_id="agent-protected",
            scope=AgentProfileScope.PROJECT,
            project_id="project-protected",
            name="Protected",
        )
        await store_group.agent_context_store.save_agent_profile(agent_profile)

        session = BootstrapSession(
            bootstrap_id="bs-protected",
            project_id="project-protected",
            owner_profile_id="owner-protected",
            agent_profile_id=agent_profile.profile_id,
            status=BootstrapSessionStatus.PENDING,
        )
        await store_group.agent_context_store.save_bootstrap_session(session)
        await store_group.conn.commit()

        orch = BootstrapSessionOrchestrator(store_group.agent_context_store, tmp_path)
        result = await orch.complete_bootstrap(
            "bs-protected",
            profile_updates={"preferred_address": "Override", "working_style": "Different"},
        )

        assert result.owner_profile_updated is False
        assert set(result.fields_skipped) == {"preferred_address", "working_style"}
        loaded = await store_group.agent_context_store.get_owner_profile("owner-protected")
        assert loaded.preferred_address == "UserChosen"  # 严格保护
        assert loaded.working_style == "UserPicked"
    finally:
        await store_group.conn.close()


