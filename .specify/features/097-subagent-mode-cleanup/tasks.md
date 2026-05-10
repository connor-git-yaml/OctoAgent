# F097 Subagent Mode Cleanup — Tasks（v0.1）

> 上游：[spec.md](spec.md) v0.2（GATE_DESIGN 已拍板）/ [plan.md](plan.md) v0.1 / [research/tech-research.md](research/tech-research.md)
>
> **Phase 顺序（plan §1 依赖图）**：0 → A → C → E → B → D → F → G → Verify

---

## 任务总览

| Phase | 描述 | 任务数 | 风险 |
|-------|------|--------|------|
| Phase 0 | 前置实测侦察 | 4 | 无（只读代码）|
| Phase A | SubagentDelegation Pydantic Model | 5 | 低 |
| Phase C | ephemeral AgentProfile（kind=subagent）| 5 | 中 |
| Phase E | Session cleanup hook + 幂等 | 5 | 中 |
| Phase B | `_ensure_agent_session` 增 SUBAGENT_INTERNAL 路径 | 6 | **高** |
| Phase D | RuntimeHintBundle caller→child 拷贝 | 4 | 低 |
| Phase F | Memory α 共享引用实施 | 6 | 中 |
| Phase G | BEHAVIOR_PACK_LOADED agent_kind=subagent 验证 | 4 | 低 |
| Verify | 全量回归 + e2e_smoke + Final Codex review | 7 | — |
| **合计** | | **46** | |

---

## 任务编号约定

- `T0.N` Phase 0 任务（前置侦察）
- `TA.N` Phase A 任务（SubagentDelegation model）
- `TC.N` Phase C 任务（ephemeral AgentProfile）
- `TE.N` Phase E 任务（session cleanup）
- `TB.N` Phase B 任务（SUBAGENT_INTERNAL session 路径）
- `TD.N` Phase D 任务（RuntimeHintBundle 拷贝）
- `TF.N` Phase F 任务（Memory α 共享引用）
- `TG.N` Phase G 任务（BEHAVIOR_PACK_LOADED 验证）
- `TVERIFY.N` Verify 阶段任务

每任务标记：
- 类型：`[code]` / `[test]` / `[review]` / `[verify]` / `[commit]` / `[recon]`
- 关联 AC：`(AC-X1, AC-X2)`
- 依赖：`(deps: TX.N)`
- 可并行：`[P]`（同 Phase 内与其他 task 无文件竞争）

---

## Phase 0：前置实测侦察（~30min）

**目的**：在开始实施前对关键路径做代码级确认，消除 Phase A-G 的不确定性。此 Phase 无 Codex review（纯读代码）。

**产出**：`.specify/features/097-subagent-mode-cleanup/phase-0-recon.md`（侦察结论归档）

### T0.1 `[recon]` SUBAGENT_COMPLETED 事件存在性确认

**描述**：grep 确认 `EventType.SUBAGENT_COMPLETED` 是否已在 enums.py 定义；若不存在，记录为 AC-EVENT-1 的实施前置。

**文件**：
- 读：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/packages/core/src/octoagent/core/models/enums.py`

**依赖**：—
**可并行**：是（[P]，与 T0.2/T0.3 无文件竞争）
**完成标准**：明确 SUBAGENT_COMPLETED 是否存在，结论写入 phase-0-recon.md

---

### T0.2 `[recon]` `_ensure_agent_session` / `_resolve_or_create_agent_profile` / `_ensure_memory_namespaces` 现状确认

**描述**：精确定位三个函数的起始行号、当前路径判断逻辑、subagent 路径缺失的确切位置；确认 tech-research §2.3 中 L2337-2345 的行号是否与当前 HEAD 一致。

**文件**：
- 读：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（line 2280-2550 范围）

**依赖**：—
**可并行**：是（[P]，与 T0.1/T0.3 无文件竞争）
**完成标准**：三个函数的精确行号 + 当前条件分支结构写入 phase-0-recon.md

---

### T0.3 `[recon]` RuntimeHintBundle 字段精确列表确认

**描述**：读取 `behavior.py:206`，列出 `RuntimeHintBundle` class 的全部字段名，确认 `surface` / `tool_universe` / `recent_failure_budget` 的实际字段名及类型；为 Phase D 拷贝逻辑提供精确字段列表。

**文件**：
- 读：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/behavior.py`（line 200-250 范围）

**依赖**：—
**可并行**：是（[P]，与 T0.1/T0.2 无文件竞争）
**完成标准**：RuntimeHintBundle 全字段清单写入 phase-0-recon.md；Phase D 实施时直接引用

---

### T0.4 `[recon]` F096 baseline 全量回归基准建立

**描述**：运行 `pytest -q --timeout=60` 记录 passed 总数，与 CLAUDE.local.md 记录的 3260 对比确认一致（或记录实际值作为 F097 baseline）；结论写入 phase-0-recon.md。

**命令**：`pytest -q --timeout=60 2>&1 | tail -5`

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/phase-0-recon.md`（汇总 T0.1-T0.4 所有侦察结论）

**依赖**：T0.1, T0.2, T0.3（同步写入同一文件）
**可并行**：否（须在 T0.1-T0.3 读完后汇总）
**完成标准**：phase-0-recon.md 存在，含 SUBAGENT_COMPLETED 存在性 + 三函数行号 + RuntimeHintBundle 字段 + baseline passed 数

---

## Phase A：SubagentDelegation Pydantic Model（~1h）

**目标 AC**：AC-A1, AC-A2, AC-A3
**User Story**：US1（Subagent 委托在 Audit Trail 中清晰可辨）

**依赖**：Phase 0 完成

---

### TA.1 `[code]` 新增 SubagentDelegation class

**描述**：在 `delegation.py` 中新增 `SubagentDelegation` Pydantic model，含所有必须字段（`delegation_id` / `parent_task_id` / `parent_work_id` / `child_task_id` / `child_agent_session_id` / `caller_agent_runtime_id` / `caller_project_id` / `caller_memory_namespace_ids` / `spawned_by` / `target_kind` / `created_at` / `closed_at`）+ `to_metadata_json()` / `from_metadata_json()` / `mark_closed()` 三个 helper。

**关联 AC**：AC-A1, AC-A2, AC-A3

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/packages/core/src/octoagent/core/models/delegation.py`
- 预期改动：+60 行（新增 class + helper methods）

**依赖**：T0.4（baseline 已确认）
**可并行**：否（TA.2 新建导出依赖此文件）
**完成标准**：`SubagentDelegation` class 可 import，`to_metadata_json` + `from_metadata_json` round-trip 不抛异常

---

### TA.2 `[code]` 导出 SubagentDelegation

**描述**：在 `packages/core` 的 models `__init__.py` 或对应导出文件中新增 `SubagentDelegation` 的 public export，确保 gateway 层可直接 import。

**关联 AC**：AC-A1

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/packages/core/src/octoagent/core/models/__init__.py`（或 delegation 相关导出文件）
- 预期改动：+2 行

**依赖**：TA.1
**可并行**：否
**完成标准**：`from octoagent.core.models import SubagentDelegation` 无 ImportError

---

### TA.3 `[test]` SubagentDelegation model 单测

**描述**：新建单测文件，覆盖：字段默认值校验（delegation_id 是 ULID 格式、target_kind 默认 SUBAGENT、closed_at 默认 None、child_agent_session_id 默认 None）；`to_metadata_json` + `from_metadata_json` round-trip（含 child_agent_session_id 字段）；`mark_closed` 返回新实例且原实例 closed_at 不变。

**关联 AC**：AC-A1, AC-A2, AC-A3

**文件**：
- 新建：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/packages/core/tests/test_subagent_delegation_model.py`
- 预期改动：+80 行

**依赖**：TA.1, TA.2
**可并行**：否
**完成标准**：`pytest packages/core/tests/test_subagent_delegation_model.py` 全通

---

### TA.4 `[review]` Phase A per-Phase Codex review（foreground）

**描述**：触发 `/codex:adversarial-review` foreground，输入 Phase A commit diff。关注点：SubagentDelegation 字段命名与 F098 WorkerDelegation 的兼容性；`child_agent_session_id` 默认 None 的语义是否合理；`to_metadata_json` / `from_metadata_json` 的异常路径。处理 high/medium finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-phase-a.md`。

**关联 AC**：AC-GLOBAL-3

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-phase-a.md`

**依赖**：TA.3
**可并行**：否
**完成标准**：0 high finding 残留；finding 闭环结果写入 codex-review-phase-a.md

---

### TA.5 `[commit]` Phase A commit

**描述**：commit 全部 Phase A 改动（delegation.py + __init__.py + test_subagent_delegation_model.py + codex-review-phase-a.md）。

**commit message 格式**：`feat(F097-Phase-A): SubagentDelegation Pydantic model + metadata round-trip + Codex review: N high / M medium 已处理 / K low ignored`

**依赖**：TA.4
**可并行**：否
**完成标准**：commit 存在于 feature/097-subagent-mode-cleanup 分支；pre-commit e2e_smoke PASS

---

## Phase C：ephemeral AgentProfile（kind=subagent）（~2h）

**目标 AC**：AC-C1, AC-C2
**User Story**：US1（Subagent 身份正确标记，audit trail 可辨）

**依赖**：Phase A 完成（ephemeral profile 需引用 SubagentDelegation 概念）

---

### TC.1 `[code]` `_resolve_or_create_agent_profile` 增 subagent 路径

**描述**：在 `agent_context.py` 的 `_resolve_or_create_agent_profile` 函数中新增 subagent 判断分支：当 `agent_runtime.delegation_mode == "subagent"` 时，创建 `AgentProfile(profile_id=str(ULID()), kind="subagent", scope=AgentProfileScope.PROJECT, ...)`，**不调用** `agent_context_store.save_agent_profile`（ephemeral，不写持久化表）。Phase 0 T0.2 确认的函数起始行号为注入点参考。

**关联 AC**：AC-C1, AC-C2

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- 预期改动：+40 行（新增 if 分支 + ephemeral profile 构造逻辑）

**依赖**：TA.5（Phase A 已 commit）
**可并行**：否
**完成标准**：`delegation_mode == "subagent"` 时构造并返回 kind=subagent 的 AgentProfile；不调用 save_agent_profile

---

### TC.2 `[test]` ephemeral AgentProfile 单测

**描述**：新建单测文件，覆盖：mock `agent_context_store.save_agent_profile`，断言 subagent 路径下**不调用**该方法；ephemeral profile 的 `profile_id` 是 ULID 格式；`kind == "subagent"`；`scope == PROJECT`；非 subagent 路径不影响（Worker 和 main 路径保持原有行为）。

**关联 AC**：AC-C1, AC-C2

**文件**：
- 新建：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_subagent_profile.py`
- 预期改动：+60 行

**依赖**：TC.1
**可并行**：否
**完成标准**：`pytest apps/gateway/tests/test_subagent_profile.py` 全通

---

### TC.3 `[test]` 全量回归（Phase C 后）

**描述**：`pytest -q --timeout=60` 必通（≥ Phase 0 baseline passed 数，0 regression）；`pytest -m e2e_smoke` 必通（8/8 PASS）。

**依赖**：TC.2
**可并行**：否
**完成标准**：回归 0 regression；e2e_smoke 8/8

---

### TC.4 `[review]` Phase C per-Phase Codex review（foreground）

**描述**：触发 `/codex:adversarial-review` foreground，输入 Phase A+C cumulative diff。关注点：ephemeral profile 与 Worker 路径是否完全隔离（`delegation_mode == "subagent"` 条件是否足够精确）；ULID profile_id 在运行时是否可能与持久化 profile 混淆；Phase C 改动是否引入对 build_task_context 主路径的意外副作用。处理 finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-phase-c.md`。

**关联 AC**：AC-GLOBAL-3

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-phase-c.md`

**依赖**：TC.3
**可并行**：否
**完成标准**：0 high finding 残留；finding 闭环结果写入 codex-review-phase-c.md

---

### TC.5 `[commit]` Phase C commit

**描述**：commit 全部 Phase C 改动（agent_context.py + test_subagent_profile.py + codex-review-phase-c.md）。

**commit message 格式**：`feat(F097-Phase-C): ephemeral AgentProfile kind=subagent 创建路径 + Codex review: N high / M medium 已处理 / K low ignored`

**依赖**：TC.4
**可并行**：否
**完成标准**：commit 存在；pre-commit e2e_smoke PASS

---

## Phase E：Session Cleanup Hook + 幂等（~2h）

**目标 AC**：AC-E1, AC-E2, AC-E3
**User Story**：US3（Subagent 完成后 Session 清洁关闭）

**依赖**：Phase A 完成（cleanup 需要 SubagentDelegation.from_metadata_json）；Phase B 完成后运行才有真实的 SUBAGENT_INTERNAL session 可 close——**但 Phase E 可在 Phase B 前实施**，cleanup 函数本身若无 SUBAGENT_INTERNAL session 则静默跳过（非 subagent task 直接 return）。

> **实施顺序说明（来自 plan §1）**：Phase E 在 Phase B 前实施。cleanup hook 在 task 无 `subagent_delegation` metadata 时立即 return，对当前 baseline 行为无影响。Phase B 完成后 cleanup 才真正被激活。

---

### TE.1 `[code]` `[x]` `_close_subagent_session_if_needed` 新增函数（含 SUBAGENT_COMPLETED emit 条件路径 — analysis F-01 修订）

**描述**：在 `task_runner.py` 中新增 `_close_subagent_session_if_needed(self, task_id: str, terminal_at: datetime, terminal_status: TaskStatus)` 异步函数。逻辑：1）从 task metadata 读取 `subagent_delegation` 字段，不存在则 return；2）反序列化为 `SubagentDelegation`；3）若 `child_agent_session_id` 为 None 则 return（spawn 失败场景）；4）若 `delegation.closed_at is not None` 则 return（幂等）；5）查 AgentSession，若 status != CLOSED 则 save 新实例（status=CLOSED, closed_at=terminal_at）；6）更新 task metadata 中的 `subagent_delegation.closed_at`（顺序写，非事务）；**7）emit SUBAGENT_COMPLETED 事件**——条件路径：(a) 若 T0.1 侦察发现 baseline 已有 `EventType.SUBAGENT_COMPLETED` 枚举且有 emit 调用 → 验证仍正确；(b) 若 T0.1 发现枚举/emit 不存在 → TE.1 同步补充：在 `events/enums.py` 新增 `SUBAGENT_COMPLETED` 枚举值 + 在 cleanup 函数末尾 emit（payload 含 `delegation_id` / `child_task_id` / `terminal_status` / `closed_at`）。Session CLOSED 状态迁移由此事件覆盖（满足 Constitution C2，参见 spec AC-EVENT-1 / analysis F-07）。try-except 隔离：cleanup 失败时 log warn，不影响主流程。

**关联 AC**：AC-E1, AC-E2, AC-E3, **AC-EVENT-1（条件路径）**

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`
- 预期改动：+30 行（新增函数）

**依赖**：TA.5（SubagentDelegation 可 import）
**可并行**：否
**完成标准**：函数存在且通过 import 检查；非 subagent task 调用时静默 return

---

### TE.2 `[code]` `[x]` 在 `_notify_completion` 中调用 cleanup hook

**描述**：在 `task_runner.py` 的 `_notify_completion`（plan 记录 line 632）中，task 进入终态后调用 `await self._close_subagent_session_if_needed(task_id, terminal_at)`。确保调用在 completion notifier 之后，异常不传播到主流程。

**关联 AC**：AC-E1

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`
- 预期改动：+5 行（调用点 + try-except）

**依赖**：TE.1
**可并行**：否
**完成标准**：`_notify_completion` 在终态后调用 cleanup hook

---

### TE.3 `[test]` `[x]` session cleanup 单测

**描述**：新建单测文件，覆盖：mock stores，测试 cleanup 被调用两次时幂等（closed_at 保持首次值，不被第二次调用覆盖）；非 subagent task（无 `subagent_delegation` 字段）时 cleanup 直接 return 不报错；`child_agent_session_id` 为 None 时跳过；cleanup 内部异常不传播到主流程（try-except 隔离验证）。

**关联 AC**：AC-E1, AC-E2, AC-E3

**文件**：
- 新建：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_subagent_cleanup.py`
- 预期改动：+80 行

**依赖**：TE.2
**可并行**：否
**完成标准**：`pytest apps/gateway/tests/test_subagent_cleanup.py` 全通

---

### TE.4 `[review]` Phase E per-Phase Codex review（foreground）

**描述**：触发 `/codex:adversarial-review` foreground，输入 Phase A+C+E cumulative diff。关注点：cleanup 挂载在 `_notify_completion` 内的异常处理是否足够；`update_task_metadata` 在 SQLite WAL 下的顺序写入是否安全；cleanup 失败的可观测性（日志级别是否合适）；幂等实现是否覆盖进程重启场景。处理 finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-phase-e.md`。

**关联 AC**：AC-GLOBAL-3

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-phase-e.md`

**依赖**：TE.3
**可并行**：否
**完成标准**：0 high finding 残留；finding 闭环结果写入 codex-review-phase-e.md

---

### TE.5 `[commit]` Phase E commit

**描述**：commit 全部 Phase E 改动（task_runner.py + test_subagent_cleanup.py + codex-review-phase-e.md）。

**commit message 格式**：`feat(F097-Phase-E): Subagent session cleanup hook + 幂等保护 + Codex review: N high / M medium 已处理 / K low ignored`

**依赖**：TE.4
**可并行**：否
**完成标准**：commit 存在；pre-commit e2e_smoke PASS

---

## Phase B：`_ensure_agent_session` 增 SUBAGENT_INTERNAL 路径（~3h，最高风险）

**目标 AC**：AC-B1, AC-B2
**User Story**：US1（SUBAGENT_INTERNAL session 使 audit trail 可区分）

**依赖**：Phase A + Phase C + Phase E 完成

> **风险注意**：本 Phase 是 F097 最高风险 Phase（修改 `_ensure_agent_session` session 创建路径），Codex review 选择 **background** 模式。

---

### TB.1 `[code]` `[x]` `_ensure_agent_session` 增第 4 路（SUBAGENT_INTERNAL）

**描述**：在 `agent_context.py` 的 `_ensure_agent_session`（line 2318 附近，以 T0.2 侦察结果为准）新增第 4 路判断：`if agent_runtime.delegation_mode == "subagent"` → 创建 `kind=SUBAGENT_INTERNAL` 的 AgentSession，`parent_worker_runtime_id` 从 `agent_runtime` 的 control metadata 中读取 caller runtime ID。同时，session 创建成功后，更新 task metadata 中 `SubagentDelegation.child_agent_session_id` 字段（C-1 决策）。新路径放在现有 3 路判断**之前**（优先匹配，避免 fallback 到 WORKER_INTERNAL）。

**关联 AC**：AC-B1, AC-B2

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- 预期改动：+25 行

**依赖**：TE.5（Phase E 已 commit）
**可并行**：否
**完成标准**：`delegation_mode == "subagent"` 时创建 SUBAGENT_INTERNAL session 并写回 child_agent_session_id；现有 3 路条件判断逻辑不改变

---

### TB.2 `[code]` `[x]` `_update_subagent_delegation_session_id` helper（或内联逻辑）

**描述**：实现将新建 session 的 `agent_session_id` 写回 task metadata 中 `SubagentDelegation.child_agent_session_id` 的逻辑（可内联在 TB.1 的 if 块内，也可提取为独立 private helper）。写入路径：从 agent_runtime 取 task_id → 读 task metadata → 反序列化 SubagentDelegation → mark child_agent_session_id → 重新序列化 → update_task_metadata。

**关联 AC**：AC-B1, AC-A1（child_agent_session_id 字段真实填充）

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`（与 TB.1 同文件，+15 行）
- 或拆为 `delegation_plane.py` / `capability_pack.py` 中的 spawn 后写入（由 T0.2 侦察结果决定最佳注入点）

**依赖**：TB.1
**可并行**：否
**完成标准**：spawn 后 task metadata 中 `subagent_delegation.child_agent_session_id` 被填充为实际 session ID

---

### TB.3 `[test]` `[x]` `_ensure_agent_session` 路径单测

**描述**：新建或扩展测试文件，覆盖：`delegation_mode == "subagent"` 触发 SUBAGENT_INTERNAL 路径，session kind 正确；`parent_worker_runtime_id` 正确填充；`child_agent_session_id` 写回 task metadata；现有 3 路（DIRECT_WORKER / WORKER_INTERNAL / MAIN_BOOTSTRAP）的所有现有单测**全部继续通过**（0 regression，AC-B2 核心验证）。

**关联 AC**：AC-B1, AC-B2

**文件**：
- 新建：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/services/test_agent_context_ensure_session.py`（或扩展已有同名文件）
- 预期改动：+70 行

**依赖**：TB.2
**可并行**：否
**完成标准**：新增测试全通；现有 `_ensure_agent_session` 相关测试 0 regression

---

### TB.4 `[test]` `[x]` 全量回归（Phase B 后，关键门禁）

**描述**：`pytest -q --timeout=60` 必通（≥ Phase 0 baseline passed 数，0 regression）；`pytest -m e2e_smoke` 必通（8/8 PASS）。本 Phase 是高风险 Phase，回归通过才能进入 Codex review。

**依赖**：TB.3
**可并行**：否
**完成标准**：回归 0 regression；e2e_smoke 8/8

---

### TB.5 `[review]` Phase B per-Phase Codex review（**background**，高风险）

**描述**：触发 `/codex:adversarial-review` **background**（高风险 Phase，background 模式避免阻塞）。输入 Phase A+C+E+B cumulative diff。关注点：第 4 路条件判断是否与现有 3 路存在交集（特别是 `WORKER_INTERNAL` 路径的 fallback 逻辑）；`parent_worker_runtime_id` 字段的信号来源是否准确；`child_agent_session_id` 写回路径的异常处理；是否引入对 Worker session 创建的意外影响。处理 finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-phase-b.md`。

**关联 AC**：AC-GLOBAL-3

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-phase-b.md`

**依赖**：TB.4
**可并行**：否
**完成标准**：0 high finding 残留；finding 闭环结果写入 codex-review-phase-b.md

---

### TB.6 `[commit]` Phase B commit

**描述**：commit 全部 Phase B 改动（agent_context.py + test_agent_context_ensure_session.py + codex-review-phase-b.md）。

**commit message 格式**：`feat(F097-Phase-B): _ensure_agent_session 增 SUBAGENT_INTERNAL 第 4 路 + child_agent_session_id 写回 + Codex review: N high / M medium 已处理 / K low ignored`

**依赖**：TB.5
**可并行**：否
**完成标准**：commit 存在；pre-commit e2e_smoke PASS

---

## Phase D：RuntimeHintBundle caller→child 拷贝（~1h）

**目标 AC**：AC-D1, AC-D2
**User Story**：US2（Subagent 继承调用方上下文）

**依赖**：Phase B 完成（plan 建议 B 后做 D，避免测试混乱）；Phase 0 T0.3 的字段列表侦察结果

---

### TD.1 `[x]` `[code]` `_launch_child_task` 增 SUBAGENT 路径 RuntimeHintBundle 拷贝

**描述**：在 `capability_pack.py` 的 `_launch_child_task`（plan 记录 line 1229 附近）中，当 `target_kind == DelegationTargetKind.SUBAGENT` 时，从 caller 的 RuntimeHintBundle（通过 control_metadata 或 agent_runtime 参数获取）提取字段，添加到 `child_message.control_metadata`。具体拷贝字段以 T0.3 侦察结果为准（至少包含 `surface` / `tool_universe` / `recent_failure_budget`）。Worker spawn 路径（target_kind=WORKER）的 `control_metadata` 不改变（AC-D2）。

**关联 AC**：AC-D1, AC-D2

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
- 预期改动：+20 行（if target_kind == SUBAGENT 分支）

**依赖**：TB.6（Phase B 已 commit）；T0.3（RuntimeHintBundle 字段清单）
**可并行**：否
**完成标准**：SUBAGENT spawn 时 child_message.control_metadata 包含 caller 的 surface 字段；WORKER spawn 时不包含

---

### TD.2 `[x]` `[test]` RuntimeHintBundle 拷贝单测

**描述**：新建或扩展测试文件，覆盖：mock caller RuntimeHintBundle 含 `surface="web"`，spawn SUBAGENT，检查 `child_message.control_metadata["surface"] == "web"`；spawn WORKER（`target_kind=WORKER`），检查 `child_message.control_metadata` 不含 `surface` 字段（AC-D2）；caller `surface` 为 None 时的降级处理（不报错）。

**关联 AC**：AC-D1, AC-D2

**文件**：
- 新建：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/services/test_capability_pack_launch.py`（或扩展已有同名文件）
- 预期改动：+40 行

**依赖**：TD.1
**可并行**：否
**完成标准**：`pytest apps/gateway/tests/services/test_capability_pack_launch.py` 全通（新增测试 + 现有测试 0 regression）

---

### TD.3 `[review]` Phase D per-Phase Codex review（foreground）

**描述**：触发 `/codex:adversarial-review` foreground，输入 Phase B+D diff（或 A+C+E+B+D 累积）。关注点：caller RuntimeHintBundle 的获取路径是否正确（从 agent_runtime 还是 control_metadata）；`surface` 字段 None 时是否有安全处理；拷贝操作是否影响 Worker spawn 路径（AC-D2 验证）。处理 finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-phase-d.md`。

**关联 AC**：AC-GLOBAL-3

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-phase-d.md`

**依赖**：TD.2
**可并行**：否
**完成标准**：0 high finding 残留；finding 闭环结果写入 codex-review-phase-d.md

---

### TD.4 `[commit]` Phase D commit

**描述**：commit 全部 Phase D 改动（capability_pack.py + test_capability_pack_launch.py + codex-review-phase-d.md）。

**commit message 格式**：`feat(F097-Phase-D): RuntimeHintBundle caller→child 拷贝（仅 SUBAGENT 路径）+ Codex review: N high / M medium 已处理 / K low ignored`

**依赖**：TD.3
**可并行**：否
**完成标准**：commit 存在；pre-commit e2e_smoke PASS

---

## Phase F：Memory α 共享引用实施（~2h）

**目标 AC**：AC-F1, AC-F2, AC-F3
**User Story**：US4（Subagent 共享调用方 Memory，α 语义）

**依赖**：Phase A（`caller_memory_namespace_ids` 字段）+ Phase B（subagent AgentRuntime 已建立）+ Phase D 完成

---

### TF.1 `[x]` `[code]` spawn 时填充 `caller_memory_namespace_ids`（Phase A/B 补全）

**描述**：在 spawn 路径（`delegation_plane.py` 或 `capability_pack.py` 的 SubagentDelegation 创建点）中，读取 caller AgentRuntime 的当前 AGENT_PRIVATE namespace IDs，填充到 `SubagentDelegation.caller_memory_namespace_ids`，随 child_task.metadata 持久化。具体注入点以 TB.1 实施后的实际代码结构为准。

**关联 AC**：AC-F2

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/delegation_plane.py`（或 capability_pack.py，视 TB.1 实际改动位置）
- 预期改动：+15 行

**依赖**：TD.4（Phase D 已 commit）
**可并行**：否
**完成标准**：spawn 后 task metadata 中 `subagent_delegation.caller_memory_namespace_ids` 非空（若 caller 有 AGENT_PRIVATE namespace）

---

### TF.2 `[x]` `[code]` `_ensure_memory_namespaces` 增 subagent α 共享路径

**描述**：在 `agent_context.py` 的 `_ensure_memory_namespaces`（plan 记录 line 2463/2517 附近，以 T0.2 侦察结果为准）中新增 subagent 路径：当 `agent_runtime.delegation_mode == "subagent"` 时，从 task metadata 读取 `SubagentDelegation.caller_memory_namespace_ids`，直接返回 caller 的 namespace ID 集合，**不为 Subagent 创建新的 AGENT_PRIVATE namespace row**（AC-F1 α 语义）。fallback：若无法读取 caller namespace IDs，走正常创建路径（异常恢复）。

**关联 AC**：AC-F1, AC-F2

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- 预期改动：+30 行

**依赖**：TF.1
**可并行**：否
**完成标准**：subagent 路径下 `_ensure_memory_namespaces` 不调用 namespace 创建 store 方法；返回的 namespace IDs 等于 caller 的 AGENT_PRIVATE namespace IDs

---

### TF.3 `[x]` `[test]` Memory α 共享单测

**描述**：单测覆盖：subagent 路径下 `_ensure_memory_namespaces` 不创建新 namespace row（mock store，断言 save_namespace 未调用）；返回的 namespace IDs 等于 mock 的 caller_memory_namespace_ids；fallback 场景（caller_memory_namespace_ids 为空）不报错，走创建路径；Worker spawn 路径（target_kind=WORKER）的 `_ensure_memory_namespaces` 行为不受影响（F094 AGENT_PRIVATE 独立路径）。

**关联 AC**：AC-F1, AC-F2

**文件**：
- 新建：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_subagent_memory_sharing.py`（单测部分）
- 预期改动：+40 行

**依赖**：TF.2
**可并行**：否
**完成标准**：单测全通

---

### TF.4 `[x]` `[test]` Memory α 共享集成测（AC-F3 核心）

**描述**：集成测验证 α 语义端到端：1）Worker（caller）在 AGENT_PRIVATE namespace 写入 fact X；2）spawn Subagent（触发 TF.1/TF.2 路径）；3）Subagent 触发 Memory recall；4）断言 caller 在 spawn 之后能读到 Subagent 的写入（namespace ID 一致性，α 语义）；Worker 路径（target_kind=WORKER）独立 AGENT_PRIVATE 不受影响（F094 路径隔离验证）。

**关联 AC**：AC-F3

**文件**：
- 新建（或扩展）：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_subagent_memory_sharing.py`（集成测部分）
- 预期改动：+30 行（与 TF.3 同文件）

**依赖**：TF.3
**可并行**：否
**完成标准**：集成测通过；α 语义端到端验证通过

---

### TF.5 `[review]` Phase F per-Phase Codex review（foreground）

**描述**：触发 `/codex:adversarial-review` foreground，输入 Phase F diff（TF.1-TF.4）。关注点：α 语义并发安全（多个 Subagent 并发写同一 caller namespace 时的 SQLite WAL 行为——已知 trade-off，需 review 确认 spec §10 Edge Cases 覆盖充分）；caller AGENT_PRIVATE namespace IDs 可能为空时的 fallback 场景；TF.1 注入点是否影响 Worker spawn 路径。处理 finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-phase-f.md`。

**关联 AC**：AC-GLOBAL-3

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-phase-f.md`

**依赖**：TF.4
**可并行**：否
**完成标准**：0 high finding 残留；finding 闭环结果写入 codex-review-phase-f.md

---

### TF.6 `[commit]` Phase F commit

**描述**：commit 全部 Phase F 改动（delegation_plane.py 或 capability_pack.py + agent_context.py + test_subagent_memory_sharing.py + codex-review-phase-f.md）。

**commit message 格式**：`feat(F097-Phase-F): Memory α 共享引用实施（_ensure_memory_namespaces 不创建新 namespace）+ Codex review: N high / M medium 已处理 / K low ignored`

**依赖**：TF.5
**可并行**：否
**完成标准**：commit 存在；pre-commit e2e_smoke PASS

---

## Phase G：BEHAVIOR_PACK_LOADED agent_kind=subagent 验证（~30min）

**目标 AC**：AC-G1, AC-AUDIT-1, AC-COMPAT-1
**User Story**：US1（audit chain 四层对齐验证）

**依赖**：Phase C 完成（ephemeral AgentProfile kind=subagent 已生效后，agent_kind 自动正确）+ Phase F 完成

> **注意**：Phase G 无需新增实施代码。Gap-C（Phase C）实施后，`make_behavior_pack_loaded_payload` 读 `str(agent_profile.kind)` 自动返回 `"subagent"`。Phase G 的工作是**补充验证测试**。

---

### TG.1 `[test]` 补充 BEHAVIOR_PACK_LOADED agent_kind=subagent 集成测

**描述**：在 `test_task_service_context_integration.py`（已有文件，tech-research 记录 line 2373 有相关结构）补充 subagent 路径的 BEHAVIOR_PACK_LOADED 断言：spawn subagent task → dispatch → query EventStore → 断言 `BEHAVIOR_PACK_LOADED.agent_kind == "subagent"` + `BEHAVIOR_PACK_LOADED.agent_id` 与 AgentRuntime.profile_id 一致（AC-AUDIT-1 四层链路前两层）。

**关联 AC**：AC-G1, AC-AUDIT-1

**文件**：
- 编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_task_service_context_integration.py`
- 预期改动：+40 行（新增 2 个 test case）

**依赖**：TF.6（Phase F 已 commit）
**可并行**：否（与 TG.2 写同一文件）
**完成标准**：新增测试全通；`BEHAVIOR_PACK_LOADED.agent_kind == "subagent"` 断言通过

---

### TG.2 `[test]` 验证现有 Worker 路径 agent_kind 不受影响（AC-COMPAT-1）

**描述**：确认 `test_agent_decision_envelope.py:640` 的 `assert payload.agent_kind == "worker"` 测试（Worker 路径）在 F097 实施后**继续通过**（0 regression）。如该测试在 Phase 0-F 期间已被回归验证，此 Task 可简化为文档确认；若需要额外补充，在 TG.1 同文件增加 Worker agent_kind 不变的显式断言。

**关联 AC**：AC-COMPAT-1

**文件**：
- 读/确认：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_agent_decision_envelope.py`（line 640 附近）
- 可选编辑：`/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/octoagent/apps/gateway/tests/test_task_service_context_integration.py`

**依赖**：TG.1
**可并行**：否
**完成标准**：`test_agent_decision_envelope.py:640` 的 Worker 路径测试通过（继续 "worker"）

---

### TG.3 `[test]` 全量回归（Phase G 后）

**描述**：`pytest -q --timeout=60` 必通（≥ Phase 0 baseline passed 数 + 所有新增测试，0 regression）；`pytest -m e2e_smoke` 必通（8/8 PASS）。

**依赖**：TG.2
**可并行**：否
**完成标准**：回归 0 regression；e2e_smoke 8/8

---

### TG.4 `[commit]` Phase G commit

**描述**：commit 全部 Phase G 改动（test_task_service_context_integration.py）。**Phase G 无 per-Phase Codex review**（测试新增命中"不需要做的节点"，参照 CLAUDE.local.md 规则）。

**commit message 格式**：`test(F097-Phase-G): BEHAVIOR_PACK_LOADED agent_kind=subagent 验证 + AC-AUDIT-1 四层链路前两层 + AC-COMPAT-1 兼容性确认`

**依赖**：TG.3
**可并行**：否
**完成标准**：commit 存在；pre-commit e2e_smoke PASS

---

## Verify：全量回归 + Final Codex Review（~2h）

**目标 AC**：AC-GLOBAL-1 ~ 6, AC-SCOPE-1, AC-EVENT-1, AC-AUDIT-1（全量闭环）

---

### TVERIFY.1 `[verify]` 全量回归 pytest（AC-GLOBAL-1）

**描述**：运行 `pytest --timeout=60 -q`，目标 ≥ Phase 0 记录的 baseline passed 数（约 3260），0 regression。记录实际 passed/failed/error 数。

**命令**：`pytest --timeout=60 -q 2>&1 | tail -10`

**依赖**：TG.4（所有 Phase 已 commit）
**完成标准**：≥ baseline passed 数；0 net regression

---

### TVERIFY.2 `[verify]` e2e_smoke 5x 循环（AC-GLOBAL-2）

**描述**：`octo e2e --loop=5`（按 F087 规范），或 `pytest -m e2e_smoke` 连续 5 次。目标 8/8 PASS × 5。

**命令**：`pytest -m e2e_smoke -v` × 5 循环（或 `octo e2e smoke --loop=5`）

**依赖**：TVERIFY.1
**完成标准**：每次 8/8 PASS；总 5 次全通

---

### TVERIFY.3 `[verify]` AC 全量 checklist 确认

**描述**：逐条核对 spec.md §5 的 22 个 AC（AC-A1~A3 / AC-B1~B2 / AC-C1~C2 / AC-D1~D2 / AC-E1~E3 / AC-F1~F3 / AC-G1 / AC-AUDIT-1 / AC-COMPAT-1 / AC-EVENT-1 / AC-SCOPE-1 / AC-GLOBAL-1~6），每条标注 ✅ 或 ⚠️（需补充）。AC-EVENT-1 手工验证：在集成测中查 EventStore，确认 `delegate_task` 路径 SUBAGENT_SPAWNED 事件存在（F092 已有路径，F097 仅验证继续有效）。AC-SCOPE-1 验证：`git diff cc64f0c -- "*/worker_runtime.py" "*/orchestrator.py"` 确认 F098/F099/F100 相关文件无改动。

**依赖**：TVERIFY.2
**完成标准**：所有 22 个 AC 标注 ✅（或有 ⚠️ 显式归档到 completion-report.md）

---

### TVERIFY.4 `[review]` Final cross-Phase Codex review（**background**，强制）

**描述**：触发 `/codex:adversarial-review` **background**，输入 spec.md + plan.md + 全部 Phase A/C/E/B/D/F/G commit diff。专门检查：是否漏 Phase / 是否偏离原计划且未在 commit message 说明；全 Phase 串联的审计链四层对齐（AgentProfile.profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id）；Memory α 共享的并发 trade-off 是否已在 spec Edge Cases 中充分说明；F098 接入点（SubagentDelegation 字段命名、BEHAVIOR_PACK_LOADED agent_kind 演化）是否预留充分。处理 finding，归档到 `.specify/features/097-subagent-mode-cleanup/codex-review-final.md`。

**关联 AC**：AC-GLOBAL-4

**文件**：
- 写：`.specify/features/097-subagent-mode-cleanup/codex-review-final.md`

**依赖**：TVERIFY.3
**完成标准**：0 high finding 残留（或显式 reject + 原因归档）；finding 闭环结果写入 codex-review-final.md

---

### TVERIFY.5 `[doc]` 产出 completion-report.md（AC-GLOBAL-5，强制）

**描述**：新建 `.specify/features/097-subagent-mode-cleanup/completion-report.md`，格式参照 F094/F096 completion-report。必含：实际 vs 计划对照表（Phase 0/A/C/E/B/D/F/G + Verify，标注"实际做了 vs 计划"）；Codex review 全闭环表（per-Phase A/C/E/B/D/F + Final，各 N high / M medium 处理 / K low ignored）；Phase 跳过显式归档（若有）；F098 接入点说明（SubagentDelegation 字段命名兼容、BEHAVIOR_PACK_LOADED agent_kind 演化路径、F096 AC-F1 推迟项说明）；spec Done Criteria 对照（plan §10 全部 ✅）。

**关联 AC**：AC-GLOBAL-5

**文件**：
- 新建：`.specify/features/097-subagent-mode-cleanup/completion-report.md`

**依赖**：TVERIFY.4
**完成标准**：completion-report.md 存在，含上述所有必填 section

---

### TVERIFY.6 `[doc]` 产出 handoff.md（前向声明）

**描述**：新建 `.specify/features/097-subagent-mode-cleanup/handoff.md`，为 F098（A2A Mode + Worker↔Worker）提供接入说明：1）`SubagentDelegation` 字段命名与 F098 `WorkerDelegation` 兼容性说明（`BaseDelegation` 提取决策点）；2）`BEHAVIOR_PACK_LOADED.agent_kind == "subagent"` 已生效，F098 需为 A2A Receiver 扩展新值（向后兼容，str 类型无约束）；3）`_enforce_child_target_kind_policy` 保持不动（F098 负责 Worker→Worker 解禁时删除）；4）F096 AC-F1 推迟项（worker_capability 路径 audit chain，等 F098 delegate_task fixture 完备后实施）；5）Memory α 语义已锁定，F098 A2A Receiver 走独立 namespace（H3-B 独立路径，不冲突）。

**文件**：
- 新建：`.specify/features/097-subagent-mode-cleanup/handoff.md`

**依赖**：TVERIFY.5
**完成标准**：handoff.md 存在，含上述 5 个接入点说明

---

### TVERIFY.7 `[commit]` Verify 阶段最终 commit（等用户拍板）

**描述**：commit Verify 阶段全部产出（codex-review-final.md + completion-report.md + handoff.md）。**不主动 push origin/master**，回归主 session 归总报告，等用户显式确认后再 push。

**commit message 格式**：`docs(F097-Phase-Verify): Final cross-Phase Codex review 闭环 + completion-report + handoff（Codex: N high / M medium 已处理 / K low ignored）`

**依赖**：TVERIFY.6
**完成标准**：commit 存在于 feature/097-subagent-mode-cleanup 分支；**不 push origin/master**

---

## 任务依赖 DAG

```
Phase 0（T0.1, T0.2, T0.3 并行）
  ↓ T0.4（汇总）
Phase A（TA.1 → TA.2 → TA.3 → TA.4 → TA.5）
  ↓
Phase C（TC.1 → TC.2 → TC.3 → TC.4 → TC.5）
  ↓
Phase E（TE.1 → TE.2 → TE.3 → TE.4 → TE.5）
  ↓
Phase B（TB.1 → TB.2 → TB.3 → TB.4 → TB.5 → TB.6）【最高风险】
  ↓
Phase D（TD.1 → TD.2 → TD.3 → TD.4）
  ↓
Phase F（TF.1 → TF.2 → TF.3 → TF.4 → TF.5 → TF.6）
  ↓
Phase G（TG.1 → TG.2 → TG.3 → TG.4）
  ↓
Verify（TVERIFY.1 → TVERIFY.2 → TVERIFY.3 → TVERIFY.4 → TVERIFY.5 → TVERIFY.6 → TVERIFY.7）
```

### 可并行任务对

| 并行对 | Phase | 说明 |
|--------|-------|------|
| T0.1 ‖ T0.2 ‖ T0.3 | Phase 0 | 三个侦察任务读不同文件，完全独立 |
| TA.1 ‖ （Phase C TC.2 不可并行）| — | Phase A 内部串行 |
| TG.1（追加测试）可与 TG.2（确认现有测试）同步读| Phase G | 同文件写操作须串行，但 TG.2 只读 |

**并行总对数**：Phase 0 内 3 个任务两两并行，形成 3 对可并行组合。其余 Phase 内部因文件竞争或逻辑依赖须串行。

---

## FR 覆盖映射表

| spec AC | 关联 Task IDs |
|---------|--------------|
| AC-A1 | TA.1, TA.2, TA.3 |
| AC-A2 | TA.1, TA.3 |
| AC-A3 | TA.1, TA.3 |
| AC-B1 | TB.1, TB.2, TB.3 |
| AC-B2 | TB.3, TB.4 |
| AC-C1 | TC.1, TC.2 |
| AC-C2 | TC.1, TC.2 |
| AC-D1 | TD.1, TD.2 |
| AC-D2 | TD.1, TD.2 |
| AC-E1 | TE.1, TE.2, TE.3 |
| AC-E2 | TE.1, TE.3 |
| AC-E3 | TE.1, TE.3 |
| AC-F1 | TF.2, TF.3 |
| AC-F2 | TF.1, TF.3 |
| AC-F3 | TF.4 |
| AC-G1 | TG.1 |
| AC-AUDIT-1 | TG.1, TVERIFY.3 |
| AC-COMPAT-1 | TG.2 |
| AC-EVENT-1 | TVERIFY.3 |
| AC-SCOPE-1 | TVERIFY.3 |
| AC-GLOBAL-1 | TVERIFY.1 |
| AC-GLOBAL-2 | TVERIFY.2 |
| AC-GLOBAL-3 | TA.4, TC.4, TE.4, TB.5, TD.3, TF.5（每 Phase Codex review）|
| AC-GLOBAL-4 | TVERIFY.4 |
| AC-GLOBAL-5 | TVERIFY.5 |
| AC-GLOBAL-6 | TVERIFY.5（completion-report Phase 跳过归档 section）|

**AC 覆盖率**：22/22（100%）

---

## 关键里程碑

| 里程碑 | 完成标志 |
|--------|---------|
| **M0 baseline 确认** | T0.4 完成；phase-0-recon.md 存在含实际 baseline passed 数 |
| **M1 数据模型就位** | TA.5 commit；SubagentDelegation 可 import；round-trip 单测通过 |
| **M2 Subagent 身份正确** | TC.5 commit；ephemeral AgentProfile kind=subagent 不写持久化表 |
| **M3 Session cleanup 就位** | TE.5 commit；幂等 cleanup hook 挂载在 _notify_completion |
| **M4 SUBAGENT_INTERNAL session 激活** | TB.6 commit；0 regression（高风险门禁）|
| **M5 上下文继承完整** | TD.4 commit；RuntimeHintBundle 字段拷贝 + Memory α 共享（TF.6）|
| **M6 audit chain 全验证** | TG.4 commit；BEHAVIOR_PACK_LOADED agent_kind=subagent 测试通过 |
| **M7 F097 完成** | TVERIFY.7 commit；待用户拍板 push origin/master |

---

## 全局约束

- **不主动 push origin/master**：TVERIFY.7 后回归主 session 归总报告，等用户显式拍板
- **Codex review finding 处理**：参照 CLAUDE.local.md §"Codex Adversarial Review 强制规则"——high/medium 必须闭环；commit message 必须含 `Codex review: N high / M medium 已处理 / K low ignored`
- **Phase 跳过显式归档**：若实施中发现某 Phase 已 baseline ready 而决定跳过，必须在 completion-report 显式写"Phase X 跳过，理由 Y"（不允许默认无说明跳过）
- **每 Phase commit 前回归 0 regression vs F096 baseline（约 3260 passed）**
- **OD-2 验证（非侵入性）**：TVERIFY.3 中验证 `subagents.spawn` 路径 SUBAGENT_SPAWNED 计数不变（F092 行为等价，F097 不修改 `emit_audit_event` 参数）
- **F098/F099/F100 范围无改动**：TVERIFY.3 的 AC-SCOPE-1 验证 `worker_runtime.py` / `orchestrator.py` 无改动
