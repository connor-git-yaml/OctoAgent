"""LiteLLM 运行时配置读取辅助。

.. deprecated:: Feature 081 P3b
    此模块**所有 export 函数已退化为 no-op stub**——保接口签名让调用方继续 import
    成功，但内部不再读取 ``litellm-config.yaml``，也不再做 alias 推断。
    Provider 直连后 alias → backend 由 ``ProviderRouter`` / ``ProviderEntry.transport``
    直接表达，本模块的"反向推断"无意义。

    P4 将连同所有调用方（``main.py`` 已在 P1 解耦；``dx/doctor.py`` 在 P4 一起删）
    一起 ``git rm``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from octoagent.provider.models import ReasoningConfig

log = structlog.get_logger()


def resolve_codex_backend_aliases(project_root: Path) -> set[str]:
    """Feature 081 P3b：no-op stub。返回空集合。

    历史行为：扫描 octoagent.yaml + litellm-config.yaml 推断哪些 alias 命中 Codex
    backend。Provider 直连后 transport=openai_responses 直接表达此意图。
    """
    log.debug("litellm_runtime_noop", function="resolve_codex_backend_aliases")
    return set()


def alias_uses_codex_backend(project_root: Path, alias_name: str) -> bool:
    """Feature 081 P3b：no-op stub。总是返回 False。

    替代方案：调用方应改用 ProviderEntry.transport == "openai_responses" 判断。
    """
    log.debug("litellm_runtime_noop", function="alias_uses_codex_backend")
    return False


def resolve_responses_api_direct_params(
    project_root: Path,
) -> dict[str, dict[str, Any]]:
    """Feature 081 P3b：no-op stub。返回空 dict。

    历史行为：从 litellm-config.yaml 提取 Responses API 直连参数（绕过 Proxy）。
    Provider 直连后所有调用都已直连，无需特殊参数提取。
    """
    log.debug("litellm_runtime_noop", function="resolve_responses_api_direct_params")
    return {}


def resolve_codex_reasoning_aliases(project_root: Path) -> dict[str, ReasoningConfig]:
    """Feature 081 P3b：no-op stub。返回空 dict。

    历史行为：从 octoagent.yaml 抽取 Codex 别名的 reasoning 默认值（thinking_level
    → ReasoningConfig）。Provider 直连后 ProviderClient 内部直接处理 reasoning。
    """
    log.debug("litellm_runtime_noop", function="resolve_codex_reasoning_aliases")
    return {}


def resolve_reasoning_supported_aliases(project_root: Path) -> set[str]:
    """Feature 081 P3b：no-op stub。返回空集合。

    历史行为：检测哪些 alias 的 provider/model 支持 reasoning。
    Provider 直连后由 ProviderClient.supports_reasoning 直接判断，不再需要预扫。
    """
    log.debug("litellm_runtime_noop", function="resolve_reasoning_supported_aliases")
    return set()
