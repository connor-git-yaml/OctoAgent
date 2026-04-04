"""Feature 061 T-033: Bootstrap 简化端到端验证

覆盖场景:
1. Worker 创建后 bootstrap 内容符合预期（shared + role_card）
2. 不同角色卡片的 Worker 行为差异符合预期
3. SC-007: 代码库中无 Worker Type 多模板遗留
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octoagent.core.models import OwnerProfile
from octoagent.core.models.agent_context import AgentRuntime, AgentRuntimeRole
from octoagent.core.store import create_store_group
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.delegation_plane import DelegationPlaneService
from octoagent.gateway.services.llm_service import LLMService
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.gateway.services.task_runner import TaskRunner
from octoagent.tooling import ToolBroker


# ============================================================
# 辅助函数
# ============================================================


async def _setup_services(tmp_path: Path):
    """创建测试运行时服务"""
    from octoagent.core.models import (
        Project,
        ProjectSelectorState,
        Workspace,
    )

    store_group = await create_store_group(
        str(tmp_path / "test.db"),
        str(tmp_path / "artifacts"),
    )
    await store_group.project_store.create_project(
        Project(
            project_id="project-default",
            slug="default",
            name="Default Project",
            is_default=True,
        )
    )
    await store_group.project_store.create_workspace(
        Workspace(
            workspace_id="workspace-default",
            project_id="project-default",
            slug="primary",
            name="Primary",
            root_path=str(tmp_path),
        )
    )
    await store_group.project_store.save_selector_state(
        ProjectSelectorState(
            selector_id="selector-web",
            surface="web",
            active_project_id="project-default",
            active_workspace_id="",
            source="tests",
        )
    )
    await store_group.conn.commit()

    sse_hub = SSEHub()
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    delegation_plane = DelegationPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=sse_hub,
        capability_pack=capability_pack,
    )
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        llm_service=LLMService(),
        delegation_plane=delegation_plane,
    )
    capability_pack.bind_delegation_plane(delegation_plane)
    capability_pack.bind_task_runner(task_runner)
    await capability_pack.startup()
    await task_runner.startup()

    return store_group, capability_pack, task_runner


# ============================================================
# 场景 1: Worker 创建后 bootstrap = shared + role_card
# ============================================================


async def test_worker_bootstrap_is_shared_only(tmp_path: Path) -> None:
    """Worker 创建后 bootstrap 内容仅含 shared 模板"""
    store_group, capability_pack, task_runner = await _setup_services(tmp_path)

    try:
        await store_group.agent_context_store.save_owner_profile(
            OwnerProfile(
                owner_profile_id="owner-profile-default",
                timezone="Asia/Shanghai",
                locale="zh-CN",
            )
        )

        # Feature 065: WorkerType 枚举已删除，测试各字符串标签
        for wtype in ["general", "ops", "research", "dev"]:
            rendered = await capability_pack.render_bootstrap_context(
                worker_type=wtype,
                project_id="project-default",
                surface="web",
            )

            # 只有 bootstrap:shared
            file_ids = [item["file_id"] for item in rendered]
            assert file_ids == ["bootstrap:shared"], (
                f"worker_type {wtype} 的 bootstrap 应只有 shared，"
                f"但得到 {file_ids}"
            )

            # 内容包含核心元信息
            content = rendered[0]["content"]
            assert "Project: Default Project" in content
            assert f"Worker Type: {wtype}" in content
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


# ============================================================
# 场景 2: role_card 行为差异
# ============================================================


async def test_role_card_present_on_agent_runtime(tmp_path: Path) -> None:
    """AgentRuntime 正确保存和读取 role_card 字段"""
    store_group, capability_pack, task_runner = await _setup_services(tmp_path)

    try:
        # 创建带 role_card 的 Agent Runtime
        runtime = AgentRuntime(
            agent_runtime_id="runtime-test-rolecard",
            project_id="project-default",
            workspace_id="",
            agent_profile_id="agent-profile-default",
            role=AgentRuntimeRole.WORKER,
            name="Test Worker",
            role_card=(
                "你是一个专注于前端开发的 Worker。"
                "优先使用 filesystem 和 terminal 工具完成 React/TypeScript 任务。"
                "遇到后端问题时建议委派给其他 Worker。"
            ),
            permission_preset="normal",
        )
        await store_group.agent_context_store.save_agent_runtime(runtime)

        # 读取验证
        loaded = await store_group.agent_context_store.get_agent_runtime(
            "runtime-test-rolecard"
        )
        assert loaded is not None
        assert "前端开发" in loaded.role_card
        assert loaded.permission_preset == "normal"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_role_card_empty_by_default(tmp_path: Path) -> None:
    """默认创建的 AgentRuntime 角色卡片为空"""
    store_group, capability_pack, task_runner = await _setup_services(tmp_path)

    try:
        runtime = AgentRuntime(
            agent_runtime_id="runtime-test-empty-rolecard",
            project_id="project-default",
            workspace_id="",
            agent_profile_id="agent-profile-default",
            role=AgentRuntimeRole.WORKER,
            name="Plain Worker",
        )
        await store_group.agent_context_store.save_agent_runtime(runtime)

        loaded = await store_group.agent_context_store.get_agent_runtime(
            "runtime-test-empty-rolecard"
        )
        assert loaded is not None
        assert loaded.role_card == ""
        assert loaded.permission_preset == "normal"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


# ============================================================
# 场景 3: SC-007 — 无 Worker Type 多模板遗留
# ============================================================


async def test_no_worker_type_specific_templates_in_pack(tmp_path: Path) -> None:
    """SC-007: BundledCapabilityPack 中无 Worker Type 特定模板"""
    store_group, capability_pack, task_runner = await _setup_services(tmp_path)

    try:
        pack = await capability_pack.get_pack()

        type_specific_ids = {
            "bootstrap:general",
            "bootstrap:ops",
            "bootstrap:research",
            "bootstrap:dev",
        }

        for f in pack.bootstrap_files:
            assert f.file_id not in type_specific_ids, (
                f"发现遗留的 Worker Type 模板: {f.file_id}"
            )

        # 应该只有 1 个 bootstrap 文件
        assert len(pack.bootstrap_files) == 1
        assert pack.bootstrap_files[0].file_id == "bootstrap:shared"
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()


async def test_all_worker_types_share_unified_tool_groups(tmp_path: Path) -> None:
    """SC-007: Feature 065 -- 所有 worker type 查询都返回同一 profile"""
    store_group, capability_pack, task_runner = await _setup_services(tmp_path)

    try:
        # Feature 065: 所有查询都回退到 general profile
        general = capability_pack.get_worker_profile("general")
        for wt in ["ops", "research", "dev"]:
            assert capability_pack.get_worker_profile(wt) is general, (
                f"worker_type {wt} 应返回同一 general profile"
            )

        # 包含所有必要分组
        required_groups = {"project", "filesystem", "terminal", "memory", "mcp", "skills", "runtime"}
        assert required_groups.issubset(set(general.default_tool_groups))
    finally:
        await task_runner.shutdown()
        await store_group.conn.close()
