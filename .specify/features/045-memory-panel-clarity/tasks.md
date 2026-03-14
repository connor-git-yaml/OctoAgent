# Tasks: Feature 045 Memory Panel Clarity Refresh

**Input**: `.specify/features/045-memory-panel-clarity/`

## Phase 1: Story 制品

- [x] T001 [P0] 补齐 `.specify/features/045-memory-panel-clarity/spec.md`
- [x] T002 [P0] 补齐 `.specify/features/045-memory-panel-clarity/plan.md`
- [x] T003 [P0] 补齐 `.specify/features/045-memory-panel-clarity/tasks.md`，并统一 Feature 制品落位到 `.specify/features/045-memory-panel-clarity/`

## Phase 2: 页面重构

- [x] T004 [P0] 在 `octoagent/frontend/src/domains/memory/MemoryPage.tsx` 新增用户态状态推导与最小配置指引
- [x] T005 [P0] 清理 Memory 页中的内部术语、raw scope/backend 信息与调试式动作文案，并通过 `MemoryDisplayRecord` 过滤内部技术写回
- [x] T006 [P0] 调整 Memory 记录区与筛选区，使其保留能力但改用用户语言表达，补齐派生信息的可读展示
- [x] T007 [P1] 在 `octoagent/frontend/src/domains/settings/SettingsPage.tsx` 增加 hash deep-link 滚动与 transport-aware Memory 配置入口，承接 `Settings > Memory`
- [x] T008 [P0] 在 `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py` 增加 `octo config memory show/local/memu-command/memu-http`，与 Web Settings 对齐

## Phase 3: 样式与验证

- [x] T009 [P0] 复用现有 workbench 样式完成新的 Memory 状态/步骤布局表达，并增加 Memory CLI snippet 卡片
- [x] T010 [P0] 更新 `octoagent/frontend/src/domains/memory/MemoryPage.test.tsx`、`octoagent/frontend/src/domains/settings/SettingsPage.test.tsx` 与相关 provider/gateway 测试断言
- [x] T011 [P0] 运行前端定向测试、Python 定向测试与 CLI 配置测试，并记录验证结果
