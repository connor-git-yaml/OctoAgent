# F103 Tasks

> 按 plan.md Phase 顺序拆分到原子任务。每任务标 [Owner / Estimate / 依赖]。

---

## Phase A — 实测侦察（已完成）

| Task | 状态 | 产出 |
|------|------|------|
| T-A1 实测 docs/blueprint/ 现状 | ✅ | phase-0-recon.md §1 |
| T-A2 实测 F084-F102 收录度 | ✅ | phase-0-recon.md §2 |
| T-A3 实测 docs/codebase-architecture/ 现状 | ✅ | phase-0-recon.md §3 |
| T-A4 整理 F084-F102 13 实施记录 SoT | ✅ | phase-0-recon.md §4（指向 CLAUDE.local.md）|

---

## Phase B — 同步主体（5 子任务）

### B-1 milestones.md M5 重写

- **T-B1.1** 删除 milestones.md 行 411-426 占位段
- **T-B1.2** 写 M5 完成版章节（13 Feature 表 + commit hash + 阶段 0/1/2/3 划分）
  - 阶段 0：F090-F092（类型系统 / 状态机 / DelegationPlane）
  - 阶段 1：F093-F096（Worker 完整对等性 4 维）
  - 阶段 2：F097-F100（委托模式两路分离 + 决策环）
  - 阶段 3：F101-F103（Notification + Routine + Blueprint 修订）
- **T-B1.3** 新增"M5 后续修复"段（F081-F088 同期落地）
- **T-B1.4** 新增 M5→M6 切换段（M5 acceptance gate 全闭环）
- **T-B1.5** M3 carry-forward 段 F033/F038 标 ✅
- **T-B1.6** 跑 e2e_smoke + commit `docs(F103-Phase-B1): milestones.md M5 重写`

### B-2 module-design.md 同步

- **T-B2.1** §9.4 apps/kernel 追加 NotificationService / DailyRoutineService / ApprovalManager
- **T-B2.2** §9.5 workers 追加 Worker 完整对等性 4 维 + 8 文件白名单
- **T-B2.3** §9.6 packages/protocol 追加 BaseDelegation + SubagentDelegation + source_runtime_kind
- **T-B2.4** §9.10 packages/provider 重写 ProviderRouter + Multi-Transport
- **T-B2.5** 新增 §9.13 Harness Layer（F084）
- **T-B2.6** 新增 §9.14 Context Layer（USER.md SoT + Memory Candidates）
- **T-B2.7** 跑 e2e_smoke + commit `docs(F103-Phase-B2): module-design.md 同步 F084-F102`

### B-3 requirements.md 同步

- **T-B3.1** FR-A2A-1 Butler → Main Agent + H1 引用（placeholder anchor）
- **T-B3.2** FR-A2A-2 加 Worker 完整对等性 4 维 + H2 引用
- **T-B3.3** FR-A2A-2b 加 SubagentDelegation 细节 + H3-A 引用
- **T-B3.4** FR-A2A-3 加 Worker↔Worker 解禁 + source_runtime_kind + ask_back + H3-B 引用
- **T-B3.5** F033 / F038 carry-forward gate 关闭标记
- **T-B3.6** 新增 §5.1.9 FR-NOTIFY-1
- **T-B3.7** 新增 §5.1.10 FR-ROUTINE-1
- **T-B3.8** 跑 e2e_smoke + commit `docs(F103-Phase-B3): requirements.md 同步 F093-F102 + H1/H2/H3 引用`

### B-4 api-and-protocol.md 同步

- **T-B4.1** §10.2 A2AMessage envelope 加 source_runtime_kind 字段 + 5 值
- **T-B4.2** §10.2.1 A2A 状态映射加 WAITING_APPROVAL 改造说明（F101）
- **T-B4.3** 新增 §10.4 Notification API
- **T-B4.4** 新增 §10.5 Routine Audit API
- **T-B4.5** 新增 §10.6 EventType 清单（NOTIFICATION_DISPATCHED / ROUTINE_* / CONTROL_METADATA_UPDATED / SUBAGENT_COMPLETED / SUBAGENT_SPAWNED / AGENT_SESSION_TURN_PERSISTED / BEHAVIOR_PACK_LOADED / BEHAVIOR_PACK_USED / MEMORY_RECALL_COMPLETED 等 ≥ 10 个）
- **T-B4.6** 新增 §10.7 ask_back 三工具
- **T-B4.7** 跑 e2e_smoke + commit `docs(F103-Phase-B4): api-and-protocol.md 同步 F084-F102 新接口`

### B-5 architecture-audit.md 增补

- **T-B5.1** 在末尾追加 §14.9 F084-F088 完成审计
- **T-B5.2** 追加 §14.10 F090-F092 重构审计
- **T-B5.3** 追加 §14.11 F093-F096 Worker 完整对等审计
- **T-B5.4** 追加 §14.12 F097-F100 委托模式两路分离审计
- **T-B5.5** 追加 §14.13 F101-F102 Notification + Routine 审计
- **T-B5.6** A7 状态枚举重叠改 ✅（F091 完成）
- **T-B5.7** 跑 e2e_smoke + commit `docs(F103-Phase-B5): architecture-audit.md 增补 §14.9-14.13`

---

## Phase C — 哲学章节

- **T-C1** 新建 docs/blueprint/agent-collaboration-philosophy.md
- **T-C2** §0 章节定位 + §1 三条哲学概览
- **T-C3** §2 H1 管家 mediated 模式（定义 + 代码层 + 业界对照）
- **T-C4** §3 H2 完整 Agent 对等性（定义 + 代码层 F093-F096 + 业界对照）
- **T-C5** §4 H3 两种委托模式并存（H3-A Subagent + H3-B A2A + F097/F098/F099 代码层）
- **T-C6** §5 业界对照横向定位表（Hermes / OpenClaw / Agent Zero / Claude Code / Swarm / CrewAI）
- **T-C7** 更新 docs/blueprint.md 顶级索引子文档表加 agent-collaboration-philosophy.md 行
- **T-C8** 回填 B-3 requirements.md 中 H1/H2/H3 placeholder anchor
- **T-C9** 跑 e2e_smoke + commit `docs(F103-Phase-C): 新增 Agent 协作三条设计哲学章节`

---

## Phase D — D13 三层消息模型

- **T-D1** 新建 docs/codebase-architecture/message-model.md
- **T-D2** §1 三层关系总览 + ASCII diagram
- **T-D3** §2 Work 层（字段 + 状态机 + 生命周期）
- **T-D4** §3 DispatchEnvelope 层（contract_version + hop_count + source_runtime_kind + target + metadata）
- **T-D5** §4 A2AMessage 层（context_id + message_id + 6 类型 TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT）
- **T-D6** §5 字段映射表（三层关联键）
- **T-D7** §6 三层职责边界
- **T-D8** 更新 docs/codebase-architecture/README.md 第 3 段加 message-model.md 引用
- **T-D9** 跑 e2e_smoke + commit `docs(F103-Phase-D): D13 三层消息模型文档（关闭架构债 D13）`

---

## Phase E — Blueprint 索引

- **T-E1** docs/blueprint.md line 15 状态行 M0-M4 → M0-M5
- **T-E2** §9 子文档索引表加 codebase-architecture 关键文档行
- **T-E3** §14 里程碑表 M5 状态 ⏳ → ✅；新增 M6 行
- **T-E4** §14 待办汇总段更新为"M5 全部完成 / M6 待启动"
- **T-E5** 跑 e2e_smoke + commit `docs(F103-Phase-E): Blueprint 顶级索引 + M5→M6 切换标记`

---

## Final — Codex review + completion-report + handoff + 回归

- **T-F1** Codex final cross-Phase review（输入 spec.md + plan.md + Phase A-E commit diff）
- **T-F2** 处理 Codex finding（high 必修 / medium 选修 / low 可忽略）
- **T-F3** 产出 `.specify/features/103-blueprint-revision/codex-review-final.md`
- **T-F4** 产出 `.specify/features/103-blueprint-revision/completion-report.md`：
  - 实际 vs 计划对照（每 Phase 实际产出 + 偏离记录）
  - F084-F102 修订条目对照表（19 Feature × 5 子文档 = 95 修订点）
  - Blueprint 各文件 diff 统计
  - Codex review 闭环结果
- **T-F5** 产出 `.specify/features/103-blueprint-revision/handoff.md`：
  - F104 vs F107 决策建议
  - M6 启动 checklist
  - F103 → M6 接口契约保留（Blueprint 索引 / 哲学章节 / message-model）
- **T-F6** 全量回归 `pytest -m "not slow and not e2e_live"` 0 regression vs F102 baseline (9185862)
- **T-F7** 最终 commit `docs(F103-Final): Codex final review + completion-report + handoff（M5 全闭环，M6 可启动）`
- **T-F8** **不主动 push origin/master**：等用户拍板

---

## 工作量估算

| Phase | 子任务数 | 产出文件数 | 估算工时 |
|-------|---------|-----------|---------|
| A 实测 | 4 | 1 | ✅ 完成（≤ 1h）|
| B 同步主体 | 5 子模块 × 6-8 task = 33 | 5 修订 | 4-6h |
| C 哲学章节 | 9 | 1 新 + 2 修订 | 2-3h |
| D D13 消息模型 | 9 | 1 新 + 1 修订 | 2-3h |
| E Blueprint 索引 | 5 | 1 修订 | 0.5h |
| Final 收尾 | 8 | 3 新（review/report/handoff）| 1-2h |
| **总计** | **65+** | **3 新 + 6 修订** | **9-15h** |

---

**Tasks v0.1 完成**。等待 GATE_TASKS 用户审查。
