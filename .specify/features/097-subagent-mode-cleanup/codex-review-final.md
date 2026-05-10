# F097 Final Cross-Phase Codex Adversarial Review

**日期**: 2026-05-10
**审视命令**: `codex review --base origin/master`（默认 prompt）
**审视范围**: 全部 7 commits vs origin/master cc64f0c (F096 baseline)
**审视模型**: GPT-5.5 high (Codex 默认 with --base)

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 |
|----|--------|---------|------|---------|
| **P1-1** | high | task_runner.py:287-293 | SubagentDelegation USER_MESSAGE event "`[subagent delegation metadata]`" 被 ContextCompactionService._load_conversation_turns 当成用户输入 → 首轮 latest_user_text 错误 | **接受归档为 known issue → user 拍板**：完整修复需引入新 event type 或加跳过标记，影响 compaction / projection / replay 多处消费方 |
| **P1-2** | high | agent_context.py:1343-1345 | ephemeral subagent profile 无 source_worker_profile_id → _ensure_agent_runtime 用 role=WORKER + 空 profile_id 查找 → 复用 caller worker runtime 导致 audit 混在一起 | **接受归档为 known issue → user 拍板**：完整修复需 ephemeral runtime 独立路径，影响面大 |
| **P2-1** | medium | agent_context.py:2616-2622 | B-3 backfill USER_MESSAGE 只含 subagent_delegation 不含 target_kind 等 TURN_SCOPED 字段 → resume/retry 时 target_kind 丢失（与 Phase D 同根 normalize 问题）| **已修复**：preserve 历史 USER_MESSAGE 的 normalize control_metadata 后再扩展 subagent_delegation，与 Phase D `_emit_subagent_delegation_init_if_needed` 同样修法 |

## 总结

- High: 2（**接受归档 → user 拍板**）
- Medium: 1（**已修复**）
- Low: 0

## P1 归档为 known issue 的理由

P1-1 + P1-2 都是**架构层深层问题**，不是 Phase F/G 范围内可独立修复的局部 bug：

### P1-1: USER_MESSAGE 事件类型复用

OctoAgent 当前事件模型中 `USER_MESSAGE` 事件被多处消费方使用：
- `ContextCompactionService._load_conversation_turns` 取 text 作为用户 turn
- `merge_control_metadata` 从 USER_MESSAGE.payload.control_metadata 合并
- 各种 projection / replay 路径

F097 在 `task_runner._emit_subagent_delegation_init_if_needed` 和 `agent_context._ensure_agent_session` B-3 backfill 两处用 USER_MESSAGE 承载 control_metadata 更新（marker text 如 `[subagent delegation metadata]`）。Codex 发现这会污染 latest_user_text。

**完整修复路径**：
- 选项 A：引入新 event type `CONTROL_METADATA_UPDATED`，only carries control_metadata
- 选项 B：USER_MESSAGE 加 `is_synthetic_marker` 标记，consumer 跳过
- 选项 C：重构 SubagentDelegation 持久化路径走 task store metadata（CL#16 原始决策）而非 event stream

每条都涉及多处 consumer 改动 + 测试更新，工作量 ~3-5h。F098 / F099 实施时再统一收口。

### P1-2: ephemeral subagent runtime 与 caller worker runtime 复用

`_ensure_agent_runtime` 当前用 `(project_id, role, worker_profile_id)` 三元组复用 active runtime。subagent ephemeral profile 没有 source_worker_profile_id（empty string），导致复用 caller worker 的 active runtime → audit 数据混在一起。

**完整修复路径**：
- 为 subagent runtime 引入独立的 query key（如用 SubagentDelegation.delegation_id 派生）
- 跳过 _ensure_agent_runtime 的复用逻辑，每次 spawn 创建新 runtime
- 这涉及 _ensure_agent_runtime 函数签名 + 调用方改动

**影响评估**：当前 Phase B/F 测试覆盖 spawn → SUBAGENT_INTERNAL session 创建 + parent_worker_runtime_id 填充等，但 audit chain 实际混叠到 caller worker runtime 的影响在测试中未直接验证。Phase G AC-AUDIT-1 测试 `payload.agent_id == profile_id` 是 ephemeral profile_id（独立），未对 agent_runtime_id 做完整 4 层一致性验证（实际可能 == caller worker runtime_id）。

工作量 ~3-5h。F098 A2A 模式实施时一并收口（F098 spec 应明确独立 runtime 路径）。

### P2-1 已修复

agent_context.py:2616 B-3 backfill 改为先 `merge_control_metadata` 取历史 normalize 字段后再扩展 subagent_delegation，与 Phase D `_emit_subagent_delegation_init_if_needed` 同样修法。回归 3355 passed / 0 failed。

## 决策建议

呈现给 user 拍板：

1. **现在合入 origin/master + Push**，P1-1/P1-2 归档为 follow-up Feature（推荐）：
   - F097 当前实施已交付 H3-A 临时 Subagent 显式建模的核心价值
   - 71 单测 + 全量 3355 + e2e_smoke 5x PASS = 充分质量守护
   - per-Phase Codex 8 high + 11 medium 全闭环，Final 1 medium 已修
   - P1-1/P1-2 是 spawn 后部分行为的边界 bug，不影响 spec 主路径 AC

2. **现在不合入，先修复 P1-1 + P1-2**（保守）：
   - 投入 ~6-10h 完整修复
   - 风险：可能再发现新 finding（已经投入 ~10h）

3. **拒绝合入**（不推荐）：F097 已是 M5 阶段 2 起点，下游 F098 / F099 / F100 阻塞

## 测试

- 全量回归（exclude e2e）：3355 passed / 0 failed
- e2e_smoke 5x 循环：8/8 PASS × 5 次
- Per-Phase Codex review 8 high + 11 medium 全闭环
- Final Cross-Phase: 2 P1 归档 + 1 P2 已修
