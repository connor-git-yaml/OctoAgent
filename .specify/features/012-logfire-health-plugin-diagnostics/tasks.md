# Tasks: Feature 012

## Phase A - 需求与设计

- [x] T001 完成产品/技术/在线调研并形成汇总
- [x] T002 完成 spec（User Story/FR/SC）与澄清项
- [x] T003 完成 requirements checklist（GATE_DESIGN 前置）

## Phase B - Tooling 实现

- [x] T004 新增 `RegisterToolResult` / `RegistryDiagnostic` 数据模型
- [x] T005 `ToolBroker` 增加 `try_register()`（fail-open）
- [x] T006 新增 `registry_diagnostics` 只读快照接口
- [x] T007 保持 `register()` strict 语义并补单测

## Phase C - Gateway 实现

- [x] T008 `/ready` 增加 `subsystems` 与 diagnostics 摘要
- [x] T009 Logfire 初始化增强（可选 HTTPX instrumentation + fail-open）
- [x] T010 LoggingMiddleware 透传 `request_id/trace_id/span_id`

## Phase D - 验证闭环

- [x] T011 tooling 测试（broker/models/protocols）
- [x] T012 gateway 测试（health/observability）
- [x] T013 生成 spec-review / quality-review / verification-report
