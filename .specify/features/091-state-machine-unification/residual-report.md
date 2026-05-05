# F091 残留扫描报告（Phase 4）

生成时间：2026-05-06
基线 commit：`fd70703` (master HEAD = F090 Phase 4)
F091 commit 链：
```
d1af23c refactor(F091-Phase-D): F090 Phase 4 medium finding 闭环
3353926 refactor(F091-Phase-C): metadata 读取端切换 runtime_context（D1 收尾）
55a661d refactor(F091-Phase-A): 加跨枚举状态映射函数（D3 主责）
b2b4d8b refactor(F091-Phase-B): 删除 F090 Phase 1 漏做的 Butler migration 死代码
```

## 1. Phase B 残留：butler migration

### 旧 production 引用（应为 0）

| grep | production 命中 | 状态 |
|------|---------------|------|
| `_migrate_butler_naming\|_migrate_butler_suffix` | 0 (生产)；2 (docstring 历史背景) | ✅ |
| `Butler Direct\|Butler Inline` | 0 (octoagent + docs/design/octoagent-architecture.md) | ✅ |

docstring 命中（保留作为历史追踪，符合 LOW #1 finding 接受方案）：
- `octoagent/packages/core/src/octoagent/core/models/agent_context.py:91`：normalize_runtime_role docstring 提到 "F091 Phase B 起删除启动 migration（_migrate_butler_naming）"
- `octoagent/packages/core/src/octoagent/core/models/agent_context.py:110`：normalize_session_kind docstring 同上

### 数据库残留

`~/.octoagent/data/sqlite/octoagent.db` 三表 0 残留（Phase B 前置检查）：
- agent_runtimes WHERE role='butler': 0
- agent_sessions WHERE kind LIKE 'butler%': 0
- memory_namespaces WHERE kind LIKE 'butler%': 0
- agent_profiles / agent_runtimes WHERE name LIKE '% Butler': 0

`~/.octoagent-master/data/`、`~/.octoagent-agent/data/` 不存在（用户单实例）。

**结论**：Phase B 残留扫描通过。所有 production 字面引用清除，docstring 保留历史追踪是接受方案。

---

## 2. Phase A 映射函数完整性

### 4 个映射 dict 完整性（runtime 验证）

| 映射 dict | 期望 keys 数 | 实际 keys 数 | 漏值 set | 状态 |
|----------|------------|------------|---------|------|
| `TASK_TO_WORK_STATUS` | 10 (TaskStatus 全枚举) | 10 | `set()` | ✅ |
| `WORK_TO_TASK_STATUS` | 10 (WorkStatus 13 - 3 ctx) | 10 | `set()` | ✅ |
| `WORKER_TO_WORK_STATUS` | 6 (WorkerRuntimeState 全) | 6 | `set()` | ✅ |
| `WORKER_TO_TASK_STATUS` | 6 (WorkerRuntimeState 全) | 6 | `set()` | ✅ |

### `WORK_STATUSES_REQUIRING_CONTEXT` 显式 raise 列表

```python
WORK_STATUSES_REQUIRING_CONTEXT = frozenset({
    WorkStatus.MERGED,      # 可从 CREATED/ASSIGNED/RUNNING 任意进入
    WorkStatus.ESCALATED,   # 可 retry 终态，丢失 retry 语义
    WorkStatus.DELETED,     # 可从任意终态进入
})
```

`work_status_to_task_status()` 对这 3 个状态显式 raise ValueError —— 测试覆盖 `TestUnsafeMappingRaisesValueError` 全 PASS。

### 直接路径与组合路径一致性

`worker_state_to_task_status(s) == work_status_to_task_status(worker_state_to_work_status(s))` 对全部 6 个 WorkerRuntimeState 值——参数化测试 `TestDirectVsComposedPathConsistency.test_all_worker_states_consistent_paths` 全 PASS。

**结论**：Phase A 映射完整性零漏值，多对一行为有 raise 防护。

---

## 3. Phase C 残留：metadata 读取端切换

### 应清除的 production reader（除显式 fallback）

| grep 模式 | production 命中（剔除测试 / fallback / docstring）| 状态 |
|----------|---------------------------------------------|------|
| `self._metadata_flag(.*single_loop_executor)` reader 调用 | 0（4 处已切换到 helper） | ✅ |
| `getattr(.*supports_single_loop_executor)` 删除 | **2 处保留**（已记录回退） | ⚠️ |
| `metadata_flag(metadata, "single_loop_executor")` fallback | 1 处（runtime_control.py:93 helper 内 fallback） | ✅ 预期 |

保留的 `getattr supports_single_loop_executor`（**Phase C 已记录回退** + Codex review 认同）：
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:899`（`_is_single_loop_main_eligible`）
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1431`（`_should_direct_execute`）

理由：duck-typed mock（SlowLLMService / CancellableLLMService）不继承 LLMService，通过缺少属性表明"不支持 single_loop"——getattr 兜底返回 False 让测试走非 single_loop 路径。F100 评估能否真正移除（需先升级 fake mock）。

### 写入端保留（F100 删）

按 prompt "不删 metadata 写入"约束，以下 metadata flag 写入保留：
- `octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:805-806`：`updated_metadata["single_loop_executor"] = True`，`updated_metadata["single_loop_executor_mode"] = ...`

**结论**：Phase C 切换的 4 处 production reader 全部用 `is_single_loop_main_active` / `is_recall_planner_skip` helper；保留点都在 commit message + 此报告显式归档。

---

## 4. Phase D 残留：F090 medium finding 闭环

### medium #1：`_prepare_single_loop_request` short-circuit 路径

修复点：[orchestrator.py:763-779](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:763) — short-circuit return 前显式检查 `runtime_context.delegation_mode == "main_inline"`，若不是先 patch。

### medium #2：DelegationPlane 标准 delegation 路径

修复点：
- [delegation_plane.py:927-941](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:927)：新增 `_delegation_mode_for_target_kind` helper
- [delegation_plane.py:838](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:838)：`_build_runtime_context` 加必填 `delegation_mode` 参数
- [delegation_plane.py:164](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:164)：`prepare_dispatch` 唯一调用方按 `initial_target_kind` 推断
- [delegation_plane.py:278](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:278)：pipeline 解析后用 `final_target_kind` 重写 `delegation_mode` + `turn_executor_kind`（Codex MEDIUM 闭环）

### `delegation_mode` 写入点全览

| 位置 | 写入值 | 路径 |
|------|--------|------|
| orchestrator.py:776 | `"main_inline"` | `_prepare_single_loop_request` short-circuit patch（medium #1）|
| orchestrator.py:845 | `"main_inline"` | `_prepare_single_loop_request` 完整路径（F090 已写）|
| delegation_plane.py:164 | `_delegation_mode_for_target_kind(initial_target_kind)` | `prepare_dispatch` 初始 runtime_context |
| delegation_plane.py:278 | `_delegation_mode_for_target_kind(final_target_kind)` | pipeline 解析后重写（Codex MEDIUM 闭环）|
| delegation_plane.py:887 | 透传参数 | `_build_runtime_context` 内构造 |
| orchestrator.py:891 | 透传参数 | `_with_delegation_mode` 内 model_copy |

**未写入路径（F100 / F092 / F098 范围）**：
- `worker_inline`：worker_runtime.py 不构造新 RuntimeControlContext，沿用 envelope 透传；`worker_inline` DelegationMode 当前是占位枚举值，无生产写入点（Codex LOW #1 接受 → 推迟）

---

## 5. 全局 grep 总览

| 命中模式 | 命中数 | 全部位置 | 是否预期 |
|---------|--------|---------|---------|
| `_migrate_butler` 函数引用 | 0 production / 2 docstring | agent_context.py 的两处 docstring 历史追踪 | ✅ |
| `Butler Direct` term | 0 | (清除) | ✅ |
| `_metadata_flag.*single_loop_executor` reader | 0 production | (替换为 helper) | ✅ |
| `metadata_flag.*single_loop_executor` fallback | 1 | runtime_control.py:93（helper 内 fallback；F100 删）| ✅ 预期 |
| `getattr.*supports_single_loop_executor` | 2 | orchestrator.py:899 / 1431（duck-typed mock 区分）| ✅ 已记录 |
| `delegation_mode == "unspecified"` 检查 | 2 | runtime_control.py:91 / orchestrator.py 注释 | ✅ helper 内部 |
| `delegation_mode=` 写入 | 6 | 主路径 4 + helper 透传 2 | ✅ 完整 |

---

## 6. 待 F100 / F092 / F098 收口项

| 项 | 范围 | 收口理由 |
|----|------|---------|
| 删除 metadata flag 写入端 (orchestrator.py:805-806) | F100 | F091 不删写入端（按 prompt 约束）|
| 删除 metadata flag fallback (runtime_control.py:93) | F100 | helper 内 fallback 必须等 F100 收口 |
| 实施 RecallPlannerMode "auto" 实际语义 | F100 | F091 raise NotImplementedError，禁止隐式 fallback |
| 评估 supports_single_loop_executor 类属性能否真正移除 | F100 | 需先升级 SlowLLMService / CancellableLLMService 等 fake mock |
| worker_inline delegation_mode 写入路径 | F100 / F098 | worker_runtime 自跑路径若有需补；F098 A2A Mode + Worker↔Worker 评估 |
| split-brain 风险（LLMService.call() 接 explicit runtime_context）| F100 | F100 删 metadata flag 前必须修；F091 范围内 F090 双轨 + delegation_plane.py L398/L770 已写 runtime_context_json，无实际触发 |
| `_with_delegation_mode` (orchestrator) + `_build_runtime_context` (delegation_plane) 合并到单一入口 | F092 | DelegationPlane Unification |

---

## 7. 残留扫描结论

✅ **Phase B 字面残留**：0 production 命中
✅ **Phase A 映射完整性**：4 个 dict 全覆盖 + `WORK_STATUSES_REQUIRING_CONTEXT` raise 防护 + 直接 vs 组合路径一致性
✅ **Phase C 读取端切换**：4 处 production reader 全替换；保留点（getattr / metadata fallback）已 commit message + 本报告显式归档
✅ **Phase D medium finding 闭环**：medium #1 + #2 修复；按 final target_kind 重写（Codex MEDIUM 闭环）
✅ **全局 grep**：所有命中均已分类（清除 / 预期保留 / F100-F098 收口）
✅ **测试覆盖**：53 + 28 + 7 = 88 unit/integration test 全 PASS

无需用户介入。Phase 5 最终验证可继续。
