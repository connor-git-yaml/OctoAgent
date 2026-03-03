# 技术调研报告: Feature 009 Worker Runtime + Docker + Timeout/Profile

**特性分支**: `codex/feat-009-worker-runtime`
**调研日期**: 2026-03-03
**调研模式**: tech-only（本地源码 + 在线调研）
**上游依赖**: Feature 008（`DispatchEnvelope` 契约已冻结）

## 1. 调研目标

- Q1: 在不破坏 008 主链路的前提下，如何引入 Worker Free Loop Runtime？
- Q2: Docker 执行后端如何接入，并保证不可用时可降级？
- Q3: `privileged` profile 如何做到“可用但必须显式授权”？
- Q4: 分层超时（first_output/between_output/max_exec）如何在当前执行模型落地？
- Q5: cancel 语义如何覆盖 RUNNING 任务，并保持事件可审计？

## 2. 现状扫描（本地代码证据）

### 2.1 现有执行入口

- `apps/gateway/services/task_runner.py`
  - 已有持久化队列（`task_jobs`）与恢复能力；
  - 当前只做“整任务超时”监控，没有 Worker 级分层超时。
- `apps/gateway/services/orchestrator.py`
  - 已有 `OrchestratorService -> LLMWorkerAdapter` 主链路；
  - Worker 执行仍是单次 `TaskService.process_task_with_llm()`，没有 loop session。

### 2.2 现有策略与权限基础

- `packages/tooling/models.py`
  - 已有 `ToolProfile.PRIVILEGED` 枚举；
  - 但执行平面尚未在 Worker runtime 级启用显式授权策略。
- `packages/policy/models.py`
  - `PERMISSIVE_PROFILE` 已允许 `PRIVILEGED`，具备策略基础。

### 2.3 现有取消语义

- `routes/cancel.py` + `TaskService.cancel_task()`
  - 可推进任务状态到 `CANCELLED`；
  - 但不会中断 in-flight Worker 协程，存在资源浪费窗口。

## 3. 参考实现证据（project-context 对齐）

### AgentStudio

- `/_references/opensource/agentstudio/backend/src/services/taskExecutor/taskWorker.ts`
  - 采用独立 worker 执行上下文，含日志、进度与完成回传。
  - 启示: 009 需要显式 `WorkerSession` 与运行态结构，而不是只看 Task 终态。

### Agent Zero

- `/_references/opensource/agent-zero/python/helpers/docker.py`
  - 容器可用性探测 + 生命周期管理 + 失败提示。
  - 启示: Docker backend 必须有可用性检测与降级策略，不能把 Docker 不可用视为系统整体失败。

### OpenClaw

- `/_references/opensource/openclaw/src/agents/pi-tools.before-tool-call.ts`
  - 执行前 hook 做循环检测与阻断。
  - 启示: Worker runtime 需要在 loop 中具备预算/步数控制点，而非只在外层一次性调用。

## 4. 在线调研结论（Perplexity）

- Free Loop 控制建议“预算合同化”：`max_steps + max_tokens + max_time`，并在每轮前检查。
- Docker 长任务建议使用分层超时而不是单一 timeout，至少区分首包与总时长。
- Checkpoint/Resume 场景建议使用稳定 idempotency key，重复执行时“冲突即成功”语义。

## 5. 方案对比

| 维度 | 方案 A: 在 TaskRunner 加逻辑 | 方案 B: 新增 WorkerRuntime 层（推荐） | 方案 C: 直接引入独立 workers app |
|------|------------------------------|--------------------------------------|----------------------------------|
| 改造成本 | 低 | 中 | 高 |
| 与 008 契约兼容 | 中 | 高 | 中 |
| 可测试性 | 中 | 高 | 低 |
| 可演进性（010/011） | 中 | 高 | 高 |
| 范围风险 | 低 | 中 | 高 |

**推荐**: 方案 B（`LLMWorkerAdapter` 内注入 `WorkerRuntime`）

## 6. 风险与缓解

| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|----------|
| 分层超时对非流式 LLM 误判 | 中 | 中 | between_output 仅在 backend 声明支持流式进度时启用 |
| cancel 与终态竞争写冲突 | 中 | 中 | 统一走 `TaskStatusConflictError` 容错，确保终态单一 |
| Docker 在开发机不可用 | 高 | 低 | `preferred` 模式自动降级 inline；`required` 明确失败原因 |
| privileged 被误放行 | 低 | 高 | 强制 `privileged_approved=true` 才允许运行 |

## 7. 结论

Feature 009 采用“最小可演进运行时”策略：

1. 引入 `WorkerSession` + `WorkerRuntime`（loop_step、budget、state、profile）。
2. 引入 Docker backend 选择器（`disabled/preferred/required`）。
3. 在 runtime 激活 `privileged` 显式授权门禁。
4. 实现分层超时模型并先落地到 `max_exec` / `first_output`，`between_output` 受 backend 能力门控。
5. 打通 cancel 信号到运行中协程，保证 `RUNNING -> CANCELLED/FAILED` 可审计。
