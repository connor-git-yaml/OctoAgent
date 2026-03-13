"""LiteLLMClient -- LiteLLM Proxy 调用封装

对齐 contracts/provider-api.md SS2。
通过 litellm.acompletion() 调用 Proxy，内部集成 CostTracker。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from .cost import CostTracker
from .exceptions import ProviderError, ProxyUnreachableError
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
        responses_reasoning_aliases: dict[str, ReasoningConfig] | None = None,
    ) -> None:
        """初始化 LiteLLM Proxy 客户端

        Args:
            proxy_base_url: Proxy 基础 URL
            proxy_api_key: Proxy 访问密钥（LITELLM_PROXY_KEY）
            timeout_s: 请求超时（秒）

        注意: proxy_api_key 是 Proxy 管理密钥，不是 LLM provider API key。
              LLM provider API key 仅存在于 Proxy 容器环境变量中。
        """
        self._proxy_base_url = proxy_base_url.rstrip("/")
        self._proxy_api_key = proxy_api_key
        self._timeout_s = timeout_s
        self._stream_model_aliases = set(stream_model_aliases or ())
        self._responses_model_aliases = set(responses_model_aliases or ())
        self._responses_reasoning_aliases = dict(responses_reasoning_aliases or {})

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
        content = response.choices[0].message.content or ""
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
        api_base: str,
        api_key: str,
        temperature: float,
        max_tokens: int | None,
        reasoning: ReasoningConfig | None,
        extra_headers: dict[str, str] | None,
        **kwargs,
    ) -> ModelCallResult:
        """通过 Proxy /v1/responses 调用 Codex-compatible alias。"""
        start_time = time.monotonic()
        text_parts: list[str] = []
        response_model_name = ""
        usage = TokenUsage()

        request_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            request_headers.update(extra_headers)

        resolved_reasoning = reasoning or self._responses_reasoning_aliases.get(model_alias)
        body: dict[str, Any] = {
            "model": model_alias,
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
            async with httpx.AsyncClient(timeout=self._timeout_s) as http_client:
                async with http_client.stream(
                    "POST",
                    self._build_responses_url(api_base),
                    headers=request_headers,
                    json=body,
                ) as resp:
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

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            model_alias: 运行时 group 名称（由 AliasRegistry.resolve() 提供）
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
        start_time = time.monotonic()

        # 路由决策：覆盖参数优先于实例默认值
        resolved_api_base = api_base or self._proxy_base_url
        resolved_api_key = api_key or self._proxy_api_key or "no-key"

        if model_alias in self._responses_model_aliases:
            return await self._complete_via_responses_api(
                messages=messages,
                model_alias=model_alias,
                api_base=resolved_api_base,
                api_key=resolved_api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning=reasoning,
                extra_headers=extra_headers,
                **kwargs,
            )

        try:
            # 构建调用参数
            # model 加 "openai/" 前缀：告诉本地 LiteLLM SDK 将请求视为
            # OpenAI 兼容端点直接转发到 Proxy，由 Proxy 负责路由到真实模型。
            proxy_model = f"openai/{model_alias}"
            use_stream = model_alias in self._stream_model_aliases
            call_kwargs = {
                "model": proxy_model,
                "messages": messages,
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
            if reasoning is not None:
                call_kwargs["reasoning_effort"] = reasoning.effort
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
