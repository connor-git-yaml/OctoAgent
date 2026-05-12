# F099 Codex Re-Re-Review (post 2f867e6)

Date: 2026-05-12
Reviewer: Codex (adversarial)
Task ID: `019e1c58-560b-7df0-996d-a29059984602`
Target commit: `2f867e6` fix(F099-Phase-Verify): N-H1 闭环（is_caller_worker resume 持久化）

> Note: Codex sandbox blocked file write; artifact transcribed by main session from Codex output.

---

## N-H1 状态：**PARTIAL（从 HIGH 降级）**

### 闭环部分

- `attach_input` resume 路径已补 `is_caller_worker_signal` 到 `resume_state_snapshot`
- `task_runner.py:599-623` 读 `latest_user_metadata.is_caller_worker_signal` 附加到 snapshot
- `WorkerRuntime.run()` resume 时读 `resume_state_snapshot.is_caller_worker_signal` 恢复字段
- `connection_metadata.py` 注册 `is_caller_worker_signal` 为 TASK_SCOPED_CONTROL_KEYS（保证 merge_control_metadata 合并）

### 未闭环部分（其余 resume 路径）

- 手动 resume
- startup orphan recovery
- prepared/deferred dispatch resume

三条路径**未补入 is_caller_worker_signal**。`WorkerRuntime.run()` 在 resume_state_snapshot 缺信号时 fallback 到硬编码 `_is_caller_worker = True`。

### 功能性正确性分析

虽然非 attach_input resume 路径未补信号，但实际**功能正确**：

| Resume 场景 | Executor | is_caller_worker 取值 | 注入结果 |
|------------|----------|---------------------|---------|
| worker→worker，attach_input resume | WorkerRuntime | snapshot 读 True | source=worker ✅ |
| worker→worker，其他 resume | WorkerRuntime | fallback hardcoded True | source=worker ✅ |
| main→worker，attach_input resume | MainRuntime（非 worker runtime）| snapshot 无信号 → False | source=main ✅ |
| main→worker，其他 resume | MainRuntime | 不走 WorkerRuntime → 默认 False | source=main ✅ |

**结论**：N-H1 实际不可触发为可观测 bug，但 defense-in-depth 设计不完整。

---

## 其他 finding

### M-1（MEDIUM，新）：`_emit_is_caller_worker_signal()` broad-catch 吞异常

- 位置：`worker_runtime.py:_emit_is_caller_worker_signal()`
- 描述：`except Exception: log.warning(...)` 吞所有 EventStore append 失败
- 影响：EventStore 故障时 audit signal 丢失，但不阻断主流程
- 建议：保留 broad-catch（与 F4 _emit_ask_back_audit 一致模式），但增加 `task_id` / `error_class` 结构化字段
- **状态**：可接受归档（与 F4 同模式）

### L-1（LOW，新）：F5 空字符串 LLM retry 风险

- 位置：`ask_back_tools.py` RUNNING guard 失败路径
- 描述：返回 `""`（空字符串），LLM 可能不识别为 error，进而重试 ask_back
- 建议：返回 `"task_not_running"` 或 metadata 说明
- **状态**：未测试覆盖，归档 F101

---

## 总结

- HIGH: **0 残留**（N-H1 降级为 PARTIAL，但实际不可触发为可观测 bug）
- MEDIUM: **1 新（M-1 broad catch）** + **1 已知保持（F5）**
- LOW: **1 新（L-1 F5 retry）** + **1 已知保持（N-L1 sse_push_fn）**

## 最终判定

**0 HIGH 残留**，符合 CLAUDE.local.md §"状态收敛"原则。

可合入 master 的前提：
- ✅ 0 HIGH 残留
- ✅ 全量回归 0 regression
- ⚠️ 4 个 known issue 必须在 handoff/completion-report 明记（F3 / F5 PARTIAL / M-1 / N-L1 → F101 接收）
- ⚠️ N-H1 PARTIAL 部分（其余 3 条 resume 路径未补信号）必须在 handoff 明记，F101 / F107 评估是否补全

---

v1.0 — Codex re-re-review
