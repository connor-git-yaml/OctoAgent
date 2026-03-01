"""凭证事件发射 -- 对齐 contracts/auth-adapter-api.md SS6, FR-012

记录凭证生命周期到 Event Store（仅元信息，不含凭证值）。
对齐 Constitution C2（Everything is an Event）和 C5（Least Privilege）。
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog
from octoagent.core.models.enums import EventType

log = structlog.get_logger()


class EventStoreProtocol(Protocol):
    """Event Store 协议（松耦合，不强依赖具体实现）"""

    async def append(
        self,
        task_id: str,
        event_type: str,
        actor_type: str,
        payload: dict[str, Any],
    ) -> Any: ...


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
            await event_store.append(
                task_id="system",
                event_type=event_type.value,
                actor_type="system",
                payload=payload,
            )
        except Exception as exc:
            # Event Store 写入失败不应阻断凭证操作（C6: Degrade Gracefully）
            log.warning(
                "credential_event_store_failed",
                event_type=event_type.value,
                error=str(exc),
            )
