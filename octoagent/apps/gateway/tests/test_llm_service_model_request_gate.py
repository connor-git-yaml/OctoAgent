"""F137 硬闸：LLMService 两条路径对 ModelRequestsNotAllowedError re-raise（AC-3）。

- 路径 1（FallbackManager 文本路径）：primary 抛 gate 异常 → LLMService.call
  propagate，不降级 Echo。
- 路径 2（SkillRunner 决策环路径）：skill_runner.run 抛 gate 异常 →
  ``_try_call_with_tools`` propagate，不落 ``return None`` → FallbackManager(Echo)。
"""

from __future__ import annotations

from typing import Any

import pytest
from octoagent.gateway.services.llm_service import LLMService
from octoagent.provider import (
    FallbackManager,
    ModelCallResult,
    ModelRequestsNotAllowedError,
)


class _RaisingAdapter:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def complete(self, **kwargs: Any) -> ModelCallResult:
        raise self._error


class _EchoStub:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs: Any) -> ModelCallResult:
        self.calls += 1
        return ModelCallResult(
            content="echo",
            model_alias="main",
            model_name="echo",
            provider="echo",
            duration_ms=1,
        )


class _RaisingSkillRunner:
    """duck-type SkillRunner：run() 直接抛 gate 异常。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, **kwargs: Any):
        self.calls += 1
        raise ModelRequestsNotAllowedError("leak via skill runner")


async def test_fallback_path_propagates_gate_error() -> None:
    """路径 1：FallbackManager primary 撞闸 → LLMService.call 直接炸，Echo 零调用。"""
    echo = _EchoStub()
    service = LLMService(
        fallback_manager=FallbackManager(
            primary=_RaisingAdapter(ModelRequestsNotAllowedError("leak")),
            fallback=echo,
        ),
    )
    with pytest.raises(ModelRequestsNotAllowedError):
        await service.call("hello")
    assert echo.calls == 0


async def test_skill_runner_path_propagates_gate_error() -> None:
    """路径 2：skill_runner.run 撞闸 → _try_call_with_tools 不吞成 None（Echo 零调用）。"""
    echo = _EchoStub()
    runner = _RaisingSkillRunner()
    service = LLMService(
        fallback_manager=FallbackManager(
            primary=_RaisingAdapter(AssertionError("primary 不应被调用")),
            fallback=echo,
        ),
        skill_runner=runner,  # type: ignore[arg-type]
    )
    with pytest.raises(ModelRequestsNotAllowedError):
        await service.call(
            "hello",
            task_id="task-1",
            trace_id="trace-1",
            metadata={
                "tool_selection": {"mounted_tools": ["system.echo"]},
            },
        )
    assert runner.calls == 1
    assert echo.calls == 0
