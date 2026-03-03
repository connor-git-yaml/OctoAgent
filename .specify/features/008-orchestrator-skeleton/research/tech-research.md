# 技术调研报告: Feature 008 Orchestrator Skeleton（单 Worker）

**特性分支**: `codex/feat-008-orchestrator-skeleton`
**调研日期**: 2026-03-02
**调研模式**: 离线 + 在线（Perplexity 补充）
**产品调研基础**: [product-research.md](product-research.md)

## 1. 调研目标

**核心问题**:
- Q1: 在当前代码结构下，Orchestrator 最小侵入落点在哪里？
- Q2: 控制平面契约如何定义，才能兼容后续多 Worker？
- Q3: 控制平面事件如何写入，确保 C2/C8 可追溯？
- Q4: 高风险 gate 如何接入而不冲击 Feature 006 策略体系？

**MVP 范围（来自产品调研）**:
- 强类型协议 + 单 Worker 路由
- 派发闭环 + 三类事件
- 高风险 gate 最小接入
- 失败分类 + 测试闭环

## 2. 架构方案对比

| 维度 | 方案 A: 保持 TaskRunner 直连 TaskService | 方案 B: 引入 Gateway 内 OrchestratorService（推荐） | 方案 C: 新建独立 kernel 进程 |
|------|-----------------------------------------|----------------------------------------------------|-----------------------------|
| 改造成本 | 低 | 中 | 高 |
| 对后续可扩展性 | 低 | 高 | 高 |
| 事件可观测性 | 中 | 高 | 高 |
| 与当前代码兼容 | 高 | 高 | 低 |
| 风险 | 控制平面继续缺位 | 可控 | 范围膨胀 |

**推荐**: 方案 B（Gateway 内 OrchestratorService）

**理由**:
1. 以最小改动切入现有主链路（替换 `TaskRunner` 调用点）。
2. 可完整满足 Feature 008 的契约与事件要求。
3. 不提前引入独立进程复杂度，避免越过 M1.5 范围。

## 3. 参考实现证据

### Agent Zero
- `/_references/opensource/agent-zero/python/tools/call_subordinate.py`
  - 证据: 先创建委派上下文，再执行 subordinate，再回收结果。
  - 启示: 控制平面要有“派发前封装 + 派发后回收”的明确阶段。
- `/_references/opensource/agent-zero/python/helpers/subagents.py`
  - 证据: 支持 default/user/project 层级合并。
  - 启示: 路由层应预留能力匹配和覆盖机制，不能把单 Worker 写死为不可扩展格式。

### AgentStudio
- `/_references/opensource/agentstudio/backend/src/services/taskExecutor/index.ts`
  - 证据: 通过工厂初始化统一执行器，并控制单例生命周期。
  - 启示: 我们可用 `OrchestratorService` 作为统一入口，由 `TaskRunner` 持有。

### Pydantic AI
- `/_references/opensource/pydantic-ai/docs/multi-agent-applications.md`
  - 证据: delegation/handoff 分层，强调“控制流由应用层组织”。
  - 启示: Orchestrator 不应绑死具体模型/工具执行，只负责路由、监督与回传。

### OpenClaw
- `/_references/opensource/openclaw/src/channels/command-gating.ts`
  - 证据: 控制命令 gate 是独立决策函数，输入简单、输出明确。
  - 启示: 高风险 gate 应保持独立判定，不与执行器强耦合。

## 3.5 在线调研补充（Perplexity，2026-03-02）

> 调研点数量: 3（满足 `.specify/project-context.md` 对“0-5 个在线调研点”的约束）

### 调研点 A: Dispatch Envelope 版本与跳数保护
- 结论: 多 Agent 编排的 envelope 常规实践是显式带 `contract_version + hop_count + max_hops/ttl`，在每次转发执行跳数保护。
- 对本 Feature 的影响: 保持 `DispatchEnvelope` 当前字段设计不变，并在 router 层执行硬校验。

### 调研点 B: Single Worker Router 的失败分类
- 结论: 单 worker 编排模式推荐将失败划分为 retryable / non-retryable，避免盲目重试。
- 对本 Feature 的影响: 保持 `WorkerResult.retryable` 为必填字段，worker 缺失/协议错误归类为 non-retryable。

### 调研点 C: 控制平面可观测事件模式
- 结论: 控制平面审计日志通常覆盖 decision -> dispatched -> returned 三段。
- 对本 Feature 的影响: 保持新增事件 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`，不折叠为通用 ERROR 事件。

## 4. 设计模式推荐

1. **Typed Envelope Pattern**
- 将 `DispatchEnvelope` 作为唯一派发载体，承载 `contract_version/route_reason/worker_capability/hop_count/max_hops`。

2. **Router + Worker Adapter Pattern**
- Router 仅负责“选谁 + 为什么”；Worker 适配器负责“怎么执行 + 返回什么结果”。

3. **Evented Control Plane Pattern**
- 在 Orchestrator 层显式写入 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`，不依赖下游隐式日志。

4. **Minimal Gate Integration Pattern**
- 高风险 gate 仅在派发入口判断，低风险直通，不重写工具级审批链路。

## 5. 技术风险清单

| # | 风险描述 | 概率 | 影响 | 缓解策略 |
|---|---------|------|------|---------|
| 1 | 控制平面与现有 TaskService 事件序列冲突 | 中 | 中 | Orchestrator 仅新增事件，不修改既有模型调用事件语义 |
| 2 | 高风险 gate 与任务状态机不一致 | 中 | 高 | deny 时显式推进状态到 FAILED，附原因 |
| 3 | hop_count 校验遗漏导致潜在循环 | 低 | 高 | 在 route 前强制校验，超限直接失败并标记 non-retryable |
| 4 | Worker 不可用导致任务悬挂 | 中 | 高 | 路由前检查 worker registry，缺失立即失败并落盘事件 |

## 6. Constitution 约束检查

| 约束 | 兼容性 | 说明 |
|------|--------|------|
| C1 Durability First | ✅ | 控制平面事件全部落盘 |
| C2 Everything is an Event | ✅ | 三类新增事件覆盖决策/派发/回传 |
| C4 Two-Phase Side-effect | ✅ | 高风险先 gate 再派发 |
| C6 Degrade Gracefully | ✅ | worker 缺失/异常均可失败回传，不阻塞全系统 |
| C8 Observability | ✅ | 每次派发均有结构化事件链 |

## 7. 结论与建议

### 总结
推荐在现有 Gateway 内新增 `OrchestratorService + SingleWorkerRouter + LLMWorkerAdapter`，并由 `TaskRunner` 调用该服务。此方案在风险、可交付性和扩展性之间平衡最佳。

### 对产研汇总的建议
- 以“契约稳定性 + 事件可追溯 + 最小侵入”作为 MVP 三大验收轴。
- 将“多 Worker 调度算法”和“运行时预算/中断细节”明确排除到 Feature 009。
