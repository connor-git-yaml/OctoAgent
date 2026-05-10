"""F098 Phase F: ephemeral subagent runtime 独立路径单测（P1-2 修复）。

F097 Phase B P1-2 known issue：subagent ephemeral profile 没有 source_worker_profile_id
→ `_ensure_agent_runtime` 用 (project, role=WORKER, profile_id="") 反查 active runtime →
**复用 caller worker active runtime** → audit chain 混叠。

F098 Phase F 修复：subagent 路径检测后跳过 find_active_runtime 复用，每次新建独立 runtime。

测试场景：
1. AC-F1: subagent path（target_kind=subagent）→ 不复用 caller worker active runtime
2. AC-F2: subagent path（agent_profile.kind=subagent）→ 不复用（fallback 信号）
3. AC-F3: subagent AgentRuntime.metadata 含 subagent_delegation_id（audit 关联）
4. AC-F4: main 路径行为不变（regression 防护）
5. AC-F5: worker 路径仍走 find_active_runtime 复用（regression 防护）
6. AC-F6: 多个 subagent spawn 各自独立 runtime（不互相复用）
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from octoagent.core.models import (
    ContextRequestKind,
    ContextResolveRequest,
    DelegationTargetKind,
)
from octoagent.core.models.agent_context import (
    AgentProfile,
    AgentProfileScope,
    AgentRuntime,
    AgentRuntimeRole,
    AgentRuntimeStatus,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.agent_context import AgentContextService


_NOW = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
_PROJECT_ID = "proj-phase-f-001"
_DELEGATION_ID = "01J0F00000000000000000DELG"


def _make_agent_profile(
    *,
    profile_id: str,
    kind: str = "main",
    name: str = "test_profile",
) -> AgentProfile:
    """构造测试用 AgentProfile。"""
    return AgentProfile(
        profile_id=profile_id,
        scope=AgentProfileScope.PROJECT,
        project_id=_PROJECT_ID,
        name=name,
        kind=kind,
        persona_summary=f"persona_summary for {name}",
        model_alias="default",
        tool_profile="default",
        metadata={},
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_request(
    *,
    request_kind: ContextRequestKind = ContextRequestKind.CHAT,
    target_kind: str = "",
    delegation_id: str = "",
    runtime_id: str = "",
) -> ContextResolveRequest:
    """构造测试用 ContextResolveRequest。"""
    delegation_metadata: dict = {}
    if target_kind:
        delegation_metadata["target_kind"] = target_kind
    if delegation_id:
        delegation_metadata["__subagent_delegation_init__"] = {
            "delegation_id": delegation_id,
            "parent_task_id": "parent-task",
            "parent_work_id": "parent-work",
            "caller_agent_runtime_id": "caller-runtime",
            "caller_project_id": _PROJECT_ID,
            "spawned_by": "delegate_task",
        }
    return ContextResolveRequest(
        request_id=f"req-{datetime.now(UTC).timestamp()}",
        request_kind=request_kind,
        surface="chat",
        project_id=_PROJECT_ID,
        agent_runtime_id=runtime_id or None,
        delegation_metadata=delegation_metadata,
        runtime_metadata={},
    )


async def _save_caller_worker_runtime(store_group, *, profile_id: str) -> AgentRuntime:
    """模拟 caller worker 已有 active runtime（F097 P1-2 触发条件）。"""
    runtime = AgentRuntime(
        agent_runtime_id="runtime-caller-worker-active",
        project_id=_PROJECT_ID,
        agent_profile_id=profile_id,
        worker_profile_id="",  # 关键：empty profile_id（subagent 也是空，复用条件）
        role=AgentRuntimeRole.WORKER,
        name="caller_worker",
        status=AgentRuntimeStatus.ACTIVE,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await store_group.agent_context_store.save_agent_runtime(runtime)
    return runtime


# ---- AC-F1: target_kind=subagent → 不复用 ----


@pytest.mark.asyncio
async def test_subagent_path_target_kind_signal_skips_find_active_runtime(tmp_path: Path) -> None:
    """AC-F1: target_kind=subagent → 不复用 caller worker active runtime。"""
    store_group = await create_store_group(
        str(tmp_path / "f-1.db"), str(tmp_path / "art")
    )
    svc = AgentContextService(store_group, project_root=tmp_path)

    # 1. 模拟 caller worker 已有 active runtime
    caller_profile = _make_agent_profile(profile_id="profile-caller-worker", kind="worker")
    await store_group.agent_context_store.save_agent_profile(caller_profile)
    caller_runtime = await _save_caller_worker_runtime(
        store_group, profile_id=caller_profile.profile_id
    )

    # 2. spawn subagent: target_kind=subagent + 同一 profile_id（理论上会复用 caller_runtime）
    # F098 Phase F 修复后：不复用，新建独立 runtime
    subagent_profile = _make_agent_profile(profile_id="profile-ephemeral-sub", kind="subagent")
    await store_group.agent_context_store.save_agent_profile(subagent_profile)
    request = _make_request(
        target_kind=DelegationTargetKind.SUBAGENT.value,
        delegation_id=_DELEGATION_ID,
    )
    # 模拟 caller worker 的 project（subagent 共享 caller project 是 F097 α 语义）
    project = type("Project", (), {"project_id": _PROJECT_ID})()

    sub_runtime = await svc._ensure_agent_runtime(
        request=request,
        project=project,
        agent_profile=subagent_profile,
    )

    # 关键：subagent runtime ≠ caller worker active runtime
    assert sub_runtime.agent_runtime_id != caller_runtime.agent_runtime_id, (
        "AC-F1 闭环失败：subagent 仍复用 caller worker active runtime"
    )
    assert sub_runtime.agent_runtime_id.startswith("runtime-"), "新 runtime 应有独立 ULID"

    await store_group.conn.close()


# ---- AC-F2: agent_profile.kind=subagent → 不复用 ----


@pytest.mark.asyncio
async def test_subagent_path_profile_kind_signal_skips_find_active_runtime(tmp_path: Path) -> None:
    """AC-F2: agent_profile.kind=subagent（无 target_kind 信号）→ 不复用（fallback 信号）。"""
    store_group = await create_store_group(
        str(tmp_path / "f-2.db"), str(tmp_path / "art")
    )
    svc = AgentContextService(store_group, project_root=tmp_path)

    caller_profile = _make_agent_profile(profile_id="profile-caller-w2", kind="worker")
    await store_group.agent_context_store.save_agent_profile(caller_profile)
    caller_runtime = await _save_caller_worker_runtime(
        store_group, profile_id=caller_profile.profile_id
    )

    # 模拟历史 task 不含 target_kind（仅 profile.kind=subagent 信号）
    subagent_profile = _make_agent_profile(profile_id="profile-ephemeral-sub-2", kind="subagent")
    await store_group.agent_context_store.save_agent_profile(subagent_profile)
    request = _make_request(target_kind="")  # 无 target_kind 信号

    project = type("Project", (), {"project_id": _PROJECT_ID})()

    sub_runtime = await svc._ensure_agent_runtime(
        request=request,
        project=project,
        agent_profile=subagent_profile,
    )

    assert sub_runtime.agent_runtime_id != caller_runtime.agent_runtime_id, (
        "AC-F2 闭环失败：profile.kind=subagent 信号未生效"
    )

    await store_group.conn.close()


# ---- AC-F3: subagent_delegation_id 写入 metadata ----


@pytest.mark.asyncio
async def test_subagent_runtime_metadata_contains_delegation_id(tmp_path: Path) -> None:
    """AC-F3: subagent AgentRuntime.metadata 含 subagent_delegation_id（audit 关联）。"""
    store_group = await create_store_group(
        str(tmp_path / "f-3.db"), str(tmp_path / "art")
    )
    svc = AgentContextService(store_group, project_root=tmp_path)

    subagent_profile = _make_agent_profile(profile_id="profile-ephemeral-sub-3", kind="subagent")
    await store_group.agent_context_store.save_agent_profile(subagent_profile)
    request = _make_request(
        target_kind=DelegationTargetKind.SUBAGENT.value,
        delegation_id=_DELEGATION_ID,
    )

    project = type("Project", (), {"project_id": _PROJECT_ID})()
    sub_runtime = await svc._ensure_agent_runtime(
        request=request,
        project=project,
        agent_profile=subagent_profile,
    )

    assert sub_runtime.metadata.get("subagent_delegation_id") == _DELEGATION_ID, (
        f"AC-F3 闭环失败：metadata 缺 subagent_delegation_id，实际 {sub_runtime.metadata}"
    )

    await store_group.conn.close()


# ---- AC-F4: main 路径行为不变 ----


@pytest.mark.asyncio
async def test_main_path_still_uses_find_active_runtime(tmp_path: Path) -> None:
    """AC-F4: main 路径行为不变（regression 防护）。"""
    store_group = await create_store_group(
        str(tmp_path / "f-4.db"), str(tmp_path / "art")
    )
    svc = AgentContextService(store_group, project_root=tmp_path)

    main_profile = _make_agent_profile(profile_id="profile-main", kind="main")
    await store_group.agent_context_store.save_agent_profile(main_profile)

    # 第一次 spawn → 创建 runtime
    project = type("Project", (), {"project_id": _PROJECT_ID})()
    request1 = _make_request(target_kind="")  # main 路径，无 target_kind
    runtime1 = await svc._ensure_agent_runtime(
        request=request1,
        project=project,
        agent_profile=main_profile,
    )

    # 第二次 spawn → 应该复用第一次的 runtime（find_active_runtime 行为）
    request2 = _make_request(target_kind="")
    runtime2 = await svc._ensure_agent_runtime(
        request=request2,
        project=project,
        agent_profile=main_profile,
    )

    assert runtime1.agent_runtime_id == runtime2.agent_runtime_id, (
        "AC-F4 闭环失败：main 路径不复用 active runtime（regression）"
    )

    await store_group.conn.close()


# ---- AC-F5: worker 路径仍走复用 ----


@pytest.mark.asyncio
async def test_worker_path_still_uses_find_active_runtime(tmp_path: Path) -> None:
    """AC-F5: worker 路径（非 subagent）仍走 find_active_runtime 复用（regression）。"""
    store_group = await create_store_group(
        str(tmp_path / "f-5.db"), str(tmp_path / "art")
    )
    svc = AgentContextService(store_group, project_root=tmp_path)

    worker_profile = _make_agent_profile(profile_id="profile-worker-non-sub", kind="worker")
    await store_group.agent_context_store.save_agent_profile(worker_profile)

    # 创建 worker 第一次 spawn
    project = type("Project", (), {"project_id": _PROJECT_ID})()
    # 模拟 worker dispatch（target_kind=worker，非 subagent）
    request1 = _make_request(target_kind=DelegationTargetKind.WORKER.value)
    runtime1 = await svc._ensure_agent_runtime(
        request=request1,
        project=project,
        agent_profile=worker_profile,
    )

    # 第二次 spawn 同一 worker → 复用 runtime
    request2 = _make_request(target_kind=DelegationTargetKind.WORKER.value)
    runtime2 = await svc._ensure_agent_runtime(
        request=request2,
        project=project,
        agent_profile=worker_profile,
    )

    assert runtime1.agent_runtime_id == runtime2.agent_runtime_id, (
        "AC-F5 闭环失败：worker 路径不复用 active runtime（regression）"
    )

    await store_group.conn.close()


# ---- AC-F6: 多个 subagent 各自独立 runtime ----


@pytest.mark.asyncio
async def test_multiple_subagents_independent_runtimes(tmp_path: Path) -> None:
    """AC-F6: 多个 subagent spawn 各自独立 runtime（不互相复用）。"""
    store_group = await create_store_group(
        str(tmp_path / "f-6.db"), str(tmp_path / "art")
    )
    svc = AgentContextService(store_group, project_root=tmp_path)

    project = type("Project", (), {"project_id": _PROJECT_ID})()

    # 创建 3 个 ephemeral subagent profile（同一 project）
    runtimes = []
    for i in range(3):
        prof = _make_agent_profile(
            profile_id=f"profile-sub-{i}",
            kind="subagent",
            name=f"sub-{i}",
        )
        await store_group.agent_context_store.save_agent_profile(prof)
        request = _make_request(
            target_kind=DelegationTargetKind.SUBAGENT.value,
            delegation_id=f"01J0F00000000000000000DEL{i}",
        )
        runtime = await svc._ensure_agent_runtime(
            request=request,
            project=project,
            agent_profile=prof,
        )
        runtimes.append(runtime)

    # 3 个 runtime 应各自独立（runtime_id 不重复）
    runtime_ids = {r.agent_runtime_id for r in runtimes}
    assert len(runtime_ids) == 3, (
        f"AC-F6 闭环失败：subagent runtimes 互相复用，仅 {len(runtime_ids)} 个独立"
    )

    await store_group.conn.close()
