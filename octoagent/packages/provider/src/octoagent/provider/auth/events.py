"""凭证事件发射 -- 对齐 contracts/auth-adapter-api.md SS6, FR-012

记录凭证生命周期到 Event Store（仅元信息，不含凭证值）。
对齐 Constitution C2（Everything is an Event）和 C5（Least Privilege）。

003-b 扩展: 新增 emit_oauth_event() 支持 OAuth 流程事件。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from octoagent.core.models.enums import ActorType, EventType
from octoagent.core.models.event import Event
from ulid import ULID

log = structlog.get_logger()

# OAuth 事件 payload 中禁止出现的敏感字段名
_SENSITIVE_FIELDS = frozenset({
    "access_token",
    "refresh_token",
    "code_verifier",
    "state",
})


class EventStoreProtocol(Protocol):
    """Event Store 协议（松耦合，不强依赖具体实现）"""

    async def append_event(self, event: Event) -> Any: ...

    async def get_next_task_seq(self, task_id: str) -> int: ...


async def emit_credential_event(
    event_store: EventStoreProtocol | None,
    event_type: EventType,
    provider: str,
    credential_type: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """发射凭证生命周期事件到 Event Store

    注意: payload 中不包含凭证值本身，仅含元信息。

    Args:
        event_store: Event Store 实例（None 时仅记录日志）
        event_type: 事件类型（CREDENTIAL_LOADED / CREDENTIAL_EXPIRED / CREDENTIAL_FAILED）
        provider: Provider 标识
        credential_type: 凭证类型（api_key / token / oauth）
        extra: 额外元信息
    """
    payload: dict[str, Any] = {
        "provider": provider,
        "credential_type": credential_type,
    }
    if extra:
        payload.update(extra)

    # 结构化日志记录（始终执行）
    log.info(
        "credential_event",
        event_type=event_type.value,
        provider=provider,
        credential_type=credential_type,
    )

    # 写入 Event Store（如果可用）
    if event_store is not None:
        try:
            await _append_system_event(
                event_store=event_store,
                event_type=event_type,
                payload=payload,
            )
        except Exception as exc:
            # Event Store 写入失败不应阻断凭证操作（C6: Degrade Gracefully）
            log.warning(
                "credential_event_store_failed",
                event_type=event_type.value,
                error=str(exc),
            )


async def emit_oauth_event(
    event_store: EventStoreProtocol | None,
    event_type: EventType,
    provider_id: str,
    payload: dict[str, Any],
) -> None:
    """发射 OAuth 流程事件到 Event Store

    复用 emit_credential_event 的 Event Store 写入逻辑。
    payload 中 MUST NOT 包含 access_token, refresh_token,
    code_verifier, state 的明文值。

    支持四种事件类型:
    - OAUTH_STARTED: {provider_id, flow_type, environment_mode}
    - OAUTH_SUCCEEDED: {provider_id, token_type, expires_in, has_refresh_token, has_account_id}
    - OAUTH_FAILED: {provider_id, failure_reason, failure_stage}
    - OAUTH_REFRESHED: {provider_id, new_expires_in}

    Args:
        event_store: Event Store 实例（None 时仅记录日志）
        event_type: OAUTH_STARTED / OAUTH_SUCCEEDED / OAUTH_FAILED / OAUTH_REFRESHED
        provider_id: Provider canonical_id
        payload: 事件负载（已脱敏）
    """
    # 安全检查：确保 payload 中不含敏感字段
    for field in _SENSITIVE_FIELDS:
        if field in payload:
            log.error(
                "oauth_event_sensitive_field_detected",
                field=field,
                event_type=event_type.value,
            )
            # 移除敏感字段而非阻断
            del payload[field]

    # 添加 provider_id 到 payload（如未包含）
    safe_payload: dict[str, Any] = {"provider_id": provider_id, **payload}

    # 结构化日志记录（始终执行）
    log.info(
        "oauth_event",
        event_type=event_type.value,
        provider_id=provider_id,
    )

    # 写入 Event Store（如果可用）
    if event_store is not None:
        try:
            await _append_system_event(
                event_store=event_store,
                event_type=event_type,
                payload=safe_payload,
            )
        except Exception as exc:
            # Event Store 写入失败不应阻断 OAuth 操作（C6: Degrade Gracefully）
            log.warning(
                "oauth_event_store_failed",
                event_type=event_type.value,
                error=str(exc),
            )


# ─────────────────── Feature 078 Phase 4：refresh 事件 helpers ───────────────────


async def emit_refresh_triggered(
    event_store: EventStoreProtocol | None,
    provider_id: str,
    *,
    mode: str,
    force: bool = False,
) -> None:
    """refresh 开始之前触发。``mode`` ∈ {preemptive, reactive}。"""
    await emit_oauth_event(
        event_store=event_store,
        event_type=EventType.OAUTH_REFRESH_TRIGGERED,
        provider_id=provider_id,
        payload={"mode": mode, "force": force},
    )


async def emit_refresh_failed(
    event_store: EventStoreProtocol | None,
    provider_id: str,
    *,
    error_type: str,
    retry_count: int = 0,
) -> None:
    """refresh 失败（单次尝试，未经过 recovery）。"""
    await emit_oauth_event(
        event_store=event_store,
        event_type=EventType.OAUTH_REFRESH_FAILED,
        provider_id=provider_id,
        payload={"error_type": error_type, "retry_count": retry_count},
    )


async def emit_refresh_recovered(
    event_store: EventStoreProtocol | None,
    provider_id: str,
    *,
    via: str,
) -> None:
    """refresh 失败后通过某种 recovery 路径成功。``via`` ∈ {store_reload, external_cli}。"""
    await emit_oauth_event(
        event_store=event_store,
        event_type=EventType.OAUTH_REFRESH_RECOVERED,
        provider_id=provider_id,
        payload={"via": via},
    )


async def emit_refresh_exhausted(
    event_store: EventStoreProtocol | None,
    provider_id: str,
    *,
    attempt_count: int,
    last_error: str,
) -> None:
    """所有 refresh fallback（含 store reload + CLI adopt）都失败。"""
    await emit_oauth_event(
        event_store=event_store,
        event_type=EventType.OAUTH_REFRESH_EXHAUSTED,
        provider_id=provider_id,
        payload={
            "attempt_count": attempt_count,
            # 截断防止超长 stack trace 进入事件；敏感字段仍由 _SENSITIVE_FIELDS 过滤
            "last_error": str(last_error)[:300],
        },
    )


async def emit_adopted_from_external_cli(
    event_store: EventStoreProtocol | None,
    provider_id: str,
    *,
    source_path: str,
    gate_reason: str,
) -> None:
    """从 ~/.codex/auth.json 接管凭证成功。"""
    await emit_oauth_event(
        event_store=event_store,
        event_type=EventType.OAUTH_ADOPTED_FROM_EXTERNAL_CLI,
        provider_id=provider_id,
        payload={
            "source_path": source_path,
            "gate_reason": gate_reason,
        },
    )


async def _append_system_event(
    *,
    event_store: EventStoreProtocol,
    event_type: EventType,
    payload: dict[str, Any],
) -> None:
    """写入 system task 事件，兼容新旧 EventStore 接口。"""
    task_id = "system"
    append_event = getattr(event_store, "append_event", None)
    get_next_task_seq = getattr(event_store, "get_next_task_seq", None)
    if callable(append_event) and callable(get_next_task_seq):
        event = Event(
            event_id=str(ULID()),
            task_id=task_id,
            task_seq=await get_next_task_seq(task_id),
            ts=datetime.now(UTC),
            type=event_type,
            actor=ActorType.SYSTEM,
            payload=payload,
            trace_id="trace-system",
        )
        has_committed_api = hasattr(type(event_store), "append_event_committed")
        if not has_committed_api and hasattr(event_store, "__dict__"):
            has_committed_api = "append_event_committed" in event_store.__dict__

        if has_committed_api:
            await event_store.append_event_committed(event, update_task_pointer=False)
            return
        await append_event(event)
        return

    # 兼容旧 mock（append 接口）
    legacy_append = getattr(event_store, "append", None)
    if callable(legacy_append):
        await legacy_append(
            task_id=task_id,
            event_type=event_type.value,
            actor_type="system",
            payload=payload,
        )
        return

    raise AttributeError("event_store does not provide append_event/get_next_task_seq or append")
