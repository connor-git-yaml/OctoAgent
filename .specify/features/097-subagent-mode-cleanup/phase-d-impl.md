# F097 Phase D — 实施报告

**日期**: 2026-05-10  
**分支**: feature/097-subagent-mode-cleanup  
**依赖**: Phase B（7a402c3）完成后实施

---

## 实施概要

Phase D 为 RuntimeHintBundle caller→child 拷贝（AC-D1 / AC-D2）。在 `_launch_child_task` 中，当 `target_kind == SUBAGENT` 时，向 `child_message.control_metadata` 追加 `__caller_runtime_hints__` raw key，包含从 caller 执行上下文提取的 RuntimeHintBundle 字段。Worker 路径（target_kind=WORKER）不受影响（AC-D2）。

---

## §1 TD.0：RuntimeHintBundle 字段实测确认

**文件**：`packages/core/src/octoagent/core/models/behavior.py:206`

实测字段清单（共 10 个，`recent_failure_budget` **不存在**，与 phase-0-recon.md 一致）：

| 字段名 | 类型 | 默认值 |
|--------|------|--------|
| `surface` | `str` | `""` |
| `can_delegate_research` | `bool` | `False` |
| `recent_clarification_category` | `str` | `""` |
| `recent_clarification_source_text` | `str` | `""` |
| `recent_worker_lane_worker_type` | `str` | `""` |
| `recent_worker_lane_profile_id` | `str` | `""` |
| `recent_worker_lane_topic` | `str` | `""` |
| `recent_worker_lane_summary` | `str` | `""` |
| `tool_universe` | `ToolUniverseHints \| None` | `None` |
| `metadata` | `dict[str, Any]` | `{}` |

---

## §2 TD.1：实施决策

### 拷贝字段写入路径

**选择方案 (c)**：使用 `__caller_runtime_hints__` raw key（与 Phase B 的 `__subagent_delegation_init__` 风格一致），不扩展 normalize CONTROL_METADATA_KEYS 白名单。

**原因**：
- Phase B 已建立 `__xxx__` 双下划线 raw key 约定，Phase D 继承保持一致性
- 不扩散白名单（避免 Phase E P1-1 类似的扩散问题）
- 将来 child runtime 消费时统一从 `control_metadata["__caller_runtime_hints__"]` 读取

### caller 字段来源

| 字段 | 来源 | 说明 |
|------|------|------|
| `surface` | `exec_ctx.runtime_context.surface`（优先）/ `parent_task.requester.channel`（fallback）| RuntimeControlContext.surface 是 spawn 时最准确的来源 |
| `can_delegate_research` | `False`（默认）| spawn 调用时不可知 caller 的 delegate_research 状态 |
| `recent_worker_lane_*` | `""`（默认）| per-turn 动态构建，spawn 时 caller 的 turn 已结束，字段不持久化 |
| `tool_universe` | `None`（默认）| ToolUniverseHints 不在 ExecutionRuntimeContext 中存储 |
| `recent_clarification_*` | `""`（默认）| 同 recent_worker_lane，per-turn 状态 |

**设计说明**：`RuntimeHintBundle` 的大多数字段（除 `surface`）是 per-turn 动态构建的，`build_runtime_hint_bundle` 函数在每次 `build_task_context` 时从 `dispatch_metadata` 重新构建，不持久化到 `AgentRuntime` 或 `ExecutionRuntimeContext`。因此 Phase D 能拷贝的最有价值字段是 `surface`（它确实来自 `RuntimeControlContext.surface`，在整个任务生命周期内稳定）。

### 实施位置

**文件**：`octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`

**位置**：`_launch_child_task`（L1285 附近），在 Phase B 的 `__subagent_delegation_init__` 写入块之后，同一个 `if target_kind == SUBAGENT` 分支内

**净增行数**：+44 行（含注释和失败隔离 try-except）

### 失败隔离

拷贝逻辑包裹在 `try/except Exception` 中：
- 失败时 `log.warning("phase_d_hint_copy_failed", ...)`
- spawn 主流程不中断（AC-D1 容错要求）
- `__caller_runtime_hints__` 不写入 control_metadata（child runtime 读取时应有 fallback 处理）

---

## §3 TD.2：测试实施

**新建文件**：`octoagent/apps/gateway/tests/services/test_capability_pack_phase_d.py`

**净增行数**：+410 行（7 个测试）

| 测试名 | 验证目标 |
|--------|---------|
| `test_subagent_spawn_has_caller_runtime_hints` | AC-D1: SUBAGENT spawn → `__caller_runtime_hints__` key 存在 |
| `test_subagent_spawn_caller_hints_fields_complete` | AC-D1: 9 个必须字段完整（surface/can_delegate_research/recent_*/tool_universe）|
| `test_subagent_spawn_surface_from_exec_ctx` | AC-D1: surface 值与 exec_ctx.runtime_context.surface 一致 |
| `test_worker_spawn_no_caller_runtime_hints` | AC-D2: WORKER spawn → control_metadata 不含 `__caller_runtime_hints__` |
| `test_main_target_no_caller_runtime_hints` | AC-D2: target_kind=main → control_metadata 不含 `__caller_runtime_hints__` |
| `test_hint_copy_failure_does_not_block_spawn` | 失败隔离: exec_ctx RuntimeError → spawn 成功，不含 hints，不抛异常 |
| `test_subagent_spawn_surface_fallback_to_channel` | AC-D1: exec_ctx.surface 为空 → fallback 到 parent_task.requester.channel |

---

## §4 TD.2 消费路径推迟声明

**推迟原因**：spec AC-D1/D2 仅要求"control_metadata 包含从 caller 拷贝的字段"，不强制 child runtime 真消费。

**推迟到**：Phase F（Memory α 共享引用）或 future Feature。

**child runtime 消费路径预留**：child runtime 在 `build_task_context` 时从 `dispatch_metadata["__caller_runtime_hints__"]` 读取，覆盖默认的 RuntimeHintBundle 构建逻辑（具体字段 `surface` 已可从 `dispatch_metadata` 的原有路径获取，`tool_universe` 等需要 Phase F+ 显式接入）。

---

## §5 验证结果

### Layer 1: 工具链验证

**Phase D 新测试**：
- 命令: `pytest octoagent/apps/gateway/tests/services/test_capability_pack_phase_d.py -v`
- 退出码: 0
- 输出摘要: `7 passed in 1.31s`

**capability_pack / agent_context 回归**：
- 命令: `pytest octoagent/apps/gateway/tests/services/ -k "capability_pack or agent_context" -q`
- 退出码: 0
- 输出摘要: `33 passed, 79 deselected in 1.59s`

**全量回归（排除 e2e）**：
- 命令: `pytest octoagent/ -q --tb=short -m "not e2e_full and not e2e_smoke"`
- 退出码: 0
- 输出摘要: `3336 passed, 12 skipped, 22 deselected, 1 xfailed, 1 xpassed in 110.54s`
- vs Phase B baseline（3329 passed）：**+7 新增测试，0 regression**

### Layer 2: 行为验证

**AC-D1 happy path**：
- "spawn SUBAGENT task → 检查 child task control_metadata → 确认含 `__caller_runtime_hints__` dict，且 `surface` 字段与 caller runtime_context.surface 值一致"
- 状态: 单测覆盖验证通过

**AC-D2 regression**：
- "spawn WORKER task → 检查 child task control_metadata → 确认不含 `__caller_runtime_hints__`"
- 状态: 单测覆盖验证通过

### Layer 3: 失败路径验证

- "exec_ctx.get_current_execution_context() 抛 RuntimeError → spawn 主流程继续，log warn 记录，control_metadata 不含 hints"
- 测试: `test_hint_copy_failure_does_not_block_spawn` PASSED

---

## §6 AC 对齐

| AC | 状态 | 说明 |
|----|------|------|
| AC-D1 | ✅ | SUBAGENT spawn → control_metadata 含 `__caller_runtime_hints__`，surface 字段与 caller 值一致 |
| AC-D2 | ✅ | WORKER / main spawn → control_metadata 不含 `__caller_runtime_hints__` |

---

## §7 实施偏差说明

| 偏差 | 计划 | 实际 | 原因 |
|------|------|------|------|
| 拷贝策略 | 直接加白名单或写到原 control_metadata 字段 | 用 `__caller_runtime_hints__` raw key | 与 Phase B 约定一致，不扩散白名单 |
| 可拷贝字段 | `surface / tool_universe / recent_failure_budget / recent_worker_lane_*` | `surface`（有值）+ 其余字段以默认值写入 | `recent_failure_budget` 不存在；其余字段 per-turn 动态构建，spawn 时不持久化 |
| 测试文件名 | `test_capability_pack_launch.py` | `test_capability_pack_phase_d.py` | 任务描述要求命名，编排器指令一致 |
| recent_worker_lane_* 实际值 | 从 caller 运行时读取 | 默认值空字符串 | caller turn 执行已结束，这些字段不在持久化的 ExecutionRuntimeContext 中 |
