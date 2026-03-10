# Tasks: Runtime Control Context Hardening

**Input**: `.specify/features/037-runtime-context-hardening/`
**Prerequisites**: `spec.md`、`plan.md`、`research/*.md`

## Phase 1: Contract

- [x] T001 定义 `RuntimeControlContext` 并导出到 core model public surface
- [x] T002 新增 gateway runtime context 编解码 helper，兼容 string-only metadata

## Phase 2: Main Runtime

- [x] T003 在 `DelegationPlaneService.prepare_dispatch()` 冻结 runtime snapshot，并写入 `Work.metadata`
- [x] T004 在 `DispatchEnvelope` / `OrchestratorRequest` / `ExecutionRuntimeContext` / `WorkerRuntime` 之间传递 runtime snapshot
- [x] T005 让 `TaskService.process_task_with_llm()` 和 `AgentContextService` 消费 runtime snapshot
- [x] T006 用 `ContextResolveRequest/Result` 驱动主 resolver，并把 lineage 写入 request snapshot / context frame
- [x] T007 让 response writeback 优先使用 `context_frame_id` 对应的 session/project/workspace

## Phase 3: Verification

- [x] T008 补充 delegation runtime lineage 测试
- [x] T009 补充 selector drift 回归测试
- [x] T010 重跑 033/034 相关上下文与控制流回归
- [x] T011 回写 verification report
- [x] T012 收敛 control plane session authority，并补 `session.focus/export` 与 backup/export 精准过滤回归
