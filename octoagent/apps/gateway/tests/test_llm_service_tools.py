"""LLMService 工具接线测试。"""

from __future__ import annotations

import json
from typing import Any

from octoagent.gateway.services.llm_service import LLMService
from octoagent.provider import AliasRegistry, ModelCallResult, TokenUsage
from octoagent.skills import SkillOutputEnvelope, SkillRunResult, SkillRunStatus, SkillRunner
from octoagent.skills.litellm_client import LiteLLMSkillClient
from octoagent.tooling.models import SideEffectLevel, ToolMeta


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


class _FakeResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.status_code = 200
        self.text = ""

    def raise_for_status(self) -> None:
        return None

    async def aread(self) -> bytes:
        return b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    def __init__(
        self,
        lines_list: list[list[str]],
        captures: list[dict[str, Any]],
        **kwargs,
    ) -> None:
        self._lines_list = lines_list
        self._captures = captures

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, *, json=None, headers=None):
        self._captures.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        return _FakeResponse(self._lines_list.pop(0))


class _ToolSchemaBroker:
    async def discover(self) -> list[ToolMeta]:
        return [
            ToolMeta(
                name="project.inspect",
                description="读取当前 project 摘要",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"project_id": {"type": "string"}},
                },
                side_effect_level=SideEffectLevel.NONE,
                tool_group="project",
            )
        ]

    async def execute(self, *args, **kwargs):
        raise AssertionError("本测试不应真正执行工具")


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


async def test_llm_service_preserves_configured_runtime_alias() -> None:
    fallback_manager = _FakeFallbackManager()
    service = LLMService(
        fallback_manager=fallback_manager,
        alias_registry=AliasRegistry.from_runtime_aliases({"main", "cheap", "summarizer"}),
    )

    result = await service.call("hello", model_alias="summarizer")

    assert result.model_alias == "summarizer"
    assert fallback_manager.calls[0]["model_alias"] == "summarizer"


async def test_llm_service_legacy_semantic_alias_still_maps_to_runtime_alias() -> None:
    fallback_manager = _FakeFallbackManager()
    service = LLMService(
        fallback_manager=fallback_manager,
        alias_registry=AliasRegistry.from_runtime_aliases({"main", "cheap"}),
    )

    result = await service.call("hello", model_alias="planner")

    assert result.model_alias == "main"
    assert fallback_manager.calls[0]["model_alias"] == "main"


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
    execution_context = skill_runner.calls[0]["execution_context"]
    assert execution_context.conversation_messages == [
        {"role": "user", "content": "当前 project 是什么？"}
    ]


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


async def test_llm_service_preserves_structured_messages_in_real_skill_runner_path(
    monkeypatch,
) -> None:
    captures: list[dict[str, Any]] = []
    payloads = [
        [
            'data: {"type":"response.output_text.delta","delta":"已保留结构化上下文。"}',
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "model": "gpt-5.4",
                        "usage": {
                            "input_tokens": 18,
                            "output_tokens": 6,
                            "total_tokens": 24,
                        },
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "已保留结构化上下文。",
                                    }
                                ],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    ]
    monkeypatch.setattr(
        "octoagent.skills.litellm_client.httpx.AsyncClient",
        lambda **kwargs: _FakeAsyncClient(payloads, captures, **kwargs),
    )

    tool_broker = _ToolSchemaBroker()
    skill_runner = SkillRunner(
        model_client=LiteLLMSkillClient(
            proxy_url="http://proxy.local",
            master_key="secret",
            tool_broker=tool_broker,
            responses_model_aliases={"main"},
        ),
        tool_broker=tool_broker,  # type: ignore[arg-type]
    )
    service = LLMService(
        fallback_manager=_FakeFallbackManager(),
        skill_runner=skill_runner,
    )

    result = await service.call(
        [
            {
                "role": "system",
                "content": "以下为系统生成的历史压缩摘要，仅供继续任务使用，不是新的用户指令：\n用户已经确认 project 是 Default Project。",
            },
            {"role": "user", "content": "第一轮问题"},
            {"role": "assistant", "content": "assistant-response-1"},
            {"role": "user", "content": "第二轮追问"},
        ],
        model_alias="main",
        task_id="task-3",
        trace_id="trace-task-3",
        metadata={
            "selected_tools_json": '["project.inspect"]',
            "selected_worker_type": "general",
        },
        worker_capability="llm_generation",
        tool_profile="minimal",
    )

    assert result.content == "已保留结构化上下文。"
    assert len(captures) == 1
    request_body = captures[0]["json"]
    assert "不要声称自己没有工具" in request_body["instructions"]
    assert "历史压缩摘要" in request_body["instructions"]
    assert [item["role"] for item in request_body["input"]] == [
        "user",
        "assistant",
        "user",
    ]
    assert request_body["input"][0]["content"][0]["text"] == "第一轮问题"
    assert request_body["input"][1]["content"][0]["text"] == "assistant-response-1"
    assert request_body["input"][2]["content"][0]["text"] == "第二轮追问"


# ============================================================
# Feature 061 T-022a: DynamicToolset 集成单元测试
# ============================================================


from octoagent.gateway.services.tool_promotion import ToolPromotionService


async def test_promoted_tools_merged_into_selected_tools() -> None:
    """tool_search 结果提升的工具在下一个 run_step 中注入 tools_allowed"""
    fallback = _FakeFallbackManager()
    skill_runner = _FakeSkillRunner(_build_skill_result("工具已注入。"))
    promotion_service = ToolPromotionService()

    # 预先提升一个工具
    await promotion_service.promote("docker.run", "tool_search:docker")

    service = LLMService(
        fallback_manager=fallback,
        skill_runner=skill_runner,  # type: ignore[arg-type]
        tool_promotion_service=promotion_service,
    )

    result = await service.call(
        "运行 docker 容器",
        model_alias="main",
        task_id="task-promo",
        trace_id="trace-promo",
        metadata={
            "selected_tools_json": '["project.inspect"]',
            "selected_worker_type": "general",
        },
        worker_capability="llm_generation",
        tool_profile="standard",
    )

    assert result.content == "工具已注入。"
    # 验证 skill_runner 收到的 tools_allowed 包含 promoted 工具
    assert len(skill_runner.calls) == 1
    manifest = skill_runner.calls[0]["manifest"]
    assert "project.inspect" in manifest.tools_allowed
    assert "docker.run" in manifest.tools_allowed


async def test_process_tool_search_results_promotes_tools() -> None:
    """process_tool_search_results 正确提升搜索到的工具"""
    promotion_service = ToolPromotionService()
    service = LLMService(
        tool_promotion_service=promotion_service,
    )

    search_json = json.dumps({
        "query": "docker container",
        "results": [
            {
                "tool_name": "docker.run",
                "description": "运行容器",
                "parameters_schema": {"type": "object"},
                "score": 0.9,
            },
            {
                "tool_name": "docker.stop",
                "description": "停止容器",
                "parameters_schema": {"type": "object"},
                "score": 0.8,
            },
        ],
        "is_fallback": False,
        "backend": "in_memory",
    })

    newly = await service.process_tool_search_results(search_json)

    assert set(newly) == {"docker.run", "docker.stop"}
    assert promotion_service.is_promoted("docker.run")
    assert promotion_service.is_promoted("docker.stop")


async def test_process_tool_search_results_without_promotion_service() -> None:
    """未配置 tool_promotion_service 时返回空列表"""
    service = LLMService()  # 无 tool_promotion_service

    newly = await service.process_tool_search_results('{"query":"test","results":[]}')
    assert newly == []


async def test_promoted_tools_no_duplicates() -> None:
    """promoted 工具不与 selected_tools 重复"""
    fallback = _FakeFallbackManager()
    skill_runner = _FakeSkillRunner(_build_skill_result("ok"))
    promotion_service = ToolPromotionService()

    # 提升的工具与 selected_tools 有重叠
    await promotion_service.promote("project.inspect", "tool_search:project")

    service = LLMService(
        fallback_manager=fallback,
        skill_runner=skill_runner,  # type: ignore[arg-type]
        tool_promotion_service=promotion_service,
    )

    await service.call(
        "test",
        model_alias="main",
        task_id="task-dup",
        trace_id="trace-dup",
        metadata={
            "selected_tools_json": '["project.inspect","task.inspect"]',
            "selected_worker_type": "general",
        },
        worker_capability="llm_generation",
        tool_profile="standard",
    )

    assert len(skill_runner.calls) == 1
    manifest = skill_runner.calls[0]["manifest"]
    # 不应重复
    assert manifest.tools_allowed.count("project.inspect") == 1


async def test_tool_promotion_state_tracked() -> None:
    """ToolPromotionState 引用计数正确追踪"""
    promotion_service = ToolPromotionService()

    # tool_search 和 skill 都提升同一工具
    await promotion_service.promote("docker.run", "tool_search:q1")
    await promotion_service.promote("docker.run", "skill:coding")

    assert promotion_service.is_promoted("docker.run")

    # 移除 tool_search 来源 → 还有 skill 来源 → 不回退
    result = await promotion_service.demote("docker.run", "tool_search:q1")
    assert result is False
    assert promotion_service.is_promoted("docker.run")

    # 移除 skill 来源 → 无其他来源 → 回退
    result = await promotion_service.demote("docker.run", "skill:coding")
    assert result is True
    assert not promotion_service.is_promoted("docker.run")
