---
feature_id: "064"
title: "并行工具调用 + Subagent 编排增强"
milestone: "M4"
status: draft
created: "2026-03-19"
updated: "2026-03-19"
priority: P0-P2
research_mode: skip
research_skip_reason: "前置深度调研已完成（四系统源码级对比：Claude Code / OpenClaw / Agent Zero / OctoAgent）"
blueprint_ref: "docs/blueprint.md §5 FR-AG, §8.6 A2A, §8.7 Skill Pipeline; Constitution #2 #3 #7 #8 #11"
predecessor: "Feature 059（Subagent 生命周期 CRUD）、Feature 061（Permission Preset + Deferred Tools）、Feature 062（Adaptive Loop Guard & Resource Limits）"
research_ref: "064-parallel-tool-subagent-orchestration/research/prior-research-summary.md"
---

# Feature 064: 并行工具调用 + Subagent 编排增强

> **⚠️ Status: 已退役（F087 followup 清理，2026-05-01）**
>
> 本 spec 描述的 in-process `SubagentExecutor` 路径（`apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py`）已被 Feature 084+ 的 `task_runner` 路径替代，原代码已作为孤悬死代码清理删除（净删 ~2k 行）。
>
> **当前生产派子任务路径**：`delegate_task` tool → `DelegationManager.delegate` (gate) → `_launch_child` → `pack_service._launch_child_task` → `task_runner.launch_child_task`。
>
> 本目录文档保留作为**历史决策证据**，不用于指导新工作。新人请阅读 `docs/codebase-architecture/e2e-testing.md` 与 `apps/gateway/src/octoagent/gateway/services/task_runner.py`。

**Feature Branch**: `feat/064-parallel-tool-subagent-orchestration`
**Created**: 2026-03-19
**Updated**: 2026-03-19
**Status**: 已退役（旧 Status: Draft）
**Input**: 前置深度调研（Claude Code / OpenClaw / Agent Zero / OctoAgent 四系统源码级对比），覆盖并行工具调用、Subagent 独立执行循环、上下文压缩、后台执行通知等六个子系统。

---

## 1. 概述

### 问题背景

OctoAgent 当前的 SkillRunner 和 Subagent 子系统存在以下结构性问题：

| # | 问题 | 影响 |
|---|------|------|
| 1 | **工具调用串行执行**：`_execute_tool_calls()` 使用 `for call in tool_calls` 串行循环 | 多个 READ_ONLY 工具（如文件搜索 + 内存检索）无法并行，增加 Agent 响应延迟 |
| 2 | **工具结果回填格式错误**：`LiteLLMSkillClient` 将工具结果以自然语言摘要回填（`"Tool execution results:\n- tool: output"`），而非标准 tool role message | LLM 无法准确关联工具调用与返回值，推理质量下降 |
| 3 | **Subagent 无独立执行循环**：Feature 059 只实现了 CRUD（spawn/kill/list），无独立 SkillRunner 实例 | Subagent 无法自主运行、无法做子任务分解、无法进度上报 |
| 4 | **Subagent 完成后无通知机制**：父 Worker 不知道 Subagent 何时完成、结果是什么 | 无法实现 spawn-then-continue 的异步编排模式 |
| 5 | **无上下文压缩**：对话历史无限增长直到触及上下文窗口限制 | 长任务后期 LLM 推理质量下降，可能触发 API 400 错误 |
| 6 | **后台任务无通知**：Task 状态变化时渠道端（Telegram/Web）无主动推送 | 用户必须手动刷新才能知道任务是否完成或需要审批 |

### 设计理念

本 Feature 遵循 OctoAgent 的核心架构原则：

- **Constitution #2 (Everything is an Event)**：所有新增操作（并行批次、Subagent 生命周期、上下文压缩）都通过事件记录
- **Constitution #3 (Tools are Contracts)**：并行调度基于工具声明的 `side_effect_level` 做出安全分桶决策
- **Constitution #7 (User-in-Control)**：DESTRUCTIVE 工具自动触发审批，Subagent 行为可取消
- **Constitution #8 (Observability)**：每个子系统产出可查询的事件流
- **Constitution #11 (Context Hygiene)**：上下文压缩保持对话历史精简
- **Constitution #14 (A2A 协议兼容)**：Subagent 通信复用已有 A2A 消息类型

---

## 2. 目标与非目标

### 目标

- **G1**：SkillRunner 支持并行执行 READ_ONLY 工具调用，减少多工具场景延迟
- **G2**：修复工具结果回填格式，使用标准 tool role message 提升 LLM 推理质量
- **G3**：赋予 Subagent 独立执行能力（独立 SkillRunner + Child Task + A2A 通信）
- **G4**：实现 Subagent 完成后向父 Worker 的 Push-based 通知机制
- **G5**：实现对话上下文压缩，避免长任务触及上下文窗口限制
- **G6**：后台任务状态变化时主动推送通知到 Telegram/Web 渠道

### 非目标

- **NG1**：不实现跨进程/跨节点的分布式 Subagent 调度（当前为单进程 asyncio 模型）
- **NG2**：不实现 Subagent 之间的直接通信（P2P），所有通信经由父 Worker 中转
- **NG3**：不实现 Agent 自动选择并行/串行策略（由 `side_effect_level` 静态分桶决定）
- **NG4**：不引入外部消息队列或编排器（如 Temporal/Celery），保持 SQLite Event Store + asyncio 方案
- **NG5**：不修改 A2A 协议模型定义（复用现有 6 种消息类型）

---

## 3. User Stories

### US-1: 并行工具调用加速（P0）

作为 Agent 使用者，当我发出一个需要查阅多个文件/搜索多个数据源的请求时，我希望系统能同时执行这些只读查询，而不是逐个串行等待，以减少响应延迟。

**Why this priority**: 直接影响用户体感延迟，且改动范围小（仅 SkillRunner 内部）、风险低。

**Independent Test**: 发送一个触发 3 个 READ_ONLY 工具调用的请求，验证总耗时接近单个最慢调用而非三者之和。

**Acceptance Scenarios**:

1. **Given** LLM 单次返回 3 个 tool_calls 且全部为 `side_effect_level=none` 工具，**When** SkillRunner 执行工具调用，**Then** 3 个调用通过 `asyncio.gather()` 并行执行，总耗时接近最慢单个调用
2. **Given** LLM 单次返回 2 个 tool_calls 中有 1 个为 `side_effect_level=reversible`，**When** SkillRunner 执行工具调用，**Then** READ_ONLY 工具先并行执行完毕，WRITE 工具随后串行执行
3. **Given** LLM 单次返回 1 个 `side_effect_level=irreversible` 工具，**When** SkillRunner 执行工具调用，**Then** 该调用触发 WAITING_APPROVAL 流程（复用 Feature 061 审批机制）

---

### US-2: 工具结果准确回填（P0）

作为 Agent 使用者，我希望工具执行结果以标准格式回填给 LLM，使其能精确关联每个工具调用和对应的结果，而不是通过自然语言猜测。

**Why this priority**: 直接影响 LLM 推理质量，修复已知 bug，且改动集中在 LiteLLMSkillClient。

**Independent Test**: 执行多轮工具调用循环，验证 LLM 对话历史中工具结果使用 `tool` role message 而非 `user` role 自然语言摘要。

**Acceptance Scenarios**:

1. **Given** SkillRunner 完成一轮 tool_calls 执行，**When** `LiteLLMSkillClient.generate()` 在下一步构建对话历史，**Then** 工具结果以 OpenAI `tool` role message 格式回填（含 `tool_call_id`），而非 `user` role 自然语言
2. **Given** 使用 Responses API 路径且工具调用结果需要回填，**When** 构建历史消息，**Then** 使用 `function_call_output` type 回填（含 `call_id`）
3. **Given** 工具返回错误结果（`is_error=True`），**When** 回填到对话历史，**Then** 错误信息仍通过标准 tool role message 回填，而非自然语言拼接

---

### US-3: Subagent 独立执行（P1）

作为 Agent 使用者，我希望 Worker 能派发子任务给 Subagent，Subagent 拥有独立的执行循环和上下文，能自主完成分配的工作并上报进度。

**Why this priority**: 是构建多 Agent 协作的基础能力，但依赖 P0 工具调用层的稳定性。

**Independent Test**: Worker 通过 `subagents.spawn` 创建一个 Subagent，分配一个简单任务，验证 Subagent 能独立完成并产出 Child Task 事件流。

**Acceptance Scenarios**:

1. **Given** Worker 调用 `subagents.spawn` 工具并附带任务描述，**When** Subagent 被创建，**Then** 系统创建 Child Task（`parent_task_id` 指向父 Task）、创建 A2AConversation、发送 A2A TASK 消息
2. **Given** Subagent 正在执行任务，**When** 每完成一步，**Then** 发射 TASK_HEARTBEAT 事件到 Child Task，包含进度摘要
3. **Given** Subagent 完成任务，**When** 执行循环结束，**Then** 发送 A2A RESULT 消息，Child Task 状态流转到 SUCCEEDED
4. **Given** 父 Worker 调用 `subagents.cancel` 工具，**When** Subagent 正在执行中，**Then** 发送 A2A CANCEL 消息，Subagent 在下一个 checkpoint 优雅停止

---

### US-4: Subagent 结果通知（P1）

作为 Agent 使用者，当 Subagent 完成工作后，我希望结果自动注入父 Worker 的对话中，而不需要父 Worker 轮询查询。

**Why this priority**: 与 US-3 配合构成完整的 Subagent 编排闭环。

**Independent Test**: Subagent 完成后验证父 Task 收到 A2A_MESSAGE_RECEIVED 事件，SSE 订阅者同时收到子和父的事件通知。

**Acceptance Scenarios**:

1. **Given** Subagent 完成任务并发送 A2A RESULT 消息，**When** Orchestrator 接收到该消息，**Then** 写入 A2A_MESSAGE_RECEIVED 事件到父 Task（事件冒泡）
2. **Given** SSE Hub 有订阅者分别监听父 Task 和 Child Task，**When** Subagent 完成，**Then** SSE Hub 同时向两个 task_id 广播事件
3. **Given** 父 Worker 正在等待 Subagent 结果，**When** 结果到达，**Then** Orchestrator 将结果摘要注入父 Worker 的对话历史，触发下一轮 SkillRunner 循环

---

### US-5: 上下文压缩（P2）

作为 Agent 使用者，我希望长任务运行过程中对话上下文不会无限增长，系统能自动压缩历史消息以保持推理质量。

**Why this priority**: 对长任务运行质量有重要影响，但当前可通过 UsageLimits 的 token 限制间接缓解。

**Independent Test**: 在对话历史超过阈值后触发上下文压缩，验证压缩后的对话仍能正常继续推理。

**Acceptance Scenarios**:

1. **Given** SkillRunner 对话历史 token 数接近上下文窗口阈值（如 80%），**When** 进入下一轮循环前，**Then** 自动触发 compaction 策略
2. **Given** 压缩触发，**When** 对话历史中存在大工具输出（> 2000 字符），**Then** 优先截断大工具输出为摘要
3. **Given** 截断大输出后仍超限，**When** 进一步压缩，**Then** 对早期对话轮次生成摘要替换原文
4. **Given** 压缩完成，**When** 查看事件流，**Then** 系统已发射 CONTEXT_COMPACTION_COMPLETED 事件，包含压缩前后 token 数

---

### US-6: 后台任务通知（P2）

作为 Agent 使用者，当后台任务完成或需要审批时，我希望通过 Telegram 或 Web 即时收到通知，而不用反复手动检查。

**Why this priority**: 提升用户体验但不影响核心执行能力。

**Independent Test**: Task 从 RUNNING 变为 SUCCEEDED 后验证 Telegram 用户收到通知消息。

**Acceptance Scenarios**:

1. **Given** Task 从 RUNNING 流转到终态（SUCCEEDED/FAILED/CANCELLED），**When** 状态变更事件被写入，**Then** 系统通过已配置的渠道推送通知
2. **Given** Task 进入 WAITING_APPROVAL 状态，**When** 状态变更事件被写入，**Then** Telegram 渠道发送审批请求消息（含 inline keyboard：批准/拒绝）
3. **Given** Task 执行中定期发射 TASK_HEARTBEAT 事件，**When** SSE Hub 接收到心跳事件，**Then** Web UI 实时更新进度显示

---

### Edge Cases

- 并行工具调用中某个工具超时或失败时，其他已成功的结果如何处理？
- Subagent spawn 后父 Worker 进程崩溃时，Subagent 如何感知并优雅终止？
- 上下文压缩时正在执行的工具调用引用了被压缩掉的历史信息怎么办？
- 多个 Subagent 同时完成时，结果注入父 Worker 对话的顺序是否需要保证？
- Telegram 通知在网络不可用时如何重试？是否需要去重？

---

## 4. 功能需求 (FR)

### P0-A: 并行工具调用

| FR | 描述 | 验收标准 |
|----|------|---------|
| FR-064-01 | SkillRunner `_execute_tool_calls()` 解析到多个 `tool_calls` 时，按 `SideEffectLevel` 分桶执行 | 单元测试验证分桶逻辑：`none` → 并行组，`reversible` → 串行组，`irreversible` → 审批组 |
| FR-064-02 | `SideEffectLevel.NONE` 工具使用 `asyncio.gather()` 并行执行，返回顺序与输入顺序一致 | 3 个 READ_ONLY 工具并行执行，总耗时 < 最慢单个调用 × 1.2 |
| FR-064-03 | `SideEffectLevel.REVERSIBLE` 工具按原始顺序串行执行，保证写操作顺序 | 2 个 WRITE 工具按序执行，第一个完成后再开始第二个 |
| FR-064-04 | `SideEffectLevel.IRREVERSIBLE` 工具串行执行，并在执行前触发 WAITING_APPROVAL 流程（复用 Feature 061 PresetBeforeHook） | DESTRUCTIVE 工具触发审批，审批通过后执行 |
| FR-064-05 | 新增 `TOOL_BATCH_STARTED` / `TOOL_BATCH_COMPLETED` EventType 包裹并行批次 | 事件 payload 包含 batch_id、tool_names 列表、execution_mode（parallel/serial） |
| FR-064-06 | 每个工具仍独立发射 `TOOL_CALL_STARTED` / `TOOL_CALL_COMPLETED` 事件（在 batch 事件之内） | 事件流结构：BATCH_STARTED → [TOOL_STARTED/COMPLETED × N] → BATCH_COMPLETED |
| FR-064-07 | 并行组中某个工具失败时，已完成的工具结果正常保留，失败工具返回错误反馈 | 3 个并行工具中 1 个失败 → 2 个正常结果 + 1 个错误反馈一起返回 |
| FR-064-08 | 分桶执行顺序为：先并行执行所有 READ_ONLY → 再串行执行所有 WRITE → 最后审批执行 DESTRUCTIVE | 混合 tool_calls 中 READ_ONLY 优先并行完成 |

### P0-B: 修复工具结果回填格式

| FR | 描述 | 验收标准 |
|----|------|---------|
| FR-064-09 | `LiteLLMSkillClient` Chat Completions 路径：工具调用时追加 `assistant` message 含 `tool_calls` 数组（标准 OpenAI function call 格式），工具结果使用 `tool` role message 回填（含 `tool_call_id`） | 对话历史中 tool_calls 和 tool results 使用标准格式，不再使用 `user` role 自然语言拼接 |
| FR-064-10 | `LiteLLMSkillClient` Responses API 路径：工具调用时追加 `function_call` item，工具结果使用 `function_call_output` item 回填（含 `call_id`） | 对话历史中工具结果使用 `function_call_output` type，不再使用 `user` role 自然语言 |
| FR-064-11 | `ToolCallSpec` 模型扩展 `tool_call_id: str` 字段，由 LLM 返回的 `id` 填充，用于结果回填关联 | ToolCallSpec 携带 tool_call_id，SkillRunner 在回填时传递该 ID |
| FR-064-12 | 向后兼容：`ToolCallSpec.tool_call_id` 默认为空字符串，未提供 ID 时回退到当前自然语言拼接模式 | 已有的非 function call 模式不受影响 |

### P1-A: Subagent 独立执行循环

| FR | 描述 | 验收标准 |
|----|------|---------|
| FR-064-13 | `subagents.spawn` 工具扩展：接受 `task_description` 参数，创建 Child Task（`parent_task_id` 指向父 Task）和独立的 SkillRunner 实例 | Child Task 与父 Task 通过 `parent_task_id` 关联 |
| FR-064-14 | Subagent spawn 时创建 `A2AConversation` 记录（`source=parent Worker URI`, `target=subagent URI`），发送 A2A TASK 消息 | A2AConversation 持久化在 A2A Store 中 |
| FR-064-15 | Subagent SkillRunner 在独立的 asyncio.Task 中运行（共享 event loop，独立上下文） | Subagent 执行不阻塞父 Worker 的主循环 |
| FR-064-16 | Subagent 继承父 Worker 的 `permission_preset`（Constitution #5: Least Privilege） | Subagent 权限不高于父 Worker |
| FR-064-17 | Subagent 支持 HEARTBEAT 进度上报：SkillRunner 每 N 步（可配置，默认 5 步）发射 TASK_HEARTBEAT 事件到 Child Task | 心跳事件包含 loop_step、summary |
| FR-064-18 | 支持 CANCEL 取消：父 Worker 发送 A2A CANCEL 消息 → Subagent 在下一个 step 检查取消标志并优雅停止 | Subagent Child Task 状态流转到 CANCELLED |
| FR-064-19 | 支持 UPDATE(input-required)：Subagent 需要额外信息时发送 A2A UPDATE 消息（state=input-required） → 父 Worker 对话中注入输入请求 | 父 Worker 收到 input-required 事件后 Orchestrator 注入对话 |
| FR-064-20 | Subagent 异常退出时自动清理：Child Task 状态流转到 FAILED，发送 A2A ERROR 消息 | 未捕获异常不会导致 Subagent 成为孤儿进程 |

### P1-B: Subagent Announce 机制（Push-based 通知）

| FR | 描述 | 验收标准 |
|----|------|---------|
| FR-064-21 | Subagent 完成后发送 A2A RESULT 消息到 A2AConversation | A2A RESULT 消息包含 summary、artifacts、terminal state |
| FR-064-22 | Orchestrator 接收 A2A RESULT 后写入 `A2A_MESSAGE_RECEIVED` 事件到父 Task（事件冒泡） | 父 Task 事件流中包含子 Subagent 的完成事件 |
| FR-064-23 | SSE Hub `broadcast()` 扩展：Subagent 生命周期事件同时广播到 Child Task 和父 Task 的 task_id | SSE 订阅者订阅父 Task 即可看到子 Subagent 的事件 |
| FR-064-24 | Orchestrator 将 Subagent 结果摘要注入父 Worker 的对话历史，触发下一轮 SkillRunner `generate()` | 父 Worker 自动恢复处理（无需人工干预） |
| FR-064-25 | 支持多 Subagent 并行：父 Worker spawn 多个 Subagent 后继续自己的工作，各 Subagent 结果按到达顺序注入 | 父 Worker 不会等待单个 Subagent 而阻塞 |

### P2-A: 上下文压缩

| FR | 描述 | 验收标准 |
|----|------|---------|
| FR-064-26 | `LiteLLMSkillClient` 在每轮 `generate()` 调用前检测对话历史 token 数（使用 tiktoken 或字符估算） | token 计数方法可配置，默认使用字符数 / 4 近似 |
| FR-064-27 | 当 token 数达到上下文窗口阈值（可配置，默认 80%）时触发 compaction | 阈值可通过 SkillManifest 或 execution_context 配置 |
| FR-064-28 | 三级压缩策略依序执行：(1) 截断 > 2000 字符的工具输出为摘要 → (2) 将早期对话轮次（保留最近 N 轮）替换为 LLM 生成的摘要 → (3) 丢弃最老的摘要块 | 每级压缩后重新检测 token 数，满足阈值则停止 |
| FR-064-29 | 压缩完成后发射 `CONTEXT_COMPACTION_COMPLETED` 事件（EventType 已定义但未实现） | 事件 payload 包含 before_tokens、after_tokens、strategy_used |
| FR-064-30 | 摘要生成使用快速/便宜模型（可配置 `compaction_model_alias`，默认使用主模型） | 避免摘要生成本身消耗过多成本 |
| FR-064-31 | system prompt 和最近一轮对话永远不被压缩 | 压缩后 system prompt 完整保留，最近一轮 user/assistant 消息完整保留 |

### P2-B: 后台执行与通知

| FR | 描述 | 验收标准 |
|----|------|---------|
| FR-064-32 | Task 从 RUNNING 流转到终态时，通过已注册的 Notification Channel 推送通知 | Web SSE 订阅者和 Telegram 用户均收到通知 |
| FR-064-33 | Task 进入 WAITING_APPROVAL 时，Telegram 渠道发送审批请求消息，包含 inline keyboard（批准/拒绝按钮） | 用户可直接在 Telegram 中完成审批操作 |
| FR-064-34 | TASK_HEARTBEAT 事件作为进度上报，Web UI 实时展示 Task 执行进度 | 心跳事件包含 loop_step、summary、预估完成度 |
| FR-064-35 | Notification Channel 协议定义：`notify(task_id, event_type, payload)` 异步接口 | 支持注册多个 channel（Telegram + Web SSE），channel 不可用时降级（Constitution #6） |
| FR-064-36 | 通知去重：同一 Task 同一终态只通知一次（基于 event_id 幂等） | 重复事件不会导致重复通知 |

---

## 5. 非功能需求 (NFR)

| NFR | 描述 | 指标 |
|-----|------|------|
| NFR-064-01 | 并行工具调用不应引入额外的事件写入延迟 | TOOL_BATCH 事件写入 p99 < 5ms |
| NFR-064-02 | 向后兼容：单个 tool_call 场景行为与改动前完全一致 | 现有 Skill 和 SKILL.md 配置零修改 |
| NFR-064-03 | Subagent 独立执行循环的内存开销可控 | 单个 Subagent SkillRunner 实例内存增量 < 10MB |
| NFR-064-04 | 上下文压缩的摘要生成不应显著增加 step 延迟 | 压缩操作 p95 < 3 秒（使用快速模型时） |
| NFR-064-05 | Telegram 通知延迟可接受 | 从终态事件写入到 Telegram 消息送达 < 5 秒 |
| NFR-064-06 | 并行工具调用中的异常隔离 | 单个工具超时/崩溃不影响同批次其他工具的结果收集 |
| NFR-064-07 | Subagent 清理的可靠性 | 进程崩溃后重启时，孤儿 Subagent 的 Child Task 自动流转到 FAILED 终态 |
| NFR-064-08 | 工具结果回填格式变更不破坏现有 LLM 推理 | Chat Completions 和 Responses API 两条路径均通过集成测试 |

---

## 6. 成功标准

### 定量指标

| SC | 描述 | 度量方式 |
|----|------|---------|
| SC-064-01 | 3 个 READ_ONLY 工具并行执行总耗时 < 最慢单个调用 × 1.5 | 集成测试计时 |
| SC-064-02 | 工具结果回填后 LLM 多轮工具调用的成功率 > 当前水平 | A/B 对比测试 |
| SC-064-03 | Subagent 可在 60 秒内完成 spawn → execute → result 全流程（简单任务） | 集成测试计时 |
| SC-064-04 | 上下文压缩后对话可正常继续推理，无 API 400 错误 | 长对话压力测试 |
| SC-064-05 | Task 终态通知在 5 秒内送达 Telegram | 端到端延迟测量 |

### 定性指标

| SC | 描述 |
|----|------|
| SC-064-06 | 所有新增操作产生可查询的 Event Store 事件 |
| SC-064-07 | 已有的单工具调用场景行为不受影响（向后兼容） |
| SC-064-08 | Subagent 全生命周期（spawn → heartbeat → result/cancel/error）可在 Task Detail 页面查看 |

---

## 7. 依赖与约束

### 上游依赖

| 依赖 | 状态 | 说明 |
|------|------|------|
| Feature 059（Subagent CRUD） | 已完成 | 提供 `spawn_subagent`/`kill_subagent`/`list_active_subagents` 函数 |
| Feature 061（Permission Preset） | 已完成 | 提供 `PresetBeforeHook`、`ApprovalBridgeProtocol`、审批机制 |
| Feature 062（Resource Limits） | 已完成 | 提供 `UsageLimits`/`UsageTracker`、`SKILL_USAGE_REPORT` 事件 |
| A2A Protocol Models | 已完成 | 提供 `A2AMessage`、6 种消息类型、`A2AConversation` |
| SSE Hub | 已完成 | 提供 `subscribe(task_id)`/`broadcast(task_id, event)` |
| ToolBroker | 已完成 | 提供 `SideEffectLevel`、`execute()`、工具 schema 发现 |

### 关键源码文件

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `packages/skills/src/octoagent/skills/runner.py` | 修改 | P0-A: `_execute_tool_calls()` 并行分桶 |
| `packages/skills/src/octoagent/skills/litellm_client.py` | 修改 | P0-B: 工具结果回填格式修复；P2-A: 上下文压缩 |
| `packages/skills/src/octoagent/skills/models.py` | 修改 | P0-B: `ToolCallSpec` 扩展 `tool_call_id` |
| `packages/core/src/octoagent/core/models/enums.py` | 修改 | P0-A: 新增 `TOOL_BATCH_STARTED`/`TOOL_BATCH_COMPLETED` EventType |
| `apps/gateway/src/octoagent/gateway/services/subagent_lifecycle.py` | 修改 | P1-A: 扩展 spawn 逻辑含独立执行循环 |
| `apps/gateway/src/octoagent/gateway/services/orchestrator.py` | 修改 | P1-B: Subagent 结果注入父 Worker |
| `apps/gateway/src/octoagent/gateway/services/sse_hub.py` | 修改 | P1-B: 扩展 broadcast 支持父子 task_id 双路广播 |
| `packages/protocol/src/octoagent/protocol/models.py` | 不修改 | 复用现有 A2A 消息类型 |
| `packages/tooling/src/octoagent/tooling/broker.py` | 不修改 | 已有 `SideEffectLevel` 和事件发射逻辑 |

### 约束

- **单进程约束**：所有 Subagent 执行循环在同一进程的 asyncio event loop 中运行，不支持跨进程调度
- **上下文窗口约束**：上下文压缩策略受模型上下文窗口大小限制，需要按模型配置阈值
- **Telegram API 约束**：通知推送受 Telegram Bot API 的速率限制（约 30 msg/sec per bot）

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 并行工具调用中共享状态竞争 | 低 | 中 | READ_ONLY 工具按定义无副作用，分桶逻辑确保有副作用的工具串行执行；ToolBroker 的 Hook Chain 和 EventStore 已线程安全 |
| 工具结果回填格式变更导致 LLM 推理退化 | 中 | 高 | P0-B 保留向后兼容回退路径（`tool_call_id` 为空时回退到自然语言模式）；Responses API 路径需要特别注意 `call_id` 一致性 |
| Subagent 执行循环泄漏（忘记清理） | 中 | 中 | asyncio.Task 使用 `try/finally` 确保 `kill_subagent` 在异常退出时也被调用；Watchdog（Feature 011）可检测孤儿 Task |
| 上下文压缩丢失关键信息 | 中 | 高 | 保留 system prompt 和最近一轮不被压缩；摘要质量通过 LLM 生成保证；压缩前后 token 数记录在事件中便于追溯 |
| 多 Subagent 并行导致父 Worker 对话混乱 | 低 | 中 | 每个 Subagent 结果注入时标注来源（subagent_runtime_id + task 摘要），使 LLM 能区分不同子任务的结果 |
| Telegram 通知发送失败 | 低 | 低 | 遵循 Constitution #6 (Degrade Gracefully)：通知失败仅记录日志，不影响 Task 执行；Web SSE 作为备用通道 |

---

## Key Entities

### 新增 EventType

- `TOOL_BATCH_STARTED`：并行工具批次开始，payload 含 `batch_id`、`tool_names`、`execution_mode`
- `TOOL_BATCH_COMPLETED`：并行工具批次完成，payload 含 `batch_id`、`duration_ms`、`success_count`、`error_count`

### 扩展模型

- `ToolCallSpec`：新增 `tool_call_id: str = ""` 字段，由 LLM response 中的 function call ID 填充
- `SkillExecutionContext`：新增 `parent_task_id: str | None = None` 字段，用于 Subagent Child Task 关联
- `SkillManifest`（可选）：新增 `compaction_model_alias: str | None = None` 字段，指定上下文压缩使用的模型
- `SkillManifest`（可选）：新增 `heartbeat_interval_steps: int = 5` 字段，Subagent 心跳间隔

### Notification Channel Protocol

```
NotificationChannelProtocol:
    async notify(task_id: str, event_type: EventType, payload: dict) -> bool
```

---

## Implementation Notes

### P0-A 并行分桶算法

```
输入: tool_calls: list[ToolCallSpec]
输出: 按桶执行结果

1. 查询每个 tool 的 SideEffectLevel（从 ToolBroker registry）
2. 分桶:
   - bucket_read: side_effect_level == NONE
   - bucket_write: side_effect_level == REVERSIBLE
   - bucket_destructive: side_effect_level == IRREVERSIBLE
3. 执行顺序:
   a. asyncio.gather(*bucket_read)  # 并行
   b. for tool in bucket_write: execute(tool)  # 串行
   c. for tool in bucket_destructive: gate_then_execute(tool)  # 审批后串行
4. 合并结果，按原始 tool_calls 顺序返回
```

### P0-B 回填格式对齐

Chat Completions 路径：
- assistant message 携带 `tool_calls` 数组（标准 OpenAI format）
- 工具结果使用 `{"role": "tool", "tool_call_id": "xxx", "content": "result"}` 回填

Responses API 路径：
- assistant output 包含 `function_call` items
- 工具结果使用 `{"type": "function_call_output", "call_id": "xxx", "output": "result"}` 回填
