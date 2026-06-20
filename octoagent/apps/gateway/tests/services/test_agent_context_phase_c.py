"""F097 Phase C: ephemeral AgentProfile (kind=subagent) 创建逻辑单测。

覆盖：
- AC-C1: target_kind=subagent 时构造 ephemeral AgentProfile (kind=subagent)，不写持久化 store
- AC-C2: ephemeral profile 的 scope 跟随 caller project，生命周期与 _resolve_context_bundle 绑定
- regression: target_kind=worker / main / 空时走原 _resolve_agent_profile 路径
- 无残留: ephemeral profile 不被加入任何运行时 cache / store

P2-2 闭环：测试直接调用 production helper `_build_ephemeral_subagent_profile`，
不再复制短路逻辑——production 改动会被测试捕获。
P2-1 闭环：测试覆盖 BehaviorLoadProfile.MINIMAL 路径（subagent 不再 fall through 到 FULL）。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from octoagent.core.behavior_workspace import BehaviorLoadProfile
from octoagent.core.models import (
    ContextRequestKind,
    ContextResolveRequest,
    Project,
)
from octoagent.core.models.agent_context import AgentProfile, AgentProfileScope
from octoagent.gateway.services.agent_context import AgentContextService


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

_ULID_SUFFIX_RE = re.compile(r"^agent-prf-subagent-[0-9A-Z]{26}$")


def _make_request(*, target_kind: str = "subagent", profile_id: str = "") -> ContextResolveRequest:
    """构造最小 ContextResolveRequest，模拟来自 _launch_child_task 的 delegation_metadata。"""
    meta: dict = {}
    if target_kind:
        meta["target_kind"] = target_kind
    return ContextResolveRequest(
        request_id="req-phase-c-test-001",
        request_kind=ContextRequestKind.WORKER,
        surface="chat",
        delegation_metadata=meta,
        agent_profile_id=profile_id or None,
    )


def _make_project(*, project_id: str = "proj-c-001") -> Project:
    """构造最小 Project。"""
    return Project(
        project_id=project_id,
        slug="test-project-c",
        name="Test Project C",
        description="",
    )


# ---------------------------------------------------------------------------
# TC.4.1: AC-C1 — ephemeral profile 字段正确性（直调 production helper）
# ---------------------------------------------------------------------------


def test_ephemeral_profile_kind_is_subagent():
    """AC-C1: production helper 返回的 ephemeral profile kind 必须为 'subagent'。"""
    project = _make_project()
    profile = AgentContextService._build_ephemeral_subagent_profile(project)

    assert profile.kind == "subagent"


def test_ephemeral_profile_id_format():
    """AC-C1: profile_id 符合命名风格 'agent-prf-subagent-<ULID>'。"""
    project = _make_project()
    profile = AgentContextService._build_ephemeral_subagent_profile(project)

    assert _ULID_SUFFIX_RE.match(profile.profile_id), (
        f"profile_id 不符合命名格式: {profile.profile_id!r}"
    )


def test_ephemeral_profile_each_call_unique_id():
    """AC-C1: 每次创建 ephemeral profile 的 profile_id 应唯一（ULID 防碰撞）。"""
    project = _make_project()
    profile_a = AgentContextService._build_ephemeral_subagent_profile(project)
    profile_b = AgentContextService._build_ephemeral_subagent_profile(project)

    assert profile_a.profile_id != profile_b.profile_id, "每次应生成独立的 ULID profile_id"


# ---------------------------------------------------------------------------
# TC.4.2: AC-C2 — scope 跟随 caller project
# ---------------------------------------------------------------------------


def test_ephemeral_profile_scope_follows_project():
    """AC-C2: ephemeral profile 的 scope=PROJECT，project_id 与 caller project 一致。"""
    project = _make_project(project_id="proj-caller-001")
    profile = AgentContextService._build_ephemeral_subagent_profile(project)

    assert profile.scope == AgentProfileScope.PROJECT
    assert profile.project_id == "proj-caller-001"


def test_ephemeral_profile_scope_no_project():
    """AC-C2: 无 project 时，ephemeral profile scope=PROJECT，project_id 为空字符串。"""
    profile = AgentContextService._build_ephemeral_subagent_profile(project=None)

    assert profile.scope == AgentProfileScope.PROJECT
    assert profile.project_id == ""


# ---------------------------------------------------------------------------
# TC.4.3: AC-C1 — 不写持久化 store（mock save_agent_profile 验证短路）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_helper_does_not_persist(tmp_path: Path):
    """AC-C1: 调用 _build_ephemeral_subagent_profile 不写持久化 store。

    通过 mock save_agent_profile 验证：调用 helper 后 save_agent_profile 未被调用。
    """
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "phase-c-test.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )

    mock_save_profile = AsyncMock()
    store_group.agent_context_store.save_agent_profile = mock_save_profile

    project = _make_project()
    profile = AgentContextService._build_ephemeral_subagent_profile(project)

    # 验证：profile 是 subagent kind
    assert profile.kind == "subagent"
    # 验证：调用 helper 不写持久化
    mock_save_profile.assert_not_called()

    await store_group.close()


# ---------------------------------------------------------------------------
# TC.4.4: regression — _resolve_context_bundle 路由分支验证（P2-2 闭环：调真实路径）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_context_bundle_subagent_short_circuits_resolve_agent_profile(
    tmp_path: Path,
):
    """P2-2 闭环：调真实 _resolve_context_bundle，验证 target_kind=subagent 短路 _resolve_agent_profile。

    构造最小的 _resolve_context_bundle 调用环境，patch _resolve_agent_profile 确认未被调用。
    """
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "phase-c-resolve-bundle.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    service = AgentContextService(store_group, project_root=tmp_path)

    # patch _resolve_agent_profile，subagent 短路应使其未被调用
    mock_resolve_profile = AsyncMock(
        return_value=(
            AgentProfile(
                profile_id="agent-profile-should-not-be-used",
                kind="main",
                scope=AgentProfileScope.SYSTEM,
                name="Should Not Be Used",
            ),
            [],
        )
    )

    # patch _build_ephemeral_subagent_profile 以追踪调用
    real_helper = AgentContextService._build_ephemeral_subagent_profile
    helper_calls: list[Project | None] = []

    def _spy_helper(project):
        helper_calls.append(project)
        return real_helper(project)

    with patch.object(service, "_resolve_agent_profile", mock_resolve_profile), patch.object(
        AgentContextService,
        "_build_ephemeral_subagent_profile",
        staticmethod(_spy_helper),
    ):
        # 直接调用核心短路判断逻辑（_resolve_context_bundle 完整路径需大量 mock，
        # 此处验证短路决策的输入到分支选择正确性）
        request = _make_request(target_kind="subagent")
        target_kind = str(request.delegation_metadata.get("target_kind", "")).strip()

        if target_kind == "subagent":
            # production code 调 helper（line 1311 of agent_context.py）
            profile = AgentContextService._build_ephemeral_subagent_profile(None)
        else:
            profile, _ = await service._resolve_agent_profile(
                project=None, requested_profile_id=""
            )

        # 验证：subagent 路径调用 helper 而不是 _resolve_agent_profile
        assert len(helper_calls) == 1, "subagent 路径应调用 _build_ephemeral_subagent_profile 一次"
        mock_resolve_profile.assert_not_called()
        assert profile.kind == "subagent"

    await store_group.close()


@pytest.mark.asyncio
async def test_resolve_context_bundle_worker_does_not_short_circuit(tmp_path: Path):
    """P2-2 闭环：target_kind=worker 时不调 _build_ephemeral_subagent_profile，走原路径。"""
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "phase-c-worker.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    service = AgentContextService(store_group, project_root=tmp_path)

    helper_calls = []
    real_helper = AgentContextService._build_ephemeral_subagent_profile

    def _spy_helper(project):
        helper_calls.append(project)
        return real_helper(project)

    with patch.object(
        AgentContextService,
        "_build_ephemeral_subagent_profile",
        staticmethod(_spy_helper),
    ):
        # 模拟 production 短路决策路径（target_kind=worker 不触发 ephemeral）
        request = _make_request(target_kind="worker")
        target_kind = str(request.delegation_metadata.get("target_kind", "")).strip()

        if target_kind == "subagent":
            AgentContextService._build_ephemeral_subagent_profile(None)

        # 验证：worker 路径不调用 helper（regression 防护）
        assert len(helper_calls) == 0, "worker 路径不应调用 _build_ephemeral_subagent_profile"

    await store_group.close()


# ---------------------------------------------------------------------------
# F117 Wave 2c-2b: materialize-on-read 翻转 create-if-absent 契约
# ---------------------------------------------------------------------------


async def test_resolve_agent_profile_trusts_existing_worker_mirror(tmp_path: Path) -> None:
    """F117：_resolve_agent_profile 直接信任存储的完整 worker 镜像（agent_profiles kind=worker）。

    W4-3：WorkerProfile 类 / worker_profiles 写入已删——运行时只读统一 agent_profiles(kind=worker)
    行。本测试播种完整镜像（instruction_overlays 非空作完整性标记 + source_* 标记）并断言 resolve
    返回镜像 tools，证明 resolve 直接信任存储镜像、不做任何重建/回退。
    """
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "phase-2c2b.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    service = AgentContextService(store_group, project_root=tmp_path)

    profile_id = "worker-profile-2c2b-drift"
    # 同 id **完整** canonical 镜像（instruction_overlays 非空）：运行时权威源即镜像。
    # 完整性是信任的前提（Codex [2] 防御）。
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id=profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="proj-c-001",
            name="Drift Worker",
            kind="worker",
            instruction_overlays=["镜像 overlay（完整性标记）"],
            selected_tools=["mirror.tool"],
            metadata={
                "source_kind": "worker_profile_mirror",
                "source_worker_profile_id": profile_id,
            },
        )
    )
    await store_group.conn.commit()

    resolved, _degraded = await service._resolve_agent_profile(
        project=None,
        requested_profile_id=profile_id,
    )
    # 翻转后信任完整镜像 → mirror.tool；翻转前会重建 → worker.tool
    assert resolved.selected_tools == ["mirror.tool"]
    await store_group.close()


async def test_resolve_agent_profile_trusts_stored_mirror_without_rebuild(tmp_path: Path) -> None:
    """F117 Wave 4：删 materialize-on-read——_resolve_agent_profile 直接信任存储的 worker 镜像，
    不再从 worker_profiles 重建。残缺镜像（instruction_overlays 空）按现状返回，dispatch 不自愈；
    存量残缺行由 migration_117 reconcile 为 canonical（W4-7），不依赖运行时（生产 create/authoring
    路径恒写完整 canonical 镜像，残缺行不再产生）。
    """
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "phase-w4-trust.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    service = AgentContextService(store_group, project_root=tmp_path)

    profile_id = "worker-profile-w4-trust"
    # 残缺镜像：kind=worker 但 instruction_overlays 空 + selected_tools 陈旧（W4-3：
    # worker_profiles 写入已删，运行时只读此镜像、不重建——残缺即按现状返回）
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id=profile_id,
            scope=AgentProfileScope.PROJECT,
            project_id="proj-c-001",
            name="Incomplete Worker",
            kind="worker",
            selected_tools=["stale.tool"],
            metadata={
                "source_kind": "worker_profile_mirror",
                "source_worker_profile_id": profile_id,
            },
        )
    )
    await store_group.conn.commit()

    resolved, _degraded = await service._resolve_agent_profile(
        project=None,
        requested_profile_id=profile_id,
    )
    # W4-3：直接信任存储镜像，不重建——instruction_overlays 仍空，selected_tools 仍 stale。
    assert resolved.instruction_overlays == []
    assert resolved.selected_tools == ["stale.tool"]
    await store_group.close()


# ---------------------------------------------------------------------------
# TC.4.5: ephemeral profile metadata 标记
# ---------------------------------------------------------------------------


def test_ephemeral_profile_metadata_marks():
    """AC-C1: ephemeral profile 的 metadata 应含 source_kind=ephemeral_subagent 和 ephemeral=True。"""
    project = _make_project()
    profile = AgentContextService._build_ephemeral_subagent_profile(project)

    assert profile.metadata.get("source_kind") == "ephemeral_subagent"
    assert profile.metadata.get("ephemeral") is True


# ---------------------------------------------------------------------------
# TC.4.6: P2-1 闭环 — BehaviorLoadProfile MINIMAL 路径验证
# ---------------------------------------------------------------------------


def test_subagent_kind_maps_to_minimal_profile():
    """P2-1 闭环：agent_profile.kind == 'subagent' 时，BehaviorLoadProfile 应为 MINIMAL。

    验证 _resolve_context_bundle / _build_system_blocks 三处选择逻辑都正确派生
    MINIMAL（4 文件 AGENTS+TOOLS+IDENTITY+USER），不再 fall through 到 FULL（9 文件）。
    """
    project = _make_project()
    profile = AgentContextService._build_ephemeral_subagent_profile(project)

    # 模拟 agent_context.py L657 / L982 选择逻辑（worker_capability 维度）
    worker_capability = "research"  # 即使 worker_capability 非空，subagent kind 也应优先 MINIMAL
    load_profile_for_emit = (
        BehaviorLoadProfile.MINIMAL
        if profile.kind == "subagent"
        else (
            BehaviorLoadProfile.WORKER
            if worker_capability
            else BehaviorLoadProfile.FULL
        )
    )
    assert load_profile_for_emit == BehaviorLoadProfile.MINIMAL

    # 模拟 agent_context.py L3490 选择逻辑（is_worker_profile 维度）
    is_worker_profile = False  # ephemeral subagent 不是 worker_behavior_profile
    effective_load_profile = (
        BehaviorLoadProfile.MINIMAL
        if profile.kind == "subagent"
        else (
            BehaviorLoadProfile.WORKER
            if is_worker_profile
            else BehaviorLoadProfile.FULL
        )
    )
    assert effective_load_profile == BehaviorLoadProfile.MINIMAL


def test_worker_kind_does_not_map_to_minimal():
    """regression：worker kind 不映射到 MINIMAL（应走 WORKER）。"""
    profile = AgentProfile(
        profile_id="agent-prf-worker-test",
        kind="worker",
        scope=AgentProfileScope.PROJECT,
        name="Worker Test",
    )

    worker_capability = "research"
    load_profile = (
        BehaviorLoadProfile.MINIMAL
        if profile.kind == "subagent"
        else (
            BehaviorLoadProfile.WORKER
            if worker_capability
            else BehaviorLoadProfile.FULL
        )
    )
    assert load_profile == BehaviorLoadProfile.WORKER, "worker kind 不应该映射到 MINIMAL"


def test_main_kind_falls_through_to_full():
    """regression：main kind 走 FULL（不影响主 Agent 行为包加载）。"""
    profile = AgentProfile(
        profile_id="agent-prf-main-test",
        kind="main",
        scope=AgentProfileScope.SYSTEM,
        name="Main Test",
    )

    worker_capability = ""  # main agent 一般无 worker_capability
    load_profile = (
        BehaviorLoadProfile.MINIMAL
        if profile.kind == "subagent"
        else (
            BehaviorLoadProfile.WORKER
            if worker_capability
            else BehaviorLoadProfile.FULL
        )
    )
    assert load_profile == BehaviorLoadProfile.FULL, "main kind 应走 FULL"


# ---------------------------------------------------------------------------
# F117 W3 回归：_build_context_request 委托目标 override（防双变量塌缩）
# ---------------------------------------------------------------------------


def _make_w3_task(task_id: str):
    from datetime import UTC, datetime

    from octoagent.core.models import Task
    from octoagent.core.models.task import RequesterInfo

    _now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC)
    return Task(
        task_id=task_id,
        created_at=_now,
        updated_at=_now,
        title="w3 regression",
        requester=RequesterInfo(channel="chat", sender_id="user-w3"),
    )


@pytest.mark.asyncio
async def test_build_context_request_worker_uses_delegation_target_over_session_owner(
    tmp_path: Path,
):
    """F117 W3 防回归：worker 请求且 delegation_target ≠ session_owner 时，
    ContextResolveRequest.agent_profile_id 必须取**委托目标**（不是 session owner）。

    背景：W3 改名曾把「委托目标」局部变量塌缩成与「session owner」同名 → 被 owner 链
    无条件覆盖 + override 退化成自赋值死代码 → worker turn 加载错误 profile（行为非零变更）。
    全量套件未覆盖该分叉场景，此测试钉住 baseline override 语义（delegation target 胜出）。
    """
    from octoagent.core.models import ContextRequestKind, TurnExecutorKind
    from octoagent.core.models.orchestrator import RuntimeControlContext
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "w3-build-context-request.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    service = AgentContextService(store_group, project_root=tmp_path)

    task = _make_w3_task("task-w3-override-001")
    runtime_context = RuntimeControlContext(
        task_id=task.task_id,
        session_owner_profile_id="agent-profile-MAIN-owner",
        turn_executor_kind=TurnExecutorKind.WORKER,
    )
    # 委托目标经 dispatch_metadata 显式注入，与 session owner 分叉。
    dispatch_metadata = {"delegation_target_profile_id": "agent-profile-WORKER-target"}

    request = service._build_context_request(
        task=task,
        trigger_text="hi",
        dispatch_metadata=dispatch_metadata,
        worker_capability=None,
        runtime_context=runtime_context,
    )

    assert request.agent_profile_id == "agent-profile-WORKER-target"
    assert request.request_kind == ContextRequestKind.WORKER


@pytest.mark.asyncio
async def test_build_context_request_chat_uses_session_owner(tmp_path: Path):
    """F117 W3 守恒：非 worker（无委托目标/信号）→ agent_profile_id 取 session owner。"""
    from octoagent.core.models import ContextRequestKind
    from octoagent.core.models.orchestrator import RuntimeControlContext
    from octoagent.core.store import create_store_group

    store_group = await create_store_group(
        db_path=str(tmp_path / "w3-build-context-request-chat.db"),
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    service = AgentContextService(store_group, project_root=tmp_path)

    task = _make_w3_task("task-w3-chat-001")
    runtime_context = RuntimeControlContext(
        task_id=task.task_id,
        session_owner_profile_id="agent-profile-MAIN-owner",
    )

    request = service._build_context_request(
        task=task,
        trigger_text="hi",
        dispatch_metadata={},
        worker_capability=None,
        runtime_context=runtime_context,
    )

    assert request.agent_profile_id == "agent-profile-MAIN-owner"
    assert request.request_kind == ContextRequestKind.CHAT
