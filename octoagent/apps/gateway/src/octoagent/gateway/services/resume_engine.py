"""ResumeEngine -- Feature 010 恢复路径与失败分类"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from octoagent.core.models import (
    TERMINAL_STATES,
    ActorType,
    Event,
    EventType,
    ResumeFailedPayload,
    ResumeFailureType,
    ResumeResult,
    ResumeStartedPayload,
    ResumeSucceededPayload,
)
from octoagent.core.store import StoreGroup
from ulid import ULID

log = structlog.get_logger()


class ResumeEngine:
    """任务恢复引擎（checkpoint 读取 + 失败分类 + 审计事件）"""

    _resume_locks: dict[str, asyncio.Lock] = {}
    _locks_guard = asyncio.Lock()
    _known_nodes = {
        "state_running",
        "model_call_started",
        "response_persisted",
        "task_succeeded",
    }

    def __init__(self, store_group: StoreGroup) -> None:
        self._stores = store_group

    async def try_resume(self, task_id: str, trigger: str = "startup") -> ResumeResult:
        """尝试从最近成功 checkpoint 恢复"""
        attempt_id = str(ULID())
        task = await self._stores.task_store.get_task(task_id)
        if task is None:
            return ResumeResult(
                ok=False,
                task_id=task_id,
                failure_type=ResumeFailureType.DEPENDENCY_MISSING,
                message="任务不存在，无法恢复",
            )

        if task.status in TERMINAL_STATES:
            return await self._emit_failed(
                task_id=task_id,
                attempt_id=attempt_id,
                failure_type=ResumeFailureType.TERMINAL_TASK,
                message=f"任务已处于终态: {task.status}",
            )

        lock = await self._try_acquire_task_lock(task_id)
        if lock is None:
            return await self._emit_failed(
                task_id=task_id,
                attempt_id=attempt_id,
                failure_type=ResumeFailureType.LEASE_CONFLICT,
                message="同一 task 已有恢复流程在执行",
            )

        checkpoint_id: str | None = None
        try:
            await self._emit_started(
                task_id=task_id,
                attempt_id=attempt_id,
                checkpoint_id=None,
                trigger=trigger,
            )

            try:
                checkpoint = await self._stores.checkpoint_store.get_latest_success(task_id)
            except Exception:
                return await self._emit_failed(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    failure_type=ResumeFailureType.SNAPSHOT_CORRUPT,
                    message="checkpoint 数据损坏，无法反序列化",
                )
            if checkpoint is None:
                return await self._emit_failed(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    failure_type=ResumeFailureType.DEPENDENCY_MISSING,
                    message="任务没有可恢复 checkpoint",
                )
            checkpoint_id = checkpoint.checkpoint_id

            if checkpoint.schema_version != 1:
                return await self._emit_failed(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    checkpoint_id=checkpoint_id,
                    failure_type=ResumeFailureType.VERSION_MISMATCH,
                    message=f"checkpoint schema_version={checkpoint.schema_version} 不兼容",
                )

            if not isinstance(checkpoint.state_snapshot, dict):
                return await self._emit_failed(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    checkpoint_id=checkpoint_id,
                    failure_type=ResumeFailureType.SNAPSHOT_CORRUPT,
                    message="checkpoint state_snapshot 结构损坏",
                )
            if checkpoint.node_id not in self._known_nodes:
                return await self._emit_failed(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    checkpoint_id=checkpoint_id,
                    failure_type=ResumeFailureType.SNAPSHOT_CORRUPT,
                    message=f"checkpoint node_id={checkpoint.node_id} 非法",
                )

            await self._emit_succeeded(
                task_id=task_id,
                attempt_id=attempt_id,
                resumed_from_node=checkpoint.node_id,
            )
            return ResumeResult(
                ok=True,
                task_id=task_id,
                checkpoint_id=checkpoint_id,
                resumed_from_node=checkpoint.node_id,
                message="恢复检查通过",
                state_snapshot=checkpoint.state_snapshot,
            )
        except Exception as exc:
            log.error(
                "resume_engine_unexpected_error",
                task_id=task_id,
                error_type=type(exc).__name__,
            )
            return await self._emit_failed(
                task_id=task_id,
                attempt_id=attempt_id,
                checkpoint_id=checkpoint_id,
                failure_type=ResumeFailureType.UNKNOWN,
                message=f"恢复流程异常: {type(exc).__name__}",
            )
        finally:
            lock.release()

    async def _try_acquire_task_lock(self, task_id: str) -> asyncio.Lock | None:
        async with self._locks_guard:
            lock = self._resume_locks.get(task_id)
            if lock is None:
                lock = asyncio.Lock()
                self._resume_locks[task_id] = lock
            if lock.locked():
                return None
            await lock.acquire()
            return lock

    async def _emit_started(
        self,
        task_id: str,
        attempt_id: str,
        checkpoint_id: str | None,
        trigger: str,
    ) -> None:
        event = await self._build_event(
            task_id=task_id,
            event_type=EventType.RESUME_STARTED,
            payload=ResumeStartedPayload(
                attempt_id=attempt_id,
                checkpoint_id=checkpoint_id,
                trigger=trigger,
            ).model_dump(),
        )
        await self._stores.event_store.append_event_committed(event)

    async def _emit_succeeded(
        self,
        task_id: str,
        attempt_id: str,
        resumed_from_node: str,
    ) -> None:
        event = await self._build_event(
            task_id=task_id,
            event_type=EventType.RESUME_SUCCEEDED,
            payload=ResumeSucceededPayload(
                attempt_id=attempt_id,
                resumed_from_node=resumed_from_node,
            ).model_dump(),
        )
        await self._stores.event_store.append_event_committed(event)

    async def _emit_failed(
        self,
        task_id: str,
        attempt_id: str,
        failure_type: ResumeFailureType,
        message: str,
        checkpoint_id: str | None = None,
    ) -> ResumeResult:
        event = await self._build_event(
            task_id=task_id,
            event_type=EventType.RESUME_FAILED,
            payload=ResumeFailedPayload(
                attempt_id=attempt_id,
                failure_type=failure_type.value,
                failure_message=message,
                recovery_hint=self._build_recovery_hint(failure_type),
            ).model_dump(),
        )
        await self._stores.event_store.append_event_committed(event)
        return ResumeResult(
            ok=False,
            task_id=task_id,
            checkpoint_id=checkpoint_id,
            failure_type=failure_type,
            message=message,
        )

    def _build_recovery_hint(self, failure_type: ResumeFailureType) -> str:
        if failure_type == ResumeFailureType.SNAPSHOT_CORRUPT:
            return "请回滚到更早 checkpoint 或重跑任务"
        if failure_type == ResumeFailureType.VERSION_MISMATCH:
            return "请执行 schema 迁移后再恢复，或直接重跑"
        if failure_type == ResumeFailureType.LEASE_CONFLICT:
            return "等待当前恢复流程结束后重试"
        if failure_type == ResumeFailureType.TERMINAL_TASK:
            return "任务已结束，无需恢复"
        if failure_type == ResumeFailureType.DEPENDENCY_MISSING:
            return "请检查 checkpoint 是否存在，必要时重跑任务"
        return "请查看事件流与日志，评估是否重跑任务"

    async def _build_event(
        self,
        task_id: str,
        event_type: EventType,
        payload: dict,
    ) -> Event:
        next_seq = await self._stores.event_store.get_next_task_seq(task_id)
        return Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=next_seq,
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id=f"trace-{task_id}",
        )
