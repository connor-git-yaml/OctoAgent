# Tasks: Feature 041 Butler / Worker Runtime Readiness + Ambient Context

## Phase 1: Research & Contract Freeze

- [x] T001 [P0] 对照 Agent Zero 的 current datetime / subordinate / browser agent 组织方式，确认 OctoAgent 当前缺口
- [x] T002 [P0] 扫描 OctoAgent 当前 `AgentContextService / CapabilityPackService / worker review/apply` 的真实实现，冻结 041 边界
- [x] T003 [P0] 建立 `Feature 041` spec / plan / research / verification 制品

## Phase 2: Ambient Runtime Context

- [x] T004 [P0] 在 `AgentContextService` 增加 ambient runtime block，并定义 timezone/locale/source 的优先级与 degraded 语义
- [x] T005 [P0] 设计并实现 `runtime.now`（或等价 deterministic 当前时间能力），同时补测试

## Phase 3: Bootstrap & Delegation Readiness

- [x] T006 [P0] 扩展 `bootstrap:shared`，注入 project/workspace + ambient runtime facts + capability summary
- [x] T007 [P0] 扩展 `bootstrap:general`，明确 weather/latest/web lookup 的 delegation 原则
- [x] T008 [P1] 扩展 research / ops bootstrap，使其清楚可用工具面、执行边界和降级说明

## Phase 4: Worker Planning & Tool Entitlement

- [x] T009 [P0] 强化 `workers.review` 对 freshness/external-fact objective 的识别和 assignment 生成
- [x] T010 [P0] 收口 research / ops worker 的 tool_profile/default entitlement，保证 governed web/browser path 可用且可解释
- [x] T011 [P0] 确保 `worker.apply` / child task / child work 保留 `tool_profile / lineage / spawned_by / project/workspace` runtime truth

## Phase 5: Acceptance & Product Surface

- [x] T012 [P0] 新增 freshness query acceptance matrix，覆盖日期、天气、官网/最新文档、backend unavailable
- [x] T013 [P0] 补 backend/frontend 回归测试，证明 041 主链成立
- [x] T014 [P1] 在 control plane / workbench 增加 freshness query 相关 degraded reason 与 runtime truth 的最小可视化

## Phase 6: Verification & Docs

- [x] T015 [P0] 输出实现后的 verification report
- [x] T016 [P0] 回写 blueprint / feature split / release gate 文档
