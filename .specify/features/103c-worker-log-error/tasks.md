# F103c — 任务清单（Tasks）

> 上游：spec.md / plan.md / baseline-recon.md
> 5 Phase 拆分，每 Phase 独立可 commit

---

## Phase A — EventType + Payload Schema（30 min）

- **A-1** 在 `octoagent/packages/core/src/octoagent/core/models/enums.py` 的 `EventType` 枚举中，紧跟 `WORKER_DISPATCHED` / `WORKER_RETURNED`（行 131-132）后追加：
  - `WORKER_LOG_EMITTED = "WORKER_LOG_EMITTED"`
  - `WORKER_ERROR = "WORKER_ERROR"`
- **A-2** 在 event_payloads 文件（先 `grep -rn "class ControlMetadataUpdatedPayload"` 找）新增：
  - `WorkerLogEmittedPayload`: `task_id: str`, `agent_runtime_id: str = ""`, `level: Literal["info","warning","error"]`, `key: str`, `payload: dict[str, Any]`, `agent_session_id: str | None = None`
  - `WorkerErrorPayload`: `task_id: str`, `agent_runtime_id: str = ""`, `error_class: str`, `error_summary: str = Field(max_length=200)`, `traceback_artifact_id: str | None = None`, `agent_session_id: str | None = None`
- **A-3** 在 `octoagent/packages/core/tests/test_models.py`（或新建 test_worker_event_payloads.py）：
  - assertion `EventType.WORKER_LOG_EMITTED == "WORKER_LOG_EMITTED"`
  - assertion `EventType.WORKER_ERROR == "WORKER_ERROR"`
  - WorkerLogEmittedPayload roundtrip（model_dump → model_validate）
  - WorkerErrorPayload roundtrip
  - WorkerErrorPayload error_summary >200 字符 → ValidationError

**Gate A**：`pytest octoagent/packages/core/tests/` 0 regression，新 5+ test PASS。

---

## Phase B — audit_worker_log helper（1.5 h）

> **Codex review 闭环说明**：B-1 helper 入参从 `StoreGroup` 改为 `TaskService`（PM3 修正 SSE 广播路径）；新增 `derive_agent_runtime_id` 派生工具（PH1）；`audit_worker_error` 内部传 event_id 给 `state_transition_event_id`（PM1 幂等）；新增 `degraded_reason` 字段。

- **B-1** 新建 `octoagent/apps/gateway/src/octoagent/gateway/services/worker_audit_logger.py`，导出：
  - `audit_worker_log(task_service: TaskService, *, task_id: str, agent_runtime_id: str, level: Literal["info","warning","error"], key: str, payload: dict[str, Any], agent_session_id: str | None = None, degraded_reason: str | None = None) -> Event | None`
    - **入口断言**（PH1 闭环）：`assert agent_runtime_id != "" or degraded_reason is not None`，违反则 raise AssertionError（防止静默空串）
    - 调 structlog `getattr(log, level)(key, **payload)`（双轨）
    - try：`event = await task_service.append_structured_event(task_id=task_id, event_type=EventType.WORKER_LOG_EMITTED, actor=ActorType.WORKER, payload=WorkerLogEmittedPayload(...).model_dump(mode="json"))`
    - except Exception → `log.warning("worker_audit_log_emit_failed", task_id=task_id, key=key, exc_info=True)` + 返回 None，**不抛**
  - `audit_worker_error(task_service: TaskService, *, task_id: str, agent_runtime_id: str, error_class: str, error_summary: str, traceback_artifact_id: str | None = None, agent_session_id: str | None = None, notification_service: NotificationService | None = None, task_title: str | None = None, degraded_reason: str | None = None) -> Event | None`
    - 同 PH1 断言
    - emit `WORKER_ERROR` 事件，**捕获返回的 Event 对象**（用于 PM1 event_id 传递）
    - 如果 notification_service 非 None 且 event 非 None → 调 `notify_task_state_change(task_id=task_id, event_type="WORKER_ERROR", payload={task_id, task_title, error_class, error_summary}, priority=NotificationPriority.HIGH, state_transition_event_id=event.event_id)`（**PM1 闭环**：传 event_id 做幂等）
    - try/except 全兜底，不抛；返回 emit 的 Event 实例（test 用），失败时返回 None
  - **`derive_agent_runtime_id(metadata: dict | None) -> tuple[str, str | None]`**（PH1 闭环派生工具）按优先级：
    1. `metadata.get("agent_runtime_id")`
    2. `metadata.get("target_agent_runtime_id")`
    3. `metadata.get("source_agent_runtime_id")`
    若以上全为空字符串/None → 返回 `("", "agent_runtime_id_unavailable")`
- **B-2** 新建 `octoagent/apps/gateway/tests/test_worker_audit_logger.py`，至少 8 case（PH1/PM1 测试加严）：
  1. `audit_worker_log` 正常路径：emit 成功 → EventStore 有 `WORKER_LOG_EMITTED` 一条 + SSE broadcast 调一次
  2. `audit_worker_log` `agent_runtime_id=""` + `degraded_reason=None` → raise AssertionError（PH1）
  3. `audit_worker_log` `agent_runtime_id=""` + `degraded_reason="agent_runtime_id_unavailable"` → 不抛，event payload 含 degraded_reason
  4. `audit_worker_log` `task_service.append_structured_event` 抛 → 不抛，log.warning 兜底，返回 None
  5. `audit_worker_error` 正常：EventStore 有 `WORKER_ERROR` + notification_service mock 收到 `priority=HIGH` **且 `state_transition_event_id == event.event_id`**（PM1 验收）
  6. `audit_worker_error` notification_service=None：仅 emit event 不抛
  7. `audit_worker_error` notification_service.notify_task_state_change 抛 → 不抛，log.warning 兜底
  8. `derive_agent_runtime_id` 4 case：4 个优先级各一 + 全空降级返回 `("", "agent_runtime_id_unavailable")`

**Gate B**：`pytest octoagent/apps/gateway/tests/test_worker_audit_logger.py` PASS；新 helper 文件 ≤ 150 行。

---

## Phase C — 8 条 logger 升级（3 h）

> 双轨原则：**保留原 structlog 调用 + 加 helper 调用**。

### C-1: worker_runtime.py（3 处）

- **C-1-1** `worker_runtime.py:442` — `worker_runtime_emit_is_caller_worker_signal_failed`
  - 前置 `await audit_worker_log(store=task_service._stores, task_id=task_id, agent_runtime_id="", level="warning", key="worker_runtime_emit_is_caller_worker_signal_failed", payload={"dispatch_id": dispatch_id})`
  - 保留原 `log.warning(...)`
- **C-1-2** `worker_runtime.py:602` — `worker_runtime_a2a_heartbeat_failed`
  - 前置 `await audit_worker_log(store=self._stores, task_id=envelope.task_id, agent_runtime_id=envelope.agent_runtime_id or "", level="warning", key="worker_runtime_a2a_heartbeat_failed", payload={"worker_id": worker_id, "loop_step": step, "error_type": type(exc).__name__})`
  - 注：先 grep 看 envelope 是否有 `agent_runtime_id` 字段，没有用 `""`
  - 保留原 `log.warning(...)`
- **C-1-3** `worker_runtime.py:630` — `worker_runtime_first_output_timeout_budget_exceeded`
  - 前置 `await audit_worker_log(store=self._stores, task_id=envelope.task_id, agent_runtime_id=..., level="info", key=..., payload={"elapsed_s": round(elapsed, 3), "threshold_s": self._config.first_output_timeout_seconds})`
  - 保留原 `log.info(...)`

### C-2: task_runner.py（3 处升级 + 1 处核心改造）

- **C-2-1** `task_runner.py:348` — `subagent_delegation_init_failed`
  - 前置 helper 调用（payload 含 caller info）
  - 保留原 log
- **C-2-2** `task_runner.py:879` — `attach_input_resume_is_caller_worker_signal_read_failed`
  - 前置 helper（level=warning）
  - 保留原 log
- **C-2-3** `task_runner.py:1187` — `task_runner_job_timeout`
  - 前置 helper（level=warning）
  - 保留原 log
- **C-2-4 核心** `task_runner.py:955-979` — dispatch_exception 改造：
  - 在 `log.error(...)` 之后、`error_summary = ...` 之后插入：
    ```python
    try:
        await audit_worker_error(
            store=self._stores,
            task_id=task_id,
            agent_runtime_id="",
            error_class=type(exc).__name__,
            error_summary=error_summary[:200],
            notification_service=self._notification_service,  # 见 implement 时确认获取路径
        )
    except Exception:
        log.warning("worker_error_audit_failed", task_id=task_id, exc_info=True)
    ```
  - **保留** baseline 的 `mark_failed` / `_ensure_task_failed` / `_close_subagent_session_if_needed`
  - implement 阶段 grep `self._notification_service` 在 task_runner 中是否已注入；如未注入，从 self._orchestrator._notification_service 取或新增字段

### C-3: dispatch_service.py（精确 1 条，Codex PM2 冻结）

- **C-3-1** `dispatch_service.py:974` — `a2a_target_profile_resolve_worker_binding_failed`
  - 前置 helper 调用（level=warning）
  - agent_runtime_id：使用上下文已派生的 `source_agent_runtime_id` 或 `target_agent_runtime_id` 变量；如均为空 → `degraded_reason="agent_runtime_id_unavailable"`
  - 保留原 `log.warning(...)`
- **C-3-2** `:988` / `:994` 两条**不升级**，保留 structlog only

### C-4: 集成测试

- **C-4** 新建 `octoagent/apps/gateway/tests/test_worker_audit_logger_integration.py`（或并入现有 test_task_runner_*）：
  - 触发 dispatch_exception（mock orchestrator.dispatch raise RuntimeError）→ 验证 EventStore 有 `WORKER_ERROR` + notification_service mock 被调 priority=HIGH
  - 触发 worker_runtime a2a heartbeat 失败 → 验证 EventStore 有 `WORKER_LOG_EMITTED` (level=warning, key=...heartbeat...)

**Gate C**：`pytest octoagent/apps/gateway/tests/` 0 regression + 集成测试 PASS。

---

## Phase D — N-H1 resume e2e 测试（1 h，独立）

- **D-1** 新建 `octoagent/apps/gateway/tests/test_n_h1_resume_signal.py`（或并入现有 resume 测试）：
  - 测试 1: CONTROL_METADATA_UPDATED 已写入 → 模拟 resume → worker_runtime.py:543 读到 `is_caller_worker_signal=="1"`
  - 测试 2: CONTROL_METADATA_UPDATED 未写入 → resume_state_snapshot 不含字段 → worker_runtime 回退到 baseline 默认（实测 worker_runtime.py:442 注释行为）
- **D-2** **不写新业务逻辑**——仅 lock baseline 行为

**Gate D**：测试 PASS，pytest grep `is_caller_worker_signal` 测试覆盖 ≥ 2 case。

---

## Phase E — 验证 + commit

- **E-1** 全量 `pytest octoagent/` —— 期望 ≥ 3649 passed (F103 baseline def6638) + 0 regression
- **E-2** `pytest -m e2e_smoke` —— 期望全部 PASS
- **E-3** 脱敏 grep: `grep -rn "token\|api_key\|secret" octoagent/apps/gateway/src/octoagent/gateway/services/worker_audit_logger.py` —— 期望 0 匹配
- **E-4** 升级清单**精确**验证（不允许 grep 数量替代）：按 spec §0.3 表逐 key 断言：
  - `worker_runtime.py` 中 3 处升级 key：`worker_runtime_emit_is_caller_worker_signal_failed` / `worker_runtime_a2a_heartbeat_failed` / `worker_runtime_first_output_timeout_budget_exceeded` 各 grep 出 `audit_worker_log` 调用
  - `task_runner.py` 中 3 处升级 key：`subagent_delegation_init_failed` / `attach_input_resume_is_caller_worker_signal_read_failed` / `task_runner_job_timeout`
  - `task_runner.py:958` `run_job_dispatch_exception` 路径下 grep 出 `audit_worker_error`
  - `dispatch_service.py:974` `a2a_target_profile_resolve_worker_binding_failed` 下 grep 出 `audit_worker_log`
  - 合计 7 `audit_worker_log` + 1 `audit_worker_error`，**精确**
- **E-5** F103b rebase 验证：如 F103b 已合 master → `git fetch origin master && git rebase origin/master` → 验证无冲突（应该零冲突，F103b 纯文档）
- **E-6** completion-report.md 起草（最终 Codex Final review 后定稿）
- **E-7** Codex Final review（必走）

---

## 依赖关系（addBlocks/addBlockedBy）

```
A → B → C → E
        ↑
D ──────┘ (可并行 A 后任何点)
```

- Phase A blocks Phase B
- Phase B blocks Phase C
- Phase C blocks Phase E
- Phase D blocked by Phase A 只（A 完成后 D 可任意时点跑）

---

## Commit 策略

每 Phase 完成后单独 commit（便于 review）：

1. `feat(F103c-A): EventType WORKER_LOG_EMITTED/WORKER_ERROR + payload schema`
2. `feat(F103c-B): audit_worker_log helper + unit tests`
3. `feat(F103c-C): 8 条 worker logger 升级 EventStore audit + WORKER_ERROR notification HIGH`
4. `test(F103c-D): N-H1 resume signal e2e 测试`
5. `docs(F103c-Final): Codex review 闭环 + completion-report`

---

## 已确认架构信息（implement 不需重新 grep）

- EventType 定义位置：`enums.py:131-132` 后追加
- StoreGroup 路径：`task_service._stores`（worker_runtime）/ `self._stores`（task_runner）
- TaskService.append_structured_event 入口：`task_service.py:368`
- NotificationService.notify_task_state_change：`notification.py:478`，priority=NotificationPriority.HIGH
- `_ensure_task_failed` → `_notify_state_change`：baseline 已通知（priority=默认 LOW），F103c 在此之外**叠加** WORKER_ERROR + HIGH 通知（不替换）
- 双轨原则：保留原 structlog 调用，前置 helper

## 待 implement 阶段 grep 确认

- `self._notification_service` 在 task_runner 中是否已注入（若无 → 通过 `self._orchestrator._notification_service` 取，已在 orchestrator.py:458 上下文证实存在）
- ~~envelope.agent_runtime_id 字段~~ **已确认**：通过 `envelope.metadata.get("agent_runtime_id")` 访问；优先级见 `derive_agent_runtime_id` 工具
- `event_payloads.py` 真实文件路径（grep `class ControlMetadataUpdatedPayload` 确认）
- ~~dispatch_service.py 4 条选择~~ **已冻结**：仅 `:974` `a2a_target_profile_resolve_worker_binding_failed` 升级
