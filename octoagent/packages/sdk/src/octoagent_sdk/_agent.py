"""轻量级 Agent 类 — OctoAgent SDK 的核心入口。

设计原则：
- 3 行代码可用
- 不依赖 FastAPI / Web 服务
- 渐进式复杂度：简单 → 带工具 → 带 Memory → 带 Policy
- 参考 Pydantic AI 的简洁 API
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import structlog

from ._tool import ToolSpec

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Agent 执行结果。"""

    text: str = ""
    tool_calls_count: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentChunk:
    """流式响应的单个片段。"""

    text: str = ""
    is_tool_call: bool = False
    tool_name: str = ""
    is_final: bool = False


# ---------------------------------------------------------------------------
# Agent 类
# ---------------------------------------------------------------------------

_MAX_STEPS = 30
_DEFAULT_TIMEOUT_S = 120


class Agent:
    """轻量级 Agent — 用 LLM + 工具完成任务。

    基本使用：
        agent = Agent(model="gpt-4o")
        result = await agent.run("你好")

    带工具：
        @tool
        async def search(query: str) -> str:
            return "结果..."

        agent = Agent(model="gpt-4o", tools=[search])
        result = await agent.run("搜索 Python")

    带 Memory：
        from octoagent.memory import SqliteMemoryStore
        agent = Agent(model="gpt-4o", memory=SqliteMemoryStore("./memory.db"))

    同步使用：
        result = agent.run_sync("你好")
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        tools: list[ToolSpec | Any] | None = None,
        system_prompt: str = "",
        api_key: str = "",
        api_base: str = "",
        max_steps: int = _MAX_STEPS,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        temperature: float = 0.7,
        memory: Any | None = None,
        policy: Any | None = None,
    ) -> None:
        self._model = model
        self._system_prompt = system_prompt
        self._api_key = api_key
        self._api_base = api_base
        self._max_steps = max_steps
        self._timeout_s = timeout_s
        self._temperature = temperature
        self._memory = memory
        self._policy = policy

        # 解析工具
        self._tools: dict[str, ToolSpec] = {}
        for t in tools or []:
            if isinstance(t, ToolSpec):
                self._tools[t.name] = t
            elif callable(t):
                # 未装饰的函数，自动包装
                spec = ToolSpec(t)
                self._tools[spec.name] = spec

        # LLM 客户端（延迟初始化）
        self._client: Any | None = None

    # ----- 公共 API -----

    async def run(self, prompt: str, **kwargs: Any) -> AgentResult:
        """异步执行 Agent 任务。"""
        start = time.monotonic()
        client = self._ensure_client()

        messages = self._build_initial_messages(prompt)
        tools_schema = self._build_tools_schema()
        total_tokens = 0
        tool_calls_count = 0

        for step in range(self._max_steps):
            # 调用 LLM
            response = await self._call_llm(client, messages, tools_schema)
            total_tokens += response.get("usage", {}).get("total_tokens", 0)

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            # 无工具调用 → 直接返回
            if finish_reason != "tool_calls" or not message.get("tool_calls"):
                text = message.get("content", "") or ""
                duration_ms = int((time.monotonic() - start) * 1000)
                return AgentResult(
                    text=text,
                    tool_calls_count=tool_calls_count,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                )

            # 有工具调用 → 执行并继续
            messages.append(message)
            for tc in message.get("tool_calls", []):
                tool_calls_count += 1
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    arguments = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}

                # Policy 检查（如果有）
                if self._policy is not None:
                    allowed = await self._check_policy(tool_name, arguments)
                    if not allowed:
                        result_text = f"ERROR: Policy denied tool call: {tool_name}"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": result_text,
                        })
                        continue

                # 执行工具
                tool_spec = self._tools.get(tool_name)
                if tool_spec is None:
                    result_text = f"ERROR: Unknown tool: {tool_name}"
                else:
                    try:
                        result_text = await tool_spec.execute(arguments)
                    except Exception as exc:
                        result_text = f"ERROR: {type(exc).__name__}: {exc}"
                        log.warning(
                            "tool_execution_failed",
                            tool=tool_name,
                            error=str(exc),
                        )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_text,
                })

        # 超过 max_steps
        duration_ms = int((time.monotonic() - start) * 1000)
        return AgentResult(
            text="[Agent reached max steps without final answer]",
            tool_calls_count=tool_calls_count,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            metadata={"max_steps_reached": True},
        )

    async def run_stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[AgentChunk]:
        """流式执行（当前实现为非流式的包装，后续优化为真流式）。"""
        result = await self.run(prompt, **kwargs)
        yield AgentChunk(text=result.text, is_final=True)

    def run_sync(self, prompt: str, **kwargs: Any) -> AgentResult:
        """同步执行（包装异步 run）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # 在已有 event loop 中（如 Jupyter），用 nest_asyncio 或新线程
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.run(prompt, **kwargs))
                return future.result(timeout=self._timeout_s)
        else:
            return asyncio.run(self.run(prompt, **kwargs))

    # ----- 内部方法 -----

    def _ensure_client(self) -> Any:
        """延迟初始化 LLM 客户端。"""
        if self._client is not None:
            return self._client

        try:
            import httpx
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
            return self._client
        except ImportError:
            raise ImportError(
                "httpx is required for Agent. Install with: pip install httpx"
            )

    def _build_initial_messages(self, prompt: str) -> list[dict[str, Any]]:
        """构建初始消息列表。"""
        messages: list[dict[str, Any]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_tools_schema(self) -> list[dict[str, Any]] | None:
        """构建 OpenAI tools schema。"""
        if not self._tools:
            return None
        return [spec.to_openai_tool() for spec in self._tools.values()]

    async def _call_llm(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """调用 LLM API（OpenAI-compatible）。"""
        # 优先使用 LiteLLMClient（如果可用）
        try:
            from octoagent.provider.client import LiteLLMClient
            return await self._call_via_litellm(messages, tools)
        except ImportError:
            pass

        # 否则直接 HTTP 调用 OpenAI-compatible API
        return await self._call_via_http(client, messages, tools)

    async def _call_via_litellm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """通过 litellm SDK 调用。"""
        try:
            from litellm import acompletion
        except ImportError:
            raise ImportError(
                "litellm is required for LLM calls. Install with: pip install litellm"
            )

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await acompletion(**kwargs)
        return response.model_dump()

    async def _call_via_http(
        self,
        client: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """通过 HTTP 直接调用 OpenAI-compatible API。"""
        base = self._api_base or "https://api.openai.com/v1"
        url = f"{base}/chat/completions"

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def _check_policy(self, tool_name: str, arguments: dict) -> bool:
        """Policy 审批检查。"""
        if self._policy is None:
            return True
        try:
            if hasattr(self._policy, "check"):
                return await self._policy.check(tool_name, arguments)
            if callable(self._policy):
                return self._policy(tool_name, arguments)
        except Exception as exc:
            log.warning("policy_check_failed", tool=tool_name, error=str(exc))
        return True  # 降级允许
