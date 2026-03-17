"""LiteLLM token/cost 数据回传测试 (T0.4)。

覆盖：
- SSE 路径：stream_options 设置、从最终 chunk 提取 token usage
- Responses API 路径：从 response.completed 提取 usage + cost
- SkillOutputEnvelope 默认值回归
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from octoagent.skills.litellm_client import LiteLLMSkillClient
from octoagent.skills.manifest import SkillManifest
from octoagent.skills.models import SkillExecutionContext, SkillOutputEnvelope

from .conftest import EchoInput, EchoOutput


# ═══════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════


def _make_manifest(model_alias: str = "main") -> SkillManifest:
    return SkillManifest(
        skill_id="test.token_data",
        version="0.1.0",
        input_model=EchoInput,
        output_model=EchoOutput,
        model_alias=model_alias,
        tools_allowed=[],
    )


def _make_context() -> SkillExecutionContext:
    return SkillExecutionContext(
        task_id="task-token",
        trace_id="trace-token",
        caller="worker",
        conversation_messages=[{"role": "user", "content": "hello"}],
    )


class _MockAsyncLines:
    """模拟 httpx response.aiter_lines()。"""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._idx = 0

    def __aiter__(self) -> _MockAsyncLines:
        return self

    async def __anext__(self) -> str:
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx]
        self._idx += 1
        return line


def _build_sse_mock_response(
    chunks: list[dict[str, Any]],
    status_code: int = 200,
) -> MagicMock:
    """构建模拟 SSE 流式 httpx 响应。"""
    lines: list[str] = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
    lines.append("data: [DONE]")

    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.aiter_lines = lambda: _MockAsyncLines(lines)

    return resp


def _build_responses_mock_response(
    events: list[dict[str, Any]],
    status_code: int = 200,
) -> MagicMock:
    """构建模拟 Responses API 流式 httpx 响应。"""
    lines: list[str] = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")

    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.aiter_lines = lambda: _MockAsyncLines(lines)

    return resp


class _AsyncContextManager:
    """模拟 async with client.stream(...) as resp。"""

    def __init__(self, resp: MagicMock) -> None:
        self._resp = resp

    async def __aenter__(self) -> MagicMock:
        return self._resp

    async def __aexit__(self, *args: Any) -> None:
        pass


# ═══════════════════════════════════════
# SkillOutputEnvelope 默认值回归
# ═══════════════════════════════════════


class TestSkillOutputEnvelopeDefaults:
    def test_token_usage_default(self) -> None:
        """token_usage 默认为空 dict。"""
        envelope = SkillOutputEnvelope(content="test", complete=True)
        assert envelope.token_usage == {}

    def test_cost_usd_default(self) -> None:
        """cost_usd 默认为 0.0。"""
        envelope = SkillOutputEnvelope(content="test", complete=True)
        assert envelope.cost_usd == 0.0

    def test_custom_token_usage(self) -> None:
        """自定义 token_usage 正确存储。"""
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        envelope = SkillOutputEnvelope(
            content="test", complete=True, token_usage=usage, cost_usd=0.01
        )
        assert envelope.token_usage == usage
        assert envelope.cost_usd == 0.01


# ═══════════════════════════════════════
# SSE 路径 token 数据
# ═══════════════════════════════════════


class TestSSETokenData:
    @pytest.mark.asyncio
    async def test_sse_extracts_usage_from_final_chunk(self) -> None:
        """SSE 路径：从最终 chunk 的 usage 字段提取 token 数据。"""
        chunks = [
            # 内容 chunk
            {
                "choices": [{"delta": {"content": "Hello"}}],
            },
            {
                "choices": [{"delta": {"content": " world"}}],
            },
            # 最终 chunk 带 usage
            {
                "choices": [{"delta": {}}],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 80,
                    "total_tokens": 280,
                },
            },
        ]
        mock_resp = _build_sse_mock_response(chunks)

        mock_client = MagicMock()
        mock_client.stream = lambda *args, **kwargs: _AsyncContextManager(mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client = LiteLLMSkillClient(
            proxy_url="http://localhost:4000",
            master_key="test-key",
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            envelope = await client.generate(
                manifest=_make_manifest(),
                execution_context=_make_context(),
                prompt="hello",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert envelope.content == "Hello world"
        assert envelope.complete is True
        assert envelope.token_usage["prompt_tokens"] == 200
        assert envelope.token_usage["completion_tokens"] == 80
        assert envelope.token_usage["total_tokens"] == 280

    @pytest.mark.asyncio
    async def test_sse_includes_stream_options(self) -> None:
        """SSE 路径：请求 body 包含 stream_options.include_usage=true。"""
        captured_body: dict[str, Any] = {}

        chunks = [
            {"choices": [{"delta": {"content": "ok"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        ]
        mock_resp = _build_sse_mock_response(chunks)

        mock_client = MagicMock()

        def capture_stream(method: str, url: str, json: dict, **kw: Any) -> _AsyncContextManager:
            captured_body.update(json)
            return _AsyncContextManager(mock_resp)

        mock_client.stream = capture_stream
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client = LiteLLMSkillClient(
            proxy_url="http://localhost:4000",
            master_key="test-key",
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            await client.generate(
                manifest=_make_manifest(),
                execution_context=_make_context(),
                prompt="test",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert captured_body.get("stream") is True
        assert captured_body.get("stream_options") == {"include_usage": True}

    @pytest.mark.asyncio
    async def test_sse_no_usage_returns_empty(self) -> None:
        """SSE 路径：无 usage 数据时，token_usage 为空 dict，cost_usd=0.0。"""
        chunks = [
            {"choices": [{"delta": {"content": "done"}}]},
        ]
        mock_resp = _build_sse_mock_response(chunks)

        mock_client = MagicMock()
        mock_client.stream = lambda *args, **kwargs: _AsyncContextManager(mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client = LiteLLMSkillClient(
            proxy_url="http://localhost:4000",
            master_key="test-key",
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            envelope = await client.generate(
                manifest=_make_manifest(),
                execution_context=_make_context(),
                prompt="test",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert envelope.token_usage == {}
        assert envelope.cost_usd == 0.0


# ═══════════════════════════════════════
# Responses API 路径 token 数据
# ═══════════════════════════════════════


class TestResponsesAPITokenData:
    @pytest.mark.asyncio
    async def test_responses_extracts_usage_and_cost(self) -> None:
        """Responses API：从 response.completed 提取 usage + cost。"""
        events = [
            {
                "type": "response.output_text.delta",
                "delta": "Bonjour",
            },
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-4o",
                    "output": [],
                    "usage": {
                        "input_tokens": 300,
                        "output_tokens": 120,
                        "total_tokens": 420,
                    },
                    "cost": 0.025,
                },
            },
        ]
        mock_resp = _build_responses_mock_response(events)

        mock_client = MagicMock()
        mock_client.stream = lambda *args, **kwargs: _AsyncContextManager(mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client = LiteLLMSkillClient(
            proxy_url="http://localhost:4000",
            master_key="test-key",
            responses_model_aliases={"responses-model"},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            envelope = await client.generate(
                manifest=_make_manifest(model_alias="responses-model"),
                execution_context=_make_context(),
                prompt="hello",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert envelope.content == "Bonjour"
        assert envelope.complete is True
        assert envelope.token_usage["prompt_tokens"] == 300
        assert envelope.token_usage["completion_tokens"] == 120
        assert envelope.cost_usd == 0.025

    @pytest.mark.asyncio
    async def test_responses_cost_from_underscore_field(self) -> None:
        """Responses API：cost 从 _cost 字段提取（LiteLLM 兼容）。"""
        events = [
            {"type": "response.output_text.delta", "delta": "ok"},
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-4o",
                    "output": [],
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                        "total_tokens": 60,
                    },
                    "_cost": 0.003,
                },
            },
        ]
        mock_resp = _build_responses_mock_response(events)

        mock_client = MagicMock()
        mock_client.stream = lambda *args, **kwargs: _AsyncContextManager(mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client = LiteLLMSkillClient(
            proxy_url="http://localhost:4000",
            master_key="test-key",
            responses_model_aliases={"responses-model"},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            envelope = await client.generate(
                manifest=_make_manifest(model_alias="responses-model"),
                execution_context=_make_context(),
                prompt="test",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert envelope.cost_usd == 0.003

    @pytest.mark.asyncio
    async def test_responses_no_cost_defaults_zero(self) -> None:
        """Responses API：无 cost 字段时默认 0.0。"""
        events = [
            {"type": "response.output_text.delta", "delta": "ok"},
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-4o",
                    "output": [],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            },
        ]
        mock_resp = _build_responses_mock_response(events)

        mock_client = MagicMock()
        mock_client.stream = lambda *args, **kwargs: _AsyncContextManager(mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        client = LiteLLMSkillClient(
            proxy_url="http://localhost:4000",
            master_key="test-key",
            responses_model_aliases={"responses-model"},
        )

        with patch("httpx.AsyncClient", return_value=mock_client):
            envelope = await client.generate(
                manifest=_make_manifest(model_alias="responses-model"),
                execution_context=_make_context(),
                prompt="test",
                feedback=[],
                attempt=1,
                step=1,
            )

        assert envelope.cost_usd == 0.0
        assert envelope.token_usage["prompt_tokens"] == 10
