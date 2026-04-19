"""ProviderConfig -- Provider 配置加载

对齐 data-model.md SS2.5 + contracts/provider-api.md SS8。
从环境变量加载配置，不硬编码 provider/模型名。

Feature 014 修改：
- `ProviderConfig` 新增 `config_source` 字段（Constitution C8 可观测性）
- `load_provider_config()` 优先读取 octoagent.yaml runtime 块，降级到环境变量（Q3 决策）
"""

import os
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field, SecretStr

log = structlog.get_logger()


class ProviderConfig(BaseModel):
    """Provider 包配置 -- 从环境变量或 octoagent.yaml 加载

    配置来源优先级（Q3 决策）：
        1. octoagent.yaml runtime 块（若存在且可解析）
        2. 降级：环境变量

    环境变量:
        LITELLM_PROXY_URL: Proxy 地址（默认 http://localhost:4000）
        LITELLM_PROXY_KEY: Proxy 访问密钥
        OCTOAGENT_LLM_MODE: LLM 运行模式（litellm/echo）
        OCTOAGENT_LLM_TIMEOUT_S: 调用超时（秒，默认 30）
        OCTOAGENT_PROJECT_ROOT: 项目根目录（用于查找 octoagent.yaml）
    """

    proxy_base_url: str = Field(
        default="http://localhost:4000",
        description="LiteLLM Proxy 基础 URL",
    )
    proxy_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Proxy 访问密钥（不是 LLM provider API key）",
    )
    llm_mode: Literal["litellm", "echo"] = Field(
        default="litellm",
        description="LLM 运行模式：litellm / echo",
    )
    timeout_s: int = Field(
        default=30,
        ge=1,
        description="LLM 调用超时（秒）",
    )
    config_source: Literal["octoagent_yaml", "env"] = Field(
        default="env",
        description="配置来源：octoagent_yaml（优先）/ env（降级）（Constitution C8 可观测性）",
    )


def _load_from_yaml(project_root: Path, kwargs: dict) -> bool:
    """尝试从 octoagent.yaml runtime 块读取配置

    Args:
        project_root: 项目根目录
        kwargs: 待填充的配置 dict（in-place 修改）

    Returns:
        True 表示成功读取了 yaml 配置，False 表示降级到环境变量
    """
    yaml_path = project_root / "octoagent.yaml"
    if not yaml_path.exists():
        log.debug("octoagent_yaml_not_found_fallback_to_env", path=str(yaml_path))
        return False

    try:
        # config_wizard 已从 provider.dx 迁移到 gateway.services.config；保留旧 import
        # 会静默踩 ModuleNotFoundError 然后走 env fallback 分支，让 yaml 配置被
        # "悄悄忽略"。显式指向新位置。
        from octoagent.gateway.services.config.config_wizard import load_config

        cfg = load_config(project_root)
        if cfg is None:
            return False

        runtime = cfg.runtime
        # 从 runtime 块取值（Q3 决策：yaml 优先）
        kwargs["proxy_base_url"] = runtime.litellm_proxy_url
        kwargs["llm_mode"] = runtime.llm_mode
        # master_key_env 在运行时从环境变量读取实际值（proxy_api_key）
        master_key_env = runtime.master_key_env
        master_key_val = os.environ.get(master_key_env, "")
        if master_key_val:
            kwargs["proxy_api_key"] = SecretStr(master_key_val)

        log.debug(
            "loaded_from_octoagent_yaml",
            llm_mode=runtime.llm_mode,
            proxy_url=runtime.litellm_proxy_url,
        )
        return True

    except Exception as exc:
        # 降级处理：任何读取失败都降级到环境变量，不崩溃（Constitution C6）
        log.debug("octoagent_yaml_read_failed_fallback", error=str(exc))
        return False


def load_provider_config() -> ProviderConfig:
    """从 octoagent.yaml 或环境变量加载 Provider 配置

    配置来源优先级（Q3 决策）：
        1. octoagent.yaml runtime 块（通过 OCTOAGENT_PROJECT_ROOT 或 cwd() 定位）
        2. 降级：环境变量映射

    环境变量映射（降级时使用）:
        LITELLM_PROXY_URL -> proxy_base_url (默认 "http://localhost:4000")
        LITELLM_PROXY_KEY -> proxy_api_key (默认 "")
        OCTOAGENT_LLM_MODE -> llm_mode (默认 "litellm")
        OCTOAGENT_LLM_TIMEOUT_S -> timeout_s (默认 30)

    Returns:
        ProviderConfig 实例（config_source 字段标记配置来源）
    """
    kwargs: dict = {}

    # 确定项目根目录（与 cli-api.md §5 一致）
    env_root = os.environ.get("OCTOAGENT_PROJECT_ROOT")
    project_root = Path(env_root) if env_root else Path.cwd()

    # 优先尝试从 octoagent.yaml 读取
    loaded_from_yaml = _load_from_yaml(project_root, kwargs)

    if loaded_from_yaml:
        kwargs["config_source"] = "octoagent_yaml"
    else:
        # 降级到环境变量读取
        if val := os.environ.get("LITELLM_PROXY_URL"):
            kwargs["proxy_base_url"] = val

        if val := os.environ.get("LITELLM_PROXY_KEY"):
            kwargs["proxy_api_key"] = SecretStr(val)

        if val := os.environ.get("OCTOAGENT_LLM_MODE"):
            kwargs["llm_mode"] = val

        kwargs["config_source"] = "env"

    # timeout 始终从环境变量读取（yaml 中无对应字段）
    if val := os.environ.get("OCTOAGENT_LLM_TIMEOUT_S"):
        try:
            kwargs["timeout_s"] = int(val)
        except ValueError:
            log.warning(
                "invalid_timeout_config",
                env_var="OCTOAGENT_LLM_TIMEOUT_S",
                value=val,
                fallback=30,
            )
            # 使用默认值，不阻塞启动

    return ProviderConfig(**kwargs)
