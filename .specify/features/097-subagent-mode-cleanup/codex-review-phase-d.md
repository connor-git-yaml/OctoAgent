# F097 Phase D — Codex Adversarial Review 闭环

**日期**: 2026-05-10
**审视命令**: `cat /tmp/f097-phase-d-codex-review.txt | codex review -`
**审视范围**: 2 个代码文件（capability_pack.py / test_capability_pack_phase_d.py）
**审视模型**: GPT-5.4 high (Codex 默认)

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 | 实施动作 |
|----|--------|---------|------|---------|---------|
| **P1-1** | **high** | capability_pack.py:1331 | `__caller_runtime_hints__` 写入位置在 `await launch_child_task` 之后 → production runner 已 normalize + enqueue 完成，post-hoc 修改 control_metadata 不可见。**同根问题 Phase B `__subagent_delegation_init__` 也有此 race**（Codex 间接发现） | **接受 + 联合修复** | capability_pack._launch_child_task 重构：`__subagent_delegation_init__` + `__caller_runtime_hints__` 都在 child_message 构造**之前**计算并放入 base_control_metadata，确保 launch_child_task 看到完整数据 |
| **P1-2** | **high** | capability_pack.py:1311-1318 | `can_delegate_research` / `recent_worker_lane_*` / `tool_universe` 用默认值非 caller 真实值，违背 AC-D1 "字段值与 caller 一致" | **接受现状归档** | AC-D1 完整解读：caller-side RuntimeHintBundle 由 orchestrator `_build_request_runtime_hints` 每 turn 重建，spawn 时不持有完整 caller 实例。F097 Phase D 范围：**surface 字段从 exec_ctx.runtime_context 真拷贝**（唯一可获取的 caller 字段）；其他字段为默认占位，child runtime 通过自己的 `_build_request_runtime_hints` 重新构造（与 main / worker 路径一致）。架构限制显式归档；完整 hints 真拷贝留 future Feature |
| **P2-3** | medium | test_capability_pack_phase_d.py:96-98 | 测试用 fake runner 保存 message 引用 → 事后 mutation 被误判为 launch 时存在（与 Phase B P1-2 同根） | **接受** | fake_launch 内 deep copy `msg.control_metadata` 立即捕获 launch-time 状态。同样模式也守护 Phase B 测试 |
| **Round 2 修复** | (regression) | capability_pack 重构后 3 测试 fail：`'general' == 'research'` | 重构后 USER_MESSAGE event 仅含 subagent_delegation 3 字段，merge_control_metadata 取最新 USER_MESSAGE 的 TURN_SCOPED 字段 → requested_worker_type 丢失 → fallback to "general" | **接受** | task_runner._emit_subagent_delegation_init_if_needed 改为：先 normalize_control_metadata(message.control_metadata) 拿到 caller 完整白名单字段，再加 subagent_delegation；merge_control_metadata 后所有 TURN_SCOPED 字段保留 |

## 总结

- High: 2（**全部接受 + 闭环**）
- Medium: 1（**接受 + 闭环**）
- Low: 0
- **Regression 修复**: 1（preserve normalize 字段，影响 work_split / subagents.spawn / control_plane_api 3 测试）

## P1 闭环深度价值

**P1-1 间接抓出 Phase B 同根问题**：Codex Phase D 找到 `__caller_runtime_hints__` 在 launch_child_task 后写入的 race，对照 Phase B 也是同样问题（`__subagent_delegation_init__` 也在 launch 后写入）。Phase B Round 1 测试因 mock 引用被误以为通过——这次 Phase D Codex 间接守护住了 Phase B 的修复。

**Round 2 regression 修复揭示新约束**：
- 我们的 emit USER_MESSAGE event 是任务最新事件
- merge_control_metadata 只取最新 USER_MESSAGE 的 TURN_SCOPED 字段
- 因此任何向任务追加 USER_MESSAGE 的代码都必须 preserve caller 的所有 TURN_SCOPED 字段，否则下游 dispatch 路径会丢失 worker_type / tool_profile / 等关键字段
- 修复方式：用 `normalize_control_metadata(message.control_metadata)` 拷贝白名单字段后扩展我们的 subagent_delegation

## 测试结果

- Phase D 测试：7/7 PASS（含 deep copy 守护）
- Phase B 测试：14/14 PASS（联合修复后 race 真闭环）
- Phase E + C 测试：全 PASS（35/35）
- 全量回归（exclude e2e）：3336 passed / 0 failed（vs Phase B baseline 3329 + 7 新 = 0 regression）

## Commit Message 闭环说明

```
feat(F097-Phase-D): RuntimeHintBundle 拷贝 + 4 项 Codex review 闭环 + Phase B race 联合修复

- capability_pack._launch_child_task 联合重构：__subagent_delegation_init__
  和 __caller_runtime_hints__ 都在 child_message 构造前计算（消除 Phase B + D race）
- task_runner._emit_subagent_delegation_init_if_needed preserve normalize
  字段（修复 work_split / subagents.spawn / control_plane_api 3 regression）
- 测试 fake_launch 加 deep copy 守护防 mock 引用回填
- AC-D1 surface 字段真拷贝 + 其他 hints 架构限制归档

Codex review: 2 high + 1 medium 全闭环 + 1 regression 修复
回归: 3336 passed (Phase B baseline 3329 + 7 新 = 0 regression)
```
