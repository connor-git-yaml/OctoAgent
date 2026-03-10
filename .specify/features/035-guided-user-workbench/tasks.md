# Tasks: Feature 035 Guided User Workbench + Visual Config Center

**Input**: `.specify/features/035-guided-user-workbench/`
**Prerequisites**: `spec.md`、`plan.md`、`checklists/requirements.md`、`contracts/*`
**Created**: 2026-03-09
**Status**: In Progress

**Task Format**: `- [ ] T{三位数} [P0/P1] [USN?] 描述 -> 文件路径`

---

## Phase 0: Contract Freeze & Test Baseline

- [ ] T001 [P0] 冻结 route map、页面矩阵、canonical API 边界，并把 034 编号冲突说明写入 spec/contracts -> `.specify/features/035-guided-user-workbench/spec.md`、`.specify/features/035-guided-user-workbench/contracts/`
- [ ] T002 [P0] 新增 frontend contract tests，断言首页和设置页只消费 canonical control-plane resources/actions，而不是私有 API -> `octoagent/frontend/src/api/client.test.ts`、新增 workbench contract tests
- [ ] T003 [P0] 新增 failing integration tests，证明当前 `/` 仍是 operator console、聊天与 control-plane 仍割裂 -> `octoagent/frontend/src/App.test.tsx`、`octoagent/apps/gateway/tests/e2e/test_control_plane_e2e.py`

## Phase 1: Shell & Design System

- [x] T004 [P0] 建立新的 `WorkbenchShell`、一级路由与导航状态 -> `octoagent/frontend/src/App.tsx`、`octoagent/frontend/src/components/shell/*`
- [x] T005 [P0] 建立统一 design tokens、layout primitives、card/form/drawer/button 组件，逐步淘汰 root pages inline styles -> `octoagent/frontend/src/index.css`、`octoagent/frontend/src/components/*`
- [ ] T006 [P1] 实现桌面/移动端响应式 shell、顶部状态条与全局“待你确认”入口 -> `octoagent/frontend/src/components/shell/*`

## Phase 2: Guided Home & Settings

- [x] T007 [P0] 实现 `Home` 页面，基于 `snapshot` 组合 readiness、next actions、project、diagnostics、operator summary -> `octoagent/frontend/src/pages/Home.tsx`
- [x] T008 [P0] 实现图形化 `SettingsCenter`，按主 Agent / Work / Memory / Channels / Projects 分组消费 `ConfigSchemaDocument` -> `octoagent/frontend/src/pages/SettingsCenter.tsx`
- [ ] T009 [P0] 为设置页补字段分组、风险提示和后端 `ui_hints` 缺口 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [ ] T010 [P0] 让设置保存、project 切换、wizard 刷新/恢复全部走 action registry，并补必要 action 缺口 -> `octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`、`octoagent/frontend/src/api/client.ts`
- [ ] T011 [P1] 在首页和设置页实现 channel readiness / pairing / degraded reason 的用户化表达 -> `octoagent/frontend/src/pages/Home.tsx`、`octoagent/frontend/src/pages/SettingsCenter.tsx`

## Phase 3: Chat Workbench

- [x] T012 [P0] 用真实 `chat.send + SSE + task detail` 链路实现 `ChatWorkbench` 主界面 -> `octoagent/frontend/src/pages/ChatWorkbench.tsx`、`octoagent/frontend/src/hooks/useChatStream.ts`
- [ ] T013 [P0] 接入 `execution`、`sessions`、`delegation`，在右侧抽屉展示当前 task/work/runtime 状态 -> `octoagent/frontend/src/pages/ChatWorkbench.tsx`
- [ ] T014 [P0] 在聊天工作台接入 approval / execution input / interrupt-resume / export / focus 等真实动作 -> `octoagent/frontend/src/pages/ChatWorkbench.tsx`、`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`
- [ ] T015 [P1] 消费 Feature 033 的 profile/bootstrap/context provenance canonical resource，在聊天侧展示主 Agent 上下文来源；033 未就绪时显式 degraded -> `octoagent/frontend/src/pages/ChatWorkbench.tsx`、`octoagent/frontend/src/types/index.ts`
- [ ] T016 [P1] 消费 Feature 034 的 compaction status / evidence refs，并以用户化语言显示上下文压缩状态 -> `octoagent/frontend/src/pages/ChatWorkbench.tsx`、`octoagent/apps/gateway/src/octoagent/gateway/services/control_plane.py`

## Phase 4: Work & Memory Centers

- [x] T017 [P0] 实现 `WorkbenchBoard`，把 session/work 组织成可扫读的状态板和基础细节卡 -> `octoagent/frontend/src/pages/WorkbenchBoard.tsx`
- [x] T018 [P0] 把 `work.cancel / retry / split / merge / escalate` 与 task/execution detail 统一收编进 Work 页面 -> `octoagent/frontend/src/pages/WorkbenchBoard.tsx`
- [x] T019 [P0] 实现 `MemoryCenter` 首页，以 `MemoryConsoleDocument` 为事实源展示摘要、主题和风险状态 -> `octoagent/frontend/src/pages/MemoryCenter.tsx`
- [ ] T020 [P1] 增量接入 subject history / proposal audit / vault authorization，保持渐进展开 -> `octoagent/frontend/src/pages/MemoryCenter.tsx`

## Phase 5: Advanced Mode & Backward Compatibility

- [x] T021 [P0] 把现有 `ControlPlane` 收编为 `Advanced` 模式，保留深链接与完整能力 -> `octoagent/frontend/src/pages/AdvancedControlPlane.tsx`、`octoagent/frontend/src/pages/ControlPlane.tsx`
- [ ] T022 [P1] 处理旧 `/tasks/:taskId`、首页书签和 legacy links 的兼容跳转 -> `octoagent/frontend/src/App.tsx`

## Phase 6: Verification & Docs

- [ ] T023 [P0] 补 frontend integration tests：首页、设置、聊天、work、memory、advanced 六条主路径 -> `octoagent/frontend/src/pages/*.test.tsx`
- [ ] T024 [P0] 补 backend/control-plane regression tests，验证新增 hints / action / resource 没有破坏 canonical contract -> `octoagent/apps/gateway/tests/test_control_plane_api.py`
- [ ] T025 [P0] 增加一条 e2e：无需终端完成首页检查、图形化保存配置、发首条消息、处理 approval、查看 memory 摘要 -> `octoagent/apps/gateway/tests/e2e/test_control_plane_e2e.py`
- [ ] T026 [P1] 更新 `docs/m4-feature-split.md`、`docs/blueprint.md`、verification report，回写 035 的产品边界与依赖 -> `docs/m4-feature-split.md`、`docs/blueprint.md`、`.specify/features/035-guided-user-workbench/verification/verification-report.md`

---

## Testing Matrix

| 维度 | 必须验证 | 失败即阻塞 |
|---|---|---|
| 首页引导 | ready / action_required / degraded 三态可解释 | 是 |
| 图形化配置 | settings -> config.apply -> snapshot refresh 真链路 | 是 |
| 聊天工作台 | send -> SSE -> task/execution/context 真链路 | 是 |
| 待确认 | approval / pairing / execution input 可在新壳内处理 | 是 |
| Work 板 | child work / retry / merge / escalate 真动作 | 是 |
| Memory | summary -> history / proposal / vault 渐进展开 | 是 |
| Advanced | 旧控制面完整可达 | 是 |
| 033/034 接口 | 缺失时显式 degraded，存在时正确消费 | 是 |
