"""Feature 017: Unified Operator Actions + Telegram callback codec。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite
from octoagent.core.models import (
    ActorType,
    Event,
    EventCausality,
    EventType,
    NormalizedMessage,
    OperatorActionKind,
    OperatorActionOutcome,
    OperatorActionRequest,
    OperatorActionResult,
    OperatorActionSource,
    PairingActionTarget,
    RequesterInfo,
    RetryLaunchRef,
    Task,
    TaskCreatedPayload,
    TaskStatus,
)
from octoagent.core.models.payloads import OperatorActionAuditPayload
from octoagent.core.store import StoreGroup, append_event_only
from octoagent.core.store.transaction import create_task_with_initial_events
from octoagent.policy.models import ApprovalDecision, ApprovalStatus
from ulid import ULID

from .task_journal import TaskJournalService
from .task_service import TaskService
from .watchdog.config import WatchdogConfig

_OPERATOR_AUDIT_TASK_ID = "ops-operator-inbox"
_OPERATOR_AUDIT_TRACE_ID = "trace-ops-operator-inbox"


def encode_telegram_operator_action(item_id: str, kind: OperatorActionKind) -> str:
    """编码 Telegram callback_data。"""
    prefix, *parts = item_id.split(":")
    if prefix == "approval" and len(parts) == 1:
        action_code = {
            OperatorActionKind.APPROVE_ONCE: "1",
            OperatorActionKind.APPROVE_ALWAYS: "A",
            OperatorActionKind.DENY: "D",
        }.get(kind)
        if action_code is None:
            raise ValueError(f"approval 不支持动作 {kind}")
        payload = f"oi|a|{action_code}|{parts[0]}"
    elif prefix == "alert" and len(parts) == 2:
        if kind != OperatorActionKind.ACK_ALERT:
            raise ValueError(f"alert 不支持动作 {kind}")
        payload = f"oi|l|K|{parts[0]}|{parts[1]}"
    elif prefix in {"task", "retry"} and len(parts) == 1:
        action_code = {
            OperatorActionKind.RETRY_TASK: "R",
            OperatorActionKind.CANCEL_TASK: "C",
        }.get(kind)
        if action_code is None:
            raise ValueError(f"task 不支持动作 {kind}")
        payload = f"oi|t|{action_code}|{parts[0]}"
    elif prefix == "pairing" and len(parts) == 1:
        action_code = {
            OperatorActionKind.APPROVE_PAIRING: "Y",
            OperatorActionKind.REJECT_PAIRING: "N",
        }.get(kind)
        if action_code is None:
            raise ValueError(f"pairing 不支持动作 {kind}")
        payload = f"oi|p|{action_code}|{parts[0]}"
    else:
        raise ValueError(f"不支持的 item_id: {item_id}")

    if len(payload.encode("utf-8")) > 64:
        raise ValueError("callback_data 超出 64-byte 限制")
    return payload


def decode_telegram_operator_action(callback_data: str) -> OperatorActionRequest:
    """解析 Telegram callback_data。"""
    parts = callback_data.split("|")
    if len(parts) < 4 or parts[0] != "oi":
        raise ValueError("非法 operator callback")

    family = parts[1]
    action = parts[2]
    if family == "a" and len(parts) == 4:
        kind = {
            "1": OperatorActionKind.APPROVE_ONCE,
            "A": OperatorActionKind.APPROVE_ALWAYS,
            "D": OperatorActionKind.DENY,
        }.get(action)
        item_id = f"approval:{parts[3]}"
    elif family == "l" and len(parts) == 5:
        kind = {"K": OperatorActionKind.ACK_ALERT}.get(action)
        item_id = f"alert:{parts[3]}:{parts[4]}"
    elif family == "t" and len(parts) == 4:
        kind = {
            "R": OperatorActionKind.RETRY_TASK,
            "C": OperatorActionKind.CANCEL_TASK,
        }.get(action)
        item_id = f"task:{parts[3]}"
    elif family == "p" and len(parts) == 4:
        kind = {
            "Y": OperatorActionKind.APPROVE_PAIRING,
            "N": OperatorActionKind.REJECT_PAIRING,
        }.get(action)
        item_id = f"pairing:{parts[3]}"
    else:
        kind = None
        item_id = ""

    if kind is None:
        raise ValueError("未知 operator callback 动作")
    return OperatorActionRequest(
        item_id=item_id,
        kind=kind,
        source=OperatorActionSource.TELEGRAM,
    )


class OperatorActionService:
    """统一动作执行与审计。"""

    def __init__(
        self,
        *,
        store_group: StoreGroup,
        sse_hub=None,
        approval_manager=None,
        task_runner=None,
        telegram_state_store=None,
        watchdog_config: WatchdogConfig | None = None,
        task_journal_service: TaskJournalService | None = None,
    ) -> None:
        self._stores = store_group
        self._sse_hub = sse_hub
        self._approval_manager = approval_manager
        self._task_runner = task_runner
        self._telegram_state_store = telegram_state_store
        self._watchdog_config = watchdog_config or WatchdogConfig.from_env()
        self._task_journal_service = task_journal_service or TaskJournalService(store_group)
        self._task_service = TaskService(store_group, sse_hub)

    async def execute(self, request: OperatorActionRequest) -> OperatorActionResult:
        normalized = self._normalize_request(request)
        prefix = normalized.item_id.split(":", 1)[0]
        if prefix == "approval":
            result, item_kind = await self._handle_approval_action(normalized), "approval"
        elif prefix == "alert":
            result, item_kind = await self._handle_alert_action(normalized), "alert"
        elif prefix in {"task", "retry"}:
            result, item_kind = await self._handle_task_action(normalized), "retryable_failure"
        elif prefix == "pairing":
            result, item_kind = await self._handle_pairing_action(normalized), "pairing_request"
        else:
            result = OperatorActionResult(
                item_id=normalized.item_id,
                kind=normalized.kind,
                source=normalized.source,
                outcome=OperatorActionOutcome.NOT_FOUND,
                message="未找到 operator item",
                handled_at=datetime.now(tz=UTC),
            )
            item_kind = "unknown"

        audit_event_id = await self._record_action_audit(
            request=normalized,
            result=result,
            item_kind=item_kind,
        )
        return result.model_copy(update={"audit_event_id": audit_event_id})

    def _normalize_request(self, request: OperatorActionRequest) -> OperatorActionRequest:
        actor_id = request.actor_id.strip() or f"user:{request.source.value}"
        actor_label = request.actor_label.strip() or actor_id
        note = request.note.strip()
        return request.model_copy(
            update={
                "actor_id": actor_id,
                "actor_label": actor_label,
                "note": note,
            }
        )

    async def _handle_approval_action(
        self,
        request: OperatorActionRequest,
    ) -> OperatorActionResult:
        approval_id = request.item_id.split(":", 1)[1]
        record = (
            self._approval_manager.get_approval(approval_id)
            if self._approval_manager
            else None
        )
        now = datetime.now(tz=UTC)
        if record is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_FOUND,
                message="审批不存在或已清理",
                handled_at=now,
            )

        if record.status == ApprovalStatus.EXPIRED:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.EXPIRED,
                message="审批已过期",
                task_id=record.request.task_id,
                handled_at=now,
            )
        if record.status != ApprovalStatus.PENDING:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.ALREADY_HANDLED,
                message="该审批已被其他端处理",
                task_id=record.request.task_id,
                handled_at=now,
            )

        decision = {
            OperatorActionKind.APPROVE_ONCE: ApprovalDecision.ALLOW_ONCE,
            OperatorActionKind.APPROVE_ALWAYS: ApprovalDecision.ALLOW_ALWAYS,
            OperatorActionKind.DENY: ApprovalDecision.DENY,
        }.get(request.kind)
        if decision is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="approval 不支持该动作",
                task_id=record.request.task_id,
                handled_at=now,
            )

        resolved = await self._approval_manager.resolve(
            approval_id,
            decision=decision,
            resolved_by=request.actor_id,
        )
        if not resolved:
            latest = self._approval_manager.get_approval(approval_id)
            outcome = OperatorActionOutcome.ALREADY_HANDLED
            message = "该审批已被其他端处理"
            if latest is not None and latest.status == ApprovalStatus.EXPIRED:
                outcome = OperatorActionOutcome.EXPIRED
                message = "审批已过期"
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=outcome,
                message=message,
                task_id=record.request.task_id,
                handled_at=now,
            )

        decision_label = {
            OperatorActionKind.APPROVE_ONCE: "审批已批准一次",
            OperatorActionKind.APPROVE_ALWAYS: "审批已加入总是批准",
            OperatorActionKind.DENY: "审批已拒绝",
        }[request.kind]
        return OperatorActionResult(
            item_id=request.item_id,
            kind=request.kind,
            source=request.source,
            outcome=OperatorActionOutcome.SUCCEEDED,
            message=decision_label,
            task_id=record.request.task_id,
            handled_at=now,
        )

    async def _handle_alert_action(
        self,
        request: OperatorActionRequest,
    ) -> OperatorActionResult:
        _, task_id, source_ref = request.item_id.split(":", 2)
        now = datetime.now(tz=UTC)
        if request.kind not in {OperatorActionKind.ACK_ALERT, OperatorActionKind.CANCEL_TASK}:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="alert 不支持该动作",
                task_id=task_id,
                handled_at=now,
            )
        if request.kind == OperatorActionKind.CANCEL_TASK:
            return await self._cancel_task(task_id=task_id, request=request)

        if await self._was_alert_acknowledged(request.item_id, task_id):
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.ALREADY_HANDLED,
                message="该告警已经被确认",
                task_id=task_id,
                handled_at=now,
            )

        active_source_ref, task_exists = await self._resolve_active_alert_source_ref(task_id)
        if not task_exists:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_FOUND,
                message="告警关联任务不存在",
                task_id=task_id,
                handled_at=now,
            )
        if active_source_ref is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.ALREADY_HANDLED,
                message="该告警已经恢复或被确认",
                task_id=task_id,
                handled_at=now,
            )
        if active_source_ref != source_ref:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.STALE_STATE,
                message="告警上下文已变化，请刷新后重试",
                task_id=task_id,
                handled_at=now,
            )
        return OperatorActionResult(
            item_id=request.item_id,
            kind=request.kind,
            source=request.source,
            outcome=OperatorActionOutcome.SUCCEEDED,
            message="告警已确认",
            task_id=task_id,
            handled_at=now,
        )

    async def _handle_task_action(
        self,
        request: OperatorActionRequest,
    ) -> OperatorActionResult:
        task_id = request.item_id.split(":", 1)[1]
        if request.kind == OperatorActionKind.RETRY_TASK:
            return await self._retry_task(task_id=task_id, request=request)
        if request.kind == OperatorActionKind.CANCEL_TASK:
            return await self._cancel_task(task_id=task_id, request=request)
        return OperatorActionResult(
            item_id=request.item_id,
            kind=request.kind,
            source=request.source,
            outcome=OperatorActionOutcome.NOT_ALLOWED,
            message="任务项不支持该动作",
            task_id=task_id,
            handled_at=datetime.now(tz=UTC),
        )

    async def _handle_pairing_action(
        self,
        request: OperatorActionRequest,
    ) -> OperatorActionResult:
        user_id = request.item_id.split(":", 1)[1]
        now = datetime.now(tz=UTC)
        if self._telegram_state_store is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="当前未启用 Telegram pairing 状态源",
                handled_at=now,
            )

        pending = self._telegram_state_store.get_pending_pairing(user_id)
        approved = self._telegram_state_store.get_approved_user(user_id)
        if pending is None:
            outcome = (
                OperatorActionOutcome.ALREADY_HANDLED
                if approved is not None and request.kind == OperatorActionKind.APPROVE_PAIRING
                else OperatorActionOutcome.NOT_FOUND
            )
            message = (
                "该 pairing 已被处理"
                if outcome == OperatorActionOutcome.ALREADY_HANDLED
                else "pairing 不存在"
            )
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=outcome,
                message=message,
                handled_at=now,
            )

        if pending.expires_at < now:
            self._telegram_state_store.delete_pending_pairing(user_id)
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.EXPIRED,
                message="pairing 请求已过期",
                handled_at=now,
            )

        if request.kind == OperatorActionKind.APPROVE_PAIRING:
            approved_user = self._telegram_state_store.upsert_approved_user(
                user_id=pending.user_id,
                chat_id=pending.chat_id,
                username=pending.username,
                display_name=pending.display_name,
            )
            pairing_target = PairingActionTarget(
                user_id=approved_user.user_id,
                chat_id=approved_user.chat_id,
                code=pending.code,
            )
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.SUCCEEDED,
                message=f"已批准 Telegram pairing: {pairing_target.user_id}",
                handled_at=now,
            )
        if request.kind == OperatorActionKind.REJECT_PAIRING:
            self._telegram_state_store.delete_pending_pairing(user_id)
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.SUCCEEDED,
                message="已拒绝 Telegram pairing",
                handled_at=now,
            )
        return OperatorActionResult(
            item_id=request.item_id,
            kind=request.kind,
            source=request.source,
            outcome=OperatorActionOutcome.NOT_ALLOWED,
            message="pairing 不支持该动作",
            handled_at=now,
        )

    async def _retry_task(
        self,
        *,
        task_id: str,
        request: OperatorActionRequest,
    ) -> OperatorActionResult:
        task = await self._stores.task_store.get_task(task_id)
        now = datetime.now(tz=UTC)
        if task is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_FOUND,
                message="来源任务不存在",
                handled_at=now,
            )
        if task.status not in {TaskStatus.FAILED, TaskStatus.REJECTED}:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="只有失败任务可以发起 retry",
                task_id=task_id,
                handled_at=now,
            )

        events = await self._stores.event_store.get_events_for_task(task_id)
        latest_worker_return = next(
            (
                event
                for event in reversed(events)
                if event.type == EventType.WORKER_RETURNED
            ),
            None,
        )
        if latest_worker_return is None or not bool(latest_worker_return.payload.get("retryable")):
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="该失败当前不可重试",
                task_id=task_id,
                handled_at=now,
            )
        existing_retry = self._find_retry_launch(events, request.item_id, task_id)
        if existing_retry is not None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.ALREADY_HANDLED,
                message="该失败已发起 retry",
                task_id=task_id,
                retry_launch=existing_retry,
                handled_at=now,
            )

        job = await self._stores.task_job_store.get_job(task_id)
        user_text = (job.user_text if job is not None else self._extract_user_text(events)).strip()
        if not user_text:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.FAILED,
                message="无法恢复原始用户输入，不能创建 retry",
                task_id=task_id,
                handled_at=now,
            )

        metadata = self._extract_user_metadata(events)
        metadata.update(
            {
                "retry_source_task_id": task_id,
                "retry_action_source": request.source.value,
                "retry_actor_id": request.actor_id,
            }
        )
        message = NormalizedMessage(
            channel=task.requester.channel,
            thread_id=task.thread_id,
            scope_id=task.scope_id,
            sender_id=task.requester.sender_id,
            sender_name=task.requester.sender_id,
            text=user_text,
            metadata=metadata,
            idempotency_key=f"operator-retry:{task_id}",
        )
        result_task_id, created = await self._task_service.create_task(message)
        if self._task_runner is not None:
            await self._task_runner.enqueue(
                result_task_id,
                user_text,
                model_alias=job.model_alias if job is not None else None,
            )
        if not created:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.ALREADY_HANDLED,
                message="该失败已发起 retry",
                task_id=task_id,
                retry_launch=RetryLaunchRef(
                    source_task_id=task_id,
                    result_task_id=result_task_id,
                ),
                handled_at=now,
            )
        return OperatorActionResult(
            item_id=request.item_id,
            kind=request.kind,
            source=request.source,
            outcome=OperatorActionOutcome.SUCCEEDED,
            message="已创建新的重试任务",
            task_id=task_id,
            retry_launch=RetryLaunchRef(
                source_task_id=task_id,
                result_task_id=result_task_id,
            ),
            handled_at=now,
        )

    async def _cancel_task(
        self,
        *,
        task_id: str,
        request: OperatorActionRequest,
    ) -> OperatorActionResult:
        task = await self._stores.task_store.get_task(task_id)
        now = datetime.now(tz=UTC)
        if task is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_FOUND,
                message="任务不存在",
                handled_at=now,
            )
        if task.status in {
            TaskStatus.CANCELLED,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.REJECTED,
        }:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.ALREADY_HANDLED,
                message="任务已处于终态，不能再次取消",
                task_id=task_id,
                handled_at=now,
            )
        if self._task_runner is None:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="当前没有可用的 TaskRunner",
                task_id=task_id,
                handled_at=now,
            )
        cancelled = await self._task_runner.cancel_task(task_id)
        if not cancelled:
            return OperatorActionResult(
                item_id=request.item_id,
                kind=request.kind,
                source=request.source,
                outcome=OperatorActionOutcome.NOT_ALLOWED,
                message="当前状态不允许取消",
                task_id=task_id,
                handled_at=now,
            )
        return OperatorActionResult(
            item_id=request.item_id,
            kind=request.kind,
            source=request.source,
            outcome=OperatorActionOutcome.SUCCEEDED,
            message="任务已取消",
            task_id=task_id,
            handled_at=now,
        )

    async def _resolve_active_alert_source_ref(
        self,
        task_id: str,
    ) -> tuple[str | None, bool]:
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return None, False
        journal = await self._task_journal_service.get_journal(self._watchdog_config)
        for entry in journal.groups.drifted + journal.groups.stalled:
            if str(entry.get("task_id", "")).strip() != task_id:
                continue
            if journal.groups.drifted and entry in journal.groups.drifted:
                events = await self._stores.event_store.get_events_for_task(task_id)
                drift_event = next(
                    (
                        event
                        for event in reversed(events)
                        if event.type == EventType.TASK_DRIFT_DETECTED
                    ),
                    None,
                )
                if drift_event is not None:
                    return drift_event.event_id, True
            last_event_ts = str(entry.get("last_event_ts", "")).strip()
            dt = datetime.fromisoformat(last_event_ts) if last_event_ts else task.updated_at
            return f"stalled-{int(dt.timestamp())}", True
        return None, True

    async def _record_action_audit(
        self,
        *,
        request: OperatorActionRequest,
        result: OperatorActionResult,
        item_kind: str,
    ) -> str:
        payload = OperatorActionAuditPayload(
            item_id=result.item_id,
            item_kind=item_kind,
            action_kind=request.kind.value,
            source=request.source.value,
            actor_id=request.actor_id,
            actor_label=request.actor_label,
            target_ref=result.item_id,
            outcome=result.outcome.value,
            message=result.message,
            note=request.note,
            result_task_id=(
                result.retry_launch.result_task_id if result.retry_launch is not None else None
            ),
            handled_at=result.handled_at,
        )

        target_task_id = result.task_id
        if target_task_id:
            event = await self._task_service.append_structured_event(
                task_id=target_task_id,
                event_type=EventType.OPERATOR_ACTION_RECORDED,
                actor=ActorType.USER,
                payload=payload.model_dump(mode="json"),
                trace_id=f"trace-{target_task_id}",
                idempotency_key=f"operator-action:{request.source.value}:{ULID()}",
            )
            return event.event_id

        event = await self._append_operational_audit(payload)
        return event.event_id

    async def _append_operational_audit(
        self,
        payload: OperatorActionAuditPayload,
    ) -> Event:
        await self._ensure_operational_task()
        event = Event(
            event_id=str(ULID()),
            task_id=_OPERATOR_AUDIT_TASK_ID,
            task_seq=await self._stores.event_store.get_next_task_seq(_OPERATOR_AUDIT_TASK_ID),
            ts=datetime.now(tz=UTC),
            type=EventType.OPERATOR_ACTION_RECORDED,
            actor=ActorType.USER,
            payload=payload.model_dump(mode="json"),
            trace_id=_OPERATOR_AUDIT_TRACE_ID,
            causality=EventCausality(idempotency_key=f"operator-audit:{ULID()}"),
        )
        await append_event_only(
            self._stores.conn,
            self._stores.event_store,
            event,
        )
        return event

    async def _was_alert_acknowledged(self, item_id: str, task_id: str) -> bool:
        events = await self._stores.event_store.get_events_for_task(task_id)
        for event in reversed(events):
            if event.type != EventType.OPERATOR_ACTION_RECORDED:
                continue
            payload = event.payload
            if str(payload.get("item_id", "")) != item_id:
                continue
            if str(payload.get("action_kind", "")) != OperatorActionKind.ACK_ALERT.value:
                continue
            return str(payload.get("outcome", "")) == OperatorActionOutcome.SUCCEEDED.value
        return False

    async def _ensure_operational_task(self) -> None:
        existing = await self._stores.task_store.get_task(_OPERATOR_AUDIT_TASK_ID)
        if existing is not None:
            return
        now = datetime.now(tz=UTC)
        task = Task(
            task_id=_OPERATOR_AUDIT_TASK_ID,
            created_at=now,
            updated_at=now,
            title="系统运维审计（operator inbox）",
            thread_id="ops-operator-inbox",
            scope_id="ops/operator-inbox",
            requester=RequesterInfo(channel="system", sender_id="system"),
            trace_id=_OPERATOR_AUDIT_TRACE_ID,
        )
        created_event = Event(
            event_id=str(ULID()),
            task_id=_OPERATOR_AUDIT_TASK_ID,
            task_seq=1,
            ts=now,
            type=EventType.TASK_CREATED,
            actor=ActorType.SYSTEM,
            payload=TaskCreatedPayload(
                title=task.title,
                thread_id=task.thread_id,
                scope_id=task.scope_id,
                channel=task.requester.channel,
                sender_id=task.requester.sender_id,
                risk_level=task.risk_level.value,
            ).model_dump(mode="json"),
            trace_id=_OPERATOR_AUDIT_TRACE_ID,
            causality=EventCausality(idempotency_key="ops-operator-inbox:create"),
        )
        try:
            await create_task_with_initial_events(
                self._stores.conn,
                self._stores.task_store,
                self._stores.event_store,
                task,
                [created_event],
            )
        except aiosqlite.IntegrityError:
            await self._stores.conn.rollback()

    @staticmethod
    def _extract_user_text(events: list[Any]) -> str:
        for event in reversed(events):
            if event.type != EventType.USER_MESSAGE:
                continue
            text = str(event.payload.get("text", "")).strip()
            if text:
                return text
            preview = str(event.payload.get("text_preview", "")).strip()
            if preview:
                return preview
        return ""

    @staticmethod
    def _find_retry_launch(
        events: list[Any],
        item_id: str,
        task_id: str,
    ) -> RetryLaunchRef | None:
        for event in reversed(events):
            if event.type != EventType.OPERATOR_ACTION_RECORDED:
                continue
            payload = event.payload
            if str(payload.get("item_id", "")) != item_id:
                continue
            if str(payload.get("action_kind", "")) != OperatorActionKind.RETRY_TASK.value:
                continue
            if str(payload.get("outcome", "")) not in {
                OperatorActionOutcome.SUCCEEDED.value,
                OperatorActionOutcome.ALREADY_HANDLED.value,
            }:
                continue
            result_task_id = str(payload.get("result_task_id", "")).strip()
            if not result_task_id:
                continue
            return RetryLaunchRef(
                source_task_id=task_id,
                result_task_id=result_task_id,
            )
        return None

    @staticmethod
    def _extract_user_metadata(events: list[Any]) -> dict[str, str]:
        for event in events:
            if event.type != EventType.USER_MESSAGE:
                continue
            metadata = event.payload.get("metadata", {})
            if isinstance(metadata, dict):
                return {
                    str(key): str(value)
                    for key, value in metadata.items()
                    if value is not None
                }
        return {}
