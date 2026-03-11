# Tasks: Feature 036 Guided Setup Governance

**Input**: `.specify/features/036-guided-setup-governance/`  
**Prerequisites**: `spec.md`、`plan.md`、`checklists/requirements.md`、`contracts/*`  
**Created**: 2026-03-10  
**Status**: Implemented（release blocker 已关闭；未勾选项属于后续内部收敛与更完整 e2e）

**Task Format**: `- [ ] T{三位数} [P0/P1] [USN?] 描述 -> 文件路径`

---

## Phase 0: Contract Freeze & Failure Baseline

- [ ] T001 [P0] 冻结 `setup-governance / policy-profiles / skill-governance / setup.review / setup.apply` contract，并把禁止平行 backend 的边界写入 spec/contracts -> `.specify/features/036-guided-setup-governance/spec.md`、`.specify/features/036-guided-setup-governance/contracts/*`
- [ ] T002 [P0] 增加 failing backend tests，证明当前系统缺少统一 setup projection、policy select、agent profile save 和 skill readiness setup surface -> `octoagent/apps/gateway/tests/test_setup_governance.py`
- [ ] T003 [P0] 增加 failing CLI/integration tests，证明当前 `octo init / octo onboard` 仍然输出命令链，未共享 review/apply -> `octoagent/packages/provider/tests/test_onboarding_setup_flow.py`

## Phase 1: Canonical Backend Projections

- [x] T004 [P0] 新增 `SetupGovernanceDocument`、`PolicyProfilesDocument`、`SkillGovernanceDocument` 及对应类型定义 -> `octoagent/packages/core/src/octoagent/core/models/*`、`octoagent/frontend/src/types/index.ts`
- [x] T005 [P0] 在 `control_plane.py` 中实现 `/api/control/resources/setup-governance` 聚合投影 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T006 [P0] 在 `control_plane.py` 中实现 `/api/control/resources/policy-profiles` 与 `/api/control/resources/skill-governance` -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T007 [P0] 扩 `snapshot`，把 `setup_governance / policy_profiles / skill_governance` 纳入 035 workbench 首屏 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T008 [P0] 扩 `config_schema` 与 control-plane ui hints，把 `front_door`、Telegram `dm_policy/group_policy/group_allow_users`、risk/help text 纳入 canonical hints -> `octoagent/packages/provider/src/octoagent/provider/dx/config_schema.py`、`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

## Phase 2: Review / Apply / Governance Actions

- [x] T009 [P0] 实现 `setup.review` action，统一生成 provider/channel/profile/tool/skill 风险摘要与阻塞项 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T010 [P0] 实现 `setup.apply` action，协调 `config.apply`、agent profile 保存、policy select、skills selection 持久化 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T011 [P0] 实现 `agent_profile.save` 与 project 默认 profile 绑定，消除静默默认值只能 refresh 不能治理的问题 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`、相关 store/service
- [x] T012 [P0] 实现 `policy_profile.select` 与 effective policy projection -> `octoagent/packages/policy/src/octoagent/policy/*`、`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [x] T013 [P1] 实现 `skills.selection.save`，保存 built-in / workspace / MCP skills 默认启用范围与禁用列表 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`、相关 store
- [x] T014 [P0] 为 review/apply/event 增加 secret redaction 与回归测试 -> `octoagent/apps/gateway/tests/test_setup_governance.py`

## Phase 3: Wizard / CLI Convergence

- [ ] T015 [P0] 扩展 `WizardSessionService` 或等价 durable setup draft，覆盖 provider/channel/security/profile/policy/skills sections -> `octoagent/packages/provider/src/octoagent/provider/dx/wizard_session.py`
- [ ] T016 [P0] 重构 `OnboardingService`，从“命令 next actions”升级为消费 canonical setup 状态和 review summary -> `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`
- [x] T017 [P0] 重构 `octo init` 为 canonical setup CLI adapter -> `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`
- [x] T018 [P1] 补 CLI 输出文案和 step summary，使 Web/CLI 都使用同一术语：`Provider / Channel / Main Agent / 安全等级 / Tools & Skills` -> `octoagent/packages/provider/src/octoagent/provider/dx/*`

## Phase 4: 035 Workbench Integration

- [x] T019 [P0] 在 035 `SettingsCenter` 中接入 `setup-governance`、`policy-profiles`、`skill-governance` 三个 canonical resources -> `octoagent/frontend/src/pages/SettingsCenter.tsx`
- [x] T020 [P0] 补 `workbench/utils.ts` 和相关类型映射，使前端能正确处理 `providers.0.id` 等 richer schema path，并支持 `agent_profiles / owner_profile / setup_governance / policy_profiles / skill_governance` 资源刷新 -> `octoagent/frontend/src/workbench/utils.ts`、`octoagent/frontend/src/types/index.ts`
- [x] T021 [P0] 实现图形化 `Setup Review` 面板，展示 blocking reasons、warnings、risk level、missing requirements -> `octoagent/frontend/src/components/setup/*`、`octoagent/frontend/src/pages/SettingsCenter.tsx`
- [x] T022 [P0] 将保存流程切换到 `setup.review -> setup.apply`，并保留 `config.apply` 作为底层协调子步骤，而非直接由前端调用 -> `octoagent/frontend/src/api/client.ts`、`octoagent/frontend/src/pages/SettingsCenter.tsx`
- [x] T023 [P1] 在 035 `Home` 增加 setup readiness 卡片，直接复用 `setup-governance` summary -> `octoagent/frontend/src/pages/Home.tsx`

## Phase 5: Verification & Backlog Sync

- [x] T024 [P0] 补 backend contract tests，验证新 resources/actions 仍然是 canonical control-plane，不泄露 secrets -> `octoagent/apps/gateway/tests/test_control_plane_api.py`、`octoagent/apps/gateway/tests/test_setup_governance.py`
- [x] T025 [P0] 补 CLI integration tests，验证 `octo init / octo onboard` 与 Web setup 共用同一 review/apply 语义 -> `octoagent/packages/provider/tests/test_onboarding_setup_flow.py`
- [x] T026 [P0] 补 frontend integration tests，验证 settings/setup 不再直接拼生资源，不再直接调用 `config.apply` 作为顶层交互，并能正确处理数组字段路径与新资源刷新 -> `octoagent/frontend/src/pages/SettingsCenter.test.tsx`
- [ ] T027 [P0] 补一条 e2e：从空项目进入 setup -> review -> apply -> doctor/readiness 通过 -> 进入 035 工作台 -> `octoagent/apps/gateway/tests/e2e/test_setup_governance_e2e.py`
- [x] T028 [P1] 回写 `docs/m4-feature-split.md`、`docs/blueprint.md`、verification report，冻结 036 的边界和依赖 -> `docs/m4-feature-split.md`、`docs/blueprint.md`、`.specify/features/036-guided-setup-governance/verification/verification-report.md`

---

## Testing Matrix

| 维度 | 必须验证 | 失败即阻塞 |
|---|---|---|
| setup 投影 | `setup-governance` 能反映 provider/channel/agent/skills 真状态 | 是 |
| review/apply | `setup.review` / `setup.apply` 风险摘要与 resource_refs 正确 | 是 |
| secrets | documents/actions/events 不泄露 secret 实值 | 是 |
| policy preset | `谨慎/平衡/自主` 真正映射到 policy/tool/approval | 是 |
| skills readiness | 缺 secret / 缺 binary / MCP disabled 三类可解释 | 是 |
| CLI/Web 一致性 | `octo init` 与 Web setup 结果一致 | 是 |
| 035 集成 | `Settings/Home` 直接消费 036 canonical resources/actions | 是 |
