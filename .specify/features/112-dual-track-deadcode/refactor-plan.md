# F112 分批重构规划（refactor-plan）

> 模式：spec-driver-refactor 第 2/5 阶段
> baseline（PYTHONPATH 锁 worktree src @ 543a93b）：**3772 passed, 10 skipped, 77 deselected, 1 xfailed, 1 xpassed**（122s, exit 0）
> 用户 plan-phase 拍板（2026-06-08）：
> - **WORKER_PRIVATE**：枚举保留（实例 5 条存量）→ 收敛 4 处读侧守卫到单一 `is_private_namespace(kind)` + 写明理由
> - **死方法范围**：一并删除 task_service / llm_service 的 2 个已死 `_metadata_flag`

---

## 批次划分（2 批，逻辑独立，可分别验证）

### Batch 1 — Track 1：metadata fallback 死代码（签名变更，src + tests 原子落地）

> 删 helper 签名的 `metadata` 形参属于跨文件签名变更，src 改动与全部 caller（含测试）必须同批落地，否则中间态 TypeError。

**src（5 文件）**：
1. `runtime_control.py`
   - 删 `metadata_flag`（line 62-73，纯死代码）
   - `is_single_loop_main_active`：签名删 `metadata` 形参（line 77-78）+ 删函数体注释残留（line 95-96）
   - `is_recall_planner_skip`：签名删 `metadata` 形参（line 101-102）+ 删函数体注释残留（line 146-147）
   - 更新模块 docstring（line 1-8）移除 "metadata flag fallback" 描述
2. `orchestrator.py`：line 771 `is_single_loop_main_active(runtime_context_for_check, metadata)` → 删第 2 实参；line 1081 `is_single_loop_main_active(runtime_context_for_check, request.metadata)` → 删第 2 实参。（`metadata` / `request.metadata` 局部仍被其他逻辑用，仅删 helper 调用实参）
3. `llm_service.py`：line 383 删第 2 实参；删死方法 `_metadata_flag`（line 1004-1009，含 `@staticmethod`）
4. `task_service.py`：line 1134 删第 2 实参；删死方法 `_metadata_flag`（line 1110-1115，含 `@staticmethod`）
5. （orchestrator 的 `_metadata_flag` def line 1067 + LIVE call line 922 **不动** —— force_full_recall producer）

**tests（6 文件）**：
- `test_runtime_control_f091.py`：删 `metadata_flag` import（line 15）+ 删 `TestMetadataFlag` 类（line 161-193，被测函数已删）；两 helper 测试类删 metadata 实参；**合并因删参后字面重复的用例**并正名（去 `_regardless_of_metadata_flag` 名实不符）——保留全部真值表行为断言（main_inline/worker_inline/main_delegate/subagent/unspecified/None）
- `test_runtime_control_f100.py`：`is_recall_planner_skip(ctx, {})` → 删 `, {}`
- `test_runtime_control_f100_perf.py`：`_measure_microseconds(helper, ctx, {})` → 删尾部 `, {}`（wrapper 透传实参，需同步）
- `test_ask_back_recall_planner_resume_f100.py`：删 helper 调用 metadata 实参（命名变量 resume_metadata / metadata_with_signal 等）；这些用例原验证"resume metadata 不触发 skip"，删参后语义退化为 unspecified/None→False，调整断言注释说明
- `test_chat_force_full_recall.py`：`is_recall_planner_skip(ctx, {})` → 删 `, {}`
- `services/test_f101_phase_f_acceptance.py`：`is_recall_planner_skip(ctx, {})` / `(None, {})` → 删 `, {}`（含 spy / 多轮 loop 处）

**Batch 1 中间验证**：
- `python -m pytest test_runtime_control_f091 f100 f100_perf ask_back_resume chat_force_full_recall f101_phase_f -q`（PYTHONPATH 锁 worktree）→ 全绿
- grep 残留：`metadata_flag`（区分 `_metadata_flag`）src 端零；两 helper 全仓无 2-arg 调用

### Batch 2 — Track 2：WORKER_PRIVATE 守卫收敛（行为零变更 DRY）

**src（3 文件）**：
1. `packages/core/src/octoagent/core/models/agent_context.py`：`MemoryNamespaceKind` 枚举后（line 168 后）新增 `is_private_namespace(kind) -> bool`（成员 = {AGENT_PRIVATE, WORKER_PRIVATE}）+ docstring 写明 WORKER_PRIVATE 保留理由（实例 5 条存量 + Constitution #1）
2. `apps/gateway/src/.../agent_context.py`：
   - line 480-483 `if kind not in {AGENT_PRIVATE, WORKER_PRIVATE}` → `if not is_private_namespace(kind)`（owner 派生 line 485 **不动**，需具体枚举值）
   - line 3791-3794 排序 key `namespace.kind in {...}` → `is_private_namespace(namespace.kind)`
   - import 加 `is_private_namespace`
3. `apps/gateway/src/.../task_service.py`：line 2149-2152 `item.kind in {...}` → `is_private_namespace(item.kind)`；import 加 `is_private_namespace`

**Batch 2 中间验证**：
- `python -m pytest packages/core/tests/test_agent_context_store.py apps/gateway/tests/.../test_agent_context_phase_f.py test_task_service_context_integration.py -q` → 全绿（含 `WORKER_PRIVATE not in kinds` 断言仍通过）
- 行为零变更核验：`is_private_namespace` 真值表与原 set-membership 等价

---

## 残留扫描目标标识符（Phase 4）
- `metadata_flag`（runtime_control 模块级，应 src 零残留；测试仅剩无）
- 两 helper 的 2-arg 调用（应全仓零）
- `kind in {... AGENT_PRIVATE, WORKER_PRIVATE ...}` 散落 set 字面（src 应收敛，仅剩 `is_private_namespace` 定义处的 frozenset）

## 最终验证（Phase 5）
- 全量回归 `-m "not e2e_smoke and not e2e_full and not e2e_live"`：≥ (3772 − 删除的死代码测试数) passed，0 newly-failing
- e2e_smoke 必过
- Codex adversarial review（跨多文件 refactor 节点）→ 0 HIGH 残留
- living-docs 漂移闸：检查 blueprint / harness-and-context.md 是否提及 metadata fallback / WORKER_PRIVATE 写路径

## 回滚策略
每批独立 commit；中间验证失败 → `git checkout -- <批次文件>` 回滚该批，不影响另一批。
