"""Feature 080 Phase 1：Provider transport 协议枚举。

LLM 调用协议层抽象。每个 transport 对应一个 HTTP 请求构建/响应解析路径，
让多个 provider（如 SiliconFlow / DeepSeek / OpenAI）可以共享同一个 transport
实现，避免笛卡尔爆炸。

设计参考：
- Hermes Agent 的 ``transport`` 字段路由（``openai_chat`` / ``anthropic_messages``
  / ``codex_responses``）
- Pydantic AI 的 Provider 抽象但只暴露 3 个最常用 transport（不 35 个 provider 一一封装）

Scope（本 Feature 080）：
- ``openai_chat``：覆盖 OpenAI Chat Completions 兼容 provider（最广）
- ``openai_responses``：OpenAI Responses API + ChatGPT Pro Codex
- ``anthropic_messages``：Anthropic Claude Messages API + Claude OAuth

后续 Feature 可扩展 ``bedrock_converse`` / ``google_gemini`` / ``vertex`` 等。
"""

from __future__ import annotations

from enum import Enum


class ProviderTransport(str, Enum):
    """LLM 调用协议类型。同一个 transport 可被多个 provider 共享。"""

    OPENAI_CHAT = "openai_chat"
    """OpenAI Chat Completions API（``POST {api_base}/v1/chat/completions``）。

    覆盖：OpenAI、SiliconFlow、DeepSeek、Groq、OpenRouter、Together AI、Mistral、
    本地 vLLM / Ollama 兼容等所有声明 OpenAI 兼容的 provider。
    流式 SSE，tool_call 按 index 累积，usage 在最后一个 chunk。
    """

    OPENAI_RESPONSES = "openai_responses"
    """OpenAI Responses API（``POST {api_base}/v1/responses``）。

    覆盖：OpenAI 原生 Responses + ChatGPT Pro Codex OAuth（api_base 为
    ``chatgpt.com/backend-api/codex``）。
    流式事件：``response.output_text.delta`` / ``response.output_item.added`` 等，
    tool_call 通过 ``function_call`` item / ``function_call_output`` 配对。
    """

    ANTHROPIC_MESSAGES = "anthropic_messages"
    """Anthropic Claude Messages API（``POST {api_base}/v1/messages``）。

    覆盖：Anthropic Claude API + Claude Pro/Max OAuth（含 ``anthropic-beta:
    oauth-2025-04-20`` 头）。
    协议差异：``messages`` 不含 ``role: "system"``（system 单独走 ``system`` 顶层
    字段）；``tools`` 用 ``{name, description, input_schema}``；流式事件
    ``message_start`` / ``content_block_*`` / ``message_delta``；usage 在 ``message_delta``。
    """


__all__ = ["ProviderTransport"]
