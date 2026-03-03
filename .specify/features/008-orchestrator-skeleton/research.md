# Feature 008 技术决策记录（research.md）

## 决策列表

### D1: Orchestrator 落点
- **决策**: 新增 `apps/gateway/services/orchestrator.py`，由 `TaskRunner` 调用。
- **理由**: 最小侵入现有链路；无需新增进程边界。
- **备选**: 独立 `apps/kernel` 进程（延后）。

### D2: 协议模型位置
- **决策**: 协议模型放在 `packages/core/models/orchestrator.py`。
- **理由**: 作为跨层共享域模型，归 core 更稳定。
- **备选**: 放 gateway（复用性差）。

### D3: 路由策略
- **决策**: `SingleWorkerRouter` 采用 rule-based 默认路由（按 capability）。
- **理由**: Feature 008 明确为单 Worker skeleton。
- **备选**: 权重路由（超范围）。

### D4: 跳数保护
- **决策**: route 前检查 `hop_count <= max_hops`，超限立即失败。
- **理由**: 防止未来多 Worker 递归委派失控。
- **备选**: 仅日志告警（不满足硬保护）。

### D5: 高风险 gate
- **决策**: 新增 `OrchestratorPolicyGate`，仅在 `risk_level=HIGH` 时执行阻断判定。
- **理由**: 满足“仅高风险 gate，不重写全量策略”。
- **备选**: 直接复用工具级审批链（耦合过深）。

### D6: 事件策略
- **决策**: 新增 `ORCH_DECISION`、`WORKER_DISPATCHED`、`WORKER_RETURNED`。
- **理由**: 控制平面必须具备完整可观测链。
- **备选**: 复用 ERROR/STATE_TRANSITION（语义不清）。

### D7: Worker 适配方式
- **决策**: `LLMWorkerAdapter` 复用 `TaskService.process_task_with_llm`。
- **理由**: 避免重复实现状态推进和 artifact 落盘逻辑。
- **备选**: 重写 worker 执行器（重复代码、风险高）。

### D8: 失败分类
- **决策**: `WorkerResult.retryable` 作为显式返回字段。
- **理由**: 对齐 Feature 008 验收要求（可重试/不可重试）。
- **备选**: 仅通过错误文案隐式区分（不可测试）。
