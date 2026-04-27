"""config_schema.py 单元测试 -- Feature 014

覆盖 EC-5（alias 指向 disabled Provider 警告）、
NFR-004（CredentialLeakError）、引用完整性校验、
往返序列化等边界场景。
"""

from __future__ import annotations

import warnings

import pytest
from octoagent.gateway.services.config.config_schema import (
    ChannelsConfig,
    ConfigParseError,
    FrontDoorConfig,
    MemoryConfig,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    RuntimeConfig,
    TelegramChannelConfig,
    build_config_schema_document,
)

# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _make_config(**kwargs: object) -> OctoAgentConfig:
    """构造最小合法配置"""
    defaults = {
        "config_version": 1,
        "updated_at": "2026-03-04",
        "providers": [
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
                enabled=True,
            )
        ],
        "model_aliases": {
            "main": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
                description="主力模型别名",
            )
        },
        "runtime": RuntimeConfig(),
        "channels": ChannelsConfig(),
    }
    defaults.update(kwargs)
    return OctoAgentConfig(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 正常解析测试
# ---------------------------------------------------------------------------


def test_parse_full_config() -> None:
    """正常解析含所有字段的配置。

    F081 cleanup：原 LiteLLM 相关字段（runtime.llm_mode/litellm_proxy_url/
    master_key_env）已全部删除，RuntimeConfig 退化为空块。
    本 test 额外验证 backward-compat 迁移效果——ProviderEntry 用旧 schema
    创建后会自动迁移到 ``auth`` first-class 字段。
    """
    config = _make_config()
    assert config.config_version == 1
    assert len(config.providers) == 1
    assert config.providers[0].id == "openrouter"
    assert "main" in config.model_aliases
    # ProviderEntry 旧字段（auth_type + api_key_env）→ 新字段（auth）自动迁移
    assert config.providers[0].auth is not None
    assert config.providers[0].auth.kind == "api_key"
    assert config.providers[0].auth.env == "OPENROUTER_API_KEY"


def test_model_alias_normalizes_routed_provider_model_string() -> None:
    alias = ModelAlias(
        provider="openrouter",
        model="qwen/qwen3.5-9b",
    )

    assert alias.model == "openrouter/qwen/qwen3.5-9b"


def test_default_values() -> None:
    """缺省字段使用默认值。

    F081 cleanup：``runtime`` 块下原本的 LiteLLM 字段（``llm_mode`` /
    ``litellm_proxy_url`` / ``master_key_env``）已全部删除；``RuntimeConfig``
    退化为空块（schema 占位），运行时不再消费这些字段。
    """
    config = OctoAgentConfig(updated_at="2026-03-04")
    assert config.config_version == 1
    assert config.providers == []
    assert config.model_aliases == {}
    assert config.front_door.mode == "loopback"
    assert config.front_door.bearer_token_env == "OCTOAGENT_FRONTDOOR_TOKEN"
    assert config.channels.telegram.enabled is False
    assert config.channels.telegram.mode == "webhook"
    assert config.channels.telegram.bot_token_env == "TELEGRAM_BOT_TOKEN"
    assert config.memory.reasoning_model_alias == ""


def test_front_door_config_accepts_comma_separated_cidrs() -> None:
    config = FrontDoorConfig(
        mode="trusted_proxy",
        trusted_proxy_cidrs="127.0.0.1/32,10.0.0.0/24",
    )

    assert config.trusted_proxy_cidrs == ["127.0.0.1/32", "10.0.0.0/24"]


def test_front_door_config_rejects_invalid_cidr() -> None:
    with pytest.raises(Exception):
        FrontDoorConfig(
            mode="trusted_proxy",
            trusted_proxy_cidrs=["not-a-cidr"],
        )


# ---------------------------------------------------------------------------
# 引用完整性校验
# ---------------------------------------------------------------------------


def test_alias_references_nonexistent_provider() -> None:
    """model_aliases.provider 引用不存在 Provider 时 ValidationError（EC-5）"""
    with pytest.raises(Exception) as exc_info:
        OctoAgentConfig(
            updated_at="2026-03-04",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                )
            ],
            model_aliases={
                "main": ModelAlias(
                    provider="nonexistent",
                    model="nonexistent/model",
                )
            },
        )
    assert "nonexistent" in str(exc_info.value)


def test_duplicate_provider_ids() -> None:
    """providers 列表中 id 重复时校验失败"""
    with pytest.raises(Exception) as exc_info:
        OctoAgentConfig(
            updated_at="2026-03-04",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                ),
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter Duplicate",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                ),
            ],
        )
    assert "openrouter" in str(exc_info.value)


def test_memory_alias_references_nonexistent_model_alias() -> None:
    with pytest.raises(Exception) as exc_info:
        _make_config(
            memory=MemoryConfig(embedding_model_alias="mem-embed"),
        )

    assert "memory.embedding_model_alias='mem-embed'" in str(exc_info.value)


def test_memory_alias_references_existing_model_alias() -> None:
    config = _make_config(
        model_aliases={
            "main": ModelAlias(
                provider="openrouter",
                model="openrouter/auto",
            ),
            "mem-embed": ModelAlias(
                provider="openrouter",
                model="openrouter/qwen/qwen3-embedding-8b",
            ),
        },
        memory=MemoryConfig(embedding_model_alias="mem-embed"),
    )

    assert config.memory.embedding_model_alias == "mem-embed"


# ---------------------------------------------------------------------------
# 凭证泄露检测（NFR-004）
# ---------------------------------------------------------------------------


def test_api_key_env_with_equals_sign_rejected() -> None:
    """api_key_env 包含 '=' 号时 Pydantic 校验失败（阻止明文凭证写入）"""
    with pytest.raises(Exception):
        ProviderEntry(
            id="openrouter",
            name="OpenRouter",
            auth_type="api_key",
            api_key_env="OPENROUTER_API_KEY=sk-xxx",  # 错误格式
        )


def test_api_key_env_lowercase_rejected() -> None:
    """api_key_env 小写开头时被 pattern 拒绝"""
    with pytest.raises(Exception):
        ProviderEntry(
            id="openrouter",
            name="OpenRouter",
            auth_type="api_key",
            api_key_env="openrouter_api_key",  # 非大写开头
        )


def test_api_key_env_valid_format() -> None:
    """合法的环境变量名通过校验"""
    entry = ProviderEntry(
        id="openrouter",
        name="OpenRouter",
        auth_type="api_key",
        api_key_env="OPENROUTER_API_KEY",
    )
    assert entry.api_key_env == "OPENROUTER_API_KEY"


# ---------------------------------------------------------------------------
# config_version 兼容性（NFR-006 向前兼容）
# ---------------------------------------------------------------------------

# F081 cleanup：删除 test_unknown_config_version_warns ——
# OctoAgentConfig 的 warn_unknown_version model_validator 已被移除。

# ---------------------------------------------------------------------------
# 往返序列化测试
# ---------------------------------------------------------------------------


def test_to_yaml_roundtrip() -> None:
    """to_yaml() / from_yaml() 往返序列化"""
    original = _make_config(
        channels=ChannelsConfig(
            telegram=TelegramChannelConfig(
                enabled=True,
                mode="polling",
                allow_users=["123"],
                allowed_groups=["-1001"],
                group_allow_users=["456"],
                polling_timeout_seconds=10,
            )
        )
    )
    yaml_str = original.to_yaml()
    # 验证包含注释头
    assert "NEVER" in yaml_str
    # 往返解析
    restored = OctoAgentConfig.from_yaml(yaml_str)
    assert restored.config_version == original.config_version
    assert restored.providers[0].id == original.providers[0].id
    assert restored.model_aliases["main"].model == original.model_aliases["main"].model
    assert restored.channels.telegram.enabled is True
    assert restored.channels.telegram.mode == "polling"
    assert restored.channels.telegram.allowed_groups == ["-1001"]


def test_build_config_schema_document_uses_canonical_provider_target_key() -> None:
    config = _make_config(
        providers=[
            ProviderEntry(
                id="anthropic",
                name="Anthropic",
                auth_type="api_key",
                api_key_env="ANTHROPIC_API_KEY",
            )
        ],
        model_aliases={
            "main": ModelAlias(provider="anthropic", model="claude-3-7-sonnet"),
        },
    )

    document = build_config_schema_document(config)
    secret_target = document.ui_hints["fields"]["providers.0.api_key_env"]["secret_target"]

    assert secret_target["target_key"] == "providers.anthropic.api_key_env"
    assert secret_target["target_key_template"] == "providers.{provider_id}.api_key_env"


def test_build_config_schema_document_exposes_provider_base_url_and_memory_step() -> None:
    config = _make_config(
        providers=[
            ProviderEntry(
                id="siliconflow",
                name="SiliconFlow",
                auth_type="api_key",
                api_key_env="SILICONFLOW_API_KEY",
                base_url="https://api.siliconflow.cn/v1",
            )
        ],
        model_aliases={
            "main": ModelAlias(
                provider="siliconflow",
                model="Qwen/Qwen3.5-32B",
            )
        },
    )

    document = build_config_schema_document(config)

    assert "memory" in document.ui_hints["wizard_order"]
    assert "providers.0.base_url" in document.ui_hints["sections"]["provider"]["fields"]
    assert document.ui_hints["fields"]["providers.0.base_url"]["default"] == (
        "https://api.siliconflow.cn/v1"
    )


def test_from_yaml_invalid_syntax() -> None:
    """YAML 语法错误时抛出 ConfigParseError"""
    bad_yaml = "config_version: :\n  broken: yaml: :"
    with pytest.raises(ConfigParseError) as exc_info:
        OctoAgentConfig.from_yaml(bad_yaml)
    assert exc_info.value.field_path == "(root)"


def test_from_yaml_schema_validation_error() -> None:
    """schema 校验失败时抛出 ConfigParseError（含字段路径）"""
    yaml_text = """
config_version: 1
updated_at: "2026-03-04"
providers:
  - id: openrouter
    name: OpenRouter
    auth_type: invalid_type
    api_key_env: OPENROUTER_API_KEY
"""
    with pytest.raises(ConfigParseError) as exc_info:
        OctoAgentConfig.from_yaml(yaml_text)
    # 错误信息中应含字段路径
    assert exc_info.value.field_path != ""


def test_from_yaml_empty_raises() -> None:
    """空文件或非映射 YAML 时抛出 ConfigParseError"""
    with pytest.raises(ConfigParseError):
        OctoAgentConfig.from_yaml("")
    with pytest.raises(ConfigParseError):
        OctoAgentConfig.from_yaml("- item1\n- item2\n")


# ---------------------------------------------------------------------------
# alias 指向 disabled Provider 警告（EC-5）
# ---------------------------------------------------------------------------


def test_validate_alias_disabled_provider() -> None:
    """alias 指向 disabled Provider 时发出 UserWarning"""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        OctoAgentConfig(
            updated_at="2026-03-04",
            providers=[
                ProviderEntry(
                    id="openrouter",
                    name="OpenRouter",
                    auth_type="api_key",
                    api_key_env="OPENROUTER_API_KEY",
                    enabled=False,  # 已禁用
                )
            ],
            model_aliases={
                "main": ModelAlias(
                    provider="openrouter",
                    model="openrouter/auto",
                )
            },
        )
    assert any("已禁用" in str(warning.message) for warning in w)


def test_telegram_webhook_requires_url_when_enabled() -> None:
    """enabled=true 且 mode=webhook 时必须提供 webhook_url。"""
    with pytest.raises(Exception) as exc_info:
        TelegramChannelConfig(enabled=True, mode="webhook")
    assert "webhook_url" in str(exc_info.value)


def test_telegram_numeric_ids_are_normalized_to_strings() -> None:
    """Telegram ID 列表允许 YAML 数字，内部统一转成字符串。"""
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        channels=ChannelsConfig(
            telegram=TelegramChannelConfig(
                enabled=True,
                mode="polling",
                allow_users=[123456],
                allowed_groups=[-1009988],
                group_allow_users=[789],
            )
        ),
    )
    assert config.channels.telegram.allow_users == ["123456"]
    assert config.channels.telegram.allowed_groups == ["-1009988"]
    assert config.channels.telegram.group_allow_users == ["789"]


# ---------------------------------------------------------------------------
# get_provider 辅助方法
# ---------------------------------------------------------------------------


def test_get_provider_found() -> None:
    """get_provider 返回正确的 ProviderEntry"""
    config = _make_config()
    provider = config.get_provider("openrouter")
    assert provider is not None
    assert provider.id == "openrouter"


def test_get_provider_not_found() -> None:
    """get_provider 不存在时返回 None"""
    config = _make_config()
    assert config.get_provider("nonexistent") is None


# ---------------------------------------------------------------------------
# 多 Provider 场景
# ---------------------------------------------------------------------------


def test_multiple_providers_valid() -> None:
    """多个 Provider 正常解析"""
    config = OctoAgentConfig(
        updated_at="2026-03-04",
        providers=[
            ProviderEntry(
                id="openrouter",
                name="OpenRouter",
                auth_type="api_key",
                api_key_env="OPENROUTER_API_KEY",
            ),
            ProviderEntry(
                id="anthropic",
                name="Anthropic",
                auth_type="api_key",
                api_key_env="ANTHROPIC_API_KEY",
            ),
        ],
        model_aliases={
            "main": ModelAlias(provider="openrouter", model="openrouter/auto"),
            "claude": ModelAlias(provider="anthropic", model="claude-opus-4-20250514"),
        },
    )
    assert len(config.providers) == 2
    assert len(config.model_aliases) == 2
