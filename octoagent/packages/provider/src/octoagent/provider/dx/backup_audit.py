"""Feature 022 backup 生命周期审计。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
from octoagent.core.models import (
    ActorType,
    BackupBundle,
    BackupLifecyclePayload,
    BackupScope,
    Event,
    EventCausality,
    EventType,
    RequesterInfo,
    Task,
    TaskCreatedPayload,
)
from octoagent.core.store import StoreGroup, append_event_only
from octoagent.core.store.transaction import create_task_with_initial_events
from ulid import ULID

_AUDIT_TASK_ID = "ops-recovery-audit"
_AUDIT_TRACE_ID = "trace-ops-recovery-audit"


class BackupAuditRecorder:
    """将 backup 生命周期写入现有 Event Store。"""

    def __init__(self, store_group: StoreGroup) -> None:
        self._store_group = store_group

    async def record_started(
        self,
        *,
        bundle_id: str,
        output_path: str,
        scopes: list[BackupScope],
    ) -> None:
        await self._append_lifecycle_event(
            event_type=EventType.BACKUP_STARTED,
            payload=BackupLifecyclePayload(
                bundle_id=bundle_id,
                output_path=output_path,
                scope_summary=[scope.value for scope in scopes],
                status="started",
                message="开始创建 backup bundle。",
            ),
            idempotency_key=f"backup:{bundle_id}:started",
        )

    async def record_completed(self, bundle: BackupBundle) -> None:
        await self._append_lifecycle_event(
            event_type=EventType.BACKUP_COMPLETED,
            payload=BackupLifecyclePayload(
                bundle_id=bundle.bundle_id,
                output_path=bundle.output_path,
                scope_summary=[scope.value for scope in bundle.manifest.scopes],
                status="completed",
                message=f"backup 完成，输出 {Path(bundle.output_path).name}",
            ),
            idempotency_key=f"backup:{bundle.bundle_id}:completed",
        )

    async def record_failed(
        self,
        *,
        bundle_id: str,
        output_path: str,
        scopes: list[BackupScope],
        message: str,
    ) -> None:
        await self._append_lifecycle_event(
            event_type=EventType.BACKUP_FAILED,
            payload=BackupLifecyclePayload(
                bundle_id=bundle_id,
                output_path=output_path,
                scope_summary=[scope.value for scope in scopes],
                status="failed",
                message=message,
            ),
            idempotency_key=f"backup:{bundle_id}:failed",
        )

    async def _append_lifecycle_event(
        self,
        *,
        event_type: EventType,
        payload: BackupLifecyclePayload,
        idempotency_key: str,
    ) -> None:
        await self._ensure_audit_task()
        now = datetime.now(tz=UTC)
        task_seq = await self._store_group.event_store.get_next_task_seq(_AUDIT_TASK_ID)
        event = Event(
            event_id=str(ULID()),
            task_id=_AUDIT_TASK_ID,
            task_seq=task_seq,
            ts=now,
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload.model_dump(mode="json"),
            trace_id=_AUDIT_TRACE_ID,
            causality=EventCausality(idempotency_key=idempotency_key),
        )
        await append_event_only(
            self._store_group.conn,
            self._store_group.event_store,
            event,
        )

    async def _ensure_audit_task(self) -> None:
        existing = await self._store_group.task_store.get_task(_AUDIT_TASK_ID)
        if existing is not None:
            return

        now = datetime.now(tz=UTC)
        task = Task(
            task_id=_AUDIT_TASK_ID,
            created_at=now,
            updated_at=now,
            title="系统运维审计（备份/恢复）",
            thread_id="ops-recovery",
            scope_id="ops/recovery",
            requester=RequesterInfo(channel="system", sender_id="system"),
            trace_id=_AUDIT_TRACE_ID,
        )
        created_event = Event(
            event_id=str(ULID()),
            task_id=_AUDIT_TASK_ID,
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
            trace_id=_AUDIT_TRACE_ID,
            causality=EventCausality(idempotency_key="ops-recovery-audit:create"),
        )
        try:
            await create_task_with_initial_events(
                self._store_group.conn,
                self._store_group.task_store,
                self._store_group.event_store,
                task,
                [created_event],
            )
        except aiosqlite.IntegrityError:
            await self._store_group.conn.rollback()
