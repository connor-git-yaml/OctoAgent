---
feature_id: "015"
title: "Octo Onboard + Doctor Guided Remediation"
milestone: "M2"
status: "Implemented"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §12.9 / §14"
predecessor: "Feature 014（统一模型配置管理，已交付）"
parallel_dependency: "Feature 016（Telegram Channel + Pairing + Session Routing）通过 channel verifier contract 对接"
---

# Feature Specification: Octo Onboard + Doctor Guided Remediation

**Feature Branch**: `codex/feat-015-octo-onboard-doctor`
**Created**: 2026-03-07
**Status**: Implemented
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 015，交付 `octo onboard`、doctor guided remediation 和可恢复的首次使用闭环。
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

OctoAgent 在 M1.5 已经交付了 `octo config` 与 `octo doctor`，但首次使用体验仍然碎片化：

1. 用户必须自己决定先跑 `octo config`、`octo doctor --live` 还是渠道接入，没有单一主路径。
2. doctor 虽然能列出问题，但还不能把失败结果直接转成“下一步该执行什么”。
3. 用户中断之后，系统不会记住已经完成到哪一步，导致重复劳动。
4. 渠道能力尚未就绪时，系统没有明确表达“当前还差什么才能真正可用”。

Feature 015 要解决的是“从零到首次可用”的闭环，而不是单独增加一个命令。系统必须让用户明确知道：

- 现在完成到了哪一步；
- 下一步该做什么；
- 当前是否真的已经可用；
- 如果中断，回来之后从哪里继续。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 014：`octo config` | 已交付 | 015 以 `octo config` 作为 provider/runtime 配置基线，不再以 `octo init` 为主路径 |
| `octo doctor` 基础检查 | 已交付 | 015 需要在其之上增加结构化 remediation，而不是重写诊断逻辑 |
| Feature 016：Telegram Channel | 待交付 | 015 只依赖 channel verifier contract，不拥有 Telegram transport / pairing 实现 |

前置约束：

- 015 不得重新定义 Telegram pairing、allowlist、thread routing 语义，这些属于 Feature 016。
- 015 必须对“channel verifier 尚未可用”的场景给出明确 blocked 状态和下一步动作。

---

## User Scenarios & Testing

### User Story 1 - 可恢复的首次使用向导 (Priority: P1)

作为首次安装 OctoAgent 的用户，我希望运行一个统一命令就能被一步步带到系统可用状态，并且中途退出后还能继续，而不是重新从头开始，这样我不需要记忆多个命令和步骤顺序。

**Why this priority**: 这是 Feature 015 的核心价值。如果用户仍然需要自己拼装 `config`、`doctor`、channel setup 的顺序，那么 015 就没有真正解决 M2 的首次使用问题。

**Independent Test**: 在一个空项目目录中运行 `octo onboard`，完成 provider 配置后中断，再次运行时验证系统从最近未完成步骤继续，而不是重复已完成步骤。

**Acceptance Scenarios**:

1. **Given** 项目中尚未完成任何 provider/runtime 配置，**When** 用户运行 `octo onboard`，**Then** 系统按统一顺序引导用户完成配置、诊断和后续验证步骤，而不是要求用户手动决定下一条命令。

2. **Given** 用户已经完成 provider 配置但还未完成后续诊断，**When** 用户中断后再次运行 `octo onboard`，**Then** 系统从最近未完成的步骤继续，并保留之前已完成步骤的结果。

3. **Given** 用户已经完成全部 onboarding 步骤，**When** 再次运行 `octo onboard`，**Then** 系统默认执行非破坏性状态检查和摘要输出，不会在未经确认的情况下重置已有配置或进度。

---

### User Story 2 - 失败时得到明确修复动作 (Priority: P1)

作为正在配置系统的用户，我希望当 doctor 发现问题时，系统不仅告诉我哪里失败了，还要明确告诉我下一步应该执行什么命令或操作，这样我可以直接修复，而不是自己猜测。

**Why this priority**: 如果失败结果仍只是检查项列表，用户仍然会回到“读日志猜下一步”的状态，015 的 guided remediation 就没有成立。

**Independent Test**: 构造一个缺少关键依赖的环境，运行 `octo onboard` 或 `octo doctor`，验证输出中包含阻塞原因、动作类型和明确的下一步命令或手动操作。

**Acceptance Scenarios**:

1. **Given** provider 或运行时环境缺少关键配置，**When** 用户运行 `octo onboard`，**Then** 系统把失败原因转成结构化修复动作，并明确标注该动作是否阻塞后续步骤。

2. **Given** `octo doctor --live` 失败，**When** 向导展示结果，**Then** 用户可以看到至少一条明确的下一步动作，而不是仅看到原始异常文本或笼统提示。

3. **Given** 同时存在多个阻塞项，**When** 向导展示 remediation，**Then** 系统按阶段或优先级归组问题，让用户能先处理最关键的阻塞项。

---

### User Story 3 - 渠道接入与首条消息验证 (Priority: P1)

作为准备实际使用 OctoAgent 的用户，我希望在 provider 和 doctor 都通过之后，继续完成首个渠道的接入和首条消息验证，这样我可以确认系统真的已经进入“可用”状态，而不是只有配置文件看起来正确。

**Why this priority**: M2 的闭环不是“配置完成”，而是“真正能发出首条消息并看到结果”。没有这一段，系统只能算“部分准备好”。

**Independent Test**: 在 channel verifier 可用时，运行 `octo onboard` 并完成 channel readiness 与 first-message verification；在 verifier 不可用时，验证系统明确返回 blocked 状态和后续依赖说明。

**Acceptance Scenarios**:

1. **Given** 选中的 channel verifier 已注册且渠道配置满足前置条件，**When** 用户在 `octo onboard` 中继续 channel 步骤，**Then** 系统执行 channel readiness 检查并完成首条消息验证，最终把该步骤标记为完成。

2. **Given** 选中的 channel verifier 尚未注册或能力未就绪，**When** 用户进入 channel 步骤，**Then** 系统明确返回 blocked 状态，说明缺少的依赖或后续 Feature，而不是把 onboarding 误报为成功。

3. **Given** 用户在 channel 步骤暂时无法继续，**When** 用户结束本次流程，**Then** 系统保留当前状态并在摘要中明确说明“哪些核心步骤已完成、哪些渠道步骤仍待完成”。

---

### User Story 4 - 明确的系统可用摘要 (Priority: P2)

作为用户，我希望在 onboarding 结束时得到一个清晰的最终摘要，明确告诉我系统现在是“已可用”“还需动作”还是“被阻塞”，以及下一步要做什么，这样我不会误判系统状态。

**Why this priority**: 没有统一的终态表达，用户只能自己解读一堆表格和日志，这会直接削弱系统可用性。

**Independent Test**: 分别构造 ready、action_required、blocked 三种场景，运行 `octo onboard`，验证最终摘要的状态和下一步动作符合预期。

**Acceptance Scenarios**:

1. **Given** 所有必要步骤均已完成，**When** onboarding 结束，**Then** 系统明确输出 `READY` 状态，并总结已完成步骤与最终可用能力。

2. **Given** 核心配置已完成但 channel 验证尚未完成，**When** onboarding 结束，**Then** 系统输出 `ACTION_REQUIRED` 或 `BLOCKED` 状态，并指明缺失步骤及下一步动作。

3. **Given** 用户重新运行已完成的 onboarding，**When** 系统输出摘要，**Then** 摘要能复述当前系统状态，而不要求用户重新解释之前的步骤结果。

---

### Edge Cases

- 当 onboarding session 持久化文件损坏或版本不兼容时，系统如何安全恢复并避免把用户带到错误步骤？
- 当 `octo config` 已完成但 `octo doctor --live` 因外部依赖（如 Docker、Proxy、Provider 凭证）失败时，系统是否能保留已完成步骤并只针对失败步骤给出动作？
- 当 channel verifier 不存在时，系统如何避免误报 READY，同时又不丢失前面已完成的 provider/runtime 结果？
- 当用户在 channel 验证超时或首条消息未返回时，系统如何保存当前状态并允许后续重试？
- 当用户重复运行 `octo onboard` 时，系统如何防止无提示地覆盖已确认有效的配置和进度？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 提供 `octo onboard` 作为首次使用的统一入口，按固定顺序串联 provider/runtime 配置检查、doctor 诊断、channel readiness 和首条消息验证。

- **FR-002**: 系统 MUST 识别项目当前已完成的 onboarding 状态，并从最近未完成步骤继续，而不是强制用户重复已完成步骤。

- **FR-003**: 系统 MUST 持久化 onboarding 进度，包括当前步骤、已完成步骤、阻塞项、最近修复动作和最后更新时间。

- **FR-004**: 系统 MUST 以现有 `octo config` 体系作为 provider/runtime 的配置基线，不要求用户必须重新运行历史路径 `octo init` 才能完成 onboarding。

- **FR-005**: 系统 MUST 将 doctor 的失败结果表达为结构化 remediation，至少包含：问题分类、阻塞级别、下一步动作，以及可执行命令或明确的手动操作说明。

- **FR-006**: 系统 MUST 在进入 channel 步骤前完成 provider/runtime readiness 与 `octo doctor --live` 级别的健康验证；未通过时不得把系统标记为可用。

- **FR-007**: 系统 MUST 支持可插拔的 channel verifier contract，使 onboarding 能对接实际渠道验证流程，同时不把具体 Telegram transport / pairing 逻辑固化到 015 中。

- **FR-008**: 当选定的 channel verifier 可用时，系统 MUST 支持执行 channel readiness 与首条消息验证，并将验证结果纳入最终摘要。

- **FR-009**: 当选定的 channel verifier 不可用、未注册或依赖未满足时，系统 MUST 明确返回 blocked 状态和下一步动作，不得误报 READY。

- **FR-010**: 系统 MUST 在 onboarding 结束时输出统一摘要，至少区分 `READY`、`ACTION_REQUIRED`、`BLOCKED` 三种状态，并列出下一步动作。

- **FR-011**: 系统 MUST 默认为非破坏性重跑；在未获得用户确认前，不得重置已完成的配置或 onboarding 进度。

- **FR-012**: 系统 SHOULD 允许用户在任意阶段安全退出，并在后续重跑时恢复到正确步骤，而不丢失已完成步骤的结果。

- **FR-013**: 系统 SHOULD 让 `octo doctor` 与 `octo onboard` 共享同一套 remediation 结果模型，避免用户在两个命令中看到彼此矛盾的修复建议。

- **FR-014**: 系统 MUST 能处理部分完成项目，即在已有配置、已有 doctor 结果或已有 channel 状态的情况下，跳过已完成步骤并继续后续流程。

- **FR-015**: 系统 MUST NOT 在 Feature 015 中重新实现 Telegram transport、pairing 存储或 thread routing 语义；这些职责属于 Feature 016。

### Key Entities

- **Onboarding Session**: 表示一次项目级 onboarding 流程，记录当前阶段、已完成步骤、阻塞项、最近 remediation 和最终状态。
- **Onboarding Step State**: 表示单个步骤（如 provider、doctor、channel、first-message）的状态、最近结果和下一步动作。
- **Doctor Remediation**: 表示 doctor 输出的结构化修复动作，包含阻塞级别、问题原因、下一步命令或手动操作说明。
- **Channel Verifier**: 表示渠道 onboarding 的可插拔验证能力，用于执行 readiness 检查和首条消息验证。
- **Onboarding Summary**: 表示流程结束时的统一摘要，明确系统是否 ready、仍需动作或被阻塞。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 用户可以通过单一 `octo onboard` 流程完成 provider/runtime 配置检查、doctor 验证和状态汇总，而不需要手动编辑配置文件或自行判断命令顺序。

- **SC-002**: 在 onboarding 中断后，系统能从最近未完成步骤恢复，且不会要求用户重复已完成步骤。

- **SC-003**: 每个阻塞性失败场景都至少提供一条明确的 remediation 动作，用户无需依赖原始异常堆栈来决定下一步。

- **SC-004**: 当 channel verifier 可用时，用户能在 onboarding 中完成 channel readiness 和首条消息验证；当 verifier 不可用时，系统明确返回 blocked 状态和缺失依赖，而不是误报成功。

- **SC-005**: onboarding 结束时，系统能稳定输出 `READY`、`ACTION_REQUIRED` 或 `BLOCKED` 之一，并附带与该状态一致的下一步动作。

- **SC-006**: 对已完成 onboarding 的项目重复运行 `octo onboard` 时，流程默认不会破坏已存在的配置或进度，且能在一次运行内输出当前 readiness 摘要。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 015 是否直接实现 Telegram pairing / routing？ | 否。015 只拥有 channel verifier contract | 保持与 Feature 016 的并行边界，避免职责漂移 |
| 2 | onboarding 的 provider 配置阶段应基于哪条路径？ | 基于 `octo config` | Feature 014 已把 `octo config` 设为当前基线，`octo init` 仅保留历史兼容意义 |
| 3 | channel verifier 不可用时是否还能标记 READY？ | 不能 | blueprint 的首次使用闭环要求包含 channel 和 first message verification |
| 4 | onboarding 重跑默认行为是否允许重置配置？ | 不允许，必须显式确认 | 避免首次使用路径变成破坏性工具，符合 User-in-Control |

---

## Scope Boundaries

### In Scope

- `octo onboard` CLI 入口
- provider/runtime/doctor/channel/first-message 的统一流程编排
- onboarding session 持久化与 resume
- doctor guided remediation
- channel verifier contract 与 blocked state 处理
- onboarding 最终摘要

### Out of Scope

- Telegram transport / ingress / reply routing / pairing 存储本体
- Web onboarding UI
- operator inbox / mobile task controls
- backup / restore / export 产品入口
- JobRunner 交互式控制台
