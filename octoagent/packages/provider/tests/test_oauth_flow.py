"""Device Flow 单元测试（mock httpx）-- T033

覆盖: 正常授权 / 超时 / 端点不可达 / 轮询间隔
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from octoagent.provider.auth.oauth import (
    DeviceFlowConfig,
    start_device_flow,
    poll_for_token,
)
from octoagent.provider.exceptions import OAuthFlowError


def _make_config() -> DeviceFlowConfig:
    return DeviceFlowConfig(
        client_id="test-client-id",
        poll_interval_s=1,
        timeout_s=30,
    )


class TestStartDeviceFlow:
    """start_device_flow() 行为"""

    async def test_success(self) -> None:
        """正常授权请求"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "device_code": "dc-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://example.com/verify",
            "expires_in": 300,
            "interval": 5,
        }

        with patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = _make_config()
            result = await start_device_flow(config)
            assert result.device_code == "dc-123"
            assert result.user_code == "ABCD-1234"
            assert result.verification_uri == "https://example.com/verify"

    async def test_endpoint_unreachable(self) -> None:
        """端点不可达 (EC-8)"""
        with patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = _make_config()
            with pytest.raises(OAuthFlowError, match="不可达"):
                await start_device_flow(config)

    async def test_http_error(self) -> None:
        """HTTP 错误状态"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = _make_config()
            with pytest.raises(OAuthFlowError, match="返回错误"):
                await start_device_flow(config)


class TestPollForToken:
    """poll_for_token() 行为"""

    async def test_success_on_first_poll(self) -> None:
        """首次轮询即成功"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "at-xyz",
            "refresh_token": "rt-xyz",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls,
            patch("octoagent.provider.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = _make_config()
            result = await poll_for_token(config, "dc-123", interval=1, timeout=10)
            assert result.access_token.get_secret_value() == "at-xyz"
            assert result.provider == "openai-codex"

    async def test_pending_then_success(self) -> None:
        """先 pending 再成功"""
        pending_response = MagicMock()
        pending_response.status_code = 400
        pending_response.json.return_value = {"error": "authorization_pending"}

        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {
            "access_token": "at-abc",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls,
            patch("octoagent.provider.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_client = AsyncMock()
            mock_client.post.side_effect = [pending_response, success_response]
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = _make_config()
            result = await poll_for_token(config, "dc-123", interval=1, timeout=10)
            assert result.access_token.get_secret_value() == "at-abc"

    async def test_access_denied(self) -> None:
        """用户拒绝授权"""
        denied_response = MagicMock()
        denied_response.status_code = 400
        denied_response.json.return_value = {"error": "access_denied"}

        with (
            patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls,
            patch("octoagent.provider.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_client = AsyncMock()
            mock_client.post.return_value = denied_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = _make_config()
            with pytest.raises(OAuthFlowError, match="拒绝"):
                await poll_for_token(config, "dc-123", interval=1, timeout=10)

    async def test_timeout(self) -> None:
        """轮询超时"""
        pending_response = MagicMock()
        pending_response.status_code = 400
        pending_response.json.return_value = {"error": "authorization_pending"}

        with (
            patch("octoagent.provider.auth.oauth.httpx.AsyncClient") as mock_client_cls,
            patch("octoagent.provider.auth.oauth.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_client = AsyncMock()
            # 始终返回 pending
            mock_client.post.return_value = pending_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            config = DeviceFlowConfig(
                client_id="test",
                poll_interval_s=1,
                timeout_s=30,
            )
            # 使用 timeout 参数覆盖为较小值以加速测试
            with pytest.raises(OAuthFlowError, match="超时"):
                await poll_for_token(config, "dc-123", interval=1, timeout=3)
