# F092 DelegationPlane Unification — 分批规划

**输入**：[impact-report.md](./impact-report.md)
**目标**：把散在 5+ 处的委托代码收敛到 `DelegationPlane` 单一编排入口；行为零变更。
**Phase 顺序原则**：F091 实证为好 pattern——**先简后难，先建立 baseline 信心**。

---

## 0. F092 范围澄清（Codex review 修订）

### 0.1 纳入 F092 的"散落 5+ 处"实测清单

只有 LLM 工具触发的旁路派发路径才属于 F092 收敛对象：
- `delegation_tools.subagents_spawn` — 直接 new DelegationManager + 调 launch_child helper
- `delegate_task_tool.delegate_task` — 同上
- `_deps.launch_child` helper — 上述两条共用的 helper

### 0.2 显式排除的派发路径（F092 范围外，标注豁免）

经 grep 验证，还有 3 条 `task_runner.launch_child_task` 派发路径**不在 F092 范围**：

| 路径 | 文件:line | 不纳入 F092 的理由 |
|------|-----------|---------------------|
| `capability_pack.apply_worker_plan` | `capability_pack.py:1000`（spawned_by="worker_review_apply"）| capability_pack 内部装配 + 经 `_launch_child_task` 自然继承 enforce + control_metadata 拼装 → Phase B 提为 public 后这条路径自动一致；不需改调用方。**无 DelegationManager gate**（设计上 worker_review_apply 不受 LLM gate 约束，因审批已经过 review_worker_plan 走完一轮）|
| `control_plane/work_service.work.split` | `work_service.py:529`（spawned_by="control_plane"）| 控制台触发，非 LLM 自主派发，本质是用户/control plane 动作。**无 DelegationManager gate / 无 enforce**——属 H3-B 解绑范围（F098） |
| `control_plane/worker_service.spawn_from_profile` | `worker_service.py:1173` | Worker lifecycle 创建路径（非委托派发动作），属 worker create 范畴。F107 Capability Layer Refactor 处理 |

**Phase 4 残留扫描必须 explicit 验证这 3 条豁免路径仍存在且未被误清理**。

### 0.3 验收 grep 必须包含的查询（Codex MEDIUM 3 修订）

旧验收清单只查 `DelegationManager(` / `launch_child(` 是不充分的——必须加：
- `_launch_child_task(` 调用点：应稳定 = 4（`_deps.py:289` Phase D 删除后变 3：apply_worker_plan + 1 处定义 + 内部转发；work_service / worker_service 不属此 method）
- `launch_child_task(` 调用点：应稳定 = 3（apply_worker_plan 内 + work_service / worker_service 豁免路径）+ task_runner.py:174 定义点
- `DelegationManager(` 在 production code：应只在 plane 内部 1 处

---

## Phase 划分总览

| Phase | 目标 | 改动范围 | 复杂度 | 验收 |
|-------|------|---------|--------|------|
| **A** | plane 新增统一 spawn API（无调用方）| `delegation_plane.py` | 低 | 测试全过 + 新 API 有单测 |
| **B** | enforce 策略提为 public + plane 在 spawn 路径上显式调用 | `capability_pack.py` + `delegation_plane.py` | 低 | grep enforce 调用点；测试全过 |
| **C** | builtin_tools 切换到 plane API（核心收敛工作）| `delegation_tools.py` + `delegate_task_tool.py` | 中 | grep `DelegationManager(` 不在 builtin_tools 出现 |
| **D** | 清理（死代码 / 重命名 / public 化）| `_deps.py` + `delegation_plane.py` + tests | 低 | grep 产线委托入口 ≤ 2；测试全过 |

**整体不变量（每 Phase 后必须满足）**：
- 全量回归 0 regression vs F091 baseline (69e5512) — `uv run pytest --collect-only` ≥ 3170
- e2e_smoke PASS
- 每 Phase commit 前做 Codex per-Phase review，0 high 残留再提交

---

## Phase A：plane 新增统一 spawn API

### 改动点

#### A.1 `delegation_plane.py`：新增 public 方法 `spawn_child`

**关键顺序保持原路径不变（Codex HIGH 1 修订）**：原路径是 gate 先 → enforce 后（隐含在 _launch_child_task 内）。新 API 必须保持此顺序。

签名（草案，实现时可微调）：
```python
async def spawn_child(
    self,
    *,
    parent_task,
    parent_work,
    objective: str,
    worker_type: str,
    target_kind: str,
    tool_profile: str,
    title: str,
    spawned_by: str,                       # "delegate_task_tool" | "subagents_spawn"
    plan_id: str = "",
    emit_audit_event: bool = False,        # 默认 False；仅 delegate_task_tool 路径设 True
    delegation_manager: DelegationManager | None = None,  # DI 钩子，便于测试
) -> SpawnChildResult:
    """统一 spawn 编排入口（Codex review 修订后顺序）：
    1. 推断 depth + active_children（容错：execution_context / task_store / list_descendant_works
       三层 try/except，任意失败降级为 depth=0 / active_children=[]）
       使用 `WORK_TERMINAL_VALUES = {s.value for s in WORK_TERMINAL_STATUSES}`，
       从 `octoagent.core.models import WORK_TERMINAL_STATUSES` 派生（Codex MEDIUM 1 修订）
    2. DelegationManager.delegate gate（depth/concurrent/blacklist）
       - 失败时返回 SpawnChildResult(status="rejected", error_code, reason)
    3. capability_pack._launch_child_task（内部仍调 enforce + 调 task_runner.launch_child_task）
       - launch_child_task raise → 捕获后返回 SpawnChildResult(status="launch_raised", reason=str(exc))
    4. 仅 emit_audit_event=True 时调 DelegationManager._emit_spawned_event
       （Codex HIGH 2 修订：subagents.spawn 当前不写此事件，迁移后必须保持不写——零行为变更）
       _emit_spawned_event 内部已吞异常（log only），不影响主流程
    返回 SpawnChildResult（status="written"|"rejected"|"launch_raised"，含 launch_child_task 全部字段）。
    """
```

#### A.2 `SpawnChildResult` 字段合同（Codex MEDIUM 2 修订）

精确字段（与 `_launch_child_task` 当前返回 dict 1:1 映射，不重命名）：

```python
@dataclass(frozen=True)
class SpawnChildResult:
    status: Literal["written", "rejected", "launch_raised"]
    # 业务字段（status="written" 时填充，其他场景空字符串/None）
    task_id: str = ""              # _launch_child_task 返回的 child task_id（注意：不重命名为 child_task_id）
    created: bool = False
    thread_id: str = ""
    target_kind: str = ""
    worker_type: str = ""
    tool_profile: str = ""
    parent_task_id: str = ""
    parent_work_id: str = ""
    title: str = ""
    objective: str = ""
    worker_plan_id: str = ""
    # 失败字段（status != "written" 时填充）
    error_code: str = ""           # gate 失败时由 DelegateResult 提供
    reason: str = ""               # gate 失败 reason 或 launch raise 异常文本
```

> 注意：当前 `_launch_child_task` 返回 dict **不含** `work_id` / `session_id`。
> 不要在 SpawnChildResult 加上这些字段（避免假关联键），由调用方单独从 work_store 查询。

#### A.2 `delegation_plane.py`：`_delegation_mode_for_target_kind` 提为 public

将 `_delegation_mode_for_target_kind` 重命名为 `delegation_mode_for_target_kind`（去 `_` 前缀）。
F091 测试 `test_delegation_mode_writes_f091.py` 用 `DelegationPlaneService._delegation_mode_for_target_kind`
访问，需同步更新。

`_build_runtime_context` 暂保留 protected（内部装配函数，长签名不适合公开）。

### 改动文件清单

- `apps/gateway/src/octoagent/gateway/services/delegation_plane.py`（新增 spawn_child + 重命名 helper）
- `apps/gateway/tests/test_delegation_mode_writes_f091.py`（同步重命名）
- 新测试文件：`apps/gateway/tests/test_delegation_plane_spawn_child.py`（spawn_child 单测）

### 验收

- `uv run pytest --collect-only -q` → ≥ 3170（含新加测试）
- `uv run pytest apps/gateway/tests/test_delegation_plane*.py -q` 全过
- grep `delegation_mode_for_target_kind` 在 plane / 测试均一致使用 public 名
- grep `DelegationPlaneService\.spawn_child\(` 仅在新单测出现（生产无调用方）

### Codex per-Phase review

输入：A.1 + A.2 + 新测试 diff
关注点：
- spawn_child 内部组装顺序与原 builtin_tools 旁路一致（**gate 先 → enforce 在 launch_child_task 内继承 → 仅 delegate_task 路径 emit**）？
- depth / active_children 推断逻辑保留 3 层 try/except 容错？
- WORK_TERMINAL_VALUES 从 `octoagent.core.models import WORK_TERMINAL_STATUSES` 派生（**禁止反向 import builtin_tools._deps**）？
- SpawnChildResult 字段精确对齐 _launch_child_task 返回 dict（不引入假关联键 work_id/session_id）？

---

## Phase B：enforce 策略提为 public（不改调用顺序）

> **Codex HIGH 1 修订**：原计划"plane 第 1 步显式调用 enforce"会改变行为（gate 之前抛 enforce 错误）。
> 现修订为：enforce **仍由 capability_pack._launch_child_task 内部调用**（位置不变），仅做"提为 public"的命名清理。

### 改动点

#### B.1 `capability_pack.py`：`_enforce_child_target_kind_policy` 提为 public

重命名为 `enforce_child_target_kind_policy`（去 `_` 前缀），仍保留 `@staticmethod`。
内部 `_launch_child_task` 调用同步更新。

理由：F092 范围说"capability_pack 只负责权限/工具选择"，enforce 是权限决策，**仍属 capability_pack 职责**。
提为 public 是为了：
1. 允许将来（F098 解绑时）plane 在 PlaneRequest 阶段可以预查询；现在 plane.spawn_child 不需要调用
2. 命名规范化（与 plane 已 public 化的 `delegation_mode_for_target_kind` 对齐）

#### B.2 plane.spawn_child **不显式调用 enforce**

enforce 由 `_launch_child_task` 内部调用即可（行为零变更）。如果 enforce 失败：
- 当前路径：gate 通过 → emit_spawned_event 已写（delegate_task 路径）→ launch_child_task → enforce raise → 上层 try/except return rejected
- 新路径（spawn_child）：gate 通过 → 调 `_launch_child_task` → enforce raise → SpawnChildResult(status="launch_raised", reason=...)
- 关键：emit_spawned_event 在 launch_child_task **之后**调用（仅 delegate_task 路径），enforce raise 时 emit 不会被触发。这与原 delegate_task_tool.py 的 emit 时机一致（`if spawned_task_id: emit`）。

### 改动文件清单

- `apps/gateway/src/octoagent/gateway/services/capability_pack.py`（仅重命名 + 内部调用同步）
- 测试文件可能涉及（grep 验证）

### 验收

- 测试全过
- grep `_enforce_child_target_kind_policy` 在 production code 应不再存在（仅旧重命名前注释 / 历史 commit）
- grep `enforce_child_target_kind_policy` 出现在 capability_pack._launch_child_task 内（**唯一调用点**）+ 单测

### Codex per-Phase review

关注点：
- 重命名后是否有外部 caller 漏改？grep production + tests 全面检查
- capability_pack 是否仍是 enforce 的拥有者（是，提为 public 后所有权不变）？
- enforce 调用顺序与原路径完全一致（`_launch_child_task` 内首行调用，gate 之后）？

---

## Phase C：builtin_tools 切换到 plane API（核心收敛工作）

### 改动点

#### C.1 `delegation_tools.py:subagents_spawn`

替换 line 130-216 的旁路逻辑：
- 删除：`current_depth` / `active_children` 推断（移到 plane.spawn_child 内部）
- 删除：直接 new DelegationManager（plane 内部 new）
- 删除：调用 launch_child helper（plane 内部调）
- 保留：`coerce_objectives` / `objectives` 多 child 拆分循环；保留 `skipped_objectives` / 部分成功 / 全 rejected 的结果聚合逻辑
- 替换为：
  ```python
  for item in items:
      result = await deps.delegation_plane.spawn_child(
          parent_task=parent_task,
          parent_work=parent_work,
          objective=item,
          worker_type=worker_type,
          target_kind=target_kind,
          tool_profile=deps._pack_service._effective_tool_profile_for_objective(objective=item),
          title=child_title,
          spawned_by="subagents_spawn",
          emit_audit_event=False,        # ← 关键：保持现有行为不写 SUBAGENT_SPAWNED 事件
      )
      if result.status == "rejected":
          skipped_objectives.append((item, result.reason))
          continue
      if result.status == "launch_raised":
          skipped_objectives.append((item, f"launch_raised: {result.reason}"))
          continue
      # status == "written"
      launched_raw.append(result)
  ```

> **Codex HIGH 2 修订**：subagents.spawn 当前路径**不调** `_emit_spawned_event`（grep 验证：仅 `delegate_task_tool.py:225` 有此调用）。
> spawn_child 必须通过 `emit_audit_event=False` 保持此行为，**不能新增审计事件**。

#### C.2 `delegate_task_tool.py:delegate_task`

同样替换 line 130-235 的旁路逻辑为单次调用 plane.spawn_child：

```python
result = await deps.delegation_plane.spawn_child(
    parent_task=parent_task,
    parent_work=parent_work,
    objective=task_description,
    worker_type=target_worker,
    target_kind="subagent",
    tool_profile=deps._pack_service._effective_tool_profile_for_objective(...) if deps._pack_service else "default",
    title=task_description[:60],
    spawned_by="delegate_task_tool",
    emit_audit_event=True,             # ← 保持现有行为：写 SUBAGENT_SPAWNED 事件
)
```

保留 sync 模式 wait + return 逻辑（这部分不属委托编排，属工具结果聚合）。

### 改动文件清单

- `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py`
- `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegate_task_tool.py`
- 测试改造（**Codex MEDIUM 3 修订：拆 2 类测试，不能把 gate 集成 mock 掉**）：
  - **工具层测试**：`test_subagents_spawn_delegation.py` / `test_delegate_task*.py` 可改为 mock `plane.spawn_child` 验证结果聚合
  - **集成层测试**：新增 `test_delegation_plane_spawn_child.py` 必须覆盖 batch active_children 累加 / all rejected / blacklist / depth / capacity_exceeded / launch raise / emit nonblocking / **emit_audit_event=True/False 区别**

### 验收

- 测试全过（含 builtin_tools 测试 + 新加 plane.spawn_child 集成测试）
- grep `DelegationManager(` 在 production code（不含 plane.py）应消失：仅 plane 内部 1 处构造
- grep `launch_child(` helper 调用应仅 plane.spawn_child 内部
- grep `_emit_spawned_event\b` 在 builtin_tools 应消失
- e2e_smoke PASS（subagents.spawn / delegate_task 都覆盖在内）

### Codex per-Phase review

关注点：
- gate 顺序保持原路径？（gate 先 → launch → emit；enforce 在 launch 内继承）
- builtin_tools 的多 objective 循环 + skipped_objectives / 部分成功处理是否仍正确（聚合逻辑放在工具层，spawn_child 只处理单个）？
- delegate_task 的 sync vs async callback_mode 行为不变？
- F085 T2 / T34 / T44 修复的不变量保留？（CAPACITY_EXCEEDED 经 plane.spawn_child 触发；SUBAGENT_SPAWNED 仅 delegate_task 路径写；WORK_TERMINAL_VALUES 一致）
- **emit_audit_event=False 在 subagents.spawn 路径下，确实未写新事件**？

---

## Phase D：清理 + 收尾

### 改动点

#### D.1 删除 `launch_child` helper @ `_deps.py:275`

如 grep 验证 builtin_tools 全部切到 plane.spawn_child，则 `launch_child` helper 可删除。
否则保留（说明有 caller 漏改，回 Phase C 修）。

#### D.2 builtin_tools 清理

- 删除 `from octoagent.gateway.harness.delegation import DelegateTaskInput, DelegationContext, DelegationManager` 在 builtin_tools（如不再使用）
- 删除 `WORK_TERMINAL_VALUES` 在 delegation_tools（如已移到 plane）

#### D.3 `_emit_spawned_event` 访问性

`DelegationManager._emit_spawned_event` 当前由 builtin_tools 直接调（line 225）。Phase C 后不再被外部调，可降回 protected。

#### D.4 文档同步

- delegation_plane.py 顶部 docstring 增加"统一 spawn API"说明
- F092 不更新 blueprint.md（行为零变更，无架构层级变更需要同步）

### 改动文件清单

- `apps/gateway/src/octoagent/gateway/services/builtin_tools/_deps.py`
- `apps/gateway/src/octoagent/gateway/services/builtin_tools/delegation_tools.py` / `delegate_task_tool.py`（清理 import）
- `apps/gateway/src/octoagent/gateway/harness/delegation.py`（_emit_spawned_event 访问性）
- `apps/gateway/src/octoagent/gateway/services/delegation_plane.py`（docstring）

### 验收

- 测试全过
- grep production code 委托关键入口 ≤ 2 处（plane.spawn_child + capability_pack._launch_child_task）
- grep `DelegationManager(` 在 production code 仅 plane 内部出现 1 处
- e2e_smoke PASS

### Codex per-Phase review

关注点：
- 删除的 helper 真无引用？
- 命名变更是否破坏外部测试？
- 文档同步是否到位？

---

## Phase 间依赖

```
A (新 API + helper public 化) → B (enforce public 化) → C (切换调用方) → D (清理)

A 和 B 可微并行（C 依赖两者完成）。但建议串行执行以便 per-Phase review 聚焦。
```

---

## Phase 跳过策略

若实施中发现某 Phase 实际不需要做（例如 Phase B 的 enforce 双重调用已被现有结构隐含解决），
按 CLAUDE.local.md §"工作流改进"，**显式归档**：
- commit message 注明"Phase X 跳过，理由 Y，影响 Z"
- completion-report.md "实际 vs 计划"对照表中标注

---

## Phase 4（残留扫描）准备项

执行 Phase 4 时需扫描的关键标识符（按 impact-report + Codex review 修订）：

### 应消失的旧名 / 旧入口

- `_enforce_child_target_kind_policy`（旧名）— production code 内应消失
- `_delegation_mode_for_target_kind`（旧名）— production + tests 内应全替换为 `delegation_mode_for_target_kind`
- `DelegationManager(` 直接构造 — 在 plane.py 之外应消失（含 builtin_tools / control_plane / capability_pack / 等）
- `launch_child(` helper（**`_deps.py:275` 定义**）— 应整体删除
- `_emit_spawned_event` 在 builtin_tools 的外部调用 — 应消失（仅留 plane 内部 / DelegationManager 内部 / 单测）

### 应保持稳定的豁免路径（**Codex HIGH 3 修订：F092 范围外**）

以下路径**不在 F092 收敛范围**，Phase 4 必须 explicit 验证它们仍存在且未被误清理：

```bash
# 豁免路径 1：apply_worker_plan
grep -n "_launch_child_task" apps/gateway/src/octoagent/gateway/services/capability_pack.py | grep ":1000"

# 豁免路径 2：control_plane work.split
grep -n "task_runner.launch_child_task" apps/gateway/src/octoagent/gateway/services/control_plane/work_service.py

# 豁免路径 3：control_plane spawn_from_profile
grep -n "task_runner.launch_child_task" apps/gateway/src/octoagent/gateway/services/control_plane/worker_service.py

# 这 3 处必须仍存在！否则说明误清理，回滚相关 Phase
```

### 通用豁免（描述性引用）

- 历史 commit 引用（git log）
- 注释 / docstring 中的描述性引用
- spec / refactor-plan / completion-report 文档中的描述性引用

---

## 风险缓解

1. **F085 不变量保护**（T2 / T34 / T44）：CAPACITY_EXCEEDED 计算 / SUBAGENT_SPAWNED 审计 / 真实 child_task_id 写入。Phase C 测试必须验证三条仍生效。
2. **e2e_smoke 必跑**：每 Phase 后跑一次 e2e_smoke，捕捉 plane 整合行为偏离。
3. **回滚预案**：Phase 失败时 `git reset --hard` 回上一 Phase commit；Phase 之间互不依赖于在途状态（每 Phase 自含可运行）。

---

## Codex review pre-Phase 3 闭环（2026-05-06）

| Severity | Title | 处理 |
|----------|-------|------|
| HIGH | enforce 顺序行为变更 | ✅ 修订 Phase A.1（spawn_child 不显式调 enforce）+ Phase B.2 改为"不显式调用 enforce"，仅做命名清理 |
| HIGH | SUBAGENT_SPAWNED 审计迁移基于错误前提 | ✅ 修订 Phase A.1（spawn_child 加 emit_audit_event 参数）+ Phase C.1 显式 emit_audit_event=False / Phase C.2 显式 True；SpawnChildResult 加 launch_raised 状态 |
| HIGH | "唯一 spawn 入口"漏掉多条生产派发路径 | ✅ 新增 §0.2"显式排除的派发路径"（apply_worker_plan / work.split / spawn_from_profile 列入豁免）+ Phase 4 残留扫描加豁免验证 |
| MED | depth/active_children 容错语义不足 | ✅ 修订 Phase A.1（明确 3 层 try/except + WORK_TERMINAL_STATUSES 直接 import core，禁止反向 import builtin_tools._deps）|
| MED | SpawnChildResult 字段合同不完整 | ✅ 新增 Phase A.2 精确字段表（与 _launch_child_task 当前 dict 1:1 映射，不引入假关联键）|
| MED | 测试迁移把 gate 集成 mock 掉 | ✅ 修订 Phase C 改动文件清单（拆 2 类测试：工具层 mock plane / 集成层新增 plane.spawn_child 必覆盖 gate）|

**闭环结论**：3 high + 3 medium 全部纳入 plan。Phase 3 实施可启动。
