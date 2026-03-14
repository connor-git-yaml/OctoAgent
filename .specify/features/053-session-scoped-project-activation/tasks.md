# Tasks - Feature 053

## Phase 1 - 规格与状态模型

- [x] T001 [P0] 新建 053 `spec.md / plan.md / tasks.md`，明确对齐 Agent Zero 的 session/chat scoped project 语义
- [x] T002 [P0] 扩展 `ControlPlaneState / SessionProjectionDocument / 前端类型`，增加 pending new conversation `project_id/workspace_id`

## Phase 2 - Session Actions

- [x] T003 [P0] 改造 `session.new`，冻结当前 selected project/workspace 并返回新会话 project snapshot
- [x] T004 [P0] 改造 `session.focus`，聚焦旧会话时同步恢复 project/workspace 选择态
- [x] T005 [P1] 改造 `session.reset`，在准备新会话起点时继承原会话 project/workspace

## Phase 3 - Chat Scope Binding

- [x] T006 [P0] 扩展 `ChatSendRequest` 与 chat route，支持新会话 token + project/workspace snapshot 透传
- [x] T007 [P0] 在 `TaskService.create_task` 主链使用 workspace-scoped `scope_id`
- [x] T008 [P1] 扩展 `ProjectStore.resolve_workspace_for_scope()`，支持 workspace-scoped scope 解析

## Phase 4 - Web 恢复链

- [x] T009 [P0] 改造 `useChatStream`，管理 pending new conversation project snapshot，并在首条消息消费
- [x] T010 [P0] 改造 `ChatWorkbench`，把 session pending scope 与当前 project selector 传给聊天 hook
- [x] T011 [P1] 确保页面刷新后仍能恢复未消费的新会话 project snapshot

## Phase 5 - 验证与文档

- [x] T012 [P0] 补 control-plane、chat route、project store 后端测试
- [x] T013 [P0] 补 `useChatStream / ChatWorkbench` 前端测试
- [x] T014 [P1] 回写 `docs/blueprint.md` 与必要 feature split/README 说明
- [x] T015 [P0] 跑定向 `pytest`、`vitest`、`tsc -b`、`ruff check`、`git diff --check`
