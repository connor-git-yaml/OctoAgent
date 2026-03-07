# Tech Research: Feature 019 — Interactive Execution Console + Durable Input Resume

## 本地代码基线

### 1. 已有可复用能力

- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`
  - 已有 durable `task_jobs` 队列、启动恢复、取消、超时监控。
- `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py`
  - 已有 backend 选择、docker 可用性检测、cancel signal、max_exec timeout。
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
  - 已有事件写入、Artifact 写入、Checkpoint 写入、状态迁移与 SSE 广播。
- `octoagent/packages/policy/src/octoagent/policy/approval_manager.py`
  - 已有 approval register / resolve / recover / SSE 广播，可复用为高风险输入 gate。
- `octoagent/apps/gateway/src/octoagent/gateway/routes/stream.py`
  - 已有统一任务 SSE 流，只要 execution 事件进入 Event Store 即可复用。

### 2. 当前缺口

- `docker` backend 仍然只是 `WorkerRuntime` 的 backend 选择语义；019 更紧迫的缺口是 execution contract、输入恢复和审批 gate。
- `TaskStatus` 虽定义了 `WAITING_INPUT` / `PAUSED`，但状态机和 task_job lifecycle 尚未被系统化消费。
- 没有 execution session / stream 的正式模型与 API。
- 人工输入既没有 durable 保存，也没有 policy gate 集成。

## Blueprint / Constitution 约束

- `docs/blueprint.md §5.1.6`：执行面至少要覆盖状态、日志、取消、artifacts、attach_input 等统一语义。
- `docs/blueprint.md §8.8.2`：backend 以 `local_docker` 为默认，但 019 仍需保留 graceful fallback。
- `docs/blueprint.md §14`：M2 明确要求长任务交互、人工输入、取消都落同一任务事件链。
- `.specify/memory/constitution.md`：
  - Durability First
  - Everything is an Event
  - Least Privilege by Default
  - Event payload 最小化，敏感原文不直接入 Event

## 参考实现证据

### Agent Zero

- `knowledge/main/about/github_readme.md`
  - 强调实时、可干预、可停止的 terminal interaction。
- `knowledge/main/about/installation.md`
  - 强调 Docker-first 运行路径。
- `docs/developer/websockets.md`
  - 提供统一实时事件总线与诊断控制台思路，说明“实时控制台”应依赖统一事件协议而非零散回调。

### OpenClaw

- `docs/tools/exec-approvals.md`
  - 说明高风险执行控制需要明确的 ask/allow/deny gate，与 execution host 的最小权限模型绑定。
- `docs/cli/approvals.md`
  - 说明审批最好作为统一 operator surface 暴露，而不是埋在执行器内部。
- `docs/web/dashboard.md`
  - 说明控制台/控制面是统一 admin surface，应暴露结构化状态而不是只靠 shell 输出。

## 设计决策

### 决策 1：不新增 execution 持久化表

- 原因：当前 task/event/artifact/task_job 已足够承载 019 所需 durable 语义。
- 好处：避免 schema migration，降低对 M0/M1.5 基线的回归风险。
- 代价：需要做 execution session projection。

### 决策 2：保留粒度化 `EXECUTION_*` 事件，再投影统一 stream 视图

- 原因：当前 Event Store / SSE / task detail 已经按 event type 工作，继续复用回归最小。
- 好处：保留结构化事件链，同时由 `ExecutionStreamEvent` 提供统一消费视图。
- 代价：session 投影逻辑需要按 `session_id` 做一次归并。

### 决策 3：输入全文落 Artifact，Event 只存 preview/ref

- 原因：满足 Constitution 的最小化日志原则。
- 好处：审计可回放，同时避免把敏感输入长期暴露在 Event payload。
- 代价：attach_input 需要额外 artifact 写入。

### 决策 4：高风险输入复用 `ApprovalManager`

- 原因：仓库里已经有成熟审批链和恢复逻辑。
- 好处：后续 017/023 可直接复用同一 approvals surface。
- 代价：execution 输入请求需要携带 `approval_id` 并处理未审批路径。
