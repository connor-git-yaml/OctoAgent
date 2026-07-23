"""共享 Provider bootstrap 逻辑。

供 `octo config init` 与 `octo onboard` 复用，避免两条入口各自维护
默认 Provider / alias 初始值。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    TelegramChannelConfig,
)
from octoagent.gateway.services.config.config_wizard import save_config
from pydantic import BaseModel

PromptFunc = Callable[[str, str], str]
ChoicePromptFunc = Callable[[str, list[str], str], str]


class ConfigBootstrapResult(BaseModel):
    """共享 bootstrap 执行结果。"""

    config: OctoAgentConfig
    source: Literal["interactive", "echo"]
    changed: bool = True


class ConfigBootstrapError(ValueError):
    """bootstrap 输入非法。"""


_LEGACY_RUNTIME_FILES = (
    (".env.litellm", "LEGACY_LITELLM_ENV_FILE_FOUND"),
    ("litellm-config.yaml", "LEGACY_LITELLM_CONFIG_FOUND"),
)


def _existing_legacy_runtime_files(project_root: Path) -> tuple[str, ...]:
    return tuple(
        filename
        for filename, _error_code in _LEGACY_RUNTIME_FILES
        if (project_root / filename).exists()
    )


def detect_legacy_runtime_files(project_root: Path) -> None:
    """仅按文件名检测退役 runtime 文件，不读取其内容。"""
    existing = set(_existing_legacy_runtime_files(project_root))
    for filename, error_code in _LEGACY_RUNTIME_FILES:
        if filename in existing:
            raise ConfigBootstrapError(error_code)


def build_canonical_reauth_config[RecoveryResult](
    project_root: Path,
    *,
    build: Callable[[], RecoveryResult],
    on_legacy: Callable[[str], None],
) -> RecoveryResult:
    """忽略退役文件内容并继续构造canonical recovery状态。"""
    for filename in _existing_legacy_runtime_files(project_root):
        on_legacy(filename)
    return build()


def validate_loaded_environment(environment: Mapping[str, str]) -> None:
    """在 dotenv 与 process env 合并后拒绝退役 runtime 输入。"""
    for key in (
        "LITELLM_PROXY_URL",
        "LITELLM_PROXY_KEY",
        "LITELLM_MASTER_KEY",
        "LITELLM_PORT",
        "OCTOAGENT_WORKER_DOCKER_MODE",
        "OCTOAGENT_WORKER_DOCKER_INFO_CHECK",
    ):
        if key in environment:
            raise ConfigBootstrapError(f"RUNTIME_CONFIG_RETIRED: {key}")


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
_CUSTOM_PROVIDER_CHOICE = "custom"


def list_bootstrap_provider_choices() -> list[str]:
    """返回首次使用支持的 provider 预设列表。"""
    return [*list(_BOOTSTRAP_PRESETS.keys()), _CUSTOM_PROVIDER_CHOICE]


def build_bootstrap_config_for_provider(
    provider_choice: str,
    *,
    provider_id: str | None = None,
    provider_name: str | None = None,
    api_key_env: str | None = None,
    base_url: str = "",
    main_model: str | None = None,
    cheap_model: str | None = None,
    enable_telegram: bool = False,
    telegram_mode: Literal["webhook", "polling"] = "polling",
    telegram_webhook_url: str = "",
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN",
    telegram_webhook_secret_env: str = "",
) -> OctoAgentConfig:
    """按指定 provider 预设构建最小可用配置。"""
    if provider_choice == _CUSTOM_PROVIDER_CHOICE:
        resolved_provider_id = str(provider_id or "").strip().lower()
        if not resolved_provider_id:
            raise ConfigBootstrapError("custom provider 需要 provider_id")
        resolved_provider_name = str(provider_name or "").strip() or resolved_provider_id
        resolved_api_key_env = str(api_key_env or "").strip() or (
            f"{resolved_provider_id.upper().replace('-', '_')}_API_KEY"
        )
        resolved_main_model = str(main_model or "").strip()
        resolved_cheap_model = str(cheap_model or "").strip() or resolved_main_model
        if not resolved_main_model:
            raise ConfigBootstrapError("custom provider 需要 main_model")
        try:
            provider_entry = ProviderEntry(
                id=resolved_provider_id,
                name=resolved_provider_name,
                auth_type="api_key",
                api_key_env=resolved_api_key_env,
                base_url=base_url.strip(),
            )
        except Exception as exc:
            raise ConfigBootstrapError(f"Provider 配置无效：{exc}") from exc

        default_aliases = {
            "main": ModelAlias(
                provider=resolved_provider_id,
                model=resolved_main_model,
                description="主力模型别名",
            ),
            "cheap": ModelAlias(
                provider=resolved_provider_id,
                model=resolved_cheap_model,
                description="低成本模型别名（用于 octo doctor --live ping）",
            ),
        }
        config = OctoAgentConfig(
            updated_at=date.today().isoformat(),
            providers=[provider_entry],
            model_aliases=default_aliases,
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

    preset = _BOOTSTRAP_PRESETS.get(provider_choice)
    if preset is None:
        raise ConfigBootstrapError(f"不支持的 provider 预设：{provider_choice}")

    final_provider_name = provider_name or preset.provider_name
    final_api_key_env = api_key_env or preset.api_key_env

    try:
        provider_entry = ProviderEntry(
            id=preset.provider_id,
            name=final_provider_name,
            auth_type=preset.auth_type,
            api_key_env=final_api_key_env,
        )
    except Exception as exc:
        raise ConfigBootstrapError(f"Provider 配置无效：{exc}") from exc

    default_aliases = {
        "main": ModelAlias(
            provider=preset.provider_id,
            model=preset.main_model,
            description=preset.main_description,
            thinking_level=preset.main_thinking,
        ),
        "cheap": ModelAlias(
            provider=preset.provider_id,
            model=preset.cheap_model,
            description=preset.cheap_description,
            thinking_level=preset.cheap_thinking,
        ),
    }

    config = OctoAgentConfig(
        updated_at=date.today().isoformat(),
        providers=[provider_entry],
        model_aliases=default_aliases,
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
        config = OctoAgentConfig(updated_at=date.today().isoformat())
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

    if prompt is None or choice_prompt is None:
        raise ConfigBootstrapError("interactive bootstrap需要显式prompt与choice_prompt")
    prompt_func = prompt
    choice_func = choice_prompt
    provider_choice = choice_func(
        "Provider 预设（openrouter / openai / openai-codex / anthropic / custom）",
        list_bootstrap_provider_choices(),
        "openrouter",
    )
    if provider_choice == _CUSTOM_PROVIDER_CHOICE:
        provider_id = prompt_func("Provider ID（如 siliconflow / ollama）", "custom-provider")
        normalized_provider_id = provider_id.strip().lower()
        provider_name = prompt_func(
            "Provider 显示名称",
            normalized_provider_id or "Custom Provider",
        )
        api_key_env_default = (
            f"{normalized_provider_id.upper().replace('-', '_')}_API_KEY"
            if normalized_provider_id
            else "CUSTOM_PROVIDER_API_KEY"
        )
        api_key_env = prompt_func(
            f"凭证环境变量名（如 {api_key_env_default}）",
            api_key_env_default,
        )
        base_url = prompt_func(
            "API Base URL（如 https://api.siliconflow.cn/v1）",
            "",
        )
        main_model = prompt_func("main 别名模型名", "")
        cheap_model = prompt_func("cheap 别名模型名（留空沿用 main）", main_model)
        return build_bootstrap_config_for_provider(
            provider_choice,
            provider_id=normalized_provider_id,
            provider_name=provider_name,
            api_key_env=api_key_env,
            base_url=base_url,
            main_model=main_model,
            cheap_model=cheap_model,
            enable_telegram=enable_telegram,
            telegram_mode=telegram_mode,
            telegram_webhook_url=telegram_webhook_url,
            telegram_bot_token_env=telegram_bot_token_env,
            telegram_webhook_secret_env=telegram_webhook_secret_env,
        )

    preset = _BOOTSTRAP_PRESETS[provider_choice]
    provider_name = prompt_func("Provider 显示名称", preset.provider_name)
    api_key_env = prompt_func(
        f"凭证环境变量名（如 {preset.api_key_env}）",
        preset.api_key_env,
    )
    return build_bootstrap_config_for_provider(
        provider_choice,
        provider_name=provider_name,
        api_key_env=api_key_env,
        enable_telegram=enable_telegram,
        telegram_mode=telegram_mode,
        telegram_webhook_url=telegram_webhook_url,
        telegram_bot_token_env=telegram_bot_token_env,
        telegram_webhook_secret_env=telegram_webhook_secret_env,
    )


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
    # Feature 081 P4：不再生成 litellm-config.yaml（Provider 直连）
    return ConfigBootstrapResult(
        config=config,
        source="echo" if echo else "interactive",
        changed=True,
    )
