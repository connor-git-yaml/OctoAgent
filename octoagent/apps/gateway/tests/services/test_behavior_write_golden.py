"""F108a W1 C2 golden 对账测试：behavior 写入两入口收口后的行为锚。

D12 写核收口（prepare_behavior_file_write / commit_behavior_file_write）的
边界由双评审 O1/F2/F8 收敛：**仅写核**进 helper；两入口各自的错误契约、
proposal 门、事件、onboarding marker、cache 失效保持收口前行为。本文件把
这些可观测行为钉死为 golden——后续 wave 若动到相关文件，行为漂移在此报警。

两入口：
- control_plane action ``behavior.write_file``（worker_service handler，
  经 ``ControlPlaneService.execute_action`` 公共契约）
- builtin tool ``behavior.write_file``（misc_tools，经 _CaptureBroker 捕获
  handler 直调，e2e_live/helpers/factories.py 同款模式）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from octoagent.core.behavior_workspace import (
    BEHAVIOR_FILE_BUDGETS,
    PendingBehaviorWrite,
    commit_behavior_file_write,
    load_onboarding_state,
    prepare_behavior_file_write,
)
from octoagent.core.models import (
    ActionRequestEnvelope,
    ControlPlaneActor,
    ControlPlaneSurface,
)
from octoagent.core.store import create_store_group
from octoagent.gateway.services.builtin_tools import misc_tools
from octoagent.gateway.services.builtin_tools._deps import ToolDeps
from octoagent.gateway.services.control_plane import ControlPlaneService
from octoagent.gateway.services.execution_context import (
    ExecutionRuntimeContext,
    bind_execution_context,
)
from octoagent.gateway.services.sse_hub import SSEHub
from octoagent.provider.dx.project_migration import ProjectWorkspaceMigrationService
from octoagent.provider.dx.telegram_pairing import TelegramStateStore
from ulid import ULID


USER_MD_BUDGET = BEHAVIOR_FILE_BUDGETS["USER.md"]


def _action_request(params: dict[str, Any]) -> ActionRequestEnvelope:
    return ActionRequestEnvelope(
        request_id=str(ULID()),
        action_id="behavior.write_file",
        surface=ControlPlaneSurface.WEB,
        actor=ControlPlaneActor(actor_id="user:web", actor_label="Owner"),
        params=params,
    )


async def _make_control_plane(tmp_path: Path, *, snapshot_store: Any = None):
    store_group = await create_store_group(
        str(tmp_path / "gateway.db"),
        str(tmp_path / "artifacts"),
    )
    await ProjectWorkspaceMigrationService(
        project_root=tmp_path,
        store_group=store_group,
    ).ensure_default_project()
    control_plane = ControlPlaneService(
        project_root=tmp_path,
        store_group=store_group,
        sse_hub=SSEHub(),
        telegram_state_store=TelegramStateStore(tmp_path),
        snapshot_store=snapshot_store,
    )
    return control_plane, store_group


class _RecordingSnapshotStore:
    """F146 件②：记录 live-state 同步调用的最小替身。"""

    def __init__(self) -> None:
        self.live: dict[str, str] = {}

    def update_live_state(self, key: str, content: str) -> None:
        self.live[key] = content

    def get_live_state(self, key: str) -> str | None:
        return self.live.get(key)


class _AutoApproveGate:
    """F136 后 REVIEW_REQUIRED + confirmed=true 须经服务端审批批准才落盘。

    golden 关注写核行为对齐（非审批语义），注入自动批准替身让写路径可达；
    审批语义本身在 test_f136_write_approval.py 用真 ApprovalGate 钉死。
    """

    async def request_approval(self, **kwargs: Any) -> Any:
        from octoagent.gateway.harness.approval_gate import ApprovalHandle

        handle = ApprovalHandle()
        handle.operator = "user:test"
        return handle

    async def wait_for_decision(self, handle: Any, timeout_seconds: float = 300.0) -> str:
        return "approved"


async def _capture_behavior_tool(tmp_path: Path, store_group: Any):
    """misc_tools 注册捕获（factories._CaptureBroker 同款）。"""
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
        _approval_gate=_AutoApproveGate(),
    )
    await misc_tools.register(_CaptureBroker(), deps)
    handler = captured.get("behavior.write_file")
    assert handler is not None, "behavior.write_file handler 应已注册"

    runtime_ctx = ExecutionRuntimeContext(
        task_id="task-golden",
        trace_id="trace-golden",
        session_id="session-golden",
        worker_id="worker.general",
        backend="inline",
        console=None,  # behavior.write_file 不触碰 console
    )

    async def _call(**kwargs: Any) -> Any:
        with bind_execution_context(runtime_ctx):
            return await handler(**kwargs)

    return _call


# ---------------------------------------------------------------------------
# A. control_plane action 入口（worker_service._handle_behavior_write_file）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_write_success_golden(tmp_path: Path) -> None:
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        result = await control_plane.execute_action(
            _action_request({"file_id": "USER.md", "content": "# 用户偏好\n中文交流\n"})
        )
        assert result.code == "BEHAVIOR_FILE_WRITTEN"
        assert result.message == "已保存行为文件"
        assert result.data["file_id"] == "USER.md"
        resolved = Path(result.data["resolved_path"])
        assert resolved.read_text(encoding="utf-8") == "# 用户偏好\n中文交流\n"
        assert [(ref.resource_type, ref.resource_id) for ref in result.resource_refs] == [
            ("agent_profiles", "agent:profiles")
        ]
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_action_write_user_md_syncs_live_state(tmp_path: Path) -> None:
    """F146 件②：Web 编辑器保存 USER.md 后同步 SnapshotStore live state——
    notifications quiet hours / user_profile.read 等读点无需重启即读到新内容。"""
    snapshot_store = _RecordingSnapshotStore()
    snapshot_store.live["USER.md"] = "# 旧内容\n"
    control_plane, store_group = await _make_control_plane(
        tmp_path, snapshot_store=snapshot_store
    )
    try:
        new_content = "# 用户偏好\n改成早上 7 点提醒\n"
        result = await control_plane.execute_action(
            _action_request({"file_id": "USER.md", "content": new_content})
        )
        assert result.code == "BEHAVIOR_FILE_WRITTEN"
        assert snapshot_store.live["USER.md"] == new_content  # live state 已同步

        # 非 USER.md 文件不触碰 live state（live state 只有 USER.md/MEMORY.md 两键）
        await control_plane.execute_action(
            _action_request({"file_id": "AGENTS.md", "content": "# 规则\n"})
        )
        assert set(snapshot_store.live.keys()) == {"USER.md"}
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_action_write_invalid_file_id_golden(tmp_path: Path) -> None:
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        result = await control_plane.execute_action(
            _action_request({"file_id": "EVIL_NOT_EXIST.md", "content": "x"})
        )
        assert result.code == "INVALID_FILE_ID"
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_action_write_budget_exceeded_golden(tmp_path: Path) -> None:
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        over = USER_MD_BUDGET + 7
        result = await control_plane.execute_action(
            _action_request({"file_id": "USER.md", "content": "x" * over})
        )
        assert result.code == "BUDGET_EXCEEDED"
        # 错误消息格式与收口前逐字一致（含三个数字字段）
        assert f"内容超出字符预算 7 字符" in result.message
        assert f"当前 {over}/预算 {USER_MD_BUDGET}" in result.message
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# B. builtin tool 入口（misc_tools.behavior_write_file）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_write_proposal_gate_golden(tmp_path: Path) -> None:
    """REVIEW_REQUIRED 且未 confirmed：proposal 门卡在预算检查与写入之间。"""
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)
        result = await handler(file_id="USER.md", content="hello", confirmed=False)
        assert result.status == "skipped"
        assert result.proposal is True
        assert result.written is False
        assert result.chars_written == 0
        assert result.budget_chars == USER_MD_BUDGET
        assert not Path(result.target).exists(), "proposal 阶段不得触盘"
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_tool_write_confirmed_success_golden(tmp_path: Path) -> None:
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)
        content = "# USER\n喜欢喝美式\n"
        result = await handler(file_id="USER.md", content=content, confirmed=True)
        assert result.status == "written"
        assert result.written is True
        assert result.chars_written == len(content)
        assert result.bytes_written == len(content.encode("utf-8"))
        assert result.preview == content[:200]
        assert result.budget_chars == USER_MD_BUDGET
        assert Path(result.target).read_text(encoding="utf-8") == content
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_tool_write_invalid_file_id_golden(tmp_path: Path) -> None:
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)
        result = await handler(file_id="EVIL_NOT_EXIST.md", content="x", confirmed=True)
        assert result.status == "rejected"
        assert result.reason.startswith("INVALID_FILE_ID")
        assert result.target == "behavior_workspace"
        assert result.written is False
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_tool_write_budget_exceeded_golden(tmp_path: Path) -> None:
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)
        over = USER_MD_BUDGET + 11
        result = await handler(file_id="USER.md", content="x" * over, confirmed=True)
        assert result.status == "rejected"
        assert result.reason == "BUDGET_EXCEEDED: exceeded by 11 chars"
        assert result.chars_written == 0
        assert result.budget_chars == USER_MD_BUDGET
        assert not Path(result.target).exists(), "超预算不得触盘"
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_tool_write_bootstrap_marker_golden(tmp_path: Path) -> None:
    """BOOTSTRAP.md + <!-- COMPLETED --> marker：onboarding 副作用保持在调用方。"""
    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)
        content = "# BOOTSTRAP\n完成。\n<!-- COMPLETED -->\n"
        result = await handler(file_id="BOOTSTRAP.md", content=content, confirmed=True)
        assert result.status == "written"
        assert result.onboarding_completed is True
        assert load_onboarding_state(tmp_path).is_completed()
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_action_write_oserror_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入异常包装留在 control_plane caller（FILE_WRITE_ERROR 契约）。"""
    from octoagent.gateway.services.control_plane import worker_service as ws_module

    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        def _boom(pending: Any, content: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ws_module, "commit_behavior_file_write", _boom)
        result = await control_plane.execute_action(
            _action_request({"file_id": "USER.md", "content": "x"})
        )
        assert result.code == "FILE_WRITE_ERROR"
        assert result.message == "写入文件失败: disk full"
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_tool_write_oserror_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入异常包装留在 builtin tool caller（rejected BehaviorWriteFileResult 契约）。"""
    import octoagent.core.behavior_workspace as bw

    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)

        def _boom(pending: Any, content: str) -> None:
            raise OSError("disk full")

        # misc_tools 经函数内 lazy import 解析 package 属性，patch package 即生效
        monkeypatch.setattr(bw, "commit_behavior_file_write", _boom)
        result = await handler(file_id="USER.md", content="x", confirmed=True)
        assert result.status == "rejected"
        assert result.reason == "FILE_WRITE_ERROR: disk full"
        assert result.written is False
    finally:
        await store_group.close()


@pytest.mark.asyncio
async def test_tool_write_invalidates_pack_cache_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cache 失效副作用留在 builtin tool caller：写入成功后必被调用一次。"""
    from octoagent.gateway.services import agent_decision as ad_module

    control_plane, store_group = await _make_control_plane(tmp_path)
    try:
        handler = await _capture_behavior_tool(tmp_path, store_group)
        calls: list[Any] = []

        def _spy(*, project_root: Path) -> None:
            calls.append(project_root)

        monkeypatch.setattr(ad_module, "invalidate_behavior_pack_cache", _spy)
        result = await handler(file_id="USER.md", content="ok", confirmed=True)
        assert result.status == "written"
        assert calls == [tmp_path]
    finally:
        await store_group.close()


# ---------------------------------------------------------------------------
# C. 写核单元（write.py 本体）
# ---------------------------------------------------------------------------


def test_prepare_does_not_touch_disk(tmp_path: Path) -> None:
    pending = prepare_behavior_file_write(
        tmp_path, "USER.md", "abc", agent_slug="main", project_slug="default"
    )
    assert isinstance(pending, PendingBehaviorWrite)
    assert pending.budget["within_budget"] is True
    assert pending.budget["current_chars"] == 3
    assert pending.budget["budget_chars"] == USER_MD_BUDGET
    assert not pending.resolved.exists(), "prepare 阶段不得触盘"


def test_prepare_invalid_file_id_raises_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prepare_behavior_file_write(
            tmp_path, "EVIL_NOT_EXIST.md", "x", agent_slug="main", project_slug="default"
        )


def test_commit_creates_parents_and_writes_utf8(tmp_path: Path) -> None:
    pending = prepare_behavior_file_write(
        tmp_path, "USER.md", "你好", agent_slug="main", project_slug="default"
    )
    commit_behavior_file_write(pending, "你好")
    assert pending.resolved.read_text(encoding="utf-8") == "你好"
