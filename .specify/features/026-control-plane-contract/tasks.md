# Tasks: Feature 026 — Control Plane Delivery

**Input**: `.specify/features/026-control-plane-contract/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/control-plane-api.md`
**Created**: 2026-03-08  
**Status**: Completed

**Tests**: backend API/projection、Telegram/Web action semantics、frontend integration、e2e 均为必选项。

## Phase 1: Docs / Design Lock

- [x] T001 回写 `.specify/features/026-control-plane-contract/*` 制品，锁定 frozen contract + delivery scope
- [x] T002 [P] 回读 `docs/blueprint.md` 与 `docs/m3-feature-split.md`，准备后续同步 026 完成口径
- [x] T003 [P] 补 product/tech/online research synthesis，记录 frozen contract 的实现约束

---

## Phase 2: Foundational Models & Audit Events (Blocking)

**Purpose**: 先建立 canonical models、automation persistence 与 control-plane event 基线，后续 producer/frontend 才能稳定接线

- [x] T004 在 `octoagent/packages/core/src/octoagent/core/models/control_plane.py` 新增 canonical resource documents、registry、action envelopes、event models、automation models
- [x] T005 在 `octoagent/packages/core/src/octoagent/core/models/enums.py` 增加 control-plane audit 事件类型
- [x] T006 在 `octoagent/packages/core/src/octoagent/core/models/payloads.py` 增加 control-plane event payload
- [x] T007 在 `octoagent/packages/core/src/octoagent/core/models/__init__.py` 导出 control-plane 公共模型
- [x] T008 在 `octoagent/packages/provider/src/octoagent/provider/dx/control_plane_state.py` 实现 selected project/workspace/session focus 持久化
- [x] T009 在 `octoagent/packages/provider/src/octoagent/provider/dx/automation_store.py` 实现 automation jobs / run history 持久化
- [x] T010 [P] 在 `octoagent/packages/core/tests/` 与 `octoagent/packages/provider/tests/` 新增 core models / automation store / control-plane state 基线测试

**Checkpoint**: canonical models、automation persistence、control-plane audit 基线完成

---

## Phase 3: Backend Resource Producers & Routes

**Goal**: 实现六类 canonical resources、snapshot、registry、actions、events routes  
**Independent Test**: `GET /api/control/snapshot` 能一次性返回六类资源与 registry；各 per-resource route 字段与 026-A contract 一致

### Tests for Backend Producers

- [x] T011 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 新增 snapshot / per-resource route 测试
- [x] T012 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_projection.py` 新增 project selector / session projection / diagnostics summary 测试
- [x] T013 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_actions.py` 新增 registry / action envelope / event emission 测试

### Implementation for Backend Producers

- [x] T014 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 实现六类 resource document producer
- [x] T015 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_events.py` 实现 control-plane audit event publisher / consumer
- [x] T016 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 实现 action registry、action executor、request/result envelope 映射
- [x] T017 在 `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py` 增加 resource / snapshot / registry / actions / events routes
- [x] T018 在 `octoagent/apps/gateway/src/octoagent/gateway/main.py` 注册 control-plane 服务、routes、automation scheduler 与 shared app state

**Checkpoint**: backend canonical producer 可用

---

## Phase 4: Existing Service Integration (M3 Control Surface)

**Goal**: 把已有 execution/operator/ops/onboarding/config/project/telegram 能力收敛到统一 action/resource 语义  
**Independent Test**: control-plane actions 能调用现有 task/execution/operator/backup/update/import/provider 流程，并发出统一 result/events

### Tests for Integration

- [x] T019 [P] 在 `octoagent/apps/gateway/tests/test_execution_api.py` / 新 control-plane 测试中覆盖 `session.interrupt` / `session.resume` / execution attach integration
- [x] T020 [P] 在 `octoagent/apps/gateway/tests/test_operator_actions.py` / 新 control-plane 测试中覆盖 approval / retry / cancel / pairing action 映射
- [x] T021 [P] 在 `octoagent/packages/provider/tests/test_backup_service.py`、`test_chat_import_service.py`、`test_update_service.py` 相关 control-plane adapter 测试

### Implementation for Integration

- [x] T022 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 接入 `OperatorActionService`
- [x] T023 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 接入 `TaskRunner` / `ExecutionConsoleService` / `TaskService`
- [x] T024 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 接入 `BackupService` / `ChatImportService` / `UpdateService`
- [x] T025 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 接入 `OnboardingService`、`ProjectStore`、`TelegramStateStore`、`Doctor/ready` 聚合
- [x] T026 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py` 或新 helper 中导出 `schema + uiHints` 生产逻辑
- [x] T027 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 实现 `config.apply`（validate + save + LiteLLM bridge sync）

**Checkpoint**: 旧能力全部通过 control-plane 统一暴露

---

## Phase 5: Automation / Scheduler Productization

**Goal**: automation 成为正式 product object  
**Independent Test**: job create/run-now/pause/resume/delete + restart recovery + run history 全部可用

### Tests for Automation

- [x] T028 [P] 在 `octoagent/apps/gateway/tests/test_automation_scheduler.py` 新增 automation store / schedule restore / run history 测试
- [x] T029 [P] 在 `octoagent/apps/gateway/tests/e2e/test_control_plane_e2e.py` 新增 automation create + run-now e2e 测试

### Implementation for Automation

- [x] T030 在 `octoagent/apps/gateway/src/octoagent/gateway/services/automation_scheduler.py` 实现 automation scheduler restore / arm / run / pause / resume
- [x] T031 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 生产 `AutomationJobDocument`
- [x] T032 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 实现 `automation.create/run/pause/resume/delete`

**Checkpoint**: automation/scheduler 面板 backend 完成

---

## Phase 6: Telegram / Web Shared Command Semantics

**Goal**: Telegram command alias 与 Web 按钮共享同一 action registry  
**Independent Test**: `/status`、`/project select`、`/approve`、`/cancel`、`/backup`、`/update` 等 command 最终落到同一 `action_id`

### Tests for Shared Semantics

- [x] T033 [P] 在 `octoagent/apps/gateway/tests/test_telegram_service.py` 新增 Telegram command alias -> action registry 测试
- [x] T034 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_actions.py` 新增 Web/Telegram 同 action_id 结果一致性测试

### Implementation for Shared Semantics

- [x] T035 在 `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py` 实现 Telegram control command 解析与 action registry 执行
- [x] T036 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane_actions.py` 实现 surface alias / unsupported / degraded 处理

**Checkpoint**: Telegram / Web 共语义成立

---

## Phase 7: Formal Web Control Plane

**Goal**: 最小 React UI 演进为正式 control plane shell  
**Independent Test**: Web 首页存在 shell + 主导航 + 资源加载 + 统一 action dispatcher

### Tests for Frontend Integration

- [x] T037 [P] 在 `octoagent/frontend/` 增加 vitest/jsdom 测试基线与 setup
- [x] T038 [P] 在 `octoagent/frontend/src/` 新增 dashboard/config/session/operator/automation 页面 integration tests
- [x] T039 [P] 在 `octoagent/apps/gateway/tests/e2e/test_control_plane_e2e.py` 覆盖 snapshot + action + frontend data-flow e2e

### Implementation for Frontend

- [x] T040 在 `octoagent/frontend/src/types/` 与 `api/` 接入 control-plane resource/action/event 类型与 client
- [x] T041 在 `octoagent/frontend/src/App.tsx` 重构正式 control-plane shell、路由与顶层 project selector
- [x] T042 在 `octoagent/frontend/src/pages/` 实现 `Dashboard / Projects / Sessions / Operator / Automation / Diagnostics / Config / Channels`
- [x] T043 在 `octoagent/frontend/src/components/` 实现 schema-driven config renderer、session center、automation panel、diagnostics console、channel/device 面板
- [x] T044 在 `octoagent/frontend/src/hooks/` 实现 snapshot polling、action dispatcher、control-plane event polling 与 shared cache
- [x] T045 在 `octoagent/frontend/src/index.css` 重构正式 control plane 视觉系统，同时保留移动端可用性

**Checkpoint**: 正式 Web Control Plane 可用

---

## Phase 8: Polish / Docs / Verification

- [x] T046 回写 `docs/blueprint.md`，同步 026 已交付的 control-plane 实现事实
- [x] T047 回写 `docs/m3-feature-split.md`，勾选 026 已完成项并标明 Memory/Vault detailed view 仍留给 027
- [x] T048 [P] 运行 Python `ruff` + 定向 `pytest`
- [x] T049 [P] 运行 frontend `npm test` / `npm run build`
- [x] T050 使用 `/review` 思维做一次全面代码审查并修复发现
- [x] T051 更新 `verification/verification-report.md`

## Completion Notes

- backend 的 action registry / executor / event publisher 实际收敛在 `apps/gateway/services/control_plane.py`，没有额外拆出 `control_plane_actions.py` 与 `control_plane_events.py`，以减少 026 内部过早分层。
- projection 与 action 语义测试主要聚合在 `test_control_plane_api.py`、`test_telegram_service.py`、`test_control_plane_e2e.py`，而不是按早期任务草案拆成更多测试文件。

## Dependencies & Execution Order

- Phase 2 是所有 backend/frontend 实现的阻塞前提
- Phase 3 必须先于 Phase 4/5/6/7
- Phase 5 automation 依赖 Phase 2 action/event/store 基线
- Phase 6 Telegram/Web shared semantics 依赖 registry/action executor 已完成
- Phase 7 前端依赖 Phase 3 的 snapshot/resources/routes 稳定
- 文档回写与 verification 在全部实现后执行
