"""litellm_generator.py -- LiteLLM 配置推导引擎 -- Feature 014

从 OctoAgentConfig 推导生成 litellm-config.yaml 和 .env.litellm。
关键特性：
- 仅处理 enabled=True 的 Provider（FR-005）
- 校验失败时不覆盖现有文件（FR-006）
- 原子写入（NFR-003）
- api_key 格式：os.environ/{api_key_env}（与 init_wizard.py 一致）
- 日志脱敏：API Key 明文不进入 structlog（NFR-004）
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
import yaml

from ..reasoning_support import supports_reasoning
from .config_schema import (
    THINKING_BUDGET_TOKENS,
    OctoAgentConfig,
    normalize_provider_model_string,
)
from .config_wizard import _atomic_write

log = structlog.get_logger()

# litellm-config.yaml 标准文件名
LITELLM_CONFIG_NAME = "litellm-config.yaml"

# .env.litellm 标准文件名
ENV_LITELLM_NAME = ".env.litellm"

# 工具生成标记注释（用于检测是否为本工具生成，EC-3）
GENERATED_MARKER = "# 由 octo config sync 自动生成，请勿手动修改"


def build_litellm_config_dict(config: OctoAgentConfig) -> dict:
    """构建 LiteLLM 配置字典（纯函数，不写文件）

    供 generate_litellm_config 和 --dry-run 共用，避免重复实现。

    Args:
        config: 已解析并校验的 OctoAgentConfig

    Returns:
        完整的 litellm-config.yaml 数据结构（dict）
    """
    enabled_provider_ids = {p.id for p in config.providers if p.enabled}
    model_list: list[dict] = []
    for alias_key, alias_val in config.model_aliases.items():
        if alias_val.provider not in enabled_provider_ids:
            log.debug(
                "skip_alias_disabled_provider",
                alias=alias_key,
                provider=alias_val.provider,
            )
            continue
        provider_entry = config.get_provider(alias_val.provider)
        if provider_entry is None:
            log.warning("provider_not_found_skip", alias=alias_key, provider=alias_val.provider)
            continue
        normalized_model = normalize_provider_model_string(
            provider_entry.id,
            alias_val.model,
        )
        litellm_params: dict = {
            "model": normalized_model,
            "api_key": f"os.environ/{provider_entry.api_key_env}",
        }
        if alias_val.thinking_level is not None and supports_reasoning(
            provider_entry.id, normalized_model
        ):
            budget = THINKING_BUDGET_TOKENS[alias_val.thinking_level]
            litellm_params["thinking"] = {"type": "enabled", "budget_tokens": budget}
        elif alias_val.thinking_level is not None:
            log.info(
                "skip_unsupported_reasoning_config",
                alias=alias_key,
                provider=provider_entry.id,
                model=normalized_model,
                thinking_level=alias_val.thinking_level,
            )
        # 自定义 base_url（如硅基流动 https://api.siliconflow.cn/v1）
        if provider_entry.base_url:
            litellm_params["api_base"] = provider_entry.base_url
        # OAuth Provider：注入 api_base 和 headers（如 openai-codex → chatgpt.com/backend-api）
        if provider_entry.auth_type == "oauth":
            from ..auth.oauth_flows import extract_account_id_from_jwt
            from ..auth.oauth_provider import BUILTIN_PROVIDERS

            oauth_cfg = BUILTIN_PROVIDERS.get(provider_entry.id)
            if oauth_cfg:
                if oauth_cfg.api_base_url:
                    litellm_params["api_base"] = oauth_cfg.api_base_url
                # ChatGPT Backend API 的 Responses 路径要求 store=false 且 stream=true
                # 使用 extra_body 确保字段写入请求体（而非被 LiteLLM 拦截为自有参数）
                litellm_params["extra_body"] = {"store": False, "stream": True}
                if oauth_cfg.extra_api_headers:
                    # 从环境变量读取 JWT，提取 account_id 以替换占位符
                    jwt = os.environ.get(provider_entry.api_key_env, "")
                    account_id = extract_account_id_from_jwt(jwt) if jwt else ""
                    litellm_params["headers"] = {
                        k: v.replace("{account_id}", account_id or "")
                        for k, v in oauth_cfg.extra_api_headers.items()
                    }
            else:
                log.warning(
                    "oauth_provider_not_in_builtin",
                    provider_id=provider_entry.id,
                    hint="Provider 未在 BUILTIN_PROVIDERS 中注册，跳过 api_base/headers 注入",
                )
        model_list.append({"model_name": alias_key, "litellm_params": litellm_params})
    return {
        "model_list": model_list,
        "general_settings": {
            "master_key": f"os.environ/{config.runtime.master_key_env}",
        },
        "litellm_settings": {
            "drop_params": True,
        },
    }


def generate_litellm_config(
    config: OctoAgentConfig,
    project_root: Path,
) -> Path:
    """从 enabled Providers 和 model_aliases 生成 litellm-config.yaml

    生成规则：
    - 仅处理 model_aliases 中 provider 对应 enabled=True 的条目
    - api_key 字段格式：os.environ/{api_key_env}
    - general_settings.master_key 引用 runtime.master_key_env
    - 头部注释：GENERATED_MARKER
    - 现有文件非本工具生成时打印警告（EC-3）
    - 校验失败时不写文件，现有文件保持不变（FR-006）

    Args:
        config: 已解析并校验的 OctoAgentConfig
        project_root: 项目根目录

    Returns:
        写入成功的 litellm-config.yaml 路径
    """
    litellm_path = project_root / LITELLM_CONFIG_NAME

    # 检查现有文件是否非本工具生成（EC-3）
    if litellm_path.exists():
        try:
            existing_content = litellm_path.read_text(encoding="utf-8")
            if GENERATED_MARKER not in existing_content:
                log.warning(
                    "litellm_config_manual_override_warning",
                    path=str(litellm_path),
                    message="litellm-config.yaml 似乎不是由本工具生成（缺少标记注释），"
                    "即将覆盖。建议将自定义配置迁移到 octoagent.yaml。",
                )
        except OSError:
            pass

    # 加载 .env.litellm 以确保 OAuth JWT 可被 extract_account_id_from_jwt 读取
    env_litellm_path = project_root / ENV_LITELLM_NAME
    if env_litellm_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_litellm_path, override=False)
        except ImportError:
            log.debug("dotenv_not_available_skip_load")

    litellm_config = build_litellm_config_dict(config)

    # 生成 YAML 内容（带标记注释头）
    content = GENERATED_MARKER + "\n" + yaml.dump(
        litellm_config,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )

    # 原子写入
    _atomic_write(litellm_path, content)

    log.debug(
        "litellm_config_generated",
        path=str(litellm_path),
        model_count=len(litellm_config["model_list"]),
        enabled_providers=[p.id for p in config.providers if p.enabled],
    )

    return litellm_path


def generate_env_litellm(
    provider_id: str,
    api_key: str,
    env_var_name: str,
    project_root: Path,
) -> Path:
    """追加/更新 .env.litellm 中的 API Key 条目

    凭证处理原则（NFR-004）：
    - api_key 明文仅在此函数内接触，不进入 OctoAgentConfig
    - 日志中脱敏（sk-***）

    Args:
        provider_id: Provider ID（仅用于日志）
        api_key: API Key 明文值
        env_var_name: 环境变量名（如 "OPENROUTER_API_KEY"）
        project_root: 项目根目录

    Returns:
        .env.litellm 文件路径
    """
    env_path = project_root / ENV_LITELLM_NAME

    # 日志脱敏
    masked_key = api_key[:3] + "***" if len(api_key) > 3 else "***"
    log.debug(
        "generate_env_litellm",
        provider_id=provider_id,
        env_var=env_var_name,
        api_key_masked=masked_key,
    )

    # 读取现有内容（不存在时创建空内容）
    if env_path.exists():
        try:
            existing = env_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    else:
        existing = ""

    # 更新或追加指定环境变量（保留其他行不变）
    lines = existing.splitlines(keepends=True)
    new_line = f"{env_var_name}={api_key}\n"
    found = False
    new_lines: list[str] = []

    for line in lines:
        # 匹配 VAR_NAME= 开头的行（精确匹配键名）
        if line.startswith(f"{env_var_name}=") or line.startswith(f"{env_var_name} ="):
            new_lines.append(new_line)
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(new_line)

    # 原子写入
    _atomic_write(env_path, "".join(new_lines))

    log.debug("env_litellm_updated", path=str(env_path), env_var=env_var_name)
    return env_path


def check_litellm_sync_status(
    config: OctoAgentConfig,
    project_root: Path,
) -> tuple[bool, list[str]]:
    """检查 octoagent.yaml 和 litellm-config.yaml 是否一致

    用于 octo doctor 调用（FR-013）。
    对比维度：model_name 集合和 api_key 格式是否匹配。

    Args:
        config: 已解析的 OctoAgentConfig
        project_root: 项目根目录

    Returns:
        (is_in_sync, diff_messages)
        - is_in_sync: True 表示一致，False 表示不一致（EC-4）
        - diff_messages: 不一致的具体说明
    """
    litellm_path = project_root / LITELLM_CONFIG_NAME

    if not litellm_path.exists():
        return False, [f"{LITELLM_CONFIG_NAME} 不存在，请运行 octo config sync 生成。"]

    try:
        content = litellm_path.read_text(encoding="utf-8")
        existing = yaml.safe_load(content)
    except Exception as exc:
        return False, [f"{LITELLM_CONFIG_NAME} 读取失败：{exc}"]

    if not isinstance(existing, dict):
        return False, [f"{LITELLM_CONFIG_NAME} 格式不是合法的 YAML 映射。"]

    # 构建期望的 model_name 集合（只含 enabled Provider 的 alias）
    enabled_provider_ids = {p.id for p in config.providers if p.enabled}
    expected_model_names: set[str] = set()
    for alias_key, alias_val in config.model_aliases.items():
        if alias_val.provider in enabled_provider_ids:
            expected_model_names.add(alias_key)

    # 从现有文件中提取 model_name 集合
    existing_model_list = existing.get("model_list", [])
    if not isinstance(existing_model_list, list):
        return False, ["litellm-config.yaml 中 model_list 不是列表格式。"]

    existing_model_names: set[str] = {
        m.get("model_name", "") for m in existing_model_list if isinstance(m, dict)
    }

    diffs: list[str] = []

    # 比较 model_name 集合
    missing = expected_model_names - existing_model_names
    extra = existing_model_names - expected_model_names

    if missing:
        diffs.append(
            f"octoagent.yaml 中有以下 alias 未同步到 litellm-config.yaml：{sorted(missing)}"
        )
    if extra:
        diffs.append(
            f"litellm-config.yaml 中有以下条目在 octoagent.yaml 中不存在：{sorted(extra)}"
        )

    # 验证 api_key 引用格式（非明文）
    for model_entry in existing_model_list:
        if not isinstance(model_entry, dict):
            continue
        params = model_entry.get("litellm_params", {})
        api_key_val = params.get("api_key", "")
        if api_key_val and not str(api_key_val).startswith("os.environ/"):
            model_name = model_entry.get("model_name", "?")
            diffs.append(
                f"litellm-config.yaml 中 '{model_name}' 的 api_key 不是 os.environ/ 格式，"
                f"疑似明文凭证，请运行 octo config sync 重新生成。"
            )

    is_in_sync = len(diffs) == 0
    return is_in_sync, diffs
