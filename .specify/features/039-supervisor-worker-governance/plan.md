# Implementation Plan: Feature 039 Supervisor Worker Governance + Internal A2A Dispatch

## 目标

把“主 Agent / Work / Worker(Subagent/Graph)”三层关系从架构描述补成运行事实，同时不重做 030/037 已建立的主链。

## 设计决策

### 1. 主 Agent 先收权，不再补更多工具

- `general` worker profile 改为 `project / session / supervision`
- supervisor 通过 `workers.review` 提案，再由具体 `research / dev / ops` worker 干活
- 这样可以把“主 Agent 不直接持有具体干活工具”变成默认系统边界

### 2. review/apply 分两段

- `workers.review` / `worker.review`
  - 只产出建议
  - 不创建 child tasks
- `worker.apply`
  - 真正派发 child tasks / child works
  - 把 `tool_profile` 写入 metadata，进入 runtime truth

### 3. 不新建 A2A backend，先让 live dispatch 真经过 A2A 归一化

- 在 `OrchestratorService._dispatch_envelope()` 入口对现有 `DispatchEnvelope` 做一次内部 roundtrip
- 使用 Feature 018 已有 `build_task_message()` 和 `dispatch_envelope_from_task_message()`
- 结合 Feature 037 的 runtime context helper，恢复 `RuntimeControlContext`

## 实施阶段

### Phase 1: Supervisor Surface

- 收口 `general` worker profile
- 新增 `workers.review`
- 调整 `work.inspect` / `subagents.list` 到 `supervision` 语义

### Phase 2: Worker Governance

- control plane 增加 `worker.review` / `worker.apply`
- `work.split` 与 child launcher 补 `tool_profile`
- work projection 暴露 `requested_tool_profile`

### Phase 3: Internal A2A Dispatch

- orchestrator live dispatch 做 A2A roundtrip
- task runner 透传 child task `tool_profile`
- 保留 runtime lineage

### Phase 4: Verification

- capability pack regression
- control plane regression
- orchestrator regression

## Interface Mapping

- `CapabilityPackService.review_worker_plan()` -> `workers.review`
- `CapabilityPackService.apply_worker_plan()` -> `ControlPlaneService._handle_worker_apply()`
- `TaskRunner._run_job()` -> `OrchestratorService.dispatch(..., tool_profile=...)`
- `OrchestratorService._normalize_dispatch_via_a2a()` -> Feature 018 A2A contract
- `ControlPlane work runtime_summary.requested_tool_profile` -> Feature 035/Advanced UI 可见运行真相
