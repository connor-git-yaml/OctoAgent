"""F098 Phase B: A2A source + target 双向独立加载单测（H3-B + Codex P1 闭环）。

测试场景：
- B-1 source role 派生（Codex review P1 闭环）：
  - AC-B1-S1: worker→worker A2A 场景，source 是 worker / WORKER_INTERNAL / "worker.<cap>"
  - AC-B1-S2: main→worker 场景 source 仍是 main / MAIN_BOOTSTRAP / "main.agent"（regression）
  - AC-B1-S3: A2AConversation 字段反映真实 source（间接通过 source 派生 验证）
  - AC-B1-S4: source 派生 fallback：metadata 缺失优雅降级（不 raise）
- B-2 target profile 解析（Codex review P2 闭环）：
  - AC-B2-T1: requested_worker_profile_id 直接 lookup
  - AC-B2-T2: worker_capability 派生（通过 _delegation_plane.capability_pack）
  - AC-B2-T3: fail-loud fallback：lookup/capability resolve 失败时不静默吞 except
  - target_profile_id != source_profile_id（独立加载验证）
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octoagent.core.models.agent_context import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntimeRole,
    AgentSessionKind,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.orchestrator import OrchestratorService


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)


def _make_orchestrator(store_group, **kwargs) -> OrchestratorService:
    """构造测试用 OrchestratorService（最小依赖）。"""
    svc = OrchestratorService.__new__(OrchestratorService)
    svc._stores = store_group
    svc._delegation_plane = kwargs.get("delegation_plane", None)
    return svc


def _make_runtime_context(
    *,
    runtime_kind: str = "main",
    metadata: dict | None = None,
):
    """构造 RuntimeContext mock。"""
    rc = MagicMock()
    rc.runtime_kind = runtime_kind
    rc.metadata = metadata or {}
    rc.surface = "chat"
    rc.session_id = ""
    rc.context_frame_id = ""
    rc.project_id = ""
    rc.agent_profile_id = ""
    return rc


# ---- B-1 source role 派生 ----


def test_resolve_a2a_source_role_main_default(tmp_path: Path):
    """AC-B1-S2: 默认 main 路径（无 worker 信号）→ MAIN / MAIN_BOOTSTRAP / "main.agent"。"""
    svc = _make_orchestrator(store_group=MagicMock())

    runtime_context = _make_runtime_context(runtime_kind="main")
    role, kind, uri = svc._resolve_a2a_source_role(
        runtime_context=runtime_context,
        runtime_metadata={},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.MAIN
    assert kind == AgentSessionKind.MAIN_BOOTSTRAP
    assert uri == "agent://main.agent"


def test_resolve_a2a_source_role_worker_from_runtime_context(tmp_path: Path):
    """AC-B1-S1: runtime_context.runtime_kind=worker → WORKER / WORKER_INTERNAL / "worker.<cap>"。"""
    svc = _make_orchestrator(store_group=MagicMock())

    runtime_context = _make_runtime_context(
        runtime_kind="worker",
        metadata={"source_worker_capability": "research"},
    )
    role, kind, uri = svc._resolve_a2a_source_role(
        runtime_context=runtime_context,
        runtime_metadata={"source_worker_capability": "research"},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.WORKER
    assert kind == AgentSessionKind.WORKER_INTERNAL
    assert uri == "agent://worker.research"


def test_resolve_a2a_source_role_worker_from_envelope_metadata(tmp_path: Path):
    """AC-B1-S1 alt: envelope.metadata.source_runtime_kind=worker（runtime_context 缺失）→ WORKER。"""
    svc = _make_orchestrator(store_group=MagicMock())

    role, kind, uri = svc._resolve_a2a_source_role(
        runtime_context=None,  # 模拟 runtime_context 缺失
        runtime_metadata={},
        envelope_metadata={
            "source_runtime_kind": "worker",
            "source_worker_capability": "code",
        },
    )

    assert role == AgentRuntimeRole.WORKER
    assert kind == AgentSessionKind.WORKER_INTERNAL
    assert uri == "agent://worker.code"


def test_resolve_a2a_source_role_worker_no_capability_fallback(tmp_path: Path):
    """AC-B1-S4: source 是 worker 但 capability 缺失 → "worker.unknown" agent_uri。"""
    svc = _make_orchestrator(store_group=MagicMock())

    runtime_context = _make_runtime_context(runtime_kind="worker", metadata={})
    role, kind, uri = svc._resolve_a2a_source_role(
        runtime_context=runtime_context,
        runtime_metadata={},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.WORKER
    assert kind == AgentSessionKind.WORKER_INTERNAL
    assert uri == "agent://worker.unknown"


def test_resolve_a2a_source_role_runtime_context_none_fallback(tmp_path: Path):
    """AC-B1-S4: runtime_context 完全缺失（None）→ 默认 main 路径（不 raise）。"""
    svc = _make_orchestrator(store_group=MagicMock())

    role, kind, uri = svc._resolve_a2a_source_role(
        runtime_context=None,
        runtime_metadata={},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.MAIN
    assert kind == AgentSessionKind.MAIN_BOOTSTRAP
    assert uri == "agent://main.agent"


def test_resolve_a2a_source_role_subagent_handled_as_worker(tmp_path: Path):
    """AC-B1-S1 兼容性：subagent runtime 走 A2A 时按 worker 处理（保持 audit 一致性）。"""
    svc = _make_orchestrator(store_group=MagicMock())

    runtime_context = _make_runtime_context(
        runtime_kind="subagent",
        metadata={"source_worker_capability": "search"},
    )
    role, kind, uri = svc._resolve_a2a_source_role(
        runtime_context=runtime_context,
        runtime_metadata={"source_worker_capability": "search"},
        envelope_metadata={},
    )

    assert role == AgentRuntimeRole.WORKER
    assert kind == AgentSessionKind.WORKER_INTERNAL
    assert uri == "agent://worker.search"


# ---- B-2 target profile 解析 ----


@pytest.mark.asyncio
async def test_resolve_target_agent_profile_explicit_id_lookup_success(tmp_path: Path):
    """AC-B2-T1: requested_worker_profile_id 直接 lookup → 返回独立 profile。"""
    store_group = await create_store_group(
        str(tmp_path / "b-1.db"), str(tmp_path / "art")
    )
    svc = _make_orchestrator(store_group=store_group)

    target_profile = AgentProfile(
        profile_id="profile-target-explicit",
        scope=AgentProfileScope.PROJECT,
        project_id="proj",
        name="target_research_worker",
        kind="worker",
        persona_summary="research worker",
        model_alias="default",
        tool_profile="research",
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_profile(target_profile)

    result_profile_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="profile-target-explicit",
        worker_capability="",
        fallback_source_profile_id="profile-source-fallback",
    )

    assert result_profile_id == "profile-target-explicit"
    assert result_profile_id != "profile-source-fallback"

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_resolve_target_agent_profile_capability_via_capability_pack(tmp_path: Path):
    """AC-B2-T2: 通过 _delegation_plane.capability_pack 按 worker_capability 派生 default profile。"""
    store_group = await create_store_group(
        str(tmp_path / "b-2.db"), str(tmp_path / "art")
    )

    capability_default_profile = AgentProfile(
        profile_id="profile-capability-default",
        scope=AgentProfileScope.PROJECT,
        project_id="proj",
        name="default_code_worker",
        kind="worker",
        persona_summary="default code worker",
        model_alias="default",
        tool_profile="code",
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )

    # mock _delegation_plane.capability_pack.resolve_worker_agent_profile
    capability_pack = MagicMock()
    capability_pack.resolve_worker_agent_profile = AsyncMock(
        return_value=capability_default_profile
    )
    delegation_plane = MagicMock()
    delegation_plane.capability_pack = capability_pack

    svc = _make_orchestrator(store_group=store_group, delegation_plane=delegation_plane)

    result_profile_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="",  # 无 explicit id
        worker_capability="code",
        fallback_source_profile_id="profile-source-fallback",
    )

    assert result_profile_id == "profile-capability-default"
    # 验证 capability_pack.resolve_worker_agent_profile 被正确调用
    capability_pack.resolve_worker_agent_profile.assert_called_once_with(worker_capability="code")

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_resolve_target_agent_profile_explicit_id_not_found_falls_back_to_capability(
    tmp_path: Path,
):
    """AC-B2-T2 chain: explicit id lookup 失败 → 走 capability fallback（fail-loud + warning）。"""
    store_group = await create_store_group(
        str(tmp_path / "b-3.db"), str(tmp_path / "art")
    )

    capability_default_profile = AgentProfile(
        profile_id="profile-capability-fallback",
        scope=AgentProfileScope.PROJECT,
        project_id="proj",
        name="default_research_worker",
        kind="worker",
        persona_summary="default research worker",
        model_alias="default",
        tool_profile="research",
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    capability_pack = MagicMock()
    capability_pack.resolve_worker_agent_profile = AsyncMock(
        return_value=capability_default_profile
    )
    delegation_plane = MagicMock()
    delegation_plane.capability_pack = capability_pack

    svc = _make_orchestrator(store_group=store_group, delegation_plane=delegation_plane)

    # explicit id 不存在 store 中 → lookup 返回 None → 走 capability fallback
    result_profile_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="profile-not-exist",
        worker_capability="research",
        fallback_source_profile_id="profile-source-fallback",
    )

    assert result_profile_id == "profile-capability-fallback"
    capability_pack.resolve_worker_agent_profile.assert_called_once()

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_resolve_target_agent_profile_fallback_to_source_when_all_fail(
    tmp_path: Path,
):
    """AC-B2-T3 fail-loud: 所有路径失败 → fallback 到 source profile（warning log + 不静默）。"""
    store_group = await create_store_group(
        str(tmp_path / "b-4.db"), str(tmp_path / "art")
    )

    # capability_pack.resolve_worker_agent_profile 返回 None（无 default）
    capability_pack = MagicMock()
    capability_pack.resolve_worker_agent_profile = AsyncMock(return_value=None)
    delegation_plane = MagicMock()
    delegation_plane.capability_pack = capability_pack

    svc = _make_orchestrator(store_group=store_group, delegation_plane=delegation_plane)

    result_profile_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="",
        worker_capability="unknown_capability",
        fallback_source_profile_id="profile-source-fallback",
    )

    # fail-loud fallback：返回 source profile（保持兼容性）
    assert result_profile_id == "profile-source-fallback"

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_resolve_target_agent_profile_no_delegation_plane_falls_back(
    tmp_path: Path,
):
    """AC-B2-T3: _delegation_plane 缺失（None）→ 走 fallback（不 raise）。"""
    store_group = await create_store_group(
        str(tmp_path / "b-5.db"), str(tmp_path / "art")
    )

    svc = _make_orchestrator(store_group=store_group, delegation_plane=None)

    result_profile_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="",
        worker_capability="code",
        fallback_source_profile_id="profile-source-fallback",
    )

    assert result_profile_id == "profile-source-fallback"

    await store_group.conn.close()


@pytest.mark.asyncio
async def test_target_profile_independent_from_source_profile(tmp_path: Path):
    """关键 AC：target_profile_id != source_profile_id 验证（H3-B 核心）。"""
    store_group = await create_store_group(
        str(tmp_path / "b-6.db"), str(tmp_path / "art")
    )

    target_profile = AgentProfile(
        profile_id="profile-receiver-worker",
        scope=AgentProfileScope.PROJECT,
        project_id="proj",
        name="receiver_worker",
        kind="worker",
        persona_summary="receiver",
        model_alias="default",
        tool_profile="default",
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_profile(target_profile)

    svc = _make_orchestrator(store_group=store_group)

    target_id = await svc._resolve_target_agent_profile(
        requested_worker_profile_id="profile-receiver-worker",
        worker_capability="",
        fallback_source_profile_id="profile-caller-main",  # source 是主 Agent
    )

    assert target_id == "profile-receiver-worker"
    assert target_id != "profile-caller-main", (
        "H3-B 核心失败：A2A target 复用 source profile（receiver 没在自己 context 工作）"
    )

    await store_group.conn.close()
