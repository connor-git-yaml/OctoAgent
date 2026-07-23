"""F151 测试侧 RuntimeServiceBundle typed fixture。"""

import asyncio
from dataclasses import dataclass
from typing import Any

from octoagent.gateway.services.runtime_service_bundle import RuntimeServiceBundle
from octoagent.gateway.services.task_runner import TaskRunner


@dataclass(frozen=True, slots=True)
class RuntimeServiceFixture:
    """公开测试所使用的最终 runtime service identity。"""

    llm_service: Any
    provider_router: Any
    background_tasks: set[asyncio.Task[Any]]
    bundle: RuntimeServiceBundle


class DelegatingLLMService:
    """在保持 bundle identity 不变时切换单次测试调用的模型替身。"""

    def __init__(self) -> None:
        self.delegate: Any | None = None

    async def call(
        self,
        prompt_or_messages: Any,
        model_alias: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if self.delegate is None:
            raise AssertionError("DelegatingLLMService.delegate must be set before use")
        return await self.delegate.call(
            prompt_or_messages,
            model_alias=model_alias,
            **kwargs,
        )


def runtime_service_fixture(
    llm_service: Any,
    *,
    provider_router: Any | None = None,
    background_tasks: set[asyncio.Task[Any]] | None = None,
) -> RuntimeServiceFixture:
    """以同一组实例创建测试 bundle，禁止 class/global service injection。"""
    final_router = provider_router if provider_router is not None else object()
    final_background = background_tasks if background_tasks is not None else set()
    bundle = RuntimeServiceBundle(
        llm_service=llm_service,
        provider_router=final_router,
        background_tasks=final_background,
    )
    return RuntimeServiceFixture(
        llm_service=llm_service,
        provider_router=final_router,
        background_tasks=final_background,
        bundle=bundle,
    )


async def start_runtime_task_runner(
    store_group: Any,
    sse_hub: Any,
    llm_service: Any,
) -> tuple[RuntimeServiceFixture, TaskRunner]:
    """用同一 typed bundle 启动测试所需的真实 TaskRunner。"""
    fixture = runtime_service_fixture(llm_service)
    task_runner = TaskRunner(
        store_group=store_group,
        sse_hub=sse_hub,
        runtime_services=fixture.bundle,
        timeout_seconds=60,
        monitor_interval_seconds=0.05,
    )
    await task_runner.startup()
    return fixture, task_runner
