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


class TestLiteLLMClientRoutingOverrides:
    """路由覆盖测试（003-b JWT 方案多认证隔离）"""

    @patch("octoagent.provider.client.acompletion")
    async def test_api_base_override(self, mock_acompletion, client):
        """api_base 覆盖优先于实例默认 Proxy URL"""
        mock_acompletion.return_value = _make_mock_litellm_response()

        await client.complete(
            messages=[{"role": "user", "content": "test"}],
            api_base="https://chatgpt.com/backend-api",
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "https://chatgpt.com/backend-api"

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
            api_base="https://chatgpt.com/backend-api",
            api_key="jwt-token-value",
            extra_headers={
                "chatgpt-account-id": "acct-e2e",
                "OpenAI-Beta": "responses=experimental",
                "originator": "octoagent",
            },
        )

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "https://chatgpt.com/backend-api"
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
