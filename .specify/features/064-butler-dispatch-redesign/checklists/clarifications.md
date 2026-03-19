# Feature 064: Butler Dispatch Redesign — 需求澄清

> 生成时间: 2026-03-19
> 前序制品: spec.md, research/dispatch-architecture-comparison.md, docs/blueprint.md

---

## 结构化歧义扫描

| 分类 | 状态 | 说明 |
|------|------|------|
| 功能范围与行为 | **Partial** | Butler Free Loop 迭代上限、停止条件未明确；Butler 与 Orchestrator 的角色边界在新架构下模糊 |
| 领域与数据模型 | **Partial** | ButlerSession / 对话历史在直接执行路径下如何持久化未说明 |
| 交互与 UX 流程 | **Clear** | 前端/Telegram/API 无影响已明确 |
| 非功能质量属性 | **Partial** | 性能目标有数字但 Free Loop 的成本控制缺失 |
| 集成与外部依赖 | **Partial** | delegate_to_worker 工具与 Delegation Plane 的交互细节不足 |
| 边界条件与异常处理 | **Missing** | LLM 调用失败时 Butler 直接执行路径的回退策略仅在 Phase 3 提及 |
| 术语一致性 | **Partial** | "Butler Free Loop" vs "Monologue Loop" vs "直接执行" 混用 |

---

## CRITICAL 问题（需用户决策）

### CRITICAL-1: Butler Free Loop 迭代上限与成本控制策略

**上下文**: spec.md 3.3 节提到"设置最大循环次数（如 10），防止无限循环"，但蓝图 11.8 节明确要求 Free Loop 必须内置**三道刹车**：轮次上限、预算阈值、无进展检测。spec 仅提及了轮次上限（且数值与蓝图建议的 50 差距很大），未涉及预算阈值和无进展检测。

**影响**: 此决策直接关系到系统安全性和成本控制——如果 Butler Free Loop 缺少成本刹车，一个恶意或低质量的多工具调用可能消耗大量 token。这属于 Constitution 第 7 条（User-in-Control）和蓝图安全门禁的核心范畴。

**推荐**: 选项 B — 对齐蓝图三道刹车 + Butler 合理默认值

**选项**:

| 选项 | 描述 | 影响 |
|------|------|------|
| A | Phase 1 只设简单轮次上限（10），其余推迟到 Phase 3+ | 快速交付但存在成本失控风险窗口 |
| B | Phase 1 即实现三道刹车（轮次上限 10、per-request token budget、2 轮无进展检测），默认值保守 | 符合蓝图要求，增加 Phase 1 工作量约 20% |
| C | Phase 1 轮次上限 10 + 简化 token 预算检查（总 token 超阈值即停），Phase 2 补齐无进展检测 | 折中方案，基本安全但不完整 |

---

## 自动解决的澄清

### AUTO-CLARIFIED-1: Butler 直接执行时的 ButlerSession 状态管理

**问题**: spec 3.4 节定义了上下文构成，但未说明 Butler Free Loop 执行期间的中间状态（工具调用结果、多轮对话）如何写入 ButlerSession。蓝图要求 Butler 拥有自己的 ButlerSession，且所有交互都必须持久化。

**自动选择**: 复用现有 `process_task_with_llm()` 的 Event Sourcing 链路。Butler 直接执行的每轮 LLM 调用和工具调用都通过 `TaskService.record_*` 方法写入 Event Store，与 Worker 执行路径保持一致的事件粒度。ButlerSession 的 conversation history 追加 Butler 的响应作为 assistant message。

**理由**: spec 2.3 节已声明 "Event Sourcing 不变"，蓝图明确 Butler 与 Worker 使用相同的 Event 模型。复用而非新建是最低风险路径，且与 Phase 4 清理阶段的简化方向一致。

`[AUTO-CLARIFIED: 复用 process_task_with_llm Event 链路 — 保持与 Worker 一致的事件粒度]`

---

### AUTO-CLARIFIED-2: delegate_to_worker 工具的 context 传递机制

**问题**: spec 3.2 节定义了 `delegate_to_worker` 工具参数（worker_type、task_description、urgency），但缺少 `context` 字段的具体定义。蓝图要求 Worker 通过 A2A context capsule 获得任务上下文，"不直接读取完整用户历史"。delegate_to_worker 调用时，由谁负责构建 context capsule？

**自动选择**: `delegate_to_worker` 工具的执行器（非 LLM 侧）负责构建 context capsule。工具参数中 `task_description` 即为 LLM 侧提供的任务描述，工具执行器从当前 ButlerSession 中提取 recent conversation summary + 相关 memory 组装为 A2A context capsule，交给现有 Delegation Plane 路径处理。LLM 不需要（也不应该）手动构建结构化的 context capsule。

**理由**: 蓝图明确 "Butler 必须通过 A2A payload / context capsule 选择性转述"，由工具执行器自动完成这步既保证了蓝图约束，又避免 LLM 产出不稳定的结构化数据。这与 Claude Code 的 Agent tool（LLM 只给任务描述，框架自动处理上下文隔离）和 Agent Zero 的 call_subordinate（共享 AgentContext 但独立 History）的行业实践一致。

`[AUTO-CLARIFIED: 工具执行器自动构建 context capsule — LLM 只提供 task_description]`

---

### AUTO-CLARIFIED-3: "Orchestrator" 与 "Butler" 的角色边界

**问题**: 当前架构中 Orchestrator 是独立的协调层，Butler 是主 Agent。spec 提出的 "Butler Free Loop" 实质上是让 Butler 接管了 Orchestrator 的部分路由职能（LLM 自主判断是否委派）。蓝图 6.1 中 Orchestrator 定义为 "Free Loop 驱动的路由与监督层"，而 Butler 是 "主执行者 + 监督者"。变更后这两者的分工如何？

**自动选择**: 维持现有代码中 OrchestratorService 作为入口层（Policy Gate + 请求准备 + 事件写入 + 结果组装），Butler Free Loop 作为其中的一个执行分支。OrchestratorService 不消失，但其核心路由决策从"代码预判 + LLM 预路由"简化为"规则快速路径 + Butler Free Loop 兜底"。Butler Free Loop 内的委派决策（delegate_to_worker tool call）替代了原来 _resolve_model_butler_decision 的功能。

**理由**: 蓝图中 Orchestrator 和 Butler 不是同一个组件。Orchestrator 管治理（Policy Gate、事件写入、监督），Butler 管执行（回答问题、使用工具、决定委派）。spec 的变更恰好是让 Butler 回归"主执行者"定位，Orchestrator 回归"治理框架"定位。这与蓝图原意完全一致，无需合并或重命名。

`[AUTO-CLARIFIED: Orchestrator 保持治理框架，Butler Free Loop 是其执行分支 — 分工对齐蓝图原意]`

---

### AUTO-CLARIFIED-4: Phase 1 中 _is_trivial_direct_answer() 的覆盖范围

**问题**: spec 变更 D 提到新增 `_is_trivial_direct_answer()` 识别简单对话（问候、身份、致谢、确认），但未定义"简单对话"的完整列表和判定逻辑。这是纯规则匹配、关键词匹配、还是分类器？如果误判将非简单问题归类为简单，是否会导致 Butler 用错误的上下文回答？

**自动选择**: Phase 1 使用保守的关键词/模式匹配，仅覆盖最明确的几类：
1. 纯问候（你好/hello/hi + 无实质问题）
2. 身份询问（你是谁/什么模型）
3. 致谢/确认（谢谢/好的/明白了）
4. 简单元问题（你能做什么）

误判风险通过以下策略降低：`_is_trivial_direct_answer()` 仅是"快速路径优化"（跳过一些不必要的准备步骤），不影响 Butler Free Loop 的核心执行。即使不被识别为 trivial，请求仍然进入 Butler Free Loop 正常处理。该函数的 false negative（漏判）不影响正确性，false positive（误判）通过限制匹配模式的严格度来避免。

**理由**: 与蓝图 "运行时真相由 RuntimeHintBundle 驱动，不扩张硬编码分类树" 的要求一致。Phase 1 的 trivial 检测只是性能优化，不是路由决策。Butler Free Loop 本身就能正确处理所有请求，trivial 检测只是让最简单的情况跳过 memory recall 等昂贵准备步骤。

`[AUTO-CLARIFIED: 保守关键词匹配，仅跳过准备步骤不影响核心执行 — 避免扩张硬编码分类树]`

---

## 与现有系统的潜在冲突点

### 冲突-1: Delegation Plane 的定位变化

**现状**: Delegation Plane (`delegation_plane.py`) 当前是所有请求的必经路径，负责创建 Work、构建 DispatchEnvelope、路由到 Worker。
**变更后**: Delegation Plane 仅在 `delegate_to_worker` tool 触发时使用。大部分请求（简单对话、工具使用）绕过 Delegation Plane。
**影响**: Delegation Plane 中可能存在一些"顺带完成"的逻辑（如 Work 创建、审计记录），需要确认这些逻辑在 Butler 直接执行路径中是否有等效覆盖。
**建议**: Phase 1 实现前需审计 Delegation Plane 的所有副作用，确保 Butler 直接执行路径不丢失关键副作用。

### 冲突-2: _InlineReplyLLMService 与 Butler Free Loop 的关系

**现状**: `_dispatch_inline_butler_decision()` 使用 `_InlineReplyLLMService(decision.reply_prompt)` 生成回复，这是一个"伪 LLM"——直接返回预生成的文本。
**变更后**: ASK_ONCE 和 BEST_EFFORT_ANSWER 路径被 Butler Free Loop 替代，Butler 自己生成回复。
**影响**: `_InlineReplyLLMService` 的使用场景是否完全消失？如果保留兼容，Phase 1 是否存在两条回复路径并行的情况？
**建议**: Phase 1 保留 `_InlineReplyLLMService` 路径作为 fallback（当 Butler Free Loop 未启用时），Phase 4 清理时移除。

### 冲突-3: tool_profile 在 Butler 直接执行时的值

**现状**: 预路由 LLM 使用 `tool_profile="minimal"`，Worker 执行使用 `tool_profile="standard"`。
**变更后**: spec 声明 Butler 使用 `tool_profile="standard"`。
**影响**: Butler 直接执行时获得 standard 工具集，意味着 Butler 可使用所有标准工具（含 web_search、memory 等）。需确认 ToolBroker/Policy Engine 对 Butler 身份的工具权限是否已正确配置。
**建议**: 无阻塞风险——当前 Worker 就使用 standard profile，Butler 复用同一逻辑即可。

### 冲突-4: Failover 机制与 LiteLLM Proxy 的职责边界

**现状**: LiteLLM Proxy 自身支持 fallback 配置（alias 路由 + fallback chain）。
**变更后**: spec 3.5 节新增应用层 Failover 候选链。
**影响**: 应用层 failover 与 LiteLLM Proxy 的 fallback 可能产生重复降级——LiteLLM 已经降级过一次，应用层又降级一次。
**建议**: Phase 3 实现 failover 时需明确分工：LiteLLM Proxy 处理同一 alias 内的多 provider fallback，应用层 failover 处理 alias 级别的降级（main -> cheap）。两层不重叠。

---

## 边界条件清单

| # | 边界条件 | spec 是否覆盖 | 建议 |
|---|---------|:---:|------|
| 1 | Butler Free Loop 中工具调用超时（如 web_search 30s 无响应） | 否 | Phase 1 应复用 Worker 的工具超时机制 |
| 2 | Butler Free Loop 中 LLM 返回空内容或解析失败 | 否 | 返回通用错误消息，写入事件，不重试 |
| 3 | delegate_to_worker 调用后 Worker 创建失败 | 否 | 回退到 Butler 直接回复 "无法处理，请稍后重试" |
| 4 | 用户在 Butler Free Loop 执行期间发送新消息 | 否 | 与现有 Worker 执行期间的行为一致（排队等待） |
| 5 | Butler Free Loop 的多轮工具调用产生的 token 超过模型上下文窗口 | 否 | 需要 _fit_prompt_budget 在循环内每轮检查 |
| 6 | 并发请求下多个 Butler Free Loop 同时运行 | 否 | 与现有单用户串行模型一致（task queue 保证） |
| 7 | Butler 调用 delegate_to_worker 但 LLM 同时返回了文本回复 | 否 | 优先处理 delegate_to_worker，忽略文本回复 |
| 8 | Failover 降级到 cheap 模型后，cheap 模型不支持 tool calling | 否 | Phase 3 需确认 cheap alias 对应的模型支持 tool calling |

---

## 术语规范化建议

| 当前用法 | 建议统一为 | 理由 |
|---------|----------|------|
| "Butler Free Loop" / "Monologue Loop" / "执行循环" | **Butler Execution Loop** | 区别于蓝图中 Orchestrator 的 Free Loop；Monologue 是 Agent Zero 术语，不适合直接搬用 |
| "直接执行" / "直接回答" / "Direct Answer" | **Butler Direct Execution** | "直接回答"暗示只有文本输出，但实际包含工具调用 |
| "预路由 LLM 调用" / "Butler Decision preflight" / "model decision" | **Butler Decision Preflight**（待废弃） | 统一为将被移除的组件名 |
| "规则快速路径" / "trivial direct answer" | **Rule-based Fast Path** | 与代码中 `_is_trivial_direct_answer()` 对齐但更准确 |

---

## 总结

| 维度 | 统计 |
|------|------|
| 检测歧义点 | 5 |
| 自动解决 | 4 |
| CRITICAL（需用户决策） | 1 |
| 与现有系统冲突点 | 4 |
| 边界条件缺失 | 8 |
| 术语规范化建议 | 4 |
