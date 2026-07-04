"""Feature 065: LLM 服务公共工具 -- Protocol / JSON 解析 / 模型别名解析。

消除 ConsolidationService / DerivedExtractionService / FlushPromptInjector
三个文件中的重复定义。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

_log = structlog.get_logger()


@runtime_checkable
class LlmServiceProtocol(Protocol):
    """LLM 服务的最小接口契约。

    契约方法是 ``call``（gateway ``LLMService.call`` 的签名子集，返回带
    ``.content`` 的 ModelCallResult）。注意**不是** ``FallbackManager.call_with_fallback``
    ——历史上本协议曾误声明为 ``call_with_fallback``，而全部消费方调用的是
    ``.call``，叠加 harness 一处误注入裸 FallbackManager，导致 memory 巩固/画像/
    派生/ToM 四条管线在生产静默 AttributeError（e051bd4b 修过一次方向相反的
    同类漂移）。正确注入对象是 gateway ``LLMService``（内部包装 FallbackManager）。
    构造期用 ``ensure_llm_call_contract`` 守卫，误接线在启动即 fail-fast。
    """

    async def call(
        self,
        prompt_or_messages: str | list[dict[str, str]],
        model_alias: str | None = None,
        **kwargs: Any,
    ) -> Any: ...


def ensure_llm_call_contract(llm_service: Any, *, owner: str) -> None:
    """构造期契约守卫：llm_service 必须实现 ``call``（或为 None 表示降级）。

    F108b W7 先例：wiring 契约违规属构造期错误，fail-fast 前移到 TypeError，
    而非运行到 cron 深处才 AttributeError。裸 ``FallbackManager``（只有
    ``call_with_fallback``）是历史误注入形态，在此显式拦截。
    """
    if llm_service is None:
        return
    if not callable(getattr(llm_service, "call", None)):
        raise TypeError(
            f"{owner} 需要实现 LlmServiceProtocol.call 的 llm_service"
            f"（如 gateway LLMService），实际得到 {type(llm_service).__name__}；"
            "若手头只有 FallbackManager，请注入包装它的 LLMService。"
        )


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
        if isinstance(parsed, list):
            return parsed
        # JSON object 包裹了数组的情况
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, list):
                    return v
        return None
    except (json.JSONDecodeError, ValueError):
        pass

    # 回退：用正则从文本中提取 JSON 数组
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def resolve_default_model_alias(project_root: Path) -> str:
    """从项目配置中读取默认的 reasoning 模型别名。

    Returns:
        模型别名字符串，默认 "main"。
    """
    try:
        from octoagent.gateway.services.config.config_wizard import load_config

        config = load_config(project_root)
        return (config.memory.reasoning_model_alias if config else "") or "main"
    except Exception:
        return "main"
