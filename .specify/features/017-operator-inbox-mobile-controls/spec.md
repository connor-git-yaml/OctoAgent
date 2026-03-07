---
feature_id: "017"
title: "Unified Operator Inbox + Mobile Task Controls"
milestone: "M2"
status: "Draft"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md M2 / docs/m2-feature-split.md Feature 017"
predecessor: "Feature 011（Watchdog + Task Journal）、Feature 016（Telegram Channel）、Feature 019（JobRunner Console）"
parallel_dependency: "Feature 021 / 023 可继续并行；017 不重写 approvals、watchdog、telegram ingress 基础链路"
---

# Feature Specification: Unified Operator Inbox + Mobile Task Controls

**Feature Branch**: `codex/feat-017-operator-inbox-mobile-controls`
**Created**: 2026-03-07
**Status**: Draft
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 017，交付统一 operator inbox、Web/Telegram 等价操作入口与 operator action 审计闭环。
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

OctoAgent 在 M2 已经分别交付了 approvals、watchdog / task journal、Telegram channel 和 JobRunner 基础链路，但 operator 体验仍然割裂：

1. pending approvals 只能在独立 approvals 面板看；
2. watchdog alerts 只能在 journal 查询里看；
3. cancel 有后端接口，但没有统一操作入口；
4. Telegram 只能发提醒文本，不能直接执行 approve / retry / cancel / acknowledge；
5. pending pairings 已经有状态源，但没有产品化的 operator surface。

结果是，操作者虽然“技术上有这些能力”，但在用户视角上仍然需要自己拼接多个页面、接口和通知，无法形成真正可日常使用的控制面。

Feature 017 要解决的是：

- 让 operator 在一个入口里看到“现在有哪些待处理动作”；
- 让 operator 在 Web 或 Telegram 上直接完成核心动作；
- 让所有动作都可回放、可追溯、可判断结果。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 011：Watchdog + Task Journal | 已交付 | 017 直接消费 journal / drift 语义，不重写检测逻辑 |
| Feature 016：Telegram Channel + Pairing | 已交付 | 017 消费 Telegram channel 与 `TelegramStateStore`，补 operator surface |
| Feature 019：JobRunner Console / cancel | 已交付 | 017 消费 cancel / execution 状态，不重做运行控制底层 |
| approvals / ApprovalManager | 已交付 | 017 直接复用 approval 状态机和事件链 |

前置约束：

- 017 不得重写 ApprovalManager、Watchdog 检测器或 Telegram ingress/routing 基础逻辑。
- 017 必须让 Web 与 Telegram 复用同一动作语义，避免双套 operator contract。
- 017 的 operator action 必须写入 Event Store；不得新建独立 action 日志源。

---

## User Scenarios & Testing

### User Story 1 - 统一查看所有待处理 operator 工作项 (Priority: P1)

作为日常操作者，我希望在一个统一 inbox 中看到 approvals、watchdog alerts、retryable failures 和 pending pairing requests，而不是切换多个页面和状态源，这样我可以快速判断现在最该处理什么。

**Why this priority**: 没有统一收件箱，M2 的多渠道与长任务治理能力在用户视角上仍然是碎片化的。

**Independent Test**: 构造同时存在 pending approval、drift alert、retryable failure 和 pending pairing 的环境，打开 Web inbox，验证所有工作项都能按统一结构展示，并显示 pending 数量、过期时间或最近动作结果。

**Acceptance Scenarios**:

1. **Given** 系统中同时存在审批、漂移告警和失败任务，**When** operator 打开 Web inbox，**Then** 系统在一个页面内展示这些工作项，而不是要求用户再跳到 approvals panel、journal 或 task detail。

2. **Given** 某个 Telegram 用户触发了 pending pairing，**When** operator 查看 inbox，**Then** pairing request 作为一类明确工作项出现，而不是只存在于状态文件中。

3. **Given** 多个工作项同时存在，**When** operator 查看 inbox，**Then** 系统显示 pending 数量、过期信息和最近动作结果，帮助用户先处理高优先级项。

---

### User Story 2 - 在 Web 或 Telegram 上完成等价操作 (Priority: P1)

作为操作者，我希望能在 Web 或 Telegram 上完成 approve / deny / retry / cancel / alert acknowledge 等核心动作，而不是收到通知后还要切回另一端，这样我能在桌面和移动场景下都真正控制系统。

**Why this priority**: 这是 M2 “渠道等价操作” 的核心要求。如果 Telegram 仍然只能提醒，017 就没有成立。

**Independent Test**: 分别从 Web 和 Telegram 对同一类工作项执行动作，验证后端动作结果一致，过期或已处理场景返回明确错误结果。

**Acceptance Scenarios**:

1. **Given** 某个审批处于 pending，**When** operator 在 Web 或 Telegram 上点击 approve / deny，**Then** 审批状态被更新，并且另一个渠道能看到一致的结果。

2. **Given** 某个 drift alert 仍处于待处理状态，**When** operator 在 Web 或 Telegram 上执行 acknowledge，**Then** 该告警从 pending 视图中移除或标记为已处理，并保留审计结果。

3. **Given** 某个任务可取消或可重试，**When** operator 在 Web 或 Telegram 上发起 cancel / retry，**Then** 系统返回结构化结果，说明动作成功、已处理、已过期或当前状态不允许该动作。

---

### User Story 3 - 看到最近动作结果并避免重复处理 (Priority: P1)

作为操作者，我希望在执行动作后立刻看到结果，并在多端协作时知道某项是否已经被处理，这样我不会重复点击，也不会因为竞态而误判系统状态。

**Why this priority**: 没有动作结果反馈和幂等结果，移动端与 Web 并发操作会迅速演变为混乱体验。

**Independent Test**: 模拟一个工作项先被 Web 端处理，再由 Telegram 端重复点击，验证 Telegram 返回 `already_handled` 或等价结果，并且最近动作结果对用户可见。

**Acceptance Scenarios**:

1. **Given** 某个审批已经在 Web 端被处理，**When** Telegram 端随后点击相同动作，**Then** 系统返回“已处理”结果，而不是重复执行。

2. **Given** 某个审批在用户操作前已经过期，**When** 用户点击 approve / deny，**Then** 系统明确返回过期结果，而不是静默失败。

3. **Given** operator 刚刚执行过一个动作，**When** 再次查看 inbox，**Then** 系统显示最近动作结果和来源渠道。

---

### User Story 4 - 所有 operator action 都可审计与回放 (Priority: P2)

作为 owner，我希望所有 operator action 都写入统一事件链，包含来源渠道、操作者和结果，这样后续排障、审计和回放都能准确还原“谁在什么时候做了什么”。

**Why this priority**: 如果控制面动作不可回放，M2 的“用户可控”和“可恢复”就缺了关键一环。

**Independent Test**: 对 approval、alert、retry/cancel 至少各执行一次动作，随后查询任务事件链，验证动作事件已落盘且包含来源、操作者、动作和结果。

**Acceptance Scenarios**:

1. **Given** operator 通过 Web 处理审批，**When** 查看事件链，**Then** 可以看到动作类型、操作者和结果。

2. **Given** operator 通过 Telegram 取消任务，**When** 查看事件链，**Then** 可以看到动作来源为 `telegram`，而不是模糊的系统操作。

3. **Given** 某项动作因为状态变化而未执行，**When** 查看事件链，**Then** 失败结果同样可见，而不是只有成功动作才被记录。

---

### Edge Cases

- 当 Web 和 Telegram 同时对同一项执行动作时，系统如何保证幂等并返回明确结果？
- 当 approval 已过期、alert 已被 ack、任务已终态时，用户点击动作如何反馈？
- 当 Telegram callback query 重放或重复到达时，系统如何避免重复执行？
- 当 pending pairing 存在，但 `telegram-state.json` 损坏或不可解析时，系统如何降级展示？
- 当某个数据源（ApprovalManager / Journal / Telegram state）短时不可用时，inbox 是否仍能部分可用并提示降级？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供统一 operator inbox，聚合至少四类工作项：approval、alert、retryable failure、pairing request。

- **FR-002**: 系统 MUST 以既有能力为数据源构建 query-time projection，而不是为 inbox 新建独立的事实源。

- **FR-003**: 系统 MUST 为每个 inbox item 提供统一的最小展示字段，至少包含：item 标识、类型、标题/摘要、关联对象、创建时间、pending/过期信息、建议动作或最近动作结果。

- **FR-004**: 系统 MUST 提供 Web operator inbox 视图，使 operator 能在单一页面查看和处理工作项。

- **FR-005**: 系统 MUST 提供 Telegram 等价操作入口，允许在 Telegram 消息上下文中直接执行核心动作，而不是只发送“请去 Web 端处理”的文本提醒。

- **FR-006**: 系统 MUST 让 Web 与 Telegram 复用同一套 operator action 语义与幂等规则。

- **FR-007**: 系统 MUST 支持以下核心动作的统一 contract：approve / deny、cancel、retry、alert acknowledge。

- **FR-008**: 系统 SHOULD 将 pending pairing request 作为一等 inbox item，并在同一收件箱中暴露统一处理入口。

- **FR-009**: 系统 MUST 在动作执行后返回结构化结果，至少区分：成功、已处理、已过期、状态不允许、目标不存在。

- **FR-010**: 系统 MUST 在 inbox 视图中展示最近动作结果，帮助 operator 判断某项是否刚被其他端处理。

- **FR-011**: 系统 MUST 记录 operator action 审计事件，至少包含：动作类型、来源渠道、操作者标识、目标对象、执行结果、时间戳。

- **FR-012**: 系统 MUST 将 task / approval 相关 operator action 写入既有任务事件链；若目标对象天然无 task_id，系统 MUST 提供可回放的统一审计链路，而不是旁路日志。

- **FR-013**: 系统 MUST 在并发或过期场景下保持幂等，避免重复 approve、重复 cancel 或重复 acknowledge。

- **FR-014**: 系统 MUST 在某个数据源短时不可用时优雅降级，明确标识受影响的 item/source，而不是让整个 inbox 不可用。

- **FR-015**: 系统 MUST NOT 在 Feature 017 中重写 ApprovalManager、Watchdog 检测器或 Telegram ingress / routing 基础逻辑。

- **FR-016**: 系统 SHOULD 为 future mobile/PWA 保留同一 operator action contract，而不把动作语义耦合到某一前端页面。

- **FR-017**: 系统 MUST 将 retry 实现为从来源工作项发起新的、可追溯的执行尝试，而不是在终态 task 上直接强行重跑。

- **FR-018**: 当 Telegram operator 控制面可用时，系统 SHOULD 将 operator action 卡片发送到已批准的 operator 渠道目标；若目标不存在，系统 MUST 明确降级为 Web-only，而不是静默失效。

### Key Entities

- **Operator Inbox Item**: 统一的 operator 工作项投影，表示 approval、alert、retryable failure 或 pairing request。
- **Operator Action Request**: 统一动作请求，描述 operator 试图执行的 approve / deny / retry / cancel / acknowledge 等动作。
- **Operator Action Result**: 统一动作结果，明确是否成功、是否已经被处理、是否过期或因状态不允许而失败。
- **Operator Action Audit Event**: 记录动作来源、操作者、目标对象与结果的结构化审计事件。
- **Pending Pairing Request**: 来源于 Telegram state 的待处理 pairing 请求，在 017 中被产品化为 inbox item。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: operator 在 Web inbox 中可以同时看到 approvals、alerts、retryable failures 和 pending pairings，而不需要切换多个页面或状态源。

- **SC-002**: operator 可以在 Web 或 Telegram 上完成 approve / deny / retry / cancel / acknowledge 等核心动作，且另一端能看到一致结果。

- **SC-003**: 每个动作都会返回明确结果，不会出现“点了但不知道是否生效”的场景。

- **SC-004**: 当动作因已处理、已过期或状态不允许而失败时，系统能稳定返回结构化失败结果，而不是静默失败。

- **SC-005**: task / approval 相关 operator action 都能在统一事件链中回放，并包含来源渠道与操作者信息。

- **SC-006**: Telegram 不再只发送“去 Web 端处理”的审批提醒，而是能作为真正的移动端操作入口。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 017 是否重做 approvals / watchdog / Telegram ingress？ | 否 | 这些能力已经交付，017 的职责是 projection + action surface |
| 2 | Web 与 Telegram 的“等价操作”是否要求完全相同 UI？ | 否，要求同一动作语义与结果 | 重点是 contract 等价，而不是视觉布局一致 |
| 3 | pending pairing request 是否进入统一 inbox？ | 是 | 这是当前明显的用户可用性缺口，且 016 已提供稳定状态源 |
| 4 | operator action 是否允许再开一套旁路日志？ | 否 | 必须复用 Event Store 审计链，符合 Everything is an Event |
| 5 | retry 是否要求与原始任务链路保持可追溯关系？ | 是 | 用户需要知道“这次重试是从哪个失败项发起的”，否则审计与回放会断裂 |
| 6 | retry 是否直接在原 task_id 上重跑？ | 否，创建 successor task / attempt，并把动作审计写回来源链路 | 现有任务状态机不允许终态直接重跑，同时需要保留可追溯性 |
| 7 | Telegram operator 卡片默认发到哪里？ | 发到已批准的 operator DM；没有可用目标时降级为 Web-only | 否则 Web-origin task / alert 在 Telegram 上没有承载位置 |

---

## Scope Boundaries

### In Scope

- 统一 operator inbox projection
- Web inbox 页面与快速操作
- Telegram inline keyboard / callback action 最小支持
- 统一 operator action contract
- operator action 审计与最近动作结果
- pending pairing request 的产品化呈现

### Out of Scope

- 原生 mobile app / PWA
- ApprovalManager / Watchdog / Telegram transport 基础逻辑重写
- JobRunner 交互式 console 全功能面板
- 长期运维后台与第三方 ITSM 集成
