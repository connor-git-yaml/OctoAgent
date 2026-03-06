# Data Model: Feature 014 — 统一模型配置管理

**Feature**: 014-unified-model-config
**Created**: 2026-03-04
**Source**: spec.md FR-001 ~ FR-004，Key Entities 节

---

## 实体总览

F014 引入 4 个新实体，全部对应 `octoagent.yaml` 文件结构：

| 实体 | 对应 Pydantic 类 | 文件位置 | 生命周期 |
|------|----------------|---------|---------|
| 统一配置 | `OctoAgentConfig` | `octoagent.yaml` | 项目创建时初始化，增量更新 |
| Provider 条目 | `ProviderEntry` | `octoagent.yaml.providers[]` | 随 `octo config provider add` 添加 |
| 模型别名 | `ModelAlias` | `octoagent.yaml.model_aliases{}` | 随 `octo config alias set` 更新 |
| 运行时配置 | `RuntimeConfig` | `octoagent.yaml.runtime` | 初始化时创建，手动编辑或命令更新 |

---

## 1. ProviderEntry — Provider 条目

**对应 FR-002**

```python
class ProviderEntry(BaseModel):
    """Provider 条目 — octoagent.yaml providers[] 列表元素

    不存储凭证值本身，凭证通过 api_key_env 引用环境变量（Constitution C5）。
    """

    id: str = Field(
        description="Provider 全局唯一 ID（如 'openrouter'、'anthropic'）",
        min_length=1,
        pattern=r"^[a-z0-9_-]+$",  # 仅允许小写字母、数字、连字符、下划线
    )
    name: str = Field(
        description="Provider 显示名称（如 'OpenRouter'）",
        min_length=1,
    )
    auth_type: Literal["api_key", "oauth"] = Field(
        description="认证类型",
    )
    api_key_env: str = Field(
        description="凭证所在环境变量名（如 'OPENROUTER_API_KEY'）。"
                    "仅存变量名称，不存实际值。",
        pattern=r"^[A-Z][A-Z0-9_]*$",  # 符合 POSIX 环境变量命名规范
    )
    enabled: bool = Field(
        default=True,
        description="是否参与配置生成（False 时不生成 litellm-config 条目）",
    )
```

**约束**:
- `id` 在 `OctoAgentConfig.providers` 列表中必须唯一
- `api_key_env` 格式校验：必须为合法环境变量名（大写字母开头），防止误将明文 `KEY=value` 写入
- `enabled=False` 时，`litellm-config.yaml` 中对应条目被移除，但配置条目保留（可逆）

**内置预设 Provider**（init 引导时的候选列表）:

| id | name | auth_type | api_key_env |
|----|------|-----------|-------------|
| openrouter | OpenRouter | api_key | OPENROUTER_API_KEY |
| openai | OpenAI | api_key | OPENAI_API_KEY |
| anthropic | Anthropic | api_key | ANTHROPIC_API_KEY |
| github | GitHub Copilot | oauth | GITHUB_TOKEN |

---

## 2. ModelAlias — 模型别名

**对应 FR-003**

```python
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
```

**约束**:
- `provider` 值必须在 `OctoAgentConfig.providers` 中存在对应 `id`（引用完整性，`@model_validator` 在 `OctoAgentConfig` 层校验）
- `provider` 对应的 `ProviderEntry.enabled` 必须为 `True`（否则同步时报 EC-5 错误）
- 别名 key 支持用户自定义（不限于内置 `main`/`cheap`，对应 Q8 决策）

**内置默认别名**（`octo config init` 时创建）:

| 别名 | 说明 |
|-----|------|
| `main` | 主力模型（高质量请求） |
| `cheap` | 低成本模型（`octo doctor --live` ping 使用） |

---

## 3. RuntimeConfig — 运行时配置

**对应 FR-004**

```python
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
```

**与 `.env` 的优先级关系**（Q3 决策，Option B）:

```
load_provider_config() 读取顺序：
1. octoagent.yaml runtime.llm_mode（若文件存在且字段有值）
2. 降级：环境变量 OCTOAGENT_LLM_MODE
3. 最终默认值："litellm"
```

---

## 4. OctoAgentConfig — 统一配置（根模型）

**对应 FR-001**

```python
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
    def validate_provider_ids_unique(self) -> "OctoAgentConfig":
        """校验 providers 列表中 id 唯一"""
        seen: set[str] = set()
        for p in self.providers:
            if p.id in seen:
                raise ValueError(f"providers 列表中存在重复的 id: '{p.id}'")
            seen.add(p.id)
        return self

    def get_provider(self, provider_id: str) -> ProviderEntry | None:
        """按 id 查找 Provider 条目"""
        return next((p for p in self.providers if p.id == provider_id), None)

    def to_yaml(self) -> str:
        """序列化为 YAML 字符串（添加注释头）"""
        ...

    @classmethod
    def from_yaml(cls, text: str) -> "OctoAgentConfig":
        """解析并校验 YAML 文本，抛出 ConfigParseError（含字段路径）"""
        ...
```

---

## 5. 辅助错误类型

```python
class ConfigParseError(ValueError):
    """octoagent.yaml 解析或 schema 校验失败

    Attributes:
        field_path: 错误字段路径（如 "providers[0].auth_type"）
        message: 人类可读错误描述
    """
    field_path: str
    message: str

class CredentialLeakError(ValueError):
    """检测到可能的明文凭证写入

    当 api_key_env 字段包含 '=' 号或明显是密钥值时抛出。
    """

class ProviderNotFoundError(KeyError):
    """引用了不存在的 Provider ID"""
```

---

## 6. 现有模型修改：ProviderConfig

**文件**: `config.py`

在现有 `ProviderConfig` 中新增 `config_source` 字段，用于记录配置来源（Constitution C8 可观测性）：

```python
class ProviderConfig(BaseModel):
    # ... 现有字段保持不变 ...

    # 新增字段（不影响现有调用方）
    config_source: Literal["octoagent_yaml", "env"] = Field(
        default="env",
        description="配置来源：octoagent_yaml（优先）/ env（降级）",
    )
```

---

## 7. YAML 序列化格式规范

**键名约定**: snake_case（与 Pydantic field 名称一致）

**日期格式**: ISO 8601 日期字符串，仅日期部分（`YYYY-MM-DD`）

**布尔值**: YAML 原生 `true`/`false`（PyYAML 默认行为）

**注释**: 生成文件时在头部和关键字段旁添加注释（使用 `ruamel.yaml` 保留注释，或 `pyyaml` 生成时插入注释字符串）

> **YAML 库选择决策**:
> 使用 `pyyaml`（已在 `pyproject.toml`）而非 `ruamel.yaml`，理由：
> - `pyyaml` 已是现有依赖，无需新增
> - F014 的 `octoagent.yaml` 由工具生成，不需要保留用户手动添加的注释（注释在每次同步时重新生成）
> - `ruamel.yaml` 的注释保留能力在用户手动编辑场景更有价值，可在后续版本升级时引入
>
> 若未来需要保留用户注释，将 `pyyaml` 替换为 `ruamel.yaml` 是零破坏性的内部实现变更。

---

## 8. 实体关系图

```
OctoAgentConfig
├── config_version: int
├── updated_at: str
├── providers: list[ProviderEntry]   ─────────────────────────────┐
│   └── ProviderEntry                                             │
│       ├── id: str (PK)                                         │
│       ├── name: str                                            │
│       ├── auth_type: "api_key" | "oauth"                       │
│       ├── api_key_env: str  ──► .env.litellm 中的 KEY          │
│       └── enabled: bool                                        │
│                                                                 │
├── model_aliases: dict[str, ModelAlias]                          │
│   └── ModelAlias                                               │
│       ├── provider: str  ──────────────── FK → ProviderEntry.id ┘
│       ├── model: str
│       └── description: str
│
└── runtime: RuntimeConfig
    ├── llm_mode: "litellm" | "echo"
    ├── litellm_proxy_url: str
    └── master_key_env: str  ──► .env.litellm 中的 LITELLM_MASTER_KEY
```

---

## 9. 文件系统关系

```
octoagent/                          ← 项目根目录（pyproject.toml 同级）
├── octoagent.yaml                  ← OctoAgentConfig 的持久化形式（纳入版本管理）
├── litellm-config.yaml             ← 由 generate_litellm_config() 推导生成
├── .env                            ← 运行时环境变量（OCTOAGENT_LLM_MODE 等）
└── .env.litellm                    ← API Key 明文（.gitignore，不纳入版本管理）
    ├── OPENROUTER_API_KEY=...
    ├── ANTHROPIC_API_KEY=...
    └── LITELLM_MASTER_KEY=...
```

**信息流方向**:
```
octoagent.yaml (信息源)
    ├──► litellm-config.yaml  (推导生成，litellm_generator.py)
    └──► load_provider_config() (运行时读取，config.py)

用户输入 API Key
    └──► .env.litellm (写入，litellm_generator.generate_env_litellm)
         ← octoagent.yaml 仅引用 env var 名称，不引用实际值
```
