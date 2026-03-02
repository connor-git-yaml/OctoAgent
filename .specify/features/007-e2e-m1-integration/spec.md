# Feature Spec: Feature 007 端到端集成 + M1 验收（Phase 1-3 优化版）

**Feature Branch**: `feat/007-e2e-m1-integration`
**Created**: 2026-03-02
**Status**: Draft (Design Gate Ready)
**Input**: 基于 `docs/blueprint.md` 与 `docs/m1-feature-split.md` 的 Feature 007 规划，完成 004/005/006 的真实联调与 M1 验收闭环。

## Phase 1-3 复盘优化

本版相较初稿的改进:

1. 补齐产品视角调研（新增 `research/product-research.md`），避免仅技术视角导致范围偏离。
2. 补齐产研汇总（新增 `research/research-synthesis.md`），把调研结论结构化为可执行决策。
3. 强化范围锁定（IN/OUT），明确 007 是“集成验收特性”而非“主链路重构特性”。

## 范围边界

### IN

- 以真实组件完成 004/005/006 端到端集成验证：
  - Tool contract/schema reflection（004）
  - SkillRunner 结构化输出与工具调用（005）
  - PolicyEngine + Approval 流程（006）
- 补齐 Feature 007 规格、计划、任务、验证报告制品。
- 补充集成测试，覆盖 blueprint M1 核心验收项的可执行证据。

### OUT

- 不改造 Gateway 聊天主链路为 SkillRunner 驱动（该项归 M1.5/M2）。
- 不在本轮引入完整 MCP 一等工具注册实现（已补齐参考路径，但该能力仍属后续里程碑范围）。
- 不扩展多渠道（Telegram/WeChat）新能力。

## 用户故事

### User Story 1 - 研发者验证真实链路

作为 OctoAgent 研发者，我希望在测试中直接用真实 `ToolBroker + PolicyHook + ApprovalManager + SkillRunner` 运行链路，
从而在不依赖 mock 的情况下确认 Feature 004/005/006 的契约可组合。

**独立验收测试**:
- 新增 integration test：结构化输出触发工具调用并成功完成。
- 新增 integration test：irreversible 工具触发审批，approve 后继续执行并成功。

### User Story 2 - 架构师确认 M1 验收闭环

作为架构负责人，我希望有一份明确的验收映射和验证报告，
从而确认 Blueprint §14 中 M1 的关键验收标准已具备证据。

**独立验收测试**:
- verification report 明确映射每条 M1 核心验收项到测试证据。

## 功能需求（FR）

- **FR-001**: MUST 提供 Feature 007 的真实联调集成测试，覆盖 `SkillRunner -> ToolBroker -> PolicyHook -> Approval` 链路。
- **FR-002**: MUST 在集成测试中使用真实 `reflect_tool_schema()` + `@tool_contract`，验证 schema 与函数签名一致。
- **FR-003**: MUST 验证 irreversible 工具在 Policy 下进入审批流程，并在 approve 后继续执行。
- **FR-004**: MUST 产出 Feature 007 全套 spec-driver 制品（`spec.md/plan.md/tasks.md/checklists/verification`）。
- **FR-005**: SHOULD 在 verification report 中明确标注 MCP 原生注册未纳入本轮范围，并给出已对齐参考路径证据（`mcp_handler.py` / `agent.system.mcp_tools.md`）。

## 设计替代方案评审

### 方案 A（采纳）

- 目标: 通过新增集成测试实现真实组件联调验收。
- 影响面: 以测试与少量 glue code 为主，不改线上主路径。
- 风险: 低，可控。

### 方案 B（未采纳）

- 目标: 直接把 Gateway 主路径切换为 SkillRunner 驱动。
- 问题:
  - 超出 Feature 007 定位，属于 M1.5 级别重构；
  - 会叠加 TaskRunner/状态机/SSE 语义变化，回归成本高。

结论: 采用方案 A。

## 非功能需求（NFR）

- **NFR-001**: 测试必须稳定可重复执行，不依赖外网模型调用。
- **NFR-002**: 新增改动不得破坏现有 004/005/006 回归测试。
- **NFR-003**: 事件链可观测（至少含 POLICY_DECISION / APPROVAL_* / TOOL_CALL_*）。

## 成功标准（SC）

- **SC-001**: `LLM(结构化输出) -> ToolBroker 执行 -> SkillRunner 完成` 集成测试通过。
- **SC-002**: irreversible 工具审批流程集成测试通过（含 approve 后执行）。
- **SC-003**: schema reflection 契约测试在真实工具函数上通过。
- **SC-004**: Feature 007 验证报告完成并可映射到 Blueprint §14 的 M1 验收核心条目。

## 约束与假设

- 假设 004/005/006 的核心能力已在各自 feature 中交付；007 仅负责“联调 + 验收闭环”。
- 对 MCP 一等工具注册保持范围外处理，仅记录后续里程碑与参考路径证据，不做臆造实现。
