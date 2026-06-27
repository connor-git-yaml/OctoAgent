# Feature Specification: F127 Sleep-Time Memory Consolidation（睡眠时记忆巩固）

**Feature ID**: F127
**Feature Branch**: `feature/127-sleep-time-consolidation`
**Created**: 2026-06-27
**Status**: **Locked v0.1**（用户已拍板 5 决策为推荐窄路径，见 §0.3；v0.2 项移入 §2.2 deferred）。**Phase A + Phase B 已实施并测试通过**（数据模型地基 71 test + spawn 编排核心风险走通；Opus 自审抓 2 真回归已修——见 §0.1.4 实施期修正）。全量回归 0 净回归 vs master 0f59bd3e（唯一 worktree 失败 `test_start_degrades_without_watchdog` 实测 master baseline 同样 FAIL=watchdog 环境装了，非 F127）。**Codex review 因环境无 timeout 机制无法安全 bound，待 push 前用户手动跑。** Phase C-H 待 go/no-go。
**M7 阶段**: M7 认知深化（旗舰 Feature；与 F111 Behavior Compactor 同期"token 成本下降后做的智能合并"族）
**Upstream**（实测核实，master 0f59bd3e）:
- **F102 DailyRoutineService**（`daily_routine.py` / `daily_routine_config.py`）：cron 触发范式 + audit-task FK 占位 + LLM/fallback + USER.md 配置解析 + 时区降级链
- **F094 Memory SOR 子系统**（`packages/memory/`）：SorRecord 四态生命周期 + `WriteAction.MERGE` + propose→validate→commit 写管道 + AGENT_PRIVATE namespace
- **F084 Memory Candidates**（`memory_candidates.py`）：候选→人审 promote/discard + atomic claim + rollback
- **F097 Subagent**（`SubagentDelegation` / `spawn_child` / `cleanup_subagent_session`）：spawn-and-die + 共享 caller AGENT_PRIVATE + cleanup hook
- **F101 NotificationService + ApprovalGate**（`notification.py` / `approval_gate.py`）：四级优先级通知 + Plan→Gate→Execute 审批 SSE
- **F099 escalate_permission**（`ask_back_tools.py` / `source_kinds.py`）：worker→主 Agent 审批中介路径
- **session memory extractor**（`session_memory_extractor.py`）：每轮 facts/solutions/entities/tom 提取 + 幂等账本
- **recall hooks**（`recall_service.py`）：temporal decay（读时计算）+ MMR diversity（读时去重）
- **F011 Watchdog**（`watchdog/scanner.py` / `detectors.py`）：任务无进展检测（**非** Agent 空闲检测）
**竞品参考**: memU（`_references/opensource/memU/`，"File System as Memory"）；Hermes Agent（`_references/opensource/hermes-agent/`，cron routine + memory manager）。**⚠️ 注意：原任务想参考的 Letta `_references/opensource/` 下不存在**——本 spec 据已存在的 memU + Hermes 做反向验证（§0.2 + §8）。
**Baseline**: 0f59bd3e（master HEAD）
**Feature 性质**: 后台记忆维护——**回顾近期 memory + session → 提议去重/合并/组织 → 破坏性变更过审批（Two-Phase）→ 强化 recall**。**不改 Agent 协作模型（H1/H2/H3）**：巩固走后台 subagent，主 Agent 仍是唯一 user-facing speaker（H1 守界）。

---

## 0. 设计基础说明（实测核实，master 0f59bd3e）

### 0.1 ★ 核心定位：F127 是「编排 + 触发 + 审批治理」，不是「造记忆原语」

实测发现 OctoAgent 的 memory 原语**比竞品成熟得多**——SorRecord 四态（CURRENT/SUPERSEDED/DELETED/ARCHIVED）+ `WriteAction.MERGE`（源标 SUPERSEDED 软删可回滚）+ propose→validate→commit 写管道 + Memory Candidates 候选审批，**这些都已存在**。竞品 memU 的「去重合并」步（`memorize.py:229` `dedupe_merge`）实测是**空壳 placeholder**；Hermes 没有 sleep-time 记忆巩固（只有通用 cron + 同步 memory manager）。

所以 F127 的真正缺口**不是**「合并/删事实的能力」（已有），而是这四件**编排层**的东西：
1. **触发**：现有 F102 是 cron + 纯主进程 LLM 调用，**不 spawn subagent**；没有"主 Agent 空闲检测"基础设施（watchdog 只检测任务无进展）。
2. **后台 subagent 执行载体**：现有 `spawn_child` 只从交互式工具路径（`delegate_task_tool.py:154` / `delegation_tools.py:157`）调用，**没有"后台 routine 派 subagent"的路径**——而且 `spawn_child` 必需 `parent_task` + `parent_work`（§0.1.1）。
3. **"发现重复/可合并"的回顾逻辑**：现有 `WriteAction.MERGE` 是"已知要合并哪些"的执行端；**没有"扫描近期事实→LLM 识别冗余→提议合并候选"的发现端**。
4. **破坏性变更的审批治理 + recall 强化**：现有 Memory Candidates 审批是给"新候选事实"用的；F127 要让"合并/删既有事实"也走审批；recall 强化现有是 hook 参数（读时计算），**SorRecord 无持久权重字段**。

### 0.1.1 ★ 关键架构约束：后台 routine 没有交互式 `parent_task`（**实施期实测核实，含对草案的修正**）

**草案原假设**：`spawn_child` 签名"必需"`parent_task` + `parent_work`。**实测修正（master 0f59bd3e）**：

1. **`spawn_child` 自身对 `None` 容错**——`delegation_plane.py:1021/1033` 用 `if parent_task is not None` / `if parent_work is not None` 包裹，仅用于推断 depth（缺则 0）+ active_children（缺则 []）。单看 spawn_child，`None` 不会立刻崩。
2. **但 `_launch_child_task`（`capability_pack.py:1034`）硬解引用**，传 `None` 必 `AttributeError`：
   - `parent_task.thread_id`（:1056）/ `parent_task.task_id`（:1064）/ `parent_task.requester.channel`（:1143）/ `parent_task.scope_id`（:1145）/ `parent_task.requester.sender_id`（:1146）
   - `parent_work.work_id`（:1065）/ `parent_work.project_id`（:1088，仅 subagent 分支）
   → **结论：必须传"真对象"，不能传 `None`，也不能放宽签名让 `None` 直通**——`None` 会在 `_launch_child_task` 内炸。

3. **F102 范式确认**：建**合成 audit task**（`DAILY_ROUTINE_AUDIT_TASK_ID`，`daily_routine.py:64/588`，`status=SUCCEEDED` + `requester=RequesterInfo(channel="system", sender_id="daily_routine")`，**无 thread_id/scope_id 显式赋值**，靠 Task 默认值 `thread_id="default"`/`scope_id=""`）作为 event_store FK 占位，**直接调 `provider_router.complete()`（:396）**——**根本不 spawn subagent**。

4. **`Task`/`Work` 模型最小必填**（`task.py` / `delegation.py:258`）：
   - `Task` 必填仅 `task_id/created_at/updated_at/title/requester`；`thread_id`（默认 `"default"`）/`scope_id`（默认 `""`）/`depth`（**模型无此字段**，spawn_child 用 `getattr(...,"depth",0)` 兜底）皆可缺。
   - `Work` 必填仅 `work_id`+`task_id`（`min_length=1`），其余全有默认（`project_id=""`/`parent_work_id=None`/`status=CREATED`）。

→ **F127 落地方案（DP-4 实测验证可行）**：建系统 owned 的 **consolidation root Task + 配套 root Work** 一对（不是只建 task），作 `spawn_child(parent_task=root_task, parent_work=root_work)` 的真父对象，沿用 F102 audit-task 合成 + ensure 范式。**草案"合成 parent_task"方向正确，但实测必须 task+work 成对合成，且不能用放宽 None 的捷径。** 这是 F127 比"照搬 F102 cron"更重的核心成本。**Phase B 实测结论见 §0.1.4。**

### 0.1.2 实测核实的可复用资产清单（勿重复造）

| 能力 | 现状 | 文件/行号（master 0f59bd3e）| F127 复用方式 |
|------|------|------|------|
| 事实合并（多→一，源软删） | ✅ `WriteAction.MERGE` commit 标源 SUPERSEDED | `write_service.py:175-183` | 直接复用执行端 |
| 软删可回滚 | ✅ DELETE/MERGE 全软标，无 `DELETE FROM` SQL | `write_service.py:438-456`；`memory_store.py` 全文无物理删 | 破坏性边界天然可回滚 |
| 写管道校验 | ✅ propose→validate→commit | `write_service.py:89-175` | 巩固提议走同一管道 |
| 候选→人审 | ✅ Memory Candidates promote/discard + atomic claim + rollback | `memory_candidates.py:272-721` | 巩固候选审批范式参考/扩展（DP-3） |
| 通知用户 | ✅ NotificationService 四级 | `notification.py:551+` | 巩固完成通知直接用 |
| 审批 SSE | ✅ ApprovalGate request/wait/resolve | `approval_gate.py:163-393` | 破坏性合并审批可挂（DP-3）|
| worker→主 Agent 审批中介 | ✅ escalate_permission | `ask_back_tools.py`；`source_kinds.py` | H1 守界下 subagent 请审批路径 |
| cron 调度 | ✅ AutomationSchedulerService（APScheduler）| `automation_scheduler.py` | 触发基础设施（DP-1）|
| 每轮事实提取 | ✅ session memory extractor + 幂等账本 | `session_memory_extractor.py` | 巩固复用幂等账本范式 |
| recall 时间衰减 | ✅ temporal decay（读时算，half_life_days）| `recall_service.py:975-1008` | recall 强化参考（DP-2）|
| recall 多样性去重 | ✅ MMR（读时去重，不改存储）| `recall_service.py:1010-1077` | **非**持久去重，区别于 F127 目标 |
| subagent 共享 caller memory | ✅ `caller_memory_namespace_ids` α 语义 | `task_runner.py:265-309` | 巩固 subagent 写入路径 |
| EventType 命名惯例 | ✅ `MEMORY_* / ROUTINE_* / SUBAGENT_* / OBSERVATION_*` | `enums.py:80-261` | 新增 `MEMORY_CONSOLIDATION_*` 对齐 |

### 0.1.3 实测核实的真实缺口（F127 要新建）

| 缺口 | 为什么是真缺口（非幻觉）| 落入 |
|------|------|------|
| 后台 routine 派 subagent 路径 | `spawn_child` 只从交互工具调用；F102 不 spawn | DP-1/DP-2 + Phase A/B |
| 合成 consolidation root parent_task | `spawn_child` 必需 parent_task；后台无现成 parent | §0.1.1 + Phase A |
| "发现冗余/可合并"回顾逻辑 | MERGE 是执行端；无扫描+LLM 提议端 | Phase C |
| 合并/删**既有**事实的审批 | Memory Candidates 给新候选用；既有事实合并无审批流 | DP-3 + Phase D |
| recall 持久权重强化 | SorRecord 无 `recall_boost/last_recalled_at/recall_count` 字段 | DP-2 + Phase E（可选）|
| 主 Agent 空闲检测（若选 idle）| watchdog 只检测任务无进展；无 session/agent `last_activity_at` | DP-1 + Phase B（条件）|
| `MEMORY_CONSOLIDATION_*` 事件 | 现有 EventType 无巩固审计类型 | Phase A |

### 0.1.4 ★ Phase B 实测结论（spawn_child 后台路径 — **走通，未 fallback**）

**结论：DP-4 合成 parent_task+work 路径实测走通，未退回 F102 式主进程 LLM 调用。** 巩固保持 H2 subagent 对等。

实测要点：
- `MemoryConsolidationService` cron 触发 → `_ensure_consolidation_root()` 合成 **root Task + root Work 一对**（沿用 F102 `_ensure_audit_task` ensure 范式：不存在则建，存在则 no-op）→ 用该对作 `plane.spawn_child(parent_task=root_task, parent_work=root_work, target_kind="subagent", callback_mode="async", spawned_by="memory_consolidation", emit_audit_event=False)`。
- root Task 必须显式赋 `thread_id`（不能靠默认 `"default"`，子 thread 命名 `{thread_id}:child:{id}` 需稳定）+ `requester=RequesterInfo(channel="system", sender_id="memory_consolidation")` + `scope_id`（供子 NormalizedMessage 继承）。root Work 赋 `work_id`+`task_id`（指向 root Task）+ `target_kind=SUBAGENT`。
- `SpawnChildResult.status`：`"written"` → 派发成功写 `MEMORY_CONSOLIDATION_TRIGGERED`；`"rejected"`（depth/capacity）→ 写 `MEMORY_CONSOLIDATION_SKIPPED(reason="capacity")` 优雅退出（FR-A4）。
- **并发单飞（FR-A5）**：进程内 `bool` 标志 `_running` check-then-set（在第一个 `await` 之前完成 → 单 event loop 协作式无 race，与 Hermes `.tick.lock` try-lock-skip 等价语义；实施选 bool 而非 `asyncio.Lock` 因后者 `acquire()` 阻塞、需额外 `locked()` 判定才能"试锁即跳"，bool 更直接）——跑中再触发 → 写 SKIPPED 立即 return，不排队不阻塞。
- **Phase B 边界**：本 Phase 只验证"能派后台 subagent + 优雅 skip + 单飞 + 事件"；subagent **内部巩固逻辑（发现端/提议）是 Phase C**——Phase B 的子任务 objective 是占位描述，spawn 成功即达 Phase B 验收。

**实施期两处修正（Opus 自审 + 全量回归抓出，含对上文的更新）**：
1. **ensure root 必须在所有 `emit` 之前**（不是"active 检查之后"）：`events` 表有 `FOREIGN KEY(task_id) REFERENCES tasks`，所有 `MEMORY_CONSOLIDATION_*` 事件 `task_id` 引用 root Task。若 ensure 延后，`disabled`/`spawn_error` 路径的 `SKIPPED` 事件会 FK 违规被 `_safe_append_event` 静默丢（C2 审计缺口）。修复：`_run_consolidation` 进 try 后**先** `_ensure_consolidation_root()` 再 active 检查。
2. **root Work 会泄漏到用户可见委派/Worker 视图**（真回归，非测试 artifact）：`_ensure_consolidation_root` 建的系统占位 root Work 进 `delegation_plane.list_works()`，污染 `control_plane` `get_delegation_document`（works 列表多一条）+ `get_worker_profiles_document`（matched_works 污染 `dynamic_context.active_project_id`）。修复：`control_plane/_base.py` 新增 `SYSTEM_INTERNAL_WORK_IDS` 单一事实源，两个 document builder 都排除（类比 F102 `_daily_routine_audit` 是系统占位，但 F102 只建 task 不建 work 故无此问题——F127 因 spawn_child 必需 work 对而首次引入此面）。`list_works()` 自身**不**过滤（保持忠实 accessor；spawn 容量检查走 `list_descendant_works(parent_work_id=...)` 不受影响）。

> **为什么不 fallback**：草案预警的 fallback（退回 F102 主进程 `provider_router.complete()`）会牺牲 H2 subagent 对等（巩固不再是独立 SUBAGENT_INTERNAL session，无 cleanup hook，无并发隔离）。实测合成 task+work 成本可控（~1 个 ensure helper + 1 对 Pydantic 构造），不值得为省这点而破坏对等。**若后续 Phase C 发现 subagent 内部跑巩固 LLM 有不可逾越的上下文/工具装配障碍，再评估 fallback——但 Phase B 层面 spawn 编排已确证可行。**

### 0.2 竞品反向验证结论（剔除幻觉）

- **memU**（`memorize.py` / `service.py`）：`dedupe_merge` 步**空壳未实现**（`memorize.py:229-232`）；记忆"整理"只发生在 `_update_category_summaries`（LLM 重写 category summary，**破坏性覆写无版本历史无审批**）；**纯被动**（只在 `memorize()` 调用时跑，无定时/阈值/空闲触发）。→ **借鉴有限**：它的 summary-rewriting 思路可参考 session 历史摘要（DP-2 范围 b），但其破坏性覆写违反 OctoAgent C4/C7，**不照搬**。
- **Hermes**（`cron/scheduler.py` / `agent/memory_manager.py`）：cron 是通用任务调度（**无记忆巩固 job**）；"consolidation"指的是 **skill 库合并**（`jobs.py:1195` `rewrite_job_skills_after_consolidation`）**不是 memory**；memory manager 是同步 pre/post-turn 钩子（**无后台回顾**）。→ **借鉴**：cron 文件锁防并发 tick（`.tick.lock`）的 try-lock-skip 范式（巩固任务也应单飞，跑中再触发就跳过）。
- **结论**：两个竞品都**没有真正的 sleep-time 记忆巩固**——F127 的"空闲/定时后台 subagent 回顾+巩固+审批"在可参考的竞品里是**空白**，OctoAgent 反而因 SOR 四态 + MERGE + 审批管道而处在**更好的起点**。F127 价值=把已有原语用后台编排+审批治理串起来，而非造原语。

### 0.3 ★ 5 个范围决策（**用户已拍板锁定为推荐窄路径**，§9 详述）

| # | 决策 | **锁定值（v0.1）**|
|---|------|------|
| ① 触发机制 | — | **纯 cron（深夜定时）**；idle-detect → §2.2 deferred（需新建 session `last_activity_at` + IdleDetector，成本高） |
| ② 巩固范围 | — | **去重 + 合并**（事实层，复用 `WriteAction.MERGE`）；session 摘要 + recall 强化 → §2.2 deferred |
| ③ 破坏性边界 | — | **合并/删既有事实必须人审**（扩展 Memory Candidates 批量候选范式，**新建 `consolidation_candidates` 表**，OQ-1 已定）+ 软删 SUPERSEDED 可回滚兜底；**绝不 agent 自主 commit** |
| ④ 验证 | — | **强 model（订阅 Sonnet/GPT-5.x）跑新增"记忆巩固域"OctoBench task + 确定性单测打底**（方案设计阶段定，Phase A+B 不跑） |
| ⑤ H1 守界 | — | **确认**：subagent 不直接对用户说话；通知走 NotificationService（系统级），审批走候选 UI / escalate→主 Agent 中介 |

> **OQ-1 决议（实施期定）**：DP-3a 候选承载 = **新建 `consolidation_candidates` 表**，**不**复用 `observation_candidates`。理由（实测）：`observation_candidates`（`core/store/sqlite_init.py:844`）的 promote/discard 路由（`memory_candidates.py`）走 `snapshot_store.append_entry()` 写 **USER.md**，**根本不调 `write_service`**；而 F127 巩固提议要走 **SOR 层 `write_service` MERGE commit**（源标 SUPERSEDED）——两者数据流完全不同。复用其表会概念错配（USER.md 候选 vs SOR 合并提议）。**复用的是 atomic-claim 范式**（条件 UPDATE + rowcount，`memory_candidates.py:304-321`），**不是表本身**。

---

## 1. 目标（Why）

- **1.1 记忆越用越准**：后台回顾近期事实，识别并消除重复/冲突，让 recall 不返回一堆冗余近似事实，agent 长期偏好被准确记住。
- **1.2 不重复问用户**：合并散落的同主题事实（如三次提到的时区/口味偏好合成一条权威事实），减少 agent "明明记过还重复确认"。
- **1.3 破坏性可控（C4 + C7）**：合并/删既有记忆是不可逆动作，**必须 Plan→Gate→Execute**——不静默改用户记忆；软删（SUPERSEDED）保证审批外仍可回滚。
- **1.4 全程可观测（C2 + C8）**：每次巩固运行 + 每条合并/删提议 + 用户决策都写审计事件（hash 不含敏感原文）。
- **1.5 不抢主 Agent 的话（H1）**：巩固走后台 subagent，用户感知是"系统帮我整理了记忆"的通知，**不是** Agent 主动发起对话。
- **1.6 主 Agent 与 Worker 对等不破坏（H2/H3）**：巩固 subagent 走现有 `spawn_child` + SUBAGENT_INTERNAL session，复用 spawn-and-die + cleanup hook，不引入新的 Agent 种类。

---

## 2. 范围声明

### 2.1 In Scope（v0.1，待 §9 决策收窄）

- **MemoryConsolidationService**：cron 触发（沿用 F102 范式）的后台巩固编排器。
- **合成 consolidation root task**：作为 `spawn_child` 的 `parent_task` + event_store FK 占位（沿用 F102 audit-task 范式，§0.1.1）。
- **后台 subagent 巩固执行**：经 `spawn_child(target_kind="subagent", callback_mode="async")` 派后台 subagent，**不阻塞主 Agent**；subagent 复用 caller AGENT_PRIVATE memory（α 共享）。
- **回顾 + 提议（发现端）**：subagent 拉取近期窗口（如最近 N 天 / 最多 M 条）AGENT_PRIVATE 事实 → LLM 识别冗余/可合并组 → 产 `WriteProposal[MERGE]`（**复用现有写管道，不造新原语**）。
- **破坏性审批（Two-Phase）**：合并/删既有事实的提议**不直接 commit**，进审批队列（DP-3 决定复用 Memory Candidates 还是 ApprovalGate）→ 用户 accept/reject → accept 才走 `write_service` commit。
- **巩固完成通知**：走 NotificationService（LOW/MEDIUM 优先级，quiet hours 友好）——"整理了记忆，N 条待你确认合并"。
- **审计事件**：新增 `MEMORY_CONSOLIDATION_*`（TRIGGERED/COMPLETED/FAILED/SKIPPED + 提议级 PROPOSED/APPROVED/REJECTED），对齐 EventType 命名惯例。
- **token budget + fallback**：LLM 输入有界截断（沿用 F102 `LLM_INPUT_CHAR_BUDGET` 范式）；LLM 不可用 → deterministic fallback（无提议，仅 SKIPPED/COMPLETED 空运行）。
- **并发单飞**：巩固任务运行中再触发则跳过（try-lock-skip，借鉴 Hermes `.tick.lock`）。
- **0 regression + C10 契约同步**。

### 2.2 Explicitly Deferred（v0.2 / 后续，§9 决策驱动）

- **idle-detect 触发**（需新建 session/agent `last_activity_at` + watchdog IdleDetector + AGENT_IDLE 事件）——v0.1 纯 cron（DP-1）。
- **session 历史摘要巩固**（把多轮 session 压成 rolling summary 沉淀）——v0.1 只整事实层（DP-2 范围 b）。
- **recall 持久权重强化**（SorRecord 新增 `recall_boost/last_recalled_at` + recall_service 读时乘系数）——v0.1 不动 recall 存储（DP-2 范围 d）。
- **跨 project / Worker 私有 memory 巩固**——v0.1 限主 Agent AGENT_PRIVATE（H2 完整对等留后续）。
- **自动模式（无人审）巩固**——v0.1 全部破坏性变更人审（C7 保守）；高置信自动合并留 v0.2 评估。
- **WeeklyRoutine 式深度巩固**——不纳入 v0.1。

### 2.3 Out of Scope（明确退出）

- **不造新 memory 原语**：合并/软删/写管道全复用现有（§0.1.2）。
- **不改 Agent 协作模型**：不新增 Agent 种类，不改 H1/H2/H3 语义。
- **不引入外部 durable-execution 引擎**（LangGraph/Temporal）：现有 event-sourcing 足够（CLAUDE.local.md 主动剔除项）。
- **不照搬 memU 破坏性覆写**：违反 C4/C7。
- **不硬编码关键词判定冗余**（C9）：是否冗余/可合并由 LLM 决策，系统只提供候选窗口 + 写管道，不写规则判相似。

---

## 3. 关键设计决策（DP）

> DP-1/DP-2/DP-3 直接对应 §9 的范围决策 ①②③；这里给设计骨架，最终取舍待用户拍板。

### DP-1 触发机制（→ 范围决策 ①）

- **DP-1a（v0.1 推荐）纯 cron**：复用 `AutomationSchedulerService`，USER.md 配 `consolidation_time`（默认深夜如 `03:00`，空闲率高）+ `consolidation_active`。沿用 F102 `to_crontab` + 时区降级链（USER.md > env > UTC）。**零新基础设施。**
- **DP-1b（v0.2）idle-detect**：需新建——session/agent 级 `last_activity_at` 追踪 + watchdog `IdleDetector`（类比 `NoProgressDetector`，复用 `no_progress_threshold_seconds` 阈值思路）+ emit `AGENT_IDLE` → 订阅触发。**成本高（动 watchdog + 新字段 + 新事件）**，故 defer。
- **理由**：cron 深夜触发已能覆盖"主 agent 空闲时巩固"的 80% 价值（深夜本就空闲），idle-detect 的边际价值（白天碎片空闲也巩固）不值 v0.1 的基础设施成本。

### DP-2 巩固范围 + "巩固"定义（→ 范围决策 ②）

"巩固" = {去重, 合并, session 摘要, 强化 recall} 的子集。实测各自成本：
- **(a) 去重/合并事实**（v0.1）：复用 `WriteAction.MERGE`，**最低成本最高价值**。LLM 识别同主题冗余事实 → 提议合并成一条权威事实。
- **(b) session 历史摘要**（v0.2）：把近期多轮对话压成 rolling summary 沉淀到 memory。现有 session extractor 已有压缩逻辑可参考，但"沉淀长期摘要"是新写入语义，成本中。
- **(c) 组织/分区重整**（v0.2 评估）：把事实重新归 partition（如误归 CHAT 的 PROFILE 事实迁回）。`WriteAction.UPDATE` 可支持但需谨慎（改 subject_key/partition 影响 recall 命中）。
- **(d) 强化 recall 权重**（v0.2）：实测 SorRecord **无** `recall_boost/last_recalled_at/recall_count` 持久字段，temporal decay 是**读时算**（`recall_service.py:975`）。要"强化"得二选一：①SorRecord.metadata 加 `recall_boost: float`，recall_service 读时乘系数（侵入 recall 热路径）；②新增持久字段。**成本中-高 + 动 recall 热路径**，故 defer。
- **v0.1 推荐 = 仅 (a)**：去重+合并事实层，复用 MERGE 写管道，不动 session 摘要、不动 recall 存储。

### DP-3 破坏性边界 + 审批方案（→ 范围决策 ③）

**前提（实测）**：MERGE/DELETE 全软删（源标 SUPERSEDED/DELETED，无物理删）——**技术上已可回滚**。但 C4（不可逆动作 Two-Phase）+ C7（User-in-Control）要求：改用户**既有**记忆**不能 agent 静默自主**，即便可回滚。

两个候选审批载体（实测都现成）：
- **DP-3a（推荐）扩展 Memory Candidates 范式**：巩固提议写入候选表（`observation_candidates` 或新建 `consolidation_candidates`），`fact_content` 存 JSON `{"action":"merge","source_ids":[...],"merged_content":"...","rationale":"..."}`，`category="consolidation"`。复用 atomic claim + rollback + promote/discard 路由 + 前端候选 UI。用户在 Web 审查后 promote → 走 `write_service` MERGE commit。**优点**：复用现成审批 UI + 原子并发防护；**缺点**：候选表 schema 偏"新事实"语义，需扩字段或建新表。
- **DP-3b（备选）ApprovalGate SSE**：巩固 subagent 经 `escalate_permission` → 主 Agent `request_approval` → SSE 推审批卡 → `wait_for_decision`。**优点**：实时交互式卡片 + 超时自动 reject；**缺点**：审批卡更适合"单个高危动作即时确认"，批量合并提议（一次几十条）用候选列表更顺手。
- **推荐 DP-3a 为主**（批量提议候选列表更自然）+ 软删 SUPERSEDED 作可回滚兜底（审批漏判也能从历史恢复）。**绝不 agent 自主 commit 既有事实合并/删除**（v0.1 红线）。

### DP-4 后台 subagent 的合成 parent_task（§0.1.1 落地）

巩固 routine 触发时：
1. 建/取系统 owned 的 consolidation root task（`MEMORY_CONSOLIDATION_ROOT_TASK_ID = "_memory_consolidation_root"`，沿用 F102 audit-task 合成 + ensure 范式，`daily_routine.py:589` 参考）。
2. 用该 root task 作 `spawn_child(parent_task=root, parent_work=<合成 root work>, target_kind="subagent", tool_profile=<受限>, callback_mode="async", spawned_by="memory_consolidation")`。
3. subagent 在 SUBAGENT_INTERNAL session 跑巩固逻辑，复用 caller（root）AGENT_PRIVATE namespace。
4. 完成触发 `cleanup_subagent_session` → emit `SUBAGENT_COMPLETED`（现成）。
- **并发限额注意**（实测）：`DelegationManager` 硬限 `MAX_DEPTH=2 / MAX_CONCURRENT_CHILDREN=3`。巩固 subagent 占 1 槽。深夜触发时 active_children 通常为 0，风险低；但需在 spawn rejected（CAPACITY_EXCEEDED）时优雅跳过（写 SKIPPED 不报错，不抢用户主动 delegate 的槽）。

### DP-5 recall 强化的诚实边界

若 §9 决策 ② 选含 recall 强化：现有 recall 是读时 hook（decay + MMR），**强化重要记忆的 recall 权重需新增持久字段或 metadata 系数**——这是动 recall 热路径的侵入式改动。v0.1 推荐**不做**，避免 F127 范围爆炸 + recall 回归风险。若做，须独立 Phase + recall 回归基线对账。

---

## 4. 功能需求（FR）

> AC↔test 绑定遵循本项目 SDD 强化约束（关键 AC 紧邻标注 test 路径）。**本 spec 为设计草案，test 路径为规划目标（实施期创建），非既存文件。**

### FR-A 触发与编排（MemoryConsolidationService）

- **FR-A1**：提供 `MemoryConsolidationService`，cron 触发（DP-1a），触发时刻读 USER.md `consolidation_time` + `consolidation_active`（复用 F102 config 解析范式 + 时区降级链）。
- **FR-A2**：`consolidation_active=False` → 写 `MEMORY_CONSOLIDATION_SKIPPED(reason="disabled")` 并 return（不 spawn）。
- **FR-A3**：触发先 ensure 合成 consolidation root task（DP-4），作 event_store FK + spawn parent。
- **FR-A4**：经 `spawn_child(callback_mode="async")` 派后台 subagent；spawn rejected（CAPACITY/depth）→ 写 `MEMORY_CONSOLIDATION_SKIPPED(reason="capacity")` 优雅退出，**不阻塞、不报错、不抢槽**。
- **FR-A5**：并发单飞——巩固运行中再触发则跳过（try-lock-skip）+ 写 SKIPPED。
- **FR-A6（H1）**：巩固全程**不向用户直接发起对话**；用户感知仅来自 FR-E 通知（系统级）。

  - `[@test]` → `octoagent/apps/gateway/tests/test_f127_consolidation_trigger.py`（cron 触发 / active=False skip / capacity skip / 单飞 skip / H1 无用户对话）

### FR-B 回顾与提议（巩固 subagent 发现端）

- **FR-B1**：subagent 拉取近期窗口 AGENT_PRIVATE CURRENT 事实（窗口 = 最近 N 天 ∪ 最多 M 条，N/M 可配，沿用 F102 token budget 有界范式）。
- **FR-B2（C9）**：是否冗余/可合并由 **LLM 决策**，系统**不**写关键词/相似度规则判重；系统只提供候选窗口 + 写管道。
- **FR-B3**：LLM 输出结构化提议 → 转 `WriteProposal[action=MERGE]`，`metadata.merge_source_ids` = 待合并源 SOR ids，`content` = 合成权威事实，`rationale` = 合并理由。
- **FR-B4**：提议过现有 `write_service` validate（source ids 存在性 / subject_key 唯一性），**validate 通过但不 commit**（待审批，FR-D）。
- **FR-B5**：token budget 截断（输入 ≤ 配额，沿用 F102 `LLM_INPUT_CHAR_BUDGET`）；窗口超限优先纳入同 subject_key 聚集的事实（提高合并命中）。
- **FR-B6**：LLM 不可用/空响应 → deterministic fallback（无提议，写 `MEMORY_CONSOLIDATION_COMPLETED(proposals=0, fallback=true)`）。

  - `[@test]` → `octoagent/apps/gateway/tests/test_f127_consolidation_review.py`（窗口拉取 / LLM 提议→WriteProposal[MERGE] / validate-no-commit / token budget / fallback 空运行）

### FR-C 破坏性变更过审批（Two-Phase，DP-3a）

- **FR-C1（C4）**：合并/删**既有**事实的提议**绝不 agent 自主 commit**——写入候选/审批队列（DP-3a：consolidation candidate）。
- **FR-C2（C7）**：用户可对每条提议 accept / reject；reject 明确丢弃（不静默超时改记忆）。
- **FR-C3**：accept → atomic claim（pending→applying）→ 调 `write_service` commit MERGE（源标 SUPERSEDED）→ 状态 applied + emit `MEMORY_CONSOLIDATION_APPROVED`；任何失败回滚 applying→pending（复用 Memory Candidates rollback 范式）。
- **FR-C4**：reject → 状态 rejected + emit `MEMORY_CONSOLIDATION_REJECTED`（**不**碰 SOR）。
- **FR-C5（可回滚兜底）**：即便审批漏判，MERGE 源是 SUPERSEDED 软删，可经 `list_sor_history` 反推 + 状态改回 CURRENT 恢复（C4 Durability）。
- **FR-C6（H1）**：候选审批 UI 是用户主动查看（Web 红点/通知引导），**不是** Agent 在对话里逼问。

  - `[@test]` → `octoagent/apps/gateway/tests/test_f127_consolidation_approval.py`（no-self-commit / accept→MERGE commit→SUPERSEDED / reject→不碰 SOR / atomic claim rollback / 软删可回滚）

### FR-D 数据模型与事件

- **FR-D1**：新增 `MEMORY_CONSOLIDATION_TRIGGERED / COMPLETED / FAILED / SKIPPED`（运行级）+ `MEMORY_CONSOLIDATION_PROPOSED / APPROVED / REJECTED`（提议级）EventType，对齐 `enums.py` 命名惯例。payload 不含敏感原文（合并内容用 hash 或 id 引用，沿用现有 PII 防护惯例）。
- **FR-D2**：consolidation candidate 持久化（DP-3a：扩 `observation_candidates` 字段或建 `consolidation_candidates` 表，含 source_ids / merged_content / rationale / status / created_at / run_id）。
- **FR-D3**：巩固运行审计（run_id / window / facts_reviewed / proposals_made / proposals_approved / proposals_rejected / elapsed_ms / fallback），可选 `MemoryConsolidationRun` 模型（参考现有 maintenance run 范式）。

  - `[@test]` → `octoagent/packages/core/tests/test_f127_consolidation_events.py`（EventType 枚举 + payload schema + PII 不泄漏）

### FR-E 通知（巩固完成）

- **FR-E1**：巩固产出待审提议 → `notify_task_state_change(priority=MEDIUM, event_type="MEMORY_CONSOLIDATION_PENDING_REVIEW")`，payload summary = "整理了 K 条记忆，N 条合并建议待确认"。
- **FR-E2**：无提议（事实已干净）→ LOW 或不通知（避免噪声，沿用 F102 空数据不推送范式）。
- **FR-E3**：通知 channels 读 USER.md（复用 F102 `summary_channels`）；quiet hours 友好（深夜触发，MEDIUM 受 quiet hours 约束，用户睡醒看到）。
- **FR-E4**：notification_id sha256 幂等（复用 F116 `generate_notification_id`）。

  - `[@test]` → `octoagent/apps/gateway/tests/test_f127_consolidation_notify.py`（pending-review 通知 / 无提议不噪声 / channels / 幂等）

### FR-F 配置（USER.md）

- **FR-F1**：USER.md 机器可读字段：`consolidation_active`（bool，默认 false——v0.1 保守默认关，用户显式开）/ `consolidation_time`（HH:MM，默认 03:00）/ `consolidation_window_days`（默认 7）/ `consolidation_max_facts`（默认 50）。
- **FR-F2**：字段解析复用 F102 USER.md 解析范式 + 1800 字符模板预算约束（memory: `project_user_md_template_budget`——加机器可读字段须吝啬，复用现有解析器不新增大段模板）。

  - `[@test]` → `octoagent/apps/gateway/tests/test_f127_consolidation_config.py`（字段解析 / 默认值 / 时区 / 预算）

---

## 5. 验收标准（AC）

> P1 故事 AC 紧邻 §4 已标 `[@test]`。本节聚合关键验收门。

- **AC-1**（FR-A）：cron 到点触发巩固；`consolidation_active=false` 跳过；spawn capacity 不足优雅跳过不报错。
- **AC-2**（FR-B / C9）：LLM 识别冗余事实并产 MERGE 提议；**无任何关键词/相似度硬规则判重**（grep 验证无规则分支）。
- **AC-3**（FR-C / C4 / C7）：合并/删既有事实**100% 过审批**，无 agent 自主 commit 路径；accept→源 SUPERSEDED；reject→SOR 不变。
- **AC-4**（FR-C5）：MERGE 后可经历史恢复（软删可回滚验证）。
- **AC-5**（FR-A6 / H1）：巩固全程无主 Agent 向用户发起对话的事件；用户交互仅候选审批 + 通知。
- **AC-6**（FR-D / C2 / C8）：每次运行 + 每条提议 + 每个决策都有审计事件；payload 无敏感原文。
- **AC-7**（FR-E）：巩固完成发通知（有提议时）；无提议不噪声。
- **AC-8**（强 model 验证，§9④）：强 model 跑新增"记忆巩固域"OctoBench task，验证巩固真改善 recall 质量（控变量 DeepSeek 照不出，需强 model 单独验证）。
- **AC-9**（0 regression）：全量回归 vs baseline 0 净回归；e2e_smoke 8/8。
- **AC-10**（H2/H3）：巩固 subagent 走现有 `spawn_child` + SUBAGENT_INTERNAL，不新增 Agent 种类；`cleanup_subagent_session` 正常 emit SUBAGENT_COMPLETED。

---

## 6. 非功能需求（NFR）

- **NFR-1（性能/不抢占）**：巩固 subagent `callback_mode="async"` 后台跑，不阻塞主 Agent 对话；深夜触发避开用户活跃期。
- **NFR-2（C6 降级）**：LLM 不可用 → fallback 空运行（不崩）；spawn 失败 → 优雅 skip；候选表写失败 → 该运行标 FAILED 不影响下次。
- **NFR-3（C5 最小权限）**：巩固 subagent `tool_profile` 受限（只需 memory 读 + 提议写，不需 terminal/web）；敏感分区（HEALTH/FINANCE）事实合并**强制人审**（不进任何自动路径）。
- **NFR-4（幂等）**：巩固提议幂等账本（复用 session extractor SHA256 范式），防 crash 重放产重复候选。
- **NFR-5（可观测）**：巩固运行可经 event_store 查询完整链路（trigger→propose→approve/reject→complete）。

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM 误判合并重要不同事实 | 丢失记忆细节 | 全部人审（C7）+ 软删可回滚（FR-C5）+ 敏感分区强制人审（NFR-3）|
| 巩固 subagent 抢用户 delegate 并发槽 | 用户主动派活被拒 | 深夜触发避峰 + capacity 不足优雅 skip（FR-A4）+ 单飞（FR-A5）|
| 后台 spawn 合成 parent_task 引入新坏味道 | 架构债 | 沿用 F102 audit-task 合成范式（已验证），不发明新机制（§0.1.1）|
| recall 强化动热路径引回归 | recall 行为变化 | v0.1 不做（DP-5 defer），若做须独立 Phase + recall 基线对账 |
| DeepSeek 控变量照不出巩固效果 | benchmark 误判无效 | 强 model 单独验证（§9④ / AC-8）|
| 候选审批表 schema 与"新事实"语义错配 | 概念泄漏 | DP-3a 决定扩字段 vs 建新表（实施期定，spec 标 open）|
| USER.md 模板预算超限 | 模板膨胀 | 复用现有解析器，机器可读字段吝啬（FR-F2 + memory 预算约束）|

---

## 8. 竞品对照表（反向验证后）

| 维度 | memU | Hermes | OctoAgent 现状 | F127 v0.1 |
|------|------|--------|----------------|-----------|
| 触发 | 被动（user call）| cron（无记忆 job）| F102 cron（不 spawn）| cron 深夜（DP-1a）|
| 后台 subagent | ✗ 主进程 | ✗ 主进程 | F097 spawn-and-die（仅交互路径）| ✅ 后台 spawn（DP-4 合成 parent）|
| 去重/合并 | dedupe_merge 空壳 | 无 | `WriteAction.MERGE`（执行端）| ✅ LLM 发现端 + MERGE 执行端 |
| 破坏性 | 覆写无版本无审批 | 无 | 软删可回滚 | ✅ 软删 + **人审**（C4/C7）|
| 人审 | ✗ | skill 有/memory 无 | Memory Candidates（新事实）| ✅ 扩展到既有事实合并 |
| recall 强化 | ✗ | ✗ | 读时 decay/MMR | defer v0.2（DP-5）|
| 版本历史 | ✗ | ✗ | SOR 四态 | ✅ 复用 |
| 并发防护 | ✗ | 文件锁 | atomic claim | ✅ 单飞 + claim |

**结论**：F127 把 OctoAgent 已有的成熟原语（SOR 四态 + MERGE + 审批管道 + cron + subagent）用后台编排 + 审批治理串起来，在竞品空白处建立差异化，而非重复造原语。

---

## 9. ★ 5 个范围决策详述（待用户拍板，本 spec 只给推荐）

### ① 触发：idle-detect / cron / 两者？
- **现状**：watchdog 只检测"任务无进展"（`scanner.py`），**无主 Agent 空闲检测**；Task 仅 `updated_at`，**无 session/agent `last_activity_at`**；cron（AutomationSchedulerService）现成。
- **推荐**：**v0.1 纯 cron（深夜 03:00 默认）**。idle-detect 列 v0.2。
- **理由**：cron 深夜触发已覆盖"空闲时巩固"80% 价值（深夜本就空闲）；idle-detect 需新建 `last_activity_at` 字段 + watchdog IdleDetector + AGENT_IDLE 事件 = 显著基础设施成本，边际价值（白天碎片空闲也巩固）不值 v0.1。**若用户更看重"任意空闲即巩固"的即时性**，可选两者但 v0.1 范围翻倍。

### ② 巩固范围：只 AGENT_PRIVATE 事实 vs 含 session 摘要？"巩固"=哪几样？
- **现状**："巩固"可含 {去重, 合并, session 摘要, 组织重整, 强化 recall}。去重/合并复用 MERGE（最便宜）；session 摘要是新写入语义；recall 强化需动 recall 热路径（无持久权重字段）。
- **推荐**：**v0.1 = 去重 + 合并（事实层）**。session 摘要 + recall 强化 + 组织重整列 v0.2。
- **理由**：去重/合并价值最高成本最低（直接复用 MERGE 写管道）；session 摘要和 recall 强化各自是独立能力域（前者新写入语义，后者侵入 recall 热路径），塞 v0.1 会范围爆炸 + 回归风险。**若用户希望一次到位含 session 摘要**，可加 Phase 但需评估 rolling summary 写入语义。

### ③ 破坏性边界：候选+审批 vs agent 自主+可回滚？
- **现状**：MERGE/DELETE 全软删（源 SUPERSEDED，可回滚），但 C4（不可逆动作 Two-Phase）+ C7（User-in-Control）要求改既有记忆不能 agent 静默自主。两个审批载体现成：Memory Candidates（批量候选列表）+ ApprovalGate（即时卡片）。
- **推荐**：**合并/删既有事实必须人审（DP-3a 扩展 Memory Candidates 范式）+ 软删 SUPERSEDED 作可回滚兜底**。**绝不 agent 自主 commit。**
- **理由**：改用户已记住的记忆是高敏感动作，即便可回滚也应人审（符合 C4/C7 + 项目"User-in-Control"协作准则）；Memory Candidates 的批量候选列表比 ApprovalGate 即时卡片更适合"一次几十条提议"。可回滚是兜底不是替代审批。**若用户接受"高置信自动合并 + 可回滚"以减审批负担**，可在 v0.2 加自动模式（非敏感分区 + 置信阈值），但 v0.1 保守全人审。

### ④ 验证：强 model 方案
- **现状**：本域 DeepSeek 控变量照不出（记忆深化看 pass_rate 看不出差异——M5 baseline 已证 DeepSeek delegation/philosophy 全 0% 是能力画像）。需强 model 单独验证。
- **推荐**：**强 model（订阅 Sonnet 或 GPT-5.x，非 DeepSeek）跑新增"记忆巩固域"OctoBench task + 确定性单测打底**。task 设计：①植入若干同主题冗余事实 → 跑巩固 → 断言产正确 MERGE 提议；②accept 后断言 recall 返回单条权威事实（非多条冗余）；③敏感分区事实断言强制人审不自动合并。确定性层（单测）验证写管道/审批/事件链；强 model 层验证 LLM 真能识别冗余。
- **理由**：巩固质量本质是 LLM 判断力（识别哪些事实该合并），DeepSeek 在 delegation/认知类任务画像弱（实测），必须强 model 才能验证"巩固真有效"。可借 M6 推迟的"OctoHarness 轻量 bootstrap + 强 model OctoBench"基础设施。**注意 benchmark ToS**：强 model 若走订阅 OAuth 跑自动化有灰色地带（CLAUDE.local.md 已记），可考虑强 model 走 API key 单跑记忆域少量 task（非全量）。

### ⑤ H1 守界：确认
- **现状**：H1 = 主 Agent 总是 receive 用户消息 + 唯一 user-facing speaker。巩固走后台 subagent。
- **推荐确认**：**巩固 subagent 不直接对用户说话**——①完成通知走 NotificationService（系统级通知，非 Agent 对话）；②审批请求经候选 UI（用户主动查看，非 Agent 逼问）或 escalate→主 Agent 中介（H1 唯一 speaker）；③subagent 在 SUBAGENT_INTERNAL session 跑，无 user-facing 通道。
- **理由**：完全符合 H1——用户感知是"系统帮我整理了记忆"（被动通知 + 主动审查），不是"Agent 突然找我聊记忆"。subagent 经 spawn-and-die 复用现有委托模型，不引入 user-facing 能力。**这条无争议，确认即可。**

---

## 10. Open Questions（实施期解决，不阻 spec 拍板）

- ~~**OQ-1**：DP-3a 候选承载——扩 `observation_candidates` 字段 vs 建 `consolidation_candidates` 新表？~~ **✅ 已定（§0.3 + Phase A）：建新表**。`observation_candidates` 走 USER.md 路径不调 write_service，与 F127 SOR MERGE 数据流错配；复用其 atomic-claim 范式而非表本身。
- **OQ-2**：合成 consolidation root task 的生命周期——长驻单例（像 F102 audit task 永久占位）vs 每次运行新建？（FK 占位用单例更简，spawn parent 用单例 depth 语义需确认）
- **OQ-3**：巩固窗口取"最近 N 天"还是"自上次巩固以来新增"？（增量更省，但需记 last_consolidation_cursor）
- **OQ-4**：提议级 EventType 是否进 control_plane endpoint 暴露？（沿用 F093 MED P2-1 convention：审计事件不一定进 endpoint）
- **OQ-5**：强 model 验证走订阅 OAuth（ToS 灰色）vs API key 少量 task？（§9④，benchmark ToS 权衡）

---

## 11. 实施约束（继承项目规则）

- **设计先行**：本 spec + plan 为草案，**未实施**；5 决策拍板后再 spec-driver implement。
- **重大架构变更节点**：F127 涉 LLM 工具新增（巩固提议）+ 跨包（memory/gateway/core）+ 新数据模型 → 命中 Codex adversarial review + 多评审 panel（强 model 专项 spec-对齐 review）强制节点。
- **0 regression**：每 Phase 后回归 vs baseline 0 净回归，e2e_smoke 必过。
- **Constitution 硬约束**：C2（事件）/ C4（破坏性 Two-Phase）/ C5（最小权限）/ C6（降级）/ C7（User-in-Control）/ C9（禁硬编码关键词替代 LLM）+ H1（主 Agent mediated）全程守。
- **completion gate**：完成时产 completion-report + living-docs 漂移闸（巩固机制进 Blueprint memory 章 + codebase-architecture）。
