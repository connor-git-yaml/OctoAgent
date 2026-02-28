"""EchoMessageAdapter -- Echo 模式 messages 接口适配

对齐 contracts/provider-api.md SS6。
将 messages 格式适配为 Echo 回声，返回 ModelCallResult。
FallbackManager 的降级后备统一使用此适配器。
"""

import asyncio
import time

from .models import ModelCallResult, TokenUsage


class EchoMessageAdapter:
    """EchoProvider 的 messages 接口适配层

    将 EchoProvider 的行为适配为 complete(messages) -> ModelCallResult 接口。
    FallbackManager 的降级后备统一使用此适配器。
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "echo",
        **kwargs,
    ) -> ModelCallResult:
        """通过 Echo 模式处理 messages

        行为:
            1. 从 messages 中提取最后一条 user message 的 content
            2. 返回 "Echo: {content}" 格式的回声
            3. 构建 ModelCallResult，provider="echo"
            4. token_usage 使用 prompt_tokens/completion_tokens/total_tokens 命名

        Args:
            messages: 消息列表
            model_alias: 模型别名
            **kwargs: 忽略

        Returns:
            ModelCallResult
        """
        start_time = time.monotonic()

        # 提取最后一条 user message 的 content
        user_content = self._extract_last_user_content(messages)

        # 模拟少量延迟
        await asyncio.sleep(0.01)

        # 构建回声响应
        response_text = f"Echo: {user_content}"

        # 计算 token（按 word 简单估算）
        prompt_tokens = len(user_content.split())
        completion_tokens = len(response_text.split())

        duration_ms = int((time.monotonic() - start_time) * 1000)

        return ModelCallResult(
            content=response_text,
            model_alias=model_alias,
            model_name="echo",
            provider="echo",
            duration_ms=duration_ms,
            token_usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            cost_usd=0.0,
            cost_unavailable=False,
            is_fallback=False,  # 由 FallbackManager 按需覆盖
            fallback_reason="",
        )

    @staticmethod
    def _extract_last_user_content(messages: list[dict[str, str]]) -> str:
        """从 messages 中提取最后一条 user message 的 content

        Args:
            messages: 消息列表

        Returns:
            最后一条 user message 的 content，无 user 消息时返回 "(empty)"
        """
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")

        # 无 user 消息时的降级处理
        if messages:
            return messages[-1].get("content", "(empty)")
        return "(empty)"
