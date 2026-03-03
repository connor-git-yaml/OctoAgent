# REST API 契约: Feature 011 — Watchdog + Task Journal + Drift Detector

**特性目录**: `.specify/features/011-watchdog-task-journal`
**创建日期**: 2026-03-03
**依据**: `spec.md` FR-014, FR-015, FR-016
**状态**: Final

---

## 概述

Feature 011 新增一个 REST API 端点，为操作者提供任务健康状态全景视图。
现有 `/api/tasks` 路由（任务列表）和 `/api/tasks/{task_id}` 路由（任务详情）不做修改。

---

## 新增端点

### GET /api/tasks/journal

**功能**: 返回当前所有非终态任务的健康状态分组视图（Task Journal，FR-014）

**说明**:
- 实时聚合（Query-time Projection）：每次请求动态从 TaskStore + EventStore 聚合
- 分组固定为四类：`running`、`stalled`、`drifted`、`waiting_approval`
- `task_status` 使用内部完整 TaskStatus（不映射为 A2A 状态，Constitution 原则 14）
- 诊断详情通过 `drift_artifact_id` 引用访问，不直接内联（Constitution 原则 11）

**注意**: 此路由路径 `/api/tasks/journal` 必须在 `/api/tasks/{task_id}` 之前注册，
避免 FastAPI 将 `journal` 识别为 `task_id` 路径参数。

#### 请求

```
GET /api/tasks/journal
Authorization: （内部服务，MVP 阶段无 Auth）
Content-Type: 无
Query Parameters: 无
```

#### 响应 — 成功（HTTP 200）

```json
{
  "generated_at": "2026-03-03T10:30:00Z",
  "summary": {
    "total": 12,
    "running": 8,
    "stalled": 2,
    "drifted": 1,
    "waiting_approval": 1
  },
  "groups": {
    "running": [
      {
        "task_id": "01HRZXYZ...",
        "task_status": "RUNNING",
        "journal_state": "running",
        "last_event_ts": "2026-03-03T10:29:45Z",
        "drift_summary": null,
        "drift_artifact_id": null,
        "suggested_actions": []
      }
    ],
    "stalled": [
      {
        "task_id": "01HRZYAB...",
        "task_status": "RUNNING",
        "journal_state": "stalled",
        "last_event_ts": "2026-03-03T10:28:50Z",
        "drift_summary": {
          "drift_type": "no_progress",
          "stall_duration_seconds": 75.3,
          "detected_at": "2026-03-03T10:29:58Z",
          "failure_count": null
        },
        "drift_artifact_id": null,
        "suggested_actions": [
          "check_worker_logs",
          "cancel_task_if_confirmed"
        ]
      }
    ],
    "drifted": [
      {
        "task_id": "01HRZABC...",
        "task_status": "RUNNING",
        "journal_state": "drifted",
        "last_event_ts": "2026-03-03T10:25:10Z",
        "drift_summary": {
          "drift_type": "repeated_failure",
          "stall_duration_seconds": 290.0,
          "detected_at": "2026-03-03T10:29:55Z",
          "failure_count": 4
        },
        "drift_artifact_id": "artifact-01HRZDDD...",
        "suggested_actions": [
          "review_failure_events",
          "check_external_dependencies",
          "cancel_task_if_confirmed"
        ]
      }
    ],
    "waiting_approval": [
      {
        "task_id": "01HRZDEF...",
        "task_status": "WAITING_APPROVAL",
        "journal_state": "waiting_approval",
        "last_event_ts": "2026-03-03T10:27:30Z",
        "drift_summary": null,
        "drift_artifact_id": null,
        "suggested_actions": [
          "review_approval_request"
        ]
      }
    ]
  }
}
```

#### 响应字段说明

| 字段路径 | 类型 | 说明 |
|---------|------|------|
| `generated_at` | string (ISO 8601) | Journal 生成时间戳 |
| `summary.total` | integer | 非终态任务总数 |
| `summary.running` | integer | 正常运行中任务数 |
| `summary.stalled` | integer | 疑似卡死任务数 |
| `summary.drifted` | integer | 已检测到漂移事件任务数 |
| `summary.waiting_approval` | integer | 待审批任务数 |
| `groups.{group}[]` | array | 各分组任务列表 |
| `groups.{group}[].task_id` | string | 任务 ID (ULID) |
| `groups.{group}[].task_status` | string | 内部 TaskStatus 枚举值 |
| `groups.{group}[].journal_state` | string | 分组标签（running/stalled/drifted/waiting_approval） |
| `groups.{group}[].last_event_ts` | string \| null | 最近事件时间戳（ISO 8601） |
| `groups.{group}[].drift_summary` | object \| null | 漂移摘要（仅 stalled/drifted 有值） |
| `groups.{group}[].drift_summary.drift_type` | string | 漂移类型（no_progress / state_machine_stall / repeated_failure） |
| `groups.{group}[].drift_summary.stall_duration_seconds` | number | 卡死/漂移持续时长（秒） |
| `groups.{group}[].drift_summary.detected_at` | string | 漂移检测时间（ISO 8601） |
| `groups.{group}[].drift_summary.failure_count` | integer \| null | 失败次数（仅 repeated_failure 有值） |
| `groups.{group}[].drift_artifact_id` | string \| null | 详细诊断 artifact 引用 ID（可选） |
| `groups.{group}[].suggested_actions` | array[string] | 建议动作列表 |

#### 分组分类规则

```
任务分组逻辑（按以下优先级顺序评估，第一个匹配条件即为最终归组）:

1. task.status in TERMINAL_STATES（SUCCEEDED/FAILED/CANCELLED/REJECTED）
   -> 不包含在 Journal 中，跳过

2. task.status == WAITING_APPROVAL
   -> journal_state = "waiting_approval"
   （无论是否有进展事件，WAITING_APPROVAL 始终独立归组）

3. task 有 TASK_DRIFT_DETECTED 事件（查询 EventStore 确认）
   AND 最近进展事件时间 <= (now - no_progress_threshold)（仍无进展）
   -> journal_state = "drifted"

4. task 有 TASK_DRIFT_DETECTED 事件（查询 EventStore 确认）
   AND 最近进展事件时间 > (now - no_progress_threshold)（已恢复进展）
   -> journal_state = "running"
   （已恢复的漂移任务回归 running，对应 spec US2 验收场景 3）

5. 无 TASK_DRIFT_DETECTED 事件
   AND 最近进展事件时间 <= (now - no_progress_threshold)
   -> journal_state = "stalled"（实时检测为卡死，但尚无已记录的 DRIFT 事件）

6. 其余非终态任务
   -> journal_state = "running"
```

**注意**: `drifted` 优先级高于 `stalled`——若任务同时满足"有 DRIFT 事件"和"超过 no_progress_threshold"两个条件，按 `drifted` 处理（规则 3），不归入 `stalled`。

#### 错误响应

无特定错误场景（查询操作只读，内部聚合）。若 EventStore 不可用，返回降级响应：

```json
{
  "error": {
    "code": "JOURNAL_DEGRADED",
    "message": "Task Journal is temporarily unavailable due to store error",
    "generated_at": "2026-03-03T10:30:00Z"
  }
}
```

HTTP Status: 503

---

## 现有端点变更（无破坏性变更）

### GET /api/tasks（不变）

现有任务列表端点，支持按 `status` 参数筛选。F011 不修改此端点。

### GET /api/tasks/{task_id}（不变）

现有任务详情端点，返回任务信息 + 关联事件 + artifacts。F011 不修改此端点。
注：F011 新增的 `TASK_HEARTBEAT`、`TASK_MILESTONE`、`TASK_DRIFT_DETECTED` 事件类型
会出现在此端点的 `events` 列表中（自动兼容，无需修改端点实现）。

---

## Watchdog 内部接口（非 REST，仅供参考）

以下为 WatchdogScanner 与其他组件的内部接口，不暴露为外部 API。

### EventStore 新增接口

```python
# 接口 1: 获取任务最新事件时间戳
async def get_latest_event_ts(self, task_id: str) -> datetime | None

# 接口 2: 按事件类型 + 时间范围查询
async def get_events_by_types_since(
    self,
    task_id: str,
    event_types: list[EventType],
    since_ts: datetime,
) -> list[Event]
```

### TaskStore 新增接口

```python
# 接口: 批量按状态集合查询任务（避免多次串行查询的竞态窗口）
async def list_tasks_by_statuses(
    self,
    statuses: list[TaskStatus],
) -> list[Task]
```

---

## 接口注册顺序要求

在 `gateway/routes/__init__.py` 或 `main.py` 中注册路由时，
必须确保 `/api/tasks/journal` 在 `/api/tasks/{task_id}` 之前注册：

```python
# 正确的注册顺序
app.include_router(watchdog.router)  # 包含 GET /api/tasks/journal
app.include_router(tasks.router)     # 包含 GET /api/tasks/{task_id}
```

或在同一 router 内确保静态路由路径优先于参数化路径。
