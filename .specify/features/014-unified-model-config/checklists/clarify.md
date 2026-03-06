# 需求澄清分析报告
# Feature 014 - 统一模型配置管理 (Unified Model Config)

**生成日期**: 2026-03-04
**分析者**: Spec Driver 需求澄清子代理
**输入文件**: `.specify/features/014-unified-model-config/spec.md`

---

## 执行摘要

**阶段**: 需求澄清
**状态**: 完成
**检测歧义点**: 7 个
**自动解决**: 5 个（MINOR/MAJOR 级别，采用"信任但验证"策略）
**CRITICAL 问题**: 2 个（需用户决策，影响实现架构或安全边界）

---

## CRITICAL 问题（需用户决策）

### 问题 C1: `.env.litellm` 文件的生命周期策略

**优先级**: CRITICAL
**来源**: spec.md §Clarifications Q2（`[NEEDS CLARIFICATION]`标记）
**背景**:
当前 `octo init` 的职责之一是生成 `.env.litellm`，其中包含 `LITELLM_MASTER_KEY` 和 Provider API Key（如 `OPENAI_API_KEY`）。F014 引入 `octoagent.yaml` 作为单一信息源后，`octo config provider add` 命令需要知道如何处理凭证落盘问题。

已知事实：
- `.env.litellm` 目前同时承载两类数据：系统级 Master Key + Provider API Key
- `octoagent.yaml` 明确不存储凭证值（FR-014, NFR-004），凭证通过 `api_key_env` 字段引用环境变量
- LiteLLM Proxy 在 `docker-compose.litellm.yml` 中会通过 `env_file: .env.litellm` 加载这些变量
- `octo init` 的 `generate_env_litellm_file()` 函数负责将凭证写入该文件

**核心问题**: F014 的 `octo config provider add` 命令在用户输入 API Key 后，凭证应当如何存储和管理？

**选项**:

| 选项 | 描述 | 影响 |
|------|------|------|
| A. 继续维护 `.env.litellm` | `octo config provider add` 仍将 API Key 写入 `.env.litellm`（key=value 格式），与当前 `octo init` 行为一致 | 实现简单，兼容现有 Docker Compose 配置；但 F014 需负责维护 `.env.litellm` 的写入逻辑 |
| B. 只写 Credential Store，用户自行管理 `.env.litellm` | API Key 存入 credential store（`~/.octoagent/`），`octo config` 不写 `.env.litellm`，改由文档说明用户需手动设置 | 符合职责分离；但 UX 体验倒退，用户仍需手动维护两个地方 |
| C. `octo config sync` 同时生成 `.env.litellm` | 同步命令不仅生成 `litellm-config.yaml`，也从 credential store 读取凭证并写入 `.env.litellm` | 实现最完整，完全达成"用户只需管理 `octoagent.yaml`"的目标；但将凭证明文落盘到 `.env.litellm`（本就是现有行为，不是新风险） |

**推荐**: 选项 A（继续维护 `.env.litellm`），理由如下：
1. 现有 `octo init` 已确立该模式，用户心智模型已建立
2. Docker Compose 依赖 `.env.litellm`，改动该依赖需同步修改 `docker-compose.litellm.yml`
3. `octo config` 的核心价值是"增量非破坏性"，不是彻底消灭 `.env.litellm`——后者属于 M3 范围的统一密钥管理

**需要用户确认的决策点**:
- `octo config provider add` 是否将 API Key 写入 `.env.litellm`（选项 A），还是将 `.env.litellm` 的维护完全移出 F014 范围（选项 B）？

---

### 问题 C2: `runtime.llm_mode` 与 `.env` 中 `OCTOAGENT_LLM_MODE` 的同步边界

**优先级**: CRITICAL
**来源**: spec.md §Clarifications Q3（`[NEEDS CLARIFICATION]`标记）
**背景**:
- `octoagent.yaml` 的 `runtime.llm_mode` 字段（FR-004）与 `.env` 中的 `OCTOAGENT_LLM_MODE` 存在语义重叠
- 当前 `octo init` 将 `OCTOAGENT_LLM_MODE` 写入 `.env`（见 `generate_env_file()` 函数），OctoAgent 应用启动时从 `.env` 读取该变量
- `octo config sync` 的当前定义（FR-005）仅涵盖 `octoagent.yaml` -> `litellm-config.yaml`，未明确是否同步 `runtime.*` 字段到 `.env`

**核心问题**: 谁是 `llm_mode` 的单一事实源？这影响运行时启动逻辑的读取路径。

**选项**:

| 选项 | 描述 | 影响 |
|------|------|------|
| A. `octoagent.yaml` 为单一事实源，`sync` 同步到 `.env` | `octo config sync` 将 `runtime.llm_mode` 写入 `.env`，覆盖 `OCTOAGENT_LLM_MODE`。应用运行时仍从 `.env` 读取，但源头是 `octoagent.yaml` | 真正实现单一信息源；但 `sync` 命令承担更多职责，且运行时需要确保 `.env` 已同步才能正确启动 |
| B. 两者独立，`octoagent.yaml` 优先但不覆盖 `.env` | `octoagent.yaml` 的 `runtime.*` 仅供 `octo config` 展示使用，OctoAgent 运行时启动时优先读取 `octoagent.yaml`，降级读取 `.env` | 向前兼容最好；但双信息源可能导致"配置文件说 litellm，实际运行 echo"的混乱 |
| C. `runtime` 块从 `octoagent.yaml` 移除，仅保留 Provider 和别名 | `octoagent.yaml` 只管模型路由相关配置，`llm_mode` 等运行时变量保持在 `.env` 中管理 | 大幅简化范围；但 `octo config` 展示内容不完整（FR-009 要求展示 `llm_mode`） |

**推荐**: 选项 B（两者独立，运行时优先读取 `octoagent.yaml`），理由如下：
1. 避免 `sync` 命令承担过多职责，保持单一职责原则
2. F014 完成后向 M2 演进时，可以将 `.env` 的读取优先级降低，实现渐进迁移
3. 现有 `octo doctor` 的 `check_llm_mode()` 检查基于环境变量，选项 B 不需要修改现有诊断逻辑

**需要用户确认的决策点**:
- `octoagent.yaml` 的 `runtime.*` 字段是否作为运行时启动参数的读取源，还是仅作为配置摘要展示用？如果是读取源，OctoAgent 启动时是否需要修改现有的 `.env` 读取逻辑？

---

## 自动解决的澄清

以下问题已根据"信任但验证"策略自动选择推荐答案，不需要用户介入。

| # | 问题 | 优先级 | 自动选择 | 理由 |
|---|------|--------|---------|------|
| 1 | `octoagent.yaml` 文件位置：项目根目录还是 `octoagent/` 子目录？ | MAJOR | 与 `pyproject.toml` 同级（即 `octoagent/` 目录内） | FR-014 原文"与 pyproject.toml 同级"，当前 `pyproject.toml` 在 `octoagent/` 中，该位置与 `litellm-config.yaml`、`.env` 保持一致 |
| 2 | `octo config migrate` 是否需要在 F014 MVP 中实现？ | MAJOR | 作为 SHOULD 级别实现，但不阻塞 MVP 验收 | EC-6 和 FR-012 均标记为 SHOULD，表示非强制。SC-004 的验收标准描述了迁移成功的场景，但允许手动迁移作为过渡方案 |
| 3 | `octo config provider add` 交互模式：交互式问答还是纯 CLI 参数？ | MINOR | 混合模式：CLI 参数优先，缺失时交互式补全 | 与 `octo init` 的 questionary 交互风格一致，且 `--api-key` 作为可选参数支持脚本化调用（SC-001 独立测试步骤暗示参数化方式） |
| 4 | `octoagent.yaml` 格式错误时的诊断粒度：行号精确度要求 | MINOR | 报告字段路径（如 `providers[0].auth_type`）+ 期望类型，不强制要求 YAML 行号 | Pydantic 的 ValidationError 原生提供 `loc`（字段路径）信息，行号需要额外的 YAML 解析器支持成本高。字段路径对用户更有用 |
| 5 | `octo config alias set` 是否允许添加全新别名（除 main/cheap 外）？ | MINOR | 允许用户自定义别名，不限于内置的 main/cheap | FR-003 说明"允许用户覆盖其映射"但未明确禁止添加新别名。开放扩展符合 Constitution C7（User-in-Control）原则 |

---

## 结构化歧义扫描结果

| 类别 | 状态 | 说明 |
|------|------|------|
| 功能范围与行为 | Partial | C2 问题（runtime 同步边界）影响 FR-004、FR-007 的实现范围 |
| 领域与数据模型 | Clear | `UnifiedConfig`、`ProviderEntry`、`ModelAlias`、`RuntimeConfig` 四个实体定义清晰，字段完整 |
| 交互与 UX 流程 | Partial | C1 问题（`.env.litellm` 维护）影响 `octo config provider add` 的完整交互流程 |
| 非功能质量属性 | Clear | NFR-001 到 NFR-006 定义完整，原子写入、schema 校验、凭证保护均有明确要求 |
| 集成与外部依赖 | Clear | LiteLLM Proxy、docker-compose.litellm.yml 的集成边界在 Out of Scope 中明确排除了 Proxy 重启 |
| 边界条件与异常处理 | Clear | EC-1 到 EC-6 覆盖了主要异常场景，EC-3 和 EC-6 已有 AUTO-RESOLVED 标记 |
| 术语一致性 | Clear | 核心术语（Provider、model_alias、credential store、sync）在规范中使用一致 |

---

## 附录：分析依据

### 关于 C1 的补充分析

查阅现有代码（`init_wizard.py`）确认：
- `generate_env_litellm_file()` 将 `LITELLM_MASTER_KEY` 和 Provider API Key 写入 `.env.litellm`
- `docker-compose.litellm.yml` 通过 `env_file: .env.litellm` 加载这些变量
- LiteLLM 配置中使用 `api_key: "os.environ/OPENAI_API_KEY"` 的方式引用

这意味着 `.env.litellm` 是 LiteLLM Proxy 的运行时依赖，不能被简单地"废弃"而不修改 Docker Compose 配置。F014 的 Out of Scope 已明确排除了"自动触发 LiteLLM Proxy 重启"，因此对 `.env.litellm` 的处置策略需要在 F014 范围内明确。

### 关于 C2 的补充分析

查阅现有代码（`doctor.py`）确认：
- `check_llm_mode()` 直接读取 `os.environ.get("OCTOAGENT_LLM_MODE")`
- `check_proxy_reachable()` 读取 `os.environ.get("LITELLM_PROXY_URL")`

如果 `octoagent.yaml` 的 `runtime.*` 字段成为真正的运行时读取源，则 `check_llm_mode()` 等检查的读取逻辑需要同步更新。这影响 FR-013（`octo doctor` 新增检查项）的实现边界。
