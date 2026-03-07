---
feature_id: "016"
title: "Telegram Channel + Pairing + Session Routing"
milestone: "M2"
status: "Implemented"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §5.1.1 / §13.4 / Appendix B.2"
predecessor: "Feature 015（Octo Onboard + Doctor Guided Remediation，已交付）"
parallel_dependency: "Feature 017（统一 operator inbox）仅消费 016 的 Telegram action/result surface"
---

# Feature Specification: Telegram Channel + Pairing + Session Routing

**Feature Branch**: `codex/feat-016-telegram-channel`
**Created**: 2026-03-07
**Status**: Implemented
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 016，交付 Telegram 作为首个真实外部渠道，并落实 pairing、allowlist、session routing、webhook/polling 与基础回传语义。
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

OctoAgent 在 M1.5 已经具备了 `NormalizedMessage -> Task -> Event -> SSE` 的最小 Agent 闭环，也已经有 `octo onboard --channel telegram` 的 verifier 接缝，但系统仍然只能通过 Web/API 入口使用：

1. Gateway 还没有真实 Telegram transport，用户不能从 Telegram 发起首条消息。
2. pairing / allowlist 还没有真正落地，系统无法明确表达“谁被允许发消息、谁会被拦截”。
3. Telegram 的 DM、群聊、forum topic、reply thread 尚无稳定的 `scope_id` / `thread_id` 规则，后续 approval / retry / cancel 无法可靠回到同一会话。
4. onboarding 与 doctor 目前只能告诉用户“缺少 verifier”，却不能真正检查 Telegram readiness。

Feature 016 要解决的不是“多一个 webhook 路由”，而是“把 Telegram 变成 OctoAgent 第一个真实可用、可审计、可恢复的外部渠道”。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 015：channel verifier contract | 已交付 | 016 需要提供真实 Telegram verifier，实现 readiness / first-message 闭环 |
| Gateway 通用入站任务链路 | 已交付 | 016 必须复用现有 `NormalizedMessage -> TaskService -> Event/SSE`，不得另起一套任务系统 |
| `octo config` / `octo doctor` | 已交付 | 016 需要在其基础上新增 Telegram 配置与诊断，不得绕开现有统一入口 |
| WebChannel / REST API | 已交付 | 016 不得破坏现有 Web/API 路由和回归测试 |

前置约束：

- 016 必须从 `master` 基线推进，保持当前 WebChannel 行为不回归。
- 016 不得吞并 017 的统一 operator inbox；Telegram action/result 只需提供基础 surface。
- 016 必须遵守 fail-closed 默认值：未授权消息、缺少 secret、错误 mode 配置都不得静默放通。

---

## User Scenarios & Testing

### User Story 1 - Telegram 私聊与群聊消息稳定进入任务链路 (Priority: P1)

作为 owner 或可信群成员，我希望在 Telegram 里发消息时，系统能稳定进入 OctoAgent 的任务链路，并保持正确的 thread/session 归属，这样我不需要回到 Web 才能真正使用 Agent。

**Why this priority**: 这是 016 的根价值。没有真实 Telegram 入站，M2 的“多渠道可用”根本没有成立。

**Independent Test**: 通过 webhook 或 polling 注入 Telegram DM、群聊、forum topic 和 reply thread 的更新，验证系统生成正确的 `NormalizedMessage`、`scope_id`、`thread_id`、Task 与事件序列。

**Acceptance Scenarios**:

1. **Given** 已配置 Telegram bot 且 owner 已授权，**When** owner 在 Telegram 私聊 bot 发送文本消息，**Then** 系统创建 Task，并把消息稳定映射到 Telegram 对应私聊会话。
2. **Given** bot 已在授权群组中，**When** 群组内发送满足策略条件的消息，**Then** 系统把该消息映射到固定群聊 `scope_id`，并在需要时区分 forum topic / reply thread。
3. **Given** Telegram 重复投递同一 update，**When** Gateway 再次接收该 update，**Then** 系统不会创建重复 Task 或重复追加消息事件。

---

### User Story 2 - Telegram pairing / allowlist 默认安全且可恢复 (Priority: P1)

作为 owner，我希望 Telegram 默认是安全关闭的：未知私聊用户先进入 pairing，群聊必须显式授权，这样系统不会因为 bot token 泄漏或群聊噪音而被静默滥用。

**Why this priority**: 没有 pairing / allowlist，Telegram 渠道一上线就会扩大攻击面，直接违背 Constitution 的 safe by default 和 user-in-control 原则。

**Independent Test**: 让未知私聊用户、未授权群聊成员、已批准私聊用户和已授权群聊分别发送消息，验证 pairing code、allowlist、生效边界和重启后的持久化状态。

**Acceptance Scenarios**:

1. **Given** 未知私聊用户首次向 bot 发消息，**When** 系统接收该消息，**Then** 原消息不会进入 Task 链路，而是返回 pairing 提示并生成待审批请求。
2. **Given** owner 已批准某个 Telegram 用户，**When** 该用户再次私聊 bot，**Then** 消息会被允许进入正常任务链路，且授权状态在系统重启后仍保留。
3. **Given** 群聊未显式加入允许列表，**When** bot 收到该群聊消息，**Then** 系统必须拒绝处理并提供明确的 blocked / remediation 信号，而不是沿用 DM pairing 结果。

---

### User Story 3 - Telegram 出站回复、审批提示与错误提示回到同一会话 (Priority: P1)

作为 Telegram 用户，我希望 Agent 的回复、审批提示、错误提示和重试结果都能回到原来的会话或线程，这样我能在手机上完整看到这次任务的状态，而不是跳回 Web 才能理解发生了什么。

**Why this priority**: 只有入站没有回传，会让 Telegram 变成单向入口，实际使用体验仍然断裂。

**Independent Test**: 触发一次 Telegram 发起的任务、一次需要审批的动作和一次失败重试场景，验证文本回复、审批提示和错误结果都回传到正确的 DM / 群聊 / topic / reply thread。

**Acceptance Scenarios**:

1. **Given** Telegram 消息成功创建 Task，**When** Agent 产出结果，**Then** 结果会回到原 Telegram 会话，并保持正确的 reply / thread 语义。
2. **Given** Telegram 发起的任务进入等待审批，**When** 系统提示用户审批，**Then** Telegram 端能收到明确的审批提示或动作结果，且状态与 Web/事件链保持一致。
3. **Given** Telegram 任务执行失败或被重试，**When** 系统回传结果，**Then** 用户能在 Telegram 中看到清晰的错误提示或重试结果，而不是只有服务端日志。

---

### User Story 4 - webhook / polling 双模式与诊断闭环 (Priority: P2)

作为 owner，我希望 Telegram 渠道既支持生产可用的 webhook，也支持本地开发时的 polling，并且 `octo doctor` 与 `octo onboard` 能明确告诉我当前是哪种模式、是否可用、下一步该做什么。

**Why this priority**: Telegram 的生产部署和本地开发环境差异很大。如果没有明确的双模式与诊断闭环，用户会不断在“为何收不到消息”上浪费时间。

**Independent Test**: 分别验证 webhook 正常、webhook secret 错误、polling 正常、mode 配置冲突与 verifier 首条消息检查场景。

**Acceptance Scenarios**:

1. **Given** Telegram 配置为 webhook 且地址可达，**When** `octo onboard --channel telegram` 或 `octo doctor` 检查该渠道，**Then** 系统能返回明确的 readiness 结果。
2. **Given** webhook secret 缺失或不匹配，**When** Gateway 接收 Telegram 请求，**Then** 系统拒绝该请求并暴露可执行的修复提示。
3. **Given** 开发环境选择 polling，**When** Telegram 渠道启动，**Then** 系统只运行单一 polling runner，且不会与 webhook 同时双活。

---

### Edge Cases

- 当 Telegram 同一 update 被 webhook 重试或 polling 重放时，系统如何避免重复 Task / 重复消息事件？
- 当私聊 pairing 请求过期、待审批数量达到上限或 Gateway 重启后，系统如何保持授权边界与用户提示一致？
- 当 bot 位于 forum topic、普通群 reply thread、私聊三种不同上下文时，`scope_id` / `thread_id` 如何稳定且可回放？
- 当 webhook secret、bot token、TLS 或群组权限配置错误时，系统如何 fail-closed 并给出 remediation，而不是静默丢消息？
- 当 Telegram 不支持或未发送文本正文（仅回调、媒体、服务消息）时，系统如何降级，而不是让 transport 崩溃？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供 Telegram 渠道配置作为统一配置的一部分，并支持声明运行模式、bot 凭证引用、DM 策略、群组 allowlist 与 webhook 参数。

- **FR-002**: 系统 MUST 支持 Telegram webhook 与 polling 两种运行模式，并保证同一 bot 在任一时刻只运行一种入站模式。

- **FR-003**: 当 Telegram 以 webhook 模式运行时，系统 MUST 使用 Telegram 提供的安全机制校验入站请求；校验失败时必须拒绝请求。

- **FR-004**: 系统 MUST 把 Telegram update 规范化为 `NormalizedMessage`，至少保留稳定的渠道、发送者、聊天会话、线程与幂等关键信息。

- **FR-005**: 系统 MUST 为 Telegram 冻结稳定的 session routing 规则，使 DM、群聊、forum topic、reply thread 在重放、恢复和回传时都映射到相同的 `scope_id` / `thread_id`。

- **FR-006**: 系统 MUST 对 Telegram 入站更新执行幂等去重；同一 update 的重复投递不得创建重复 Task 或重复追加消息事件。

- **FR-007**: 系统 MUST 默认对未知私聊用户启用 pairing 或等价的显式授权流程；在授权前，原消息不得进入任务链路。

- **FR-008**: 系统 MUST 持久化 Telegram pairing 与 allowlist 状态，使批准结果、待审批请求和必要的过期策略在进程重启后仍可恢复。

- **FR-009**: 系统 MUST 将 Telegram 私聊授权与群组授权分开处理；群组是否允许触发 Agent 必须由显式 group allowlist / policy 决定，不得自动继承 DM pairing 结果。

- **FR-010**: 系统 MUST 支持 Telegram 出站文本回复，并保持与原消息一致的会话 / thread / reply 语义。

- **FR-011**: 当 Telegram 发起的任务进入等待审批、失败或重试等关键状态时，系统 MUST 能向 Telegram 用户回传清晰的提示或结果，不得只在服务端日志中可见。

- **FR-012**: 系统 MUST 为 `octo onboard --channel telegram` 提供真实 Telegram verifier，使 channel readiness 与首条消息验证可基于真实 Telegram 配置执行。

- **FR-013**: 系统 MUST 让 `octo doctor` 检测 Telegram 渠道的关键故障，包括但不限于缺少 bot token、mode 冲突、webhook 安全配置问题、未完成 pairing / allowlist、渠道不可达。

- **FR-014**: 系统 MUST 在 Telegram 渠道异常时优雅降级：不能因为 Telegram transport 失败而拖垮 Gateway 主进程，也不能让其它渠道整体不可用。

- **FR-015**: 系统 MUST 保持现有 Web/API 消息入口、Task 流程、resume/cancel/approval REST 语义不回归。

- **FR-016**: 系统 MUST 为 Feature 016 增加集成测试，覆盖 pairing、重复 update 去重、群聊/topic 路由、Telegram 回传与 verifier 闭环。

### Key Entities

- **Telegram Channel Config**: Telegram 渠道的统一配置，表达 mode、bot 凭证引用、allowlist、group policy 与 webhook 参数。
- **Telegram Pairing Request**: 私聊首次接入时生成的待审批请求，包含 pairing code、来源用户、过期信息与状态。
- **Telegram Session Key**: Telegram 会话与线程的规范化标识，负责把 DM、群聊、topic、reply 映射到稳定的 `scope_id/thread_id`。
- **Telegram Inbound Update**: 从 Telegram 接收的原始更新，经过校验和归一化后进入 `NormalizedMessage`。
- **Telegram Outbound Message**: 发往 Telegram 的回传消息，包含文本内容以及必要的 reply / thread / action 元数据。
- **Telegram Channel Readiness**: `octo onboard` / `octo doctor` 使用的渠道就绪状态，表达当前是 READY、ACTION_REQUIRED 还是 BLOCKED。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可在 `octo onboard --channel telegram` 引导下完成 Telegram readiness 与首条消息验证，而不是卡在“缺少 verifier”。

- **SC-002**: Telegram 私聊消息能够稳定进入 `NormalizedMessage -> Task` 链路，且重复投递不会创建重复 Task。

- **SC-003**: 未授权私聊和未授权群组不会静默触发 Agent；系统会给出明确的 pairing / blocked / remediation 结果。

- **SC-004**: Telegram 的 DM、群聊、forum topic、reply thread 都能稳定映射到一致的 `scope_id` / `thread_id`，并在回传时保持同一会话语义。

- **SC-005**: Telegram 发起的任务在成功、等待审批、失败和重试四种关键状态下，都能在 Telegram 中看到对应的回传结果。

- **SC-006**: 现有 Web/API 路由、Task 生命周期、resume/cancel/approval 主流程在引入 Telegram 后保持回归通过。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 016 的 transport 应归属哪一层？ | OctoGateway | 蓝图已经明确 `Channels -> OctoGateway -> OctoKernel`，Kernel 不应直接感知 Telegram |
| 2 | webhook 与 polling 的默认关系？ | 默认优先 webhook，本地/无 HTTPS 时允许 polling | 符合蓝图与官方 Bot API 约束，且便于开发与生产并存 |
| 3 | DM 与群组授权是否共用同一 allowlist？ | 否，必须分离 | 参考 OpenClaw 与安全原则，群组不能继承 DM pairing |
| 4 | 016 是否一并实现统一 operator inbox？ | 否 | 该能力属于 Feature 017，016 只提供基础 Telegram action/result surface |
| 5 | 016 是否必须交付真实 Telegram verifier？ | 是 | 否则 015 的 onboarding/doctor 闭环仍然是假的，M2 首次使用链路不成立 |

---

## Scope Boundaries

### In Scope

- Telegram channel config
- webhook / polling 双模式
- DM pairing / allowlist 与群组 allowlist
- Telegram session routing（DM、群聊、topic、reply）
- Telegram 文本回传、审批提示、错误提示、重试结果
- `octo onboard` / `octo doctor` 的真实 Telegram verifier
- 016 对应的集成测试与回归测试

### Out of Scope

- 统一 operator inbox 与跨渠道 pending 聚合
- 多 bot account / 多租户 Telegram 管理
- 高级媒体、poll、复杂富交互组件
- Telegram 之外的其它移动端控制面
- 备份/恢复产品入口与会话导出
