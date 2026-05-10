# F097 Phase F 实施报告 — Memory α 共享引用

**日期**: 2026-05-10
**Phase**: F（Memory α 共享引用实施）
**baseline**: 620100a（Phase D 完成，3336 passed）
**worktree**: `F097-subagent-mode-cleanup`

---

## §1 实施概述

Phase F 实现了 OD-1 锁定的 α 语义：**Subagent 直接复用 caller 的 AGENT_PRIVATE namespace ID，不创建新的 namespace row**。

---

## §2 改动文件清单

| 文件 | 类型 | 净增减 |
|------|------|--------|
| `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py` | TF.1 修改 | +36 行 |
| `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py` | TF.2 修改 | +100 行（α 路径 + 调用方 delegation 读取）|
| `octoagent/apps/gateway/tests/services/test_agent_context_phase_f.py` | TF.3/TF.4 新建 | +380 行（8 个测试）|

---

## §3 AC 对齐确认

### AC-F1（α 语义：subagent 不创建新 AGENT_PRIVATE namespace）

**实施位置**：`agent_context.py` `_ensure_memory_namespaces` 函数头部
- 新增参数 `_subagent_delegation: SubagentDelegation | None = None`
- 当 `_subagent_delegation is not None` 时走 α 路径：
  - 处理 PROJECT_SHARED（与 main/worker 相同逻辑）
  - 从 `_subagent_delegation.caller_memory_namespace_ids` 读取 namespace IDs
  - `get_memory_namespace(ns_id)` 获取已有 namespace 对象，直接 append
  - **不调用** `save_memory_namespace`（不创建新 AGENT_PRIVATE row）
- 测试覆盖：`test_ensure_memory_namespaces_subagent_alpha_shared`（断言 subagent 获得 caller namespace ID，且 store 中无新建 AGENT_PRIVATE row）

### AC-F2（spawn 时填充 caller_memory_namespace_ids）

**实施位置**：`task_runner.py` `_emit_subagent_delegation_init_if_needed` 函数
- 在构造 `SubagentDelegation` 之前，若 `caller_runtime != "<unknown>"` 则：
  - 调用 `self._stores.agent_context_store.list_memory_namespaces(agent_runtime_id=caller_runtime, kind=MemoryNamespaceKind.AGENT_PRIVATE)`
  - 提取 `namespace_id` 列表填入 `caller_memory_namespace_ids`
  - 查询失败 log warn，不阻断 spawn
- `"<unknown>"` caller 时跳过查询（保持 `[]`，避免无效 DB 查询）
- 测试覆盖：`test_spawn_fills_caller_memory_namespace_ids`（端到端验证 delegation 写入 namespace ID）、`test_spawn_caller_without_namespace_gets_empty_ids`、`test_spawn_unknown_caller_skips_namespace_query`

### AC-F3（α 语义端到端：namespace ID 一致性）

**测试覆盖**：`test_subagent_memory_namespace_ids_match_caller`
- 创建 caller AGENT_PRIVATE namespace（namespace_id=`_CALLER_NS_ID`，agent_runtime_id=`_CALLER_RUNTIME_ID`）
- 构造含 `caller_memory_namespace_ids=[_CALLER_NS_ID]` 的 delegation
- 调用 `_ensure_memory_namespaces`（传入 delegation）
- 断言返回的 AGENT_PRIVATE namespace：
  - `namespace_id == _CALLER_NS_ID`（ID 一致）
  - `agent_runtime_id == _CALLER_RUNTIME_ID`（归属 caller runtime）

---

## §4 α 语义实现细节

### 调用方式（TF.2 注入点）

在 `_resolve_context_bundle`（L1402 调用 `_ensure_memory_namespaces` 之前）新增：

```python
# 若当前是 SUBAGENT_INTERNAL，读取 SubagentDelegation 以传入 α 共享路径
_subagent_delegation_for_memory: SubagentDelegation | None = None
if _target_kind_for_profile == "subagent":
    try:
        _task_events = await self._stores.event_store.get_events_for_task(task.task_id)
        _control = merge_control_metadata(_task_events)
        _raw_del_mem = _control.get("subagent_delegation")
        if _raw_del_mem:
            _subagent_delegation_for_memory = SubagentDelegation.model_validate(...)
    except Exception as _mem_del_exc:
        log.warning("subagent_delegation_memory_lookup_failed", ...)
```

`_target_kind_for_profile` 是 `_resolve_context_bundle` 中已有的局部变量（Phase C 引入），复用不增加额外 DB 查询。

### namespace 共享方式

α 语义：Subagent 不创建自己的 AGENT_PRIVATE namespace row，而是调用 `get_memory_namespace(caller_ns_id)` 拿到 caller 已有的 namespace 对象，直接 append 到 `namespaces` 列表。这意味着：
- Subagent 读 memory 时能看到 caller namespace 中的 facts
- Subagent 写 memory 时写入的也是 caller namespace（同一 namespace_id），caller 可读

### 降级行为

| 场景 | 行为 |
|------|------|
| `caller_memory_namespace_ids = []` | subagent 不获得 AGENT_PRIVATE namespace，log warn，不报错 |
| `caller_agent_runtime_id = "<unknown>"` | TF.1 跳过 DB 查询，`caller_memory_namespace_ids = []` |
| delegation 读取失败 | `_subagent_delegation_for_memory = None`，走正常创建路径（fallback）|
| caller namespace 在 DB 中不存在 | `get_memory_namespace` 返回 None，静默跳过 |

---

## §5 Regression 防护（AC-F2 最关键）

**Worker 路径**（`_subagent_delegation=None`）：
- `_ensure_memory_namespaces` 的 `if _subagent_delegation is not None` 块不触发
- 直接走原有代码（PROJECT_SHARED + AGENT_PRIVATE 创建）
- 测试：`test_ensure_memory_namespaces_worker_creates_own_namespace`（断言 worker 创建独立 namespace）

**main 路径**：
- 同 worker，`_subagent_delegation=None`，行为不变
- 测试：`test_ensure_memory_namespaces_main_creates_own_namespace`

---

## §6 验证结果

### Layer 1: 工具链验证

**Phase F 新测试**:
- 命令：`pytest -p no:rerunfailures octoagent/apps/gateway/tests/services/test_agent_context_phase_f.py -v`
- 退出码：0
- 输出：8 passed in 2.07s

**memory / agent_context 回归**:
- 命令：`pytest -p no:rerunfailures octoagent/apps/gateway/tests/ -k "memory or agent_context" -q`
- 退出码：0
- 输出：120 passed, 1266 deselected

**全量回归**:
- 命令：`pytest -p no:rerunfailures octoagent/ -q --tb=short -m "not e2e_full and not e2e_smoke"`
- 退出码：0
- 输出：3344 passed（vs Phase D baseline 3336，净增 +8 新测试）

### Layer 2: 行为验证

AC-F1 happy path：
- `_ensure_memory_namespaces(delegation=delegation_with_caller_ns_id)` → 返回列表含 `_CALLER_NS_ID` → 无新建 AGENT_PRIVATE row
- 验证：`test_ensure_memory_namespaces_subagent_alpha_shared` PASS

AC-F2 happy path：
- `launch_child_task(msg_with_delegation_init + caller_has_AGENT_PRIVATE_ns)` → delegation 写入 → `merge_control_metadata` 读 → `caller_memory_namespace_ids = [_CALLER_NS_ID]`
- 验证：`test_spawn_fills_caller_memory_namespace_ids` PASS

AC-F3 happy path：
- subagent namespace IDs 直接等于 caller AGENT_PRIVATE namespace 的 `namespace_id` 和 `agent_runtime_id`
- 验证：`test_subagent_memory_namespace_ids_match_caller` PASS

### Layer 3: 失败路径验证

- `caller_memory_namespace_ids=[]` → 不创建 namespace，不报错（`test_ensure_memory_namespaces_subagent_empty_caller_ids` PASS）
- `caller_agent_runtime_id="<unknown>"` → 跳过 DB 查询，不报错（`test_spawn_unknown_caller_skips_namespace_query` PASS）
- Worker 路径（delegation=None）→ 独立创建 AGENT_PRIVATE（regression 保护，`test_ensure_memory_namespaces_worker_creates_own_namespace` PASS）

---

## §7 实施偏差记录

| 计划 | 实际 | 影响 |
|------|------|------|
| TF.1 注入点：`delegation_plane.py` 或 `capability_pack.py` | 实际注入点：`task_runner.py:_emit_subagent_delegation_init_if_needed`（Phase B 实际实施后发现该函数是 SubagentDelegation 构造点）| 无影响，逻辑等价 |
| TF.2 信号：`agent_runtime.delegation_mode`（字段不存在）| 实际信号：`_target_kind_for_profile == "subagent"`（Phase C 已有局部变量，复用）| 无影响，比 phase-0-recon 建议的 task events 多跳方案更直接 |
| TF.3/TF.4 文件路径：`tests/test_subagent_memory_sharing.py`（tasks.md 原始描述）| 实际路径：`tests/services/test_agent_context_phase_f.py`（按任务提示 §TF.4 约定）| 无影响 |
| fallback 走正常创建路径（spec 描述）| 实际 fallback：`_subagent_delegation_for_memory=None`（delegation 读取失败），走正常创建路径；`_subagent_delegation` 传 None 时直接走原 else 分支 | 与 spec 等价 |

---

## §8 推迟项

无。Phase F 范围内所有 AC（AC-F1/F2/F3）已全部实现并测试通过。
