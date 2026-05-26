"""F103c Worker Audit Logger — 把 Worker 关键内部 log/error 升级到 EventStore audit chain。

Constitution 闭环：
- 原则 2 Everything is an Event：Worker 内部关键 log 不再仅落 stderr
- 原则 8 Observability：主 Agent / control_plane 可观察 worker 健康
- 原则 11 Context Hygiene：payload 由 caller 负责脱敏；helper 不做敏感字段过滤
- H1 强化：Worker fatal error 经主 Agent (NotificationService priority=HIGH) feedback

设计点（Codex pre-impl review 闭环）：
- helper 入参用 ``TaskService``（不是 ``StoreGroup``）：经 ``append_structured_event``
  路径同时落 EventStore + SSE 广播（PM3）
- ``audit_worker_error`` 把新 emit 的 ``WORKER_ERROR`` event_id 传入
  ``state_transition_event_id``，让 NotificationService sha256 去重做幂等（PM1）
- ``derive_agent_runtime_id`` 按 ``agent_runtime_id / target_agent_runtime_id /
  source_agent_runtime_id`` 优先级派生；派生失败时返回 ``degraded_reason``
  ``agent_runtime_id_unavailable``，禁止静默空串（PH1）
- 全部 helper 内部 try/except 兜底；emit / notify 失败仅 log.warning 不抛
"""

from __future__ import annotations

from typing import Any, Literal

import structlog

from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from octoagent.core.models.payloads import (
    WorkerErrorPayload,
    WorkerLogEmittedPayload,
)

from .notification import NotificationPriority, NotificationService
from .task_service import TaskService

log = structlog.get_logger()

LogLevel = Literal["info", "warning", "error"]

_AGENT_RUNTIME_ID_KEYS: tuple[str, ...] = (
    "agent_runtime_id",
    "target_agent_runtime_id",
    "source_agent_runtime_id",
)

DEGRADED_REASON_UNAVAILABLE = "agent_runtime_id_unavailable"


def derive_agent_runtime_id(
    metadata: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """按优先级从 envelope/task metadata 派生 agent_runtime_id。

    Returns:
        (agent_runtime_id, degraded_reason)：
        - 派生成功 → ("<非空字符串>", None)
        - 派生失败 → ("", "agent_runtime_id_unavailable")
    """
    if not metadata:
        return ("", DEGRADED_REASON_UNAVAILABLE)
    for key in _AGENT_RUNTIME_ID_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return (value, None)
    return ("", DEGRADED_REASON_UNAVAILABLE)


def _assert_audit_inputs(agent_runtime_id: str, degraded_reason: str | None) -> None:
    """PH1 入口断言：禁止静默空串。"""
    if agent_runtime_id == "" and degraded_reason is None:
        raise AssertionError(
            "worker_audit_logger: agent_runtime_id 为空时 degraded_reason 必填 "
            "（F103c PH1 闭环；用 derive_agent_runtime_id 派生）"
        )


async def audit_worker_log(
    task_service: TaskService | None,
    *,
    task_id: str,
    agent_runtime_id: str,
    level: LogLevel,
    key: str,
    payload: dict[str, Any] | None = None,
    agent_session_id: str | None = None,
    degraded_reason: str | None = None,
) -> Event | None:
    """Worker 关键内部 logger 升级 EventStore audit chain。

    双轨：先调 structlog 保持本地排障路径，再 emit ``WORKER_LOG_EMITTED``。

    Args:
        task_service: gateway TaskService 实例（带 sse_hub）。可为 None（仅 structlog）。
        task_id: 必填。
        agent_runtime_id: 必填字符串；空串仅当 degraded_reason 同时设置。
        level: structlog 级别。
        key: 原 structlog key 名（保留语义）。
        payload: 原 structlog kwargs，须由 caller 脱敏。
        agent_session_id: 可选会话关联。
        degraded_reason: audit chain 降级原因；空 runtime_id 时必填。

    Returns:
        emit 成功返回 Event；失败/无 task_service 返回 None。
    """
    _assert_audit_inputs(agent_runtime_id, degraded_reason)

    payload = payload or {}
    # 双轨：保留 structlog 调用
    getattr(log, level)(key, task_id=task_id, **payload)

    if task_service is None:
        return None

    try:
        event_payload = WorkerLogEmittedPayload(
            task_id=task_id,
            agent_runtime_id=agent_runtime_id,
            level=level,
            key=key,
            payload=payload,
            agent_session_id=agent_session_id,
            degraded_reason=degraded_reason,
        )
        event = await task_service.append_structured_event(
            task_id=task_id,
            event_type=EventType.WORKER_LOG_EMITTED,
            actor=ActorType.WORKER,
            payload=event_payload.model_dump(mode="json"),
        )
        return event
    except Exception:
        log.warning(
            "worker_audit_log_emit_failed",
            task_id=task_id,
            key=key,
            exc_info=True,
        )
        return None


async def audit_worker_error(
    task_service: TaskService | None,
    *,
    task_id: str,
    agent_runtime_id: str,
    error_class: str,
    error_summary: str,
    traceback_artifact_id: str | None = None,
    agent_session_id: str | None = None,
    notification_service: NotificationService | None = None,
    task_title: str | None = None,
    degraded_reason: str | None = None,
) -> Event | None:
    """Worker fatal exception 走 EventStore + 主 Agent feedback。

    流程：
    1. emit ``WORKER_ERROR`` 事件（取返回 event）
    2. 如 notification_service 非 None → notify_task_state_change priority=HIGH，
       state_transition_event_id=event.event_id（PM1 幂等闭环）

    全部容错；任一步失败仅 log.warning 不抛，便于在 task_runner exception
    分支中安全调用而不破坏后续 mark_failed / _ensure_task_failed 路径。

    Args:
        error_summary: 已脱敏；caller 应保证 ≤200 字符（payload schema 强约束）。
    """
    _assert_audit_inputs(agent_runtime_id, degraded_reason)

    # 双轨：error 级别 structlog
    log.error(
        "worker_error_audit",
        task_id=task_id,
        error_class=error_class,
        error_summary=error_summary,
    )

    if task_service is None:
        return None

    event: Event | None = None
    try:
        error_payload = WorkerErrorPayload(
            task_id=task_id,
            agent_runtime_id=agent_runtime_id,
            error_class=error_class,
            error_summary=error_summary[:200],
            traceback_artifact_id=traceback_artifact_id,
            agent_session_id=agent_session_id,
            degraded_reason=degraded_reason,
        )
        event = await task_service.append_structured_event(
            task_id=task_id,
            event_type=EventType.WORKER_ERROR,
            actor=ActorType.WORKER,
            payload=error_payload.model_dump(mode="json"),
        )
    except Exception:
        log.warning("worker_error_emit_failed", task_id=task_id, exc_info=True)
        return None

    if notification_service is not None and event is not None:
        try:
            await notification_service.notify_task_state_change(
                task_id=task_id,
                event_type="WORKER_ERROR",
                payload={
                    "task_id": task_id,
                    "task_title": task_title or task_id,
                    "error_class": error_class,
                    "error_summary": error_summary[:200],
                },
                priority=NotificationPriority.HIGH,
                state_transition_event_id=event.event_id,
            )
        except Exception:
            log.warning(
                "worker_error_notify_failed",
                task_id=task_id,
                exc_info=True,
            )

    return event
