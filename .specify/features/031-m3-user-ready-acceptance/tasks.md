# Tasks: Feature 031 — M3 User-Ready E2E Acceptance

**Input**: `.specify/features/031-m3-user-ready-acceptance/`
**Prerequisites**: `spec.md`, `plan.md`, `contracts/m3-acceptance-matrix.md`
**Created**: 2026-03-08
**Status**: Completed

**Task Format**: `- [ ] T{三位数} [P0/P1] [USN?] 描述 -> 文件路径`

---

## Phase 1: Setup（冻结 release contract）

- [x] T001 [P0] 完成 031 的 speckit 制品：`plan.md`、`tasks.md`、`contracts/m3-acceptance-matrix.md` -> `.specify/features/031-m3-user-ready-acceptance/`
- [x] T002 [P0] 把 031 的八个 release gates 映射到 acceptance matrix，形成单一事实源 -> `.specify/features/031-m3-user-ready-acceptance/contracts/m3-acceptance-matrix.md`

## Phase 2: Foundational（收口关键接缝）

- [x] T003 [P0] 修补 control plane `project.select` 与 `selector-web` 的同步关系，确保 delegation / capability pack 继承同一 project 上下文 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T004 [P0] 补齐 WeFlow `.jsonl` 微信导出支持，作为 OpenClaw 迁移 rehearsal 的正式输入格式 -> `octoagent/packages/memory/src/octoagent/memory/imports/source_adapters/wechat.py`
- [x] T005 [P0] 为 `.jsonl` 导入能力补齐 import workbench 回归 -> `octoagent/packages/provider/tests/test_import_workbench_service.py`

## Phase 3: Acceptance Harness（新增联合验收）

- [x] T006 [P0] 新增 first-use + dashboard + front-door boundary 联合验收 -> `octoagent/tests/integration/test_f031_m3_acceptance.py`
- [x] T007 [P0] 新增 project isolation + secrets + import + memory + automation 联合验收 -> `octoagent/tests/integration/test_f031_m3_acceptance.py`
- [x] T008 [P0] 新增 project selection -> delegation work inheritance 联合验收 -> `octoagent/tests/integration/test_f031_m3_acceptance.py`

## Phase 4: Verification & Reporting（发布证据）

- [x] T009 [P1] 产出 OpenClaw -> OctoAgent migration rehearsal record -> `.specify/features/031-m3-user-ready-acceptance/verification/openclaw-migration-rehearsal.md`
- [x] T010 [P1] 产出 031 verification report，逐项回填 gate 结论、命令、证据与剩余风险 -> `.specify/features/031-m3-user-ready-acceptance/verification/verification-report.md`
- [x] T011 [P0] 执行 backend/frontend 定向验证并记录结果 -> `octoagent/tests/integration/`、`octoagent/apps/gateway/tests/`、`octoagent/packages/provider/tests/`、`octoagent/frontend/`
- [x] T012 [P1] 回写 `docs/blueprint.md` 与 `docs/m3-feature-split.md` 的里程碑状态 -> `docs/blueprint.md`、`docs/m3-feature-split.md`

## Deferred / Boundary Tasks

- [ ] T013 [P1] [SKIP] 真实生产 OpenClaw live cutover -> 后续 owner 执行
  **SKIP 原因**: 031 只要求 rehearsal 与 release boundary，不直接迁移 live secrets / live jobs

- [ ] T014 [P1] [SKIP] 新增公网 IAM / multi-user auth system -> M4
  **SKIP 原因**: 031 只明确 front-door boundary，不扩展出新的 multi-user 安全域
