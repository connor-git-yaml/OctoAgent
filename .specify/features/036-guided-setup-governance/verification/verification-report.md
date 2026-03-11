# Verification Report: Feature 036 Guided Setup Governance

## 状态

- 阶段：主链实现完成，blocking gaps 已关闭
- 日期：2026-03-11

## 本次验证内容

1. `setup-governance / policy-profiles / skill-governance` 已作为 canonical control-plane resources 落地，并进入 `snapshot`。
2. `setup.review / setup.apply / agent_profile.save / policy_profile.select / skills.selection.save` 已全部落地，不新增平行 backend。
3. `skill_selection` 现已持久化到 `Project.metadata.skill_selection`，并反映到：
   - `skill_governance` 的 `selected / selection_source`
   - `setup.review` 的 tool/skill readiness 风险汇总
   - `capability_pack` 的 project-scoped skills / MCP 投影
4. 035 `SettingsCenter` 已把 skills 默认范围纳入 `setup.review -> setup.apply` 保存流。
5. CLI `octo init / octo project edit --apply-wizard / octo onboard` 已开始复用 canonical `setup.review / setup.apply` 语义，不再各自维护一套完全独立的 setup 口径。

## 已执行验证

### Backend

```bash
cd octoagent && uv run --group dev pytest apps/gateway/tests/test_control_plane_api.py -q
```

结果：`40 passed`

覆盖：

- `setup.apply` 持久化 config / policy / agent profile / skill selection
- `skills.selection.save` project metadata 持久化
- canonical resources/actions 不泄露 secret 实值

### CLI / Provider

```bash
cd octoagent && uv run --group dev pytest \
  packages/provider/tests/test_onboard.py \
  packages/provider/tests/dx/test_project_commands.py \
  packages/provider/tests/dx/test_wizard_session.py \
  packages/provider/tests/test_init_wizard.py -q
```

结果：`32 passed`

覆盖：

- `octo onboard --status-only` 输出 canonical setup review 摘要
- `octo project edit --apply-wizard` 先走 canonical `setup.review`
- CLI wizard draft 与 canonical setup adapter 之间的最小汇流

### Frontend

```bash
cd octoagent/frontend && npm test -- --run src/App.test.tsx
cd octoagent/frontend && npm run build
```

结果：

- `src/App.test.tsx`: `15 passed`
- build：通过

覆盖：

- `SettingsCenter` 保存时提交 `skill_selection`
- `setup.review -> setup.apply` 仍按 `resource_refs` 回刷
- workbench 类型与资源路由继续兼容

## Residual / Deferred

- `WizardSessionService` 与 `OnboardingService` 内部仍保留各自的 durable shell；当前已经通过 CLI adapter 复用 canonical review/apply，但未来仍可继续把内部状态机再进一步收口。
- 036 仍缺独立的端到端 setup e2e（当前以 control-plane / provider / frontend 三层定向回归代替）。

## 结论

- 036 的两个 release blocker 已关闭：`skills.selection.save` 已交付，CLI / Web setup 也已经汇流到 canonical `setup.review / setup.apply` 语义。
- 当前剩余项属于内部重构与更大范围 e2e 完整度，不再阻塞 040 或 M4 的整体验收。
