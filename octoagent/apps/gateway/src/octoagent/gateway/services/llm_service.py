"""LLMService -- Feature 002 版本

对齐 contracts/gateway-changes.md SS3。
支持 FallbackManager + AliasRegistry，返回 ModelCallResult。
保留 M0 EchoProvider/MockProvider/LLMResponse 供向后兼容（标记废弃）。
"""

import asyncio
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass

from octoagent.provider import (
    AliasRegistry,
    EchoMessageAdapter,
    FallbackManager,
    ModelCallResult,
)

# ============================================================
# M0 遗留类型（废弃，保留供旧测试兼容）
# ============================================================


@dataclass
class LLMResponse:
    """LLM 调用响应 -- M0 遗留类型

    .. deprecated:: 0.2.0
        Feature 002 起使用 ``ModelCallResult`` 替代。
        保留此类仅供 M0 测试兼容，后续版本将删除。
        迁移指南: 将 ``LLMResponse`` 替换为 ``from octoagent.provider import ModelCallResult``。
    """

    content: str
    model_alias: str
    duration_ms: int
    token_usage: dict[str, int]

    def __post_init__(self):
        warnings.warn(
            "LLMResponse 已废弃，请使用 octoagent.provider.ModelCallResult 替代。"
            "此类将在 v0.3.0 移除。",
            DeprecationWarning,
            stacklevel=2,
        )


class LLMProvider(ABC):
    """LLM 提供者抽象接口 -- M0 遗留接口

    .. deprecated:: 0.2.0
        Feature 002 起使用 FallbackManager + EchoMessageAdapter 替代。
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        warnings.warn(
            f"{cls.__name__} 继承自已废弃的 LLMProvider，"
            "请迁移到 FallbackManager + EchoMessageAdapter 模式。",
            DeprecationWarning,
            stacklevel=2,
        )

    @abstractmethod
    async def call(self, prompt: str) -> LLMResponse:
        """调用 LLM"""
        ...


class EchoProvider(LLMProvider):
    """Echo 模式 -- 返回输入回声

    .. deprecated:: 0.2.0
        Feature 002 起使用 EchoMessageAdapter 替代。
        保留供 M0 旧测试兼容。
    """

    def __init__(self, model_alias: str = "echo") -> None:
        self._model_alias = model_alias

    async def call(self, prompt: str) -> LLMResponse:
        """返回输入的回声"""
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
    """Mock 模式 -- 返回固定响应

    .. deprecated:: 0.2.0
        Feature 002 起使用 Mock ModelCallResult 替代。
    """

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


# ============================================================
# Feature 002 LLMService
# ============================================================


class LLMService:
    """LLM 服务 -- Feature 002 版本

    变更:
    - 构造器接受 FallbackManager + AliasRegistry（替代直接持有 providers dict）
    - call() 支持 messages 格式和 prompt 字符串（向后兼容）
    - 返回 ModelCallResult（替代 LLMResponse）

    向后兼容: 无参构造时自动创建 Echo 模式的 FallbackManager + AliasRegistry。
    """

    def __init__(
        self,
        fallback_manager: FallbackManager | None = None,
        alias_registry: AliasRegistry | None = None,
        default_provider: LLMProvider | None = None,
    ) -> None:
        """初始化 LLM 服务

        Args:
            fallback_manager: 包含 primary + fallback 的降级管理器
            alias_registry: 语义 alias 注册表
            default_provider: M0 兼容参数（废弃，仅向后兼容）
        """
        if fallback_manager is not None:
            # Feature 002 模式
            self._fallback_manager = fallback_manager
            self._alias_registry = alias_registry or AliasRegistry()
        else:
            # M0 向后兼容模式：自动创建 Echo FallbackManager
            echo_adapter = EchoMessageAdapter()
            self._fallback_manager = FallbackManager(
                primary=echo_adapter,
                fallback=None,
            )
            self._alias_registry = AliasRegistry()

        # M0 兼容：保留旧的 providers dict（仅供向后兼容）
        self._providers: dict[str, LLMProvider] = {}
        self._providers["echo"] = EchoProvider()
        self._providers["mock"] = MockProvider()

    def register(self, alias: str, provider: LLMProvider) -> None:
        """注册 LLM provider -- M0 兼容"""
        self._providers[alias] = provider

    async def call(
        self,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str | None = None,
    ) -> ModelCallResult:
        """调用 LLM

        Args:
            prompt_or_messages:
                - str: 纯文本 prompt（M0 兼容，自动转为 messages 格式）
                - list[dict]: messages 格式（Feature 002 推荐）
            model_alias:
                - 语义 alias（如 "planner"）-> AliasRegistry 解析为运行时 group
                - 运行时 group（如 "main"）-> 直接透传
                - None -> 使用 "main" 默认

        Returns:
            ModelCallResult
        """
        # 转换 prompt 为 messages 格式
        if isinstance(prompt_or_messages, str):
            messages = [{"role": "user", "content": prompt_or_messages}]
        else:
            messages = prompt_or_messages

        # 解析 alias
        resolved_alias = model_alias or "main"
        resolved_alias = self._alias_registry.resolve(resolved_alias)

        # 通过 FallbackManager 调用
        return await self._fallback_manager.call_with_fallback(
            messages=messages,
            model_alias=resolved_alias,
        )
