# Research - Feature 051 Gap Matrix

## 当前对比结论

说明：本矩阵记录的是 051 启动时的基线差异。到 2026-03-14 收口完成时，1-4 已按实现关闭：session transcript 主链、agent-led recall、tool universe hints、behavior budget/truncation、compatibility fallback 收薄都已接回正式运行链。

### 1. Session / History

- **OctoAgent 当前实现**
  - `SessionContextState` 仍以 `task_ids / recent_turn_refs / recent_artifact_refs / rolling_summary` 为主
  - `RecentConversation` 仍需通过 `recent_turn_refs -> task events/artifacts` 重建
- **Agent Zero**
  - 主循环直接围绕 history 运行，tool result 也写回同一 history
- **OpenClaw**
  - `initSessionState -> createAgentSession -> replaceMessages` 明确以 session transcript 为主
- **差距结论**
  - OctoAgent 仍是 session-backed reconstruction，不是 transcript-native session

### 2. Memory / Recall

- **OctoAgent 当前实现**
  - context 装配时固定做 `_search_memory_hits()`
  - Butler 拿到的是 `MemoryRuntime + MemoryRecallHints`
- **Agent Zero**
  - recall query 由模型基于当前 history 生成，再 search / post-filter
- **OpenClaw**
  - `memory_search / memory_get` 是正式工具面
- **差距结论**
  - OctoAgent 还没有 agent-led recall 主链

### 3. Tooling / Decision

- **OctoAgent 当前实现**
  - ButlerDecision preflight 看不到真实 mounted/blocked tools
  - 实际 tool selection 在 delegation / inline tooling 更后面的步骤
- **Agent Zero**
  - 同一主循环里直接 `process_tools()`
- **OpenClaw**
  - context assembly 时就把 tools 和 availability 拼进 session prompt
- **差距结论**
  - OctoAgent 仍然存在“先决策，再知道真实工具宇宙”的顺序问题

### 4. Behavior Files

- **OctoAgent 当前实现**
  - 已有 `AGENTS.md / USER.md / PROJECT.md / TOOLS.md`
  - 但没有显式 budget / truncation / user-local overlay
- **Agent Zero**
  - default / user / project 覆盖链更成熟
- **OpenClaw**
  - 有明确的文件字数预算与截断
- **差距结论**
  - OctoAgent 的文件化方向是对的，但还缺上下文预算治理

### 5. Orchestration

- **OctoAgent 当前实现**
  - durability、A2A、审计链明显更强
  - 但控制面仍替 Agent 做了较多 recall / routing / pre-decision
- **Agent Zero / OpenClaw**
  - 更偏 agent-native loop
- **差距结论**
  - OctoAgent 的真正缺口不是底座能力，而是“能力如何以 Agent-native 方式暴露给模型”

## 051 对应收口策略

1. 先补 `behavior budget + tool universe hints`
2. 再把 `AgentSession` 升级成 transcript-native 真相源
3. 然后接 `agent-led recall`
4. 最后继续收薄 compatibility fallback
