---
feature_id: "026"
title: "Control Plane Contract（第一阶段）"
milestone: "M3"
status: "Draft"
created: "2026-03-08"
research_mode: "tech-only"
blueprint_ref: "docs/m3-feature-split.md §3.3 / Feature 026 F026-T00；docs/blueprint.md M3 产品化约束"
predecessor: "Feature 012 / 015 / 017 / 019 的现有模型与投影"
parallel_dependency: "供 Feature 025-B / 026-B 直接消费"
---

# Feature Specification: Control Plane Contract（第一阶段）

**Feature Branch**: `026-control-plane-contract`
**Created**: 2026-03-08
**Status**: Draft
**Input**: 落实 M3 Feature 026 第一阶段 `Control Plane Contract`，以上游为 `docs/m3-feature-split.md` 中 F026-T00 与 `docs/blueprint.md` 的 M3 产品化约束。
**调研基础**: `research/tech-research.md`、`research/online-research.md`

## Problem Statement

OctoAgent 已经在 M2 形成了若干可复用的控制面对象雏形，但这些能力仍然分散在不同模块与表面：

1. `wizard session` 只在 onboarding flow 中存在本地模型，还没有成为 CLI/Web 共用的正式 contract。
2. `config schema` 目前主要是配置模型本身，缺少 `schema + uiHints` 这一层供不同表面稳定消费。
3. `execution session`、`operator action`、`/ready` diagnostics 都已有投影雏形，但仍然是 route-specific payload，而不是共享 control-plane resource。
4. `project selector` 与 `automation job` 在 M3 已经被 blueprint 指定为正式产品对象，但如果不先冻结 contract，下游实现会各自造 DTO。
5. CLI/Web/Telegram 还没有共用的 `action/command registry`，后续很容易出现“同一动作三套语义”的漂移。

Feature 026 第一阶段的目标不是实现控制台页面，而是先冻结一组 versioned control-plane contract，作为 025-B / 026-B 的共同协议基础。

## Contract Freeze Summary

本阶段冻结以下 contract 族：

- 六类资源 document：
  - `wizard session`
  - `config schema + uiHints`
  - `project selector`
  - `session/chat projection`
  - `automation job`
  - `diagnostics summary`
- 共用 `action/command registry`
- 共用 `action request/result envelope`
- 共用 `control-plane event envelope`
- 兼容策略与 frontend/backend 消费边界

---

## User Scenarios & Testing

### User Story 1 - 多端共享同一控制面资源对象 (Priority: P1)

作为后续实现 CLI/Web/Telegram 控制面的开发者，我希望所有表面都基于同一套 versioned resource documents 读取 `wizard`、`config`、`project`、`session`、`automation`、`diagnostics`，这样不同端可以并行开发而不需要各自发明 JSON 结构。

**Why this priority**: 这是 `Control Plane Contract Gate` 的核心要求；如果资源对象不统一，后续并行开发没有意义。

**Independent Test**: 针对任一资源类型，检查规范中是否定义了 canonical document、版本字段、消费者边界和 degraded 表达方式；只要这一组 contract 成立，就能独立支撑后续实现。

**Acceptance Scenarios**:

1. **Given** backend 发布某个 `wizard session` document，**When** CLI 与 Web 读取它，**Then** 二者看到相同的 `contract_version`、`schema_version` 和 step semantics。
2. **Given** backend 发布 `config schema + uiHints` document，**When** Web 深度消费 `uiHints` 而 CLI 只消费 schema 基础字段，**Then** 两端仍然共享同一配置语义，而不是各自维护不同字段定义。
3. **Given** 某个资源暂时不可用或部分降级，**When** consumer 读取 contract，**Then** 它会看到显式的 degraded/unavailable 状态，而不是依赖缺字段来猜测状态。

---

### User Story 2 - 相同动作在不同表面语义一致 (Priority: P1)

作为系统操作者，我希望 Telegram slash command、Web 按钮和 CLI command 对同一动作具有相同语义和结果码，这样我在不同控制面操作时不会遇到“名字相同但行为不同”的情况。

**Why this priority**: 如果动作语义不统一，control-plane contract 即使有资源对象也仍然会失去治理价值。

**Independent Test**: 选择任一动作，例如 `project.select` 或 `session.interrupt`，验证规范是否要求它通过同一个 `action_id`、参数 schema、结果 envelope 和事件语义对外暴露。

**Acceptance Scenarios**:

1. **Given** registry 中存在 `action_id = project.select`，**When** Web 与 Telegram 以各自 alias 触发该动作，**Then** backend 都按同一目标 project 选择语义解释请求。
2. **Given** 某个动作当前只支持 Web 和 CLI，**When** Telegram 读取 registry，**Then** 它会看到 `unsupported/hidden` 的显式声明，而不是自行发明替代语义。
3. **Given** 同一动作要求审批或存在幂等约束，**When** 不同表面触发该动作，**Then** 都共享相同的 risk/approval/idempotency 语义。

---

### User Story 3 - 下游实现可以按兼容策略安全演进 (Priority: P1)

作为 025-B / 026-B 的实现者，我希望 control-plane contract 明确版本策略、事件模型和消费边界，这样后续迭代可以知道哪些是 minor-compatible 变化，哪些必须升 major，并确保旧 consumer 不会被静默打断。

**Why this priority**: 026 第一阶段的价值在于“冻结协议”，没有兼容策略和事件模型就无法称为可冻结 contract。

**Independent Test**: 审查规范是否清楚定义了 `contract_version`、breaking vs additive change 规则、统一 event envelope，以及 frontend/backend 的职责边界。

**Acceptance Scenarios**:

1. **Given** 某个 resource document 新增了可选字段，**When** 旧 consumer 读取新文档，**Then** 它可以忽略未知字段并继续依赖既有语义。
2. **Given** backend 执行一个注册动作，**When** 该动作被接受并完成，**Then** system 会按统一 event envelope 发出 `requested` 与 `completed`/`rejected` 事件。
3. **Given** 某项变更改变了已有动作或字段的语义，**When** 该 contract 发布，**Then** 它必须被归类为 breaking change 并通过 major version 表达。

---

### User Story 4 - 先冻结协议，不提前承诺页面实现 (Priority: P2)

作为 M3 产品化的负责人，我希望 026 第一阶段只冻结协议和边界，而不被迫同时交付控制台页面、scheduler 面板、runtime console 和 memory console，这样设计 gate 可以先通过，下游功能再按 contract 实现。

**Why this priority**: blueprint 明确要求先过 gate 再并行，而不是把所有页面和协议一起绑死在同一批工作里。

**Independent Test**: 检查规范是否把页面实现与 runtime console 等排除在本阶段范围外，同时仍能为下游提供完整 contract。

**Acceptance Scenarios**:

1. **Given** 025-B 只需要 `wizard session`、`config schema`、`project selector`，**When** 它消费本 contract，**Then** 不需要等待 Web 控制台页面实现。
2. **Given** 026-B 需要 `session/chat projection`、`automation job`、`diagnostics summary`，**When** 它消费本 contract，**Then** 不需要先定义 memory console 或 runtime console 页面细节。

---

### Edge Cases

- 当 consumer 收到更高 major 版本的 contract 时，应如何识别“不兼容”而不是错误解析？
- 当某个表面不支持特定动作或特定 `uiHints` 时，如何显式降级而不是静默漂移？
- 当 `project selector` 当前没有任何可选 project，或正在迁移默认 project 时，contract 如何表达 fallback 与 warning？
- 当 `session/chat projection` 只提供摘要与 export ref，而不内联 transcript 时，consumer 如何识别这是预期边界而不是数据缺失？
- 当 `automation job` 动作是异步的，如 `run-now` 已受理但尚未完成，结果与事件如何表达 `deferred`？
- 当 diagnostics 某个子系统 unavailable，但总体控制面仍可工作时，如何明确 degrade 而不把整个 contract 判成失败？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 发布带 `contract_version` 的 versioned control-plane contract，且所有 contract document 与 action/event envelope 都必须声明所属 contract version。
- **FR-002**: 系统 MUST 定义六类 canonical resource documents：`WizardSessionDocument`、`ConfigSchemaDocument`、`ProjectSelectorDocument`、`SessionProjectionDocument`、`AutomationJobDocument`、`DiagnosticsSummaryDocument`。
- **FR-003**: 每个 resource document MUST 提供稳定的 `resource_type`、`resource_id`、`schema_version`、`generated_at` 或 `updated_at`，以及可被 consumer 理解的状态/能力摘要。
- **FR-004**: `WizardSessionDocument` MUST 表达当前 step、step 状态、可恢复性、阻塞原因与下一步建议动作，供 CLI/Web 共用。
- **FR-005**: `ConfigSchemaDocument` MUST 同时包含 machine-readable config schema 与 transport-agnostic `uiHints`；`uiHints` MUST NOT 绑定某个单独前端框架或组件实现。
- **FR-006**: `ProjectSelectorDocument` MUST 表达当前 project、可选 project/workspace、默认或 fallback 选择、readiness/warnings，以及是否允许切换的能力信息。
- **FR-007**: `SessionProjectionDocument` MUST 表达 session/chat 的生命周期摘要、关键 capability（如 history/export/queue/focus/reset/interrupt/resume 的可用性）以及相关引用；它 MUST NOT 以“必须内联完整 transcript”为前提。
- **FR-008**: `AutomationJobDocument` MUST 表达 job 标识、project/channel/target 绑定、schedule 摘要、当前状态、支持动作、最新运行摘要以及 run history 的引用或 cursor。
- **FR-009**: `DiagnosticsSummaryDocument` MUST 表达 readiness/health 聚合结果、子系统状态、degraded reasons、最近失败摘要与深度诊断引用；它 MUST NOT 被要求内联原始日志流或完整 event stream。
- **FR-010**: 系统 MUST 定义共用的 `ActionRegistryDocument`，其中每个 `ActionDefinition` 至少包含 `action_id`、人类可读标签、支持表面、参数 schema、结果语义、risk/approval hints、idempotency hints 和 surface aliases。
- **FR-011**: CLI/Web/Telegram MUST 共享同一个 `action_id` 语义与结果码语义；表面只 MAY 提供不同 alias、文案或触发方式。
- **FR-012**: registry MUST 能显式声明某动作在某表面上的 `supported`、`unsupported`、`hidden` 或 `degraded` 状态，而不是允许 consumer 发明替代语义。
- **FR-013**: 系统 MUST 定义跨表面共用的 `ActionRequestEnvelope` 与 `ActionResultEnvelope`，并要求其与 registry 的 `action_id` 和参数/结果语义一一对应。`ActionRequestEnvelope` MUST 携带稳定 `request_id`；`ActionResultEnvelope` MUST 回显同一 `request_id`，并在 `deferred` 或其他异步场景下提供稳定 `correlation_id`，使后续事件可以与最初请求稳定关联。
- **FR-014**: 系统 MUST 定义统一 `ControlPlaneEvent` envelope，至少包含 `event_type`、`contract_version`、`request_id`、`correlation_id`、`causation_id`、`actor`、`surface`、`occurred_at` 与 `payload_summary`。其中 `request_id` MUST 用于把动作请求、即时结果与后续事件稳定串联。
- **FR-015**: `ControlPlaneEvent` MUST 同时支持资源事件与动作事件两类语义：`control.resource.*` 事件 MUST 携带单个 `resource_ref`（含 `resource_type`、`resource_id`、`schema_version`）；`control.action.*` 事件 MAY 携带 `0..n` 个 `resource_refs` 和/或 `target_refs`，且 MUST NOT 假定所有动作都天然对应某一个 canonical resource。事件族至少包括：`control.resource.projected`、`control.resource.removed`、`control.action.requested`、`control.action.completed`、`control.action.rejected`、`control.action.deferred`。
- **FR-016**: 兼容策略 MUST 规定：新增可选字段、可选 `uiHints`、可选 registry metadata 属于 minor-compatible 变化；删除字段、改变字段语义、改变动作语义或结果码语义属于 breaking change，并 MUST 通过 major version 表达。
- **FR-017**: consumer MUST 能忽略未知可选字段与其不支持的 `uiHints`；backend MUST 在当前 major contract 生命周期内为 deprecated 字段或动作提供显式替代信息。
- **FR-018**: backend MUST 作为 canonical producer，负责资源投影生成、授权判定、动作执行、兼容元数据与事件发射。
- **FR-019**: frontend/Web、CLI 与 Telegram MUST 被视为 contract consumer：它们可以读取/缓存 contract documents、渲染支持范围内的字段、提交 action 请求并消费结果/事件，但 MUST NOT 自造 canonical resource 字段、私自修改投影或重定义动作语义。
- **FR-020**: contract MUST 显式表达 unavailable/degraded 状态，从而满足 `Degrade Gracefully`，避免某一 backend capability 不可用时让整个控制面 contract 失效。
- **FR-021**: Feature 026 第一阶段 MUST 产出可被 025-B / 026-B 直接消费的 contract 规范，且这些下游实现 MUST NOT 依赖本阶段先交付 Web 控制台页面、session center UI、scheduler 面板、runtime console 页面或 memory console。
- **FR-022**: Feature 026 第一阶段 MUST NOT 在 contract 中定义具体页面布局、组件树、scheduler runtime 内部机制、raw log streaming 协议或 Memory Console / Vault 浏览行为。

### Key Entities

- **ControlPlaneContract**: 控制面共享契约集合，定义整体 `contract_version` 与兼容策略。
- **WizardSessionDocument**: 多端共享的 onboarding/config wizard 会话投影。
- **ConfigSchemaDocument**: 机器可读配置 schema 与 `uiHints` sidecar 的统一文档。
- **ProjectSelectorDocument**: 当前 project 选择态、候选列表和切换能力的摘要对象。
- **SessionProjectionDocument**: 用户/Agent 会话生命周期的聚合投影对象。
- **AutomationJobDocument**: automation/scheduler job 的只读摘要与能力边界对象。
- **DiagnosticsSummaryDocument**: 运行健康、子系统状态与深度诊断引用的聚合摘要对象。
- **ActionRegistryDocument**: 共用动作注册表，定义 canonical `action_id` 与 surface aliases。
- **ActionRequestEnvelope / ActionResultEnvelope**: CLI/Web/Telegram 共用的动作请求与结果 envelope，至少包含稳定 `request_id`，并在异步场景中保留 `correlation_id`。
- **ControlPlaneEvent**: 控制面资源变化与动作执行的统一事件 envelope；资源事件绑定单个 `resource_ref`，动作事件可关联多个 `resource_refs` / `target_refs`。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 规范一次性冻结六类 mandatory resource documents、action registry、action request/result envelope、control-plane event envelope 与兼容策略，且无未解决的澄清占位标记残留。
- **SC-002**: 规范明确说明同一 `action_id` 可被 CLI/Web/Telegram 以不同 alias 触发，但语义、参数、结果码以及 `request_id/correlation_id` 关联路径保持一致。
- **SC-003**: 六类 resource documents 全部具备 producer/consumer 边界与 degraded/unavailable 表达规则。
- **SC-004**: 规范可让下游实现明确区分 minor-compatible 与 major-breaking contract 变更，而不依赖临时口头约定。
- **SC-005**: `checklists/requirements.md` 的所有检查项均通过，从而满足 `GATE_DESIGN` 的规范质量要求。

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 026 第一阶段是否需要交付 Web 控制台页面？ | 否 | 本阶段目标是先冻结 contract，页面实现留给 026-B |
| 2 | `uiHints` 是否允许写成 React/Rich/Telegram 的组件 DSL？ | 否 | `uiHints` 需要跨表面消费，只能表达字段元数据与交互意图 |
| 3 | `session/chat projection` 是否必须内联完整 transcript？ | 否 | 本阶段只冻结投影摘要与引用边界，完整 transcript/export 属于下游消费对象 |
| 4 | Telegram slash command、CLI command、Web button 是否可以定义不同动作语义？ | 否 | 三者必须共享同一 `action_id` 语义，只允许 alias 和展示方式不同 |
| 5 | `diagnostics summary` 是否包含 runtime console 的 raw logs/event stream？ | 否 | 本阶段只冻结摘要与引用，原始控制台数据属于后续 runtime console 子线 |

## Scope Boundaries

### In Scope

- versioned control-plane contract 的资源对象冻结
- `action/command registry` 冻结
- `ActionRequestEnvelope` / `ActionResultEnvelope` 冻结
- `ControlPlaneEvent` envelope 与事件族冻结
- 兼容策略、deprecation 策略、frontend/backend 消费边界
- 供 025-B / 026-B 直接消费的 contract 规范制品

### Out of Scope

- Web 控制台页面实现
- session center UI
- scheduler 面板与 runtime console 页面
- Memory Console / Vault 浏览与授权检索
- project migration 具体实现
- secret store/runtime scheduler 具体逻辑
- raw log streaming / full event stream 浏览协议
