# Contract: `octoagent.yaml` Schema 规范

**Feature**: 014-unified-model-config
**Created**: 2026-03-04
**Traces to**: FR-001 ~ FR-004, NFR-002, NFR-004, Constitution C5

---

## 契约范围

本文档定义 `octoagent.yaml` 文件的完整 schema 规范，包括：
- 顶层结构
- 每个字段的类型、约束、默认值
- 版本兼容性规则
- 凭证安全规则

---

## 完整 Schema（Pydantic v2 注解风格）

```yaml
# ── 版本元信息 ────────────────────────────────────────────────
config_version: integer           # REQUIRED, 当前为 1, ge=1
updated_at: string                # REQUIRED, ISO 8601 日期（YYYY-MM-DD）

# ── Providers ─────────────────────────────────────────────────
providers:                        # list[ProviderEntry], 默认 []
  - id: string                    # REQUIRED, 全局唯一, 正则 ^[a-z0-9_-]+$
    name: string                  # REQUIRED, 显示名称, min_length=1
    auth_type: "api_key"|"oauth"  # REQUIRED, 认证类型
    api_key_env: string           # REQUIRED, 正则 ^[A-Z][A-Z0-9_]*$
    enabled: boolean              # OPTIONAL, 默认 true

# ── Model Aliases ─────────────────────────────────────────────
model_aliases:                    # dict[str, ModelAlias], 默认 {}
  "<alias_name>":
    provider: string              # REQUIRED, 必须存在于 providers[].id
    model: string                 # REQUIRED, LiteLLM 模型字符串, min_length=1
    description: string           # OPTIONAL, 默认 ""

# ── Runtime ───────────────────────────────────────────────────
runtime:                          # RuntimeConfig, 默认见下方
  llm_mode: "litellm"|"echo"     # OPTIONAL, 默认 "litellm"
  litellm_proxy_url: string      # OPTIONAL, 默认 "http://localhost:4000"
  master_key_env: string         # OPTIONAL, 正则 ^[A-Z][A-Z0-9_]*$, 默认 "LITELLM_MASTER_KEY"
```

---

## 字段详细规范

### 顶层字段

| 字段 | 类型 | 必填 | 约束 | 默认值 |
|-----|------|------|------|--------|
| `config_version` | int | 是 | `>= 1` | — |
| `updated_at` | str | 是 | `YYYY-MM-DD` 格式 | — |
| `providers` | list | 否 | 元素唯一（按 `id`） | `[]` |
| `model_aliases` | dict | 否 | key 为 string，value 为 ModelAlias | `{}` |
| `runtime` | object | 否 | 见 RuntimeConfig 规范 | 见默认值 |

### ProviderEntry 字段

| 字段 | 类型 | 必填 | 约束 | 备注 |
|-----|------|------|------|------|
| `id` | str | 是 | `^[a-z0-9_-]+$`, min_length=1 | 在列表内唯一 |
| `name` | str | 是 | min_length=1 | 显示名称 |
| `auth_type` | str | 是 | `"api_key"` 或 `"oauth"` | 枚举 |
| `api_key_env` | str | 是 | `^[A-Z][A-Z0-9_]*$` | 凭证所在环境变量**名称**（非值） |
| `enabled` | bool | 否 | 无 | 默认 `true` |

### ModelAlias 字段

| 字段 | 类型 | 必填 | 约束 | 备注 |
|-----|------|------|------|------|
| `provider` | str | 是 | 必须存在于 `providers[].id` | 跨实体引用完整性 |
| `model` | str | 是 | min_length=1 | LiteLLM 模型字符串 |
| `description` | str | 否 | 无 | 默认 `""` |

### RuntimeConfig 字段

| 字段 | 类型 | 必填 | 约束 | 默认值 |
|-----|------|------|------|--------|
| `llm_mode` | str | 否 | `"litellm"` 或 `"echo"` | `"litellm"` |
| `litellm_proxy_url` | str | 否 | 合法 URL 格式 | `"http://localhost:4000"` |
| `master_key_env` | str | 否 | `^[A-Z][A-Z0-9_]*$` | `"LITELLM_MASTER_KEY"` |

---

## 跨字段校验规则（model_validator）

### 规则 1：model_aliases 引用完整性

所有 `model_aliases.<alias>.provider` 的值必须出现在 `providers[].id` 列表中。

**错误示例**:
```yaml
providers:
  - id: openrouter
    ...
model_aliases:
  main:
    provider: anthropic   # ERROR: 'anthropic' 未在 providers 中定义
    model: claude-opus-4
```

**错误消息**: `model_aliases.main.provider='anthropic' 未在 providers 列表中找到（可用 id: ['openrouter']）`

### 规则 2：providers.id 唯一性

`providers` 列表中所有条目的 `id` 字段必须互不相同。

**错误示例**:
```yaml
providers:
  - id: openrouter
    ...
  - id: openrouter   # ERROR: 重复 id
    ...
```

**错误消息**: `providers 列表中存在重复的 id: 'openrouter'`

### 规则 3：禁止明文凭证（Constitution C5）

`api_key_env` 字段若包含 `=` 号，视为用户误将 `KEY=value` 写成了 `api_key_env: "OPENROUTER_API_KEY=sk-xxx"`，触发 `CredentialLeakError`。

凭证泄露的额外检测模式（SHOULD 级别）：
- 值以 `sk-` 开头（OpenAI/Anthropic API Key 前缀）
- 值以 `or-v1-` 开头（OpenRouter API Key 前缀）
- 值长度 > 30 且非全大写（非环境变量名格式）

---

## 版本兼容性

### 当前版本：`config_version: 1`

- 解析器 MUST 支持 `config_version: 1`
- 解析器遇到 `config_version: 2` 或更高时 MUST 打印警告并建议运行迁移命令，但不阻止读取（向前兼容）

### 版本升级策略（future）

当需要引入破坏性字段变更时：
1. `config_version` 递增
2. 提供 `octo config migrate` 自动升级旧格式
3. 旧版解析逻辑保留（至少保留两个版本）

---

## 安全约束摘要

| 约束 | 强制程度 | 实现位置 |
|-----|---------|---------|
| `api_key_env` 只存环境变量**名称**，不存值 | MUST（Constitution C5） | `ProviderEntry` 字段正则校验 |
| `octoagent.yaml` 写入前扫描明文凭证 | MUST（NFR-004） | `validate_no_plaintext_credentials()` |
| 文件权限 | SHOULD | 生成时 `chmod 644`（不含可执行位） |
| 纳入版本管理 | MUST（FR-014） | 项目 `.gitignore` 不排除此文件 |

---

## 完整示例（两 Provider）

```yaml
# OctoAgent 统一配置文件
# 此文件安全纳入版本管理（不含凭证）
# API Key 存储在 .env.litellm（已在 .gitignore）
# 由 octo config 命令管理，也支持直接手动编辑

config_version: 1
updated_at: "2026-03-04"

providers:
  - id: openrouter
    name: OpenRouter
    auth_type: api_key
    api_key_env: OPENROUTER_API_KEY
    enabled: true

  - id: anthropic
    name: Anthropic
    auth_type: api_key
    api_key_env: ANTHROPIC_API_KEY
    enabled: false

model_aliases:
  main:
    provider: openrouter
    model: openrouter/auto
    description: 主力模型别名（高质量请求）
  cheap:
    provider: openrouter
    model: openrouter/auto
    description: 低成本模型别名（doctor --live ping 使用）

runtime:
  llm_mode: litellm
  litellm_proxy_url: http://localhost:4000
  master_key_env: LITELLM_MASTER_KEY
```
