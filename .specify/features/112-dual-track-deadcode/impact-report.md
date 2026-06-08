# F112 影响分析报告（impact-report）

> 模式：spec-driver-refactor（大规模重构 5 阶段第 1/5 阶段）
> 重构目标：双轨收口死代码清理 —— F090 D1→F091→F100 metadata fallback 残渣 + WORKER_PRIVATE 枚举
> 原则：**行为零变更**（F090/F091/F100 是纯类型/状态/编排重构，运行时行为 100% 等价）
> baseline：`origin/master` = `543a93b`（= `d2936e0` + 一个 docs commit，测试基线不变）
> 诊断方法：全仓 grep 实证（brief 三处声明均为未对抗验证的 medium，行号已漂移，逐个核实）

---

## 一、Track 1 — metadata fallback 死代码（F091→F100 残渣）

### 1a. module-level `metadata_flag`（`runtime_control.py:62`）—— 纯死代码 ✅ 确认

- 全仓 grep `metadata_flag`（区分于 `_metadata_flag`）：**零生产 caller**。
- 唯一引用：`apps/gateway/tests/test_runtime_control_f091.py`（import line 15 + 断言 line 186/189/192）。
- 该 helper 是 F091 Phase C 想做的"单一来源"，但从未被生产采纳（其 docstring 自承"runtime_control 之外的 generic flag 仍可继续用各服务的 `_metadata_flag`"）。
- **处置**：删除函数 + 删对应单测断言。

### 1b. `is_single_loop_main_active` 的 `metadata` 死形参（`runtime_control.py:76`）✅ 确认

- 函数体不使用 `metadata`：`line 96` 显式注释 `# _ = metadata  # 参数保留以兼容 caller signature`；F100 Phase E2 已移除 metadata fallback，unspecified/None → return False。
- 生产 caller 3 处：
  - `orchestrator.py:771` `is_single_loop_main_active(runtime_context_for_check, metadata)`
  - `orchestrator.py:1081` `is_single_loop_main_active(runtime_context_for_check, request.metadata)`
  - `llm_service.py:383` `is_single_loop_main_active(runtime_context, metadata)`
- **处置**：从签名移除 `metadata` 形参 + 改 3 处 caller + 改单测调用。

### 1c. `is_recall_planner_skip` 的 `metadata` 死形参（`runtime_control.py:100`）✅ 确认

- 函数体不使用 `metadata`：F100 v0.3 unspecified/None → return False（`line 146-148`），不再 fallback `metadata["single_loop_executor"]`。
- 生产 caller 1 处：`task_service.py:1134` `is_recall_planner_skip(runtime_context, dispatch_metadata)`
- **处置**：从签名移除 `metadata` 形参 + 改 1 处 caller + 改单测调用。

### 1d. 额外发现 —— per-service `_metadata_flag` 死方法（**brief 未列举，同族残渣**）

grep `_metadata_flag(`（带括号=调用）全仓仅 1 处调用：`orchestrator.py:922`。三个 def：

| 位置 | 状态 | 处置 |
|------|------|------|
| `orchestrator.py:1067`（def）+ `:922`（call `"force_full_recall"`）| **LIVE**（F101 force_full_recall producer）| 保留 |
| `task_service.py:1111`（def）| **DEAD**（全仓零调用）| 建议删（同族死代码）|
| `llm_service.py:1005`（def）| **DEAD**（全仓零调用）| 建议删（同族死代码）|

> 注：brief 原文只点名"module-level `metadata_flag` 死 helper"。1d 是 grep 顺带证实的同族死代码，是否纳入 F112 范围 → 提请用户决策（见 plan §决策点）。

---

## 二、Track 2 — WORKER_PRIVATE 枚举（F094 残渣）

### 枚举定义
`packages/core/src/octoagent/core/models/agent_context.py:168` → `WORKER_PRIVATE = "worker_private"`

### 写路径已死 ✅ 确认
- active 写路径（`agent_context.py:2857`）用 `private_kind = MemoryNamespaceKind.AGENT_PRIVATE`。
- `build_private_memory_scope_ids` 唯一生产 caller（`agent_context.py:2866`）传的就是 AGENT_PRIVATE；`_deps.py:160` 仅注释。
- F094 注释（`agent_context.py:2848-2856`）已显式记载："baseline 中 WORKER_PRIVATE 路径已废弃——新 dispatch 不再生成 `kind=worker_private` 记录；既有 baseline worker_private namespace records 保留不动……`build_private_memory_scope_ids` 函数本身不动（避免 namespace.memory_scope_ids 字段已有数据破坏）"。

### 读侧守卫 4 处（处理既有 records，**当前仍被需要**）
| 位置 | 作用 |
|------|------|
| `agent_context.py:480-483` | `build_private_memory_scope_ids` 成员检查 `kind in {AGENT_PRIVATE, WORKER_PRIVATE}` |
| `agent_context.py:485` | `owner = "worker" if kind is WORKER_PRIVATE else "main"`（owner 派生，需具体枚举值）|
| `agent_context.py:3791-3794` | namespace 排序 key（private 类优先）|
| `task_service.py:2149-2152` | resolve private namespace 时选 `kind in {AGENT_PRIVATE, WORKER_PRIVATE}` |

### ⚠️ 硬前置：托管实例存量检查（数据相关，单次授权）

查 `~/.octoagent` 两个 SQLite（`.tables` 确认均有 `memory_namespaces` / `memory_sor`）：

| DB | `kind='worker_private'` namespaces | scope_ids `LIKE %private/worker%` | memory_sor worker-private |
|----|----|----|----|
| `data/sqlite/octoagent.db`（**活跃实例**）| **5** | 5 | 0 |
| `app/octoagent/data/sqlite/octoagent.db`（legacy）| 0 | 0 | 0 |

活跃实例 5 条记录创建于 2026-04-06 ~ 2026-04-26（F094 废弃前），scope_id 形态 `memory/private/worker/...`，真实有效（非脏数据）。

**结论：有存量 → WORKER_PRIVATE 枚举必须保留。** 删除枚举值会导致这 5 行在 `MemoryNamespaceKind("worker_private")` 反序列化时 `raise ValueError`，破坏既有数据读取（违反 Constitution #1 Durability）。

> 这与 F091 store 层教训同源：删枚举/迁移后遇 legacy 行 raise（F091 HIGH：`AgentRuntimeRole("butler")` raise）。WORKER_PRIVATE 不能重蹈。

---

## 三、受影响文件清单

**生产（src）**：
- `apps/gateway/src/octoagent/gateway/services/runtime_control.py`（删 `metadata_flag` + 两 helper 删形参）
- `apps/gateway/src/octoagent/gateway/services/orchestrator.py`（2 处 caller）
- `apps/gateway/src/octoagent/gateway/services/llm_service.py`（1 处 caller + 可选删死 `_metadata_flag`）
- `apps/gateway/src/octoagent/gateway/services/task_service.py`（1 处 caller + 可选删死 `_metadata_flag`）
- （WORKER_PRIVATE 守卫收敛若采纳）`agent_context.py` + `task_service.py` + 可能 `core/models/agent_context.py`

**测试（tests）**：
- `test_runtime_control_f091.py`（删 `metadata_flag` 断言 + 改两 helper 调用）
- `test_runtime_control_f100.py`、`test_runtime_control_f100_perf.py`
- `test_ask_back_recall_planner_resume_f100.py`
- `test_chat_force_full_recall.py`
- `services/test_f101_phase_f_acceptance.py`

**WORKER_PRIVATE 相关测试（保留枚举 → 这些应继续通过，不能改坏）**：
- `packages/core/tests/test_agent_context_store.py`（断言 `WORKER_PRIVATE not in kinds`）
- `packages/memory/tests/migrations/test_migration_063.py` / `test_migration_094.py`

---

## 四、风险评级

**低**。理由：
- Track 1 是纯死代码删除 + 死形参删除，F100 已确认 unspecified/None→False 与 baseline 等价。
- Track 2 采保守保留（有存量），仅可选做 DRY 收敛（行为零变更）。
- 无跨包接口签名对外变更（两 helper 是内部 service helper）；WORKER_PRIVATE 枚举不动。
- 影响文件 < 15，远低于 refactor 模式 critical 阈值（100）。

**风险点**：
- 删两 helper 形参会改 helper 签名 → 所有 caller（含测试）必须同步，漏改即 TypeError（编译期可抓）。
- 不能误删 orchestrator 的 LIVE `_metadata_flag`。
- WORKER_PRIVATE 守卫收敛若做，必须保证 `owner` 派生（line 485）语义不变。
