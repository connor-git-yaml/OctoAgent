# Implementation Plan: F111 Behavior Compactor

**Feature ID**: F111
**Status**: v1.0 收窄定稿（2026-07-15，对应 spec v1.0；设计稿 Phase 拆分按拍板②cron+③候选表重排）
**Spec**: [spec.md](./spec.md)
**Baseline**: master `f2081010`（实测全量 baseline：**5207 passed / 14 skipped / 9 deselected(real_llm) / 1 xfailed / 1 xpassed**，178s）
**前置依赖**: F127 / F107 / F108a 写核 / F063 P3 原语 / F102 config 范式 / F144 e2e 范式 全部在 master（spec §0.1.1 f2081010 复核）

---

## 0. 规划前提

1. F111 是发现端 + 编排 + 审批治理 Feature，不造原语（spec §10 对账表）。
2. 核心成本在四件：①发现端本体（占位符 + 契约解析 + 护栏 H1-H6）②候选表 + 审批端（claim/新鲜度/CONFLICT/apply）③cron 编排（仿 F127 全套）④REST + CLI 面。
3. 规模 **L**（拍板②cron 使规模从设计稿 M 升 L，与 milestones.md 一致）。

---

## 1. Phase 拆分（6 实施 Phase + Verify，先简后难）

> 每 Phase 后 0 regression vs baseline（≥5207 passed）+ 焦点套件；小步 commit（中文、无 Co-Authored-By）。

### Phase A — 地基（纯新增零行为变更）
- **范围**：
  - `core/models/enums.py`：`BEHAVIOR_COMPACT_{TRIGGERED,COMPLETED,FAILED,SKIPPED,PROPOSED,APPLIED,REJECTED,CONFLICTED}` 8 事件（MEMORY_CONSOLIDATION 块邻接）。
  - `core/models/payloads.py`：8 个 payload schema（计数/hash/id 引用，无原文全文）。
  - `core/behavior_workspace/_types.py`：`COMPACT_ELIGIBLE_FILE_IDS` 白名单 + 派生 `COMPACT_EXCLUDED_FILE_IDS`。
  - `core/behavior_workspace/protected.py`（新）：PROTECTED 占位符提取/插回/校验（`extract_protected_sections` → `(占位后文本, sections)`；`verify_and_reinsert(compacted, sections)` → exactly-once 校验 + 插回 + 终局字节断言；不配对标记 → malformed 错误）。
  - `core/models/behavior_compact.py`（新）：`BehaviorCompactCandidate` + `BehaviorCompactCandidateStatus` 五态。
  - `core/store/behavior_compact_store.py`（新）+ core sqlite_init DDL：insert / get / list / `claim_candidate_for_apply`（CAS）/ `mark_candidate_status`（expected_status CAS）/ `has_blocking_candidate(file_key, source_hash)`。R4 共享连接不在方法内 commit。
- **`[@test]`**：`packages/core/tests/test_f111_protected_sections.py` + `test_f111_behavior_compact_store.py`（claim 竞态 / CAS / 幂等查询）+ 事件 payload 测试。
- **风险**：低。

### Phase B — 发现端（核心价值）
- **范围**：`services/behavior_compact_discovery.py`（llm_client 注入 Protocol 仿 `ConsolidationLLMClient`；流程 = eligible → 读盘 → too_small/too_large → PROTECTED 占位 → prompt（C9）→ LLM(main alias, max_tokens=8192) → 契约 A' 解析 → 插回 → H1/H3/H6 → input-hash 幂等 → insert 候选 → conn.commit → emit PROPOSED；每步 SKIPPED reason 事件）+ `services/behavior_compact_config.py`（compact_active/compact_time + 复用 timezone/channels extractors）。
- **`[@test]`**：`apps/gateway/tests/test_f111_compact_discovery.py`（AC-1~6/9 + 契约解析兜底 + 幂等）+ `test_f111_compact_config.py`（解析/注释块/fallback/锚定）。
- **风险**：中。契约 A' 解析 robust 性 + H6 extractors 复用面。

### Phase C — 审批端 + REST（C4/C7 红线）
- **范围**：`services/behavior_compact_approval.py`（accept = claim → eligible+新鲜度+H2 复验 → prepare/commit 写盘 → record_behavior_version → invalidate cache → APPLIED；判定失败→CONFLICT / 异常→回滚 PENDING；reject）+ `routes/behavior_compact.py`（GET candidates / POST accept / POST reject / POST trigger——trigger 在 Phase D 接 service，本 Phase 可先 stub 或后置）+ `main.py` 注册（protected deps）。
- **`[@test]`**：`test_f111_compact_approval.py`（AC-8 全分支）+ `test_f111_compact_routes.py`（AC-13）。
- **风险**：中-高（红线 Phase）。AC-7 静态断言发现端不 import 写核 commit。

### Phase D — cron 编排 + harness 装配
- **范围**：`services/behavior_compaction.py`（仿 `MemoryConsolidationService`：startup ensure root + cron 注册 / `_run_compaction` cron 回调（active → 单飞 bool + 持久 child 检查 → spawn subagent 审计容器 → 逐 SHARED 文件发现端 → COMPLETED/FAILED → 通知）/ `run_manual`（REST trigger 消费：无 active 门、无 spawn、共享单飞、同步返回 outcomes）/ shutdown）+ `control_plane/_base.SYSTEM_INTERNAL_WORK_IDS` 补 `_behavior_compact_root_work` + harness 装配（`octo_harness.py` F127 块邻接 + shutdown 块）+ REST trigger 接通。
- **`[@test]`**：`test_f111_compact_trigger.py`（AC-10，仿 test_f127_consolidation_trigger.py 结构：startup/disabled/单飞/capacity/占位泄漏 guard/通知决策表/手动无 active 门）。
- **风险**：中。F127 handoff 9 坑主要命中区（坑 1 events PK / 坑 2 spawn 真父对 / 坑 3 占位泄漏一族）。

### Phase E — CLI
- **范围**：`provider/dx/behavior_commands.py` 扩 `compact` 子命令（薄 HTTP 壳照 attest 范式：实例 env 解析 port/token + httpx + 脱敏；`octo behavior compact [FILE_ID] [--project SLUG]` → trigger + diff 预览；`--list` / `--apply ID` / `--reject ID`；`--list-size` 本地测量 + 阈值标注；gateway 不可用引导 `octo service`）。
- **`[@test]`**：`packages/provider/tests/test_f111_behavior_compact_cli.py`（AC-14，fake HTTP）。
- **风险**：低-中（env/token 解析细节）。

### Phase F — e2e（拍板④b/④c）
- **范围**：
  - `e2e_live/test_e2e_scripted_behavior_compact.py`（AC-11：真 harness + 脚本化 compact LLM stub 经编排服务 `llm_client` 公开缝注入 + resolve_for_alias bomb + REST trigger→accept 全链 + reject 半边；marker `e2e_scripted + e2e_live` + `pytest.importorskip` 防御）。
  - `e2e_live/test_e2e_behavior_compact_real_llm.py`(AC-12：marker `e2e_full + real_llm`；凭证缺失 SKIP；植入语义重复 AGENTS.md 变体断言质量下限)。
  - tests/AGENTS.md marker 表措辞微调（spec §6 AC-11 注）。
- **风险**：低-中（hermetic 纪律 + marker 一致性闸）。

### Phase Verify — 双评审 + 文档
- Codex `codex review --base origin/master`（scoped）迭代 0 HIGH；Opus 式对抗自审（挑战面：发现端丢规则/H1/H2 机械可验性 / cron 无人值守审批堆积 / 禁区双层可绕性 / #9 边界 / F127 9 坑命中检查）。
- 全量回归 + e2e_smoke + e2e_scripted 终门；real_llm 用例真打一次记录（配额纪律）。
- completion-report + handoff（若有下游）+ living-docs（milestones F111 行 / tests/AGENTS.md / Blueprint behavior 相关章漂移检查）。

---

## 2. 依赖图

```
Phase A（地基：事件+白名单+PROTECTED+候选表）
   └─→ Phase B（发现端 + config）
          ├─→ Phase C（审批端 + REST）      ← C4/C7 红线
          └─→ Phase D（cron 编排 + harness）← F127 坑区
                 └─→ Phase E（CLI）
                        └─→ Phase F（e2e）→ Verify
```
C 与 D 都依赖 B、彼此独立可换序；E 依赖 C+D（REST 面就位）。

## 3. 估算

| 维度 | 值 |
|------|-----|
| Phase | 6 + Verify |
| 规模 | **L**（cron 编排入 v0.1）|
| 新文件 | 8 生产（protected/model/store/discovery/config/approval/compaction/routes）+ 9-10 测试 |
| 改动文件 | enums/payloads/_types/sqlite_init/_base/octo_harness/main/behavior_commands + tests/AGENTS.md |
| 净增行 | ~2000-2800（含测试）|

## 4. 关键风险与缓解

| 风险 | Phase | 缓解 |
|------|-------|------|
| 契约 A' 解析（LLM 忘分隔符/包 fence/混解释）| B | 分隔符定位容忍前后噪声 + fence 剥离 + 缺失→fallback（保守 0 候选）|
| 合并丢规则/改语义（根本难点）| B/F | H1/H2/H3/H6 确定性层 + H4 人审 diff + AC-12 真 LLM 质量下限 |
| F127 坑 1（events PK task_seq）| B/C/D | 全部 emit 走 `append_event_committed` + 先 commit 业务态再 emit |
| F127 坑 2（spawn 真父对）| D | ensure root Task+Work 成对 + 显式 thread_id/requester |
| F127 坑 3（占位泄漏一族）| D | SYSTEM_INTERNAL_WORK_IDS + guard 测试 + channel="system" + grep 全部 list/scan/notify 面复核 |
| F127 坑 5（claim 后异常卡 APPLYING）| C | try/except 全程回滚 PENDING |
| H6 extractors 覆盖不全 | B | fail-open 到 H4 人审（少保护不误伤）+ 归档 |
| pre-commit hook 跨树 import | F | e2e 文件 `pytest.importorskip` 防御（F138 先例）|
| CLI env/token 解析 | E | 照抄 attest_commands 已验证路径 |
