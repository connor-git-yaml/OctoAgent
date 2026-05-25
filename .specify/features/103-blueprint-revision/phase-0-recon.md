# F103 Phase 0 — Spec 阶段实测侦察报告

> Feature: F103 Blueprint v0.1 Incremental 修订（M5 最后一个 Feature）
> Baseline: origin/master @ `9185862`（F102 Phase F 完成）
> Worktree: `.claude/worktrees/F103-blueprint-revision`
> 实测日期: 2026-05-25
> 沿用 8 连 pattern（F093 → F102）的"spec 阶段必做实测侦察"工作流

---

## 0. 实测核心结论

| 维度 | 实测 | 影响 |
|------|------|------|
| docs/blueprint/ 当前状态 | 5060 行 / 11 子文档 + 顶级索引 | F084-F102 改动主体未收录，需 incremental 同步 |
| docs/codebase-architecture/ 当前状态 | 1313 行 / 6 子文档 + 1 modules 子目录 | F084 单独有专文，F085-F102 部分散落，缺统一索引 + 缺 D13 message-model.md |
| F084-F102 收录度 | F084 单独有 codebase-architecture/harness-and-context.md；**F085-F102 完全未反映** | F103 主体工作=同步 18 个 Feature × 5 个 Blueprint 子文档 |
| Blueprint 顶级索引（blueprint.md） | M5 占位 16 行（行 411-426）+ §0 文档元信息状态行（line 15）只到 M4 | 必须重写 M5 + 状态行 + 索引层新增哲学章节链接 |

---

## 1. docs/blueprint/ 文件清单 + 行数

```text
docs/blueprint.md                          441 行  顶级索引
docs/blueprint/api-and-protocol.md         130 行  Gateway-Kernel / A2A / Tool Call 协议
docs/blueprint/appendix.md                 116 行  术语表 + 示例配置
docs/blueprint/architecture-audit.md       260 行  §14.5-14.8 短板/架构/Worker/代码审计
docs/blueprint/architecture-overview.md    152 行  分层架构 + Mermaid 图
docs/blueprint/architecture-tradeoffs.md   143 行  14 个架构权衡点
docs/blueprint/core-design.md              913 行  9 个子系统核心设计（最大）
docs/blueprint/deployment-and-ops.md       564 行  部署 + Docker + 备份 + DX
docs/blueprint/milestones.md               426 行  M0-M5 里程碑（M5 占位）
docs/blueprint/module-design.md            224 行  Monorepo 结构 + 12 模块职责
docs/blueprint/requirements.md             216 行  FR + NFR
docs/blueprint/testing-strategy.md         162 行  10 个测试类别 + 覆盖矩阵
─────────────────────────────────────────────────
合计                                      3306 行  + 441 顶级 = 3747 行
```

## 2. docs/blueprint/ F084-F102 已收录程度对照

| Feature | Scope | 在 Blueprint 内已收录？ | 应修订位置 |
|---------|-------|----------------------|-----------|
| **F081** LiteLLM 退役 / ProviderRouter | provider stack | ❌ 未提（仅 CLAUDE.md 提）| module-design.md §9.10 + core-design.md §8.9 + milestones.md M5 后续修复 |
| **F083** 测试并发加速 | 测试基础设施 | ❌ 未提 | testing-strategy.md（可选）+ milestones.md M5 |
| **F084** Context + Harness 全栈重构 | harness 层 + USER.md SoT | 仅 docs/codebase-architecture/harness-and-context.md 独立 | module-design.md（Harness 章节）+ milestones.md M5 + architecture-audit.md §14.9 |
| **F085** capability_pack 拆分 | 已修复 A1（260 行已写 ✅）| ✅ architecture-audit.md §14.8 A1 已记录 | 不需要新增 |
| **F086** APScheduler 框架 | scheduler/cron | ❌ 未提 | core-design.md §8 + milestones.md M5 |
| **F087** Agent e2e Live Test Suite | 测试套件 | 仅 docs/codebase-architecture/e2e-testing.md | testing-strategy.md + milestones.md M5 |
| **F088** Module Singletons | 测试 hermetic 隔离 | ❌ 未提 | testing-strategy.md（可选） |
| **F089** spec-driver SKILL 编排拆分 | 工作流 | ❌ 不在 OctoAgent 主线 | 不收录（这是 spec-driver 插件改造） |
| **F090** Type System & Naming Cleanup | 类型系统 | ❌ 未提（仅 architecture-audit §14.8 A6 提了 Butler 清理）| architecture-audit.md §14.10 + module-design.md（控制流） |
| **F091** State Machine Unification | 状态机 | ❌ 未提（architecture-audit §14.8 A7 4 枚举重叠是问题陈述）| architecture-audit.md §14.10（A7 改 ✅）+ api-and-protocol.md |
| **F092** DelegationPlane Unification | 编排 | ❌ 未提 | module-design.md（控制流）+ architecture-audit.md §14.10 |
| **F093** Worker Full Session Parity | Worker session | ❌ 未提（requirements.md FR-A2A-2 提 WorkerSession 但未细化）| requirements.md FR-A2A-2 细化 + module-design.md + architecture-audit.md §14.11 |
| **F094** Worker Memory Parity | AGENT_PRIVATE namespace | ❌ 未提 | requirements.md FR-MEM-1 + core-design.md §8.7 + architecture-audit.md §14.11 |
| **F095** Worker Behavior Workspace Parity | _PROFILE_ALLOWLIST 8 文件 | ❌ 未提（milestones.md M3 BehaviorWorkspace 提了原状）| milestones.md M3 BehaviorWorkspace 补充 + module-design.md |
| **F096** Worker Recall Audit & Provenance | list_recall_frames endpoint + audit chain | ❌ 未提 | api-and-protocol.md + architecture-audit.md §14.11 |
| **F097** Subagent Mode Cleanup (H3-A) | SubagentDelegation | ❌ 未提（requirements.md FR-A2A-2b 提 Subagent 但未细化）| requirements.md FR-A2A-2b 细化 + api-and-protocol.md + architecture-audit.md §14.12 |
| **F098** A2A Mode + Worker↔Worker (H3-B) | A2A 真 P2P + 解禁 worker↔worker | ❌ 未提（FR-A2A-3 提 A2A 但 D14 worker→worker 硬禁止反向需文档化）| requirements.md FR-A2A-3 细化 + api-and-protocol.md A2A envelope + architecture-audit.md §14.12 |
| **F099** Ask-Back Channel + Source Generalization | ask_back 三工具 + source_runtime_kind 5 值 | ❌ 未提 | api-and-protocol.md + architecture-audit.md §14.12 + 新增"Agent 协作哲学"H3 |
| **F100** Decision Loop Alignment (H1) | force_full_recall + RecallPlannerMode AUTO | ❌ 未提 | architecture-audit.md §14.12 + 新增"Agent 协作哲学"H1 |
| **F101** Notification + Attention Model | NotificationService + WAITING_APPROVAL + NOTIFICATION_DISPATCHED | ❌ 未提（M5 占位仅"通知中心"4 字）| milestones.md M5 重写 + module-design.md + api-and-protocol.md + architecture-audit.md §14.13 |
| **F102** Proactive Followup (DailyRoutine) | DailyRoutineService + ROUTINE_* 4 EventType + USER.md +3 字段 | ❌ 未提 | milestones.md M5 + module-design.md + api-and-protocol.md + architecture-audit.md §14.13 + 新章节"Proactive Followup" |

**结论**：Blueprint 中已收录的 F084-F102 改动 = 1 个（F085 A1）；待新增 = 17 个。

## 3. docs/codebase-architecture/ 文件清单 + Blueprint 引用状态

```text
docs/codebase-architecture/README.md                     177 行  导览索引
docs/codebase-architecture/bootstrap-profile-flow.md     215 行  F084 前 bootstrap 流程（已退役但保留）
docs/codebase-architecture/current-doc-map.md            136 行  文档地图
docs/codebase-architecture/e2e-testing.md                207 行  F087 13 能力域 e2e_live 套件
docs/codebase-architecture/harness-and-context.md        232 行  F084 Harness + Context 全栈
docs/codebase-architecture/provider-direct-routing.md    169 行  F080/081 ProviderRouter
docs/codebase-architecture/testing-concurrency.md        177 行  F083 thread shutdown hang 修复
docs/codebase-architecture/modules/                      [目录] 6 个模块文档（01-06）
```

**Blueprint 顶级索引引用情况**：
- `docs/blueprint.md` line 303 提到 `[codebase-architecture/](codebase-architecture/) 的实现级文档`，但是**索引层未列具体子文档**
- CLAUDE.md "设计文档索引" 表中已含 `docs/codebase-architecture/ - 6 个模块实现级文档`

**待 F103 新增**：`docs/codebase-architecture/message-model.md`（D13 三层消息模型）

## 4. F084-F102 实施记录数据源（CLAUDE.local.md 已整理）

CLAUDE.local.md §"M5 / M6 战略规划" 已含完整数据源：
- §"三条核心设计哲学（M5/M6 服务的目标）"：H1/H2/H3 完整描述（F103 §"Agent 协作三条设计哲学"章节直接消费）
- §"M5（13 Feature，4 阶段）"：F090-F103 状态表 + 一句话目的 + commit hash
- §"架构债 → Feature 映射（D1-D14）"：14 条架构债定位
- §"F090 实施偏离记录" / §"F091 实施记录" / ... / §"F102 实施记录"：13 个 Feature 完整实施记录

**结论**：F103 spec/plan/impl 阶段不需要查 git log 或重新 review feature dir——CLAUDE.local.md 已是 SoT 数据源。

## 5. F101 + F102 handoff.md 已明确的修订要点

### F101 → F102 handoff（已合入 master）
- 引用：`.specify/features/101-notification-attention/handoff.md` §1-§8
- 关键产出：NotificationService + NOTIFICATION_DISPATCHED + USER.md SoT + Telegram/Web channels + dismiss

### F102 → F103 handoff（关键，本 Feature 起点）
- 引用：`.specify/features/102-proactive-followup/handoff.md` §1-§7
- **§2 已显式列出 F103 修订要点**（直接 import 到 spec.md）：
  - §2.1 应新增章节："Agent 协作三条设计哲学"补充 H1 实施样本（F102） + "Proactive Followup" 新章节
  - §2.2 应更新章节：系统服务清单 + NotificationService 接口 + EventType 清单 + USER.md 机器可读字段
- §3 接口契约（NotificationService channels 参数 / daily_routine_config 复用 pattern / _ensure_audit_task pattern）
- §4 范围建议（必做 + 可选 + 不在范围）

## 6. F103 范围 5 大块映射（用户 prompt → 实际工作）

| Block | 用户 prompt 描述 | 实际工作量估算 |
|-------|-----------------|--------------|
| **A 实测侦察** | docs/blueprint 现状 + F084-F102 已收录度 + codebase-architecture 清单 + 13 实施记录 | **本文档** = 完整闭环 |
| **B 同步主体** | B-1 milestones M5 / B-2 module-design / B-3 requirements / B-4 api-and-protocol / B-5 architecture-audit | 5 子文档修订 ≈ 200-400 行修改 |
| **C 哲学章节** | H1/H2/H3 + 业界对照（Hermes/OpenClaw/Agent Zero/Claude Code/Swarm/CrewAI） | 1 新文档 ≈ 200-300 行（位置 spec 阶段定）|
| **D D13 消息模型** | docs/codebase-architecture/message-model.md（Work / DispatchEnvelope / A2AMessage） | 1 新文档 ≈ 150-250 行 + ASCII diagram |
| **E Blueprint 索引** | docs/blueprint.md 顶级索引 + M5→M6 切换标记 | ≈ 20-50 行修改 |

## 7. 不在范围（明确排除）

- ❌ 代码改动（任何 .py / .ts / .tsx 文件改动违反 F103 纯文档约束）
- ❌ F107 推迟项实施（D2/D8/D9/D11/D12 等）
- ❌ F101 dismiss 持久化 / F102 WeeklyRoutine 等推迟项
- ❌ .specify/features/ 已有 spec 改动（immutable 历史）
- ❌ CLAUDE.local.md / CLAUDE.md 改动（已是 SoT 数据源）
- ❌ Blueprint v0.2 重组（v0.1 incremental 修订）
- ❌ 测试改动（纯文档应无测试影响，但要跑回归确认）

## 8. 关键决策点（GATE_DESIGN 待用户拍板）

### 8.1 §"Agent 协作三条设计哲学"位置（A vs B）

| 选项 | 位置 | 优点 | 缺点 |
|------|------|------|------|
| **A**（推荐）| `docs/blueprint/agent-collaboration-philosophy.md` 独立子文档（与现有 11 个子文档同级） | 与现有结构对称（子文档按章节分） | 顶级索引 docs/blueprint.md §X 需新增章节号 |
| B | `docs/blueprint.md` 顶级索引内直接加新章节（如 §2.3 "设计哲学"） | 不破坏现有子文档结构 | 顶级索引会变胖（441 行 → ~700 行） |

### 8.2 D13 message-model.md 位置（A vs B）

| 选项 | 位置 | 优点 | 缺点 |
|------|------|------|------|
| **A**（推荐）| `docs/codebase-architecture/message-model.md` 独立新文档 | 与现有 codebase-architecture 子文档对称 | 需在 codebase-architecture/README.md 索引层加引用 |
| B | 合并到 `docs/codebase-architecture/harness-and-context.md` 末尾 | 不新增文档 | 主题不匹配（harness 是 F084 主题，message-model 是 F092/F097/F098 主题）|

### 8.3 F103 Phase 顺序确认

按用户 prompt 建议：**A 实测 → B 同步主体（5 子模块）→ C 新增哲学章节 → D D13 消息模型 → E Blueprint 索引 → Final**

Phase A（本文档）已完成。Phase B 可拆为 B-1 ~ B-5 子任务，每个子任务后跑一次 e2e_smoke 确认（虽然纯文档应无影响）。

## 9. F103 完成后启动 M6 的 checklist（handoff.md 给 M6 的预备）

- F090-F103 全部 acceptance gate 关闭 → M6 可启动
- M6 第 1 个 Feature 选择：**F104 文件工作台 v0.1**（diff 视图，复用 F084 SnapshotStore）vs **F107 Capability Layer Refactor**（清理 D2/D8/D9/D11/D12 + dismiss 持久化等推迟项）
  - 建议 F104 先做（用户 ROI 高 + 范围窄 + 不破坏 baseline）
  - F107 后做（架构债清理 + 推迟项整合，范围大，更适合中段执行）
  - 但最终 M6 顺序由用户 GATE 决定

---

**Phase 0 实测侦察完成**。

下一步：写 spec.md，含 11 AC（每 Block 2 AC + 全局 3 AC）+ FR-A1/B1-B5/C1-C3/D1-D3/E1-E3。
