# F098 Quality Checklist — GATE_DESIGN 阶段产出

**日期**: 2026-05-10
**关联**: spec.md v0.1 + clarification.md（9 OD 已 batch 接受推荐）
**评估结论**: **GO（通过 GATE_DESIGN）**

---

## 1. Spec 完整性（M1-M5）

| ID | 检查项 | 状态 | 备注 |
|----|--------|------|------|
| M1 | spec.md 含完整范围（What）| ✅ | §3 9 块（B/C/D/E/F/G/H/I/J）|
| M2 | 不在范围（Out of Scope）已明确 | ✅ | §4 11 项 |
| M3 | 关键实体定义完整（model / payload / API）| ✅ | §7 4 类实体 |
| M4 | User Stories 优先级合理 | ✅ | §6 7 stories（P1×4 / P2×3）|
| M5 | Phase 顺序 + 依赖关系清晰 | ✅ | §12 mermaid-style 流程图 |

## 2. AC 覆盖（M6-M11）

| ID | 检查项 | 状态 | 备注 |
|----|--------|------|------|
| M6 | 每块（B-J）至少 2 条 AC | ✅ | §5 共 28 条 AC |
| M7 | audit chain 验收单独章节 | ✅ | AC-AUDIT-1 列 5 维度 |
| M8 | 向后兼容验收 | ✅ | AC-COMPAT-1/2 |
| M9 | 事件可观测验收 | ✅ | AC-EVENT-1/2/3 |
| M10 | 范围边界验收 | ✅ | AC-SCOPE-1（F099/F100/F107 不动）|
| M11 | 全局回归 + Codex review 验收 | ✅ | AC-GLOBAL-1 ~ 5 |

## 3. 风险评估（R1-R6）

| ID | 检查项 | 状态 | 备注 |
|----|--------|------|------|
| R1 | Phase H task state machine 改造风险 | ✅ | Codex review 强制 + callback 异常隔离 |
| R2 | Phase D orchestrator.py 拆分 import 风险 | ✅ | re-export 兼容性策略已定 |
| R3 | Phase E 向后兼容（历史 USER_MESSAGE 数据）| ✅ | merge_control_metadata 合并两类 events + migration test |
| R4 | Phase G EventStore API 演化 | ✅ | append_event_pending 是新 API（向后兼容）|
| R5 | Phase F subagent runtime 数量增长 | ✅ | runtime 与 task 同生命周期 + inactive 不复用 |
| R6 | worker→worker 死循环 | ✅ | DelegationManager max_depth=2（F084）|

## 4. Constitution 兼容性（C1-C10）

| ID | 检查项 | 状态 | 备注 |
|----|--------|------|------|
| C1-1 | C1 Durability First | ✅ | spec §11 已映射；CONTROL_METADATA_UPDATED + BaseDelegation 持久化 |
| C2-1 | C2 Everything is an Event | ✅ | CONTROL_METADATA_UPDATED 是 first-class event |
| C3-1 | C3 Tools are Contracts | ✅ | 工具 schema 不变；delegate_task 语义保持 |
| C4-1 | C4 Side-effect Two-Phase | ✅ | 不涉及不可逆操作 |
| C5-1 | C5 Least Privilege | ✅ | A2A receiver 在自己 secret scope（F095 SOUL.worker.md 协同）|
| C6-1 | C6 Degrade Gracefully | ✅ | atomic rollback / callback 异常隔离 / fallback 路径 |
| C7-1 | C7 User-in-Control | ✅ | 不改取消 / 审批路径 |
| C8-1 | C8 Observability | ✅ | audit chain 5 维度对齐 + CONTROL_METADATA_UPDATED 可观测 |
| C9-1 | C9 Agent Autonomy | ✅ | LLM 决策 worker→worker 委托时机 |
| C10-1 | C10 Policy-Driven Access | ✅ | 不改权限决策；删除 enforce_child_target_kind_policy 是架构决策不是权限决策 |

## 5. 工作流强制规则（W1-W5）

| ID | 检查项 | 状态 | 备注 |
|----|--------|------|------|
| W1 | 每 Phase Codex review | ✅ | 已纳入 plan |
| W2 | Final cross-Phase Codex review | ✅ | 已纳入 plan |
| W3 | completion-report.md 必产出 | ✅ | 已纳入 plan |
| W4 | handoff.md（给 F099）必产出 | ✅ | 已纳入 plan |
| W5 | Phase 跳过 / 偏离显式归档 | ✅ | 已纳入 plan |

## 6. 数据指标（D1-D6）

| ID | 检查项 | 状态 | 备注 |
|----|--------|------|------|
| D1 | 单测覆盖目标（≥ 50）| ✅ | spec §9 |
| D2 | 回归基线（≥ 3355）| ✅ | spec §9 |
| D3 | e2e_smoke 5x（40/40）| ✅ | spec §9 |
| D4 | 代码改动估计（+800 / -200）| ✅ | spec §9 |
| D5 | commits 估计（8-10）| ✅ | spec §9 |
| D6 | 新事件类型（+1 CONTROL_METADATA_UPDATED）| ✅ | spec §9 |

---

## GATE_DESIGN 决策汇总

**结论**：**GO**（通过 GATE_DESIGN）

- 27 项 checklist 全部 ✅
- 9 OD（OD-1 ~ OD-9）batch 接受推荐
- spec v0.1 → v0.2 GATE_DESIGN 锁定
- plan 阶段可以启动

**Caveats（implementation 阶段需关注）**：

1. **Phase H 必走 Codex review**（task state machine 改造）
2. **Phase D 必走 Codex review**（最大文件拆分）
3. **Phase E migration test**（向后兼容必须有专测）
4. **EventStore API 演化文档化**（append_event_pending 内部约定）
5. **callback 注入 vs 反向 import 决策**（plan 阶段细化设计）
6. **Phase 顺序刚性约束**（E + F → B → C → I → H → G → J → D，不得乱序）

---

**quality-checklist 完成。GATE_DESIGN 通过，进入 plan 阶段。**
