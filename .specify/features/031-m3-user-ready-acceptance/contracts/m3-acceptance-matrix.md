# Contract: M3 Acceptance Matrix

**Feature**: `031-m3-user-ready-acceptance`
**Created**: 2026-03-08
**Traces to**: `FR-001` ~ `FR-012`

---

## 契约范围

本文定义 031 的单一验收事实源：

- M3 八个 release gates
- 对应的自动化场景与 supporting evidence
- 最低通过标准
- 剩余风险记录位置

031 的测试、migration rehearsal、verification report 和里程碑结论必须以本矩阵为准。

---

## 1. Gate -> Scenario 映射

| Gate | 场景 ID | 场景名称 | 主要 surface | 最低通过标准 | 主证据 | Supporting Evidence |
|---|---|---|---|---|---|---|
| `GATE-M3-FIRST-USE` | `SCN-031-001` | 首次使用 + dashboard | CLI + gateway + control plane | `config init -> snapshot -> first chat -> sessions` 成立 | `octoagent/tests/integration/test_f031_m3_acceptance.py::test_m3_first_use_dashboard_and_trust_boundary_acceptance` | `octoagent/apps/gateway/tests/e2e/test_control_plane_e2e.py` |
| `GATE-M3-PROJECT-ISOLATION` | `SCN-031-002` | project isolation | provider + control plane + automation | secret bindings、import scope、automation target 不串 project | `octoagent/tests/integration/test_f031_m3_acceptance.py::test_m3_project_isolation_secret_import_and_automation_acceptance` | `octoagent/packages/provider/tests/dx/test_secret_service.py` |
| `GATE-M3-TRUST-BOUNDARY` | `SCN-031-003` | front-door boundary | gateway | loopback 默认拒绝非本机；bearer / trusted_proxy 已有定向回归 | `octoagent/tests/integration/test_f031_m3_acceptance.py::test_m3_first_use_dashboard_and_trust_boundary_acceptance` | `octoagent/apps/gateway/tests/test_frontdoor_auth.py` |
| `GATE-M3-UPDATE-RESTORE` | `SCN-031-004` | update / restore drill | provider + ops API | backup / recovery / update preview/apply/restart/verify 均有自动化证据 | `octoagent/apps/gateway/tests/test_ops_api.py` | `octoagent/packages/provider/tests/test_update_service.py`、`.specify/features/024-installer-updater-doctor-migrate/verification/verification-report.md` |
| `GATE-M3-MEMORY-IMPORT` | `SCN-031-005` | import -> memory -> vault / MemU | import workbench + memory console | import run 产生 memory effects，且可被 memory/import resources 追溯 | `octoagent/tests/integration/test_f031_m3_acceptance.py::test_m3_project_isolation_secret_import_and_automation_acceptance` | `.specify/features/027-memory-console-vault-authorized-retrieval/verification/verification-report.md`、`.specify/features/029-wechat-import-workbench/verification/verification-report.md`、`octoagent/packages/provider/tests/test_import_workbench_service.py` |
| `GATE-M3-DELEGATION-AUTOMATION` | `SCN-031-006` | automation + delegation inheritance | control plane + delegation plane | automation job 与 work dispatch 继承正确 project/workspace | `octoagent/tests/integration/test_f031_m3_acceptance.py::test_m3_project_selection_syncs_delegation_work_context` | `octoagent/apps/gateway/tests/test_delegation_plane.py`、`octoagent/apps/gateway/tests/test_control_plane_api.py` |
| `GATE-M3-MIGRATION-OPENCLAW` | `SCN-031-007` | OpenClaw migration rehearsal | local snapshot + import path | 完成一次有 mapping / rollback / deferred items 的 rehearsal | `.specify/features/031-m3-user-ready-acceptance/verification/openclaw-migration-rehearsal.md` | `_references/openclaw-snapshot/`、`octoagent/packages/provider/tests/test_import_workbench_service.py::test_import_workbench_detects_weflow_jsonl_export` |
| `GATE-M3-RELEASE-REPORT` | `SCN-031-008` | release report | spec / docs | 形成 gates / evidence / risks / boundary 汇总报告 | `.specify/features/031-m3-user-ready-acceptance/verification/verification-report.md` | `docs/blueprint.md`、`docs/m3-feature-split.md` |

---

## 2. 通过规则

### Gate 级通过规则

每个 gate 通过必须满足：

1. 至少一条自动化主证据成立；
2. supporting evidence 已回填；
3. 若存在部署或迁移边界，必须在 verification report 中列为 remaining risk 或 deployment note。

### Feature 级通过规则

031 通过必须满足：

1. `SCN-031-001` ~ `SCN-031-008` 均回填完成；
2. 不超出 031 定义范围；
3. migration rehearsal 已生成；
4. release report 已明确写出“是否可对用户开放”。

---

## 3. 禁止行为

- 不得以“024-030 各自都过了”替代 031 的联合验收
- 不得省略 front-door boundary 和 migration rehearsal
- 不得为了通过矩阵而新增新的业务能力
- 不得隐藏 remaining risks
