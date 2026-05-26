# F103c — 实施计划（Plan）

> 上游：spec.md / baseline-recon.md
> Mode：spec-driver-story（1 天预估）
> Phase 顺序：A → B → C → D → E（小步快走，便于 per-Phase review）

---

## 0. 基线代码定位（精准定位）

| spec 范围 | 实际文件:line | 备注 |
|---|---|---|
| EventType 定义 | `octoagent/packages/core/src/octoagent/core/models/enums.py:71-` | StrEnum 类 EventType，紧跟 `WORKER_DISPATCHED` / `WORKER_RETURNED`（行 131-132）后追加 |
| EventStore append 入口 | `octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py:437` | `task_service._stores.event_store.append_event_committed(event, update_task_pointer=False)` |
| TaskService 结构化事件 | `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:368` | `append_structured_event(task_id, event_type, actor, payload)` |
| NotificationService 入口 | `octoagent/apps/gateway/src/octoagent/gateway/services/notification.py:478` | `notify_task_state_change(...)` 含 `priority`/`channels` |
| Worker dispatch_exception | `task_runner.py:955-979` | baseline 已通过 `_ensure_task_failed → _notify_state_change` 通知，但 `priority=LOW` 默认 + 无 error_class/traceback |
| Worker 8 条 logger 升级点 | spec §0.3 表格 | 全部 structlog `log.warning/error/info` |

## 1. baseline 现状再校准（比侦察更准）

**侦察 2 / spec 0.1 已写**：dispatch_exception 走 `log.error + mark_failed + _ensure_task_failed`。**进一步发现**：

- ✅ `_ensure_task_failed` (orchestrator.py:2596) **内部已调 `_notify_state_change`**
- ✅ `_notify_state_change` (orchestrator.py:458) 调 `NotificationService.notify_task_state_change` (**默认 `priority=LOW`**)
- ❌ payload 仅 `task_id / task_title / from_status / to_status / reason`，**无 error_class / error_summary / traceback**
- ❌ event_type 字段 = `"STATE_TRANSITION:FAILED"`（不是独立的 `WORKER_ERROR`）

**F103c delta**（精确）：

| 维度 | baseline | F103c 目标 |
|---|---|---|
| Worker fatal error 是否通知 | ✅ 已通知 | 保持 |
| 通知 priority | LOW（默认）| **HIGH** |
| 通知 payload | 缺 error 细节 | 加 error_class / error_summary（≤200 字符）/ traceback_artifact_id |
| 独立 EventType | 无 | 新增 `WORKER_ERROR`（emit 在 dispatch_exception 路径）|
| Worker 内部 logger audit | 0 | 8 条关键升级（双轨）|

---

## 2. Phase 拆分

### Phase A — EventType 定义 + Payload Schema（最小风险）

**目标**：注册新 EventType + payload Pydantic 模型，**零行为变更**。

**改动**：

1. `octoagent/packages/core/src/octoagent/core/models/enums.py`
   - 在 WORKER_DISPATCHED/RETURNED 之后追加：
     - `WORKER_LOG_EMITTED = "WORKER_LOG_EMITTED"`
     - `WORKER_ERROR = "WORKER_ERROR"`

2. `octoagent/packages/core/src/octoagent/core/models/event_payloads.py`（或同类 payloads 文件，先 grep 找）
   - 新增 `WorkerLogEmittedPayload(BaseModel)`: task_id / agent_runtime_id / level / key / payload / agent_session_id?
   - 新增 `WorkerErrorPayload(BaseModel)`: task_id / agent_runtime_id / error_class / error_summary / traceback_artifact_id? / agent_session_id?
   - `Literal["info", "warning", "error"]` for level
   - `error_summary` 用 `Field(max_length=200)` 强约束

3. 单测（`octoagent/packages/core/tests/test_models.py`）：
   - `WORKER_LOG_EMITTED == "WORKER_LOG_EMITTED"` assertion
   - `WORKER_ERROR == "WORKER_ERROR"` assertion
   - WorkerLogEmittedPayload schema roundtrip
   - WorkerErrorPayload schema roundtrip + error_summary >200 字符 raise validation error

**验收**：unit test 全过，`pytest octoagent/packages/core/tests/` 0 regression。

---

### Phase B — audit_worker_log helper（独立组件）

**目标**：新文件提供 helper，**不接入任何调用点**（接入留 Phase C）。

**Codex PM3 闭环**：helper 入参从 `StoreGroup` 改为 `TaskService`，确保 SSE 广播路径与现有 `ExecutionConsole.emit_log` 一致（AC1-2 / AC2-1 要求事件 SSE 可见）。

**改动**：

1. 新建 `octoagent/apps/gateway/src/octoagent/gateway/services/worker_audit_logger.py`
   - 导出 `audit_worker_log(task_service: TaskService, *, task_id: str, agent_runtime_id: str, level: Literal["info","warning","error"], key: str, payload: dict[str, Any], agent_session_id: str | None = None, degraded_reason: str | None = None) -> None`
   - 内部：
     - 构造 `WorkerLogEmittedPayload`（含 degraded_reason）
     - try：`await task_service.append_structured_event(task_id=..., event_type=EventType.WORKER_LOG_EMITTED, actor=ActorType.WORKER, payload=worker_log_payload.model_dump(mode="json"))` —— 走 `_sse_hub.broadcast` 路径
     - except Exception → 回退 structlog `log.warning("worker_audit_log_emit_failed", exc_info=True)`，**不抛**
   - 同时调 structlog `getattr(log, level)(key, **payload)` —— 双轨

2. 提供 sibling helper `audit_worker_error(task_service: TaskService, *, task_id: str, agent_runtime_id: str, error_class: str, error_summary: str, traceback_artifact_id: str | None = None, agent_session_id: str | None = None, notification_service: NotificationService | None = None, task_title: str | None = None, degraded_reason: str | None = None) -> Event | None`
   - 内部：
     - emit `WORKER_ERROR` 事件，**取返回的 event_id**（`append_structured_event` 返回 Event 对象）
     - 如果 notification_service 非 None → 调 `notify_task_state_change(priority=NotificationPriority.HIGH, event_type="WORKER_ERROR", payload={task_id, task_title, error_class, error_summary}, state_transition_event_id=event.event_id)` （**Codex PM1 闭环**：传 event_id 做幂等）
   - 容错：notification 失败 → log.warning + 不抛
   - 返回新 emit 的 Event 实例（test 用），失败时返回 None

3. **agent_runtime_id 派生工具**（plan 新增）：在 helper 模块内提供小工具 `derive_agent_runtime_id(envelope_or_metadata) -> tuple[str, str | None]`（返回 `(agent_runtime_id, degraded_reason)`），按以下优先级：
   1. `envelope.metadata.get("agent_runtime_id")` (worker_runtime 路径)
   2. `envelope.metadata.get("target_agent_runtime_id")` (a2a target 路径)
   3. `envelope.metadata.get("source_agent_runtime_id")` (a2a source 路径)
   4. 若全部为空字符串 → 返回 `("", "agent_runtime_id_unavailable")`

   **Codex PH1 闭环**：caller 不允许传空字符串 + 无 degraded_reason；helper 内部 assert（如违反则 raise + 测试覆盖）。

4. 单测（新建 `octoagent/apps/gateway/tests/test_worker_audit_logger.py`）：
   - 正常路径：emit 成功 → EventStore 有 1 条 `WORKER_LOG_EMITTED` + SSE broadcast 调一次
   - 容错路径：task_service=None / append_structured_event 抛异常 → 不抛，structlog 仍有输出
   - audit_worker_error 正常：emit `WORKER_ERROR` + 调 notification_service.notify_task_state_change `priority=HIGH` + `state_transition_event_id == event.event_id`
   - audit_worker_error 容错：notification_service=None 时只 emit event 不抛
   - audit_worker_error 容错：notification_service.notify_task_state_change 抛 → 不抛，log.warning 兜底
   - **派生工具**：`derive_agent_runtime_id` 4 case（4 个优先级 + 全空降级）
   - **空串 assert**：caller 传 `agent_runtime_id="" + degraded_reason=None` → helper raise AssertionError

**验收**：新模块 unit test ≥ 5 case 全过；无任何其他代码变更影响。

---

### Phase C — 8 条 logger 升级（接入 helper）

**目标**：spec §0.3 清单逐条接入 helper，双轨：保留原 structlog + 加 `audit_worker_log` 调用。

**改动**：

#### C-1: worker_runtime.py（3 处升级 + 1 处 dispatch_exception 的 ERROR 暂不在此 Phase）

| Line | 改造 |
|------|------|
| `worker_runtime.py:442` | `log.warning(...)` → 前置 `agent_runtime_id, degraded_reason = derive_agent_runtime_id(envelope.metadata)` + `await audit_worker_log(task_service, task_id=task_id, agent_runtime_id=agent_runtime_id, degraded_reason=degraded_reason, level="warning", key="worker_runtime_emit_is_caller_worker_signal_failed", payload={"dispatch_id": dispatch_id})` |
| `worker_runtime.py:602` | 心跳失败：同上派生 + `audit_worker_log(task_service, task_id=envelope.task_id, agent_runtime_id=..., level="warning", key="worker_runtime_a2a_heartbeat_failed", payload={"worker_id": worker_id, "loop_step": step, "error_type": type(exc).__name__})` |
| `worker_runtime.py:630` | 首次输出超时：同上派生 + `audit_worker_log(task_service, level="info", key=..., payload={"elapsed_s": round(elapsed,3), "threshold_s": ...})` |

**Codex PH1 闭环**：所有调用点先通过 `derive_agent_runtime_id(envelope.metadata)` 派生，不允许传字面量空串。如派生失败，自动填 `degraded_reason="agent_runtime_id_unavailable"`。

`task_service` 实例：worker_runtime 主 loop 通过 `task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)` 已构造（grep 行 ~540），helper 复用同一实例。

#### C-2: task_runner.py（3 处升级，**不含 dispatch_exception**）

| Line | 改造 |
|------|------|
| `task_runner.py:348` | `subagent_delegation_init_failed`：前置 `audit_worker_log(level="warning", key=..., payload={...})` |
| `task_runner.py:879` | `attach_input_resume_is_caller_worker_signal_read_failed`：同上 |
| `task_runner.py:1187` | `task_runner_job_timeout`：同上 |

#### C-3: dispatch_service.py（精确 1 条）

**Codex PM2 闭环**：冻结至单一 key，不允许 implement 阶段裁剪或新增。

| Line | Logger Key | EventType |
|------|-----------|-----------|
| `dispatch_service.py:974` | `a2a_target_profile_resolve_worker_binding_failed` | `WORKER_LOG_EMITTED` (level=warning) |

`a2a_target_profile_explicit_id_not_found`(988) / `a2a_target_profile_fallback_to_source`(994) **保留 structlog only**（不在范围）。

agent_runtime_id 派生：dispatch_service 上下文已持有 `source_agent_runtime_id` 或 `target_agent_runtime_id`（行 126-131 / 215）——优先使用本地变量，避免重新派生。

#### C-4: task_runner.py:958 dispatch_exception（核心 H1 修复）

**核心改动**：

```python
except Exception as exc:
    log.error("run_job_dispatch_exception", task_id=task_id, error_type=type(exc).__name__,
              error=str(exc), exc_info=True)
    error_summary = f"dispatch_exception:{type(exc).__name__}:{str(exc)[:200]}"
    
    # F103c 新增：写 WORKER_ERROR 事件 + 提升 notification priority
    try:
        # 1. agent_runtime_id 派生（PH1 闭环）
        task_obj = await service.get_task(task_id)
        task_metadata = task_obj.metadata if task_obj else {}
        runtime_id, degraded_reason = derive_agent_runtime_id(task_metadata)
        # 2. emit WORKER_ERROR + 高优先级通知（event_id 传递做幂等，PM1 闭环）
        await audit_worker_error(
            task_service=service,  # 而不是 store=self._stores（PM3 闭环）
            task_id=task_id,
            agent_runtime_id=runtime_id,
            degraded_reason=degraded_reason,
            error_class=type(exc).__name__,
            error_summary=error_summary[:200],
            notification_service=self._notification_service,
            task_title=(task_obj.title if task_obj else "") or task_id,
        )
    except Exception:
        log.warning("worker_error_audit_failed", task_id=task_id, exc_info=True)
    
    try:
        await self._stores.task_job_store.mark_failed(task_id, error_summary)
    except Exception:
        log.warning("run_job_mark_failed_fallback", task_id=task_id, exc_info=True)
    try:
        await self._orchestrator._ensure_task_failed(task_id, f"trace-{task_id}", error_summary)
    except Exception:
        log.warning("run_job_mark_task_failed_fallback", task_id=task_id, exc_info=True)
    await self._close_subagent_session_if_needed(task_id)
    return
```

**关键决策**：
- 不存 traceback 到 artifact（避免新增 artifact 依赖；改造范围爆炸）。`error_summary` ≤ 200 字符即足。
- `_notification_service` 注入：task_runner.__init__ 已有 `self._notification_service`（如果没有，从 self._orchestrator._notification_service 取，spec-driver 在 Phase 实施时 grep 确认）。
- **保留** baseline 的 `_ensure_task_failed` 调用 — STATE_TRANSITION + LOW priority 通知保留作为兜底；F103c 加 WORKER_ERROR + HIGH 是叠加，不替换。

**验收**：
- Phase C 完成后，全量 pytest 0 regression
- 手动触发 dispatch_exception（mock orchestrator.dispatch raise RuntimeError），验证 EventStore 含 1 条 `WORKER_ERROR` + NotificationService mock 收到 `priority=HIGH`

---

### Phase D — N-H1 resume e2e 测试（仅补测试）

**目标**：spec FR-D 要求，**不写新逻辑**，只补 e2e test 验证 baseline 已 cover。

**改动**：

1. 新建 `octoagent/apps/gateway/tests/test_n_h1_resume.py`（或并入现有 task_runner resume 测试文件）
2. 测试流程：
   - 构造 task，dispatch（caller_worker 标记 → CONTROL_METADATA_UPDATED 写入）
   - 模拟"重启"：通过 `_resume_engine.try_resume()` 直调路径（不真重启进程）
   - 验证 `worker_runtime.py:543` 从 resume_state_snapshot 读到 `is_caller_worker_signal == "1"`
3. 测试 2：CONTROL_METADATA_UPDATED 不存在时，resume_state_snapshot 不含该字段，worker_runtime 回退到 baseline 默认 True（参见 worker_runtime.py:442 注释）

**验收**：测试 PASS，且不引入任何 src/ 改动。

---

### Phase E — 验证与文档

**目标**：

1. 全量 `pytest octoagent/` 0 regression vs F103 baseline (def6638)
2. 触发 e2e_smoke：`pytest -m e2e_smoke` PASS
3. 脱敏审查：grep 新增 payload 字段无 `token` / `api_key` / `prompt` / `secret`
4. completion-report.md 草稿（最终在 Phase 5 Codex Final review 后定稿）

---

## 3. 依赖与顺序

```
A (EventType + Payload) ─┬─ 必须先于 ─┐
                         │            │
B (audit helper)         ┴─ 必须先于 ─┤
                                      │
C (8 条升级 + dispatch_exception) ──┴── 必须先于 ─┐
                                                  │
D (N-H1 e2e test) ─── 独立，可在 A 完成后任何时点 ─┤
                                                  │
E (验证 + 文档) ───────── 最后 ────────────────────┘
```

**Phase A / B 可并行**：B 只调用 A 的 type 定义，但 A 工作量 ~30min 太短，按顺序更简单。

**Phase D 独立**：仅测试代码，与 A/B/C 解耦。

---

## 4. 风险点与缓解

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| `agent_runtime_id` 在某调用点不可用 | 中 | payload 中 `agent_runtime_id: str` 允许 `""` 占位；可观察性损失但不阻塞 |
| dispatch_exception 路径加新调用 → 异常路径异常风险 | 高 | helper 内部全 try/except，**绝不抛**到外层 |
| audit helper 调用频次过高 → EventStore 压力 | 中 | spec §EC1 已说明本期不限速，留 F108 |
| notification HIGH 推送风暴 | 低 | 现有 sha256 去重已 cover；本期不加新去重 |
| 升级清单 8 条覆盖不全 / 升错点 | 中 | Codex pre-impl review 把关 |
| task_runner 没有 `_notification_service` 字段 | 中 | implement 阶段先 grep 确认；可能需从 self._orchestrator._notification_service 取 |
| 替换 emit_event 路径选错（直接 event_store vs TaskService.append_structured_event）| 中 | plan 选 TaskService.append_structured_event（与 ExecutionConsole.emit_log 一致），但 implement 时再确认是否 task_seq 序列化合理 |

---

## 5. Quality Gates

| Gate | 检查 |
|------|------|
| Constitution（原则 2/8/11）| WORKER_LOG_EMITTED / WORKER_ERROR payload 脱敏 + 无 secret + error_summary ≤ 200 字符 |
| Replay compatibility | 新 EventType 通过 `test_models.py` 回放兼容测试 |
| 0 regression | 全量 pytest vs F103 baseline (def6638) |
| e2e_smoke PASS | 必走 |
| Codex pre-impl review | spec + plan 完成后跑 1 轮 |
| Per-Phase review | A / B / C / D 完成后各 1 轮（如时间允许）|
| Final cross-Phase review | E 阶段必走 |

---

## 6. 时间预算（修正）

| Phase | 估时 | 说明 |
|-------|------|------|
| A EventType + Payload | 30 min | 加 2 enum 值 + 2 Pydantic class + 4 unit test |
| B audit helper | 1.5 h | 新文件 + 2 helper + 5 unit test |
| C 8 条升级 | 3 h | 含 dispatch_exception 改造（最大块）|
| D N-H1 e2e test | 1 h | 新测试用例 |
| Codex pre-impl review + 处理 | 1.5 h | spec + plan 一并 review |
| Codex Final review + 处理 | 1.5 h | 全量 + completion-report |
| 验证 + commit | 1 h | regression + e2e_smoke + commit |
| **总计** | **~10 h** | 1 天内 |

---

## 7. 不在范围（spec §4 + 额外排除）

- ❌ traceback 存 artifact（spec FR-A3 traceback_artifact_id 是 optional 字段，**本期不实施**，留空 None；如未来需要再加）
- ❌ structlog processor 框架级集成（让所有 structlog 调用自动 audit）—— 范围太大
- ❌ task_seq 序列化优化 / 排序保证 —— 现有 task_seq 已 cover
- ❌ control_plane API 新增 WORKER_ERROR/WORKER_LOG_EMITTED 查询端点 —— 现有 events API 已 cover EventType filter
- ❌ frontend UI 显示新事件 —— 不在 F103c 范围
