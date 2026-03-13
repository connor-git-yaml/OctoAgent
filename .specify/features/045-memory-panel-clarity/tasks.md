# Tasks: Feature 045 Memory Panel Clarity Refresh

**Input**: `.specify/features/045-memory-panel-clarity/`

## Phase 1: Story 制品

- [x] T001 [P0] 补齐 `.specify/features/045-memory-panel-clarity/spec.md`
- [x] T002 [P0] 补齐 `.specify/features/045-memory-panel-clarity/plan.md`
- [x] T003 [P0] 补齐 `.specify/features/045-memory-panel-clarity/tasks.md`，并统一 Feature 制品落位到 `.specify/features/045-memory-panel-clarity/`

## Phase 2: 页面重构

- [x] T004 [P0] 在 `octoagent/frontend/src/pages/MemoryCenter.tsx` 新增用户态状态推导与最小配置指引
- [x] T005 [P0] 清理 Memory 页中的内部术语、raw scope/backend 信息与调试式动作文案
- [x] T006 [P0] 调整 Memory 记录区与筛选区，使其保留能力但改用用户语言表达
- [x] T007 [P1] 在 `octoagent/frontend/src/pages/SettingsCenter.tsx` 增加 hash deep-link 滚动，承接 `Settings > Memory` 入口

## Phase 3: 样式与验证

- [x] T008 [P0] 复用现有 workbench 样式完成新的 Memory 状态/步骤布局表达
- [x] T009 [P0] 更新 `octoagent/frontend/src/App.test.tsx` 中与 Memory 页相关的断言
- [x] T010 [P0] 运行前端定向测试并记录验证结果
