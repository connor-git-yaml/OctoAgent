# Requirements Quality Checklist

**Feature**: 060 - Context Engineering Enhancement
**Spec**: `.specify/features/060-context-engineering-enhancement/spec.md`
**Checked**: 2026-03-17
**Result**: **FAIL** (16/19 passed, 3 failed)

---

## Content Quality

- [x] **CQ-01: 聚焦用户价值和业务需求**
  - Problem Statement 清晰描述了四个结构性差距和三个架构缺陷对用户体验和系统可靠性的影响。
  - Product Goal 从用户可感知的维度定义目标（预算不超限、成本降低、延迟降低、进度可恢复）。
  - 每个 User Story 附有 "Why this priority" 阐述业务/用户价值。

- [ ] **CQ-02: 无实现细节（未提及具体语言、框架、API 实现方式）**
  - **FAIL**: 规范中包含大量实现层细节，严重违反"需求规范不应包含实现细节"原则。具体问题：
    - Problem Statement 引用了具体代码位置（`context_compaction.py:533-538`、`llm_service.py:315-317`）和内部方法名（`_fit_prompt_budget()`、`_build_task_context()`、`_build_loaded_skills_context()`）。
    - FR-000a 指定了具体类名和方法名（`SkillDiscovery`、`MemoryRetrievalProfile`），而非描述能力需求。
    - FR-000b 指定了具体方法签名变更（`build_context()` MUST 接受 `conversation_budget` 参数）。
    - FR-000c 指定了具体实现方案（"Skill 内容通过 `ContextBudgetPlanner` 预估并在 `_fit_prompt_budget()` 的 `_build_system_blocks()` 中作为系统块参与 token 计算"）。
    - FR-000e 指定了具体算法公式（`len(text)/4` 和 `len(text)/1.5` 之间加权插值）。
    - FR-000f 提及具体库（`tiktoken`）和编码器名称（`cl100k_base`）。
    - FR-017 指定了具体存储字段（`AgentSession.metadata["compressed_layers"]`）。
    - User Story 0 的"架构决策"小节包含了完整的 `ContextBudgetPlanner.plan()` 接口签名和预算分配示例数值。
    - User Story 2 指定了 `AgentSession.metadata["compressed_layers"]` 的具体存储方案。
    - 整个 "Implementation Strategy" 章节（Phase 0-4）详细列出了逐文件的改动范围和设计要点，属于技术设计而非需求规范。
  - **建议**: 将实现细节（文件名、方法签名、算法公式、Phase 分解）移至 `plan.md`（技术规划文档），spec.md 只保留"做什么"和"验收标准"。

- [ ] **CQ-03: 面向非技术利益相关者编写**
  - **FAIL**: 规范大量使用了仅开发者可理解的术语和内部代码引用。示例：
    - "当前 `_fit_prompt_budget()` 的 ~240 种组合暴力搜索" -- 非技术读者无法理解这意味着什么。
    - "`LLMService._build_loaded_skills_context()` 在 `_fit_prompt_budget()` 完成之后才把已加载的 SKILL.md 内容拼接到 system prompt（`llm_service.py:315-317`）" -- 完全是代码级描述。
    - "使用 `asyncio.Task` 后台执行，结果缓存到 `AgentSession`" -- 实现级表述。
    - 表格中引用 `SessionReplay`、`rolling_summary`、`dialogue_limit` 等内部概念时未提供面向用户的解释。
  - **建议**: 在 Problem Statement 和 User Story 主体部分使用面向用户的语言描述问题和期望行为，将技术分析放入独立的 "Technical Context" 附录或 `plan.md`。

- [x] **CQ-04: 所有必填章节已完成**
  - 包含完整章节：Problem Statement、Product Goal、User Scenarios & Testing（6 个 User Story）、Edge Cases（9 项）、Functional Requirements（26 条 FR）、Implementation Strategy（5 个 Phase）、Success Criteria（8 条 SC）、Key Entities（5 个）。

---

## Requirement Completeness

- [x] **RC-01: 无 [NEEDS CLARIFICATION] 标记残留**
  - 全文搜索未发现任何 `[NEEDS CLARIFICATION]` 标记。

- [x] **RC-02: 需求可测试且无歧义**
  - 每个 FR 使用 MUST/SHOULD 明确强制级别。
  - 每个 User Story 包含 Given/When/Then 格式的验收场景。
  - 关键阈值已量化（soft limit ratio、archive_ratio、10 秒超时、50 条合并阈值等）。

- [x] **RC-03: 成功标准可测量**
  - SC-000: token 总数不超 `max_input_tokens` -- 可直接测量。
  - SC-000a: 中文 token 估算误差 < 30% -- 可量化对比。
  - SC-001: Settings 可配置 + fallback 工作 -- 可功能测试。
  - SC-002: 三层结构 + control plane 可审计 -- 可结构化验证。
  - SC-003: LLM 调用次数减少 -- 可计数对比。
  - SC-004: p50 延迟不高于 034 -- 可性能测试。
  - SC-005: 进度笔记可恢复 -- 可端到端测试。
  - SC-006: 审计链 + Subagent 行为 -- 可回归测试。
  - SC-007: 首选组合命中率 > 80% -- 可统计验证。

- [x] **RC-04: 成功标准是技术无关的**
  - SC 描述的是可观测的系统行为和用户可感知的结果（token 不超限、延迟不增加、笔记可恢复），而非实现方式。
  - 注：SC-007 涉及 `_fit_prompt_budget()` 内部行为和命中率，边界偏技术侧，但仍描述的是可观测结果而非实现方式，判定通过。

- [x] **RC-05: 所有验收场景已定义**
  - User Story 0: 3 个验收场景 -- 覆盖预算分配、Skill 纳入、中文估算。
  - User Story 1: 3 个验收场景 -- 覆盖 Settings 展示、模型绑定、fallback 链。
  - User Story 2: 3 个验收场景 -- 覆盖三层划分、Archive 合并、决策保留。
  - User Story 3: 3 个验收场景 -- 覆盖截断触发、LLM 摘要门控、JSON 智能精简。
  - User Story 4: 3 个验收场景 -- 覆盖异步触发、同步 fallback、失败回退。
  - User Story 5: 3 个验收场景 -- 覆盖笔记写入、压缩后注入、重启恢复。

- [x] **RC-06: 边界条件已识别**
  - 9 个 Edge Case 覆盖了：模型不可用 fallback、短对话不压缩、超时回退、笔记数量上限、Subagent 豁免、重要内容保护、预估偏差兜底、多 Skill 超限截断、中英文混合估算。

- [x] **RC-07: 范围边界清晰**
  - 明确标注 Subagent 豁免（保持 034 行为）。
  - 明确 SessionReplay 职责收窄范围。
  - 明确 rolling_summary 语义升级方向。
  - 明确 `_fit_prompt_budget()` 保留为安全兜底。
  - 明确与 Feature 034 的继承关系。

- [x] **RC-08: 依赖和假设已识别**
  - 前置依赖：Feature 034（已实现基础二级压缩）。
  - Blueprint 引用：`docs/blueprint.md` 相关章节。
  - 假设：`tiktoken` 可选依赖（FR-000f 使用 SHOULD 而非 MUST）。
  - 假设：`AgentSession.metadata` 已有存储基础设施。
  - 假设：`AliasRegistry` 支持新增语义别名。

---

## Feature Readiness

- [x] **FR-RDY-01: 所有功能需求有明确的验收标准**
  - 26 条 FR 均使用 MUST/SHOULD 明确强制级别。
  - 每条 FR 的验收可通过对应 User Story 的 Given/When/Then 场景验证。
  - FR-025 明确列出了测试覆盖范围（全局预算分配回归、Skill 注入预算纳入、中文 token 估算精度等 8 项）。

- [x] **FR-RDY-02: 用户场景覆盖主要流程**
  - 6 个 User Story 覆盖了所有核心流程：
    - 全局预算统一管理（US0，P0）
    - 压缩模型独立配置（US1，P1）
    - 分层历史压缩（US2，P1）
    - 两阶段廉价+LLM 压缩（US3，P1）
    - 异步后台压缩（US4，P2）
    - Worker 进度笔记（US5，P2）
  - 优先级分级合理：P0 地基 -> P1 核心能力 -> P2 体验优化。

- [x] **FR-RDY-03: 功能满足 Success Criteria 中定义的可测量成果**
  - SC-000 (token 不超限) <-> FR-000/000a/000b/000c/000d (全局预算统一)
  - SC-000a (中文估算误差 < 30%) <-> FR-000e/000f (token 估算升级)
  - SC-001 (Settings 可配 + fallback) <-> FR-001/002/003/004 (压缩模型配置)
  - SC-002 (三层结构) <-> FR-005/006/007/008/009/009a (分层历史)
  - SC-003 (LLM 调用减少) <-> FR-010/011/012/013 (两阶段压缩)
  - SC-004 (延迟不增) <-> FR-014/015/016/017 (异步压缩)
  - SC-005 (笔记可恢复) <-> FR-018/019/020/021 (进度笔记)
  - SC-006 (审计 + Subagent) <-> FR-022/023 (治理与兼容)
  - SC-007 (首选组合命中率) <-> FR-000/000d (预算协同)
  - 所有 SC 均有对应 FR 支撑，无悬空成功标准。

- [ ] **FR-RDY-04: 规范中无实现细节泄漏**
  - **FAIL**: 与 CQ-02 相同，规范中存在大量实现细节泄漏（参见 CQ-02 详细列表）。
  - 主要泄漏区域：
    - Problem Statement 中的代码行号引用和内部方法名。
    - FR-000a/000b/000c/000e/000f/017 中的具体类名、方法签名、算法公式。
    - User Story 0 的"架构决策"小节中的完整接口设计。
    - User Story 2 的 SessionReplay / rolling_summary 关系表中的内部存储字段。
    - 整个 "Implementation Strategy" 章节（约占规范 30% 篇幅）。
  - **建议**: 将 Implementation Strategy 章节及 FR 中的实现级描述迁移至 `plan.md`，spec.md 中 FR 只保留行为层面的"做什么"描述。

---

## Summary

| Dimension | Total | Passed | Failed |
|-----------|-------|--------|--------|
| Content Quality | 4 | 2 | 2 |
| Requirement Completeness | 8 | 8 | 0 |
| Feature Readiness | 4 | 3 | 1 |
| **Total** | **16** | **13** | **3** |

### Failed Items

| ID | Item | Issue |
|----|------|-------|
| CQ-02 | 无实现细节 | 规范包含大量实现细节：具体文件名/行号、方法签名、算法公式、整个 Implementation Strategy 章节 |
| CQ-03 | 面向非技术利益相关者 | 大量使用内部代码引用和开发者术语，非技术读者无法理解 |
| FR-RDY-04 | 无实现细节泄漏 | 与 CQ-02 同源，Implementation Strategy 约占 30% 篇幅，FR 中包含具体类名/方法签名 |

### Remediation Suggestions

1. **将 Implementation Strategy 章节完整迁移至 `plan.md`**：这是最大的改动，但也是最必要的。spec.md 是需求规范，不应包含逐文件的改动计划和代码级设计要点。
2. **清理 FR 中的实现细节**：FR-000a/000b/000c/000e/000f/017 中引用的具体类名、方法签名、算法公式应替换为行为描述。例如 FR-000b 应改为"压缩服务 MUST 接受由全局预算规划器提供的对话预算，而非直接使用总 token 上限"。
3. **精简 Problem Statement 中的代码引用**：保留问题描述和影响分析，移除 `context_compaction.py:533-538` 等代码行号引用，改为在 plan.md 或 research/ 中记录技术分析。
4. **User Story 中的"架构决策"和"关系表"移至 plan.md**：User Story 主体保留 Given/When/Then 场景和验收标准，技术设计细节移至技术规划文档。
