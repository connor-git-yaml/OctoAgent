# Requirements Checklist: Feature 018 — A2A-Lite Envelope + A2AStateMapper

**Purpose**: 验证 `spec.md` 是否足够清晰、可测、可直接进入技术规划与实现。
**Created**: 2026-03-07
**Feature**: `.specify/features/018-a2a-lite-envelope/spec.md`

---

## Content Quality

- [x] CHK001 无运行时实现细节泄漏
  - Notes: spec 描述 contract、映射语义、fixture 与 guard 行为，没有绑定具体 broker、队列或网络实现。

- [x] CHK002 聚焦下游消费者价值
  - Notes: 三个 User Story 都以“后续 Feature 开发者如何复用 contract”为中心，而不是泛泛描述协议概念。

- [x] CHK003 面向非单一模块
  - Notes: spec 同时覆盖 kernel、worker、subagent 和测试消费者，避免只站在某一个包内部视角描述。

- [x] CHK004 必填章节完整
  - Notes: 已包含 Problem Statement、User Scenarios、Edge Cases、Requirements、Key Entities、Success Criteria、Clarifications 与 Scope Boundaries。

## Requirement Completeness

- [x] CHK005 无 `[NEEDS CLARIFICATION]` 残留
  - Notes: 所有关键设计点已在 Clarifications 自动冻结。

- [x] CHK006 所有核心需求可测试
  - Notes: 每条核心 FR 都可通过 schema / mapper / fixture / guard 的单元测试验证。

- [x] CHK007 成功标准可量化
  - Notes: SC 以“六类消息全部通过”“四类 part 均有映射测试”“四类 guard 结果均可区分”等可验证结果表达。

- [x] CHK008 边界条件已识别
  - Notes: 已覆盖 schema_version、hop_limit、语义压缩、storage_ref、JSON part 等关键边界。

- [x] CHK009 范围边界清晰
  - Notes: 明确排除 transport、durable queue、真实接线与 core DB migration。

## Constitution Alignment

- [x] CHK010 C1 Durability First 对齐
  - Notes: 本特性只冻结 contract，不引入新的“只存在于内存但被误认为 durable”的任务状态；真实 durable ledger 被明确列为 out of scope。

- [x] CHK011 C3 Tools are Contracts 对齐
  - Notes: Feature 018 的核心就是冻结可复用 contract / fixture，满足“单一事实源”要求。

- [x] CHK012 C6 Degrade Gracefully 对齐
  - Notes: 对不支持版本、hop 超限、replay 等情况定义了协议级守卫，而不是让后续业务层随机失败。

- [x] CHK013 C8 Observability is a Feature 对齐
  - Notes: trace、idempotency、message_id、metadata 都是 envelope 一级字段，便于后续观测与审计。

- [x] CHK014 原则 14 A2A Compatibility 对齐
  - Notes: spec 明确要求 `A2AStateMapper`、artifact mapping 和 `internal_status` 元数据补偿。

## Readiness

- [x] CHK015 可以直接进入 plan / implement
  - Notes: 技术边界、代码落点和测试方向都已明确，无阻断性缺口。

---

## Summary

| 维度 | 检查项数 | 通过 | 未通过 |
|---|---:|---:|---:|
| Content Quality | 4 | 4 | 0 |
| Requirement Completeness | 5 | 5 | 0 |
| Constitution Alignment | 5 | 5 | 0 |
| Readiness | 1 | 1 | 0 |
| **Total** | **15** | **15** | **0** |
