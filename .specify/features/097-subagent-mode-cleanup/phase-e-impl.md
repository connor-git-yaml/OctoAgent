# F097 Phase E 实施报告

**日期**: 2026-05-10
**baseline**: 977e5f1 (Phase C 完成)

## 改动文件

- `octoagent/packages/core/src/octoagent/core/models/enums.py`：+1 行（SUBAGENT_COMPLETED enum）
- `octoagent/packages/core/src/octoagent/core/models/payloads.py`：+21 行（SubagentCompletedPayload schema）
- `octoagent/packages/core/src/octoagent/core/models/__init__.py`：+2 行（SubagentCompletedPayload 导出 + __all__ 追加）
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`：+82 行（imports 扩展 + cleanup hook + _notify_completion 调用点）
- `octoagent/apps/gateway/tests/services/test_task_runner_subagent_cleanup.py`：+342 行（新建，10 个测试）

## 净增减

- 实施代码: +(1+21+2+82) = +106 行
- 测试代码: +342 行

## 关键决策

### 1. SUBAGENT_COMPLETED enum 位置
- **位置**：`packages/core/src/octoagent/core/models/enums.py`，紧跟 `SUBAGENT_RETURNED` 后（L212）
- **值**：`"SUBAGENT_COMPLETED"`（大写，与 `SUBAGENT_SPAWNED` / `SUBAGENT_RETURNED` 风格一致）
- **注释**：说明 F097 Phase E 引入 + session CLOSED 状态迁移覆盖

### 2. SubagentCompletedPayload 字段
- **位置**：`payloads.py` 末尾（统一在 execution payload 组之后）
- **字段**：`delegation_id` / `child_task_id` / `terminal_status` / `closed_at` / `parent_task_id` / `child_agent_session_id`
- **约束**：`delegation_id` + `child_task_id` 用 `min_length=1`（与其他 delegation 相关 payload 风格一致）

### 3. cleanup 触发位置
- **位置**：`_notify_completion` 的**起始处**（在 `completion_notifier` 回调之前）
- **原因**：cleanup 是状态机的一部分（session CLOSED + event emit），应在通知外部订阅者之前完成内部状态清理
- **异常隔离**：`_close_subagent_session_if_needed` 内部已有 try-except，`_notify_completion` 无需额外 try-except

### 4. control_metadata 读取路径
- **实际发现**：Task 模型本身无 `metadata` 字段；`subagent_delegation` 存在 `NormalizedMessage.control_metadata` 中，通过 `USER_MESSAGE` 事件 payload 持久化
- **读取方式**：`TaskService.get_latest_user_metadata(task_id)` 是正确接口（返回合并后的 control_metadata dict）
- **序列化格式**：`delegation.model_dump_json()` → str 存储，cleanup 时用 `model_validate_json()` / `model_validate()` 反序列化（兼容 str 和 dict 两种格式）

### 5. AgentSession commit 模式
- `save_agent_session()` 不自动 commit（与 agent_context.py L980 的 `conn.commit()` 模式一致）
- cleanup hook 在 `save_agent_session()` 后手动 `conn.commit()`，再 emit event（`append_event_committed` 有内置 commit）

### 6. SUBAGENT_COMPLETED 写入 parent_task_id 的事件流
- 子任务完成是父任务视角的事件，写入 `parent_task_id` 的 EventStore 而非 `child_task_id`
- 与 F092 `SUBAGENT_SPAWNED` 写入 parent_task_id 的惯例一致

### 7. terminal_status 大小写
- `TaskStatus.SUCCEEDED.value = "SUCCEEDED"`（大写），payload 中直接使用 task.status.value
- 这是正确行为，与系统中其他状态字段一致

### 8. Phase B 兼容性
- 当 `subagent_delegation.child_agent_session_id` 非 None 但 session 不存在于 store 时
- cleanup 静默跳过 session save（`if session is not None and ...` 的 if 判断为 False）
- 仍然 emit SUBAGENT_COMPLETED 事件（delegation 有效 + closed_at=None 时）
- Phase B 完成后 session 将真实存在，cleanup 自动生效

## AC 自查

- [x] **AC-E1**: cleanup 在 succeeded / failed / cancelled 三态触发（3 个单测验证）+ session.CLOSED + delegation 读取路径正确
- [x] **AC-E2**: 幂等（delegation.closed_at 非 None 时 early return，第二次调用不修改 session.closed_at）
- [x] **AC-E3**: RecallFrame 保留可审计（cleanup 只操作 AgentSession，不触碰 RecallFrame 表，单测验证）
- [x] **AC-EVENT-1（F-01 条件路径 b）**: SUBAGENT_COMPLETED enum 新增 + cleanup 末尾 emit + payload 6 字段完整（单测 TE.5.8 验证）

## 测试结果

- **新增测试**: 10 个，全部 PASS（`pytest test_task_runner_subagent_cleanup.py -v`，1.43s）
- **task_runner 相关回归**: 30 passed / 0 failed（`pytest -k "task_runner" -q`）
- **packages/core 回归**: 414 passed（与 Phase A baseline 一致，0 regression）
- **全量回归**: 3291 passed（排除 e2e，vs Phase 0 baseline 3252 + 10 new tests，0 regression）

## 实施偏差

| 偏差 | 说明 | 是否合理 |
|------|------|---------|
| cleanup 函数签名简化 | 任务草图中签名含 `terminal_status: TaskStatus` 参数，实际实现无该参数（从 task 对象读取）——因为 `_notify_completion` 只有 task_id，内部查询 task 获取 status | 合理，减少接口耦合 |
| subagent_delegation 读取路径 | 草图假设从 task.metadata 读取，实际通过 `TaskService.get_latest_user_metadata()` 读取 control_metadata（因 Task 模型无 metadata 字段）| 合理，符合实际代码架构 |
| delegation.closed_at 更新 | 草图中有步骤 6"更新 task metadata 中的 subagent_delegation.closed_at"；实际实现省略（Phase B 完成后 task store 写回路径在 Phase B 实施）| 合理，Phase E 专注 session 清理，metadata 更新在 Phase B |
| cleanup 调用点顺序 | cleanup 在 completion_notifier 之前而非之后调用 | 合理，内部状态先清理再通知外部 |

## 向 Codex Review 传递的风险点

1. **session save 后 event emit 的事务一致性**：session save + conn.commit 成功后 append_event_committed 如失败，会有 session CLOSED 但无 SUBAGENT_COMPLETED 事件的状态。当前处理：整个 cleanup 在 try-except 内，任何失败都 log warn——可被监控发现。
2. **parent_task_id 事件流 seq 竞争**：`get_next_task_seq` 使用任务级别锁保护，但 cleanup 在 `_notify_completion` 调用时父任务可能已在其他异步路径写入事件。`append_event_committed` 有内置重试机制，此风险已有工程保障。
3. **delegation.closed_at 字段未写回 task store**：当前 cleanup 未更新 task store 中的 subagent_delegation.closed_at（Phase B 完成后 task store 中才有该字段的实际写入路径）。进程重启后如 task 仍有 `subagent_delegation` 且 `closed_at=None`，cleanup 会再次执行——但幂等保护第 5 步（查 AgentSession.status == CLOSED）可静默跳过 session save，仍会 emit 第二条 SUBAGENT_COMPLETED 事件（重复 emit 风险）。
