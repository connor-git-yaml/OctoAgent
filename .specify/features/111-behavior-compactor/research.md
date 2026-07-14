# F111 Behavior Compactor — 研究笔记（可复用件盘点 + 证据路径）

**日期**: 2026-07-06
**用途**: 记录 F111 设计阶段的实测发现，给实施阶段直接用（勿重复 discover）。所有路径相对 `octoagent/`（monorepo 根，worktree 内在 `.claude/worktrees/F111-compactor/octoagent/`）。

---

## 1. F127 可复用件盘点（直接复用 / 仿写，含证据路径）

> F127 handoff（`.specify/features/127-sleep-time-consolidation/handoff.md`）§1 已列可复用件；本节补实测的**精确文件 + 行号 + 复用/差异判定**。

### 1.1 ★ 破坏性 behavior 写入人审 —— 直接复用 F136（不是 F127）

**关键修正**：F127 handoff §1.1 说复用 F127 的候选审批范式。但实测发现 **F136 才是 F111 更该直接复用的**——F136 就是"behavior 文件写入过服务端 ApprovalGate"，与 F111 的落盘需求逐字对应。

- **文件**：`apps/gateway/src/octoagent/gateway/services/builtin_tools/write_approval.py::gate_behavior_write`（287 行，F136 交付）。
- **它做了什么**：REVIEW_REQUIRED behavior 文件写入的服务端审批门——request_approval（含 unified diff）→ ApprovalManager 双注册（Web resolve 依赖，否则 404）→ mark_waiting_approval → notify CRITICAL（豁免 quiet hours）→ wait_for_decision(300s) → 按 decision 条件恢复 RUNNING。返回 `WriteApprovalOutcome{decision, approval_id, reason}`，调用方仅 `decision=="approved"` 才落盘。
- **F111 复用方式**：LLM 工具路径（`behavior.compact`）直接调它，`old_content`=落盘前重读、`new_content`=LLM 精简后全文、`budget_chars`=`check_behavior_file_budget`。**机械一致，零改动**。
- **关键坑（F136 handoff §坑1，最坑）**：审批渲染渠道（Web/OperatorInbox/Telegram）读 `risk_explanation` 不读 `diff_content`——`gate_behavior_write` 已把 diff 拼进 `risk_explanation`（:126-132），F111 复用即自动拿到。
- **allow-always 语义（F136 handoff §坑2/3）**：`gate_behavior_write` 已传 `allow_always_eligible=False`（:183）——每次写独立审批，用户点"总是批准"降级为一次性。F111 复用即继承。

### 1.2 ★ behavior 版本历史 + 可回滚兜底 —— 直接复用 F107 W1

- **文件**：`packages/core/src/octoagent/core/store/behavior_version_store.py::SqliteBehaviorVersionStore`（248 行，F107 W1 交付）+ `packages/core/src/octoagent/core/models/behavior_version.py`（62 行）。
- **关键方法**：`record_version(key, content, baseline_content=)`（append-only + 首版 baseline，:59-111）；`get_version_content(key, version_no)`（恢复读旧版，:215-219）；`list_versions` / `get_latest_two`（diff）。
- **F111 复用**：compact 落盘后调 `record_version`（old_content 落盘前重读，与 F136 FR-9 同语义）——用户不满意走 F107 恢复流回退整个文件。**这是 F111 对 §0.1.2 问题 1"文件级覆写可回滚"的兜底**。
- **实测确认覆盖全 9 文件**：F136 `test_f136_write_approval.py::test_approved_write_lands_with_version_and_events` 已验证 approved 落盘后 record_version；F107 deny-list 排的是 W2 workspace git 的 secrets/behavior，behavior 版本历史是 W1 独立表不受影响。
- **版本 key 派生**：`packages/core/src/octoagent/core/behavior_workspace/paths.py::behavior_version_key_from_path`（:209，从磁盘路径派生，4 scope 归一化）/ `behavior_version_key_for`（:266，从 file_id 派生）——记版本时用（F136 用的是哪个 F111 照抄）。

### 1.3 ★ LLM 发现端确定性组件 —— 仿写 F127（非复用，结构同构数据面不同）

- **文件**：`apps/gateway/src/octoagent/gateway/services/consolidation_discovery.py::ConsolidationDiscoveryService`（716 行，F127 Phase C）。
- **可仿结构**：
  - `ConsolidationLLMClient` Protocol（llm_client 注入式，:94-108，测试 stub / production `ProviderRouterMessageAdapter`）——**F111 照抄这个注入范式**（发现端确定性可单测）。
  - `_extract_llm_text`（三路兜底取 LLM 文本，:380-396）——**直接可复用**。
  - `_build_discovery_prompt`（C9 让 LLM 判冗余不给阈值规则，:326-378）——**F111 改写 prompt**（对象从"事实列表"换成"单个 behavior 文件全文"）。沿用其"宁缺毋滥，合并是破坏性操作要谨慎"句（:362，实测有效）。
  - `_parse_groups`（:398-452，F065 `parse_llm_json_array` + 组校验：id 白名单 + `MIN_GROUP_SOURCE_COUNT` + 非空）——**F111 输出契约不同**（§8：整文件全文非 JSON 数组），解析逻辑要重写（推荐分隔符切分而非 JSON）。
  - fallback（`_identify_merge_groups` :289-324，LLM None/异常/空/解析失败 → 空 + used_fallback）——**F111 照抄语义**（fallback 0 提议不崩）。
- **★ 关键差异（F111 vs F127 发现端）**：
  - F127 合并单元=SOR 记录（N→1），有 `source_ids ⊆ 窗口` 确定性白名单挡幻觉。
  - F111 合并单元=整文件文本重写，**无 source_ids 白名单护栏**——护栏换成 H1 变小 / H2 PROTECTED 保留 / H3 结构（spec §0.1.3）。
  - F127 输出 JSON `{"groups": [...]}`；F111 输出精简后 markdown 全文（spec §8 契约 A，分隔符包裹，JSON 包全文笨重易截断）。

### 1.4 破坏性候选人审服务（若③选候选表才仿；推荐③用 F136 gate 则不用）

- **文件**：`apps/gateway/src/octoagent/gateway/services/consolidation_approval.py::ConsolidationApprovalService`（501 行，F127 Phase D）。
- **可仿件（仅当③翻转为候选表）**：atomic claim（`claim_candidate_for_apply` PENDING→APPLYING CAS）+ 失败语义二分（判定失败=确定性→CONFLICT 终态；自身异常=临时故障→回滚 PENDING）+ commit 前验证。
- **推荐③用 F136 gate 则本节不适用**（behavior 一次一文件走 gate 阻塞，无 SOR 候选表的 atomic-claim/CONFLICT 需求，spec DP-3）。

### 1.5 后台 cron 编排（若②选 cron 才仿；推荐②纯手动则不用）

- **文件**：`apps/gateway/src/octoagent/gateway/services/memory_consolidation.py::MemoryConsolidationService`（867 行，F127 Phase B）。
- **可仿件（仅当②翻转为 cron）**：cron 注册（`_register_cron` :253）+ 合成 root Task+Work（`_ensure_consolidation_root` :406，返回 `(TaskModel, Work)` 对）+ spawn_child + 单飞 bool + capacity skip + 系统占位泄漏防御（`SYSTEM_INTERNAL_WORK_IDS`）。
- **实测确认 F127 无手动触发入口**（只 cron）——F111 若②纯手动需自建触发（CLI/工具直调发现端，比 cron 简单）。
- **推荐②纯手动则本节不适用**（省整个 cron + spawn + 单飞 + 占位泄漏防御链）。

### 1.6 config 解析（若需 USER.md 配置项才仿）

- **文件**：`apps/gateway/src/octoagent/gateway/services/consolidation_config.py`（F127）/ `daily_routine_config.py`（F102）。
- **可仿件**：key 左边界锚定正则（防 `previous_xxx_active` 误匹配）+ HTML 注释行/块剥离 + 非法值 fallback + WARNING + USER.md 1800 字符预算（memory `project_user_md_template_budget`）。
- **F111 用途**：若需配置 `compact_excluded_files` 解禁 SOUL 等（DP-4 用户翻转）或 cron 时间（②cron）。v0.1 推荐窄路径下禁区用代码常量，无需 USER.md config。

### 1.7 G-lite 真 LLM 验证脚手架 —— 仿写

- **文件**：`.specify/features/127-sleep-time-consolidation/glite/run_glite.py`（F127 交付，可复跑）。
- **可仿**：临时隔离 SQLite（每轮新库不碰 ~/.octoagent）+ 只借实例 provider 配置（bench alias → DeepSeek-V3.2）+ alias 重定向 wrapper + 硬断言（管道通 + 质量下限）/ 质量观察（不作断言）二分 + n≥3 + 原始响应录制。
- **F111 植入物**：含若干条语义重复行为规则的 AGENTS.md 变体 → 断言发现端产出更小 + PROTECTED 保留 + 非 fallback + 无幻觉规则。

---

## 2. F111 底座已有件盘点（勿重复造，含证据）

| 底座 | 文件 | 状态 |
|------|------|------|
| behavior 文件测量 | `packages/core/src/octoagent/core/behavior_workspace/skeleton.py::measure_behavior_total_size`（:44）+ `_types.py::_BEHAVIOR_SIZE_WARNING_THRESHOLD=15000`（:64）| ✅ F063 P3 遗留，**仅测量无消费者**（`_BEHAVIOR_SIZE_WARNING_THRESHOLD` grep 无 production 消费者，只测试引用）——size-warning 注入 + compactor 从未做 |
| behavior 写核 prepare/commit | `packages/core/src/octoagent/core/behavior_workspace/write.py::prepare_behavior_file_write`(:34) / `commit_behavior_file_write`(:60)| ✅ F108a 两段式收口，审批门放两段之间 |
| 单文件字符预算对账 | `packages/core/src/octoagent/core/behavior_workspace/budget.py::check_behavior_file_budget`(:98) + `BEHAVIOR_FILE_BUDGETS`| ✅ H1"合并后变小"判定用 |
| behavior 文件路径解析 | `paths.py::resolve_write_path_by_file_id`(:166)（4 scope 路由，未知 file_id ValueError）| ✅ 落盘路径 + 禁区漏网防御同位 |
| 9 个 behavior 文件 review 模式 | `template.py::get_behavior_file_review_modes`(:144)——全 9 文件 REVIEW_REQUIRED | ✅ 确认 compact 落盘必过审批（无 NONE auto-apply 文件）|
| LLM JSON 解析（若契约 B）| `apps/gateway/.../inference/llm_common.py::parse_llm_json_array`（F065）| ✅ code fence + 正则兜底 |

---

## 3. F111 真缺口（要新建，spec §11 已列，此处补实施定位）

| 缺口 | 新建位置（建议）| 说明 |
|------|------|------|
| 发现端本体 | `apps/gateway/src/octoagent/gateway/services/behavior_compactor_discovery.py` | 仿 consolidation_discovery.py 结构 |
| PROTECTED 提取 + 插回 | `packages/core/src/octoagent/core/behavior_workspace/`（新 helper 或补 template.py）| F063 P3 `plan.md:212-219` 设计过从未实现；`<!-- 🔒 PROTECTED -->...<!-- /🔒 PROTECTED -->` |
| 禁区常量 | `packages/core/src/octoagent/core/behavior_workspace/_types.py`（邻接 `*_BEHAVIOR_FILE_IDS`）| `COMPACT_EXCLUDED_FILE_IDS = {SOUL, IDENTITY, BOOTSTRAP, HEARTBEAT}` |
| `behavior.compact` LLM 工具 | `apps/gateway/.../builtin_tools/misc_tools.py`（邻接 behavior_write_file）或新文件 | 仿 behavior_write_file 注册 + 调发现端 + F136 gate |
| `octo behavior compact` CLI | `packages/provider/src/octoagent/provider/dx/behavior_commands.py`（新 `compact` 子命令）| DP-2 选项 a：diff 预览 + --apply（CLI 全本地，LLM 接线需实测）|
| `BEHAVIOR_COMPACT_*` 事件 | `packages/core/src/octoagent/core/models/enums.py`（EventType，behavior 事件邻接）| 5 个 + payload schema |
| 输出契约 | 发现端内（§8 契约 A 分隔符包裹全文）| **F111 特有**，与 F127 JSON 数组不同 |

---

## 4. 实测确认的关键接线不确定性（实施期需先验证）

1. **DP-2 选项 a：`octo behavior compact` CLI 的 LLM 接线**——实测 `behavior_commands.py` 全本地（`_resolve_project_root` 本地读文件，不走 gateway）。但 compact 需 LLM。**实施期先验证 CLI 能否独立起 `provider_router.complete()`**（凭证/alias 装配是否可用于 CLI 进程）；不行退 control_plane HTTP（需 gateway 运行 + 新 endpoint）。推荐先试本地最简。
2. **PROTECTED 插回锚点策略**（spec §7 edge case）——LLM 精简时 PROTECTED 区段可能被挪位置。推荐"提取时记录相对位置序 + 合并后按文档结构顺序插回 + 字节级对账"，对账失败 → 丢弃提议（不落盘）。Phase A 定死。
3. **输出契约 A 分隔符 robust 性**——LLM 可能忘输出分隔符或输出多余解释。缺分隔符 → fallback（保守不产候选）。Phase B 关键。

---

## 4b. 收窄期实测补遗（2026-07-15，rebase f2081010 后）

> 设计稿基线 1e64ecd3 → f2081010（133 commits：M8 后半 + M9 十 Feature）。§1/§2 资产清单逐条复核**全部仍成立**，另有新增可用件与新证据：

1. **岔路③判定证据（决定性）**：`write_approval.py` 实读——`BEHAVIOR_WRITE_APPROVAL_TIMEOUT_SECONDS=300.0`(:39) + `wait_for_decision(timeout_seconds=300)`(:416) 阻塞模型；超时分支**刻意不恢复 RUNNING**(:431-434，F101 HIGH-02 v3)；审批通知 `NotificationPriority.CRITICAL`(:403) 豁免 quiet hours。三者对 nightly 无人值守批量提议全是硬冲突 → **③=候选表**（spec §0.3 全文归档）。
2. **F127 编排细节实读**（`memory_consolidation.py`）：发现端**内联**在 cron 回调（`_run_discovery`，spawn 后跑），spawn 的 subagent 是 H2 审计容器（占位 objective）；单飞 = 进程内 bool + `_has_active_consolidation_child` 持久态双层；root 事件全挂 root task `task_seq=0` 经 `append_event_committed`；`_notify_pending_review` 用 `session_id=""` 全局桶 + `state_transition_event_id=run_id` 幂等。F111 逐项照抄。
3. **REST Two-Phase 落盘先例**：`worker_service._handle_behavior_restore_version`(:645)——用户 REST 动作直接 `prepare/commit_behavior_file_write` + `record_behavior_version(source="restore")` + `invalidate_behavior_pack_cache`，**不过 F136 gate**（actor 是人）。F111 accept 落盘序列逐字对照此先例。注意它对超预算内容 raise `BUDGET_EXCEEDED`——F111 accept 不设此闸（spec DP-8 归档偏离：盘上文件可超预算，compact 正是修它的工具，H1 已保变小）。
4. **M9 新件**（设计稿时不存在）：F138 `ScriptedModelClient`（skills/testing，`generate(manifest=...)` SkillRunner 协议——与发现端 `complete(messages=...)` 协议**不同**，故 AC-11 的脚本缝在编排服务 `llm_client` 公开注入缝，spec §6 AC-11 注归档）；F144 `test_e2e_scripted_write_approval.py`（真 harness+REST 全链范式，AC-11 直接仿）；`tests/AGENTS.md` 四层判定表（新测试按它归层）；CI changed-lines 90% 门。
5. **CLI→gateway HTTP 范式**：`attest_commands.py` 实测有完整先例（实例 env 解析 mode/token 变量名/port + `httpx.Client(timeout=10, follow_redirects=False)` + `_scrub` 脱敏防异常回显 token）。Phase E 照抄。
6. **系统占位泄漏面现状**：`SYSTEM_INTERNAL_WORK_IDS`（`control_plane/_base.py:48` frozenset 字面量 + `expand_internal_work_ids` BFS 后代排除）；task_runner:1009/1276 + orchestrator:484 按 `channel=="system"` 通用抑制（F127 finding-E）——F111 root Task 用 channel="system" 即自动被覆盖，无需新增抑制点；`test_f127_consolidation_trigger.py::TestSystemWorkExclusion` guard 测试范式照抄。
7. **core 候选表落点**：behavior 域表进 `core/store/sqlite_init.py`（`_BEHAVIOR_VERSIONS_DDL`:493 邻接，DDL+INDEXES 常量对注册）；与 memory 域 `consolidation_candidates`（memory/store/sqlite_init.py:243）分居各自包——F111 新表进 core 侧。

## 5. Constitution / H1 合规检查（设计阶段自查）

- **C4 Side-effect Two-Phase** ✅：发现端只产提议（不 commit）；落盘唯一入口 = 人审（LLM 工具走 F136 gate 服务端证据 / CLI 走用户本人 --apply）。AC-7 静态证明无自主 commit。
- **C7 User-in-Control** ✅：每个提议用户 accept/reject；F107 版本历史兜底可回退。
- **C9 Agent Autonomy** ✅：LLM 判冗余不写规则引擎（AC-2 grep 断言）；确定性层只做护栏（变小/PROTECTED/结构），不判"什么是冗余"。size-warning 只提示不自动 compact。
- **C6 Degrade Gracefully** ✅：LLM 不可用 → fallback 0 提议；审批基础设施缺失 → fail-closed 不落盘（F136 gate 继承）。
- **C2 Everything-is-an-Event** ✅：`BEHAVIOR_COMPACT_*` 5 事件（PII 防护 diff/hash 引用）。
- **C5 Least Privilege** ✅：behavior 文件不含 secrets（既有约束）；事件体不含原文全文。
- **H1 管家 mediated** ✅：手动触发是用户主动；LLM 工具路径主 Agent 仍 user-facing speaker（建议压缩对话内自然发生，落盘依据是审批卡片）；若②cron 则后台 subagent 不抢话。
- **DP-4 禁区**：SOUL/IDENTITY（人格自我漂移）/ BOOTSTRAP（H1 违反）/ HEARTBEAT（收益低）默认排除——避免 LLM 重写 Agent 自我认知。
