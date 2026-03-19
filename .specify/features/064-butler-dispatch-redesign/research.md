# 技术决策研究: Feature 064 — Butler Dispatch Redesign

**Date**: 2026-03-19
**Spec**: `spec.md`
**调研来源**: `research/dispatch-architecture-comparison.md`

---

## Decision 1: Butler Execution Loop 的实现方式

### 问题

Butler 从"路由器"转变为"主执行者"后，其执行循环应如何实现？是新建独立的执行链路，还是复用现有 `process_task_with_llm()` 链路？

### 决策

**复用 `TaskService.process_task_with_llm()` 链路。**

### 理由

1. `process_task_with_llm()` 已实现完整的 Event Sourcing 链路（STATE_TRANSITION → MODEL_CALL_STARTED → MODEL_CALL_COMPLETED → ARTIFACT_CREATED → STATE_TRANSITION），复用确保 Butler 直接执行与 Worker 执行的事件粒度一致
2. 该链路已处理 Checkpoint、Artifact 存储、上下文编译（`_build_task_context()` → `_build_system_blocks()`）等复杂逻辑，重复实现维护成本高
3. 该链路支持 `dispatch_metadata` 参数注入，可通过 `butler_execution_mode=direct` 区分执行模式
4. Butler 作为 `process_task_with_llm()` 的调用方，传入 `self._llm_service`（真实 LLM）替代 `_InlineReplyLLMService`（伪 LLM），行为自然转变

### 替代方案

| 方案 | 理由被拒绝 |
|------|-----------|
| 新建 `_butler_execution_loop()` 独立循环 | 需要重复实现 Event 写入/Artifact 存储/Checkpoint/上下文构建，违反 DRY，增加维护面 |
| 直接在 `dispatch()` 中内联 LLM 调用 | 方法过大，职责不清晰，难以测试 |

---

## Decision 2: 轮次上限策略

### 问题

蓝图 11.8 节要求 Free Loop 内置三道刹车（轮次上限、预算阈值、无进展检测）。Phase 1 应实现哪些？

### 决策

**Phase 1 仅设轮次上限 10，不做 per-request token budget 和无进展检测。**

（CRITICAL-1 决议，由用户确认）

### 理由

1. **行业实践对齐**: Claude Code / OpenClaw / Agent Zero 均无 per-request token budget 机制。轮次上限是通用做法
2. **Phase 1 场景覆盖**: Phase 1 的主要场景是简单对话（1 轮）和工具使用（2-3 轮），10 轮上限远超需求，提供安全余量
3. **成本风险窗口可控**: 单用户场景下，即使 Butler 运行 10 轮，成本也远低于 Worker 的 50 轮上限
4. **渐进增强**: Phase 3 引入 failover 时可同步增加成本控制，此时有更多实际运行数据支撑阈值设定

### 替代方案

| 方案 | 理由被拒绝 |
|------|-----------|
| Phase 1 实现三道刹车 | 增加工作量 ~20%，且缺乏实际运行数据来设定合理的 budget 阈值和无进展检测窗口 |
| 轮次上限 50（对齐 Worker） | Butler 处理的场景比 Worker 简单得多（简单对话 vs 编程任务），50 轮过于宽松 |

---

## Decision 3: `_is_trivial_direct_answer()` 的实现方式

### 问题

简单对话识别应使用关键词匹配、分类器、还是 embedding 相似度？

### 决策

**保守的正则关键词匹配，仅覆盖 4 类明确模式。**

### 理由

1. **宪法 13A 对齐**: "优先提供上下文而非堆积硬策略" + "不扩张硬编码分类树"。关键词匹配限定为最小集，不试图覆盖所有简单问题
2. **误判安全**: 该函数仅作为性能优化（跳过 memory recall），false negative 不影响正确性（请求正常进入完整流程），false positive 通过严格匹配（长度 < 30 + 全量匹配）避免
3. **零额外开销**: 关键词匹配 < 1ms，不引入模型调用或索引查询
4. **与核心目标一致**: Feature 064 的核心是"消除预路由 LLM"，不应在规则层引入新的复杂分类逻辑

### 替代方案

| 方案 | 理由被拒绝 |
|------|-----------|
| LLM 分类器 | 引入额外 LLM 调用（即使是 cheap 模型），与"消除预路由 LLM"目标矛盾 |
| Embedding 相似度 | 需要预计算 embedding 集合 + 向量搜索，过度工程化 |
| 更广泛的关键词覆盖 | 扩张硬编码分类树，增加维护成本和误判风险 |
| 不做 trivial 检测 | 所有请求都走完整上下文构建，简单问候也执行 memory recall，浪费 ~200ms |

---

## Decision 4: `dispatch()` 中 Butler Direct 的触发位置

### 问题

在 `dispatch()` 方法的控制流中，Butler Direct Execution 应在哪个位置触发？

### 决策

**在 `_dispatch_inline_butler_decision()` 之后、freshness check 之前。**

具体位置: 当 `butler_decision is None`（无特殊规则决策）且 `delegated_request is None`（无委派请求）时，检查 `_should_butler_direct_execute()` 条件，走 Butler Direct Execution。否则 fallback 到现有的 Delegation Plane → Worker Dispatch。

### 理由

1. **保留所有现有路径**: freshness（天气）、inline butler decision（ASK_ONCE/BEST_EFFORT）、delegation plane 都保留，Butler Direct 仅替代"无特殊决策时的默认路径"
2. **回滚安全**: 如果 `_should_butler_direct_execute()` 返回 False（如 LLMService 不支持），自动 fallback 到 Delegation Plane，行为与变更前一致
3. **渐进迁移**: Phase 4 清理前，两条路径并存，可逐步验证 Butler Direct 的覆盖范围

### 替代方案

| 方案 | 理由被拒绝 |
|------|-----------|
| 替换整个 dispatch() 控制流 | 变更范围太大，无法渐进回滚 |
| 在 freshness check 之后触发 | freshness 路径会错误地拦截非天气请求（当 freshness_request 恰好非 None 时） |

---

## Decision 5: Butler Direct Execution 的 LLMService 选择

### 问题

Butler 直接执行时应使用哪个 LLMService 实例？`self._llm_service`（Orchestrator 持有的 LLM）还是新建实例？

### 决策

**使用 `self._llm_service`。**

### 理由

1. `self._llm_service` 已具备 tool calling 能力（`supports_single_loop_executor=True`），是 Orchestrator 初始化时注入的完整 LLMService
2. 该实例已配置 model alias 路由、fallback manager、成本统计等功能
3. Worker 执行时也通过类似的 LLMService 实例调用 LLM，Butler 复用保持一致性
4. 不需要为 Butler 专门创建不同配置的 LLMService

### 替代方案

| 方案 | 理由被拒绝 |
|------|-----------|
| 新建 ButlerLLMService | 不必要的抽象层，增加维护成本 |
| 使用 cheap 模型 | Butler 应使用 main 模型以保证回复质量，cheap 仅在 failover 时使用 |

---

## Decision 6: delegate_to_worker 的 context capsule 构建者

### 问题

当 LLM 调用 `delegate_to_worker` 工具时，A2A context capsule 由谁构建？LLM（通过工具参数）还是工具执行器（自动从 session 提取）？

### 决策

**工具执行器自动构建 context capsule。LLM 只提供 `task_description` 参数。**

（AUTO-CLARIFIED-2 决议）

### 理由

1. **蓝图对齐**: "Butler 必须通过 A2A payload / context capsule 选择性转述"，由工具执行器完成既保证约束又避免 LLM 产出不稳定的结构化数据
2. **行业实践**: Claude Code 的 Agent tool（LLM 只给任务描述，框架自动处理上下文隔离）、Agent Zero 的 call_subordinate（共享 AgentContext 但独立 History）均采用此模式
3. **可靠性**: LLM 生成的 JSON context 结构不稳定，工具执行器从 ButlerSession 提取 recent conversation + memory 更可靠

### 替代方案

| 方案 | 理由被拒绝 |
|------|-----------|
| LLM 构建完整 context capsule | LLM 输出结构化数据不稳定，增加解析失败风险 |
| LLM 提供 context 摘要 + 执行器补充 | 增加工具参数复杂度，LLM 摘要质量不可控 |
