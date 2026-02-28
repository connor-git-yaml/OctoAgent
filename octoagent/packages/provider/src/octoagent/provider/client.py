"""LiteLLMClient -- LiteLLM Proxy 调用封装

对齐 contracts/provider-api.md SS2。
通过 litellm.acompletion() 调用 Proxy，内部集成 CostTracker。
"""

import time

import httpx
import structlog

from .cost import CostTracker
from .exceptions import ProviderError, ProxyUnreachableError
from .models import ModelCallResult

log = structlog.get_logger()

# 隔离 litellm 导入，方便测试 Mock
try:
    from litellm import acompletion
except ImportError:  # pragma: no cover
    acompletion = None  # type: ignore[assignment]

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


class LiteLLMClient:
    """LiteLLM Proxy 客户端

    封装 litellm.acompletion() 调用，集成 CostTracker 计算成本。
    """

    def __init__(
        self,
        proxy_base_url: str = "http://localhost:4000",
        proxy_api_key: str = "",
        timeout_s: int = 30,
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

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ModelCallResult:
        """发送 chat completion 请求到 LiteLLM Proxy

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            model_alias: 运行时 group 名称（由 AliasRegistry.resolve() 提供）
            temperature: 采样温度
            max_tokens: 最大生成 token 数，None 使用模型默认
            **kwargs: 其他 LiteLLM 支持的参数

        Returns:
            ModelCallResult，包含完整的响应、成本、路由信息

        Raises:
            ProxyUnreachableError: Proxy 连接失败或超时
            ProviderError: Proxy 返回错误（如模型不可用、配额耗尽）
        """
        start_time = time.monotonic()

        try:
            # 构建调用参数
            call_kwargs = {
                "model": model_alias,
                "messages": messages,
                "api_base": self._proxy_base_url,
                "api_key": self._proxy_api_key or "no-key",
                "temperature": temperature,
                "timeout": self._timeout_s,
                **kwargs,
            }
            if max_tokens is not None:
                call_kwargs["max_tokens"] = max_tokens

            log.debug(
                "litellm_call_start",
                model_alias=model_alias,
                message_count=len(messages),
            )

            # 调用 LiteLLM SDK
            response = await acompletion(**call_kwargs)

            # 计算耗时
            duration_ms = int((time.monotonic() - start_time) * 1000)

            # 提取响应内容
            content = response.choices[0].message.content or ""

            # 通过 CostTracker 计算成本
            cost_usd, cost_unavailable = CostTracker.calculate_cost(response)

            # 通过 CostTracker 解析 token 使用
            token_usage = CostTracker.parse_usage(response)

            # 提取模型和 provider 信息
            model_name, provider = CostTracker.extract_model_info(response)

            result = ModelCallResult(
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

            log.info(
                "litellm_call_completed",
                model_alias=model_alias,
                model_name=model_name,
                provider=provider,
                duration_ms=duration_ms,
                cost_usd=cost_usd,
            )

            return result

        except (ProxyUnreachableError, ProviderError):
            # 已包装的异常直接抛出
            raise
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            log.error(
                "litellm_call_failed",
                model_alias=model_alias,
                error=str(e),
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
                    message=f"LLM 调用失败: {e}",
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
            log.debug("health_check_failed", url=url, error=str(e))
            return False
