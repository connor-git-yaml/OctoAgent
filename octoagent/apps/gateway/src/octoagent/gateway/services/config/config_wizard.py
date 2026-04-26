"""config_wizard.py -- 增量读写引擎 -- Feature 014

提供 octoagent.yaml 的读取、增量更新、原子写入功能。
关键特性：
- 原子写入（写临时文件 + os.replace，NFR-003）
- 非破坏性更新（先读后 patch，FR-010）
- 凭证泄露检测（NFR-004）
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import structlog

from .config_schema import (
    ConfigParseError,
    CredentialLeakError,
    ModelAlias,
    OctoAgentConfig,
    ProviderEntry,
    ProviderNotFoundError,
    detect_legacy_yaml_keys,
)

log = structlog.get_logger()

# octoagent.yaml 的标准文件名
OCTOAGENT_YAML_NAME = "octoagent.yaml"


def _resolve_yaml_path(project_root: Path) -> Path:
    """解析 octoagent.yaml 的完整路径"""
    return project_root / OCTOAGENT_YAML_NAME


def _atomic_write(path: Path, content: str) -> None:
    """原子写入文件（写临时文件 + os.replace，NFR-003）"""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def load_config(project_root: Path) -> OctoAgentConfig | None:
    """读取并校验 octoagent.yaml

    Args:
        project_root: 项目根目录（octoagent.yaml 所在目录）

    Returns:
        OctoAgentConfig 实例，若文件不存在返回 None

    Raises:
        ConfigParseError: YAML 语法错误或 schema 校验失败（EC-1）
    """
    yaml_path = _resolve_yaml_path(project_root)
    try:
        text = yaml_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.debug("octoagent_yaml_not_found", path=str(yaml_path))
        return None
    except OSError as exc:
        raise ConfigParseError(
            message=f"无法读取 octoagent.yaml：{exc}",
            field_path="(file)",
        ) from exc

    # Feature 081 P2：在 raw YAML 层做 legacy schema 检测（修 Codex F2）。
    # 必须在 Pydantic 解析之前——一旦经过 model_validate 旧字段会被默认值填充，
    # 无法区分用户实际配过 vs 用了默认值。
    try:
        import yaml as _yaml  # 局部 import，避免污染模块顶层

        raw = _yaml.safe_load(text)
        legacy_keys = detect_legacy_yaml_keys(raw)
        if legacy_keys:
            log.warning(
                "octoagent_yaml_legacy_schema_detected",
                path=str(yaml_path),
                keys=legacy_keys,
                hint="请运行 `octo config migrate-080` 升级到新 schema（v2）",
            )
    except Exception as exc:  # 检测失败不应影响主流程
        log.debug(
            "legacy_yaml_detection_skipped",
            error_type=type(exc).__name__,
        )

    return OctoAgentConfig.from_yaml(text)


def save_config(config: OctoAgentConfig, project_root: Path) -> None:
    """原子写入 octoagent.yaml

    先写临时文件，再 os.replace 原子替换，防止写入中断导致配置损坏（NFR-003）。
    写入前执行凭证泄露检测（NFR-004，C5 必经防线）。

    Args:
        config: 要写入的配置对象
        project_root: 项目根目录

    Raises:
        CredentialLeakError: 检测到明文凭证（写入被阻止）
    """
    # 凭证安全检测（必经路径，不依赖调用方记得调用）
    validate_no_plaintext_credentials(config)

    yaml_path = _resolve_yaml_path(project_root)
    try:
        _atomic_write(yaml_path, config.to_yaml())
    except OSError as exc:
        raise OSError(f"写入 octoagent.yaml 失败：{exc}") from exc

    log.debug("octoagent_yaml_saved", path=str(yaml_path))


def wizard_update_provider(
    config: OctoAgentConfig,
    entry: ProviderEntry,
    overwrite: bool = False,
    preserve_existing_base_url: bool = True,
) -> tuple[OctoAgentConfig, bool]:
    """增量添加/更新 Provider 条目

    先读现有配置，patch 后写回，不清空其他字段（FR-010，非破坏性）。

    Args:
        config: 当前配置对象
        entry: 要添加/更新的 ProviderEntry
        overwrite: 若为 True，同 id 存在时覆盖；否则跳过（不修改）
        preserve_existing_base_url: 更新时 incoming.base_url 为空是否保留已有值

    Returns:
        (更新后的 OctoAgentConfig, 是否实际修改了配置)
    """
    existing_provider = next((p for p in config.providers if p.id == entry.id), None)
    if existing_provider is not None:
        if not overwrite:
            # 不覆盖，直接返回原配置
            log.debug(
                "provider_already_exists_skip",
                provider_id=entry.id,
            )
            return config, False
        merged_entry = merge_provider_entry(
            existing_provider,
            entry,
            preserve_existing_base_url=preserve_existing_base_url,
        )
        new_providers = [p if p.id != entry.id else merged_entry for p in config.providers]
    else:
        new_providers = list(config.providers) + [entry]

    updated = config.model_copy(
        update={
            "providers": new_providers,
            "updated_at": date.today().isoformat(),
        }
    )
    return updated, True


def merge_provider_entry(
    existing: ProviderEntry,
    incoming: ProviderEntry,
    *,
    preserve_existing_base_url: bool = True,
) -> ProviderEntry:
    """合并 Provider 更新，避免省略可选字段时误清空已有值。"""
    merged = incoming.model_dump(mode="python")
    if preserve_existing_base_url and not str(merged.get("base_url", "")).strip():
        merged["base_url"] = existing.base_url
    return existing.model_copy(update=merged)


def wizard_update_model(
    config: OctoAgentConfig,
    alias: str,
    model_alias: ModelAlias,
) -> OctoAgentConfig:
    """更新或新建 model alias 条目

    Args:
        config: 当前配置对象
        alias: 别名键（如 'main'、'cheap'）
        model_alias: 新的 ModelAlias 对象

    Returns:
        更新后的 OctoAgentConfig
    """
    new_aliases = dict(config.model_aliases)
    new_aliases[alias] = model_alias

    return config.model_copy(
        update={
            "model_aliases": new_aliases,
            "updated_at": date.today().isoformat(),
        }
    )


def wizard_disable_provider(
    config: OctoAgentConfig,
    provider_id: str,
) -> OctoAgentConfig:
    """将 Provider.enabled 设为 False

    不删除配置条目，保持可逆性（Constitution C7 User-in-Control）。

    Args:
        config: 当前配置对象
        provider_id: 要禁用的 Provider ID

    Returns:
        更新后的 OctoAgentConfig

    Raises:
        ProviderNotFoundError: provider_id 不存在时
    """
    provider = config.get_provider(provider_id)
    if provider is None:
        raise ProviderNotFoundError(
            f"Provider '{provider_id}' 不存在，无法禁用。"
            f"可用的 Provider: {[p.id for p in config.providers]}"
        )

    new_providers = [
        p.model_copy(update={"enabled": False}) if p.id == provider_id else p
        for p in config.providers
    ]

    return config.model_copy(
        update={
            "providers": new_providers,
            "updated_at": date.today().isoformat(),
        }
    )


def validate_no_plaintext_credentials(config: OctoAgentConfig) -> None:
    """校验配置中不含明文凭证

    检查 api_key_env 字段格式（不含 '=' 号，不是疑似密钥值）。
    违反则抛出 CredentialLeakError（NFR-004）。

    Args:
        config: 待校验的配置对象

    Raises:
        CredentialLeakError: 检测到可能的明文凭证
    """
    for provider in config.providers:
        env_name = provider.api_key_env
        # 检查 '=' 号（如误写 KEY=value）
        if "=" in env_name:
            raise CredentialLeakError(
                f"providers[{provider.id}].api_key_env 包含 '=' 号，"
                f"疑似明文凭证。请只填写环境变量名（如 'OPENROUTER_API_KEY'），"
                f"不要填写 'KEY=value' 格式的完整赋值语句。"
            )
        # 检查疑似 API Key 值（以 sk- 开头等常见模式）
        if env_name.startswith(("sk-", "key-", "Bearer ", "token_")):
            raise CredentialLeakError(
                f"providers[{provider.id}].api_key_env 疑似包含明文 API Key"
                f"（值以 'sk-'/'key-' 等开头）。"
                f"请只填写环境变量名（如 'OPENROUTER_API_KEY'），不要填写密钥值本身。"
            )
