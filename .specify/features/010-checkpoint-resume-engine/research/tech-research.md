# Feature 010 技术调研：Checkpoint & Resume Engine

**特性分支**: `010-checkpoint-resume-engine`
**调研日期**: 2026-03-03
**调研模式**: full（含在线调研）
**产品调研基础**: `research/product-research.md`

## 1. 调研问题

1. 当前代码是否已经具备 checkpoint/resume 基础结构？
2. 如何在不引入重型编排器的前提下实现“可恢复且幂等”？
3. 参考项目在恢复和幂等上的可复用模式是什么？

## 2. 当前代码基线（AgentsStudy）

### 2.1 持久化层尚无 checkpoint 表

- `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py:10-130` 当前仅包含 `tasks/events/artifacts/task_jobs`，无 checkpoint 专用 DDL。
- 结论：Feature 010 必须新增持久化模型与索引，不应复用 task_jobs 替代 checkpoint。

### 2.2 Task 指针未暴露 checkpoint 语义

- `octoagent/packages/core/src/octoagent/core/models/task.py:21-25` 的 `TaskPointers` 仅有 `latest_event_id`。
- 结论：需要补充 `latest_checkpoint_id` 与恢复元信息，否则 UI/API 难以追踪恢复位置。

### 2.3 当前恢复语义是“失败清算”，不是“断点续跑”

- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py:92-106` 在网关重启后将 RUNNING job 直接标记失败。
- 结论：需把恢复入口从“mark_failed”升级为“try_resume -> fail_safe”。

### 2.4 事件枚举尚未覆盖恢复生命周期

- `octoagent/packages/core/src/octoagent/core/models/enums.py:59-108` 已有 `ORCH_*`/`WORKER_*`，无 `CHECKPOINT_*`、`RESUME_*` 事件。
- 结论：需要扩展事件类型与 payload，满足 Constitution C2/C8 的可审计要求。

## 3. 开源参考证据

## 3.1 Pydantic Graph：节点级 snapshot + 从持久化恢复

- `/_references/opensource/pydantic-ai/docs/graph.md:577-589`
  - 明确“节点前后 snapshot”与 `iter_from_persistence` 恢复执行。
- `/_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/graph.py:286-308`
  - 恢复流程：`load_next()` -> `snapshot.node.set_snapshot_id()` -> `GraphRun(...)`。
- `/_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/persistence/file.py:68-102`
  - `record_run` 标记 `running/success/error`；`load_next` 把 `created -> pending`，避免重复消费。
- `/_references/opensource/pydantic-ai/pydantic_graph/pydantic_graph/persistence/file.py:147-166`
  - 通过锁文件避免并发写冲突。

启示：
- Checkpoint 需要状态位（created/pending/running/success/error）。
- Resume 要有“可消费但不可重复消费”的状态机，而不只是取最新一条快照。

## 3.2 Agent Zero：计划执行的 in_progress/done 与本地落盘

- `/_references/opensource/agent-zero/python/helpers/task_scheduler.py:61-117`
  - `TaskPlan(todo/in_progress/done)` 明确执行进度语义。
- `/_references/opensource/agent-zero/python/helpers/task_scheduler.py:470-539`
  - `tasks.json` 的 save/reload 持久化保证重启后状态可恢复。

启示：
- 010 不仅要存“最后一次快照”，还要表达“当前节点是否执行中/完成”的恢复语义。

## 3.3 OpenClaw：调度持久化 + 重启不丢计划

- `/_references/opensource/openclaw/docs/automation/cron-jobs.md:14-26,71-74`
  - 强调 job 落盘与重启后保留计划。

启示：
- Resume 引擎必须保证启动扫描可重建待恢复任务集合。

## 3.4 AgentStudio：执行器重试与隔离执行（对比项）

- `/_references/opensource/agentstudio/backend/src/services/taskExecutor/index.ts:25-38`
  - 执行器配置层已包含 `maxRetries/retryDelay`。
- `/_references/opensource/agentstudio/backend/src/services/taskExecutor/taskWorker.ts:41-187`
  - 侧重 worker 隔离与结果上报，未内建节点级 checkpoint。

启示：
- “有 retry”不等于“可恢复”。010 应把重试与恢复拆开建模。

## 4. 在线补充结论（摘要）

详见 `research/online-research.md`。

- Temporal 文档强调 Activity 必须幂等，Workflow 代码必须确定性。
- SQLite WAL 官方文档强调 checkpoint 与单写者并发约束。
- Pydantic AI 官方 durable execution 页面确认可通过 Temporal/DBOS/Prefect 构建可恢复执行。

## 5. 方案对比

### 方案 A：任务级粗粒度恢复（仅重跑 TaskRunner）

- 优点：实现快
- 缺点：不能保证副作用幂等，恢复粒度粗

### 方案 B：节点级 checkpoint + resume 状态机（推荐）

- 优点：精确恢复、可观测、可验证
- 缺点：需要新增模型/事件/事务边界

### 方案 C：直接接入外部 durable 编排器

- 优点：能力上限高
- 缺点：偏离当前里程碑“最小可恢复核心”

## 6. 技术决策建议

1. 引入 `checkpoints` 表与 `SqliteCheckpointStore`，不与 `task_jobs` 混用。
2. 新增恢复事件：`CHECKPOINT_SAVED/RESUME_STARTED/RESUME_SUCCEEDED/RESUME_FAILED`。
3. 在 checkpoint 写入与关键事件写入之间建立事务边界，避免“快照成功但事件缺失”。
4. 为恢复动作增加租约/幂等键，确保同一 task 仅有一个活跃恢复流程。

## 7. 风险与缓解

- 风险：并发 resume 导致重复执行
  - 缓解：任务级恢复租约 + 事件幂等键
- 风险：快照 schema 升级兼容性
  - 缓解：快照 `schema_version` + 向后兼容解码 + 损坏降级
- 风险：恢复状态与 Task 状态机冲突
  - 缓解：定义合法流转矩阵，非法流转直接 fail-safe
