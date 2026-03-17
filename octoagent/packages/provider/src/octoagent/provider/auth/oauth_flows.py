"""OAuth 流程编排 -- 对齐 contracts/auth-oauth-pkce-api.md SS5, FR-005, FR-008, FR-009

实现 Auth Code + PKCE 完整流程（JWT 方案，对齐 OpenClaw/pi-ai）:
1. 生成 PKCE verifier/challenge + 独立 state
2. 构建授权 URL
3. 根据环境检测结果选择自动浏览器或手动粘贴模式
4. 启动回调服务器等待回调或接受手动粘贴的 redirect URL
5. 使用授权码 + code_verifier 向 token 端点请求 access_token（JWT）
6. 从 JWT access_token 中提取 chatgpt_account_id
7. 构建 OAuthCredential 并返回（access_token 为 JWT，直连 chatgpt backend API）

注：不做 Token Exchange（sk-... API Key），个人账户不支持。
    OpenClaw/pi-ai 采用相同方案，已验证可行。
"""

from __future__ import annotations

import webbrowser
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

import structlog
from octoagent.core.models.enums import EventType
from pydantic import BaseModel, Field, SecretStr

from ..exceptions import OAuthFlowError
from .callback_server import CallbackResult, wait_for_callback, wait_for_gateway_callback
from .credentials import OAuthCredential
from .environment import EnvironmentContext
from .events import EventStoreProtocol, emit_oauth_event
from .oauth_provider import OAuthProviderConfig, OAuthProviderRegistry
from .pkce import generate_pkce, generate_state

log = structlog.get_logger()

# OpenAI JWT 中 account_id 的 claim 路径
_JWT_CLAIM_PATH = "https://api.openai.com/auth"


class OAuthTokenResponse(BaseModel):
    """OAuth Token 端点响应"""

    access_token: SecretStr = Field(description="访问令牌")
    refresh_token: SecretStr = Field(
        default=SecretStr(""),
        description="刷新令牌",
    )
    id_token: str = Field(
        default="",
        description="OIDC id_token（用于 Token Exchange 流程）",
    )
    token_type: str = Field(default="Bearer", description="Token 类型")
    expires_in: int = Field(default=3600, description="过期时间（秒）")
    scope: str = Field(default="", description="授予的 scopes")
    account_id: str | None = Field(
        default=None,
        description="账户 ID（从响应 JSON 提取，若无则为 None）",
    )


def build_authorize_url(
    config: OAuthProviderConfig,
    client_id: str,
    code_challenge: str,
    state: str,
) -> str:
    """构建 OAuth 授权 URL

    URL 参数:
    - client_id
    - redirect_uri
    - response_type=code
    - scope (空格分隔)
    - code_challenge
    - code_challenge_method=S256
    - state
    - extra_auth_params (来自 config)

    Args:
        config: Provider 配置
        client_id: 解析后的 Client ID
        code_challenge: PKCE code_challenge
        state: CSRF state 参数

    Returns:
        完整的授权 URL 字符串
    """
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.scopes),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    # 合并额外参数
    params.update(config.extra_auth_params)

    return f"{config.authorization_endpoint}?{urlencode(params)}"


def extract_account_id_from_jwt(access_token: str) -> str | None:
    """从 JWT access_token 中提取 chatgpt_account_id

    OpenAI 的 JWT access_token payload 结构:
    {
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct_..."
        }
    }

    与 OpenClaw/pi-ai 的 extractAccountId() 逻辑一致。

    Args:
        access_token: JWT 格式的 access_token

    Returns:
        chatgpt_account_id 字符串，解析失败返回 None
    """
    import base64
    import json

    parts = access_token.split(".")
    if len(parts) != 3:
        log.debug("jwt_not_three_parts", parts=len(parts))
        return None

    try:
        # JWT payload 是 base64url 编码，需要补齐 padding
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError) as exc:
        log.debug("jwt_decode_failed", error=str(exc))
        return None

    auth_claim = payload.get(_JWT_CLAIM_PATH)
    if not isinstance(auth_claim, dict):
        log.debug("jwt_missing_auth_claim")
        return None

    account_id = auth_claim.get("chatgpt_account_id")
    if isinstance(account_id, str) and account_id:
        return account_id

    log.debug("jwt_missing_account_id")
    return None


def _curl_post(
    url: str,
    form_data: dict[str, str],
    *,
    error_prefix: str,
    max_retries: int = 3,
) -> dict:
    """通过 subprocess 调用 curl 发送 POST 请求

    完全绕过 Python ssl 模块。含重试逻辑以应对 asyncio TCP 服务器
    关闭后的瞬态 SSL 连接失败（curl exit code 35）。

    Args:
        url: 请求 URL
        form_data: form-urlencoded 表单数据
        error_prefix: 错误消息前缀
        max_retries: 最大重试次数（默认 3）

    Returns:
        解析后的 JSON 响应字典

    Raises:
        OAuthFlowError: 请求失败
    """
    import json
    import subprocess
    import time
    import urllib.parse

    payload = urllib.parse.urlencode(form_data)
    cmd = [
        "curl", "-s", "-S",
        "--max-time", "30",
        "-X", "POST",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-d", payload,
        "-w", "\n%{http_code}",
        url,
    ]

    last_error = ""
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
            log.debug("curl_retry", attempt=attempt + 1, url=url)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=35,
            )
        except FileNotFoundError as exc:
            raise OAuthFlowError(
                f"{error_prefix}失败: curl 未安装",
                provider="",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise OAuthFlowError(
                f"{error_prefix}超时",
                provider="",
            ) from exc

        if result.returncode == 0:
            break

        last_error = result.stderr.strip()[:300]
        # curl exit 35 = SSL 连接错误，可重试
        if result.returncode == 35 and attempt < max_retries - 1:
            continue

        raise OAuthFlowError(
            f"{error_prefix}失败: curl 错误 - {last_error}",
            provider="",
        )

    # 解析响应：最后一行是 HTTP 状态码，前面是 body
    output = result.stdout.strip()
    lines = output.rsplit("\n", 1)
    if len(lines) == 2:
        body_str, status_str = lines
    else:
        body_str = output
        status_str = "0"

    try:
        status_code = int(status_str)
    except ValueError:
        body_str = output
        status_code = 200

    if status_code >= 400:
        raise OAuthFlowError(
            f"{error_prefix}失败: HTTP {status_code} - {body_str[:500]}",
            provider="",
        )

    try:
        return json.loads(body_str)
    except json.JSONDecodeError as exc:
        raise OAuthFlowError(
            f"{error_prefix}失败: 无法解析响应 - {body_str[:200]}",
            provider="",
        ) from exc


async def exchange_code_for_token(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> OAuthTokenResponse:
    """授权码 + PKCE verifier 交换 Token

    发送 POST 请求到 token 端点:
    - grant_type: authorization_code
    - code: 授权码
    - code_verifier: PKCE verifier
    - client_id: Client ID
    - redirect_uri: 回调 URI

    通过 subprocess 调用 curl 发送请求，完全绕过 Python ssl 模块
    在 asyncio 回调服务器关闭后的 TLS 状态异常问题（Python 3.14）。

    Content-Type 使用 application/x-www-form-urlencoded（与官方 Codex CLI 一致）。

    Args:
        token_endpoint: Token 端点 URL
        code: 授权码
        code_verifier: PKCE code_verifier
        client_id: OAuth Client ID
        redirect_uri: 回调 URI

    Returns:
        OAuthTokenResponse 实例

    Raises:
        OAuthFlowError: Token 交换失败
    """
    data = _curl_post(token_endpoint, {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    }, error_prefix="Token 交换")

    try:
        return OAuthTokenResponse(
            access_token=SecretStr(data["access_token"]),
            refresh_token=SecretStr(data.get("refresh_token", "")),
            id_token=data.get("id_token", ""),
            token_type=data.get("token_type", "Bearer"),
            expires_in=data.get("expires_in", 3600),
            scope=data.get("scope", ""),
            account_id=data.get("account_id"),
        )
    except KeyError as exc:
        raise OAuthFlowError(
            f"Token 响应缺少必需字段: {exc}",
            provider="",
        ) from exc


async def refresh_access_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
) -> OAuthTokenResponse:
    """使用 refresh_token 获取新的 access_token

    通过 subprocess 调用 curl 发送 form-urlencoded POST（与 exchange_code_for_token 一致）。
    刷新后从新 JWT access_token 中提取 account_id。

    Args:
        token_endpoint: Token 端点 URL
        refresh_token: 刷新令牌值
        client_id: OAuth Client ID

    Returns:
        OAuthTokenResponse 实例

    Raises:
        OAuthFlowError: 刷新失败（如 invalid_grant）
    """
    data = _curl_post(token_endpoint, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }, error_prefix="Token 刷新")

    try:
        access_token_str = data["access_token"]
    except KeyError as exc:
        raise OAuthFlowError(
            f"Token 响应缺少必需字段: {exc}",
            provider="",
        ) from exc

    # 从新 JWT 中提取 account_id
    jwt_account_id = extract_account_id_from_jwt(access_token_str)

    return OAuthTokenResponse(
        access_token=SecretStr(access_token_str),
        refresh_token=SecretStr(
            data.get("refresh_token", refresh_token)
        ),
        token_type=data.get("token_type", "Bearer"),
        expires_in=data.get("expires_in", 3600),
        scope=data.get("scope", ""),
        account_id=jwt_account_id or data.get("account_id"),
    )


async def manual_paste_flow(
    auth_url: str,
    expected_state: str,
) -> CallbackResult:
    """手动粘贴模式 -- 用户粘贴 redirect URL

    流程:
    1. 输出 auth_url 到终端
    2. 等待用户粘贴 redirect URL
    3. 解析 URL 提取 code 和 state
    4. 验证 state 一致性

    Args:
        auth_url: 完整的授权 URL
        expected_state: 预期的 state 参数值

    Returns:
        CallbackResult 包含 code 和 state

    Raises:
        OAuthFlowError: URL 解析失败或 state 不匹配
    """
    import sys

    # 输出授权 URL
    print("\n请在浏览器中打开以下 URL 完成授权:")
    print(f"\n  {auth_url}\n")
    print("授权完成后，请将浏览器地址栏中的 redirect URL 粘贴到此处:")
    sys.stdout.flush()

    # 读取用户输入
    try:
        redirect_url = input("> ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise OAuthFlowError(
            "用户取消了 OAuth 授权",
            provider="",
        ) from exc

    if not redirect_url:
        raise OAuthFlowError(
            "未输入 redirect URL",
            provider="",
        )

    # 解析 URL
    parsed = urlparse(redirect_url)
    query_params = parse_qs(parsed.query)

    code_values = query_params.get("code", [])
    state_values = query_params.get("state", [])

    if not code_values:
        raise OAuthFlowError(
            "redirect URL 中缺少 code 参数，请确认 URL 格式正确",
            provider="",
        )
    if not state_values:
        raise OAuthFlowError(
            "redirect URL 中缺少 state 参数，请确认 URL 格式正确",
            provider="",
        )

    code = code_values[0]
    state = state_values[0]

    # 验证 state
    if state != expected_state:
        raise OAuthFlowError(
            "state 参数不匹配，可能存在 CSRF 风险，请重新授权",
            provider="",
        )

    return CallbackResult(code=code, state=state)


def _normalize_oauth_failure_stage(exc: OAuthFlowError, stage: str) -> str:
    """归一化 OAuth 失败阶段，确保事件可检索。"""
    reason = str(exc)

    if "state 参数不匹配" in reason:
        return "state_validation"
    if "redirect URL" in reason or "用户取消" in reason or "未输入 redirect URL" in reason:
        return "manual_callback"
    if "Token 交换失败" in reason or "Token 响应缺少必需字段" in reason:
        return "token_exchange"
    return stage


async def run_auth_code_pkce_flow(
    config: OAuthProviderConfig,
    registry: OAuthProviderRegistry,
    env: EnvironmentContext,
    on_auth_url: Callable[[str], Awaitable[None]] | None = None,
    on_status: Callable[[str], None] | None = None,
    event_store: EventStoreProtocol | None = None,
    *,
    use_gateway_callback: bool = False,
) -> OAuthCredential:
    """执行 Auth Code + PKCE OAuth 流程

    完整步骤:
    1. 解析 client_id（从 config 或环境变量）
    2. 生成 PKCE verifier/challenge + 独立 state
    3. 构建授权 URL
    4. 根据环境上下文选择交互模式:
       - 本地: 自动打开浏览器 + 启动回调服务器
       - 远程/VPS: 输出 URL + 等待手动粘贴 redirect URL
       - 端口冲突: 自动降级到手动模式
    5. 验证 state 参数一致性
    6. 使用授权码 + code_verifier 向 token 端点请求 access_token
    7. 构建 OAuthCredential 并返回

    Args:
        config: Provider OAuth 配置
        registry: Provider 注册表（用于解析 client_id）
        env: 环境上下文
        on_auth_url: 自定义授权 URL 处理回调（默认使用 webbrowser.open）
        on_status: 状态更新回调（用于 CLI 输出）
        event_store: Event Store 实例（用于事件发射）

    Returns:
        OAuthCredential 实例

    Raises:
        OAuthFlowError: 授权失败、超时或 state 验证失败
    """
    environment_mode = "manual" if env.use_manual_mode else "auto"
    await emit_oauth_event(
        event_store=event_store,
        event_type=EventType.OAUTH_STARTED,
        provider_id=config.provider_id,
        payload={
            "flow_type": config.flow_type,
            "environment_mode": environment_mode,
        },
    )

    if on_status:
        on_status("正在发起 OAuth PKCE 授权...")

    flow_stage = "resolve_client_id"

    try:
        # 步骤 1: 解析 client_id
        client_id = registry.resolve_client_id(config)

        # 步骤 2: 生成 PKCE + state
        flow_stage = "pkce_generation"
        pkce_pair = generate_pkce()
        state = generate_state()

        # 步骤 3: 构建授权 URL
        flow_stage = "build_authorize_url"
        auth_url = build_authorize_url(config, client_id, pkce_pair.code_challenge, state)

        # 步骤 4: 根据环境选择交互模式
        if env.use_manual_mode:
            # 远程/VPS/强制手动模式：输出 URL + 手动粘贴
            if on_status:
                on_status("使用手动模式（远程环境或 --manual-oauth）")
            flow_stage = "manual_callback"
            callback_result = await manual_paste_flow(auth_url, state)
        elif use_gateway_callback:
            # Gateway 路由模式：复用 Gateway 端口，无需独立回调服务器
            flow_stage = "gateway_callback"
            if on_auth_url:
                await on_auth_url(auth_url)
            else:
                webbrowser.open(auth_url)

            if on_status:
                on_status("已打开浏览器，等待授权回调（Gateway 路由模式）...")

            callback_result = await wait_for_gateway_callback(
                expected_state=state,
                timeout=float(config.timeout_s),
            )
        else:
            # 独立服务器模式（CLI 降级用）
            flow_stage = "local_callback"
            try:
                if on_auth_url:
                    await on_auth_url(auth_url)
                else:
                    webbrowser.open(auth_url)

                if on_status:
                    on_status("已打开浏览器，等待授权回调...")

                callback_result = await wait_for_callback(
                    port=config.redirect_port,
                    expected_state=state,
                    timeout=float(config.timeout_s),
                )
            except OSError:
                # 端口冲突 -> 降级到手动模式
                log.warning(
                    "oauth_port_conflict_fallback",
                    port=config.redirect_port,
                )
                if on_status:
                    on_status(
                        f"端口 {config.redirect_port} 被占用，降级到手动模式"
                    )
                flow_stage = "manual_callback"
                callback_result = await manual_paste_flow(auth_url, state)

        # 步骤 5: state 验证（回调服务器和手动模式内部已验证，此处双重保障）
        flow_stage = "state_validation"
        if callback_result.state != state:
            raise OAuthFlowError(
                "state 参数不匹配，可能存在 CSRF 风险",
                provider=config.provider_id,
            )

        # 步骤 6: Token 交换
        flow_stage = "token_exchange"
        if on_status:
            on_status("正在交换授权码...")

        token_resp = await exchange_code_for_token(
            token_endpoint=config.token_endpoint,
            code=callback_result.code,
            code_verifier=pkce_pair.code_verifier,
            client_id=client_id,
            redirect_uri=config.redirect_uri,
        )

        # 步骤 6.5: 从 JWT access_token 提取 chatgpt_account_id
        # JWT 方案（对齐 OpenClaw/pi-ai）：不做 Token Exchange，
        # 直接用 JWT 作为 Bearer token 调用 chatgpt.com/backend-api
        access_value = token_resp.access_token.get_secret_value()
        jwt_account_id = extract_account_id_from_jwt(access_value)
        # 优先使用 JWT 中提取的 account_id，其次使用 token 响应中的
        resolved_account_id = jwt_account_id or token_resp.account_id

        if jwt_account_id:
            log.info(
                "oauth_jwt_account_id_extracted",
                provider=config.provider_id,
                account_id=jwt_account_id,
            )
        else:
            log.warning(
                "oauth_jwt_account_id_missing",
                provider=config.provider_id,
                hint="JWT 中未找到 chatgpt_account_id，部分 API 功能可能受限",
            )

        # 步骤 7: 构建 OAuthCredential
        flow_stage = "build_credential"
        credential = OAuthCredential(
            provider=config.provider_id,
            access_token=token_resp.access_token,
            refresh_token=token_resp.refresh_token,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=token_resp.expires_in),
            account_id=resolved_account_id,
        )

        # 发射 OAUTH_SUCCEEDED 事件
        refresh_value = token_resp.refresh_token.get_secret_value()
        await emit_oauth_event(
            event_store=event_store,
            event_type=EventType.OAUTH_SUCCEEDED,
            provider_id=config.provider_id,
            payload={
                "token_type": token_resp.token_type,
                "expires_in": token_resp.expires_in,
                "has_refresh_token": bool(refresh_value),
                "has_account_id": token_resp.account_id is not None,
            },
        )

        if on_status:
            on_status("OAuth 授权成功!")

        return credential

    except OAuthFlowError as exc:
        await emit_oauth_event(
            event_store=event_store,
            event_type=EventType.OAUTH_FAILED,
            provider_id=config.provider_id,
            payload={
                "failure_reason": str(exc),
                "failure_stage": _normalize_oauth_failure_stage(exc, flow_stage),
            },
        )
        raise
    except Exception as exc:
        # 未预期的异常
        await emit_oauth_event(
            event_store=event_store,
            event_type=EventType.OAUTH_FAILED,
            provider_id=config.provider_id,
            payload={
                "failure_reason": str(exc),
                "failure_stage": flow_stage,
            },
        )
        raise OAuthFlowError(
            f"OAuth 流程失败: {exc}",
            provider=config.provider_id,
        ) from exc
