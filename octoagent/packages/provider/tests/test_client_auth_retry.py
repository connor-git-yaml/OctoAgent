"""LiteLLMClient 认证错误重试测试 -- T011, T012, T013

验证:
- [T011] _is_auth_error() 正确识别认证类错误
- [T012] refresh-on-401 重试逻辑
- [T013] refresh_token 失效时的用户提示
对齐 contracts/token-refresh-api.md SS3, FR-002, FR-003
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octoagent.provider.client import LiteLLMClient
from octoagent.provider.exceptions import AuthenticationError, ProviderError


def _make_mock_litellm_response(content: str = "Hello!"):
    """构造 Mock LiteLLM acompletion 返回"""
    response = MagicMock()
    response.model = "gpt-4o-mini"
    choice = MagicMock()
    choice.message.content = content
    response.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    usage.total_tokens = 30
    response.usage = usage
    response._hidden_params = {
        "custom_llm_provider": "openai",
        "response_cost": 0.001,
    }
    return response


class TestIsAuthError:
    """[T011] _is_auth_error() 认证错误判定测试"""

    def test_authentication_error_recognized(self) -> None:
        """AuthenticationError 被正确识别"""
        e = AuthenticationError("Unauthorized", status_code=401, provider="openai-codex")
        assert LiteLLMClient._is_auth_error(e) is True

    def test_litellm_401_recognized_by_status_code(self) -> None:
        """带 status_code=401 属性的异常被正确识别"""

        class FakeLiteLLMError(Exception):
            status_code = 401

        e = FakeLiteLLMError("Unauthorized")
        assert LiteLLMClient._is_auth_error(e) is True

    def test_litellm_403_recognized_by_status_code(self) -> None:
        """带 status_code=403 属性的异常被正确识别"""

        class FakeLiteLLMError(Exception):
            status_code = 403

        e = FakeLiteLLMError("Forbidden")
        assert LiteLLMClient._is_auth_error(e) is True

    def test_litellm_auth_error_by_class_name(self) -> None:
        """类名为 AuthenticationError 的异常被识别"""
        # 模拟 litellm.AuthenticationError（不直接导入 litellm）
        FakeAuth = type("AuthenticationError", (Exception,), {})
        e = FakeAuth("auth failed")
        assert LiteLLMClient._is_auth_error(e) is True

    def test_litellm_permission_denied_by_class_name(self) -> None:
        """类名为 PermissionDeniedError 的异常被识别"""
        FakePerm = type("PermissionDeniedError", (Exception,), {})
        e = FakePerm("permission denied")
        assert LiteLLMClient._is_auth_error(e) is True

    def test_non_auth_error_not_recognized(self) -> None:
        """500 错误不被误判为认证错误"""

        class FakeServerError(Exception):
            status_code = 500

        e = FakeServerError("Internal Server Error")
        assert LiteLLMClient._is_auth_error(e) is False

    def test_connection_error_not_recognized(self) -> None:
        """ConnectionError 不被误判"""
        e = ConnectionError("Connection refused")
        assert LiteLLMClient._is_auth_error(e) is False

    def test_timeout_not_recognized(self) -> None:
        """TimeoutError 不被误判"""
        e = TimeoutError("timeout")
        assert LiteLLMClient._is_auth_error(e) is False

    def test_plain_exception_not_recognized(self) -> None:
        """普通 Exception 不被误判"""
        e = Exception("something went wrong")
        assert LiteLLMClient._is_auth_error(e) is False

    def test_401_in_message_with_auth_keyword(self) -> None:
        """消息中包含 401 + auth 关键词被识别"""
        e = Exception("HTTP 401 unauthorized access denied")
        assert LiteLLMClient._is_auth_error(e) is True

    def test_403_in_message_with_forbidden_keyword(self) -> None:
        """消息中包含 403 + forbidden 关键词被识别"""
        e = Exception("HTTP 403 forbidden - permission denied")
        assert LiteLLMClient._is_auth_error(e) is True


class TestRefreshOnAuthError:
    """[T012] refresh-on-401 重试逻辑测试"""

    @patch("octoagent.provider.client.acompletion")
    async def test_401_triggers_callback_and_retry_success(
        self, mock_acompletion
    ) -> None:
        """401 触发回调 + 重试成功"""
        # 第一次调用返回 401 错误，第二次成功
        FakeAuth = type("AuthenticationError", (Exception,), {"status_code": 401})
        mock_acompletion.side_effect = [
            FakeAuth("401 Unauthorized"),
            _make_mock_litellm_response("Retry success!"),
        ]

        # 刷新回调返回新凭证
        refreshed = SimpleNamespace(
            credential_value="new-token",
            api_base_url=None,
            extra_headers=None,
        )
        callback = AsyncMock(return_value=refreshed)

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            auth_refresh_callback=callback,
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "test"}],
        )

        assert result.content == "Retry success!"
        callback.assert_called_once()
        assert mock_acompletion.call_count == 2

    @patch("octoagent.provider.client.acompletion")
    async def test_callback_returns_none_raises_original_error(
        self, mock_acompletion
    ) -> None:
        """回调返回 None 时抛出用户友好错误"""
        FakeAuth = type("AuthenticationError", (Exception,), {"status_code": 401})
        mock_acompletion.side_effect = FakeAuth("401 Unauthorized")

        callback = AsyncMock(return_value=None)

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            auth_refresh_callback=callback,
        )

        with pytest.raises(ProviderError, match="重新授权"):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

    @patch("octoagent.provider.client.acompletion")
    async def test_no_callback_raises_directly(self, mock_acompletion) -> None:
        """无 callback 时直接抛出"""
        FakeAuth = type("AuthenticationError", (Exception,), {"status_code": 401})
        mock_acompletion.side_effect = FakeAuth("401 Unauthorized")

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            # 不传 auth_refresh_callback
        )

        with pytest.raises(ProviderError, match="LLM 调用失败"):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

    @patch("octoagent.provider.client.acompletion")
    async def test_retry_still_401_does_not_loop(self, mock_acompletion) -> None:
        """重试后仍 401 不再循环（仅重试一次）"""
        FakeAuth = type("AuthenticationError", (Exception,), {"status_code": 401})
        # 两次都返回 401
        mock_acompletion.side_effect = [
            FakeAuth("401 first"),
            FakeAuth("401 second"),
        ]

        refreshed = SimpleNamespace(
            credential_value="refreshed-token",
            api_base_url=None,
            extra_headers=None,
        )
        callback = AsyncMock(return_value=refreshed)

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            auth_refresh_callback=callback,
        )

        with pytest.raises(ProviderError, match="LLM 调用失败"):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

        # callback 只被调用一次（不循环）
        callback.assert_called_once()
        # acompletion 被调用两次（初始 + 重试）
        assert mock_acompletion.call_count == 2

    @patch("octoagent.provider.client.acompletion")
    async def test_non_auth_error_not_retried(self, mock_acompletion) -> None:
        """非认证错误不触发重试"""
        mock_acompletion.side_effect = RuntimeError("Some other error")

        callback = AsyncMock()

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            auth_refresh_callback=callback,
        )

        with pytest.raises(ProviderError):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

        # 非认证错误不触发回调
        callback.assert_not_called()


class TestNonOAuthProviderRegression:
    """[T031] 非 OAuth Provider 回归测试 -- 无 auth_refresh_callback"""

    @patch("octoagent.provider.client.acompletion")
    async def test_api_key_provider_not_affected(self, mock_acompletion) -> None:
        """API Key Provider（无 callback）的 LLM 调用路径不受 refresh 逻辑影响"""
        mock_acompletion.return_value = _make_mock_litellm_response("API Key works!")

        # 创建不带 auth_refresh_callback 的 client（模拟 API Key Provider）
        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test-api-key",
            # 不传 auth_refresh_callback -- 非 OAuth 路径
        )

        result = await client.complete(
            messages=[{"role": "user", "content": "test"}],
        )

        assert result.content == "API Key works!"
        # 确认调用正常，不涉及 refresh 逻辑

    @patch("octoagent.provider.client.acompletion")
    async def test_api_key_provider_error_raises_directly(
        self, mock_acompletion
    ) -> None:
        """API Key Provider 错误直接抛出，不触发 refresh 逻辑"""
        mock_acompletion.side_effect = RuntimeError("Rate limit exceeded")

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test-api-key",
        )

        with pytest.raises(ProviderError, match="LLM 调用失败"):
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )


class TestRefreshFailureUserPrompt:
    """[T013] refresh_token 失效时的用户提示测试"""

    @patch("octoagent.provider.client.acompletion")
    async def test_refresh_failure_suggests_reauth(self, mock_acompletion) -> None:
        """刷新失败后错误消息包含"重新授权"建议文案"""
        FakeAuth = type("AuthenticationError", (Exception,), {"status_code": 401})
        mock_acompletion.side_effect = FakeAuth("401 Unauthorized")

        # 回调返回 None 表示刷新失败
        callback = AsyncMock(return_value=None)

        client = LiteLLMClient(
            proxy_base_url="http://localhost:4000",
            proxy_api_key="sk-test",
            auth_refresh_callback=callback,
        )

        with pytest.raises(ProviderError) as exc_info:
            await client.complete(
                messages=[{"role": "user", "content": "test"}],
            )

        error_msg = str(exc_info.value)
        assert "重新授权" in error_msg
        assert "octo auth setup" in error_msg
