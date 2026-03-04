# 数据模型说明: Feature 013 — M1.5 E2E 集成验收

**Date**: 2026-03-04
**性质**: 纯集成验收 Feature，不引入新数据模型（FR-013 范围锁定）

---

## 说明

Feature 013 的职责是集成验收，不新增任何业务实体或数据库表。本文档描述测试过程中依赖的**现有数据模型**，以及 F013 测试代码对这些模型的**使用方式**。

---

## 现有实体使用关系

### 核心实体（来自 octoagent.core.models）

#### Task

```python
# octoagent/packages/core/src/octoagent/core/models/task.py
class Task:
    task_id: str          # ULID 格式，全局唯一
    status: TaskStatus    # CREATED / RUNNING / SUCCEEDED / FAILED / CANCELLED / REJECTED
    trace_id: str         # 全链路追踪标识符（贯通 F008~F012）
    created_at: datetime
    updated_at: datetime
```

**F013 使用方式**:
- 场景 A、D：通过 `GET /api/tasks/{task_id}` 查询 task.status，断言最终为 SUCCEEDED
- 场景 B：手动推进 task.status 到 RUNNING，再通过 ResumeEngine 恢复
- 场景 C：手动推进 task.status 到 RUNNING（不完成），触发 WatchdogScanner 检测

#### Event

```python
# octoagent/packages/core/src/octoagent/core/models/event.py
class Event:
    event_id: str         # ULID 格式
    task_id: str          # 关联 Task
    task_seq: int         # 任务内有序序号
    ts: datetime          # 事件时间戳
    type: EventType       # 事件类型枚举
    actor: ActorType      # SYSTEM / USER / AGENT
    payload: dict         # 事件载荷（JSON）
    trace_id: str         # OTel trace_id
    span_id: str          # OTel span_id
```

**F013 断言的事件类型**:

| 场景 | 事件类型 | 来源 Feature | 断言意图 |
|------|---------|-------------|---------|
| 场景 A | `ORCH_DECISION` | Feature 008 | 路由决策已记录 |
| 场景 A | `WORKER_DISPATCHED` | Feature 008 | Worker 派发已记录 |
| 场景 A | `WORKER_RETURNED` | Feature 008/009 | Worker 回传已记录 |
| 场景 C | `TASK_DRIFT_DETECTED` | Feature 011 | 无进展告警已写入 |

#### CheckpointSnapshot

```python
# octoagent/packages/core/src/octoagent/core/models/checkpoint.py
class CheckpointSnapshot:
    checkpoint_id: str              # 手动指定（格式: cp-{task_id}）
    task_id: str                    # 关联 Task
    node_id: str                    # 图节点名（如 "model_call_started"）
    status: CheckpointStatus        # CREATED / PENDING / RUNNING / SUCCESS / ERROR
    schema_version: int             # 版本号，当前为 1
    state_snapshot: dict            # 恢复所需状态（如 {"next_node": "response_persisted"}）
    side_effect_cursor: str | None  # 副作用游标（可为 None）
    created_at: datetime
    updated_at: datetime
```

**F013 使用方式**（场景 B 专用）:
- 阶段 1：构造 `CheckpointSnapshot(status=CheckpointStatus.SUCCESS)` 写入 `CheckpointStore`
- 阶段 2：通过 `ResumeEngine.try_resume()` 读取，验证恢复路径

#### TaskDriftDetectedPayload

```python
# octoagent/packages/core/src/octoagent/core/models/payloads.py
class TaskDriftDetectedPayload:
    drift_type: str                  # 漂移类型（如 "no_progress"）
    detected_at: str                 # 检测时间（ISO 格式）
    task_id: str
    trace_id: str
    last_progress_ts: str | None     # 最后进展时间
    stall_duration_seconds: float    # 停滞时长（秒）
    suggested_actions: list[str]     # 建议操作
    watchdog_span_id: str            # OTel span_id（F012 前为空字符串）
    failure_count: int
    failure_event_types: list[str]
    current_status: str              # 任务当前状态
```

**F013 使用方式**（场景 C）: 从 `TASK_DRIFT_DETECTED` 事件的 payload 字段反序列化，断言 task_id 匹配。

---

## 测试数据规范

### 幂等键命名规范

F013 测试使用固定幂等键，避免跨测试 task_id 冲突：

| 文件 | 幂等键格式 | 示例 |
|------|-----------|------|
| test_f013_e2e_full.py | `f013-sc-a-{sequence}` | `f013-sc-a-001` |
| test_f013_checkpoint.py | `f013-sc-b-{sequence}` | `f013-sc-b-001` |
| test_f013_watchdog.py | `f013-sc-c-{sequence}` | `f013-sc-c-001` |
| test_f013_trace.py | `f013-sc-d-{sequence}` | `f013-sc-d-001` |

### CheckpointSnapshot 测试数据规范

场景 B 中使用的 CheckpointSnapshot 应满足：

- `checkpoint_id`：格式为 `cp-{task_id}`（便于断言 resume 返回值）
- `node_id`：`"model_call_started"`（现有 TaskService.process_task_with_llm 支持的恢复节点）
- `status`：`CheckpointStatus.SUCCESS`（get_latest_success 只查询 SUCCESS 状态）
- `schema_version`：`1`（当前版本，版本不匹配会触发降级）
- `state_snapshot`：`{"next_node": "response_persisted"}`

---

## 存储层访问路径

F013 测试直接访问的 Store 接口（无新增接口）：

| Store | 方法 | 场景 | 说明 |
|-------|------|------|------|
| `TaskStore` | `get_task(task_id)` | A, B, C, D | 查询任务状态（poll_until 条件） |
| `EventStore` | `get_events_by_types_since()` | C | 查询 TASK_DRIFT_DETECTED 事件 |
| `CheckpointStore` | `save_checkpoint()` | B（阶段 1） | 写入 SUCCESS checkpoint |
| `CheckpointStore` | `get_latest_success()` | B（阶段 2，通过 ResumeEngine） | 恢复读取 |
| `ArtifactStore` | `list_artifacts_for_task()` | A | 断言执行产物非空 |

---

## 无新增实体说明

本 Feature 严格遵守 FR-013（不引入新业务能力），因此：

- 无新增数据库表
- 无新增 Pydantic 模型
- 无修改现有 EventType 枚举
- 无修改现有 TaskStatus 状态机
- 验收报告（`verification/m1.5-acceptance-report.md`）为 Markdown 文档，不落数据库
