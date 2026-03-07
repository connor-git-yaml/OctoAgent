---
feature_id: "019"
title: "Interactive Execution Console + Durable Input Resume"
milestone: "M2"
status: "Implemented"
created: "2026-03-07"
research_mode: "tech-only"
blueprint_ref: "docs/blueprint.md §5.1.6 / §8.8 / §12.1.3 / §14"
predecessor: "Feature 008/009/010/011/018 已交付控制平面、Worker Runtime、恢复、可观测与协议基线"
parallel_dependency: "Feature 017 将消费交互控制入口；Feature 023 将消费 019 的执行面和事件链"
---

# Feature Specification: Interactive Execution Console + Durable Input Resume

**Feature Branch**: `codex/feat-019-jobrunner-interactive-console`
**Created**: 2026-03-07
**Status**: Implemented
**Input**: 基于 `docs/m2-feature-split.md` 的 Feature 019，把当前 Worker Runtime 从“后台跑完即可”推进到“可观察、可交互、可取消、可恢复”的执行面。
**调研基础**: `research/tech-research.md`

---

## Problem Statement

当前代码库已经具备：

- `TaskRunner` 的持久化队列和重启恢复；
- `WorkerRuntime` 的 backend 选择、超时与取消骨架；
- `TaskService` 的事件/Artifact/Checkpoint 写入；
- `SSE` 与任务详情接口。

但执行面仍有三个关键空洞：

1. `WorkerRuntime` 只有 backend 选择语义，没有正式的 execution session / interactive console contract；
2. 长任务没有正式的 `ExecutionConsoleSession` / `ExecutionStreamEvent` 模型，前后端只能从零散事件里猜执行状态；
3. 人工输入无法在同一事件链中被审计，也没有最小权限 gate，导致后续 017/023 无法稳定消费。

Feature 019 的目标不是一次性做完整远程执行平台，而是在**不新增持久化表**的前提下，把现有 task/event/artifact/task_job 基线升级成一个可投影、可恢复、可交互的 execution 控制平面。

---

## User Scenarios & Testing

### User Story 1 - 操作者能看到统一的执行控制台状态 (Priority: P1)

作为操作者，我希望每个长任务都能暴露统一的执行控制台会话和事件流，这样我能知道它在跑什么、当前步骤是什么、有没有新日志、是否已经结束。

**Why this priority**: 如果没有统一控制台模型，日志、步骤、产物、取消状态仍然散落在各类事件里，019 的“可观察执行面”目标就不成立。

**Independent Test**: 构造一个运行中的任务，验证 `ExecutionConsoleSession` 可以稳定返回 backend、state、current_step、latest artifact、是否可取消/可输入；同时验证 SSE 流中出现结构化 execution stream 事件。

**Acceptance Scenarios**:

1. **Given** 任务已进入 worker 执行，**When** 查询 execution session，**Then** 返回值必须明确包含 `session_id`、`backend`、`state`、`current_step` 与最近 artifact。
2. **Given** worker 正在执行多步流程，**When** 写入状态/日志/步骤事件，**Then** 控制台事件流必须保持同一 `session_id` 且顺序可回放。
3. **Given** 任务执行结束，**When** 再次查询 execution session，**Then** 返回值必须进入终态并保留最后的摘要和产物指针。

---

### User Story 2 - 长任务可请求并接收人工输入 (Priority: P1)

作为操作者，我希望运行中的任务能够明确请求人工输入，并在我提交输入后继续执行，而不是只能取消后重来。

**Why this priority**: `attach_input` 是 blueprint 对长任务交互的明确要求，也是“交互式执行控制台”与普通后台任务的核心差异。

**Independent Test**: 使用一个会在执行过程中请求输入的测试 worker，验证任务会进入 `WAITING_INPUT`，提交输入后恢复为 `RUNNING`，并最终产出结果。

**Acceptance Scenarios**:

1. **Given** 执行中的任务请求人工输入，**When** 控制台进入等待态，**Then** task status 与 execution session 都必须明确反映 `WAITING_INPUT`。
2. **Given** 操作者提交了输入，**When** 输入被接纳，**Then** 系统必须把该动作写入同一任务事件链，并恢复执行。
3. **Given** 进程在等待输入阶段重启，**When** 操作者之后补交输入，**Then** 任务仍可基于持久化 task/task_job/artifact 状态恢复继续，而不是直接丢失。

---

### User Story 3 - 高风险输入必须走最小权限 gate 且全程可审计 (Priority: P2)

作为系统 owner，我希望需要人工输入的高风险执行能够复用现有审批链，这样输入本身不会绕过 Policy/Approval 体系。

**Why this priority**: blueprint 明确要求“人工输入、取消、重试都必须事件化并可回放”，而 Constitution 又要求 least privilege 和 user-in-control。

**Independent Test**: 构造一个需要审批的输入请求，验证系统会创建 approval，未经批准的输入被拒绝；批准后输入可被接纳并继续执行。

**Acceptance Scenarios**:

1. **Given** 高风险任务请求人工输入，**When** 系统发出输入请求，**Then** 必须生成可查询的 approval 记录，并在 execution stream 中暴露 `approval_id`。
2. **Given** approval 尚未被允许，**When** 直接调用 `attach_input`，**Then** 请求必须被拒绝且保留审计事件。
3. **Given** approval 已通过，**When** 提交输入，**Then** 输入动作必须与 approval、task、execution session 形成同一可回放链路。

---

### Edge Cases

- Docker 不可用但 `docker_mode=preferred` 时，执行面如何 graceful fallback 到 inline，同时保留统一控制台语义？
- 输入请求发生在进程重启前，且输入提交发生在重启后时，如何避免任务卡死在 `WAITING_INPUT`？
- 日志/输入文本超过 event payload 限制时，如何转存 Artifact 并在 stream 中只保留摘要与引用？
- 任务已被取消或进入终态后，再次 `attach_input` 时如何返回明确冲突，而不是静默忽略？
- 同一任务多次请求输入时，如何区分“最近一次仍待处理”的 request 与历史 request？

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 定义 `ExecutionConsoleSession`、`ExecutionStreamEvent` 与 `ExecutionRuntimeContext`，作为执行控制台的单一事实源。
- **FR-002**: 系统 MUST 保持 `WorkerRuntime` 的 `docker/inline` backend 选择与 graceful fallback，并通过统一 execution contract 暴露 backend 语义；本 Feature 不要求完整独立容器调度器。
- **FR-003**: 系统 MUST 定义 `EXECUTION_STATUS_CHANGED / LOG / STEP / INPUT_* / CANCEL_REQUESTED` 等 execution payload families，供 session projection 与 API 统一消费。
- **FR-004**: 系统 MUST 将 stdout/stderr、当前步骤、人工输入请求、人工输入接纳、取消请求、最终状态映射为可回放的 `EXECUTION_*` 事件，并可投影为统一 execution stream 视图。
- **FR-005**: 系统 MUST 将 execution stream 事件写入现有 task event 链，而不是只保存在内存会话。
- **FR-006**: 系统 MUST 在不新增持久化表的前提下，基于 `tasks`、`task_jobs`、`events`、`artifacts` 投影出 execution session 状态。
- **FR-007**: 当任务请求人工输入时，系统 MUST 将 task 状态推进为 `WAITING_INPUT`，并在输入接纳后恢复到 `RUNNING`。
- **FR-008**: 系统 MUST 支持运行中等待输入，以及等待输入后重启再补交输入两种路径。
- **FR-009**: 系统 MUST 将人工输入原文以 Artifact 或等价 durable 方式保存；事件 payload 仅保留预览与引用，避免直接内联敏感原文。
- **FR-010**: 当输入请求被标记为需要审批，或任务风险为 `HIGH` 时，系统 MUST 复用现有 `ApprovalManager` 生成审批记录，并在批准前拒绝 `attach_input`。
- **FR-011**: 系统 MUST 暴露 execution session 查询与输入提交 API，供后续 Web/Telegram 控制面直接消费。
- **FR-012**: 现有 `POST /api/tasks/{task_id}/cancel` MUST 继续生效，并能与 execution session 状态保持一致。
- **FR-013**: 任务进入 `WAITING_INPUT` 时，后台 job 状态 MUST 可持久化为非运行中等待态，避免重启恢复误判为 orphan failure。
- **FR-014**: execution 相关长文本 MUST 优先落 Artifact，并在 stream 中仅保留摘要与 `artifact_id` / metadata 引用。
- **FR-015**: Feature 019 SHOULD 通过 `ExecutionRuntimeContext` 或等价机制，为后续 Worker/LLM/Skill 实现暴露日志、步骤和输入 API，而不强绑具体 LLM 实现。

### Key Entities

- **ExecutionConsoleSession**: 由 task/task_job/event/artifact 投影出的控制台会话视图。
- **ExecutionStreamEvent**: 执行过程中的统一事件协议，承载状态、日志、步骤、输入与产物引用。
- **ExecutionRuntimeContext**: 运行中暴露给 worker/LLM/skill 的上下文对象，用于写日志、请求输入和读取恢复态输入。
- **HumanInputArtifact**: 人工输入的 durable 存储载体，避免敏感原文直接写入 event payload。

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: 查询 execution session 时，运行中、等待输入、成功、失败、取消五类状态都能返回稳定结构。
- **SC-002**: 一个请求输入的长任务可以完成“等待输入 -> 提交输入 -> 恢复执行 -> 成功结束”的闭环，并有自动化测试覆盖。
- **SC-003**: 任务在 `WAITING_INPUT` 阶段重启后，补交输入仍可恢复执行，不会被 startup recovery 误标为失败。
- **SC-004**: 高风险输入路径在未审批前拒绝输入，审批后允许输入，并在事件链中保留 approval 关联信息。
- **SC-005**: 现有 cancel、SSE、task detail、artifact 查询回归测试继续通过，不引入对现有 task/event schema 的破坏性变更。

---

## Clarifications

### Session 2026-03-07

| # | 问题 | 自动选择 | 理由 |
|---|---|---|---|
| 1 | 是否新增 execution 专用持久化表？ | 不新增 | 先基于现有 task/event/artifact/task_job 投影，压低回归与迁移成本 |
| 2 | 是否现在就做完整 Docker container orchestration？ | 先做可消费 backend 抽象与 runtime 接入，保留 docker label/入口与 graceful fallback | 当前仓库尚无独立 worker image/bootstrap 基础，先完成 019 的控制平面与交互语义 |
| 3 | 人工输入全文是否直接写 Event payload？ | 否 | 对齐 Constitution 最小化日志原则，全文落 Artifact，事件只保留 preview/ref |

---

## Scope Boundaries

### In Scope

- Execution console contract 与 gateway runtime 接入
- docker/inline backend 统一状态面
- ExecutionConsoleSession / ExecutionStreamEvent
- execution session 查询 + attach_input API
- WAITING_INPUT 生命周期、审批 gate、事件化审计
- 长任务/输入/取消/恢复测试

### Out of Scope

- 完整 Docker image 构建流水线
- SSH / remote_gpu backend
- Web/Telegram UI 交互界面本身
- 多 worker 并发调度策略重构
