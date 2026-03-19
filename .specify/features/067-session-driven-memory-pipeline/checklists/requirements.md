# Requirements Quality Checklist

**Feature**: 067-session-driven-memory-pipeline
**Spec**: spec.md
**Date**: 2026-03-19
**Status**: FAIL

---

## Content Quality

- [ ] **CQ-01: 无实现细节** — 规范中不应提及具体语言、框架、API 实现方式
  - Notes: spec.md 包含大量实现细节：
    - FR-001 指定了触发点位置 "`record_response_context()` 流程末尾"
    - FR-003 指定了具体字段名 `memory_cursor_seq`（整型，默认 0）
    - FR-009~FR-012 指定了要删除的具体方法名（`_record_memory_writeback`、`_persist_compaction_flush`、`FlushPromptInjector`、`_auto_consolidate_after_flush`）
    - Key Entities 章节描述了具体数据模型字段（`turn_seq`、`kind`、`role`、`tool_name`、`summary`、`recent_transcript`、`rolling_summary`）
    - Acceptance Scenarios 中出现了代码级引用（`cursor_seq=5`、`turn_seq > 5`）
    - Edge Cases 中提到 `max_tokens` 参数、`subject_key` 去重等实现细节
  - 建议: 将实现细节（方法名、字段名、触发点位置）移至技术规划文档（plan.md），spec.md 应聚焦于"做什么"和"为什么"，而非"怎么做"和"改哪里"

- [x] **CQ-02: 聚焦用户价值和业务需求** — 规范以用户价值为核心驱动
  - Notes: User Stories 整体围绕用户可感知的价值编写（自动记忆提取、消除碎片化、崩溃恢复保证数据不丢失）

- [ ] **CQ-03: 面向非技术利益相关者编写** — 非技术人员应能理解规范主旨
  - Notes: User Stories 部分尚可，但 Requirements 和 Edge Cases 中大量技术术语（fire-and-forget、cursor_seq、propose-validate-commit、evidence_ref、max_tokens、幂等性）使得非技术读者难以理解

- [x] **CQ-04: 所有必填章节已完成** — User Scenarios & Testing、Requirements、Success Criteria 均已填写
  - Notes: 三个必填章节均完整。额外包含 Edge Cases 和 Key Entities 章节

---

## Requirement Completeness

- [x] **RC-01: 无 [NEEDS CLARIFICATION] 标记残留** — 全文无未解决的澄清标记
  - Notes: 全文搜索确认无 [NEEDS CLARIFICATION] 标记

- [x] **RC-02: 需求可测试且无歧义** — 每条需求有明确的 MUST/SHOULD 标记且可验证
  - Notes: FR-001~FR-020 均使用 MUST/SHOULD 标记，每个 User Story 配有具体的 Acceptance Scenarios（Given/When/Then 格式）

- [x] **RC-03: 成功标准可测量** — Success Criteria 中的每项成果可被客观验证
  - Notes: SC-001~SC-007 均描述了可观测的结果状态

- [ ] **RC-04: 成功标准是技术无关的** — 成功标准不应依赖于具体实现方案
  - Notes: SC-007 提到"被废弃的 4 条旧路径（`_record_memory_writeback`、FLUSH maintenance、`FlushPromptInjector`、`_auto_consolidate_after_flush`）的代码和调用点被完全移除，不留下死代码"，这直接引用了代码级实现细节，属于实现方案而非业务成果
  - 建议: SC-007 应改为"系统中不存在除统一管线和 memory.write 之外的其他记忆写入路径"，具体要删除哪些方法属于 plan.md 的范畴

- [x] **RC-05: 所有验收场景已定义** — 每个 User Story 有完整的 Given/When/Then 场景
  - Notes: 6 个 User Stories 共定义了 17 个 Acceptance Scenarios，覆盖正常流程和关键分支

- [x] **RC-06: 边界条件已识别** — Edge Cases 章节覆盖异常和极端场景
  - Notes: 识别了 8 种边界条件，包括 LLM 不可用、Session 关闭、大量信息、工具压缩、并发写入、cursor 不一致、过渡期、空 Session

- [x] **RC-07: 范围边界清晰** — 明确定义了特性包含和不包含的内容
  - Notes: 明确了废弃 4 条路径、保留 3 条通道（统一管线、memory.write、Scheduler/手动 Consolidation），范围边界清晰

- [x] **RC-08: 依赖和假设已识别** — 关键依赖和前置假设有文档记录
  - Notes: 依赖于 propose-validate-commit 治理流程、AgentSession/Turn 数据模型、LLM 服务可用性等，相关信息分散在规范各处已覆盖

---

## Feature Readiness

- [x] **FE-01: 所有功能需求有明确的验收标准** — FR 与 Acceptance Scenarios 有对应关系
  - Notes: FR-001~FR-020 均可通过对应的 Acceptance Scenarios 验证

- [x] **FE-02: 用户场景覆盖主要流程** — User Stories 覆盖核心使用场景
  - Notes: 6 个 User Stories 覆盖了：自动提取（US1）、旧路径废弃（US2）、Cursor 恢复（US3）、LLM 智能提取（US4）、Fragment 角色转变（US5）、兜底机制（US6）

- [x] **FE-03: 功能满足 Success Criteria** — User Stories 的实现能达成 SC 中的可测量成果
  - Notes: US1->SC-001/SC-002/SC-004, US2->SC-003/SC-007, US3->SC-005, US4->SC-006, US6 提供韧性保障

- [ ] **FE-04: 规范中无实现细节泄漏** — spec.md 不包含属于 plan.md 的技术实现信息
  - Notes: 与 CQ-01 同源问题。规范中包含的实现细节应迁移至 plan.md：
    - 具体方法名和删除指令（FR-009~FR-012）
    - 具体字段名和类型定义（FR-003 中的 memory_cursor_seq 整型默认 0）
    - 具体触发点位置（FR-001 中的 record_response_context() 流程末尾）
    - 数据模型字段列表（Key Entities 中的 turn_seq、kind、role 等）

---

## Summary

| Dimension | Total | Passed | Failed |
|-----------|-------|--------|--------|
| Content Quality | 4 | 2 | 2 |
| Requirement Completeness | 8 | 7 | 1 |
| Feature Readiness | 4 | 3 | 1 |
| **Total** | **16** | **12** | **4** |

### Failed Items

| ID | Issue | Suggested Fix |
|----|-------|---------------|
| CQ-01 | 规范包含大量实现细节（方法名、字段名、触发点位置） | 将实现细节移至 plan.md，spec.md 聚焦于行为描述 |
| CQ-03 | 技术术语过多，非技术读者难以理解 | 在 Requirements 中用行为描述替代技术术语 |
| RC-04 | SC-007 引用了具体代码方法名 | 改为"系统中不存在多余的记忆写入路径"等业务描述 |
| FE-04 | 实现细节泄漏到需求规范中 | 与 CQ-01 同源，统一修复 |
