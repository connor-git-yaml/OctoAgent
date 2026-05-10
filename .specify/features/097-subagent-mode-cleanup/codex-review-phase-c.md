# F097 Phase C — Codex Adversarial Review 闭环

**日期**: 2026-05-10
**审视命令**: `cat /tmp/f097-phase-c-codex-review.txt | codex review -`
**审视范围**: 2 个代码文件
- `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
- `octoagent/apps/gateway/tests/services/test_agent_context_phase_c.py`
**审视模型**: GPT-5.4 high (Codex 默认)

## Findings 闭环表

| ID | 严重度 | 文件:行 | 描述 | 处理决策 | 实施动作 |
|----|--------|---------|------|---------|---------|
| **P2-1** | medium | agent_context.py:1304 (扩散到 L657 / L982 / L3490) | Subagent 命中 ephemeral 路径后 `AgentProfile.kind=="subagent"`，但 3 处 `BehaviorLoadProfile` 选择只 `kind=="worker" → WORKER`，subagent fall through 到 FULL（加载主 Agent 完整 9 文件）；与已有 `BehaviorLoadProfile.MINIMAL`（4 文件 AGENTS+TOOLS+IDENTITY+USER）的 Subagent 语义不符 | **接受** | 3 处 BehaviorLoadProfile 选择全部加 subagent→MINIMAL 优先分支：L657 / L982 (`build_task_context`) + L3490 (`_build_system_blocks`) |
| **P2-2** | medium | test_agent_context_phase_c.py:59-68 | 测试 helper `_make_ephemeral_profile_from_service` 复制 production 短路构造逻辑，验证的是复制品；production 改错测试也会通过 | **接受** | (1) 提取 `_build_ephemeral_subagent_profile` 为 production staticmethod helper；(2) production code 改用 helper 调用；(3) 测试改为直调真实 helper（删除复制 helper） |

## 总结

- High: 0
- Medium: 2（全部接受 + 闭环）
- Low: 0

## Codex 审视价值

P2-1 找到了**严重的 behavior pack 加载语义 bug**——若不修复，subagent 实际会加载主 Agent 完整 9 文件行为包（含 BOOTSTRAP），完全不符合 H3-A spawn-and-die 临时 subagent 设计意图。这正是 Codex adversarial review 的核心价值：从外部视角审视语义一致性，识别"AC 写到了但实施细节漏掉的副作用"。

P2-2 是**测试基础设施 finding**——production 提 helper + 测试调真实 helper 的模式与 F092 / F094 / F095 的 review 一致性原则相符（避免"测试通过但实施错"）。

## 测试结果

- Phase C 测试：10 → 12 passed（+2 P2-1 验证 + helper-based 测试加入 + 1 main fall-through verification）
- packages/core 全量：414 passed（vs Phase A baseline 0 regression）
- gateway 全量（exclude e2e）：1317 passed / 0 failed

## Commit Message 闭环说明

```
feat(F097-Phase-C): ephemeral AgentProfile (kind=subagent) + 3 处 BehaviorLoadProfile MINIMAL

- _resolve_context_bundle 短路：target_kind=subagent 时构造 ephemeral
  AgentProfile (kind=subagent, scope=PROJECT, profile_id=ULID)，不写持久化 store
- 提取 _build_ephemeral_subagent_profile 为 staticmethod helper（Codex P2-2 闭环）
- 3 处 BehaviorLoadProfile 选择加 subagent→MINIMAL 优先分支（Codex P2-1 闭环）：
  - build_task_context L657 (load_profile_for_emit)
  - build_task_context L982 (load_profile_emit, BEHAVIOR_PACK_LOADED 一致性)
  - _build_system_blocks L3490 (effective_load_profile)
- 12 单测覆盖：AC-C1 字段 / AC-C2 scope / 不持久化 / 短路决策 / regression
  worker / regression main / P2-1 MINIMAL 派生 / fall-through 验证

AC 对齐: AC-C1 / AC-C2 / 间接验证 P2-1 修复
Codex review: 0 high / 2 medium 全闭环 / 0 low
回归: packages/core 414 / gateway 1317 (0 regression vs Phase A baseline)
```
