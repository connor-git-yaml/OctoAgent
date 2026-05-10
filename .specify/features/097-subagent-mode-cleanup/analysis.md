# F097 Subagent Mode Cleanup — 一致性分析报告（spec ↔ plan ↔ tasks）

**分析日期**: 2026-05-10
**分析范围**: spec.md (v0.2) / plan.md / tasks.md / 三向追溯
**判定**: **GO with caveats** — 0 CRITICAL / 2 HIGH / 6 MEDIUM / 4 LOW

---

## 1. 执行摘要

22 个 AC 三向追溯通过率 **86.4% (19/22)**，3 个 ⚠️ 需修复后再实施。
46 任务全部有 Phase 归属，每 Phase 三件套（Codex review + regression + commit）齐全，GATE_DESIGN 5 项决策在 plan 和 tasks 中均有明确落点。

---

## 2. 发现表

| ID | 类别 | 严重性 | 位置 | 摘要 | 修复建议 |
|----|------|--------|------|------|---------|
| F-01 | 覆盖缺口 | HIGH | spec AC-EVENT-1 ↔ tasks | AC-EVENT-1 要求验证 SUBAGENT_COMPLETED 事件（若 F092 未实现则补充）。clarification Q-6 推迟到 plan 侦察，T0.1 仅侦察存在性，无任何 task 负责"侦察结果为不存在时触发实施" | tasks Phase E 或 G 增加条件实施 task，或 TE.1 描述明确补充 SUBAGENT_COMPLETED emit 逻辑（条件路径） |
| F-02 | 不一致 | HIGH | plan §1 依赖图 ↔ tasks Phase E 注释 | plan §1 写"Phase E 依赖 Phase B"，但实施顺序是 E 先于 B。tasks Phase E 头部注释明确"cleanup 函数若无 SUBAGENT_INTERNAL session 则静默跳过" | 修正 plan §1，将"Phase E 依赖 Phase B"改为"Phase E 仅依赖 Phase A，cleanup hook 在 Phase B 完成前静默跳过" |
| F-03 | 规格不足 | MEDIUM | AC-D1 ↔ TD.1 | spec AC-D1 写 `recent_worker_lane_*`；tasks TD.1 写 `recent_failure_budget`；不完全对应；具体字段以 T0.3 侦察 behavior.py 结果为准 | 统一 spec/plan/tasks，注明"具体字段以 T0.3 侦察结果为准，至少含 surface + tool_universe；recent_worker_lane_* / recent_failure_budget 待 T0.3 确认" |
| F-04 | 不一致 | MEDIUM | plan 附录 Impact Assessment | "直接修改文件数 6"与各 Phase 文件清单实际 7-9 个不一致（agent_context.py 跨 C/B/F 三次） | 修正附录文件数统计，补充"agent_context.py 横跨 Phase C/B/F 三次修改"说明 |
| F-05 | 歧义 | MEDIUM | tasks TB.2 文件位置 | TB.2 描述含"或拆为 delegation_plane.py / capability_pack.py"，文件路径未确定 | T0.2 侦察后在 phase-0-recon.md 明确 TB.2 注入点，TB.1 commit 前更新 tasks.md |
| F-06 | 不一致 | MEDIUM | spec AC-GLOBAL-1 ↔ plan §5 / TVERIFY.1 | spec 写 `≥ 3191 passed`（F095 baseline），plan/tasks 写 `≥ 3260`（F096 final 正确值） | **修复 spec AC-GLOBAL-1 为 ≥ 3260**（与 plan/tasks 对齐） |
| F-07 | 宪法对齐 | MEDIUM | spec §11 C2 ↔ Gap-E | Session status=CLOSED 状态迁移无对应 event emit 立场（SESSION_CLOSED vs "由 SUBAGENT_COMPLETED 覆盖"均缺失） | spec Gap-E 或 AC-EVENT-1 明确立场：若不 emit 独立 SESSION_CLOSED 则说明"SUBAGENT_COMPLETED 已覆盖" |
| F-08 | 重复 | LOW | spec §0 ↔ clarification.md | spec §0 决策表与 clarification CRITICAL 拍板结论重复（措辞微小差异） | clarification.md 末尾加"已并入 spec §0"的简短说明 |
| F-09 | 覆盖缺口 | LOW | tasks AC-GLOBAL-6 | "Phase 跳过显式归档"无专门触发机制，TVERIFY.5 仅产出 completion-report 整体任务 | TVERIFY.5 增加"扫描 Phase 是否有跳过，若有则在 completion-report 显式归档" |
| F-10 | 歧义 | LOW | plan §4 Codex review 节点 ↔ tasks TG.4 | plan §4 表中 Phase G 缺失，但 tasks TG.4 明确"Phase G 无 per-Phase Codex review" | plan §4 表末加 `Phase G \| 跳过 \| — \| —` 明确记录 |
| F-11 | 不一致 | LOW | tasks FR 覆盖映射表 ↔ TVERIFY.6 | TVERIFY.6（handoff.md）在 FR 覆盖表中无 AC 映射 | 补充 TVERIFY.6 标注"产出文档型任务，无 spec AC 对应"|
| F-12 | 规格不足 | LOW | AC-F2 ↔ TF.1 注入点 | AC-F2 注入点依赖 TB.1/TB.2 实施结果，未预设 | plan §2 Phase F 补充"TF.1 注入点在 SubagentDelegation 创建时（Phase B TB.1/TB.2 确定的位置），AC-F2 与 TB.2 child_agent_session_id 写回路径并行" |

---

## 3. 三向追溯矩阵（22 AC × Plan Phase × Task ID）

| AC | Plan Phase | Task ID | 对齐 |
|----|-----------|---------|------|
| AC-A1 (含 child_agent_session_id) | Phase A | TA.1, TA.2, TA.3 | ✅ |
| AC-A2 | Phase A | TA.1, TA.3 | ✅ |
| AC-A3 (CL#16) | Phase A | TA.1, TA.3 | ✅ |
| AC-B1 | Phase B | TB.1, TB.2, TB.3 | ✅ |
| AC-B2 | Phase B | TB.3, TB.4 | ✅ |
| AC-C1 | Phase C | TC.1, TC.2 | ✅ |
| AC-C2 | Phase C | TC.1, TC.2 | ✅ |
| AC-D1 | Phase D | TD.1, TD.2 | ⚠️ F-03 字段不一致 |
| AC-D2 | Phase D | TD.1, TD.2 | ✅ |
| AC-E1 | Phase E | TE.1, TE.2, TE.3 | ✅ |
| AC-E2 | Phase E | TE.1, TE.3 | ✅ |
| AC-E3 | Phase E | TE.1, TE.3 | ✅ |
| AC-F1 (α) | Phase F | TF.2, TF.3 | ✅ |
| AC-F2 | Phase F | TF.1, TF.3 | ⚠️ F-12 注入点依赖 |
| AC-F3 | Phase F | TF.4 | ✅ |
| AC-G1 | Phase G | TG.1 | ✅ |
| AC-AUDIT-1 | Phase G + Verify | TG.1, TVERIFY.3 | ✅ |
| AC-COMPAT-1 | Phase G | TG.2 | ✅ |
| AC-EVENT-1 | Verify | TVERIFY.3 | ⚠️ F-01 SUBAGENT_COMPLETED 条件路径缺失 |
| AC-SCOPE-1 | Verify | TVERIFY.3 | ✅ |
| AC-GLOBAL-1 | Verify | TVERIFY.1 | ⚠️ F-06 数字不一致 (spec 3191 vs plan/tasks 3260) |
| AC-GLOBAL-2 | Verify | TVERIFY.2 | ✅ |
| AC-GLOBAL-3 | Phase A/C/E/B/D/F | TA.4, TC.4, TE.4, TB.5, TD.3, TF.5 | ✅ |
| AC-GLOBAL-4 | Verify | TVERIFY.4 | ✅ |
| AC-GLOBAL-5 | Verify | TVERIFY.5 | ✅ |
| AC-GLOBAL-6 | Verify | TVERIFY.5 | ⚠️ F-09 无专门触发机制 |

---

## 4. 跨 Feature 文件冲突检测（Pass G）

F097 核心文件路径在近期活跃 Feature 中**无并行文件重叠**（F096 已完成合入 master baseline cc64f0c，无并行 Feature）。

**Pass G: CLEAN**

---

## 5. 宪法对齐审查

| Constitution 原则 | 状态 | 备注 |
|------------------|------|------|
| C1 Durability First | ✅ | SubagentDelegation + Task 持久化 |
| C2 Everything is an Event | ⚠️ | Session CLOSED 状态迁移立场缺失 (F-07) |
| C3 Tools are Contracts | ✅ | tools schema 不变 |
| C4 Side-effect Two-Phase | ✅ | 不涉及 |
| C5 Least Privilege | ✅ | 不涉及 |
| C6 Degrade Gracefully | ✅ | cleanup 幂等 + 静默跳过 |
| C7 User-in-Control | ✅ | subagents.kill 已存在 |
| C8 Observability | ⚠️ | Gap-D RuntimeHintBundle 拷贝无 emit 声明（LOW）|
| C9 Agent Autonomy | ✅ | spawn 时机 LLM 决策 |
| C10 Policy-Driven Access | ✅ | 不涉及 |

---

## 6. GATE_TASKS 判定

**GO with caveats** — 修复 2 个 HIGH (F-01, F-02) + 1 个关键 MEDIUM (F-06) 后启动 Phase 6 implement。

修复落点：
- F-01 → 更新 spec AC-EVENT-1 + tasks 增条件实施路径
- F-02 → 修正 plan §1 依赖图文字
- F-06 → 修正 spec AC-GLOBAL-1 数字 3191 → 3260
