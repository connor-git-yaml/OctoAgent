"""LLMService -- 对齐 spec FR-M0-LLM-1/2/3

Echo 模式：返回输入消息的回声（不依赖外部 LLM）。
Mock 模式：返回固定响应。
实现 model alias 抽象，便于 M1 替换为真实 LLM 代理。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """LLM 调用响应"""

    content: str
    model_alias: str
    duration_ms: int
    token_usage: dict[str, int]


class LLMProvider(ABC):
    """LLM 提供者抽象接口"""

    @abstractmethod
    async def call(self, prompt: str) -> LLMResponse:
        """调用 LLM

        Args:
            prompt: 用户输入文本

        Returns:
            LLMResponse 响应
        """
        ...


class EchoProvider(LLMProvider):
    """Echo 模式 -- 返回输入回声

    用于端到端验证架构可行性，不依赖外部 LLM 服务。
    """

    def __init__(self, model_alias: str = "echo") -> None:
        self._model_alias = model_alias

    async def call(self, prompt: str) -> LLMResponse:
        """返回输入的回声"""
        # 模拟少量延迟
        await asyncio.sleep(0.01)

        response_text = f"Echo: {prompt}"
        prompt_tokens = len(prompt.split())
        completion_tokens = len(response_text.split())

        return LLMResponse(
            content=response_text,
            model_alias=self._model_alias,
            duration_ms=10,
            token_usage={
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
        )


class MockProvider(LLMProvider):
    """Mock 模式 -- 返回固定响应"""

    def __init__(
        self,
        response: str = "This is a mock response.",
        model_alias: str = "mock",
    ) -> None:
        self._response = response
        self._model_alias = model_alias

    async def call(self, prompt: str) -> LLMResponse:
        """返回固定响应"""
        return LLMResponse(
            content=self._response,
            model_alias=self._model_alias,
            duration_ms=5,
            token_usage={"prompt": 1, "completion": 1, "total": 2},
        )


class LLMService:
    """LLM 服务 -- model alias 路由

    M0 仅支持 echo/mock 两种 provider。
    M1+ 将支持 LiteLLM Proxy 路由。
    """

    def __init__(self, default_provider: LLMProvider | None = None) -> None:
        self._providers: dict[str, LLMProvider] = {}
        if default_provider:
            self._default = default_provider
        else:
            self._default = EchoProvider()
        self._providers["echo"] = EchoProvider()
        self._providers["mock"] = MockProvider()

    def register(self, alias: str, provider: LLMProvider) -> None:
        """注册 LLM provider"""
        self._providers[alias] = provider

    async def call(
        self,
        prompt: str,
        model_alias: str | None = None,
    ) -> LLMResponse:
        """调用 LLM

        Args:
            prompt: 输入文本
            model_alias: 模型别名，None 则使用默认 provider

        Returns:
            LLMResponse
        """
        if model_alias and model_alias in self._providers:
            provider = self._providers[model_alias]
        else:
            provider = self._default

        return await provider.call(prompt)
