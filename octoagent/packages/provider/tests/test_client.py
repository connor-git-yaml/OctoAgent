"""LiteLLMClient 单元测试

对齐 tasks.md T016: Mock litellm.acompletion()，验证 complete() 返回 ModelCallResult、
health_check() 返回 bool、超时处理、ProxyUnreachableError 抛出。
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from octoagent.provider.client import LiteLLMClient
from octoagent.provider.exceptions import ProviderError, ProxyUnreachableError
from octoagent.provider.models import ModelCallResult, ReasoningConfig


@pytest.fixture
def client():
    """创建 LiteLLMClient 实例"""
    return LiteLLMClient(
        proxy_base_url="http://localhost:4000",
        proxy_api_key="sk-test",
        timeout_s=30,
    )


def _make_mock_litellm_response(
    content: str = "Hello!",
    model: str = "gpt-4o-mini",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    total_tokens: int = 30,
):
    """构造 Mock LiteLLM acompletion 返回"""
    response = MagicMock()
    response.model = model

    # choices
    choice = MagicMock()
    choice.message.content = content
    response.choices = [choice]

    # usage
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = total_tokens
    response.usage = usage

    # _hidden_params
    response._hidden_params = {
        "custom_llm_provider": "openai",
        "response_cost": 0.001,
    }

    return response


class TestLiteLLMClientComplete:
    """complete() 方法测试"""

    @patch("octoagent.provider.client.acompletion")
    async def test_successful_call(self, mock_acompletion, client):
        """成功调用返回完整 ModelCallResult"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        result = await client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            model_alias="main",
        )

        assert isinstance(result, ModelCallResult)
        assert result.content == "Hello!"
        assert result.model_alias == "main"
        assert result.model_name == "gpt-4o-mini"
        assert result.duration_ms >= 0
        assert result.is_fallback is False

    @patch("octoagent.provider.client.acompletion")
    async def test_connection_error_raises_proxy_unreachable(
        self, mock_acompletion, client
    ):
        """连接错误抛出 ProxyUnreachableError"""
        mock_acompletion.side_effect = ConnectionError("Connection refused")

        with pytest.raises(ProxyUnreachableError) as exc_info:
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )
        assert "localhost:4000" in str(exc_info.value)

    @patch("octoagent.provider.client.acompletion")
    async def test_timeout_raises_proxy_unreachable(self, mock_acompletion, client):
        """超时抛出 ProxyUnreachableError"""

        mock_acompletion.side_effect = TimeoutError("timeout")

        with pytest.raises(ProxyUnreachableError):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

    @patch("octoagent.provider.client.acompletion")
    async def test_model_alias_passed_to_litellm(self, mock_acompletion, client):
        """model_alias 正确传递给 litellm"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            model_alias="cheap",
        )

        call_kwargs = mock_acompletion.call_args
        assert call_kwargs.kwargs["model"] == "openai/cheap"

    @patch("octoagent.provider.client.acompletion")
    async def test_cost_tracked(self, mock_acompletion, client):
        """成本数据正确计算"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        result = await client.complete(
            messages=[{"role": "user", "content": "test"}],
        )

        # 成本应该有值（从 mock 的 _hidden_params 或 completion_cost）
        assert result.cost_usd >= 0.0

    @patch("octoagent.provider.client.acompletion")
    async def test_token_usage_parsed(self, mock_acompletion, client):
        """Token 使用数据正确解析"""
        mock_acompletion.return_value = _make_mock_litellm_response(
            prompt_tokens=50, completion_tokens=100, total_tokens=150
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "test"}],
        )

        assert result.token_usage.prompt_tokens == 50
        assert result.token_usage.completion_tokens == 100
        assert result.token_usage.total_tokens == 150

    @patch("octoagent.provider.client.log.error")
    @patch("octoagent.provider.client.acompletion")
    async def test_error_log_redacts_sensitive_tokens(
        self, mock_acompletion, mock_log_error, client
    ):
        """异常日志应脱敏，避免 api_key/token 泄露"""
        mock_acompletion.side_effect = RuntimeError(
            "upstream failed api_key=sk-secret token=tok-abc"
        )

        with pytest.raises(ProviderError):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

        assert mock_log_error.call_count == 1
        _, kwargs = mock_log_error.call_args
        assert "sk-secret" not in kwargs["error"]
        assert "tok-abc" not in kwargs["error"]
        assert "[REDACTED]" in kwargs["error"]

    @patch("octoagent.provider.client.stream_chunk_builder")
    @patch("octoagent.provider.client.acompletion")
    async def test_stream_alias_collects_chunks_into_completion(
        self,
        mock_acompletion,
        mock_stream_chunk_builder,
    ):
        """被标记为流式 alias 时，应消费 SSE 并组装回完整响应。"""

        class FakeStream:
            def __init__(self, chunks):
                self._chunks = chunks

            def __aiter__(self):
                async def _gen():
                    for chunk in self._chunks:
                        yield chunk

                return _gen()

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            timeout_s=30,
            stream_model_aliases={"main"},
        )
        chunks = [MagicMock(), MagicMock()]
        mock_acompletion.return_value = FakeStream(chunks)
        mock_stream_chunk_builder.return_value = _make_mock_litellm_response(
            content="streamed hello",
            model="gpt-5.4",
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "Hello"}],
            model_alias="main",
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["stream"] is True
        assert call_kwargs["stream_options"] == {"include_usage": True}
        assert mock_stream_chunk_builder.call_args.kwargs["chunks"] == chunks
        assert result.content == "streamed hello"
        assert result.model_name == "gpt-5.4"

    async def test_responses_alias_uses_proxy_responses_stream_and_parses_content(self):
        """Codex alias 应走 /v1/responses 并拼接 output_text delta。"""

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self):
                for line in [
                    'data: {"type":"response.output_text.delta","delta":"你好"}',
                    'data: {"type":"response.output_text.delta","delta":"！"}',
                    'data: {"type":"response.completed","response":{"model":"gpt-5.4","usage":{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}',
                    "data: [DONE]",
                ]:
                    yield line

        class _StreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

        calls: list[tuple[str, str, dict[str, str], dict[str, object]]] = []

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

            def stream(
                self,
                method: str,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
            ):
                calls.append((method, url, headers, json))
                return _StreamContext()

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4001",
            proxy_api_key="sk-test",
            timeout_s=30,
            responses_model_aliases={"main"},
        )

        with patch("octoagent.provider.client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.complete(
                messages=[
                    {"role": "system", "content": "请简短回复"},
                    {"role": "user", "content": "你好"},
                ],
                model_alias="main",
            )

        assert result.content == "你好！"
        assert result.model_name == "gpt-5.4"
        assert result.provider == "openai"
        assert result.token_usage.prompt_tokens == 3
        assert result.token_usage.completion_tokens == 2
        assert result.token_usage.total_tokens == 5
        assert result.cost_unavailable is True
        assert calls == [
            (
                "POST",
                "http://localhost:4001/v1/responses",
                {
                    "Authorization": "Bearer sk-test",
                    "Content-Type": "application/json",
                },
                {
                    "model": "main",
                    "instructions": "请简短回复",
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "你好"}],
                        }
                    ],
                    "store": False,
                    "stream": True,
                },
            )
        ]

    async def test_responses_alias_uses_default_reasoning_map(self):
        """未显式传 reasoning 时，Responses alias 使用默认 reasoning 配置。"""

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self):
                yield 'data: {"type":"response.completed","response":{"model":"gpt-5.4","output":[{"content":[{"type":"output_text","text":"ok"}]}]}}'
                yield "data: [DONE]"

        class _StreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

        captured: list[dict[str, object]] = []

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

            def stream(
                self,
                method: str,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
            ):
                del method, url, headers
                captured.append(json)
                return _StreamContext()

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4001",
            proxy_api_key="sk-test",
            timeout_s=30,
            responses_model_aliases={"main"},
            responses_reasoning_aliases={"main": ReasoningConfig(effort="xhigh")},
        )

        with patch("octoagent.provider.client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.complete(
                messages=[{"role": "user", "content": "你好"}],
                model_alias="main",
            )

        assert result.content == "ok"
        assert captured[0]["reasoning"] == {"effort": "xhigh"}

    async def test_responses_alias_encodes_assistant_history_as_output_text(self):
        """Responses API 历史 assistant 消息应使用 output_text。"""

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            async def aiter_lines(self):
                yield 'data: {"type":"response.completed","response":{"model":"gpt-5.4","output":[{"content":[{"type":"output_text","text":"杭州今天多云。"}]}]}}'
                yield "data: [DONE]"

        class _StreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

        captured: list[dict[str, object]] = []

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

            def stream(
                self,
                method: str,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
            ):
                del method, url, headers
                captured.append(json)
                return _StreamContext()

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4001",
            proxy_api_key="sk-test",
            timeout_s=30,
            responses_model_aliases={"main"},
        )

        with patch("octoagent.provider.client.httpx.AsyncClient", FakeAsyncClient):
            result = await client.complete(
                messages=[
                    {"role": "user", "content": "今天杭州天气怎么样？"},
                    {"role": "assistant", "content": "我去查一下。"},
                    {"role": "user", "content": "直接告诉我结果。"},
                ],
                model_alias="main",
            )

        assert result.content == "杭州今天多云。"
        assert captured[0]["input"] == [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "今天杭州天气怎么样？"}],
            },
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "我去查一下。"}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "直接告诉我结果。"}],
            },
        ]


class TestLiteLLMClientRoutingOverrides:
    """路由覆盖测试（003-b JWT 方案多认证隔离）"""

    @patch("octoagent.provider.client.acompletion")
    async def test_api_base_override(self, mock_acompletion, client):
        """api_base 覆盖优先于实例默认 Proxy URL"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            api_base="https://chatgpt.com/backend-api/codex",
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "https://chatgpt.com/backend-api/codex"

    @patch("octoagent.provider.client.acompletion")
    async def test_api_key_override(self, mock_acompletion, client):
        """api_key 覆盖优先于实例默认 Proxy key"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            api_key="jwt-access-token",
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_key"] == "jwt-access-token"

    @patch("octoagent.provider.client.acompletion")
    async def test_extra_headers_passed(self, mock_acompletion, client):
        """extra_headers 传递给 LiteLLM SDK"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        headers = {
            "chatgpt-account-id": "acct-123",
            "OpenAI-Beta": "responses=experimental",
        }
        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            extra_headers=headers,
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["extra_headers"] == headers

    @patch("octoagent.provider.client.acompletion")
    async def test_no_override_uses_defaults(self, mock_acompletion, client):
        """不传覆盖参数时使用实例默认值"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "http://localhost:4000"
        assert call_kwargs["api_key"] == "sk-test"
        assert "extra_headers" not in call_kwargs

    @patch("octoagent.provider.client.acompletion")
    async def test_full_jwt_routing(self, mock_acompletion, client):
        """完整 JWT 路由覆盖（模拟 HandlerChainResult 传递）"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            api_base="https://chatgpt.com/backend-api/codex",
            api_key="jwt-token-value",
            extra_headers={
                "chatgpt-account-id": "acct-e2e",
                "OpenAI-Beta": "responses=experimental",
                "originator": "pi",
            },
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "https://chatgpt.com/backend-api/codex"
        assert call_kwargs["api_key"] == "jwt-token-value"
        assert call_kwargs["extra_headers"]["chatgpt-account-id"] == "acct-e2e"


class TestLiteLLMClientHealthCheck:
    """health_check() 方法测试"""

    @patch("httpx.AsyncClient.get")
    async def test_healthy_proxy(self, mock_get, client):
        """Proxy 可达时返回 True"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = await client.health_check()
        assert result is True

    @patch("httpx.AsyncClient.get")
    async def test_unreachable_proxy(self, mock_get, client):
        """Proxy 不可达时返回 False"""
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        result = await client.health_check()
        assert result is False

    @patch("httpx.AsyncClient.get")
    async def test_timeout(self, mock_get, client):
        """健康检查超时返回 False"""
        mock_get.side_effect = httpx.TimeoutException("timeout")

        result = await client.health_check()
        assert result is False

    @patch("httpx.AsyncClient.get")
    async def test_server_error(self, mock_get, client):
        """服务器错误返回 False"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_get.return_value = mock_response

        result = await client.health_check()
        assert result is False


class TestLiteLLMClientReasoning:
    """reasoning 参数传递测试"""

    @patch("octoagent.provider.client.acompletion")
    async def test_reasoning_effort_passed(self, mock_acompletion, client):
        """ReasoningConfig.effort 作为 reasoning_effort 传递给 LiteLLM"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            reasoning=ReasoningConfig(effort="high"),
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "high"

    @patch("octoagent.provider.client.acompletion")
    async def test_reasoning_xhigh(self, mock_acompletion, client):
        """xhigh 级别正确传递"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            reasoning=ReasoningConfig(effort="xhigh"),
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "xhigh"

    @patch("octoagent.provider.client.acompletion")
    async def test_reasoning_none_effort(self, mock_acompletion, client):
        """effort=none 正确传递（禁用推理）"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            reasoning=ReasoningConfig(effort="none"),
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["reasoning_effort"] == "none"

    @patch("octoagent.provider.client.acompletion")
    async def test_no_reasoning_omits_param(self, mock_acompletion, client):
        """不传 reasoning 时不包含 reasoning_effort"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs

    @patch("octoagent.provider.client.acompletion")
    async def test_reasoning_with_routing_overrides(self, mock_acompletion, client):
        """reasoning 与路由覆盖参数共存"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            api_base="https://chatgpt.com/backend-api",
            api_key="jwt-token",
            extra_headers={"chatgpt-account-id": "acct-123"},
            reasoning=ReasoningConfig(effort="high", summary="auto"),
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "https://chatgpt.com/backend-api"
        assert call_kwargs["api_key"] == "jwt-token"
        assert call_kwargs["extra_headers"]["chatgpt-account-id"] == "acct-123"
        assert call_kwargs["reasoning_effort"] == "high"

    @patch("octoagent.provider.client.acompletion")
    async def test_unsupported_alias_omits_reasoning_effort(self, mock_acompletion):
        """不支持 reasoning 的 alias 应自动忽略 reasoning_effort。"""
        mock_acompletion.return_value = _make_mock_litellm_response()
        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            timeout_s=30,
            reasoning_supported_aliases={"main"},
        )

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            model_alias="cheap",
            reasoning=ReasoningConfig(effort="high"),
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs


class TestResponsesApiAuthRefresh:
    """Responses API 预检查 / direct_params api_key 优先级 -- Feature 064c 接入"""

    def _make_fake_async_client(
        self, captured: list[tuple[str, dict[str, str], dict]]
    ):
        class FakeResponse:
            def raise_for_status(self) -> None:  # noqa: D401
                return None

            async def aiter_lines(self):
                yield (
                    'data: {"type":"response.completed","response":{"model":"gpt-5.4",'
                    '"output":[{"content":[{"type":"output_text","text":"ok"}]}]}}'
                )
                yield "data: [DONE]"

        class _StreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                del exc_type, exc, tb

            def stream(
                self,
                method: str,
                url: str,
                *,
                headers: dict[str, str],
                json: dict,
            ):
                del method
                captured.append((url, headers, json))
                return _StreamContext()

        return FakeAsyncClient

    async def test_responses_api_invokes_callback_before_request(self):
        """进入 Responses API 分支时会调用 auth_refresh_callback 做预检查。"""
        callback_calls: list[int] = []

        class FakeRefreshResult:
            credential_value = "refreshed-token"
            api_base_url = "https://chatgpt.com/backend-api/codex"
            extra_headers = {"chatgpt-account-id": "acct-fresh"}

        async def _callback():
            callback_calls.append(1)
            return FakeRefreshResult()

        captured: list[tuple[str, dict[str, str], dict]] = []
        FakeClient = self._make_fake_async_client(captured)

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4001",
            proxy_api_key="sk-test",
            timeout_s=30,
            responses_model_aliases={"main"},
            auth_refresh_callback=_callback,
        )

        with patch("octoagent.provider.client.httpx.AsyncClient", FakeClient):
            await client.complete(
                messages=[{"role": "user", "content": "你好"}],
                model_alias="main",
            )

        assert callback_calls == [1]
        assert len(captured) == 1
        _, headers, _ = captured[0]
        assert headers["Authorization"] == "Bearer refreshed-token"
        assert headers["chatgpt-account-id"] == "acct-fresh"

    async def test_refreshed_api_key_overrides_direct_params_snapshot(self):
        """direct_params 静态 api_key 不应覆盖 callback 刷新后的新 token。"""

        class FakeRefreshResult:
            credential_value = "refreshed-token"
            api_base_url = "https://chatgpt.com/backend-api/codex"
            extra_headers = {"chatgpt-account-id": "acct-fresh"}

        async def _callback():
            return FakeRefreshResult()

        captured: list[tuple[str, dict[str, str], dict]] = []
        FakeClient = self._make_fake_async_client(captured)

        # direct_params 携带启动快照的"过期" api_key，应被 callback 刷新结果覆盖
        client = LiteLLMClient(
            proxy_base_url="http://localhost:4001",
            proxy_api_key="sk-test",
            timeout_s=30,
            responses_model_aliases={"main"},
            responses_direct_params={
                "main": {
                    "api_base": "https://chatgpt.com/backend-api/codex",
                    "api_key": "stale-token",
                    "model": "gpt-5.4",
                    "headers": {"originator": "pi"},
                }
            },
            auth_refresh_callback=_callback,
        )

        with patch("octoagent.provider.client.httpx.AsyncClient", FakeClient):
            await client.complete(
                messages=[{"role": "user", "content": "你好"}],
                model_alias="main",
            )

        assert len(captured) == 1
        url, headers, body = captured[0]
        assert url.startswith("https://chatgpt.com/backend-api/codex")
        assert headers["Authorization"] == "Bearer refreshed-token"
        # direct_params 中的静态 headers 仍然生效（originator），account_id 来自刷新结果
        assert headers["originator"] == "pi"
        assert headers["chatgpt-account-id"] == "acct-fresh"
        assert body["model"] == "gpt-5.4"

    async def test_callback_failure_falls_back_to_direct_params(self):
        """auth_refresh_callback 抛异常时使用 direct_params 的 api_key 继续调用。"""

        async def _callback():
            raise RuntimeError("boom")

        captured: list[tuple[str, dict[str, str], dict]] = []
        FakeClient = self._make_fake_async_client(captured)

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4001",
            proxy_api_key="sk-test",
            timeout_s=30,
            responses_model_aliases={"main"},
            responses_direct_params={
                "main": {
                    "api_base": "https://chatgpt.com/backend-api/codex",
                    "api_key": "snapshot-token",
                    "model": "gpt-5.4",
                    "headers": {},
                }
            },
            auth_refresh_callback=_callback,
        )

        with patch("octoagent.provider.client.httpx.AsyncClient", FakeClient):
            await client.complete(
                messages=[{"role": "user", "content": "你好"}],
                model_alias="main",
            )

        assert len(captured) == 1
        _, headers, _ = captured[0]
        assert headers["Authorization"] == "Bearer snapshot-token"
