# F090 Refactor Plan

生成时间：2026-05-05
拍板范围：A/A/A（D1 仅清控制信号 / D2 仅加 kind 字段 / D5 重命名）
基线 commit：`ff4635d` (master HEAD)
worktree：`.claude/worktrees/F090-type-system-cleanup`
分支：`feature/090-type-system-cleanup`

## 0. 执行原则

| 原则 | 含义 |
|------|------|
| **行为零变更** | 纯类型/命名重构。任何 e2e 行为差异都是 bug |
| **每 Phase 独立 commit** | 每个 Phase 完成 → 全量回归 → Codex review → commit |
| **不 push origin/master** | F090 全部 Phase 完成后给用户归总报告 + 等拍板 push |
| **失败回退** | 中间验证失败 → 暂停 → 用户选 A) 修复继续 / B) 回滚此 Phase / C) 中止 |
| **不锁日历** | 节奏按实际推进，不预设完成时间 |

## 1. Phase 切分（4 Phase 串行）

按"先简后难 / 先命名后语义"排序。前置 Phase 失败不阻塞后续 Phase 范围（4 Phase 互相独立），但严格串行执行避免 rebase 冲突。

```
Phase 1: Butler 残留清理（最简单，建立 baseline 信心）
   ↓
Phase 2: D5 WorkerSession → WorkerDispatchState（纯重命名）
   ↓
Phase 3: D2 AgentProfile.kind 字段（语义层小改）
   ↓
Phase 4: D1 metadata flag → RuntimeControlContext 扩展（最复杂）
```

---

## 2. Phase 1：Butler 残留清理

### 2.1 范围

**删除**（已运行完成的死代码）：
- `_migrate_butler_naming(conn)` 函数体 [startup_bootstrap.py:329](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:329)
- `_migrate_butler_suffix(store_group, agent_profile)` 函数体 [startup_bootstrap.py:337](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:337)
- 所有调用点（startup_bootstrap.py 内部）
- 函数相关的 imports / docstring

**修订**：
- `docs/codebase-architecture/*` 中 "Butler Direct" 术语 → "Main Direct"

**保留**（数据防御层）：
- `normalize_runtime_role()` [agent_context.py:80](octoagent/packages/core/src/octoagent/core/models/agent_context.py:80)
- `normalize_session_kind()` [agent_context.py:94](octoagent/packages/core/src/octoagent/core/models/agent_context.py:94)
- `test_migration_063.py` fixture（migration 测试需要）

### 2.2 前置检查（重要）

删除 migration 函数前必须确认：所有 active 实例已经过这些 migration（无脏数据残留）。

**自动检查脚本**（不实际执行 migration，只读 SQLite）：
```bash
for db in ~/.octoagent/data/*.db /Users/connorlu/.octoagent-master/data/*.db /Users/connorlu/.octoagent-agent/data/*.db; do
  [ -f "$db" ] || continue
  echo "=== $db ==="
  sqlite3 "$db" "SELECT 'agent_runtimes butler 残留' AS check_, COUNT(*) FROM agent_runtimes WHERE role='butler';"
  sqlite3 "$db" "SELECT 'agent_sessions butler_main 残留', COUNT(*) FROM agent_sessions WHERE kind LIKE 'butler%';"
  sqlite3 "$db" "SELECT 'memory_namespaces butler_private 残留', COUNT(*) FROM memory_namespaces WHERE kind LIKE 'butler%';"
done
```

**预期**：所有计数为 0。任何非 0 → 暂停 Phase 1，先跑 migration 再继续。

### 2.3 执行步骤

1. 跑前置检查脚本，确认所有 active 实例无残留
2. 删除 `_migrate_butler_naming` / `_migrate_butler_suffix` 函数体 + 调用点
3. grep 修订 docs 中的 "Butler Direct" 术语
4. **中间验证**：
   - `python -m pyright octoagent/` 或仓库用的类型检查器
   - `grep -rn "_migrate_butler" octoagent/apps octoagent/packages` 应为 0 命中
   - `grep -rn "Butler Direct" docs/` 应为 0 命中
5. 全量回归：`pytest octoagent/` 比对 F089 baseline
6. e2e_smoke：`pytest -m e2e_smoke octoagent/`
7. Codex adversarial review：`/codex:adversarial-review`
8. 处理 finding（high/medium 必处理，low 可记录 ignore）
9. commit（commit message 含 Codex review 闭环说明）

### 2.4 验收

- [ ] startup_bootstrap.py butler migration 函数体 0 命中
- [ ] docs/codebase-architecture/ "Butler Direct" 0 命中
- [ ] normalize_runtime_role / normalize_session_kind 保留
- [ ] test_migration_063.py 仍 PASS
- [ ] 全量回归 0 regression vs baseline
- [ ] e2e_smoke PASS
- [ ] Codex review 闭环（0 high 残留）

### 2.5 改动量级

~2 文件（startup_bootstrap.py + 1-2 个 docs 文件）/ ~50 行删除 + ~5 行 docs 修订

---

## 3. Phase 2：D5 WorkerSession → WorkerDispatchState

### 3.1 范围

**重命名**：
- `class WorkerSession` → `class WorkerDispatchState`（[orchestrator.py:165](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:165)）
- 所有 import / 类型注解 / 构造调用

**不留 deprecated alias**（13 处一次性改完，避免 alias 长期残留）。

### 3.2 影响文件

| 文件 | 命中类型 |
|------|---------|
| [orchestrator.py (models)](octoagent/packages/core/src/octoagent/core/models/orchestrator.py) | class 定义 + validator |
| [models/__init__.py](octoagent/packages/core/src/octoagent/core/models/__init__.py) | import + export |
| [orchestrator.py (services)](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py) | import + 参数注解 |
| [worker_runtime.py](octoagent/apps/gateway/src/octoagent/gateway/services/worker_runtime.py) | import + 参数注解×2 + 构造×1 + docstring |
| [adapters.py](octoagent/apps/gateway/src/octoagent/gateway/services/adapters.py) | import + 参数注解 |
| [a2a_runtime.py](octoagent/packages/core/src/octoagent/core/models/a2a_runtime.py) | docstring（"MainAgentSession -> WorkerSession"） |
| tests/test_a2a_models.py | import + 测试构造 |
| tests/test_orchestrator.py | import |

### 3.3 执行步骤

1. 改 [orchestrator.py:165](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:165) class 定义 + validator 名 `_validate_loop` 保留（属于 dispatch state 概念依然合理）
2. 改 models/__init__.py 的 import + `__all__` export
3. 改 services 层 6 个文件
4. 改 docstring 中的 "WorkerSession" 字面引用
5. 改 2 个 tests 文件
6. **中间验证**：
   - `grep -rn "class WorkerSession\|WorkerSession\b" octoagent/` 应为 0 命中（除 git 历史）
   - 类型检查 PASS
7. 全量回归 + e2e_smoke
8. Codex review
9. commit

### 3.4 验收

- [ ] `class WorkerSession` production + tests 0 命中
- [ ] `WorkerDispatchState` 替换覆盖所有 13 处
- [ ] tests/test_a2a_models.py 测试构造 PASS（字段集不变）
- [ ] 全量回归 0 regression vs Phase 1 commit
- [ ] e2e_smoke PASS
- [ ] Codex review 闭环

### 3.5 改动量级

~6 production 文件 + 2 tests / ~17 处替换 / 0 行新增

### 3.6 可能 Codex finding 预测

- [可能 high]：是否考虑过 `WorkerDispatchState` 与 F091 状态机统一的接口？
  → 应在 commit message 显式说明 F091 接口点：WorkerRuntimeState 留给 F091 改造
- [可能 medium]：a2a_runtime.py docstring 用了 "WorkerSession" 描述 carrier，
  改成 "WorkerDispatchState" 是否会让 carrier 概念混乱？
  → 实际读源码判断 docstring 是否需要重写而不仅是替换字面

---

## 4. Phase 3：D2 AgentProfile.kind 字段

### 4.1 范围

**新增字段**：
- AgentProfile 加 `kind: Literal["main", "worker", "subagent"]`（默认 `"main"`）
  位置：[agent_context.py:120-139](octoagent/packages/core/src/octoagent/core/models/agent_context.py:120)

**改造逻辑**：
- WorkerProfile → AgentProfile 镜像处（grep 找）写入 `kind="worker"`
- `_is_worker_behavior_profile()` ([behavior_workspace.py:1551](octoagent/packages/core/src/octoagent/core/behavior_workspace.py:1551))
  改读 `agent_profile.kind == "worker"`，移除 metadata 探测

**保留**：
- `WorkerProfile` 类完全保留（独立 SQL + 独立类型）
- 不改 worker_profiles 表 schema
- 不改 FE 类型

### 4.2 关键设计决策

**Q：AgentProfile 现有实例怎么办（kind 字段默认 "main"）？**

A：默认 `"main"` 是安全的——所有现有 AgentProfile 都是主 Agent 用的（worker
独立用 WorkerProfile + 镜像生成 AgentProfile）。镜像逻辑写入 kind="worker"
后，新建的 worker 镜像会带正确 kind；现有镜像启动时通过 normalize 兼容层重新读取。

**Q：subagent kind 何时使用？**

A：F097 Subagent Mode Cleanup 时使用。F090 只声明枚举不强制使用——预留语义槽位。

**Q：`_is_worker_behavior_profile` 旧 metadata 探测要删吗？**

A：**保留 metadata 探测作为 fallback**——确保 kind 字段未填充的旧 AgentProfile
仍能正确识别为 worker。读取顺序：先读 kind，kind 为空/未填则 fallback metadata。
这是数据防御层。F107 完全合并时移除 fallback。

### 4.3 执行步骤

1. 改 agent_context.py：AgentProfile 加 `kind` 字段
2. grep 找 WorkerProfile → AgentProfile 镜像逻辑（关键词：
   `worker_profile_mirror` / `source_kind` / `agent_profile_id` 在 worker
   构造路径），改造写入 `kind="worker"`
3. 改 behavior_workspace.py:1551 `_is_worker_behavior_profile`：
   ```python
   def _is_worker_behavior_profile(agent_profile: AgentProfile) -> bool:
       if agent_profile.kind == "worker":
           return True
       # fallback：兼容尚未填充 kind 的历史数据
       metadata = agent_profile.metadata
       return (
           str(metadata.get("source_kind", "")).strip() == "worker_profile_mirror"
           or bool(str(metadata.get("source_worker_profile_id", "")).strip())
       )
   ```
4. **中间验证**：
   - 类型检查 PASS（Literal 类型 + 默认值）
   - 加测试：worker 镜像生成的 AgentProfile.kind == "worker"
   - 加测试：旧 AgentProfile（无 kind）的 fallback 路径仍可识别 worker
5. 全量回归 + e2e_smoke
6. Codex review
7. commit

### 4.4 验收

- [ ] AgentProfile.kind 字段已添加，默认 "main"
- [ ] WorkerProfile→AgentProfile 镜像逻辑写入 kind="worker"
- [ ] _is_worker_behavior_profile 优先读 kind，metadata 探测降级 fallback
- [ ] 单测覆盖：新 worker 镜像 kind=worker / 老数据 fallback / main AgentProfile kind=main
- [ ] 全量回归 0 regression vs Phase 2 commit
- [ ] e2e_smoke PASS（特别关注 worker 行为文件加载）
- [ ] Codex review 闭环

### 4.5 改动量级

~3 文件 / ~10 行新增 + ~5 行 fallback 兼容逻辑 + 测试 ~30 行

### 4.6 可能 Codex finding 预测

- [可能 high]：metadata fallback 路径是否真有数据走？是否能直接删？
  → 答：删 fallback 必须证明所有 active 实例的 worker AgentProfile.kind 已填，
  这要等 F107 完全迁移后再做
- [可能 medium]：kind="subagent" 在 F090 内不实际使用，是否应该 F090 不声明
  这个枚举值，等 F097 再加？
  → 答：声明枚举值不影响行为，避免 F097 时改字段类型；保留

---

## 5. Phase 4：D1 metadata flag → RuntimeControlContext 扩展

### 5.1 范围

**RuntimeControlContext 字段扩展**（[orchestrator.py:33](octoagent/packages/core/src/octoagent/core/models/orchestrator.py:33)）：

```python
# 新增字段
delegation_mode: Literal[
    "main_inline",     # main agent 自己跑（原 single_loop_executor=True 主路径）
    "main_delegate",   # main agent 派给 worker（原 single_loop_executor=False 标准路径）
    "worker_inline",   # worker 自己跑（原 single_loop_executor=True worker 路径）
    "subagent",        # subagent 临时执行
] = Field(default="main_delegate", description="本次派发的执行模式")

recall_planner_mode: Literal[
    "full",  # 跑完整 recall planner
    "skip",  # 跳过（原 single_loop_executor=True 时的语义）
    "auto",  # 由系统按 delegation_mode 推断
] = Field(default="full", description="Recall planner 行为模式")
```

**改造文件**（22 处）：

| 文件 | 改造点 |
|------|-------|
| [orchestrator.py L758](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:758) | `_metadata_flag(metadata, "single_loop_executor")` → `runtime_context.delegation_mode in ("main_inline", "worker_inline")` |
| L785 | route_reason 拼接保留为日志（不算控制信号） |
| L799-808 | metadata dict 写入 → 改写到 `runtime_context` 字段（同时**保留**写到 metadata 作为 backward compat 一层兼容期）|
| L825 | `getattr(self._llm_service, "supports_single_loop_executor", False)` → `True` 直接判断（删除属性） |
| [llm_service.py L218](octoagent/apps/gateway/src/octoagent/gateway/services/llm_service.py:218) | 删除 `supports_single_loop_executor = True` 类属性 |
| L375 | `single_loop_executor = self._metadata_flag(metadata, "single_loop_executor")` → 从 runtime_context 读 |
| L379, L423, L912, L919, L921, L984 | 条件分支与参数传递改读 runtime_context |
| [task_service.py L1022-1026](octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py:1022) | `_metadata_flag` 静态方法**保留**作为 generic helper（其他场景仍可能用到 metadata flag）|
| L1044 | `if self._metadata_flag(dispatch_metadata, "single_loop_executor")` → `if runtime_context.recall_planner_mode == "skip"` |

**不改的部分**：
- `selected_worker_type` / `selected_tools` / `recommended_tools` / `tool_selection`
  这 47 处在 24 文件分布属于数据载荷（ToolSelection），留 F107 处理
- `_metadata_flag` helper 保留（其他 metadata flag 场景可能用到）

### 5.2 关键设计决策

**Q：写入兼容期保留 metadata 字段吗？**

A：**保留双写一段时间**。理由：
- F090 选 A 路径，目标是"扩展类型 + 改造读取"，不是"删除 metadata 字段"
- 双写期内既写 runtime_context 也写 metadata，读取统一从 runtime_context 读
- Phase 4 完成后所有读取已切换；F091 或 F092 时再做 metadata 写入端清理

实际上**简化方案**：写入只写 runtime_context（不写 metadata）；读取统一从 runtime_context；**移除** metadata flag 写入。理由：
- 选 A 的目标本就是"消除隐式 metadata 控制信号"，留双写违反目标
- 测试覆盖能保证零行为变更
- runtime_context 已是必填字段（OrchestratorRequest.runtime_context: RuntimeControlContext | None，但所有调用方都填了）

**最终采用**：写入只写 runtime_context；读取从 runtime_context 读；metadata flag 写入端删除。

**Q：runtime_context = None 的兼容兜底？**

A：实际所有调用路径都填了 runtime_context。Phase 4 改造前先做一次 grep 确认
`OrchestratorRequest(runtime_context=None)` 或 `runtime_context=None` 在 production 0 命中。
如有兜底路径，加 `assert runtime_context is not None` 显式拦截而不是隐式 fallback。

### 5.3 执行步骤

1. **前置 grep**：确认 `runtime_context=None` 在 production 调用 0 命中
2. 改 orchestrator.py 模型层：RuntimeControlContext 加 `delegation_mode` / `recall_planner_mode` 字段
3. 改 orchestrator.py services 层 9 处 metadata 写入：
   - L799-808 区域改写到 runtime_context（不写 metadata）
   - L758, L825 改读 runtime_context
4. 改 llm_service.py 9 处：
   - 删 L218 supports_single_loop_executor 类属性（直接 True 视为常量行为）
   - L374-379 改读 runtime_context.delegation_mode 和 recall_planner_mode
   - L423, L912, L919, L921, L984 改条件
5. 改 task_service.py L1044：改读 `runtime_context.recall_planner_mode == "skip"`
6. **中间验证**：
   - `grep -rn "single_loop_executor" octoagent/apps octoagent/packages | grep -v test_ | grep -v ".specify"` 应为 0 命中（除非 RuntimeControlContext 字段定义中保留 deprecated comment 引用）
   - `grep -rn "supports_single_loop_executor" octoagent/apps octoagent/packages` 应为 0 命中
   - `grep -rn '"single_loop_executor"' octoagent/apps octoagent/packages` 应为 0 命中
   - 类型检查 PASS
   - 单测覆盖 RuntimeControlContext 新字段 + 各 delegation_mode 路径
7. 全量回归 + e2e_smoke（特别关注 single_loop 主路径）
8. Codex review
9. commit

### 5.4 验收

- [ ] RuntimeControlContext 新增 delegation_mode / recall_planner_mode 字段
- [ ] orchestrator.py / llm_service.py / task_service.py 共 22 处全部改造
- [ ] `single_loop_executor` 在 production 0 命中
- [ ] `supports_single_loop_executor` 0 命中
- [ ] 写入端 metadata flag 全部移除
- [ ] 单测覆盖各 delegation_mode 路径
- [ ] 全量回归 0 regression vs Phase 3 commit
- [ ] e2e_smoke PASS（5x 循环验证 single_loop 主路径稳定）
- [ ] Codex review 闭环

### 5.5 改动量级

~3 文件 / ~30 行核心改造 + ~5 行字段定义 + 测试 ~50 行

### 5.6 可能 Codex finding 预测

- [可能 high]：双写 → 单写的切换顺序是否正确（先加新字段读取 → 再删旧 metadata 写入），
  避免中间态半成品 commit？
  → 答：F090 内一次 commit 完成全部切换，不留中间半成品状态
- [可能 high]：`runtime_context = None` 的兜底删除会否产生 NoneType 错误？
  → 答：前置 grep 已确认 0 命中；如有遗漏，单测会暴露
- [可能 medium]：delegation_mode 与现有 turn_executor_kind 是否冗余？
  → 答：turn_executor_kind 粒度更粗（SELF/WORKER/SUBAGENT），delegation_mode
  细到 main_inline vs main_delegate；F091 决定是否合并
- [可能 medium]：`_metadata_flag` helper 保留是否有死代码风险？
  → 答：grep 调用点，如其他地方仍用就保留；如仅 single_loop_executor 一处用，删

---

## 6. 串行依赖与可并行性

| Phase | 依赖前置 Phase | 文件冲突风险 |
|-------|---------------|-------------|
| 1 Butler | 无 | 极低（仅 startup_bootstrap.py + docs） |
| 2 D5 重命名 | 无（与 Phase 1 文件不重叠） | 低（orchestrator.py 模型改 class 名） |
| 3 D2 kind | 无（agent_context.py 加字段，与 Phase 1 normalize 函数不重叠） | 低 |
| 4 D1 RuntimeControlContext | **可与 Phase 2 串行（都改 orchestrator.py 模型层）** | 中（Phase 2 改 class WorkerSession，Phase 4 改 RuntimeControlContext，不同 class） |

**严格串行执行**——避免 rebase 冲突 + 让每 Phase 在干净 baseline 上跑回归。

## 7. 总改动量级（拍板 A/A/A 后）

| Phase | 文件 | 行数 | 风险 |
|-------|------|------|------|
| 1 Butler | ~2 | ~50 行删除 + 5 行 docs | 极低 |
| 2 D5 重命名 | 6+2 | ~17 处替换 | 低 |
| 3 D2 kind | ~3 | ~10 行新增 + 5 行 fallback + 30 行测试 | 中 |
| 4 D1 RuntimeContext 扩展 | ~3 | ~30 行核心 + 5 行字段 + 50 行测试 | 高 |
| **总计** | **~16 文件** | **~200 行（含测试）** | |

## 8. 每 Phase commit message 模板

```
refactor(F090-Phase{N}): {Phase 标题}

- 变更说明（1-3 个 bullet）
- 影响面：{N 文件 / M 行}
- 验证：全量回归 PASS / e2e_smoke PASS

Codex adversarial review: {N high / M medium / K low ignored}
- 处理 finding 1: <一句话>
- 处理 finding 2: <一句话>
- low ignored: <列表>

Phase 1 baseline: ff4635d (master HEAD)
```

## 9. 失败回退策略

每 Phase 失败处理：

| 失败场景 | 处理 |
|---------|------|
| 中间残留扫描失败 | 修复 grep 报的命中点，再次扫描 |
| 类型检查失败 | 修复类型错误，不放过 |
| 全量回归出现 regression | 用 `git diff` + 单测定位，**不能"先 commit 再说"** |
| e2e_smoke 失败 | 必修。可能是 RuntimeControlContext 字段写入/读取语义偏差 |
| Codex review 报 high | 必处理，不允许 commit |
| Phase commit 后发现回归 | `git revert <commit>` 而不是 `git reset --hard`（保留历史），重做 |

## 10. F091 接口点说明

F090 完成后给 F091（State Machine Unification）的接口点：

1. **新增的 `delegation_mode` Literal**：F091 决定是否升格为正式 enum
   （StrEnum）并与 TurnExecutorKind 关系建模
2. **保留的 WorkerRuntimeState**：F091 范围内，与 TaskStatus / WorkerExecutionStatus /
   WorkStatus / AgentSessionStatus 建嵌套关系 + 单向映射函数
3. **保留的 AgentSessionStatus**：F091 范围内
4. **AgentProfile.kind Literal**：未来若需在 F091/F107 升格 enum，已有锚点
