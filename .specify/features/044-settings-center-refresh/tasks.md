# Tasks: Feature 044 Settings Center Refresh

**Input**: `.specify/features/044-settings-center-refresh/`

## Phase 1: Story 制品

- [x] T001 [P0] 补齐 `.specify/features/044-settings-center-refresh/spec.md`
- [x] T002 [P0] 补齐 `.specify/features/044-settings-center-refresh/plan.md`
- [x] T003 [P0] 补齐 `.specify/features/044-settings-center-refresh/tasks.md`，并统一 Feature 制品落位到 `.specify/features/044-settings-center-refresh/`

## Phase 2: 页面重构

- [ ] T004 [P0] 重构 `octoagent/frontend/src/pages/SettingsCenter.tsx` 的页面 IA，删除 Butler rail 与旧 hero 文案
- [ ] T005 [P0] 在 `SettingsCenter.tsx` 落地多 Provider 管理交互：新增、删除、启停、默认顺序
- [ ] T006 [P0] 将 alias provider 字段改为来自 Provider 列表的选择器
- [ ] T007 [P1] 压缩冗长说明文本，把 review / action 收口到统一“保存检查”区

## Phase 3: 样式与验证

- [ ] T008 [P0] 在 `octoagent/frontend/src/index.css` 补齐新的 Settings 页面样式，并移除对右侧 rail 的依赖
- [ ] T009 [P0] 更新 `octoagent/frontend/src/App.test.tsx` 中与 Settings 相关的断言
- [ ] T010 [P0] 运行前端定向测试与必要的本地页面检查，记录验证结果
