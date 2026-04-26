"""litellm_generator.py -- LiteLLM 配置推导引擎 -- Feature 014

.. deprecated:: Feature 081 P3b
    此模块**所有 export 函数已退化为 no-op stub**——保接口签名让调用方继续 import
    成功，但内部不再生成 ``litellm-config.yaml`` / ``.env.litellm``，也不再做 sync
    检查。Provider 直连后这些操作已无意义。

    P4 将连同所有调用方一起 ``git rm``：
    - ``setup_service.py`` / ``mcp_service.py`` / ``config_tools.py``
    - ``dx/install_bootstrap.py`` / ``dx/config_bootstrap.py`` / ``dx/config_commands.py``
    - ``dx/onboarding_service.py`` / ``dx/doctor.py``

历史职责（Feature 080 之前）：
- 从 OctoAgentConfig 推导生成 litellm-config.yaml 和 .env.litellm
- 校验失败时不覆盖现有文件
- 原子写入

历史导出已全部 no-op 化；GENERATED_MARKER / LITELLM_CONFIG_NAME / ENV_LITELLM_NAME
保留为字符串常量，避免调用方还在用作引用时崩溃。
"""

from __future__ import annotations

from pathlib import Path

import structlog

from .config_schema import OctoAgentConfig

log = structlog.get_logger()

# litellm-config.yaml 标准文件名（保留常量供测试/路径策略仍可引用）
LITELLM_CONFIG_NAME = "litellm-config.yaml"

# .env.litellm 标准文件名
ENV_LITELLM_NAME = ".env.litellm"

# 工具生成标记注释（保留供调用方继续可引用，no-op 后不再写入实际文件）
GENERATED_MARKER = "# 由 octo config sync 自动生成，请勿手动修改"


def build_litellm_config_dict(config: OctoAgentConfig) -> dict:
    """Feature 081 P3b：no-op stub。返回空 dict，不再构建 LiteLLM 配置。

    P4 删除本模块时调用方需同步清理。
    """
    log.debug("litellm_generator_noop", function="build_litellm_config_dict")
    return {"model_list": [], "general_settings": {}, "litellm_settings": {}}


def generate_litellm_config(
    config: OctoAgentConfig,
    project_root: Path,
) -> Path:
    """Feature 081 P3b：no-op stub。

    历史行为：写 litellm-config.yaml 到 project_root。
    现在：仅返回 will-be 路径，不写文件——Provider 直连后无需此衍生配置。
    P4 删除本模块时调用方需改成不再调用本函数。
    """
    log.debug("litellm_generator_noop", function="generate_litellm_config")
    return project_root / LITELLM_CONFIG_NAME


def generate_env_litellm(
    provider_id: str,
    api_key: str,
    env_var_name: str,
    project_root: Path,
) -> Path:
    """Feature 081 P3b：no-op stub。

    历史行为：把 API key 追加到 .env.litellm。
    现在：返回 will-be 路径，不写文件——新 setup wizard 应改写到 .env。
    P4 删除本模块时调用方需改成 .env / credential store 路径。
    """
    log.debug("litellm_generator_noop", function="generate_env_litellm")
    return project_root / ENV_LITELLM_NAME


def check_litellm_sync_status(
    config: OctoAgentConfig,
    project_root: Path,
) -> tuple[bool, list[str]]:
    """Feature 081 P3b：no-op stub。

    历史行为：对比 octoagent.yaml 与 litellm-config.yaml 是否同步。
    现在：总是返回 (True, [])——Provider 直连后无 litellm-config.yaml 需要同步。
    P4 删除本模块时调用方（doctor / onboarding_service）改成不调用本函数。
    """
    log.debug("litellm_generator_noop", function="check_litellm_sync_status")
    return True, []
