"""F117 Wave 2bc 回归测试：镜像完整性 + dropped-fallback 闭合。

Wave 2b read-switch 删除了 capability_pack._resolve_profile_skill_selection 的
worker_profiles metadata fallback——只读统一 agent_profiles(kind=worker) 镜像的 metadata。
本测试断言：当镜像携带 worker 的 capability_provider_selection metadata 时，运行时仍能解析
（双评审 Opus MED-3 指出此路径此前无测试覆盖，会掩盖真实 dropped-fallback 回归）。
"""

from __future__ import annotations

from pathlib import Path

from octoagent.core.models import AgentProfile, AgentProfileScope
from octoagent.core.store import create_store_group
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.tooling import ToolBroker


async def _build_capability_pack(tmp_path: Path) -> tuple:
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=ToolBroker(event_store=store_group.event_store),
    )
    await capability_pack.startup()
    return store_group, capability_pack


async def test_worker_mirror_metadata_capability_selection_resolves(tmp_path: Path) -> None:
    """镜像携 capability_provider_selection metadata → 运行时解析（dropped-fallback 闭合）。"""
    store_group, capability_pack = await _build_capability_pack(tmp_path)
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="worker-profile-selection-x",
            scope=AgentProfileScope.PROJECT,
            project_id="project-default",
            name="Selection Worker",
            kind="worker",
            metadata={
                "source_kind": "worker_profile_mirror",
                "source_worker_profile_id": "worker-profile-selection-x",
                "capability_provider_selection": {
                    "selected_item_ids": ["skill:coding-agent"],
                    "disabled_item_ids": ["skill:other"],
                },
            },
        )
    )
    await store_group.conn.commit()

    selected, disabled = await capability_pack._resolve_profile_skill_selection(
        profile_id="worker-profile-selection-x"
    )
    assert "skill:coding-agent" in selected
    assert "skill:other" in disabled
    await store_group.close()


async def test_worker_mirror_resolves_tool_universe_with_9_fields(tmp_path: Path) -> None:
    """镜像携 9 工具字段 → resolve_worker_binding 返回 worker 工具（非 builtin_fallback）。

    断言 Wave 2bc 后：只要镜像（kind=worker / source_kind 标记）携工具字段，read-switch 即解析
    其 selected_tools / default_tool_groups（draft/created worker 经镜像完整性 fix 后均如此）。
    """
    store_group, capability_pack = await _build_capability_pack(tmp_path)
    await store_group.agent_context_store.save_agent_profile(
        AgentProfile(
            profile_id="worker-profile-tooluniverse-y",
            scope=AgentProfileScope.PROJECT,
            project_id="project-default",
            name="Tool Universe Worker",
            kind="worker",
            model_alias="cheap",
            tool_profile="standard",
            summary="工具宇宙 worker",
            default_tool_groups=["network", "project"],
            selected_tools=["web.search"],
            runtime_kinds=["worker"],
            metadata={
                "source_kind": "worker_profile_mirror",
                "source_worker_profile_id": "worker-profile-tooluniverse-y",
            },
        )
    )
    await store_group.conn.commit()

    binding = await capability_pack.resolve_worker_binding(
        requested_profile_id="worker-profile-tooluniverse-y"
    )
    assert binding.source_kind == "worker_profile"
    assert binding.profile_id == "worker-profile-tooluniverse-y"
    assert binding.model_alias == "cheap"
    assert "web.search" in binding.selected_tools
    assert "network" in binding.default_tool_groups
    await store_group.close()
