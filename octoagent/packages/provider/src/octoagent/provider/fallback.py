"""FallbackManager -- 降级管理器

对齐 contracts/provider-api.md SS5。
Lazy probe 策略：每次调用时先尝试 primary，失败则切换到 fallback。
不维护显式的"降级状态"标记。
"""

import structlog

from .exceptions import ProviderError
from .models import ModelCallResult
from .provider_client import LLMCallError

log = structlog.get_logger()


class FallbackManager:
    """降级管理器

    降级链: LiteLLMClient -> EchoMessageAdapter
    Proxy 内部的 model fallback 由 Proxy 自行处理，对本组件透明。
    """

    def __init__(
        self,
        primary,
        fallback=None,
    ) -> None:
        """初始化降级管理器

        Args:
            primary: 主 LLM 客户端（LiteLLMClient 或 EchoMessageAdapter）
            fallback: 降级客户端（默认 EchoMessageAdapter），None 表示无降级
        """
        self._primary = primary
        self._fallback = fallback

    async def call_with_fallback(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs,
    ) -> ModelCallResult:
        """带降级的 LLM 调用

        Lazy probe 策略：每次调用时先尝试 primary，失败则切换到 fallback。
        不维护显式的"降级状态"标记。

        Args:
            messages: 消息列表
            model_alias: 运行时 alias 名称
            **kwargs: 传递给 primary.complete() 的额外参数

        Returns:
            ModelCallResult
            - primary 成功: is_fallback=False
            - fallback 成功: is_fallback=True, fallback_reason=<错误描述>
            - 全部失败: 抛出 ProviderError

        Raises:
            ProviderError: primary 和 fallback 均失败
        """
        # 先尝试 primary
        primary_error: Exception | None = None
        try:
            result = await self._primary.complete(
                messages=messages,
                model_alias=model_alias,
                **kwargs,
            )
            return result
        except Exception as e:
            primary_error = e
            if isinstance(e, LLMCallError) and e.status_code in (401, 403):
                # 凭证失效（provider_client 内部已 force_refresh 失败）：
                # fallback（Echo）无法恢复凭证，且 Echo 假成功会把事故掩盖成
                # 正常回复——2026-06-12 production 实测这条路径让凭证断链的
                # task 永远到不了 FAILED 终态。直接向上抛，由上层
                # _handle_llm_failure 把 task 推进 FAILED。
                log.warning(
                    "primary_auth_error_skip_fallback",
                    status_code=e.status_code,
                    model_alias=model_alias,
                )
                raise
            log.warning(
                "primary_failed_attempting_fallback",
                error=str(e),
                model_alias=model_alias,
            )

        # Primary 失败，尝试 fallback
        if self._fallback is None:
            raise ProviderError(
                f"Primary 调用失败且无 fallback 配置: {primary_error}",
                recoverable=False,
            ) from primary_error

        try:
            result = await self._fallback.complete(
                messages=messages,
                model_alias=model_alias,
            )
            # 标记为降级调用
            result = result.model_copy(
                update={
                    "is_fallback": True,
                    "fallback_reason": f"Primary 失败: {primary_error}",
                }
            )
            log.info(
                "fallback_activated",
                fallback_reason=str(primary_error),
                model_alias=model_alias,
            )
            return result
        except Exception as fallback_error:
            log.error(
                "both_primary_and_fallback_failed",
                primary_error=str(primary_error),
                fallback_error=str(fallback_error),
            )
            raise ProviderError(
                f"Primary 和 Fallback 均失败。Primary: {primary_error}; Fallback: {fallback_error}",
                recoverable=False,
            ) from fallback_error
