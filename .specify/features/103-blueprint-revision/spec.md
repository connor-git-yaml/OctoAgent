# F103 Blueprint v0.1 Incremental 修订 — Spec

> Feature ID: F103
> Slug: blueprint-revision
> Type: **纯文档 Feature**（不允许任何代码改动）
> Stage: M5 阶段 3 收尾（M5 全部 13 Feature 最后一个）
> Baseline: origin/master @ `9185862`（F102 Phase F 完成）
> Worktree: `.claude/worktrees/F103-blueprint-revision`
> Branch: `feature/103-blueprint-revision`
> 上游依据: CLAUDE.md / CLAUDE.local.md §"M5 / M6 战略规划" / F102 handoff.md §2

---

## 0. 目标与背景

### 0.1 目标

把 F084-F102 在 master 上累计 19 个 Feature 的架构改动 incremental 同步进 Blueprint v0.1，并：

1. **新增独立顶级章节 §"Agent 协作三条设计哲学"**（H1 管家 mediated / H2 完整 Agent 对等性 / H3 两种委托模式并存）作为 OctoAgent 协作模型的权威说明
2. **关闭架构债 D13**（三层消息模型文档缺失）—— `Work` × `DispatchEnvelope` × `A2AMessage` 三层关系明确化
3. **更新 Blueprint 顶级索引**反映 M5 实际完成状态 + 启动 M6 切换标记

### 0.2 背景

- M5 13 Feature（F090-F102）已全部完成。F084-F088 是 M5 启动前的基础设施修复（与 M5 同期落地）。
- Blueprint v0.1 顶级索引（`docs/blueprint.md`）M5 段落仅 16 行占位（"语音/多模态/Companion/通知中心"），与实际 M5 完成内容（架构债清理 + Worker 对等性 + 委托模式分离 + Notification + Routine）不符。
- F102 handoff.md §2 已显式列出修订要点；本 Feature 把要点扩展到 F084-F101 全量同步。
- CLAUDE.local.md 已是同步源 SoT（13 个 Feature 实施记录 + 架构债映射 + M5/M6 战略规划完整版）。

### 0.3 完成定义

- 18 个 Feature 改动主体进入 Blueprint 5 个子文档（milestones / module-design / requirements / api-and-protocol / architecture-audit）
- 新增 `docs/blueprint/agent-collaboration-philosophy.md` 独立顶级章节
- 新增 `docs/codebase-architecture/message-model.md` 关闭 D13 架构债
- 更新 `docs/blueprint.md` 顶级索引含 M5→M6 切换标记
- F102 baseline (9185862) 全量回归 0 regression（纯文档应无影响，但跑一次确认）
- Codex final cross-Phase review 通过（0 high 残留）
- completion-report.md + handoff.md 产出，给 M6 第 1 个 Feature 决策建议

---

## 1. 不在范围（明确排除）

> 禁令优先于指令，每条附带原因（CLAUDE.md §"Prompt 与规则编写"）。

| 禁令 | 原因 |
|------|------|
| **不动任何 .py / .ts / .tsx 文件** | F103 是纯文档 Feature；代码改动违反 scope |
| **不实施 F107 推迟项**（D2/D8/D9/D11/D12 / dismiss 持久化 / FR-D4 API / FR-E1 control_plane）| 这些是 F107 主范围，F103 仅文档化"推迟"事实 |
| **不实施 WeeklyRoutine** | F102 已明确排除；独立 Feature |
| **不动 .specify/features/090-102/** 已有 spec | immutable 历史记录 |
| **不动 CLAUDE.md / CLAUDE.local.md** | 已是同步源 SoT，反向流违反单一事实源 |
| **不做 Blueprint v0.2 重组** | v0.1 incremental 修订；触发 v0.2 条件需 Constitution 10 条新增或 M0-M4 里程碑根本改写 |
| **不动测试代码** | 纯文档应无测试影响，跑一次回归确认即可 |

---

## 2. Acceptance Criteria（AC）

> 13 AC：每 Block（A/B/C/D/E）2-3 AC + 全局 3 AC。AC 通过 = 实际产出对照本 spec 验证。

### AC-A 实测侦察（Phase 0 完成的）

- **AC-A1**：phase-0-recon.md 含 docs/blueprint/ 12 个文件 + 行数对照
- **AC-A2**：phase-0-recon.md 含 F084-F102 在 Blueprint 已收录度对照表（19 行 × 4 列）

### AC-B 同步主体

- **AC-B1 milestones.md §M5 重写**：M5 段落含 13 Feature 完成状态 + commit hash + F084-F088 后续修复段（M5 占位的 16 行 → 完整章节）
- **AC-B2 module-design.md 同步 F084-F102**：新增 Harness Layer 段落（F084）+ DelegationPlane 统一段落（F092）+ NotificationService 段落（F101）+ DailyRoutineService 段落（F102）+ Worker 完整对等性段落（F093-F096）+ ProviderRouter 段落（F081）
- **AC-B3 requirements.md 同步 FR**：FR-A2A-2 Worker 完整对等性细化（F093-F096）+ FR-A2A-2b Subagent SubagentDelegation 细化（F097）+ FR-A2A-3 A2A H3-B 细化（F098 Worker↔Worker 解禁 + F099 ask_back）+ F033/F038 carry-forward gate 关闭标记
- **AC-B4 api-and-protocol.md 同步新接口**：A2AMessage envelope 加 source_runtime_kind 5 值（F099）+ 新增 §10.4 Notification API（F101）+ §10.5 Routine Audit（F102）+ §10.6 EventType 清单（NOTIFICATION_DISPATCHED / ROUTINE_* / CONTROL_METADATA_UPDATED / SUBAGENT_COMPLETED / AGENT_SESSION_TURN_PERSISTED 等 F084-F102 新增）+ §10.7 ask_back 三工具
- **AC-B5 architecture-audit.md 增补 §14.9-14.13**：§14.9 F084-F088 完成审计 / §14.10 F090-F092 类型系统/状态机/Delegation / §14.11 F093-F096 Worker 完整对等 / §14.12 F097-F100 委托模式两路分离 / §14.13 F101-F102 Notification + Routine

### AC-C Agent 协作三条设计哲学

- **AC-C1 新增 docs/blueprint/agent-collaboration-philosophy.md**：含 H1/H2/H3 完整论述（每个哲学 ≥ 50 行：定义 + 代码层落地引用 + 业界对照）
- **AC-C2 业界对照**：H3 段含 Hermes Agent / OpenClaw / Agent Zero / Claude Code / OpenAI Swarm / CrewAI 横向定位表
- **AC-C3 顶级索引更新**：docs/blueprint.md 子文档索引表加 `agent-collaboration-philosophy.md` 一行

### AC-D D13 三层消息模型

- **AC-D1 新增 docs/codebase-architecture/message-model.md**：含 Work → DispatchEnvelope → A2AMessage 三层关系 ASCII diagram + 字段映射表
- **AC-D2 三层职责边界明确化**：Work（执行单元 / 状态机 / artifact）/ DispatchEnvelope（运行时包装 / contract_version / hop_count）/ A2AMessage（对话消息 / context_id / message_id）
- **AC-D3 codebase-architecture/README.md 索引更新**：第 3 段模块导览加 message-model.md 引用

### AC-E Blueprint 索引

- **AC-E1 docs/blueprint.md 顶级索引**：§0 文档元信息状态行（line 15）更新到 M5-Delivered；§14 里程碑表加 M5 状态 ✅；待办汇总段加"M5 全部完成"声明
- **AC-E2 docs/codebase-architecture/ 子文档引用**：blueprint.md 顶级索引 §9 子文档表新增 codebase-architecture 文档引用（含 message-model.md / harness-and-context.md / e2e-testing.md / provider-direct-routing.md）
- **AC-E3 M5→M6 切换标记**：blueprint.md §14 里程碑表 M5 状态 ✅ + 新增 M6 行（M6 计划 F104-F110）

---

## 3. Functional Requirements（FR）

> FR 是 AC 的实施细节。

### FR-A 实测侦察（已完成）

- **FR-A1**：phase-0-recon.md 已产出（含 9 大节）

### FR-B 同步主体（5 个子模块）

- **FR-B1 milestones.md M5 章节重写**：
  - 行 411-426 占位段替换
  - 新增"M5 完成状态总览"段（含 13 Feature 表格 + commit hash + Phase 划分）
  - 新增"M5 实施记录索引"段（指向 CLAUDE.local.md §"F0XX 实施记录"系列）
  - 保留 M3 carry-forward 段（F033/F038/F067 历史记录，标记"已完成"）
  - 删除占位 5 条 unchecked 项（语音/多模态/Companion/通知中心/Behavior Compactor / 071b Slice C）—— 其中通知中心 ✅ F101 完成，Behavior Compactor 转 M6 F110，语音/多模态/Companion 转 M6 F108/F109/F105，071b Slice C 已在 M4 F075 完成（仅深度 i18n 留到 M5）
  - 末尾新增 M5 acceptance 段（M5 全部 13 Feature 完成 → M6 可启动）

- **FR-B2 module-design.md 同步**：
  - §9 末尾新增"§9.13 Harness Layer（F084 引入）"：ToolRegistry / ToolsetResolver / ThreatScanner / SnapshotStore / ApprovalGate / DelegationManager
  - §9.4 apps/kernel 补充：NotificationService（F101）/ DailyRoutineService（F102）/ ApprovalManager（F101 WAITING_APPROVAL）
  - §9.5 workers 段补充：F093 Worker Full Session Parity / F094 AGENT_PRIVATE memory namespace / F095 8 文件白名单（去 BOOTSTRAP 加 USER）/ F096 audit chain
  - §9.6 packages/protocol 补充：F098 BaseDelegation 抽象 + F097 SubagentDelegation + F099 source_runtime_kind 枚举
  - §9.10 packages/provider 重写：ProviderRouter 替代 LiteLLM Proxy（F080/F081）+ Multi-Transport（OpenAI Chat / OpenAI Responses / Anthropic Messages）
  - 新增"§9.14 Context Layer"：USER.md SoT + Memory Candidates API + user_profile 三工具

- **FR-B3 requirements.md 同步**：
  - FR-A2A-1 Butler 改名 Main Agent + 引用 "Agent 协作三条设计哲学" H1
  - FR-A2A-2 Worker 段加 "Worker 完整对等性"（F093-F096 4 维：Session / Memory / Behavior / Recall Audit）+ 引用 H2
  - FR-A2A-2b Subagent 段加 SubagentDelegation（F097 ephemeral profile / SUBAGENT_INTERNAL session / Memory α 共享）+ 引用 H3-A
  - FR-A2A-3 A2A-Lite 段加 Worker↔Worker 解禁（F098 D14 关闭）+ source_runtime_kind 5 值（F099）+ CONTROL_METADATA_UPDATED event（F098）+ 引用 H3-B
  - F033 / F038 carry-forward gate 标 ✅ 关闭
  - 新增 FR-NOTIFY-1 Notification Service（F101 4 级优先级 / quiet hours / dismiss）
  - 新增 FR-ROUTINE-1 Daily Routine（F102 cron / LLM/fallback / USER.md SoT）

- **FR-B4 api-and-protocol.md 同步**：
  - §10.2 A2AMessage envelope 加 `metadata.source_runtime_kind: MAIN | WORKER | SUBAGENT | AUTOMATION | USER_CHANNEL`（F099）
  - §10.2.1 A2A 状态映射加 WAITING_APPROVAL 改造（F101 单 owner + CAS + 双注册）
  - 新增 §10.4 Notification API：`GET /api/notifications` / `POST /api/notifications/{id}/dismiss` / NOTIFICATION_DISPATCHED EventType 字段
  - 新增 §10.5 Routine Audit API：`_daily_routine_audit` task / ROUTINE_TRIGGERED/COMPLETED/FAILED/SKIPPED 4 EventType
  - 新增 §10.6 EventType 清单：F084-F102 新增 ≥ 10 个 EventType（NOTIFICATION_DISPATCHED / ROUTINE_* × 4 / CONTROL_METADATA_UPDATED / SUBAGENT_COMPLETED / SUBAGENT_SPAWNED / AGENT_SESSION_TURN_PERSISTED / BEHAVIOR_PACK_LOADED / BEHAVIOR_PACK_USED / MEMORY_RECALL_COMPLETED）
  - 新增 §10.7 ask_back 三工具：worker.ask_back / worker.request_input / worker.escalate_permission（F099）

- **FR-B5 architecture-audit.md 增补 §14.9-14.13**：
  - §14.9 F084-F088 完成审计：F084 Harness 全栈 / F085 capability_pack 拆分 / F086 APScheduler / F087 e2e_live 13 能力域 / F088 Module Singletons
  - §14.10 F090-F092 重构审计：F090 类型系统（D1/D2/D5）/ F091 状态机统一（D3 + A7 ✅）/ F092 DelegationPlane 收敛（D4，1+3 路径）
  - §14.11 F093-F096 Worker 完整对等审计：F093 Session（D6 拆分）/ F094 Memory AGENT_PRIVATE / F095 Behavior 8 文件白名单 / F096 Recall Audit 4 层 chain
  - §14.12 F097-F100 委托模式两路分离审计：F097 SubagentDelegation（H3-A）/ F098 A2A WorkerDelegation（H3-B + D14 解禁 + D7 拆分）/ F099 ask_back + source_runtime_kind（5 值）/ F100 Decision Loop Alignment（H1 + force_full_recall）
  - §14.13 F101-F102 用户感知 ROI 审计：F101 NotificationService + WAITING_APPROVAL + ApprovalGate SSE / F102 DailyRoutineService + USER.md SoT 3 字段

### FR-C 哲学章节

- **FR-C1 新建 docs/blueprint/agent-collaboration-philosophy.md**（≥ 200 行）：
  - §0 章节定位（与 §2 Constitution 同级，约束 OctoAgent 协作模型）
  - §1 三条哲学概览（H1/H2/H3 一句话定义）
  - §2 H1 管家 mediated 模式（定义 / 代码层 / 业界对照）
  - §3 H2 完整 Agent 对等性（定义 / 代码层 / F093-F096 4 维落地）
  - §4 H3 两种委托模式并存（定义 / H3-A Subagent / H3-B A2A / 代码层 / F097-F099 落地）
  - §5 业界对照横向定位表

- **FR-C2 H3 业界对照表**：
  - Hermes Agent（H3-A Subagent + H3-B A2A 都有 / 委托深度限制 max_depth=2）
  - OpenClaw（subagents 工具 / session 推送模式 / 无 Work 抽象）
  - Agent Zero（call_subordinate 单一接口 / 共享 project / 无持久化通信）
  - Claude Code（Task tool subagent / 共享 context / 单向通信）
  - OpenAI Swarm（handoff 模式 / 无持久化）
  - CrewAI（role-based crew / Pipeline 调度）

- **FR-C3 docs/blueprint.md 顶级索引子文档表加哲学章节行**：
  | 文件 | 对应章节 | 说明 |
  |------|---------|------|
  | [agent-collaboration-philosophy.md](blueprint/agent-collaboration-philosophy.md) | §2.3（新增）| Agent 协作三条设计哲学（H1/H2/H3）|

### FR-D D13 三层消息模型

- **FR-D1 新建 docs/codebase-architecture/message-model.md**（≥ 150 行）：
  - §1 三层关系总览 + ASCII diagram
  - §2 Work 层（执行单元）：字段 / 状态机 / 生命周期
  - §3 DispatchEnvelope 层（运行时包装）：字段 / contract_version / hop_count / source_runtime_kind / target / metadata
  - §4 A2AMessage 层（对话消息）：字段 / context_id / message_id / TASK/UPDATE/CANCEL/RESULT/ERROR/HEARTBEAT
  - §5 字段映射表（三层之间 task_id / agent_id / session_id 等关联键）
  - §6 三层职责边界（"A2A 是 Work 的一种通信形式，Work 不必是 A2A 触发"）

- **FR-D2 ASCII diagram 必含元素**：
  ```text
  Work (执行单元)
    │
    ├─→ DispatchEnvelope (运行时包装)
    │     │
    │     └─→ A2AMessage (对话消息)
    │           └─→ A2AConversation (持久化消息序列)
    │
    └─→ Artifact (产物)
  ```

- **FR-D3 docs/codebase-architecture/README.md 索引加 message-model.md 引用**

### FR-E Blueprint 索引

- **FR-E1 docs/blueprint.md §0 文档元信息状态行**：line 15 状态从 "M0-M4 ✅" 升级到 "M0-M5 ✅（2026-05-25 同步）"
- **FR-E2 docs/blueprint.md §14 里程碑表**：M5 状态从 ⏳ 改 ✅；新增 M6 行（M6 计划 / Surface 扩张 / F104-F110 / 状态 ⏳）；末尾待办汇总段更新为"M5 全部完成 / M6 待启动"
- **FR-E3 docs/blueprint.md 子文档索引**：§9 子文档表加 codebase-architecture/ 关键文档引用（README / message-model / harness-and-context / e2e-testing / provider-direct-routing）
- **FR-E4 docs/blueprint.md §15 风险清单 / §16 实现前 checklist 不动**（这些是 v0.1 启动时的快照，保留作为历史）

---

## 4. 跨 Block 不变量

- **I-1 行为零变更**：F103 是纯文档 Feature，所有运行时行为 100% 等价于 baseline `9185862`
- **I-2 测试零回归**：F102 baseline 测试数（含 e2e_smoke 5x 循环）保持
- **I-3 不破坏现有链接**：所有 docs/blueprint/ 内 `[xxx](xxx.md)` 链接保持有效
- **I-4 中文输出**：所有新增/修订段落使用中文（CLAUDE.md §"语言与风格"）
- **I-5 SoT 单一性**：Blueprint 与 CLAUDE.local.md 内容重叠时，Blueprint 是面向"长期协作者"的产品文档，CLAUDE.local.md 是面向"当前实施者"的工作记录——重叠的实施记录在 Blueprint 内只保留索引指针，不复制内容

---

## 5. Phase 划分（详细见 plan.md）

| Phase | 名称 | 产出 | 跨 Phase 风险 |
|-------|------|------|------------|
| **A** | 实测侦察 | phase-0-recon.md | 已完成 |
| **B** | 同步主体 | B-1 milestones / B-2 module-design / B-3 requirements / B-4 api-and-protocol / B-5 architecture-audit | 中（5 子模块串行降低 review 负担）|
| **C** | 哲学章节 | agent-collaboration-philosophy.md + blueprint.md 索引 | 低（独立新文档）|
| **D** | D13 消息模型 | message-model.md + codebase-architecture/README.md | 低（独立新文档）|
| **E** | Blueprint 索引 | docs/blueprint.md 顶级修订 | 低 |
| **Final** | Codex review + completion-report + handoff + 回归 | codex-review-final.md / completion-report.md / handoff.md + 回归 0 regression | 必走 |

---

## 6. 关键决策点（GATE_DESIGN）

### 6.1 §"Agent 协作三条设计哲学"位置（已默认 A）

- **A（推荐）独立子文档** `docs/blueprint/agent-collaboration-philosophy.md`
  - 与现有 11 个子文档同级；顶级索引加 §2.3 引用
- B 顶级索引内（在 docs/blueprint.md 直接加 §2.3）
  - 顶级索引会变胖（441 → ~700 行）

### 6.2 D13 message-model.md 位置（已默认 A）

- **A（推荐）独立新文档** `docs/codebase-architecture/message-model.md`
  - 与现有 codebase-architecture 子文档对称
- B 合并到 `docs/codebase-architecture/harness-and-context.md` 末尾
  - 主题不匹配（harness 是 F084 主题，message-model 是 F092/F097/F098 主题）

### 6.3 Phase 顺序（已默认 A→B→C→D→E→Final）

按 spec.md §5 顺序执行。

### 6.4 F085 单独 commit vs 合并到 §14.9

F085 capability_pack 拆分已在 architecture-audit.md §14.8 A1 ✅ 记录。F103 §14.9 仅追加"F085 capability_pack 后续 follow-up（推迟到 F107）"段落即可，不重复记录主体。

---

## 7. 全局回归（每 Phase 后 + Final）

> 纯文档 Feature 理论无测试影响，但仍跑回归确认。

- 每 Phase 后：`make e2e-smoke` 或 `pytest -m e2e_smoke`（≤ 5s 期望）
- Final 前：`pytest packages/ apps/ -m "not slow and not e2e_live"`（≤ 60s 期望，baseline 3571+ passed）
- F102 baseline 行：3571 passed（vs F100 baseline +21）—— F103 应保持 ≥ 3571

---

## 8. Final cross-Phase Codex Review 要点

> CLAUDE.local.md §"工作流改进"强制：F103 commit 前必走 Final cross-Phase review。

review 重点（纯文档 review 与代码 review 不同）：

1. **内容准确性 vs 代码现状**（最重要）：
   - F084-F102 改动是否被准确描述？
   - 是否有任何过期/错误的技术细节（如 EventType 名字写错 / commit hash 写错）？
   - 是否引用了不存在的代码标识符（类名 / 函数名 / 字段名）？

2. **结构合理性**：
   - 新增章节位置是否合理？
   - 与现有章节是否冲突或重复？
   - 链接是否有效？

3. **是否遗漏 F084-F102 任何重要改动**：
   - 18 Feature × 5 子文档 = 90 个修订点；review 必须实际抽样 ≥ 20% 验证
   - 特别检查：F099 ask_back 三工具 / F101 NOTIFICATION_DISPATCHED / F102 ROUTINE_* / F098 CONTROL_METADATA_UPDATED 这些"小但易遗漏"的细节

4. **完成定义 vs 实际产出**：
   - 13 AC 是否实际通过？
   - completion-report.md 是否真实反映状态（不是"宣传文"）？

---

## 9. 验收 checklist（完成时必须回报）

### Block A 实测验收
- [x] docs/blueprint/ 当前文件清单 + 行数对照（phase-0-recon.md §1）
- [x] 已收录 vs 待修订的章节清单（phase-0-recon.md §2）
- [x] docs/codebase-architecture/ 子文档清单 + Blueprint 引用状态（phase-0-recon.md §3）
- [x] F084-F102 13 实施记录已整理为同步数据源（phase-0-recon.md §4 / CLAUDE.local.md）

### Block B 验收
- [ ] milestones.md §M5 重写（13 Feature 完成状态 + commit hash）
- [ ] module-design.md 同步 F084-F102 各 Feature 改动
- [ ] requirements.md F033 / F038 carry-forward gate 关闭标记 + Worker 完整对等性段落
- [ ] api-and-protocol.md 新接口同步（ask_back / source_runtime_kind / RuntimeControlContext / NOTIFICATION_DISPATCHED / ROUTINE_* / CONTROL_METADATA_UPDATED 等）
- [ ] architecture-audit.md §14.9-14.13 增补

### Block C 验收
- [ ] 新增 §"Agent 协作三条设计哲学"位置决策（独立子文档 A）
- [ ] H1/H2/H3 内容含代码层落地引用
- [ ] 业界对照（Hermes/OpenClaw/Agent Zero/Claude Code/Swarm/CrewAI）

### Block D 验收
- [ ] message-model.md 新增（独立新文档 A）
- [ ] Work → DispatchEnvelope → A2AMessage 字段映射 ASCII diagram
- [ ] 三层职责边界

### Block E 验收
- [ ] 顶级章节索引同步
- [ ] docs/codebase-architecture/ 子文档引用
- [ ] M5 → M6 切换标记

### 全局验收
- [ ] 全量回归 0 regression vs F102 baseline (9185862)
- [ ] e2e_smoke 5x 循环 PASS
- [ ] 每 Phase Codex review 闭环（0 high 残留）
- [ ] **Final cross-Phase Codex review** 通过
- [ ] **completion-report.md** 已产出
- [ ] **handoff.md** 给 M6 第 1 个 Feature
- [ ] **M5 收尾确认**：所有 F090-F103 acceptance gate 关闭 → M6 可启动

---

**Spec v0.1 完成**。等待 GATE_DESIGN 用户审查。
