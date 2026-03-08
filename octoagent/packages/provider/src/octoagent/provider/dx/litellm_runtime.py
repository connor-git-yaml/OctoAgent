"""LiteLLM 运行时配置读取辅助。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from ..models import ReasoningConfig
from ..auth.oauth_provider import BUILTIN_PROVIDERS
from .config_wizard import load_config
from .litellm_generator import LITELLM_CONFIG_NAME

log = structlog.get_logger()
_CODEX_BACKEND_API_BASE = (
    BUILTIN_PROVIDERS["openai-codex"].api_base_url or ""
).rstrip("/")


def _load_litellm_config(project_root: Path) -> dict[str, Any] | None:
    """读取 litellm-config.yaml，失败时返回 None。"""
    litellm_path = project_root / LITELLM_CONFIG_NAME
    if not litellm_path.exists():
        return None

    try:
        data = yaml.safe_load(litellm_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning(
            "litellm_runtime_config_read_failed",
            path=str(litellm_path),
            error_type=type(exc).__name__,
        )
        return None

    if not isinstance(data, dict):
        log.warning(
            "litellm_runtime_config_invalid",
            path=str(litellm_path),
            actual_type=type(data).__name__,
        )
        return None
    return data


def resolve_codex_backend_aliases(project_root: Path) -> set[str]:
    """解析命中 Codex backend 路由的 model aliases。"""
    aliases: set[str] = set()

    try:
        cfg = load_config(project_root)
    except Exception as exc:
        log.warning(
            "codex_backend_aliases_config_invalid_fallback",
            error_type=type(exc).__name__,
        )
        cfg = None

    if cfg is not None:
        for alias_name, alias_cfg in cfg.model_aliases.items():
            provider = cfg.get_provider(alias_cfg.provider)
            if (
                provider is not None
                and provider.enabled
                and provider.auth_type == "oauth"
                and provider.id == "openai-codex"
            ):
                aliases.add(alias_name)

    litellm_config = _load_litellm_config(project_root)
    if litellm_config is None:
        return aliases

    model_list = litellm_config.get("model_list", [])
    if not isinstance(model_list, list):
        log.warning(
            "litellm_runtime_model_list_invalid",
            path=str(project_root / LITELLM_CONFIG_NAME),
            actual_type=type(model_list).__name__,
        )
        return aliases

    for model_entry in model_list:
        if not isinstance(model_entry, dict):
            continue
        model_name = model_entry.get("model_name")
        params = model_entry.get("litellm_params")
        if not isinstance(model_name, str) or not model_name:
            continue
        if not isinstance(params, dict):
            continue
        api_base = params.get("api_base")
        if isinstance(api_base, str) and api_base.rstrip("/") == _CODEX_BACKEND_API_BASE:
            aliases.add(model_name)

    return aliases


def alias_uses_codex_backend(project_root: Path, alias_name: str) -> bool:
    """判断指定 alias 是否命中 Codex backend 路由。"""
    return alias_name in resolve_codex_backend_aliases(project_root)


def resolve_codex_reasoning_aliases(project_root: Path) -> dict[str, ReasoningConfig]:
    """解析 Codex backend alias 的默认 reasoning 配置。"""
    try:
        cfg = load_config(project_root)
    except Exception as exc:
        log.warning(
            "codex_reasoning_aliases_config_invalid",
            error_type=type(exc).__name__,
        )
        return {}

    if cfg is None:
        return {}

    result: dict[str, ReasoningConfig] = {}
    for alias_name, alias_cfg in cfg.model_aliases.items():
        provider = cfg.get_provider(alias_cfg.provider)
        if (
            provider is None
            or not provider.enabled
            or provider.auth_type != "oauth"
            or provider.id != "openai-codex"
            or alias_cfg.thinking_level is None
        ):
            continue
        result[alias_name] = ReasoningConfig(effort=alias_cfg.thinking_level)
    return result
