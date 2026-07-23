"""Gateway runtime graph 的实例级服务 holder。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class RuntimeServiceModeError(RuntimeError):
    """服务构造或调用违反 runtime/storage-only 模式合同时抛出。"""


def validate_runtime_service_mode(
    *,
    runtime_services: RuntimeServiceBundle | None,
    storage_only: bool,
) -> bool:
    """验证 runtime bundle 与 storage-only 严格二选一并返回模式。"""
    runtime_mode = runtime_services is not None
    storage_mode = storage_only is True
    if runtime_mode == storage_mode or storage_only not in {False, True}:
        raise RuntimeServiceModeError("runtime_services 与 storage_only=True 必须严格二选一")
    return storage_mode


@dataclass(slots=True)
class RuntimeServiceBundle:
    """单个 Gateway runtime graph 共享的最终运行时依赖。"""

    llm_service: Any
    provider_router: Any
    background_tasks: set[asyncio.Task[Any]]
    _llm_closed: bool = field(default=False, init=False, repr=False)
    _router_closed: bool = field(default=False, init=False, repr=False)

    async def aclose(self) -> None:
        """幂等关闭本地 LLM 链，再关闭共享 ProviderRouter。"""
        if not self._llm_closed:
            await self.llm_service.aclose()
            self._llm_closed = True
        if not self._router_closed:
            await self.provider_router.aclose()
            self._router_closed = True
