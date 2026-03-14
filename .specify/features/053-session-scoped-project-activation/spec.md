---
feature_id: "053"
title: "Session-Scoped Project Activation"
milestone: "M4"
status: "Implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md §M3 产品化约束；§M3 核心对象关系；Feature 025/033/041/051/052"
predecessor: "Feature 025（Project / Workspace）、Feature 041（Butler/Worker runtime）、Feature 051（session-native runtime）、Feature 052（trusted tooling surface）"
---

# Feature Specification: Session-Scoped Project Activation

**Feature Branch**: `codex/053-session-scoped-project-activation`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Implemented  
**Input**: 对齐 Agent Zero 的 `each chat/context has its own active project` 语义，把当前 OctoAgent 的 project 绑定从 `surface-selected` 推进为 `session-scoped snapshot`。重点补齐 `/new`、session focus、chat send 首条消息、task scope 与 Web 恢复链的接缝。

## Problem Statement

当前 OctoAgent 已具备正式 `Project / Workspace / AgentSession / SessionCenter` 骨架，但 project 激活仍主要依赖：

- `ControlPlaneState.selected_project_id / selected_workspace_id`
- `ProjectSelectorState(surface="web")`
- task `scope_id -> project binding` 的兜底解析

这会带来三个结构性问题：

1. **`/new` 只是新对话，不是新 session 的 project 快照**  
   当前 `session.new` 只写 `new_conversation_token`，不会冻结当前 `project_id/workspace_id`。

2. **会话恢复与 task scope 仍可能退回 surface 级 project**  
   新 chat task 的 `scope_id` 默认是 `chat:<channel>:<thread>`；如果没有显式 workspace 绑定，就会在后续解析中退回 default project 或当前 selector。

3. **切换会话不会自然带回该会话自己的 project 工作模式**  
   当前 `session.focus` 只更新 focused session/thread，不会同步当前 selected project/workspace。

这与 Agent Zero 的设计有本质差异。Agent Zero 把 active project 直接挂到当前 chat/context：

- `activate_project(context_id, name)` 把 project 绑定到当前 context
- `get_context_project_name(context)` 也从当前 context 取 active project
- 文档明确说明“Each chat can have its own active project”

因此当前 OctoAgent 更像“control-plane-first 的 project selector”，而不是“session-first 的 project runtime”。

## Product Goal

把当前 Web / Butler 会话的 project 绑定收口到 session 自身：

- `/new` 时冻结当前 `project_id/workspace_id`，形成新会话起点的 project snapshot
- 新对话第一条消息必须消费这个 snapshot，而不是重新读取 surface 当前选择态
- 会话续聊必须沿用该 task/session 绑定的 project/workspace
- `session.focus` 切换到旧会话时，UI 当前 project 也应同步切到该会话绑定的 project/workspace
- task scope、session projection、runtime context、Web 恢复链必须一致，不允许出现“会话看起来属于 A project，实际 runtime 又掉回 B project”的漂移

## Scope Alignment

### In Scope

- `session.new / session.focus / session.reset` 的 project/workspace snapshot
- `ControlPlaneState` 的 pending new conversation scope
- `ChatSendRequest` 扩展与 Web 前端透传
- chat 首条消息的 workspace-scoped `scope_id`
- `ProjectStore.resolve_workspace_for_scope()` 对 workspace-scoped scope 的解析
- `ChatWorkbench / useChatStream` 的新对话 project 快照与恢复链
- 对应后端、前端、单元测试与文档回写

### Out of Scope

- `.octoagent/` project envelope
- project variables / knowledge / instructions 文件系统收口
- CLI/Telegram 的完整 session-scoped project UX
- 重做 task 数据模型为显式 `project_id/workspace_id` 列

## User Stories & Testing

### User Story 1 - `/new` 后的新会话应绑定创建时的 project (Priority: P1)

作为 Web 用户，我希望点“开始新对话”后，这个新会话立即冻结当前 project/workspace。即使我在第一条消息发出前又切换了 surface 级 project，新会话仍然应该留在原本的 project 里。

**Independent Test**: 在 A project 下触发 `session.new`，拿到 token 后切到 B project，再发第一条消息，验证新 task/session 仍解析到 A project。

**Acceptance Scenarios**

1. **Given** 当前选中 project A，**When** 用户执行 `session.new`，**Then** 系统保存 `new_conversation_token + project A + workspace A`。
2. **Given** `session.new` 后用户又切换到了 project B，**When** 发出第一条消息，**Then** 新 task 的 scope/runtime 仍绑定到 project A。
3. **Given** 页面刷新后仍存在未消费的 `new_conversation_token`，**When** 用户继续发第一条消息，**Then** 系统仍能恢复该 token 对应的 project snapshot。

---

### User Story 2 - 旧会话聚焦时应恢复该会话自己的 project 工作模式 (Priority: P1)

作为管理多个 project 会话的用户，我希望聚焦一个旧 session 时，当前 project 选择也切回这个 session 的 project/workspace，而不是继续停留在另一个会话的 surface 选择上。

**Independent Test**: 创建两个属于不同 project 的会话，聚焦切换它们，验证 control-plane 的 selected project/workspace 跟随会话切换。

**Acceptance Scenarios**

1. **Given** session A 属于 project A，session B 属于 project B，**When** 聚焦 session A，**Then** current selected project/workspace 切到 A。
2. **Given** 当前已聚焦某个会话，**When** 再切换到另一个 project 的会话，**Then** project selector 与 session projection 不得出现不一致。

---

### User Story 3 - 会话续聊必须沿用原会话 project，而不是当前 surface 选择 (Priority: P1)

作为用户，我希望在已有 task/session 里继续发消息时，系统沿用这个会话的 project/workspace，而不是看我此刻 UI 上切到了哪个 project。

**Independent Test**: 先创建 project A 会话，再切到 project B，但继续在 A 会话里发消息；验证上下文解析、session replay、memory runtime 仍使用 A。

**Acceptance Scenarios**

1. **Given** task 已属于 project A，**When** 用户继续在该 task 上发消息，**Then** runtime context 与 AgentSession 仍解析到 project A。
2. **Given** 当前 surface selected project 是 B，**When** 继续 A 会话，**Then** 不得把最新 turn 误绑定到 B。

## Edge Cases

- 当 `session.new` token 已过期或丢失时，系统可安全回退到当前 selected project/workspace，但必须显式记录这是 fallback。
- 当 token 对应的 project/workspace 已不存在时，系统必须回退到可用 project/workspace，而不是写入悬空绑定。
- 当 task 是旧数据、没有 workspace-scoped `scope_id` 时，系统仍应兼容读取现有 `scope_id -> selector -> default project` 链路。
- 当会话属于 project A，而当前 selected workspace 属于 project B 时，`session.focus` 必须优先恢复会话自己的 workspace，避免出现跨 project workspace 组合。

## Functional Requirements

- **FR-001**: 系统 MUST 将 `session.new` 升级为“新会话起点 + project/workspace snapshot”操作，而不只是 token 切换。
- **FR-002**: `ControlPlaneState` 与 `SessionProjectionDocument` MUST 显式暴露待消费的新会话 `project_id/workspace_id`。
- **FR-003**: `session.focus` MUST 同步恢复该 session 的 `project_id/workspace_id` 到当前 control-plane selection。
- **FR-004**: `session.reset` MUST 在清空 continuity 的同时，准备一个绑定到原 session project/workspace 的新会话起点。
- **FR-005**: `ChatSendRequest` MUST 支持新会话 token 与 project/workspace snapshot 的显式透传。
- **FR-006**: 首条新对话消息 MUST 使用 workspace-scoped `scope_id` 落盘，使 task 对 project/workspace 的绑定脱离 surface selector，成为 durable task/session 属性。
- **FR-007**: `ProjectStore.resolve_workspace_for_scope()` MUST 支持从 workspace-scoped `scope_id` 直接解析 workspace。
- **FR-008**: 会话续聊 MUST 优先沿用 task/session 已绑定的 project/workspace，不得因为 surface project 变化而漂移。
- **FR-009**: Web `useChatStream / ChatWorkbench` MUST 正确消费和恢复新会话 token + project snapshot，包括页面刷新后的继续发送。
- **FR-010**: 本 Feature MUST 提供后端、前端与存储层测试，覆盖 `/new`、focus、reset、首条消息绑定、刷新恢复与旧会话续聊。

## Key Entities

- **SessionScopedProjectSnapshot**: 新会话起点冻结的 `project_id/workspace_id`。
- **PendingNewConversationScope**: 存在于 `ControlPlaneState` 中、等待首条消息消费的会话起点绑定。
- **WorkspaceScopedChatScope**: 形如 `workspace:<workspace_id>:chat:<channel>:<thread_id>` 的 task scope，用于把 task durable 地绑定到 workspace。
- **SessionProjectReactivation**: 通过 `session.focus` 把当前 selected project/workspace 恢复到目标会话绑定的过程。

## Success Criteria

- **SC-001**: `/new` 后即使切换 surface project，新会话第一条消息仍绑定到创建时的 project/workspace。
- **SC-002**: 聚焦不同 project 的旧会话时，current selected project/workspace 会跟随会话切换。
- **SC-003**: 新 chat task 的 `scope_id` 变为 workspace-scoped durable scope，后续 `resolve_workspace_for_scope()` 能直接解析到正确 workspace。
- **SC-004**: 页面刷新后，未消费的新会话 token 仍能恢复对应 project/workspace，并正确创建新会话。
- **SC-005**: 旧数据与旧 scope 语义不回归，既有 session/task 仍可正常恢复与导出。
