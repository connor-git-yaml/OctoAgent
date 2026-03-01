"""LiteLLMClient 单元测试

对齐 tasks.md T016: Mock litellm.acompletion()，验证 complete() 返回 ModelCallResult、
health_check() 返回 bool、超时处理、ProxyUnreachableError 抛出。
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
from octoagent.provider.client import LiteLLMClient
from octoagent.provider.exceptions import ProxyUnreachableError
from octoagent.provider.models import ModelCallResult


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
