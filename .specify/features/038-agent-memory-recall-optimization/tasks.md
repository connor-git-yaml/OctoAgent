# Tasks: Feature 038 Agent Memory Recall Optimization

## Phase 0 - 调研与范围冻结

- [x] T001 [P0] 深读 OpenClaw / Agent Zero / OpenClaw MemU 实际脚本，区分“可借鉴机制”和“不应照抄的实现”
- [x] T002 [P0] 识别本仓当前 memory 主链断点：console-only resolver、runtime recall 薄弱、chat import 裸建 memory service

## Phase 1 - Memory Recall Contract

- [x] T003 [P0] 定义 `MemoryRecallHit` / `MemoryRecallResult`
- [x] T004 [P0] 在 `MemoryService` 中实现 `recall_memory()`、query expansion、citation、preview、backend truth

## Phase 2 - Runtime Wiring

- [x] T005 [P0] 让 `AgentContextService` 使用 recall pack，并把 provenance 写入 `ContextFrame`
- [x] T006 [P0] 让 `TaskService` compaction flush 使用 project-scoped runtime memory service
- [x] T007 [P0] 让 `ChatImportService` indexing/write path 走 `MemoryRuntimeService`

## Phase 3 - Tool Surface

- [x] T008 [P0] 在 `CapabilityPackService` 增加 `memory.recall`
- [x] T009 [P1] 让 `memory.read / search / citations` 优先解析当前 runtime project/workspace

## Phase 4 - Verification

- [x] T010 [P0] 补充 `MemoryService` recall 单元测试
- [x] T011 [P0] 补充 `TaskService` recall/context 集成测试
- [x] T012 [P0] 补充 `CapabilityPack` 的 `memory.recall` 测试
- [x] T013 [P0] 补充 `ChatImportService` runtime resolver 测试
- [x] T014 [P0] 输出 verification report

## Deferred

- [x] T015 [P1] 引入 recall rerank / post-filter hooks
- [x] T016 [P1] 为 delayed recall 设计 durable event/artifact 承载，而不是进程内临时 extras
- [x] T017 [P2] 把 recall provenance 增量接进 Control Plane 的可视化资源
