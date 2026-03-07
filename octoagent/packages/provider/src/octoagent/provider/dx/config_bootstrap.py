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

from .config_schema import (
    ChannelsConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
    TelegramChannelConfig,
)
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


def apply_telegram_channel_config(
    config: OctoAgentConfig,
    *,
    enabled: bool,
    mode: Literal["webhook", "polling"],
    bot_token_env: str = "TELEGRAM_BOT_TOKEN",
    webhook_url: str = "",
    webhook_secret_env: str = "",
) -> OctoAgentConfig:
    """在统一配置上补齐 Telegram channel 最小配置。"""
    telegram_config = TelegramChannelConfig(
        enabled=enabled,
        mode=mode,
        bot_token_env=bot_token_env,
        webhook_url=webhook_url,
        webhook_secret_env=webhook_secret_env,
    )
    return config.model_copy(
        update={
            "updated_at": date.today().isoformat(),
            "channels": ChannelsConfig(telegram=telegram_config),
        }
    )


def build_bootstrap_config(
    *,
    echo: bool = False,
    prompt: PromptFunc | None = None,
    enable_telegram: bool = False,
    telegram_mode: Literal["webhook", "polling"] = "polling",
    telegram_webhook_url: str = "",
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN",
    telegram_webhook_secret_env: str = "",
) -> OctoAgentConfig:
    """构建首次使用所需的最小合法配置。"""
    if echo:
        config = OctoAgentConfig(
            updated_at=date.today().isoformat(),
            runtime=RuntimeConfig(llm_mode="echo"),
        )
        if enable_telegram:
            return apply_telegram_channel_config(
                config,
                enabled=True,
                mode=telegram_mode,
                bot_token_env=telegram_bot_token_env,
                webhook_url=telegram_webhook_url,
                webhook_secret_env=telegram_webhook_secret_env,
            )
        return config

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

    config = OctoAgentConfig(
        updated_at=date.today().isoformat(),
        providers=[provider_entry],
        model_aliases=default_aliases,
        runtime=RuntimeConfig(llm_mode="litellm"),
    )
    if enable_telegram:
        return apply_telegram_channel_config(
            config,
            enabled=True,
            mode=telegram_mode,
            bot_token_env=telegram_bot_token_env,
            webhook_url=telegram_webhook_url,
            webhook_secret_env=telegram_webhook_secret_env,
        )
    return config


def bootstrap_config(
    project_root: Path,
    *,
    echo: bool = False,
    prompt: PromptFunc | None = None,
    enable_telegram: bool = False,
    telegram_mode: Literal["webhook", "polling"] = "polling",
    telegram_webhook_url: str = "",
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN",
    telegram_webhook_secret_env: str = "",
) -> ConfigBootstrapResult:
    """构建并持久化 bootstrap 配置。"""
    config = build_bootstrap_config(
        echo=echo,
        prompt=prompt,
        enable_telegram=enable_telegram,
        telegram_mode=telegram_mode,
        telegram_webhook_url=telegram_webhook_url,
        telegram_bot_token_env=telegram_bot_token_env,
        telegram_webhook_secret_env=telegram_webhook_secret_env,
    )
    save_config(config, project_root)
    generate_litellm_config(config, project_root)
    return ConfigBootstrapResult(
        config=config,
        source="echo" if echo else "interactive",
        changed=True,
    )
