"""OctoAgent 统一配置 Schema -- Feature 014

定义 octoagent.yaml 的 Pydantic v2 数据模型。
所有 Provider/ModelAlias/RuntimeConfig/OctoAgentConfig 实体均在此文件定义。
对应 FR-001 ~ FR-004，data-model.md 全部实体定义。
"""

from __future__ import annotations

import warnings
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

# octoagent.yaml 文件头注释（不含凭证提示）
_YAML_HEADER = (
    "# OctoAgent 统一配置文件\n"
    "# 此文件安全纳入版本管理（不含凭证）\n"
    "# 凭证由 .env.litellm 管理（已在 .gitignore）\n"
    "# NEVER 在此文件存储 API Key 明文 — 使用 api_key_env 存储环境变量名\n"
    "#\n"
)

# ---------------------------------------------------------------------------
# 辅助错误类型
# ---------------------------------------------------------------------------


class ConfigParseError(ValueError):
    """octoagent.yaml 解析或 schema 校验失败

    Attributes:
        field_path: 错误字段路径（如 "providers[0].auth_type"）
        message: 人类可读错误描述
    """

    def __init__(self, message: str, field_path: str = "") -> None:
        super().__init__(message)
        self.field_path = field_path
        self.message = message


class CredentialLeakError(ValueError):
    """检测到可能的明文凭证写入

    当 api_key_env 字段包含 '=' 号或明显是密钥值时抛出。
    """


class ProviderNotFoundError(KeyError):
    """引用了不存在的 Provider ID"""


# ---------------------------------------------------------------------------
# ProviderEntry — Provider 条目
# ---------------------------------------------------------------------------


class ProviderEntry(BaseModel):
    """Provider 条目 — octoagent.yaml providers[] 列表元素

    不存储凭证值本身，凭证通过 api_key_env 引用环境变量（Constitution C5）。
    """

    id: str = Field(
        description="Provider 全局唯一 ID（如 'openrouter'、'anthropic'）",
        min_length=1,
        pattern=r"^[a-z0-9_-]+$",
    )
    name: str = Field(
        description="Provider 显示名称（如 'OpenRouter'）",
        min_length=1,
    )
    auth_type: Literal["api_key", "oauth"] = Field(
        description="认证类型",
    )
    api_key_env: str = Field(
        description="凭证所在环境变量名（如 'OPENROUTER_API_KEY'）。仅存变量名称，不存实际值。",
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )
    enabled: bool = Field(
        default=True,
        description="是否参与配置生成（False 时不生成 litellm-config 条目）",
    )


# ---------------------------------------------------------------------------
# ModelAlias — 模型别名
# ---------------------------------------------------------------------------

# thinking_level 到 LiteLLM budget_tokens 的映射
# 通过 litellm_params.thinking 传递给 LiteLLM Proxy（由各 Provider 适配）
THINKING_BUDGET_TOKENS: dict[str, int] = {
    "xhigh": 32000,
    "high": 16000,
    "medium": 8000,
    "low": 1024,
}


class ModelAlias(BaseModel):
    """模型别名 — octoagent.yaml model_aliases{} 字典的值

    将用户可见别名（如 'main'、'cheap'）绑定到具体 Provider 和模型字符串。
    别名 key 由外部字典管理，此模型只存储 value。
    """

    provider: str = Field(
        description="关联 ProviderEntry.id（如 'openrouter'）",
        min_length=1,
    )
    model: str = Field(
        description="传递给 LiteLLM 的完整模型字符串（如 'openrouter/auto'、'gpt-4o'）",
        min_length=1,
    )
    description: str = Field(
        default="",
        description="可选的人类可读描述",
    )
    thinking_level: Literal["xhigh", "high", "medium", "low"] | None = Field(
        default=None,
        description=(
            "推理深度级别（可选）：xhigh/high/medium/low。"
            "生成 litellm_params.thinking = {type: enabled, budget_tokens: N}。"
            "xhigh=32000, high=16000, medium=8000, low=1024 tokens。"
        ),
    )


# ---------------------------------------------------------------------------
# RuntimeConfig — 运行时配置
# ---------------------------------------------------------------------------


class RuntimeConfig(BaseModel):
    """运行时配置 — octoagent.yaml runtime 块

    控制 OctoAgent 运行时行为，与 .env 独立。
    运行时优先读此配置，降级到 .env（Q3 决策）。
    """

    llm_mode: Literal["litellm", "echo"] = Field(
        default="litellm",
        description="LLM 运行模式：litellm（真实调用）/ echo（开发测试回显）",
    )
    litellm_proxy_url: str = Field(
        default="http://localhost:4000",
        description="LiteLLM Proxy 地址",
    )
    master_key_env: str = Field(
        default="LITELLM_MASTER_KEY",
        description="Master Key 所在的环境变量名",
        pattern=r"^[A-Z][A-Z0-9_]*$",
    )


# ---------------------------------------------------------------------------
# OctoAgentConfig — 统一配置根模型
# ---------------------------------------------------------------------------


class OctoAgentConfig(BaseModel):
    """统一配置根模型 — octoagent.yaml 的结构化表示

    是 F014 引入的核心数据模型，作为所有模型和 Provider 配置的单一信息源。
    """

    config_version: int = Field(
        default=1,
        description="配置文件版本号，用于向前兼容迁移",
        ge=1,
    )
    updated_at: str = Field(
        description="最后更新时间（ISO 8601 日期，如 '2026-03-04'）",
    )
    providers: list[ProviderEntry] = Field(
        default_factory=list,
        description="已配置的 Provider 列表",
    )
    model_aliases: dict[str, ModelAlias] = Field(
        default_factory=dict,
        description="模型别名映射（key 为别名字符串，value 为 ModelAlias）",
    )
    runtime: RuntimeConfig = Field(
        default_factory=RuntimeConfig,
        description="运行时配置块",
    )

    @model_validator(mode="after")
    def validate_provider_ids_unique(self) -> "OctoAgentConfig":
        """校验 providers 列表中 id 唯一"""
        seen: set[str] = set()
        for p in self.providers:
            if p.id in seen:
                raise ValueError(f"providers 列表中存在重复的 id: '{p.id}'")
            seen.add(p.id)
        return self

    @model_validator(mode="after")
    def validate_alias_provider_refs(self) -> "OctoAgentConfig":
        """校验 model_aliases 中所有 provider 引用必须存在于 providers 列表"""
        provider_ids = {p.id for p in self.providers}
        for alias_key, alias_val in self.model_aliases.items():
            if alias_val.provider not in provider_ids:
                raise ValueError(
                    f"model_aliases.{alias_key}.provider='{alias_val.provider}' "
                    f"未在 providers 列表中找到（可用 id: {sorted(provider_ids)}）"
                )
        return self

    @model_validator(mode="after")
    def warn_alias_disabled_provider(self) -> "OctoAgentConfig":
        """alias 指向已禁用 Provider 时发出 UserWarning（EC-5）"""
        disabled_ids = {p.id for p in self.providers if not p.enabled}
        for alias_key, alias_val in self.model_aliases.items():
            if alias_val.provider in disabled_ids:
                warnings.warn(
                    f"model_aliases.{alias_key}.provider='{alias_val.provider}' "
                    f"对应的 Provider 已禁用（enabled=False），同步时不会生成 litellm 条目。"
                    f"请运行 octo config alias set {alias_key} 更新别名，"
                    f"或运行 octo config provider enable {alias_val.provider} 启用 Provider。",
                    UserWarning,
                    stacklevel=2,
                )
        return self

    @model_validator(mode="before")
    @classmethod
    def warn_unknown_version(cls, data: object) -> object:
        """config_version != 1 时打印 WARNING 并继续（NFR-006 向前兼容）"""
        if isinstance(data, dict):
            version = data.get("config_version", 1)
            if isinstance(version, int) and version != 1:
                warnings.warn(
                    f"octoagent.yaml config_version={version} 与当前版本（1）不匹配，"
                    f"建议运行 octo config migrate 升级配置格式。继续使用当前配置。",
                    UserWarning,
                    stacklevel=2,
                )
        return data

    def get_provider(self, provider_id: str) -> ProviderEntry | None:
        """按 id 查找 Provider 条目"""
        return next((p for p in self.providers if p.id == provider_id), None)

    def to_yaml(self) -> str:
        """序列化为带注释头的 YAML 字符串

        使用 pyyaml 生成，每次同步时重新生成注释（data-model.md §7 YAML 库选择决策）。
        """
        data = self.model_dump(mode="python")
        body = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return _YAML_HEADER + body

    @classmethod
    def from_yaml(cls, text: str) -> "OctoAgentConfig":
        """解析并校验 YAML 文本

        抛出 ConfigParseError（含字段路径）：
        - YAML 语法错误
        - schema 校验失败
        """
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigParseError(
                message=f"YAML 语法错误：{exc}",
                field_path="(root)",
            ) from exc

        if not isinstance(raw, dict):
            raise ConfigParseError(
                message="octoagent.yaml 根节点必须是 YAML 映射（dict），实际为空或非映射类型",
                field_path="(root)",
            )

        from pydantic import ValidationError

        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            # 取第一个错误作为代表，提供字段路径
            errors = exc.errors()
            if errors:
                first = errors[0]
                loc_parts = [str(x) for x in first.get("loc", [])]
                field_path = ".".join(loc_parts) if loc_parts else "(root)"
                msg = first.get("msg", str(exc))
                raise ConfigParseError(
                    message=f"字段 {field_path}：{msg}",
                    field_path=field_path,
                ) from exc
            raise ConfigParseError(
                message=str(exc),
                field_path="(root)",
            ) from exc
