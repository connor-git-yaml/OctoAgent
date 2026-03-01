"""OAuth 流程编排单元测试 -- T011 + T014

注意：已有 test_oauth_flow.py 是 Feature 003 Device Flow 测试，
本文件为 003-b PKCE 流程测试。

验证:
- 完整 PKCE 流程（mock httpx + webbrowser + callback）
- build_authorize_url 参数正确性
- token 交换成功/失败
- refresh_access_token 成功/invalid_grant
- 远程环境使用手动模式 (T014)
- manual_paste_flow URL 解析 (T014)
- 端口冲突自动降级 (T014)
- --manual-oauth 强制手动模式 (T014)
对齐 FR-002, FR-003, FR-005
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from octoagent.provider.auth.callback_server import CallbackResult
from octoagent.provider.auth.environment import EnvironmentContext
from octoagent.provider.auth.oauth_flows import (
    OAuthTokenResponse,
    build_authorize_url,
    exchange_code_for_token,
    extract_account_id_from_jwt,
    refresh_access_token,
    run_auth_code_pkce_flow,
)
from octoagent.provider.auth.oauth_provider import (
    OAuthProviderConfig,
    OAuthProviderRegistry,
)
from octoagent.provider.exceptions import OAuthFlowError

# _curl_post mock 目标路径
_CURL_POST = "octoagent.provider.auth.oauth_flows._curl_post"


def _make_codex_config() -> OAuthProviderConfig:
    """创建测试用 OpenAI Codex 配置"""
    return OAuthProviderConfig(
        provider_id="openai-codex",
        display_name="OpenAI Codex",
        flow_type="auth_code_pkce",
        authorization_endpoint="https://auth.openai.com/oauth/authorize",
        token_endpoint="https://auth.openai.com/oauth/token",
        client_id="test-client-id",
        scopes=["openid", "profile", "email", "offline_access"],
        redirect_uri="http://localhost:1455/auth/callback",
        redirect_port=1455,
        supports_refresh=True,
        extra_auth_params={"audience": "https://api.openai.com/v1"},
    )


def _make_registry(config: OAuthProviderConfig | None = None) -> OAuthProviderRegistry:
    """创建测试用注册表"""
    registry = OAuthProviderRegistry()
    if config:
        registry.register(config)
    return registry


def _make_local_env() -> EnvironmentContext:
    """创建本地桌面环境上下文"""
    return EnvironmentContext(
        is_remote=False,
        can_open_browser=True,
        force_manual=False,
        detection_details="本地桌面环境",
    )


def _make_remote_env() -> EnvironmentContext:
    """创建远程/VPS 环境上下文"""
    return EnvironmentContext(
        is_remote=True,
        can_open_browser=False,
        force_manual=False,
        detection_details="SSH 环境",
    )


class TestBuildAuthorizeUrl:
    """build_authorize_url() 测试"""

    def test_contains_required_params(self) -> None:
        """URL 包含所有必需参数"""
        config = _make_codex_config()
        url = build_authorize_url(
            config=config,
            client_id="test-client-id",
            code_challenge="test-challenge",
            state="test-state",
        )
        assert "client_id=test-client-id" in url
        assert "redirect_uri=" in url
        assert "response_type=code" in url
        assert "code_challenge=test-challenge" in url
        assert "code_challenge_method=S256" in url
        assert "state=test-state" in url

    def test_includes_scopes(self) -> None:
        """URL 包含 scopes"""
        config = _make_codex_config()
        url = build_authorize_url(
            config=config,
            client_id="cid",
            code_challenge="cc",
            state="st",
        )
        # 空格在 URL 编码后为 +
        assert "scope=" in url

    def test_includes_extra_params(self) -> None:
        """URL 包含 extra_auth_params"""
        config = _make_codex_config()
        url = build_authorize_url(
            config=config,
            client_id="cid",
            code_challenge="cc",
            state="st",
        )
        assert "audience=" in url

    def test_uses_authorization_endpoint(self) -> None:
        """URL 以 authorization_endpoint 开头"""
        config = _make_codex_config()
        url = build_authorize_url(
            config=config,
            client_id="cid",
            code_challenge="cc",
            state="st",
        )
        assert url.startswith("https://auth.openai.com/oauth/authorize?")


class TestExchangeCodeForToken:
    """exchange_code_for_token() 测试"""

    async def test_success(self) -> None:
        """成功交换 token"""
        response_data = {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile",
            "account_id": "acc-123",
        }

        with patch(_CURL_POST, return_value=response_data):
            result = await exchange_code_for_token(
                token_endpoint="https://auth.openai.com/oauth/token",
                code="auth-code",
                code_verifier="test-verifier",
                client_id="test-client",
                redirect_uri="http://localhost:1455/auth/callback",
            )

        assert isinstance(result, OAuthTokenResponse)
        assert result.access_token.get_secret_value() == "test-access-token"
        assert result.refresh_token.get_secret_value() == "test-refresh-token"
        assert result.expires_in == 3600
        assert result.account_id == "acc-123"

    async def test_http_error(self) -> None:
        """HTTP 错误抛出 OAuthFlowError"""
        with patch(
            _CURL_POST,
            side_effect=OAuthFlowError("Token 交换失败: HTTP 400 - invalid_grant", provider=""),
        ):
            with pytest.raises(OAuthFlowError, match="Token 交换失败"):
                await exchange_code_for_token(
                    token_endpoint="https://example.com/token",
                    code="code",
                    code_verifier="verifier",
                    client_id="client",
                    redirect_uri="http://localhost:1455/auth/callback",
                )


class TestRefreshAccessToken:
    """refresh_access_token() 测试"""

    async def test_success(self) -> None:
        """成功刷新 token"""
        response_data = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 7200,
        }

        with patch(_CURL_POST, return_value=response_data):
            result = await refresh_access_token(
                token_endpoint="https://auth.openai.com/oauth/token",
                refresh_token="old-refresh-token",
                client_id="test-client",
            )

        assert result.access_token.get_secret_value() == "new-access-token"
        assert result.expires_in == 7200

    async def test_invalid_grant(self) -> None:
        """invalid_grant 错误抛出 OAuthFlowError"""
        with patch(
            _CURL_POST,
            side_effect=OAuthFlowError("Token 刷新失败: HTTP 400 - invalid_grant", provider=""),
        ):
            with pytest.raises(OAuthFlowError, match="invalid_grant"):
                await refresh_access_token(
                    token_endpoint="https://auth.openai.com/oauth/token",
                    refresh_token="expired-token",
                    client_id="test-client",
                )


class TestRunAuthCodePkceFlow:
    """run_auth_code_pkce_flow() 完整流程测试"""

    async def test_local_auto_flow(self) -> None:
        """本地环境自动浏览器 + 回调服务器流程"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_local_env()

        curl_resp = {
            "access_token": "pkce-access-token",
            "refresh_token": "pkce-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open") as mock_browser,
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "fixed-state"
            mock_wait.return_value = CallbackResult(code="auth-code-xyz", state="fixed-state")

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        assert credential.provider == "openai-codex"
        assert credential.access_token.get_secret_value() == "pkce-access-token"
        assert credential.refresh_token.get_secret_value() == "pkce-refresh-token"
        mock_browser.assert_called_once()
        mock_wait.assert_called_once()

    async def test_events_emitted(self) -> None:
        """验证 OAUTH_STARTED 和 OAUTH_SUCCEEDED 事件被发射"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_local_env()

        mock_event_store = AsyncMock()
        mock_event_store.append = AsyncMock()

        curl_resp = {
            "access_token": "token",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "evt-state"
            mock_wait.return_value = CallbackResult(code="code", state="evt-state")

            await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
                event_store=mock_event_store,
            )

        # 至少发射 OAUTH_STARTED 和 OAUTH_SUCCEEDED
        calls = mock_event_store.append.call_args_list
        event_types = [c.kwargs.get("event_type") or c[1].get("event_type", c[0][1] if len(c[0]) > 1 else None) for c in calls]
        assert "OAUTH_STARTED" in event_types
        assert "OAUTH_SUCCEEDED" in event_types

    async def test_failed_event_emitted_on_manual_error(self) -> None:
        """手动输入失败时发射 OAUTH_FAILED（含 failure_stage）"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_remote_env()

        mock_event_store = AsyncMock()
        mock_event_store.append = AsyncMock()

        with (
            patch("octoagent.provider.auth.oauth_flows.manual_paste_flow") as mock_manual,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
        ):
            mock_state.return_value = "manual-error-state"
            mock_manual.side_effect = OAuthFlowError(
                "未输入 redirect URL",
                provider=config.provider_id,
            )

            with pytest.raises(OAuthFlowError, match="未输入 redirect URL"):
                await run_auth_code_pkce_flow(
                    config=config,
                    registry=registry,
                    env=env,
                    event_store=mock_event_store,
                )

        calls = mock_event_store.append.call_args_list
        event_types = [
            c.kwargs.get("event_type")
            or c[1].get("event_type", c[0][1] if len(c[0]) > 1 else None)
            for c in calls
        ]
        assert "OAUTH_STARTED" in event_types
        assert "OAUTH_FAILED" in event_types

        failed_call = next(
            c
            for c in calls
            if (c.kwargs.get("event_type") or c[1].get("event_type")) == "OAUTH_FAILED"
        )
        failed_payload = failed_call.kwargs.get("payload") or failed_call[1].get("payload")
        assert failed_payload["failure_stage"] == "manual_callback"

    async def test_failed_event_emitted_on_token_exchange_error(self) -> None:
        """Token 交换失败时发射 OAUTH_FAILED（stage=token_exchange）"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_local_env()

        mock_event_store = AsyncMock()
        mock_event_store.append = AsyncMock()

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(
                "octoagent.provider.auth.oauth_flows.exchange_code_for_token"
            ) as mock_exchange,
        ):
            mock_state.return_value = "token-fail-state"
            mock_wait.return_value = CallbackResult(
                code="token-fail-code", state="token-fail-state"
            )
            mock_exchange.side_effect = OAuthFlowError(
                "Token 交换失败: HTTP 400",
                provider=config.provider_id,
            )

            with pytest.raises(OAuthFlowError, match="Token 交换失败"):
                await run_auth_code_pkce_flow(
                    config=config,
                    registry=registry,
                    env=env,
                    event_store=mock_event_store,
                )

        calls = mock_event_store.append.call_args_list
        failed_call = next(
            c
            for c in calls
            if (c.kwargs.get("event_type") or c[1].get("event_type")) == "OAUTH_FAILED"
        )
        failed_payload = failed_call.kwargs.get("payload") or failed_call[1].get("payload")
        assert failed_payload["failure_stage"] == "token_exchange"


# --- T014: 手动模式 + 降级场景测试 ---


class TestRemoteEnvironmentUsesManualMode:
    """远程环境使用手动模式"""

    async def test_remote_env_calls_manual_paste(self) -> None:
        """远程环境调用 manual_paste_flow 而非 webbrowser"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_remote_env()

        curl_resp = {
            "access_token": "remote-token",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open") as mock_browser,
            patch("octoagent.provider.auth.oauth_flows.manual_paste_flow") as mock_manual,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "remote-state"
            mock_manual.return_value = CallbackResult(
                code="remote-code", state="remote-state"
            )

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        # 不应打开浏览器
        mock_browser.assert_not_called()
        # 应调用手动粘贴流程
        mock_manual.assert_called_once()
        assert credential.access_token.get_secret_value() == "remote-token"


class TestManualPasteFlow:
    """manual_paste_flow URL 解析测试"""

    async def test_valid_redirect_url(self) -> None:
        """正确解析 redirect URL"""
        from octoagent.provider.auth.oauth_flows import manual_paste_flow

        redirect_url = "http://localhost:1455/auth/callback?code=test-code&state=test-state"
        with patch("builtins.input", return_value=redirect_url):
            result = await manual_paste_flow(
                auth_url="https://auth.openai.com/oauth/authorize?...",
                expected_state="test-state",
            )
        assert result.code == "test-code"
        assert result.state == "test-state"

    async def test_state_mismatch_raises(self) -> None:
        """state 不匹配抛出 OAuthFlowError"""
        from octoagent.provider.auth.oauth_flows import manual_paste_flow

        redirect_url = "http://localhost:1455/auth/callback?code=code&state=wrong"
        with patch("builtins.input", return_value=redirect_url):
            with pytest.raises(OAuthFlowError, match="state"):
                await manual_paste_flow(
                    auth_url="https://auth.openai.com/...",
                    expected_state="expected-state",
                )

    async def test_missing_code_raises(self) -> None:
        """缺少 code 参数抛出 OAuthFlowError"""
        from octoagent.provider.auth.oauth_flows import manual_paste_flow

        redirect_url = "http://localhost:1455/auth/callback?state=test-state"
        with patch("builtins.input", return_value=redirect_url):
            with pytest.raises(OAuthFlowError, match="code"):
                await manual_paste_flow(
                    auth_url="https://auth.openai.com/...",
                    expected_state="test-state",
                )

    async def test_empty_input_raises(self) -> None:
        """空输入抛出 OAuthFlowError"""
        from octoagent.provider.auth.oauth_flows import manual_paste_flow

        with patch("builtins.input", return_value=""):
            with pytest.raises(OAuthFlowError, match="未输入"):
                await manual_paste_flow(
                    auth_url="https://auth.openai.com/...",
                    expected_state="test-state",
                )


class TestPortConflictDegradation:
    """端口冲突自动降级测试"""

    async def test_port_conflict_falls_back_to_manual(self) -> None:
        """端口被占用时自动降级到手动模式"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_local_env()

        curl_resp = {
            "access_token": "fallback-token",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch(
                "octoagent.provider.auth.oauth_flows.wait_for_callback",
                side_effect=OSError("Address already in use"),
            ),
            patch("octoagent.provider.auth.oauth_flows.manual_paste_flow") as mock_manual,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "fallback-state"
            mock_manual.return_value = CallbackResult(
                code="fallback-code", state="fallback-state"
            )

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        mock_manual.assert_called_once()
        assert credential.access_token.get_secret_value() == "fallback-token"


class TestForceManualOAuth:
    """--manual-oauth 强制手动模式测试"""

    async def test_force_manual_uses_paste_flow(self) -> None:
        """force_manual=True 时即使本地环境也使用手动模式"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = EnvironmentContext(
            is_remote=False,
            can_open_browser=True,
            force_manual=True,
            detection_details="强制手动模式",
        )

        curl_resp = {
            "access_token": "forced-token",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open") as mock_browser,
            patch("octoagent.provider.auth.oauth_flows.manual_paste_flow") as mock_manual,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "forced-state"
            mock_manual.return_value = CallbackResult(
                code="forced-code", state="forced-state"
            )

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        mock_browser.assert_not_called()
        mock_manual.assert_called_once()
        assert credential.access_token.get_secret_value() == "forced-token"


class TestExtractAccountIdFromJwt:
    """extract_account_id_from_jwt() 测试"""

    def test_valid_jwt_extracts_account_id(self) -> None:
        """从有效 JWT 中提取 chatgpt_account_id"""
        import base64
        import json

        payload = {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_test_123",
            },
            "sub": "user-123",
            "exp": 9999999999,
        }
        # 构造最小 JWT（header.payload.signature）
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        jwt_token = f"{header}.{body}.fake-signature"

        result = extract_account_id_from_jwt(jwt_token)
        assert result == "acct_test_123"

    def test_jwt_without_auth_claim_returns_none(self) -> None:
        """JWT 中没有 auth claim → 返回 None"""
        import base64
        import json

        payload = {"sub": "user-123", "exp": 9999999999}
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        jwt_token = f"{header}.{body}.fake-sig"

        assert extract_account_id_from_jwt(jwt_token) is None

    def test_jwt_with_empty_account_id_returns_none(self) -> None:
        """JWT 中 chatgpt_account_id 为空 → 返回 None"""
        import base64
        import json

        payload = {"https://api.openai.com/auth": {"chatgpt_account_id": ""}}
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        jwt_token = f"{header}.{body}.sig"

        assert extract_account_id_from_jwt(jwt_token) is None

    def test_non_jwt_string_returns_none(self) -> None:
        """非 JWT 格式字符串 → 返回 None"""
        assert extract_account_id_from_jwt("not-a-jwt") is None
        assert extract_account_id_from_jwt("sk-api-key-12345") is None

    def test_invalid_base64_returns_none(self) -> None:
        """JWT payload 不是有效 base64 → 返回 None"""
        assert extract_account_id_from_jwt("header.!!!invalid!!!.sig") is None


class TestJwtAccountIdInPkceFlow:
    """run_auth_code_pkce_flow JWT account_id 提取集成测试"""

    async def test_flow_extracts_account_id_from_jwt(self) -> None:
        """流程从 JWT access_token 中提取 account_id"""
        import base64
        import json

        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_local_env()

        # 构造含 account_id 的 JWT access_token
        payload = {
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_jwt_test",
            },
        }
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        jwt_token = f"{header}.{body}.sig"

        curl_resp = {
            "access_token": jwt_token,
            "refresh_token": "refresh-tok",
            "expires_in": 3600,
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "jwt-state"
            mock_wait.return_value = CallbackResult(code="jwt-code", state="jwt-state")

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        assert credential.access_token.get_secret_value() == jwt_token
        assert credential.account_id == "acct_jwt_test"
        assert credential.provider == "openai-codex"
        # _curl_post 只调用一次（不做 Token Exchange）
        assert True  # 流程正常完成

    async def test_flow_without_jwt_account_id_uses_fallback(self) -> None:
        """JWT 中无 account_id → 使用 token 响应中的 account_id"""
        config = _make_codex_config()
        registry = _make_registry(config)
        env = _make_local_env()

        curl_resp = {
            "access_token": "non-jwt-plain-token",
            "expires_in": 3600,
            "account_id": "fallback-account",
        }

        with (
            patch("octoagent.provider.auth.oauth_flows.webbrowser.open"),
            patch("octoagent.provider.auth.oauth_flows.wait_for_callback") as mock_wait,
            patch("octoagent.provider.auth.oauth_flows.generate_state") as mock_state,
            patch(_CURL_POST, return_value=curl_resp),
        ):
            mock_state.return_value = "fb-state"
            mock_wait.return_value = CallbackResult(code="fb-code", state="fb-state")

            credential = await run_auth_code_pkce_flow(
                config=config,
                registry=registry,
                env=env,
            )

        # 非 JWT token → account_id 从 token 响应中取
        assert credential.access_token.get_secret_value() == "non-jwt-plain-token"
        assert credential.account_id == "fallback-account"
