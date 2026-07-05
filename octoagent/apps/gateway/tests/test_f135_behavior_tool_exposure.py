"""F135 gap-1：behavior.write_file 对主 Agent（web 来源）的可见性回归锚。

根因（root-cause-gap1.md，经真实 OctoHarness 复现）：`behavior.write_file` 不在
`CoreToolSet.default()` → 对主 Agent 是 Deferred 工具（只在 system prompt 文本列名、无
完整 schema）→ 须先 tool_search 两跳激活 → 弱 model/单轮场景不可靠 → Agent 回复"入口
没暴露给我"，首次见面填画像的引导闭环在生产走不通。

修复=把 `behavior.write_file` 提进 CoreToolSet（发现层），使主 Agent 一跳直接可调用。
**治理不受影响**：写 REVIEW_REQUIRED 文件（USER.md）仍走 handler 内 Two-Phase
proposal→confirm（Core/deferred 是发现层，review_mode 是执行层，两者正交）。

- AC-1.1：CoreToolSet 含 behavior.write_file（纯单元）。
- AC-1.2：真实选择引擎（CapabilityPackService.resolve_profile_first_tools）为主 Agent
  mount behavior.write_file、不再 defer。
- AC-1.3：即便提 Core，REVIEW_REQUIRED 未确认仍返回 proposal（治理未绕过）。
- AC-1.4：confirmed 写 USER.md 落盘 + 产 F107 行为版本记录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from octoagent.core.behavior_workspace import BEHAVIOR_FILE_BUDGETS
from octoagent.core.models.capability import ToolIndexQuery
from octoagent.core.store import create_store_group
from octoagent.gateway.services.builtin_tools import misc_tools
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.capability_pack import CapabilityPackService
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.tooling import ToolBroker
from octoagent.tooling.models import CoreToolSet

USER_MD_BUDGET = BEHAVIOR_FILE_BUDGETS["USER.md"]


# ---------------------------------------------------------------------------
# AC-1.1：CoreToolSet 含 behavior.write_file（纯单元）
# ---------------------------------------------------------------------------


def test_behavior_write_file_is_core() -> None:
    """AC-1.1：behavior.write_file 已进 CoreToolSet.default()（发现层直挂完整 schema）。"""
    core = CoreToolSet.default()
    assert core.is_core("behavior.write_file"), (
        "behavior.write_file 必须是 Core 工具——否则主 Agent 只能 tool_search 两跳激活，"
        "首次引导填 USER.md 闭环脆弱（F135 gap-1 根因）"
    )
    # tool_search 自身仍在 Core（FR-018 不回归）。
    assert core.is_core("tool_search")


# ---------------------------------------------------------------------------
# AC-1.2：真实选择引擎为主 Agent mount behavior.write_file（非 deferred）
# ---------------------------------------------------------------------------


async def _build_capability_pack(tmp_path: Path) -> tuple[CapabilityPackService, Any]:
    """构造带 builtin 工具的真实 CapabilityPackService（test_capability_pack_tools 同款轻量路径）。

    startup() 内部 _register_builtin_tools()（含 misc_tools → behavior.write_file）+ refresh()，
    得到真实 pack。不 mock 选择引擎——走真实 resolve_worker_binding → resolve_profile_first_tools。
    """
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    tool_broker = ToolBroker(event_store=store_group.event_store)
    capability_pack = CapabilityPackService(
        project_root=tmp_path,
        store_group=store_group,
        tool_broker=tool_broker,
    )
    await capability_pack.startup()
    return capability_pack, store_group


async def test_main_web_agent_mounts_behavior_write_file(tmp_path: Path) -> None:
    """AC-1.2：主 Agent（system-default profile）的工具选择 mount behavior.write_file、不 defer。

    模拟 orchestrator._resolve_single_loop_tool_selection 为主 Agent 调 resolve_profile_first_tools。
    """
    capability_pack, store_group = await _build_capability_pack(tmp_path)
    try:
        # 先确认工具在 pack 里（builtin 已注册）。
        pack = await capability_pack.get_pack()
        pack_tool_names = {t.tool_name for t in pack.tools}
        assert "behavior.write_file" in pack_tool_names

        # 主 Agent = 无 requested_profile_id 的 general 单轮；requested_profile_id 空 →
        # resolve_worker_binding 走 builtin_fallback（default_tool_groups 含 "behavior"）。
        selection = await capability_pack.resolve_profile_first_tools(
            ToolIndexQuery(
                query="帮我完成 USER.md 初始化",
                limit=12,
                tool_groups=[],
                worker_type="general",
                tool_profile="standard",
                project_id="",
            ),
            worker_type="general",
            requested_profile_id="",
        )
        deferred_names = {e.get("name") for e in selection.deferred_tool_entries}
        assert "behavior.write_file" in selection.selected_tools, (
            "behavior.write_file 必须 mount（完整 schema 一跳可调）给主 Agent；"
            f"实际 mounted={selection.selected_tools}"
        )
        assert "behavior.write_file" not in deferred_names, (
            "behavior.write_file 不应再被压成 deferred（否则退回 tool_search 两跳）"
        )
        # 不应被任何过滤挡掉。
        blocked_names = {b.tool_name for b in selection.blocked_tools}
        assert "behavior.write_file" not in blocked_names
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# AC-1.3 / AC-1.4：治理未被绕过（提 Core 后仍走 Two-Phase）+ 版本记录
# ---------------------------------------------------------------------------


async def _capture_behavior_tool(tmp_path: Path, store_group: Any):
    """misc_tools 注册捕获 handler 直调（test_behavior_write_golden 同款）。"""
    captured: dict[str, Any] = {}

    class _CaptureBroker:
        async def try_register(self, meta: Any, handler: Any) -> None:
            captured[meta.name] = handler

    deps = ToolDeps(
        project_root=tmp_path,
        stores=store_group,
        tool_broker=None,
        tool_index=None,
        skill_discovery=None,
        memory_console_service=None,
        memory_runtime_service=None,
    )
    await misc_tools.register(_CaptureBroker(), deps)
    handler = captured.get("behavior.write_file")
    assert handler is not None, "behavior.write_file handler 应已注册"

    runtime_ctx = ExecutionRuntimeContext(
        task_id="task-f135",
        trace_id="trace-f135",
        session_id="session-f135",
        worker_id="worker.general",
        backend="inline",
        console=None,
    )

    async def _call(**kwargs: Any) -> Any:
        with bind_execution_context(runtime_ctx):
            return await handler(**kwargs)

    return _call


async def test_behavior_write_review_required_still_two_phase(tmp_path: Path) -> None:
    """AC-1.3：behavior.write_file 提 Core 后，写 REVIEW_REQUIRED 文件（USER.md）未确认仍返回
    proposal（治理执行层不受发现层变更影响，Constitution #4/#7/#10 守住）。"""
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    try:
        call = await _capture_behavior_tool(tmp_path, store_group)
        # confirmed=False → proposal 门卡住，不触盘。
        result = await call(file_id="USER.md", content="# USER\n喜欢美式\n", confirmed=False)
        assert result.status == "skipped"
        assert result.proposal is True
        assert result.written is False
        assert not Path(result.target).exists(), "proposal 阶段绝不触盘（治理未绕过）"
    finally:
        await store_group.close()


async def test_behavior_write_confirmed_records_version(tmp_path: Path) -> None:
    """AC-1.4：confirmed=True 写 USER.md 落盘 + 产 F107 行为版本记录（引导可追溯 + 可恢复）。"""
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    try:
        call = await _capture_behavior_tool(tmp_path, store_group)
        content = "# USER\n喜欢喝美式\n时区 Asia/Shanghai\n"
        result = await call(file_id="USER.md", content=content, confirmed=True)
        assert result.status == "written"
        assert result.written is True
        assert Path(result.target).read_text(encoding="utf-8") == content

        # F107 行为版本记录：从实际落盘路径派生 key，查 behavior_versions 存在该文件的记录。
        from octoagent.core.behavior_workspace import behavior_version_key_from_path

        key = behavior_version_key_from_path(tmp_path, Path(result.target))
        versions = await store_group.behavior_version_store.list_versions(key)
        assert len(versions) >= 1, "confirmed 写 USER.md 应产 F107 行为版本记录（可追溯 + 可恢复）"
    finally:
        await store_group.close()
