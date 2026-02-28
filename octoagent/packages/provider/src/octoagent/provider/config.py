"""ProviderConfig -- Provider 配置加载

对齐 data-model.md SS2.5 + contracts/provider-api.md SS8。
从环境变量加载配置，不硬编码 provider/模型名。
"""

import os
from typing import Literal

import structlog
from pydantic import BaseModel, Field, SecretStr

log = structlog.get_logger()


class ProviderConfig(BaseModel):
    """Provider 包配置 -- 从环境变量加载

    环境变量:
        LITELLM_PROXY_URL: Proxy 地址（默认 http://localhost:4000）
        LITELLM_PROXY_KEY: Proxy 访问密钥
        OCTOAGENT_LLM_MODE: LLM 运行模式（litellm/echo）
        OCTOAGENT_LLM_TIMEOUT_S: 调用超时（秒，默认 30）
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


def load_provider_config() -> ProviderConfig:
    """从环境变量加载 Provider 配置

    环境变量映射:
        LITELLM_PROXY_URL -> proxy_base_url (默认 "http://localhost:4000")
        LITELLM_PROXY_KEY -> proxy_api_key (默认 "")
        OCTOAGENT_LLM_MODE -> llm_mode (默认 "litellm")
        OCTOAGENT_LLM_TIMEOUT_S -> timeout_s (默认 30)

    Returns:
        ProviderConfig 实例
    """
    kwargs: dict = {}

    if val := os.environ.get("LITELLM_PROXY_URL"):
        kwargs["proxy_base_url"] = val

    if val := os.environ.get("LITELLM_PROXY_KEY"):
        kwargs["proxy_api_key"] = SecretStr(val)

    if val := os.environ.get("OCTOAGENT_LLM_MODE"):
        kwargs["llm_mode"] = val

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
