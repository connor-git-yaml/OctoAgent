# F092 DelegationPlane Unification — 残留扫描报告（Phase 4）

**Phase 4 时点**：commit f85490f（Phase D 完成后）
**baseline**：F091 完成 commit 69e5512
**测试基线**：3100 passed + 10 skipped + 1 xfailed + 1 xpassed（不含 e2e_live）

---

## 1. 旧标识符清零验证

| 标识符 | 应消失位置 | 实际残留 | 结论 |
|--------|-----------|---------|------|
| `_enforce_child_target_kind_policy` | production code | 0 | ✅ 全清 |
| `_delegation_mode_for_target_kind` | production + tests | 0 | ✅ 全清 |
| `DelegationManager(` 直接构造 | production code（不含 plane.py / harness/delegation.py 自身）| 0 | ✅ 全清 |
| `launch_child(` helper 调用 | production code | 0（helper 已删）| ✅ 全清 |
| `_emit_spawned_event` 外部调用 | production code（不含 plane.py / harness/delegation.py 定义）| 0 | ✅ 全清 |

### 验证 grep 命令

```bash
# 1. 旧名 _enforce_child_target_kind_policy
grep -rn "_enforce_child_target_kind_policy" apps/ packages/ → 无输出

# 2. 旧名 _delegation_mode_for_target_kind
grep -rn "_delegation_mode_for_target_kind" apps/ packages/ → 无输出

# 3. DelegationManager( 直接构造（production，不含 plane / harness/delegation 定义）
grep -rn "DelegationManager(" apps/ packages/ | grep -v "delegation_plane.py" | grep -v "harness/delegation.py" | grep -v "test_" → 无输出

# 4. launch_child( helper 调用（不含 launch_child_task）
grep -rn "launch_child(" apps/ packages/ | grep -v "launch_child_task" → 无输出

# 5. _emit_spawned_event 外部调用
grep -rn "_emit_spawned_event\b" apps/ packages/ | grep -v "delegation_plane.py" | grep -v "harness/delegation.py:266" | grep -v "test_"
→ 无输出（仅测试中合理 stub / 反射访问）
```

---

## 2. 豁免路径验证（refactor-plan.md §0.2）

按 Codex Phase 2 review HIGH 3 修订要求，3 条非 builtin_tools 派发路径**不在 F092 收敛范围**，
Phase 4 必须 explicit 验证它们仍存在且未被误清理。

| 豁免路径 | 文件:line | 验证 grep | 状态 |
|---------|-----------|-----------|------|
| capability_pack.apply_worker_plan | `capability_pack.py:1000` `await self._launch_child_task(...)` | `grep -n "_launch_child_task" capability_pack.py` → 1000 / 1229（定义）| ✅ 仍存在 |
| control_plane work.split | `work_service.py:529` `await self._ctx.task_runner.launch_child_task(message)` | `grep -n "task_runner.launch_child_task" work_service.py` → 529 | ✅ 仍存在 |
| control_plane spawn_from_profile | `worker_service.py:1173` `await self._ctx.task_runner.launch_child_task(...)` | `grep -n "task_runner.launch_child_task" worker_service.py` → 1173 | ✅ 仍存在 |

这 3 条路径会在 F098 H3-B 解绑（worker→worker）/ F107 Capability Layer Refactor 范围内重组。

---

## 3. 收敛验证：散落 5+ 处 → 1 处生产入口

### 3.1 DelegationManager 生产构造

```bash
grep -rn "DelegationManager(" apps/ packages/ | grep -v "test_" | grep -v __pycache__
```

唯一构造点：
- `apps/gateway/src/octoagent/gateway/services/delegation_plane.py:1058` （spawn_child 内部）

测试用构造：
- `apps/gateway/tests/harness/test_delegation_manager.py`（DelegationManager 单元测试）
- `apps/gateway/tests/test_delegation_plane_spawn_child.py:469-...`（real_delegation_manager batch test）

**收敛结论**：F091 baseline 时分布在 builtin_tools/delegation_tools.py:173 + builtin_tools/delegate_task_tool.py:164（2 处）；F092 后仅 plane 内部 1 处。配合前置 5+ 处委托相关代码（含工具 handler / helper / capability_pack 拼装 / manager 构造）→ 现在 plane.spawn_child 是唯一入口。

### 3.2 委托动作流入口

- LLM 工具入口（subagents.spawn / delegate_task）→ 调用 `deps.delegation_plane.spawn_child(...)`
- spawn_child 内部组装：
  - DelegationManager.delegate（gate）
  - capability_pack._launch_child_task（实际派发，内部继承 enforce）
  - mgr._emit_spawned_event（仅 emit_audit_event=True）

### 3.3 D4 闭环结论

> "委托代码散落 5+ 处" → "DelegationPlane 单一编排入口"

- ✅ 工具入口（builtin_tools/*.py）只做参数收集 + 调 plane API
- ✅ DelegationManager 仅 plane 内部构造（其他 production 入口 0 处）
- ✅ launch_child helper 已删除（_deps.py 简化）
- ✅ _emit_spawned_event 由 plane 统一调度（其他 production 入口 0 处）
- ✅ enforce_child_target_kind_policy 仍由 capability_pack._launch_child_task 调用（保持原顺序，零行为变更）

---

## 4. 描述性引用豁免清单

以下引用是**描述性**（注释 / docstring / 错误消息字符串），保留作为历史脉络：

| 文件 | 行 | 内容 | 类型 |
|------|---|------|------|
| `harness/__init__.py:3` | "包含 ... DelegationManager 等核心组件" | docstring 描述 |
| `delegate_task_tool.py:14` | "C4 两阶段记录：DelegationManager.delegate() 写 SUBAGENT_SPAWNED ..." | 模块文档 |
| `delegate_task_tool.py:99` | "F092 Phase C：旁路 DelegationManager + launch_child 已收敛到 plane.spawn_child" | 注释（历史脉络）|
| `delegate_task_tool.py:165` | `f"launch_child_failed: {type(exc).__name__}: ..."` | 错误消息字符串（保持兼容）|
| `delegate_task_tool.py:202` | "sync_mode_no_task_id: launch_child 未返回 task_id ..." | 错误消息（同上）|
| `delegate_task_tool.py:278` | "DelegateTaskInput 通过 @tool_contract 绑定" | 注释 |
| `delegation_tools.py:120` | "F092 Phase C：旁路逻辑（DelegationManager 直接 new + launch_child helper）" | 注释 |
| `delegation_tools.py:131-132` | "替代原 launch_child helper 内部解析" | 注释 |
| `delegation_tools.py:200` | `f"DelegationManager 拒绝全部 {len(items)} 个 objective"` | 错误消息 |
| `harness/delegation.py:210` | "实际调度由 launch_child / subagent lifecycle 在 Phase 4 接入" | 历史 docstring（不影响）|
| `core/models/enums.py:211` | "DelegationManager 派发子任务" | 枚举注释 |

---

## 5. 测试中的反射式访问豁免

测试中通过反射或 stub 方式访问 protected `_emit_spawned_event` 是合理豁免：

- `test_delegation_plane_spawn_child.py:48-65` `_StubManager._emit_spawned_event`（stub class 方法，非真实访问）
- `test_delegation_plane_spawn_child.py:247,274` 测试名/描述提及 emit_spawned_event（描述性）
- `test_delegate_task_contract.py:178,191` 注释/描述（描述性）
- `test_delegation_manager.py:262-282` DelegationManager 单元测试直接调用 `mgr._emit_spawned_event(...)`（合理：测 DelegationManager 自身单元行为）

---

## 6. 测试基线对照

| 时点 | passed | 净增 vs F091 | 备注 |
|------|--------|--------------|------|
| F091 完成（69e5512）| 3081 | baseline | F091 commit message 报告 |
| F092 Phase A（ff5adb8）| 3092 | +11 | 加入 spawn_child 集成测试 |
| F092 Phase B（1917dba）| 3092 | +11 | 仅重命名，无新测试 |
| F092 Phase C（12fe31e）| 3100 | +19 | +1 batch capacity gate + +1 batch raise propagate + 重写 5 spawn 测试 |
| F092 Phase D（f85490f）| 3100 | +19 | 仅删除 launch_child helper |

测试覆盖增强：
- spawn_child 集成测试 17 个（gate / written / raise propagate / depth refresh / additional accumulation / real DelegationManager batch capacity gate）
- subagents.spawn 工具层测试 6 个（mock plane，覆盖聚合逻辑 / propagate）
- delegate_task 测试 1 个（验证 emit_audit_event=True / audit_task_fallback 透传）

测试统计验证命令（Codex Final review MEDIUM 2 修订正确路径）：
```bash
# Phase A 加 spawn_child 集成测试 11 → Phase C 加 6 = 17 个
grep -c "^async def test_\|^def test_" apps/gateway/tests/test_delegation_plane_spawn_child.py

# Phase C 重写 subagents.spawn 工具层测试 6 个（净 +1，原 5 个）
grep -c "^async def test_\|^def test_" apps/gateway/tests/builtin_tools/test_subagents_spawn_delegation.py

# Phase C 重写 delegate_task spawn 事件测试（净 0，重命名为 test_delegate_task_passes_emit_audit_event_true_to_plane）
grep -c "^async def test_\|^def test_" apps/gateway/tests/tools/test_delegate_task_contract.py

# F091 测试无变化（仅重命名 _delegation_mode_for_target_kind → delegation_mode_for_target_kind）
grep -c "^async def test_\|^def test_" apps/gateway/tests/test_delegation_mode_writes_f091.py
```

---

## 7. 风险残留 / 已知技术债

| 项目 | 严重度 | 处理 |
|------|--------|------|
| plane 跨越调用 capability_pack._launch_child_task protected API | LOW | 推迟 F107 Capability Layer Refactor（Codex Phase A LOW 3 闭环）|
| 3 条非 builtin_tools 派发路径仍散落（apply_worker_plan / work.split / spawn_from_profile）| MEDIUM | 显式列入 F092 范围外豁免；F098 / F107 处理 |
| `_emit_spawned_event` 仍是 protected（跨 service 调用）| LOW | 与 LOW 1 一并 F107 处理 |

---

## 8. 验收 checklist 闭环

| 项目 | 状态 |
|------|------|
| DelegationPlane 成为唯一编排入口 | ✅ grep DelegationManager( production = 1 处（仅 plane）|
| capability_pack 不再 enforce target_kind 策略（重命名为 public）| ✅ grep _enforce 0 残留 |
| DelegationManager 接 PlaneRequest 返回 success/error API 清晰 | ✅ DelegateResult 二态 + SpawnChildResult 二态 |
| delegation_tools 工具入口只做参数收集 + 调 plane | ✅ 6 工具 handler 简化 |
| 委托相关代码 5+ 处 → 1+ 处收敛 | ✅ 仅 plane.spawn_child 1 处 |
| 全量回归 0 regression vs F091 baseline (69e5512) | ✅ 3100 passed (+19 含新加测试)|
| e2e_smoke 每 Phase 后 PASS | ✅ A/B/C/D 4 phase 全 PASS |
| 每 Phase Codex review 闭环（0 high 残留）| ✅ Phase A 1 high+1 medium / Phase C 3 high+2 medium 全闭环 |
| Final cross-Phase Codex review | 待 Phase 5 |
| completion-report.md | 待 Phase 5 |
