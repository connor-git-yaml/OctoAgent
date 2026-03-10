# Tasks: Feature 039 Supervisor Worker Governance + Internal A2A Dispatch

## Phase 1: Supervisor Surface

- [x] T001 [P0] 收口 `general` worker profile 默认工具组，只保留 supervisor 所需工具面
- [x] T002 [P0] 新增 `workers.review` built-in tool，并产出带 `worker_type / target_kind / tool_profile` 的 worker proposal
- [x] T003 [P1] 调整 `work.inspect / subagents.list` 的 tool group 语义为 supervision

## Phase 2: Worker Governance

- [x] T004 [P0] 在 control plane 增加 `worker.review` action
- [x] T005 [P0] 在 control plane 增加 `worker.apply` action
- [x] T006 [P0] 让 worker apply 生成 child tasks 时透传 `tool_profile`
- [x] T007 [P0] 在 DelegationPlane / control plane projection 暴露 `requested_tool_profile`

## Phase 3: Internal A2A Dispatch

- [x] T008 [P0] 在 orchestrator live dispatch 上增加内部 A2A roundtrip
- [x] T009 [P0] 恢复 A2A roundtrip 后的 `runtime_context` 与 work lineage
- [x] T010 [P0] 让 `TaskRunner` 把 child task metadata 的 `tool_profile` 传给 orchestrator

## Phase 4: Verification

- [x] T011 [P0] 补 capability pack 的 worker review/tool surface regression
- [x] T012 [P0] 补 control plane 的 worker review/apply regression
- [x] T013 [P0] 补 orchestrator 的 A2A roundtrip regression
- [x] T014 [P0] 跑通 lint + pytest，并回写 verification report
