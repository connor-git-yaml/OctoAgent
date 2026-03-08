"""LLMService 工具接线测试。"""

from __future__ import annotations

from typing import Any

from octoagent.gateway.services.llm_service import LLMService
from octoagent.provider import ModelCallResult, TokenUsage
from octoagent.skills import SkillOutputEnvelope, SkillRunResult, SkillRunStatus


class _FakeFallbackManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_with_fallback(self, *, messages, model_alias):
        self.calls.append({"messages": messages, "model_alias": model_alias})
        return ModelCallResult(
            content="fallback",
            model_alias=model_alias,
            model_name="mock-fallback",
            provider="mock",
            duration_ms=5,
            token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,
            fallback_reason="",
        )


class _FakeSkillRunner:
    def __init__(self, result: SkillRunResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def run(self, *, manifest, execution_context, skill_input, prompt):
        self.calls.append(
            {
                "manifest": manifest,
                "execution_context": execution_context,
                "skill_input": skill_input,
                "prompt": prompt,
            }
        )
        return self._result


def _build_skill_result(content: str = "tool-answer") -> SkillRunResult:
    return SkillRunResult(
        status=SkillRunStatus.SUCCEEDED,
        output=SkillOutputEnvelope(
            content=content,
            complete=True,
            metadata={
                "model_name": "gpt-5.4",
                "provider": "openai",
                "token_usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        ),
        attempts=1,
        steps=1,
        duration_ms=123,
    )


async def test_llm_service_prefers_skill_runner_when_selected_tools_present() -> None:
    fallback = _FakeFallbackManager()
    skill_runner = _FakeSkillRunner(_build_skill_result("已通过工具确认当前 project。"))
    service = LLMService(
        fallback_manager=fallback,
        skill_runner=skill_runner,  # type: ignore[arg-type]
    )

    result = await service.call(
        "当前 project 是什么？",
        model_alias="main",
        task_id="task-1",
        trace_id="trace-task-1",
        metadata={
            "selected_tools_json": '["project.inspect","task.inspect"]',
            "selected_worker_type": "general",
        },
        worker_capability="llm_generation",
        tool_profile="minimal",
    )

    assert result.content == "已通过工具确认当前 project。"
    assert result.model_name == "gpt-5.4"
    assert result.provider == "openai"
    assert fallback.calls == []
    assert len(skill_runner.calls) == 1
    manifest = skill_runner.calls[0]["manifest"]
    assert manifest.tools_allowed == ["project.inspect", "task.inspect"]
    assert "不要声称自己没有工具" in manifest.description


async def test_llm_service_falls_back_when_selected_tools_missing() -> None:
    fallback = _FakeFallbackManager()
    skill_runner = _FakeSkillRunner(_build_skill_result())
    service = LLMService(
        fallback_manager=fallback,
        skill_runner=skill_runner,  # type: ignore[arg-type]
    )

    result = await service.call(
        "你好",
        model_alias="main",
        task_id="task-2",
        trace_id="trace-task-2",
        metadata={"selected_tools_json": "[]"},
    )

    assert result.content == "fallback"
    assert len(fallback.calls) == 1
    assert skill_runner.calls == []
