# F091 State Machine Unification + F090 残留收尾 — 影响分析报告

生成时间：2026-05-06
基线 commit：`fd70703` (master HEAD = F090 Phase 4)
worktree：`.claude/worktrees/F091-state-machine-unification`
分支：`feature/091-state-machine-unification`

---

## 0. 输入校正（用户 prompt vs 实际代码）

| 用户描述 | 实际代码状况 | 校正动作 |
|---------|------------|---------|
| 4 个枚举重叠（含 `WorkerExecutionStatus` 3 值） | **`WorkerExecutionStatus` 实际不存在**（grep 全 octoagent 0 命中）。当前只有 3 个枚举 + `TurnExecutorKind`（runtime 视角执行者类型）相关联 | 块 A 范围调整为 3 枚举（`TaskStatus` / `WorkerRuntimeState` / `WorkStatus`）+ 映射函数 |
| 共享 `TERMINAL_STATUSES = {SUCCEEDED, FAILED, CANCELLED, DELETED, MERGED}` | 该集合不能直接共享：`TaskStatus` 没有 DELETED/MERGED 但有 REJECTED；`WorkerRuntimeState` 没有 DELETED/MERGED/REJECTED 但有 TIMED_OUT；`WorkStatus` 终态 7 值 | 改为：保留各枚举自己的终态集合就近定义（已存在），引入 `*_to_*` **映射函数** 让 round-trip 安全；**不强制单一全局集合**（语义不同会破坏现有 SSE/migration 路径） |
| 22 处 `metadata.get("single_loop_executor")` 读取需切换 | grep 实际生产 reader 仅 **6 处真实条件判断 + 2 处 getattr 类属性读** = **8 处**；其余 12 处是参数 propagation / 局部变量 / docstring / 模型层注释 | 块 C 范围精确为 8 处生产改造（详见 §3） |
| `_metadata_flag` helper 在 task_service L1022-1026 是 generic helper，保留 | 实际 helper 在 3 处定义（task_service L1022 / llm_service L997 / orchestrator L1007），都是生产 reader 端共用 | 保留所有 `_metadata_flag` helper（generic 用途），仅改调用方传 key 时的语义 |
| Phase 4 medium 1：pack_revision 不变路径不补 runtime_context | 实测 `_prepare_single_loop_request` L761-762 早期 short-circuit return 时未走 `_with_delegation_mode`，runtime_context 保持上轮值 | 块 D 修复：要么 short-circuit 前先 patch runtime_context；要么块 C 把读取从 metadata 切到 runtime_context 后该 finding 自动失效（推荐路径）|
| Phase 4 medium 2：DelegationPlane 标准 delegation 路径不写 delegation_mode | 实测 `_build_runtime_context` L838 构造 RuntimeControlContext 时未传 `delegation_mode`（默认 "unspecified"），与 `_prepare_single_loop_request` 写 "main_inline" 不对称 | 块 D 修复：根据 `target_kind` 推断 delegation_mode 并写入 |

---

## 1. 块 A：状态枚举统一（D3 主责）

### 1.1 现存枚举

| 枚举 | 文件 | 行 | 值数 | 终态集合常量 | 终态值 |
|------|------|----|------|------------|--------|
| `TaskStatus` | [enums.py](octoagent/packages/core/src/octoagent/core/models/enums.py:10) | 10 | 10 | `TERMINAL_STATES` (L63) | SUCCEEDED, FAILED, CANCELLED, REJECTED |
| `WorkerRuntimeState` | [orchestrator.py (models)](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:14) | 14 | 6 | （无显式集合）| 隐含: SUCCEEDED, FAILED, CANCELLED, TIMED_OUT |
| `WorkStatus` | [delegation.py](octoagent/packages/core/src/octoagent/core/models/delegation.py:28) | 28 | 13 | `WORK_TERMINAL_STATUSES` (L86) | SUCCEEDED, FAILED, CANCELLED, MERGED, ESCALATED, TIMED_OUT, DELETED |
| `PipelineRunStatus` | （已有）| - | - | `_TERMINAL_STATUSES` (pipeline_tool.py:55) | SUCCEEDED, FAILED, CANCELLED |

### 1.2 已有映射 pattern（F091 应沿用）

[pipeline_tool.py:68-78](octoagent/packages/skills/src/octoagent/skills/pipeline_tool.py:68) 已有 module-level dict 映射：
```python
_PIPELINE_TO_TASK_STATUS = {
    PipelineRunStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
    PipelineRunStatus.FAILED: TaskStatus.FAILED,
    PipelineRunStatus.CANCELLED: TaskStatus.CANCELLED,
}
_PIPELINE_TO_WORK_STATUS = {...}
```

[delegation_plane.py:1132](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:1132) `_work_status_from_pipeline()` 实例方法做 PipelineRunStatus → WorkStatus 映射。

**结论**：F091 块 A 的映射函数应沿用相同 module-level dict pattern，放在 `delegation.py`（WorkStatus 所在文件）。

### 1.3 终态集合分布（"散落"实测）

| 集合 | 位置 | 类型 | 用途 |
|------|------|------|------|
| `TERMINAL_STATES` | [enums.py:63](octoagent/packages/core/src/octoagent/core/models/enums.py:63) | `set[TaskStatus]` | TaskStatus 终态 |
| `WORK_TERMINAL_STATUSES` | [delegation.py:86](octoagent/packages/core/src/octoagent/core/models/delegation.py:86) | `frozenset[WorkStatus]` | WorkStatus 终态 |
| `_TERMINAL_STATUSES` (pipeline) | [pipeline_tool.py:55](octoagent/packages/skills/src/octoagent/skills/pipeline_tool.py:55) | `frozenset[PipelineRunStatus]` | PipelineRunStatus 终态 |
| `_TERMINAL_STATUSES` (e2e #1) | [test_e2e_smoke_real_llm.py:47](octoagent/apps/gateway/tests/e2e_live/test_e2e_smoke_real_llm.py:47) | `frozenset[str]` | SSE event 字符串兼容（含 lowercase + alias `completed`/`canceled`）|
| `_TERMINAL_STATUSES` (e2e #2) | [test_e2e_delegation_a2a.py:26](octoagent/apps/gateway/tests/e2e_live/test_e2e_delegation_a2a.py:26) | `frozenset[str]` | 同上 |
| `_TERMINAL_STATUSES` (e2e #3) | [test_e2e_mcp_skill_pipeline.py:29](octoagent/apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py:29) | `frozenset[str]` | 同上 |
| `_TERMINAL_STATUSES` (e2e #4) | [test_e2e_memory_pipeline.py:26](octoagent/apps/gateway/tests/e2e_live/test_e2e_memory_pipeline.py:26) | `frozenset[str]` | 同上 |

**关键观察**：4 个 e2e_live `_TERMINAL_STATUSES` 是 string set（含 `"completed"`、`"canceled"` 等 SSE 别名），**不可** 改用枚举集合——会破坏 SSE 兼容层。**F091 不收口这 4 处**。

### 1.4 跨枚举映射方案（拍板：A 路径）

放置于 `octoagent/packages/core/src/octoagent/core/models/delegation.py`（WorkStatus 邻接，方便 unit test 一处测）：

```python
# task_status_to_work_status: TaskStatus → WorkStatus（task 视角抬升到 work）
TASK_TO_WORK_STATUS: dict[TaskStatus, WorkStatus] = {
    TaskStatus.CREATED: WorkStatus.CREATED,
    TaskStatus.QUEUED: WorkStatus.ASSIGNED,
    TaskStatus.RUNNING: WorkStatus.RUNNING,
    TaskStatus.WAITING_INPUT: WorkStatus.WAITING_INPUT,
    TaskStatus.WAITING_APPROVAL: WorkStatus.WAITING_APPROVAL,
    TaskStatus.PAUSED: WorkStatus.PAUSED,
    TaskStatus.SUCCEEDED: WorkStatus.SUCCEEDED,
    TaskStatus.FAILED: WorkStatus.FAILED,
    TaskStatus.CANCELLED: WorkStatus.CANCELLED,
    TaskStatus.REJECTED: WorkStatus.FAILED,  # task rejection → work failed
}

def task_status_to_work_status(status: TaskStatus) -> WorkStatus: ...

# work_status_to_task_status: WorkStatus → TaskStatus（work 视角降到 task；多对一）
WORK_TO_TASK_STATUS: dict[WorkStatus, TaskStatus] = {
    WorkStatus.CREATED: TaskStatus.CREATED,
    WorkStatus.ASSIGNED: TaskStatus.QUEUED,
    WorkStatus.RUNNING: TaskStatus.RUNNING,
    WorkStatus.WAITING_INPUT: TaskStatus.WAITING_INPUT,
    WorkStatus.WAITING_APPROVAL: TaskStatus.WAITING_APPROVAL,
    WorkStatus.PAUSED: TaskStatus.PAUSED,
    WorkStatus.SUCCEEDED: TaskStatus.SUCCEEDED,
    WorkStatus.FAILED: TaskStatus.FAILED,
    WorkStatus.CANCELLED: TaskStatus.CANCELLED,
    WorkStatus.MERGED: TaskStatus.SUCCEEDED,    # work merged 视为 task succeeded
    WorkStatus.ESCALATED: TaskStatus.FAILED,    # work escalated 视为 task failed（升级语义）
    WorkStatus.TIMED_OUT: TaskStatus.FAILED,    # work timeout → task failed
    WorkStatus.DELETED: TaskStatus.CANCELLED,   # work deleted → task cancelled（清理语义）
}

# worker_state_to_work_status: WorkerRuntimeState → WorkStatus
WORKER_TO_WORK_STATUS: dict[WorkerRuntimeState, WorkStatus] = {
    WorkerRuntimeState.PENDING: WorkStatus.ASSIGNED,
    WorkerRuntimeState.RUNNING: WorkStatus.RUNNING,
    WorkerRuntimeState.SUCCEEDED: WorkStatus.SUCCEEDED,
    WorkerRuntimeState.FAILED: WorkStatus.FAILED,
    WorkerRuntimeState.CANCELLED: WorkStatus.CANCELLED,
    WorkerRuntimeState.TIMED_OUT: WorkStatus.TIMED_OUT,
}

# worker_state_to_task_status: WorkerRuntimeState → TaskStatus（compose）
WORKER_TO_TASK_STATUS: dict[WorkerRuntimeState, TaskStatus] = {
    WorkerRuntimeState.PENDING: TaskStatus.RUNNING,
    WorkerRuntimeState.RUNNING: TaskStatus.RUNNING,
    WorkerRuntimeState.SUCCEEDED: TaskStatus.SUCCEEDED,
    WorkerRuntimeState.FAILED: TaskStatus.FAILED,
    WorkerRuntimeState.CANCELLED: TaskStatus.CANCELLED,
    WorkerRuntimeState.TIMED_OUT: TaskStatus.FAILED,
}
```

**测试**：unit test 覆盖每个映射 dict 的 round-trip 完整性 + 无遗漏值（`assert set(_*MAP.keys()) == set(SourceEnum)`）。

### 1.5 不在块 A 范围

- ❌ 不重写 4 个 e2e_live `_TERMINAL_STATUSES`（SSE 字符串兼容层，行为不变）
- ❌ 不删除现有 `TERMINAL_STATES` / `WORK_TERMINAL_STATUSES`（保留作为各枚举本地终态集合）
- ❌ 不改 `WorkerRuntimeState` 加显式终态集合（worker_runtime.py 9 处状态赋值都是显式终态值，无歧义）
- ❌ 不动 `validate_transition` / `validate_work_transition`（流转规则与映射独立）

### 1.6 影响文件

| 文件 | 改造 |
|------|------|
| [delegation.py](octoagent/packages/core/src/octoagent/core/models/delegation.py) | 加 4 个映射 dict + 4 个映射函数（约 80 行新增）|
| [models/__init__.py](octoagent/packages/core/src/octoagent/core/models/__init__.py) | export 新映射函数（约 10 行新增）|
| [tests/test_state_machine_mappings_f091.py](octoagent/packages/core/tests/) | **新建** unit test（约 100 行）|

---

## 2. 块 B：Butler migration 死函数清理（F090 Phase 1 漏做项）

### 2.1 前置检查（DB 残留扫描）

实测命令：
```bash
ls ~/.octoagent ~/.octoagent-master ~/.octoagent-agent
# 实测：仅 ~/.octoagent 存在；~/.octoagent-master 与 ~/.octoagent-agent 不存在（用户当前单实例）
```

实例 `~/.octoagent/data/sqlite/octoagent.db` butler 残留：
| 表 | 残留计数 |
|----|---------|
| `agent_runtimes` WHERE role='butler' | **0** |
| `agent_sessions` WHERE kind LIKE 'butler%' | **0** |
| `memory_namespaces` WHERE kind LIKE 'butler%' | **0** |

**结论**：可安全删除 migration 函数体。其余两实例不存在，无需迁移。

### 2.2 删除目标

| 类型 | 位置 | 行 | 内容 |
|------|------|----|------|
| 函数体 | [startup_bootstrap.py](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:329) | 329-334 | `async def _migrate_butler_naming(conn): ...` |
| 函数体 | [startup_bootstrap.py](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:337) | 337-369 | `async def _migrate_butler_suffix(store_group, agent_profile): ...` |
| 调用点 | [startup_bootstrap.py:62](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:62) | 62 | `await _migrate_butler_suffix(store_group, agent_profile)` + 上方注释 |
| 调用点 | [startup_bootstrap.py:65](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:65) | 65 | `await _migrate_butler_naming(store_group.conn)` + 上方注释 |
| docstring 引用 | [agent_context.py:88](octoagent/packages/core/src/octoagent/core/models/agent_context.py:88) | 88 | normalize_runtime_role docstring 提到 `_migrate_butler_naming` |
| docstring 引用 | [agent_context.py:102](octoagent/packages/core/src/octoagent/core/models/agent_context.py:102) | 102 | normalize_session_kind docstring 提到 `_migrate_butler_naming` |
| 设计文档术语 | [docs/design/octoagent-architecture.md:37](docs/design/octoagent-architecture.md:37) | 37 | `Butler Direct` 在 ASCII 图 |
| 设计文档术语 | [docs/design/octoagent-architecture.md:186](docs/design/octoagent-architecture.md:186) | 186 | `**分支 A：Butler Direct Execution（最常见路径）**` |

### 2.3 保留（数据防御层）

- `normalize_runtime_role()` @ [agent_context.py:80](octoagent/packages/core/src/octoagent/core/models/agent_context.py:80) — 保留但更新 docstring（去掉 `_migrate_butler_naming` 提及）
- `normalize_session_kind()` @ [agent_context.py:94](octoagent/packages/core/src/octoagent/core/models/agent_context.py:94) — 同上
- `test_migration_063.py` fixture（migration 测试本体）— 不动

### 2.4 影响文件

| 文件 | 改造 |
|------|------|
| [startup_bootstrap.py](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py) | 删 ~50 行（函数体 + 调用点 + 注释）|
| [agent_context.py](octoagent/packages/core/src/octoagent/core/models/agent_context.py) | 改 2 个 docstring（约 4 行）|
| [docs/design/octoagent-architecture.md](docs/design/octoagent-architecture.md) | 改 2 处 "Butler Direct" → "Main Direct"（约 2 行）|

---

## 3. 块 C：metadata 读取端切换 runtime_context（F090 D1 收尾）

### 3.1 实际生产 reader（grep 实测精确分布）

| 文件 | 行 | 类型 | 改造目标 |
|------|----|------|---------|
| [llm_service.py:218](octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py:218) | 类属性 | `supports_single_loop_executor = True` | 删除该属性（已恒为 True，等同常量行为）|
| [llm_service.py:375](octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py:375) | 局部变量 | `single_loop_executor = self._metadata_flag(metadata, "single_loop_executor")` | 改读 `runtime_context.delegation_mode in ("main_inline", "worker_inline")`，metadata fallback |
| [task_service.py:1044](octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:1044) | 条件判断 | `if self._metadata_flag(dispatch_metadata, "single_loop_executor"): return None` | 改读 `runtime_context.recall_planner_mode == "skip"`，metadata fallback |
| [orchestrator.py:761](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:761) | 条件判断 | `if self._metadata_flag(metadata, "single_loop_executor") and pack_rev == stored_rev: return request` | 改读 `runtime_context.delegation_mode == "main_inline"`，metadata fallback |
| [orchestrator.py:877](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:877) | 条件判断 | `if not bool(getattr(self._llm_service, "supports_single_loop_executor", False))` | 删 getattr 兜底 → 删整个 guard（属性永远 True）|
| [orchestrator.py:1017](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1017) | 条件判断 | `if self._metadata_flag(request.metadata, "single_loop_executor"): return None, {}` | 改读 `runtime_context.delegation_mode in ("main_inline", "worker_inline")`，metadata fallback |
| [orchestrator.py:1402](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1402) | 条件判断 | `if not bool(getattr(self._llm_service, "supports_single_loop_executor", False))` | 删 getattr 兜底 → 删整个 guard |
| [orchestrator.py:1399](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:1399) | docstring | `1. LLMService 支持 tool calling（supports_single_loop_executor）` | docstring 改写明 "已恒为 True" |

### 3.2 不改造的 12 处（参数 propagation / 局部变量 / 写入端）

- llm_service.py L379, L423, L912, L919, L921, L955, L984 — 都是上游传入 single_loop_executor 局部变量（在 L375 决议后透传），不需要改读取
- orchestrator.py L788 (route_reason 拼接), L802-803 (metadata 写入) — F091 不动写入端（F100 删）
- 模型层 orchestrator.py L33-50 注释 + L90 字段 description — 已是 F090 改造结果，不动

### 3.3 metadata fallback 策略（F091 vs F100）

按用户 prompt 关键约束：**F091 范围内读取端"runtime_context 优先 + metadata fallback"**：

```python
# 通用读取 helper（建议放 task_service / runtime_control 模块）
def _is_single_loop_main(
    runtime_context: RuntimeControlContext | None,
    metadata: dict[str, Any] | None,
) -> bool:
    if runtime_context is not None:
        if runtime_context.delegation_mode in ("main_inline", "worker_inline"):
            return True
        if runtime_context.delegation_mode != "unspecified":
            return False
    # fallback: F091 兼容期（F100 删除）
    return _metadata_flag(metadata or {}, "single_loop_executor")
```

**F100 收口**：
- 删除 fallback 分支
- 删除 metadata 写入端 (orchestrator.py L802-803)
- 实施 RecallPlannerMode "auto" 实际语义

### 3.4 影响文件

| 文件 | 改造 |
|------|------|
| [llm_service.py](octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py) | 删 L218 类属性 + 改 L375 reader（共 ~10 行）|
| [task_service.py](octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py) | 改 L1044 reader（~5 行）|
| [orchestrator.py (services)](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py) | 改 L761, L877, L1017, L1402 reader + L1399 docstring（共 ~20 行）|
| 新增 helper 位置 | [runtime_control.py](octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py)（已有）或 task_service 内 | 加 `_is_single_loop_main` / `_is_recall_planner_skip` 辅助函数（~30 行）|
| 单测 | tests/test_runtime_control_f091.py 新建 | 各 delegation_mode 路径覆盖（~80 行）|

---

## 4. 块 D：F090 Phase 4 medium finding 闭环

### 4.1 medium #1: pack_revision 不变 short-circuit 路径未补 runtime_context 同步

**位置**：[orchestrator.py L755-762](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:755)

```python
async def _prepare_single_loop_request(self, request) -> OrchestratorRequest:
    if not self._is_single_loop_main_eligible(request):
        return request
    metadata = dict(request.metadata)
    pack_rev = ...
    stored_rev = metadata.get("_pack_revision", 0)
    if self._metadata_flag(metadata, "single_loop_executor") and pack_rev == stored_rev:
        return request   # ← short-circuit：未 patch runtime_context
    ...
    # 完整路径走到 L820: _with_delegation_mode(...) 补 runtime_context
```

**修复路径**（与块 C 协同）：
- 块 C 把 reader 改为读 `runtime_context.delegation_mode`，则 short-circuit return 必须保证 runtime_context 已 patch
- 在 L761 条件分支内、return 前先检查 `runtime_context.delegation_mode == "main_inline"`，若不是 → 调用 `_with_delegation_mode` 补 patch 后再 return

### 4.2 medium #2: DelegationPlane 标准 delegation 路径不写 delegation_mode

**位置**：[delegation_plane.py L838-883 `_build_runtime_context`](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:838)

实测：构造 RuntimeControlContext 时未传 `delegation_mode`，默认 `"unspecified"`。导致：
- main_delegate（主 Agent 派给 worker） → 应写 "main_delegate"
- worker_inline（worker 自跑） → 应写 "worker_inline"（worker_runtime 路径）
- subagent → 应写 "subagent"

**修复方案**：
1. `_build_runtime_context` 加参数 `delegation_mode: DelegationMode`
2. 调用方（L147）根据 `target_kind` 推断：
   - `DelegationTargetKind.SUBAGENT` → `"subagent"`
   - `DelegationTargetKind.WORKER` / `ACP_RUNTIME` / `GRAPH_AGENT` / `FALLBACK` → `"main_delegate"`
3. worker_runtime.py 内部调 LLM 时（如有自跑路径）单独写 `"worker_inline"`（核查无此路径则跳过）

### 4.3 影响文件

| 文件 | 改造 |
|------|------|
| [orchestrator.py (services)](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py) | `_prepare_single_loop_request` short-circuit 前补 runtime_context patch（~5 行）|
| [delegation_plane.py](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py) | `_build_runtime_context` 加 delegation_mode 参数 + L147 调用补传（~10 行）|
| 单测 | tests/test_delegation_plane_f091.py 新建或扩展现有 | 验证 standard delegation 写 delegation_mode（~30 行）|

---

## 5. 风险评级与 GATE 决议

### 5.1 风险评级

| 块 | 风险 | 理由 |
|----|------|------|
| 块 A | **低** | 纯加映射函数 + 测试，不改现有调用方；新增非破坏性变化 |
| 块 B | **极低** | 删死代码 + DB 残留 0 命中确认 |
| 块 C | **中** | 读取端切换有大量 callsite，但每处都用 fallback 保兼容；单测覆盖各 path |
| 块 D | **低** | medium 修复属定义层补全（写入更精确），不改读取语义 |

### 5.2 总体超阈值检查

- 影响文件数：< 100 ✓（实测 ~10 production + 4 test）
- 跨包引用：✓（packages/core + apps/gateway，已存在）
- 风险评级：medium（块 C 主导）
- 影响行数：~250 行（含测试）

### 5.3 GATE_TASKS 行为预期

`HAS_GATE_POLICY = true`，gate 行为待 Phase 2 加载实际配置后决议。

---

## 6. F091 与 F092 (DelegationPlane Unification) 接口点

F092 范围（"散在 5+ 处的委托代码统一到 DelegationPlane 单一编排入口"）会进一步重构 delegation_plane.py / orchestrator.py 的 dispatch 路径。

F091 块 D 在 `_build_runtime_context` 加 delegation_mode 参数后，F092 应：
1. 将 `_with_delegation_mode` (orchestrator.py) 与 `_build_runtime_context` (delegation_plane.py) 合并到 DelegationPlane 单一入口
2. 把 `delegation_mode` 推断逻辑从 caller 移到 DelegationPlane 内部决议
3. 验证 single_loop_main / standard delegation / subagent 三路 delegation_mode 写入路径在新 DelegationPlane 内统一

F091 不做这些 — 仅保证 medium finding 关闭，运行时行为零变更。

---

## 7. 不在 F091 范围（F092-F100 后续 Feature）

| 项目 | 推迟到 |
|------|--------|
| 删除 metadata 写入端 (orchestrator.py L802-803) | F100 |
| RecallPlannerMode "auto" 实际语义 | F100 |
| WorkerProfile 完全合并 | F107 |
| 委托代码单一入口（`DelegationPlane`） | F092 |
| Worker 真实运行行为变更 | F093-F096 |
| `agent_context.py` 拆分 | F093 顺手 |
| `orchestrator/task_service` 拆分 | F098 顺手 |
| 4 个 e2e_live `_TERMINAL_STATUSES` 收口 | （不做，SSE 兼容层）|
