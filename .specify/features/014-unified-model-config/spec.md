---
feature_id: "014"
title: "统一模型配置管理 (Unified Model Config)"
milestone: "M1.5"
status: "Draft"
created: "2026-03-04"
research_mode: "skip"
research_skip_reason: "用户指定（需求描述中已包含充分的调研背景）"
blueprint_ref: "docs/blueprint.md §12.9"
predecessor: "Feature 003 (Auth Adapter + DX 工具，已交付)"
---

# Feature Specification: 统一模型配置管理 (Unified Model Config)

**Feature Branch**: `feat/014-unified-model-config`
**Created**: 2026-03-04
**Status**: Draft
**Input**: 将 OctoAgent 分散的多文件配置统一到单一 `octoagent.yaml`，并将 `octo init` 进化为增量式 `octo config` CLI，使系统能够方便地为用户提供真实 LLM 服务。
**Blueprint 依据**: §12.9（DX 工具）、Constitution C6（优雅降级）、Constitution C7（User-in-Control）
**[无调研基础]**: 本特性跳过独立调研阶段，基于需求描述中内嵌的调研背景直接生成规范。

---

## 问题陈述 (Problem Statement)

当前 OctoAgent 的配置系统存在以下痛点，阻碍用户流畅地配置并使用真实 LLM 服务：

1. **配置分散**：用户需要同时维护三个独立文件——`.env`（运行时环境变量）、`.env.litellm`（Proxy 凭证环境变量）、`litellm-config.yaml`（模型路由配置）。三个文件之间没有显式关联，修改一处需要手动同步其他文件。

2. **破坏性初始化**：现有 `octo init` 每次运行都会询问是否覆盖所有配置，缺乏增量更新能力。用户无法仅添加一个新 Provider 而保留其他设置。

3. **模型别名不透明**：`main`/`cheap` 这两个别名是系统内部约定，用户不清楚它们对应哪个真实模型，也无法在不理解 LiteLLM 配置语法的情况下修改映射。

4. **难以切换 Provider**：若用户想从 OpenRouter 切换到 Anthropic，需要手动编辑至少两个文件，且容易遗漏或产生不一致。

**目标状态**：完成 F014 后，所有模型和 Provider 配置集中在单一 `octoagent.yaml` 文件中，`octo config` 命令支持非破坏性的增量管理，用户可以在 5 分钟内完成 Provider 切换并通过 `octo doctor --live` 验证真实 LLM 调用成功。

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 非破坏性查看与更新配置 (Priority: P1)

作为已完成初始配置的 OctoAgent 用户，我希望运行 `octo config` 查看当前配置摘要，并能够只修改我关心的配置项（例如切换 Provider 或更新 API Key），而不触碰其他已有的设置。

**Why this priority**: 这是 F014 存在的核心理由。破坏性的 `octo init` 是当前最大的 DX 痛点——用户每次配置变更都面临"全量覆盖还是放弃"的两难。非破坏性更新是最高优先级。

**Independent Test**: 可通过以下步骤独立测试：（1）先完成一次完整配置（Provider A + API Key A）；（2）运行 `octo config`，选择只更新 API Key；（3）验证 `octoagent.yaml` 中 API Key 已更新，Provider 配置、model_aliases、runtime 等其他字段保持不变。

**Acceptance Scenarios**:

1. **Given** 用户已有有效的 `octoagent.yaml` 配置文件，**When** 运行 `octo config` 不带任何子命令，**Then** 系统显示当前配置摘要，包括已启用的 Providers、model_aliases 与真实模型的映射关系、以及运行时参数，不对配置做任何修改。

2. **Given** 用户的 `octoagent.yaml` 已有 OpenRouter Provider 配置，**When** 用户运行 `octo config` 并选择"仅更新 API Key"，**Then** 系统提示输入新 API Key，更新 `octoagent.yaml` 中该 Provider 的凭证引用，其他 Provider、model_aliases 和 runtime 配置保持不变。

3. **Given** `octoagent.yaml` 不存在，**When** 运行 `octo config`，**Then** 系统提示用户尚未完成配置，引导用户运行 `octo config init` 或 `octo config provider add` 开始初始化，不报错退出。

---

### User Story 2 - 单一配置文件统一管理 (Priority: P1)

作为 OctoAgent 用户，我希望所有与模型和 Provider 相关的配置都集中在 `octoagent.yaml` 一个文件中，这样我知道去哪里查找和修改配置，不需要了解 `.env`、`.env.litellm`、`litellm-config.yaml` 三个文件的分工。

**Why this priority**: 单一信息源是配置系统可维护性的基础。三文件模型将内部实现细节暴露给用户，是当前 DX 问题的根本原因。

**Independent Test**: 可通过以下步骤独立测试：运行 `octo config provider add openrouter --api-key <key>`，检查：（1）`octoagent.yaml` 被创建/更新；（2）`litellm-config.yaml` 被自动推导生成；（3）用户无需手动编辑 `.env` 或 `litellm-config.yaml`。

**Acceptance Scenarios**:

1. **Given** 用户完成 `octo config provider add openrouter` 流程，**When** 检查项目文件，**Then** 用户关心的配置（Provider、模型别名、运行时参数）均在 `octoagent.yaml` 中，用户无需理解 `.env.litellm` 或 `litellm-config.yaml` 的格式即可管理配置。

2. **Given** `octoagent.yaml` 中的 Provider 或 model_aliases 发生变化，**When** 变化被保存，**Then** 系统自动将 `octoagent.yaml` 中的配置推导并写入 `litellm-config.yaml`，两个文件始终保持一致，用户无需手动同步。

3. **Given** 用户直接编辑了 `octoagent.yaml` 文件，**When** 运行 `octo config`，**Then** 系统读取修改后的文件并展示最新配置，手动编辑与命令行管理两种方式均被支持。

---

### User Story 3 - 增量添加新 Provider (Priority: P1)

作为希望扩展 LLM Provider 的用户，我希望通过 `octo config provider add <id>` 命令增量添加新 Provider，而无需重新配置整个系统，使得我可以在保留现有 OpenRouter 配置的同时添加 Anthropic 作为备用 Provider。

**Why this priority**: 多 Provider 支持是 OctoAgent 架构的核心优势。增量添加是"非破坏性更新"故事在 Provider 维度的具体实现，直接影响用户配置 fallback 策略的能力。

**Independent Test**: 可通过以下步骤独立测试：（1）系统已有 OpenRouter 配置；（2）运行 `octo config provider add anthropic --auth-type api_key`，按提示输入 API Key；（3）验证 `octoagent.yaml` 的 providers 列表中新增了 anthropic，OpenRouter 配置未改变；（4）验证 `litellm-config.yaml` 被重新生成并包含两个 Provider 的模型条目。

**Acceptance Scenarios**:

1. **Given** `octoagent.yaml` 中已有 openrouter Provider 配置，**When** 用户运行 `octo config provider add anthropic` 并输入有效 API Key，**Then** `octoagent.yaml` 的 providers 列表新增 anthropic 条目，openrouter 条目保持不变，`litellm-config.yaml` 被同步更新。

2. **Given** 用户尝试添加已存在的 Provider ID（如再次执行 `octo config provider add openrouter`），**When** 系统检测到重复，**Then** 系统提示 Provider 已存在，询问是否更新（update）或跳过（skip），不自动覆盖。

3. **Given** 用户希望禁用某个 Provider 而非删除，**When** 运行 `octo config provider disable openrouter`，**Then** `octoagent.yaml` 中该 Provider 的 `enabled` 字段设为 `false`，`litellm-config.yaml` 中对应的模型条目被移除，其他 Provider 不受影响。

---

### User Story 4 - 透明的模型别名管理 (Priority: P2)

作为 OctoAgent 用户，我希望能清楚地看到 `main`/`cheap` 等别名对应哪个真实模型，并能通过 `octo config alias set` 命令修改别名映射，使得我可以根据自己的需求选择最合适的模型，而不是接受系统默认值。

**Why this priority**: 模型别名透明度直接影响用户对系统的掌控感。归为 P2 是因为默认别名可以工作，但用户无法理解和自定义时会产生困惑，是重要的 DX 改善。

**Independent Test**: 可通过以下步骤测试：（1）运行 `octo config alias list`，验证能看到每个别名对应的真实模型名和 Provider；（2）运行 `octo config alias set main --provider anthropic --model claude-opus-4-20250514`；（3）验证 `octoagent.yaml` 的 model_aliases.main 被更新，`litellm-config.yaml` 中 main 模型条目也随之更新。

**Acceptance Scenarios**:

1. **Given** `octoagent.yaml` 包含 model_aliases 配置，**When** 运行 `octo config alias list`，**Then** 系统以表格形式显示所有别名、对应的 Provider、真实模型名和描述，信息完整、人类可读。

2. **Given** 用户想将 `main` 别名指向 Anthropic 的 claude-opus，**When** 运行 `octo config alias set main --provider anthropic --model claude-opus-4-20250514`，**Then** `octoagent.yaml` 中 model_aliases.main 被更新，`litellm-config.yaml` 的 main 模型条目同步更新，变更立即生效。

3. **Given** 用户为不存在或未启用的 Provider 设置别名，**When** 系统校验，**Then** 系统提示该 Provider 未配置或未启用，并建议先运行 `octo config provider add <id>` 添加 Provider。

---

### User Story 5 - 配置驱动的 LiteLLM 配置生成 (Priority: P1)

作为 OctoAgent 用户，我希望 `octoagent.yaml` 的变更能自动推导出正确的 `litellm-config.yaml`，使得我不需要了解 LiteLLM 配置语法，系统能自动维护两者的一致性。

**Why this priority**: 这是 `octoagent.yaml` 作为单一信息源的核心承诺。若两个文件需要手动同步，单一信息源的价值则无法实现。

**Independent Test**: 可通过以下步骤测试：直接修改 `octoagent.yaml` 中的 model_aliases.main.model 字段，然后运行 `octo config sync`（或任何触发同步的命令），验证 `litellm-config.yaml` 中对应的 model 参数已更新。

**Acceptance Scenarios**:

1. **Given** `octoagent.yaml` 包含一个启用的 Provider 和 model_aliases 配置，**When** 运行 `octo config sync`，**Then** 系统读取 `octoagent.yaml`，推导并写入 `litellm-config.yaml`，包含正确的 model_list 和 general_settings.master_key 引用。

2. **Given** `octoagent.yaml` 中有多个 enabled=true 的 Provider，**When** 执行同步，**Then** `litellm-config.yaml` 中每个 model_alias 的 litellm_params 指向正确 Provider 的 API Key 环境变量，路由配置正确。

3. **Given** `octoagent.yaml` 格式存在语法错误，**When** 运行同步操作，**Then** 系统报告具体的解析错误（行号和错误描述），不生成损坏的 `litellm-config.yaml`，现有文件保持不变。

---

### User Story 6 - 配置完成后验证全链路 LLM 调用 (Priority: P2)

作为刚完成配置的用户，我希望通过 `octo doctor --live` 验证整个链路（`octoagent.yaml` -> `litellm-config.yaml` -> LiteLLM Proxy -> 真实 LLM Provider）是否正常工作，使得我在开始使用 Agent 前能确认配置无误。

**Why this priority**: 端到端验证是配置系统的完整性闭环。`octo doctor --live` 已存在，F014 需要确保其诊断逻辑与新的 `octoagent.yaml` 格式兼容，并能提供基于新配置结构的诊断信息。

**Independent Test**: 可通过完整配置流程（`octo config provider add` -> `octo config sync` -> 启动 LiteLLM Proxy -> `octo doctor --live`）验证整个链路，确认最后一步返回 LLM 调用成功。

**Acceptance Scenarios**:

1. **Given** 用户完成了 `octo config` 配置、运行了 `octo config sync`、且 LiteLLM Proxy 已启动，**When** 运行 `octo doctor --live`，**Then** 系统向 cheap 别名发送测试请求，收到有效 LLM 响应，报告"端到端 LLM 调用成功"。

2. **Given** `octoagent.yaml` 存在但 `litellm-config.yaml` 与其不一致（未同步），**When** 运行 `octo doctor`（不带 --live），**Then** 系统检测到两文件不一致，报告警告并建议运行 `octo config sync`。

3. **Given** LiteLLM Proxy 可达但上游 Provider API Key 无效，**When** 运行 `octo doctor --live`，**Then** 系统区分"Proxy 层正常"与"Provider 层失败"，明确指出是哪个 Provider 的凭证问题，并建议更新 API Key。

---

### Edge Cases

- **EC-1** (关联 FR-003, Story 1/3): `octoagent.yaml` 被用户手动编辑产生语法错误——系统在读取时应解析并报告具体错误（行号、字段名），不静默使用损坏的配置，不生成错误的 `litellm-config.yaml`。

- **EC-2** (关联 FR-005, Story 2): `octoagent.yaml` 的 providers 列表引用了 credential store 中不存在的凭证（如用户删除了凭证但未更新配置）——系统应在同步时报告警告，提示该 Provider 凭证缺失，但不阻止对其他有效 Provider 进行同步。

- **EC-3** (关联 FR-006, Story 5): 同步时发现 `litellm-config.yaml` 被用户手动修改过——[AUTO-RESOLVED: 以 `octoagent.yaml` 为单一事实源，执行同步时覆盖 `litellm-config.yaml`，并在执行前打印警告提示用户该文件将被重写] 理由：`octoagent.yaml` 是 F014 定义的单一信息源，手动修改衍生文件是反模式。

- **EC-4** (关联 FR-007, Story 6): `octoagent.yaml` 和 `litellm-config.yaml` 同时存在但 `octo doctor` 检测到两者不一致——系统应将此作为 WARN 级别检查项，提供具体的差异信息和修复命令。

- **EC-5** (关联 FR-004, Story 3): 用户为 model_alias 指定的 Provider 处于 disabled 状态——系统应在配置保存或同步时报告校验错误，提示用户先启用该 Provider 或选择其他 Provider。

- **EC-6** (关联 FR-002, Story 2): 用户从旧三文件体系迁移到 `octoagent.yaml` 体系——[AUTO-RESOLVED: 提供 `octo config migrate` 命令从现有 `.env`/`.env.litellm`/`litellm-config.yaml` 读取并生成 `octoagent.yaml`，作为可选的迁移路径] 理由：降低已有用户的迁移成本，同时不强制依赖旧文件。

---

## Requirements *(mandatory)*

### Functional Requirements

**octoagent.yaml 数据模型**

- **FR-001**: 系统 MUST 定义 `octoagent.yaml` 配置文件的规范结构，包含以下顶层字段：`config_version`（整数，当前为 1）、`updated_at`（ISO 8601 日期）、`providers`（Provider 列表）、`model_aliases`（别名映射）、`runtime`（运行时参数）。配置文件 MUST 能通过 schema 校验，错误字段须报告具体位置。
  *Traces to: Story 2*

- **FR-002**: 每个 Provider 条目 MUST 包含以下字段：`id`（字符串，全局唯一）、`name`（显示名称）、`auth_type`（认证类型：`api_key` / `oauth`）、`api_key_env`（凭证所在的环境变量名）、`enabled`（布尔值，控制是否参与配置生成）。
  *Traces to: Story 2, Story 3*

- **FR-003**: 每个 model_alias 条目 MUST 包含以下字段：`provider`（关联的 Provider id）、`model`（传递给 LiteLLM 的完整模型字符串）、`description`（可选的人类可读描述）。系统 MUST 内置 `main` 和 `cheap` 两个别名，允许用户覆盖其映射。
  *Traces to: Story 4*

- **FR-004**: `runtime` 配置块 MUST 包含：`llm_mode`（`litellm` 或 `echo`）、`litellm_proxy_url`（默认 `http://localhost:4000`）、`master_key_env`（Master Key 所在的环境变量名，默认 `LITELLM_MASTER_KEY`）。
  *Traces to: Story 2*

**配置生成与同步**

- **FR-005**: 系统 MUST 提供配置同步能力，将 `octoagent.yaml` 推导并写入 `litellm-config.yaml`。同步逻辑 MUST 基于 `enabled=true` 的 Provider 和有效的 model_aliases 生成对应的 `model_list` 条目，并正确引用 `api_key_env` 所指定的环境变量。
  *Traces to: Story 5*

- **FR-006**: 同步操作 MUST 验证 `octoagent.yaml` 的内容完整性（schema 校验 + 引用完整性），在校验失败时拒绝写入 `litellm-config.yaml`，并向用户报告具体错误，现有 `litellm-config.yaml` 保持不变。
  *Traces to: Story 5, EC-3*

- **FR-007**: 系统 SHOULD 在以下场景自动触发同步：`octo config provider add/disable`、`octo config alias set`、`octo config sync` 命令执行时。同步后系统 SHOULD 打印被写入文件的路径和摘要。
  *Traces to: Story 2, Story 3, Story 5*

**octo config CLI 命令**

- **FR-008**: 系统 MUST 提供 `octo config` 命令组，包含以下子命令：
  - `octo config`（无子命令）：显示当前配置摘要
  - `octo config init`：首次初始化引导（全量，用于无 `octoagent.yaml` 的环境）
  - `octo config provider add <id>`：增量添加 Provider
  - `octo config provider list`：列出所有已配置的 Provider 及状态
  - `octo config provider disable <id>`：禁用（不删除）指定 Provider
  - `octo config alias list`：列出所有模型别名及映射
  - `octo config alias set <alias>`：更新指定别名的映射
  - `octo config sync`：手动触发从 `octoagent.yaml` 到 `litellm-config.yaml` 的同步
  *Traces to: Story 1, Story 3, Story 4, Story 5*

- **FR-009**: `octo config`（无子命令）MUST 展示：已配置的 Providers（id、名称、状态）、model_aliases 列表（别名、Provider、真实模型名）、runtime 参数（llm_mode、proxy_url）。展示 MUST 格式化为人类可读的表格或结构化文本。
  *Traces to: Story 1*

- **FR-010**: `octo config provider add <id>` MUST 实现非破坏性添加：若 Provider 已存在则提示用户选择"更新"或"跳过"，不自动覆盖；若 Provider 为新增则引导用户完成认证配置并写入 `octoagent.yaml`。
  *Traces to: Story 3, EC-2*

- **FR-011**: `octo config init` MUST 为全量初始化路径，当 `octoagent.yaml` 已存在时，MUST 提示用户并要求显式确认后才能继续，防止意外覆盖。
  *Traces to: Story 1*

**与旧配置体系的兼容性**

- **FR-012**: 系统 SHOULD 提供 `octo config migrate` 命令，从现有 `.env`、`.env.litellm`、`litellm-config.yaml` 读取配置并生成对应的 `octoagent.yaml`，作为旧用户的迁移路径。迁移后原文件 SHOULD 被保留（不自动删除）。
  *Traces to: EC-6*

- **FR-013**: `octo doctor` SHOULD 新增检查项：验证 `octoagent.yaml` 与 `litellm-config.yaml` 是否一致，不一致时报告 WARN 并建议运行 `octo config sync`。
  *Traces to: Story 6*

**配置文件位置**

- **FR-014**: `octoagent.yaml` MUST 存储在项目根目录（与 `pyproject.toml` 同级）。该文件 MUST 被纳入版本管理（不在 `.gitignore` 中排除），但其中 MUST NOT 包含任何凭证值（API Key、Token），凭证值仍通过 credential store 和环境变量管理。
  *Traces to: Story 2; Constitution C5 合规*

### Key Entities

- **UnifiedConfig（统一配置）**: `octoagent.yaml` 的结构化表示。包含 config_version、updated_at、providers 列表、model_aliases 映射、runtime 块。是 F014 引入的核心数据模型，作为所有配置的单一信息源。

- **ProviderEntry（Provider 条目）**: unified_config.providers 列表中的单个条目。记录 Provider 的元数据（id、name、auth_type）和凭证引用方式（api_key_env）。不存储凭证值本身。

- **ModelAlias（模型别名）**: unified_config.model_aliases 中的单个映射。将一个用户可见的别名（如 `main`、`cheap`）绑定到具体的 Provider 和模型名称。

- **RuntimeConfig（运行时配置）**: unified_config.runtime 块的结构化表示。控制系统运行时行为（llm_mode、proxy_url、master_key_env）。

---

## 非功能需求 (NFR)

- **NFR-001**: `octo config sync` 执行时间 MUST 在本地文件操作范围内，不得进行网络调用或 LLM 调用，确保同步操作快速（目标 < 1 秒）。

- **NFR-002**: `octoagent.yaml` 的 schema 校验 MUST 在所有读取操作前执行，schema 校验错误 MUST 提供人类可读的错误信息（包含字段路径和期望类型），不得以技术性堆栈信息回应用户。

- **NFR-003**: 任何修改 `octoagent.yaml` 的操作（add、set、migrate）在写入前 MUST 对原文件进行原子性写入（先写临时文件，再原子替换），防止写入中断产生损坏的配置文件。

- **NFR-004**: `octoagent.yaml` 中 MUST NOT 出现任何凭证值。`octo config` 命令在生成或修改配置时 MUST 校验并拒绝写入包含明文凭证的配置。（Constitution C5 合规）

- **NFR-005**: `octo config` 命令组 MUST 与现有 `octo init` 命令共存，不破坏 Feature 003 已交付的 `octo init` 和 `octo doctor` 功能，两者可并行使用。

- **NFR-006**: `octoagent.yaml` 格式变更时（如 config_version 升级），系统 SHOULD 提供向前兼容的读取，并在加载旧版本时提示用户运行迁移命令。

---

## 边界与排除 (Out of Scope)

以下内容明确不在 Feature 014 范围内：

- **`octoagent.yaml` 涵盖非模型配置**（如 Telegram 渠道配置、Logfire 配置）——F014 仅管理 Provider 和模型别名配置，其他配置的统一归入 M2 或后续特性。

- **Web UI 配置界面**——F014 仅提供 CLI 管理，Web UI 管理页面属于 M3 范畴。

- **运行时动态配置热重载**——F014 的配置变更在下次系统启动或手动同步后生效，不支持 OctoAgent 运行时动态重载 `octoagent.yaml`。

- **多用户/多项目共享配置**——F014 的 `octoagent.yaml` 是项目级配置，用户级（`~/.octoagent/`）的多项目管理属于后续特性。

- **废弃 `octo init`**——F014 不删除现有 `octo init` 命令，两套命令并存，后续里程碑统一规划命令结构。

- **自动触发 LiteLLM Proxy 重启**——`octo config sync` 只负责更新配置文件，不负责重启已运行的 Proxy 容器，用户需手动重启 Proxy 使配置生效。

---

## 成功标准 (Success Criteria)

### Measurable Outcomes

- **SC-001**: 用户完成 `octo config provider add <id>` 后，`octoagent.yaml` 存在且格式正确，`litellm-config.yaml` 被自动同步生成，整个过程无需用户手动编辑任何文件。

- **SC-002**: 在已有 Provider A 配置的情况下，执行 `octo config provider add <Provider B>` 后，Provider A 的配置保持不变（非破坏性），Provider B 被正确添加。

- **SC-003**: 执行 `octo config`（无子命令）后，用户能在一个输出界面看到所有已配置 Providers、model_aliases 到真实模型的映射、以及运行时参数，无需打开任何配置文件。

- **SC-004**: 从三文件体系（`.env` + `.env.litellm` + `litellm-config.yaml`）迁移到 `octoagent.yaml` 体系后，通过 `octo doctor --live` 验证端到端 LLM 调用成功，迁移过程可在 5 分钟内完成。

- **SC-005**: `octo doctor` 能检测并报告 `octoagent.yaml` 与 `litellm-config.yaml` 的不一致状态，提供明确的修复建议（`octo config sync`）。

- **SC-006**: `octoagent.yaml` 中不包含任何明文凭证值，凭证通过环境变量引用（`api_key_env` 字段），可安全纳入版本管理。

- **SC-007**: 所有 `octo config` 子命令的错误信息（格式错误、引用缺失、校验失败）均为人类可读的中文描述，包含具体字段路径和修复建议。

---

## Appendix: Constitution Compliance Notes

| Constitution 条款 | 合规要求 | 对应 FR |
| --- | --- | --- |
| C5 (Least Privilege) | `octoagent.yaml` 不存储凭证值，凭证通过 api_key_env 引用环境变量；octo config 命令拒绝写入明文凭证 | FR-014, NFR-004 |
| C6 (Degrade Gracefully) | `octoagent.yaml` 不存在时系统不崩溃，`octo config` 引导用户初始化；同步失败不覆盖现有 litellm-config.yaml | FR-006, NFR-005 |
| C7 (User-in-Control) | 非破坏性更新（FR-010, FR-011）；Provider 禁用（不删除）提供可逆操作；同步前展示变更摘要 | FR-010, FR-011, FR-007 |
| C8 (Observability is a Feature) | `octo doctor` 新增配置一致性检查项（FR-013）；`octo config sync` 打印写入的文件路径和摘要 | FR-007, FR-013 |

---

## Clarifications

### Session 2026-03-04

**Q1 - `octo config init` 与 `octo init` 的关系？**

- **状态**: [AUTO-RESOLVED: F014 交付 `octo config` 命令组作为新入口，`octo init` 保持现状，两者并行共存，不在 F014 中废弃旧命令]
- **理由**: 避免破坏 Feature 003 已交付的功能，后续里程碑统一规划 CLI 命令结构。

**Q2 - `octoagent.yaml` 写入哪些环境变量到 `.env`？**

- **状态**: [NEEDS CLARIFICATION — 待用户决策，详见 checklists/clarify.md 问题 C1]
- **推荐默认值**: 选项 A（`octo config provider add` 仍将 API Key 写入 `.env.litellm`，与 `octo init` 行为一致）
- **影响**: FR-008（`octo config provider add` 命令实现细节）、NFR-004（凭证保护）

**Q3 - `octo config sync` 是否同步 `.env` 中的 `OCTOAGENT_LLM_MODE` 等运行时变量？**

- **状态**: [NEEDS CLARIFICATION — 待用户决策，详见 checklists/clarify.md 问题 C2]
- **推荐默认值**: 选项 B（两者独立，运行时优先读取 `octoagent.yaml`，降级读取 `.env`）
- **影响**: FR-004（`runtime` 配置块定义）、FR-007（sync 命令职责边界）、FR-013（`octo doctor` 新增检查项）

**Q4 - `octoagent.yaml` 文件位置**

- **状态**: [AUTO-CLARIFIED: 与 `pyproject.toml` 同级（即 `octoagent/` 子目录内）— 与现有 `.env`、`.env.litellm`、`litellm-config.yaml` 保持一致，符合 FR-014 原文]

**Q5 - `octo config migrate` 是否纳入 F014 MVP**

- **状态**: [AUTO-CLARIFIED: 作为 SHOULD 级别实现，不阻塞 MVP 验收 — FR-012 和 EC-6 均标记为 SHOULD，不是强制需求]

**Q6 - `octo config provider add` 交互模式**

- **状态**: [AUTO-CLARIFIED: 混合模式（CLI 参数优先，缺失时交互式补全）— 与 `octo init` 的 questionary 风格一致，同时支持脚本化调用]

**Q7 - `octoagent.yaml` 格式错误诊断粒度**

- **状态**: [AUTO-CLARIFIED: 报告字段路径（`providers[0].auth_type`）+ 期望类型，不强制行号 — Pydantic ValidationError 原生提供 `loc` 路径，成本低且对用户更实用]

**Q8 - `octo config alias set` 是否允许自定义别名**

- **状态**: [AUTO-CLARIFIED: 允许用户自定义别名，不限于内置 main/cheap — 开放扩展符合 Constitution C7（User-in-Control）]
