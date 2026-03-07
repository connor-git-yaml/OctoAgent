---
feature_id: "023"
title: "M2 Integration Acceptance"
milestone: "M2"
status: "Verified"
created: "2026-03-07"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §5.1 / §12.4 / §12.9 / §14"
predecessor: "Feature 015-022（M2 全量能力基线）"
parallel_dependency: "023 为 M2 汇合验收 Feature，不与新增业务能力并行"
---

# Feature Specification: M2 Integration Acceptance

**Feature Branch**: `codex/feat-023-m2-integration-acceptance`  
**Created**: 2026-03-07  
**Status**: Verified  
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 023，对 015-022 进行用户视角端到端验收收口。  
**调研基础**: `research/research-synthesis.md`、`research/product-research.md`、`research/tech-research.md`、`research/online-research.md`

---

## Problem Statement

OctoAgent 的 M2 能力在代码层已经大体齐备，但仍停留在“分段 Feature 通过”的状态：

1. 首次使用路径仍会在 `octo config init`、`octo doctor --live`、Telegram channel 配置、pairing 和首条消息之间断开。
2. Web 与 Telegram 的 operator control 已分别存在，但还没有证明它们处理的是“同一条待办、同一条审计链、同一套结果语义”。
3. A2A-Lite、JobRunner、interactive execution 各自有 contract 和测试，但还没有联合证明协议消息真正进入执行面。
4. Memory、Chat Import、backup/export/restore 各自可用，但尚未证明导入后的数据真的进入同一 durability boundary。

Feature 023 的目标不是新增业务能力，而是把这些已经存在的能力收束成一个对用户真实成立的 M2：

- 新用户可以从零完成一次首次 working flow；
- operator 可以在 Web 或 Telegram 上等价处理待办；
- A2A 与 JobRunner 的执行链具备真实联通证据；
- 导入后的数据能被导出、备份和 restore dry-run 消费；
- 最终输出一份明确的 M2 验收报告和剩余风险清单。

---

## Scope Boundaries

### In Scope

- 修补阻塞首次使用闭环的最小 DX 断点
- 新增 023 联合验收测试
- 新增 M2 验收矩阵与验收报告
- 补充必要 contract / checklist / data model 文档

### Out of Scope

- 新增新的业务能力或新产品域
- destructive restore apply
- 新的 Telegram 功能
- 新的 A2A 消息类型或公开 API
- 新的 frontend 运维控制台
- 新的 source adapter / memory policy

### Allowed Changes Rule

023 **允许**修改已有 CLI / verifier / gateway / protocol / tests，但只限以下两类：

1. 修补阻塞联合验收的断点；
2. 为联合验收增加必要的测试胶水与报告制品。

任何超出该边界的功能性扩张都必须拒绝，留给后续 Feature。

---

## Pre-conditions

| 依赖项 | 当前状态 | 说明 |
|---|---|---|
| Feature 015: Onboard + Doctor | 已交付 | 023 复用现有 CLI / onboarding / remediation 基线 |
| Feature 016: Telegram Channel | 已交付 | 023 复用现有 pairing / ingress / egress 能力 |
| Feature 017: Operator Inbox | 已交付 | 023 复用现有 Web / Telegram actions 和 audit chain |
| Feature 018: A2A-Lite | 已交付 | 023 复用现有 TASK/RESULT/ERROR contract |
| Feature 019: JobRunner + Interactive Console | 已交付 | 023 复用现有 TaskRunner / WorkerRuntime / input resume |
| Feature 020: Memory Core | 已交付 | 023 复用 proposal/validate/commit contract |
| Feature 021: Chat Import | 已交付 | 023 复用 import report / audit / fragment / proposal path |
| Feature 022: Backup/Restore/Export | 已交付 | 023 复用 backup/export/restore dry-run/recovery summary |

前置约束：

- 023 不得重新定义上述 Feature 的主 contract；
- 023 必须优先消费真实本地组件，而不是把联合验收完全 fake 化；
- 外部依赖（Telegram API、LiteLLM Proxy、第三方 provider）允许通过 mock transport 替身稳定测试；
- 验收报告必须明确记录 remaining risks，不得只写 PASS。

---

## User Scenarios & Testing

### User Story 1 - 新用户完成首次 working flow (Priority: P0)

作为第一次使用 OctoAgent 的 owner，我希望从一个新的项目目录开始，通过稳定的一条路径完成 `config -> doctor -> onboard -> pairing -> first inbound task`，这样我不用猜测下一步操作，也不会在 CLI/YAML/Web 之间迷失。

**Why this priority**: 如果首次使用链仍断裂，M2 无法宣称“可日常使用”。

**Independent Test**: 在全新项目目录中，执行统一配置、diagnostics、Telegram pairing 和首条消息入站，最终验证系统创建了 Telegram task，并能让 onboarding 判定首次消息链路已完成。

**Acceptance Scenarios**:

1. **Given** 新项目目录还没有配置文件，**When** 用户运行 `octo config init` 并选择 Telegram 作为目标 channel，**Then** 生成的统一配置足以成为 `octo doctor --live` 的合法前置，不要求额外手工补 `.env` 才能继续。

2. **Given** 用户选择 Telegram channel，**When** 首次使用流程推进到 channel 配置阶段，**Then** 系统提供可操作的 Telegram 配置闭环，而不是要求用户手工修改 YAML 才能继续。

3. **Given** Telegram bot 已可出站发送消息，**When** 用户真正通过 gateway 发送首条私聊消息并完成 pairing，**Then** onboarding 最终必须以“检测到入站 task 或等价本地证据”为完成标准，而不是只以 `sendMessage()` 成功为准。

### User Story 2 - Operator 控制在 Web 与 Telegram 上等价 (Priority: P0)

作为日常操作者，我希望同一条 approval / pairing / alert / retryable failure 待办在 Web 和 Telegram 上都能用相同语义处理，并写入同一条审计链，这样我不需要记住两个渠道的不同规则。

**Why this priority**: 操作面语义如果漂移，M2 的“多渠道日常使用”会变成双套系统。

**Independent Test**: 为同一类 operator item 分别在 Web 与 Telegram 触发动作，验证 item_id、outcome、审计事件和任务状态回放一致。

**Acceptance Scenarios**:

1. **Given** 存在 Telegram pairing request，**When** owner 在 Web operator inbox 批准该 pairing，**Then** Telegram 状态源更新、审计事件落盘，并且 Telegram 侧再次尝试同一动作时返回已处理语义。

2. **Given** 存在 approval / retry / cancel / alert ack item，**When** 用户分别在 Web 和 Telegram 处理，**Then** 两端都返回同一 outcome 语义，并落到同一 operational audit chain。

3. **Given** 某 item 已被一端处理，**When** 另一端再次处理同一 item，**Then** 系统返回 `already_handled` 或等价结果，而不会二次执行副作用。

### User Story 3 - A2A 消息真正驱动执行面 (Priority: P0)

作为系统维护者，我希望 `A2A TASK` 消息不只是通过 schema round-trip，而是能真正驱动 dispatch、worker runtime、interactive execution 和 result/error 映射，这样我才能相信协议层不是脱离执行面的空壳。

**Why this priority**: 018 和 019 的价值只有在协议到执行面被联合验证后才成立。

**Independent Test**: 从真实 `DispatchEnvelope` 生成 `A2AMessage(TASK)`，再转换回 dispatch 信封进入 runtime，最终产出 `RESULT/ERROR` 消息并验证状态映射正确。

**Acceptance Scenarios**:

1. **Given** 一个可执行 task，**When** 它被映射为 `A2A TASK` 并重新进入 dispatch/runtime，**Then** 任务能够真正执行并产出 `RESULT`，而不是只通过模型层 round-trip。

2. **Given** runtime 进入 `WAITING_INPUT`、`CANCELLED` 或失败路径，**When** 结果被映射回 A2A，**Then** `A2AStateMapper` 和 execution side effects 必须保持一致。

3. **Given** interactive execution 发生 input resume 或 cancel，**When** runtime 完成，**Then** 执行面状态和 A2A 消息状态都必须可回放、可解释。

### User Story 4 - 导入后的数据进入可恢复边界 (Priority: P0)

作为把历史聊天导入系统的 owner，我希望导入后的 fragments / SoR / artifacts 能被 export、backup 和 restore dry-run 一起消费，这样我能确认导入结果不是留在系统角落里的孤岛。

**Why this priority**: 如果 020/021/022 只是各自可用，M2 仍无法证明 durability boundary 已闭环。

**Independent Test**: 真实执行一次 `octo import chats`，随后执行 export、backup 与 restore dry-run，验证导入结果进入持久化边界并出现在恢复证据中。

**Acceptance Scenarios**:

1. **Given** 用户成功导入一批聊天历史，**When** 检查 memory/import/audit 结果，**Then** 可见 fragments、facts、artifacts 和 import report 已持久化。

2. **Given** 上述导入已经完成，**When** 用户执行 `octo export chats` 与 `octo backup create`，**Then** 导入相关任务/产物进入导出与备份结果。

3. **Given** 已创建 backup bundle，**When** 用户执行 `octo restore dry-run`，**Then** 系统能输出结构化恢复计划，并把最近 recovery drill 状态更新为可回看的证据。

### User Story 5 - 输出 M2 验收报告与风险清单 (Priority: P1)

作为项目 owner，我希望在 023 结束时拿到一份可回看的 M2 验收报告，明确哪些联合链已经闭环、哪些风险仍存在、哪些项被明确推迟，这样我能决定是否宣布 M2 完成。

**Why this priority**: 没有最终报告，023 很容易退化成“做了几条测试，但没人知道里程碑结论”。

**Independent Test**: 生成一份 023 验收报告，包含验收矩阵、测试命令、结果、剩余风险和 out-of-scope 列表。

**Acceptance Scenarios**:

1. **Given** 所有 023 自动化测试执行完成，**When** 生成验收报告，**Then** 报告中必须能追溯到四条联合验收线及其证据。

2. **Given** 某些链路仍存在边界或已知问题，**When** 报告生成，**Then** 这些风险必须被明确列为 remaining risks，而不是隐去。

3. **Given** 某些工作被明确留给后续里程碑，**When** 报告生成，**Then** 报告必须列明 out-of-scope / deferred 项，避免误解成 023 未完成。

---

## Edge Cases

- 当用户已有 `octoagent.yaml` 但没有 `.env` / `.env.litellm` 时，`doctor` 应如何判定缺失是阻塞还是可跳过？
- 当 Telegram channel 已启用但缺少 webhook/polling 关键字段时，系统如何给出下一步动作而不是只返回 `SKIP`？
- 当 bot 出站正常但 gateway 未启动、webhook secret 错误或 polling loop 未生效时，onboarding 如何避免误判完成？
- 当同一 operator item 已被 Web 端处理，Telegram 端回调再到达时，如何证明不会二次执行副作用？
- 当 A2A 消息 round-trip 成功但 runtime 失败或等待输入时，如何保证映射状态与执行状态仍一致？
- 当 chat import 已生成 artifacts / fragments / facts，但 export/backup 只覆盖部分对象时，如何发现 durability chain 裂缝？
- 当某条联合验收链只能通过 mock 外部依赖稳定运行时，如何清晰区分“本地真实组件”与“外部替身”边界？

---

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 把 `octo config init` 产物与 `octo doctor --live` 的前置假设对齐，使统一配置成为首次使用的合法入口。

- **FR-002**: 系统 MUST 为 Telegram channel 提供可操作的配置闭环，使用户在首次使用链中无需手工编辑 YAML 才能继续。

- **FR-003**: onboarding 的首条消息验证 MUST 以“检测到真实入站 task 或等价本地证据”为完成标准，不得仅以 bot 出站消息成功为准。

- **FR-004**: 系统 MUST 提供一条自动化首次使用验收链，至少覆盖 `config -> doctor -> onboarding -> pairing -> first inbound task`。

- **FR-005**: 系统 MUST 验证同一 operator item 在 Web 与 Telegram 上的处理结果语义一致，至少覆盖 pairing、approval、retry、cancel、alert ack。

- **FR-006**: 同一 operator item 被一端处理后，另一端再次执行 MUST 返回 `already_handled` 或等价结果，不得重复副作用。

- **FR-007**: 系统 MUST 验证 `A2A TASK` 消息可以真实进入 dispatch/runtime，并最终映射回 `RESULT/ERROR`。

- **FR-008**: A2A 联合验收 MUST 覆盖至少一种成功路径和一种非成功路径（如 waiting input、cancel、failed）。

- **FR-009**: 系统 MUST 验证 interactive execution 的 input resume / cancel 行为与 A2A / runtime 状态映射一致。

- **FR-010**: 系统 MUST 验证 `octo import chats` 产生的持久化结果进入 export / backup / restore dry-run 的消费边界。

- **FR-011**: import / memory / recovery 联合验收 MUST 至少覆盖 fragments、facts、artifacts、audit events 和 recovery summary。

- **FR-012**: 023 MUST 生成一份 M2 验收矩阵，逐项映射 `GATE-M2-ONBOARD`、`GATE-M2-CHANNEL-PARITY`、`GATE-M2-A2A-CONTRACT`、`GATE-M2-MEMORY-GOVERNANCE`、`GATE-M2-RESTORE`。

- **FR-013**: 023 MUST 生成一份验收报告，至少包含测试命令、验收结论、剩余风险和 out-of-scope 列表。

- **FR-014**: 023 MUST NOT 在本 Feature 中新增新的业务能力、独立控制面、destructive restore 或新的 source adapter。

- **FR-015**: 023 SHOULD 优先复用真实本地组件，外部 API / provider 调用才允许通过 mock transport 或 fake client 替身。

- **FR-016**: 023 MUST 保持现有 contract 不被重定义；若需修补断点，只能在现有 contract 语义内收敛。

### Key Entities

- **Acceptance Scenario**: 表示一条可独立验证的联合验收路径，绑定用户目标、触发步骤、期望证据和失败风险。
- **Acceptance Matrix Row**: 表示一个 M2 gate 到具体测试、组件和验证命令的追踪关系。
- **First-Use Checkpoint**: 表示首次使用链中的阶段性证据，例如统一配置就绪、doctor 通过、pairing 已批准、首条入站任务已创建。
- **Operator Parity Record**: 表示某个 operator item 在 Web / Telegram 两端的动作结果、审计事件和冲突处理语义。
- **A2A Execution Trace**: 表示从 `A2A TASK` 到 runtime 到 `RESULT/ERROR` 的完整协议-执行链证据。
- **Recovery Evidence Set**: 表示 chat import、memory、export、backup、restore dry-run 之间的联合持久化证据集合。
- **M2 Verification Report**: 表示 023 的最终验收报告，记录结论、证据、风险和边界。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 在新项目目录中，用户可通过单条主路径完成首次 working flow，不再因为 `.env` 前置、Telegram 配置或首条消息误判而中断。

- **SC-002**: Web / Telegram 对同一 operator item 的关键动作在自动化验证中表现出一致的 outcome 和单一审计链。

- **SC-003**: `A2A TASK -> runtime -> RESULT/ERROR` 至少有一条成功路径和一条非成功路径的自动化联合证据。

- **SC-004**: `octo import chats` 产生的 artifacts / fragments / facts 在 export、backup、restore dry-run 中可被联合验证。

- **SC-005**: 023 输出的验收矩阵能够逐项覆盖 M2 split 文档要求的五个 gate。

- **SC-006**: 023 验收报告可以明确回答“M2 是否可视为日常可用”，并同时列出剩余风险。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 023 是否允许修改已有实现，而不仅是补测试？ | 允许，但只限修阻塞联合验收的最小断点 | 否则首次使用闭环无法成立 |
| 2 | 023 是否新增新的业务能力？ | 否 | `docs/m2-feature-split.md` 已明确 023 只做汇合验收 |
| 3 | 首次 owner pairing 的主路径是什么？ | Web / operator inbox 主路径，手工 state 编辑仅作降级 | 更符合 safe-by-default 与用户可控原则 |
| 4 | A2A 联合验收是否必须新增公开 API？ | 否 | 023 只需证明协议层与执行层真实联通 |
| 5 | 外部 Telegram / provider 调用是否必须真实联网？ | 否，可使用 mock transport / fake client 替身 | 023 核心是本地真实组件联通，而非第三方网络稳定性 |
