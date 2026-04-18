"""LiteLLMClient -- LiteLLM Proxy 调用封装

对齐 contracts/provider-api.md SS2。
通过 litellm.acompletion() 调用 Proxy，内部集成 CostTracker。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import structlog

from .cost import CostTracker
from .exceptions import AuthenticationError, ProviderError, ProxyUnreachableError
from .models import ModelCallResult, ReasoningConfig, TokenUsage

log = structlog.get_logger()

# 隔离 litellm 导入，方便测试 Mock
try:
    from litellm import acompletion, stream_chunk_builder
except ImportError:  # pragma: no cover
    acompletion = None  # type: ignore[assignment]
    stream_chunk_builder = None  # type: ignore[assignment]

# 健康检查超时（硬编码，应快速响应）
HEALTH_CHECK_TIMEOUT_S = 5

# 连接类异常类型集合（触发 ProxyUnreachableError，进而触发 FallbackManager 降级）
_CONNECTION_ERROR_TYPES = (
    ConnectionError,
    OSError,
    TimeoutError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.TimeoutException,
)


def _is_connection_error(e: Exception) -> bool:
    """判断异常是否为连接类错误（Proxy 不可达）"""
    if isinstance(e, _CONNECTION_ERROR_TYPES):
        return True
    # LiteLLM 的 APIConnectionError 也属于连接类错误
    error_name = type(e).__name__
    return error_name in ("APIConnectionError", "APITimeoutError")


_SENSITIVE_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|token|authorization)\b\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]+"),
]


def _redact_sensitive_text(text: str) -> str:
    """对异常文本做轻量脱敏，避免凭证进入日志。"""
    redacted = text
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class LiteLLMClient:
    """LiteLLM Proxy 客户端

    封装 litellm.acompletion() 调用，集成 CostTracker 计算成本。
    """

    def __init__(
        self,
        proxy_base_url: str = "http://localhost:4000",
        proxy_api_key: str = "",
        timeout_s: int = 30,
        *,
        stream_model_aliases: set[str] | None = None,
        responses_model_aliases: set[str] | None = None,
        responses_direct_params: dict[str, dict[str, Any]] | None = None,
        responses_reasoning_aliases: dict[str, ReasoningConfig] | None = None,
        reasoning_supported_aliases: set[str] | None = None,
        auth_refresh_callback: Callable[[], Awaitable[Any | None]] | None = None,
    ) -> None:
        """初始化 LiteLLM Proxy 客户端

        Args:
            proxy_base_url: Proxy 基础 URL
            proxy_api_key: Proxy 访问密钥（LITELLM_PROXY_KEY）
            timeout_s: 请求超时（秒）
            auth_refresh_callback: 认证刷新回调函数。当 LLM 调用返回 401/403 时，
                调用此函数获取刷新后的凭证。返回 None 表示刷新失败。
                返回值应具有 credential_value, api_base_url, extra_headers 属性
                （即 HandlerChainResult 或兼容对象）。
                对齐 contracts/token-refresh-api.md SS3。

        注意: proxy_api_key 是 Proxy 管理密钥，不是 LLM provider API key。
              LLM provider API key 仅存在于 Proxy 容器环境变量中。
        """
        self._proxy_base_url = proxy_base_url.rstrip("/")
        self._proxy_api_key = proxy_api_key
        self._timeout_s = timeout_s
        self._stream_model_aliases = set(stream_model_aliases or ())
        self._responses_model_aliases = set(responses_model_aliases or ())
        self._responses_direct_params = dict(responses_direct_params or {})
        self._responses_reasoning_aliases = dict(responses_reasoning_aliases or {})
        self._reasoning_supported_aliases = (
            None
            if reasoning_supported_aliases is None
            else set(reasoning_supported_aliases)
        )
        self._auth_refresh_callback = auth_refresh_callback

    @staticmethod
    def _normalize_system_messages(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """将所有 system 消息合并为一条放在开头。

        部分模型（Qwen、Gemma 等）只接受恰好一个 system 消息且必须在最前面。
        多个连续的 system 消息也会被拒绝。
        """
        if not messages:
            return messages

        system_parts: list[str] = []
        non_system: list[dict[str, str]] = []
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "").strip()
                if content:
                    system_parts.append(content)
            else:
                non_system.append(msg)

        if not system_parts:
            return messages

        # 已经只有一个 system 且在开头——不需要变
        if len(system_parts) == 1 and messages[0].get("role") == "system":
            return messages

        merged_system: dict[str, str] = {"role": "system", "content": "\n\n".join(system_parts)}
        return [merged_system, *non_system]

    @staticmethod
    def _is_auth_error(e: Exception) -> bool:
        """判断异常是否为认证类错误（401/403）

        检查方式（检查异常本身及其 __cause__ 链）:
        1. 异常类型为 AuthenticationError
        2. LiteLLM SDK 异常名称包含 Authentication/Authorization
        3. 异常状态码或消息包含 401/403

        对齐 contracts/token-refresh-api.md SS3。
        """

        def _check_single(exc: BaseException) -> bool:
            # 直接匹配 AuthenticationError
            if isinstance(exc, AuthenticationError):
                return True

            # 检查 LiteLLM SDK 异常类型名
            error_name = type(exc).__name__
            if error_name in ("AuthenticationError", "PermissionDeniedError"):
                return True

            # 检查异常是否有 status_code 属性（LiteLLM 异常通常有）
            status_code = getattr(exc, "status_code", None)
            if status_code in (401, 403):
                return True

            # 检查异常消息中的状态码
            error_msg = str(exc).lower()
            if ("401" in error_msg and ("auth" in error_msg or "unauthorized" in error_msg)) or (
                "403" in error_msg and ("forbidden" in error_msg or "permission" in error_msg)
            ):
                return True

            return False

        # 检查异常本身
        if _check_single(e):
            return True

        # 检查 __cause__ 链（`raise ... from e` 产生的链）
        cause = e.__cause__
        while cause is not None:
            if _check_single(cause):
                return True
            cause = cause.__cause__

        return False

    def _resolve_reasoning_for_alias(
        self,
        *,
        model_alias: str,
        reasoning: ReasoningConfig | None,
    ) -> ReasoningConfig | None:
        resolved = reasoning or self._responses_reasoning_aliases.get(model_alias)
        if resolved is None:
            return None
        if (
            self._reasoning_supported_aliases is None
            or model_alias in self._reasoning_supported_aliases
        ):
            return resolved
        log.info("skip_unsupported_reasoning_runtime", model_alias=model_alias)
        return None

    async def _collect_stream_response(
        self,
        response: AsyncIterator[Any],
        *,
        messages: list[dict[str, str]],
    ) -> Any:
        """消费 LiteLLM 流式响应并组装为完整 completion 对象。"""
        if stream_chunk_builder is None:  # pragma: no cover
            raise ProviderError(
                message="LiteLLM 未提供 stream_chunk_builder，无法解析流式响应",
                recoverable=True,
            )

        chunks: list[Any] = []
        async for chunk in response:
            chunks.append(chunk)

        if not chunks:
            raise ProviderError(
                message="LLM 返回了空的流式响应",
                recoverable=True,
            )

        complete_response = stream_chunk_builder(
            chunks=chunks,
            messages=messages,
        )
        if complete_response is None:
            raise ProviderError(
                message="LLM 流式响应组装失败",
                recoverable=True,
            )
        return complete_response

    def _build_result(
        self,
        *,
        response: Any,
        model_alias: str,
        duration_ms: int,
    ) -> ModelCallResult:
        """将 LiteLLM completion 响应转换为统一 ModelCallResult。"""
        msg = response.choices[0].message
        content = msg.content or ""
        # Qwen3 等 thinking 模型：content 为空时从 reasoning_content 提取
        if not content:
            rc = getattr(msg, "reasoning_content", None)
            if not rc:
                psf = getattr(msg, "provider_specific_fields", None) or {}
                rc = psf.get("reasoning_content") if isinstance(psf, dict) else None
            if rc:
                content = str(rc)
        cost_usd, cost_unavailable = CostTracker.calculate_cost(response)
        token_usage = CostTracker.parse_usage(response)
        model_name, provider = CostTracker.extract_model_info(response)

        return ModelCallResult(
            content=content,
            model_alias=model_alias,
            model_name=model_name,
            provider=provider,
            duration_ms=duration_ms,
            token_usage=token_usage,
            cost_usd=cost_usd,
            cost_unavailable=cost_unavailable,
            is_fallback=False,
            fallback_reason="",
        )

    @staticmethod
    def _build_responses_instructions(messages: list[dict[str, str]]) -> str:
        """把 system 消息折叠为 Responses API 的 instructions。"""
        instructions = [
            str(message.get("content", "")).strip()
            for message in messages
            if message.get("role") == "system" and str(message.get("content", "")).strip()
        ]
        if instructions:
            return "\n\n".join(instructions)
        return "Reply helpfully."

    @staticmethod
    def _build_responses_input(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        """把 chat messages 转为 Responses API 的 input 列表。"""
        input_items: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user")).strip() or "user"
            if role == "system":
                continue
            content = str(message.get("content", ""))
            content_type = "output_text" if role == "assistant" else "input_text"
            input_items.append(
                {
                    "role": role,
                    "content": [{"type": content_type, "text": content}],
                }
            )
        return input_items

    @staticmethod
    def _build_responses_url(api_base: str) -> str:
        base = api_base.rstrip("/")
        if base.endswith("/backend-api") or base.endswith("/backend-api/codex"):
            return f"{base}/responses"
        return f"{base}/v1/responses"

    async def _complete_via_responses_api(
        self,
        *,
        messages: list[dict[str, str]],
        model_alias: str,
        model_name: str | None = None,
        api_base: str,
        api_key: str,
        temperature: float,
        max_tokens: int | None,
        reasoning: ReasoningConfig | None,
        extra_headers: dict[str, str] | None,
        **kwargs,
    ) -> ModelCallResult:
        """通过 Responses API 直连 Codex Backend。

        Args:
            model_alias: 运行时别名（如 "main"），用于日志和结果标识。
            model_name: 真实模型名（如 "gpt-5.4"），发送给 Backend。
                        若为 None/空则回退到 model_alias。
        """
        start_time = time.monotonic()
        text_parts: list[str] = []
        response_model_name = ""
        usage = TokenUsage()
        # Codex Backend 需要真实模型名，不认别名
        wire_model = model_name or model_alias

        request_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            request_headers.update(extra_headers)

        resolved_reasoning = self._resolve_reasoning_for_alias(
            model_alias=model_alias,
            reasoning=reasoning,
        )
        body: dict[str, Any] = {
            "model": wire_model,
            "instructions": self._build_responses_instructions(messages),
            "input": self._build_responses_input(messages),
            "store": False,
            "stream": True,
        }
        if max_tokens is not None:
            body["max_output_tokens"] = max_tokens
        if resolved_reasoning is not None:
            body["reasoning"] = resolved_reasoning.to_responses_api_param()
        body.update(kwargs)

        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout_s) as http_client,
                http_client.stream(
                    "POST",
                    self._build_responses_url(api_base),
                    headers=request_headers,
                    json=body,
                ) as resp,
            ):
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    event_type = str(event.get("type", ""))
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta", ""))
                        if delta:
                            text_parts.append(delta)
                        continue

                    if event_type != "response.completed":
                        continue

                    response = event.get("response", {})
                    if isinstance(response, dict):
                        response_model_name = str(response.get("model", "") or "")
                        usage_payload = response.get("usage", {})
                        if isinstance(usage_payload, dict):
                            usage = TokenUsage(
                                prompt_tokens=int(usage_payload.get("input_tokens", 0) or 0),
                                completion_tokens=int(
                                    usage_payload.get("output_tokens", 0) or 0
                                ),
                                total_tokens=int(usage_payload.get("total_tokens", 0) or 0),
                            )
                        if not text_parts:
                            output_items = response.get("output", [])
                            if isinstance(output_items, list):
                                for output in output_items:
                                    if not isinstance(output, dict):
                                        continue
                                    for part in output.get("content", []):
                                        if (
                                            isinstance(part, dict)
                                            and part.get("type") == "output_text"
                                            and part.get("text")
                                        ):
                                            text_parts.append(str(part["text"]))
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_error = _redact_sensitive_text(str(e))
            log.error(
                "responses_call_failed",
                model_alias=model_alias,
                error=sanitized_error,
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
            if _is_connection_error(e):
                raise ProxyUnreachableError(
                    proxy_url=self._proxy_base_url,
                    original_error=e,
                ) from e
            raise ProviderError(
                message=f"LLM 调用失败: {sanitized_error}",
                recoverable=True,
            ) from e

        duration_ms = int((time.monotonic() - start_time) * 1000)
        content = "".join(text_parts).strip()
        if not content:
            raise ProviderError(
                message="LLM 返回了空的 Responses 响应",
                recoverable=True,
            )

        return ModelCallResult(
            content=content,
            model_alias=model_alias,
            model_name=response_model_name or model_alias,
            provider="openai",
            duration_ms=duration_ms,
            token_usage=usage,
            cost_usd=0.0,
            cost_unavailable=True,
            is_fallback=False,
            fallback_reason="",
        )

    async def _do_complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str,
        temperature: float,
        max_tokens: int | None,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        reasoning: ReasoningConfig | None = None,
        **kwargs,
    ) -> ModelCallResult:
        """内部 complete 实现，不含 auth retry 逻辑。

        提取为独立方法以便 retry 时复用。
        """
        start_time = time.monotonic()

        # 路由决策：覆盖参数优先于实例默认值
        resolved_api_base = api_base or self._proxy_base_url
        resolved_api_key = api_key or self._proxy_api_key or "no-key"

        if model_alias in self._responses_model_aliases:
            # Responses API 调用直连 Codex Backend，绕过 Proxy，
            # 避免 Proxy 内部 fallback 到不支持 Responses API 的 Provider
            # Responses API 直连绕过 LiteLLM SDK 的 "os.environ/KEY" 解析，
            # direct_params 是启动快照；调用前主动触发 auth_refresh_callback，
            # 让其内部做预过期检查（PkceOAuthAdapter.resolve() 5 分钟 buffer）。
            if self._auth_refresh_callback is not None:
                try:
                    refreshed = await self._auth_refresh_callback()
                except Exception:
                    log.warning(
                        "responses_api_precheck_refresh_failed",
                        model_alias=model_alias,
                        exc_info=True,
                    )
                    refreshed = None
                if refreshed is not None:
                    api_key = (
                        getattr(refreshed, "credential_value", None) or api_key
                    )
                    api_base = (
                        getattr(refreshed, "api_base_url", None) or api_base
                    )
                    extra_headers = (
                        getattr(refreshed, "extra_headers", None) or extra_headers
                    )
                    resolved_api_base = api_base or self._proxy_base_url
                    resolved_api_key = api_key or self._proxy_api_key or "no-key"

            direct = self._responses_direct_params.get(model_alias)
            if direct:
                direct_base = direct.get("api_base", resolved_api_base)
                # 显式传入的 api_key 优先于启动快照；让预检查/401 重试刷新后的
                # 新 token 覆盖 direct_params 里过期的静态值。
                direct_key = api_key or direct.get("api_key", resolved_api_key)
                direct_headers = {**(extra_headers or {}), **direct.get("headers", {})}
                # Codex Backend 不认别名，必须用真实模型名（如 gpt-5.4）
                direct_model = direct.get("model") or model_alias
            else:
                direct_base = resolved_api_base
                direct_key = resolved_api_key
                direct_headers = extra_headers
                direct_model = model_alias
            return await self._complete_via_responses_api(
                messages=messages,
                model_alias=model_alias,
                model_name=direct_model,
                api_base=direct_base,
                api_key=direct_key,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning=reasoning,
                extra_headers=direct_headers,
                **kwargs,
            )

        try:
            # 构建调用参数
            # model 加 "openai/" 前缀：告诉本地 LiteLLM SDK 将请求视为
            # OpenAI 兼容端点直接转发到 Proxy，由 Proxy 负责路由到真实模型。
            proxy_model = f"openai/{model_alias}"
            use_stream = model_alias in self._stream_model_aliases
            # 非 OpenAI 模型（Qwen 等）要求 system 消息在开头，
            # 预处理：合并所有 system 消息到第一条
            normalized_messages = self._normalize_system_messages(messages)
            call_kwargs = {
                "model": proxy_model,
                "messages": normalized_messages,
                "api_base": resolved_api_base,
                "api_key": resolved_api_key,
                "temperature": temperature,
                "timeout": self._timeout_s,
                **kwargs,
            }
            if max_tokens is not None:
                call_kwargs["max_tokens"] = max_tokens
            if extra_headers:
                call_kwargs["extra_headers"] = extra_headers
            # Chat Completions API 使用顶层 reasoning_effort 字符串
            resolved_reasoning = self._resolve_reasoning_for_alias(
                model_alias=model_alias,
                reasoning=reasoning,
            )
            if resolved_reasoning is not None:
                call_kwargs["reasoning_effort"] = resolved_reasoning.effort
            if use_stream:
                # ChatGPT backend / Codex OAuth 路径经 LiteLLM Proxy 会返回 SSE 分片，
                # 这里显式切到 stream 模式，再在客户端聚合回完整结果。
                call_kwargs["stream"] = True
                call_kwargs["stream_options"] = {"include_usage": True}

            log.debug(
                "litellm_call_start",
                model_alias=model_alias,
                message_count=len(messages),
                routing_override=api_base is not None,
            )

            # 调用 LiteLLM SDK
            response = await acompletion(**call_kwargs)
            if use_stream:
                response = await self._collect_stream_response(
                    response,
                    messages=messages,
                )

            # 计算耗时
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = self._build_result(
                response=response,
                model_alias=model_alias,
                duration_ms=duration_ms,
            )

            log.info(
                "litellm_call_completed",
                model_alias=model_alias,
                model_name=result.model_name,
                provider=result.provider,
                duration_ms=duration_ms,
                cost_usd=result.cost_usd,
            )

            return result

        except (ProxyUnreachableError, ProviderError):
            # 已包装的异常直接抛出
            raise
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_error = _redact_sensitive_text(str(e))
            log.error(
                "litellm_call_failed",
                model_alias=model_alias,
                error=sanitized_error,
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
            # 区分连接类错误与业务错误
            if _is_connection_error(e):
                raise ProxyUnreachableError(
                    proxy_url=self._proxy_base_url,
                    original_error=e,
                ) from e
            else:
                # LiteLLM SDK 业务错误（模型不存在、配额耗尽、invalid request 等）
                raise ProviderError(
                    message=f"LLM 调用失败: {sanitized_error}",
                    recoverable=True,
                ) from e

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        reasoning: ReasoningConfig | None = None,
        **kwargs,
    ) -> ModelCallResult:
        """发送 chat completion 请求到 LiteLLM Proxy

        包含 refresh-on-auth-error 重试逻辑：当返回 401/403 且有
        auth_refresh_callback 时，自动刷新凭证并重试一次。

        对齐 contracts/token-refresh-api.md SS3, FR-002。

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            model_alias: 运行时 alias（由 AliasRegistry.resolve() 提供）
            temperature: 采样温度
            max_tokens: 最大生成 token 数，None 使用模型默认
            api_base: API base URL 覆盖（如 JWT 方案直连 Provider API）
            api_key: API key 覆盖（如 JWT access_token 作为 Bearer token）
            extra_headers: 附加 HTTP headers（如 chatgpt-account-id）
            reasoning: Reasoning 配置（用于 Codex/o-系列模型的思考模式）
            **kwargs: 其他 LiteLLM 支持的参数

        Returns:
            ModelCallResult，包含完整的响应、成本、路由信息

        Raises:
            ProxyUnreachableError: Proxy 连接失败或超时
            ProviderError: Proxy 返回错误（如模型不可用、配额耗尽）
        """
        try:
            return await self._do_complete(
                messages,
                model_alias,
                temperature,
                max_tokens,
                api_base=api_base,
                api_key=api_key,
                extra_headers=extra_headers,
                reasoning=reasoning,
                **kwargs,
            )
        except Exception as e:
            # refresh-on-auth-error 重试逻辑
            if not self._is_auth_error(e) or self._auth_refresh_callback is None:
                raise

            log.info(
                "auth_error_triggering_refresh",
                model_alias=model_alias,
                error_type=type(e).__name__,
            )

            try:
                refreshed = await self._auth_refresh_callback()
            except Exception:
                log.warning(
                    "auth_refresh_callback_failed",
                    model_alias=model_alias,
                    exc_info=True,
                )
                raise e from None  # 抛出原始认证错误，抑制 callback 异常链

            if refreshed is None:
                raise ProviderError(
                    message=(
                        "认证凭证已失效且刷新失败。请重新授权: "
                        "octo auth setup"
                    ),
                    recoverable=True,
                ) from e

            # 使用刷新后的凭证重试一次
            call_kwargs: dict[str, Any] = dict(
                api_base=getattr(refreshed, "api_base_url", None) or api_base,
                api_key=getattr(refreshed, "credential_value", None) or api_key,
                extra_headers=getattr(refreshed, "extra_headers", None) or extra_headers,
                reasoning=reasoning,
                **kwargs,
            )

            log.info(
                "auth_refresh_retrying",
                model_alias=model_alias,
            )

            try:
                return await self._do_complete(
                    messages, model_alias, temperature, max_tokens, **call_kwargs,
                )
            except Exception as retry_err:
                # 重试后仍然失败：检测 Anthropic 政策拒绝
                # 对齐 contracts/claude-provider-api.md SS3, FR-010
                retry_msg = str(retry_err).lower()
                if "permission_error" in retry_msg or (
                    "403" in retry_msg and "permission" in retry_msg
                ):
                    raise ProviderError(
                        message=(
                            "Claude 订阅凭证被 Anthropic 拒绝。\n"
                            "此凭证可能仅授权用于 Claude Code 应用，不支持第三方调用。\n"
                            "建议: 使用 Anthropic API Key 替代订阅凭证。\n"
                            "配置方法: octo auth setup -> 选择 Anthropic -> 输入 API Key"
                        ),
                        recoverable=True,
                    ) from retry_err
                raise

    async def health_check(self) -> bool:
        """检查 LiteLLM Proxy 可达性

        发送 GET {proxy_base_url}/health/liveliness 请求。

        Returns:
            True 如果 Proxy 活跃，False 如果不可达或异常

        注意: 此方法不抛出异常，所有异常内部捕获并返回 False。
              超时设置为 5 秒（硬编码，健康检查应快速响应）。
        """
        url = f"{self._proxy_base_url}/health/liveliness"
        try:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.get(url, timeout=HEALTH_CHECK_TIMEOUT_S)
                return resp.status_code == 200
        except Exception as e:
            log.debug("health_check_failed", url=url, error=_redact_sensitive_text(str(e)))
            return False
