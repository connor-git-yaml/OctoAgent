"""cron_tools：cron 自助工具（F132 — OC-5）。

让主 Agent 按自然语言帮用户建/改/删定时任务。后端 CRUD（F026 AutomationStore + Scheduler）
已在，本模块补 agent 写工具暴露。只读 `cron.list` 在 runtime_tools.py（不动）。

工具列表：
- cron.create：新建定时任务（reminder_text 默认路径 / action_id 白名单高级路径）。REVERSIBLE。
- cron.update：改已有任务（enabled-only 直改 / 其他字段走审批）。
- cron.delete：删任务（破坏性，走服务端 ApprovalGate Two-Phase）。

设计要点（spec §1）：
- DP-3 NL↔cron 由 LLM 自译，工具只校验（Constitution #9，不写规则引擎）。
- DP-3 【Codex P1-1】APScheduler DOW = Monday=0（非 Unix Monday=1）：拒绝纯数字 DOW，
  强制命名星期（mon/tue/...）防每周提醒 off-by-one 错一天。
- DP-2/P1-2 action_id 路径仅白名单安全动作，拒 update.apply 等高危（否则免审批排高危操作）。
- DP-4 时区走 F115 降级链（USER.md > env > UTC），复用 extract_user_timezone_from_user_md。
- DP-5 破坏性操作（delete / update 改 schedule）走 gate_destructive_action。
- DP-6 落盘后调 scheduler.sync_job/remove_job（否则要等重启才生效）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel
from ulid import ULID

from octoagent.core.models import (
    AutomationJob,
    AutomationScheduleKind,
    CronMutationResult,
)
from octoagent.core.models.enums import ActorType, EventType, SideEffectLevel
from octoagent.core.models.event import Event
from octoagent.gateway.harness.tool_registry import ToolEntry
from octoagent.gateway.harness.tool_registry import register as _registry_register
from octoagent.gateway.services.control_plane.automation_store import AutomationStore
from octoagent.tooling import reflect_tool_schema, tool_contract

from ._deps import ToolDeps

log = structlog.get_logger(__name__)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# 【Codex P1-2】agent 工具 action_id 路径安全白名单——只允许无破坏性的定时化动作。
# 拒绝 update.apply / runtime.restart / operator.* 等：automation scheduler 触发时按
# SYSTEM surface 直接 execute_action，coordinator 不按 approval_hint 拦截，放任 agent 排
# 任意 action = 让 Agent 免审批安排高风险操作（绕过 Constitution #4/#7）。高危动作的
# 定时化留 Web/CLI 显式操作。
_CRON_AGENT_ACTION_ALLOWLIST: frozenset[str] = frozenset({
    "reminder.notify",
    "memory.consolidate",
    "memory.profile_generate",
})

_JOB_NAME_MAX = 120
_REMINDER_MSG_MAX = 2000

_TOOL_ENTRYPOINTS: dict[str, frozenset[str]] = {
    "cron.create": frozenset({"agent_runtime", "web", "telegram"}),
    "cron.update": frozenset({"agent_runtime", "web", "telegram"}),
    "cron.delete": frozenset({"agent_runtime", "web", "telegram"}),
}

# APScheduler crontab 命名星期（无歧义，绕开 Monday=0 数字陷阱）。
_NAMED_DOW = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})


# --------------------------------------------------------------------------
# 时区解析（F115 降级链复用）
# --------------------------------------------------------------------------


def _resolve_timezone(deps: ToolDeps, explicit_tz: str) -> str:
    """解析生效时区，降级链：显式参数 > USER.md user_timezone > env > UTC（F115）。

    复用 daily_routine_config.extract_user_timezone_from_user_md（已 zoneinfo 校验）。
    """
    if explicit_tz.strip():
        # 显式传入也校验合法性——非法 IANA 名不静默落库。
        try:
            import zoneinfo

            zoneinfo.ZoneInfo(explicit_tz.strip())
            return explicit_tz.strip()
        except Exception:  # noqa: BLE001
            log.warning("cron_explicit_timezone_invalid", tz=explicit_tz)
            # 落到降级链（不因非法显式值直接 UTC，仍尝试 USER.md）

    # USER.md：优先从 snapshot live state 读，回退磁盘。
    user_md_content: str | None = None
    snapshot_store = deps._snapshot_store
    if snapshot_store is not None:
        try:
            user_md_content = snapshot_store.get_live_state("USER.md")
        except Exception:  # noqa: BLE001
            user_md_content = None
    if user_md_content is None:
        user_md = deps.project_root / "behavior" / "system" / "USER.md"
        try:
            user_md_content = user_md.read_text(encoding="utf-8") if user_md.exists() else None
        except OSError:
            user_md_content = None

    try:
        from octoagent.gateway.services.daily_routine_config import (
            extract_user_timezone_from_user_md,
        )

        from_user_md = extract_user_timezone_from_user_md(user_md_content)
        if from_user_md:
            return from_user_md
    except Exception:  # noqa: BLE001
        pass

    env_tz = os.environ.get("OCTOAGENT_USER_TIMEZONE", "").strip()
    if env_tz:
        try:
            import zoneinfo

            zoneinfo.ZoneInfo(env_tz)
            return env_tz
        except Exception:  # noqa: BLE001
            pass
    return "UTC"


# --------------------------------------------------------------------------
# schedule 校验
# --------------------------------------------------------------------------


class _ScheduleValidation(BaseModel):
    ok: bool
    reason: str = ""


def _cron_field_is_numeric_dow(dow_field: str) -> bool:
    """判断 cron 第 5 字段（星期）是否含纯数字 DOW（含范围/列表/步长）。

    【Codex P1-1】APScheduler Monday=0，数字 DOW 会让 LLM 按 Unix 约定产出的
    每周提醒错一天。`*` 与命名星期（mon..sun）放行；任何数字（0-7）拒绝。
    """
    field = dow_field.strip().lower()
    if field == "*" or field == "?":
        return False
    # 拆 list/range/step，任一 token 含数字即判定为数字 DOW。
    for token in field.replace("/", ",").replace("-", ",").split(","):
        t = token.strip()
        if not t:
            continue
        if t in _NAMED_DOW:
            continue
        if any(ch.isdigit() for ch in t):
            return True
        # 非命名非数字（如残缺）——交给 from_crontab 报错，这里不判数字。
    return False


def _validate_schedule(
    schedule_kind: AutomationScheduleKind, schedule_expr: str, timezone: str
) -> _ScheduleValidation:
    """校验 schedule_expr 与 kind 匹配且可被调度器解析。工具层只校验，不解析 NL。"""
    expr = schedule_expr.strip()
    if not expr:
        return _ScheduleValidation(ok=False, reason="schedule_expr 不能为空")

    if schedule_kind == AutomationScheduleKind.CRON:
        fields = expr.split()
        if len(fields) != 5:
            return _ScheduleValidation(
                ok=False,
                reason=f"cron 表达式须为 5 字段（分 时 日 月 星期），收到 {len(fields)} 字段：{expr!r}",
            )
        # 【Codex P1-1】数字 DOW 拒绝——引导改命名星期。
        if _cron_field_is_numeric_dow(fields[4]):
            return _ScheduleValidation(
                ok=False,
                reason=(
                    f"星期字段 {fields[4]!r} 用了数字。本调度器（APScheduler）星期从 Monday=0 计，"
                    "与常见 Unix cron（Monday=1）不同，数字会导致每周提醒错一天。"
                    "请改用命名星期：mon/tue/wed/thu/fri/sat/sun（如每周一=mon）。"
                ),
            )
        try:
            CronTrigger.from_crontab(expr, timezone=timezone)
        except Exception as exc:  # noqa: BLE001
            return _ScheduleValidation(
                ok=False, reason=f"cron 表达式无法解析：{exc}"
            )
        return _ScheduleValidation(ok=True)

    if schedule_kind == AutomationScheduleKind.INTERVAL:
        try:
            seconds = int(expr)
        except ValueError:
            return _ScheduleValidation(
                ok=False, reason=f"interval 的 schedule_expr 必须是秒数整数，收到 {expr!r}"
            )
        if seconds <= 0:
            return _ScheduleValidation(ok=False, reason="interval 秒数必须大于 0")
        return _ScheduleValidation(ok=True)

    if schedule_kind == AutomationScheduleKind.ONCE:
        try:
            datetime.fromisoformat(expr)
        except ValueError:
            return _ScheduleValidation(
                ok=False,
                reason=f"once 的 schedule_expr 必须是 ISO datetime（如 2026-07-10T09:00:00），收到 {expr!r}",
            )
        return _ScheduleValidation(ok=True)

    return _ScheduleValidation(ok=False, reason=f"不支持的 schedule_kind: {schedule_kind}")


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------


async def _resolve_project_id(deps: ToolDeps) -> str:
    """解析当前 project_id（automation job 须绑 project）。缺失回退 _default。"""
    try:
        from ._deps import resolve_runtime_project_context

        project, _workspace, _task = await resolve_runtime_project_context(deps)
        if project is not None and getattr(project, "project_id", ""):
            return project.project_id
    except Exception:  # noqa: BLE001
        pass
    return ""


async def _sync_scheduler(deps: ToolDeps, job: AutomationJob, *, remove: bool = False) -> bool:
    """落盘后同步 scheduler。返回是否成功（False=需重启生效）。"""
    scheduler = deps._automation_scheduler
    if scheduler is None:
        log.warning("cron_scheduler_unbound", job_id=job.job_id, remove=remove)
        return False
    try:
        if remove:
            await scheduler.remove_job(job.job_id)
        else:
            await scheduler.sync_job(job)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "cron_scheduler_sync_failed",
            job_id=job.job_id,
            remove=remove,
            error=str(exc),
        )
        return False


async def _emit_cron_event(
    deps: ToolDeps, *, operation: str, job: AutomationJob
) -> None:
    """写 AUTOMATION_JOB_MUTATED 审计事件（Constitution C2）。降级不阻断主路径。"""
    AUDIT_TASK_ID = "_cron_audit"
    try:
        from ..execution_context import get_current_execution_context

        ctx = get_current_execution_context()
        task_id = (ctx.task_id if ctx else "") or AUDIT_TASK_ID
    except Exception:  # noqa: BLE001
        task_id = AUDIT_TASK_ID
    try:
        event_store = deps.stores.event_store
        task_seq = await event_store.get_next_task_seq(task_id)
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=task_seq,
            ts=datetime.now(timezone.utc),
            type=EventType.AUTOMATION_JOB_MUTATED,
            actor=ActorType.SYSTEM,
            payload={
                "operation": operation,
                "job_id": job.job_id,
                "name": job.name,
                "action_id": job.action_id,
                "schedule_kind": job.schedule_kind.value,
                "schedule_expr": job.schedule_expr,
                "timezone": job.timezone,
                "enabled": job.enabled,
            },
            trace_id=task_id,
        )
        await event_store.append_event_committed(event, update_task_pointer=False)
    except Exception as exc:  # noqa: BLE001
        log.error(
            "cron_event_emit_failed",
            operation=operation,
            error_type=type(exc).__name__,
            error=str(exc),
        )


# --------------------------------------------------------------------------
# 注册
# --------------------------------------------------------------------------


async def register(broker: Any, deps: ToolDeps) -> None:
    """注册 cron.create / cron.update / cron.delete 三个写工具。"""

    store = AutomationStore(deps.project_root)

    # ============ cron.create ============

    @tool_contract(
        name="cron.create",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="automation",
        produces_write=True,
        tags=["cron", "automation", "scheduler", "reminder", "schedule"],
        manifest_ref="builtin://cron.create",
        metadata={"entrypoints": ["agent_runtime", "web", "telegram"]},
    )
    async def cron_create(
        name: str,
        schedule_kind: Literal["cron", "interval", "once"],
        schedule_expr: str,
        reminder_text: str = "",
        action_id: str = "",
        action_params: dict[str, Any] | None = None,
        timezone: str = "",
        enabled: bool = True,
    ) -> CronMutationResult:
        """创建一个定时任务，帮用户在指定时间自动提醒或执行。

        用户说自然语言（如「每天早上8点提醒我喝水」「每周一9点提醒交周报」），你负责把它
        译成结构化参数调用本工具——本工具只校验，不替你理解自然语言。

        两种用法（二选一）：
        1) **提醒**（默认，最常用）：传 reminder_text，到点把这段文字推送提醒用户。
           例：cron.create(name="喝水提醒", schedule_kind="cron", schedule_expr="0 8 * * *",
               reminder_text="该喝水啦")
        2) **执行管理动作**（高级）：传 action_id（+ action_params）。仅允许安全动作
           （reminder.notify / memory.consolidate / memory.profile_generate）；其他被拒。

        schedule_kind + schedule_expr 对应关系：
        - "cron"：5 字段 crontab「分 时 日 月 星期」。**星期必须用命名**（mon/tue/wed/thu/
          fri/sat/sun），不要用数字（本调度器星期从周一=0 计，数字会错一天）。
          例：每天8点="0 8 * * *"；每周一9点="0 9 * * mon"；每月1号10点="0 10 1 * *"；
              工作日18点="0 18 * * mon-fri"。
        - "interval"：秒数整数。例：每 30 分钟="1800"。
        - "once"：ISO datetime（一次性）。例：明天下午3点（你按当前时间算好）="2026-07-07T15:00:00"。

        timezone：留空自动用用户时区（USER.md 配置 > 环境变量 > UTC）；也可显式传 IANA 名
        （如 "Asia/Shanghai"）。cron/once 的时刻按此时区解释。

        Args:
            name: 任务名称（给用户看，如「交周报提醒」）。
            schedule_kind: cron / interval / once。
            schedule_expr: 与 kind 对应的表达式。
            reminder_text: 到点推送的提醒文字（默认路径，与 action_id 二选一）。
            action_id: 高级：执行的管理动作（白名单内），与 reminder_text 二选一。
            action_params: action_id 路径的参数。
            timezone: IANA 时区名，留空自动解析用户时区。
            enabled: 是否立即启用（默认 True）。
        """
        name = (name or "").strip()
        if not name:
            return CronMutationResult(
                status="rejected", target="cron", reason="name 不能为空"
            )
        if len(name) > _JOB_NAME_MAX:
            name = name[:_JOB_NAME_MAX]

        try:
            kind = AutomationScheduleKind(schedule_kind)
        except ValueError:
            return CronMutationResult(
                status="rejected",
                target="cron",
                reason=f"不支持的 schedule_kind: {schedule_kind!r}（用 cron/interval/once）",
            )

        # reminder_text vs action_id 二选一
        reminder_text = (reminder_text or "").strip()
        action_id = (action_id or "").strip()
        if reminder_text and action_id:
            return CronMutationResult(
                status="rejected",
                target="cron",
                reason="reminder_text 与 action_id 二选一，不能同时传",
            )
        if not reminder_text and not action_id:
            return CronMutationResult(
                status="rejected",
                target="cron",
                reason="须传 reminder_text（提醒文字）或 action_id（管理动作）之一",
            )

        if reminder_text:
            if len(reminder_text) > _REMINDER_MSG_MAX:
                reminder_text = reminder_text[:_REMINDER_MSG_MAX]
            resolved_action_id = "reminder.notify"
            params = {"message": reminder_text}
        else:
            # 【Codex P1-2】action_id 白名单
            if action_id not in _CRON_AGENT_ACTION_ALLOWLIST:
                return CronMutationResult(
                    status="rejected",
                    target="cron",
                    reason=(
                        f"action_not_allowed: {action_id!r} 不在 cron 工具允许的安全动作白名单内"
                        f"（允许：{sorted(_CRON_AGENT_ACTION_ALLOWLIST)}）。高危动作请让用户在 Web/CLI 显式操作。"
                    ),
                )
            resolved_action_id = action_id
            params = dict(action_params or {})

        # 时区 + schedule 校验
        effective_tz = _resolve_timezone(deps, timezone)
        validation = _validate_schedule(kind, schedule_expr, effective_tz)
        if not validation.ok:
            return CronMutationResult(
                status="rejected", target="cron", reason=validation.reason
            )

        project_id = await _resolve_project_id(deps)
        job = AutomationJob(
            job_id=str(ULID()),
            name=name,
            action_id=resolved_action_id,
            params=params,
            project_id=project_id,
            schedule_kind=kind,
            schedule_expr=schedule_expr.strip(),
            timezone=effective_tz,
            enabled=bool(enabled),
        )
        store.save_job(job)
        synced = await _sync_scheduler(deps, job)
        await _emit_cron_event(deps, operation="create", job=job)

        reason = None
        if not synced:
            reason = "job 已保存，但调度器未同步（需重启后台服务生效）"
        return CronMutationResult(
            status="written",
            target=f"automation_job:{job.job_id}",
            job_id=job.job_id,
            job_name=job.name,
            scheduler_synced=synced,
            preview=f"{name} · {schedule_kind} {schedule_expr} · {effective_tz}",
            reason=reason,
        )

    # ============ cron.update ============

    @tool_contract(
        name="cron.update",
        side_effect_level=SideEffectLevel.REVERSIBLE,
        tool_group="automation",
        produces_write=True,
        tags=["cron", "automation", "scheduler", "schedule"],
        manifest_ref="builtin://cron.update",
        metadata={"entrypoints": ["agent_runtime", "web", "telegram"]},
    )
    async def cron_update(
        job_id: str,
        enabled: bool | None = None,
        name: str = "",
        schedule_kind: Literal["cron", "interval", "once", ""] = "",
        schedule_expr: str = "",
        reminder_text: str = "",
        timezone: str = "",
    ) -> CronMutationResult:
        """修改一个已有定时任务。

        - 只暂停/恢复（只传 enabled）→ 直接生效，无需审批（可逆）。
        - 改时间/名称/提醒内容（传 schedule/name/reminder_text）→ 因改变既定行为，
          需用户在审批卡片确认后才生效。

        只传要改的字段，其余保持不变。

        Args:
            job_id: 目标任务 ID（用 cron.list 查）。
            enabled: True=恢复 / False=暂停（只传这个则直接生效）。
            name: 新名称（留空不改）。
            schedule_kind: 新的 cron/interval/once（改时间时与 schedule_expr 一起传）。
            schedule_expr: 新表达式（同 cron.create 规则，星期用命名）。
            reminder_text: 新提醒文字（仅对 reminder 型任务有效）。
            timezone: 新时区（留空不改）。
        """
        job_id = (job_id or "").strip()
        if not job_id:
            return CronMutationResult(
                status="rejected", target="cron", reason="job_id 不能为空"
            )
        job = store.get_job(job_id)
        if job is None:
            return CronMutationResult(
                status="rejected",
                target=f"automation_job:{job_id}",
                reason="job_not_found: 定时任务不存在（用 cron.list 查看现有任务）",
            )

        name = (name or "").strip()
        schedule_kind = (schedule_kind or "").strip()
        schedule_expr = (schedule_expr or "").strip()
        reminder_text = (reminder_text or "").strip()
        timezone = (timezone or "").strip()

        # 判定改动是否含非 enabled 字段（决定是否走审批）。
        mutates_behavior = bool(
            name or schedule_kind or schedule_expr or reminder_text or timezone
        )
        if enabled is None and not mutates_behavior:
            return CronMutationResult(
                status="rejected",
                target=f"automation_job:{job_id}",
                reason="未提供任何要修改的字段",
            )

        # 构造 patch（先校验 schedule 再决定审批，避免审批通过后才发现表达式非法）。
        updates: dict[str, Any] = {}
        if name:
            updates["name"] = name[:_JOB_NAME_MAX]
        new_kind = job.schedule_kind
        if schedule_kind:
            try:
                new_kind = AutomationScheduleKind(schedule_kind)
            except ValueError:
                return CronMutationResult(
                    status="rejected",
                    target=f"automation_job:{job_id}",
                    reason=f"不支持的 schedule_kind: {schedule_kind!r}",
                )
            updates["schedule_kind"] = new_kind
        new_tz = job.timezone
        if timezone:
            new_tz = _resolve_timezone(deps, timezone)
            updates["timezone"] = new_tz
        if schedule_expr:
            validation = _validate_schedule(new_kind, schedule_expr, new_tz)
            if not validation.ok:
                return CronMutationResult(
                    status="rejected",
                    target=f"automation_job:{job_id}",
                    reason=validation.reason,
                )
            updates["schedule_expr"] = schedule_expr
        elif schedule_kind:
            # 改了 kind 但没给新 expr——旧 expr 可能与新 kind 不匹配，校验之。
            validation = _validate_schedule(new_kind, job.schedule_expr, new_tz)
            if not validation.ok:
                return CronMutationResult(
                    status="rejected",
                    target=f"automation_job:{job_id}",
                    reason=f"改 schedule_kind 后原表达式不匹配：{validation.reason}",
                )
        if reminder_text:
            if job.action_id != "reminder.notify":
                return CronMutationResult(
                    status="rejected",
                    target=f"automation_job:{job_id}",
                    reason="reminder_text 仅对提醒型任务（reminder.notify）有效",
                )
            updates["params"] = {"message": reminder_text[:_REMINDER_MSG_MAX]}
        if enabled is not None:
            updates["enabled"] = bool(enabled)

        # DP-5：改行为字段 → 审批；仅 enabled → 直改。
        if mutates_behavior:
            from ..execution_context import get_current_execution_context
            from .write_approval import gate_destructive_action

            exec_ctx = get_current_execution_context()
            summary = (
                f"Agent 请求修改定时任务「{job.name}」（{job_id}）\n"
                f"变更字段：{', '.join(k for k in updates)}"
            )
            outcome = await gate_destructive_action(
                deps,
                exec_ctx=exec_ctx,
                tool_name="cron.update",
                operation_summary=summary,
                args_summary=f"job_id={job_id!r}, fields={sorted(updates)}",
            )
            if outcome.decision != "approved":
                return CronMutationResult(
                    status="rejected",
                    target=f"automation_job:{job_id}",
                    job_id=job_id,
                    job_name=job.name,
                    approval_requested=True,
                    reason=outcome.reason or "审批未通过，未修改",
                )

        updated = job.model_copy(update=updates)
        store.save_job(updated)
        synced = await _sync_scheduler(deps, updated, remove=not updated.enabled)
        await _emit_cron_event(deps, operation="update", job=updated)

        reason = None if synced else "已修改，但调度器未同步（需重启后台服务生效）"
        return CronMutationResult(
            status="written",
            target=f"automation_job:{job_id}",
            job_id=job_id,
            job_name=updated.name,
            scheduler_synced=synced,
            approval_requested=mutates_behavior,
            reason=reason,
        )

    # ============ cron.delete ============

    @tool_contract(
        name="cron.delete",
        side_effect_level=SideEffectLevel.IRREVERSIBLE,
        tool_group="automation",
        produces_write=True,
        tags=["cron", "automation", "scheduler", "delete"],
        manifest_ref="builtin://cron.delete",
        metadata={"entrypoints": ["agent_runtime", "web", "telegram"]},
    )
    async def cron_delete(job_id: str) -> CronMutationResult:
        """删除一个定时任务（不可逆）。

        因为删除不可撤销，需要用户在审批卡片确认后才真正删除。

        Args:
            job_id: 目标任务 ID（用 cron.list 查）。
        """
        job_id = (job_id or "").strip()
        if not job_id:
            return CronMutationResult(
                status="rejected", target="cron", reason="job_id 不能为空"
            )
        job = store.get_job(job_id)
        if job is None:
            return CronMutationResult(
                status="rejected",
                target=f"automation_job:{job_id}",
                reason="job_not_found: 定时任务不存在（用 cron.list 查看现有任务）",
            )

        # DP-5：删除必走服务端 ApprovalGate Two-Phase。
        from ..execution_context import get_current_execution_context
        from .write_approval import gate_destructive_action

        exec_ctx = get_current_execution_context()
        summary = (
            f"Agent 请求删除定时任务「{job.name}」（{job_id}）\n"
            f"调度：{job.schedule_kind.value} {job.schedule_expr}（{job.timezone}）\n"
            f"删除后不可恢复。"
        )
        outcome = await gate_destructive_action(
            deps,
            exec_ctx=exec_ctx,
            tool_name="cron.delete",
            operation_summary=summary,
            args_summary=f"job_id={job_id!r}, name={job.name!r}",
        )
        if outcome.decision != "approved":
            return CronMutationResult(
                status="rejected",
                target=f"automation_job:{job_id}",
                job_id=job_id,
                job_name=job.name,
                approval_requested=True,
                reason=outcome.reason or "审批未通过，未删除",
            )

        deleted = store.delete_job(job_id)
        if not deleted:
            return CronMutationResult(
                status="rejected",
                target=f"automation_job:{job_id}",
                job_id=job_id,
                job_name=job.name,
                approval_requested=True,
                reason="删除失败（job 可能已被并发删除）",
            )
        synced = await _sync_scheduler(deps, job, remove=True)
        await _emit_cron_event(deps, operation="delete", job=job)

        reason = None if synced else "已删除记录，但调度器未同步移除（需重启后台服务生效）"
        return CronMutationResult(
            status="written",
            target=f"automation_job:{job_id}",
            job_id=job_id,
            job_name=job.name,
            scheduler_synced=synced,
            approval_requested=True,
            reason=reason,
        )

    # ---- 注册到 broker + ToolRegistry ----
    for handler in (cron_create, cron_update, cron_delete):
        await broker.try_register(reflect_tool_schema(handler), handler)

    for _name, _handler, _sel in (
        ("cron.create", cron_create, SideEffectLevel.REVERSIBLE),
        ("cron.update", cron_update, SideEffectLevel.REVERSIBLE),
        ("cron.delete", cron_delete, SideEffectLevel.IRREVERSIBLE),
    ):
        _registry_register(ToolEntry(
            name=_name,
            entrypoints=_TOOL_ENTRYPOINTS[_name],
            toolset="automation",
            handler=_handler,
            schema=BaseModel,
            side_effect_level=_sel,
        ))
