---
feature_id: "053"
title: "Session-Scoped Project Activation"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 053

## 1. 目标

把 Project 激活从 `surface-selected` 推进到 `session-scoped snapshot`：

- `session.new` 冻结当前 project/workspace
- 首条消息消费这个 snapshot 创建新 task/session
- `session.focus` 恢复该 session 的 project/workspace
- 会话续聊沿用原会话的 project/workspace

## 2. 对标结论

### 2.1 对 Agent Zero

- Agent Zero 的 active project 挂在 chat/context 上，而不是全局 surface 选择
- 我们借鉴：`each chat/session has its own active project`
- 我们保留：更强的 Project/Workspace typed bindings、audit、session/export/reset 主链

### 2.2 对当前 OctoAgent

- 优点：Project/Workspace、AgentSession、SessionCenter 都已经具备正式模型
- 差距：`/new` 只是 token；focus 不恢复 project；chat 首条消息 project 绑定不 durable
- 收口原则：优先复用现有 `RuntimeControlContext + AgentSession + ControlPlaneState`，避免平行状态机

## 3. 设计原则

### 3.1 先冻结，再发送

`/new` 是会话边界，不是 UI 装饰。进入新会话时必须先冻结 project/workspace，再允许第一条消息消费。

### 3.2 Durable binding 优先于 surface selector

一旦 task/session 创建完成，project/workspace 应该从 task scope 或 session 自身恢复，而不是继续依赖 surface 当前选择。

### 3.3 旧链路兼容

旧 task 若没有 workspace-scoped scope，仍可通过旧 selector/default 规则解析；新语义优先覆盖新会话主链，不强制迁移旧数据。

## 4. 实施切片

### Slice A - 状态模型与 session actions

- 扩展 `ControlPlaneState` 与 `SessionProjectionDocument`
- `session.new / focus / reset` 读写 session-scoped project snapshot

### Slice B - chat send / runtime context / scope binding

- 扩展 `ChatSendRequest`
- 新会话首条消息透传 token + project/workspace
- 新 task 使用 workspace-scoped `scope_id`

### Slice C - 前端恢复链与 UX

- `useChatStream` 维护 pending new conversation scope
- `ChatWorkbench` 把 pending scope 与当前 project 传给 hook
- 页面刷新后仍能恢复未消费的新会话 snapshot

### Slice D - 文档与验证

- 回写 Feature 053、blueprint、必要 README/M4 split
- 后端、前端、存储层定向回归

## 5. 风险

- 如果 pending token 只存在前端本地，刷新后 project 快照会丢失
- 如果 focus 不同步 project selector，Session Center 与 Project Selector 会继续漂移
- 如果 task scope 仍沿用 `chat:web:<thread>`，续聊和列表过滤仍会掉回 default project

## 6. 验证方式

- control-plane API：`session.new / focus / reset`
- chat send route：新会话 project 快照消费与 scope 绑定
- project store：workspace-scoped scope 解析
- frontend：`useChatStream` 与 `ChatWorkbench` 恢复链

## 7. 本轮实施顺序

1. 先做 Slice A，保证 `/new / focus / reset` 都有 project snapshot
2. 再做 Slice B，把首条消息和 task scope 真正绑到 snapshot
3. 再做 Slice C，把 Web 恢复链补齐
4. 最后做 Slice D，跑定向验证并回写文档

## 8. 实施结果

- Slice A 已完成：`session.new / session.focus / session.reset` 现在都会读写 session-scoped project/workspace snapshot
- Slice B 已完成：新 chat task 会写入 `workspace:<workspace_id>:chat:<channel>:<thread_id>` durable scope，`TaskService.create_task()` 也会按 workspace snapshot 回填
- Slice C 已完成：`useChatStream / ChatWorkbench` 已支持 pending new conversation project snapshot，并能在页面刷新后继续消费
- Slice D 已完成：后端与前端定向回归、`tsc -b`、`ruff check`、`git diff --check` 全部通过
