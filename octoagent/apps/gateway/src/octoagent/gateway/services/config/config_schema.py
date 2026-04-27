"""OctoAgent 统一配置 Schema -- Feature 014

定义 octoagent.yaml 的 Pydantic v2 数据模型。
所有 Provider/ModelAlias/RuntimeConfig/OctoAgentConfig 实体均在此文件定义。
对应 FR-001 ~ FR-004，data-model.md 全部实体定义。

Feature 081 P2 升级（v1 → v2 schema）：
- ProviderEntry 加 ``transport`` / ``api_base`` / ``auth`` first-class 字段
- 旧 ``auth_type`` / ``api_key_env`` / ``base_url`` 标 deprecated 但保留可读
"""

from __future__ import annotations

import ipaddress
import warnings
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from octoagent.provider.dx.control_plane_models import ConfigSchemaDocument

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


class AuthApiKey(BaseModel):
    """Feature 081 P2：API Key 认证 first-class schema。"""

    kind: Literal["api_key"] = Field(default="api_key", description="discriminator")
    env: str = Field(
        min_length=1,
        pattern=_ENV_NAME_PATTERN,
        description="凭证所在环境变量名（如 'OPENROUTER_API_KEY'）。仅存变量名称，不存实际值。",
    )


class AuthOAuth(BaseModel):
    """Feature 081 P2：OAuth 认证 first-class schema。"""

    kind: Literal["oauth"] = Field(default="oauth", description="discriminator")
    profile: str = Field(
        min_length=1,
        description="auth-profiles.json 内的 profile 名称（如 'openai-codex-default'）",
    )


ProviderAuth = Annotated[
    Union[AuthApiKey, AuthOAuth],
    Field(discriminator="kind"),
]


class ProviderEntry(BaseModel):
    """Provider 条目 — octoagent.yaml providers[] 列表元素

    不存储凭证值本身，凭证通过 ``auth`` / ``api_key_env`` 引用环境变量
    或 ``auth-profiles.json``（Constitution C5）。

    Feature 081 P2 升级：
    - 新增 ``transport`` / ``api_base`` / ``auth`` first-class 字段
    - 旧 ``auth_type`` / ``api_key_env`` / ``base_url`` 标 deprecated 但保留可读
    - ``model_validator`` 自动把旧字段映射到新字段（backward-compat）
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
    enabled: bool = Field(
        default=True,
        description="是否参与配置生成（False 时不生成 ProviderRouter 条目）",
    )

    # ── Feature 081 P2 新字段（first-class）──
    transport: Literal["openai_chat", "openai_responses", "anthropic_messages"] | None = Field(
        default=None,
        description=(
            "LLM 调用协议；不设时由 ProviderRouter 按 id / api_base 推断"
            "（与 fallback 推断同源）。可选值：openai_chat / openai_responses / anthropic_messages"
        ),
    )
    api_base: str = Field(
        default="",
        description="Provider HTTP 基础 URL（替代旧 base_url）。留空使用 Provider 默认。",
    )
    auth: ProviderAuth | None = Field(
        default=None,
        description=(
            "认证配置（discriminated union）。不设时由 ``auth_type`` + ``api_key_env`` "
            "自动迁移（backward-compat）。"
        ),
    )

    # ── Deprecated 字段（Feature 081 P2 标记，保留可读，下个 minor 删除）──
    auth_type: Literal["api_key", "oauth"] | None = Field(
        default=None,
        deprecated=True,
        description="DEPRECATED Feature 081 P2：使用 ``auth.kind`` 替代",
    )
    api_key_env: str = Field(
        default="",
        pattern=_OPTIONAL_ENV_NAME_PATTERN,
        deprecated=True,
        description="DEPRECATED Feature 081 P2：使用 ``auth.env``（kind=api_key）替代",
    )
    base_url: str = Field(
        default="",
        deprecated=True,
        description="DEPRECATED Feature 081 P2：使用 ``api_base`` 替代",
    )

    @model_validator(mode="after")
    def _migrate_legacy_to_new_fields(self) -> ProviderEntry:
        """Feature 081 P2：把 deprecated 旧字段自动映射到新字段。

        - ``base_url`` → ``api_base``（仅当新字段为空）
        - ``auth_type`` + ``api_key_env`` → ``auth.kind`` + ``auth.env``/``profile``
          （仅当 ``auth`` 为 None）
        """
        if not self.api_base and self.base_url:
            self.api_base = self.base_url
        if self.auth is None:
            if self.auth_type == "api_key" and self.api_key_env:
                self.auth = AuthApiKey(env=self.api_key_env)
            elif self.auth_type == "oauth":
                # OAuth profile 名称按惯例为 ``{provider_id}-default``
                self.auth = AuthOAuth(profile=f"{self.id}-default")
        return self

    # ── Feature 081 P4 修复（Codex high finding F1+F2）：v2 优先 / v1 fallback 属性 ──
    # 调用方应优先用这些属性而非直接读 auth_type / api_key_env，因为 v2 yaml 经过
    # F081 cleanup 后 auth_type / api_key_env 字段被省略（仍可读但 default=None/""），
    # 直接读旧字段会让 setup_service / drift_check 等在 v2 yaml 下漏检测。

    @property
    def effective_auth_kind(self) -> str:
        """认证类型；v2 ``auth.kind`` 优先，v1 ``auth_type`` fallback；空串表示未配置。"""
        if self.auth is not None:
            return self.auth.kind
        return self.auth_type or ""

    @property
    def effective_api_key_env(self) -> str:
        """API key 环境变量名；v2 ``auth.env`` 优先，v1 ``api_key_env`` fallback；空串表示未配置。"""
        if self.auth is not None and isinstance(self.auth, AuthApiKey):
            return self.auth.env
        return self.api_key_env or ""

    @property
    def effective_oauth_profile(self) -> str:
        """OAuth profile 名；v2 ``auth.profile`` 优先；v1 按 ``{id}-default`` 推断。"""
        if self.auth is not None and isinstance(self.auth, AuthOAuth):
            return self.auth.profile
        if self.auth_type == "oauth":
            return f"{self.id}-default"
        return ""


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
    """运行时配置 — octoagent.yaml runtime 块（占位，目前无字段）。

    F081 P3b 退役 LiteLLM 后所有原有字段均已删除（``llm_mode`` / ``litellm_proxy_url``
    / ``master_key_env``）。块本身保留作为 schema 占位，后续新增运行时配置直接在此扩展。
    """


# ---------------------------------------------------------------------------
# MemoryConfig — Memory backend / recall 默认配置
# ---------------------------------------------------------------------------


class MemoryConfig(BaseModel):
    """Memory 默认配置。

    统一使用本地内建引擎模式（SQLite / Vault + Qwen Embedding）。
    """

    reasoning_model_alias: str = Field(
        default="",
        description="记忆加工模型别名（留空时 fallback 到 main）",
    )
    expand_model_alias: str = Field(
        default="",
        description="查询扩写模型别名（留空时 fallback 到 main）",
    )
    embedding_model_alias: str = Field(
        default="",
        description="语义检索 embedding 模型别名（留空时使用内建 Qwen3-Embedding-0.6B）",
    )
    rerank_model_alias: str = Field(
        default="",
        description="结果重排模型别名（留空时使用 heuristic 重排）",
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
    def validate_memory_alias_refs(self) -> OctoAgentConfig:
        """校验 memory 配置中引用的 alias 必须存在于 model_aliases。"""
        memory_alias_fields = {
            "reasoning_model_alias": self.memory.reasoning_model_alias,
            "expand_model_alias": self.memory.expand_model_alias,
            "embedding_model_alias": self.memory.embedding_model_alias,
            "rerank_model_alias": self.memory.rerank_model_alias,
        }
        available_aliases = sorted(self.model_aliases.keys())
        for field_name, alias_name in memory_alias_fields.items():
            normalized_alias = alias_name.strip()
            if not normalized_alias:
                continue
            if normalized_alias not in self.model_aliases:
                raise ValueError(
                    f"memory.{field_name}='{normalized_alias}' "
                    f"未在 model_aliases 中找到（可用 alias: {available_aliases}）"
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

    # F081 cleanup（2026-04-27）：原 ``warn_unknown_version`` model_validator 已删除。
    # 旧逻辑硬编码 ``version != 1`` 触发 warning，v2 schema 切换后反把 v2 误判为不匹配。
    # schema 兼容现在通过 ``ProviderEntry`` 的 v1→v2 字段互转处理；
    # ``config_version`` 仅作信息字段保留。

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
            "memory",
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
                    "providers.0.base_url",
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
            "memory": {
                "title": "Memory",
                "description": "本地记忆引擎别名配置。",
                "fields": [
                    "memory.reasoning_model_alias",
                    "memory.expand_model_alias",
                    "memory.embedding_model_alias",
                    "memory.rerank_model_alias",
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
            "providers.0.base_url": {
                "label": "API Base URL",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_provider.base_url if active_provider else "",
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
            "memory.reasoning_model_alias": {
                "label": "记忆加工模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.reasoning_model_alias,
            },
            "memory.expand_model_alias": {
                "label": "查询扩写模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.expand_model_alias,
            },
            "memory.embedding_model_alias": {
                "label": "语义检索模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.embedding_model_alias,
            },
            "memory.rerank_model_alias": {
                "label": "结果重排模型别名",
                "input": "text",
                "required": False,
                "recommended": False,
                "default": active_memory.rerank_model_alias,
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
                    config.front_door.bearer_token_env if config else "OCTOAGENT_FRONTDOOR_TOKEN"
                ),
            },
            "front_door.trusted_proxy_header": {
                "label": "Trusted Proxy Header",
                "input": "text",
                "required": True,
                "recommended": True,
                "default": (
                    config.front_door.trusted_proxy_header if config else "X-OctoAgent-Proxy-Auth"
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
