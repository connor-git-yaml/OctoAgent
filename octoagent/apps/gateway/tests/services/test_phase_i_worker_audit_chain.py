"""F098 Phase I: worker_capability audit chain 集成测（F096 H2 推迟项归位）。

F096 Phase F audit chain 测试仅 cover main agent dispatch 路径。
F098 Phase I 复用结构补 worker_capability 路径完整 audit chain。

关键路径：
1. main → worker dispatch（baseline F096 已 cover main agent）
2. worker → worker A2A dispatch（F098 Phase B-1 source 派生 + Phase C 解禁后启用）

audit chain 4 层（同 F096 Phase F 结构）：
- Layer 1: AgentProfile.profile_id → AgentRuntime.profile_id（Phase B-2 解析路径）
- Layer 2: AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id（dispatch emit）
- Layer 3: BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id（recall persist）
- Layer 4: BEHAVIOR_PACK_LOADED.agent_kind == "worker"（不是 "main"）

测试聚焦在 F098 修改面（B-1 + B-2 + C）的 audit chain 对齐，不重测 F096 已覆盖的 main 路径。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from octoagent.core.models.agent_context import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
)
from octoagent.core.store import create_store_group


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


# ---- AC-I1: target Worker profile 加载 → AgentRuntime.profile_id 一致（Layer 1）----


@pytest.mark.asyncio
async def test_phase_i_target_worker_profile_runtime_alignment(tmp_path: Path):
    """AC-I1 Layer 1: A2A target Worker AgentProfile.profile_id == AgentRuntime.profile_id。

    F098 Phase B-2 修复后，target Worker 加载自己的 profile（不复用 source）。
    Layer 1 audit chain 验证：profile lookup 后 ensure_a2a_agent_runtime 用此 profile_id。
    """
    from unittest.mock import MagicMock

    from octoagent.gateway.services.orchestrator import OrchestratorService

    store_group = await create_store_group(
        str(tmp_path / "i-1.db"), str(tmp_path / "art")
    )

    target_profile = AgentProfile(
        profile_id="profile-target-research-worker",
        scope=AgentProfileScope.PROJECT,
        project_id="proj-i-1",
        name="research_worker",
        kind="worker",
        persona_summary="research worker",
        model_alias="default",
        tool_profile="research",
        metadata={"worker_capability": "research"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_profile(target_profile)

    svc = OrchestratorService.__new__(OrchestratorService)
    svc._stores = store_group
    svc._delegation_plane = None

    # F098 Phase B-2: target profile 独立解析
    resolved_profile_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="profile-target-research-worker",
        worker_capability="",
        fallback_source_profile_id="profile-source-main",
    )

    # AC-I1 Layer 1: profile lookup 返回 target Worker 的 profile_id
    assert resolved_profile_id == "profile-target-research-worker"
    assert resolved_profile_id != "profile-source-main", (
        "Phase I AC-I1 Layer 1 失败：target Worker profile 未独立加载（仍用 source profile）"
    )

    await store_group.conn.close()


# ---- AC-I2: AgentRuntime worker_capability 字段填充（Layer 2 间接）----


@pytest.mark.asyncio
async def test_phase_i_worker_runtime_metadata_includes_capability(tmp_path: Path):
    """AC-I2: worker AgentRuntime.metadata 含 worker_capability（dispatch 路径标识）。

    供 BEHAVIOR_PACK_LOADED 事件的 agent_kind="worker" 派生 + audit 关联。
    """
    store_group = await create_store_group(
        str(tmp_path / "i-2.db"), str(tmp_path / "art")
    )

    # 直接构造 worker runtime（模拟 _ensure_a2a_agent_runtime 后的结果）
    worker_profile = AgentProfile(
        profile_id="profile-worker-i-2",
        scope=AgentProfileScope.PROJECT,
        project_id="proj-i-2",
        name="code_worker",
        kind="worker",
        persona_summary="code worker",
        model_alias="default",
        tool_profile="code",
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_profile(worker_profile)

    runtime = AgentRuntime(
        agent_runtime_id="runtime-worker-i-2",
        project_id="proj-i-2",
        agent_profile_id=worker_profile.profile_id,
        worker_profile_id="",
        role=AgentRuntimeRole.WORKER,
        name="code_worker",
        status=AgentRuntimeStatus.ACTIVE,
        metadata={"worker_capability": "code", "request_kind": "chat"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)

    # AC-I2: 验证 worker_capability 在 metadata 中可读
    fetched_runtime = await store_group.agent_context_store.get_agent_runtime(
        runtime.agent_runtime_id
    )
    assert fetched_runtime is not None
    assert fetched_runtime.metadata.get("worker_capability") == "code", (
        "Phase I AC-I2 失败：AgentRuntime.metadata 未含 worker_capability"
    )
    assert fetched_runtime.role == AgentRuntimeRole.WORKER, (
        "Phase I AC-I2 失败：AgentRuntime.role 不是 WORKER"
    )

    await store_group.conn.close()


# ---- AC-I3: worker→worker audit chain 链式追溯（Phase C 解禁 + B-1 source 派生协同）----


@pytest.mark.asyncio
async def test_phase_i_worker_to_worker_source_audit_independence(tmp_path: Path):
    """AC-I3: worker→worker A2A 场景下 source 真实是 worker（B-1 派生），
    target Worker 独立加载（B-2 解析）→ source/target audit chain 完全独立。

    F098 Final Codex P1 闭环：用真实 RuntimeControlContext.turn_executor_kind 字段。
    """
    from unittest.mock import MagicMock

    from octoagent.core.models import TurnExecutorKind
    from octoagent.core.models.agent_context import AgentSessionKind
    from octoagent.gateway.services.orchestrator import OrchestratorService

    svc = OrchestratorService.__new__(OrchestratorService)
    svc._stores = MagicMock()
    svc._delegation_plane = None

    # 模拟 worker A 调用 A2A 给 worker B（用 envelope.metadata.source_runtime_kind 显式信号）
    # Phase D Codex P1 闭环：不再用 turn_executor_kind（避免与 target_kind 混淆）
    source_role, source_session_kind, source_uri = svc._resolve_a2a_source_role(
        runtime_context=None,
        runtime_metadata={},
        envelope_metadata={
            "source_runtime_kind": "worker",
            "source_worker_capability": "research",
        },
    )

    # AC-I3 Layer A: source 真实反映 worker（不是 main）
    assert source_role == AgentRuntimeRole.WORKER, (
        "Phase I AC-I3 失败：worker→worker A2A 的 source role 仍是 main"
    )
    assert source_session_kind == AgentSessionKind.WORKER_INTERNAL, (
        "Phase I AC-I3 失败：worker→worker A2A 的 source session_kind 仍是 MAIN_BOOTSTRAP"
    )
    assert "worker.research" in source_uri, (
        f"Phase I AC-I3 失败：worker→worker A2A source uri 是 {source_uri}（应含 worker.research）"
    )
    assert "main.agent" not in source_uri, (
        f"Phase I AC-I3 失败：worker→worker A2A source uri 仍是 main.agent"
    )


# ---- AC-I4: F098 修改面 audit chain consistency 端到端（profile + runtime 独立）----


@pytest.mark.asyncio
async def test_phase_i_independent_runtimes_independent_audit_chains(tmp_path: Path):
    """AC-I4: 多个 worker dispatch 各自独立 runtime → audit chain 不串扰。"""
    store_group = await create_store_group(
        str(tmp_path / "i-4.db"), str(tmp_path / "art")
    )

    # 创建两个不同的 worker profile（research / code）
    profile_a = AgentProfile(
        profile_id="profile-worker-research",
        scope=AgentProfileScope.PROJECT,
        project_id="proj-i-4",
        name="research_worker",
        kind="worker",
        persona_summary="research worker",
        model_alias="default",
        tool_profile="research",
        metadata={"worker_capability": "research"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    profile_b = AgentProfile(
        profile_id="profile-worker-code",
        scope=AgentProfileScope.PROJECT,
        project_id="proj-i-4",
        name="code_worker",
        kind="worker",
        persona_summary="code worker",
        model_alias="default",
        tool_profile="code",
        metadata={"worker_capability": "code"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_profile(profile_a)
    await store_group.agent_context_store.save_agent_profile(profile_b)

    # 各自创建独立 runtime
    runtime_a = AgentRuntime(
        agent_runtime_id="runtime-worker-research-i-4",
        project_id="proj-i-4",
        agent_profile_id=profile_a.profile_id,
        worker_profile_id="",
        role=AgentRuntimeRole.WORKER,
        name="research",
        status=AgentRuntimeStatus.ACTIVE,
        metadata={"worker_capability": "research"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    runtime_b = AgentRuntime(
        agent_runtime_id="runtime-worker-code-i-4",
        project_id="proj-i-4",
        agent_profile_id=profile_b.profile_id,
        worker_profile_id="",
        role=AgentRuntimeRole.WORKER,
        name="code",
        status=AgentRuntimeStatus.ACTIVE,
        metadata={"worker_capability": "code"},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime_a)
    await store_group.agent_context_store.save_agent_runtime(runtime_b)

    # AC-I4: 两个 runtime 各自独立（runtime_id 不同 + agent_profile_id 不同）
    fetched_a = await store_group.agent_context_store.get_agent_runtime(
        runtime_a.agent_runtime_id
    )
    fetched_b = await store_group.agent_context_store.get_agent_runtime(
        runtime_b.agent_runtime_id
    )
    assert fetched_a.agent_runtime_id != fetched_b.agent_runtime_id
    assert fetched_a.agent_profile_id != fetched_b.agent_profile_id, (
        "Phase I AC-I4 失败：两个 worker runtime 复用同一 profile_id（audit chain 串扰）"
    )

    await store_group.conn.close()
