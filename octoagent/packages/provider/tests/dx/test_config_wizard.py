"""config_wizard.py 单元测试 -- Feature 014

覆盖：
- load_config 文件不存在返回 None
- load_config YAML 语法错误时 ConfigParseError（EC-1）
- save_config 原子写入（验证临时文件不残留，原子替换后内容正确）
- wizard_update_provider 新增 Provider
- wizard_update_provider 重复添加同 id 时 overwrite=False 不覆盖
- wizard_disable_provider 设 enabled=False
- validate_no_plaintext_credentials 检测 api_key_env 格式异常
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octoagent.provider.dx.config_schema import (
    ConfigParseError,
    CredentialLeakError,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    ProviderNotFoundError,
)
from octoagent.provider.dx.config_wizard import (
    load_config,
    save_config,
    validate_no_plaintext_credentials,
    wizard_disable_provider,
    wizard_update_model,
    wizard_update_provider,
)

# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _make_provider(provider_id: str = "openrouter") -> ProviderEntry:
    """构造最小合法 ProviderEntry"""
    names = {"openrouter": "OpenRouter", "anthropic": "Anthropic", "openai": "OpenAI"}
    envs = {
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    return ProviderEntry(
        id=provider_id,
        name=names.get(provider_id, provider_id.title()),
        auth_type="api_key",
        api_key_env=envs.get(provider_id, f"{provider_id.upper()}_API_KEY"),
    )


def _make_config(providers: list[ProviderEntry] | None = None) -> OctoAgentConfig:
    """构造最小合法配置"""
    if providers is None:
        providers = [_make_provider("openrouter")]
    return OctoAgentConfig(
        updated_at="2026-03-04",
        providers=providers,
    )


# ---------------------------------------------------------------------------
# load_config 测试
# ---------------------------------------------------------------------------


def test_load_config_not_exist(tmp_path: Path) -> None:
    """文件不存在时返回 None"""
    result = load_config(tmp_path)
    assert result is None


def test_load_config_valid(tmp_path: Path) -> None:
    """正常文件返回 OctoAgentConfig"""
    config = _make_config()
    save_config(config, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded.providers[0].id == "openrouter"


def test_load_config_invalid_yaml(tmp_path: Path) -> None:
    """YAML 语法错误时抛出 ConfigParseError（EC-1）"""
    bad_yaml = "config_version: :\n  broken: yaml: :"
    (tmp_path / "octoagent.yaml").write_text(bad_yaml, encoding="utf-8")
    with pytest.raises(ConfigParseError) as exc_info:
        load_config(tmp_path)
    # 错误信息应包含字段路径
    assert exc_info.value.field_path != ""


def test_load_config_schema_error(tmp_path: Path) -> None:
    """schema 校验失败时抛出 ConfigParseError"""
    invalid_yaml = """
config_version: 1
updated_at: "2026-03-04"
providers:
  - id: openrouter
    name: OpenRouter
    auth_type: INVALID_TYPE
    api_key_env: OPENROUTER_API_KEY
"""
    (tmp_path / "octoagent.yaml").write_text(invalid_yaml, encoding="utf-8")
    with pytest.raises(ConfigParseError):
        load_config(tmp_path)


# ---------------------------------------------------------------------------
# save_config 原子写入测试
# ---------------------------------------------------------------------------


def test_save_config_creates_file(tmp_path: Path) -> None:
    """save_config 成功写入文件"""
    config = _make_config()
    save_config(config, tmp_path)
    yaml_path = tmp_path / "octoagent.yaml"
    assert yaml_path.exists()


def test_save_config_no_tmp_residue(tmp_path: Path) -> None:
    """save_config 完成后不残留 .yaml.tmp 临时文件（NFR-003）"""
    config = _make_config()
    save_config(config, tmp_path)
    tmp_file = tmp_path / "octoagent.yaml.tmp"
    assert not tmp_file.exists(), "临时文件不应残留"


def test_save_config_content_correct(tmp_path: Path) -> None:
    """save_config 写入内容正确，往返读取一致"""
    config = _make_config()
    save_config(config, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded is not None
    assert loaded.providers[0].id == config.providers[0].id
    assert loaded.providers[0].api_key_env == config.providers[0].api_key_env


def test_save_config_overwrites_existing(tmp_path: Path) -> None:
    """save_config 覆盖已有文件时内容正确"""
    config1 = _make_config([_make_provider("openrouter")])
    save_config(config1, tmp_path)

    config2 = _make_config([_make_provider("openrouter"), _make_provider("anthropic")])
    save_config(config2, tmp_path)

    loaded = load_config(tmp_path)
    assert loaded is not None
    assert len(loaded.providers) == 2


# ---------------------------------------------------------------------------
# wizard_update_provider 测试
# ---------------------------------------------------------------------------


def test_wizard_update_provider_new(tmp_path: Path) -> None:
    """新增 Provider 成功"""
    config = _make_config([])  # 空配置
    new_entry = _make_provider("openrouter")
    updated, changed = wizard_update_provider(config, new_entry)
    assert changed is True
    assert len(updated.providers) == 1
    assert updated.providers[0].id == "openrouter"


def test_wizard_update_provider_duplicate_no_overwrite() -> None:
    """重复 id 时 overwrite=False 不覆盖（FR-010 非破坏性）"""
    config = _make_config([_make_provider("openrouter")])
    # 试图用不同 name 添加同 id
    duplicate = ProviderEntry(
        id="openrouter",
        name="OpenRouter MODIFIED",
        auth_type="api_key",
        api_key_env="OPENROUTER_API_KEY",
    )
    updated, changed = wizard_update_provider(config, duplicate, overwrite=False)
    assert changed is False
    # 名称未被修改
    assert updated.providers[0].name == "OpenRouter"


def test_wizard_update_provider_duplicate_with_overwrite() -> None:
    """重复 id 时 overwrite=True 正确覆盖"""
    config = _make_config([_make_provider("openrouter")])
    modified = ProviderEntry(
        id="openrouter",
        name="OpenRouter v2",
        auth_type="api_key",
        api_key_env="OPENROUTER_NEW_KEY",
    )
    updated, changed = wizard_update_provider(config, modified, overwrite=True)
    assert changed is True
    assert updated.providers[0].name == "OpenRouter v2"
    assert updated.providers[0].api_key_env == "OPENROUTER_NEW_KEY"


def test_wizard_update_provider_preserves_other_providers() -> None:
    """新增 Provider 时不影响其他已有 Provider"""
    config = _make_config([_make_provider("openrouter")])
    new_entry = _make_provider("anthropic")
    updated, _ = wizard_update_provider(config, new_entry)
    ids = [p.id for p in updated.providers]
    assert "openrouter" in ids
    assert "anthropic" in ids


# ---------------------------------------------------------------------------
# wizard_update_model 测试
# ---------------------------------------------------------------------------


def test_wizard_update_model_new_alias() -> None:
    """新建 model alias"""
    config = _make_config()
    alias = ModelAlias(provider="openrouter", model="openrouter/auto", description="测试别名")
    updated = wizard_update_model(config, "test_alias", alias)
    assert "test_alias" in updated.model_aliases
    assert updated.model_aliases["test_alias"].model == "openrouter/auto"


def test_wizard_update_model_overwrite_existing() -> None:
    """覆盖已有 model alias"""
    config = _make_config()
    config = wizard_update_model(
        config,
        "main",
        ModelAlias(provider="openrouter", model="openrouter/gpt4", description="更新后"),
    )
    assert config.model_aliases["main"].model == "openrouter/gpt4"


def test_wizard_update_model_normalizes_routed_provider_model_string() -> None:
    config = _make_config()
    config = wizard_update_model(
        config,
        "cheap",
        ModelAlias(provider="openrouter", model="qwen/qwen3.5-9b", description="轻量模型"),
    )

    assert config.model_aliases["cheap"].model == "openrouter/qwen/qwen3.5-9b"


# ---------------------------------------------------------------------------
# wizard_disable_provider 测试
# ---------------------------------------------------------------------------


def test_wizard_disable_provider_sets_enabled_false() -> None:
    """设 enabled=False（不删除条目）"""
    config = _make_config([_make_provider("openrouter")])
    assert config.providers[0].enabled is True

    updated = wizard_disable_provider(config, "openrouter")
    assert updated.providers[0].enabled is False
    # 条目仍然存在（可逆）
    assert len(updated.providers) == 1


def test_wizard_disable_provider_not_found() -> None:
    """Provider 不存在时抛出 ProviderNotFoundError"""
    config = _make_config([])
    with pytest.raises(ProviderNotFoundError):
        wizard_disable_provider(config, "nonexistent")


def test_wizard_disable_provider_preserves_others() -> None:
    """禁用一个 Provider 时不影响其他 Provider"""
    config = _make_config([_make_provider("openrouter"), _make_provider("anthropic")])
    updated = wizard_disable_provider(config, "openrouter")
    openrouter = updated.get_provider("openrouter")
    anthropic = updated.get_provider("anthropic")
    assert openrouter is not None and openrouter.enabled is False
    assert anthropic is not None and anthropic.enabled is True


# ---------------------------------------------------------------------------
# validate_no_plaintext_credentials 测试
# ---------------------------------------------------------------------------


def test_validate_no_plaintext_credentials_valid() -> None:
    """合法配置不抛出异常"""
    config = _make_config()
    validate_no_plaintext_credentials(config)  # 不应抛出


def test_validate_no_plaintext_credentials_sk_prefix() -> None:
    """api_key_env 以 sk- 开头时抛出 CredentialLeakError"""
    # 绕过 ProviderEntry 的 pattern 校验，直接构造问题情形
    # 注意：正常情况下 ProviderEntry 的 pattern 会阻止 = 号
    # 但对于 sk- 前缀（大写开头的合法环境变量名），需要在 wizard 层检测
    # 测试场景：mock 一个有问题的 provider（绕过 schema 校验）
    from unittest.mock import MagicMock

    mock_provider = MagicMock()
    mock_provider.api_key_env = "sk-abc123"
    mock_provider.id = "bad_provider"

    mock_config = MagicMock()
    mock_config.providers = [mock_provider]

    with pytest.raises(CredentialLeakError):
        validate_no_plaintext_credentials(mock_config)


def test_validate_no_plaintext_credentials_equals_sign() -> None:
    """api_key_env 含 '=' 时抛出 CredentialLeakError（绕过 schema 直接测试 wizard 层）"""
    from unittest.mock import MagicMock

    mock_provider = MagicMock()
    mock_provider.api_key_env = "KEY=value"
    mock_provider.id = "bad_provider"

    mock_config = MagicMock()
    mock_config.providers = [mock_provider]

    with pytest.raises(CredentialLeakError):
        validate_no_plaintext_credentials(mock_config)
