# Contract: CLI API — `octo config` 命令组

**Feature**: 014-unified-model-config
**Created**: 2026-03-04
**Traces to**: FR-008, FR-009, FR-010, FR-011, FR-012, NFR-005

---

## 契约范围

本文档定义 `octo config` 命令组（Click group）的完整接口契约，包括：
- 命令签名（参数、选项、返回码）
- 交互行为规范（混合模式：CLI 参数优先，缺失时交互补全）
- 输出格式规范
- 错误处理规范
- 与现有命令的共存保证

---

## 1. 命令组：`octo config`

### 1.1 `octo config`（无子命令）

**描述**: 显示当前 `octoagent.yaml` 配置摘要。

**签名**:
```
octo config [--yaml-path PATH]
```

**选项**:
| 选项 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `--yaml-path` | Path | `./octoagent.yaml` | 指定配置文件路径（供测试用） |

**行为**:
- 若 `octoagent.yaml` 存在且有效：打印格式化摘要（见 §4 输出格式）
- 若 `octoagent.yaml` 不存在：打印提示，建议运行 `octo config init` 或 `octo config provider add`，**正常退出（exit code 0）**
- 若 `octoagent.yaml` 存在但格式错误：打印具体错误（字段路径），**exit code 1**

**返回码**:
- `0`: 成功（包括文件不存在但友好提示的情况）
- `1`: 文件格式错误或内部错误

---

### 1.2 `octo config init`

**描述**: 全量初始化 `octoagent.yaml`（首次使用或重置）。

**签名**:
```
octo config init [--force] [--echo]
```

**选项**:
| 选项 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `--force` | flag | False | 跳过已有配置文件的确认提示 |
| `--echo` | flag | False | 直接初始化为 echo 模式（非交互式，供 CI 使用） |

**行为**:
- 若 `octoagent.yaml` 已存在且未传 `--force`：**必须要求用户显式确认**（FR-011），确认前打印当前配置摘要
- 确认后：交互式引导填写 provider、model_aliases、runtime 配置
- 完成后自动调用 sync 逻辑生成 `litellm-config.yaml`
- 打印生成的文件路径列表

**禁止行为**:
- 不得在用户未确认的情况下覆盖已有 `octoagent.yaml`（FR-011）

**返回码**:
- `0`: 初始化成功
- `1`: 用户取消或写入失败

---

### 1.3 `octo config provider add <id>`

**描述**: 增量添加新 Provider，或更新已有 Provider 的配置。

**签名**:
```
octo config provider add <PROVIDER_ID> [--auth-type TYPE] [--api-key-env VAR_NAME]
```

**位置参数**:
| 参数 | 类型 | 必填 | 描述 |
|-----|------|------|------|
| `PROVIDER_ID` | str | 是 | Provider 标识（如 `openrouter`、`anthropic`） |

**选项**:
| 选项 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `--auth-type` | `api_key\|oauth` | 交互式选择 | 认证类型 |
| `--api-key-env` | str | 推断或交互式输入 | 凭证环境变量名（如 `OPENROUTER_API_KEY`） |
| `--name` | str | 自动推断 | Provider 显示名称 |
| `--no-credential` | flag | False | 仅注册 Provider 条目，不写 API Key（适用于 OAuth 或环境变量已有值的场景） |

**混合模式规则（Q6 决策）**:
1. 读取所有 CLI 参数
2. 缺失的必要信息（如 API Key 值）通过 questionary 交互补全
3. 若终端非 TTY（如 CI 管道）且缺少必要参数，报错退出而非卡住等待输入

**行为**:
1. 加载现有 `octoagent.yaml`（不存在则创建空配置）
2. 检查 Provider ID 是否已存在：
   - 已存在：询问用户"更新（u）/ 跳过（s）"，不自动覆盖（FR-010）
   - 不存在：引导输入配置
3. 若需要 API Key：通过 `questionary.password()` 安全输入，**写入 `.env.litellm`**（Q2 决策），`octoagent.yaml` 仅记录 `api_key_env` 变量名
4. 写入 `octoagent.yaml`（原子写入，NFR-003）
5. 自动触发同步：生成/更新 `litellm-config.yaml`（FR-007）
6. 打印摘要：`已添加 Provider: openrouter (api_key_env=OPENROUTER_API_KEY)`

**禁止行为**:
- 不得将 API Key 明文写入 `octoagent.yaml`（Constitution C5，NFR-004）
- 检测到明文凭证时，`CredentialLeakError` 阻断写入并报错

**返回码**:
- `0`: 添加/更新成功，或用户选择跳过
- `1`: 写入失败或 schema 校验失败

---

### 1.4 `octo config provider list`

**描述**: 列出所有已配置的 Provider 及状态。

**签名**:
```
octo config provider list
```

**输出格式**（Rich Table）:
```
Provider 列表
─────────────────────────────────────────────────────
  ID           名称           认证类型    环境变量              状态
  openrouter   OpenRouter     api_key    OPENROUTER_API_KEY    enabled
  anthropic    Anthropic      api_key    ANTHROPIC_API_KEY     disabled
─────────────────────────────────────────────────────
```

**行为**:
- 若 `octoagent.yaml` 不存在：提示未配置，建议运行 `octo config provider add`，exit 0
- enabled 列颜色：`enabled` 绿色，`disabled` 黄色

---

### 1.5 `octo config provider disable <id>`

**描述**: 禁用（不删除）指定 Provider。

**签名**:
```
octo config provider disable <PROVIDER_ID> [--yes]
```

**选项**:
| 选项 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `--yes` | flag | False | 跳过确认提示 |

**行为**:
1. 将 `octoagent.yaml` 中该 Provider 的 `enabled` 设为 `false`
2. 若有 `model_aliases` 引用该 Provider，打印警告（不阻断，但建议更新 alias）
3. 自动触发同步：`litellm-config.yaml` 中移除该 Provider 的条目
4. 打印确认信息

**返回码**:
- `0`: 禁用成功
- `1`: Provider 不存在或写入失败

---

### 1.6 `octo config alias list`

**描述**: 列出所有模型别名及映射关系。

**签名**:
```
octo config alias list
```

**输出格式**（Rich Table）:
```
Model Aliases
─────────────────────────────────────────────────────────────────────
  别名    Provider      模型字符串              描述
  main    openrouter    openrouter/auto        主力模型别名
  cheap   openrouter    openrouter/auto        低成本模型别名
─────────────────────────────────────────────────────────────────────
```

---

### 1.7 `octo config alias set <alias>`

**描述**: 更新或新建指定别名的映射。

**签名**:
```
octo config alias set <ALIAS> [--provider PROVIDER_ID] [--model MODEL_STR] [--description DESC]
```

**位置参数**:
| 参数 | 类型 | 必填 | 描述 |
|-----|------|------|------|
| `ALIAS` | str | 是 | 别名名称（如 `main`、`cheap`、或用户自定义） |

**选项**:
| 选项 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `--provider` | str | 交互选择 | Provider ID |
| `--model` | str | 交互输入 | LiteLLM 模型字符串 |
| `--description` | str | 交互输入（可留空） | 别名描述 |

**行为**:
1. 校验 `--provider` 存在于 `providers` 列表且 `enabled=True`（EC-5）
2. 更新 `model_aliases.<alias>` 条目（新建或覆盖）
3. 原子写入 `octoagent.yaml`
4. 自动触发同步（FR-007）
5. 打印确认

**校验错误处理**:
- Provider 不存在：`错误：Provider 'xxx' 未配置，请先运行 octo config provider add xxx`
- Provider 已禁用：`警告：Provider 'xxx' 已禁用，请先运行 octo config provider enable xxx 或选择其他 Provider`

---

### 1.8 `octo config sync`

**描述**: 手动触发从 `octoagent.yaml` 到 `litellm-config.yaml` 的同步。

**签名**:
```
octo config sync [--dry-run]
```

**选项**:
| 选项 | 类型 | 默认值 | 描述 |
|-----|------|--------|------|
| `--dry-run` | flag | False | 仅显示将要写入的内容，不实际写文件 |

**行为**:
1. 读取 `octoagent.yaml` 并完整校验（schema + 引用完整性）
2. 校验失败：打印错误，不写入 `litellm-config.yaml`，现有文件保持不变（FR-006）
3. 若 `litellm-config.yaml` 已存在且非工具生成（无标记注释）：打印警告（EC-3），不阻断
4. 生成并原子写入 `litellm-config.yaml`
5. 打印写入路径和摘要（FR-007）

**摘要输出格式**:
```
同步完成
  写入: /path/to/octoagent/litellm-config.yaml
  包含 2 个 model aliases（main, cheap）
  基于 1 个 enabled Provider（openrouter）
```

**NFR-001 保证**: 同步操作为纯本地文件操作，不进行网络调用，目标完成时间 < 1 秒。

**返回码**:
- `0`: 同步成功（或 --dry-run 预览成功）
- `1`: schema 校验失败或文件写入失败

---

### 1.9 `octo config migrate`（SHOULD 级别，非 MVP 阻断）

**描述**: 从旧三文件体系（`.env` + `.env.litellm` + `litellm-config.yaml`）读取配置并生成 `octoagent.yaml`。

**签名**:
```
octo config migrate [--dry-run] [--yes]
```

**行为**:
1. 读取 `.env`（`OCTOAGENT_LLM_MODE` 等运行时变量）
2. 读取 `.env.litellm`（Provider API Key 和 Master Key）
3. 读取 `litellm-config.yaml`（model_list 条目，推导 Providers 和 model_aliases）
4. 生成 `octoagent.yaml`（原子写入）
5. 原文件不删除（用户手动清理）

**注意**: 此命令标记为 SHOULD 级别，在 MVP 交付时可能尚未实现。若未实现，`octo config migrate` 应打印"此命令尚未实现，计划在后续版本提供"并 exit 0。

---

## 2. 与现有命令的共存契约（NFR-005）

| 现有命令 | 共存保证 |
|---------|---------|
| `octo init` | 完全不变，不删除、不修改命令路径和行为 |
| `octo init --manual-oauth` | 完全不变 |
| `octo doctor` | 不变，新增 2 个检查项（`octoagent_yaml_valid`、`litellm_sync`）通过追加方式添加 |
| `octo doctor --live` | 不变 |

**注册方式**:
```python
# cli.py 修改：仅添加以下一行
from .config_commands import config
main.add_command(config)
```

---

## 3. 错误处理契约

### 3.1 通用错误格式

所有错误信息必须满足（SC-007，NFR-002）：
- 使用中文描述
- 包含具体字段路径（如 `providers[0].auth_type`）
- 包含修复建议（如 `请运行 octo config provider add openrouter`）
- 不展示 Python 堆栈信息（对用户不友好，使用 `except ... as exc: console.print(...)` 包装）

### 3.2 错误码与对应场景

| exit code | 场景 |
|----------|------|
| 0 | 成功，或文件不存在时友好提示 |
| 1 | schema 校验失败、写入失败、引用完整性错误、凭证泄露检测 |
| 130 | 用户按 Ctrl+C 中断 |

### 3.3 YAML 格式错误输出示例

```
错误：octoagent.yaml 格式无效

  字段路径: providers[0].auth_type
  错误说明: 值 'password' 不是合法的认证类型，允许值: api_key, oauth

修复建议：
  1. 编辑 octoagent.yaml，将 providers[0].auth_type 改为 'api_key' 或 'oauth'
  2. 或运行 octo config init 重新生成配置
```

---

## 4. 输出格式规范

### 4.1 `octo config`（无子命令）摘要格式

```
OctoAgent 配置摘要
══════════════════════════════════════════════
配置文件: /path/to/octoagent/octoagent.yaml
版本: 1  |  最后更新: 2026-03-04

Providers（1 个启用 / 2 个配置）:
  ID           名称          状态
  openrouter   OpenRouter    enabled
  anthropic    Anthropic     disabled

Model Aliases（2 个）:
  别名    →  Provider      模型字符串
  main    →  openrouter    openrouter/auto
  cheap   →  openrouter    openrouter/auto

Runtime:
  llm_mode:          litellm
  litellm_proxy_url: http://localhost:4000
  master_key_env:    LITELLM_MASTER_KEY

配置来源: octoagent.yaml（优先级高于 .env）
══════════════════════════════════════════════
```

### 4.2 未配置时的引导提示

```
尚未找到 octoagent.yaml 配置文件。

快速开始：
  octo config provider add openrouter   # 添加 Provider 并自动初始化配置
  octo config init                      # 全量交互式初始化

旧版用户：若已有 .env / .env.litellm / litellm-config.yaml，
  运行 octo config migrate 自动迁移配置。
```

---

## 5. 文件路径解析顺序

所有命令在确定 `octoagent.yaml` 位置时遵循以下顺序：
1. `--yaml-path` CLI 参数（若有）
2. `OCTOAGENT_PROJECT_ROOT` 环境变量指定的目录 + `octoagent.yaml`
3. `Path.cwd() / "octoagent.yaml"`

此顺序保证 CI 环境和本地开发环境的一致性，也方便测试时通过 `tmp_path` 注入临时目录。
