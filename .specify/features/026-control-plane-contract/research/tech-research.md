# Feature 026 技术调研：Control Plane Contract（第一阶段）

**日期**: 2026-03-08
**调研模式**: tech-only
**核心参考**:
- `docs/m3-feature-split.md` §3.3 / Feature 026 F026-T00
- `docs/blueprint.md` M3 产品化约束
- `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_models.py`
- `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`
- `octoagent/packages/core/src/octoagent/core/models/execution.py`
- `octoagent/packages/core/src/octoagent/core/models/operator_inbox.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/health.py`
- `_references/opensource/openclaw/docs/experiments/onboarding-config-protocol.md`
- `_references/opensource/openclaw/docs/web/control-ui.md`
- `research/online-research.md`

## 1. 设计约束

Feature 026 第一阶段必须同时满足以下硬约束：

1. 先过 `Control Plane Contract Gate`，再允许 CLI/Web/Telegram 并行开发。
2. 范围只冻结 contract，不交付 Web 控制台页面、session center UI、scheduler 面板、runtime console 页面、memory console。
3. contract 至少覆盖六类资源：`wizard session`、`config schema + uiHints`、`project selector`、`session/chat projection`、`automation job`、`diagnostics summary`。
4. 需要定义 CLI/Web/Telegram 共用的 `action/command registry`，并明确兼容策略、事件模型、frontend/backend 消费边界。
5. contract 必须能被 Feature 025-B / 026-B 直接消费，而不是和某个表面实现绑死。

## 2. 现有代码基盘点

### 2.1 已有可复用对象

- `OnboardingSession` 已经具备 step/state/summary/next actions 的稳定结构，适合作为 `wizard session` 的本地上游模型。
- `OctoAgentConfig` 已经定义了配置主模型，但目前缺少可跨端消费的 `config schema + uiHints` document。
- `ExecutionConsoleSession`、`ExecutionStreamEvent` 已经证明“session 是可投影对象”，但当前仍偏 execution-plane，缺少对 chat/session center 的统一 contract。
- `OperatorActionKind`、`OperatorActionRequest`、`OperatorActionResult` 已经提供了统一动作语义雏形，适合演进为更通用的 action registry/request/result envelope。
- `/ready` 已具备子系统健康汇总逻辑，可作为 `diagnostics summary` 的上游聚合来源。

### 2.2 当前缺口

- `wizard`、`config`、`execution`、`operator`、`ready` 仍然分散在 provider/core/gateway，多数是 route-specific payload，并非共享 contract。
- `project selector` 与 `automation job` 还没有正式产品对象，若不先冻结 contract，下游实现极易各自定义 DTO。
- CLI 命令、Telegram slash commands、Web 按钮之间尚无统一 `action_id -> args -> result` 映射层。
- 当前代码里没有显式的 control-plane event envelope，无法稳定承接跨表面的状态更新和异步动作反馈。

## 3. 外部参考实现收敛

### 3.1 OpenClaw onboarding/config protocol

可借鉴：

- `wizard.start / next / status / cancel` 证明 wizard session 可以被定义成 transport-agnostic contract。
- `config.schema -> {schema, uiHints, version, generatedAt}` 说明配置中心应把 machine-readable schema 与 UI hints 分层，而不是把 form layout 写死在单端。
- `uiHints` keyed-by-path 的 sidecar 结构很适合 OctoAgent 当前的 CLI/Web 共用目标。

不应照搬：

- 026 第一阶段不需要复制 OpenClaw 的页面层、Gateway RPC 细节或具体 Web renderer。
- `uiHints` 不能演变成“前端私有组件 DSL”，否则 Telegram/CLI 会被排除在 contract 外。

### 3.2 OpenClaw slash commands / control UI / cron jobs

可借鉴：

- 控制面动作应先收敛成统一 action semantics，再由 slash commands / Web controls / automation triggers 去调用。
- session、cron、control UI 被放进同一产品文档族，说明 session/automation 应作为产品对象建模，而不是 route 拼装。

不应照搬：

- 026 第一阶段不做具体页面 IA、cron 实现或 session center 交互。
- OpenClaw 的命令组织形式不应限制 OctoAgent 的 `action_id` 命名和 approval/policy 语义。

### 3.3 Agent Zero projects / scheduler / memory dashboard

可借鉴：

- `Project`、`Scheduler`、`Memory` 都是一等公民对象，且有独立 dashboard/操作面。
- automation 与 project 绑定、memory 与 project 绑定，这对 `project selector` 与 `automation job` contract 很关键。

不应照搬：

- 026 第一阶段不把 memory dashboard 拉进来；Memory Console 属于 027。
- 不能把 scheduler dashboard 的页面结构误当成 contract。

## 4. 架构方案对比

| 维度 | 方案 A：各表面各自 DTO | 方案 B：共享 versioned contract + adapter | 方案 C：纯 JSON Schema/配置中心先行 |
|------|------------------------|------------------------------------------|-----------------------------------|
| 核心思路 | CLI/Web/Telegram 各自定义请求与投影 | 定义统一资源/动作/event contract，各表面消费同一协议 | 先只做 schema/hints，其他对象继续分散 |
| 与现有代码兼容性 | 低，后续回收成本高 | 高，可复用 015/017/019/012 现有模型 | 中，只解决 config，不解决 session/automation |
| 对 025-B/026-B 支撑 | 差，容易再次分叉 | 最好，可并行消费同一 contract | 不足，无法覆盖六类资源 |
| 兼容策略清晰度 | 差 | 高，天然可挂 `contract_version` | 中，只能覆盖 schema |
| 风险 | 语义漂移、重复实现 | 需要先做抽象与边界定义 | 继续推迟真正的 control-plane gate |

**推荐**: 方案 B，先冻结共享 versioned contract，再由实现层做 adapter。

## 5. 推荐架构

### 5.1 三层划分

1. **Domain Models**
   - 保留现有 `OnboardingSession`、`ExecutionConsoleSession`、health summary 等业务模型。
2. **Control Plane Contract**
   - 新增一层共享 contract，定义资源 document、action registry、request/result envelope、event envelope、compatibility policy。
3. **Surface Adapters**
   - CLI/Web/Telegram 分别消费 contract，并把 surface-specific alias / command / button 映射到同一 `action_id`。

### 5.2 资源 contract 收敛

建议冻结以下资源 document：

- `WizardSessionDocument`
- `ConfigSchemaDocument`
- `ProjectSelectorDocument`
- `SessionProjectionDocument`
- `AutomationJobDocument`
- `DiagnosticsSummaryDocument`

每个 document 至少包含：

- `resource_type`
- `resource_id`
- `schema_version`
- `generated_at` / `updated_at`
- `capabilities`
- `status` 或 `degraded_reason`
- 面向 consumer 的稳定摘要字段

### 5.3 动作 contract 收敛

建议冻结以下共享对象：

- `ActionRegistryDocument`
- `ActionDefinition`
- `ActionRequestEnvelope`
- `ActionResultEnvelope`

关键原则：

- `action_id` 是跨表面的唯一语义锚点。
- `surface aliases` 只负责把 CLI verb、Telegram slash、Web button label 映射到同一 `action_id`。
- `risk/approval hints`、`idempotency scope`、`args_schema`、`supported_surfaces` 必须是 registry 的一部分，而不是散落在各个表面的 handler 中。
- `ActionRequestEnvelope` 至少要包含稳定 `request_id`；`ActionResultEnvelope` 在异步/`deferred` 场景中必须回显同一 `request_id`，并提供 `correlation_id` 供后续事件复用。

### 5.4 事件模型

建议冻结统一 control-plane event envelope，至少覆盖：

- `control.resource.projected`
- `control.resource.removed`
- `control.action.requested`
- `control.action.completed`
- `control.action.rejected`
- `control.action.deferred`

所有事件都需要：

- `contract_version`
- `request_id`
- `correlation_id`
- `causation_id`
- `actor`
- `surface`
- `occurred_at`
- `payload_summary`

事件引用规则：

- `control.resource.*` 事件绑定单个 `resource_ref`，其中包含 `resource_type`、`resource_id`、`schema_version`
- `control.action.*` 事件允许关联 `0..n` 个 `resource_refs` 和/或 `target_refs`
- 动作事件不能假定所有 action 都天然对应某一个 canonical resource；例如 `approve`、`model switch`、`backup.restore`、`import`、`update` 都可能只有 target 或 operation 关联

这使后续 Web、Telegram、CLI 都可以一致消费状态变化，而无需共享内部 service 调用栈。

### 5.5 兼容策略

- 整体 contract 使用 `SemVer`。
- 新增可选字段、可选 hint、可选 registry metadata 属于 minor-compatible 变更。
- 新增必填字段、删除字段、改变动作语义、改变结果码语义属于 major-breaking 变更。
- consumer 必须忽略未知可选字段与未知 hint；backend 必须在当前 major 内保留 deprecated 字段/动作的替代信息。

### 5.6 frontend / backend 边界

- backend 负责：canonical projection、authorization、action execution、event emission、compatibility metadata。
- frontend/Web、CLI、Telegram 负责：读取 resource documents、渲染支持范围内的字段与 hint、按 `action_id` 发送动作请求、消费结果与事件。
- consumer 不得自造 canonical fields、不得本地重解释 action semantics、不得绕过 backend 修改投影。

## 6. 依赖与落点建议

- 第一阶段不需要新增重型运行时依赖。
- 推荐在共享包中落 contract 定义，优先考虑 `packages/protocol` 的 control-plane 子模块，因为它天然面向跨应用/跨表面共享。
- 现有 provider/core/gateway 继续保留自己的业务模型与 routes，通过 adapter 输出 contract documents。

## 7. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | contract 直接照抄现有 route payload，导致后续无法演进 | 中 | 高 | 明确区分 domain model 与 contract model，先抽象 envelope 和 resource docs |
| 2 | `uiHints` 演变成 Web-only 组件 DSL | 中 | 高 | 只允许 field metadata / interaction intent，禁止页面布局和组件实现细节进入 contract |
| 3 | `project selector`、`automation job` 因尚未实现而定义过度空泛 | 中 | 中 | 冻结最小摘要/能力边界，避免提前承诺 runtime 细节 |
| 4 | Telegram/CLI 为了便捷继续保留私有动作语义 | 高 | 高 | 强制所有动作先注册 `action_id`，surface alias 只能映射不能改语义 |
| 5 | diagnostics summary 与 runtime console 边界混淆 | 中 | 中 | 只冻结 summary/ref/capability，不把 raw logs/event stream 塞进本阶段 contract |

## 8. 结论

026 第一阶段的正确落点不是“先做一个新页面”，而是：

1. 在共享层冻结 versioned control-plane contract。
2. 用 adapter 吸收 015/017/019/012 已有模型，避免重造业务对象。
3. 让 025-B / 026-B 基于同一资源 contract、action registry、event envelope 并行实现。

这样既满足 `GATE_DESIGN`，也不会把后续表面开发绑死在某个单端实现上。
