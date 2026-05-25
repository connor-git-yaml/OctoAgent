# F103 → M6 Handoff

> Source: F103 Blueprint v0.1 Incremental 修订（M5 最后一个 Feature）
> Target: M6 第 1 个 Feature（F104 vs F107 决策待定）
> F103 终态: feature/103-blueprint-revision 分支，8 commits + Final review + 全量回归 0 regression
> Status: READY_TO_MERGE（待用户拍板 Final review + push）
> Date: 2026-05-25

---

## 1. F103 关键产出（M6 启动前必读）

### 1.1 新增文档（3 个）

| 文档 | 行数 | 作用 |
|------|------|------|
| `docs/blueprint/agent-collaboration-philosophy.md` | 330 | OctoAgent 多 Agent 协作模型的**权威说明**（H1 管家 / H2 完整对等 / H3 两种委托），与 §2 Constitution 同级；M6 Feature 实施时必须遵守 |
| `docs/codebase-architecture/message-model.md` | 348 | 三层消息模型（Work × DispatchEnvelope × A2AMessage）+ 字段映射 + 三层职责边界；M6 Feature 改这三层任何字段时必读 |
| `.specify/features/103-blueprint-revision/*.md` | ~1000 | F103 spec / plan / tasks / phase-0-recon / completion-report / handoff（本文）|

### 1.2 修订文档（6 个）

| 文档 | 净增 | 作用 |
|------|------|------|
| `docs/blueprint.md` | +17 | 顶级索引（M5-Delivered + M6 待启动 + 实现级文档引用 + 三条设计哲学概览段）|
| `docs/blueprint/milestones.md` | +107 | M5 章节重写（13 Feature + 4 阶段 + M5 后续修复 F081-F088 + M5→M6 切换 + M6 计划）|
| `docs/blueprint/module-design.md` | +66 | 新增 §9.13 Harness Layer + §9.14 Context Layer；§9.4 / §9.5 / §9.6 / §9.10 / §9.12 同步 |
| `docs/blueprint/requirements.md` | +57 | FR-A2A-1/2/2b/3 含 H1/H2/H3 引用 + 新增 §5.1.9 FR-NOTIFY / §5.1.10 FR-ROUTINE |
| `docs/blueprint/api-and-protocol.md` | +102 | A2AMessage envelope source_runtime_kind + 新增 §10.4 Notification API / §10.5 Routine Audit / §10.6 EventType 清单 / §10.7 ask_back 三工具 |
| `docs/blueprint/architecture-audit.md` | +219 | A7 改 ✅ + 新增 §14.9-14.13（F084-F088 / F090-F092 / F093-F096 / F097-F100 / F101-F102 审计）|
| `docs/codebase-architecture/README.md` | +6 | §4 跨模块专题段 |

### 1.3 8 Phase commits

```
c43aaa1 docs(F103-Phase-B1): milestones.md M5 章节重写 + F033/F038 carry-forward gate 关闭
7ed7258 docs(F103-Phase-B2): module-design.md 同步 F084-F102 各 Feature 改动
3be557e docs(F103-Phase-B3): requirements.md 同步 F084-F102 + H1/H2/H3 引用
97aa301 docs(F103-Phase-B4): api-and-protocol.md 同步 F084-F102 新接口
221a328 docs(F103-Phase-B5): architecture-audit.md 增补 §14.9-14.13（F084-F102 审计）
eb0d8cb docs(F103-Phase-C): 新增 §2.3 Agent 协作三条设计哲学独立章节
844c04c docs(F103-Phase-D): D13 三层消息模型文档（关闭架构债 D13）
a036200 docs(F103-Phase-E): Blueprint 顶级索引 + M5→M6 切换标记
```

---

## 2. M6 启动 checklist

- [ ] F103 Final review（Codex finding 0 HIGH 残留）→ 当前状态待补
- [ ] F103 完成后 user pull request → push origin/master（按 CLAUDE.local.md §Spawned Task 处理流程，用户拍板）
- [ ] F103 远端分支 `feature/103-blueprint-revision` 合入后删除（CLAUDE.local.md §远端分支精简）
- [ ] F103 worktree 清理（`git worktree remove`）

**M5 全部 acceptance gate 关闭确认**：

- F090-F103 acceptance criteria 全部通过 ✅
- F102 baseline (9185862) e2e_smoke 5x 循环 PASS ✅（F103 实测 8 passed in 5.02s）
- 全量回归 ≥ 3571 passed（F102 baseline）+ 0 regression ✅（F103 实测 3649 passed）
- 三条设计哲学（H1/H2/H3）已显式建模到代码 ✅
- 14 条架构债（D1-D14）：12 条已闭环、2 条显式推迟 F107 ✅
- D13（三层消息模型文档缺失）F103 关闭 ✅

**M6 启动条件已满足。**

---

## 3. M6 第 1 个 Feature 决策建议：F104 vs F107

### 3.1 F104 文件工作台 v0.1（推荐先做）

**目的**：git-aware artifact diff UI；复用 F084 SnapshotStore

**优点**：
- **用户 ROI 高**：文件工作台是 M5 期间用户最频繁反馈的缺口
- **范围窄**：只做 diff 视图（branch/blame 推到 F106）
- **不破坏 baseline**：纯 UI 扩展，不动 core
- **F084 SnapshotStore 已稳定**（M5 阶段全部依赖它，无 regression）

**风险**：
- 前端工作量（前端 UI 框架 + diff 库选型）
- 复用 SnapshotStore 时需明确 diff target（live vs snapshot）

**估算**：spec + plan + 实施 + Final review 约 1-2 周

### 3.2 F107 Capability Layer Refactor（推迟到 F104 之后）

**目的**：清理架构债 D9/D11/D12 + F090 D2 完全合并 + F101 推迟 4 项

**为什么不推荐 M6 先做**：
- **范围大**：D9 + D11 + D12 + D2 + 4 项推迟 = 8+ 子任务，不易并行
- **风险高**：涉及 WorkerProfile 完全合并 + 独立 SQL 表数据迁移 + FE 类型同步 → 跨包接口大改
- **跨 Feature 依赖**：F104 / F105 如果不先做，M6 surface 扩张完全卡住
- **F103 已给 F107 留好 fixture**：CLAUDE.local.md §"M6（Surface 扩张：F104-F110）"中 F107 范围已明确列出所有推迟项 → 启动时 spec 阶段直接列入

**估算**：spec + plan + 8+ Phase 实施 + Final review 约 2-4 周（最大不确定性）

### 3.3 决策建议

**推荐顺序**：F104 → F105 → F106 → F107 → F108/F109 → F110

**理由**：
1. F104 / F105 / F106 是 Surface 扩张（用户感知度高 / 范围窄 / 风险低）
2. F107 是架构债清理（用户感知度低 / 范围大 / 风险高）—— 应在 Surface 扩张积累足够 ROI 后再做
3. F108/F109 语音（依赖 F093 已完成）+ F110 Behavior Compactor 是独立项（任何时点可启动）

**Sub-推荐**：如果用户 M6 第 1 个 Feature 倾向"先治架构债再扩张"哲学，F107 也合理；但建议 F107 启动前先用 F104 验证 SnapshotStore + Frontend 集成路径。

---

## 4. F103 → M6 接口契约保留

M6 启动前应已合入 F103——F103 建立的接口（Blueprint 章节 + Philosophy + Message Model）应保持稳定：

### F103 不应被 M6 改动的项

- `docs/blueprint/agent-collaboration-philosophy.md` 章节定位（与 §2 Constitution 同级，权威说明）
- H1 / H2 / H3 哲学定义（如 H1 主 Agent 唯一 user-facing speaker）
- 三层消息模型（Work × DispatchEnvelope × A2AMessage）字段映射 + 职责边界
- M5 → M6 切换标记（M5 状态 ✅）
- M3 carry-forward F033 / F038 关闭标记

### F103 可被 M6 演进的项

- agent-collaboration-philosophy.md 业界对照表（M6 期间新工具发布可更新）
- message-model.md 字段引用（M6 Feature 改字段时同步）
- architecture-audit.md §14.13 末（M6 Feature 完成时追加 §14.14+）
- milestones.md M6 章节（F104-F110 实施时填实）

---

## 5. F103 中的工作流改进沉淀

### 5.1 纯文档 Feature 的工作流差异

F103 是 M5 首个**纯文档 Feature**（无代码改动），工作流模式与之前 12 个 Feature 不完全一致：

| 维度 | F090-F102（含代码改动）| F103（纯文档） |
|------|---------------------|--------------|
| baseline 已通 pattern | 8 连出现 | N/A（文档要么有要么没有）|
| per-Phase Codex review | 每 Phase 后跑 1-3 finding | 仅 Final cross-Phase review |
| pre-commit hook | e2e_smoke 必跑 | SKIP_E2E=1（纯文档不影响） |
| 跨 Feature handoff | handoff.md 给下游 Feature | handoff.md 给 M6（结构化决策建议）|
| 修订条目对照表 | spec 中 N AC × M FR | spec 中 N AC × M FR + completion-report 95 修订点对照表 |

### 5.2 后续纯文档 Feature 沿用

未来 M6 / M7 期间如果再做类似 Blueprint 修订（如 F103-v2 v0.2 重组），可沿用 F103 工作流：

- spec 阶段实测侦察必做（沿用 8 连 pattern + phase-0-recon.md 模板）
- 每 Phase commit（不跑 per-Phase Codex review）
- Final cross-Phase Codex review 必走（重点是"内容准确性 vs 代码现状" + "是否遗漏 X% 修订点"）
- completion-report.md 含修订条目对照表（M5 阶段 95 修订点是 baseline）
- handoff.md 给下游（结构化决策建议）

---

## 6. F103 已知 limitations（M6 文档化）

| Limitation | 影响 | 建议 |
|-----------|------|------|
| `docs/blueprint/core-design.md` 未在 F103 范围内同步 | core-design.md 是 §8 核心设计（9 子系统 / 913 行），含 NotificationService / DailyRoutineService / Harness 的设计细节，但 F103 范围不动 | M6 期间如有需要可单独修订（建议 F104 实施时一并清）|
| `docs/blueprint/deployment-and-ops.md` 未同步 F081 ProviderRouter 替代 LiteLLM Proxy 的部署影响 | deployment-and-ops.md 564 行未在 F103 范围内动 | 等 ProviderRouter 部署进入用户视野时单独修订 |
| `docs/blueprint/testing-strategy.md` 未同步 F083/F087 测试基础设施变更 | testing-strategy.md 162 行未动 | 等 M6 后期或 F107 时同步 |

**F103 范围明确排除 core-design / deployment-and-ops / testing-strategy** —— 这三个文件的修订工作量超出 F103 incremental 范围，需独立 Feature 或 M6 任一 Feature 顺手清。

---

## 7. F103 完成确认信号

F103 push 到 origin/master 后启动 M6 之前确认：

- [ ] F103 Final cross-Phase Codex review 完成（0 HIGH 残留）
- [ ] F103 push 到 origin/master（用户拍板）
- [ ] F103 远端分支 feature/103-blueprint-revision 删除（CLAUDE.local.md §远端分支精简）
- [ ] F103 worktree 不再需要时清理（`git worktree remove`）
- [ ] CLAUDE.local.md M5/M6 战略规划表中 F103 状态行更新为 ✅

**F103 完成 = M5 全部 acceptance gate 关闭 = M6 可启动。**

---

## 8. 引用

- F103 spec：[`.specify/features/103-blueprint-revision/spec.md`](spec.md)
- F103 plan：[`.specify/features/103-blueprint-revision/plan.md`](plan.md)
- F103 tasks：[`.specify/features/103-blueprint-revision/tasks.md`](tasks.md)
- F103 phase-0-recon：[`.specify/features/103-blueprint-revision/phase-0-recon.md`](phase-0-recon.md)
- F103 completion-report：[`.specify/features/103-blueprint-revision/completion-report.md`](completion-report.md)
- F103 codex-review-final：[`.specify/features/103-blueprint-revision/codex-review-final.md`](codex-review-final.md)（待补 Final review 完成后产出）
- M5 / M6 战略规划：[CLAUDE.local.md §"M5 / M6 战略规划"](../../../CLAUDE.local.md)
- 三条设计哲学：[docs/blueprint/agent-collaboration-philosophy.md](../../../docs/blueprint/agent-collaboration-philosophy.md)
- 三层消息模型：[docs/codebase-architecture/message-model.md](../../../docs/codebase-architecture/message-model.md)

---

**End of Handoff**
