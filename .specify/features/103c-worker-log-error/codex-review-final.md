# F103c — Codex Final Cross-Phase Review

> 时间：2026-05-26
> 范围：spec/plan/tasks/code/test 全量
> Baseline：F103 def6638 → F103b 1a358b4（rebase 后）→ F103c HEAD

## ⚠️ 重要说明：Codex backend 中断

Codex agent（`a24f2e53434f0b08a`）在执行 final review 过程中**完成 50+ 个工具调用后未输出 final finding** —— output 文件最后修改时间停在 23:13:06（提示 turn completed 但没有 actionable answer），后续 5+ 分钟无新输出，疑似 Codex backend / 通信中断（参考 CLAUDE.local.md F103 节"Codex review 中断（网络）→ 主 session 接管按 spec §8 review 重点主动抓 finding"模式）。

**主 session 接管按 pre-impl review 4 项维度手动 grep + diff 审视**。结论附后。

---

## 主 session 手动 Final Review（按 pre-impl finding 维度逐条验证）

### Check 1: PH1（HIGH）`agent_runtime_id=""` 派生是否全合规

**验证方法**：grep 所有 `audit_worker_log` / `audit_worker_error` 调用点，确认前置均有 `derive_agent_runtime_id` 调用。

```bash
grep -B 4 "audit_worker_log\|audit_worker_error" octoagent/apps/gateway/src/octoagent/gateway/services/{worker_runtime,task_runner,dispatch_service}.py
```

**结果**：8 处全部合规

| File:Line | 调用 | 派生工具 |
|-----------|------|---------|
| `worker_runtime.py:446` (in `_emit_is_caller_worker_signal`) | `audit_worker_log` | `derive_agent_runtime_id(envelope_metadata)` |
| `worker_runtime.py:614` (a2a heartbeat) | `audit_worker_log` | `derive_agent_runtime_id(envelope.metadata)` |
| `worker_runtime.py:651` (first output timeout) | `audit_worker_log` | `derive_agent_runtime_id(envelope.metadata)` |
| `task_runner.py:359` (subagent_delegation_init_failed) | `audit_worker_log` | `derive_agent_runtime_id(_task_meta)` |
| `task_runner.py:903` (attach_input resume read failed) | `audit_worker_log` | `derive_agent_runtime_id(_resume_meta)` |
| `task_runner.py:1006` (dispatch_exception) | `audit_worker_error` | `derive_agent_runtime_id(_exc_meta)` |
| `task_runner.py:1245` (job timeout) | `audit_worker_log` | `derive_agent_runtime_id(_to_meta)` |
| `dispatch_service.py:986` (a2a target profile failed) | `audit_worker_log` | `derive_agent_runtime_id(envelope_metadata)` |

**helper 入口断言**（worker_audit_logger.py `_assert_audit_inputs`）防御性兜底：caller 即便 bypass 派生，helper 也会 raise AssertionError。

**结论**：✅ **PH1 完全闭环**，无 audit chain 破坏风险。

### Check 2: PM1（MEDIUM）event_id 传递做幂等

**验证方法**：审视 `audit_worker_error` 实现是否真把 emit 返回的 event_id 传给 `state_transition_event_id`。

文件：`worker_audit_logger.py:193`

```python
await notification_service.notify_task_state_change(
    task_id=task_id,
    event_type="WORKER_ERROR",
    payload={...},
    priority=NotificationPriority.HIGH,
    state_transition_event_id=event.event_id,  # ← PM1 闭环
)
```

**单测验证**：`test_worker_audit_logger.py` TestAuditWorkerError 5 case：
- `test_emits_and_notifies_high_with_event_id`：断言 `state_transition_event_id == mock_event.event_id` ✅
- `test_emit_failure_skips_notify`：emit 失败 → notify 不调用（防止 notify 没有有效 event_id）✅

**结论**：✅ **PM1 完全闭环**，sha256 幂等做到位；跨 task storm control 已明确归档 F108。

### Check 3: PM2（MEDIUM）升级清单精确性

**验证方法**：spec §0.3 表 vs 代码 grep 双向核对。

```
$ grep -n "audit_worker_log\|audit_worker_error" worker_runtime.py task_runner.py dispatch_service.py
worker_runtime.py: 3 audit_worker_log
task_runner.py:    3 audit_worker_log + 1 audit_worker_error
dispatch_service.py: 1 audit_worker_log
合计：7 audit_worker_log + 1 audit_worker_error = 8 处
```

逐 key 核对（7 + 1 全部覆盖 spec §0.3 表）：
- ✅ `worker_runtime_emit_is_caller_worker_signal_failed`
- ✅ `worker_runtime_a2a_heartbeat_failed`
- ✅ `worker_runtime_first_output_timeout_budget_exceeded`
- ✅ `subagent_delegation_init_failed`
- ✅ `attach_input_resume_is_caller_worker_signal_read_failed`
- ✅ `task_runner_job_timeout`
- ✅ `a2a_target_profile_resolve_worker_binding_failed`
- ✅ `run_job_dispatch_exception` (WORKER_ERROR 路径)

`dispatch_service.py:988` (`a2a_target_profile_explicit_id_not_found`) / `:994` (`a2a_target_profile_fallback_to_source`) 保留 structlog only（不在范围）—— 与 spec §0.3 一致。

**结论**：✅ **PM2 完全闭环**，无范围漂移。

### Check 4: PM3（MEDIUM）helper 入参 TaskService

**验证方法**：grep helper 签名 + caller 调用点。

```python
# worker_audit_logger.py 函数签名
async def audit_worker_log(
    task_service: TaskService | None,  # ✅ TaskService（不是 StoreGroup）
    ...
```

caller 调用全部传 TaskService 实例：
- worker_runtime.py：传 `task_service`（line 540 `task_service = TaskService(self._stores, self._sse_hub, project_root=self._project_root)`）
- task_runner.py：传 `_task_svc_init` / `_task_svc_for_audit` / `service` / `_task_svc_to`（全部 `TaskService(self._stores, self._sse_hub)`）
- dispatch_service.py：传 `_audit_svc = TaskService(self._stores, self._sse_hub)`

helper 内部走 `task_service.append_structured_event(...)` → `_sse_hub.broadcast` 路径（task_service.py:393-394）—— SSE 广播全部 cover。

**结论**：✅ **PM3 完全闭环**，AC1-2 / AC2-1 SSE 可见性成立。

---

## 主 session 额外 review（pre-impl 未覆盖的新维度）

### A. dispatch_exception 改造路径异常隔离

`task_runner.py:1004-1025`：audit_worker_error 调用包在 outer try/except 中，audit 失败仅 log.warning 不影响后续 `mark_failed` / `_ensure_task_failed` / `_close_subagent_session_if_needed`。

```python
try:
    ...
    agent_runtime_id, degraded_reason = derive_agent_runtime_id(_exc_meta)
    ...
    await audit_worker_error(...)
except Exception:
    log.warning("worker_error_audit_failed", task_id=task_id, exc_info=True)
try:
    await self._stores.task_job_store.mark_failed(task_id, error_summary)
except Exception:
    log.warning("run_job_mark_failed_fallback", task_id=task_id, exc_info=True)
...
```

**结论**：✅ exception 路径异常隔离做到位，无 mark_failed 阻塞风险。

### B. helper 高频调用对 EventStore 性能影响

升级路径中真正高频的：
- `worker_runtime.py:614` (a2a heartbeat 失败) —— 每 loop_step 失败时触发；正常情况心跳不失败，failure-only 调用
- `worker_runtime.py:651` (first output timeout) —— 每 loop_step 超时时触发；正常情况不超时

**正常路径不触发**——所有升级都在 except / failure 分支。Constitution §11 上下文卫生无破坏风险。

**结论**：✅ EventStore 容量风险可忽略；F108 storm control 兜底。

### C. dispatch_service `_resolve_target_agent_profile` 签名扩展兼容性

grep `_resolve_target_agent_profile(` 全仓库：
- 定义：`dispatch_service.py:925`（扩签名加 `task_id=""` + `envelope_metadata=None` 默认值）
- caller：`dispatch_service.py:209`（升级后传 task_id + envelope_metadata）

无其他 caller。signature 改动**完全向后兼容**。

**结论**：✅ 无 caller 破坏。

### D. F101 NotificationService 集成兼容性

`notify_task_state_change(priority=NotificationPriority.HIGH, ...)` 传入：
- payload 含 task_id / task_title / error_class / error_summary
- state_transition_event_id 是新 emit `WORKER_ERROR` event_id（独立于 STATE_TRANSITION）
- event_type="WORKER_ERROR"（字符串，不与 STATE_TRANSITION:FAILED 冲突）

F101 sha256 去重键：`task_id:event_type:state_transition_event_id`
- 不同 event_type 不冲突（去重独立）
- 同一 event_type 同一 event_id → 幂等（防重发）

**结论**：✅ F101 集成无冲突。

### E. payload 脱敏审计

```bash
$ grep -rn "token\|api_key\|secret\|prompt_text" octoagent/apps/gateway/src/octoagent/gateway/services/worker_audit_logger.py
（空）
```

helper 模块自身无敏感字段。caller 传入 payload：
- worker_runtime.py 3 处 payload：dispatch_id / trace_id / worker_id / loop_step / error_type / elapsed_s / threshold_s
- task_runner.py 3 处 payload：delegation_id / session_id / error / 空
- task_runner.py dispatch_exception payload：error_class / error_summary（已 [:200] 截断）/ task_title

**无敏感字段泄露**。Constitution §8 MUST NOT 合规。

**结论**：✅ 脱敏审查通过。

### F. 测试覆盖完整性

| 维度 | 测试 |
|------|------|
| EventType + Payload schema | test_models.py 4 case（含 max_length 200 边界）|
| audit_worker_log 正常 + 容错 + PH1 断言 | test_worker_audit_logger.py 5 case |
| audit_worker_error 正常 + PM1 event_id + 容错 + 截断 | test_worker_audit_logger.py 5 case |
| derive_agent_runtime_id 4 优先级 + 全空降级 | test_worker_audit_logger.py 5 case |
| N-H1 resume_state_snapshot baseline lock | test_n_h1_resume_signal_f103c.py 3 case |
| _emit_is_caller_worker_signal C-1-1 升级行为 | test_n_h1_resume_signal_f103c.py 3 case |

**合计 25 个新 test，全过**。

**结论**：✅ 测试覆盖完整。

---

## 最终结论

| 检查项 | 状态 |
|--------|------|
| PH1 闭环 | ✅ |
| PM1 闭环 | ✅ |
| PM2 闭环 | ✅ |
| PM3 闭环 | ✅ |
| dispatch_exception 异常隔离 | ✅ |
| EventStore 性能影响 | ✅ |
| signature 兼容性 | ✅ |
| F101 集成 | ✅ |
| payload 脱敏 | ✅ |
| 测试覆盖 | ✅ |

**0 HIGH / 0 MEDIUM / 0 LOW 残留**。

**总体结论**：**建议合入 origin/master**。

**关键决策建议**：
1. F103c 范围严格收敛（spec 阶段从 8 滚雪球路径收回到 7+1 精确清单），单一 day 内完成。
2. baseline-recon.md 实证显示 9 连 spec 阶段实测 pattern 对避免"基于 prompt 假设设计"价值显著——**强制后续 Feature 沿用**。
3. Codex pre-impl review 4 项 finding 全是实质问题（PH1/PM1/PM2/PM3）——**强制 Codex review 节点继续保留**。

## 已知 limitations

- Codex Final review 因 backend 中断未输出 official 报告；主 session 按 4 项 finding 维度手动 grep + diff 验证全合规
- 主 session manual review 可能遗漏 Codex 视角发现的隐性问题；用户合入前可独立触发一次 `/codex:adversarial-review` 二次确认（建议但非强制）
- e2e_live test_e2e_delegation_a2a 失败属环境 OAuth 凭证过期，与 F103c 无关
