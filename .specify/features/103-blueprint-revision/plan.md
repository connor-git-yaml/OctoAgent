# F103 Plan

> 参照 spec.md。F103 是纯文档 Feature，按 Phase A→B→C→D→E→Final 串行推进。
> 每 Phase 末尾必走快速 e2e_smoke 回归（纯文档应无影响，跑确认）。
> Final 前必走 Codex final cross-Phase review。

---

## Phase A — 实测侦察（已完成）

**产出**：`.specify/features/103-blueprint-revision/phase-0-recon.md`

**结论摘要**：
- docs/blueprint/ 现状：12 子文档 + 顶级索引 = 3747 行
- F084-F102 收录度：F085 ✅（1 个）+ 18 个待新增
- docs/codebase-architecture/ 现状：6 子文档 + modules 子目录 = 1313 行
- F101 / F102 handoff.md 已为 F103 显式列出修订要点

**Phase A 不变量**：实测数据准确，避免后续 Phase 基于假设决策。

---

## Phase B — 同步主体（5 子模块串行）

### B-1 milestones.md M5 章节重写

**步骤**：
1. 读取 milestones.md 行 411-426（M5 占位段）
2. 删除占位段 + 替换为 M5 完成版（含 13 Feature 表格 + commit hash + Phase 划分）
3. 末尾追加 M5→M6 切换段
4. 跑 e2e_smoke 确认无影响

**关键内容**：
- M5 阶段 0（F090-F092 严格串行）：类型系统 / 状态机 / DelegationPlane
- M5 阶段 1（F093-F096）：Worker 完整对等性（4 维）
- M5 阶段 2（F097-F100）：委托模式两路分离
- M5 阶段 3（F101-F103）：Notification + Routine + Blueprint 修订
- 新增 §"M5 后续修复"段（F081-F088 的 M5 同期落地）

**Codex per-Phase review 重点**：
- 13 Feature commit hash 准确性（从 CLAUDE.local.md §"M5 / M6 战略规划"准确复制）
- F084-F088 是否归类正确（它们是 M5 同期但不在 13 Feature 内）

### B-2 module-design.md 同步

**步骤**：
1. 读取 module-design.md 当前结构（224 行）
2. 在 §9.4 apps/kernel 追加：NotificationService（F101）/ DailyRoutineService（F102）/ ApprovalManager
3. 在 §9.5 workers 追加：Worker 完整对等性 4 维（F093-F096）
4. 在 §9.6 packages/protocol 追加：BaseDelegation 抽象 + SubagentDelegation + source_runtime_kind 枚举（F097/F098/F099）
5. 重写 §9.10 packages/provider：ProviderRouter（F080/F081 替代 LiteLLM Proxy）+ Multi-Transport
6. 新增 §9.13 Harness Layer（F084 引入）
7. 新增 §9.14 Context Layer（USER.md SoT + Memory Candidates API + user_profile 三工具）

**Codex per-Phase review 重点**：
- 新增章节是否引用准确的代码标识符（实际存在的类/函数名）
- 与 docs/codebase-architecture/harness-and-context.md 是否一致

### B-3 requirements.md 同步

**步骤**：
1. 读取 requirements.md 当前 FR-A2A-1/2/2b/3（行 65-103）
2. FR-A2A-1 Butler → Main Agent 命名同步 + 引用 H1
3. FR-A2A-2 加 Worker 完整对等性 4 维 + 引用 H2
4. FR-A2A-2b 加 SubagentDelegation 细节 + 引用 H3-A
5. FR-A2A-3 加 Worker↔Worker 解禁 + source_runtime_kind + ask_back + 引用 H3-B
6. F033 / F038 段加 ✅ 关闭标记（milestones.md M3 carry-forward 同样标）
7. 新增 §5.1.9 Notification + Attention（FR-NOTIFY-1）
8. 新增 §5.1.10 Proactive Followup（FR-ROUTINE-1）

**Codex per-Phase review 重点**：
- FR 编号不冲突（FR-NOTIFY / FR-ROUTINE 是新前缀）
- 引用 H1/H2/H3 时章节链接有效（先做 B 再做 C 时 anchor 不存在 → 用 placeholder，C 完成后填）

### B-4 api-and-protocol.md 同步

**步骤**：
1. 读取 api-and-protocol.md 当前 4 段（§10.1-§10.3 + §10.2.1/§10.2.2）
2. §10.2 A2AMessage envelope 加 `source_runtime_kind` 字段 + 5 值定义
3. §10.2.1 A2A 状态映射加 WAITING_APPROVAL 改造说明（F101 单 owner + CAS）
4. 新增 §10.4 Notification API
5. 新增 §10.5 Routine Audit API
6. 新增 §10.6 EventType 清单（F084-F102 新增 ≥ 10 个）
7. 新增 §10.7 ask_back 三工具

**Codex per-Phase review 重点**：
- EventType 字符串准确性（实际 enum 值，不是中文翻译）
- 字段名校验（如 NotificationPriority 4 级而非 3 级 / channels 是 frozenset 而非 list）

### B-5 architecture-audit.md 增补 §14.9-14.13

**步骤**：
1. 读取 architecture-audit.md 当前 §14.5-14.8（260 行）
2. 末尾追加：
   - §14.9 F084-F088 完成审计（已修复短板）
   - §14.10 F090-F092 类型系统/状态机/Delegation 重构审计
   - §14.11 F093-F096 Worker 完整对等审计
   - §14.12 F097-F100 委托模式两路分离审计
   - §14.13 F101-F102 Notification + Routine 审计
3. A7 状态枚举重叠（260 行末）改 ✅（F091 完成）
4. A4 provider/dx 定位模糊 保留 🟠（F107 推迟）

**Codex per-Phase review 重点**：
- §14.10-14.13 每个 Feature 写到一致的"决策点 + 关键架构产出 + 已知 limitation"格式
- 与 milestones.md §M5 / module-design.md 各章节是否冲突或重复

### Phase B 不变量

- 5 子模块 commit 各自独立（不合并），每个 commit message 含"Phase B-N / per-Phase Codex review N high 闭环"
- 每 commit 后 e2e_smoke 5x 循环 PASS
- 内容来源 100% 是 CLAUDE.local.md SoT，不引入新假设

---

## Phase C — 哲学章节

### 步骤

1. 新建 `docs/blueprint/agent-collaboration-philosophy.md`（≥ 200 行）
2. 内容结构：
   - §0 章节定位
   - §1 三条哲学概览
   - §2 H1 管家 mediated 模式
   - §3 H2 完整 Agent 对等性
   - §4 H3 两种委托模式并存（H3-A Subagent + H3-B A2A）
   - §5 业界对照横向定位表
3. 更新 docs/blueprint.md 顶级索引子文档表加 agent-collaboration-philosophy.md 行
4. 回填 B-3 requirements.md 中 H1/H2/H3 链接 anchor（已在 B 阶段用 placeholder）
5. 跑 e2e_smoke 确认

### Codex per-Phase review 重点

- H1/H2/H3 与 CLAUDE.local.md §"三条核心设计哲学"定义一致
- 业界对照表格内容准确（特别是 Hermes Agent / Claude Code 的对照）
- 代码层落地引用准确（如 F100 `RuntimeControlContext.force_full_recall` / F099 source_runtime_kind 5 值）

---

## Phase D — D13 三层消息模型

### 步骤

1. 新建 `docs/codebase-architecture/message-model.md`（≥ 150 行）
2. 内容结构：
   - §1 三层关系总览 + ASCII diagram
   - §2 Work 层
   - §3 DispatchEnvelope 层
   - §4 A2AMessage 层
   - §5 字段映射表
   - §6 三层职责边界
3. 更新 docs/codebase-architecture/README.md 第 3 段加 message-model.md 引用
4. 跑 e2e_smoke 确认

### Codex per-Phase review 重点

- Work / DispatchEnvelope / A2AMessage 三层字段名准确（从 packages/core/models 取真实字段）
- ASCII diagram 渲染正确（Markdown 代码块包裹）
- "A2A 是 Work 的一种通信形式"语义清晰

---

## Phase E — Blueprint 索引

### 步骤

1. 读取 docs/blueprint.md 当前 441 行
2. 修改：
   - line 15 状态行：M0-M4 ✅ → M0-M5 ✅
   - §9 子文档索引表加 codebase-architecture 关键文档行
   - §14 里程碑表 M5 状态 ⏳ → ✅；新增 M6 行
   - §14 待办汇总段更新为"M5 全部完成 / M6 待启动"
   - 子文档索引表加 agent-collaboration-philosophy.md（Phase C 完成后）
3. 跑 e2e_smoke 确认

### Codex per-Phase review 重点

- 链接全部有效
- M5 状态切换语义清晰（M5 全部 acceptance gate 关闭 → M6 可启动）
- 不引入新 v0.2 重组（结构保持）

---

## Final — Codex Final cross-Phase Review + 收尾产出

### 步骤

1. **Final Codex review**：把 spec.md + plan.md + Phase A-E 全部 commit diff 输入 codex review
2. **处理 finding**：high 必修 / medium 选修（commit message 显式归档）/ low 可忽略
3. **产出 completion-report.md**：含
   - 实际 vs 计划对照（每 Phase 实际产出 + 偏离记录）
   - F084-F102 修订条目对照表（19 Feature × 5 子文档 = 95 修订点）
   - Blueprint 各文件 diff 统计（行数变化）
   - Codex review 闭环结果
4. **产出 handoff.md** 给 M6 第 1 个 Feature：
   - F104 vs F107 决策建议
   - M6 启动 checklist
   - F103 → M6 接口契约保留
5. **全量回归**：`pytest -m "not slow and not e2e_live"` 0 regression vs F102 baseline (9185862)
6. **commit**：所有 Phase 改动汇入 feature/103-blueprint-revision 分支
7. **不主动 push origin/master**：等用户拍板（CLAUDE.local.md §Spawned Task 处理流程）

### Final review 重点

- 内容准确性 vs 代码现状（最重要，纯文档 review 特点）
- 是否遗漏 F084-F102 任何重要改动（抽样 ≥ 20% 修订点验证）
- 结构合理性（新增章节位置 + 链接 + 与现有章节冲突）
- 完成定义 vs 实际产出（13 AC 是否真通过）

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Phase B 5 子模块 commit 互相依赖 anchor link | B 阶段 H1/H2/H3 用 placeholder anchor，C 完成后统一回填 |
| F084-F102 引用代码标识符错误 | 每 Phase 跑 grep 验证（如 `grep -r "NOTIFICATION_DISPATCHED" packages/`） |
| 纯文档 commit 触发 pre-commit hook e2e_smoke 失败 | F102 baseline e2e_smoke 5x 已 PASS；F103 纯文档应零影响 |
| Codex review CLI 不可用 | 备选：local codex review subcommand foreground（F093 实证有效）|
| Phase B → Phase C 顺序问题（B 引用 C anchor）| 用 placeholder + Phase E 阶段统一回填验证 |

---

**Plan v0.1 完成**。
