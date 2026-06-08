# F112 最终验证报告（verification-report）

> 模式：spec-driver-refactor 第 5/5 阶段
> 所有测试 PYTHONPATH 锁 worktree src（防 venv symlink 假 0 regression，见 project_worktree_venv_symlink）
> 调用方式：`uv run --no-sync python -m pytest -q -p no:cacheprovider`

## 1. 全量回归（0 regression 闸）

| | baseline @ 543a93b | post-change run #1 | **final run（all edits in，权威）** |
|--|--|--|--|
| passed | 3772 | 3749 | **3750** |
| failed | 0 | 1（flaky）| **0** |
| skipped | 10 | 10 | 10 |
| deselected | 77 | 77 | 77 |
| xfailed | 1 | 1 | 1 |
| xpassed | 1 | 1 | 1 |
| collected | 3772 | 3750 | 3750 |
| EXIT | 0 | 1 | **0** |

> final run（含全部 code+comment+doc 编辑）：**3750 passed, 0 failed, exit 0**。run #1 那条 flaky（SC3 SQLite race）本轮 PASS，进一步证实其 flaky 性质（非 F112 回归）。

**collected 差 = 3772 − 3750 = 22**，与刻意删除的死代码/重复测试**精确吻合**：
- `test_runtime_control_f091.py` −18：
  - `TestMetadataFlag` 整类 −13（`metadata_flag` 函数已删，11 param + 2 method）
  - single-loop 字面重复合并 −3（`test_unspecified_returns_false_with_metadata_flag_false` / `test_unspecified_no_metadata_returns_false` / `test_runtime_context_none_metadata_none` 删参后与保留用例相同）
  - recall 字面重复合并 −2（`test_skip_mode_overrides_metadata` / `test_full_mode_overrides_metadata_when_delegation_explicit` 删参后与 explicit 用例相同）
- `test_ask_back_recall_planner_resume_f100.py` −4：删参后全部塌缩为 `helper(None/unspecified) is False`，保留 2 个场景用例（None / unspecified），删 4 个重复/结构上已无意义（helper 不再接触 metadata）的用例

**真值表覆盖零丢失**：所有 delegation_mode × recall_planner_mode 行为断言保留在合并后的 f091 + 完整的 f100；删除的仅是"被删函数的测试"和"删参后字面重复的测试"。

### 唯一 failed 是已知 flaky（非 F112 回归）
- `tests/integration/test_sc3_projection.py::TestSC3Projection::test_rebuild_preserves_task_state`
- 错误：`OperationalError: cannot commit transaction - SQL statements in progress`（aiosqlite 并发事务 race）
- 判定 flaky 证据：
  1. F112 diff **零触碰** transaction / projection / rebuild / store / event 逻辑（仅 runtime_control helpers + is_private_namespace + tests）
  2. 隔离重跑 **3/3 PASS**
  3. 与 F083 已记录的"task_runner 状态机测试 SQLite race ~20% 失败率"工程债同源（CLAUDE.local.md / testing-concurrency.md）

**结论：0 genuine regression。**

## 2. 焦点测试（行为零变更重点域）

| 套件 | 结果 |
|------|------|
| recall planner / single-loop（f091 + f100 + f100_perf + ask_back_resume + chat_force_full_recall + f101_phase_f）| 91 passed |
| memory namespace（test_agent_context_store + agent_context_phase_f + task_service_context_integration + migration_063 + migration_094）| 89 passed |

## 3. 行为等价性核验（is_private_namespace）
逐枚举对照 `is_private_namespace(k)` vs 原 `k in {AGENT_PRIVATE, WORKER_PRIVATE}`：
- project_shared → False/False ✅
- agent_private → True/True ✅
- worker_private → True/True ✅
- `MemoryNamespaceKind("worker_private")` 反序列化 OK（5 条既有 records 安全）✅

## 4. 残留扫描
见 residual-report.md：旧标识符/旧形态零残留；WORKER_PRIVATE 显式引用收敛到单一 frozenset + owner 派生 + 历史注释。

## 5. living-docs 漂移闸（**首轮自查有误，Codex 抓到，已修正**）

⚠️ 自查教训：首轮 drift grep 从 `octoagent/` 跑 `grep docs/`，但 `docs/` 实际在 **repo 根**（`docs/`，非 `octoagent/docs/`），且 `2>/dev/null` 把"目录不存在"错误吞了 → 误得"0 命中 / 无文档需同步"。Codex 从 repo 根扫描抓到真漂移。

修正后从 repo 根全量扫描 `is_recall_planner_skip(|is_single_loop_main_active(|metadata_flag|metadata fallback|runtime_control.py:NNN`，命中 3 处：

| 文件:行 | 性质 | 处置 |
|---------|------|------|
| `docs/blueprint/agent-collaboration-philosophy.md:65/68/69` | **真契约漂移**（权威蓝图记 2 参签名 `is_recall_planner_skip(runtime_context, metadata)` + stale `:106` 行号）| ✅ 改单参签名 + 去脆性行号（已漂移 2 次）+ 加 F112 说明注释 |
| `docs/blueprint/architecture-audit.md:264` | F091 历史审计日志（"runtime_context 优先 + metadata fallback"）| ✅ 加前向说明"（F100/F112 后已无 metadata fallback）" |
| `docs/codebase-architecture/message-model.md:179` | 历史叙述，已含"F100 移除...当前 unspecified→False 等价" | 准确，无需改 |

重扫确认：0 残留 2 参签名 / stale 行号；3 处 "metadata fallback" 均为历史+澄清。

## 6. e2e_smoke
`pytest -m e2e_smoke` → **8 passed, 3831 deselected**（~2s，PYTHONPATH 锁 worktree）。✅

## 7. Codex adversarial review（working-tree，跨多文件 refactor 节点）

第 1 轮：网络中断（"stream disconnected before completion" → Turn failed，同 F103/F103c pattern），captured 到 preliminary "approve / No material findings"，但未完成完整结构化 pass。
第 2 轮（重试）：**Turn completed（无中断）**。Verdict: **needs-attention**，1 finding：

| # | 严重度 | finding | 闭环 |
|---|--------|---------|------|
| 1 | MEDIUM | 权威 blueprint `agent-collaboration-philosophy.md:65-69` 仍记 2 参 helper 签名 + stale 行号 → 按文档实现会写出 2 参调用触发 TypeError；且反驳 F112 报告"无文档需同步"结论 | ✅ 已修（见 §5）+ grep 重扫确认 0 残留 |

**0 HIGH**。Codex 流式 verdict 在 finding 前持续 "approve"，独立确认：无漏改 caller（含 AST 扫描）、无误删 orchestrator LIVE `_metadata_flag`、`is_private_namespace` 与 StrEnum 集合成员等价、测试文件保留 single-loop 四态 + recall AUTO 四态 + force_full_recall 覆盖。

MEDIUM 为 doc-only 漂移，修复亦 doc-only（grep 可验证），不引入代码风险，未再跑第 3 轮 Codex（修复确定性可 grep 验证）。

主 session 补充对抗自查（与 Codex 正交，全 PASS）：
- 全仓（apps+packages）0 个 2-arg / 多行 / keyword `metadata=` helper 调用；0 个 deleted `_metadata_flag` 的 getattr/字符串动态引用
- `is_private_namespace` 逐枚举 + 反序列化等价
- 删 helper 后 `Mapping`/`Any` import 仍被使用（无 unused import 引入）
- py_compile 6 个 touched src 全过
