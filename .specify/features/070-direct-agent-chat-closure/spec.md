---
feature_id: "070"
title: "Direct Agent Chat Closure"
milestone: "M4"
status: "In Progress"
created: "2026-03-20"
updated: "2026-03-20"
predecessor: "Feature 055"
blueprint_ref: "docs/blueprint.md §Agent Runtime §Agent Sessions §Workbench"
---

# Feature Specification: Direct Agent Chat Closure

**Feature Branch**: `070-direct-agent-chat-closure`  
**Created**: 2026-03-20  
**Status**: In Progress  
**Input**: 用户反馈“最新代码下，与非主 Agent 建立直接会话后，新建 fin 和继续 finance 都会失败；Claude 多次排查仍未闭环”

## Problem Statement

当前 Web 端已经允许用户“直接和某个非主 Agent 建立会话”，但这条链路并未真正闭合，导致：

1. **direct session 的对象语义错误**  
   新建 `fin / finance / 研究员小 A` 直聊会话时，后端仍然创建 `BUTLER_MAIN` runtime/session，只是把 worker profile id 塞进 `agent_profile_id`，使“直聊某个 Agent”和“Butler 主会话”语义混淆。

2. **执行时模型别名丢失**  
   research/finance 这类 Agent profile 自身配置了 `model_alias=cheap`，但实际任务事件里 `MODEL_CALL_STARTED.model_alias` 仍然是 `main`，导致在当前 Codex/ChatGPT 环境中直接报：
   `The 'main' model is not supported when using Codex with a ChatGPT account.`

3. **用户会话列表混入内部 session**  
   `/api/control/resources/sessions` 会把 `worker_internal` 和内部 butler runtime session 一起投影成 `channel=web` 的用户会话，导致左侧列表出现多个重复 `fin/finance`，恢复和续聊时容易拿错 session。

4. **first message 绑定不稳定**  
   direct session 首条消息没有稳定带上外层 `session_id / thread_id`，造成建完 session 后第一次发消息仍可能落到错误 task/scope。

## User Scenarios & Testing

### User Story 1 - 新建非主 Agent 直接会话可正常回复 (Priority: P1)

用户从 `Agents` 或 `Chat` 中新建一个 `Research/Finance` 直聊会话，并发送第一条消息，系统应真正以该 Agent 的 direct session 身份执行，而不是退回 Butler 主会话或错误模型。

**Why this priority**: 这是 direct agent chat 存在的最核心价值；当前主路径直接失败。

**Independent Test**: 在全新创建的 `fin` 会话中发送一条简单消息，能够稳定生成任务并使用该 Agent profile 的模型别名完成回复。

**Acceptance Scenarios**:

1. **Given** 用户新建 `研究员小 A` 直聊会话，**When** 发送第一条消息，**Then** 后端创建的 runtime/session 应是 direct worker 语义，而不是 `BUTLER_MAIN`
2. **Given** 该 Agent profile 配置了 `model_alias=cheap`，**When** 任务开始执行，**Then** `MODEL_CALL_STARTED.model_alias` 必须为 `cheap` 而不是 `main`

---

### User Story 2 - 继续旧的非主 Agent 会话不会混入内部 session (Priority: P1)

用户在左侧列表继续一个旧的 `finance` 会话时，系统应恢复到正确的用户会话，而不是恢复到内部 worker session。

**Why this priority**: 当前用户看到多个重复 `fin/finance`，继续旧会话时容易命中错误 session，导致“怎么看都不对”的体验。

**Independent Test**: 在已有 direct finance 会话存在的情况下刷新页面，左侧列表只展示真正用户可继续的 direct session；点击后恢复到正确 task/thread。

**Acceptance Scenarios**:

1. **Given** 系统中同时存在用户 direct finance 会话和多个内部 worker session，**When** 拉取 `/api/control/resources/sessions`，**Then** 用户会话列表不得包含 `worker_internal`
2. **Given** 用户点击左侧已有 `finance` 会话，**When** 页面恢复当前会话，**Then** 恢复的必须是 direct session 对应的 projected session，而不是内部 runtime session

---

### User Story 3 - direct session 首条消息绑定稳定 (Priority: P2)

用户新建 direct session 后立刻发送首条消息，前后端应稳定沿用同一 `session_id / thread_id / scope`，避免出现“建了新会话但首条消息跑进别的 task”。

**Why this priority**: 这是 direct session 体验看起来随机失效的另一条关键接缝。

**Independent Test**: 创建空 direct session，首条消息后检查 task.thread_id、scope_id、`USER_MESSAGE.control_metadata` 是否与会话 seed 对齐。

**Acceptance Scenarios**:

1. **Given** 用户创建一个尚未产生 task 的 direct session，**When** 发送首条消息，**Then** task 必须使用创建时约定的 `thread_id` 和 `session_id`
2. **Given** 页面刷新后重新进入这个 direct session，**When** 再发送消息，**Then** 后续消息必须继续沿用该 session/task 主链

## Edge Cases

- direct session 对应的 worker profile 被删除或失效时，系统应给出明确失败提示，而不是 silently 回退到 `main`
- 旧数据里已经存在 `BUTLER_MAIN + worker-profile-id` 这种历史 direct session 时，前端恢复和投影应尽量兼容，不要直接把历史会话完全消失
- 一个用户 direct session 和同名内部 worker session 共存时，列表标题允许重复，但内部会话不得暴露到用户主路径

## Requirements

### Functional Requirements

- **FR-001**: 系统必须把“用户直接与某个非主 Agent 会话”建模成独立的 direct session 语义，而不是复用 `BUTLER_MAIN`
- **FR-002**: direct session 首条消息必须稳定携带并持久化 `session_id` 与 `thread_id`
- **FR-003**: direct session 执行时必须优先使用所选 agent/worker profile 的 `model_alias`
- **FR-004**: `/api/control/resources/sessions` 必须过滤 `worker_internal` 等内部 session，不得暴露到用户聊天列表
- **FR-005**: 前端恢复 direct session 时必须以 route session 为最高优先级，不得被内部 runtime session 或其他 web session 抢占
- **FR-006**: 历史 direct session 若仍采用旧的 `BUTLER_MAIN + worker-profile-id` 形态，系统必须尽量兼容恢复与显示，直到数据迁移完成

### Key Entities

- **Direct Agent Session**: 用户显式发起、由某个非主 Agent 直接处理的会话对象
- **Projected Session**: 提供给前端列表和恢复逻辑使用的用户可见会话投影，必须屏蔽内部 runtime session
- **Agent/Worker Profile**: 决定 direct session 身份、模型别名与能力面的 profile 对象
- **Thread Seed**: direct session 创建时生成的稳定 `thread_id/session_id` 锚点

## Success Criteria

### Measurable Outcomes

- **SC-001**: 新建 `fin/finance` 直聊会话后，首条消息成功回复率达到 100%，且不再触发 `main model not supported` 错误
- **SC-002**: `/api/control/resources/sessions` 中用户可见会话不再包含 `worker_internal` 类型记录
- **SC-003**: direct session 首条消息对应的 `USER_MESSAGE.control_metadata` 必须稳定包含 `session_id` 与 `thread_id`
- **SC-004**: 用户侧左栏的 `fin/finance` 列表不再出现由内部 runtime session 泄漏导致的重复项
