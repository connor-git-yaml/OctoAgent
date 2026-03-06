"""共享 provider/runtime bootstrap 逻辑。

供 `octo config init` 与 `octo onboard` 复用，避免两条入口各自维护
默认 Provider / alias / runtime 初始值。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

import click
from pydantic import BaseModel

from .config_schema import ModelAlias, OctoAgentConfig, ProviderEntry, RuntimeConfig
from .config_wizard import save_config
from .litellm_generator import generate_litellm_config

PromptFunc = Callable[[str, str], str]


class ConfigBootstrapResult(BaseModel):
    """共享 bootstrap 执行结果。"""

    config: OctoAgentConfig
    source: Literal["interactive", "echo"]
    changed: bool = True


class ConfigBootstrapError(ValueError):
    """bootstrap 输入非法。"""


def _default_prompt(text: str, *, default: str) -> str:
    return str(click.prompt(text, default=default))


def build_bootstrap_config(
    *,
    echo: bool = False,
    prompt: PromptFunc | None = None,
) -> OctoAgentConfig:
    """构建首次使用所需的最小合法配置。"""
    if echo:
        return OctoAgentConfig(
            updated_at=date.today().isoformat(),
            runtime=RuntimeConfig(llm_mode="echo"),
        )

    prompt_func = prompt or (lambda text, default: _default_prompt(text, default=default))
    provider_id = prompt_func(
        "Provider ID（如 openrouter / anthropic / openai）",
        "openrouter",
    )
    provider_name = prompt_func("Provider 显示名称", provider_id.title())
    api_key_env = prompt_func(
        "凭证环境变量名（如 OPENROUTER_API_KEY）",
        f"{provider_id.upper()}_API_KEY",
    )

    try:
        provider_entry = ProviderEntry(
            id=provider_id,
            name=provider_name,
            auth_type="api_key",
            api_key_env=api_key_env,
        )
    except Exception as exc:
        raise ConfigBootstrapError(f"Provider 配置无效：{exc}") from exc

    default_aliases = {
        "main": ModelAlias(
            provider=provider_id,
            model=f"{provider_id}/auto",
            description="主力模型别名",
        ),
        "cheap": ModelAlias(
            provider=provider_id,
            model=f"{provider_id}/auto",
            description="低成本模型别名（用于 octo doctor --live ping）",
        ),
    }

    return OctoAgentConfig(
        updated_at=date.today().isoformat(),
        providers=[provider_entry],
        model_aliases=default_aliases,
        runtime=RuntimeConfig(llm_mode="litellm"),
    )


def bootstrap_config(
    project_root: Path,
    *,
    echo: bool = False,
    prompt: PromptFunc | None = None,
) -> ConfigBootstrapResult:
    """构建并持久化 bootstrap 配置。"""
    config = build_bootstrap_config(echo=echo, prompt=prompt)
    save_config(config, project_root)
    generate_litellm_config(config, project_root)
    return ConfigBootstrapResult(
        config=config,
        source="echo" if echo else "interactive",
        changed=True,
    )
