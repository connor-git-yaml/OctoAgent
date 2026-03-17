---
feature_id: "060"
title: "Context Engineering Enhancement"
milestone: "M4"
status: "Draft"
created: "2026-03-17"
updated: "2026-03-17"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md §8.7.5 Context Compaction；§8.8 Memory；§14 Constitution #6 Degrade Gracefully"
predecessor: "Feature 034（Context Compression Main/Worker — 已实现基础二级压缩）"
---

# Feature Specification: Context Engineering Enhancement

**Feature Branch**: `codex/060-context-engineering-enhancement`
**Created**: 2026-03-17
**Updated**: 2026-03-17
**Status**: Draft
**Input**: 对标 Agent Zero 的多层历史压缩 + utility model 分工方案，升级 Feature 034 的基础二级压缩为产品级 Context Engineering 体系：全局 token 预算统一管理、分层历史结构、异步后台压缩、两阶段压缩策略、Worker 进度笔记、Settings 中 compaction model 可配置、中文 token 估算修正。

## Problem Statement

Feature 034 已建立基础的上下文压缩链路（token 预算 6000、soft limit 4500、summarizer alias 调用、降级保障），但对比 Agent Zero 的实现和行业最佳实践，以及对现有架构的深入审查，存在**四个结构性差距**和**三个架构缺陷**：

### 结构性差距（对标 Agent Zero）

1. **压缩粒度太粗**
   当前只有"保留最近 N 轮 + 压缩其余全部为一段摘要"的二级模式。当对话从 4 轮增长到 20+ 轮时，所有旧历史被压成一段文本，丢失了中间阶段的决策细节和话题边界。Agent Zero 的四层结构（Message → Topic → Bulk → Summary）按距离递进压缩，近期保真度高、远期只保留骨架，信息密度显著优于扁平摘要。

2. **压缩在请求路径上同步执行**
   `_build_task_context()` 中同步调用 summarizer，增加每次 LLM 请求的延迟。Agent Zero 在消息循环结束后后台启动压缩，下一轮开始时才按需等待，让压缩和用户思考时间重叠。

3. **只有 LLM 摘要一种压缩手段**
   对长工具输出、大 JSON 响应等结构化内容，直接调 LLM 做摘要既慢又贵。Agent Zero 先做"廉价压缩"（截断大消息、移除冗余结构），再对剩余内容用小模型摘要，成本降低一个量级。

4. **Worker 长任务缺少进度笔记**
   当 Worker 执行跨多轮的复杂任务时，上下文压缩会丢失中间步骤的执行细节。Agent Zero 通过 `agent.data` + `extras_persistent` 让 Agent 主动记录关键里程碑。OctoAgent 的 `AgentSession.metadata` 有类似基础设施，但没有 Worker 主动写 progress note 的协议和工具。

### 架构缺陷（现有代码审查发现）

5. **全局 token 预算断裂——压缩层与装配层各管一半**
   `ContextCompactionService` 在 `build_context()` 中只看到对话历史的 token 数（`max_input_tokens=6000`），不知道 `AgentContextService._fit_prompt_budget()` 会在之后追加 1500-3000 token 的系统块（AgentProfile ~80、OwnerProfile ~150、BehaviorSystem 400-1200、BehaviorToolGuide 200-400、SessionReplay 300-800、MemoryRecall 100-600）。压缩层把对话压到 4500 token 以内以为合格，但装配层又追加系统块后实际交付到模型的总量远超 6000。当前 `_fit_prompt_budget()` 用 ~240 种参数组合做暴力搜索来兜底，但这是一种**补偿性设计**而非**协同性设计**——压缩层应该在启动时就知道留给自己的实际预算是多少。

6. **Skill 注入游离于预算体系之外**
   `LLMService._build_loaded_skills_context()` 在 `_fit_prompt_budget()` 完成**之后**才把已加载的 SKILL.md 内容拼接到 system prompt（`llm_service.py:315-317`），每个 Skill 消耗 0-500 token 完全不在预算计算范围内。这不是"设计取舍"而是一个**预算漏洞**：当用户同时加载多个 Skill 时，实际交付 token 会超出预算上限，导致模型截断或响应质量下降。

7. **Token 估算对中文内容系统性偏低**
   `estimate_text_tokens()` 使用 `len(text) / 4`（`context_compaction.py:533-538`），这是英文的合理估算（~4 chars/token），但中文平均 1.5-2 chars/token，导致中文内容的 token 数被低估约 50%。在中文为主的对话中，实际 token 消耗远超预算预期。

## Product Goal

在 Feature 034 已有基础上，把上下文管理从"基础能用"升级为"产品级 Context Engineering"：

- **统一全局 token 预算**：让压缩层和装配层共享同一个预算视图，消除"压缩完了又追加系统块超预算"的断裂问题
- **将 Skill 注入纳入预算管理**：修复 `_build_loaded_skills_context()` 游离于 `_fit_prompt_budget()` 之外的预算漏洞
- **升级 token 估算**：引入中文感知的 token 估算，消除对中文内容系统性低估 ~50% 的问题
- 引入分层历史结构，让压缩粒度从二级变为三级（Recent / Compressed / Archive），明确与现有 SessionReplay 和 rolling_summary 的关系
- 两阶段压缩：先廉价截断大消息和工具输出，再用小模型做语义摘要
- 压缩从同步路径移到后台异步，减少请求延迟
- Settings 中新增 `compaction_model` 配置入口，支持用户指定专用压缩模型，未配置时 fallback 到 `summarizer` → `main`
- Worker 引入 progress note 协议，让 Agent 主动记录关键里程碑到 Artifact，上下文重置后可恢复

## User Scenarios & Testing

### User Story 0 - 全局 token 预算统一管理 (Priority: P0)

作为系统架构的一部分，压缩层和装配层 MUST 共享同一个 token 预算视图，消除当前"压缩层只看对话历史、装配层再追加 1500-3000 系统块、Skill 注入完全不计入"的三段断裂问题。

**Why this priority**: 这是所有后续优化的地基。不解决预算断裂，分层压缩、异步压缩都无法正确决策目标 token 数。当前 `_fit_prompt_budget()` 的 ~240 种组合暴力搜索是对这个缺陷的补偿性兜底，但在增加 Skill、Memory、进度笔记等更多注入源后，组合爆炸会让暴力搜索不可持续。

**Independent Test**: 在中文多轮对话 + 加载 2 个 Skill + 挂载 Memory 的场景下，验证实际交付给模型的 system + conversation 总 token 数不超过 `max_input_tokens`。

**Acceptance Scenarios**:

1. **Given** 系统启动上下文构建流程，**When** 压缩层（`ContextCompactionService`）决定对话压缩目标，**Then** 该目标基于 `max_input_tokens - system_overhead_tokens` 计算，而非固定使用 `max_input_tokens`。其中 `system_overhead_tokens` 包含：AgentProfile、OwnerProfile、BehaviorSystem、BehaviorToolGuide、已加载 Skill 内容估算、Memory 回忆预估。
2. **Given** 用户加载了 3 个 Skill（估算各 300 token），**When** 上下文构建完成，**Then** Skill 占用的 ~900 token 已被计入预算，实际交付 token 不超限。
3. **Given** 中文为主的对话，**When** token 估算运行，**Then** 使用中文感知的估算函数（而非 `len(text)/4`），估算误差 < 20%。

**架构决策**:

当前上下文构建涉及三个独立环节，各自管理自己的 token 消耗：

| 环节 | 当前行为 | 问题 |
|------|----------|------|
| `ContextCompactionService.build_context()` | 把对话压到 `max_input_tokens × soft_limit_ratio` 以内 | 不知道系统块会占多少 |
| `AgentContextService._fit_prompt_budget()` | 把 system_blocks + compressed_messages 暴力搜索到 6000 以内 | 用 ~240 种参数组合兜底，是补偿而非协同 |
| `LLMService._build_loaded_skills_context()` | 在预算计算之后追加 Skill 内容 | 完全游离于预算体系之外 |

**060 目标架构**: 引入 `ContextBudgetPlanner`——在上下文构建开始时统一规划各组成部分的 token 预算分配：

```
ContextBudgetPlanner.plan(max_input_tokens) -> BudgetAllocation:
  system_blocks_budget:  ~2000  (AgentProfile + Owner + Behavior + ToolGuide)
  skill_injection_budget: ~600  (基于已加载 Skill 数量预估)
  memory_recall_budget:   ~400  (基于 Memory 配置预估)
  conversation_budget:    ~3000 (max_input_tokens - 上述总和，传给压缩层)
```

压缩层只负责将对话压缩到 `conversation_budget` 以内，不再使用 `max_input_tokens` 作为自己的目标。装配层的 `_fit_prompt_budget()` 仍保留作为安全兜底（因为各组件的实际 token 可能偏离预估），但不再是正确性的主要保障。

---

### User Story 1 - 压缩模型可在 Settings 中独立配置 (Priority: P1)

作为平台管理员，我希望在 Settings 页面为上下文压缩指定一个轻量模型（如 haiku / gpt-4o-mini），让压缩成本与主模型解耦，且未配置时系统自动 fallback 不报错。

**Why this priority**: 压缩是高频操作，用主力模型做摘要既慢又贵；没有独立配置入口，用户无法优化成本。

**Independent Test**: 进入 Settings，设置 `compaction_model` 为 haiku，触发一次多轮对话压缩，验证 summarizer 调用走的是 haiku 而非 main；删除该配置后重试，验证 fallback 到 summarizer alias 或 main。

**Acceptance Scenarios**:

1. **Given** 用户进入 Settings 的 Model Aliases 区域，**When** 查看可配置别名列表，**Then** 能看到 `compaction` 语义别名及其当前绑定（默认显示"未配置，使用 summarizer"）。
2. **Given** 用户将 `compaction` 绑定到某个轻量模型并保存，**When** 下一次上下文压缩触发，**Then** 系统使用该模型调用摘要，而非 main 或 summarizer。
3. **Given** `compaction` 别名未配置，**When** 压缩触发，**Then** 系统按 `compaction → summarizer → main` 的 fallback 链解析，不报错。

---

### User Story 2 - 长对话中旧历史分层压缩而非全量摘要 (Priority: P1)

作为主 Agent 用户，我希望在 10+ 轮对话后，近期对话保持完整，中期历史只保留决策和结果，远期历史只保留一句话骨架——而不是全部旧历史被压成一段不分层的摘要。

**Why this priority**: 扁平摘要在长对话中信息密度急剧下降，模型容易"忘记"中期发生的关键转折。

**Independent Test**: 发送 15 轮对话，检查 control plane 的 `CONTEXT_COMPACTION_COMPLETED` 事件，验证产出包含 recent（原文）、compressed（保留决策的中期摘要）、archive（骨架摘要）三个层级。

**Acceptance Scenarios**:

1. **Given** 对话达到 8 轮且超过 soft limit，**When** 压缩触发，**Then** 最近 2 轮保持原文（Recent 层），中间轮次被压缩为保留决策的短摘要（Compressed 层），最早轮次被归档为骨架（Archive 层）。
2. **Given** 对话继续增长到 20 轮，**When** 再次压缩，**Then** Archive 层不会无限增长，旧 archive 条目会被递归合并，总 token 数维持在 `BudgetAllocation.conversation_budget × archive_ratio` 以内。
3. **Given** Compressed 层某个话题包含关键决策（如"用户确认了方案 B"），**When** 该话题被进一步压缩到 Archive，**Then** 决策要点仍保留在 Archive 摘要中。

**与现有 SessionReplay / rolling_summary 的关系**:

当前系统已有两个"历史压缩"机制，060 的分层结构需要明确与它们的边界：

| 现有机制 | 当前作用 | 060 后的定位 |
|----------|----------|--------------|
| `SessionReplay` | `_fit_prompt_budget()` 中作为系统块注入，提供 session 级别的对话摘要回放（dialogue_limit 从 8→6→4→3→None 五级修剪） | **保留但收窄职责**：SessionReplay 只提供"跨 session 的上下文连续性"（如上次 session 的最终状态）。当前 session 内的中期历史由 Compressed 层承担，不再依赖 SessionReplay 的 dialogue 级回放。 |
| `rolling_summary` | 每轮结束后累积更新（`record_response_context()`），注入为 RecentSummary 系统块（1200→800→400→0 四级修剪） | **升级为 Archive 层的持久化载体**：rolling_summary 的"累积摘要"语义与 Archive 层天然对齐。060 后 rolling_summary 存储的是 Archive 层内容，而非当前的"全部历史的扁平摘要"。Compressed 层的内容单独存储在 `AgentSession.metadata["compressed_layers"]`。 |

这个设计意味着 `_fit_prompt_budget()` 中的 `recent_summary` 参数语义不变（仍是 Archive 层摘要），但 SessionReplay 的修剪不再需要承担"当前 session 内中期历史"的职责——该职责由 Compressed 层消息直接承担。

---

### User Story 3 - 大消息先廉价截断再 LLM 摘要 (Priority: P1)

作为系统运维者，我希望工具输出、长 JSON 响应等结构化内容先被截断/精简，只在截断后仍超预算时才调用 LLM 摘要，从而降低压缩成本和延迟。

**Why this priority**: 工具输出通常占据大量 token 但信息密度低；直接用 LLM 摘要浪费算力。

**Independent Test**: 创建一个包含超大工具输出（>2000 token）的对话，触发压缩，验证该工具输出先被截断到阈值，且 LLM summarizer 调用次数少于全量摘要方案。

**Acceptance Scenarios**:

1. **Given** 某轮工具输出超过单条消息 token 上限（可配置，默认 `max_input_tokens × 0.3`），**When** 压缩触发，**Then** 该消息先被截断（保留头尾 + 中间省略标记），不调用 LLM。
2. **Given** 截断后整体 token 数仍超过 soft limit，**When** 系统进入 LLM 摘要阶段，**Then** 仅对截断后的内容做摘要，而非原始全文。
3. **Given** 某条消息是纯 JSON 结构且超大，**When** 截断触发，**Then** 系统识别 JSON 结构并保留关键字段（如 status、error、result），移除数组冗余项。

---

### User Story 4 - 压缩异步执行不阻塞请求 (Priority: P2)

作为用户，我希望压缩不增加我等待模型回复的时间。

**Why this priority**: 同步压缩每次增加 1-3 秒延迟；异步后可完全隐藏在用户思考时间内。

**Independent Test**: 在多轮对话中观察模型响应延迟，对比开启异步压缩前后的 p50/p95 延迟差异。

**Acceptance Scenarios**:

1. **Given** 上一轮对话结束，**When** 系统判断需要压缩，**Then** 压缩任务在后台启动，不阻塞当前轮次的返回。
2. **Given** 用户快速发送下一条消息，**When** 后台压缩尚未完成，**Then** 系统等待压缩完成后再构建上下文（同步 fallback），而不是使用未压缩的超大上下文。
3. **Given** 后台压缩任务失败，**When** 下一轮需要构建上下文，**Then** 系统回退到同步压缩路径，行为与 Feature 034 一致。

---

### User Story 5 - Worker 进度笔记 (Priority: P2)

作为 Worker 长任务的执行者，我希望在完成每个关键步骤后自动记录进度笔记，这样即使上下文被压缩或重置，我的后续步骤仍能从笔记中恢复。

**Why this priority**: 压缩会丢失中间步骤细节；没有进度笔记，Worker 在上下文重置后会重复已完成的工作或遗漏依赖。

**Independent Test**: 创建一个 Worker 长任务（5+ 步），中途触发上下文压缩，验证压缩后 Worker 通过读取进度笔记继续工作，不重复已完成步骤。

**Acceptance Scenarios**:

1. **Given** Worker 完成一个有意义的步骤（如"已创建文件 X"、"已调用 API 获得结果 Y"），**When** 该步骤完成，**Then** Worker 通过 `progress_note` 工具将里程碑写入 Artifact Store。
2. **Given** 上下文被压缩，**When** Worker 开始下一轮循环，**Then** 上下文构建时自动注入最近的进度笔记摘要，Worker 知道已完成什么、下一步该做什么。
3. **Given** Worker 因进程重启而丢失内存状态，**When** 重新加载任务，**Then** 进度笔记从 Artifact Store 恢复，Worker 能从断点继续。

**与 rolling_summary 的定位区分**:

| 机制 | 语义 | 写入时机 | 内容类型 |
|------|------|----------|----------|
| `rolling_summary`（→ Archive 层） | "发生了什么" | 每轮自动累积 | 对话历史的摘要压缩 |
| `progress_note` | "做了什么、下一步做什么" | Agent 主动调用 | 结构化里程碑（step_id + status + next_steps） |

两者互补而非替代：rolling_summary 由系统自动维护，记录对话层面的上下文；progress_note 由 Agent 主动写入，记录任务执行层面的进度。上下文注入时，Archive 层摘要和进度笔记注入到不同的系统块中，分别回答"之前聊了什么"和"任务进展到哪了"。

## Edge Cases

- 当 `compaction` alias 配置的模型不可用（provider down）时，系统 MUST fallback 到 `summarizer` → `main`，不得阻断主请求。
- 当对话只有 1-3 轮时，系统不应触发任何压缩，即使单条消息很大（通过 `min_turns_to_compact` 控制）。
- 当后台压缩任务超时（>10 秒）时，系统 MUST 回退到同步压缩或原始历史，不得让用户无限等待。
- 当进度笔记的 Artifact 数量累积超过阈值（如 50 条）时，系统 MUST 合并旧笔记，防止注入上下文时反而增加 token 消耗。
- 当 Subagent 使用上下文时，MUST 继续绕过所有压缩机制（保持 Feature 034 行为）。
- 当截断大消息时，如果消息的 metadata 包含 `important: true` 标志或消息角色为 approval/gate 相关（如审批决策），MUST 保留该内容不截断。MVP 阶段：仅识别 `metadata.important == true` 标志，不做自然语言"重要性"推断。
- 当 `ContextBudgetPlanner` 预估系统块开销后实际值偏差超过 20% 时，`_fit_prompt_budget()` 的暴力搜索兜底 MUST 仍能保证总 token 不超限（预估不准时的安全网）。
- 当用户同时加载 5+ 个 Skill 且 Skill 总内容 > 2000 token 时，系统 MUST 按 Skill 加载顺序截断超出预算部分，并在 control plane 记录被截断的 Skill 列表。
- 纯中文对话 vs 纯英文对话的 token 估算偏差 MUST < 30%（当前偏差约 100%）。混合语言内容按比例加权。

## Functional Requirements

### 全局 token 预算统一（Phase 0 — 地基）

- **FR-000**: 系统 MUST 引入 `ContextBudgetPlanner`，在上下文构建开始时计算各组成部分的 token 预算分配（system_blocks、skill_injection、memory_recall、conversation_history），保证各部分之和 ≤ `max_input_tokens`。
- **FR-000a**: `ContextBudgetPlanner` 计算 `conversation_budget` 时 MUST 扣除：系统块基础开销（AgentProfile + OwnerProfile + BehaviorSystem + BehaviorToolGuide 的预估 token 数）、已加载 Skill 内容的预估 token 数（基于 `SkillDiscovery` 缓存中各 Skill 的 content 长度）、Memory 回忆的预估 token 数（基于 `MemoryRetrievalProfile` 配置的 top_k 和平均 hit 长度）。
- **FR-000b**: `ContextCompactionService.build_context()` MUST 接受 `conversation_budget` 参数（由 `ContextBudgetPlanner` 提供），替代当前直接使用 `max_input_tokens` 的行为。
- **FR-000c**: `LLMService._build_loaded_skills_context()` 生成的 Skill 内容 MUST 在 `_fit_prompt_budget()` 之前或之内完成，纳入预算计算。具体方案：Skill 内容通过 `ContextBudgetPlanner` 预估并在 `_fit_prompt_budget()` 的 `_build_system_blocks()` 中作为系统块参与 token 计算，而非在之后追加。
- **FR-000d**: `_fit_prompt_budget()` MUST 继续保留作为安全兜底机制——当 `ContextBudgetPlanner` 预估与实际偏差 > 20% 时，暴力搜索仍能保证总 token ≤ `max_input_tokens`。

### Token 估算升级

- **FR-000e**: `estimate_text_tokens()` MUST 替换为中文感知版本。策略：检测文本中非 ASCII 字符比例，按比例在 `len(text)/4`（英文）和 `len(text)/1.5`（中文）之间加权插值。
- **FR-000f**: 当项目运行环境可用 `tiktoken`（或等价快速 tokenizer 库）时，`estimate_text_tokens()` SHOULD 升级为实际 tokenizer 计数，`len/N` 作为 fallback。

### 压缩模型配置

- **FR-001**: 系统 MUST 在 `AliasRegistry` 中新增 `compaction` 语义别名，category 为 `cheap`，默认不绑定具体模型。
- **FR-002**: `ContextCompactionService` 的模型解析链 MUST 为 `compaction → summarizer → main`；当前优先级的别名未配置或不可用时，自动降级到下一级。
- **FR-003**: Settings 前端 MUST 在 Model Aliases 区域展示 `compaction` 别名，标注用途为"上下文压缩（推荐轻量模型）"，并显示当前 fallback 链路。
- **FR-004**: `compaction` 别名的 fallback 行为 MUST 记录到 `CONTEXT_COMPACTION_COMPLETED` 事件中（`model_alias` 字段显示实际使用的模型）。

### 分层历史结构

- **FR-005**: 系统 MUST 将压缩后的上下文分为三层：Recent（最近 N 轮原文）、Compressed（中期话题摘要）、Archive（远期骨架摘要）。
- **FR-006**: 各层 token 预算分配 MUST 可配置，默认 Recent 50%、Compressed 30%、Archive 20%。注意：这里的百分比是 `conversation_budget`（由 `ContextBudgetPlanner` 分配）的分配，不是 `max_input_tokens` 的分配。
- **FR-007**: 当 Compressed 层超出配额时，最旧的 Compressed 条目 MUST 被递归合并到 Archive 层。
- **FR-008**: Archive 层 MUST 有大小上限（默认 `conversation_budget × archive_ratio`），超出时合并最旧条目。Archive 层的持久化载体为 `AgentSession.rolling_summary`（语义从"全部历史扁平摘要"升级为"Archive 层骨架摘要"）。
- **FR-009**: `CompiledTaskContext` MUST 扩展，增加 `layers` 字段描述各层的 token 占用和条目数，供 control plane 审计。
- **FR-009a**: SessionReplay 在当前 session 内 MUST 只提供"上次 session 最终状态的简要上下文"，当前 session 的中期历史由 Compressed 层消息承担。`_fit_prompt_budget()` 中 SessionReplay 的 dialogue_limit 修剪梯度（8→6→4→3→None）保留，但仅用于跨 session 场景。

### 两阶段压缩

- **FR-010**: 压缩 MUST 先执行"廉价截断"阶段：识别超大消息（>单条 token 阈值），截断保留头尾，不调用 LLM。
- **FR-011**: 廉价截断 MUST 对 JSON/结构化内容做智能精简（保留关键字段，移除数组冗余项），而非盲截断。
- **FR-012**: 只有廉价截断后仍超预算时，才进入"LLM 摘要"阶段，调用 compaction 模型做语义摘要。
- **FR-013**: 两阶段压缩的执行情况（截断了多少消息、摘要了多少消息）MUST 记录到压缩事件元数据中。

### 异步后台压缩

- **FR-014**: 系统 MUST 在每轮 LLM 调用完成后，在后台启动压缩任务（如果预判下一轮可能超限）。
- **FR-015**: 下一轮 `_build_task_context()` 时，如果后台压缩已完成，MUST 直接使用压缩结果；如果未完成，MUST 等待完成（最长等待时间可配置，默认 10 秒）。
- **FR-016**: 后台压缩超时或失败时，MUST 回退到同步压缩路径，行为与 Feature 034 一致。
- **FR-017**: 异步压缩的结果 MUST 通过 `AgentSession` 的 `rolling_summary`（Archive 层）和 `metadata["compressed_layers"]`（Compressed 层）持久化，进程重启后不丢失。

### Worker 进度笔记

- **FR-018**: 系统 MUST 定义 `progress_note` 工具，允许 Worker 在执行过程中记录结构化里程碑（包含 `step_id`、`description`、`status`、`key_decisions`、`next_steps`）。
- **FR-019**: 进度笔记 MUST 持久化到 Artifact Store（type: `progress-note`），绑定到当前 task_id 和 agent_session_id。
- **FR-020**: 上下文构建时，MUST 自动注入最近 N 条进度笔记的摘要到独立的 `ProgressNotes` 系统块中（N 可配置，默认 5），该块 MUST 被 `ContextBudgetPlanner` 纳入预算计算。
- **FR-021**: 当进度笔记累积超过阈值时，MUST 自动合并旧笔记（保留里程碑列表，移除详细描述）。

### 治理与兼容

- **FR-022**: 所有新增压缩路径 MUST 继续走既有审计链（事件 + artifact + memory flush），不得引入绕过 control plane 的快捷路径。
- **FR-023**: Subagent MUST 继续绕过所有压缩机制，保持 Feature 034 行为不变。
- **FR-024**: 新增配置项 MUST 通过 `setup.review → setup.apply` 流程验证，不得直写环境变量。
- **FR-025**: 前后端测试 MUST 覆盖：全局预算分配回归、Skill 注入预算纳入、中文 token 估算精度、三级压缩回归、两阶段压缩回归、异步/同步 fallback、进度笔记 CRUD、模型 fallback 链路。

### Key Entities

- **BudgetAllocation**: 全局 token 预算分配结果，包含 `max_input_tokens / system_blocks_budget / skill_injection_budget / memory_recall_budget / progress_notes_budget / conversation_budget / estimation_method`（estimation_method: "cjk_aware" | "tokenizer" | "legacy_char_div_4"）。
- **ContextLayer**: 描述一个压缩层级（Recent / Compressed / Archive），包含 `layer_id / turns / token_count / max_tokens`。
- **CompactionPhaseResult**: 描述两阶段压缩的每阶段结果，包含 `phase / messages_affected / tokens_saved / model_used`。
- **ProgressNote**: Worker 写入的结构化进度笔记，包含 `note_id / task_id / agent_session_id / step_id / description / status / key_decisions / next_steps / created_at`。
- **CompactionModelConfig**: 压缩模型的配置，包含 `alias / fallback_chain / timeout_ms / max_summary_chars`。

## Implementation Strategy

### Phase 0: 全局 token 预算统一 + Token 估算升级（地基，优先级最高）

**改动范围**：
- `context_compaction.py`: `estimate_text_tokens()` 替换为中文感知版本；`_chunk_segments_by_token_budget()` 中 `char_budget = max(256, transcript_budget * 4)` 的硬编码 `*4` 需同步改为动态计算（使用中文感知的 chars-per-token 比率）
- 新增 `context_budget.py`（`gateway/services/` 下独立模块）: `ContextBudgetPlanner` 类
- `agent_context.py`: `_fit_prompt_budget()` 和 `_build_system_blocks()` 接收预算参数
- `llm_service.py`: `_build_loaded_skills_context()` 移到预算体系内
- `task_service.py`: 在 `_build_task_context()` 开头调用 `ContextBudgetPlanner.plan()`

**设计要点**：
- **Token 估算升级**：`estimate_text_tokens(text)` 检测文本中非 ASCII 字符比例 `r`，使用公式 `len(text) / (4 × (1-r) + 1.5 × r)` 估算。可选：若 `tiktoken` 可导入则使用 `cl100k_base` encoder 精确计算（一次性初始化开销 ~100ms，之后每次 <1ms）。
- **Skill 注入修复**：将 `LLMService._build_loaded_skills_context()` 的调用时机从 `_fit_prompt_budget()` 之后移到之前。具体方案：在 `AgentContextService._build_system_blocks()` 中新增一个 `LoadedSkills` 系统块，接收已加载 Skill 内容作为参数，使其参与 `_fit_prompt_budget()` 的 token 计算。`LLMService` 中不再追加 Skill 内容。
- **BudgetPlanner 输入**：`max_input_tokens`、已加载 Skill 名称列表（查 SkillDiscovery 估算内容长度）、Memory 配置（top_k × 平均 hit 长度 ~60 token）、是否有进度笔记。
- **BudgetPlanner 输出**：`BudgetAllocation` 数据类——`conversation_budget` 传给 `ContextCompactionService.build_context()`，各系统块预算传给 `_fit_prompt_budget()`。
- **向后兼容**：`ContextCompactionService.build_context()` 的 `conversation_budget` 参数可选，未传时回退到 `max_input_tokens`（兼容现有调用方）。

### Phase 1: 压缩模型配置 + 两阶段压缩

**改动范围**：
- `provider/alias.py`: 注册 `compaction` 语义别名
- `context_compaction.py`: 实现 fallback 链 `compaction → summarizer → main`；新增廉价截断阶段
- `SettingsPage.tsx`: 在 Model Aliases 区域展示 compaction 别名
- `ContextCompactionConfig`: 新增 `compaction_alias`、`large_message_ratio`、`json_smart_truncate` 配置项

**设计要点**：
- 廉价截断参考 Agent Zero 的 `compress_large_messages()`：单条消息超过 `conversation_budget × 0.3` 时截断（注意使用 Phase 0 提供的 `conversation_budget` 而非 `max_input_tokens`）
- JSON 智能截断：解析 JSON 结构，保留 status/error/result 等关键字段，数组只保留前 2 项 + 总数提示
- 非 JSON 文本：保留头 40% + 尾 10% + 中间 `[... truncated N tokens ...]` 标记

### Phase 2: 分层历史结构

**改动范围**：
- `context_compaction.py`: 重构 `build_context()` 和 `_build_compacted_messages()`，引入 Recent/Compressed/Archive 三层
- `core/models/agent_context.py`: 扩展 `CompiledTaskContext` 增加 `layers` 字段；`AgentSession.metadata` 增加 `compressed_layers` 存储 Compressed 层内容
- `agent_context.py`: 调整 SessionReplay 在当前 session 内的职责收窄
- `task_service.py`: 压缩事件元数据增加层级信息

**设计要点**：
- Recent 层：保留最近 `recent_turns` 轮原文（当前行为不变）
- Compressed 层：将中间轮次按话题分组，每组生成一段保留决策的摘要。存储在 `AgentSession.metadata["compressed_layers"]`。
- Archive 层：将最旧的 Compressed 条目递归合并为骨架摘要。持久化载体为 `AgentSession.rolling_summary`（语义升级：从"全部历史的扁平摘要"变为"Archive 层骨架摘要"）。
- 话题边界（已澄清）：MVP 不做话题语义分割。分组策略为固定轮次窗口（3-4 轮为一组），每个 user+assistant 对为原子单元。后续可升级为 embedding-based 话题检测
- 预算分配：`recent_ratio=0.50`、`compressed_ratio=0.30`、`archive_ratio=0.20`——基于 `conversation_budget`（Phase 0 输出）
- 迁移兼容：`AgentSession.metadata` 增加 `compaction_version` 字段（`"v1"` = 034 扁平摘要，`"v2"` = 060 Archive 层），读取 `rolling_summary` 时按版本解析，兼容旧 session 数据
- **SessionReplay 调整**：当前 session 内，SessionReplay 只注入"上次 session 的最终状态摘要"，不再承担中期历史的职责。`_fit_prompt_budget()` 中 SessionReplay 的 dialogue_limit 梯度保留但仅用于跨 session 场景。

### Phase 3: 异步后台压缩

**改动范围**：
- `context_compaction.py`: 新增 `schedule_background_compaction()` 和 `await_compaction_result()`
- `task_service.py`: 在 LLM 调用完成后触发后台压缩；在下一轮 `_build_task_context()` 时消费结果
- `ContextCompactionService`: 增加类属性 `_pending_compactions: dict[str, asyncio.Task]` 和 `_compaction_locks: dict[str, asyncio.Lock]`（纯内存，不持久化——进程重启后 asyncio.Task 必然丢失）

**设计要点**：
- 使用 `asyncio.Task` 后台执行，状态通过 `_pending_compactions` 内存 dict 跟踪（非 AgentSession 字段）
- 并发控制（已澄清）：per-session `asyncio.Lock`，后台压缩和 turn hook 的 `rolling_summary` / `compressed_layers` 写入前获取同一把锁
- 超时保护：默认 10 秒，通过 `asyncio.wait_for` 保护，超时后释放锁并回退同步路径
- 进程重启：后台任务丢失但不影响正确性（下一轮会重新同步压缩）

### Phase 4: Worker 进度笔记

**改动范围**：
- `packages/tooling/`: 新增 `progress_note` 工具定义
- `agent_context.py`: 上下文构建时注入最近进度笔记到独立 `ProgressNotes` 系统块
- `context_budget.py`: `ContextBudgetPlanner` 纳入进度笔记的预算预估
- Worker bootstrap prompt: 引导 Worker 在关键步骤后调用 `progress_note`

**设计要点**：
- 进度笔记存储为 Artifact（type: `progress-note`，格式 JSON）
- 注入上下文时只注入最近 5 条笔记的 `step_id + description + status` 摘要（独立系统块，不与 Archive 摘要混合）
- 自动合并：超过 50 条时，旧笔记合并为一条 `[历史里程碑汇总]`

## Success Criteria

- **SC-000**: 在中文多轮对话 + 加载 2 个 Skill + 挂载 Memory 的场景下，实际交付给模型的总 token 数不超过 `max_input_tokens`（当前架构下这个条件不可靠地满足）。
- **SC-000a**: 中文 token 估算误差从当前 ~100%（`len/4`）降低到 < 30%。
- **SC-001**: 用户可以在 Settings 中为 `compaction` 别名绑定轻量模型，压缩调用走指定模型；未配置时 fallback 链正常工作。
- **SC-002**: 10+ 轮对话的压缩结果包含 Recent/Compressed/Archive 三层，control plane 可审计各层 token 占用。
- **SC-003**: 包含超大工具输出的对话，压缩时先截断后摘要，LLM 摘要调用次数少于纯 LLM 方案。
- **SC-004**: 异步压缩开启后，多轮对话的 p50 请求延迟不高于 Feature 034 同步压缩方案。
- **SC-005**: Worker 进度笔记在上下文压缩后仍可恢复，不重复已完成步骤。
- **SC-006**: 所有压缩路径继续通过既有审计链，Subagent 绕过行为不变。
- **SC-007**: `_fit_prompt_budget()` 的暴力搜索在 `ContextBudgetPlanner` 生效后，命中首选组合（无需降级修剪）的概率 > 80%（当前估计 < 50%）。

## Clarifications

### Session 2026-03-17

| # | 问题 | 自动选择 | 理由 |
|---|------|---------|------|
| 1 | `ContextBudgetPlanner` 应放在哪个模块？ | 独立模块 `context_budget.py` 放在 `gateway/services/` 下 | BudgetPlanner 是压缩层和装配层的上游协调者，不应属于任一下游。独立模块符合单一职责，且避免 `context_compaction.py` 继续膨胀。`task_service.py._build_task_context()` 在调用 `build_context()` 和 `build_task_context()` 之前先调用 `ContextBudgetPlanner.plan()`，将 `BudgetAllocation` 分别传递给两个下游。 |
| 2 | `_fit_prompt_budget()` 在 BudgetPlanner 生效后参数空间应扩展还是缩减？ | 参数空间不扩展，Skill 截断由 BudgetPlanner 预分配处理 | BudgetPlanner 在上游已为 Skill 分配预算，`_build_system_blocks()` 中的 LoadedSkills 系统块按预算截断。`_fit_prompt_budget()` 不需要为 Skill 增加新搜索维度，保持现有组合空间作为安全兜底（对齐 FR-000d）。 |
| 3 | Compressed 层话题分组算法的粒度和边界如何定义？ | MVP 使用轮次级分组（固定 window 3-4 轮），不做话题语义分割 | 避免引入 NLP 话题检测的延迟和复杂度。分组策略：每个 user+assistant 对为原子单元，按固定窗口分组摘要。后续可升级为基于 embedding 的话题边界检测。 |
| 4 | 异步压缩并发写入 `AgentSession` 的并发控制策略？ | per-session `asyncio.Lock` | 单进程 async 架构下 asyncio.Lock 足够。为每个活跃 session 维护锁，后台压缩和 turn hook 写入前获取同一把锁，超时由 `asyncio.wait_for` 保护（10 秒后释放锁并回退同步路径）。 |
| 5 | `progress_note` 对 Subagent 和 Butler 的可见性？ | Worker 自身可见 + Butler 通过 control plane 可查，Subagent 不可见 | 进度笔记绑定 `task_id + agent_session_id`，Subagent 有独立 session 且绕过压缩机制。Butler 可通过 control plane API 查询 Worker 的 progress-note Artifact（对齐 Constitution 原则 8）。 |
