# Feature 007 技术调研（tech-only）

## 调研目标

- 验证 004/005/006 的真实依赖是否已可无 mock 联调：
  - `ToolBroker`（004）
  - `SkillRunner`（005）
  - `PolicyEngine/ApprovalManager`（006）
- 对标 OpenClaw 与 Agent Zero，在 OctoAgent 当前代码中识别可直接落地的改进。

## 运行时上下文注入

- 特性目录: `.specify/features/007-e2e-m1-integration`
- 参考基线:
  - `docs/blueprint.md`
  - `docs/m1-feature-split.md`
  - `.specify/memory/constitution.md`
  - `spec-driver.config.yaml`
- 重点参考源码:
  - `_references/opensource/openclaw/src/agents/tool-policy-pipeline.ts`
  - `_references/opensource/openclaw/src/gateway/exec-approval-manager.ts`
  - `_references/opensource/openclaw/src/infra/exec-approvals.ts`
  - `_references/opensource/agent-zero/python/helpers/tool.py`
  - `_references/opensource/agent-zero/python/tools/skills_tool.py`
  - `_references/opensource/agent-zero/python/tools/code_execution_tool.py`

[参考路径缺失] `_references/opensource/agent-zero/python/tools/mcp_tool.py`

## 当前代码现状（2026-03-02）

1. 004（ToolBroker）已具备真实执行链路
- `ToolBroker.execute()` 已覆盖 before/after hook、事件写入、不可逆工具无策略点拒绝（FR-010a）。
- `reflect_tool_schema()` + `@tool_contract` 已形成契约反射单一事实源。

2. 005（SkillRunner）可调用 ToolBrokerProtocol，但集成验证仍偏 mock
- SkillRunner 主链路已可执行 `tool_calls -> tool_broker.execute()`。
- 现有 `packages/skills/tests/test_integration.py` 使用 `MockToolBroker`，尚缺“真实 ToolBroker + PolicyHook + Approval”联调证据。

3. 006（Policy/Approval）已具备可恢复与 fail-closed 方向改造
- `PolicyCheckHook` 已可挂到 ToolBroker before hook。
- `ApprovalManager` 已具备事件恢复、allow-once 消费、过期处理。

## 对标结论

### OpenClaw

1. `tool-policy-pipeline.ts`
- 采用多层策略流水线，每层携带 `label`，并有 `stripPluginOnlyAllowlist` 防误封核心工具。
- OctoAgent 当前 2 层策略在 M1 足够，但应在 007 验证中确保“label 可追溯 + fail-closed 不被旁路”。

2. `exec-approval-manager.ts`
- 关键机制：`register()` 幂等、`consumeAllowOnce()` 原子消费、resolved grace period。
- OctoAgent 已实现同类语义，007 需补“真实链路回归测试”证明这些语义在 ToolBroker 实战中生效。

### Agent Zero

1. `skills_tool.py`
- 技能能力通过统一工具入口加载，强调“技能管理与执行解耦”。
- 对 OctoAgent 启示：007 不应把 SkillRunner 强耦合在 Gateway 路由层，可先通过 integration tests 固化组件契约。

2. `code_execution_tool.py`
- 展示了交互式工具执行与会话管理（长会话 shell）。
- 对 OctoAgent 启示：M1 先不引入交互式 session；007 只预留测试挂点，避免 scope 膨胀到 M2。

## 方案评估与建议

### 方案 A（推荐，当前执行）

- 仅做“真实组件联调 + 验收闭环”：
  - 新增 `Feature 007` 集成测试，接入真实 `ToolBroker + PolicyHook + ApprovalManager + SkillRunner`。
  - 不改 Gateway 主运行链路，不引入新的生产耦合。

优点:
- 风险低、范围可控，满足 Blueprint 007 的“串行集成验收”定位。
- 不破坏已稳定的 M0/M1 路由逻辑。

缺点:
- 用户真实聊天路径尚未直接运行 SkillRunner（仍由后续 M1.5/M2 统一推进）。

### 方案 B（不推荐在本轮实施）

- 直接把 Gateway 的 LLM 处理主路径切换到 SkillRunner。

风险:
- 改动面涉及任务状态机、SSE 事件语义、Artifact 输出格式，超出 007 集成验收范围。
- 与当前 `TaskRunner` 持久化调度路径叠加后，回归成本高。

## 本轮技术决策

- 采用方案 A。
- 在 `tests/integration` 新增 Feature 007 联调测试，覆盖：
  1) 结构化输出触发工具调用；
  2) irreversible 工具触发审批并 approve 后继续执行；
  3) schema 反射结果与函数签名一致（利用真实 `reflect_tool_schema`）；
  4) 事件链可观测（至少含 POLICY_DECISION/APPROVAL_* /TOOL_CALL_*）。

## 风险与后续

- 风险 1: Blueprint 里“MCP 一等工具注册”引用的 Agent Zero `mcp_tool.py` 在当前参考树缺失。
  - 处理: 007 先做本地工具注册路径验证；MCP 原生注册留到 M1.5/后续补充。
- 风险 2: SkillRunner 生产模型客户端尚无统一实现。
  - 处理: 007 联调测试使用受控 `QueueModelClient`，避免引入网络不稳定性；后续再补真实 StructuredModelClient。
