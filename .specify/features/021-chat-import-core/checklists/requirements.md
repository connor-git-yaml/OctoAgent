# 需求质量检查表 — Feature 021: Chat Import Core

**生成日期**: 2026-03-07
**检查对象**: `.specify/features/021-chat-import-core/spec.md`
**检查版本**: Draft

---

## Content Quality（内容质量）

| # | 检查项 | 状态 | Notes |
|---|---|---|---|
| CQ-1 | 无不必要的实现细节泄漏 | [x] | spec 说明了 contract、入口和治理边界，但未锁死具体表结构或摘要算法实现。 |
| CQ-2 | 聚焦用户价值和业务结果 | [x] | 用户故事围绕“可导入、可预览、可恢复、可审计、不中毒主 scope”。 |
| CQ-3 | 面向非技术利益相关者也可理解 | [x] | 叙述以导入体验、用户信任和可回看性为主，只在治理边界保留必要技术术语。 |
| CQ-4 | 必填章节完整 | [x] | 包含 Problem Statement、Pre-conditions、User Scenarios、Edge Cases、Requirements、Key Entities、Success Criteria、Clarifications、Scope Boundaries。 |

**Content Quality 小计**: 4 / 4 通过

---

## Requirement Completeness（需求完整性）

| # | 检查项 | 状态 | Notes |
|---|---|---|---|
| RC-1 | 无 `[NEEDS CLARIFICATION]` 残留 | [x] | 入口、dry-run、scope、原文 artifact、SoR 治理边界均已明确。 |
| RC-2 | 需求可测试且无歧义 | [x] | 每个 P1/P2 Story 都定义了独立测试路径，重复执行、恢复、proposal 失败等都有验收场景。 |
| RC-3 | 成功标准可测量 | [x] | SC-001 到 SC-006 可通过 CLI、数据库状态和 artifact / event 校验验证。 |
| RC-4 | 成功标准与技术实现解耦 | [x] | 成功标准聚焦用户能否安全导入与回看结果，而非限定某个具体算法。 |
| RC-5 | 验收场景覆盖主要流程 | [x] | 覆盖 dry-run、真实导入、重复执行、cursor 恢复、scope 隔离与 proposal 治理。 |
| RC-6 | 边界条件已识别 | [x] | 已识别 message id 缺失、部分窗口失败、大文本 artifact 化、能力降级等边界。 |
| RC-7 | 范围边界清晰 | [x] | 已明确排除具体 adapter、Web 面板、回滚与自动订阅。 |
| RC-8 | 依赖和假设已识别 | [x] | 已显式依赖 020、022 和既有 Event / Artifact / NormalizedMessage 体系。 |

**Requirement Completeness 小计**: 8 / 8 通过

---

## Feature Readiness（特性就绪度）

| # | 检查项 | 状态 | Notes |
|---|---|---|---|
| FR-A | 所有功能需求有对应的用户场景 | [x] | FR-001~FR-018 均可追溯到四个 User Story 与 Edge Cases。 |
| FR-B | P1 范围足以形成最小可用交付 | [x] | CLI 入口 + dry-run + dedupe + report + import-to-memory 就可形成 M2 MVP。 |
| FR-C | 规范保留并发边界 | [x] | 明确 021 只消费 020 contract，不吞并具体 source adapter 或 Web 管理面。 |
| FR-D | 无阻断级 spec 风险 | [x] | 当前主要风险是上游文档未同步入口缺口，已在 spec 中显式记录并要求设计批准后回写。 |

**Feature Readiness 小计**: 4 / 4 通过

---

## 汇总

| 维度 | 通过 | 总计 | 通过率 |
|---|---|---|---|
| Content Quality | 4 | 4 | 100% |
| Requirement Completeness | 8 | 8 | 100% |
| Feature Readiness | 4 | 4 | 100% |
| **合计** | **16** | **16** | **100%** |

---

## 执行摘要

- `GATE_RESEARCH`: 已满足（离线调研 + 在线调研完成）
- `GATE_DESIGN`: 可以进入用户审批
- 当前需要确认的关键设计方向：
  - 是否批准把 021 从“导入内核”提升为“导入内核 + `octo import chats` + `--dry-run` + ImportReport”
  - 是否批准后续在实现前回写 `docs/blueprint.md` 与 `docs/m2-feature-split.md`，同步上述入口缺口
