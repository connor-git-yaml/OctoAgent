# F097 Subagent Mode Cleanup — Completion Report

**Feature**: F097 H3-A 临时 Subagent 显式建模
**Branch**: `feature/097-subagent-mode-cleanup`（vs origin/master cc64f0c）
**完成日期**: 2026-05-10
**完成方式**: spec-driver-feature 完整编排（Phase 0 → A → C → E → B → D → F → G → Verify）+ 7 commits

## 计划 vs 实际对照

| Phase | 计划范围 | 实际 commit | 状态 |
|-------|---------|------------|------|
| Phase 0 | 实测侦察 | (含在 88f8773) | ✅ 完成（baseline 3252 / SUBAGENT_COMPLETED 未定义 / 函数名/字段修正）|
| Phase A | SubagentDelegation Pydantic model | `88f8773` | ✅ 完成（17 单测 + Codex 0H / 2M 闭环）|
| Phase C | ephemeral AgentProfile (kind=subagent) + MINIMAL profile | `977e5f1` | ✅ 完成（12 单测 + Codex 0H / 2M 闭环 P2-1 严重 BehaviorLoadProfile 派生 bug）|
| Phase E | cleanup hook + SUBAGENT_COMPLETED enum/emit | `6c01338` | ✅ 完成（13 单测 + Codex 2H / 2M 闭环 P1-1 真实路径不工作 / P1-2 幂等失效）|
| Phase B | SUBAGENT_INTERNAL session 路径 + spawn 写 SubagentDelegation | `7a402c3` | ✅ 完成（14 单测 + Codex 3H / 3M 闭环 — race 严重）|
| Phase D | RuntimeHintBundle 拷贝 + Phase B race 联合修复 | `620100a` | ✅ 完成（7 单测 + Codex 2H / 1M + 3 regression 修复）|
| Phase F | Memory α 共享引用 | `4756177` | ✅ 完成（10 单测 + Codex 1H / 2M 闭环 — memory.write 路径接 caller scope）|
| Phase G | BEHAVIOR_PACK_LOADED.agent_kind 验证 + AC-AUDIT-1 / AC-COMPAT-1 | `fb88237` | ✅ 完成（9 单测，无 Codex review per "不需要" 节点）|
| Verify | 全量回归 + e2e_smoke 5x + Final Codex review | (含在最后调整) | ✅ 完成（3355 passed / e2e 8/8 × 5）|

## Codex Review 总闭环

### Per-Phase Reviews

| Phase | High | Medium | Low | 关键 finding |
|-------|------|--------|-----|------------|
| A | 0 | 2 | 0 | Literal[SUBAGENT] 防边界泄漏 / 必填 ID min_length=1 |
| C | 0 | 2 | 0 | **重要**: subagent → MINIMAL profile 派生（避免加载主 Agent 完整 9 文件含 BOOTSTRAP）|
| E | 2 | 2 | 0 | **重要**: real-path 不工作 — normalize 白名单过滤 subagent_delegation / EventStore.idempotency 防重复 emit |
| B | 3 | 3 | 0 | **最严重**: launch_child_task race / target_kind 信号丢失 / existing != None 跳过回填 |
| D | 2 | 1 | 0 | **重要**: __caller_runtime_hints__ 在 launch 后写入是 race（与 Phase B P1-2 同根 — 联合修复）+ regression 修复（preserve normalize 字段）|
| F | 1 | 2 | 0 | **重要**: memory.write SUBAGENT_INTERNAL 路径接 caller scope（避免 SCOPE_UNRESOLVED 拒绝）|
| G | — | — | — | 跳过（纯测试新增，命中"不需要 Codex review"节点）|
| **Per-Phase 总计** | **8 high** | **11 medium** | **0 low** | **全部接受 + 闭环**（部分推迟项已显式归档到下游 Phase / Feature）|

### Final Cross-Phase Review

| ID | 严重 | 处理 |
|----|------|------|
| Final P1-1 | high | USER_MESSAGE event 复用为 control_metadata 承载体污染 latest_user_text → **归档为 known issue 待 user 拍板** |
| Final P1-2 | high | ephemeral subagent profile 与 caller worker runtime 复用导致 audit 混叠 → **归档为 known issue 待 user 拍板** |
| Final P2-1 | medium | B-3 backfill USER_MESSAGE 缺 normalize → **已修复**（preserve 历史 control_metadata）|

详细分析：[codex-review-final.md](codex-review-final.md)

## AC 覆盖

22 spec AC 全部覆盖：

| AC | 状态 | 测试位置 |
|----|------|---------|
| AC-A1 / A2 / A3 (SubagentDelegation 建模 + child_agent_session_id + task metadata 持久化) | ✅ | test_subagent_delegation_model.py (17) |
| AC-B1 / B2 (SUBAGENT_INTERNAL 第 4 路 + main/worker regression) | ✅ | test_agent_context_phase_b.py (14) |
| AC-C1 / C2 (ephemeral profile 不持久化 + scope 跟随 caller) | ✅ | test_agent_context_phase_c.py (12) |
| AC-D1 / D2 (RuntimeHintBundle 拷贝 + worker 路径不变) | ✅ | test_capability_pack_phase_d.py (7) |
| AC-E1 / E2 / E3 (cleanup 三态 + 幂等 + RecallFrame 保留) | ✅ | test_task_runner_subagent_cleanup.py (13) |
| AC-F1 / F2 / F3 (Memory α 共享引用) | ✅ | test_agent_context_phase_f.py (10) |
| AC-G1 (BEHAVIOR_PACK_LOADED.agent_kind="subagent") | ✅ | test_behavior_pack_loaded_phase_g.py (9) |
| AC-AUDIT-1 (四层 audit chain) | ✅ | test_behavior_pack_loaded_phase_g.py |
| AC-COMPAT-1 (main/worker 不变) | ✅ | test_behavior_pack_loaded_phase_g.py |
| AC-EVENT-1 (SUBAGENT_SPAWNED + SUBAGENT_COMPLETED) | ✅ | test_task_runner_subagent_cleanup.py |
| AC-SCOPE-1 (F098/F099/F100 不动) | ✅ | git diff 验证 |
| AC-GLOBAL-1 ~ 6 (回归 + e2e + Codex + report) | ✅ | 全程贯穿 |

**总测试**：71 单测覆盖 22 AC（vs F095 final 3191 / F096 final 3260 / F097 final 3355 = +95 累计新增）

## 数据指标

- **Commits**: 7（88f8773 / 977e5f1 / 6c01338 / 7a402c3 / 620100a / 4756177 / fb88237 + Final cross-Phase 修复）
- **代码改动**: 38 文件 / +9207 / -69（含 22 spec-driver 制品 + 16 实施代码 / 测试）
- **实施代码**: 11 src 文件改动（agent_context.py 主要 / capability_pack.py / task_runner.py / memory_tools.py / connection_metadata.py / 5 个 model）
- **测试代码**: 7 测试文件新建（包含 71 单测）
- **vs F096 baseline**: 0 regression（3252 → 3355 = +103 累计新增）
- **e2e_smoke**: 8/8 PASS × 5 次循环 = 40/40

## F098 接入点（必读）

F098 (A2A Mode + Worker↔Worker) 必须接管的 F097 接入点：

1. **F096 H2 推迟项 AC-F1 worker_capability 路径**：F096 H2 推迟到 F098 — delegate_task fixture 完备时 audit chain 完整集成测
2. **Phase E P2-3 事务边界**：session.save + event.append 跨事务设计（涉及 spawn 路径联合设计，需在 F098 spec 阶段一并处理）
3. **Phase B P2-4 终态统一层**：cleanup 挪到 task_service._write_state_transition（涉及 task state machine 改造）
4. **Final P1-1 USER_MESSAGE 复用**：引入新 event type 或 synthetic marker 跳过 compaction（影响 ContextCompactionService）
5. **Final P1-2 ephemeral runtime 独立**：subagent 应有独立 _ensure_agent_runtime 路径（避免与 caller worker runtime 复用）
6. **F097 SubagentDelegation vs F098 A2A WorkerDelegation 概念边界**：BaseDelegation 公共抽象在 F098 评估
7. **agent_kind enum 演化**：F098 是否新增 agent_kind="worker_a2a" 等

## Phase 跳过 / 偏离归档

无显式跳过 Phase。但有以下实施偏离已归档：

- Phase B/D 都需要 round 2 修复（race + normalize regression）— 详见 codex-review-phase-b.md / codex-review-phase-d.md
- Phase E P2-3 / P2-4 显式归档推迟到 Phase B（实际 Phase B 收口）
- Phase D P1-2 caller hints 真拷贝架构限制（surface 唯一真拷贝，其他归档）
- Final P1-1 / P1-2 归档为 known issue 待 user 拍板

## 风险评估（vs 原 plan §6 R1-R5）

| 风险 | 计划评估 | 实际验证 |
|------|---------|---------|
| R1 Memory 共享语义 spec 不清晰 | 高 | ✅ GATE_DESIGN α 锁定 + Phase F 实施验证 |
| R2 _ensure_agent_session 改动破坏现有 | 中 | ✅ 0 regression（main/worker 路径 14 测试 + 全量 3355）|
| R3 cleanup hook 时机错误 | 中 | ✅ Codex P2-5 闭环 + TERMINAL_STATES 检测 |
| R4 ephemeral profile_id 冲突 | 低 | ✅ ULID 生成 + 不持久化（Phase C 验证）|
| R5 list_subagent_sessions parent_worker_runtime_id 准确性 | 中 | ⚠️ Final P1-2 部分相关（runtime 复用问题）— 归档 |

## Constitution 兼容性（最终验证）

| 原则 | 状态 |
|------|------|
| C1 Durability First | ✅ SubagentDelegation + Task + AgentSession 全持久化 |
| C2 Everything is an Event | ✅ SUBAGENT_SPAWNED / SUBAGENT_COMPLETED / BEHAVIOR_PACK_LOADED 全 emit |
| C3 Tools are Contracts | ✅ memory.write schema 不变 |
| C4 Side-effect Two-Phase | ✅ 不涉及 |
| C5 Least Privilege | ✅ 不涉及 |
| C6 Degrade Gracefully | ✅ cleanup 幂等 + fail-closed + 异常隔离 |
| C7 User-in-Control | ✅ subagents.kill 已存在 |
| C8 Observability | ✅ 7 类新 event + audit chain 四层对齐 |
| C9 Agent Autonomy | ✅ spawn 时机 LLM 决策 |
| C10 Policy-Driven Access | ✅ 不涉及 |

## 下一步建议

**user 拍板路径**：

| 选项 | 描述 | 推荐 |
|------|------|------|
| **A** | 现在合入 origin/master + Push，Final P1-1/P1-2 归档为 follow-up Feature | ✅ 推荐 |
| **B** | 现在不合入，先修复 Final P1-1 + P1-2（投入 ~6-10h） | 保守 |
| **C** | 拒绝合入 | 不推荐（F097 是 M5 阶段 2 起点，阻塞 F098/F099/F100）|

详见 [codex-review-final.md §决策建议](codex-review-final.md)。

## 文件清单

```
.specify/features/097-subagent-mode-cleanup/
├── spec.md (v0.2 GATE_DESIGN 已锁)
├── plan.md (8 Phase + Verify)
├── tasks.md (46 任务)
├── trace.md (编排时间线)
├── analysis.md (一致性 86.4% + GO with caveats)
├── clarification.md (8 歧义 6 auto + 2 critical)
├── quality-checklist.md (27 项 GO with caveats)
├── research/tech-research.md (实测 9 BAP / 6 真 Gap)
├── phase-0-recon.md (baseline 3252)
├── phase-a-impl.md / codex-review-phase-a.md
├── phase-b-impl.md / codex-review-phase-b.md
├── phase-c-impl.md / codex-review-phase-c.md
├── phase-d-impl.md / codex-review-phase-d.md
├── phase-e-impl.md / codex-review-phase-e.md
├── phase-f-impl.md / codex-review-phase-f.md
├── phase-g-impl.md
├── codex-review-final.md
├── completion-report.md (本文件)
└── handoff.md
```
