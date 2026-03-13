"""Reasoning / thinking 能力判定。

采用保守白名单：
- 明确支持的 provider / model 组合才会启用 reasoning/thinking
- 其他组合默认视为不支持，由上层自动忽略该参数

这样可以避免把 thinking / reasoning_effort 盲目透传给不兼容模型。
"""

from __future__ import annotations

import re

_OPENAI_REASONING_PREFIXES = (
    "gpt-5",
    "o1",
    "o3",
    "o4",
)

_OPENROUTER_REASONING_PATTERNS = (
    re.compile(r"(^|/)(deepseek-r1|deepseek-r1-distill)\b"),
    re.compile(r"(^|/)qwq\b"),
    re.compile(r"(^|/)(o1|o3|o4)\b"),
    re.compile(r"(^|/)gpt-5\b"),
    re.compile(r"claude-3\.7-sonnet-thinking"),
    re.compile(r"claude-opus-4-thinking"),
    re.compile(r"gemini-2\.5-(pro|flash-thinking)"),
)


def _normalize(value: str) -> str:
    return value.strip().lower()


def supports_reasoning(provider_id: str, model_name: str) -> bool:
    """判断指定 provider/model 是否应启用 reasoning/thinking。

    这里的目标不是穷举所有兼容模型，而是避免明显不兼容的 alias 继续携带
    reasoning 参数或 LiteLLM `thinking` 配置。
    """

    provider = _normalize(provider_id)
    model = _normalize(model_name)
    if not provider or not model:
        return False

    if provider == "openai-codex":
        return True

    if provider == "openai":
        return model.startswith(_OPENAI_REASONING_PREFIXES)

    if provider == "anthropic":
        return "thinking" in model

    if provider == "openrouter":
        return any(pattern.search(model) for pattern in _OPENROUTER_REASONING_PATTERNS)

    return False
