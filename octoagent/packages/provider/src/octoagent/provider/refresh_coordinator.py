"""Token 刷新并发协调器 -- 对齐 contracts/token-refresh-api.md SS4, FR-005

per-provider asyncio.Lock 实现并发刷新串行化。
保证同一 Provider 同一时刻只有一个刷新操作执行，
不同 Provider 的刷新操作互不阻塞。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

log = structlog.get_logger()

T = TypeVar("T")


class TokenRefreshCoordinator:
    """per-provider 刷新串行化协调器

    保证同一 Provider 同一时刻只有一个刷新操作执行。
    不同 Provider 的刷新操作互不阻塞。
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, provider_id: str) -> asyncio.Lock:
        """获取或创建指定 Provider 的锁"""
        return self._locks.setdefault(provider_id, asyncio.Lock())

    async def refresh_if_needed(
        self,
        provider_id: str,
        refresh_fn: Callable[[], Awaitable[T | None]],
        *,
        timeout_s: float = 30.0,
    ) -> T | None:
        """在 provider 锁保护下执行刷新

        如果锁已被其他协程持有，等待刷新完成后返回结果。
        同一时刻只有一个 refresh_fn 实际执行。

        Args:
            provider_id: Provider canonical_id（锁粒度）
            refresh_fn: 实际执行刷新的异步函数
            timeout_s: 整体超时（秒，默认 30）。包含锁等待 + 实际 refresh。
                超时后 CancelledError 通过 async with 释放锁，返回 None 不抛。
                对齐 Feature 078 Phase 3：防止 refresh_fn hang 导致整条业务线程挂死。

        Returns:
            refresh_fn 的返回值；失败或超时返回 None
        """
        try:
            return await asyncio.wait_for(
                self._run_under_lock(provider_id, refresh_fn),
                timeout=timeout_s,
            )
        except TimeoutError:
            log.error(
                "refresh_coordinator_timeout",
                provider_id=provider_id,
                timeout_s=timeout_s,
            )
            return None

    async def _run_under_lock(
        self,
        provider_id: str,
        refresh_fn: Callable[[], Awaitable[T | None]],
    ) -> T | None:
        lock = self._get_lock(provider_id)
        async with lock:
            log.debug(
                "refresh_coordinator_acquired_lock",
                provider_id=provider_id,
            )
            try:
                return await refresh_fn()
            except Exception:
                log.warning(
                    "refresh_coordinator_refresh_failed",
                    provider_id=provider_id,
                    exc_info=True,
                )
                return None
