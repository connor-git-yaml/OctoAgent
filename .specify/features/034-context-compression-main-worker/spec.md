---
feature_id: "034"
title: "Main/Worker Context Compaction"
milestone: "M4"
status: "Implemented"
created: "2026-03-09"
updated: "2026-03-09"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §8.7.5 / §8.8 / §8.9.1 / §14 M4；docs/m3-feature-split.md §7 M4 Backlog；Agent Zero history compression 相关源码"
predecessor: "Feature 020 / Feature 028 / Feature 030 / Feature 031"
---

# Feature Specification: Main/Worker Context Compaction

**Feature Branch**: `034-context-compression-main-worker`  
**Created**: 2026-03-09  
**Updated**: 2026-03-09  
**Status**: Implemented  
**Input**: 把 Agent Zero 的“小模型帮助大模型压缩上下文”能力真正接入 OctoAgent，只落在主 Agent 和 Worker，不接到 Subagent。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/online-research.md`、`research/research-synthesis.md`

## Problem Statement

当前仓库在 Feature 034 之前存在三个实质缺口：

1. `TaskService.process_task_with_llm()` 真正送给模型的仍然只有最新 `user_text`，主 Agent / Worker 没有稳定的多轮历史重建。
2. `USER_MESSAGE` 事件只持久化预览文本，导致续对话、压缩、导出和 operator 面都拿不到完整用户输入。
3. `before_compaction_flush()` 已在 Memory Core 中预留，但主运行链没有任何“cheap/summarizer 压缩 -> artifact/evidence -> memory flush”的真实接线。

Agent Zero 的经验说明，上下文压缩不能只是一个脱离主循环的 util 函数，而要嵌入真正的 prompt assembly 路径中；同时它需要 cheap/utility model、最近轮次保留、旧历史压缩和失败时的 graceful degradation。

本 Feature 的目标不是做一套新的 Memory Console，也不是给 Subagent 增加另一层复杂度，而是把“主 Agent / Worker 的真实上下文窗口治理”做成可用、可审计、可回灌 Memory 的产品能力。

## Product Goal

交付一个真正可用的上下文压缩基线：

- 主 Agent 和 Worker 在每次主模型调用前，都从事件链和 artifact 重建真实多轮上下文
- 当上下文接近预算时，使用 `summarizer` alias 压缩旧历史，保留最近轮次原文
- 压缩结果生成 artifact、事件和 memory flush evidence chain，而不是静默发生
- `summarizer` 不可用或输出空摘要时，系统自动退回原始历史，不得悄悄丢轮次
- Subagent 明确不接入这条机制，避免额外嵌套压缩复杂度

## Architecture Boundary

### 运行链位置

- canonical 入口：`TaskService.process_task_with_llm()`
- 主 Agent 路径：chat/task routes -> `TaskRunner` -> `TaskService`
- Worker 路径：`WorkerRuntime` -> `TaskService`
- Subagent 路径：通过 `dispatch_metadata.target_kind=subagent` 或 `worker_capability=subagent` 明确绕过

### 压缩职责

- `ContextCompactionService` 负责：
  - 从 `USER_MESSAGE` + `MODEL_CALL_COMPLETED.artifact_ref` 重建对话轮次
  - 估算输入 token
  - 在超预算时调用 `summarizer` alias 压缩旧历史
  - 生成主模型实际请求上下文和快照文本

### Memory 接缝

- compaction 只能通过 `MemoryService.run_memory_maintenance(FLUSH)` 回灌
- evidence 只能引用 request snapshot artifact 和 compaction summary artifact
- 不允许旁路写 SoR；任何长期事实仍由 020/028 的治理模型处理

### 非目标

- 不实现 Subagent 上下文压缩
- 不实现新的 Memory Console UI、Session Center UI 或 Runtime Console 页面
- 不在本 Feature 内做独立 scheduler/background compaction worker

---

## User Scenarios & Testing

### User Story 1 - 主 Agent 续对话时真的能带上历史，并在超预算时压缩旧历史 (Priority: P1)

作为用户，我希望我在同一个 task/thread 下继续追问时，系统真的能记住上一轮对话；当上下文过长时，它应该压缩旧内容而不是直接忘掉。

**Why this priority**: 这是主 Agent 可用性的底线。如果它每次只看最新一句，用户会直接感知为“没有记忆”。

**Independent Test**: 通过 chat route 连续发送两轮消息，确认第二次主模型请求包含第一轮 user/assistant 历史；再构造长历史，确认 summarizer 被触发且主模型拿到“压缩摘要 + 最近轮次”。

**Acceptance Scenarios**:

1. **Given** 同一 task 下已有上一轮 user/assistant 对话，**When** 用户继续发送消息，**Then** 主模型请求中必须包含之前的轮次，而不是只包含最新输入。
2. **Given** 历史 token 超过压缩阈值且轮次足够，**When** 进入下一次主模型调用，**Then** 系统必须调用 `summarizer` alias 压缩旧历史，并保留最近轮次原文。

---

### User Story 2 - Worker 共享同一条压缩链，但 Subagent 明确绕过 (Priority: P1)

作为系统 owner，我希望 Worker 和主 Agent 复用同一套上下文压缩机制，但 Subagent 不要再叠一层压缩，避免运行链复杂化和调试困难。

**Why this priority**: Feature 034 的落点明确是 main agent + worker；如果 Subagent 也默认接入，会把 delegation plane 的行为边界搅乱。

**Independent Test**: 让 `WorkerRuntime` 处理长上下文 task，验证普通 worker 路径会复用压缩能力；再用 `target_kind=subagent` 调度，验证不会触发 summarizer。

**Acceptance Scenarios**:

1. **Given** Worker 处理长上下文任务，**When** 进入主模型调用，**Then** 它应走与主 Agent 相同的 context assembly / compaction 逻辑。
2. **Given** dispatch target 是 `subagent`，**When** 进入运行链，**Then** 系统不得触发 `summarizer` 压缩。

---

### User Story 3 - 压缩行为必须可审计，并回灌 Memory flush 钩子 (Priority: P1)

作为系统 owner，我希望每次上下文压缩都能留下 artifact、事件和 memory flush 记录，这样后续才能在 console 或审计链中解释“压缩了什么、为什么压缩、是否进入记忆系统”。

**Why this priority**: 如果压缩是黑盒，后续用户只会看到回答质量变化，却无法追踪原因。

**Independent Test**: 触发一次 compaction，确认生成 request snapshot artifact、summary artifact、`CONTEXT_COMPACTION_COMPLETED` 事件和 `memory_maintenance_runs` / `memory_fragments`。

**Acceptance Scenarios**:

1. **Given** 发生上下文压缩，**When** 请求继续执行，**Then** 系统必须写入 `llm-request-context` artifact 和 `context-compaction-summary` artifact。
2. **Given** 压缩已完成，**When** 读取事件和 Memory 审计表，**Then** 必须能看到 `CONTEXT_COMPACTION_COMPLETED` 与对应 `memory_flush_run_id` / evidence refs。

---

### User Story 4 - summarizer 不可用时要优雅退回，而不是弄丢历史或直接失败 (Priority: P1)

作为系统 owner，我希望 cheap/summarizer 模型出问题时，系统仍然能继续调用主模型；它可以放弃压缩，但不能把旧历史默默裁掉，更不能因为压缩失败导致整次任务失败。

**Why this priority**: 这直接对应 Constitution 6；没有 graceful degradation，这套能力在生产里会成为额外故障源。

**Independent Test**: 模拟 `summarizer` 抛错，验证主模型请求仍带完整历史，且不产生伪造的 compaction event。

**Acceptance Scenarios**:

1. **Given** `summarizer` 调用失败，**When** 系统准备主模型请求，**Then** 应退回原始历史上下文，而不是只保留最近轮次。
2. **Given** 压缩失败，**When** 检查事件链，**Then** 不得写入伪造的 `CONTEXT_COMPACTION_COMPLETED`。

## Edge Cases

- 老任务只有 `text_preview` 没有 `payload.text` 时，必须继续能恢复历史，但精度允许退化到 preview。
- `summarizer` 返回空字符串时，系统必须视为压缩失败并退回原始历史，不能把旧轮次静默丢掉。
- `MemoryService.run_memory_maintenance(FLUSH)` 失败时，请求仍应继续，压缩事件可缺少 `memory_flush_run_id`，但主模型调用不能被拖死。
- Subagent 由主 Agent 委派执行时，即便上下文很长，也必须明确绕过这套压缩，以保持 delegation 语义可预测。

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 在 `USER_MESSAGE` payload 中持久化完整 `text`，不能只保存 `text_preview`。
- **FR-002**: `TaskService.process_task_with_llm()` MUST 在主模型调用前，从 task 事件和 artifact 重建多轮上下文。
- **FR-003**: 对话重建 MUST 同时包含历史 `USER_MESSAGE` 与 `MODEL_CALL_COMPLETED.artifact_ref` 对应的 assistant 内容。
- **FR-004**: 主 Agent 和 Worker MUST 共用同一条 context assembly / compaction 逻辑。
- **FR-005**: Subagent MUST NOT 接入本 Feature；当 `dispatch_metadata.target_kind=subagent` 或 `worker_capability=subagent` 时，系统必须跳过 compaction。
- **FR-006**: 当估算输入 token 超过阈值且轮次达到最小门槛时，系统 MUST 调用 `summarizer` alias 压缩旧历史。
- **FR-007**: 压缩策略 MUST 保留最近轮次原文，只压缩较旧轮次。
- **FR-008**: 每次主模型调用 MUST 生成 `llm-request-context` artifact，记录实际发送给主模型的上下文快照。
- **FR-009**: 当 compaction 成功发生时，系统 MUST 生成 `context-compaction-summary` artifact。
- **FR-010**: 当 compaction 成功发生时，系统 MUST 写入 `CONTEXT_COMPACTION_COMPLETED` 事件，至少包含压缩前后 token、压缩/保留轮次、artifact refs 和可选 `memory_flush_run_id`。
- **FR-011**: compaction 回灌 Memory 时 MUST 通过 `MemoryMaintenanceCommand(kind=FLUSH)` 进入治理路径，不得直接写 SoR。
- **FR-012**: flush evidence MUST 至少包含 compaction summary artifact 和 request snapshot artifact。
- **FR-013**: `summarizer` 调用失败或返回空摘要时，系统 MUST 退回原始历史，不得静默丢轮次。
- **FR-014**: `summarizer` 降级路径 MUST NOT 生成伪造的 `CONTEXT_COMPACTION_COMPLETED` 事件。
- **FR-015**: control-plane / operator 侧在读取最新用户消息时 SHOULD 优先使用 `payload.text`，缺失时再回退到 `text_preview`。
- **FR-016**: 本 Feature MUST 通过真实运行链被用户使用；不得只在独立 helper 或假接口中实现。
- **FR-017**: 验证矩阵 MUST 覆盖 chat 续对话、多轮压缩、worker 复用、subagent 绕过、summarizer 失败降级和 memory flush 审计。

### Key Entities

- **CompiledTaskContext**: 主模型实际请求上下文，包含消息序列、快照文本、token 统计和压缩元数据。
- **ConversationTurn**: 从事件和 artifact 重建出来的 user/assistant 轮次。
- **ContextCompactionCompletedPayload**: 上下文压缩完成事件的结构化 payload。
- **llm-request-context Artifact**: 每次主模型请求的实际上下文快照。
- **context-compaction-summary Artifact**: summarizer 生成的历史压缩摘要。

## Success Criteria

### Measurable Outcomes

- **SC-001**: 在同一 task/thread 下继续对话时，第二次主模型请求能够带上之前的 user/assistant 历史，而不是只包含最新一句。
- **SC-002**: 长历史场景下，系统能真实触发 `summarizer` alias，并在事件链中记录压缩前后 token 与 artifact refs。
- **SC-003**: Worker 路径复用同一套 compaction 逻辑，Subagent 路径不触发 summarizer。
- **SC-004**: compaction 成功后，`memory_maintenance_runs` 与 `memory_fragments` 至少留下 1 条可审计记录。
- **SC-005**: `summarizer` 失败时，请求仍能继续，且最终主模型输入保留完整历史，不发生静默轮次丢失。

## Clarifications

### Session 2026-03-09

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 压缩能力应落在主 Agent、Worker、还是所有 agent？ | 主 Agent + Worker | 用户明确要求 Subagent 不需要 |
| 2 | 压缩应接在 helper 还是真实运行链？ | 真实运行链 | 用户明确要求“不要假装实现” |
| 3 | 压缩结果是否允许直接写 SoR？ | 否 | blueprint 已规定只能经 `before_compaction_flush()` / governance 路径进入记忆体系 |
| 4 | summarizer 失败时是否允许整次请求失败？ | 否 | 对齐 Constitution 6，压缩要 graceful degradation |
| 5 | Agent Zero 的后台异步压缩是否原样照搬？ | 否，改为 inline prompt assembly | OctoAgent 当前是 per-request `TaskService` 模式，不是同构 loop extension 模式 |

