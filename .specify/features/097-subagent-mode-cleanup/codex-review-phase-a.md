# F097 Phase A — Codex Adversarial Review 闭环

**日期**: 2026-05-10
**审视命令**: `cat /tmp/f097-phase-a-codex-review.txt | codex review -`
**审视范围**: git uncommitted/untracked 中 3 个代码文件（delegation.py / __init__.py / test_subagent_delegation_model.py）
**审视模型**: GPT-5.4 high (Codex 默认)

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 | 实施动作 |
|----|--------|---------|------|---------|---------|
| **P2-1** | medium | delegation.py:366 | `target_kind: DelegationTargetKind` 字段类型完整接受 worker 等枚举值，与 model 边界"固定 SUBAGENT"冲突；从 metadata 反序列化时可错误构造 | **接受** | 改 `target_kind: Literal[DelegationTargetKind.SUBAGENT]`；新增单测 `test_target_kind_rejects_non_subagent_value` |
| **P2-2** | medium | delegation.py:338 | 必填 ID（delegation_id / parent_task_id 等 7 字段）接受空字符串，与 Work / DelegationEnvelope 现有 `min_length=1` 约束不一致 | **接受** | 7 个必填 ID 加 `Field(..., min_length=1)`；新增单测 `test_required_ids_reject_empty_string` |

## 总结

- High: 0
- Medium: 2（全部接受 + 闭环）
- Low: 0

## Codex 审视价值

两条 finding 均是合理的 boundary hardening：
1. P2-1 防止 model 边界泄漏到反序列化路径（特别重要因为 CL#16 决策走 `child_task.metadata` JSON 路径）
2. P2-2 与项目已有 model 约定一致（防御性编程，避免下游回查时拿到 '' 引发隐性 bug）

## 测试结果

- 修复后 `test_subagent_delegation_model.py`：14 → 17 passed（新增 3 Codex-hardening tests）
- packages/core 全量：411 → 414 passed（+3，0 regression vs Phase 0 baseline）

## Commit Message 闭环说明

```
feat(F097-Phase-A): SubagentDelegation Pydantic model

- 新增 SubagentDelegation 含 12 字段（含 GATE_DESIGN C-1 引入的
  child_agent_session_id；CL#16 持久化路径走 child_task.metadata）
- target_kind: Literal[SUBAGENT]（Codex P2-1 防 model 边界泄漏）
- 必填 ID min_length=1（Codex P2-2 与 Work/DelegationEnvelope 一致）
- 17 单测覆盖（含 round-trip + Codex hardenings）

AC 对齐: AC-A1 / AC-A2 / AC-A3
Codex review: 0 high / 2 medium 已处理 / 0 low
回归: packages/core 414 passed (Phase 0 baseline 411 + 3 新)
```
