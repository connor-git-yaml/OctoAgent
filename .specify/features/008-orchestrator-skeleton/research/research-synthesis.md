# 产研汇总: Feature 008 Orchestrator Skeleton（单 Worker）

**特性分支**: `codex/feat-008-orchestrator-skeleton`
**汇总日期**: 2026-03-02
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md)
**执行者**: 主编排器（inline）
**在线调研补充**: Perplexity 3 个调研点（2026-03-02）

## 1. 产品×技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---------|-----------|-----------|-----------|---------|------|
| 强类型控制平面协议 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 单 Worker 路由与派发 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 三类控制平面事件 | P1 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP |
| 高风险 gate 最小接入 | P1 | 中 | 中 | ⭐⭐ | 纳入 MVP（最小版本） |
| 多 Worker 负载均衡 | P2 | 中 | 高 | ⭐ | 延后 |
| 智能路由策略学习 | P3 | 低 | 高 | ⭐ | 延后 |

## 2. 可行性评估

### 技术可行性
- 当前代码已具备 `TaskRunner` 调度入口和 Event Store 基础设施。
- 通过新增服务层可完成派发主循环，不需要重构整个 Gateway。
- `EventType`/payload 可扩展，风险可控。
- 在线调研结果与本地设计一致：控制平面契约需显式版本与跳数保护，失败应保留 retryable 分类。

### 资源评估
- **预估工作量**: 1 个 feature 周期（文档 + 代码 + 测试）
- **关键技能需求**: asyncio、Pydantic、事件溯源、FastAPI 测试
- **外部依赖**: 无新增三方依赖（复用现有栈）

### 约束与限制
- 本 feature 不实现多 Worker 调度算法。
- 本 feature 不引入独立 kernel 进程与跨进程协议。
- 本 feature 不替代 Feature 006 的工具级审批链路。

## 3. 风险评估

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|------|------|------|------|---------|------|
| 1 | 范围膨胀到 Feature 009 | 产品 | 中 | 高 | 严格锁定单 Worker skeleton 范围 | 监控中 |
| 2 | 任务状态流转与 gate 冲突 | 技术 | 中 | 中 | deny 统一走 FAILED 路径并附原因 | 监控中 |
| 3 | 事件序列回归影响既有测试 | 技术 | 中 | 中 | 增量新增 ORCH 事件，不修改既有事件语义 | 监控中 |
| 4 | hop_count 保护遗漏 | 技术 | 低 | 高 | route 前硬校验并测试覆盖 | 已规划 |

## 4. 最终推荐方案

### 推荐架构
- `TaskRunner` 调用 `OrchestratorService.dispatch()`。
- `OrchestratorService` 负责：
  - 构造 `OrchestratorRequest`
  - 走 `PolicyGate`（仅高风险）
  - 走 `SingleWorkerRouter` 产出 `DispatchEnvelope`
  - 写入 `ORCH_DECISION/WORKER_DISPATCHED/WORKER_RETURNED`
  - 调用 `LLMWorkerAdapter` 执行并回传 `WorkerResult`

### 推荐实施路径
1. **Phase 1 (MVP)**: 模型 + Router + Orchestrator + 事件 + TaskRunner 接入
2. **Phase 2**: 高风险 gate 细化（审批 token/策略注入）
3. **Phase 3**: 多 Worker 与策略路由（Feature 009+）

## 5. MVP 范围界定

### 纳入
- 协议模型: `OrchestratorRequest/DispatchEnvelope/WorkerResult`
- 单 Worker 派发闭环
- 三类控制平面事件
- 高风险 gate 最小接入
- 单元/集成测试

### 排除
- 多 Worker 选择算法
- Worker Runtime budget/max_steps
- Docker 执行隔离细节
- Checkpoint/Resume 引擎

## 6. 结论

Feature 008 应以“控制平面契约冻结 + 事件可观测 + 高风险最小 gate”作为交付核心。该方案可稳定衔接 Feature 009/010，且不会破坏现有 M1 任务主链路。
