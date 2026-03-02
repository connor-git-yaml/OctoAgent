# 技术决策研究: Feature 005 — Pydantic Skill Runner

**Feature Branch**: `codex/feat-005-pydantic-skill-runner`
**日期**: 2026-03-02
**输入**: `spec.md` + `research/tech-research.md` + `docs/blueprint.md` + `docs/m1-feature-split.md`

---

## Decision 1: Runner 执行模型

**决策**: 采用 Free-Loop SkillRunner（step loop）而非直接引入 Graph Engine。

**理由**:
1. Blueprint 对 M1 的 005 明确是 SkillRunner，不是 Pipeline Engine。
2. 可直接吸收 AgentZero 双层循环经验（完成信号 + 步数上限）。
3. 避免在 M1 提前引入节点编排复杂度。

**替代方案**:
- 直接用 pydantic-graph 建 Skill 执行器。缺点：实现复杂度高，且与 005 范围不一致。

---

## Decision 2: 模型调用抽象

**决策**: 在 `packages/skills` 定义 `StructuredModelClientProtocol`，SkillRunner 依赖协议而非绑定具体 Provider。

**理由**:
1. 与 Feature 004 的 Protocol 化思路一致，可用 mock 客户端做稳定测试。
2. 当前 `LiteLLMClient.complete()` 偏文本输出，先通过协议解耦，便于后续替换为原生 structured output。
3. 降低 007 前的跨包耦合风险。

---

## Decision 3: ToolBroker 集成边界

**决策**: SkillRunner 仅依赖 `ToolBrokerProtocol.execute()` 和 `ToolResult` 锁定字段。

**理由**:
1. 避免耦合 Broker 内部 Hook 实现。
2. 对齐 004 的契约稳定目标（供 005/006 并行开发）。

---

## Decision 4: 终止与循环控制

**决策**: 终止机制采用三重保护：`complete 信号` + `max_steps` + `repeat_signature_threshold`。

**理由**:
1. `complete` 对齐 AgentZero `break_loop`。
2. `repeat signature` 对齐 OpenClaw 反循环策略。
3. `max_steps` 作为兜底，防止隐性死循环。

---

## Decision 5: 异常分流语义

**决策**: 错误按三类分流：
- `SkillRepeatError`（可重试）
- `SkillValidationError`（结构化反馈后重试）
- `ToolExecutionError`（工具侧错误）

**理由**:
1. 对齐 Blueprint §8.4.3 的异常分流要求。
2. 对齐 Constitution C13（失败可解释）。

---

## Decision 6: 上下文预算防护

**决策**: ToolResult 回灌前执行 `ContextBudgetPolicy`；超限优先使用 `artifact_ref` 或摘要。

**理由**:
1. 直接对齐 OpenClaw context guard 借鉴项。
2. 满足 Constitution C11（Context Hygiene）。

---

## Decision 7: 生命周期钩子

**决策**: 提供 SkillRunner 生命周期 hook 接口：
- `skill_start/skill_end`
- `before_llm_call/after_llm_call`
- `before_tool_execute/after_tool_execute`

**理由**:
1. 吸收 AgentZero extension 点经验。
2. 便于 observability 与后续策略扩展。

---

## Decision 8: Skill 文档注入

**决策**: 支持 `description_md`，缺失时降级为短描述并告警，不阻断执行。

**理由**:
1. 吸收 AgentZero `SKILL.md` 设计。
2. 满足 Constitution C6（Degrade Gracefully）。

---

## Decision 9: 事件与审计

**决策**: 在 core 枚举扩展 Skill 级事件：
- `SKILL_STARTED`
- `SKILL_COMPLETED`
- `SKILL_FAILED`

**理由**:
1. Tool 级事件已存在，Skill 级事件补齐审计闭环。
2. 满足 Constitution C2/C8。

---

## Decision 10: 代码落位

**决策**: 新增 `packages/skills` 工作区包，不侵入 gateway/app 层。

**理由**:
1. 与 `core/provider/tooling` 的包分层一致。
2. 降低 M1 阶段变更面，先确保包级可测闭环。
