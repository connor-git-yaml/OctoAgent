---
feature_id: "026"
title: "Control Plane Delivery（消费 026-A Contract）"
milestone: "M3"
status: "Implemented"
created: "2026-03-08"
updated: "2026-03-08"
research_mode: "full"
blueprint_ref: "docs/m3-feature-split.md Feature 026；docs/blueprint.md M3 产品化约束；.specify/features/026-control-plane-contract/spec.md(026-A freeze)"
predecessor: "Feature 015 / 017 / 019 / 021 / 022 / 025-A"
---

# Feature Specification: Control Plane Delivery（消费 026-A Contract）

**Feature Branch**: `codex/feat-026-control-plane`
**Created**: 2026-03-08
**Updated**: 2026-03-08
**Status**: Implemented
**Input**: 落实 M3 Feature 026 后续全部范围，以上游为 `docs/m3-feature-split.md` 的 Feature 026、`docs/blueprint.md` 的 M3 产品化约束，以及已冻结的 026-A contract。
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

Feature 026-A 已经冻结了 canonical control-plane contract，但当前系统仍停留在“能力分散存在、产品控制台不存在”的状态：

1. `wizard session`、`config schema`、`project/workspace`、`execution session`、`operator inbox`、`backup/update/import` 仍散落在 provider/core/gateway 的不同 API 和文件模型中。
2. Web 端仍只有 `TaskList + TaskDetail + 两张卡片` 的最小 UI，缺少正式 control plane shell、统一导航与资源消费者。
3. Telegram 端虽然已有 callback/operator 行为，但没有统一 `action_id` / `command alias` 层，仍是 surface-specific 语义。
4. 自动化/调度、runtime diagnostics、项目切换、配置中心、channel/device 管理等 M3 控制面目标还没有统一资源文档、动作注册表和审计事件链。

本阶段目标不是重新设计 026-A contract，而是**严格消费已冻结 contract**，把 backend canonical producer、action registry、event path 与正式 Web control plane 一次性落地，并与 025-A project/workspace 基线兼容。

## Product Goal

交付一个可以日常使用的 control plane：

- backend 对外发布六类 canonical resources 与统一 `ActionRequest/ActionResult/ControlPlaneEvent`
- Web UI 从最小 task 页演进为正式 Control Plane
- Telegram / Web 共享相同动作语义与结果码
- Operator、Backup/Restore/Import/Update、Config、Projects、Sessions、Automation、Diagnostics、Channels/Devices 全部进入同一控制台
- 保持 025-A 兼容，并给 025-B Secret Store / Wizard 留直接消费接口

## Contract Compatibility

本 Feature 明确继承 026-A 的 frozen contract，不得重新定义以下 canonical semantics：

- `WizardSessionDocument`
- `ConfigSchemaDocument`
- `ProjectSelectorDocument`
- `SessionProjectionDocument`
- `AutomationJobDocument`
- `DiagnosticsSummaryDocument`
- `ActionRegistryDocument`
- `ActionRequestEnvelope`
- `ActionResultEnvelope`
- `ControlPlaneEvent`

本阶段允许的变化只限于：

- 为 frozen contract 增加实现所需的可选字段
- 为资源增加可选 `capabilities / refs / uiHints / degraded` 细节
- 为 registry 增加可选 metadata、更多 action definitions、更多 surface aliases

不允许：

- 改变既有字段语义
- 改变既有 `action_id` / 结果码 / 事件类型语义
- 引入替代性的 resource naming 或 surface-private canonical field

---

## User Scenarios & Testing

### User Story 1 - 我可以在一个正式控制台里查看并操作所有核心控制面对象 (Priority: P1)

作为 operator，我希望 Web 首页不是“任务列表拼接几张卡片”，而是一个正式 Control Plane，可以切换 Projects、查看 Sessions、处理 Operator 工作项、执行 Backup/Update/Import、查看 Diagnostics 与 Automation。

**Why this priority**: 这是 M3 产品化控制面的核心目标，若没有正式壳层和统一入口，026 仍只是 contract 文档。

**Independent Test**: 启动 Gateway 后，Web 打开 `/`，可以看到正式 control plane shell 与 `Dashboard / Projects / Sessions / Operator / Automation / Diagnostics / Config / Channels` 导航，且各分区都消费 backend resource documents，而不是直接拼 route-specific DTO。

**Acceptance Scenarios**:

1. **Given** Gateway 已启动且存在 default project，**When** Web 打开 control plane，**Then** 它能同时读取项目、会话、配置、operator、diagnostics、automation 摘要并渲染统一导航。
2. **Given** operator inbox 里存在审批、告警、可重试失败或 pairing request，**When** 我进入统一控制台，**Then** 我可以在同一 UI 中处理，而不必跳到独立 route。
3. **Given** update/recovery/import 等运维能力已经存在，**When** 我进入 Operator/Diagnostics/Automation 区域，**Then** 这些动作能以统一 action registry 暴露，而不是散在多个私有 API 上。

---

### User Story 2 - 同一个动作在 Web 与 Telegram 上语义一致 (Priority: P1)

作为 operator，我希望 Telegram 指令和 Web 按钮触发的是同一个 `action_id`，这样审批、取消、重试、项目切换、备份、状态查询不会出现“名字相同但行为不同”的情况。

**Why this priority**: Feature 026 的关键不是“多几个页面”，而是统一控制语义。

**Independent Test**: 对同一个动作，例如 `project.select`、`task.cancel`、`backup.create`，验证 Web 提交与 Telegram command alias 都经同一 registry/action executor 处理，并返回兼容的 `ActionResultEnvelope`。

**Acceptance Scenarios**:

1. **Given** registry 中存在 `project.select`，**When** Web dropdown 与 Telegram `/project select ...` 触发该动作，**Then** backend 都按相同 project/workspace 选择语义解释请求。
2. **Given** `task.cancel` 已在 Web 中作为按钮暴露，**When** Telegram 以 `/cancel <task_id>` 调用，**Then** 两边共享同一风险、审计和结果码语义。
3. **Given** 某个动作在 Telegram 上暂不支持完整参数输入，**When** Telegram 查询 registry，**Then** 它会看到 `unsupported` 或 `degraded`，而不是自行发明语义。

---

### User Story 3 - 我可以把配置、项目、会话和自动化当成正式资源管理 (Priority: P1)

作为 operator，我希望 `project selector`、`config schema + uiHints`、`session/chat projection`、`automation job` 都是可直接读取和操作的正式资源，而不是若干 route 拼出来的 JSON。

**Why this priority**: 没有正式资源对象，就无法支撑后续 Wizard、Secret Store、Automation 和 Control UI 演进。

**Independent Test**: 通过 backend route 分别读取六类 canonical resources，验证 `contract_version`、`resource_type`、`resource_id`、`schema_version`、`capabilities`、`degraded` 表达均符合 026-A contract。

**Acceptance Scenarios**:

1. **Given** 025-A 已创建 default project，**When** 读取 `ProjectSelectorDocument`，**Then** 当前 project、候选 project/workspace、fallback/warnings 与切换能力全部明确表达。
2. **Given** `octoagent.yaml` 存在，**When** 读取 `ConfigSchemaDocument`，**Then** 返回 machine-readable schema、transport-agnostic `uiHints` 和当前可编辑值。
3. **Given** 系统存在多个 thread/task/execution session，**When** 读取 `SessionProjectionDocument`，**Then** 我能看到 history/export/focus/reset/interrupt/resume 等 capability 摘要和明细引用。
4. **Given** 存在自动化作业，**When** 读取 `AutomationJobDocument`，**Then** 我能看到 project binding、schedule、状态、run history 摘要和支持动作。

---

### User Story 4 - 运行诊断、恢复与渠道设备管理都进入统一控制台 (Priority: P1)

作为 operator，我希望 health、doctor、update、backup/recovery、Telegram pairing/device trust、channel readiness 和 runtime 诊断都进入同一个控制面，而不是分散在 CLI 或点状 API。

**Why this priority**: M3 产品化要求“系统可观察、可恢复、可审批、可运维”。

**Independent Test**: `DiagnosticsSummaryDocument` 能聚合 readiness/health/update/recovery/channel/provider/runtime 状态；Channels 面板与 Operator 面板能处理 pairing、allowlist、policy 相关工作流。

**Acceptance Scenarios**:

1. **Given** `/ready` 某个 profile degraded，**When** 读取 `DiagnosticsSummaryDocument`，**Then** 它明确展示 degraded reason、子系统状态和深度诊断引用。
2. **Given** Telegram 存在待配对用户和已授权用户，**When** 我进入 Channels/Devices 区域，**Then** 我能看到 pending/approved/device trust 视图并触发审批动作。
3. **Given** 最近存在 backup/update/import 记录，**When** 我查看控制台，**Then** 它会以统一入口展示最新状态和可继续动作。

---

### User Story 5 - 自动化作业是正式的、可恢复的产品对象 (Priority: P2)

作为 operator，我希望 automation/scheduler 是正式资源，而不是零散脚本；我可以创建、运行、暂停、恢复作业，并看到历史运行结果。

**Why this priority**: M3 blueprint 明确要求 automation/scheduler 产品化。

**Independent Test**: 创建一个绑定 `project_id` 的 automation job，验证它可持久化、可被 Gateway 启动时恢复加载、可立即 run-now、可 pause/resume，并生成 run history 与 control-plane events。

**Acceptance Scenarios**:

1. **Given** 我创建一个 `backup.create` 的 cron/interval job，**When** Gateway 重启，**Then** job 仍存在并可重新调度。
2. **Given** 我手动执行 `run-now`，**When** action 被受理，**Then** result/event 会返回稳定 `request_id/correlation_id`。
3. **Given** 作业已暂停，**When** 我读取 `AutomationJobDocument`，**Then** 能看到其状态、下一次运行与最近一次运行摘要。

## Edge Cases

- 当前 project 只有一个 default project 时，`project.select` 应如何表达“可见但不可切换”？
- config schema 中包含列表、映射、枚举、env-name bridge、敏感字段引用时，`uiHints` 如何保持 transport-agnostic？
- `session` 可能只有 task 摘要、没有 live execution session；资源应如何显式 degrade？
- 自动化作业可能触发 `deferred` 动作，例如 `update.apply`；result/event 如何稳定关联到 attempt/run？
- Telegram surface 不适合复杂 JSON 参数输入时，应如何明确标识 `unsupported/degraded` 而不是让 alias 语义漂移？
- diagnostics 某个子系统 unavailable 但整体 control plane 仍可工作时，资源必须如何表达局部降级？
- Memory Console / Vault 本 Feature 只做入口集成时，控制台如何明确这是“可导航入口”而不是“空实现”？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 实现 026-A 已冻结的 six canonical resources、`ActionRegistryDocument`、`ActionRequestEnvelope`、`ActionResultEnvelope` 与 `ControlPlaneEvent` 的 backend canonical producer。
- **FR-002**: backend 发布的 control-plane resources MUST 保留 026-A 的 `contract_version`、`resource_type`、`resource_id`、`schema_version`、`generated_at/updated_at` 与 `degraded/unavailable` 语义，不得重定义 canonical field。
- **FR-003**: 系统 MUST 提供正式 control-plane route surface，至少包含 per-resource route、action registry route、action execution route、control-plane events route，以及一个供 Web 首屏加载的 snapshot route。
- **FR-004**: `WizardSessionDocument` MUST 以现有 onboarding/session 基线为上游，表达当前 step、step 状态、next actions、阻塞原因、可恢复性与 summary；它 MUST 为 025-B Secret Store / Wizard 预留直接消费位点。
- **FR-005**: `ConfigSchemaDocument` MUST 提供 machine-readable schema、transport-agnostic `uiHints`、当前配置值、validation hints 与兼容桥信息；`uiHints` MUST NOT 绑定某个具体前端框架。
- **FR-006**: `ProjectSelectorDocument` MUST 兼容 025-A 的 default project migration 基线，表达当前 project/workspace、候选 project/workspace、fallback/default、warnings 与 switch capabilities，并允许 future Secret Store/Wizard 直接依赖它。
- **FR-007**: `SessionProjectionDocument` MUST 以 task/thread/execution/operator 现有数据为上游，提供 session/chat 生命周期摘要、capabilities、latest execution summary、history/export/focus/reset/new/interrupt/resume refs；它 MUST NOT 依赖完整 transcript 内联。
- **FR-008**: `AutomationJobDocument` MUST 作为正式持久化对象存在，表达 job 标识、project binding、schedule、状态、支持动作、最新运行摘要、run history cursor/ref 与 degraded reason。
- **FR-009**: `DiagnosticsSummaryDocument` MUST 聚合 readiness/health、provider/model/runtime、update/recovery、channel readiness、project migration、recent failures、degraded reasons 与深度诊断 refs；它 MAY 引用日志/事件流，但 MUST NOT 强制内联 raw logs。
- **FR-010**: 系统 MUST 定义共用 `ActionRegistryDocument`，并为每个动作声明 `action_id`、label、supported surfaces、surface aliases、参数 schema、结果语义、risk/approval hints、idempotency hints、resource refs 与 degraded/unsupported 状态。
- **FR-011**: Web 与 Telegram MUST 共享同一 `action_id` 语义与结果码语义；Telegram slash/文本 command 仅可作为 alias 层，不得定义独立 canonical 动作。
- **FR-012**: 系统 MUST 提供共用 `ActionRequestEnvelope` / `ActionResultEnvelope`，至少包含稳定 `request_id`；当动作是异步或 deferred 时，result/event MUST 返回稳定 `correlation_id` 以关联后续进展。
- **FR-013**: 系统 MUST 通过统一 `ControlPlaneEvent` path 发出 `control.action.requested/completed/rejected/deferred` 和 `control.resource.projected/removed` 事件，并允许 Web Diagnostics/Automation 面板消费这些事件。
- **FR-014**: control-plane action executor MUST 复用现有 backend service 边界与审计事实链：task/execution 相关动作通过 `TaskRunner/ExecutionConsole/TaskService`，operator 相关动作通过 `OperatorActionService`，backup/update/import 相关动作通过 provider DX services；frontend MUST NOT 直接调用 side-effect library。
- **FR-015**: 对审批或高风险动作，control-plane action executor MUST 复用现有 `PolicyEngine/ApprovalManager` 能力，并把结果映射回统一 action result / control-plane events。
- **FR-016**: 系统 MUST 提供正式 Web Control Plane Shell，至少包含 `Dashboard`、`Projects`、`Sessions`、`Operator`、`Automation`、`Diagnostics`、`Config`、`Channels` 这几类主导航区域。
- **FR-017**: Web Control Plane MUST 以 control-plane resource documents 和 action registry 为唯一 canonical API，不得在前端定义平行 DTO 或改写动作语义。
- **FR-018**: `Config Center` MUST 基于 `ConfigSchemaDocument.schema + uiHints` 渲染；修改配置时 MUST 先做 backend validation，再保存 `octoagent.yaml`，并同步需要的 LiteLLM 配置桥。
- **FR-019**: `Project Selector` MUST 作为 control plane 统一能力同时出现在 shell 顶层与项目面板中，并持久化当前 selection/focus 状态；selection 不存在时 MUST fallback 到 default project。
- **FR-020**: `Session Center` MUST 提供 session list/detail 视图，并把 approvals、retry、cancel、resume、execution input、export 等控制入口统一呈现。
- **FR-021**: `Operator / Management` 面 MUST 把 approvals / retry / cancel / pairing / backup / restore / import / update 收敛到统一控制台入口，并以 registry/action executor 统一调用。
- **FR-022**: `Channels / Devices` 面 MUST 展示 channel readiness、Telegram pairing/approved users、group/device trust 与当前 channel policy；对未实现的设备域 MUST 以显式 `degraded` / `not_yet_enabled` 表达。
- **FR-023**: `Automation / Scheduler` 面 MUST 支持 create / run-now / pause / resume / delete / run-history，并在 Gateway 启动时恢复调度状态。
- **FR-024**: `Runtime Diagnostics Console` MUST 至少提供 health summary、recent control-plane events、recent execution/task failures、provider/update/recovery/runtime snapshot 与 deep refs；它 MAY 通过 polling 消费 control-plane events。
- **FR-025**: Memory Console / Vault 在本 Feature 内 MUST 仅作为统一控制台入口和 contract-level integration 呈现，不实现详细领域浏览、授权检索或深度查询页面。
- **FR-026**: backend 与 frontend MUST 保持对 025-A Project/Workspace 基线兼容，并为 025-B Secret Store/Wizard 预留直接消费入口，不得把 secret 实值写入 YAML、control-plane docs 或前端缓存。
- **FR-027**: 所有新增持久化对象与控制动作 MUST 满足 `Durability First` 与 `Everything is an Event`：automation/job selection 等状态必须可恢复，关键动作必须进入审计事件链。
- **FR-028**: 本 Feature MUST 补齐关键 API 测试、projection 测试、Telegram/Web action 语义测试、frontend integration 测试和 e2e 测试矩阵。

### Key Entities

- **ControlPlaneContract**: 026-A 冻结并由本阶段实现的整体 contract 集。
- **WizardSessionDocument**: onboarding / config wizard 的共享会话投影。
- **ConfigSchemaDocument**: 机器可读配置 schema、`uiHints`、当前配置值与 bridge 元数据。
- **ProjectSelectorDocument**: 当前项目选择态、候选 project/workspace、fallback 与切换能力摘要。
- **SessionProjectionDocument**: 面向 Session Center 的 thread/task/execution/operator 聚合投影。
- **AutomationJobDocument**: 自动化/调度作业正式对象，含 project binding、schedule、状态和运行历史摘要。
- **DiagnosticsSummaryDocument**: readiness/health/provider/runtime/update/recovery/channel 聚合诊断对象。
- **ActionRegistryDocument**: 全表面共享动作注册表。
- **ActionRequestEnvelope / ActionResultEnvelope**: control-plane 动作请求/结果统一封装。
- **ControlPlaneEvent**: control-plane 动作与资源变化统一事件。
- **ControlPlaneState**: 当前 selected project/workspace、session focus 与 UI-level durable preference 的最小持久化对象。
- **AutomationJobRun**: automation job 的单次执行记录，用于 run history、deferred correlation 与 diagnostics。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 六类 canonical resources、registry、action envelopes、control-plane events 均有 backend 实现和路由发布，且字段语义与 026-A 无冲突。
- **SC-002**: Web Control Plane Shell 在一个正式入口中覆盖 `Dashboard / Projects / Sessions / Operator / Automation / Diagnostics / Config / Channels`，不再以 task list 充当首页。
- **SC-003**: 至少 8 个关键动作由统一 registry 暴露并可被 Web/Telegram 共享语义调用，包含 `project.select`、`task.cancel`、`task.retry`、`backup.create`、`update.apply`、`update.dry_run`、`wizard.restart`、`automation.run`。
- **SC-004**: `config.apply`、`project.select`、`automation.create/run/pause/resume`、`operator task/approval actions`、`backup/update/import` 路径均有统一 action result 与 control-plane events。
- **SC-005**: 自动化作业在 Gateway 重启后仍可恢复加载和运行，run history 可查询。
- **SC-006**: 前端只消费 control-plane route surface，不再直接拼接旧的 route-specific DTO；旧 task detail 只作为明细视图保留。
- **SC-007**: 测试矩阵包含 backend API/projection、Telegram/Web action 语义、frontend integration、e2e，且关键路径全部通过。

## Clarifications

### Session 2026-03-08

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 是否允许重新定义 026-A 的 canonical resource 或 action semantics？ | 否 | 用户明确要求消费 026-A，不得重定义 |
| 2 | Secret Store 实值与 Wizard 详细领域是否在本 Feature 实现？ | 否 | 本阶段只做 025-B 直接消费预留，不落 secret 实值存储 |
| 3 | Memory Console / Vault 是否要实现详细浏览与授权检索？ | 否 | 仅保留统一控制台入口与 contract-level integration |
| 4 | Web Control Plane 是否必须替换当前最小 TaskList 首页？ | 是 | 用户要求把最小 React Web UI 演进为正式 control plane |
| 5 | Telegram 是否必须共享相同动作语义？ | 是 | 这是 Feature 026 的核心约束之一 |

## Scope Boundaries

### In Scope

- control-plane backend resources / routes / snapshot
- action/command registry、action request/result envelope、control-plane event path
- wizard/config/project/session/automation/diagnostics 六类 canonical resources 的 producer
- project selector、session center、automation panel、diagnostics console、config center、channel/device 管理面、operator/ops unified entry
- Telegram / Web 共用 command/action semantics
- 正式 Web Control Plane shell 与关键页面
- 关键 API、projection、frontend integration、e2e 测试

### Out of Scope

- Secret Store 实值持久化
- Wizard 详细多步 UI 编排与 Secret 输入细节
- Memory Console / Vault 详细浏览与授权检索
- 全量 agent/skill/subagent 可视化编排器
- ToolBroker 新领域建模或 worker-internal tool catalog 改造

## Risks & Design Notes

- 当前代码里只有 execution/operator/ops/onboarding/project 的局部投影，026 最大风险是“继续把旧 DTO 包一层”。本阶段必须抽出真正的 resource document 与 action registry。
- config schema renderer 最大风险是演化成 React-only form DSL；因此 `uiHints` 只能表达 field intent/section/layout-hint，不能表达具体组件树。
- 自动化作业若直接调用旧 route 而不走统一 action executor，会破坏审计与语义统一；因此 automation 必须基于 action registry 执行。
- project selector 当前只能稳定依赖 025-A 的 default project 基线；多 project/多 workspace 的 richer behavior 应通过 `degraded` / `not_configured` 明示，而不是假装已经完整可用。
