---
feature_id: "071"
title: "Session Owner / Execution Target Separation"
milestone: "M4"
status: "Implemented"
created: "2026-03-20"
updated: "2026-03-20"
predecessor: "Feature 064, Feature 070"
blueprint_ref: "docs/blueprint.md §Agent Runtime §Agent Sessions §Delegation Plane"
---

# Feature Specification: Session Owner / Execution Target Separation

**Feature Branch**: `feat/071-session-owner-execution-separation`  
**Created**: 2026-03-20  
**Status**: Implemented  
**Input**: 用户要求把“选定先和谁说话”和“本轮交给谁执行”彻底拆开，并让主 Agent / worker / subagent 的动作边界清晰可解释

## Problem Statement

当前主链已经同时支持：

- 默认主 Agent 会话
- 非主 Agent 直接会话
- Butler direct execution
- Worker / Subagent / Graph delegation

但这些能力的语义边界还没有被系统清晰建模，导致以下坏味道：

1. **用户选定 Profile 被错误当成执行路由**  
   用户选择 `Profile + Project` 的真实意图是“第一句先和谁说话”，但当前实现会直接把 `agent_profile_id` 写成 `requested_worker_profile_id`，把“会话 owner”提升成“本轮执行 target”。

2. **继承上下文会把普通主会话污染成显式 worker 会话**  
   `DelegationPlane` 会把 `inherited_agent_profile_id` 自动提升成 `requested_worker_profile_id`，导致同一条 task 一旦沾上某个 worker profile，后续就越来越像“显式 worker route”，Butler direct execution 被系统性绕开。

3. **主 Agent / worker / subagent 的动作图没有被正式写死**  
   当前 runtime 更多依赖 metadata flag、字符串 kind 和兼容分支，而不是一等语义对象，导致职责边界模糊：
   - 主 Agent 可以自己答，也可以委派给 worker，也可以 spawn subagent
   - worker 应该只能自己答或 spawn subagent
   - 这些规则当前没有被硬约束

4. **Feature 064 与 Feature 070 的目标互相打架**  
   064 需要 Butler direct execution 降低简单问题延迟；  
   070 需要 direct non-main agent session 成为一等对象。  
   当前由于 `session owner / inherited profile / requested_worker_profile_id` 混线，导致两者无法稳定共存。

## Target Semantics

本 Feature 要把以下 4 个概念拆开，并形成正式对象语义：

1. **Session Owner**
   - 表达：当前这条用户会话默认在和谁对话
   - 来源：用户显式选择 `Profile + Project`，或系统默认 root agent
   - 含义：UI / 恢复 / 历史 / 会话归属
   - **不是执行 target**

2. **Turn Executor**
   - 表达：这一轮当前是谁在执行
   - 取值：
     - `self`
     - `worker`
     - `subagent`
   - 含义：运行时阶段、轨迹展示、审批入口、任务归因

3. **Delegation Target**
   - 表达：当且仅当当前 Agent 决定委派时，真正交给谁
   - 来源：Butler / worker 的明确决策
   - **不得从 session owner 或 inherited profile 自动推导**

4. **Inherited Context Owner**
   - 表达：上下文来自哪个 agent/session/profile
   - 用途：记忆、会话 continuity、行为装配、project_path_manifest
   - **不得直接当作执行 target**

## User Scenarios & Testing

### User Story 1 - 用户选择先和谁说话，但不会自动触发委派 (Priority: P1)

用户创建一个 `Profile + Project` 对话时，系统应把该 profile 视为这条会话的默认 owner，而不是把这次消息直接路由成 worker/A2A。

**Why this priority**: 这是当前最核心的语义混乱点，也是 064 与 070 互相冲突的根源。

**Independent Test**: 分别创建 `默认主 Agent 会话` 与 `finance 直聊会话`，检查第一条消息执行时是否按 session owner 自己处理，而不是被自动包装成委派。

**Acceptance Scenarios**:

1. **Given** 用户新建一个默认主 Agent 会话，**When** 发送普通问题，**Then** 该轮应先走主 Agent 自处理判定，而不是因为 session owner 非空就落成 explicit worker route
2. **Given** 用户新建一个 `finance` 直聊会话，**When** 发送第一条消息，**Then** 该轮应由 `finance` session owner 自己处理，而不是先包装成 `worker_internal` A2A
3. **Given** 用户只是选择了 `Profile + Project`，**When** 该轮没有显式 delegation decision，**Then** 系统不得写入 `requested_worker_profile_id`

### User Story 2 - 主 Agent 与 worker 的动作边界清晰可验证 (Priority: P1)

系统应把主 Agent 和 worker 的可选动作写成正式约束，而不是继续靠 metadata flag 和兼容分支隐式协商。

**Why this priority**: 当前编排坏味道的核心是动作图没有被正式建模。

**Independent Test**: 验证同一问题在主 Agent 会话和 worker 直聊会话中，对应允许动作不同：
- 主 Agent：`self / delegate_to_worker / spawn_subagent`
- worker：`self / spawn_subagent`

**Acceptance Scenarios**:

1. **Given** 当前会话 owner 是主 Agent，**When** 面对有界简单问题，**Then** 可 self-handle，不必生成 work/A2A
2. **Given** 当前会话 owner 是主 Agent，**When** 面对需要专业化或权限隔离的问题，**Then** 可显式 delegate 到 worker
3. **Given** 当前会话 owner 是 worker，**When** 面对需要进一步拆分的复杂任务，**Then** 只能 spawn subagent，而不是转交另一个 worker
4. **Given** 当前会话 owner 是 worker，**When** 面对普通可解问题，**Then** 应由该 worker 自己处理，不应退回 Butler 主链

### User Story 3 - Butler direct execution 与 direct non-main session 可同时成立 (Priority: P1)

Feature 064 的直执行优势和 Feature 070 的 direct-agent chat 必须能共存。

**Why this priority**: 这是当前真实回归的核心。

**Independent Test**: 对同一套实例，验证：
- 默认主会话的简单问题仍可 Butler direct execute
- direct `finance` 会话可稳定由 finance owner 自处理
- 只有真正 delegation 时才出现 A2A / work / worker_internal

**Acceptance Scenarios**:

1. **Given** 默认主会话中的简单问题，**When** 系统判定为一般问题，**Then** 仍走 Butler direct execution
2. **Given** direct `finance` 会话中的普通问题，**When** finance owner 有能力自处理，**Then** 不得退回 Butler direct execution，也不得自动委派成 worker_internal
3. **Given** 任一会话中的显式 delegation，**When** work 被创建，**Then** `delegation_target_profile_id` 才应出现

### User Story 4 - UI 能解释“谁在说话”和“谁在执行” (Priority: P2)

用户必须能从 Web 界面明确知道：
- 这条会话默认在和谁说话
- 这一轮是谁在执行
- 如果发生 delegation，是谁把任务交给了谁

**Why this priority**: 否则 runtime truth 再强，用户也只能看到“又被路由了”的错觉。

**Independent Test**: Chat / Session 列表 / 轨迹界面能同时显示 `session owner` 与 `turn executor`，且文案不混用。

**Acceptance Scenarios**:

1. **Given** 当前会话 owner 是 `finance`，**When** finance 自己处理，**Then** UI 显示“当前正在与 finance 对话，由 finance 正在处理”
2. **Given** 当前会话 owner 是主 Agent，且主 Agent 委派给 research worker，**When** work 运行中，**Then** UI 应显示“主 Agent 对话中，本轮由 research worker 执行”

## Edge Cases

- 历史会话已经被写入 `agent_profile_id` 但并非显式 direct worker route时，迁移逻辑必须避免把旧主会话误升级成 worker 会话
- 用户从 direct worker 会话切回默认主 Agent 会话时，恢复逻辑不得继续沿用旧的 worker execution target
- 同一个 agent profile 可同时作为 session owner 与 delegation target 出现在不同 task 中，但两者的数据字段必须可区分
- subagent 结果注入父任务时，不得把 `spawned_by/subagent` 语义再回写成 `requested_worker_profile_id`

## Requirements

### Functional Requirements

- **FR-001**: 系统必须把 `session_owner_profile_id`、`turn_executor_kind`、`delegation_target_profile_id` 建模为语义上独立的概念
- **FR-002**: 选择 `Profile + Project` 只决定 `session_owner_profile_id`，不得自动写入 `requested_worker_profile_id`
- **FR-003**: `inherited_agent_profile_id` 只能作为上下文 continuity 线索，不能自动提升成 `delegation_target_profile_id`
- **FR-004**: 默认主会话中的 Butler direct execution eligibility 只能被“显式 delegation target”阻断，不能被 session owner/inherited profile 阻断
- **FR-005**: direct non-main agent session 必须作为一等 direct session 存在，并由该 owner 自己处理首轮消息
- **FR-006**: worker runtime 不得再向另一个 worker delegation；worker 只能 `self` 或 `spawn_subagent`
- **FR-007**: UI 和 control plane 必须能同时投影 `session owner` 与 `turn executor`
- **FR-008**: 历史会话与历史 events 的兼容迁移必须可恢复，不得因新语义引入会话消失或错误恢复

### Key Entities

- **Session Owner Profile**: 决定当前这条用户会话默认在和谁对话的 profile
- **Turn Executor Kind**: 当前这一轮的执行方式，枚举为 `self / worker / subagent`
- **Delegation Target Profile**: 当前 Agent 显式委派时的目标 profile
- **Execution Route**: `self / delegated_worker / spawned_subagent` 的结构化运行语义
- **Inherited Context Owner**: continuity 与上下文归属引用，不作为 delegation signal

## Success Criteria

### Measurable Outcomes

- **SC-001**: 默认主会话中的简单问题恢复为稳定的 Butler direct execution，不再因上下文里残留 worker profile 而退回 A2A
- **SC-002**: direct non-main agent session 的首条消息成功率达到 100%，且不再把 owner profile 自动提升成 requested worker profile
- **SC-003**: event / work / control plane 中可以清晰区分 `session owner`、`turn executor`、`delegation target`
- **SC-004**: Chat / Session UI 中，用户可明确分辨“正在和谁说话”和“这一轮是谁在执行”
