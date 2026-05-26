# F103c Baseline 实测侦察报告

> 时间：2026-05-26
> Spec 阶段必做侦察（沿用 9 连 pattern），确保设计基于事实而非假设。

## 侦察 1：Worker logger 当前现状

**统计**：54 条 logger 调用，全部 `structlog.get_logger()`，无 logging 模块混用。

| 文件 | info | warning | error | debug | 总计 |
|------|------|---------|-------|-------|------|
| `worker_runtime.py` | 1 | 2 | 0 | 1 | 4 |
| `task_runner.py` | 4 | 22 | 2 | 3 | 31 |
| `dispatch_service.py` | 0 | 4 | 0 | 0 | 4 |
| `notification.py` | 1 | 6 | 0 | 8 | 15 |

**输出方向**：全部走 `logging.StreamHandler()`（`logging_config.py:59`），默认 stderr。**无任何 logger 直接 emit EventStore**。

## 侦察 2：Worker error 当前表面路径

调用链：
```
worker_runtime 主 loop (无 top-level catch)
  → task_service.process_task_with_llm() exception 上浮
  → task_runner.py:955-976 except Exception
    → log.error("run_job_dispatch_exception")  [仅 structlog stderr]
    → 标记 task = FAILED + job = FAILED
    → _notify_completion()
      → NotificationService.notify_task_state_change()
        → 推送 STATE_TRANSITION 给主 Agent / channels
```

**关键缺口**：dispatch exception **不 emit 独立事件**（如 `WORKER_ERROR`）。主 Agent 只能通过 STATE_TRANSITION 间接看到"task FAILED"，看不到 Worker 内部失败原因。

## 侦察 3：stderr / stdout 实际泄露

- ✅ **0 条** `print()` / `sys.stderr.write()` 直接调用
- ✅ 全部 `structlog → logging.StreamHandler` 路由
- ⚠️ StreamHandler 默认 stderr（标准 Python logging 行为）

**结论**：baseline **已经不直接打 stderr**（违反 H1 表层不存在）。"stderr 泄露"是伪问题；**真问题是 logger 不进 EventStore audit chain**。

## 侦察 4：N-H1 PARTIAL F099 残留路径

| 路径 | baseline 状态 | 位置 |
|------|------|------|
| 首次 dispatch 写入 `CONTROL_METADATA_UPDATED` | ✅ 已实现 | `worker_runtime.py:426-434` |
| Resume 从 `resume_state_snapshot` 读 `is_caller_worker_signal` | ✅ 已实现 | `worker_runtime.py:543-549` |
| `attach_input` 无 live waiter 分支恢复 | ✅ F101 已修 | `task_runner.py:868-877` |
| Worker restart / dispatcher recovery 路径 | ✅ **baseline 已 cover** | `task_runner.py:354-390` → `_resume_engine.try_resume()` → `_spawn_job(resume_state_snapshot=...)` → `worker_runtime.py:543` |
| Subagent cleanup 后重启恢复 | ✅ EventStore replay | RecoveryStatusStore + RecoveryEngine |

**关键结论**：N-H1 worker restart 路径 **baseline 已被 EventStore replay + resume_state_snapshot 路径 cover**。F103c **缺的不是新逻辑**，而是 e2e 测试验证。

## 侦察 5：F101 NotificationService 复用入口

```python
NotificationService.notify_task_state_change(
    task_id, event_type, payload,
    priority=NotificationPriority.LOW,  # LOW/MEDIUM/HIGH/QUIET
    channels=None,  # F102 新增 per-call 过滤
    state_transition_event_id="",  # F101 去重
    ...
)
```

NotificationService **不自发 NOTIFICATION_DISPATCHED**（已规范化，调用方负责 emit_event）。

## 侦察 6：EventType 当前清单 + 命名约定

**WORKER_* 前缀已有**（`enums.py`）：
- `WORKER_DISPATCHED`
- `WORKER_RETURNED`

**命名约定**：过去式动词+宾语（`MODEL_CALL_COMPLETED`, `STATE_TRANSITION`）。

**emit_event 入口**：`store.emit_event(type=EventType.*, payload={...})`，自动落 EventStore + SSE。

## Logger 分类清单（关键升级 8-10 条）

按 H1 视角 + 原则 11 上下文卫生（避免 EventStore 爆炸），分 3 类：

### 类别 1：必须升级 EventStore audit（关键 8 条）

| File:Line | Logger Key | 理由 |
|-----------|-----------|------|
| `worker_runtime.py:442` | `worker_runtime_emit_is_caller_worker_signal_failed` | resume 路径关键信号，失败=潜在 H1 违规 |
| `worker_runtime.py:602` | `worker_runtime_a2a_heartbeat_failed` | A2A worker 健康，主 Agent 应可见 |
| `worker_runtime.py:630` | `worker_runtime_first_output_timeout_budget_exceeded` | 用户期待 worker 响应 |
| `task_runner.py:348` | `subagent_delegation_init_failed` | 委派初始化失败，影响 H3-A |
| `task_runner.py:879` | `attach_input_resume_is_caller_worker_signal_read_failed` | N-H1 resume 失败 |
| `task_runner.py:958` | `run_job_dispatch_exception` | Worker 主异常路径（也升级 WORKER_ERROR）|
| `task_runner.py:1187` | `task_runner_job_timeout` | 用户可观察任务超时 |
| `dispatch_service.py` A2A profile 解析失败 | `a2a_profile_*_failed` | A2A profile 降级路径 |

### 类别 2：保留本地 structlog（不进 EventStore，约 15 条）

- Docker availability check / 内部 debug log
- NotificationService 内部 debug log（quiet hours / channel routing）
- 配置加载 log

### 类别 3：升级 NotificationService（priority=high）

- Worker `dispatch_exception`（task_runner.py:958）—— 与 WORKER_ERROR 同时触发 NotificationService

## 范围决策（spec 阶段拍板）

| 决策 | 选择 | 理由 |
|------|------|------|
| 新 EventType 数量 | 2 个 | `WORKER_LOG_EMITTED`（通用 audit）+ `WORKER_ERROR`（独立语义）|
| Logger 升级条数 | 8 条关键 | 完全升级 28-30 条违反原则 11 + 滚雪球；提供 helper 让后续按需升级 |
| N-H1 worker restart | 仅补 e2e 测试 | baseline 已 cover，无新逻辑 |
| stderr 路由改造 | **不做** | baseline 已经走 structlog，无 print 泄露 |
| WORKER_HEALTH EventType | **不做** | 用 `WORKER_LOG_EMITTED.payload.level` 区分，避免 EventType 膨胀 |
| NotificationService 集成范围 | 仅 Worker fatal error | priority=high 触达用户，其他 logger 走 EventStore 即可 |

**1 天范围总评估**：~9.5h（spec 1.5h + 实施 5h + 测试 1h + Codex review + verify 2h）。
