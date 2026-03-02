# Implementation Plan: Feature 007 端到端集成 + M1 验收

## 1. 实施目标

在不扩大 M1 范围的前提下，完成 004/005/006 的真实联调与验收证据闭环：

- SkillRunner 结构化输出 -> ToolBroker 执行
- ToolBroker before hook -> PolicyCheckHook
- ask 决策 -> ApprovalManager 审批等待/放行
- 事件链落盘可追溯

## 2. 技术上下文

- 代码基线: `master`（分支 `feat/007-e2e-m1-integration`）
- 关键模块:
  - `octoagent/packages/tooling`
  - `octoagent/packages/skills`
  - `octoagent/packages/policy`
- 测试层选择: `octoagent/tests/integration`

## 3. 设计决策

1. **不改主链路**
- 007 只做集成验收，不重写 Gateway LLM 主处理逻辑。

2. **测试优先集成**
- 优先新增 integration tests，必要时仅补最小 glue code。

3. **受控模型输出**
- 使用受控 `QueueModelClient`（测试内）模拟结构化输出，避免外部模型不稳定导致假失败。

## 4. 文件变更计划

### 新增

- `octoagent/tests/integration/test_f007_e2e_integration.py`
- `.specify/features/007-e2e-m1-integration/tasks.md`
- `.specify/features/007-e2e-m1-integration/verification/verification-report.md`

### 更新

- `.specify/features/007-e2e-m1-integration/spec.md`
- `.specify/features/007-e2e-m1-integration/plan.md`
- `.specify/features/007-e2e-m1-integration/checklists/requirements.md`

## 5. 验证策略

- 第一层（Feature 007 新增）
  - schema reflection 契约验证
  - SkillRunner+ToolBroker+Policy+Approval 真实链路验证
- 第二层（回归）
  - 005 现有 skill tests
  - 006 approval/policy integration tests
  - 004 tooling tests（必要子集）

## 6. 风险与缓解

- 风险: MCP 一等工具原生注册仍属后续里程碑范围
  - 缓解: 已补齐 Agent Zero 参考路径证据（`mcp_handler.py` / `agent.system.mcp_tools.md`），本轮不臆造实现。
- 风险: 并发审批事件序列冲突
  - 缓解: 使用现有重试语义，测试覆盖事件链完整性。

## 7. GATE 结论

- GATE_DESIGN: PASS（依据用户最新指令继续执行，并完成 Phase 1-3 优化复盘）
