# F100 Phase F — ask_back resume 真实恢复机制实测

**Date**: 2026-05-15
**Phase**: F（HIGH-3 修复关键拦截点）
**Status**: 实测完成，发现 v0.3 修订已自动覆盖 HIGH-3

---

## 1. 实测调用链

`worker.ask_back` 工具调用 → `execution_context.request_input(prompt)` → task.status=WAITING_INPUT
→ user `attach_input(task_id, text)` → `task_runner.attach_input` (line 577-640)
→ `_spawn_job(task_id, resume_from_node="state_running", resume_state_snapshot={..., "is_caller_worker_signal": "1"})`（F099 N-H1 修复）
→ (background) `_run_job(task_id, ..., resume_from_node, resume_state_snapshot)` (line 680)
→ **line 692**: `metadata = await service.get_latest_user_metadata(task_id)`
→ **line 693-700**: `self._orchestrator.dispatch(task_id=, user_text=, ..., metadata=metadata)`

---

## 2. `get_latest_user_metadata` 字段范围

文件：`task_service.py:2704-2707`

仅返回 `TASK_SCOPED_CONTROL_KEYS` 中的字段（按 `connection_metadata.py:33-59` 定义）。

**TASK_SCOPED_CONTROL_KEYS** 内容（实测）：
- session_owner_profile_id / inherited_context_owner_profile_id / session_id / thread_id
- parent_task_id / parent_work_id / spawned_by / source_agent_runtime_id / source_agent_session_id
- child_title / worker_plan_id / retry_source_task_id / retry_action_source / retry_actor_id
- subagent_delegation (F097)
- **is_caller_worker_signal** (F099 N-H1)

**不包含**：`runtime_context_json` / `runtime_context`

---

## 3. resume 路径 runtime_context 演化

```
turn N（chat 首发派发）:
  chat.py:433-445 写入 metadata[RUNTIME_CONTEXT_JSON_KEY] = encode(RuntimeControlContext(...))
  → orchestrator.dispatch 调用
  → orchestrator._prepare_single_loop_request 等内部 patch runtime_context
  → 派发到 worker_runtime / 直跑 LLM
  → 最终 task_service._build_memory_recall_plan
     runtime_context 从 dispatch_metadata 中解析（可能含或不含 runtime_context_json）

turn N → WAITING_INPUT (ask_back)
  task.status = WAITING_INPUT
  WorkerRuntime 挂起

attach_input → turn N+1 resume:
  task_runner._run_job line 692: metadata = get_latest_user_metadata(task_id)
    ↑ 注意：此 metadata 走 TASK_SCOPED_CONTROL_KEYS allowlist，不含 runtime_context_json
  task_runner._run_job line 693-700: orchestrator.dispatch(..., metadata=metadata)
    ↑ 派发时不再有 turn N 的 runtime_context_json
  orchestrator._prepare_single_loop_request line 770:
    runtime_context_for_check = request.runtime_context or runtime_context_from_metadata(metadata)
    request.runtime_context = None（dispatch caller 没传）
    runtime_context_from_metadata(metadata) = None（metadata 不含 runtime_context_json）
    → runtime_context_for_check = None
  orchestrator._prepare_single_loop_request line 771:
    is_single_loop_main_active(None, metadata)
    F091 baseline: None → fallback metadata_flag("single_loop_executor")
                   metadata 走 TASK_SCOPED_CONTROL_KEYS 已不含 single_loop_executor
                   → False
    → orchestrator 走 standard routing 路径
```

---

## 4. baseline 行为：ask_back resume 后 turn N+1 跑 recall planner

由于 resume 后 runtime_context 信息丢失（不在 TASK_SCOPED_CONTROL_KEYS 透传范围），baseline 行为是：
- `is_recall_planner_skip(None, metadata)` → fallback metadata_flag → metadata 缺 single_loop_executor → **False**
- → **recall planner phase 跑**（不 skip）

这与 spec.md v0.1/v0.2 假设的"worker_inline 默认 skip"**不一致**——因为 runtime_context 已丢失，`delegation_mode` 不再是 worker_inline 而是 None/unspecified。

---

## 5. F100 v0.3 行为：与 baseline 完全等价（HIGH-3 自动闭环）

v0.3 修订：unspecified/None → return False（移除 fallback，但等价结果）

- `is_recall_planner_skip(None, metadata)` → **return False**（v0.3）
- `is_recall_planner_skip(None, metadata)` → fallback metadata_flag → False（baseline）
- **行为完全等价**

**结论**：Codex HIGH-3 担心的"Phase E 删 fallback 后 ask_back resume 静默漂移"在 v0.3 修订下**不存在**——unspecified/None 始终 return False。

---

## 6. spec FR-E 叙述修正

spec.md v0.2 假设 worker_inline 通过 turn N+1 派发时 orchestrator 重新设置 delegation_mode。**实测发现**这个假设错误——orchestrator 在 resume 路径上也没有重新 patch 到 worker_inline。

**实际机制（v0.3 闭环）**：
- resume 后 runtime_context 信息完全丢失 → runtime_context = None
- helper 返回 False（恶意默认走 standard routing + 跑 recall planner）
- baseline 与 F100 v0.3 行为一致

**FR-E1/E3 spec 叙述更新**（v0.3 修订记录在 spec §0.3）：
- 删除"worker_inline 在 turn N+1 派发时 orchestrator 重设 delegation_mode"这个假设
- 改为"ask_back resume 后 runtime_context 丢失（TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json）；helper 默认 return False；行为与 baseline metadata flag 缺失时等价"

---

## 7. 测试覆盖

新增 `tests/test_ask_back_recall_planner_resume.py`：
- AC-5：构造 ask_back resume 路径的 metadata（不含 runtime_context_json）→ `is_recall_planner_skip(None, metadata)` 返回 False
- AC-N-H1-COMPAT：构造含 `is_caller_worker_signal="1"` 的 metadata → resume_state_snapshot 透传不破
- AC-12：F099 ask_back 三工具行为 + source_runtime_kind 5 值枚举不动（已由 F099 测试覆盖）

---

## 8. Phase F 总结

- ✅ 实测调用链：`attach_input` → `_run_job` → `orchestrator.dispatch(metadata=get_latest_user_metadata)`
- ✅ TASK_SCOPED_CONTROL_KEYS 不含 runtime_context_json（Codex HIGH-3 验证 +）
- ✅ baseline ask_back resume 后 turn N+1 **跑** recall planner（不 skip）
- ✅ F100 v0.3 行为与 baseline 完全等价（HIGH-3 自动闭环）
- ✅ spec FR-E 叙述更新（删除错误假设）
- ✅ 测试覆盖准备

**MEDIUM 风险归档（F101 + handoff）**：
若 F101 / 独立 Feature 期望 ask_back resume 后保持 turn N delegation_mode（如 worker_inline → skip），需把 `runtime_context_json` 加入 TASK_SCOPED_CONTROL_KEYS，或显式 patch 到 resume_state_snapshot。**F100 不做此改动**——保持 baseline 行为兼容。
