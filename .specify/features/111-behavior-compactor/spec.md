# Feature Specification: F111 Behavior Compactor（行为规则 LLM 智能合并去冗余）

**Feature ID**: F111
**Feature Branch**: `feature/111-behavior-compactor`
**Created**: 2026-07-06
**Status**: **Draft v0.1（设计先行 / 未实施）**——研究 + spec + plan 草案已产出，5 个设计岔路回用户拍板（§0.3 / §9），拍板后再走 spec-driver implement。
**M7 阶段**: M7 认知深化（F127 睡眠时记忆巩固之后的同域下一棒；F063 Phase 3 推迟项）
**Baseline**: master `1e64ecd3`（含 F127 全套 + F135/F136 + F107 behavior 版本历史）
**Upstream**（实测核实，master `1e64ecd3`）:
- **F127 Sleep-Time Memory Consolidation**（旗舰，同域前身）：
  - `consolidation_discovery.py`（发现端确定性组件，llm_client 注入式）—— **F111 发现端直接仿**
  - `consolidation_approval.py`（Phase D 破坏性 accept/reject 人审，atomic claim + CONFLICT 终态）—— **F111 若走候选表则仿**
  - `memory_consolidation.py`（cron 触发 + 合成 root Task+Work 派后台 subagent）—— **F111 若走定时后台则仿**
  - `consolidation_store.py` + `consolidation_candidates` 表（五态 + 阻断白名单幂等账本）
  - `routes/consolidation_candidates.py`（REST GET/accept/reject/bulk_reject + 409 契约）
- **F136 behavior.write_file 服务端审批**（`builtin_tools/write_approval.py::gate_behavior_write`）—— **★ F111 破坏性写入人审直接复用**：request_approval + ApprovalManager 双注册 + WAITING_APPROVAL + notify CRITICAL + wait_for_decision + 条件恢复 RUNNING + unified diff 拼进 risk_explanation 的全套生产范式。
- **F107 文件工作台 v0.2 W1**（`store/behavior_version_store.py::SqliteBehaviorVersionStore` + `models/behavior_version.py`）—— **★ F111 可回滚兜底直接复用**：`record_version`（append-only + 首版 baseline）+ `get_version_content`（恢复读旧版）；behavior 文件恒 inline。
- **F063 Phase 3 已落地原语**（`behavior_workspace/skeleton.py::measure_behavior_total_size` + `_types.py::_BEHAVIOR_SIZE_WARNING_THRESHOLD=15000`）—— **F111 直接复用测量原语**（Phase 3 只落了测量，警告注入 + compactor 本体 + CLI 从未做）。
- **behavior 写核**（`behavior_workspace/write.py::prepare_behavior_file_write / commit_behavior_file_write`，F108a 收口的 prepare/commit 两段式）—— **F111 落盘直接复用**（两段之间放审批门，与 F136 同位）。
- **F065 LLM JSON 解析**（`inference/llm_common.py::parse_llm_json_array`，code fence 剥离 + 正则兜底）。
- **F102 DailyRoutineService**（cron 范式 + USER.md config 解析 + 时区降级链 + token budget + fallback）。

**Feature 性质**: 行为规则文件维护——**回顾单个 behavior 文件 → LLM 识别冗余/矛盾规则 → 产合并提议 → 破坏性写入过人审（Two-Phase）→ 落盘 + F107 版本历史兜底**。**不改 Agent 协作模型（H1/H2/H3）**：若走定时后台，走后台 subagent，主 Agent 仍是唯一 user-facing speaker（H1 守界）；**绝不 agent 自主 commit** 改行为文件。

---

## 0. 设计基础说明（实测核实，master 1e64ecd3）

### 0.1 ★ 核心定位：F111 是「发现端 + 触发 + 审批治理」，绝大部分底座已由 F127/F136/F107 建好

沿用 F127 §0.1 的"编排 vs 造原语"框架。**实测发现 F111 的所有难点底座都已存在**——F127 刚把"LLM 提议改用户既有数据 → 人审 → apply"整条路走通，F136 刚把"behavior 文件写入过服务端 ApprovalGate"整条路走通，F107 刚把"behavior 文件版本历史 + 可回滚"整条路走通。F111 = **把这三条已验证的路，用一个新的发现端（行为规则去冗余 LLM）串起来**，而非造任何原语。

**F111 与 F127 的关系（一句话）**：同族不同源——F127 操作对象是 memory SOR（自带 SUPERSEDED 软删可回滚），F111 操作对象是 behavior 文件（可回滚兜底挂 F107 版本历史）。发现端"LLM 判冗余 → validate → 候选/提议 → 人审 → apply"的骨架同构；差异点全在数据面。

### 0.1.1 实测核实的可复用资产清单（勿重复造）

| 能力 | 现状 | 文件（master 1e64ecd3）| F111 复用方式 |
|------|------|------|------|
| behavior 文件写入服务端审批门 | ✅ `gate_behavior_write`（request_approval + 双注册 + WAITING_APPROVAL + notify + wait + 条件恢复 + diff） | `builtin_tools/write_approval.py` | **★ 直接复用**：F111 合并提议落盘走同一门（改内容源为"合并后全文"即可）|
| behavior 写核 prepare/commit | ✅ 路径解析 + 预算检查 / mkdir + write_text 两段 | `behavior_workspace/write.py` | 落盘复用（审批门放两段之间，与 F136 同位）|
| behavior 版本历史 + 可回滚 | ✅ `record_version`（append-only + 首版 baseline）/ `get_version_content` | `store/behavior_version_store.py` | **★ 可回滚兜底**：合并落盘后记版本，用户不满意可 F107 恢复流回退 |
| behavior 文件版本 key 派生 | ✅ `behavior_version_key_from_path` / `behavior_version_key_for`（4 scope 归一化）| `behavior_workspace/paths.py` | 记版本时用 |
| 行为文件总大小/单文件测量 | ✅ `measure_behavior_total_size` + 阈值常量 | `behavior_workspace/skeleton.py` / `_types.py` | 手动触发展示 + 阈值提示（F063 P3 遗留原语）|
| 单文件字符预算 | ✅ `check_behavior_file_budget` / `BEHAVIOR_FILE_BUDGETS` | `behavior_workspace/budget.py` | "合并后是否变小"判定 + 审批卡片展示 |
| LLM 发现端确定性组件范式 | ✅ `ConsolidationDiscoveryService`（llm_client 注入 + prompt + 解析兜底 + 组校验 + validate-no-commit）| `services/consolidation_discovery.py` | **★ 发现端直接仿**（换窗口源为 behavior 文件文本、换提议为"精简后全文"）|
| LLM JSON 解析 | ✅ `parse_llm_json_array`（fence 剥离 + 正则兜底）| `inference/llm_common.py` | 复用 |
| 破坏性候选人审（若走候选表）| ✅ `ConsolidationApprovalService`（atomic claim + CONFLICT 终态 + 失败语义二分）| `services/consolidation_approval.py` | 参考（**但见 §0.1.2 DP-3 权衡：behavior 走 F136 gate 可能比造候选表更轻**）|
| cron 触发 + 合成 root spawn | ✅ `MemoryConsolidationService`（cron + ensure root Task+Work + spawn_child + 单飞 + capacity skip）| `services/memory_consolidation.py` | 若走定时后台则仿 |
| USER.md config 解析 | ✅ `ConsolidationConfig` / `DailyRoutineConfig`（左边界锚定正则 + 注释剥离 + fallback）| `services/consolidation_config.py` / `daily_routine_config.py` | config 复用范式 |
| 通知 | ✅ NotificationService 四级 + quiet hours discard | `services/notification.py` | 提议就绪通知 |

### 0.1.2 ★ 关键设计张力：behavior 是「整文件重写」，不是「多条记录合并」

F127 的合并单元是 **SOR 记录**（`WriteAction.MERGE` 把 N 条源事实标 SUPERSEDED、产 1 条新权威事实）——粒度是"记录级"，源软删可回滚是 SOR 层天然属性。

F111 的合并单元是 **整个 behavior 文件的文本**（LLM 读全文 → 输出精简后全文 → 覆写落盘）——粒度是"文件级全量重写"。这带来三个 F127 没有的问题：

1. **可回滚兜底不同**：SOR 软删是记录级；behavior 是文件级覆写 → 兜底必须挂 **F107 版本历史**（`record_version` 记旧版 + 新版，用户不满意走 F107 恢复流回退整个文件）。**实测确认 F107 版本历史对全部 9 个 behavior 文件真覆盖**（F136 FR-9 已验证 approved 落盘后 record_version；deny-list 不排 behavior——F107 W2 的 deny-list 排的是 workspace git 里的 secrets/behavior，behavior 版本历史是 W1 独立表，不受影响）。
2. **"合并质量"更难自动校验**：F127 有 `write_service` validate（proposal 结构校验）+ source_ids ⊆ 窗口（确定性白名单挡幻觉）。F111 是自由文本重写——LLM 可能**丢失规则、篡改语义、引入新规则**，没有"source_ids 白名单"这种确定性护栏。**这是 F111 比 F127 更需要人审 + 强 model 验证的根本原因**（§0.1.3 缓解）。
3. **审批载体差异（DP-3 核心权衡）**：F127 造了 `consolidation_candidates` 表（因为 SOR 合并要走 `write_service` MERGE commit，需要持久候选 + atomic claim 防双 commit + CONFLICT 新鲜度验证）。**F111 走 F136 的 `gate_behavior_write` 可能更轻**——behavior 写入本就是"一次一文件、内容在服务端闭包持有、批准后 LLM 无法换内容"（F136 DP-6 无 TOCTOU 面），审批卡片直接展示 unified diff，不需要持久候选表。**但代价**：手动触发 `octo behavior compact` 若一次扫多文件产多提议，gate 阻塞模型是"一个一个等审批"（串行），不像候选表能"批量提议、用户异步逐个 accept"。→ **这是 DP-3 决策点**。

### 0.1.3 ★ 合并质量护栏（针对 §0.1.2 问题 2，F111 特有）

behavior 是自由文本重写，无 SOR 的确定性白名单护栏，故 F111 必须叠加多层护栏（发现端确定性层能做的先做，LLM 判断力留强 model 验证）：

- **H1 合并后必须更小**（沿用 F063 Phase 3 + OpenClaw 经验 `plan.md:233`"合并后更大 → 跳过"）：`check_behavior_file_budget` 对账，合并后字符数 ≥ 原文 → 丢弃提议不弹审批（去冗余的定义就是变小；变大说明 LLM 没在去冗余而在扩写）。
- **H2 保护标记原样保留**（F063 Phase 3 设计 `plan.md:212-219`）：`<!-- 🔒 PROTECTED -->...<!-- /🔒 PROTECTED -->` 之间的内容提取 → 合并后原样插回 → 校验插回后 PROTECTED 区段字节级不变。防 LLM 篡改用户显式标记为不可动的核心规则。
- **H3 结构完整性弱校验**（确定性）：合并后仍是合法 Markdown（至少非空 + 保留原有顶层标题集合的子集/超集判断）；空输出/纯空白 → fallback 丢弃。
- **H4 破坏性人审 + diff 可视（C4/C7）**：最终护栏——用户在审批卡片看 unified diff（增删了哪些规则行），亲自判断"合并有没有丢东西"。这是 §0.1.2 问题 2 无法自动化的部分兜到人。
- **H5 强 model 验证**（§9 决策④）：DeepSeek 照不出"合并是否保留语义"（同 F127 G-lite 结论）——合并质量归 M7 统一强 model OctoBench 方案，植入若干条语义重复的行为规则小变体，断言合并后规则集语义等价 + 变小 + PROTECTED 保留。

### 0.1.4 ★ 禁区文件单一事实源（针对"哪些 behavior 文件不可自动合并"）

沿用 F127 `SENSITIVE_PARTITIONS` 三层防御范式。behavior 对应物 = **哪些 behavior 文件默认排除出 compact 范围**。

实测 9 个 behavior 文件（`template.py`）全部 `REVIEW_REQUIRED`。但 compact 的破坏性不均：
- **SOUL.md / IDENTITY.md**（AGENT_PRIVATE，Agent 人格/身份核心）：LLM 重写自己的人格文件有"自我漂移"风险——建议 **v0.1 默认排除**（禁区单一事实源常量 `COMPACT_EXCLUDED_FILE_IDS`）。
- **BOOTSTRAP.md**（引导脚本，H1 主 Agent 用户首次见面）：一次性引导脚本，compact 无意义且违 H1（F095 已确认 BOOTSTRAP 是主 Agent 用户见面脚本）——**默认排除**。
- **HEARTBEAT.md**（运行节奏）：短、结构化，冗余空间小——**建议排除**（收益低风险不值）。
- **AGENTS.md / TOOLS.md / USER.md / PROJECT.md / KNOWLEDGE.md**（规则/偏好/项目语境类）：这些是"用久了积累冗余规则"的真正目标（用户 prompt 明示 AGENTS.md/TOOLS.md/instructions）——**v0.1 compact 范围**。

→ **禁区决策见 DP-4**。防御多层：发现端范围排除（根治）+ 落盘前 file_id 校验（防漏网，与 F136 `resolve_write_path_by_file_id` 未知 file_id ValueError 同位）。

### 0.2 竞品反向验证结论（剔除幻觉）

沿用 F063 research（`_references/opensource/`）+ F127 已验证结论：

- **Agent Zero behaviour merge**（F063 Phase 3 原参考 `plan.md:196/228`）：Agent Zero 的 `behaviour_adjustment` 是"LLM 自改写行为规则"——**违反 OctoAgent C4/C7**（`CLAUDE.local.md` M6 竞品结论已明确"主动剔除 Agent Zero behaviour_adjustment 自改写规则，违 Constitution #4/#7"）。F111 **不照搬其自动改写**，只借"LLM 智能合并去冗余"的思路，且强制人审。
- **OpenClaw**（F063 参考"合并后更大则跳过"经验 `plan.md:233`）：可借鉴"合并后变大跳过"这一确定性护栏（→ H1）。
- **memU / Hermes**（F127 §0.2 已验证）：memU `dedupe_merge` 空壳；Hermes 的 "consolidation" 是 skill 库合并非 memory/behavior。→ 借鉴有限。
- **结论**：behavior 文件的"LLM 智能合并去冗余 + 强制人审 + 版本历史兜底"在可参考竞品里是空白（Agent Zero 有合并但无人审、无版本历史）——OctoAgent 因 F107 版本历史 + F136 审批门而处在更好起点。F111 价值 = 把已有底座用新发现端串起来。

### 0.3 ★ 5 个范围决策（回用户拍板，§9 详述；本 spec 按推荐窄路径写，用户可翻转）

| # | 决策 | **推荐值（v0.1）** | 备选 |
|---|------|------|------|
| ① 范围收窄 | 单文件内去冗余 vs 含跨文件矛盾检测 | **单文件内去冗余**（仿 F127 收窄先例）| 含跨文件矛盾检测（+1-2 Phase，规模升）|
| ② 触发机制 | 纯手动 `octo behavior compact` vs 接 cron 定时 | **纯手动**（compact 是用户主动维护动作，不像记忆巩固天然后台）| 手动 + cron（+cron 编排 Phase，与 F127 共用）|
| ③ 与 F127 关系 | 独立发现端 + 复用 F136 gate vs 共用 F127 候选表/审批管道 | **独立发现端 + 复用 F136 `gate_behavior_write`**（behavior 一次一文件，gate 比候选表轻）| 共用候选表（批量异步审批，但造 behavior 候选表概念错配 memory 候选）|
| ④ 验证 | 强 model OctoBench vs 仅确定性单测 | **强 model OctoBench（归 M7 统一方案）+ 确定性单测打底**（同 F127 G-lite 模式）| 仅确定性单测（**不推荐**——合并质量是 LLM 判断，DeepSeek 照不出）|
| ⑤ H1 守界 | 手动前台 vs 后台 subagent | **手动前台直调发现端**（用户主动触发，非后台）；若选②cron 则后台 subagent | — |

> **决策耦合**：①②③④⑤ 相互关联。**推荐组合 = ①单文件 + ②纯手动 + ③F136 gate + ④强 model + ⑤前台**——这是最窄、最快、复用最大化的 v0.1，把 F111 从"XL 编排"收成"M 发现端 + gate 接线"。若用户选 ②cron + ③候选表，规模升 L 且与 F127 编排大量重叠（可共用 `memory_consolidation.py` 范式）。

---

## 1. 目标（Why）

- **1.1 行为文件越用越精**：用久了 AGENTS.md/TOOLS.md 等积累重复/矛盾规则（"禁止 X" 与后来加的 "允许 X 当 Y"），compact 让 LLM 智能合并去冗余，规则集更小更一致。
- **1.2 降 token 成本**：behavior 文件每轮注入 LLM 上下文（受 `BEHAVIOR_FILE_BUDGETS` 约束但仍占预算）——去冗余直接降注入体积。这是 `CLAUDE.local.md` F111 定位"token 成本下降后做"的正解。
- **1.3 破坏性可控（C4 + C7）**：改用户的行为规则是不可逆动作（即便可回滚），**必须 Plan→Gate→Execute**——不静默改行为文件；F107 版本历史保证审批外仍可回退。
- **1.4 全程可观测（C2 + C8）**：每次 compact 运行 + 每个合并提议 + 用户决策都写审计事件。
- **1.5 不抢主 Agent 的话（H1）**：若走 cron 后台则走后台 subagent；手动触发是用户主动动作，主 Agent 不主动发起 compact 对话。
- **1.6 绝不 agent 自主 commit 改行为文件**：这是 F111 的红线——发现端只产提议，落盘唯一入口是用户经审批卡片批准（复用 F136 gate 服务端证据，非 LLM 自证）。

---

## 2. 范围声明

### 2.1 In Scope（v0.1 推荐窄路径）

- **发现端**（新建 `BehaviorCompactorDiscovery` 确定性组件，仿 `ConsolidationDiscoveryService`）：读单个 behavior 文件全文 → LLM 识别冗余/矛盾规则并产精简后全文 → 确定性护栏（H1 变小 / H2 PROTECTED 保留 / H3 结构 / 幂等）→ 产合并提议。
- **破坏性写入人审**（复用 F136 `gate_behavior_write`）：提议 → 审批卡片（unified diff）→ 用户批准 → 落盘 + F107 版本历史。
- **手动触发**：`octo behavior compact` CLI + 可能的 `behavior.compact` LLM 工具（agent 主动建议压缩，但触发审批同 F136）。
- **禁区单一事实源**（`COMPACT_EXCLUDED_FILE_IDS`：SOUL/IDENTITY/BOOTSTRAP/HEARTBEAT）+ 多层防御。
- **测量复用**（`measure_behavior_total_size` + 阈值提示：可选把 F063 Phase 3 遗留的 size-warning 注入接上——超阈值提示用户 compact）。
- **事件**：新增 `BEHAVIOR_COMPACT_{TRIGGERED,PROPOSED,APPLIED,REJECTED,SKIPPED}`（PII 防护：diff/hash 引用，事件体不含 behavior 原文全文）。
- **验证**：确定性单测 + G-lite 式真 DeepSeek 发现端跑通 + 强 model 质量评估方案设计（归 M7 OctoBench）。

### 2.2 Out of Scope（显式排除，带理由 → v0.2 或独立 Feature）

- **跨文件矛盾检测**（①备选）：v0.1 只单文件内。跨文件（如 AGENTS.md 的 "禁止删库" vs TOOLS.md 的 "允许 db.drop"）需读多文件全文 + 更复杂的冲突建模 → v0.2。
- **cron 定时后台 compact**（②备选）：v0.1 纯手动。若做，与 F127 `memory_consolidation.py` 编排大量重叠，共用范式 → v0.2 或与 F127 编排层合并设计。
- **SOUL/IDENTITY 人格文件 compact**：自我漂移风险 → 默认禁区。用户可显式解禁（配置项），但 v0.1 不默认开。
- **behavior 候选表 + 批量异步审批**（③备选）：v0.1 走 F136 gate 串行审批。若手动一次扫多文件产多提议的体验不佳，v0.2 评估造 behavior 候选表（或复用 F127 候选表加 behavior 维度——需评估概念错配）。
- **F127 侧未闭合的前端候选审批 UI / SSE 隐藏任务订阅**（F127 handoff §3）：若 F111 也出候选，UI 需求可与 F127 合并设计一次做 → 前端 follow-up。
- **behavior workspace git 集成**（F107 W2 workspace git）：behavior 版本历史是 F107 W1 独立表，compact 落盘走 W1 record_version 即可，不涉 W2 workspace git（W2 deny-list 本就排 behavior）。

---

## 3. 关键决策点（GATE_DESIGN，回用户拍板）

> 本 spec 按推荐值写；用户可翻转，翻转对 Phase 影响见 plan §6 矩阵。

### DP-1 范围收窄 = 单文件内去冗余（推荐）；跨文件矛盾检测（v0.2）

**推荐单文件理由**：①仿 F127 收窄先例（旗舰都先窄路径 v0.1）；②单文件"读全文→精简全文"是自包含操作，护栏（变小/PROTECTED/结构）都在单文件闭包内可确定性校验；③跨文件矛盾检测需要"跨文件规则语义建模"——两条规则是否矛盾是比"是否冗余"更难的 LLM 判断，且矛盾的解法（保留哪条/如何调和）本身是决策，不宜 v0.1 自动化。跨文件留 v0.2 用强 model 验证过 ROI 再做。

### DP-2 触发 = 纯手动 `octo behavior compact`（推荐）；cron 定时（v0.2）

**推荐纯手动理由**：①compact 是**用户主动维护动作**（"我觉得行为文件乱了，帮我理一理"），不像记忆巩固是"后台自动越用越准"的天然被动过程；②手动触发时用户在场 → 审批卡片即时可批，无 F127 深夜 quiet-hours discard 死角；③避免 F127 式"合成 root spawn 后台编排"的重成本（若手动前台直调发现端，省掉整个 cron + spawn + 单飞 + 系统占位泄漏防御链）。**实测确认 `octo behavior` CLI 当前全本地**（不走 gateway）——但 compact 需要 LLM + 审批基础设施（在 gateway），故 `octo behavior compact` 需走 control_plane HTTP 到 gateway（与其他本地 behavior 命令不同，这是 DP-2 的接线细节，plan 展开）。

> ⚠️ 手动 CLI 触发的审批问题：CLI 是命令行、不在 Web/Telegram 会话里——`gate_behavior_write` 的审批卡片推给谁？**这是 DP-2 必须解决的接线点**（见 plan §Phase 拆分）：选项 a）CLI 触发只产 diff 预览 + 要求用户在 Web 行为工作台确认（不走 gate，走用户本人 actor 的 UI 流程，类比 F136 §2.2 control_plane behavior.write_file action "actor 是用户本人"）；选项 b）CLI 触发经 control_plane 发起真 gate 审批，用户在 Telegram/Web 批。**推荐选项 a**——CLI 用户本人就是审批人，diff 预览 + 二次确认（`octo behavior compact --apply` 两步）比绕一圈 gate 更直接，且不撞 F136"LLM 自证"问题（CLI actor 是人不是 LLM）。若同时提供 `behavior.compact` LLM 工具（agent 建议压缩），那条路才走 F136 gate（actor 是 LLM 需服务端证据）。

### DP-3 与 F127 关系 = 独立发现端 + 复用 F136 gate（推荐）；共用 F127 候选表（v0.2 评估）

**推荐独立发现端 + F136 gate 理由**：①behavior 合并是"一次一文件全量重写"，F136 gate 的"内容服务端闭包持有 + 批准后 LLM 无法换 + diff 可视"正好契合，无需持久候选表；②F127 候选表是为 SOR MERGE commit 的 atomic-claim + CONFLICT 新鲜度设计的——behavior 走 gate 阻塞模型，内容在闭包里不存在"pending 期间源过期"的 TOCTOU（F136 DP-6 已证），CONFLICT 那套用不上；③造 behavior 候选表会概念错配（F127 OQ-1 教训：memory 候选 vs SOR 提议数据流不同，复用错表概念错配）——同理 behavior 提议 ≠ memory 候选。**代价 = 手动一次扫多文件产多提议时 gate 串行审批**（DP-2 推荐选项 a 的 diff 预览 + 逐文件确认可缓解，不需要真 gate 阻塞）。→ 若②选 cron 后台，则 gate 阻塞在后台不合适，需要候选表异步审批（③翻转为候选表，规模升 L）。

### DP-4 禁区 = COMPACT_EXCLUDED_FILE_IDS = {SOUL, IDENTITY, BOOTSTRAP, HEARTBEAT}（推荐）

**理由**（§0.1.4）：SOUL/IDENTITY 人格文件自我漂移风险；BOOTSTRAP 一次性引导脚本违 H1；HEARTBEAT 短结构化收益低。v0.1 compact 范围 = {AGENTS, TOOLS, USER, PROJECT, KNOWLEDGE}（规则/偏好/语境类，用户明示的 AGENTS/TOOLS/instructions 命中）。单一事实源常量 + 多层防御（发现端范围排除根治 + 落盘前 file_id 校验防漏网）。**用户可翻转**：若想允许 SOUL compact（有些用户人格文件也会冗余），显式加配置解禁——但 v0.1 默认保守排除。

### DP-5 合并质量护栏分层（§0.1.3，Constitution #6 + #9）

- **确定性层能做的先做**（H1 变小 / H2 PROTECTED 保留 / H3 结构 / 幂等）——这些是 `check_behavior_file_budget` + PROTECTED 提取 + Markdown 弱校验，可确定性单测。
- **合并"是否保留语义"是 LLM 判断**（C9 不写规则）——留 H4 人审 diff + H5 强 model 验证。
- **降级**（C6）：LLM 不可用/空响应/解析失败 → fallback 0 提议不崩（同 F127）；审批基础设施缺失 → fail-closed 不落盘（同 F136 gate）。

---

## 4. User Scenarios（P1）

### US-1 用户主动 compact（UX 主场景）

用户（CLI 或对 agent 说）："我的 AGENTS.md 规则越写越乱有重复，帮我理一理。"
**推荐路径（DP-2 选项 a）**：`octo behavior compact AGENTS.md` → 读全文 → LLM 精简 → 护栏校验（变小 + PROTECTED 保留 + 结构 OK）→ 展示 unified diff 预览（哪些规则被合并/去重）→ 用户 `--apply` 确认（或 Web 行为工作台确认）→ 落盘 + F107 版本记录 → 报告"AGENTS.md 从 3200 字符精简到 2100 字符，合并了 4 组重复规则，PROTECTED 区段保留"。
**预期**：不满意可 `octo behavior diff AGENTS.md`（F107 已有）看版本对比 + F107 恢复流回退。

### US-2 LLM 工具路径（若提供 behavior.compact 工具）

Agent 观察到某 behavior 文件超阈值（`measure_behavior_total_size` > 15000）→ 主动建议"你的 TOOLS.md 有点大且有重复，要我压缩吗？" → 用户同意 → agent 调 `behavior.compact(file_id="TOOLS.md")` → 走 **F136 gate**（actor 是 LLM 需服务端证据）→ 审批卡片（diff）→ 用户批准 → 落盘。**与 F136 behavior.write_file 唯一差异**：内容源是"LLM 精简后全文"而非"LLM 直接写的全文"，审批门完全一致。

### US-3 合并质量不达标（护栏拦截）

LLM 精简 AGENTS.md 时误删了关键规则导致输出比原文短很多但丢了 PROTECTED 区段 → **H2 护栏拦截**（PROTECTED 区段字节级对账失败 → 丢弃提议不弹审批 + 记 SKIPPED reason=protected_violation）。或 LLM 输出比原文更大（没在去冗余）→ **H1 护栏拦截**（丢弃 + SKIPPED reason=not_smaller）。用户看到"本次未产生合并提议（LLM 输出未通过质量护栏）"，行为文件零触碰。

### US-4 禁区文件（DP-4）

用户 `octo behavior compact SOUL.md` → **禁区拦截**（SOUL 在 `COMPACT_EXCLUDED_FILE_IDS`）→ 报错"SOUL.md 是人格核心文件，默认不参与自动合并（自我漂移风险）。如需精简请在 Web 行为工作台手动编辑，或显式配置解禁。" → 零触碰。

---

## 5. FR（功能需求，v0.1 推荐路径）

- **FR-1 发现端确定性组件**：`BehaviorCompactorDiscovery`（llm_client 注入式，仿 `ConsolidationDiscoveryService`）读单文件全文 → LLM 精简 → 产提议。**不 commit**（C4：落盘唯一入口是人审）。
- **FR-2 LLM 判冗余不写规则（C9）**：prompt 让 LLM 识别语义重复/矛盾规则并精简，**不写关键词/相似度/行数阈值规则**判冗余。沿用 F127 prompt"宁缺毋滥，合并是破坏性操作要谨慎"。
- **FR-3 变小护栏（H1）**：合并后字符数必须 < 原文（`check_behavior_file_budget` 对账），否则丢弃提议。
- **FR-4 PROTECTED 保留护栏（H2）**：提取 `<!-- 🔒 PROTECTED -->...<!-- /🔒 PROTECTED -->` → 合并后原样插回 → 字节级对账；违反则丢弃。
- **FR-5 结构 + fallback（H3/C6）**：合并后非空 + 合法 Markdown 弱校验；LLM 不可用/空/解析失败 → fallback 0 提议不崩。
- **FR-6 禁区（DP-4）**：`COMPACT_EXCLUDED_FILE_IDS` 单一事实源；发现端范围排除 + 落盘前 file_id 校验双层。
- **FR-7 破坏性写入人审（C4/C7，复用 F136）**：
  - CLI 路径（DP-2 选项 a）：diff 预览 + `--apply` 二次确认（actor=用户本人）→ 落盘 + F107 版本记录。
  - LLM 工具路径（US-2）：`behavior.compact` → `gate_behavior_write`（actor=LLM 需服务端证据）→ 批准后落盘。
  - **绝不 agent 自主 commit**：无任何路径让发现端/LLM 直接落盘。
- **FR-8 F107 版本历史兜底**：落盘后 `record_version`（old_content 落盘前重读，与 F136 FR-9 同）——用户不满意可 F107 恢复流回退。
- **FR-9 事件（C2）**：`BEHAVIOR_COMPACT_{TRIGGERED,PROPOSED,APPLIED,REJECTED,SKIPPED}`（PII 防护：diff 摘要 + hash，不含 behavior 原文全文）。
- **FR-10 测量复用**：`measure_behavior_total_size` 展示 compact 前后；可选接上 F063 Phase 3 遗留的超阈值提示（`_BEHAVIOR_SIZE_WARNING_THRESHOLD` 当前无消费者）。
- **FR-11 token budget（C6）**：LLM 输入截断（behavior 文件可能大，超预算截断 + 提示）+ 输出 token 预算。

---

## 6. AC ↔ test 显式绑定（SDD 强化）

> `[@test]` 绑定：每条 P1 AC 紧邻标注 test 文件路径；verify 阶段机械校验存在 + PASS。测试文件命名 `test_f111_behavior_compactor.py`（发现端单测，core/gateway 分层）。

| AC | 内容 | test |
|----|------|------|
| AC-1 | 发现端读单文件 → LLM 精简 → 产提议（validate-no-commit，绝不落盘）| `test_f111_compactor_discovery.py::test_discover_proposes_without_write` |
| AC-2 | C9 无硬规则：grep 发现端源码无关键词/相似度/行数阈值判冗余 | `test_f111_compactor_discovery.py::test_no_hardcoded_dedup_rules`（grep 断言）|
| AC-3 | H1 变小护栏：LLM 输出 ≥ 原文 → 丢弃提议 + SKIPPED(not_smaller) | `::test_larger_output_rejected` |
| AC-4 | H2 PROTECTED 保留：PROTECTED 区段字节级对账，违反 → 丢弃 + SKIPPED(protected_violation) | `::test_protected_section_preserved` + `::test_protected_violation_rejected` |
| AC-5 | H3/C6 fallback：LLM None/空/解析失败 → 0 提议不崩（fallback=True）| `::test_llm_unavailable_fallback` |
| AC-6 | DP-4 禁区：SOUL/IDENTITY/BOOTSTRAP/HEARTBEAT 不产提议（发现端范围排除）| `::test_excluded_files_skipped` |
| AC-7 | C4 红线：无 agent 自主 commit 路径（发现端到落盘唯一经人审）——静态证明发现端不调 write.commit / gate | `::test_no_autonomous_commit_path`（grep + 调用图断言）|
| AC-8 | FR-7 LLM 工具路径复用 F136 gate：`behavior.compact` confirmed=true → request_approval → approve 才落盘 + F107 版本 | `test_f111_compact_tool_approval.py::test_compact_gated_until_approval`（真 ApprovalGate + resolver 协程，仿 F136 test 范式）|
| AC-9 | FR-8 落盘后 record_version + old_content 批准后重读 | `::test_applied_records_version` |
| AC-10 | CLI 路径 diff 预览 + --apply 两步（actor 用户本人不走 gate）| `test_f111_behavior_compact_cli.py::test_cli_preview_then_apply` |
| AC-11 | 0 regression vs master 1e64ecd3；e2e_smoke 8/8 | 全量 pytest |

---

## 7. Edge cases（已推演）

- **合并后 PROTECTED 区段被 LLM 挪位置但内容不变**：H2 只对账内容字节，不对账位置——插回策略是"原样插回原位置区间"，需定义插回锚点（推荐：PROTECTED 区段整体从 LLM 输出剥离前先记录，合并后按原文档顺序插回。plan 展开）。
- **文件无 PROTECTED 区段**：H2 空集对账（trivially pass），正常合并。
- **文件本就很小无冗余**：LLM 判定无可合并 → 0 提议正常空运行（非 fallback，同 F127 too_few_facts）。
- **CLI 触发时 gateway 未运行**（DP-2 选项 a 若走 control_plane）：CLI 报错引导启动服务（同其他需 gateway 的命令）；若 DP-2 选项 a 纯本地读文件 + 只 LLM 调用则不需 gateway（但 LLM 调用需 provider 配置 → 需实测 CLI 能否独立起 provider_router）。**这是 DP-2 选项 a 的实测点**。
- **审批等待期间 behavior 文件被并发改**（LLM 工具路径）：F136 DP-6 已处理——diff 用请求时快照，版本 baseline 批准后重读。F111 复用同语义。
- **LLM 输出裸 JSON / 混入解释文字**：`parse_llm_json_array` 兜底（同 F127）——但注意 F111 输出是"精简后全文"不是 JSON 数组，输出契约需重新设计（见 §8：可能不用 JSON，直接要 LLM 输出精简后 markdown 全文 + 单独一段 rationale，比 JSON 包裹全文更自然）。
- **合并把两条矛盾规则"调和"成一条改了语义**：H4 人审 diff 兜（用户看到 diff 判断）；确定性层无法拦（这正是 §0.1.2 问题 2）。

---

## 8. ★ 发现端输出契约设计（F111 特有，与 F127 的关键差异）

F127 发现端输出是 **JSON 数组**（`{"groups": [{"source_ids", "merged_content", ...}]}`）——因为它要把 N 条源事实映射到 M 个合并组，结构化必要。

F111 是**单文件整体精简**——输出是"这个文件精简后的完整 markdown 全文"。**JSON 包裹整个文件全文既笨重（转义）又易触发 LLM 输出截断**。推荐两种契约（plan 定）：

- **契约 A（推荐）**：LLM 直接输出精简后 markdown 全文（用明确分隔符如 `===COMPACTED===` 包裹，或要求整个 response 就是精简后文件）+ 可选尾部 `===RATIONALE===` 段说明合并了什么。确定性层用分隔符切分 → 全文过护栏 → rationale 进审批卡片。**优点**：无 JSON 转义、贴合"重写文件"本质、不易截断。**缺点**：需要 robust 分隔符解析（LLM 可能忘记分隔符 → fallback）。
- **契约 B**：JSON `{"compacted_content": "<全文>", "rationale": "...", "merged_groups": [...]}`。**优点**：结构化、可带合并组元数据供审计。**缺点**：全文 JSON 转义 + 大文件易触发输出 token 上限截断 → 半个 JSON 解析失败。

→ **推荐契约 A**（分隔符包裹全文），rationale 单独段。这是 plan Phase C 的核心设计。

---

## 9. 决策详述（回用户拍板）

### 决策 ① 范围收窄：单文件内去冗余 vs 含跨文件矛盾检测

- **推荐：单文件内去冗余**（v0.1）。理由见 DP-1。跨文件矛盾检测的"是否矛盾 + 如何调和"是比"是否冗余"高一档的 LLM 判断，且调和本身是决策不宜自动化，留 v0.2 强 model 验证 ROI。
- **翻转影响**：选跨文件 → +1-2 Phase（跨文件规则语义建模 + 冲突呈现 + 调和提议），规模 M→L，且需要更强的强 model 验证。

### 决策 ② 触发：纯手动 vs 接 cron 定时

- **推荐：纯手动 `octo behavior compact`**（v0.1）。理由见 DP-2（用户主动维护动作 + 在场即时审批 + 省 F127 式后台编排重成本）。
- **翻转影响**：选 cron → 复用 F127 `memory_consolidation.py` 编排（cron + 合成 root spawn + 单飞 + 系统占位泄漏防御 + quiet hours），规模 M→L，且撞 F127 所有后台编排的坑（handoff §2 坑 1-3 系统占位泄漏一族）。

### 决策 ③ 与 F127 巩固的关系：独立发现端 + F136 gate vs 共用候选表/审批管道

- **推荐：独立发现端 + 复用 F136 `gate_behavior_write`**（v0.1）。理由见 DP-3（behavior 一次一文件、gate 无 TOCTOU、候选表概念错配）。
- **翻转影响**：选候选表 → 造 behavior 候选表（或 F127 候选表加 behavior 维度，需评估概念错配）+ atomic claim + REST accept/reject，规模 M→L，换来"批量异步审批"（仅当②cron 或一次扫多文件时才有价值）。

### 决策 ④ 验证：强 model OctoBench vs 仅确定性单测

- **推荐：强 model OctoBench（归 M7 统一方案）+ 确定性单测打底**（同 F127 G-lite 模式）。理由：合并"是否保留语义"是 LLM 判断力，DeepSeek 照不出（同 F127 G-lite 结论）。
- **验证方案（spec 定）**：①**确定性单测**覆盖护栏（变小/PROTECTED/结构/幂等/禁区/fallback/无自主 commit）；②**G-lite 式真 DeepSeek 发现端跑通**（植入一个含若干重复规则的 AGENTS.md 变体 → 断言发现端产出更小 + PROTECTED 保留 + 非 fallback + 无幻觉规则）——验管道通 + 质量下限，不作强 model 质量断言；③**强 model 质量评估归 M7 统一 OctoBench 方案**：新增"行为合并域"task（植入语义重复的行为规则 → compact → 断言合并后规则集**语义等价** + 变小 + PROTECTED 保留），与 F127"记忆巩固域"task 一起定义（F127 handoff §3 已约定两域一起设计 benchmark task）。
- **翻转影响**：选仅确定性单测 → 无法证"合并真保留语义"（**不推荐**，AC-8 质量维度失守）。

### 决策 ⑤ H1 守界：手动前台 vs 后台 subagent

- **推荐：手动前台直调发现端**（配 ②纯手动）。用户主动触发，主 Agent 不主动发起 compact 对话；LLM 工具路径（US-2）走 F136 gate 时主 Agent 仍是 user-facing speaker（agent 建议压缩是对话内自然发生，落盘依据是审批卡片非对话）。
- **翻转影响**：选 ②cron 后台 → 必须走后台 subagent（H2 对等 + H1 守界，同 F127 §0.1.4 spawn 编排），⑤ 随 ② 联动。

---

## 10. 全局约束

- **本 spec 为草案，未实施**——5 决策拍板后再 spec-driver implement。
- **Phase 顺序可微调**（先简后难原则）；Phase 跳过须显式归档。
- **每 Phase 后 0 regression vs baseline 1e64ecd3**（≥ baseline passed），e2e_smoke 8/8。
- **PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`，禁 uv sync**（memory `project_worktree_venv_symlink`）。
- **命中"重大架构变更"**（新 LLM 工具 / 跨包 / 新事件类型）→ Codex（`codex review --base` scoped diff）+ Opus 双评审 panel，high/medium 全闭环，分歧列人裁。
- **不主动 push / commit origin**——本会话仅产 spec + plan + 研究笔记草案，停在实施前。
- **completion-report + handoff + living-docs 漂移闸**（同 F127）。

---

## 11. F111 是否部分已有（勿重复造）

| F111 需要 | 已有？ | 证据 |
|-----------|--------|------|
| behavior 文件测量 | ✅ 完整 | `skeleton.py::measure_behavior_total_size` + `_types.py::_BEHAVIOR_SIZE_WARNING_THRESHOLD`（F063 P3 遗留原语，仅测量无消费者）|
| behavior 写核（落盘）| ✅ 完整 | `write.py::prepare/commit_behavior_file_write`（F108a 两段式）|
| behavior 写入服务端审批门 | ✅ 完整 | `write_approval.py::gate_behavior_write`（F136）|
| behavior 版本历史 + 可回滚 | ✅ 完整 | `behavior_version_store.py`（F107 W1，全 9 文件覆盖）|
| behavior 版本 key 派生 | ✅ 完整 | `paths.py::behavior_version_key_from_path/for` |
| 单文件字符预算对账 | ✅ 完整 | `budget.py::check_behavior_file_budget` |
| LLM 发现端确定性组件范式 | ✅ 完整可仿 | `consolidation_discovery.py`（F127）|
| LLM JSON 解析（若用契约 B）| ✅ 完整 | `llm_common.py::parse_llm_json_array`（F065）|
| cron + 合成 root spawn（若②cron）| ✅ 完整可仿 | `memory_consolidation.py`（F127）|
| USER.md config 解析（若需配置）| ✅ 完整可仿 | `consolidation_config.py` / `daily_routine_config.py` |
| **compactor 本体（发现端逻辑）** | ❌ 真缺口 | F063 P3 从未做（只落测量原语）——F111 主体工作 |
| **PROTECTED 区段提取 + 插回** | ❌ 真缺口 | F063 P3 设计了（`plan.md:212-219`）但从未实现 |
| **compact 发现端输出契约** | ❌ 真缺口 | F111 特有（§8，与 F127 JSON 数组不同，是整文件重写）|
| **`octo behavior compact` CLI** | ❌ 真缺口 | 从未做 |
| **`behavior.compact` LLM 工具** | ❌ 真缺口 | 从未做 |
| **`BEHAVIOR_COMPACT_*` 事件** | ❌ 真缺口 | 需新增 EventType |

**结论**：F111 底座（测量/落盘/审批门/版本历史/发现端范式）**几乎全有**——真缺口只有 5 件：①发现端本体逻辑 ②PROTECTED 提取插回 ③输出契约设计 ④CLI ⑤LLM 工具 + 事件类型。这正是把 F111 从"XL 编排"收成"M 发现端 + 接线"的原因（推荐窄路径下）。
