"""Feature 065: LLM 服务公共工具 -- Protocol / JSON 解析 / 模型别名解析。

消除 ConsolidationService / DerivedExtractionService / FlushPromptInjector
三个文件中的重复定义。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

_log = structlog.get_logger()


@runtime_checkable
class LlmServiceProtocol(Protocol):
    """LLM 服务的最小接口契约。"""

    async def call_with_fallback(
        self,
        messages: list[dict[str, str]],
        model_alias: str = "main",
        **kwargs: Any,
    ) -> Any: ...


def parse_llm_json_array(text: str) -> list[dict[str, Any]] | None:
    """从 LLM 响应中解析 JSON 数组。

    处理 markdown code block 包裹和常见格式问题。

    Returns:
        解析后的 JSON 数组，格式错误时返回 None。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        start = 1
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[start:end])
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else None
    except (json.JSONDecodeError, ValueError):
        return None


def resolve_default_model_alias(project_root: Path) -> str:
    """从项目配置中读取默认的 reasoning 模型别名。

    Returns:
        模型别名字符串，默认 "main"。
    """
    try:
        from .config_wizard import load_config

        config = load_config(project_root)
        return (config.memory.reasoning_model_alias if config else "") or "main"
    except Exception:
        return "main"
