"""ProviderRouterMessageAdapter -- ProviderRouter 包装成 LLMProviderProtocol。

Feature 081 P1 引入：替代 LiteLLMClient 作为 FallbackManager.primary。

LLMService.call() 在没有 SkillRunner 路径时（如 context_compaction 直接调用）
会走 FallbackManager.call_with_fallback()，期待 primary 实现 complete()。

LiteLLMClient 退役后，本 adapter 作为新的 primary：把 messages 形式适配成
ProviderClient.call() 签名（instructions / history / tools / model_name），
经由 ProviderRouter 直连 provider，返回 ModelCallResult。
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from .models import ModelCallResult, TokenUsage
from .provider_router import ProviderRouter

log = structlog.get_logger()

__all__ = ["ProviderRouterMessageAdapter"]


class ProviderRouterMessageAdapter:
    """ProviderRouter 的 messages 接口适配层。

    与 EchoMessageAdapter 同构，但底层走真实 provider 直连。

    使用场景：
    - context_compaction.py → llm_service.call() → FallbackManager → 本 adapter
    - 其他直接 LLMService.call() 入口（M0 兼容路径）

    与 SkillRunner / ProviderModelClient 的区别：
    - SkillRunner 走工具调用循环，本 adapter 仅做单次调用（无工具）
    - ProviderModelClient 维护 history 缓存，本 adapter 是无状态的
    """

    def __init__(self, provider_router: ProviderRouter) -> None:
        self._router = provider_router

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> ModelCallResult:
        """通过 ProviderRouter 直连 provider，返回 ModelCallResult。

        Args:
            messages: OpenAI 格式的 messages（含 role + content）
            model_alias: 配置 alias（如 ``main`` / ``compaction`` / ``summarizer``）
            **kwargs: 透传字段（兼容 LiteLLMClient 接口；本 adapter 不消费）

        Returns:
            ModelCallResult（``is_fallback=False``，由 FallbackManager 按需覆盖）
        """
        start_time = time.monotonic()

        # 拆分 system 消息为 instructions，其余作为 history
        instructions, history = self._split_system_and_history(messages)

        resolved = self._router.resolve_for_alias(model_alias, task_scope=None)

        try:
            content, tool_calls, metadata = await resolved.client.call(
                instructions=instructions,
                history=history,
                tools=[],
                model_name=resolved.model_name,
                reasoning=None,
            )
        except Exception:
            log.warning(
                "router_message_adapter_call_failed",
                alias=model_alias,
                provider_id=resolved.client.runtime.provider_id,
            )
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # 从 metadata 提取 token usage
        usage_meta = metadata.get("usage") if isinstance(metadata, dict) else None
        if isinstance(usage_meta, dict):
            prompt_tokens = int(usage_meta.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage_meta.get("completion_tokens", 0) or 0)
            total_tokens = int(usage_meta.get("total_tokens", prompt_tokens + completion_tokens))
        else:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0

        return ModelCallResult(
            content=content or "",
            model_alias=model_alias,
            model_name=resolved.model_name,
            provider=resolved.client.runtime.provider_id,
            duration_ms=duration_ms,
            token_usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            cost_usd=0.0,
            cost_unavailable=True,  # cost calculator 未在本 Feature 内重构
            is_fallback=False,
            fallback_reason="",
        )

    @staticmethod
    def _split_system_and_history(
        messages: list[dict[str, str]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """从 messages 中分离 system → instructions，其余 → history。"""
        instructions_parts: list[str] = []
        history: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                if content:
                    instructions_parts.append(content)
            else:
                history.append({"role": role, "content": content})
        instructions = "\n\n".join(instructions_parts)
        return instructions, history
