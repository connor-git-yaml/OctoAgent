# Tasks: Feature 018 — A2A-Lite Envelope + A2AStateMapper

**Input**: `.specify/features/018-a2a-lite-envelope/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/*.md`
**Created**: 2026-03-07
**Status**: Ready

**Task Format**: `- [ ] T{三位数} [P0/P1] [P?] [USN?] 描述 → 文件路径`

---

## Phase 1: Setup

- [ ] T001 [P0] [B] 把 `octoagent-protocol` 注册到 workspace → `octoagent/pyproject.toml`
- [ ] T002 [P0] [P] 创建 `packages/protocol` 包骨架与 `pyproject.toml` → `octoagent/packages/protocol/`

## Phase 2: Foundational

- [ ] T003 [P0] [B] 实现 `A2AMessageType`、`A2ATaskState`、`A2ATraceContext`、`A2AMessage` → `octoagent/packages/protocol/src/octoagent/protocol/envelope.py`
- [ ] T004 [P0] [B] 实现六类 payload model 与 `DispatchEnvelope`/`WorkerResult`/`WorkerSession` 桥接 builder → `octoagent/packages/protocol/src/octoagent/protocol/payloads.py`
- [ ] T005 [P0] [B] 实现 `A2AStateMapper` 与 metadata helper → `octoagent/packages/protocol/src/octoagent/protocol/state_mapper.py`
- [ ] T006 [P0] [B] 实现 protocol-side artifact 视图与 `A2AArtifactMapper` → `octoagent/packages/protocol/src/octoagent/protocol/artifact_mapper.py`
- [ ] T007 [P0] [B] 实现 `DeliveryLedger` / `DeliveryAssessment` / `DeliveryDecision` → `octoagent/packages/protocol/src/octoagent/protocol/delivery.py`
- [ ] T008 [P0] [P] 实现 fixture builders / catalog → `octoagent/packages/protocol/src/octoagent/protocol/fixtures.py`
- [ ] T009 [P0] 导出公共 API → `octoagent/packages/protocol/src/octoagent/protocol/__init__.py`

## Phase 3: User Story 1 — 统一 A2A-Lite 消息

- [ ] T010 [P0] [US1] 编写 envelope 单元测试（字段、alias、wrap、forward、hop 校验） → `octoagent/packages/protocol/tests/test_envelope.py`
- [ ] T011 [P0] [US1] 编写 payload bridge 测试（DispatchEnvelope / WorkerResult / WorkerSession） → `octoagent/packages/protocol/tests/test_fixtures.py`

## Phase 4: User Story 2 — 状态与 Artifact 映射

- [ ] T012 [P0] [US2] 编写 `A2AStateMapper` 测试（双向映射、internal_status metadata） → `octoagent/packages/protocol/tests/test_state_mapper.py`
- [ ] T013 [P0] [US2] 编写 `A2AArtifactMapper` 测试（text/file/json/image part、metadata、storage_ref 补全） → `octoagent/packages/protocol/tests/test_artifact_mapper.py`

## Phase 5: User Story 3 — fixture 与协议守卫

- [ ] T014 [P0] [US3] 编写 `DeliveryLedger` 测试（duplicate/replay/version/hop） → `octoagent/packages/protocol/tests/test_delivery.py`
- [ ] T015 [P0] [US3] 编写 fixture catalog 测试（六类 fixture 全部可导入、可序列化） → `octoagent/packages/protocol/tests/test_fixtures.py`

## Phase 6: Verification

- [ ] T016 [P0] 执行 protocol 包测试与相关 core 回归 → `octoagent/packages/protocol/tests/`、`octoagent/packages/core/tests/`
- [ ] T017 [P0] 生成 verification report → `.specify/features/018-a2a-lite-envelope/verification/verification-report.md`
