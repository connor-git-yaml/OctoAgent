# Contract: M4 Acceptance Matrix

**Feature**: `040-guided-experience-acceptance`  
**Created**: 2026-03-11  
**Updated**: 2026-03-11  
**Traces to**: `FR-001` ~ `FR-009` + Feature `033` / `036` close-out gates

---

## 契约范围

本文定义 M4 当前这轮“可用性 / 串联 / 安全性 / 三层结构”升级的联合验收事实源：

- guided workbench 是否真的消费 canonical control-plane contract；
- setup / supervisor-worker / memory-operator-recovery 三条主路径是否闭环；
- 033 与 036 关闭后，M4 如何完成最终签收。

040 的 verification、milestone 回写与 release 结论必须以本矩阵为准。
2026-03-13 起，需要补充一个重要约束：040 证明的是 guided experience、setup convergence 和 workbench orchestration surface 的可用性；它 **不自动等价于** 039 的 message-native A2A 主链和 041 的 Butler-owned freshness runtime 已完全闭合。

---

## 1. Gate -> Scenario 映射

| Gate | 场景 ID | 场景名称 | 主要 surface | 最低通过标准 | 主证据 | Supporting Evidence |
|---|---|---|---|---|---|---|
| `GATE-M4-GUIDED-WORKBENCH` | `SCN-040-001` | Home / Settings readiness 闭环 | guided workbench + control plane | `Home` 显示 readiness，`Settings` 走 `setup.review -> setup.apply`，且不引入私有 backend | `octoagent/frontend/src/App.test.tsx::设置页会先执行 setup.review，再通过 setup.apply 提交并按 resource_refs 回刷` | `octoagent/apps/gateway/tests/test_control_plane_api.py::test_setup_apply_persists_config_policy_and_agent_profile`、Feature 036 verification |
| `GATE-M4-SUPERVISOR-WORKFLOW` | `SCN-040-002` | supervisor -> worker review/apply | WorkbenchBoard + orchestration | 用户可在图形化工作台 review/apply worker plan，主 Agent 继续保持 supervisor-only，并且 freshness/runtime 相关事实链已由 039/041 闭合 | `octoagent/frontend/src/App.test.tsx::Work 页面会先展示 worker.review 方案，再批准 worker.apply` | Feature 039 / 041 verification |
| `GATE-M4-MEMORY-OPERATOR-RECOVERY` | `SCN-040-003` | memory -> operator -> export/recovery | MemoryCenter + diagnostics + sessions | `/memory` 使用 canonical `sessions / diagnostics / session.export / backup.create` 串联主路径 | `octoagent/frontend/src/App.test.tsx::Memory 页面会串起 operator 动作和 export/recovery 入口` | `octoagent/apps/gateway/tests/test_control_plane_api.py::test_backup_create_and_restore_plan_actions_refresh_diagnostics` |
| `GATE-M4-SETUP-CONVERGENCE` | `SCN-040-004` | setup governance 最终收口 | Web Settings + CLI onboarding | `skills.selection.save` 已交付，且 CLI / Web 共用同一 setup 状态机 | Feature 036 verification report | Feature 036 tasks / spec |
| `GATE-M4-CONTEXT-CONTINUITY` | `SCN-040-005` | main agent context continuity | ChatWorkbench + runtime + memory | 033 真实交付，或 release report 明确阻塞 M4 最终签收 | Feature 033 verification report | `octoagent/frontend/src/App.test.tsx::聊天发送后会回刷 sessions、delegation 和 context 摘要`、Feature 034 verification |
| `GATE-M4-RELEASE-REPORT` | `SCN-040-006` | M4 release report | spec / docs | 形成 gates / evidence / blockers / release decision 汇总报告 | `.specify/features/040-guided-experience-acceptance/verification/verification-report.md` | `docs/blueprint.md`、`docs/m4-feature-split.md` |

---

## 2. 通过规则

### Gate 级通过规则

每个 gate 通过必须满足：

1. 至少一条主证据成立；
2. supporting evidence 已回填；
3. 若 gate 仍有 blocker，必须在 verification report 中显式写出，不得用“部分完成”替代。

### Feature 级通过规则

040 feature 自身通过必须满足：

1. `SCN-040-001`、`SCN-040-002`、`SCN-040-003`、`SCN-040-006` 已回填；
2. 040 不新增平行 backend / 私有 REST / 新产品对象；
3. release report 明确写出 M4 是否可签收，以及被谁阻塞。
4. 040 的通过结论不得被解读为 039/041 的运行语义已经 fully closed。

### M4 最终签收规则

M4 最终签收必须额外满足：

1. `SCN-040-004` 已关闭；
2. `SCN-040-005` 已关闭；
3. 039 的 message-native Butler -> Worker A2A 主链与 041 的 Butler-owned freshness runtime acceptance 已全部闭合；
4. release report 已把总体结论更新为可签收，而不是 blocked / conditional。

---

## 3. 禁止行为

- 不得以“035/036/039 各自都做了”替代 040 的联合验收
- 不得继续把 legacy `/api/ops/*` 或 `/api/operator/*` 当成 guided 主路径事实源
- 若 033/036 重新退化，必须重新把 blocker 显式提升到 gate report
- 不得为了通过 gate 再新增一套 workbench 专用 backend
