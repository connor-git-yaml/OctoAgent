# Feature 028 Tasks

## Phase 1: 规格与契约对齐

- [x] T001 建立 028 spec / research / data-model / contract 制品
- [x] T002 对齐 020 governance 约束、025-B project binding、026 control-plane hook、027 canonical resource 边界
- [x] T003 冻结 `MemUBackend` / `MemUBridge` 深度集成 contract

## Phase 2: Engine Contract & Failback Baseline

- [x] T004 新增 integration models：`MemoryBackendStatus`、`MemorySyncBatch`、`MemoryIngestBatch`、`MemoryEvidenceProjection`、`MemoryMaintenanceRun`
- [x] T005 扩展 `MemoryBackend` / `MemUBridge` 协议，覆盖 diagnostics / sync / ingest / derivation / evidence / maintenance
- [x] T006 为 `SqliteMemoryBackend` 提供 028 contract 的 fallback 实现
- [x] T007 在 `MemoryService` 中接入 backend 状态跟踪、fallback 与自动 failback
- [x] T008 将 backend diagnostics 兼容接入 027 memory resources
- [x] T009 将 memory subsystem diagnostics 接入 026 control-plane summary
- [x] T010 新增 backend contract / failback / control-plane integration 测试

## Phase 3: Sync / Replay / Diagnostics Hardening

- [x] T011 实现真实 `MemUBridge` transport（HTTP 或 local-process plugin bridge）
- [x] T012 完善 `sync_batch()` / replay backlog / retry-after / reconnect 语义
- [x] T013 为 diagnostics 增加 ingest / maintenance 最近执行状态与 project binding 展示

说明：
`T012` 已补齐本地持久化 sync backlog、`memory.sync.resume` replay、`memory.bridge.reconnect` 探活与 diagnostics 状态回写。

## Phase 4: Multimodal Ingest

- [x] T014 实现 `text | image | audio | document` ingest batch handoff
- [x] T015 将非文本输入统一收敛为 artifact refs + extractor/sidecar output
- [x] T016 为 ingest partial success / idempotency / fallback 补单测与集成测试

## Phase 5: Derived Layers

- [x] T017 实现 Category / relation / entity / ToM 派生层 projection
- [x] T018 实现 derived -> `WriteProposalDraft` 的治理接缝
- [x] T019 补齐 derived layer evidence chain 与授权边界测试

## Phase 6: Maintenance Execution Chain

- [x] T020 实现 `memory.flush`、`memory.reindex`、`memory.bridge.reconnect`、`memory.sync.resume`
- [x] T021 记录 `MemoryMaintenanceRun` 审计对象与输出 refs
- [x] T022 补齐 maintenance / conflict / degraded 场景测试

## Phase 7: Verification & 收口

- [x] T023 更新 verification 报告并补齐测试矩阵
- [x] T024 对齐 027 / 026 / 028 文档与集成契约
