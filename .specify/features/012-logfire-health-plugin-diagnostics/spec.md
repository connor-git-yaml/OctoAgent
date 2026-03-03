# Feature Spec: Feature 012 Logfire + Health/Plugin Diagnostics

**Feature Branch**: `codex/feat-012-logfire-health-plugin-diagnostics`
**Created**: 2026-03-03
**Status**: Implemented (Phase 7 Complete)
**Input**: 推进需求 012

## Clarifications

- 设计门禁（GATE_DESIGN）结论：继续实现（用户指令“推进需求 012”视为批准）。
- `/ready` 中新增 `subsystems` 为诊断增强，不改变现有 core readiness 判定逻辑。
- `register()` 保持 strict 语义，新增 `try_register()` 承担 fail-open 注册场景。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 运维可见性提升 (Priority: P1)

作为系统维护者，我希望通过统一健康检查快速看到核心子系统状态，
从而在故障发生时能在 1 次请求内定位问题层级。

**Why this priority**: 直接影响系统可运维性，是 M1.5 集成前的稳定性前提。

**Independent Test**: 调用 `/ready`，返回 `checks` 与 `subsystems` 结构，且在组件异常时返回可解释状态。

**Acceptance Scenarios**:

1. **Given** 系统正常运行, **When** 调用 `GET /ready`, **Then** 返回 200 且包含 `subsystems` 状态映射。
2. **Given** 某子系统不可用, **When** 调用 `GET /ready`, **Then** 返回状态含 `unavailable` 并保留其他可用检查结果。

---

### User Story 2 - 工具注册诊断可追踪 (Priority: P1)

作为研发者，我希望工具注册失败时系统不崩溃且有结构化诊断，
从而可以快速定位冲突工具并决定修复或降级。

**Why this priority**: 直接对应 C6（Degrade Gracefully），避免插件/工具问题拖垮系统。

**Independent Test**: 调用 `ToolBroker.try_register()` 重复注册同名工具时，返回失败并写入 diagnostics。

**Acceptance Scenarios**:

1. **Given** 工具首次注册, **When** 调用 `try_register`, **Then** 返回 `ok=true` 且 diagnostics 无 error。
2. **Given** 同名工具重复注册, **When** 调用 `try_register`, **Then** 返回 `ok=false` 且 diagnostics 含冲突详情。

---

### User Story 3 - 可观测初始化可降级 (Priority: P2)

作为系统维护者，我希望 Logfire 在配置错误时自动降级而不是阻断启动，
从而保证系统持续可用。

**Why this priority**: 满足 C6 与 C8，减少配置错误造成的启动失败。

**Independent Test**: 设置异常 Logfire 环境，应用仍能启动并输出结构化 warning。

**Acceptance Scenarios**:

1. **Given** `LOGFIRE_SEND_TO_LOGFIRE=true` 且配置有效, **When** 启动应用, **Then** Logfire instrumentation 生效。
2. **Given** `LOGFIRE_SEND_TO_LOGFIRE=true` 但初始化抛错, **When** 启动应用, **Then** 启动成功且记录 `logfire_init_failed`。

---

### Edge Cases

- 工具注册 diagnostics 列表为空时，健康检查应返回 `ok` 而不是 `unknown`。
- 子系统对象未挂载到 `app.state` 时，健康检查应返回 `unavailable`，不可抛异常。
- 日志上下文字段缺失时，系统应继续运行并在诊断中标记不完整。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `ToolBroker.try_register(tool_meta, handler)`，用于 fail-open 注册流程。
- **FR-002**: 系统 MUST 维护结构化 `registry_diagnostics`，至少包含 `tool_name`、`error_type`、`message`、`timestamp`。
- **FR-003**: 系统 MUST 在 `/ready` 响应中增加 `subsystems` 检查块，包含关键运行子系统状态。
- **FR-004**: 系统 MUST 保持 `register()` 的现有严格语义（冲突时抛错），避免破坏既有调用方。
- **FR-005**: 系统 MUST 支持 Logfire 开关与失败降级，不得因 Logfire 初始化失败导致应用不可用。
- **FR-006**: 系统 SHOULD 在观测测试中验证关键上下文字段（`request_id/trace_id/span_id`）存在性与一致性。
- **FR-007**: 系统 MUST 为新增能力提供自动化测试覆盖（tooling 单测 + gateway 路由测试）。

### Key Entities

- **RegistryDiagnostic**: 工具注册诊断项，记录一次注册异常或告警。
- **SubsystemHealth**: 子系统健康状态映射值（`ok`/`unavailable`/`degraded`）。
- **ReadyResponseExtension**: `/ready` 响应扩展结构，新增 `subsystems` 字段。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `ToolBroker.try_register()` 在重复注册场景下 100% 返回可诊断结果，不抛未处理异常。
- **SC-002**: `/ready` 响应在正常与异常路径均稳定返回结构化 `subsystems` 字段。
- **SC-003**: Logfire 初始化异常场景下应用可启动，且有可检索 warning 日志。
- **SC-004**: 新增/更新测试全部通过，且不回归现有 health/tooling 相关测试。
