"""LLMService -- Feature 002 版本

对齐 contracts/gateway-changes.md SS3。
支持 FallbackManager + AliasRegistry，返回 ModelCallResult。
保留 M0 EchoProvider/MockProvider/LLMResponse 供向后兼容（标记废弃）。
"""

import asyncio
import json
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from octoagent.provider import (
    AliasRegistry,
    EchoMessageAdapter,
    FallbackManager,
    ModelCallResult,
    TokenUsage,
)
from octoagent.skills import SkillExecutionContext, SkillManifest, SkillRunner, SkillRunStatus
from octoagent.tooling.models import ToolProfile
from pydantic import BaseModel, Field

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

    _SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING: bool = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if getattr(cls, "_SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING", False):
            return
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

    _SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING = True

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

    _SUPPRESS_LLM_PROVIDER_DEPRECATION_WARNING = True

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


class _GenericSkillInput(BaseModel):
    """普通聊天接 skill runner 的最小输入模型。"""

    objective: str = Field(min_length=1)


class _GenericSkillOutput(BaseModel):
    """SkillRunner 普通聊天输出模型。"""

    content: str = ""
    complete: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    skip_remaining_tools: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


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
        *,
        skill_runner: SkillRunner | None = None,
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
        self._skill_runner = skill_runner

    def register(self, alias: str, provider: LLMProvider) -> None:
        """注册 LLM provider -- M0 兼容"""
        self._providers[alias] = provider

    async def call(
        self,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str | None = None,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        worker_capability: str | None = None,
        tool_profile: str | None = None,
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

        skill_result = await self._try_call_with_tools(
            prompt_or_messages=prompt_or_messages,
            model_alias=resolved_alias,
            task_id=task_id,
            trace_id=trace_id,
            metadata=metadata or {},
            worker_capability=worker_capability or "llm_generation",
            tool_profile=tool_profile or "standard",
        )
        if skill_result is not None:
            return skill_result

        # 通过 FallbackManager 调用
        return await self._fallback_manager.call_with_fallback(
            messages=messages,
            model_alias=resolved_alias,
        )

    async def _try_call_with_tools(
        self,
        *,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str,
        task_id: str | None,
        trace_id: str | None,
        metadata: dict[str, Any],
        worker_capability: str,
        tool_profile: str,
    ) -> ModelCallResult | None:
        if self._skill_runner is None or not task_id or not trace_id:
            return None

        selected_tools = self._parse_selected_tools(metadata)
        if not selected_tools:
            return None

        conversation_messages = self._coerce_messages(prompt_or_messages)
        prompt = self._coerce_prompt(prompt_or_messages)
        if not prompt:
            return None

        try:
            profile = ToolProfile(str(tool_profile).strip().lower() or ToolProfile.STANDARD)
        except ValueError:
            profile = ToolProfile.STANDARD

        worker_type = self._normalize_worker_type(metadata.get("selected_worker_type", ""))
        manifest = SkillManifest(
            skill_id=f"chat.{worker_type}.inline",
            input_model=_GenericSkillInput,
            output_model=_GenericSkillOutput,
            model_alias=model_alias,
            description=self._build_skill_description(worker_type, selected_tools),
            tools_allowed=selected_tools,
            tool_profile=profile,
        )
        execution_context = SkillExecutionContext(
            task_id=task_id,
            trace_id=trace_id,
            caller=f"worker:{worker_type}",
            agent_runtime_id=str(metadata.get("agent_runtime_id", "")).strip(),
            agent_session_id=str(metadata.get("agent_session_id", "")).strip(),
            work_id=str(metadata.get("work_id", "")).strip(),
            conversation_messages=conversation_messages,
            metadata=metadata,
        )

        try:
            result = await self._skill_runner.run(
                manifest=manifest,
                execution_context=execution_context,
                skill_input={"objective": prompt},
                prompt=prompt,
            )
        except Exception:
            return None

        if result.status != SkillRunStatus.SUCCEEDED or result.output is None:
            return None

        meta = result.output.metadata
        usage = meta.get("token_usage", {}) if isinstance(meta, dict) else {}
        return ModelCallResult(
            content=result.output.content,
            model_alias=model_alias,
            model_name=str(meta.get("model_name", "")) if isinstance(meta, dict) else "",
            provider=str(meta.get("provider", "")) if isinstance(meta, dict) else "",
            duration_ms=result.duration_ms,
            token_usage=TokenUsage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                total_tokens=int(usage.get("total_tokens", 0) or 0),
            ),
            cost_usd=float(meta.get("cost_usd", 0.0) or 0.0) if isinstance(meta, dict) else 0.0,
            cost_unavailable=bool(meta.get("cost_unavailable", True))
            if isinstance(meta, dict)
            else True,
            is_fallback=False,
            fallback_reason="",
        )

    @staticmethod
    def _parse_selected_tools(metadata: dict[str, Any]) -> list[str]:
        tool_selection = metadata.get("tool_selection")
        if isinstance(tool_selection, dict):
            mounted_tools = tool_selection.get("mounted_tools")
            if isinstance(mounted_tools, list):
                normalized = [str(item).strip() for item in mounted_tools if str(item).strip()]
                if normalized:
                    return normalized
            effective_tool_universe = tool_selection.get("effective_tool_universe")
            if isinstance(effective_tool_universe, dict):
                selected_tools = effective_tool_universe.get("selected_tools")
                if isinstance(selected_tools, list):
                    normalized = [
                        str(item).strip() for item in selected_tools if str(item).strip()
                    ]
                    if normalized:
                        return normalized
        raw = metadata.get("selected_tools_json", "[]")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        try:
            payload = json.loads(str(raw or "[]"))
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        return [str(item).strip() for item in payload if str(item).strip()]

    @staticmethod
    def _coerce_messages(
        prompt_or_messages: str | list[dict[str, str]],
    ) -> list[dict[str, str]]:
        if isinstance(prompt_or_messages, str):
            content = prompt_or_messages.strip()
            return [{"role": "user", "content": content}] if content else []

        normalized: list[dict[str, str]] = []
        for item in prompt_or_messages:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            role = str(item.get("role", "user")).strip().lower() or "user"
            if role not in {"system", "user", "assistant"}:
                role = "user"
            normalized.append({"role": role, "content": content})
        return normalized

    @classmethod
    def _coerce_prompt(cls, prompt_or_messages: str | list[dict[str, str]]) -> str:
        if isinstance(prompt_or_messages, str):
            return prompt_or_messages.strip()
        messages = cls._coerce_messages(prompt_or_messages)
        for item in reversed(messages):
            if item["role"] == "user":
                return item["content"]
        parts = [item["content"] for item in messages]
        return "\n\n".join(parts).strip()

    @staticmethod
    def _normalize_worker_type(value: Any) -> str:
        raw = str(value).strip().lower()
        if raw in {"ops", "research", "dev", "general"}:
            return raw
        return "general"

    @staticmethod
    def _build_skill_description(worker_type: str, selected_tools: list[str]) -> str:
        tool_list = ", ".join(selected_tools)
        return (
            f"你是 OctoAgent 的 {worker_type} worker。"
            f" 当前会话已经接入以下工具：{tool_list}。"
            " 当用户问题需要 project/session/runtime 等事实时，优先调用工具核实。"
            " 不要声称自己没有工具；只在确实没有必要时直接回答。"
            " 如果工具已经返回足够证据，直接输出结论；"
            "不要把“我先查一下”“我再看看”“接下来我会”这类计划句当成最终答复。"
            " 完成后用自然语言给出最终答复。"
        )
