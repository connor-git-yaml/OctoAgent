# 重构方案：统一 Butler/Worker 编排，消除过时的 Agent 分类

## 背景

orchestrator.py 沿用了一套 hardcoded 的 Worker 分类体系（`llm_generation`/`ops`/`research`/`dev`），
但实际运行时这 4 类 Agent 行为完全一致——同一个 WorkerRuntime、同一套工具集、同一个 profile。
Feature 065 已经把 `_build_worker_profiles` 收缩为单一 `"general"` profile 并注释说明
"差异化通过 PermissionPreset + Behavior Files 表达"，但散布在各处的分类代码尚未清理。

## 目标

1. **OrchestratorService 只创建 1 个 LLMWorkerAdapter**（而非 4 个）
2. **删除 `ButlerDecisionMode.DELEGATE_RESEARCH`**——天气场景走 `DELEGATE_GRAPH` 或直接 Butler Execution
3. **删除 `DELEGATE_DEV` / `DELEGATE_OPS` 的 fallback 逻辑**——Graph Pipeline 失败统一回退到 Butler Direct Execution
4. **capability_pack.py 工具定义去掉 `worker_types` 字段**——所有 Agent 共享同一工具集
5. **清理分类辅助函数**（`_classify_worker_type`、`_coerce_worker_type_name`、`_target_kind_for_worker_type`、`_worker_profile_label`、`_requires_standard_web_access` 中的 worker_type 参数等）
6. **前端 `WorkerType` 类型简化**

## 分步实施

### Step 1: orchestrator.py — 合并 4 个 Worker 为 1 个

**文件**: `orchestrator.py`

- `__init__` 中 `default_workers` 从 4 个 LLMWorkerAdapter 精简为 1 个：
  ```python
  default_workers = [
      LLMWorkerAdapter(
          store_group, sse_hub, llm_service,
          worker_id="worker.llm.default",
          capability="llm_generation",  # 保留为历史兼容默认值
          ...
      ),
  ]
  ```
- `self._workers` 字典依然保留 `{"llm_generation": adapter}` 格式，但只有一个条目
- `dispatch()` 中 `worker_capability` 参数默认值不变（`"llm_generation"`），保持调用方无感

### Step 2: 消除 DELEGATE_RESEARCH 及 Graph fallback 分类

**文件**: `orchestrator.py`, `butler_behavior.py`, `behavior.py`

**A) ButlerDecisionMode 枚举清理**（`behavior.py`）：
- 删除 `DELEGATE_RESEARCH = "delegate_research"`
- （`DELEGATE_DEV`/`DELEGATE_OPS` 从未进入枚举，但在 _fallback 中作为字符串赋值，一并清理）

**B) butler_behavior.py 天气决策**：
- 目前天气场景返回 `DELEGATE_RESEARCH`，改为返回 `DELEGATE_GRAPH` 或直接走 Butler Direct Execution
- `decide_butler_decision()` 天气分支返回 `DELEGATE_GRAPH` + 天气 pipeline_id
- 没有天气 pipeline 时返回 `DIRECT_ANSWER`（None）→ 自动走 Butler Direct Execution

**C) `_fallback_delegate_graph_decision` 删除**（`orchestrator.py`）：
- Graph Pipeline 启动失败时，不再按 tags 分类为 ops/dev/research
- 直接返回 None → 让调用方走 `_dispatch_butler_direct_execution`

**D) `_resolve_butler_target_worker_type` 删除**（`orchestrator.py`）

### Step 3: capability_pack.py 工具定义去除 worker_types

**文件**: `capability_pack.py`

- 所有工具定义中移除 `worker_types=[...]` 参数
- `BundledToolDefinition` 模型中保留 `worker_types` 字段（兼容序列化），但默认空列表
- `ToolIndex` 匹配时跳过 `worker_types` 空列表（视为"对所有 worker 可用"）——需确认 tool_index.py 已支持
- 删除辅助函数：
  - `_classify_worker_type`
  - `_coerce_worker_type_name`
  - `_target_kind_for_worker_type`
  - `_worker_profile_label`（合并为单一标签或直接使用 profile_name）
  - `_requires_standard_web_access` 中的 `worker_type` 参数
  - `_requires_weather_toolset` 中的 `worker_type` 参数
  - `_effective_tool_profile_for_objective` 中的 `worker_type` 参数

### Step 4: 清理下游引用

**文件**: 多处

- `_build_worker_assignment`：简化，不再调用 `_classify_worker_type`，worker_type 统一为 `"general"`
- `_launch_child_task`：worker_type 参数可保留但传入统一值
- `_build_worker_profiles`：保持现状（已经只返回 general）
- `_builtin_worker_type_from_profile_id`：保持 `"general"` 映射
- `_ResolvedWorkerBinding` 中 `worker_type` 保留但始终为 `"general"`
- bootstrap 模板中 `{{worker_type}}` 占位符保留（显示为 general）

### Step 5: 前端类型简化

**文件**: `frontend/src/types/index.ts`

- `WorkerType` 类型改为 `type WorkerType = "general"` 或直接删除，用 `string` 替代
- 下游使用 WorkerType 的接口字段保留（兼容 API response），但不再有行为差异

### Step 6: 测试更新

- `test_butler_delegate_graph.py`：删除 `DELEGATE_OPS`/`DELEGATE_DEV`/`DELEGATE_RESEARCH` fallback 测试
- `test_butler_behavior.py`：天气测试改为验证 `DELEGATE_GRAPH` 或 `DIRECT_ANSWER`
- `test_orchestrator.py`：确认只有 1 个 worker adapter
- `test_task_runner.py`：`worker_id="worker.llm.dev"` → `"worker.llm.default"`
- `test_worker_runtime.py`：`worker_capability="ops"` → `"llm_generation"`
- `test_delegation_plane.py`：`worker_capability="dev"/"research"` → `"llm_generation"`
- `test_task_service_context_integration.py`：`worker_capability="research"` → `"llm_generation"`

## 不变的部分

- **`ButlerDecisionMode.DELEGATE_GRAPH`**：保留，Graph Pipeline 是明确的编排路径
- **`ButlerDecisionMode.ASK_ONCE` / `BEST_EFFORT_ANSWER`**：保留，这些是 Butler 行为模式
- **`ButlerDecision.target_worker_type`**：字段保留但仅用于 Graph Pipeline 的 pipeline 选择
- **Policy Gate / Approval 链路**：不变
- **Delegation Plane**：不变（已经有独立的 profile-based routing）
- **`OrchestratorRequest.worker_capability`**：字段保留，默认 `"llm_generation"`，用于事件标记

## 风险和注意

1. **天气 freshness 链路** 目前深度依赖 `DELEGATE_RESEARCH` + `butler_owned_freshness`。
   这条链路逻辑复杂（~800 行），需仔细改为 DELEGATE_GRAPH 或 Butler Direct Execution。
   建议先跑通 happy path 再处理 edge case。

2. **历史事件数据** 中存在 `worker_capability="research"` 等值。这些是已落盘的事件，
   不需要迁移——只要代码不再依赖这些分类做运行时决策即可。

3. **`worker_types` 在 ToolIndex 中的匹配逻辑**。需确认空列表的语义是"对所有人可用"。
