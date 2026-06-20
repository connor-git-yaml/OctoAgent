# F126 一致性分析报告（analysis.md）

- Feature: F126 capability-efficiency / 基线 master cd9a56c3
- 制品: spec.md（17 FR / 14 AC / 4 SD / §7 C1–C5）、plan.md、tasks.md（T000–T153）、clarifications.md（C1–C5 + GATE_DESIGN 拍板）、checklist.md（47 项）、research/tech-research.md、trace.md
- 方法: 确定性结构校验（解析 FR/AC/SD ID + tasks 引用，机械核对 orphan/uncovered），辅以语义一致性扫描

## 执行摘要

- **状态**: PASS — 0 CRITICAL / 0 HIGH / 2 MEDIUM / 4 LOW，可进入 implement。
- M1/L1 已在 implement 前由主节点顺手修（标称统一 17 FR / 14 AC；T130-GATE fallback 编号改 "T135 起"）。

## 1. FR 覆盖（orphan 检测）

17 条 FR 全部有 ≥1 task：FR-1.1→T101/T105 · FR-1.2→T103/T104 · FR-1.3→T101 · FR-1.4→T101/T105/T140 · FR-1.5→T101 · FR-1.6→T102 · FR-2.1→T135/T141 · FR-2.2→T135b · FR-2.3→T135 · FR-2.4→T135c · FR-2.5→T120 · FR-2.6→T135b · FR-3.1→T131 · FR-3.2→T130/T132/T133 · FR-3.3→T151 · FR-3.4→T138 · FR-3.5→T138。**无 orphan（17/17）。**

## 2. AC 覆盖（uncovered 检测 + AC↔test 文件一致）

14 条 AC 全部有 test 任务，spec AC↔test 绑定文件与 tasks 落点逐字一致：AC-GATE-1→T120+T136 · AC-1.1/1.3/1.4/1.5→T106 · AC-1.2→T107 · AC-2.1/2.2/2.3→T136 · AC-3.1/3.2→T134 · AC-3.3→T151（living-docs 校验，非 pytest，方式显式）· AC-3.4→T139 · AC-LOOP-1→T137(e2e)。**无 uncovered（14/14，P1 AC 13 条全绑定真实 test 路径）。**

## 3. SD 依赖一致性

- SD-1（KV-cache 硬门）：T120 标 [硬前置门 AC-GATE-1]；项2 实现 T135/T135b/T135c/T136/T137 全标依赖 T120 PASS，step2 标题 + T130-GATE 双重声明 BLOCKED。✓
- SD-2（占位↔readback）：step1 read-back（T130–T134）排在 step2 eviction（T135–T137）之前；T135 依赖含 T131；AC-LOOP-1 e2e（T137）依赖 T135b + T131/T132。✓
- SD-3（项1 独立）：批次1（T101–T108）仅依赖 T000，不依赖批次2；T120 注明与批次1 可并行。✓
- SD-4（预算统一）：T138 标 [SD-4]，依赖 T135b 占位语义统一。✓

## 4. GATE_DESIGN 决议（C1–C5）落实

C1 宽松+豁免→T101+T102 · C2 独立工具→T131 · C3 per-turn 8k+告警再卸载+runner _call_hook→T138 · C4 占位三冻结值首折叠构造一次→T135b · C5 工具层主隔离+store Optional task 纵深（纳入本批次）→T130+T132+T133（store task 隔离单测）。**5/5 落地，无遗漏豁免/隔离单测任务。**

## 5. Constitution 一致性

4 类 emit：校验拒绝→T140（复用 TOOL_CALL_FAILED）· 折叠→T141 新增 TOOL_RESULT_EVICTED（emit 落 T135b）· read-back→T132（复用 broker 标准路径）· 预算→T141 新增 PER_TURN_BUDGET_EXCEEDED（emit 落 T138）。#10 权限走中央在 T132 第①道防护体现。**一致。**

## 6. prefix-cache 不变量

占位冻结→T135b + T136::test_deterministic_frozen_placeholder（字节级 ==）· 不改写中段→T135 + T136::test_no_mid_history_rewrite · resume 配对→T135c + T136::test_resume_pairing_intact · 与 W8 边界→T135 声明 + T150 双评审重点 + plan §6。**四不变量全覆盖。**

## 7. Out-of-scope 一致性

ContextCompactionService 仅作 "task=None 须零变更的内部 caller" 被动引用（T130/T133），非改动落点；W8 system 组装层 / AmbientRuntime 仅声明不触及；ConversationTurn 扩字段无任何落点。**零误碰。**

## 8. 收尾任务齐备

0 regression T000+T152 · 双评审 T108（批次1）+T150（批次2 Codex+Opus 0 HIGH）· living-docs T151 · 交付 T153。**齐。**

## Findings

**CRITICAL: 0 · HIGH: 0**

**MEDIUM:**
- **M1** 标称计数失真：spec/plan/tasks/trace 写「16 FR / 13 AC」，实际 17 FR / 14 AC。不影响覆盖（实测全覆盖），但属 SDD 强化警示的人工计数失真。→ **主节点已修**（标称统一 17/14）。
- **M3** per-turn P2 降级测试对齐注记缺失：T138 若降级"仅告警不卸载"，AC-3.4 test_aggregate_overflow_offloaded 断言会失败；tasks 未显式注明降级时 AC-3.4 断言收窄为"仅告警 emit"。→ implement 期 T139/T153 补注 + completion-report 归档。

**LOW:**
- **L1** T130-GATE fallback 文案 "T131-tail 起" 编号笔误，应为 "T135 起"（依赖序总览表述正确）。→ **主节点已修**。
- **L2** store 层 task 隔离单测 T133 未定名 → implement 时定名（如 test_artifact_store_task_isolation.py）写回 completion-report。
- **L3** "30 任务" 计数口径（含/不含 T130-GATE + T135b/c 子任务）→ 纯口径，无需修。
- **L4** FR-3.5 "统一治理避免双重截断" 单测 T139 未显式断言占位同源 → T139 可加断言"项2 占位与项3 卸载占位格式同源"（checklist §5 作 verify 兜底）。

## 覆盖汇总

FR 17/17（0 orphan）· AC 14/14（0 uncovered）· SD 4/4 · C1–C5 5/5 · 4 emit + 2 新 EventType 全在 · Out-of-scope 误碰 0 · 收尾齐。

**PASS：无阻断性不一致，可进入 implement。**
