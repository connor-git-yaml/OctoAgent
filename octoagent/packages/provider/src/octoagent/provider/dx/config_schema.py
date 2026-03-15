"""OctoAgent 统一配置 Schema -- Feature 014

定义 octoagent.yaml 的 Pydantic v2 数据模型。
所有 Provider/ModelAlias/RuntimeConfig/OctoAgentConfig 实体均在此文件定义。
对应 FR-001 ~ FR-004，data-model.md 全部实体定义。
"""

from __future__ import annotations

import ipaddress
import warnings
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .control_plane_models import ConfigSchemaDocument

# octoagent.yaml 文件头注释（不含凭证提示）
_YAML_HEADER = (
    "# OctoAgent 统一配置文件\n"
    "# 此文件安全纳入版本管理（不含凭证）\n"
    "# 凭证由 .env.litellm 管理（已在 .gitignore）\n"
    "# NEVER 在此文件存储 API Key 明文 — 使用 api_key_env 存储环境变量名\n"
    "#\n"
)

_ENV_NAME_PATTERN = r"^[A-Z][A-Z0-9_]*$"
_OPTIONAL_ENV_NAME_PATTERN = r"^$|^[A-Z][A-Z0-9_]*$"
_ROUTED_PROVIDER_MODEL_PREFIXES: dict[str, str] = {
    "openrouter": "openrouter",
    "github": "github",
}

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


def normalize_provider_model_string(provider_id: str, model_name: str) -> str:
    """将用户输入的模型名规范化为 provider-aware LiteLLM 字符串。

    目标不是猜所有 provider 的私有写法，而是把明确需要 provider 前缀的 routed
    providers 统一收口，避免 UI/CLI 里输入裸模型名后运行时才 404。
    """

    provider = str(provider_id).strip().lower()
    model = str(model_name).strip()
    if not provider or not model:
        return model

    required_prefix = _ROUTED_PROVIDER_MODEL_PREFIXES.get(provider)
    if not required_prefix:
        return model

    normalized_prefix = f"{required_prefix}/"
    if model.lower().startswith(normalized_prefix):
        return model
    return f"{required_prefix}/{model}"


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
        pattern=_ENV_NAME_PATTERN,
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

    @model_validator(mode="after")
    def _normalize_model_string(self) -> ModelAlias:
        self.model = normalize_provider_model_string(self.provider, self.model)
        return self


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
        pattern=_ENV_NAME_PATTERN,
    )


# ---------------------------------------------------------------------------
# MemoryConfig — Memory backend / recall 默认配置
# ---------------------------------------------------------------------------


class MemoryConfig(BaseModel):
    """Memory 默认配置。

    用于 Web / Gateway 的 Memory Settings 页面与 runtime fallback。
    project binding 仍然优先；当未绑定时，允许使用 octoagent.yaml 中的配置作为兜底。
    """

    backend_mode: Literal["local_only", "memu"] = Field(
        default="local_only",
        description=(
            "Memory 引擎层级：local_only 走内建记忆引擎；"
            "memu 表示仍需兼容旧 MemU 接入链路。"
        ),
    )
    reasoning_model_alias: str = Field(
        default="",
        description="Memory 加工/总结/候选整理所用的模型别名。留空时回退到 main。",
    )
    expand_model_alias: str = Field(
        default="",
        description="Memory 查询扩写所用的模型别名。留空时回退到 main。",
    )
    embedding_model_alias: str = Field(
        default="",
        description="Memory 语义检索所用的 embedding 模型别名。留空时回退到内建默认层。",
    )
    rerank_model_alias: str = Field(
        default="",
        description="Memory 结果重排所用的模型别名。留空时回退到 heuristic rerank。",
    )
    bridge_transport: Literal["http", "command"] = Field(
        default="http",
        description="兼容接入 transport：http 连接远端 bridge；command 调用本地 bridge 命令。",
    )
    bridge_url: str = Field(
        default="",
        description="兼容接入的 bridge 基础地址，例如 https://memory.example.com",
    )
    bridge_command: str = Field(
        default="",
        description="兼容接入的本地 bridge 命令，例如 uv run python scripts/memu_bridge.py",
    )
    bridge_command_cwd: str = Field(
        default="",
        description="执行兼容本地 bridge 命令时的工作目录（可选）",
    )
    bridge_command_timeout_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=120.0,
        description="执行兼容本地 bridge 命令的超时时间（秒）",
    )
    bridge_api_key_env: str = Field(
        default="",
        description="兼容 bridge API Key 所在环境变量名（可选）",
        pattern=_OPTIONAL_ENV_NAME_PATTERN,
    )
    bridge_timeout_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="请求兼容 bridge 的超时时间（秒）",
    )
    bridge_api_key_header: str = Field(
        default="Authorization",
        min_length=1,
        description="MemU bridge API Key 使用的请求头名称",
    )
    bridge_api_key_scheme: str = Field(
        default="Bearer",
        description="MemU bridge API Key 前缀；不需要前缀时可留空",
    )
    bridge_health_path: str = Field(
        default="/health",
        min_length=1,
        description="健康检查路径",
    )
    bridge_search_path: str = Field(
        default="/memory/search",
        min_length=1,
        description="检索路径",
    )
    bridge_sync_path: str = Field(
        default="/memory/sync",
        min_length=1,
        description="同步路径",
    )
    bridge_ingest_path: str = Field(
        default="/memory/ingest",
        min_length=1,
        description="写入 ingest 路径",
    )
    bridge_derivations_path: str = Field(
        default="/memory/derivations/query",
        min_length=1,
        description="派生记忆查询路径",
    )
    bridge_evidence_path: str = Field(
        default="/memory/evidence/resolve",
        min_length=1,
        description="证据链查询路径",
    )
    bridge_maintenance_path: str = Field(
        default="/memory/maintenance",
        min_length=1,
        description="维护任务路径",
    )


# ---------------------------------------------------------------------------
# FrontDoorConfig — 对外入口边界配置
# ---------------------------------------------------------------------------


class FrontDoorConfig(BaseModel):
    """Gateway front-door 边界配置。"""

    mode: Literal["loopback", "bearer", "trusted_proxy"] = Field(
        default="loopback",
        description="owner-facing API 的 front-door 模式",
    )
    bearer_token_env: str = Field(
        default="OCTOAGENT_FRONTDOOR_TOKEN",
        description="bearer 模式下的 token 环境变量名",
        pattern=_ENV_NAME_PATTERN,
    )
    trusted_proxy_header: str = Field(
        default="X-OctoAgent-Proxy-Auth",
        description="trusted_proxy 模式下代理注入的共享鉴权 header",
        min_length=1,
    )
    trusted_proxy_token_env: str = Field(
        default="OCTOAGENT_TRUSTED_PROXY_TOKEN",
        description="trusted_proxy 模式下代理共享 token 的环境变量名",
        pattern=_ENV_NAME_PATTERN,
    )
    trusted_proxy_cidrs: list[str] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"],
        description="trusted_proxy 模式下允许直接连接 Gateway 的代理来源 CIDR 列表",
    )

    @field_validator("trusted_proxy_header")
    @classmethod
    def normalize_trusted_proxy_header(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("trusted_proxy_header 不能为空")
        return normalized

    @field_validator("trusted_proxy_cidrs", mode="before")
    @classmethod
    def normalize_trusted_proxy_cidrs(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise TypeError("trusted_proxy_cidrs 仅支持字符串列表")
                stripped = item.strip()
                if stripped:
                    normalized.append(stripped)
            return normalized
        raise TypeError("trusted_proxy_cidrs 必须是字符串列表或逗号分隔字符串")

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: list[str]) -> list[str]:
        for item in value:
            ipaddress.ip_network(item, strict=False)
        return value

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> FrontDoorConfig:
        if self.mode == "trusted_proxy" and not self.trusted_proxy_cidrs:
            raise ValueError("front_door.mode=trusted_proxy 时必须提供 trusted_proxy_cidrs")
        return self


# ---------------------------------------------------------------------------
# TelegramChannelConfig / ChannelsConfig — 渠道配置
# ---------------------------------------------------------------------------


class TelegramChannelConfig(BaseModel):
    """Telegram 渠道最小配置。

    provider/dx、doctor 与 gateway 共享此结构，作为 Feature 016 的最小单一事实源。
    """

    enabled: bool = Field(
        default=False,
        description="是否启用 Telegram channel",
    )
    mode: Literal["webhook", "polling"] = Field(
        default="webhook",
        description="Telegram 接入模式",
    )
    bot_token_env: str = Field(
        default="TELEGRAM_BOT_TOKEN",
        description="Bot token 所在环境变量名",
        pattern=_ENV_NAME_PATTERN,
    )
    webhook_url: str = Field(
        default="",
        description="webhook 模式下的外部 URL",
    )
    webhook_secret_env: str = Field(
        default="",
        description="webhook secret 所在环境变量名（可选）",
        pattern=_OPTIONAL_ENV_NAME_PATTERN,
    )
    dm_policy: Literal["pairing", "allowlist", "open", "disabled"] = Field(
        default="pairing",
        description="DM 默认访问策略",
    )
    allow_users: list[str] = Field(
        default_factory=list,
        description="显式允许的 Telegram user id 列表",
    )
    allowed_groups: list[str] = Field(
        default_factory=list,
        description="显式允许的 Telegram group/chat id 列表",
    )
    group_policy: Literal["allowlist", "open", "disabled"] = Field(
        default="allowlist",
        description="群聊默认访问策略",
    )
    group_allow_users: list[str] = Field(
        default_factory=list,
        description="群聊内额外允许发言的 user id 列表",
    )
    polling_timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=600,
        description="polling 模式单次 long polling 超时时间（秒）",
    )

    @field_validator(
        "allow_users",
        "allowed_groups",
        "group_allow_users",
        mode="before",
    )
    @classmethod
    def normalize_id_list(cls, value: object) -> list[str]:
        """允许 YAML 中使用数字 chat_id，但内部统一存字符串。"""
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Telegram ID 列表必须是 list")
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str | int):
                normalized.append(str(item))
                continue
            raise TypeError("Telegram ID 仅支持 str/int")
        return normalized

    @model_validator(mode="after")
    def validate_webhook_requirements(self) -> TelegramChannelConfig:
        if self.enabled and self.mode == "webhook" and not self.webhook_url:
            raise ValueError("channels.telegram.mode=webhook 时必须提供 webhook_url")
        return self


class ChannelsConfig(BaseModel):
    """统一渠道配置块。"""

    telegram: TelegramChannelConfig = Field(
        default_factory=TelegramChannelConfig,
        description="Telegram 渠道配置",
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
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="Memory backend 与高级检索默认配置",
    )
    front_door: FrontDoorConfig = Field(
        default_factory=FrontDoorConfig,
        description="owner-facing API 对外入口边界配置",
    )
    channels: ChannelsConfig = Field(
        default_factory=ChannelsConfig,
        description="多渠道配置块",
    )

    @model_validator(mode="after")
    def validate_provider_ids_unique(self) -> OctoAgentConfig:
        """校验 providers 列表中 id 唯一"""
        seen: set[str] = set()
        for p in self.providers:
            if p.id in seen:
                raise ValueError(f"providers 列表中存在重复的 id: '{p.id}'")
            seen.add(p.id)
        return self

    @model_validator(mode="after")
    def validate_alias_provider_refs(self) -> OctoAgentConfig:
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
    def warn_alias_disabled_provider(self) -> OctoAgentConfig:
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
    def from_yaml(cls, text: str) -> OctoAgentConfig:
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


def build_config_schema_document(
    config: OctoAgentConfig | None = None,
) -> ConfigSchemaDocument:
    """产出 026-A 兼容的 schema + uiHints 文档。"""

    active_provider = config.providers[0] if config and config.providers else None
    active_telegram = config.channels.telegram if config else TelegramChannelConfig()
    active_memory = config.memory if config else MemoryConfig()
    provider_target_key = (
        f"providers.{active_provider.id}.api_key_env"
        if active_provider is not None
        else "providers.openrouter.api_key_env"
    )
    schema = OctoAgentConfig.model_json_schema()
    ui_hints: dict[str, Any] = {
        "wizard_order": [
            "project",
            "provider",
            "models",
            "runtime",
            "telegram",
            "review",
        ],
        "sections": {
            "provider": {
                "title": "Provider",
                "description": "配置默认 provider 与凭证引用名。",
                "fields": [
                    "providers.0.id",
                    "providers.0.name",
                    "providers.0.auth_type",
                    "providers.0.api_key_env",
                ],
            },
            "models": {
                "title": "Model Aliases",
                "description": "配置 main / cheap alias。",
                "fields": [
                    "model_aliases.main.model",
                    "model_aliases.cheap.model",
                ],
            },
            "runtime": {
                "title": "Runtime",
                "description": "配置 runtime 与 proxy 入口。",
                "fields": [
                    "runtime.llm_mode",
                    "runtime.litellm_proxy_url",
                    "runtime.master_key_env",
                ],
            },
            "memory": {
                "title": "Memory",
                "description": "配置 Memory 默认质量层、模型绑定与兼容接入。",
                "fields": [
                    "memory.backend_mode",
                    "memory.reasoning_model_alias",
                    "memory.expand_model_alias",
                    "memory.embedding_model_alias",
                    "memory.rerank_model_alias",
                    "memory.bridge_transport",
                    "memory.bridge_url",
                    "memory.bridge_command",
                    "memory.bridge_command_cwd",
                    "memory.bridge_command_timeout_seconds",
                    "memory.bridge_api_key_env",
                    "memory.bridge_timeout_seconds",
                    "memory.bridge_api_key_header",
                    "memory.bridge_api_key_scheme",
                    "memory.bridge_health_path",
                    "memory.bridge_search_path",
                    "memory.bridge_sync_path",
                    "memory.bridge_ingest_path",
                    "memory.bridge_derivations_path",
                    "memory.bridge_evidence_path",
                    "memory.bridge_maintenance_path",
                ],
            },
            "security": {
                "title": "Front Door",
                "description": "配置 owner-facing API 的对外访问边界。",
                "fields": [
                    "front_door.mode",
                    "front_door.bearer_token_env",
                    "front_door.trusted_proxy_header",
                    "front_door.trusted_proxy_token_env",
                    "front_door.trusted_proxy_cidrs",
                ],
            },
            "telegram": {
                "title": "Telegram",
                "description": "可选启用 Telegram channel。",
                "fields": [
                    "channels.telegram.enabled",
                    "channels.telegram.mode",
                    "channels.telegram.bot_token_env",
                    "channels.telegram.webhook_url",
                    "channels.telegram.webhook_secret_env",
                ],
            },
        },
        "fields": {
            "providers.0.id": {
                "label": "Provider ID",
                "input": "text",
                "required": True,
                "recommended": True,
                "default": active_provider.id if active_provider else "openrouter",
            },
            "providers.0.name": {
                "label": "Provider 显示名",
                "input": "text",
                "required": True,
                "recommended": True,
                "default": active_provider.name if active_provider else "OpenRouter",
            },
            "providers.0.auth_type": {
                "label": "认证方式",
                "input": "choice",
                "required": True,
                "choices": ["api_key", "oauth"],
                "default": active_provider.auth_type if active_provider else "api_key",
            },
            "providers.0.api_key_env": {
                "label": "Provider 凭证环境变量名",
                "input": "env_name",
                "required": True,
                "recommended": True,
                "secret_target": {
                    "target_kind": "provider",
                    "target_key": provider_target_key,
                    "target_key_template": "providers.{provider_id}.api_key_env",
                    "provider_id_field": "providers.0.id",
                },
                "default": (
                    active_provider.api_key_env if active_provider else "OPENROUTER_API_KEY"
                ),
            },
            "model_aliases.main.model": {
                "label": "main 模型",
                "input": "text",
                "required": True,
                "recommended": True,
                "default": (
                    config.model_aliases.get("main").model
                    if config and "main" in config.model_aliases
                    else "openrouter/auto"
                ),
            },
            "model_aliases.cheap.model": {
                "label": "cheap 模型",
                "input": "text",
                "required": True,
                "recommended": True,
                "default": (
                    config.model_aliases.get("cheap").model
                    if config and "cheap" in config.model_aliases
                    else "openrouter/auto"
                ),
            },
            "runtime.llm_mode": {
                "label": "LLM 模式",
                "input": "choice",
                "required": True,
                "choices": ["litellm", "echo"],
                "default": config.runtime.llm_mode if config else "litellm",
            },
            "runtime.litellm_proxy_url": {
                "label": "LiteLLM Proxy URL",
                "input": "text",
                "required": False,
                "recommended": True,
                "default": (
                    config.runtime.litellm_proxy_url if config else "http://localhost:4000"
                ),
            },
            "runtime.master_key_env": {
                "label": "Master Key 环境变量名",
                "input": "env_name",
                "required": False,
                "recommended": True,
                "secret_target": {
                    "target_kind": "runtime",
                    "target_key": "runtime.master_key_env",
                },
                "default": config.runtime.master_key_env if config else "LITELLM_MASTER_KEY",
            },
            "memory.backend_mode": {
                "label": "Memory 引擎层级",
                "input": "choice",
                "required": True,
                "recommended": True,
                "choices": ["local_only", "memu"],
                "default": active_memory.backend_mode,
            },
            "memory.reasoning_model_alias": {
                "label": "加工模型别名",
                "input": "text",
                "required": False,
                "recommended": True,
                "default": active_memory.reasoning_model_alias,
            },
            "memory.expand_model_alias": {
                "label": "扩写模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.expand_model_alias,
            },
            "memory.embedding_model_alias": {
                "label": "Embedding 模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.embedding_model_alias,
            },
            "memory.rerank_model_alias": {
                "label": "Rerank 模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.rerank_model_alias,
            },
            "memory.bridge_transport": {
                "label": "兼容接入方式",
                "input": "choice",
                "required": True,
                "recommended": True,
                "choices": ["http", "command"],
                "default": active_memory.bridge_transport,
            },
            "memory.bridge_url": {
                "label": "兼容 Bridge 地址",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_url,
            },
            "memory.bridge_command": {
                "label": "兼容本地命令",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_command,
            },
            "memory.bridge_command_cwd": {
                "label": "本地命令工作目录",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_command_cwd,
            },
            "memory.bridge_command_timeout_seconds": {
                "label": "本地命令超时时间（秒）",
                "input": "text",
                "required": False,
                "recommended": True,
                "default": active_memory.bridge_command_timeout_seconds,
            },
            "memory.bridge_api_key_env": {
                "label": "兼容 API Key 环境变量名",
                "input": "env_name",
                "required": False,
                "recommended": False,
                "secret_target": {
                    "target_kind": "memory",
                    "target_key": "memory.bridge_api_key_env",
                },
                "default": active_memory.bridge_api_key_env,
            },
            "memory.bridge_timeout_seconds": {
                "label": "兼容 Bridge 超时时间（秒）",
                "input": "text",
                "required": False,
                "recommended": True,
                "default": active_memory.bridge_timeout_seconds,
            },
            "memory.bridge_api_key_header": {
                "label": "API Key Header",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_api_key_header,
            },
            "memory.bridge_api_key_scheme": {
                "label": "API Key 前缀",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_api_key_scheme,
            },
            "memory.bridge_health_path": {
                "label": "健康检查路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_health_path,
            },
            "memory.bridge_search_path": {
                "label": "检索路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_search_path,
            },
            "memory.bridge_sync_path": {
                "label": "同步路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_sync_path,
            },
            "memory.bridge_ingest_path": {
                "label": "写入路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_ingest_path,
            },
            "memory.bridge_derivations_path": {
                "label": "派生查询路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_derivations_path,
            },
            "memory.bridge_evidence_path": {
                "label": "证据链路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_evidence_path,
            },
            "memory.bridge_maintenance_path": {
                "label": "维护任务路径",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.bridge_maintenance_path,
            },
            "front_door.mode": {
                "label": "Front-door 模式",
                "input": "choice",
                "required": True,
                "recommended": True,
                "choices": ["loopback", "bearer", "trusted_proxy"],
                "default": config.front_door.mode if config else "loopback",
            },
            "front_door.bearer_token_env": {
                "label": "Bearer Token 环境变量名",
                "input": "env_name",
                "required": True,
                "recommended": True,
                "default": (
                    config.front_door.bearer_token_env
                    if config
                    else "OCTOAGENT_FRONTDOOR_TOKEN"
                ),
            },
            "front_door.trusted_proxy_header": {
                "label": "Trusted Proxy Header",
                "input": "text",
                "required": True,
                "recommended": True,
                "default": (
                    config.front_door.trusted_proxy_header
                    if config
                    else "X-OctoAgent-Proxy-Auth"
                ),
            },
            "front_door.trusted_proxy_token_env": {
                "label": "Trusted Proxy Token 环境变量名",
                "input": "env_name",
                "required": True,
                "recommended": True,
                "default": (
                    config.front_door.trusted_proxy_token_env
                    if config
                    else "OCTOAGENT_TRUSTED_PROXY_TOKEN"
                ),
            },
            "front_door.trusted_proxy_cidrs": {
                "label": "Trusted Proxy CIDRs",
                "input": "csv",
                "required": True,
                "recommended": True,
                "default": (
                    ", ".join(config.front_door.trusted_proxy_cidrs)
                    if config
                    else "127.0.0.1/32, ::1/128"
                ),
            },
            "channels.telegram.enabled": {
                "label": "启用 Telegram",
                "input": "confirm",
                "required": False,
                "recommended": False,
                "default": active_telegram.enabled,
            },
            "channels.telegram.mode": {
                "label": "Telegram 模式",
                "input": "choice",
                "required": False,
                "recommended": True,
                "choices": ["polling", "webhook"],
                "default": active_telegram.mode,
            },
            "channels.telegram.bot_token_env": {
                "label": "Telegram Bot Token 环境变量名",
                "input": "env_name",
                "required": False,
                "recommended": True,
                "secret_target": {
                    "target_kind": "channel",
                    "target_key": "channels.telegram.bot_token_env",
                },
                "default": active_telegram.bot_token_env,
            },
            "channels.telegram.webhook_url": {
                "label": "Telegram Webhook URL",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_telegram.webhook_url,
                "visible_when": {
                    "field": "channels.telegram.mode",
                    "equals": "webhook",
                },
            },
            "channels.telegram.webhook_secret_env": {
                "label": "Telegram Webhook Secret 环境变量名",
                "input": "env_name",
                "required": False,
                "recommended": False,
                "secret_target": {
                    "target_kind": "channel",
                    "target_key": "channels.telegram.webhook_secret_env",
                },
                "default": active_telegram.webhook_secret_env,
                "visible_when": {
                    "field": "channels.telegram.mode",
                    "equals": "webhook",
                },
            },
        },
    }
    return ConfigSchemaDocument(schema_payload=schema, ui_hints=ui_hints)
