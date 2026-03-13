# Verification Report: Feature 040 — M4 Guided Experience Integration Acceptance

**Feature ID**: `040`
**Date**: 2026-03-10
**Updated**: 2026-03-13
**Branch**: `codex/040-guided-experience-acceptance`
**Status**: Passed

## Verification Scope

- guided workbench 是否真实消费 canonical control-plane resources / actions
- `setup.review/apply -> Home readiness -> Work worker.review/apply -> Chat context status` 联合验收
- `Memory -> Operator -> Export/Recovery` 是否已从 guided 主入口串成单条路径
- M4 是否已经具备正式 release gate artifact

## Executed Checks

### Backend

```bash
cd octoagent && uv run --group dev pytest apps/gateway/tests/test_control_plane_api.py -q
```

结果：`40 passed`

覆盖：

- `setup.review / setup.apply / agent_profile.save / policy_profile.select / skills.selection.save`
- `session.export`
- `backup.create / restore.plan`
- `memory.export.inspect / memory.restore.verify`

### Frontend

```bash
cd octoagent/frontend && npm test -- --run src/App.test.tsx
```

结果：`15 passed`

覆盖：

- `SettingsCenter` 的 `setup.review -> setup.apply` 与 `skill_selection`
- `WorkbenchBoard` 的 `worker.review / worker.apply`
- `ChatWorkbench` 的 context refresh / status surface
- `MemoryCenter` 的 `operator.approval.resolve / backup.create / session.export / diagnostics.refresh`

### CLI / Provider

```bash
cd octoagent && uv run --group dev pytest \
  packages/provider/tests/test_onboard.py \
  packages/provider/tests/dx/test_project_commands.py \
  packages/provider/tests/dx/test_wizard_session.py \
  packages/provider/tests/test_init_wizard.py -q
```

结果：`32 passed`

### Frontend Build

```bash
cd octoagent/frontend && npm run build
```

结果：通过

## Gate Results

| Gate | 结论 | 证据 |
|---|---|---|
| `GATE-M4-GUIDED-WORKBENCH` | PASS | `App.test.tsx::设置页会先执行 setup.review，再通过 setup.apply 提交并按 resource_refs 回刷` + `test_control_plane_api.py::test_setup_apply_persists_config_policy_and_agent_profile` |
| `GATE-M4-SUPERVISOR-WORKFLOW` | PASS | `App.test.tsx::Work 页面会先展示 worker.review 方案，再批准 worker.apply` + Feature 039 verification |
| `GATE-M4-MEMORY-OPERATOR-RECOVERY` | PASS | `App.test.tsx::Memory 页面会串起 operator 动作和 export/recovery 入口` + `test_control_plane_api.py::test_backup_create_and_restore_plan_actions_refresh_diagnostics` |
| `GATE-M4-SETUP-CONVERGENCE` | PASS | Feature 036 verification report + provider CLI tests + `App.test.tsx` skills selection 保存回归 |
| `GATE-M4-CONTEXT-CONTINUITY` | PASS | Feature 033 verification report + `test_task_service_context_integration.py` + `test_f033_agent_context_continuity.py` |
| `GATE-M4-RELEASE-REPORT` | PASS | 本报告 + `contracts/m4-acceptance-matrix.md` + `docs/blueprint.md` / `docs/m4-feature-split.md` |

## Release Decision

**结论**：Feature 040 已完成，且截至 2026-03-13，039/041 的运行语义与 acceptance 也已完成收口，M4 当前升级波次可正式签收。

当前 release 口径应为：

- **040 feature**：已完成
- **M4 milestone**：已可签收

## Key Release Notes

- `Home` / `Settings` / `Work` / `Chat` / `Memory` 都直接消费 control-plane canonical resources/actions，而不是扩散一套 workbench 私有 backend。
- `skill_selection` 已进入统一 setup 主路径，CLI 与 Web 都复用 canonical `setup.review / setup.apply` 语义。
- `MemoryCenter` 已把 operator、backup、session export、recovery summary 收进同一条 guided 路径。
- M4 已具备正式 acceptance matrix 与 gate report，不再依赖口头同步 blocker 状态。
- 从 2026-03-13 起，040 的 release gate 不再替代 039 的 message-native A2A 与 041 的 Butler-owned freshness runtime 验收；而当前这两项也都已经完成。

## Residual Risks

- 035/036 仍可继续补更细的 detail drawer、bootstrap/operator UX 与独立 e2e，但这些都属于后续体验增强，不再阻塞 040 feature 通过。
