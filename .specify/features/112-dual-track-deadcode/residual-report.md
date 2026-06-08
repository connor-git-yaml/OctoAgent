# F112 残留扫描报告（residual-report）

> 模式：spec-driver-refactor 第 4/5 阶段（编排器亲自执行，不委派）
> 扫描对象：旧标识符 / 旧调用形态在全仓的残留

## 扫描标识符与结果

| 目标标识符/形态 | 期望 | 实测结果 |
|----------------|------|----------|
| module-level `metadata_flag`（runtime_control）src 调用 | 0 | ✅ 0（仅 test_runtime_control_f091.py 一处**注释**说明"已删"，非代码引用）|
| `is_single_loop_main_active(x, y)` 2-arg 调用 | 0 | ✅ 0（含多行形态）|
| `is_recall_planner_skip(x, y)` 2-arg 调用 | 0 | ✅ 0（含多行形态）|
| 死方法 `_metadata_flag`（task_service / llm_service）| 0 | ✅ 0（已删；orchestrator LIVE `_metadata_flag` 保留：def :1067 + call :922 force_full_recall）|
| `_measure_microseconds(helper, x, {})` 残留 | 0 | ✅ 0 |
| 散落 `kind in {AGENT_PRIVATE, WORKER_PRIVATE}` set 字面（src）| 收敛到 1 | ✅ 仅剩 core `_PRIVATE_MEMORY_NAMESPACE_KINDS` frozenset（单一来源）；3 处消费端改 `is_private_namespace`|

## WORKER_PRIVATE 显式枚举值残留（**预期保留**，非清理目标）

实例有 5 条存量 → 枚举与读侧能力必须保留。最终 src 中 `WORKER_PRIVATE` 显式引用收敛为：
- `core/models/agent_context.py:168` 枚举定义（保留）
- `core/models/agent_context.py:172` `_PRIVATE_MEMORY_NAMESPACE_KINDS` frozenset 成员（单一来源）
- `core/models/agent_context.py:180/182` `is_private_namespace` docstring 保留理由
- `gateway/services/agent_context.py:484` owner 派生（`owner = "worker" if kind is WORKER_PRIVATE`，需具体枚举值，无法收敛）+ :483 说明注释
- `gateway/services/agent_context.py:2848` F094 历史注释（保留，记录写路径废弃决策）
- migration_063 / migration_094 / session_memory_extractor 注释（历史迁移，本 Feature 不动）

## 豁免（非残留，故意保留）

- `test_runtime_control_f091.py:9` docstring 提到 "module-level metadata_flag 已删" —— 说明性文字，非代码引用。
- orchestrator `_metadata_flag`（:922 call / :1067 def）—— LIVE，承载 F101 force_full_recall hint，非本 Feature 死代码。
- ~~`test_runtime_control_f100.py` 的 `is_single_loop_main_active` import —— pre-existing 未使用 import~~ —— **用户要求一并删除（grep 确认零调用，f100 33 passed）**，已不再豁免。

## living-docs 漂移闸（修正版）

> ⚠️ 首轮自查从 `octoagent/` 跑 `grep docs/` 搜了不存在的 `octoagent/docs/`（docs 在 repo 根），`2>/dev/null` 吞了报错 → 误判"0 命中"。Codex 抓到真漂移。

从 **repo 根**重扫 `is_recall_planner_skip(|is_single_loop_main_active(|metadata_flag|metadata fallback|runtime_control.py:NNN` → 命中 3 处，**已处置**（详见 verification-report §5）：
- `docs/blueprint/agent-collaboration-philosophy.md` 真契约漂移（2 参签名）→ 已改单参 + 去脆性行号 + F112 注释
- `docs/blueprint/architecture-audit.md:264` F091 历史日志 → 加前向澄清
- `docs/codebase-architecture/message-model.md:179` 历史叙述已准确 → 不改

重扫确认 0 残留契约漂移。被删概念在**代码层**确为内部实现细节，但**权威蓝图**显式记录了 helper 签名契约，必须同步——首轮"无需同步"结论是错的。

## 结论
旧标识符/旧形态 **零残留**；WORKER_PRIVATE 显式引用收敛到"单一 frozenset + 不可避免的 owner 派生 + 历史注释"。
