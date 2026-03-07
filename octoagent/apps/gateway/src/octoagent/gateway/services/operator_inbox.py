"""Feature 017: Unified Operator Inbox 查询投影。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from octoagent.core.models import (
    EventType,
    OperatorActionKind,
    OperatorActionOutcome,
    OperatorActionResult,
    OperatorActionSource,
    OperatorInboxItem,
    OperatorInboxResponse,
    OperatorInboxSummary,
    OperatorItemKind,
    OperatorItemState,
    OperatorQuickAction,
    TaskStatus,
)

from .task_journal import TaskJournalService
from .watchdog.config import WatchdogConfig

_ALERT_LOOKBACK_DAYS = 30


class OperatorInboxService:
    """聚合 approvals / alerts / retryable failures / pairing requests。"""

    def __init__(
        self,
        *,
        store_group,
        approval_manager,
        telegram_state_store=None,
        watchdog_config: WatchdogConfig | None = None,
        task_journal_service: TaskJournalService | None = None,
    ) -> None:
        self._stores = store_group
        self._approval_manager = approval_manager
        self._telegram_state_store = telegram_state_store
        self._watchdog_config = watchdog_config or WatchdogConfig.from_env()
        self._task_journal_service = task_journal_service or TaskJournalService(store_group)

    async def get_inbox(self) -> OperatorInboxResponse:
        now = datetime.now(tz=UTC)
        degraded_sources: list[str] = []
        recent_results: dict[str, OperatorActionResult] = {}
        suppressed_alerts: set[str] = set()
        handled_retry_items: set[str] = set()

        try:
            recent_results, suppressed_alerts, handled_retry_items = (
                await self._load_recent_action_results()
            )
        except Exception:
            degraded_sources.append("audit")

        items: list[OperatorInboxItem] = []

        try:
            items.extend(await self._build_approval_items(now, recent_results))
        except Exception:
            degraded_sources.append("approvals")

        try:
            items.extend(await self._build_alert_items(now, recent_results, suppressed_alerts))
        except Exception:
            degraded_sources.append("journal")

        try:
            items.extend(
                await self._build_retryable_failure_items(
                    now,
                    recent_results,
                    handled_retry_items,
                )
            )
        except Exception:
            degraded_sources.append("retryable_failures")

        try:
            items.extend(self._build_pairing_items(now, recent_results, degraded_sources))
        except Exception:
            degraded_sources.append("pairings")

        items.sort(key=self._sort_key)
        summary = OperatorInboxSummary(
            total_pending=len(items),
            approvals=sum(1 for item in items if item.kind == OperatorItemKind.APPROVAL),
            alerts=sum(1 for item in items if item.kind == OperatorItemKind.ALERT),
            retryable_failures=sum(
                1 for item in items if item.kind == OperatorItemKind.RETRYABLE_FAILURE
            ),
            pairing_requests=sum(
                1 for item in items if item.kind == OperatorItemKind.PAIRING_REQUEST
            ),
            degraded_sources=sorted(set(degraded_sources)),
            generated_at=now,
        )
        return OperatorInboxResponse(summary=summary, items=items)

    async def get_item(self, item_id: str) -> OperatorInboxItem | None:
        inbox = await self.get_inbox()
        for item in inbox.items:
            if item.item_id == item_id:
                return item
        return None

    async def _load_recent_action_results(
        self,
    ) -> tuple[dict[str, OperatorActionResult], set[str], set[str]]:
        events = await self._stores.event_store.get_all_events()
        latest_by_item: dict[str, tuple[datetime, OperatorActionResult]] = {}
        suppressed_alerts: set[str] = set()
        handled_retry_items: set[str] = set()

        for event in events:
            if event.type != EventType.OPERATOR_ACTION_RECORDED:
                continue
            payload = event.payload
            item_id = str(payload.get("item_id", "")).strip()
            if not item_id:
                continue
            handled_at = payload.get("handled_at") or event.ts.isoformat()
            try:
                result = OperatorActionResult(
                    item_id=item_id,
                    kind=OperatorActionKind(str(payload.get("action_kind", "ack_alert"))),
                    source=OperatorActionSource(str(payload.get("source", "system"))),
                    outcome=OperatorActionOutcome(str(payload.get("outcome", "failed"))),
                    message=str(payload.get("message", "")),
                    task_id=(event.task_id if not event.task_id.startswith("ops-") else None),
                    audit_event_id=event.event_id,
                    retry_launch=(
                        None
                        if not payload.get("result_task_id")
                        else {
                            "source_task_id": event.task_id,
                            "result_task_id": str(payload.get("result_task_id")),
                        }
                    ),
                    handled_at=datetime.fromisoformat(str(handled_at)),
                )
            except Exception:
                continue
            previous = latest_by_item.get(item_id)
            if previous is None or result.handled_at >= previous[0]:
                latest_by_item[item_id] = (result.handled_at, result)
            if (
                item_id.startswith("alert:")
                and result.kind == OperatorActionKind.ACK_ALERT
                and result.outcome == OperatorActionOutcome.SUCCEEDED
            ):
                suppressed_alerts.add(item_id)
            if (
                result.kind == OperatorActionKind.RETRY_TASK
                and result.outcome
                in {
                    OperatorActionOutcome.SUCCEEDED,
                    OperatorActionOutcome.ALREADY_HANDLED,
                }
                and result.retry_launch is not None
            ):
                handled_retry_items.add(item_id)

        latest_results = {item_id: item[1] for item_id, item in latest_by_item.items()}
        return latest_results, suppressed_alerts, handled_retry_items

    async def _build_approval_items(
        self,
        now: datetime,
        recent_results: dict[str, OperatorActionResult],
    ) -> list[OperatorInboxItem]:
        items: list[OperatorInboxItem] = []
        pending_records = self._approval_manager.get_pending_approvals()
        for record in pending_records:
            request = record.request
            task = await self._stores.task_store.get_task(request.task_id)
            item_id = f"approval:{request.approval_id}"
            items.append(
                OperatorInboxItem(
                    item_id=item_id,
                    kind=OperatorItemKind.APPROVAL,
                    state=OperatorItemState.PENDING,
                    title=f"{request.tool_name} 需要审批",
                    summary=request.risk_explanation,
                    task_id=request.task_id,
                    thread_id=task.thread_id if task is not None else None,
                    source_ref=request.approval_id,
                    created_at=request.created_at,
                    expires_at=request.expires_at,
                    pending_age_seconds=max((now - request.created_at).total_seconds(), 0.0),
                    suggested_actions=["review_approval_request"],
                    quick_actions=[
                        OperatorQuickAction(
                            kind=OperatorActionKind.APPROVE_ONCE,
                            label="批准一次",
                            style="primary",
                        ),
                        OperatorQuickAction(
                            kind=OperatorActionKind.APPROVE_ALWAYS,
                            label="总是批准",
                            style="secondary",
                        ),
                        OperatorQuickAction(
                            kind=OperatorActionKind.DENY,
                            label="拒绝",
                            style="danger",
                        ),
                    ],
                    recent_action_result=recent_results.get(item_id),
                    metadata={
                        "tool_name": request.tool_name,
                        "policy_label": request.policy_label,
                        "side_effect_level": request.side_effect_level.value,
                    },
                )
            )
        return items

    async def _build_alert_items(
        self,
        now: datetime,
        recent_results: dict[str, OperatorActionResult],
        suppressed_alerts: set[str],
    ) -> list[OperatorInboxItem]:
        response = await self._task_journal_service.get_journal(self._watchdog_config)
        alert_entries = response.groups.drifted + response.groups.stalled
        items: list[OperatorInboxItem] = []
        for entry in alert_entries:
            task_id = str(entry.get("task_id", "")).strip()
            if not task_id:
                continue
            task = await self._stores.task_store.get_task(task_id)
            if task is None:
                continue
            source_ref, created_at = await self._resolve_alert_source_ref(
                task_id,
                entry,
                task.updated_at,
            )
            item_id = f"alert:{task_id}:{source_ref}"
            if item_id in suppressed_alerts:
                continue
            summary_parts: list[str] = []
            drift_summary = entry.get("drift_summary")
            if isinstance(drift_summary, dict):
                drift_type = str(drift_summary.get("drift_type", "")).strip()
                if drift_type:
                    summary_parts.append(f"drift={drift_type}")
                stall_duration = drift_summary.get("stall_duration_seconds")
                if stall_duration is not None:
                    summary_parts.append(f"stalled={float(stall_duration):.0f}s")
            if not summary_parts:
                summary_parts.append("任务长时间无进展，需要人工确认")
            items.append(
                OperatorInboxItem(
                    item_id=item_id,
                    kind=OperatorItemKind.ALERT,
                    state=OperatorItemState.PENDING,
                    title=f"任务 {task.title} 需要关注",
                    summary=" / ".join(summary_parts),
                    task_id=task_id,
                    thread_id=task.thread_id,
                    source_ref=source_ref,
                    created_at=created_at,
                    pending_age_seconds=max((now - created_at).total_seconds(), 0.0),
                    suggested_actions=[
                        str(action) for action in entry.get("suggested_actions", [])
                    ],
                    quick_actions=[
                        OperatorQuickAction(
                            kind=OperatorActionKind.ACK_ALERT,
                            label="确认告警",
                            style="secondary",
                        )
                    ],
                    recent_action_result=recent_results.get(item_id),
                    metadata={
                        "journal_state": str(entry.get("journal_state", "")),
                        "task_status": str(entry.get("task_status", "")),
                        "drift_artifact_id": str(entry.get("drift_artifact_id", "") or ""),
                    },
                )
            )
        return items

    async def _build_retryable_failure_items(
        self,
        now: datetime,
        recent_results: dict[str, OperatorActionResult],
        handled_retry_items: set[str],
    ) -> list[OperatorInboxItem]:
        tasks = await self._stores.task_store.list_tasks_by_statuses([TaskStatus.FAILED])
        items: list[OperatorInboxItem] = []
        for task in tasks:
            events = await self._stores.event_store.get_events_for_task(task.task_id)
            latest_worker_return = next(
                (
                    event
                    for event in reversed(events)
                    if event.type == EventType.WORKER_RETURNED
                ),
                None,
            )
            if latest_worker_return is None:
                continue
            if not bool(latest_worker_return.payload.get("retryable")):
                continue
            item_id = f"task:{task.task_id}"
            if item_id in handled_retry_items:
                continue
            items.append(
                OperatorInboxItem(
                    item_id=item_id,
                    kind=OperatorItemKind.RETRYABLE_FAILURE,
                    state=OperatorItemState.PENDING,
                    title=f"任务 {task.title} 可重试",
                    summary=str(
                        latest_worker_return.payload.get("summary", "worker 返回可重试失败")
                    ),
                    task_id=task.task_id,
                    thread_id=task.thread_id,
                    source_ref=latest_worker_return.event_id,
                    created_at=latest_worker_return.ts,
                    pending_age_seconds=max((now - latest_worker_return.ts).total_seconds(), 0.0),
                    suggested_actions=["retry_task"],
                    quick_actions=[
                        OperatorQuickAction(
                            kind=OperatorActionKind.RETRY_TASK,
                            label="重试",
                            style="primary",
                        )
                    ],
                    recent_action_result=recent_results.get(item_id),
                    metadata={
                        "worker_id": str(latest_worker_return.payload.get("worker_id", "")),
                        "error_type": str(latest_worker_return.payload.get("error_type", "")),
                        "backend": str(latest_worker_return.payload.get("backend", "")),
                    },
                )
            )
        return items

    def _build_pairing_items(
        self,
        now: datetime,
        recent_results: dict[str, OperatorActionResult],
        degraded_sources: list[str],
    ) -> list[OperatorInboxItem]:
        if self._telegram_state_store is None:
            return []
        pairings = self._telegram_state_store.list_pending_pairings()
        if getattr(self._telegram_state_store, "last_issue", None):
            degraded_sources.append("pairings")
        items: list[OperatorInboxItem] = []
        for pairing in pairings:
            item_id = f"pairing:{pairing.user_id}"
            title = pairing.display_name or pairing.username or pairing.user_id
            items.append(
                OperatorInboxItem(
                    item_id=item_id,
                    kind=OperatorItemKind.PAIRING_REQUEST,
                    state=OperatorItemState.PENDING,
                    title=f"Telegram pairing 请求: {title}",
                    summary=pairing.last_message_text or "待 owner 审批 Telegram 私聊授权",
                    source_ref=pairing.code,
                    created_at=pairing.requested_at,
                    expires_at=pairing.expires_at,
                    pending_age_seconds=max((now - pairing.requested_at).total_seconds(), 0.0),
                    suggested_actions=["approve_pairing", "reject_pairing"],
                    quick_actions=[
                        OperatorQuickAction(
                            kind=OperatorActionKind.APPROVE_PAIRING,
                            label="批准 pairing",
                            style="primary",
                        ),
                        OperatorQuickAction(
                            kind=OperatorActionKind.REJECT_PAIRING,
                            label="拒绝",
                            style="danger",
                        ),
                    ],
                    recent_action_result=recent_results.get(item_id),
                    metadata={
                        "user_id": pairing.user_id,
                        "chat_id": pairing.chat_id,
                        "username": pairing.username,
                        "code": pairing.code,
                    },
                )
            )
        return items

    async def _resolve_alert_source_ref(
        self,
        task_id: str,
        entry: dict[str, Any],
        fallback_created_at: datetime,
    ) -> tuple[str, datetime]:
        drift_events = await self._stores.event_store.get_events_by_types_since(
            task_id=task_id,
            event_types=[EventType.TASK_DRIFT_DETECTED],
            since_ts=datetime.now(tz=UTC) - timedelta(days=_ALERT_LOOKBACK_DAYS),
        )
        if drift_events:
            latest_drift = max(drift_events, key=lambda event: event.ts)
            return latest_drift.event_id, latest_drift.ts
        last_event_ts = str(entry.get("last_event_ts", "")).strip()
        created_at = (
            datetime.fromisoformat(last_event_ts)
            if last_event_ts
            else fallback_created_at
        )
        return f"stalled-{int(created_at.timestamp())}", created_at

    @staticmethod
    def _sort_key(item: OperatorInboxItem) -> tuple[int, datetime, float]:
        priority = {
            OperatorItemKind.APPROVAL: 0,
            OperatorItemKind.ALERT: 1,
            OperatorItemKind.RETRYABLE_FAILURE: 2,
            OperatorItemKind.PAIRING_REQUEST: 3,
        }[item.kind]
        deadline = item.expires_at or item.created_at
        age = -(item.pending_age_seconds or 0.0)
        return priority, deadline, age
