# Implementation Plan: F111 Behavior Compactor

**Feature ID**: F111
**Status**: Draft v0.1（**设计先行 / 未实施**；Phase 拆分基于 spec.md §9 推荐窄路径——若用户对 5 决策做不同选择，相应 Phase 范围调整，见 §6）
**Spec**: [spec.md](./spec.md)
**Baseline**: `1e64ecd3`（master HEAD）
**前置依赖**: F127 / F136 / F107 / F063 Phase 3 原语 / F065 / F102 全部已合入 master（实测核实，§1）

---

## 0. 规划前提（实测结论摘要）

1. **F111 是发现端 + 接线 Feature，几乎不造底座**——测量/落盘/审批门/版本历史/发现端范式全现成（spec §11）。
2. **推荐窄路径（DP-1~5 全推荐值）下核心成本在三件**：①发现端本体（单文件读全文→LLM 精简→护栏）②PROTECTED 提取插回 ③CLI + 可选 LLM 工具接线。**不是**"合并能力"或"审批基础设施"。
3. **规划按 spec.md §9 推荐窄路径**（①单文件 + ②纯手动 + ③F136 gate + ④强 model + ⑤前台）。决策变更对 Phase 影响见 §6。

---

## 1. 前置依赖核实（master 1e64ecd3）

| 依赖 | 状态 | 关键复用点（文件） |
|------|------|------|
| F136 behavior 审批门 | ✅ master | `builtin_tools/write_approval.py::gate_behavior_write`（全套 escalate 生产范式）|
| F107 behavior 版本历史 | ✅ master | `store/behavior_version_store.py::record_version/get_version_content` + `models/behavior_version.py` |
| F107 版本 key 派生 | ✅ master | `behavior_workspace/paths.py::behavior_version_key_from_path/for` |
| F063 P3 测量原语 | ✅ master | `behavior_workspace/skeleton.py::measure_behavior_total_size` + `_types.py::_BEHAVIOR_SIZE_WARNING_THRESHOLD` |
| behavior 写核 | ✅ master | `behavior_workspace/write.py::prepare/commit_behavior_file_write` |
| 单文件预算对账 | ✅ master | `behavior_workspace/budget.py::check_behavior_file_budget` + `BEHAVIOR_FILE_BUDGETS` |
| F127 发现端范式 | ✅ master | `services/consolidation_discovery.py`（llm_client 注入 + 组校验 + fallback）|
| F065 LLM JSON 解析 | ✅ master | `inference/llm_common.py::parse_llm_json_array`（若用契约 B）|
| F136 现有 behavior.write_file 工具 | ✅ master | `builtin_tools/misc_tools.py:181-476`（LLM 工具注册 + gate 接线范式）|
| behavior CLI | ✅ master | `provider/dx/behavior_commands.py`（ls/show/edit/diff/apply，**全本地不走 gateway**）|
| F102 config 解析 | ✅ master | `services/consolidation_config.py` / `daily_routine_config.py`（若②cron 需 config）|

**结论**：所有依赖就位，F111 可启动（决策拍板后）。

---

## 2. Phase 拆分（v0.1 推荐窄路径，5 Phase + Verify）

> 顺序遵循项目"先简后难、先建 baseline 信心"惯例（F091 经验：非字母序）。
> **每 Phase 后 0 regression vs baseline 1e64ecd3 + e2e_smoke 8/8。**

### Phase A — 事件 + 禁区常量 + PROTECTED 提取插回（地基，最先，纯新增行为零变更）

- **范围**：
  - 新增 EventType `BEHAVIOR_COMPACT_{TRIGGERED,PROPOSED,APPLIED,REJECTED,SKIPPED}`（`core/models/enums.py`，插在 behavior 相关事件邻接）+ payload Pydantic schema（PII 防护：diff 摘要 + hash，事件体不含 behavior 原文全文）。
  - `COMPACT_EXCLUDED_FILE_IDS` 单一事实源常量（`behavior_workspace/_types.py`，与既有 `*_BEHAVIOR_FILE_IDS` 邻接）= {SOUL, IDENTITY, BOOTSTRAP, HEARTBEAT}（DP-4）。
  - **PROTECTED 区段提取 + 插回**（`behavior_workspace/`，新 helper 或补 `template.py`）：`extract_protected_sections(content) -> list[(anchor, text)]` + `reinsert_protected_sections(compacted, sections) -> str` + 字节级对账校验（H2）。F063 Phase 3 设计了（`plan.md:212-219`）但从未实现——本 Phase 补齐。
- **依赖**：无（纯新增）。**行为零变更**（不动任何既有读写路径）。
- **风险**：低。PROTECTED 插回锚点策略需定（spec §7 edge case：按原文档顺序插回 vs 锚点匹配）——推荐"提取时记录 PROTECTED 区段在原文的相对位置序，合并后按序插回文档对应结构位置"。
- **AC**：AC-4（PROTECTED 保留）部分 + FR-9 事件 schema。
- **`[@test]` 绑定**：`packages/core/tests/test_f111_protected_sections.py`（提取/插回/字节对账/空集）+ `packages/core/tests/test_f111_compact_events.py`（EventType + payload PII 不泄漏）。

### Phase B — 发现端确定性组件（BehaviorCompactorDiscovery，核心价值）

- **范围**：
  - 新建 `services/behavior_compactor_discovery.py`（仿 `consolidation_discovery.py` 结构：llm_client 注入式 Protocol + DiscoveryOutcome dataclass）。
  - 流程：读单文件全文 → 禁区校验（`COMPACT_EXCLUDED_FILE_IDS` 排除，FR-6）→ 提取 PROTECTED（Phase A）→ LLM 精简（prompt C9 判冗余不写规则，FR-2；token budget 截断，FR-11）→ 解析输出（§8 契约 A 分隔符包裹全文）→ 护栏（H1 变小 `check_behavior_file_budget` / H2 PROTECTED 插回对账 / H3 结构非空 markdown）→ 产提议对象（`BehaviorCompactProposal`：file_id + compacted_content + rationale + old_hash + size_before/after，**不落盘 C4**）→ emit `BEHAVIOR_COMPACT_PROPOSED`。
  - fallback（FR-5/C6）：LLM None/异常/空/解析失败/护栏不过 → 0 提议不崩（记 SKIPPED reason）。
  - 幂等（可选）：同文件同 old_hash 已有 pending 提议则跳过（若③走候选表才需；③走 gate 则提议瞬时不持久，幂等意义弱）。
- **依赖**：Phase A（事件 + 禁区 + PROTECTED）。
- **风险**：中。**LLM 提议质量 = F111 核心价值**，但确定性层（读文件/护栏/解析/fallback）可单测；LLM 层留强 model 验证（Verify）。**输出契约 A 的分隔符解析 robust 性**是本 Phase 关键（LLM 忘分隔符 → fallback）。
- **AC**：AC-1（提议不落盘）/ AC-2（无硬规则 grep）/ AC-3（变小）/ AC-4（PROTECTED）/ AC-5（fallback）/ AC-6（禁区）。
- **`[@test]` 绑定**：`apps/gateway/tests/test_f111_compactor_discovery.py`（read→propose no-write / grep 无 dedup 规则 / 变小拦截 / PROTECTED 拦截 / fallback / 禁区跳过 / 分隔符解析兜底）。

### Phase C — 破坏性写入人审接线（C4/C7 红线）

- **范围**：
  - **LLM 工具路径（US-2）**：新增 `behavior.compact` builtin 工具（`builtin_tools/misc_tools.py` 或新 `behavior_compact_tool.py`，仿 `behavior_write_file` 注册）→ 调 Phase B 发现端产提议 → 走 **F136 `gate_behavior_write`**（内容源 = compacted_content，old_content = 落盘前重读，budget_chars 从 `check_behavior_file_budget`）→ approved 才落盘（`commit_behavior_file_write`）→ `record_version`（F107，FR-8）→ emit `BEHAVIOR_COMPACT_APPLIED`；reject/timeout/unavailable 不落盘 emit `BEHAVIOR_COMPACT_REJECTED`。
  - **绝不 agent 自主 commit**（AC-7 红线）：发现端到落盘唯一经 gate；静态证明发现端不调 `commit_behavior_file_write` / 不绕 gate。
- **依赖**：Phase B（有提议）+ F136 gate（现成）。
- **风险**：中-高。**C4/C7 红线 Phase**——必须证明无 agent 自主 commit 路径（AC-7）+ F107 版本兜底（AC-9）。gate 接线机械复用 F136（低风险），红线在"发现端与落盘之间只有 gate 一条路"。
- **AC**：AC-7（无自主 commit）/ AC-8（gate 阻塞到批准）/ AC-9（record_version）。
- **`[@test]` 绑定**：`apps/gateway/tests/test_f111_compact_tool_approval.py`（真 ApprovalGate + resolver 协程 approve→落盘+版本 / reject→不落盘 / 无自主 commit 静态断言，仿 F136 `test_f136_write_approval.py` 范式）。

### Phase D — CLI 触发（octo behavior compact，DP-2 选项 a）

- **范围**：
  - `provider/dx/behavior_commands.py` 新增 `compact` 子命令：`octo behavior compact <file_id>` → 读文件 → 调发现端产提议（**DP-2 选项 a 接线点**：CLI 是否本地起 provider_router 直调 LLM，还是经 control_plane HTTP 到 gateway——**实测决定**，见 §5 风险）→ 展示 unified diff 预览 + 变化摘要（size before/after + 合并了几组）→ `--apply` 二次确认（actor=用户本人，不走 gate，类比 F136 §2.2 control_plane action "用户本人 actor"）→ 落盘 + `record_version`。
  - `octo behavior compact --list-size`（可选）：`measure_behavior_total_size` 展示各文件大小 + 超阈值标注（接上 F063 P3 遗留 `_BEHAVIOR_SIZE_WARNING_THRESHOLD`，FR-10）。
- **依赖**：Phase B（发现端）+ Phase A（护栏在发现端内）。**不依赖 Phase C**（CLI 走用户本人 actor 不走 gate，与 LLM 工具路径解耦）。
- **风险**：中。**核心不确定性 = DP-2 选项 a 的 CLI-LLM 接线**（本地 provider_router vs control_plane HTTP）。实测 `octo behavior` CLI 全本地——若 compact 也本地则需 CLI 能独立起 provider_router（需验证凭证/alias 装配）；若走 control_plane 则需 gateway 运行 + 新 control_plane endpoint。**推荐先实测本地 provider_router 可行性**（最简），不行再退 control_plane。
- **AC**：AC-10（CLI 预览 + --apply 两步）。
- **`[@test]` 绑定**：`packages/provider/tests/test_f111_behavior_compact_cli.py`（预览不落盘 / --apply 落盘 + 版本 / 禁区拒绝 / 变小摘要）。

### Phase E — 测量提示接线 + 端到端贯通（可选轻量）

- **范围**：
  - 可选：接上 F063 Phase 3 遗留的超阈值 size-warning（`_BEHAVIOR_SIZE_WARNING_THRESHOLD` 当前无消费者）——resolve behavior pack 后或 CLI 展示时超阈值提示"建议 compact"（FR-10）。**注意 C9**：只提示不自动 compact（不硬编码"超阈值就压缩"）。
  - 端到端：CLI/工具触发 → 发现端 → 护栏 → 人审 → 落盘 → 版本 全链路 event_store 可查（FR-9）。
  - e2e_smoke 集成（可选新增 e2e 域，或归 F119 式 backfill）。
- **依赖**：Phase A-D。
- **风险**：低（测量原语现成，只接线）。
- **AC**：AC-11（0 regression + e2e_smoke）+ 全链事件可查。

### Phase Verify — Final review + 强 model 验证方案 + 文档

- **范围**：
  - **Codex adversarial review（Final cross-Phase）**：输入 plan + 全 Phase diff，查偏离/漏 Phase/隐性债（强制，命中重大架构变更节点：新 LLM 工具 + 新事件类型）。
  - **多评审 panel**：强 model（Opus/另 provider）spec-对齐专项 review，分歧项列"必须人裁"（尤其 AC-7 红线证明 + §0.1.2 问题 2 合并质量兜人是否充分）。
  - **G-lite 式真 DeepSeek 发现端跑通**（§9 决策④方案②）：植入含重复规则的 AGENTS.md 变体 → 断言发现端产更小 + PROTECTED 保留 + 非 fallback + 无幻觉规则——验管道通 + 质量下限。脚手架仿 F127 `glite/run_glite.py`（临时隔离 SQLite + bench alias DeepSeek + 硬断言/质量观察二分 + n≥3）。
  - **强 model 质量评估方案设计**（归 M7 统一 OctoBench）：定义"行为合并域"OctoBench task（植入语义重复行为规则 → compact → 断言合并后规则集语义等价 + 变小 + PROTECTED 保留），与 F127"记忆巩固域"task 一起定义（F127 handoff §3 约定）。
  - completion-report + handoff + living-docs 漂移闸（Blueprint behavior 章 + `docs/codebase-architecture/harness-and-context.md` 或 `file-workbench.md` 同步）。
- **依赖**：Phase A-E。
- **AC**：全 AC 闭环 + 0 regression 最终门。

---

## 3. 依赖关系图（推荐窄路径）

```
Phase A（事件 + 禁区 + PROTECTED 提取插回）  ← 地基，最先，纯新增
   └─→ Phase B（发现端确定性组件）           ← ★ 核心价值（LLM 精简 + 护栏 + 输出契约）
          ├─→ Phase C（LLM 工具 + F136 gate 人审）  ← ★ C4/C7 红线
          └─→ Phase D（CLI 触发 + --apply）          ← 与 C 解耦（用户本人 actor 不走 gate）
                 └─→ Phase E（测量提示 + 端到端）
                        └─→ Phase Verify（review + G-lite + 强 model 方案 + 文档）
```

**串行为主**（A→B→{C‖D}→E→Verify）；C 与 D 都依赖 B 但彼此独立（gate 路径 vs CLI 路径），可并行实施。

---

## 4. 估算

| 维度 | 推荐窄路径估算 |
|------|------|
| **Phase 数** | 5 实施 Phase + 1 Verify = 6 |
| **规模** | **M**（复用最大化：发现端仿 F127 + 审批复用 F136 + 版本复用 F107 + 测量复用 F063 P3；真缺口仅发现端本体 + PROTECTED 插回 + 输出契约 + CLI + 工具 + 事件）|
| **新增文件（约）** | `behavior_compactor_discovery.py`（service）/ PROTECTED helper（core）/ `behavior.compact` 工具 / CLI compact 子命令 / 4-5 个 test 文件 |
| **改动文件（约）** | `core/models/enums.py`（EventType）/ `_types.py`（禁区常量）/ `misc_tools.py`（工具注册）/ `behavior_commands.py`（CLI）/ 可选 resolver（size-warning 接线）|
| **净增行数（粗估）** | ~800-1300 行（含测试）——比 F127 XL（~1500-2500）小，因底座全现成 |
| **风险等级** | 中（Phase B 输出契约 robust 性 + Phase C C4/C7 红线 + Phase D CLI-LLM 接线实测）|

> **规模对比 F127**：F127 是 XL（造 cron 后台编排 + 合成 root spawn + 候选表 + CONFLICT 新鲜度 + 敏感三层防御）。F111 推荐窄路径是 **M**——因为 F127/F136/F107 已把所有难底座建好，F111 只是"新发现端 + 接现成审批门 + CLI"。**这是推荐窄路径的最大价值**。

---

## 5. 关键风险与缓解（规划视角）

| 风险 | Phase | 缓解 |
|------|-------|------|
| 发现端输出契约（整文件全文）LLM 忘分隔符/截断 | B | 契约 A 分隔符包裹 + robust 解析（缺分隔符 → fallback）；token 输出预算足够容纳全文；大文件输入截断 + 提示（FR-11）|
| **合并丢规则/篡改语义**（§0.1.2 问题 2，F111 根本难点）| B/C/Verify | 确定性护栏（变小/PROTECTED/结构）兜一部分 + **H4 人审 diff 兜语义**（用户看增删）+ **H5 强 model 验证**（G-lite 管道 + M7 OctoBench 语义等价断言）|
| agent 自主 commit 绕过审批 | C | AC-7 grep + 调用图静态证明发现端不调 commit/不绕 gate + 复用 F136 服务端证据（非 LLM 自证）|
| **CLI-LLM 接线**（DP-2 选项 a 本地 provider_router vs control_plane）| D | **实施期实测**：先验 CLI 能否独立起 provider_router（凭证/alias 装配）；不行退 control_plane HTTP（gateway 运行）——推荐先试本地最简路径 |
| PROTECTED 插回锚点错位 | A | 提取时记录相对位置序 + 合并后按序插回 + 字节级对账（对账失败 → 丢弃提议，不落盘）|
| 禁区文件漏网被 compact | A/B | `COMPACT_EXCLUDED_FILE_IDS` 单一事实源 + 发现端范围排除（根治）+ 落盘前 file_id 校验（防漏，与 F136 未知 file_id ValueError 同位）|
| DeepSeek 照不出合并质量致误判 | Verify | G-lite 只验管道 + 质量下限（不作强 model 断言）；强 model 质量归 M7 OctoBench（同 F127）|

---

## 6. ★ 决策变更对 Phase 的影响矩阵

> spec.md §9 的 5 决策若用户选非推荐项，Phase 范围如下调整：

| 决策 | 推荐（本 plan 基线）| 若选他项 → Phase 影响 |
|------|------|------|
| ① 范围 | 单文件去冗余（Phase B）| 选"含跨文件矛盾检测" → **+1-2 Phase**（跨文件规则语义建模 + 冲突呈现 + 调和提议 UI），规模 M→L，强 model 验证更重 |
| ② 触发 | 纯手动 CLI（Phase D）| 选"含 cron 定时" → **+1-2 Phase**（复用 F127 `memory_consolidation.py`：cron + 合成 root Task+Work + spawn subagent + 单飞 + 系统占位泄漏防御 + quiet hours + config 解析），规模 M→L，撞 F127 后台编排全部坑 |
| ③ F127 关系 | 独立发现端 + F136 gate（Phase C）| 选"共用候选表" → Phase C **范围变**（造 behavior 候选表 or F127 候选表加 behavior 维度[评估概念错配] + atomic claim + REST accept/reject/bulk_reject + 前端候选 UI），规模 M→L，换来批量异步审批（仅②cron 或一次多文件才有价值）|
| ④ 验证 | 强 model OctoBench + 确定性单测（Verify）| 选"仅确定性单测" → Verify **范围减**（但无法证合并保留语义，AC-8 质量维度失守，**不推荐**）|
| ⑤ H1 | 手动前台（Phase D）| 随②联动：选②cron → 后台 subagent（同 F127 §0.1.4 spawn 编排 + H1 守界）|

**最大范围（5 决策全选他项）**：~9-10 Phase，规模升 L，与 F127 编排大量重叠——**建议 v0.1 收窄到推荐窄路径（M），跨文件/cron/候选表列 v0.2**。

**最小范围（推荐窄路径，M）**：5 Phase + Verify，~800-1300 行，复用最大化。

---

## 7. 实施约束（继承项目规则）

- **本 plan 为草案，未实施**——5 决策拍板后再 spec-driver implement。
- **Phase 顺序可微调**（先简后难原则）；Phase 跳过须显式归档（commit message / completion-report）。
- **每 Phase 后 Codex per-Phase review**（命中重大架构变更：新 LLM 工具 + 新事件类型）+ Final cross-Phase review 强制。
- **多评审 panel**（强 model spec-对齐专项）在 Phase Verify + 重大决策节点。
- **0 regression vs baseline 1e64ecd3**（≥ baseline passed）每 Phase 守 + e2e_smoke 8/8。
- **PYTHONPATH 锁 worktree + `uv run --no-sync python -m pytest`，禁 uv sync**。
- **不主动 push / commit origin**——本会话仅产 spec + plan + 研究笔记草案，停在实施前。
