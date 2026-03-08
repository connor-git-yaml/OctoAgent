"""共享 provider/runtime bootstrap 逻辑。

供 `octo config init` 与 `octo onboard` 复用，避免两条入口各自维护
默认 Provider / alias / runtime 初始值。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
ChoicePromptFunc = Callable[[str, list[str], str], str]


class ConfigBootstrapResult(BaseModel):
    """共享 bootstrap 执行结果。"""

    config: OctoAgentConfig
    source: Literal["interactive", "echo"]
    changed: bool = True


class ConfigBootstrapError(ValueError):
    """bootstrap 输入非法。"""


def _default_prompt(text: str, *, default: str) -> str:
    return str(click.prompt(text, default=default))


def _default_choice_prompt(text: str, *, choices: list[str], default: str) -> str:
    return str(click.prompt(text, type=click.Choice(choices), default=default))


@dataclass(frozen=True)
class _ProviderBootstrapPreset:
    provider_id: str
    provider_name: str
    auth_type: Literal["api_key", "oauth"]
    api_key_env: str
    main_model: str
    cheap_model: str
    main_description: str
    cheap_description: str
    main_thinking: Literal["xhigh", "high", "medium", "low"] | None = None
    cheap_thinking: Literal["xhigh", "high", "medium", "low"] | None = None


_BOOTSTRAP_PRESETS: dict[str, _ProviderBootstrapPreset] = {
    "openrouter": _ProviderBootstrapPreset(
        provider_id="openrouter",
        provider_name="OpenRouter",
        auth_type="api_key",
        api_key_env="OPENROUTER_API_KEY",
        main_model="openrouter/auto",
        cheap_model="openrouter/auto",
        main_description="主力模型别名",
        cheap_description="低成本模型别名（用于 octo doctor --live ping）",
    ),
    "openai": _ProviderBootstrapPreset(
        provider_id="openai",
        provider_name="OpenAI",
        auth_type="api_key",
        api_key_env="OPENAI_API_KEY",
        main_model="openai/auto",
        cheap_model="openai/auto",
        main_description="主力模型别名",
        cheap_description="低成本模型别名（用于 octo doctor --live ping）",
    ),
    "openai-codex": _ProviderBootstrapPreset(
        provider_id="openai-codex",
        provider_name="OpenAI Codex (ChatGPT Pro OAuth)",
        auth_type="oauth",
        api_key_env="OPENAI_API_KEY",
        main_model="gpt-5.4",
        cheap_model="gpt-5.4",
        main_description="主力模型（GPT-5.4，深度推理）",
        cheap_description="低成本模型（GPT-5.4，轻量推理）",
        main_thinking="xhigh",
        cheap_thinking="low",
    ),
    "anthropic": _ProviderBootstrapPreset(
        provider_id="anthropic",
        provider_name="Anthropic",
        auth_type="api_key",
        api_key_env="ANTHROPIC_API_KEY",
        main_model="anthropic/auto",
        cheap_model="anthropic/auto",
        main_description="主力模型别名",
        cheap_description="低成本模型别名（用于 octo doctor --live ping）",
    ),
}


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
    choice_prompt: ChoicePromptFunc | None = None,
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
    choice_func = choice_prompt or (
        lambda text, choices, default: _default_choice_prompt(text, choices=choices, default=default)
    )
    provider_choice = choice_func(
        "Provider 预设（openrouter / openai / openai-codex / anthropic）",
        list(_BOOTSTRAP_PRESETS.keys()),
        "openrouter",
    )
    preset = _BOOTSTRAP_PRESETS[provider_choice]
    provider_id = preset.provider_id
    provider_name = prompt_func("Provider 显示名称", preset.provider_name)
    api_key_env = prompt_func(
        f"凭证环境变量名（如 {preset.api_key_env}）",
        preset.api_key_env,
    )

    try:
        provider_entry = ProviderEntry(
            id=provider_id,
            name=provider_name,
            auth_type=preset.auth_type,
            api_key_env=api_key_env,
        )
    except Exception as exc:
        raise ConfigBootstrapError(f"Provider 配置无效：{exc}") from exc

    default_aliases = {
        "main": ModelAlias(
            provider=provider_id,
            model=preset.main_model,
            description=preset.main_description,
            thinking_level=preset.main_thinking,
        ),
        "cheap": ModelAlias(
            provider=provider_id,
            model=preset.cheap_model,
            description=preset.cheap_description,
            thinking_level=preset.cheap_thinking,
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
    choice_prompt: ChoicePromptFunc | None = None,
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
        choice_prompt=choice_prompt,
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
