---
feature_id: "051"
title: "Session-Native Agent Runtime & Recall Loop"
created: "2026-03-14"
updated: "2026-03-14"
status: "Implemented"
---

# Plan - Feature 051

## 1. 目标

把当前 OctoAgent 的运行时从“有 durable 能力，但仍偏 control-plane 预判”推进到：

- session-native transcript
- tool-aware ButlerDecision
- agent-led memory recall
- behavior budget / truncation / overlay
- thin compatibility fallback

## 2. 非目标

- 不一次性删空所有旧 task/event 兼容层
- 不在本轮重做整个前端导航和 Agents 页面
- 不下放 governance、approval、audit、memory arbitration
- 不用 md 文件替代 runtime truth / durable store

## 3. 差异矩阵

### 3.1 当前 OctoAgent vs Agent Zero / OpenClaw

1. **Session 真相源**
   - OctoAgent：`SessionContextState + task/event reconstruction`
   - Agent Zero：history-native main loop
   - OpenClaw：session store + transcript replacement
   - 改造结论：优先补 transcript-native `AgentSession`

2. **Memory 主链**
   - OctoAgent：system-prefetch-led
   - Agent Zero：agent/query-led recall extension
   - OpenClaw：memory_search / memory_get first-class tools
   - 改造结论：recall 触发与 query 生成交还给 Agent

3. **Tool 决策上下文**
   - OctoAgent：preflight 看不到真实 mounted tools
   - Agent Zero：同一循环里直接 process_tools
   - OpenClaw：session prompt 前已装配 tools + availability
   - 改造结论：把 `ToolUniverseHints` 接到 ButlerDecision

4. **Behavior files**
   - OctoAgent：已有显式文件，但无 budget / truncation / user-local overlay
   - Agent Zero：多层 overlay
   - OpenClaw：有 bootstrap max chars 与 file truncation
   - 改造结论：先补预算和 overlay contract

5. **Fallback 厚度**
   - OctoAgent：compatibility fallback 仍承担部分产品逻辑
   - Agent Zero / OpenClaw：主路径更依赖统一 loop/context
   - 改造结论：把 fallback 收缩成 guardrail

## 4. 设计原则

### 4.1 Session 优先于 Task Reconstruction

recent conversation、follow-up、summary、export/reset 优先从 `AgentSession` 读取。

### 4.2 工具真相优先于抽象能力标签

ButlerDecision 必须先知道本轮真实 tool universe，再决定 direct / ask / delegate。

### 4.3 Recall 是 Agent 行为，不是系统预取

Memory runtime 提供入口、预算和 provenance；是否 recall、怎么 recall 由 Agent 决定。

### 4.4 文件显式化必须伴随预算

显式行为文件不是“整份塞进 prompt”的许可证，必须有 budget / truncation / provenance。

### 4.5 兼容层要薄

fallback 只做护栏和兼容，不再替代产品主路径。

## 5. 实施切片

### Slice A - Behavior Budget + Tool Universe Hints

目标：

- 为 behavior workspace 增加 budget / truncation
- 为 ButlerDecision 增加真实 tool universe hints

原因：

- 实现面小、风险可控
- 直接补上当前最明显的 OpenClaw 差距

### Slice B - Session-Native Recent Conversation

目标：

- `AgentSession` 正式记录 transcript turn
- `RecentConversation` 不再通过 task/event 重建

### Slice C - Agent-Led Recall Runtime

目标：

- 定义 recall plan / evidence bundle
- 先在 Butler 或 Worker 的一条主路径上接通 agent-led recall

### Slice D - Fallback Thinning

目标：

- 收缩 `decide_butler_decision()` 的 compatibility tree
- 把产品逻辑迁回模型决策 + context

### Slice E - Acceptance & Docs

目标：

- 补 session / tooling / recall 的 acceptance matrix
- 回写 blueprint / README / feature docs

## 6. 风险

- 如果 session transcript 双写策略没设计好，会破坏现有 continuity
- 如果 tool universe hints 与实际挂载不一致，会制造新的“假上下文”
- 如果 recall 直接切太猛，可能让当前 memory 路径短期回退
- 如果 budget 截断不透明，用户会觉得行为文件“写了但不生效”

## 7. 验证方式

- behavior budget 单测
- ButlerDecision request artifact / metadata 测试
- session transcript continuity 回归
- agent-led recall 合同测试
- 与 Agent Zero / OpenClaw 差异复核

## 8. 本轮实施策略（2026-03-14）

本轮先执行 Slice A，并推进 Slice B 的 session-first 读链：

1. 为 `BehaviorWorkspace` 增加 budget / truncation 元数据与渲染规则
2. 为 `ButlerDecision` 注入真实 `ToolUniverseHints`
3. 让 `RecentConversation` 优先读取 `AgentSession` transcript cache，task/event reconstruction 降级为 fallback
4. 跑定向回归并重新评估剩余差距

当前进度（2026-03-14）：

- Slice A 已完成：`BehaviorWorkspace` 已具备 budget / truncation / optional user-local overlay，`ToolUniverseHints` 已进入 ButlerDecision preflight
- Slice B 已完成：`AgentSession` 除了正式 `recent_transcript / rolling_summary` 外，现已补齐 `AgentSessionTurn` store；`user / assistant / tool_call / tool_result / context_summary` 会写入 `agent_session_turns`，`RecentConversation`、`session.export`、`session.reset` 都优先消费该 store
- Slice C 已完成：Butler chat 默认切到 `agent-led hint-first` memory runtime；在 `planner_enabled` profile 下，`ButlerDecision + RecallPlan` 已收口成统一 `ButlerLoopPlan`，direct-answer 路径会把 recall 计划以前置 `precomputed_recall_plan` 注入主调用，避免再额外触发独立 recall planner phase；当 MemU backend 可用时，执行面继续通过 `MemorySearchOptions` contract 下发 `expanded_queries / focus_terms / rerank_mode / post_filter_mode`；Worker 保留 `detailed_prefetch`
- Slice D 已完成：compatibility fallback 已收缩为 guardrail / parse failure / migration path，只保留天气缺地点边界与天气 follow-up 恢复语义
- Slice E 已完成：定向回归、文档、blueprint 已同步；`AgentSessionTurn` replay/sanitize 投影与默认 general Butler 单循环执行器已接入主链

当前状态：

1. transcript replay/sanitize 已正式从 `agent_session_turns` 重建，并统一供 `SessionReplay` / `RecentConversation` / recall planning 使用。
2. 默认 general Butler 已进入单循环主执行器：主模型调用直接带着已挂载工具运行，`ButlerDecision` 与 `RecallPlan` 辅助调用只保留给 compatibility / explicit delegation 路径。
