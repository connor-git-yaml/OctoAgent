# Tasks: Feature 014 — 统一模型配置管理 (Unified Model Config)

**Feature Branch**: `feat/014-unified-model-config`
**Created**: 2026-03-04
**Input**: spec.md, plan.md, data-model.md, contracts/cli-api.md
**Prerequisites**: Feature 003 (Auth Adapter + DX 工具) 已交付

**预计工期**: 1-2 天（约 12 个任务）
**测试策略**: 单元测试 + 边界场景 + 条件集成测试（有 API Key 时运行）

---

## 格式说明

- `[P]`: 可并行（不同文件，无直接依赖）
- `[USN]`: 所属 User Story（US1~US6）
- 依赖关系标注在任务描述末尾
- Setup / 基础 / Polish 阶段不标 `[USN]`

---

## Phase 1: 基础设施

**目的**: 创建测试目录骨架和测试工具文件，解除后续所有测试任务的阻塞

- [x] F014-T01 创建测试目录骨架并添加 `__init__.py`，包含两个路径：`packages/provider/tests/dx/__init__.py` 和 `packages/provider/tests/integration/`（若不存在）

---

## Phase 2: 核心数据模型（基础层，阻塞所有后续任务）

**目的**: 实现 `OctoAgentConfig` Pydantic v2 数据模型，其他所有模块依赖此文件
**阻塞**: F014-T01 完成后可立即开始

- [x] F014-T02 实现 Pydantic v2 数据模型文件 `packages/provider/src/octoagent/provider/dx/config_schema.py`，包含以下内容：
  - `ProviderEntry`（id/name/auth_type/api_key_env/enabled，含正则约束）
  - `ModelAlias`（provider/model/description）
  - `RuntimeConfig`（llm_mode/litellm_proxy_url/master_key_env，含默认值）
  - `OctoAgentConfig`（根模型，含 `@model_validator` 校验 provider 引用完整性和 id 唯一性）
  - 辅助错误类：`ConfigParseError`、`CredentialLeakError`、`ProviderNotFoundError`
  - `OctoAgentConfig.to_yaml()` 和 `OctoAgentConfig.from_yaml()` 序列化方法
  - 对应 FR-001 ~ FR-004，data-model.md 全部实体定义

- [x] F014-T03 [P] 实现 schema 单元测试 `packages/provider/tests/dx/test_config_schema.py`，覆盖：
  - 正常解析（含所有字段）
  - `model_aliases.provider` 引用不存在 Provider 时校验失败（EC-5）
  - `providers` 列表 id 重复时校验失败
  - `api_key_env` 包含 `=` 号时 `CredentialLeakError`（NFR-004）
  - `config_version` 不为 1 时提示迁移
  - `to_yaml()` / `from_yaml()` 往返序列化
  - alias 指向 disabled Provider 时警告（EC-5）
  - 依赖：F014-T02

**Checkpoint**: 数据模型稳定后，F014-T04/T05/T06/T07 可同步展开

---

## Phase 3: User Story 2 & 5 — 单一配置文件 + LiteLLM 配置生成（P1）

**目标**: `octoagent.yaml` 作为单一信息源，变更后自动推导出 `litellm-config.yaml`，用户无需手动维护三文件体系

**独立验证**: 运行 `octo config sync`，检查 `litellm-config.yaml` 被正确生成；直接修改 `octoagent.yaml` 后再次 sync，验证推导结果更新

### 实现

- [x] F014-T04 实现增量读写引擎 `packages/provider/src/octoagent/provider/dx/config_wizard.py`，包含以下公共函数：
  - `load_config(project_root: Path) -> OctoAgentConfig | None`（不存在返回 None，格式错误抛 `ConfigParseError`）
  - `save_config(config: OctoAgentConfig, project_root: Path) -> None`（原子写入：写临时文件 + `os.replace`，NFR-003）
  - `wizard_update_provider(config, entry, overwrite=False) -> tuple[OctoAgentConfig, bool]`（增量添加/更新，FR-010）
  - `wizard_update_model(config, alias, model_alias) -> OctoAgentConfig`（更新或新建别名）
  - `wizard_disable_provider(config, provider_id) -> OctoAgentConfig`（设 enabled=False）
  - `validate_no_plaintext_credentials(config) -> None`（凭证泄露检测，NFR-004）
  - 依赖：F014-T02

- [x] F014-T05 [P] 实现 LiteLLM 配置推导引擎 `packages/provider/src/octoagent/provider/dx/litellm_generator.py`，包含：
  - `generate_litellm_config(config, project_root) -> Path`（仅处理 enabled=True Provider；原子写入；打印 EC-3 警告；FR-005/FR-006）
  - `generate_env_litellm(provider_id, api_key, env_var_name, project_root) -> Path`（追加/更新 `.env.litellm`；原子写入；Q2 决策）
  - `check_litellm_sync_status(config, project_root) -> tuple[bool, list[str]]`（用于 `octo doctor`；FR-013）
  - 生成规则：`api_key` 字段格式 `os.environ/{api_key_env}`；头部注释 `# 由 octo config sync 自动生成，请勿手动修改`；`general_settings.master_key` 引用 `runtime.master_key_env`
  - 依赖：F014-T02

### 测试

- [x] F014-T06 [P] 实现 config_wizard 单元测试 `packages/provider/tests/dx/test_config_wizard.py`，覆盖：
  - `load_config` 文件不存在返回 None
  - `load_config` YAML 语法错误时 `ConfigParseError`（EC-1）
  - `save_config` 原子写入（验证临时文件不残留，原子替换后内容正确）
  - `wizard_update_provider` 新增 Provider
  - `wizard_update_provider` 重复添加同 id 时 `overwrite=False` 不覆盖
  - `wizard_disable_provider` 设 enabled=False
  - `validate_no_plaintext_credentials` 检测 api_key_env 格式异常
  - 依赖：F014-T04

- [x] F014-T07 [P] 实现 litellm_generator 单元测试 `packages/provider/tests/dx/test_litellm_generator.py`，覆盖：
  - 生成 `litellm-config.yaml` model_list 条目数与 enabled alias 数一致
  - `api_key` 引用格式为 `os.environ/OPENROUTER_API_KEY`（非明文）
  - 多 Provider 场景：每个 alias 路由到正确 Provider
  - disabled Provider 不产生 model_list 条目
  - 同步检测：in_sync / out_of_sync 返回正确（EC-4）
  - schema 校验失败时不覆盖现有 `litellm-config.yaml`（FR-006）
  - `litellm-config.yaml` 已存在（非工具生成）时打印警告（EC-3）
  - `generate_env_litellm` 凭证缺失时 WARN 不阻断（EC-2）
  - 依赖：F014-T05

**Checkpoint**: Phase 3 完成后，核心配置生成闭环可独立验证

---

## Phase 4: User Story 1 & 3 — octo config CLI 命令组（P1）

**目标**: 提供非破坏性查看与更新入口，支持增量添加 Provider，用户不需要手动编辑文件

**独立验证**:
- 运行 `octo config`，看到格式化摘要
- 运行 `octo config provider add openrouter`，验证 `octoagent.yaml` 新增条目，现有 Provider 不变
- 再次运行同一命令，验证提示"已存在，更新/跳过"而非自动覆盖

### 实现

- [x] F014-T08 实现 Click 命令组 `packages/provider/src/octoagent/provider/dx/config_commands.py`，包含完整命令树：
  - `config`（无子命令）：显示配置摘要，Rich 格式化输出（contracts/cli-api.md §4.1）；文件不存在时友好引导（exit 0）；FR-009
  - `config init [--force] [--echo]`：全量初始化；文件已存在时必须确认（FR-011）；自动触发 sync
  - `config provider add <id> [--auth-type] [--api-key-env] [--name] [--no-credential]`：混合模式（Q6）；重复 ID 询问 update/skip（FR-010）；API Key 写 `.env.litellm` 不写 `octoagent.yaml`（Q2/NFR-004）；自动触发 sync（FR-007）
  - `config provider list`：Rich Table 输出，enabled 绿色/disabled 黄色
  - `config provider disable <id> [--yes]`：设 enabled=False；alias 引用时打印警告；自动触发 sync
  - `config alias list`：Rich Table 输出
  - `config alias set <alias> [--provider] [--model] [--description]`：校验 provider 存在且 enabled（EC-5）；自动触发 sync（FR-007）
  - `config sync [--dry-run]`：完整校验 + 写入摘要（FR-007）；dry-run 仅预览不写文件
  - `config migrate [--dry-run] [--yes]`：SHOULD 级别，未实现时打印提示 exit 0（contracts/cli-api.md §1.9）
  - 所有错误使用中文+字段路径+修复建议（SC-007/NFR-002）；不展示 Python 堆栈
  - 依赖：F014-T04, F014-T05

- [x] F014-T09 修改 `packages/provider/src/octoagent/provider/dx/cli.py`，在 `main` group 中添加一行注册 `config` 命令组：`from .config_commands import config; main.add_command(config)`；不修改现有 `init`、`doctor` 命令（NFR-005）
  - 依赖：F014-T08

**Checkpoint**: Phase 4 完成后，`octo config` 全部子命令可通过 CLI 手动测试

---

## Phase 5: User Story 6 — 运行时配置优先级 + octo doctor 集成（P2）

**目标**: `load_provider_config()` 优先读取 `octoagent.yaml` runtime 块；`octo doctor` 增加配置一致性检查

**独立验证**: 设置 `octoagent.yaml` runtime.llm_mode=echo，启动系统，验证使用 echo 模式而非读取 `.env` 中的 `OCTOAGENT_LLM_MODE`

### 实现

- [x] F014-T10 修改 `packages/provider/src/octoagent/provider/config.py`，更新 `load_provider_config()` 函数：
  - 优先从 `cwd()` 或 `OCTOAGENT_PROJECT_ROOT` 指定目录读取 `octoagent.yaml`
  - 若存在且可解析，从 `runtime.llm_mode`、`runtime.litellm_proxy_url`、`runtime.master_key_env` 取值（Q3 决策）
  - 降级到原有环境变量读取，记录 structlog debug 日志
  - 在 `ProviderConfig` 新增 `config_source: Literal["octoagent_yaml", "env"] = "env"` 字段（Constitution C8）
  - 不破坏现有 `test_config.py` 测试（NFR-005）
  - 依赖：F014-T02

- [x] F014-T11 [P] 修改 `packages/provider/src/octoagent/provider/dx/doctor.py`，追加两个新检查项（不修改现有签名）：
  - `check_octoagent_yaml_valid`：读取并校验 `octoagent.yaml` 格式（RECOMMENDED 级别）；不存在时跳过不报错（Constitution C6）
  - `check_litellm_sync`：调用 `check_litellm_sync_status()` 检测两文件一致性（WARN 级别）；不一致时 `fix_hint` 提示 `octo config sync`（FR-013/SC-005）
  - 依赖：F014-T05

**Checkpoint**: Phase 5 完成后，运行时配置读取和 doctor 诊断均与新配置体系对齐

---

## Phase 6: User Story 4 — 模型别名管理透明化（P2）

**目标**: 用户可通过 `octo config alias list` 和 `octo config alias set` 管理别名映射

**说明**: 此功能依赖 F014-T08 中已实现的 `alias list` 和 `alias set` 子命令，本阶段补充集成测试和 example 文件

**独立验证**: 运行 `octo config alias list` 查看别名表格；运行 `octo config alias set main --provider anthropic --model claude-opus-4-20250514`，验证 `octoagent.yaml` 和 `litellm-config.yaml` 同步更新

- [x] F014-T12 [P] 创建示例配置文件 `octoagent/octoagent.yaml.example`，包含标准 YAML 格式（含注释）、openrouter 示例 Provider、main/cheap 默认别名、默认 runtime 配置；纳入版本管理（plan.md §新增文件5）

---

## Phase 7: 集成测试与 Polish

**目标**: 端到端集成测试、确保所有边界场景覆盖，以及 docstring 完善

- [x] F014-T13 [P] 实现条件集成测试 `packages/provider/tests/integration/test_live_llm.py`，使用 `pytest.mark.skipif` 在无 `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` 时跳过：
  - 完整流程：构造 `octoagent.yaml` → 生成 `litellm-config.yaml` → 验证文件格式正确（不启动 Proxy，仅验证配置完整性）
  - 若环境变量存在：调用真实 LLM cheap 别名，验证返回有效响应（SC-004）
  - 依赖：F014-T04, F014-T05, F014-T08

---

## FR 覆盖映射表

| 功能需求 | 覆盖任务 |
|---------|---------|
| FR-001 (`OctoAgentConfig` 结构) | F014-T02, F014-T03 |
| FR-002 (`ProviderEntry` 字段) | F014-T02, F014-T03 |
| FR-003 (`ModelAlias` + 内置别名) | F014-T02, F014-T03 |
| FR-004 (`RuntimeConfig` 块) | F014-T02, F014-T03 |
| FR-005 (同步推导 litellm-config.yaml) | F014-T05, F014-T07 |
| FR-006 (校验失败不写文件) | F014-T05, F014-T07 |
| FR-007 (自动触发同步 + 摘要输出) | F014-T08 |
| FR-008 (`octo config` 命令组) | F014-T08, F014-T09 |
| FR-009 (无子命令显示摘要) | F014-T08 |
| FR-010 (非破坏性 provider add) | F014-T04, F014-T08 |
| FR-011 (`config init` 覆盖确认) | F014-T08 |
| FR-012 (`config migrate` SHOULD 级别) | F014-T08（打印占位提示）|
| FR-013 (`octo doctor` 一致性检查) | F014-T05, F014-T11 |
| FR-014 (配置文件位置 + 不含凭证) | F014-T04, F014-T08, F014-T12 |

**NFR 覆盖**:

| 非功能需求 | 覆盖任务 |
|-----------|---------|
| NFR-001 (sync < 1s 纯本地) | F014-T05（无网络调用）|
| NFR-002 (人类可读错误 + 字段路径) | F014-T02, F014-T08 |
| NFR-003 (原子写入) | F014-T04, F014-T05 |
| NFR-004 (禁止明文凭证) | F014-T02, F014-T04, F014-T08 |
| NFR-005 (不破坏 octo init/doctor) | F014-T09, F014-T11 |

**Edge Case 覆盖**:

| 边界场景 | 覆盖任务 |
|---------|---------|
| EC-1 (YAML 语法错误) | F014-T06 (`test_load_config_invalid_yaml`) |
| EC-2 (凭证引用缺失 WARN) | F014-T07 (`test_sync_warns_on_missing_credential_env`) |
| EC-3 (litellm-config 手动修改警告) | F014-T07 (`test_generate_overwrites_with_warning`) |
| EC-4 (doctor 检测不一致) | F014-T07 (`test_check_litellm_sync_status`) |
| EC-5 (alias 指向 disabled Provider) | F014-T03 (`test_validate_alias_disabled_provider`) |

---

## 依赖关系与并行策略

### Phase 依赖链

```
F014-T01 (目录骨架)
    └─► F014-T02 (config_schema.py)
            ├─► F014-T03 [P] (test_config_schema.py)
            ├─► F014-T04 (config_wizard.py)
            │       └─► F014-T06 [P] (test_config_wizard.py)
            ├─► F014-T05 [P] (litellm_generator.py)
            │       └─► F014-T07 [P] (test_litellm_generator.py)
            └─► F014-T10 (config.py 修改)
F014-T04 + F014-T05
    └─► F014-T08 (config_commands.py)
            ├─► F014-T09 (cli.py 修改)
            └─► F014-T13 [P] (test_live_llm.py)
F014-T05
    └─► F014-T11 [P] (doctor.py 修改)
F014-T12 [P] (yaml.example) — 无依赖，可随时进行
```

### 关键并行机会

1. F014-T03 / F014-T04 / F014-T05 / F014-T10 —— F014-T02 完成后四路并行
2. F014-T06 / F014-T07 —— 分别在 T04 / T05 完成后并行开始
3. F014-T11 / F014-T12 / F014-T13 —— 可与 F014-T09 同步进行
4. 约 **50%** 任务可并行（6/13 个任务标注 [P]）

### 推荐实现策略：MVP First

1. 完成 **F014-T01 → T02**（基础数据模型）
2. 并行推进 **T04 + T05**（读写引擎 + 生成引擎）
3. 完成 **T08 → T09**（CLI 命令组注册）
4. **停止验证**：手动运行 `octo config provider add` 和 `octo config sync`，确认端到端流程
5. 补充 **T03 + T06 + T07**（单元测试覆盖）
6. 完成 **T10 + T11**（运行时集成）
7. 补充 **T12 + T13**（示例文件 + 集成测试）

**MVP 边界**（P1 User Stories 完整交付）: T01 → T02 → T04 → T05 → T08 → T09，对应 US1/US2/US3/US5

---

## 任务汇总

| 任务 ID | 类型 | 关联 User Story | 文件路径 | 可并行 |
|---------|------|----------------|---------|-------|
| F014-T01 | Setup | — | `tests/dx/__init__.py` | 否 |
| F014-T02 | 实现 | US2/US5 | `dx/config_schema.py` | 否 |
| F014-T03 | 测试 | US2/US5 | `tests/dx/test_config_schema.py` | 是 |
| F014-T04 | 实现 | US1/US3 | `dx/config_wizard.py` | 是（T02后）|
| F014-T05 | 实现 | US2/US5 | `dx/litellm_generator.py` | 是（T02后）|
| F014-T06 | 测试 | US1/US3 | `tests/dx/test_config_wizard.py` | 是 |
| F014-T07 | 测试 | US2/US5 | `tests/dx/test_litellm_generator.py` | 是 |
| F014-T08 | 实现 | US1/US3/US4 | `dx/config_commands.py` | 否 |
| F014-T09 | 实现（修改） | US1/US3 | `dx/cli.py` | 否 |
| F014-T10 | 实现（修改） | US6 | `provider/config.py` | 是（T02后）|
| F014-T11 | 实现（修改） | US6 | `dx/doctor.py` | 是 |
| F014-T12 | 文档 | US2 | `octoagent/octoagent.yaml.example` | 是 |
| F014-T13 | 集成测试 | US6 | `tests/integration/test_live_llm.py` | 是 |

**总计**: 13 个任务，覆盖 6 个 User Stories，全部 14 条 FR，100% FR 覆盖率
