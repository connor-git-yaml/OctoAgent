---
feature_id: "051"
title: "Session-Native Agent Runtime & Recall Loop"
milestone: "M4"
status: "Implemented"
created: "2026-03-14"
updated: "2026-03-14"
research_mode: "full"
blueprint_ref: "docs/blueprint.md §2 Constitution；docs/blueprint.md M3/M4 carry-forward；docs/agent-runtime-refactor-plan.md；Feature 033（context continuity）、038（memory recall optimization）、039（supervisor worker governance）、041（runtime readiness）、049（behavior workspace）；Agent Zero unified history/tool loop；OpenClaw session/context assembly"
predecessor: "Feature 033、038、039、041、049"
parallel_dependency: "Feature 050 继续负责普通用户 Agents 管理中心；051 负责把运行时真正收口到 session-native / agent-led recall / tool-aware decision runtime。"
---

# Feature Specification: Session-Native Agent Runtime & Recall Loop

**Feature Branch**: `codex/051-session-native-agent-runtime`  
**Created**: 2026-03-14  
**Updated**: 2026-03-14  
**Status**: Implemented  
**Input**: 基于当前 OctoAgent 与 Agent Zero / OpenClaw 的源码级对比，继续把运行时从“有 durable 底座但仍偏 control-plane 预判”推进到“session-native transcript、agent-led memory recall、tool-aware decision runtime”。本轮不再满足于局部补丁，而是把 session、memory、tooling、behavior budget 和编排入口的职责重新收口。

## Problem Statement

051 启动时，049 虽然已经把默认行为从代码特判迁移到显式 behavior files 与 `ButlerDecision` contract，但运行时仍有五个结构性缺口：

1. **Session 还不是唯一真相源**
   `Butler` 的 recent conversation 仍然需要通过 `SessionContextState.recent_turn_refs -> task events/artifacts` 反推，而不是直接读取 transcript-native `AgentSession` 历史。

2. **Memory recall 仍主要由系统预取**
   当前上下文装配会在 prompt 前固定触发 `_search_memory_hits()`，然后把 recall 结果塞回 prompt。Butler 虽然已看到 `MemoryRuntime + MemoryRecallHints`，但 recall 的时机、query 和候选过滤仍然主要由系统替 Agent 做掉。

3. **ButlerDecision 看不到真实工具宇宙**
   预路由模型调用目前只看到 `BehaviorSystem + RuntimeHints + RecentConversation`。实际工具挂载发生在 delegation / inline tooling 路径更后面，因此 ButlerDecision 仍不知道“当前这轮实际能直接用哪些工具、哪些被 block、为什么 block”。

4. **Behavior files 没有预算 / 截断 / overlay 护栏**
   当前 behavior workspace 直接整文件读入并原样拼 prompt。随着 `AGENTS.md / USER.md / TOOLS.md` 继续演化，系统会重新滑回“上下文越来越大、但 agent 真正可用信息密度下降”的问题。

5. **Compatibility fallback 仍然过厚**
   天气、排优先级、推荐、比较等路径虽然已降级为 compatibility fallback，但依然承担真实产品分支。这与“优先提供上下文，而不是堆积硬策略”的宪法方向仍不完全一致。

因此，051 的目标不是再补几条 hint，而是：

> 把 OctoAgent 的 Butler/Worker 运行时真正改造成 session-native、tool-aware、agent-led recall 的主链，让控制面回归治理、审计和降级，而不是继续替 Agent 做大量先验判断。

## Product Goal

交付一条更接近 Agent Zero / OpenClaw 的运行时主链：

- `AgentSession` 成为 recent conversation、follow-up、compaction 与导出的唯一真相源
- Butler / Worker 都能基于正式 session transcript 和 summary 工作，而不是依赖 task/event 拼装
- `ButlerDecision` 在决策前看到真实 `tool universe`，知道哪些工具已挂载、哪些可请求、哪些不可用及原因
- Memory 主链升级为 `agent-led recall`：模型基于当前问题与 session 历史决定 recall，而不是系统固定预取
- behavior workspace 增加预算、截断、来源说明与 overlay contract，避免“文件越显式，上下文越失控”
- compatibility fallback 收缩为真正的 guardrail / compatibility path，而不是隐藏主路径

## Current Delivery Status

截至 2026-03-14，本 Feature 已完成以下阶段性交付：

- `BehaviorWorkspace` 已具备 budget / truncation / optional user-local overlay contract
- `ButlerDecision` 已看到真实 `ToolUniverseHints`
- `AgentSession` 已新增正式 `recent_transcript / rolling_summary` 字段；旧 `metadata.recent_transcript` 保留为兼容 shadow，`RecentConversation` 已优先读取正式字段
- 完成回合后的 `user/assistant` turn、context compaction 产出的 `rolling_summary / trimmed recent_transcript` 都会回写到 `AgentSession + SessionContextState`
- `AgentSessionTurn` 已成为正式 turn store：`user / assistant / tool_call / tool_result / context_summary` 会持久化到 `agent_session_turns`，`recent_transcript` 退化为 projection cache
- `session.export` 底层 payload 已开始携带 `SessionContextState + AgentSession` continuity 信息，不再只导出 task / event / artifact
- 控制面已新增 `session.new / session.reset` 正式动作；Web Chat 的 `开始新对话` 现在会调用服务端 action，下一次发送也会回写 `session.focus`，不再只是本地清状态
- Butler chat 默认改为 `agent-led hint-first` memory runtime：不再固定预取详细 recall，改为注入 `MemoryRuntime + MemoryRecallHints`
- 当 `AgentProfile.context_budget_policy.memory_recall.planner_enabled=true` 且 LLM 支持 recall planning phase 时，Butler 会先产出 `RecallPlan`，再按模型生成的 query 执行 plan-driven recall，并把 `RecallEvidenceBundle` 回注主 prompt；当 MemU backend 可用时，该执行面会优先复用 `MemoryService` 的高级 backend search path
- Butler 运行时已把 `ButlerDecision + RecallPlan` 收口为统一 `ButlerLoopPlan`：同一轮模型规划同时产出 decision 与 recall plan；当 decision 为 `direct_answer` 时，recall plan 会以前置 `precomputed_recall_plan` 注入主调用，避免再触发独立 recall planner phase
- MemU recall 执行面已升级为显式 backend contract：`MemorySearchOptions` 会通过 `command/http` bridge 下发 `expanded_queries / focus_terms / subject_hint / rerank_mode / post_filter_mode / min_keyword_overlap`
- Worker runtime 继续保留 `detailed_prefetch`，避免专业 worker 退化
- compatibility fallback 已显式收薄：仅保留天气补问恢复、天气缺地点边界提示、parse failure / migration path；排优先级、推荐、比较等产品场景已退出硬编码 tree
- `AgentSessionTurn` 现在还会经过正式 replay/sanitize 投影：session replay 会从 turn store 重建最近对话，修复 tool call/result pairing、丢弃孤立 tool call、保留孤立 tool result 的可读摘要，并把结果统一注入 `SessionReplay` system block 与 `RecentConversation`
- 默认 general Butler 路径已切到 `single_loop_executor`：主调用会直接进入 `LLM + SkillRunner` 工具循环，不再额外触发 `ButlerDecision` 或 `memory_recall_planning` 辅助 phase；`ButlerDecision/ButlerLoopPlan` 保留给 compatibility / explicit delegation / legacy preflight 场景

截至当前，051 的原始主缺口已全部收口；后续如果继续演进，只属于可选增强，例如 provider-native tool-role transcript replay 或更激进的多 runtime 统一 loop。

## Design Direction

### 051-A Session-Native Transcript

- `AgentSession` 必须正式承载：
  - transcript messages
  - tool turns / tool result summary
  - rolling summary / compaction state
  - last active work / A2A conversation refs
- `SessionContextState` 不再只保留 `task_ids / recent_turn_refs` 这种“从别处反查”的弱引用模型
- `RecentConversation`、follow-up 恢复、export/reset/new conversation 统一从 `AgentSession` 读取

### 051-B Tool-Aware Decision Runtime

- ButlerDecision preflight 必须看到真实 `ToolUniverseHints`
- `ToolUniverseHints` 至少包含：
  - mounted tools
  - blocked tools
  - availability reasons
  - current tool profile
  - delegated vs inline execution boundary
- 决策模型应尽量基于真实挂载事实做判断，而不是只靠 `can_delegate_research=True/False`

### 051-C Agent-Led Memory Recall

- 默认路径从“系统先 recall，再把结果塞给 Agent”迁移到：
  1. 模型结合 `user_text + session transcript + memory runtime` 决定是否 recall
  2. 生成 recall query / recall scope
  3. 调用 `memory.search / memory.read / memu retrieve` 等工具
  4. 将 evidence 和 provenance 回写本轮上下文
- 控制面只保留 namespace、审计、budget 和降级策略

### 051-D Behavior Budget & Overlay

- behavior files 引入字符预算和截断规则
- 运行时必须显式暴露：
  - effective source chain
  - original length / effective length
  - truncation flag / truncation reason
- overlay 至少支持：
  - built-in defaults
  - system files
  - project files
  - 可选 user-local overlay

### 051-E Fallback Thin Guardrails

- `compatibility_fallback` 只保留：
  - 安全护栏
  - loop guard
  - 明显 schema/parse failure 的降级
  - 旧会话迁移兼容
- 天气链路只保留“缺地点不能假装已查到正确城市”和“补地点后恢复 follow-up”这类边界/恢复语义
- 推荐、排期、比较等产品场景不应继续扩张为 fallback tree

## Scope Alignment

### In Scope

- `AgentSession` transcript / summary / tool turn 真相源建模
- `ToolUniverseHints` contract 与 ButlerDecision prompt 接线
- agent-led recall runtime 主链与 Memory/MemU 工具入口设计
- behavior workspace 的 budget / truncation / overlay contract
- compatibility fallback 的收缩策略
- Butler / Worker session continuity 与 recent conversation 读取方式重构

### Out of Scope

- 重做整个 Web IA 或普通用户设置导航
- 把所有 worker runtime 一次性替换成新对象并删除全部旧兼容层
- 在本 Feature 内完成完整的 persona marketplace
- 把 governance / approval / audit / memory arbitration 下放给 md 文件或 Agent 自治

## User Scenarios & Testing

### User Story 1 - follow-up 真正依赖 session 历史，而不是脆弱 heuristic (Priority: P1)

作为用户，我希望第二轮、第三轮补充信息时，Butler 能基于真实会话上下文理解我在接什么，而不是重新猜分类或误判输入。

**Independent Test**: 同一会话里连续发“今天天气怎么样” -> “深圳” -> “那明天呢”，系统能仅依赖 `AgentSession` transcript/summaries 恢复上下文，不再依赖 task events 拼接。

### User Story 2 - Butler 在决策前知道这轮到底有哪些工具可用 (Priority: P1)

作为用户，我希望 Butler 在说“我去查”或“我得委派”之前，先基于本轮真实可用工具判断，而不是先做一个与实际挂载脱节的预判。

**Independent Test**: 在 `web.search` 可用、不可用、被 policy block 三种场景下，ButlerDecision 都能给出不同且可解释的决策。

### User Story 3 - Memory recall 由 Agent 主导，而不是系统固定预取 (Priority: P1)

作为系统设计者，我希望 recall 变成 Agent 自主动作，这样 MemU 的 summarize / rerank / expanding 能自然接入主链。

**Independent Test**: 面对同样的问题，Butler 可以根据 session/history 决定要不要 recall、用什么 query、拿回哪些 evidence，而不是永远由 `_search_memory_hits()` 预先决定。

### User Story 4 - 行为文件不会无限膨胀并污染 prompt (Priority: P2)

作为维护者，我希望 `AGENTS.md / USER.md / PROJECT.md / TOOLS.md` 可以继续显式化，但不会因为越来越长而重新把系统拖回低密度 prompt。

**Independent Test**: 人为写一个超长 `TOOLS.md`，运行时会显示截断、保留头部关键信息和 provenance，而不是整份注入 prompt。

## Edge Cases

- 旧 task/event 仍需兼容导出和回放，但不应再是 recent conversation 主路径
- `ButlerDecision` 模型不支持或返回非法 JSON 时，系统必须能回退，但 fallback provenance 要显式可见
- MemU 或 memory backend 不可用时，agent-led recall 需要降级到本地 recall / no-recall，而不是卡死主链
- behavior files 缺失、超长、编码异常时，必须有 deterministic fallback 与 budget 记录

## Functional Requirements

- **FR-001**: 系统 MUST 将 `AgentSession` 升级为 transcript-native 持久对象，recent conversation / follow-up 恢复 MUST 优先读取该对象，而不是继续主要依赖 `task_id -> events/artifacts` 反查。
- **FR-002**: `SessionContextState` SHOULD 从“弱引用集合”转向“session state snapshot + transcript summary + refs”模式，不得继续只存 `recent_turn_refs` 并把大部分语义外包给 task history。
- **FR-003**: `ButlerDecision` prompt MUST 注入真实 `ToolUniverseHints`，包括 mounted tools、blocked tools、availability reasons、current tool profile 与 delegated/inline 边界。
- **FR-004**: 系统 MUST 为 `ToolUniverseHints` 提供 provenance，能在 request metadata 或 artifact 中审计“决策时 Agent 看到的工具宇宙是什么”。
- **FR-005**: Memory 主链 MUST 支持 agent-led recall contract，至少包含 query synthesis、scope selection、tool invocation 与 evidence/provenance 回写。
- **FR-006**: 控制面 MAY 继续提供 hint-first memory runtime，但 MUST NOT 再把固定预取作为唯一 recall 入口。
- **FR-007**: behavior workspace MUST 引入字符预算、截断策略和 source-chain metadata，并在 UI/CLI/control plane 中可查看 effective length / truncation status。
- **FR-008**: 系统 SHOULD 支持 `default -> system -> project -> optional user-local` 的 behavior overlay chain；即使 user-local 暂未开放 UI，也必须有明确 contract。
- **FR-009**: 兼容性 fallback MUST 收缩为 guardrail / parse-failure / migration path，不得继续承担大量产品场景判断。
- **FR-010**: Butler / Worker runtime 在 session、memory、tooling 三个维度 MUST 继续保留审计与 durable lineage，不得因为更靠近 Agent Zero / OpenClaw 而牺牲 OctoAgent 的治理主链。
- **FR-011**: 系统 MUST 从 `agent_session_turns` 构建正式 replay/sanitize 投影，至少完成 turn 去重、tool call/result pairing 修复、orphan tool call 清理与 replay provenance 暴露。
- **FR-012**: 默认 general Butler 主路径 MUST 支持单循环执行：主模型调用直接带着已挂载工具进入工具循环，不得再强制附带独立 `ButlerDecision` 或 `memory_recall_planning` 辅助 phase。

## Key Entities

- **AgentSessionTranscript**: `AgentSession` 的正式消息历史与压缩摘要，替代 `recent_turn_refs -> task history` 弱链路。
- **ToolUniverseHints**: 决策前注入给 Butler 的真实工具宇宙快照。
- **RecallPlan**: Agent 生成的 recall 查询计划，包含 query、scope、budget 与 fallback 策略。
- **RecallEvidenceBundle**: recall 后真正取回的 evidence/provenance 集合。
- **BehaviorBudget**: behavior files 的预算、截断和来源链元信息。

## Success Criteria

- **SC-001**: Butler follow-up 相关逻辑优先由 `AgentSession` transcript 支撑，`RecentConversation` 不再主要依赖 task/event 反推。
- **SC-002**: ButlerDecision request artifact 中能看到真实工具宇宙，而不是只有抽象 `can_delegate_research` 之类的薄 hints。
- **SC-003**: Memory 主链至少在 Butler 或 Worker 其一实现 agent-led recall，并通过测试证明 recall query 不再完全由系统固定生成；Butler 直接回答路径下不再额外触发独立 recall planner phase。
- **SC-004**: behavior files 超预算时，prompt 注入、control plane 视图和测试都能显式看到截断状态。
- **SC-005**: compatibility fallback tree 明显收薄，并在源码和测试层都有可见证据。
- **SC-006**: 默认 general Butler 请求在定向回归中只发生一次主模型调用，不再生成 `butler-decision-request/response` 与 `memory-recall-plan-request/response` 辅助 artifact。
