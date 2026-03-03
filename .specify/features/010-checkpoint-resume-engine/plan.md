# Implementation Plan: Feature 010 Checkpoint & Resume Engine

## 1. 实施目标

在现有 `TaskRunner + Orchestrator` 基线上引入节点级 checkpoint 与 resume 引擎，满足以下闭环：

- 中断后从最后成功 checkpoint 恢复
- 恢复过程可审计
- 恢复重试不重复执行已确认副作用

## 2. 技术上下文

- 语言: Python 3.12+
- 核心依赖: FastAPI, aiosqlite, Pydantic
- 存储: SQLite WAL（现有 task/event/artifact/task_jobs）
- 关键模块:
  - `octoagent/packages/core`（模型、事件、存储）
  - `octoagent/apps/gateway/services/task_runner.py`（启动恢复入口）
- 参考基线:
  - `docs/blueprint.md` FR-TASK-4 + M1.5 执行约束
  - `.specify/memory/constitution.md` C1/C2/C4/C6/C8

## 3. 现状差距

1. 无 checkpoint 表与 store。
2. Task 指针无 checkpoint 字段。
3. 重启恢复路径直接将 RUNNING job 标记失败。
4. 无恢复生命周期事件与失败分类。

## 4. 设计决策

### D1: Checkpoint 作为一等存储对象

- 新增 `checkpoints`、`side_effect_ledger` 表。
- 新增 `SqliteCheckpointStore` 与 `SqliteSideEffectLedgerStore`。

### D2: Resume 引擎嵌入 TaskRunner

- `startup()` 阶段由 `try_resume` 先处理可恢复任务，再走失败清算。
- 对同 task 加恢复互斥（lease/lock）。

### D3: 事务边界明确化

- checkpoint 写入与关键事件写入同事务提交。
- 失败场景触发补偿事件，避免静默半提交。

### D4: 幂等优先

- 不可逆副作用执行前必须尝试写入 side-effect ledger。
- 若幂等键已存在，恢复流程跳过/复用结果。

## 5. 文件变更计划（实现阶段目标）

### 新增（目标）

- `octoagent/packages/core/src/octoagent/core/models/checkpoint.py`
- `octoagent/packages/core/src/octoagent/core/store/checkpoint_store.py`
- `octoagent/packages/core/src/octoagent/core/store/side_effect_ledger_store.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/resume_engine.py`
- `octoagent/tests/integration/test_f010_checkpoint_resume.py`

### 更新（目标）

- `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py`
- `octoagent/packages/core/src/octoagent/core/models/task.py`
- `octoagent/packages/core/src/octoagent/core/models/enums.py`
- `octoagent/packages/core/src/octoagent/core/models/payloads.py`
- `octoagent/packages/core/src/octoagent/core/store/__init__.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`

## 6. 验证策略

### 单元测试

- checkpoint 状态流转合法性
- latest checkpoint 查询规则
- side-effect ledger 幂等行为

### 集成测试

- 中断重启恢复（从最后成功节点继续）
- 重复恢复不重复副作用
- 损坏快照安全失败
- 并发恢复冲突

### 回归测试

- 现有 task runner / orchestrator / event store 关键路径不回归

## 7. 风险与缓解

- 并发冲突风险: 引入 task 级恢复锁 + 事件幂等键。
- 兼容风险: 模型字段新增采用默认值，保持历史数据可读。
- 语义漂移风险: 通过 contracts + integration tests 锁定恢复行为。

## 8. Gate 结论

- GATE_DESIGN: PASS（需求、研究、契约、计划齐备，可进入任务分解）
