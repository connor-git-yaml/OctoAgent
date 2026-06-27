# Implementation Plan: F127 Sleep-Time Memory Consolidation

**Feature ID**: F127
**Status**: Draft v0.1（**设计先行 / 未实施**；Phase 拆分基于 spec.md §9 推荐路径——若用户对 5 决策做不同选择，相应 Phase 范围调整，见 §6）
**Spec**: [spec.md](./spec.md)
**Baseline**: 0f59bd3e（master HEAD）
**前置依赖**: F102 / F094 / F097 / F101 / F099 全部已合入 master（实测核实，§1）

---

## 0. 规划前提（实测结论摘要）

> 完整组件验证见 spec.md §0.1.2 / §0.1.3。规划核心结论：

1. **F127 是编排层 Feature，不造 memory 原语**——MERGE/软删/写管道/审批/cron/subagent 全现成。
2. **核心成本在 §0.1.1 的"后台 spawn 合成 parent_task"** + "LLM 发现端" + "既有事实合并审批治理"，**不是**"合并能力"。
3. **规划按 spec.md §9 推荐路径**（v0.1 = cron + 去重合并事实 + 全人审 + 强 model 验证 + H1 确认）。若决策变更，Phase 范围按 §6 矩阵调整。

---

## 1. 前置依赖核实（master 0f59bd3e）

| 依赖 | 状态 | 关键复用点 |
|------|------|------|
| F102 DailyRoutineService | ✅ master | `daily_routine.py` cron 范式 / audit-task 合成 / config 解析 / 时区降级 / token budget / fallback |
| F094 Memory SOR | ✅ master | `write_service.py:175` MERGE commit / `enums.py` SorStatus+WriteAction / propose-validate-commit |
| F084 Memory Candidates | ✅ master | `memory_candidates.py:272-721` atomic claim + rollback + promote/discard |
| F097 Subagent | ✅ master | `delegation_plane.py:955` spawn_child / `task_runner.py:1335` cleanup hook / SUBAGENT_INTERNAL |
| F101 Notify+Approval | ✅ master | `notification.py` 四级 / `approval_gate.py` request/wait/resolve |
| F099 escalate | ✅ master | `ask_back_tools.py` worker→主 Agent 审批中介 |
| AutomationScheduler | ✅ master | `automation_scheduler.py` APScheduler cron |

**结论**：所有依赖就位，F127 可启动（决策拍板后）。

---

## 2. Phase 拆分（v0.1 推荐路径，7 Phase + Verify）

> 顺序遵循项目"先简后难、先建 baseline 信心"惯例（F091 经验：B→A→C→D 而非字母序）。
> **每 Phase 后 0 regression vs baseline + e2e_smoke。**

### Phase A — 数据模型 + 事件 + 合成 root task（地基，最先）✅ **已实施**
- **范围（实施确认）**：
  - 新增 EventType `MEMORY_CONSOLIDATION_{TRIGGERED,COMPLETED,FAILED,SKIPPED,PROPOSED,APPROVED,REJECTED}`（`enums.py`，插在 `MEMORY_RECALL_*` 邻接 line 82 后）+ payload Pydantic schema（PII 防护，合并内容用 id/hash 引用不含原文）。
  - consolidation candidate 持久化：**新建 `consolidation_candidates` 表**（OQ-1 已定，**不**扩 observation_candidates）→ DDL 加在 `memory/store/sqlite_init.py`（现有 11 memory 表后，line 211 邻接）+ `init_memory_db` 内 `await conn.execute()`（line 351 后）+ 索引。store 层 CRUD + atomic-claim（复用 `memory_candidates.py:304-321` 条件 UPDATE + rowcount 范式）。
  - 合成 `MEMORY_CONSOLIDATION_ROOT_TASK_ID` + `MEMORY_CONSOLIDATION_ROOT_WORK_ID` + ensure 范式（**task+work 成对**，参考 `daily_routine.py:588` audit-task 合成 + `_ensure_audit_task` no-op 幂等）。
  - `MemoryConsolidationRun` 模型（FR-D3，参考 `memory_maintenance_runs` 表范式 `sqlite_init.py:189`）。
- **依赖**：无（纯新增）。**行为零变更**（不动任何既有读写路径）。
- **风险**：低。**FK 占位 + 表 schema 是后续 Phase 的地基，先稳。**
- **AC**：FR-D1/D2/D3 / `test_f127_consolidation_events.py`。
- **`[@test]` 实测绑定**：`octoagent/packages/core/tests/test_f127_consolidation_events.py`（EventType 枚举 + payload schema + PII 不泄漏 + run model round-trip）；`octoagent/packages/memory/tests/test_f127_consolidation_store.py`（候选表 CRUD + atomic claim CAS + init_memory_db 建表）。

### Phase B — 触发编排（MemoryConsolidationService cron）✅ **已实施（spawn 走通，未 fallback）**
- **范围（实施确认）**：
  - `MemoryConsolidationService`（cron 触发，复用 F102 `AutomationSchedulerService._scheduler.add_job` + `CronTrigger.from_crontab` + 时区降级链 USER.md>env>UTC）。
  - config 解析（`ConsolidationConfig`：`consolidation_active/time/window_days/max_facts`，复用 F102 `DailyRoutineConfig` 解析范式 + `to_crontab`，FR-F）。**v0.1 不动 USER.md 模板**（避免 1800 字符预算超限风险）——config 走独立解析，模板字段 Phase C/F 再评估接入。
  - 触发流程：`active=False` skip（写 SKIPPED）→ try-lock-skip 单飞 → `_ensure_consolidation_root()`（合成 root task+work 对）→ `plane.spawn_child(target_kind="subagent", callback_mode="async", emit_audit_event=False)` → `rejected` 写 SKIPPED(capacity) / `written` 写 TRIGGERED。
  - **Phase B 子 objective 为占位**（巩固逻辑是 Phase C）；spawn 成功即达 Phase B 验收。
  - emit `MEMORY_CONSOLIDATION_TRIGGERED/SKIPPED`。
- **依赖**：Phase A（事件 + root task/work + run model）。
- **风险（已消解）**：§0.1.1 核心难点"合成 parent_task 派后台 subagent"**实测走通**——`spawn_child` 容忍 `None` 但 `_launch_child_task` 硬解引用，故合成 **真 task+work 对**（非放宽 None）。depth=0 / active_children 从 root_work 查（深夜通常 0）。
- **AC**：FR-A1~A6 / `test_f127_consolidation_trigger.py`。
- **`[@test]` 实测绑定**：`octoagent/apps/gateway/tests/test_f127_consolidation_trigger.py`（cron 注册 / active=False skip / capacity rejected→skip / 单飞 lock skip / spawn written→TRIGGERED / H1 无 user-facing / ensure root 幂等）。
- **fallback 状态**：**未触发**。§0.1.4 记录"为什么不 fallback"——保 H2 subagent 对等，合成成本可控。若 Phase C 发现 subagent 内部巩固有不可逾越障碍再评估。

### Phase C — 回顾 + 提议（巩固 subagent 发现端）
- **范围**：
  - subagent 巩固逻辑：拉近期窗口 AGENT_PRIVATE 事实 → LLM 识别冗余 → 产 `WriteProposal[MERGE]`（FR-B）。
  - LLM prompt 设计（C9：让 LLM 判冗余，**不写规则**）+ token budget 截断 + fallback 空运行。
  - 提议过 `write_service` validate（**不 commit**）。
  - 幂等账本（NFR-4，复用 session extractor SHA256）。
  - emit `MEMORY_CONSOLIDATION_PROPOSED`。
- **依赖**：Phase B（subagent 已能 spawn）+ Phase A（候选表）。
- **风险**：中。**LLM 提议质量 = F127 核心价值**，但确定性层（validate/写候选）可单测；LLM 层留强 model 验证（Phase Verify）。
- **AC**：FR-B1~B6 / AC-2（grep 验证无硬规则）/ `test_f127_consolidation_review.py`。

### Phase D — 审批 + commit（Two-Phase 破坏性边界）
- **范围**：
  - 候选审批路由（DP-3a：复用/扩展 Memory Candidates promote/discard，FR-C）。
  - accept → atomic claim → `write_service` MERGE commit（源 SUPERSEDED）→ applied；rollback 范式。
  - reject → rejected（不碰 SOR）。
  - 敏感分区（HEALTH/FINANCE）强制人审（NFR-3）。
  - emit `MEMORY_CONSOLIDATION_APPROVED/REJECTED`。
- **依赖**：Phase C（有提议候选）+ Phase A（候选表）。
- **风险**：中-高。**C4/C7 红线 Phase**——必须证明无 agent 自主 commit 路径（AC-3）+ 软删可回滚（AC-4）。
- **AC**：FR-C1~C6 / AC-3/AC-4 / `test_f127_consolidation_approval.py`。

### Phase E — 通知（巩固完成）
- **范围**：
  - pending-review 通知（`NotificationService.notify_task_state_change`，MEDIUM，FR-E）。
  - 无提议不噪声；channels 读 USER.md；notification_id 幂等。
  - emit `MEMORY_CONSOLIDATION_COMPLETED`。
- **依赖**：Phase D（审批流就位）。
- **风险**：低（NotificationService 现成）。
- **AC**：FR-E1~E4 / AC-7 / `test_f127_consolidation_notify.py`。

### Phase F — H1 守界 + 端到端贯通验证
- **范围**：
  - 验证巩固全程无主 Agent 向用户发起对话事件（AC-5）。
  - 验证 subagent 走 SUBAGENT_INTERNAL + cleanup hook emit SUBAGENT_COMPLETED（AC-10）。
  - 端到端：cron 触发 → spawn → 提议 → 审批 → commit → 通知 全链路 event_store 可查（NFR-5）。
  - e2e_smoke 集成（可选新增 e2e 域）。
- **依赖**：Phase A-E。
- **风险**：低-中（集成验证）。
- **AC**：AC-5/AC-9/AC-10。

### Phase Verify — Final review + 强 model 验证 + 文档
- **范围**：
  - **Codex adversarial review（Final cross-Phase）**：输入 plan + 全 Phase diff，查偏离/漏 Phase/隐性债（强制，命中重大架构变更节点）。
  - **多评审 panel**：强 model（Opus/另 provider）spec-对齐专项 review，分歧项列"必须人裁"。
  - **强 model benchmark 验证（§9④ / AC-8）**：强 model 跑新增"记忆巩固域"OctoBench task（植入冗余事实→巩固→断言正确合并+recall 单条权威）。
  - completion-report + living-docs 漂移闸（Blueprint memory 章 + codebase-architecture 同步）。
- **依赖**：Phase A-F。
- **AC**：AC-8 / 全 AC 闭环 / 0 regression 最终门。

---

## 3. 依赖关系图

```
Phase A（数据模型+事件+root task）  ← 地基，最先
   ├─→ Phase B（触发编排 cron）      ← ★ 最高风险（合成 parent_task）
   │      └─→ Phase C（回顾+提议）
   │             └─→ Phase D（审批+commit）  ← ★ C4/C7 红线
   │                    └─→ Phase E（通知）
   │                           └─→ Phase F（H1+端到端）
   └────────────────────────────────────→ Phase Verify（review+强model+文档）
```

**串行为主**（A→B→C→D→E→F→Verify）；Phase 间数据流强依赖（候选表→提议→审批→通知），不宜并行。

---

## 4. 估算

| 维度 | 估算 |
|------|------|
| **Phase 数** | 7 实施 Phase + 1 Verify = 8 |
| **规模** | **L**（跨包 core/memory/gateway + 新数据模型 + 新 LLM 工具路径 + 后台 spawn 编排 + 审批治理）|
| **新增文件（约）** | `memory_consolidation.py`（service）/ `consolidation_config` 解析 / consolidation candidate store / 审批路由 / 6 个 test 文件 |
| **改动文件（约）** | `enums.py`（EventType）/ sqlite_init（表）/ USER.md 模板 + 解析 / 可能 `memory_candidates.py`（DP-3a 扩展）|
| **净增行数（粗估）** | ~1500-2500 行（含测试），取决于 DP-3a 是扩字段还是新表 |
| **风险等级** | 中-高（Phase B 合成 parent_task + Phase D C4/C7 红线）|

---

## 5. 关键风险与缓解（规划视角）

| 风险 | Phase | 缓解 |
|------|-------|------|
| 合成 parent_task 派后台 subagent 走不通 | B | Phase B 早做暴露；备选退回 F102 式主进程 LLM 调用（牺牲 H2 subagent 对等换简化）|
| agent 自主 commit 既有事实绕过审批 | D | AC-3 grep 验证无自主 commit 路径 + 软删兜底 + 多评审 panel 专查 |
| LLM 误合并重要事实 | C/D | 全人审（C7）+ 敏感分区强制人审 + 软删可回滚 + 强 model 验证提议质量 |
| 巩固 subagent 抢并发槽 | B | 深夜触发 + capacity skip + 单飞 |
| recall 强化动热路径（若决策②含 recall）| 额外 Phase | v0.1 不做（DP-5）；若做须独立 Phase + recall 基线对账 |
| DeepSeek 照不出效果致 benchmark 误判 | Verify | 强 model 单独跑记忆域 task（AC-8）|

---

## 6. ★ 决策变更对 Phase 的影响矩阵

> spec.md §9 的 5 决策若用户选非推荐项，Phase 范围如下调整：

| 决策 | 推荐（本 plan 基线）| 若选他项 → Phase 影响 |
|------|------|------|
| ① 触发 | 纯 cron（Phase B）| 选"含 idle-detect" → **+1 Phase**（新建 session `last_activity_at` + watchdog IdleDetector + AGENT_IDLE 事件 + 订阅触发）|
| ② 范围 | 去重+合并（Phase C/D）| 选"含 session 摘要" → **+1 Phase**（rolling summary 写入语义）；选"含 recall 强化" → **+1-2 Phase**（SorRecord 字段 + recall_service 热路径 + 回归基线）|
| ③ 破坏性 | 全人审（Phase D）| 选"高置信自动合并" → Phase D **范围增**（自动模式 + 置信阈值 + 非敏感分区白名单 + 自动路径额外审计），风险升 |
| ④ 验证 | 强 model OctoBench（Verify）| 选"仅确定性单测" → Verify **范围减**（但 AC-8 无法证巩固真有效，**不推荐**——LLM 判断力必须强 model 验）|
| ⑤ H1 | subagent 后台（确认）| 无变更（无争议）|

**最大范围（5 决策全选他项）**：~11-12 Phase，规模升 XL，跨多个独立能力域——**强烈建议 v0.1 收窄到推荐路径，其余列 v0.2**。

---

## 7. 实施约束（继承项目规则）

- **本 plan 为草案，未实施**——5 决策拍板后再 spec-driver implement。
- **Phase 顺序可微调**（先简后难原则）；Phase 跳过须显式归档（commit message / completion-report）。
- **每 Phase 后 Codex per-Phase review**（命中重大架构变更）+ Final cross-Phase review 强制。
- **多评审 panel**（强 model spec-对齐专项）在 Phase Verify + 重大决策节点。
- **0 regression vs baseline 0f59bd3e**（≥ baseline passed）每 Phase 守。
- **不主动 push / commit**——本会话仅产 spec + plan 草案，停在实施前。
