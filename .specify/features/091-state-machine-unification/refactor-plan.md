# F091 Refactor Plan — State Machine Unification + F090 残留收尾

生成时间：2026-05-06
基线 commit：`fd70703` (master HEAD = F090 Phase 4)
worktree：`.claude/worktrees/F091-state-machine-unification`
分支：`feature/091-state-machine-unification`

## 0. 执行原则

| 原则 | 含义 |
|------|------|
| **行为零变更** | 纯类型/状态/读取改造，运行时行为必须 100% 等价 |
| **每 Phase 独立 commit** | 每 Phase 完成 → 全量回归 → Codex review → commit |
| **不 push origin/master** | 全部 Phase 完成后给用户归总报告 + 等拍板 push |
| **失败回退** | 中间验证失败 → 暂停 → 用户选 A) 修复继续 / B) 回滚此 Phase / C) 中止 |
| **不锁日历** | 节奏按实际推进，不预设完成时间 |
| **每 Phase commit 前必走 Codex adversarial review** | per-Phase finding 闭环（high/medium 必处理）|
| **最后 Phase 前必走 Final cross-Phase Codex review** | 输入 refactor-plan + 全部 Phase commit diff |
| **完成时必产出 completion-report.md** | 实际做了 vs 计划对照表，Phase 跳过显式归档理由 |

## 1. Phase 切分（4 Phase 严格串行）

按"先简后难 / 先独立后耦合"排序：

```
Phase B: Butler migration 死函数清理（最简单，建立 baseline 信心，与其他 Phase 文件不重叠）
   ↓
Phase A: 状态枚举统一（纯加映射函数，不改现有调用方）
   ↓
Phase C: metadata 读取端切换 runtime_context（核心改造，最大改动量）
   ↓
Phase D: Phase 4 medium finding 闭环（依赖 Phase C 切换完，写入端补全才有意义）
```

**串行依赖说明**：
- Phase B 与 Phase A/C/D 文件零重叠 → 独立 commit
- Phase A 加映射函数不改调用方 → 与 Phase C/D 互不影响（A 的映射函数 F091 内不被 C/D 调用，仅供后续 Feature 使用）
- Phase C 改读取端切换到 runtime_context → 是 Phase D 修复 medium 的前置（D 写入端补全才有意义）
- Phase D 修复后回归 medium finding 闭环

---

## 2. Phase B：Butler migration 死函数清理（F090 Phase 1 漏做项）

### 2.1 范围

详见 [impact-report.md §2](impact-report.md#2-块-bbutler-migration-死函数清理f090-phase-1-漏做项)。

**删除**：
- `_migrate_butler_naming(conn)` 函数体 [startup_bootstrap.py:329-334](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:329)
- `_migrate_butler_suffix(store_group, agent_profile)` 函数体 [startup_bootstrap.py:337-369](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:337)
- 调用点 [startup_bootstrap.py:62, :65](octoagent/apps/gateway/src/octoagent/gateway/services/startup_bootstrap.py:62) + 上方注释

**修订**：
- `agent_context.py:88, :102` docstring 去掉 `_migrate_butler_naming` 提及
- `docs/design/octoagent-architecture.md:37, :186` "Butler Direct" → "Main Direct"

**保留**：
- `normalize_runtime_role()` / `normalize_session_kind()` 函数本体（数据防御层）
- `test_migration_063.py` fixture

### 2.2 前置检查（已做，记录在 impact-report §2.1）

`~/.octoagent/data/sqlite/octoagent.db` 三表 butler 残留 = 0；其他两实例不存在。

### 2.3 执行步骤

1. 删 `_migrate_butler_naming` / `_migrate_butler_suffix` 函数体
2. 删调用点（startup_bootstrap.py L60-66 区域）+ 上方注释
3. 改 normalize_runtime_role / normalize_session_kind docstring
4. 改 docs/design/octoagent-architecture.md 2 处术语
5. **中间验证**：
   - `grep -rn "_migrate_butler" octoagent/apps octoagent/packages` 应为 0 命中
   - `grep -rn "Butler Direct" docs/` 应为 0 命中
   - `grep -rn "_migrate_butler_naming" octoagent/` 应为 0 命中（含 docstring 引用）
   - 类型检查 PASS
6. 全量回归：`make test` 比对 F090 Phase 4 baseline (fd70703)
7. e2e_smoke：`octo e2e --smoke` 或 pre-commit hook 跑
8. **Codex adversarial review**（per-Phase）
9. 处理 finding（high/medium 必处理）
10. commit（含 Codex review 闭环说明）

### 2.4 验收

- [ ] startup_bootstrap.py butler migration 函数体 0 命中
- [ ] startup_bootstrap.py 调用点 0 命中
- [ ] agent_context.py docstring 不再引用 `_migrate_butler_naming`
- [ ] docs/design/octoagent-architecture.md "Butler Direct" 0 命中
- [ ] normalize_runtime_role / normalize_session_kind 函数体保留
- [ ] test_migration_063.py 仍 PASS
- [ ] 全量回归 0 regression vs F090 Phase 4 baseline (fd70703)
- [ ] e2e_smoke PASS
- [ ] Codex review 闭环（0 high 残留）

### 2.5 改动量级

~3 production 文件 + 1 docs / ~50 行删除 + ~6 行 docstring 修订 + ~2 行 docs 修订

### 2.6 可能 Codex finding 预测

- [可能 medium]：删除 docstring 中的 `_migrate_butler_naming` 引用是否影响 grep-driven 调试？
  → 答：保留 normalize 函数本体的语义说明（"butler → MAIN 历史兼容"），仅删除已不存在函数名的字面引用
- [可能 low]：是否考虑删 normalize_runtime_role 的 fallback 路径？
  → 答：保留——数据防御层，无成本，遇到极端外部数据可兜底；F107 完全合并时再评估

---

## 3. Phase A：状态枚举统一（D3 主责）

### 3.1 范围

详见 [impact-report.md §1](impact-report.md#1-块-a状态枚举统一d3-主责)。

**新增映射**（在 `delegation.py`，沿用 pipeline_tool.py 的 module-level dict 模式）：
- `TASK_TO_WORK_STATUS` dict + `task_status_to_work_status()` 函数
- `WORK_TO_TASK_STATUS` dict + `work_status_to_task_status()` 函数
- `WORKER_TO_WORK_STATUS` dict + `worker_state_to_work_status()` 函数
- `WORKER_TO_TASK_STATUS` dict + `worker_state_to_task_status()` 函数

**导出**：models/__init__.py 加 4 个映射函数和 4 个映射 dict 到 `__all__`

**单测**：tests/test_state_machine_mappings_f091.py 新建
- 每个映射 dict 完整性（`set(_*MAP.keys()) == set(SourceEnum)`）
- 终态保持映射（终态 → 终态，对应映射）
- round-trip 一致性（task→work→task 在终态 + RUNNING/CREATED/CANCELLED 等保持等价）
- `WorkStatus.MERGED → TaskStatus.SUCCEEDED` / `WorkStatus.ESCALATED → TaskStatus.FAILED` 等显式 case
- WorkerRuntimeState 6 值全部映射不漏

### 3.2 设计决策

**Q：映射放 delegation.py 还是 enums.py？**
A：放 `delegation.py`。理由：
- WorkStatus 在 delegation.py 定义；TaskStatus/WorkerRuntimeState 来自 enums.py/orchestrator.py
- 集中跨枚举映射在 WorkStatus 邻接，单测一处覆盖
- 与 pipeline_tool.py 现有 `_PIPELINE_TO_TASK_STATUS` 模式一致（在使用方就近定义）
- 不在 enums.py：避免 enums.py 反向 import delegation.py

**Q：映射函数加在哪个 commit / 谁先用？**
A：F091 内**仅定义 + 单测**，不改任何现有调用方。后续 Feature（F092 DelegationPlane / F093 Worker Session Parity 等）按需使用。Phase A 是"为后续重构铺路"，不修改运行时行为。

**Q：是否需要反向映射（如 `task_status_to_worker_state`）？**
A：F091 不需要。运行时方向是 worker→task / pipeline→task / pipeline→work，反向方向（如 task→worker）实际无业务用途，留 future-proof 不做。

**Q：MERGED → SUCCEEDED 的语义合理吗？**
A：**Yes**——Work merged 表示该 delegation 已被合并到父 work（不是失败而是优化），从 task 角度等同 succeeded。如果以后语义变了，单测会捕获。

**Q：DELETED → CANCELLED 合理吗？**
A：**Yes**——work_store 删除 work 通常是 "cleanup retried failed"，task 视角看就是 cancelled。但 task 一旦 SUCCEEDED 不会再 DELETE（终态规则），所以不会冲突。

### 3.3 执行步骤

1. 改 [delegation.py](octoagent/packages/core/src/octoagent/core/models/delegation.py)：在 `WORK_TERMINAL_STATUSES` 之后加 4 个映射 dict + 4 个映射函数
2. 改 [models/__init__.py](octoagent/packages/core/src/octoagent/core/models/__init__.py)：export 新映射函数和 dict
3. 新建 `octoagent/packages/core/tests/test_state_machine_mappings_f091.py`：单测覆盖完整性 + round-trip
4. **中间验证**：
   - 类型检查 PASS（dict[Enum, Enum] 注解 + 函数签名）
   - `pytest octoagent/packages/core/tests/test_state_machine_mappings_f091.py` PASS
5. 全量回归：`make test` 比对 Phase B baseline
6. e2e_smoke：仍 PASS（无运行时改动）
7. **Codex adversarial review**（per-Phase）
8. 处理 finding（high/medium 必处理）
9. commit（含 Codex review 闭环说明）

### 3.4 验收

- [ ] `delegation.py` 加 4 个映射 dict + 4 个映射函数
- [ ] `models/__init__.py` export 新增成员
- [ ] 单测覆盖：4 个映射 dict 完整性 + round-trip + 显式 edge case
- [ ] WorkerRuntimeState 全部 6 值映射不漏
- [ ] WorkStatus 全部 13 值映射不漏（包括 MERGED, ESCALATED, DELETED）
- [ ] TaskStatus 全部 10 值映射不漏（包括 REJECTED, QUEUED, PAUSED）
- [ ] 全量回归 0 regression vs Phase B commit
- [ ] e2e_smoke PASS
- [ ] Codex review 闭环

### 3.5 改动量级

2 文件 + 1 测试 / ~80 行新增（dict + 函数）+ ~10 行 export + ~100 行测试

### 3.6 可能 Codex finding 预测

- [可能 high]：是否考虑 `WorkerRuntimeState.PENDING → TaskStatus.RUNNING` 的合理性（pending 状态下 task 实际还没真跑）？
  → 答：从用户视角看 PENDING 即 "task 已派发等待执行" = RUNNING（用户不需要区分微观状态）；F093 Worker Session Parity 时再细化
- [可能 medium]：4 个映射 dict 是否应该用 frozendict 防止运行时被改？
  → 答：Python 的 dict 是 mutable，但 module-level dict 约定不被修改；pipeline_tool.py 已有同样模式可参考；保持一致性
- [可能 medium]：是否需要 `TASK_TERMINAL_TO_WORK_TERMINAL` 等专门"终态→终态"映射函数？
  → 答：现有映射函数已覆盖终态情况（终态值映射到终态值），加专门函数会重复

---

## 4. Phase C：metadata 读取端切换 runtime_context（F090 D1 收尾）

### 4.1 范围

详见 [impact-report.md §3](impact-report.md#3-块-cmetadata-读取端切换-runtime_contextf090-d1-收尾)。

**8 处生产改造**：
- llm_service.py L218 删 `supports_single_loop_executor` 类属性
- llm_service.py L375 reader → 优先读 runtime_context.delegation_mode
- task_service.py L1044 reader → 优先读 runtime_context.recall_planner_mode == "skip"
- orchestrator.py L761 reader → 优先读 runtime_context.delegation_mode == "main_inline"
- orchestrator.py L877 删 getattr guard（属性永远 True）
- orchestrator.py L1017 reader → 优先读 runtime_context.delegation_mode
- orchestrator.py L1402 删 getattr guard
- orchestrator.py L1399 docstring 改写

**新增 helper**：
- 在 task_service / runtime_control 模块加 `_is_single_loop_main(runtime_context, metadata) -> bool` 和 `_is_recall_planner_skip(runtime_context, metadata) -> bool` helpers
- runtime_context 优先；fallback 到 metadata flag

**保留**：
- 写入端 metadata flag 不删（F100 删）
- `_metadata_flag` helper 三处定义保留（generic 用途）

### 4.2 设计决策

**Q：helper 放在哪个模块？**
A：放 [runtime_control.py](octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py)（已有，是 RuntimeControlContext 序列化的统一位置）。理由：
- 跨 task_service / llm_service / orchestrator 共用
- 与 `runtime_context_from_metadata` / `encode_runtime_context` 邻接
- 单一来源，不会三处复制

**Q：fallback 何时退化？**
A：F100 完全切单轨时（写入端 metadata flag 删除后）。F091 必须保留 fallback 因为：
- 写入端仍写双轨（runtime_context + metadata）
- 部分历史 metadata 可能在测试 / mock 路径不带 runtime_context
- 单测应同时覆盖"runtime_context 路径"和"metadata fallback 路径"

**Q：runtime_context = None 时怎么办？**
A：`_is_single_loop_main(runtime_context=None, metadata=...)` 应直接走 metadata fallback 不报错。理由：
- 部分单测构造 OrchestratorRequest 时 runtime_context=None
- F090 commit message 已说明 chat 路径有 metadata["runtime_context_json"] 透传
- F091 范围内不强制 runtime_context 必填（F100 评估）

**Q：delegation_mode == "unspecified" 时怎么办？**
A：fallback 到 metadata flag。理由：
- "unspecified" 是 F090 D1 的兼容期默认值
- standard delegation 路径未写时（F091 块 D 修复前）该字段为 unspecified
- 兼容期不强制语义

### 4.3 执行步骤

1. 在 [runtime_control.py](octoagent/apps/gateway/src/octoagent/gateway/services/runtime_control.py) 加 `_is_single_loop_main` / `_is_recall_planner_skip` helpers
2. 改 llm_service.py L218 删 `supports_single_loop_executor` 类属性
3. 改 llm_service.py L375 reader 用 helper
4. 改 task_service.py L1044 reader 用 helper
5. 改 orchestrator.py L761 / L1017 reader 用 helper
6. 改 orchestrator.py L877 / L1402 删 getattr guard（直接走主路径）
7. 改 orchestrator.py L1399 docstring
8. 新建/扩展 tests/test_runtime_control_f091.py 单测
9. **中间验证**：
   - `grep -rn "supports_single_loop_executor" octoagent/` 0 命中
   - `grep -rn 'getattr(self._llm_service, "supports_single_loop_executor"' octoagent/` 0 命中
   - 类型检查 PASS
   - `pytest octoagent/apps/gateway/tests/test_runtime_control_f091.py` PASS
10. 全量回归：`make test` 比对 Phase A baseline
11. e2e_smoke：必过（特别关注 single_loop 主路径稳定）
12. **Codex adversarial review**（per-Phase，特别关注 fallback 行为零变更）
13. 处理 finding（high/medium 必处理）
14. commit（含 Codex review 闭环说明）

### 4.4 验收

- [ ] llm_service.py L218 类属性已删除
- [ ] llm_service.py L375 reader 改读 runtime_context（优先）+ metadata（fallback）
- [ ] task_service.py L1044 reader 改读 recall_planner_mode == "skip"（优先）+ metadata（fallback）
- [ ] orchestrator.py L761 / L1017 reader 改读 delegation_mode（优先）+ metadata（fallback）
- [ ] orchestrator.py L877 / L1402 getattr guard 已删除
- [ ] orchestrator.py L1399 docstring 已改写
- [ ] runtime_control.py 加 `_is_single_loop_main` / `_is_recall_planner_skip` helpers
- [ ] 单测覆盖：runtime_context 路径 / metadata fallback 路径 / runtime_context=None 路径 / delegation_mode="unspecified" 路径
- [ ] 全量回归 0 regression vs Phase A commit
- [ ] e2e_smoke PASS（5x 循环验证 single_loop 主路径稳定）
- [ ] Codex review 闭环

### 4.5 改动量级

~4 production 文件 + 1 helper 文件 + 1 测试 / ~40 行核心改造 + ~30 行 helper + ~80 行测试

### 4.6 可能 Codex finding 预测

- [可能 high]：fallback 路径若 runtime_context.delegation_mode 是 "main_delegate" 但 metadata flag 是 True（不可能但…），怎么处理？
  → 答：runtime_context 优先生效；fallback 仅当 runtime_context 字段 = "unspecified" 才查 metadata；测试覆盖此场景
- [可能 high]：删除 `supports_single_loop_executor` 类属性是否影响其他 LLMService 子类（如测试 mock）？
  → 答：grep 子类如有 override，需要一并删除；单测里如有 monkey-patch 该属性的，需更新
- [可能 medium]：getattr guard 删除后单测如有 mock 不支持 single_loop 的 LLMService，怎么走？
  → 答：单测应改用 dependency injection 而非 attribute mock；如有破坏的测试，加 ApiNotSupportedError 等显式异常路径
- [可能 medium]：双 helper（_is_single_loop_main / _is_recall_planner_skip）语义重叠太多？
  → 答：F091 保持两个 helper（语义不同：执行模式 vs recall 行为）；F100 启用 RecallPlannerMode "auto" 实际语义后再评估合并

---

## 5. Phase D：F090 Phase 4 medium finding 闭环

### 5.1 范围

详见 [impact-report.md §4](impact-report.md#4-块-df090-phase-4-medium-finding-闭环)。

**Medium #1 修复**：
- `_prepare_single_loop_request` short-circuit 路径（[orchestrator.py:761](octoagent/apps/gateway/src/octoagent/gateway/services/orchestrator.py:761)）补 runtime_context 同步
- 在 short-circuit return 前先检查 runtime_context.delegation_mode，若 ≠ "main_inline" → 调 `_with_delegation_mode` patch 后再 return

**Medium #2 修复**：
- `_build_runtime_context` ([delegation_plane.py:838](octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py:838)) 加 `delegation_mode: DelegationMode` 参数
- 调用方（L147）根据 `target_kind` 推断：
  - `DelegationTargetKind.SUBAGENT` → `"subagent"`
  - `DelegationTargetKind.WORKER` / `ACP_RUNTIME` / `GRAPH_AGENT` / `FALLBACK` → `"main_delegate"`

### 5.2 设计决策

**Q：worker_inline 路径在哪写？**
A：grep `WorkerRuntimeState` 调用链（worker_runtime.py），核查 worker 是否在 dispatch 内部构造新 RuntimeControlContext。**实测 worker_runtime.py 内部不构造新 RuntimeControlContext，沿用 dispatch envelope 的**。所以 worker_inline 写入路径在 F091 范围内**没有需要改造的位置**。F098 A2A Mode + Worker↔Worker 时再处理。

**Q：medium #1 修复与 Phase C 的关系？**
A：Phase C 完成后 reader 优先读 runtime_context.delegation_mode；medium #1 的 short-circuit 路径之所以未补 patch 是因为 reader 当时仍读 metadata（兼容期）。Phase C 切换后：
- 若 short-circuit 仍跑：runtime_context.delegation_mode 可能是 "unspecified" → fallback metadata flag → 仍走 single_loop（行为零变更）
- 但语义上不对称：写入端两条路径只有一条同步 runtime_context
- Phase D 修复让两条路径都同步 runtime_context，消除"半成品"状态

**Q：DelegationTargetKind 5 值如何精确映射 delegation_mode？**
A：
- `WORKER` → `"main_delegate"`（最常见）
- `SUBAGENT` → `"subagent"`
- `ACP_RUNTIME` → `"main_delegate"`（ACP 仍是主 Agent 派给外部 worker 性质）
- `GRAPH_AGENT` → `"main_delegate"`（同上）
- `FALLBACK` → `"main_delegate"`（fallback 路径仍属主派发）

worker_inline 不在此枚举（属于 worker 内部自跑）— F091 范围内无写入点。

### 5.3 执行步骤

1. 改 orchestrator.py `_prepare_single_loop_request` short-circuit 前补 runtime_context patch
2. 改 delegation_plane.py `_build_runtime_context` 加 delegation_mode 参数
3. 改 delegation_plane.py L147 调用补传 delegation_mode（基于 target_kind 推断）
4. 加 helper `_delegation_mode_for_target_kind(target_kind: DelegationTargetKind) -> DelegationMode` 在 delegation_plane.py
5. 扩展 tests/test_delegation_plane_f091.py：
   - standard delegation 写入 delegation_mode 验证
   - subagent 路径写入 "subagent"
   - main_inline short-circuit 后 runtime_context 已 patch
6. **中间验证**：
   - 类型检查 PASS
   - 单测 PASS
   - `grep -rn "delegation_mode=" octoagent/apps/gateway/src/octoagent/gateway/services/` 至少 3 个写入点（_prepare_single_loop_request / _build_runtime_context / short-circuit patch）
7. 全量回归：`make test` 比对 Phase C baseline
8. e2e_smoke：必过
9. **Codex adversarial review**（per-Phase，特别关注 medium 闭环完整性）
10. 处理 finding（high/medium 必处理）
11. commit（含 Codex review 闭环说明）

### 5.4 验收

- [ ] `_prepare_single_loop_request` short-circuit 前 runtime_context 已 patch
- [ ] `_build_runtime_context` 加 delegation_mode 参数
- [ ] `_build_runtime_context` 调用方按 target_kind 写入正确 delegation_mode
- [ ] subagent 路径写入 "subagent"，worker 路径写入 "main_delegate"
- [ ] F090 Phase 4 commit message 标记的 2 条 medium finding 全部闭环
- [ ] 单测覆盖各 delegation 路径写入正确
- [ ] 全量回归 0 regression vs Phase C commit
- [ ] e2e_smoke PASS
- [ ] Codex review 闭环

### 5.5 改动量级

~2 production 文件 + 1 测试 / ~15 行核心改造 + ~50 行测试

### 5.6 可能 Codex finding 预测

- [可能 high]：worker_inline 路径在 F091 不写是否会让 worker 自跑场景的 delegation_mode 永远是 "unspecified"？
  → 答：grep 确认 worker_runtime 不构造新 RuntimeControlContext，沿用 envelope 透传；envelope 的 delegation_mode 由 dispatcher 写入；worker_inline 实际由调用方决定（不归 _build_runtime_context 管）；F098 评估
- [可能 medium]：`_delegation_mode_for_target_kind` helper 是否过度抽象？
  → 答：5 个 case 用 helper 封装比 inline 5 行 if-else 清晰；后续 F092 可演进
- [可能 medium]：FALLBACK / ACP_RUNTIME / GRAPH_AGENT 都映射 "main_delegate" 是否需要新增 DelegationMode 值？
  → 答：F091 不扩 enum；保持现有 5 值（unspecified/main_inline/main_delegate/worker_inline/subagent）；细分推迟 F092 评估

---

## 6. 串行依赖图

| Phase | 依赖前置 | 文件冲突风险 | 与其他 Phase 共享文件 |
|-------|---------|------------|---------------------|
| B Butler | F090 baseline | 极低 | 无（仅 startup_bootstrap.py + agent_context.py docstring + docs）|
| A 映射函数 | Phase B（不严格依赖，但建议串行） | 极低 | 无（仅加 delegation.py + 测试）|
| C 读取切换 | Phase A | 中 | orchestrator.py / llm_service.py / task_service.py / runtime_control.py |
| D medium 闭环 | Phase C | 中 | orchestrator.py / delegation_plane.py |

**严格串行执行**——避免 rebase 冲突 + 让每 Phase 在干净 baseline 上跑回归。

---

## 7. 总改动量级

| Phase | 文件 | 行数 | 风险 |
|-------|------|------|------|
| B Butler 清理 | ~4 | ~50 删除 + ~8 修订 | 极低 |
| A 映射函数 | 2+1 | ~80 新增 + ~10 export + ~100 测试 | 低 |
| C 读取切换 | ~5+1 | ~40 改造 + ~30 helper + ~80 测试 | 中 |
| D medium 闭环 | 2+1 | ~15 改造 + ~50 测试 | 低 |
| **总计** | **~14 文件** | **~470 行（含 ~230 测试）** | |

---

## 8. 每 Phase commit message 模板

```
refactor(F091-Phase{B|A|C|D}): {Phase 标题}

- 变更说明（1-3 个 bullet）
- 影响面：{N 文件 / M 行}
- 验证：全量回归 PASS / e2e_smoke PASS

Codex adversarial review: {N high / M medium / K low ignored}
- 处理 finding 1: <一句话>
- 处理 finding 2: <一句话>
- low ignored: <列表>

Phase {prev} baseline: {prev_commit_short}
F090 baseline: fd70703
```

---

## 9. 失败回退策略

| 失败场景 | 处理 |
|---------|------|
| 中间残留扫描失败 | 修复 grep 报的命中点，再次扫描 |
| 类型检查失败 | 修复类型错误，不放过 |
| 全量回归出现 regression | 用 `git diff` + 单测定位，**不能"先 commit 再说"** |
| e2e_smoke 失败 | 必修。可能是 runtime_context 字段读取/fallback 语义偏差 |
| Codex review 报 high | 必处理，不允许 commit |
| Phase commit 后发现回归 | `git revert <commit>` 而不是 `git reset --hard`（保留历史），重做 |

---

## 10. F092 接口点说明

F091 完成后给 F092（DelegationPlane Unification）的接口点：

1. **新增的 4 个映射函数**（Phase A）：F092 整合 dispatch 路径时使用，不必再每处定义 case 转换
2. **`_with_delegation_mode` (orchestrator) + `_build_runtime_context` (delegation_plane)**：F092 应合并到 DelegationPlane 单一入口
3. **`_delegation_mode_for_target_kind` helper**（Phase D）：F092 评估升格为 DelegationPlane 内部 contract
4. **runtime_context.delegation_mode 写入路径完整性**：F092 验证 main_inline / main_delegate / worker_inline / subagent 四路写入路径全部走单一入口
5. **`_is_single_loop_main` / `_is_recall_planner_skip` helpers**（Phase C）：F100 删除 fallback 时影响这两个 helper

---

## 11. Final cross-Phase Codex Review（强制）

在 Phase D commit 前必须做一次 Final Codex review：

**输入**：
- `refactor-plan.md`（本文件）
- 全部 Phase commit diff（B + A + C + D）

**专门检查**：
- 是否漏 Phase / 是否偏离原计划但未在 commit 说明
- 跨 Phase 是否有"半成品"状态遗留
- 4 块的验收 checklist 是否全部完成
- F090 Phase 4 标记的 2 条 medium finding 是否真闭环
- 块 A 加的映射函数是否有 import 循环风险

**通过后**：
- 写 [completion-report.md](completion-report.md)（实际做了 vs 计划对照表 + Codex finding 闭环表 + 给用户的归总报告）

---

## 12. completion-report 必含字段

按 CLAUDE.local.md "工作流改进" 强制要求：

- [ ] 每 Phase 实际做了 vs 计划对照表
- [ ] 任何"实际偏离原计划"的 Phase 必须显式归档（理由 + 影响 + 后续 Feature 接管）
- [ ] Phase 跳过显式归档（F091 不预计跳过任何 Phase）
- [ ] 全量 Codex finding 闭环统计（per-Phase + Final）
- [ ] 全量回归对照（passed 数 vs F090 baseline fd70703）
- [ ] e2e_smoke 实测记录
- [ ] 给用户的归总报告（解决的问题 / 风险 / 建议合入 master 与否）
- [ ] F092 接口点确认
