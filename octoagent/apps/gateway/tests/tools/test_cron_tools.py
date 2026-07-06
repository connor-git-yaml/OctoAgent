"""F132 cron 自助工具行为测试（cron.create / cron.update / cron.delete）。

覆盖 spec AC：
- AC-1.1 create reminder job → 落盘 + scheduler sync + written
- AC-1.2 非法 cron → rejected
- AC-1.2b 纯数字 DOW → rejected（Codex P1-1 off-by-one 防护）
- AC-1.2c 白名单外 action_id → rejected（Codex P1-2）
- AC-1.3 时区缺省走 F115（USER.md）链
- AC-2.1 update enabled-only → 无审批直改
- AC-2.2 update 改 schedule → 走审批
- AC-2.3 update 目标不存在 → rejected
- AC-3.1 delete → 审批通过后删除
- AC-3.2 delete 审批 rejected → 保留
- AC-3.3 delete 无 approval_gate → fail-closed
- AC-4.2 三工具注册进 registry（entrypoints 含 agent_runtime）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octoagent.core.models import AutomationJob, AutomationScheduleKind
from octoagent.core.models.tool_results import CronMutationResult
from octoagent.gateway.services.builtin_tools import cron_tools
from octoagent.gateway.services.control_plane.automation_store import AutomationStore


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


class _CaptureBroker:
    """捕获 register() 里 try_register 的 handler，按 tool name 存。"""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    async def try_register(self, tool_meta, handler):  # noqa: ANN001
        self.handlers[tool_meta.name] = handler
        return SimpleNamespace(registered=True)


def _make_deps(project_root: Path, *, scheduler=None, approval_gate=None):
    """最小 ToolDeps stub。event_store 用 AsyncMock 收审计事件。"""
    event_store = MagicMock()
    event_store.get_next_task_seq = AsyncMock(return_value=1)
    event_store.append_event_committed = AsyncMock()
    stores = SimpleNamespace(event_store=event_store)

    deps = SimpleNamespace(
        project_root=project_root,
        stores=stores,
        _snapshot_store=None,
        _automation_scheduler=scheduler,
        _approval_gate=approval_gate,
        _approval_manager=None,
        _notification_service=None,
        _pack_service=None,
    )
    return deps


async def _register_and_get(deps):
    """注册 cron 工具，返回 {name: handler}。"""
    broker = _CaptureBroker()
    # _registry_register 有全局副作用（ToolRegistry），测试里屏蔽掉避免污染。
    with patch.object(cron_tools, "_registry_register"):
        await cron_tools.register(broker, deps)
    return broker.handlers


def _fake_scheduler():
    sched = SimpleNamespace()
    sched.sync_job = AsyncMock()
    sched.remove_job = AsyncMock()
    return sched


# ---------------------------------------------------------------------------
# AC-1.1 / AC-1.2 / AC-1.2b / AC-1.2c — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_reminder_job(tmp_path: Path) -> None:
    """AC-1.1: reminder_text 路径 → 落 reminder.notify job + scheduler sync + written。"""
    sched = _fake_scheduler()
    deps = _make_deps(tmp_path, scheduler=sched)
    handlers = await _register_and_get(deps)

    result = await handlers["cron.create"](
        name="喝水提醒",
        schedule_kind="cron",
        schedule_expr="0 8 * * *",
        reminder_text="该喝水啦",
    )
    assert isinstance(result, CronMutationResult)
    assert result.status == "written"
    assert result.job_id
    assert result.scheduler_synced is True
    sched.sync_job.assert_awaited_once()

    # 落盘校验：job 存在、action_id=reminder.notify、message 透传
    stored = AutomationStore(tmp_path).get_job(result.job_id)
    assert stored is not None
    assert stored.action_id == "reminder.notify"
    assert stored.params.get("message") == "该喝水啦"
    assert stored.schedule_expr == "0 8 * * *"


@pytest.mark.asyncio
async def test_create_invalid_cron_rejected(tmp_path: Path) -> None:
    """AC-1.2: 非法 cron 表达式 → rejected，不落盘。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    result = await handlers["cron.create"](
        name="坏任务",
        schedule_kind="cron",
        schedule_expr="每天",
        reminder_text="x",
    )
    assert result.status == "rejected"
    assert result.reason
    assert AutomationStore(tmp_path).list_jobs() == []


@pytest.mark.asyncio
async def test_create_numeric_dow_rejected(tmp_path: Path) -> None:
    """AC-1.2b: 纯数字 DOW（0 9 * * 1）→ rejected 提示命名星期（Codex P1-1）。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    result = await handlers["cron.create"](
        name="周提醒",
        schedule_kind="cron",
        schedule_expr="0 9 * * 1",
        reminder_text="交周报",
    )
    assert result.status == "rejected"
    assert "mon" in result.reason  # 引导用命名星期
    assert AutomationStore(tmp_path).list_jobs() == []


@pytest.mark.asyncio
async def test_create_named_dow_accepted(tmp_path: Path) -> None:
    """命名星期 mon 放行（对照 AC-1.2b）。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    result = await handlers["cron.create"](
        name="周提醒",
        schedule_kind="cron",
        schedule_expr="0 9 * * mon",
        reminder_text="交周报",
    )
    assert result.status == "written"


@pytest.mark.asyncio
async def test_create_action_id_not_allowed(tmp_path: Path) -> None:
    """AC-1.2c: 白名单外 action_id（update.apply）→ rejected（Codex P1-2）。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    result = await handlers["cron.create"](
        name="偷偷升级",
        schedule_kind="cron",
        schedule_expr="0 3 * * *",
        action_id="update.apply",
    )
    assert result.status == "rejected"
    assert "action_not_allowed" in result.reason
    assert AutomationStore(tmp_path).list_jobs() == []


@pytest.mark.asyncio
async def test_create_action_id_whitelisted_ok(tmp_path: Path) -> None:
    """白名单内 action_id（memory.consolidate）放行。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    result = await handlers["cron.create"](
        name="定期巩固",
        schedule_kind="cron",
        schedule_expr="0 4 * * *",
        action_id="memory.consolidate",
    )
    assert result.status == "written"
    stored = AutomationStore(tmp_path).get_job(result.job_id)
    assert stored.action_id == "memory.consolidate"


@pytest.mark.asyncio
async def test_create_both_reminder_and_action_rejected(tmp_path: Path) -> None:
    """reminder_text 与 action_id 同传 → rejected。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)
    result = await handlers["cron.create"](
        name="x",
        schedule_kind="cron",
        schedule_expr="0 8 * * *",
        reminder_text="a",
        action_id="reminder.notify",
    )
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_create_timezone_fallback_user_md(tmp_path: Path) -> None:
    """AC-1.3: timezone 缺省 → 走 F115 链读 USER.md user_timezone。"""
    # 造 USER.md 含机器可读时区
    user_md = tmp_path / "behavior" / "system" / "USER.md"
    user_md.parent.mkdir(parents=True, exist_ok=True)
    user_md.write_text('- **user_timezone**: "Asia/Shanghai"\n', encoding="utf-8")

    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)
    result = await handlers["cron.create"](
        name="提醒",
        schedule_kind="cron",
        schedule_expr="0 8 * * *",
        reminder_text="x",
    )
    assert result.status == "written"
    stored = AutomationStore(tmp_path).get_job(result.job_id)
    assert stored.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_create_scheduler_unbound_degraded(tmp_path: Path) -> None:
    """scheduler 未绑定 → job 落盘但 scheduler_synced=False（DP-6 degraded）。"""
    deps = _make_deps(tmp_path, scheduler=None)
    handlers = await _register_and_get(deps)
    result = await handlers["cron.create"](
        name="提醒",
        schedule_kind="interval",
        schedule_expr="1800",
        reminder_text="x",
    )
    assert result.status == "written"
    assert result.scheduler_synced is False
    assert AutomationStore(tmp_path).get_job(result.job_id) is not None


# ---------------------------------------------------------------------------
# AC-2 — update
# ---------------------------------------------------------------------------


def _seed_job(tmp_path: Path, **overrides) -> AutomationJob:
    job = AutomationJob(
        job_id=overrides.get("job_id", "job-seed-1"),
        name=overrides.get("name", "现有提醒"),
        action_id=overrides.get("action_id", "reminder.notify"),
        params=overrides.get("params", {"message": "old"}),
        schedule_kind=overrides.get("schedule_kind", AutomationScheduleKind.CRON),
        schedule_expr=overrides.get("schedule_expr", "0 8 * * *"),
        timezone=overrides.get("timezone", "UTC"),
        enabled=overrides.get("enabled", True),
    )
    AutomationStore(tmp_path).save_job(job)
    return job


@pytest.mark.asyncio
async def test_update_toggle_enabled_no_approval(tmp_path: Path) -> None:
    """AC-2.1: 只改 enabled → 直接生效（无审批）+ scheduler sync。"""
    _seed_job(tmp_path)
    sched = _fake_scheduler()
    deps = _make_deps(tmp_path, scheduler=sched)
    handlers = await _register_and_get(deps)

    # patch gate_destructive_action 确保 enabled-only 不调用它
    with patch(
        "octoagent.gateway.services.builtin_tools.write_approval.gate_destructive_action",
        new=AsyncMock(),
    ) as gate:
        result = await handlers["cron.update"](job_id="job-seed-1", enabled=False)

    assert result.status == "written"
    assert result.approval_requested is False
    gate.assert_not_awaited()
    # 禁用 → scheduler remove（sync_job with remove=True 走 remove_job）
    sched.remove_job.assert_awaited_once()
    assert AutomationStore(tmp_path).get_job("job-seed-1").enabled is False


@pytest.mark.asyncio
async def test_update_schedule_requires_approval(tmp_path: Path) -> None:
    """AC-2.2: 改 schedule_expr → 走审批；approved 才落盘。"""
    _seed_job(tmp_path)
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    approved = SimpleNamespace(decision="approved", reason="")
    with patch(
        "octoagent.gateway.services.builtin_tools.write_approval.gate_destructive_action",
        new=AsyncMock(return_value=approved),
    ) as gate, patch(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        return_value=SimpleNamespace(task_id="t1", session_id="s1"),
    ):
        result = await handlers["cron.update"](
            job_id="job-seed-1", schedule_expr="0 9 * * mon"
        )
    gate.assert_awaited_once()
    assert result.status == "written"
    assert result.approval_requested is True
    assert AutomationStore(tmp_path).get_job("job-seed-1").schedule_expr == "0 9 * * mon"


@pytest.mark.asyncio
async def test_update_schedule_rejected_keeps_old(tmp_path: Path) -> None:
    """改 schedule 审批被拒 → 保留原表达式。"""
    _seed_job(tmp_path)
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    rejected = SimpleNamespace(decision="rejected", reason="用户拒绝")
    with patch(
        "octoagent.gateway.services.builtin_tools.write_approval.gate_destructive_action",
        new=AsyncMock(return_value=rejected),
    ), patch(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        return_value=SimpleNamespace(task_id="t1", session_id="s1"),
    ):
        result = await handlers["cron.update"](
            job_id="job-seed-1", schedule_expr="0 9 * * fri"
        )
    assert result.status == "rejected"
    assert AutomationStore(tmp_path).get_job("job-seed-1").schedule_expr == "0 8 * * *"


@pytest.mark.asyncio
async def test_update_missing_job(tmp_path: Path) -> None:
    """AC-2.3: 目标 job 不存在 → rejected job_not_found。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)
    result = await handlers["cron.update"](job_id="nope", enabled=True)
    assert result.status == "rejected"
    assert "job_not_found" in result.reason


@pytest.mark.asyncio
async def test_update_numeric_dow_rejected_before_approval(tmp_path: Path) -> None:
    """改 schedule 用数字 DOW → 校验先拒，不发起审批。"""
    _seed_job(tmp_path)
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)
    with patch(
        "octoagent.gateway.services.builtin_tools.write_approval.gate_destructive_action",
        new=AsyncMock(),
    ) as gate, patch(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        return_value=SimpleNamespace(task_id="t1", session_id="s1"),
    ):
        result = await handlers["cron.update"](job_id="job-seed-1", schedule_expr="0 9 * * 1")
    assert result.status == "rejected"
    gate.assert_not_awaited()  # 校验失败在审批之前


# ---------------------------------------------------------------------------
# AC-3 — delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_requires_approval_then_deletes(tmp_path: Path) -> None:
    """AC-3.1: delete 走审批；approved → 删除 + scheduler remove。"""
    _seed_job(tmp_path)
    sched = _fake_scheduler()
    deps = _make_deps(tmp_path, scheduler=sched)
    handlers = await _register_and_get(deps)

    approved = SimpleNamespace(decision="approved", reason="")
    with patch(
        "octoagent.gateway.services.builtin_tools.write_approval.gate_destructive_action",
        new=AsyncMock(return_value=approved),
    ) as gate, patch(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        return_value=SimpleNamespace(task_id="t1", session_id="s1"),
    ):
        result = await handlers["cron.delete"](job_id="job-seed-1")
    gate.assert_awaited_once()
    assert result.status == "written"
    assert result.approval_requested is True
    sched.remove_job.assert_awaited_once()
    assert AutomationStore(tmp_path).get_job("job-seed-1") is None


@pytest.mark.asyncio
async def test_delete_rejected_keeps_job(tmp_path: Path) -> None:
    """AC-3.2: 审批 rejected → job 保留，status=rejected。"""
    _seed_job(tmp_path)
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)

    rejected = SimpleNamespace(decision="rejected", reason="用户拒绝")
    with patch(
        "octoagent.gateway.services.builtin_tools.write_approval.gate_destructive_action",
        new=AsyncMock(return_value=rejected),
    ), patch(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        return_value=SimpleNamespace(task_id="t1", session_id="s1"),
    ):
        result = await handlers["cron.delete"](job_id="job-seed-1")
    assert result.status == "rejected"
    assert AutomationStore(tmp_path).get_job("job-seed-1") is not None


@pytest.mark.asyncio
async def test_delete_fail_closed_no_gate(tmp_path: Path) -> None:
    """AC-3.3: approval_gate 缺失 → gate 返回 unavailable → fail-closed 不删。"""
    _seed_job(tmp_path)
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler(), approval_gate=None)
    handlers = await _register_and_get(deps)

    # 用真实 gate_destructive_action（approval_gate=None → unavailable）
    with patch(
        "octoagent.gateway.services.execution_context.get_current_execution_context",
        return_value=SimpleNamespace(task_id="t1", session_id="s1"),
    ):
        result = await handlers["cron.delete"](job_id="job-seed-1")
    assert result.status == "rejected"
    assert AutomationStore(tmp_path).get_job("job-seed-1") is not None


@pytest.mark.asyncio
async def test_delete_missing_job(tmp_path: Path) -> None:
    """delete 目标不存在 → rejected（审批前）。"""
    deps = _make_deps(tmp_path, scheduler=_fake_scheduler())
    handlers = await _register_and_get(deps)
    result = await handlers["cron.delete"](job_id="nope")
    assert result.status == "rejected"
    assert "job_not_found" in result.reason


# ---------------------------------------------------------------------------
# AC-4.2 — registration / entrypoints
# ---------------------------------------------------------------------------


def test_cron_tools_entrypoints() -> None:
    """AC-4.2: 三工具 entrypoints 含 agent_runtime（+ web/telegram）。"""
    eps = cron_tools._TOOL_ENTRYPOINTS
    for name in ("cron.create", "cron.update", "cron.delete"):
        assert name in eps
        assert "agent_runtime" in eps[name]
        assert "web" in eps[name]


@pytest.mark.asyncio
async def test_cron_tools_registered(tmp_path: Path) -> None:
    """AC-4.2: register() 把三工具注册进 broker。"""
    deps = _make_deps(tmp_path)
    handlers = await _register_and_get(deps)
    assert {"cron.create", "cron.update", "cron.delete"} <= set(handlers)


def test_action_allowlist_excludes_high_risk() -> None:
    """白名单不含高危动作（Codex P1-2 回归护栏）。"""
    allow = cron_tools._CRON_AGENT_ACTION_ALLOWLIST
    assert "reminder.notify" in allow
    for danger in ("update.apply", "runtime.restart", "operator.task.cancel", "automation.delete"):
        assert danger not in allow
