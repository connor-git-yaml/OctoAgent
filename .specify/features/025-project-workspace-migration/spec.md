---
feature_id: "025"
title: "Secret Store + Unified Config Wizard（第二阶段）"
milestone: "M3"
status: "Implemented"
created: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2695-2701 / docs/m3-feature-split.md Feature 025 / .specify/features/026-control-plane-contract/spec.md"
predecessor: "Feature 025 第一阶段（Project / Workspace + default project migration，已交付）、Feature 024（Installer/Updater/Doctor/Migrate，已交付）、Feature 026-A（Control Plane Contract，已冻结）"
parallel_dependency: "Feature 026-B 只消费本阶段交付的 wizard/config/project selector/secret 状态面，不得反向重定义语义"
---

# Feature Specification: Secret Store + Unified Config Wizard（第二阶段）

**Feature Branch**: `codex/feat-025-project-workspace-migration`  
**Created**: 2026-03-08  
**Status**: Implemented  
**Input**: 落实 M3 Feature 025 第二阶段：Secret Store + Unified Config Wizard。范围只包含 Secret Store 分层设计与实现、SecretRef（`env/file/exec/keychain`）、provider/channel/gateway secret bindings、project-scoped secret bindings、runtime short-lived injection、`octo secrets audit/configure/apply/reload/rotate`、统一 wizard session 在 CLI 路径的落地、基于已冻结 control-plane contract 的 `config schema + uiHints` 消费，以及 `octo project create/select/edit/inspect` 的 CLI 主路径。  
**调研基础**: `research/tech-research.md`、`research/product-research.md`、`research/online-research.md`、`research/research-synthesis.md`

---

## Problem Statement

Feature 025 第一阶段已经交付 `Project / Workspace` 正式模型、default project migration、legacy metadata backfill 与 env bridge，M3 的 project migration gate 已经打通；但普通用户视角的配置主路径仍然没有成型：

1. provider auth、Telegram bot token、gateway token、webhook secret 仍然分散在 `auth-profiles.json`、`.env`、`.env.litellm`、`octoagent.yaml` 的 env-name 引用和若干历史命令里。
2. `Project` 虽然已经是一等公民数据对象，但用户还不能通过正式 CLI 把它当作工作单元来 `create/select/edit/inspect`。
3. 当前 `octo init` 仍是一次性脚本式引导，不是可恢复、可取消、可多端共用语义的 `wizard session`。
4. `config schema` 目前主要是后端配置模型本身，还没有进入“基于同一 contract 消费 `schema + uiHints`”的统一主路径。
5. runtime 仍主要依赖长期环境变量；即使 024 已经交付 update/restart/verify/recovery 基线，也还没有围绕 secret apply/reload 的 project-scoped 注入和审计闭环。

因此，025-B 的目标不是再加几条零散命令，而是把 **project + wizard + secret store** 收敛成一条连续路径：

`octo project create/select -> wizard session -> octo secrets audit/configure/apply/reload -> octo project inspect -> doctor/onboard`

本阶段必须复用 025-A 已交付的 project/workspace/migration 基线，并消费 026-A 已冻结的 `WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument` 语义；本阶段不得重新定义这些 contract，也不得把完整 Web 配置中心、Session Center、Scheduler、Runtime Console 偷带进来。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 025 第一阶段：Project / Workspace + default project migration | 已交付 | 本阶段复用 `Project` / `Workspace` / `ProjectBinding` 及 default project migration，不得重做迁移模型 |
| Feature 024：Installer / Updater / Doctor / Migrate | 已交付 | 本阶段复用 managed runtime descriptor、reload/restart/verify 与现有 ops/recovery 基线 |
| Feature 026-A：Control Plane Contract | 已冻结 | `WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument` 为上游 canonical contract |
| Feature 014：统一配置基线 | 已交付 | `octoagent.yaml` 继续承载非 secret 配置与 `*_env` 名称引用 |
| Feature 016：Telegram Channel | 已交付 | Telegram token / webhook secret 是本阶段必须纳入 secret bindings 的典型目标 |

前置约束：

- 本阶段 **必须** 复用现有 `provider.dx`、`project migration`、`gateway ops/recovery` 基线，不允许旁路再造第二套 project/config runtime。
- 本阶段 **必须** 保持 secret 实值不进入日志、事件、LLM 上下文，也不得写入 `octoagent.yaml` 或版本管理文件。
- 本阶段 **不得** 重写 025-A 的 legacy migration 逻辑、`Project` / `Workspace` 主键语义或 default project 生成策略。
- 本阶段 **不得** 重新定义 026-A 的 wizard session / project selector / config schema 语义，只能实现 producer/consumer 与 CLI 主路径。
- 本阶段 **不得** 交付完整 Web 配置中心页面、Session Center、Scheduler、Runtime Console、Memory Console。

---

## Scope Boundaries

### In Scope

- Secret Store 分层：global provider auth bridge、project-scoped secret bindings、runtime short-lived injection
- SecretRef：`env` / `file` / `exec` / `keychain`
- provider / channel / gateway secret bindings
- `octo secrets audit/configure/apply/reload/rotate`
- `octo project create/select/edit/inspect`
- CLI wizard session 落地与恢复
- 基于 026-A contract 的 `config schema + uiHints` 消费
- 与 024 managed runtime / ops/recovery 的 reload 能力接线

### Out of Scope

- 完整 Web Config Center / Secrets Center / Project Center 页面
- Session Center、Scheduler、Runtime Console、Memory Console
- 重新设计 project/workspace migration 模型
- project 以外的多租户/多节点 secret federation
- 把 secret 实值持久化进 YAML、SQLite event store、artifact 或 LLM 上下文

---

## User Scenarios & Testing

### User Story 1 - 通过统一 CLI 向导完成 project-aware 配置主路径 (Priority: P1)

作为普通用户，我希望从 `octo project create/select` 进入统一 wizard，在同一条 CLI 路径中完成 provider、channel、gateway、model 的配置，并把 secrets 绑定到当前 project，这样我不需要记住多套配置文件、环境变量和零散命令。

**Why this priority**: M3 的产品化约束要求“安装、配置、首聊、管理台打开必须是一条连续路径”；如果 CLI 主路径仍然碎片化，Feature 025 的用户价值就没有落地。

**Independent Test**: 在空项目和已有 default project 的项目上分别执行 `project create/select` + wizard session，验证系统能恢复同一 session、消费同一 `config schema + uiHints` 语义、并输出待应用的 secret/config plan。

**Acceptance Scenarios**:

1. **Given** 当前实例已经完成 025-A migration，**When** 用户执行 `octo project create` 或 `octo project select` 后进入 wizard，**Then** 系统以 026-A `WizardSessionDocument` 语义持久化会话，而不是临时脚本状态。
2. **Given** wizard 中需要配置 provider、Telegram、gateway 和 model alias，**When** CLI 渲染 schema，**Then** 它消费的是同一份 `ConfigSchemaDocument + uiHints`，而不是 CLI 私有字段定义。
3. **Given** 用户中途退出 wizard，**When** 稍后重新进入 `octo project edit --wizard`，**Then** 系统可以恢复上次 session 的 step、blocking reason 和下一步动作建议。

---

### User Story 2 - 通过统一 Secret Store 生命周期管理 project-scoped secrets (Priority: P1)

作为操作者，我希望把 provider key、OAuth token、Telegram bot token、gateway token、webhook secret 等高价值密钥统一纳入 `octo secrets audit/configure/apply/reload/rotate` 生命周期，并把它们绑定到当前 project，这样 secret 不会散落在 `.env`、日志和多个脚本里。

**Why this priority**: Feature 025 的核心是把“环境变量优先”的历史路径降级为高级路径，建立统一 secret 生命周期和 project-scoped bindings。

**Independent Test**: 针对 `env/file/exec/keychain` 四类 SecretRef 分别构造项目，验证 audit/configure/apply/reload/rotate 的 happy path 与 degrade path 都能独立通过。

**Acceptance Scenarios**:

1. **Given** 当前 project 需要 provider API key 与 Telegram bot token，**When** 用户执行 `octo secrets configure`，**Then** 系统会为当前 project 生成/更新 bindings plan，而不是直接把 secret 明文写入 `octoagent.yaml`。
2. **Given** 系统检测到某个 `SecretRef(file)` 路径缺失或某个 `SecretRef(exec)` 执行失败，**When** 用户运行 `octo secrets audit`，**Then** 它会返回结构化问题和 remediation，而不是崩溃或静默跳过。
3. **Given** secret source 已更新，**When** 用户运行 `octo secrets apply --dry-run` 再 `octo secrets reload`，**Then** 当前 project 的 runtime materialization 会被刷新，而不会把 secret 实值写入日志、事件或 LLM 上下文。

---

### User Story 3 - 把 Project 变成正式的 CLI 操作对象 (Priority: P1)

作为日常使用者，我希望能正式地 `create/select/edit/inspect` project，并在 `inspect` 里看到当前 project、workspace、readiness、warning 和 bindings 摘要，这样 project 不再只是底层数据库对象，而是真正可理解、可操作的产品单元。

**Why this priority**: 025-A 只交付了 project/workspace 的领域底座；025-B 必须把它提升到用户可感知的主路径，否则 M3 的 project/workspace 仍然只是内部实现。

**Independent Test**: 在一个包含多个 project 的测试目录中执行 `create/select/edit/inspect`，验证 active project 切换、wizard/edit 主路径、inspect redaction/readiness 摘要和后续 secrets/apply 的 project 绑定一致。

**Acceptance Scenarios**:

1. **Given** 当前实例已有多个 project，**When** 用户执行 `octo project select`，**Then** 系统按 026-A `ProjectSelectorDocument` 语义更新当前 project，而不是靠隐式目录约定或临时环境变量。
2. **Given** 用户执行 `octo project edit`，**When** 需要修改 instructions、memory mode、channel/provider bindings 或 wizard 入口配置，**Then** 系统会沿用当前 project 语义和相同 contract，而不是开另一套配置命令。
3. **Given** 用户执行 `octo project inspect`，**When** 输出当前 project 摘要，**Then** 它会显示 readiness/warnings/bindings/status，但不会暴露任何 secret 明文。

---

### User Story 4 - 通过短生命周期注入让 runtime 安全消费 secret (Priority: P2)

作为维护者，我希望 provider/channel/gateway/runtime 仍然能继续通过现有 `*_env` 语义消费 secret，但真实 secret 值只在 runtime short-lived injection 边界被解析并短时注入，这样既能复用现有配置模型，又不会把密钥长期落盘或散布到不该出现的上下文里。

**Why this priority**: 当前 provider/channel/gateway 已经广泛依赖 `api_key_env` / `master_key_env` / `bot_token_env` 语义；025-B 的价值在于在不破坏兼容性的前提下收口 secret 解析与注入。

**Independent Test**: 针对 managed runtime 和 unmanaged runtime 分别执行 `apply/reload`，验证前者得到短生命周期 env snapshot 并刷新运行态，后者返回可执行的降级提示而不是 silent success。

**Acceptance Scenarios**:

1. **Given** 当前 runtime 由 024 managed runtime descriptor 管理，**When** 用户执行 `octo secrets reload`，**Then** 系统会重解析当前 project bindings，并通过现有 reload/restart/verify 基线刷新运行时。
2. **Given** 当前实例不是 managed runtime，**When** 用户执行 `octo secrets reload`，**Then** 系统会返回 `degraded/action_required` 结果和下一步建议，而不是假装热重载成功。
3. **Given** 某个 provider/channel secret 被轮换，**When** 后续 runtime 解析配置，**Then** 它仍沿用 `*_env` 命名语义，但真实值只来自当前 project 的有效 materialization，而不是仓库文件里的明文。

---

### Edge Cases

- 当 OS keychain 不可用或后端缺失时，系统如何显式降级到 `env/file/exec` 路径，而不是 silently no-op？
- 当 `SecretRef(file)` 指向相对路径或权限过宽文件时，`audit` 应如何报告并给出 remediation？
- 当 `SecretRef(exec)` 返回空值、非零退出码或执行超时时，`apply/reload` 应如何 fail-closed？
- 当用户切换 active project 后忘记重新 `apply/reload`，`inspect` 与 `doctor` 如何明确指出“bindings 已更新但 runtime 未同步”？
- 当某个 secret 既存在 legacy env bridge 又存在 project binding 时，优先级应如何固定，避免“到底读谁”的漂移？
- 当 Gateway/Telegram 当前处于运行中轮换 token，`reload` 应如何表达是否需要 restart，而不是一律声称热重载完成？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 将 025-A 已交付的 `Project` / `Workspace` / `ProjectBinding` / default project migration 视为本阶段前置基线；025-B MUST 复用该 canonical model，而 MUST NOT 重新设计 migration 主键、default project 语义或 legacy backfill 流程。

- **FR-002**: 系统 MUST 将 026-A 已冻结的 `WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument` 语义视为上游 canonical contract；025-B MAY 实现 producer/consumer，但 MUST NOT 改变这些对象的字段语义、生命周期语义或状态语义。

- **FR-003**: 系统 MUST 提供正式 `SecretRef` 模型，至少支持 `env`、`file`、`exec`、`keychain` 四种 source type，并为每种 source type 定义可审计的解析元数据。

- **FR-004**: `SecretRef`、project secret bindings 和相关持久化对象 MUST 只存储引用元数据、解析状态、目标 consumer、审计信息与轮换摘要，而 MUST NOT 持久化 secret 明文。

- **FR-005**: 系统 MUST 提供 project-scoped secret bindings，用于把 provider、channel、gateway、runtime 所需 secret 绑定到当前 project；当后续 consumer 需要 workspace 维度时，绑定模型 MAY 追加 workspace 关联，但 project 语义必须是 canonical 隔离边界。

- **FR-006**: 系统 MUST 支持 provider / channel / gateway secret bindings，至少覆盖 `runtime.master_key_env`、provider `api_key_env`、Telegram `bot_token_env`、Telegram `webhook_secret_env` 和后续 gateway token 类目标。

- **FR-007**: 系统 SHOULD 允许把既有 provider auth profile 作为 bridge 导入或引用到当前 project secret bindings，但 MUST NOT 继续把“未绑定的全局凭证状态”当作 project runtime 的隐式事实源。

- **FR-008**: CLI MUST 提供 `octo project create`，用于创建 project、初始化最小 metadata，并允许用户选择是否立即设为 active project。

- **FR-009**: CLI MUST 提供 `octo project select`，并按 026-A `ProjectSelectorDocument` 语义表达当前 project、候选 project、warnings/readiness 和切换结果。

- **FR-010**: CLI MUST 提供 `octo project edit`，且其 wizard 模式 MUST 基于同一 `WizardSessionDocument` 语义恢复/推进 session，而不是复用一次性脚本逻辑。

- **FR-011**: CLI MUST 提供 `octo project inspect`，输出 active project、workspace、关键 bindings、readiness/warnings、last applied/reload 摘要，但 MUST redact secret values。

- **FR-012**: 系统 MUST 提供可恢复、可取消、可查询状态的 CLI wizard session 路径；至少支持 start/resume/status/cancel，并记录 current step、blocking reason、next actions 和 session version。

- **FR-013**: CLI wizard MUST 消费 `ConfigSchemaDocument + uiHints`；CLI 对不支持的 `uiHints` MAY 忽略或降级展示，但 MUST 保持同一配置语义和校验规则。

- **FR-014**: 系统 MUST 提供 `octo secrets audit`，至少检查：当前 project 是否缺失必需 bindings、`SecretRef` 是否可解析、legacy env bridge 与 project bindings 是否冲突、是否存在疑似明文落盘风险，以及 runtime 是否需要 reload/restart。

- **FR-015**: 系统 MUST 提供 `octo secrets configure`，用于生成或更新当前 project 的 secret/binding 计划；该命令 MAY 交互式收集 secret source，但 MUST NOT 在 configure 阶段直接把 secret 注入运行时。

- **FR-016**: 系统 MUST 提供 `octo secrets apply`，且 MUST 支持 `--dry-run`；真实 apply MUST 原子写入 canonical bindings 与相关状态摘要，并在失败时回滚当前 apply 的新增/变更记录。

- **FR-017**: 系统 MUST 提供 `octo secrets reload`，并复用 024 已交付的 managed runtime / ops / recovery 基线；对于 unmanaged runtime，系统 MUST 明确返回 `degraded/action_required`，而 MUST NOT 伪装成热重载成功。

- **FR-018**: 系统 MUST 提供 `octo secrets rotate`，用于替换现有 `SecretRef` 或其目标 material，并留下结构化轮换摘要、影响面和后续建议，而 MUST NOT 输出或存储 secret 明文。

- **FR-019**: 运行时 secret 消费 MUST 通过 runtime short-lived injection 完成：系统 MAY 生成受限生命周期的 env snapshot 或等价的注入载荷，但 MUST NOT 把生效后的 secret 实值写入 `octoagent.yaml`、日志、事件、artifact 或 LLM 上下文。

- **FR-020**: 现有 provider/channel/gateway 配置中的 `*_env` 语义 MUST 继续成立；025-B MUST 在不破坏现有配置模型的前提下，把 project secret bindings materialize 成当前 runtime 可消费的 env-name 映射。

- **FR-021**: 普通用户 happy path MUST 不再依赖“先手工 `export` 多个环境变量再运行”；`env/file/exec` 仍 MAY 作为高级路径保留，但默认主路径应以 project + wizard + secret store 为中心。

- **FR-022**: 025-B MUST 明确与 026-B 的消费边界：本阶段只交付 CLI 主路径、contract producer/consumer、project selector/wizard/config schema 的状态面与 secret lifecycle；完整 Web 配置中心、Session Center、Scheduler、Runtime Console MUST 留到 026-B 或后续子线。

### Key Entities

- **SecretRef**: secret 的引用对象，描述 source type、定位参数、可审计解析元数据与 redaction 规则。
- **ProjectSecretBinding**: 把某个 `SecretRef` 绑定到 project 内特定 consumer target 的 canonical 记录。
- **SecretResolutionResult**: 一次 `audit/apply/reload/rotate` 过程中产生的解析摘要、警告、失败原因与后续建议。
- **SecretMaterialization**: runtime short-lived injection 期间的临时有效载荷或 env snapshot 摘要，不包含持久化明文。
- **WizardSessionDocument**: 026-A 冻结的 wizard canonical document，025-B 在 CLI 中消费与持久化。
- **ConfigSchemaDocument**: 026-A 冻结的配置 schema + `uiHints` 文档，025-B 在 CLI 中消费。
- **ProjectSelectorDocument**: 026-A 冻结的当前 project / 候选 project / readiness 投影，025-B 在 CLI 中消费。
- **ProjectRuntimeBindingSummary**: 当前 project 已应用的 bindings、runtime 同步状态、待 reload/restart 警告的摘要对象。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 新用户可以仅通过 `project create/select`、统一 wizard 与 `secrets apply/reload` 完成 provider/channel/gateway/model 的最小配置，而不需要手工维护多处 `.env` 或 shell `export`。

- **SC-002**: `env` / `file` / `exec` / `keychain` 四类 `SecretRef` 在测试中均有独立 happy path 与 failure path 覆盖，且 failure path 会给出结构化 remediation。

- **SC-003**: 当前 project 的 provider/channel/gateway secret bindings 在 `apply + reload` 后可被 runtime 消费；managed runtime 可以完成刷新，unmanaged runtime 会明确报告 `action_required/degraded`。

- **SC-004**: `project inspect`、`secrets audit`、wizard/session 状态输出在测试中都不会泄露 secret 明文，且日志/事件/LLM 上下文中不存在 secret 实值。

- **SC-005**: 025-B 不会破坏 025-A 的 project/workspace/migration 基线，也不会重定义 026-A contract；`checklists/requirements.md` 的所有检查项均通过。

- **SC-006**: 026-B 可以直接消费 025-B 产出的 wizard/config/project selector/secret 状态面，而不需要重新定义 project/secret/wizard 语义。

---

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 025-B 是否重做 025-A 的 project/workspace migration？ | 否 | 用户已明确要求复用 025-A 基线，不得重做迁移模型 |
| 2 | 025-B 是否重新定义 wizard session / project selector / config schema 语义？ | 否 | 这些语义已在 026-A 冻结，本阶段只消费与落地 |
| 3 | 普通用户路径是否仍以手工环境变量为默认入口？ | 否 | `env/file/exec` 保留为高级路径，默认路径改为 `project + wizard + secret store` |
| 4 | 本阶段是否交付完整 Web 配置中心和 Session Center？ | 否 | 用户已显式排除，Web 厚页面留给 026-B |
| 5 | 是否允许 secret 明文进入日志、事件或 LLM 上下文？ | 否 | 这违反 Constitution 的 least privilege / observability 边界，本阶段必须 fail-closed |
