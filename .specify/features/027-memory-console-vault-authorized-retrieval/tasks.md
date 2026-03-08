# Tasks: Feature 027 — Memory Console + Vault Authorized Retrieval

**Input**: `.specify/features/027-memory-console-vault-authorized-retrieval/`  
**Prerequisites**: `spec.md`, `plan.md`, `data-model.md`, `contracts/`  
**Created**: 2026-03-08  
**Status**: Implemented

**Tests**: memory 单元测试、gateway control-plane API/integration、frontend integration、关键 e2e 均为必选项。

## Phase 1: Docs / Design Lock

- [x] T001 回写 `.specify/features/027-memory-console-vault-authorized-retrieval/*` 制品，锁定 020/026/028 边界
- [x] T002 [P] 回读 `docs/blueprint.md`、`docs/m3-feature-split.md`、Feature 020/025-B/026 制品，确保 027 范围不漂移
- [x] T003 [P] 固化 `memory-console-api`、`vault-authorization-api`、`memory-export-restore`、`memory-permissions` contracts

---

## Phase 2: Foundational Models & Durable Schema (Blocking)

**Purpose**: 先建立 memory durable schema、权限/授权模型和 control-plane canonical models，后续 projection/API/UI 才有稳定基线

- [x] T004 在 `octoagent/packages/memory/src/octoagent/memory/enums.py` 新增 Vault 授权相关枚举
- [x] T005 在 `octoagent/packages/memory/src/octoagent/memory/models/common.py` 扩展 query/policy/permission 相关模型
- [x] T006 [P] 在 `octoagent/packages/memory/src/octoagent/memory/models/` 新增/扩展 VaultAccessRequest、VaultAccessGrant、VaultRetrievalAudit 等模型
- [x] T007 在 `octoagent/packages/memory/src/octoagent/memory/store/sqlite_init.py` 新增授权申请、grant、retrieval audit 表与索引
- [x] T008 在 `octoagent/packages/memory/src/octoagent/memory/store/memory_store.py` 新增 proposals 列表、subject history、多条件 search、vault auth CRUD/query 方法
- [x] T009 在 `octoagent/packages/core/src/octoagent/core/models/control_plane.py` 扩展 Memory canonical documents / actions / result codes 所需模型
- [x] T010 在 `octoagent/packages/core/src/octoagent/core/models/payloads.py` / `__init__.py` 导出 027 所需 audit payload / canonical model
- [x] T011 [P] 在 `octoagent/packages/memory/tests/` 与 `octoagent/packages/core/tests/` 新增 durable schema / model / store 基线测试

**Checkpoint**: 027 的 durable schema、query primitives、canonical models 已冻结

---

## Phase 3: Scope Bridge & Projection Services

**Goal**: 把 020 的 `scope_id` 解释成 025-B 的 project/workspace 语义，并生成 operator-facing projection  
**Independent Test**: 给定多个 `ProjectBinding(type=MEMORY_SCOPE|SCOPE|IMPORT_SCOPE)` 和 memory records，系统能输出正确的 project/workspace projection，并对 orphan scope 标记 degraded

### Tests for Projection Layer

- [x] T012 [P] 在 `octoagent/packages/provider/tests/dx/` 新增 scope bridge 测试
- [x] T013 [P] 在 `octoagent/packages/provider/tests/dx/` 新增 memory projection / subject history / orphan scope 测试

### Implementation for Projection Layer

- [x] T014 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_scope_bridge.py` 实现 `scope_id -> project/workspace` 桥接
- [x] T015 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 实现 Memory overview projection
- [x] T016 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 实现 `subject_key` history projection
- [x] T017 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 实现 proposal audit projection
- [x] T018 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 实现 Vault authorization document projection

**Checkpoint**: backend 已能独立产出 Memory canonical resources

---

## Phase 4: Vault Authorization & Retrieval Chain

**Goal**: 让 Vault default deny 具备正式申请/批准/检索闭环  
**Independent Test**: 未授权检索返回 `authorization_required`；批准后检索成功且落 request/grant/retrieval audit

### Tests for Vault Authorization

- [x] T019 [P] 在 `octoagent/packages/memory/tests/test_memory_service.py` 或新增测试文件中覆盖 Vault grant / expiry / scope mismatch
- [x] T020 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 中覆盖 `vault.access.request/resolve/retrieve` action 结果码与 HTTP 语义
- [x] T021 [P] 在 `octoagent/apps/gateway/tests/integration/` 或等价测试中覆盖 control-plane audit/event emission

### Implementation for Vault Authorization

- [x] T022 在 `octoagent/packages/memory/src/octoagent/memory/service.py` 或新 service 中实现 Vault 授权判断与 retrieval audit helper
- [x] T023 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 注册 `vault.access.request`
- [x] T024 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 注册 `vault.access.resolve`
- [x] T025 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 注册 `vault.retrieve`
- [x] T026 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 将 Vault 授权动作接入现有 operator/policy/audit 路径

**Checkpoint**: Vault 授权链与检索链可独立演示

---

## Phase 5: Proposal Audit & Memory Inspect / Verify

**Goal**: 让 operator 能解释“记忆为何成立”，并在恢复前先看风险  
**Independent Test**: proposal 审计能解释 accepted/rejected 流程；inspect/verify 能输出结构化阻断项但不执行 destructive restore

### Tests for Proposal / Inspect / Verify

- [x] T027 [P] 在 `octoagent/packages/provider/tests/dx/` 新增 proposal audit projection 测试
- [x] T028 [P] 在 `octoagent/packages/provider/tests/dx/` 新增 memory export inspect / restore verify adapter 测试
- [x] T029 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 覆盖 `memory.export.inspect` / `memory.restore.verify`

### Implementation for Proposal / Inspect / Verify

- [x] T030 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_console_service.py` 完成 WriteProposal audit document
- [x] T031 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_recovery_service.py` 实现 Memory export inspect
- [x] T032 在 `octoagent/packages/provider/src/octoagent/provider/dx/memory_recovery_service.py` 实现 Memory restore verify
- [x] T033 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 注册 `memory.export.inspect` 与 `memory.restore.verify`

**Checkpoint**: proposal audit 与 inspect/verify 能独立工作

---

## Phase 6: Gateway Control Plane Integration

**Goal**: 把 Memory 资源和动作正式接到现有 control plane  
**Independent Test**: `/api/control/snapshot` 包含 `memory`，per-resource routes 与 action registry 均发布 027 新资源/动作

### Tests for Control Plane Integration

- [x] T034 [P] 在 `octoagent/apps/gateway/tests/test_control_plane_api.py` 新增 snapshot/resources/memory 路由测试
- [x] T035 [P] 在 `octoagent/apps/gateway/tests/test_main.py` 或等价测试中覆盖 gateway 启动时 memory schema / service 初始化

### Implementation for Control Plane Integration

- [x] T036 在 `octoagent/apps/gateway/src/octoagent/gateway/main.py` 初始化 memory schema 与 027 相关 services
- [x] T037 在 `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py` 扩展 snapshot、per-resource producer 与 action registry
- [x] T038 在 `octoagent/apps/gateway/src/octoagent/gateway/routes/control_plane.py` 增加 `/api/control/resources/memory`、`/memory-subjects/{subject_key}`、`/memory-proposals`、`/vault-authorization`

**Checkpoint**: 027 backend canonical surface 完成

---

## Phase 7: Web Memory Console

**Goal**: 在现有 Control Plane 中交付正式 Memory/Vault 领域视图  
**Independent Test**: Web 可以浏览 Memory overview、subject history、proposal audit、vault authorization，并调用 inspect/verify / retrieve 等动作

### Tests for Frontend

- [x] T039 [P] 在 `octoagent/frontend/src/pages/ControlPlane.test.tsx` 新增 Memory section integration 测试
- [x] T040 [P] 在 `octoagent/frontend/src/api/` / `src/types/` 的测试中覆盖新资源与动作类型（通过 `ControlPlane.test.tsx` 的资源刷新与动作断言覆盖）

### Implementation for Frontend

- [x] T041 在 `octoagent/frontend/src/types/index.ts` 扩展 Memory/Vault canonical types
- [x] T042 在 `octoagent/frontend/src/api/client.ts` 扩展 Memory 资源与动作调用
- [x] T043 在 `octoagent/frontend/src/pages/ControlPlane.tsx` 新增 Memory section、filters、subject history、proposal audit、vault authorization panel
- [x] T044 在 `octoagent/frontend/src/index.css` 增补 Memory/Vault 视图样式与移动端布局（复用既有 Control Plane 样式栈，无需新增专用 CSS）

**Checkpoint**: Web Memory Console 可用

---

## Phase 8: Polish / Verification / Sync

- [x] T045 [P] 运行 `ruff` 与 memory/provider/gateway 定向 `pytest`
- [x] T046 [P] 运行 frontend `npm test` / `npm run build`
- [x] T047 运行关键 e2e：Memory overview + Vault 授权检索 + proposal audit + inspect/verify（以 gateway control-plane API + frontend integration 覆盖关键路径）
- [x] T048 用 `/review` 思维执行一次全面自查，优先修复权限泄漏、状态漂移、跨 project 串读问题
- [x] T049 更新 `verification/verification-report.md`
- [x] T050 如实现事实改变 M3 文档口径，回写 `docs/blueprint.md` 与 `docs/m3-feature-split.md`（本轮实现未改变 M3 拆分口径，无需额外回写）

## Dependencies & Execution Order

- Phase 2 是全部实现的阻塞前提
- Phase 3 依赖 Phase 2 的 durable schema 与 canonical models
- Phase 4 依赖 Phase 2 + 3
- Phase 5 依赖 Phase 2 + 3；可与 Phase 4 并行推进
- Phase 6 依赖 Phase 3/4/5 的 backend services 基本稳定
- Phase 7 依赖 Phase 6 的 API surface 稳定
- Phase 8 在所有目标故事完成后执行

## Parallel Opportunities

- T005/T006/T011 可并行
- T012/T013 可并行
- Phase 4 与 Phase 5 可在同一 backend 基线下并行
- T039/T040 可并行
- T045/T046 可并行
