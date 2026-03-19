# Quality Checklist: Feature 064 — Butler Dispatch Redesign

> Generated: 2026-03-19
> Source: `.specify/features/064-butler-dispatch-redesign/spec.md`
> References: `research/dispatch-architecture-comparison.md`, `docs/blueprint.md`, `.specify/memory/constitution.md`

---

## I. Content Quality（内容质量）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| C-1 | 无实现细节（未提及具体语言、框架、API 实现方式） | [ ] | spec.md 包含大量 Python 伪代码（Section 3.1-3.5）、具体类名（`DelegateToWorkerTool`）、具体文件名（`orchestrator.py`、`butler_behavior.py`）、具体函数签名（`_dispatch_butler_direct_execution()`）、具体模型 alias（`"main"`/`"cheap"`）和 failover 配置。这些属于技术方案而非需求规范 |
| C-2 | 聚焦用户价值和业务需求 | [x] | Section 1（动机）清晰描述了用户痛点（30s+ 响应延迟）和目标（降至单次 LLM 调用耗时），Section 5（预期效果）给出了面向用户的场景和耗时对比 |
| C-3 | 面向非技术利益相关者编写 | [ ] | 大量内容面向开发者：Python 伪代码、工具 schema 定义、文件级变更清单、token 预算策略引用、failover 配置。非技术读者无法理解 Section 3-4 的大部分内容 |
| C-4 | 所有必填章节已完成 | [x] | 包含动机、架构变更、详细设计、迁移策略、预期效果、验证标准、长期演进等章节 |

**Content Quality 汇总**: 2/4 通过

---

## II. Requirement Completeness（需求完整性）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| R-1 | 无 `[NEEDS CLARIFICATION]` 标记残留 | [x] | 全文搜索未发现任何 `[NEEDS CLARIFICATION]` 标记 |
| R-2 | 需求可测试且无歧义 | [x] | Section 6 定义了功能验证（4 项）、性能验证（2 项）、兼容性验证（4 项），每项均可构造具体测试用例 |
| R-3 | 成功标准可测量 | [x] | "简单问题端到端延迟 < 15s"、"单次 LLM 调用"、"无 Worker 创建"、"完整 Event 链"等均可量化验证 |
| R-4 | 成功标准是技术无关的 | [ ] | 成功标准涉及 "LLM 调用次数"、"Worker 创建"、"Event 链" 等实现概念。面向用户的成功标准应聚焦在响应时间和功能完整性，而非内部调用次数 |
| R-5 | 所有验收场景已定义 | [ ] | 缺少以下场景的验收定义：(1) 模型降级/failover 触发后的用户体验；(2) Butler Free Loop 达到迭代上限时的行为；(3) delegate_to_worker 工具调用失败时的回退；(4) 并发请求场景；(5) 从旧版本滚动升级期间的兼容行为 |
| R-6 | 边界条件已识别 | [ ] | 缺少以下边界条件讨论：(1) Free Loop 最大迭代次数超限后的行为；(2) 工具执行超时的处理；(3) context overflow 时的降级路径；(4) LLM 既输出文本又调用 delegate_to_worker 的处理；(5) 用户在 Butler 执行期间发送新消息的处理 |
| R-7 | 范围边界清晰 | [x] | Section 2.3（不变的部分）明确列出 8 个不变组件及理由；Section 4.1 分 4 个 Phase 明确范围递进；Phase 1 最小变更集锁定 2 个文件 |
| R-8 | 依赖和假设已识别 | [ ] | 缺少显式的依赖和假设声明。隐含假设包括：(1) LLM 能通过 system prompt 指引可靠地判断何时委派；(2) `self._llm_service` 已具备 tool calling 能力；(3) 现有 tool_profile="standard" 已包含 Butler 所需的工具集；(4) failover chain 中 "cheap" alias 已在 LiteLLM 中配置。这些应显式列出 |

**Requirement Completeness 汇总**: 4/8 通过

---

## III. Feature Readiness（特性就绪度）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| F-1 | 所有功能需求有明确的验收标准 | [ ] | Section 3.3（Monologue Loop 模式）、Section 3.5（模型降级）描述了功能行为但缺少对应的验收标准。验证标准（Section 6）仅覆盖 Phase 1 的直接回答和基本委派场景，未覆盖 Phase 2-3 的功能需求 |
| F-2 | 用户场景覆盖主要流程 | [x] | Section 5 覆盖了四类主要场景：简单问候、一般对话、需要搜索的问题、需要 Worker 的复杂任务，对应从简到繁的用户交互模式 |
| F-3 | 功能满足 Success Criteria 中定义的可测量成果 | [x] | Section 5 的预期效果表与 Section 6 的验证标准一一对应，每个场景有明确的目标耗时和 LLM 调用次数 |
| F-4 | 规范中无实现细节泄漏 | [ ] | 同 C-1。spec.md 实质上是一份混合了需求规范和技术设计方案的文档。Section 3（详细设计）、Section 4.2（Phase 1 最小变更集）、以及贯穿全文的 Python 伪代码和文件级变更描述，均属于实现细节，应迁移至 plan.md |

**Feature Readiness 汇总**: 2/4 通过

---

## IV. 技术可行性（与现有架构兼容性）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| T-1 | 与蓝图架构一致 | [x] | 蓝图 Section 6.1 明确 Butler 是"主执行者 + 监督者，永远 Free Loop"。当前 spec 将 Butler 从"路由器"转变为"主执行者"，完全对齐蓝图设计意图 |
| T-2 | 与三层 Agent 模型兼容 | [x] | Butler/Worker/Subagent 三层结构不变。Butler 增加直接执行能力不改变层级关系。delegate_to_worker 工具保留了 Butler 向 Worker 委派的路径 |
| T-3 | 与现有通信协议兼容 | [x] | A2A 协议不变（Section 2.3 明确）。委派后的 Butler<->Worker 通信格式、envelope 不受影响 |
| T-4 | 与现有数据模型兼容 | [x] | Task/Event/Artifact 模型不变。仅新增 `butler_execution_mode=direct` metadata 字段，向后兼容 |
| T-5 | 迁移路径可行且可回滚 | [x] | 4 Phase 分阶段迁移，每阶段标注了风险等级和可回滚性。Phase 1 仅修改 2 个文件，最小化变更风险 |
| T-6 | 不引入新的外部依赖 | [x] | 所有变更在现有代码框架内完成，未引入新库或服务 |

**技术可行性汇总**: 6/6 通过

---

## V. 宪法合规性（Constitution Compliance）

| # | Constitution Rule | Pass | Notes |
|---|------------------|------|-------|
| CON-1 | Durability First（原则 1） | [x] | Butler 直接执行路径保持完整的 Event Sourcing（Section 2.2 变更 B 明确），Task 状态持久化不变 |
| CON-2 | Everything is an Event（原则 2） | [x] | Section 2.2 变更 B 明确要求 "保持完整的 Event Sourcing（MODEL_CALL_STARTED -> MODEL_CALL_COMPLETED -> ARTIFACT_CREATED）"。Section 6.1 验证项 "所有场景生成完整 Event 链" |
| CON-3 | Tools are Contracts（原则 3） | [x] | delegate_to_worker 工具有完整的 schema 定义（Section 3.2），参数类型、enum、description 齐全，符合工具契约化要求 |
| CON-4 | Side-effect Must be Two-Phase（原则 4） | [x] | Policy Gate 保持不变（Section 2.3），不可逆操作仍需通过 Gate。delegate_to_worker 本身是可逆操作（只是委派），不触发 Two-Phase 要求 |
| CON-5 | Least Privilege by Default（原则 5） | [x] | tool_profile="standard" 限定 Butler 可用工具集，不扩展额外权限。secrets 处理机制不变 |
| CON-6 | Degrade Gracefully（原则 6） | [x] | Section 3.5 新增模型降级（Failover）机制，定义了 FAILOVER_CHAIN 和 FAILOVER_TRIGGERS，直接对齐此原则 |
| CON-7 | User-in-Control（原则 7） | [x] | Policy Gate 在 LLM 调用前执行不变。审批、取消能力不变。用户控制链路未被绕过 |
| CON-8 | Observability is a Feature（原则 8） | [x] | Butler 直接执行路径生成完整事件链（CON-2）。新增 `butler_execution_mode=direct` metadata 支持区分执行模式的可观测查询 |
| CON-9 | 原则 13A — 优先提供上下文而非堆积硬策略 | [x] | spec 的核心理念是"让 LLM 自主决策回答/工具/委派"而非用代码预判路由，直接对齐此原则 |

**宪法合规性汇总**: 9/9 通过

---

## VI. 测试可验证性（Testability）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| TV-1 | 每个功能需求可构造测试用例 | [ ] | Phase 1 的功能需求（直接回答、规则快速路径）可测试。但 Phase 2-3 的需求（delegate_to_worker 工具行为、failover 触发条件、Free Loop 迭代控制）缺少可直接映射的测试场景描述 |
| TV-2 | 性能验证标准可自动化 | [x] | "简单问题端到端延迟 < 15s" 可通过自动化测试框架验证；"LLM 调用次数" 可通过 Event Store 查询计数 |
| TV-3 | 兼容性验证可回归 | [x] | "前端 SSE 事件流正常"、"Telegram 消息收发正常"、"现有测试通过" 均为回归测试场景，可纳入 CI |
| TV-4 | 边界条件可测试 | [ ] | 缺少边界条件的验证场景（同 R-6）：Free Loop 超限、工具超时、context overflow、并发请求等 |
| TV-5 | 验证标准覆盖所有 Phase | [ ] | Section 6 的验证标准主要对应 Phase 1。Phase 2（delegate_to_worker）和 Phase 3（Failover）缺少独立的验证标准条目 |

**测试可验证性汇总**: 2/5 通过

---

## VII. 安全性（Security）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| S-1 | 权限模型不被绕过 | [x] | Policy Gate 在 Butler Free Loop 之前执行不变。tool_profile 限定可用工具集 |
| S-2 | 数据边界清晰 | [x] | Butler 直接执行使用与原 Worker 相同的上下文构建逻辑（`_build_system_blocks()`），数据隔离不变 |
| S-3 | Secrets 不进 LLM 上下文 | [x] | secrets 按 project/scope 分区机制不变，spec 未引入新的 secrets 暴露路径 |
| S-4 | 新增工具的副作用等级已声明 | [ ] | delegate_to_worker 工具的副作用等级未声明。根据宪法原则 3，工具必须声明副作用等级（none / reversible / irreversible） |
| S-5 | 委派决策不可被 prompt injection 操纵 | [ ] | spec 未讨论 Butler 自主委派模式下的 prompt injection 风险。恶意用户输入可能诱导 LLM 不当委派或绕过委派（例如将高风险任务伪装为简单问题直接执行）。需要至少在需求层面声明对此的防护策略 |

**安全性汇总**: 3/5 通过

---

## VIII. 可观测性（Observability）

| # | Check Item | Pass | Notes |
|---|-----------|------|-------|
| O-1 | Butler 直接执行的事件链完整 | [x] | Section 2.2 变更 B 明确 "MODEL_CALL_STARTED -> MODEL_CALL_COMPLETED -> ARTIFACT_CREATED" 事件链 |
| O-2 | 新增执行模式可区分 | [x] | `butler_execution_mode=direct` metadata 支持区分直接执行和 Worker 委派 |
| O-3 | 委派路径的事件链不断裂 | [x] | delegate_to_worker 工具触发后走现有 Delegation Plane -> Worker Dispatch，事件链与原有路径一致 |
| O-4 | Failover 事件可追溯 | [ ] | Section 3.5 定义了 failover 机制，但未说明 failover 触发时是否生成事件（如 MODEL_FAILOVER_TRIGGERED），以及 failover 后的模型信息是否记录在 Event metadata 中 |
| O-5 | Free Loop 迭代可审计 | [ ] | Section 3.3 提到"设置最大循环次数"，但未说明每次循环迭代是否生成独立事件，也未说明循环终止原因（正常完成 / 达到上限 / 用户取消）是否记录 |
| O-6 | 性能指标可查询 | [x] | 通过 Event Store 可计算 LLM 调用次数、端到端延迟、执行模式分布等指标 |

**可观测性汇总**: 4/6 通过

---

## Summary

| Dimension | Total | Passed | Failed |
|-----------|-------|--------|--------|
| I. Content Quality | 4 | 2 | 2 |
| II. Requirement Completeness | 8 | 4 | 4 |
| III. Feature Readiness | 4 | 2 | 2 |
| IV. 技术可行性 | 6 | 6 | 0 |
| V. 宪法合规性 | 9 | 9 | 0 |
| VI. 测试可验证性 | 5 | 2 | 3 |
| VII. 安全性 | 5 | 3 | 2 |
| VIII. 可观测性 | 6 | 4 | 2 |
| **Total** | **47** | **32** | **15** |

---

## Failed Items Detail

### High Priority（阻塞进入 plan 阶段）

1. **C-1 / F-4 — 实现细节泄漏**：spec.md 混合了需求规范和技术设计。Python 伪代码、文件级变更清单、具体函数名等应迁移至 plan.md。spec.md 应聚焦于"做什么"和"为什么"，而非"怎么做"。

2. **R-5 — 验收场景不完整**：缺少 failover 触发、Free Loop 超限、delegate_to_worker 失败、并发请求、滚动升级等场景的验收定义。

3. **R-6 — 边界条件未识别**：Free Loop 最大迭代次数超限、工具执行超时、context overflow、LLM 同时输出文本和委派工具调用、用户并发消息等边界条件需要显式列出并定义处理策略。

4. **R-8 — 依赖和假设未显式声明**：LLM tool calling 能力、tool_profile 覆盖范围、LiteLLM alias 配置等隐含假设需要显式列出。

### Medium Priority（建议修复后进入 plan 阶段）

5. **C-3 — 受众定位偏开发者**：spec.md 更接近技术设计文档。如果 spec 的目标受众包括非技术利益相关者，需要补充面向用户的执行摘要。

6. **R-4 — 成功标准含技术概念**：建议增加面向用户视角的成功标准（如"用户感知响应时间"），与技术验证指标分离。

7. **TV-1 / TV-4 / TV-5 — 测试覆盖不完整**：Phase 2-3 的验证标准缺失，边界条件测试场景缺失。

8. **S-4 — delegate_to_worker 副作用等级未声明**：需要在工具定义中补充。

9. **S-5 — Prompt injection 防护未讨论**：Butler 自主委派模式引入了新的攻击面，需要至少在需求层面声明防护策略。

10. **O-4 / O-5 — Failover 和 Free Loop 的可观测事件未定义**：需要补充事件类型和 metadata 字段。
