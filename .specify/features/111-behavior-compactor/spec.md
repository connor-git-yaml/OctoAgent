# Feature Specification: F111 Behavior Compactor（行为规则 LLM 智能合并去冗余）

**Feature ID**: F111
**Feature Branch**: `feature/111-behavior-compactor`
**Created**: 2026-07-06（设计先行草案）/ **收窄**: 2026-07-15（按用户 2026-07-06 拍板 + 收窄期实测）
**Status**: **v1.0 收窄定稿（可实施）**——5 岔路全部收敛（§0.0），岔路③由收窄期实测定（§0.3）。
**M7 阶段**: M7 认知深化（F127 睡眠时记忆巩固之后的同域下一棒；F063 Phase 3 推迟项）
**Baseline**: master `f2081010`（M9 十 Feature 质量体系全清；rebase 自设计稿基线 1e64ecd3，落差 133 commits 均为 M8 后半 + M9，与本 Feature 触碰面零冲突，可复用资产清单 §0.1.1 逐条复核仍成立）

---

## 0.0 ★ 收窄拍板结论（先读，覆盖设计稿 §0.3/§9 的推荐值）

设计稿（2026-07-06）按"最窄路径"推荐 ①单文件+②纯手动+③F136 gate+④强 model+⑤前台。
用户拍板（2026-07-06，milestones.md M7 表 F111 行为权威文本）**翻转了②**并把③交实测定：

| # | 岔路 | **拍板/收窄结论（v1.0）** | 决策来源 |
|---|------|------|------|
| ① | 范围 | **单文件内去冗余**；跨文件矛盾检测显式 defer v0.2 | 用户拍板（维持推荐）|
| ② | 触发 | **cron 定时 + 手动 `octo behavior compact` 双触发**（cron 为主诉求）。cron 复用 F127 编排范式（cron + 合成 root Task+Work + spawn 后台 subagent + 单飞 + quiet hours）；手动路径前台直调发现端（不 spawn）| **用户拍板（翻转设计稿推荐）** |
| ③ | 审批载体 | **独立 `behavior_compact_candidates` 候选表（仿 F127 模式）+ REST accept/reject**；不用 F136 `gate_behavior_write`。实测证据与理由见 §0.3（gate 阻塞语义与 nightly 无人值守批量提议三重硬冲突）| **收窄期实测定（本 spec §0.3）** |
| ④ | 验证 | 三层：a) 确定性护栏进 L4/L3（H1 变小 / H2 PROTECTED 字节级 / 禁区双层）；b) **全链 `e2e_scripted` 覆盖**（脚本化 LLM 输出驱动 提议→审批→落盘→版本历史，零真 LLM）；c) **合并质量真 LLM 用例挂 `e2e_full + real_llm`**（进 M9 F141 release live lane）| 用户拍板 |
| ⑤ | H1 守界 | cron 路径后台 subagent（审计容器，主 Agent 唯一 user-facing speaker）；**绝不 agent 自主 commit**（一切写入过人审——候选表 accept 是唯一落盘入口）| 用户拍板 |

**范围收敛的连带决策**（本 spec 定，理由在正文）：

- **v0.1 无 `behavior.compact` LLM 工具**——拍板双触发 = cron + CLI，不含 LLM 工具路径。
  设计稿 US-2/AC-8 的"LLM 工具走 F136 gate"整条 defer v0.2（届时 gate 的"用户在场即时批"
  语义才有调用方，§2.2）。
- **cron 扫描范围 v0.1 = SHARED 3 文件**（AGENTS.md / TOOLS.md / USER.md，全局唯一实例）；
  PROJECT.md / KNOWLEDGE.md 经手动 CLI 指定 project 触发；per-project cron fan-out defer
  v0.2（§3 DP-6）。
- **CLI = 薄 HTTP 壳**（走 gateway REST，同 attest_commands 范式）——不在 CLI 进程独立起
  provider_router（§3 DP-7）。

---

## 0. 设计基础说明（实测核实，master f2081010）

### 0.1 ★ 核心定位：F111 是「发现端 + 触发 + 审批治理」，绝大部分底座已由 F127/F136/F107 建好

沿用 F127 §0.1 的"编排 vs 造原语"框架。**实测发现 F111 的所有难点底座都已存在**——F127 把"LLM 提议改用户既有数据 → 候选表 → 人审 → apply"整条路走通，F107 把"behavior 文件版本历史 + 可回滚"整条路走通，F108a 把"behavior 写核 prepare/commit 两段式"收口。F111 = **把这几条已验证的路，用一个新的发现端（行为规则去冗余 LLM）串起来**，而非造任何原语。

**F111 与 F127 的关系（一句话）**：同族不同源——F127 操作对象是 memory SOR（自带 SUPERSEDED 软删可回滚），F111 操作对象是 behavior 文件（可回滚兜底挂 F107 版本历史）。发现端"LLM 判冗余 → 护栏 → 候选 → 人审 → apply"的骨架同构；差异点全在数据面（§0.2）。

### 0.1.1 实测核实的可复用资产清单（勿重复造，全部在 f2081010 复核过）

| 能力 | 现状 | 文件 | F111 复用方式 |
|------|------|------|------|
| cron 触发 + 合成 root spawn + 单飞 + 占位泄漏防御 | ✅ `MemoryConsolidationService` | `services/memory_consolidation.py` | **★ 编排直接仿**（自己的 root id + `SYSTEM_INTERNAL_WORK_IDS` 补 id）|
| LLM 发现端确定性组件范式 | ✅ `ConsolidationDiscoveryService`（llm_client 注入 + prompt + fallback + 幂等账本白名单）| `services/consolidation_discovery.py` | **★ 发现端直接仿**（数据面换 behavior 文件，§0.2）|
| 候选表五态 + atomic claim + CONFLICT + 失败语义二分 | ✅ `ConsolidationStore` + `ConsolidationApprovalService` | `memory/store/consolidation_store.py` / `services/consolidation_approval.py` | **★ 模式直接仿**（新建 behavior 域表，不复用 memory 表——概念错配防治，§0.3）|
| 候选人审 REST 面 | ✅ `routes/consolidation_candidates.py`（GET/accept/reject + 409 契约）| 同左 | **★ 路由结构直接仿** |
| behavior 写核 prepare/commit | ✅ 路径解析 + 预算检查 / mkdir + write_text 两段 | `behavior_workspace/write.py` | accept 落盘复用 |
| behavior 版本历史 + 可回滚 | ✅ `record_behavior_version`（record-after + 首版 baseline）+ F107 恢复流 | `services/behavior_versioning.py` + `store/behavior_version_store.py` | **★ 可回滚兜底直接复用**（accept 落盘后记版本，用户不满意走 F107 恢复流回退）|
| REST Two-Phase 落盘先例（无 F136 gate）| ✅ `worker_service._handle_behavior_restore_version`（actor=用户经 REST，confirmed 两段，写后 record_version + invalidate cache）| `control_plane/worker_service.py:645` | **accept 落盘序列逐字对照**（用户 REST 动作本身就是审，不再叠 gate）|
| 行为文件测量 | ✅ `measure_behavior_total_size` + `_BEHAVIOR_SIZE_WARNING_THRESHOLD=15000`（无消费者）| `behavior_workspace/skeleton.py` / `_types.py` | CLI `--list-size` 展示 + 超阈值提示（F063 P3 遗留原语首次接上消费者）|
| 单文件字符预算 | ✅ `check_behavior_file_budget` / `BEHAVIOR_FILE_BUDGETS` | `behavior_workspace/budget.py` | H1"变小"对账 + 候选元数据展示 |
| USER.md config 解析范式 | ✅ 左边界锚定正则 + HTML 注释块剥离 + fallback + 1800 字符预算纪律 | `services/consolidation_config.py` / `daily_routine_config.py` | **compact_active / compact_time 两字段照范式新增**；user_timezone / summary_channels 直接复用 F102 extractors |
| 通知 | ✅ NotificationService 四级 + quiet hours discard + `session_id=""` 全局桶 | `services/notification.py` + F127 `_notify_pending_review` | 仅 proposals>0 发一条 MEDIUM，照抄 F127 决策表 |
| CLI → gateway HTTP 范式 | ✅ attest_commands（实例 env 解析 port/token + httpx + `_scrub` 脱敏）| `provider/dx/attest_commands.py` | CLI compact 薄壳照范式 |
| e2e 脚本化全链范式 | ✅ `test_e2e_scripted_write_approval.py`（F144 gap-1：真 harness + REST + 零真调用三重防御）| `apps/gateway/tests/e2e_live/` | **★ L3 全链用例直接仿**（脚本缝差异见 §6 AC-11 注）|
| G-lite 真 LLM 验证脚手架 | ✅ `glite/run_glite.py`（F127）| `.specify/features/127-sleep-time-consolidation/glite/` | real_llm 质量用例的植入物/断言二分范式参考（但 F111 直接落成 `real_llm` marker 测试进 lane，非独立脚本——M9 后 lane 是正式载体）|

### 0.1.2 ★ 关键设计张力：behavior 是「整文件重写」，不是「多条记录合并」

F127 的合并单元是 **SOR 记录**（N 条源 → 1 条新权威事实，源软删可回滚是 SOR 层天然属性，`source_ids ⊆ 窗口` 白名单挡幻觉）。

F111 的合并单元是**整个 behavior 文件的文本**（LLM 读全文 → 输出精简后全文 → 覆写落盘）。三个 F127 没有的问题及对策：

1. **可回滚兜底不同**：文件级覆写 → 兜底挂 **F107 版本历史**（accept 落盘后 `record_behavior_version`，用户不满意走 F107 恢复流回退整个文件）。实测确认 F107 版本历史对全部 9 个 behavior 文件真覆盖（W1 独立表，不受 W2 workspace git deny-list 影响）。
2. **"合并质量"更难自动校验**：无 source_ids 白名单这种确定性护栏——LLM 可能丢规则、篡改语义、引入新规则。对策 = 护栏分层（§0.1.3 H1-H6 确定性层 + H4 人审 diff + H5 真 LLM 质量用例）。
3. **新鲜度语义更凶**：behavior 文件比 SOR 更容易被用户并发编辑（Web 工作台 / 直接改盘 / agent 写工具），候选 pending 过夜期间源文件变更概率高——**accept 前 source_hash 新鲜度验证不可省**（F127 handoff §1.1 明示），失配 → CONFLICT 终态。

### 0.1.3 ★ 合并质量护栏（分层，确定性层能做的先做）

- **H1 合并后必须严格更小**（F063 P3 + OpenClaw"合并后更大跳过"经验）：`len(compacted) >= len(original)` → 丢弃提议 + SKIPPED(not_smaller)。去冗余的定义就是变小。
- **H2 PROTECTED 区段字节级保留**：`<!-- 🔒 PROTECTED -->...<!-- /🔒 PROTECTED -->` 区段走**占位符方案**——发现端先提取区段、替换成 `<<<PROTECTED_n>>>` 占位符再送 LLM（LLM 根本看不到也改不了受保护内容）；LLM 输出中每个占位符必须 **exactly-once**（缺失/重复 → 丢弃 + SKIPPED(protected_violation)）；确定性替换回原文后再做终局断言（无残留占位符 + 每个区段字节级包含——构造性保证 + belt-and-braces）。占位符方案强于"输出比对"方案：篡改在构造上不可能，且省 prompt 预算。**碰撞守卫**（自查①）：原文本身含 `<<<PROTECTED_` 字面量 → 占位/计数会错乱，整文件 SKIPPED(placeholder_collision)，不猜测。
- **H3 结构弱校验**：合并后非空（strip 后）；与原文完全相同 → 无事可提（SKIPPED(no_change)，非 fallback）。
- **H4 破坏性人审 + diff 可视**：候选 REST/CLI 展示 unified diff（服务端 difflib 生成），用户亲自判断"有没有丢东西"——语义层无法自动化的部分兜到人。
- **H5 真 LLM 质量用例**（拍板④c）：植入语义重复规则的 AGENTS.md 变体 → 断言合并后更小 + PROTECTED 保留 + 非 fallback，挂 `e2e_full + real_llm` 进 release live lane（M9 F141 已建成 lane 载体，比设计稿"归 M7 统一 OctoBench 方案"更落地）。
- **H6 USER.md 机器可读字段 parity（F111 特有，收窄期新增）**：USER.md 在 compact 范围内（拍板①），但它承载 F102/F115/F127/**F111 自身**的机器可读配置字段（`user_timezone` / `daily_summary_time` / `routine_active` / `summary_channels` / `consolidation_*` / **`compact_active` / `compact_time`**——自查②：漏掉自身字段会让 compact 把 `compact_active: true` 合并掉后静默自我关闭）——LLM 精简若把这些行"合并掉"，会静默改变 cron/通知/巩固行为。护栏 = **复用生产 extractors**（`daily_routine_config` / `consolidation_config` / `behavior_compact_config` 的 `extract_*_from_user_md`，单一事实源）对 original 与 compacted 各跑一遍，任一提取值不一致 → 丢弃 + SKIPPED(config_drift)。这是契约级确定性对账（复用生产解析函数），不是关键词判重规则，C9 合规；未来新增 config 字段未覆盖时的语义 = 少保护不误伤（fail-open 到 H4 人审 diff）。

**降级（C6）**：LLM None/异常/空响应/缺分隔符 → fallback 0 提议不崩；护栏不过 → SKIPPED 有 reason 可审计；行为文件零触碰。

### 0.1.4 ★ 禁区文件单一事实源（fail-closed 白名单）

**单一事实源 = 正向白名单 `COMPACT_ELIGIBLE_FILE_IDS = ("AGENTS.md", "TOOLS.md", "USER.md", "PROJECT.md", "KNOWLEDGE.md")`**（`behavior_workspace/_types.py`，与既有 `*_BEHAVIOR_FILE_IDS` 邻接）。排除集 `COMPACT_EXCLUDED_FILE_IDS` 从 `ALL_BEHAVIOR_FILE_IDS - eligible` **派生**（非独立常量，防两处漂移）= {SOUL, IDENTITY, BOOTSTRAP, HEARTBEAT}。

> 偏离设计稿命名归档：设计稿写"单一事实源常量 COMPACT_EXCLUDED_FILE_IDS"。收窄改为
> **eligible 白名单为主源**——未来新增第 10 个 behavior 文件时，排除表方案会让它静默变成
> 可 compact（fail-open），白名单方案默认不可 compact 直到显式加入（fail-closed）。

排除理由：SOUL/IDENTITY（人格核心，LLM 重写自己人格有自我漂移风险）；BOOTSTRAP（一次性引导脚本，compact 无意义且违 H1）；HEARTBEAT（短结构化，收益低不值风险）。

防御两层：**发现端范围排除**（根治——非 eligible 不读不送 LLM 不产候选）+ **accept 落盘前 file_id 校验**（防漏网/防存量脏数据，与新鲜度验证同位，判定失败 → CONFLICT）。

### 0.2 发现端数据面差异（vs F127，实施对照表）

| 维度 | F127 巩固发现端 | F111 compact 发现端 |
|------|------|------|
| 输入 | scope 窗口内 N 条 SOR 事实 | 单个 behavior 文件全文（PROTECTED 已占位符化）|
| LLM 输出契约 | JSON `{"groups":[...]}`（N→M 映射需结构化）| **分隔符契约**：`===COMPACTED===` 全文 + `===RATIONALE===` 理由（§8）|
| 挡幻觉护栏 | source_ids ⊆ valid_ids 白名单 | H1 变小 + H2 占位符 exactly-once + H3 + H6（§0.1.3）|
| 输入超预算 | 截断（少看几条事实，下次补）| **SKIP 不截断**（reason=too_large）——整文件重写截断=丢内容，性质不同（偏离设计稿 FR-11，理由归档）|
| 幂等账本 | 输出 content_hash 阻断 {PENDING,APPLYING,APPLIED} | **输入 (file_id, source_hash) 阻断 {PENDING,APPLYING}**——整文件重写下"同源已有待审提议"比"同输出"更对：LLM 输出非确定，同源重跑会产不同输出文本，输出 hash 挡不住重复提议堆积；APPLIED 不需阻断（apply 后文件 hash 即变，同 source_hash 天然不再复现；经 F107 恢复回退后允许重新提议恰是正确语义）。REJECTED 不阻断（用户拒过可重试，同 F127）。输出 content_hash 字段保留作审计 |
| model alias | `cheap`（识别合并组是轻量判断）| **`main`**——整文件重写质量直接决定候选可用率（写坏=用户全拒=功能死），且 nightly ≤3 文件 ×~4k token 成本可忽略（偏离 F127 选择，理由归档）|
| 太少跳过 | facts < 2 → 空运行 | `len(content) < 200` → SKIPPED(too_small)（资源护栏，非判重规则）|

### 0.3 ★ 岔路③实测结论：候选表，不用 F136 gate（收窄期核心产出）

任务书要求"优先复用 F136 `gate_behavior_write`——前提是实测审批卡片在无人值守 cron 下能持久排队且后台任务不被阻塞挂死"。**实测（读 `write_approval.py` + `notification` 语义）结论：三重硬冲突，gate 不适配 nightly 批量提议**：

1. **阻塞 + 300s 超时丢弃**：gate 是 `wait_for_decision(timeout_seconds=300)` 阻塞模型（`write_approval.py:39/416`）。nightly 03:30 无人值守 → 每个提议 5 分钟即 `decision=timeout` → 调用方不得落盘 → 提议全部丢弃，cron 路径零交付。审批请求本身不为"次日再批"设计（`ApprovalRequest.expires_at` 同 300s）。
2. **超时不恢复 RUNNING**：超时分支刻意不做 `mark_running_from_waiting_approval`（`write_approval.py:431-434`，F101 HIGH-02 v3——task_runner monitor 是终态唯一 owner）→ 后台 compact 任务会系统性卡 WAITING_APPROVAL 直至 monitor 判 FAILED，每晚制造失败任务噪声。
3. **CRITICAL 通知豁免 quiet hours**：gate 的审批通知是 `NotificationPriority.CRITICAL`（`write_approval.py:403`，F136 设计给"用户在场等结果"场景）→ 深夜 cron 会每文件一条 CRITICAL 穿透 quiet hours 打扰用户——与 F127 nightly 语义（仅 proposals>0 一条 MEDIUM + quiet hours discard + 次日 Web 主动发现）正好相反。

辅助理由：④gate 串行阻塞（一次一张卡）vs 候选表批量持久 + 异步逐条审；⑤ApprovalGate handle 在内存，gateway 重启丢 pending，候选表 SQLite 持久过夜；⑥v0.1 无 LLM 工具路径（§0.0），gate 在 F111 没有调用方——它的适用场景（LLM 对话内发起、用户在场即时批）留给 v0.2 `behavior.compact` 工具。

**概念错配防治**（设计稿 §0.1.2 问题 3 + F127 OQ-1 教训）：**不复用** `consolidation_candidates` 表（字段是 SOR 记录级：source_sor_ids/partition/proposal_id），**新建 behavior 文件级 `behavior_compact_candidates`**（core 域，字段 §5 FR-7）。复用的是**模式**：五态（PENDING→APPLYING→APPLIED / REJECTED / CONFLICT）+ atomic claim（条件 UPDATE + rowcount CAS）+ CONFLICT 新鲜度终态 + 失败语义二分（判定失败→CONFLICT / 自身异常→回滚 PENDING）+ 幂等账本阻断白名单。

**审批面 v0.1 = REST + CLI**（`GET /api/behavior/compact/candidates` + `accept`/`reject` + `octo behavior compact --list/--apply/--reject`）；前端候选审批 UI 与 F127 的同类缺口合并成一次前端 follow-up（§2.2）。

### 0.4 竞品反向验证结论（沿用设计稿，无变化）

- **Agent Zero behaviour merge**：LLM 自改写行为规则违 C4/C7（M6 竞品结论已剔除）——F111 只借"LLM 智能合并"思路，强制人审。
- **OpenClaw**："合并后更大则跳过"确定性护栏 → H1。
- **memU / Hermes**：dedupe_merge 空壳 / skill 库合并非 behavior——借鉴有限。
- **结论**：behavior 文件"LLM 智能合并 + 强制人审 + 版本历史兜底"在可参考竞品里是空白；OctoAgent 因 F107+F127 底座处在更好起点。

---

## 1. 目标（Why）

- **1.1 行为文件越用越精**：用久了 AGENTS.md/TOOLS.md 积累重复/矛盾规则，compact 让 LLM 智能合并去冗余，规则集更小更一致——且**无人值守自动发生**（cron 主诉求），用户只需次日批复。
- **1.2 降 token 成本**：behavior 文件每轮注入 LLM 上下文——去冗余直接降注入体积。
- **1.3 破坏性可控（C4 + C7）**：改行为规则必须 Plan（发现端提议）→ Gate（候选人审）→ Execute（accept 落盘）；F107 版本历史保证审批外仍可回退。
- **1.4 全程可观测（C2 + C8）**：每次运行 + 每个候选 + 每次决策写审计事件。
- **1.5 H1 守界**：cron 后台 subagent 不抢话；用户感知仅来自 NotificationService（仅 proposals>0 一条 MEDIUM）。
- **1.6 绝不 agent 自主 commit**：发现端只产候选；落盘唯一入口 = 用户 accept（REST/CLI，actor 是人）。

---

## 2. 范围声明

### 2.1 In Scope（v1.0）

- **地基**：`COMPACT_ELIGIBLE_FILE_IDS` 白名单 + PROTECTED 占位符提取/插回 helper（core）+ `BehaviorCompactCandidate` 模型/store/DDL（core）+ `BEHAVIOR_COMPACT_*` 8 事件 + payload schema。
- **发现端**：`BehaviorCompactDiscoveryService`（gateway，llm_client 注入式）：读单文件 → 禁区/太小/太大检查 → PROTECTED 占位符化 → LLM 精简（契约 §8）→ 护栏 H1/H2/H3/H6 → 幂等 → 写 PENDING 候选 + emit PROPOSED。
- **审批端**：`BehaviorCompactApprovalService`（gateway）：accept = atomic claim → 禁区+新鲜度+H2 复验 → `commit_behavior_file_write` → `record_behavior_version` → invalidate behavior pack cache → APPLIED + emit；reject = REJECTED + emit；失败语义二分。
- **cron 编排**：`BehaviorCompactionService`（gateway，仿 `MemoryConsolidationService`）：cron（compact_time，默认 03:30）→ compact_active 检查 → 单飞（进程内 bool + 持久 child 检查）→ 合成 root Task+Work → spawn 后台 subagent（审计容器）→ 逐 SHARED eligible 文件跑发现端 → COMPLETED/FAILED → 仅 proposals>0 通知 MEDIUM。
- **手动触发**：REST `POST /api/behavior/compact/trigger`（同步跑发现端，无 spawn，无 active 门——用户显式动作）+ CLI `octo behavior compact [FILE_ID] [--project SLUG]`。
- **审批面**：REST `GET candidates` / `POST {id}/accept` / `POST {id}/reject`（409 契约同 F127）+ CLI `--list` / `--apply ID` / `--reject ID`。
- **测量接线（FR-10）**：CLI `octo behavior compact --list-size`（`measure_behavior_total_size` + 超 `_BEHAVIOR_SIZE_WARNING_THRESHOLD` 标注"建议 compact"，本地只读不走 HTTP）。
- **config**：USER.md 机器可读字段 `compact_active`（默认 **False** 保守关）+ `compact_time`（默认 03:30，与 F127 03:00 错峰）；`user_timezone` / `summary_channels` 复用 F102 extractors，不新增字段。
- **验证**：L4 单测全覆盖护栏/状态机/编排 + L3 `e2e_scripted` 全链 + `real_llm` 质量用例（lane 载体）。

### 2.2 Out of Scope（显式排除 → v0.2 或独立 follow-up）

- **跨文件矛盾检测**（拍板①defer）：跨文件规则语义建模 + 调和是更高一档 LLM 判断。
- **`behavior.compact` LLM 工具 + F136 gate 接线**：v0.1 双触发无 LLM 工具路径；v0.2 加工具时 gate 的"在场即时批"语义才有调用方。
- **per-project cron fan-out**：cron v0.1 只扫 SHARED 3 文件；PROJECT/KNOWLEDGE 手动可达。理由：nightly LLM 成本有界（≤3 调用）+ 项目枚举/slug 解析复杂度不进 v0.1。
- **bulk_reject**：单次运行候选 ≤3，逐条 reject 成本可忽略（F127 因 MAX_PROPOSALS_PER_RUN=20 才需要）。
- **前端候选审批 UI / SSE 订阅**：与 F127 同类缺口（其 handoff §3）合并一次前端 follow-up。
- **SOUL/IDENTITY 解禁配置项**：v0.1 白名单硬排除，不提供解禁开关（少一个可误配的危险面；真有需求 v0.2 议）。
- **size-warning 注入 resolve/对话路径**：FR-10 v0.1 只落 CLI 展示；"超阈值时 agent 主动建议 compact"依赖 LLM 工具路径，随 v0.2。
- **behavior_compact_runs 运行审计表**：F127 有表但其编排服务实测未写入（events 已覆盖审计）；F111 不建（少一张无消费者的表）。

---

## 3. 关键设计决策（DP，全部已收敛）

### DP-1 范围 = 单文件内去冗余（拍板①）
护栏（变小/PROTECTED/结构/config parity）全在单文件闭包内确定性可校验。

### DP-2 触发 = cron + 手动双触发（拍板②）
- **cron**：仿 F127 全套（§0.1.1 行 1）。自己的占位 id：`BEHAVIOR_COMPACT_ROOT_TASK_ID = "_behavior_compact_root"` / `BEHAVIOR_COMPACT_ROOT_WORK_ID = "_behavior_compact_root_work"` / thread `_behavior_compact`；root Work id 加入 `control_plane/_base.SYSTEM_INTERNAL_WORK_IDS`（frozenset 字面量 + guard 测试防漂移，同 F127 范式）；root Task `channel="system"` + `status=SUCCEEDED`（既有通用系统任务抑制面自动覆盖：task_runner:1009/1276 + orchestrator:484 按 channel 过滤，`expand_internal_work_ids` BFS 排除后代 Work）。
- **手动**：REST trigger 同步直调发现端（前台，不 spawn，秒级返回）；`compact_active=False` 不拦手动（active 只门 cron——用户显式动作永远可用）；与 cron 共享单飞（并发触发 → SKIPPED(already_running)）。
- **spawn 的角色**（沿用 F127 归档偏离）：后台 subagent 是 H2 对等审计容器（SUBAGENT_INTERNAL session + cleanup + SUBAGENT_COMPLETED），发现端是确定性组件在编排服务内跑——`tool_profile="minimal"` 挂不到工具（F127 handoff 坑 7），free-loop 发现端撞同一堵墙，F111 不重试。

### DP-3 审批载体 = 独立候选表 + REST（§0.3 实测定）

### DP-4 禁区 = eligible 白名单 fail-closed（§0.1.4）

### DP-5 护栏分层 H1-H6（§0.1.3）+ C9 边界
"是否冗余、怎么合并"完全由 LLM 判断（prompt 不给关键词/相似度/行数阈值规则）；确定性层只做护栏（大小对账 / PROTECTED 占位符 / 非空 / config parity / 禁区 / 新鲜度）。资源护栏（too_small=200 / too_large=输入预算）是成本闸不是判重规则。

### DP-6 cron 范围 = SHARED 3 文件（§0.0 连带决策）

### DP-7 CLI = 薄 HTTP 壳（§0.0 连带决策）
compact 需要 LLM + 候选表 + 审批面，全在 gateway；CLI 本地起 provider_router 会造第二条落盘/审批路径（TOCTOU + 双事实源）。gateway 未运行 → CLI 报错引导 `octo service`（M8 后 gateway 是常驻服务，这是正常形态）。`--list-size` 例外走本地（只读测量无副作用）。

### DP-8 apply 不设预算硬闸（偏离 restore handler 先例，归档）
`_handle_behavior_restore_version` 对超预算内容拒绝写入；F111 accept **不拒**：盘上文件可能已超预算（用户手工编辑造成——写路径都拦预算但直接改盘不经写路径），compact 恰是修它的工具，H1 已保证严格变小（只会更接近预算）。候选元数据带 budget 信息供人审参考。

---

## 4. User Scenarios（P1）

### US-1 nightly cron 自动 compact（主场景）
用户在 USER.md 设 `compact_active: true`。03:30 cron 触发 → 后台 subagent 审计容器 + 发现端扫 AGENTS/TOOLS/USER → AGENTS.md 识别出 4 组重复规则 → 护栏通过 → 写 1 条 PENDING 候选 → 一条 MEDIUM 通知"帮你整理了行为规则：1 条精简提议待确认"（quiet hours 内 discard，次日 Web/CLI 主动发现）→ 用户早上 `octo behavior compact --list` 看 diff → `--apply <id>` → 落盘 + 版本记录。不满意 → F107 恢复流回退。

### US-2 手动 compact
用户："我的 AGENTS.md 越写越乱。" → `octo behavior compact AGENTS.md` → CLI 调 REST trigger → 秒级返回 diff 预览 + 候选 id + "3200 → 2100 字符" → 用户看完 `--apply <id>` 确认落盘。

### US-3 护栏拦截
LLM 输出丢了 PROTECTED 占位符 → H2 拦截（SKIPPED protected_violation，零候选零通知）；输出更大 → H1 拦截（not_smaller）；USER.md 精简把 `user_timezone` 行合并掉 → H6 拦截（config_drift）。行为文件零触碰，事件可审计。

### US-4 禁区 + 新鲜度
`octo behavior compact SOUL.md` → 400"SOUL.md 是人格核心文件，不参与自动合并"。候选 pending 过夜期间用户改了 AGENTS.md → 次日 accept → source_hash 失配 → 409 CONFLICT 终态"文件已变更，请重新触发 compact"。

---

## 5. FR（功能需求）

- **FR-1 发现端确定性组件**：`BehaviorCompactDiscoveryService`（llm_client 注入式 Protocol，仿 `ConsolidationLLMClient` 契约）读单文件 → LLM 精简 → 护栏 → 写 PENDING 候选。**绝不落盘**（C4）。
- **FR-2 LLM 判冗余不写规则（C9）**：prompt 让 LLM 识别语义重复/矛盾并精简，不写关键词/相似度/行数阈值判重；沿用 F127"宁缺毋滥，合并是破坏性操作要谨慎"句。
- **FR-3 H1 变小护栏**：`len(compacted) >= len(original)` → 丢弃 + SKIPPED(not_smaller)。
- **FR-4 H2 PROTECTED 占位符护栏**：提取 → `<<<PROTECTED_n>>>` 占位 → LLM 输出占位符 exactly-once 校验 → 确定性插回 → 终局字节级包含断言；违反 → 丢弃 + SKIPPED(protected_violation)。
- **FR-5 H3/H6/C6**：非空 + 非同一（no_change）；USER.md config parity（H6，config_drift）；LLM None/异常/空/缺分隔符 → fallback 0 提议不崩。
- **FR-6 禁区双层**：发现端 eligible 白名单排除（根治）+ accept 前 file_id 校验（漏网 → CONFLICT）。
- **FR-7 候选表**：`behavior_compact_candidates`（core sqlite_init）：candidate_id PK / run_id / file_id / agent_slug / project_slug / source_hash（提议时原文 sha256，新鲜度锚）/ compacted_content / rationale / size_before / size_after / content_hash（输出 sha256，审计）/ status（pending/applying/applied/rejected/conflict）/ created_at / decided_at。幂等：同 (file_id, agent_slug, project_slug, source_hash) 存在 {PENDING,APPLYING} 候选 → 跳过（§0.2）。
- **FR-8 accept 唯一落盘入口（C4/C7 红线）**：atomic claim（PENDING→APPLYING CAS）→ 验证（eligible + 重读盘 sha256==source_hash + H2 复验 compacted 含全部 PROTECTED 区段）→ 判定失败 → CONFLICT 终态 + emit CONFLICTED + REST 409；验证自身异常 → 回滚 PENDING 可重试（handoff 坑 5）→ `prepare/commit_behavior_file_write` → `record_behavior_version(old=重读盘内容, source="compact")` → `invalidate_behavior_pack_cache` → APPLIED（CAS + 先 commit 状态再 emit，handoff 坑 1）。reject：PENDING→REJECTED CAS + emit。
- **FR-9 事件（C2）**：`BEHAVIOR_COMPACT_{TRIGGERED,COMPLETED,FAILED,SKIPPED,PROPOSED,APPLIED,REJECTED,CONFLICTED}`（8 个，对称 F127；payload 计数/hash/id 引用，**不含 behavior 原文全文**——PII/体积纪律）。payload Pydantic schema 落 core `models/payloads.py`。
- **FR-10 测量接线**：CLI `--list-size` 展示各文件大小 + 超阈值标注（F063 P3 遗留原语首个消费者）。只提示不自动 compact（C9）。
- **FR-11 输入/输出预算（C6）**：输入超 `COMPACT_INPUT_CHAR_BUDGET`（8000，自查③收紧）→ SKIPPED(too_large)（**不截断**，§0.2 归档偏离）；输出 `max_tokens=COMPACT_OUTPUT_TOKEN_BUDGET`（8192）+ `===RATIONALE===` 尾分隔符必需（截断守卫，§8）。
- **FR-12 cron 编排**：仿 F127 全套（DP-2）；misfire grace 30s；cron 注册失败/spawn 异常不阻塞 gateway（C6）。
- **FR-13 通知**：仅 proposals>0 发一条 MEDIUM（`notify_task_state_change`，type=`BEHAVIOR_COMPACT_PENDING_REVIEW` 字符串，`session_id=""` 全局桶 + `state_transition_event_id=run_id` 幂等 + channels=summary_channels + quiet hours 由 NotificationService 处理）；0 提议/FAILED/SKIPPED/手动触发全静默。
- **FR-14 config**：`compact_active`（默认 False）/ `compact_time`（默认 03:30）照 F127 解析范式（左边界锚定 + 注释块剥离 + fallback + WARNING）；**不动 USER.md 模板**（1800 字符预算）。

---

## 6. AC ↔ test 显式绑定（SDD 强化）

| AC | 内容 | test |
|----|------|------|
| AC-1 | 发现端产候选绝不落盘（validate-no-commit）：discovery 后行为文件字节不变 + 候选 PENDING | `apps/gateway/tests/test_f111_compact_discovery.py::test_discover_proposes_without_write` |
| AC-2 | C9 无硬编码判重：发现端源码 grep 无相似度/行数阈值/关键词判重规则 | `test_f111_compact_discovery.py::test_no_hardcoded_dedup_rules` |
| AC-3 | H1：LLM 输出 ≥ 原文 → 0 候选 + SKIPPED(not_smaller) | `::test_larger_output_rejected` |
| AC-4 | H2：占位符缺失/重复 → 0 候选 + SKIPPED(protected_violation)；正常路径 PROTECTED 区段字节级保留在候选内容中 | `::test_protected_placeholder_roundtrip` + `::test_protected_violation_rejected`；helper 层 `packages/core/tests/test_f111_protected_sections.py` |
| AC-5 | C6 fallback：LLM None/异常/空/缺分隔符 → 0 候选不崩（fallback=True）| `::test_llm_unavailable_fallback` + `::test_missing_delimiter_fallback` |
| AC-6 | 禁区第一层：非 eligible 文件不读不产候选（SOUL/IDENTITY/BOOTSTRAP/HEARTBEAT）| `::test_excluded_files_skipped` |
| AC-7 | C4 红线：发现端模块无 `commit_behavior_file_write` 调用（静态）+ accept 是唯一写路径（approval 单测覆盖写前置条件）| `::test_no_autonomous_commit_path`（grep 断言）+ `test_f111_compact_approval.py` 全套 |
| AC-8 | accept 全链：claim → 新鲜度 → 落盘 + record_version + cache invalidate + APPLIED 事件；reject 不落盘；source_hash 失配 → CONFLICT 终态 + 不落盘；claim 竞态 → 拒绝不双写；IO 异常 → 回滚 PENDING | `apps/gateway/tests/test_f111_compact_approval.py`（accept/reject/conflict/race/rollback 各一）|
| AC-9 | H6：USER.md config 字段被精简丢失 → SKIPPED(config_drift) | `test_f111_compact_discovery.py::test_user_md_config_drift_rejected` |
| AC-10 | cron 编排：active=False 跳过 / 单飞 / capacity 优雅 skip / root Work 进 SYSTEM_INTERNAL_WORK_IDS（guard 防漂移）/ 仅 proposals>0 通知 | `apps/gateway/tests/test_f111_compact_trigger.py`（仿 test_f127_consolidation_trigger.py）|
| AC-11 | L3 全链（拍板④b）：脚本化 LLM 输出 → REST trigger → 候选 → REST accept → 真落盘 + 版本 + 事件链；reject 半边文件不变。零真 LLM（resolve_for_alias bomb）零宿主 OAuth | `apps/gateway/tests/e2e_live/test_e2e_scripted_behavior_compact.py`（marker `e2e_scripted + e2e_live`）|
| AC-12 | 真 LLM 质量（拍板④c）：植入语义重复 AGENTS.md 变体 → 真 LLM 发现端 → 候选更小 + PROTECTED 保留 + 非 fallback | `apps/gateway/tests/e2e_live/test_e2e_behavior_compact_real_llm.py`（marker `e2e_full + real_llm`，release live lane；凭证缺失 SKIP）|
| AC-13 | REST 契约：list/accept/reject + 409（conflict/claim 失败）+ trigger | `apps/gateway/tests/test_f111_compact_routes.py` |
| AC-14 | CLI：trigger 预览 / --apply / --reject / --list / --list-size / gateway 不可用引导 | `packages/provider/tests/test_f111_behavior_compact_cli.py` |
| AC-15 | 0 regression vs baseline f2081010（全量 `-m "not real_llm"` ≥ baseline passed）+ e2e_smoke 全绿 | 全量 pytest |

> **AC-11 marker 归档**（写给 tests/AGENTS.md 一致性闸）：`e2e_scripted` 语义表写"ScriptedModelClient
> 经 OctoHarness(model_client=...) DI"。F111 v0.1 无决策环工具（LLM 工具 defer v0.2），compact
> 管道的 LLM 缝是 message-adapter 协议（`complete(messages=...)`）而非 SkillRunner 协议
> （`generate(manifest=...)`）——脚本脑实现前者，经编排服务的 `llm_client` 公开注入缝进入。
> 交付物与 e2e_scripted 行核心语义一致（脚本化 LLM 输出 / 全链确定性 / 零真 LLM 零 OAuth /
> CI-runnable）；实施时同步微调 tests/AGENTS.md marker 表措辞覆盖此形态（完成闸要求表与实况一致）。

---

## 7. Edge cases（已推演）

- **PROTECTED 被 LLM 挪位置**：占位符方案下位置=LLM 保留占位符的位置（语义上 LLM 有权重排非保护内容的顺序），内容字节级不变由构造保证。占位符 exactly-once 失败 → 丢弃。
- **文件无 PROTECTED 区段**：空集 trivially pass。
- **PROTECTED 标记不配对**（有开无闭）：提取器按保守语义处理——不配对视为格式损坏，整文件 SKIPPED(protected_malformed)（不猜测边界，防半个区段泄给 LLM）。
- **文件本就精炼**：LLM 判无可合并输出原文 → H3 no_change SKIPPED（正常空运行非 fallback）。
- **候选 pending 期间文件被改**：accept 时 source_hash 失配 → CONFLICT 409（US-4）。
- **同文件多个 pending 候选**：input-hash 幂等挡同源重复；不同源（文件变了再触发）可并存，先 accept 的生效，其余 accept 时新鲜度失配 → CONFLICT 自然收敛。
- **accept 与 cron 并发**：accept 走 store CAS；发现端只 insert 新候选不改旧候选状态，无写冲突。
- **gateway 重启**：候选表持久，重启后照常 list/accept；cron 跨 tick 单飞用持久 child Work 检查兜（进程内 bool 丢失场景，照抄 F127 补强）。
- **LLM 输出混入解释文字/code fence**：分隔符解析只取 `===COMPACTED===` 与 `===RATIONALE===` 之间内容，容忍前后噪声；剥 code fence 包裹（LLM 常见怪癖，F127 G-lite 实测）。
- **两条矛盾规则被"调和"成改语义的一条**：确定性层无法拦——H4 人审 diff 兜（这正是强制人审的原因）。

---

## 8. 发现端输出契约（契约 A'，分隔符包裹全文）

```
===COMPACTED===
<精简后完整 markdown 全文（含占位符 <<<PROTECTED_n>>>）>
===RATIONALE===
<合并了什么、为什么（进候选 rationale 与人审展示）>
```

- 解析：定位 `===COMPACTED===` 起点 → 截到 `===RATIONALE===` → strip 外层 code fence（若 LLM 包了 ```）→ 占位符校验 → 插回。
- 缺 `===COMPACTED===` → fallback（0 候选）。
- **★ `===RATIONALE===` 是必需的完整性信号（自查③，偏离设计稿"可选尾段"）**：输出 token
  截断产生的"半个文件"天然**更小**，能骗过 H1——若 RATIONALE 可选，截断输出会被当合法候选。
  尾分隔符缺失 → 一律 fallback（0 候选），把"截断"与"忘格式"统一按保守丢弃处理。配套
  `COMPACT_INPUT_CHAR_BUDGET=8000`（预算内文件 ≤4000 + 2 倍手工编辑超限余量）+
  `COMPACT_OUTPUT_TOKEN_BUDGET=8192`，保证合规输入的完整输出不会触顶。
- **为什么不用 JSON（契约 B）**：全文 JSON 转义笨重 + 大文件易触发输出截断产生半个 JSON；分隔符契约无转义、贴合"重写文件"本质（设计稿 §8 结论维持）。

---

## 9. 全局约束

- **Phase 顺序可微调**（先简后难）；Phase 跳过须显式归档。
- **每 Phase 后 0 regression vs baseline f2081010** + e2e_smoke 全绿；终门全量 + e2e_scripted。
- **M9 门禁**：changed-lines ≥90% 覆盖（新生产代码必须带测试）；pre-commit hook import venv 最近 sync 树——新 e2e 测试若 import 新 src 模块须 `pytest.importorskip` 防御（F138 先例）；真 LLM 用例必须 `e2e_full + real_llm` 双标；flaky 不 blanket rerun（六字段入册 quarantine.json）；时序敏感测试标 `xdist_group`。
- **PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`，禁 uv sync**。
- **命中重大架构变更**（cron 编排 + 新事件类型 + behavior 写路径）→ Codex（`codex review --base origin/master` scoped 迭代 0 HIGH）+ Opus 式对抗自审，分歧列人裁。
- **不主动 push origin**——完成后归总报告等用户拍板。
- **completion-report + handoff + living-docs 漂移闸**（Blueprint behavior 章 / tests/AGENTS.md marker 表 / milestones.md F111 行）。

---

## 10. F111 真缺口 vs 已有（实施对账表）

| F111 需要 | 已有？ | 落点 |
|-----------|--------|------|
| cron/spawn/单飞/占位泄漏防御编排 | ✅ 仿 F127 | 新 `services/behavior_compaction.py` |
| 发现端本体（读→占位→LLM→护栏→候选）| ❌ 真缺口 | 新 `services/behavior_compact_discovery.py` |
| PROTECTED 占位符提取+插回 | ❌ 真缺口（F063 P3 设计过从未实现）| 新 `core/behavior_workspace/protected.py` |
| 输出契约解析 | ❌ 真缺口（F111 特有）| 发现端内 |
| 候选表 + store | ❌ 真缺口（模式仿 F127，表新建）| 新 `core/models/behavior_compact.py` + `core/store/behavior_compact_store.py` + core sqlite_init DDL |
| 审批端（claim/新鲜度/apply）| ❌ 真缺口（模式仿 F127）| 新 `services/behavior_compact_approval.py` |
| REST 面 | ❌ 真缺口（结构仿 F127 路由）| 新 `routes/behavior_compact.py` + main.py 注册 |
| CLI compact 子命令 | ❌ 真缺口 | `provider/dx/behavior_commands.py` 扩 |
| config 两字段 | ❌ 真缺口（范式照抄）| 新 `services/behavior_compact_config.py` |
| `BEHAVIOR_COMPACT_*` 事件 | ❌ 真缺口 | `core/models/enums.py` + `payloads.py` |
| 落盘/版本/缓存失效/预算/测量/通知/LLM 客户端 | ✅ 全现成 | 复用（§0.1.1）|
