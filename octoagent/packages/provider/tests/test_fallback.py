"""FallbackManager 单元测试

对齐 tasks.md T022: 验证 primary 成功不触发 fallback、primary 失败触发 fallback
（is_fallback=True + fallback_reason）、双方失败抛 ProviderError、lazy probe 恢复。
"""

from unittest.mock import AsyncMock

import pytest
from octoagent.provider.exceptions import ProviderError, ProxyUnreachableError
from octoagent.provider.fallback import FallbackManager
from octoagent.provider.models import ModelCallResult


def _make_result(content: str = "ok", is_fallback: bool = False) -> ModelCallResult:
    """构造测试用 ModelCallResult"""
    return ModelCallResult(
        content=content,
        model_alias="main",
        model_name="gpt-4o",
        provider="openai",
        duration_ms=100,
        is_fallback=is_fallback,
    )


@pytest.fixture
def mock_primary():
    """Mock LiteLLMClient"""
    client = AsyncMock()
    client.complete = AsyncMock(return_value=_make_result("primary response"))
    return client


@pytest.fixture
def mock_fallback():
    """Mock EchoMessageAdapter"""
    adapter = AsyncMock()
    adapter.complete = AsyncMock(
        return_value=_make_result("echo response", is_fallback=False)
    )
    return adapter


class TestFallbackManagerPrimarySuccess:
    """Primary 成功场景"""

    async def test_primary_success_no_fallback(self, mock_primary, mock_fallback):
        """Primary 成功时不调用 fallback"""
        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        result = await fm.call_with_fallback(messages, model_alias="main")

        assert result.content == "primary response"
        assert result.is_fallback is False
        mock_primary.complete.assert_called_once()
        mock_fallback.complete.assert_not_called()


class TestFallbackManagerPrimaryFailure:
    """Primary 失败场景"""

    async def test_proxy_unreachable_triggers_fallback(
        self, mock_primary, mock_fallback
    ):
        """ProxyUnreachableError 触发 fallback"""
        mock_primary.complete.side_effect = ProxyUnreachableError(
            "http://localhost:4000", ConnectionError("refused")
        )
        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        result = await fm.call_with_fallback(messages)

        assert result.is_fallback is True
        assert result.fallback_reason != ""
        mock_fallback.complete.assert_called_once()

    async def test_provider_error_triggers_fallback(
        self, mock_primary, mock_fallback
    ):
        """ProviderError 触发 fallback"""
        mock_primary.complete.side_effect = ProviderError("model unavailable")
        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        result = await fm.call_with_fallback(messages)

        assert result.is_fallback is True

    async def test_generic_exception_triggers_fallback(
        self, mock_primary, mock_fallback
    ):
        """通用异常也触发 fallback"""
        mock_primary.complete.side_effect = RuntimeError("unexpected")
        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        result = await fm.call_with_fallback(messages)

        assert result.is_fallback is True


class TestFallbackManagerBothFail:
    """Primary 和 fallback 均失败"""

    async def test_both_fail_raises_provider_error(self, mock_primary, mock_fallback):
        """双方失败抛出 ProviderError"""
        mock_primary.complete.side_effect = ProxyUnreachableError(
            "http://localhost:4000", ConnectionError("refused")
        )
        mock_fallback.complete.side_effect = RuntimeError("echo also failed")

        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        with pytest.raises(ProviderError):
            await fm.call_with_fallback(messages)

    async def test_no_fallback_configured(self, mock_primary):
        """未配置 fallback 时直接抛 ProviderError"""
        mock_primary.complete.side_effect = ProxyUnreachableError(
            "http://localhost:4000", ConnectionError("refused")
        )
        fm = FallbackManager(primary=mock_primary, fallback=None)
        messages = [{"role": "user", "content": "test"}]

        with pytest.raises(ProviderError):
            await fm.call_with_fallback(messages)


class TestFallbackManagerLazyProbe:
    """Lazy probe 恢复测试"""

    async def test_recovery_after_failure(self, mock_primary, mock_fallback):
        """Primary 恢复后自动恢复使用（lazy probe）"""
        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        # 第一次: primary 失败，使用 fallback
        mock_primary.complete.side_effect = ProxyUnreachableError(
            "http://localhost:4000", ConnectionError("refused")
        )
        result1 = await fm.call_with_fallback(messages)
        assert result1.is_fallback is True

        # 第二次: primary 恢复
        mock_primary.complete.side_effect = None
        mock_primary.complete.return_value = _make_result("recovered")
        result2 = await fm.call_with_fallback(messages)
        assert result2.is_fallback is False
        assert result2.content == "recovered"

    async def test_model_alias_passed_through(self, mock_primary, mock_fallback):
        """model_alias 正确传递给 primary 和 fallback"""
        fm = FallbackManager(primary=mock_primary, fallback=mock_fallback)
        messages = [{"role": "user", "content": "test"}]

        await fm.call_with_fallback(messages, model_alias="cheap")

        call_kwargs = mock_primary.complete.call_args
        assert call_kwargs.kwargs.get("model_alias") == "cheap" or \
               (len(call_kwargs.args) > 1 and call_kwargs.args[1] == "cheap")
