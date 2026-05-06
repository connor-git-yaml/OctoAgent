# F092 DelegationPlane Unification — 影响分析报告

**分支**：`feature/092-delegation-plane-unification`
**基线**：`69e5512`（F091 完成 commit）
**测试基线**：`3170 tests collected`（uv run pytest --collect-only）
**风险评级**：**MEDIUM**
**报告日期**：2026-05-06

---

## 1. 重构目标（一句话）

把当前散在 5+ 个文件的委托代码统一到 `DelegationPlane` **单一编排入口**——
所有委托动作（spawn / dispatch / cancel / merge / kill / steer）都通过 plane 走，
`capability_pack` 只负责权限/工具选择，`harness/DelegationManager` 作底层约束检查器，
`delegation_tools.py` 工具入口只做参数收集。**行为零变更**。

---

## 2. 5 个起点文件职责切片

| 文件 | 行数 | 当前职责 | F092 期望职责 |
|------|------|---------|--------------|
| `apps/gateway/src/octoagent/gateway/services/delegation_plane.py` | 1319 | Work 投影 + dispatch state + pipeline 编排 + runtime_context 构造 | **唯一编排入口**：spawn/dispatch/cancel/merge/kill 全部从此处走 |
| `apps/gateway/src/octoagent/gateway/services/capability_pack.py` | 2079 | 工具选择 + Worker plan 评审 + `_launch_child_task` + `_enforce_child_target_kind_policy`（1244/1282-1300）| 权限/工具选择**专责**；`_enforce_child_target_kind_policy` 不再由 capability_pack 直接调用，改为 plane 在编排路径上调用 |
| `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py` | 484 | 6 工具入口（work.inspect/subagents.spawn/kill/steer/work.merge/work.delete）+ 直接调 `DelegationManager` + `launch_child`（绕过 plane）| 工具入口**只做参数收集**，把派发动作下沉到 plane 的统一 API |
| `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py` | （F085 引入）| 工具入口（delegate_task）+ 直接调 `DelegationManager` + `launch_child` | 同上：下沉到 plane |
| `apps/gateway/src/octoagent/gateway/harness/delegation.py` | 354 | `DelegationManager`（depth ≤ 2 / concurrent ≤ 3 / blacklist gate）+ `_emit_spawned_event` | 底层**约束检查器**，仅由 plane 调用，不再由 builtin_tools 直接 new |
| `packages/core/src/octoagent/core/models/delegation.py` | 326 | `WorkKind` / `WorkStatus`（15 枚举）/ `VALID_WORK_TRANSITIONS` / `DelegationTargetKind` / `Work` / `DelegationEnvelope` / `DelegationResult` + F091 跨枚举映射函数 | **不动**（数据模型层，F092 只调用不改） |

---

## 3. 实测验证：委托相关代码散落点

### 3.1 grep 入口分布

```
grep -rn "_enforce_child_target_kind_policy\|enforce_child_target_kind" production code
→ apps/gateway/.../capability_pack.py:1244 （唯一调用点：_launch_child_task 内）
→ apps/gateway/.../capability_pack.py:1282 （定义点）

grep -rn "DelegationManager(" production code
→ apps/gateway/.../builtin_tools/delegation_tools.py:173 （subagents.spawn 内创建）
→ apps/gateway/.../builtin_tools/delegate_task_tool.py:164 （delegate_task 内创建）
（即 builtin_tools 各自独立 new manager，不通过 plane）

grep -rn "launch_child(" production code
→ apps/gateway/.../builtin_tools/_deps.py:275 （helper 定义）
→ apps/gateway/.../builtin_tools/delegation_tools.py:202 （subagents.spawn 调用）
→ apps/gateway/.../builtin_tools/delegate_task_tool.py:193 （delegate_task 调用）

grep -rn "DelegationPlaneService\|delegation_plane\." production code → 27 处引用
（main.py / octo_harness.py 各 1 处装配；orchestrator.py / capability_pack.py / task_runner.py
为主要服务层调用方；delegation_tools 通过 deps._delegation_plane 间接调）
```

### 3.2 散落 5+ 处的实证清单

| 入口 | 文件:line | 当前直接耦合 |
|------|-----------|--------------|
| 委托动作 1：subagents.spawn | `delegation_tools.py:173` | DelegationManager + capability_pack._launch_child_task（旁路 plane）|
| 委托动作 2：delegate_task | `delegate_task_tool.py:164` | DelegationManager + capability_pack._launch_child_task（旁路 plane）|
| 委托动作 3：subagents.kill | `delegation_tools.py:283-296` | task_runner.cancel_task + plane.cancel_work |
| 委托动作 4：subagents.steer | `delegation_tools.py`（steer 实现）| 操作子 work state |
| 委托动作 5：work.merge | `delegation_tools.py` | plane.merge_work |
| 委托动作 6：work.delete | `delegation_tools.py` | plane.delete_work |
| 编排路径：prepare_dispatch | `delegation_plane.py:148-280` | runtime_context 构造 + pipeline 5 节点 + 自身 mark_dispatched |
| target_kind 校验：`_enforce_child_target_kind_policy` | `capability_pack.py:1244, 1282-1300` | 仅在 capability_pack._launch_child_task 内 |
| 约束检查：`DelegationManager.delegate` | `harness/delegation.py:90-336` | 被 builtin_tools 各自 new 调用 |

**结论**：散落 6 个工具入口 × 2 条 builtin_tools 文件 + 1 处 enforce + 1 处 plane 编排 + 1 处 manager 约束 = **散落 5+ 处属实**。问题在"工具入口直接拼装 manager + capability_pack 内部函数"，绕过了 plane。

---

## 4. 横切调用图（从用户/LLM 视角）

```
LLM tool call
 ├─ subagents.spawn / delegate_task （旁路路径，2 条）
 │    │
 │    ├─ DelegationManager.delegate()                            ← gate（depth/concurrent/blacklist）
 │    │
 │    ├─ launch_child() (helper @ _deps.py:275)
 │    │    │
 │    │    └─ capability_pack._launch_child_task()
 │    │         │
 │    │         ├─ _enforce_child_target_kind_policy()           ← Worker→Worker 禁止
 │    │         │
 │    │         └─ task_runner.launch_child_task()                ← 真实子任务创建
 │    │
 │    └─ DelegationManager._emit_spawned_event()                  ← 写 SUBAGENT_SPAWNED 审计事件
 │
 ├─ subagents.kill                                               ← 走 plane.cancel_work
 ├─ subagents.steer                                              ← 走 plane 状态转换
 ├─ work.merge                                                    ← 走 plane.merge_work
 ├─ work.delete                                                   ← 走 plane.delete_work
 └─ work.inspect                                                  ← 走 plane.list_descendant_works (read-only)

主 Agent 派发 Worker 路径（与上独立）：
orchestrator.prepare_dispatch
 └─ delegation_plane.prepare_dispatch()
      ├─ _build_runtime_context()                                ← F091 Phase D 完成
      ├─ _delegation_mode_for_target_kind()                      ← F091 Phase D 完成
      └─ pipeline run (5 节点：route/bootstrap/tool_index/gate/finalize)
           └─ delegation_plane.mark_dispatched()
```

**关键割裂点**：
- 旁路路径（spawn/delegate_task）**不经过 plane.prepare_dispatch**，自行拼装 DelegationManager + launch_child；plane 仅承担生命周期管理（cancel/merge/delete）和 prepare_dispatch（被 orchestrator 调用，与 builtin_tools 无关）
- 这就是 D4 的本质：**编排逻辑没有单一入口**

---

## 5. 5 处职责重叠（实测）

| 职责 | 当前散落 | F092 收敛后 |
|------|---------|------------|
| **target_kind 校验** | capability_pack._enforce_child_target_kind_policy（仅 1 处但被 _launch_child_task 调用，不在 plane 路径上）| 由 plane 显式调用（无论旁路还是主路径） |
| **runtime_context 构造** | plane._build_runtime_context（F091 完成）/ capability_pack._launch_child_task control_metadata（line 1254-1264）| 统一入口由 plane 提供，capability_pack 改用 plane API |
| **delegation_mode 推断** | plane._delegation_mode_for_target_kind（F091 完成，2 处调用：line 164/278） | 同上，提供 public helper |
| **depth/concurrent gate** | DelegationManager.delegate（hard gate）+ delegation_tools / delegate_task_tool 各自调用 | 仅 plane 调用 manager |
| **launch_child 拼装** | _deps.py:275 + delegation_tools.py:202 + delegate_task_tool.py:193 → 调 capability_pack._launch_child_task | 由 plane 统一 spawn 入口提供 |

---

## 6. 跨包/跨模块引用图

### 6.1 `packages/core/src/.../models/delegation.py` 被导入方

仅数据模型，无逻辑。被以下生产代码 import：
- `delegation_plane.py`（Work / WorkKind / WorkStatus / DelegationTargetKind / DelegationMode 等）
- `capability_pack.py`（DelegationTargetKind / DynamicToolSelection）
- `orchestrator.py`（DelegationTargetKind / DelegationEnvelope / DelegationResult）
- `task_service.py`（WorkStatus / Work）
- `builtin_tools/delegation_tools.py`（间接通过 core.models）
- `models/__init__.py`（导出枢纽）

**F092 影响**：**零** — 不动数据模型。

### 6.2 `apps/gateway/.../harness/delegation.py` 被导入方

- `builtin_tools/delegate_task_tool.py:35`（DelegateTaskInput / DelegationContext / DelegationManager）
- `builtin_tools/delegation_tools.py:20`（同上）

**F092 影响**：这两处 import 改为 plane API，DelegationManager 仍保留但仅由 plane 内部 import。

### 6.3 `delegation_plane.py` 被导入方

无外部 API 暴露。仅 gateway 内部 6 处调用：
- `main.py` / `octo_harness.py`：装配 + bind
- `orchestrator.py`：prepare_dispatch
- `capability_pack.py`：list_descendant_works / cancel_work / merge_work（review/apply_worker_plan 内）
- `task_runner.py`：bind dispatch_scheduler
- `builtin_tools/_deps.py`：通过 deps._delegation_plane 间接

**F092 影响**：plane 新增公开 API（如 `spawn_child` / `dispatch`），现有 API 保留兼容。

---

## 7. F091 Phase D 已铺路成果（F092 直接复用）

### 7.1 `_build_runtime_context`（@staticmethod，delegation_plane.py:842）

签名（17 个 keyword-only 参数，含 `delegation_mode`）：
```python
@staticmethod
def _build_runtime_context(*, request, task, project_id, work_id, parent_work_id,
    pipeline_run_id, session_owner_profile_id, inherited_context_owner_profile_id,
    delegation_target_profile_id, turn_executor_kind, agent_profile_id,
    context_frame_id, route_reason, worker_capability,
    delegation_mode: DelegationMode) → RuntimeControlContext
```
当前调用：`prepare_dispatch:148`（1 处）。

**F092 复用方式**：保留签名不变。可能由 plane 的新 spawn API 内部调用以构造旁路路径的 runtime_context（替代 capability_pack._launch_child_task 当前手动拼装的 control_metadata）。

### 7.2 `_delegation_mode_for_target_kind`（@staticmethod，delegation_plane.py:892）

```python
@staticmethod
def _delegation_mode_for_target_kind(target_kind: DelegationTargetKind) → DelegationMode:
    if target_kind is DelegationTargetKind.SUBAGENT:
        return "subagent"
    return "main_delegate"
```
当前调用：`prepare_dispatch:164` + `_handle_route_resolve:278`（2 处）。

**F092 复用方式**：保留签名不变，可能在 plane 的统一 spawn API 内调用，或提升为 public（去 `_` 前缀）。

---

## 8. 风险评级与可控性

### 8.1 评级：MEDIUM

| 维度 | 评估 |
|------|------|
| 直接改动文件 | 6-8 个（5 起点 + 2-3 周边：_deps.py / orchestrator.py 可能微调） |
| 跨包改动 | 是（packages/core ↔ apps/gateway）但 packages/core 仅数据模型，**只读**不动 |
| 数据迁移 | **否**（无字段增删，无 schema 改动） |
| 公开 API 破坏 | **否**（plane 新增 API，旧 API 兼容保留至 Phase 4 残留扫描确认无外部调用方再决定是否删） |
| 行为零变更（核心 invariant） | 是（必须验证 e2e_smoke + delegation 相关单测全 pass） |
| 测试基线 | 3170 collected @ 69e5512 |

### 8.2 风险点（按降序）

1. **subagents.spawn 旁路一致性**（中）：旁路改走 plane 后，必须保证 DelegationManager.delegate + _enforce_child_target_kind_policy + _emit_spawned_event 的顺序与原一致；否则破坏 F085 T2 / T34 / T44 修复的 audit 不变量。
2. **runtime_context 构造去重**（中）：若把 capability_pack._launch_child_task 内拼装的 control_metadata 替换为 plane._build_runtime_context，必须 grep 所有 control_metadata 字段消费方（task_runner.launch_child_task 等），确保字段对齐。
3. **DelegationManager 注入路径**（低）：当前由 builtin_tools 各自 new；改由 plane 内部 new 后，stores 注入需在 plane 装配时一次完成。
4. **测试 fixture 调用 DelegationManager**（低）：测试可能直接 new DelegationManager，F092 只动生产代码，测试保持。
5. **`_enforce_child_target_kind_policy` 的调用时机**（低）：原在 capability_pack._launch_child_task 内调用；F092 后若搬到 plane，需在等价时机调用，不能晚于 launch_child_task。

### 8.3 不在范围（明确排除）— 给后续 Feature 预留接口

- ❌ 不删除 `_enforce_child_target_kind_policy` Worker→Worker 禁止 → **F098 H3-B 解绑**
- ❌ 不实施 A2A / Subagent 模式分离 → **F095 / F097**
- ❌ 不动 `WorkStatus` 枚举或 F091 映射函数 → F091 已稳定
- ❌ 不动 `D2 WorkerProfile` 合并 → **F107**
- ❌ 不改 Worker 真实运行行为 → **F093-F096**

---

## 9. 测试基线锚定

```
uv run pytest --collect-only -q
→ 3170 tests collected in 7.58s
```

### 9.1 直接相关测试文件（F092 必须保持 0 regression）

| 测试文件 | 路径 | 覆盖点 |
|---------|------|--------|
| test_delegation_plane.py | apps/gateway/tests/ | prepare_dispatch / mark_dispatched / complete_work / list_works / pipeline handlers |
| test_delegation_manager.py | apps/gateway/tests/harness/ | DelegationManager.delegate gate（depth/concurrent/blacklist）|
| test_subagents_spawn_delegation.py | apps/gateway/tests/builtin_tools/ | subagents.spawn 集成路径（DelegationManager + launch_child） |
| test_delegation_mode_writes_f091.py | apps/gateway/tests/ | F091 Phase D delegation_mode 写入回归 |
| test_e2e_delegation_a2a.py | apps/gateway/tests/e2e_live/ | A2A e2e |

### 9.2 间接相关（可能受影响）

- test_capability_pack*.py（_launch_child_task / build_tool_context）
- test_orchestrator*.py（prepare_dispatch 调用方）
- test_task_runner*.py（launch_child_task 接收端）

---

## 10. F093/F095 接口点说明（阶段 1 起点预留）

F092 完成后，下一阶段（F093 Worker Full Session Parity / F095 Worker Behavior Parity）需要的接口点：

- **统一 spawn API**：F093/F095 创建 Worker 独立 session / behavior 时调用 plane 统一入口，避免重新散落
- **DelegationMode 显式参数**：F093 涉及 Worker session 隔离时，可通过 plane 的 spawn API 接收 `delegation_mode="main_delegate"` 显式传递（已有基础设施，F091 Phase D 完成）
- **runtime_context F091 接口**：F093 需扩展 RuntimeControlContext 字段（如 worker_session_id）时，plane 的 _build_runtime_context 是统一接入点

---

## 11. 验收 checklist（从主 prompt 复制，回报时打勾）

- [ ] DelegationPlane 成为唯一编排入口（grep 验证：spawn/dispatch/split/merge/cancel 调用都从 plane 走）
- [ ] capability_pack 不再 enforce target_kind 策略（_enforce_child_target_kind_policy 仍保留 Worker→Worker 禁止，但只在 plane 调用，不在多处）
- [ ] DelegationManager 接 PlaneRequest 返回 success/error 的 API 清晰
- [ ] delegation_tools 工具入口只做参数收集 + 调 plane
- [ ] 委托相关代码 5+ 处收敛验证（grep `delegation` 关键 production 入口数从 5+ 降到 1+）
- [ ] 全量回归 0 regression vs F091 baseline (69e5512) — **3170 collected**
- [ ] e2e_smoke 每 Phase 后 PASS
- [ ] 每 Phase Codex review 闭环（0 high 残留）
- [ ] Final cross-Phase Codex review 通过
- [ ] completion-report.md 已产出
- [ ] F093 / F095 接口点说明已写入
- [ ] Phase 跳过显式归档（若有）
