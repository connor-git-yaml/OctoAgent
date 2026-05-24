# F102 Proactive Followup — Spec Driver Trace

**Feature**: 102-proactive-followup
**Branch**: feature/102-proactive-followup
**Worktree**: `.claude/worktrees/F102-proactive-followup`
**Base baseline**: F101 (commit 74c9ab3, `docs(F101-Final): ... READY_TO_MERGE`)
**Mode**: feature（完整 17 Phase）
**Research mode**: codebase-scan（用户拍板，2026-05-18）
**Gate policy**: balanced（GATE_DESIGN 硬门禁强制暂停）
**Preset**: quality-first（全 phase 用 opus）

## 不在范围（明确排除）

- ❌ 不动 F101 NotificationService 核心结构（仅复用）
- ❌ 不实施 dismiss 跨重启持久化（F107）
- ❌ 不动 Blueprint 修订（F103）
- ❌ 不实施 D8 control_plane DI 重构（F107）
- ❌ 不动 Hermes Agent 其他模式（仅 Routine）

## Phase 序列与执行计划

| # | Phase | Agent | 条件 | 备注 |
|---|-------|-------|------|------|
| 0   | constitution_check       | inline           | always               | init-project.sh 已通过 |
| 0.5 | research_mode_determination | inline        | always               | codebase-scan |
| 1a  | product_research         | product-research | full \| product-only | **跳过** |
| 1b  | tech_research            | tech-research    | full \| tech-only \| codebase-scan \| custom | 跑（含 Hermes Routine + 仓库实测） |
| 1c  | research_synthesis       | inline           | full                 | **跳过** |
| 1d  | online_research          | inline           | online required      | **跳过** |
| 2   | specify                  | specify          | always               | spec.md |
| 3   | clarify_and_checklist    | parallel         | always               | clarify + checklist 并行 |
| 3.5 | gate_design              | gate             | always               | 🔒 硬门禁，必须用户拍板 |
| 4   | plan                     | plan             | always               | plan.md（Phase 顺序：D→B→E→F→C→Final 建议） |
| 5   | tasks                    | tasks            | always               | tasks.md |
| 5.5 | analyze                  | analyze          | always               | GATE_ANALYSIS + GATE_TASKS |
| 6   | implement                | implement        | always               | 按 plan Phase 顺序逐 Phase + Codex review |
| 6.5 | verify_independent       | inline           | always               | 编排器独立验证 |
| 7a  | spec_review              | spec-review      | always               | 并行 |
| 7b  | quality_review           | quality-review   | always               | 并行 |
| 7c  | verify                   | verify           | always               | + Final cross-Phase Codex review + GATE_VERIFY |

## 执行日志

[00:43] Phase 0+0.5: 初始化完成 | mode=codebase-scan | preset=quality-first | gate_policy=balanced
[00:48] Phase 1b: tech_research COMPLETED | artifacts=research/tech-research.md | duration=~4min
  - 7 任务全实测，30+ file:line 定位
  - **4 个仓库现状与 prompt 冲突**：
    (1) Hermes Agent 源码不存在（只有 memU）→ 改为基于 ObservationRoutine pattern 推断
    (2) USER.md 当前只有 active_hours 一个机器可读字段，需新增 daily_summary_time 等
    (3) approval_timeout_seconds 在 policy/models.py 不在 USER.md（F101 prompt 描述误差）
    (4) 无 TASK_COMPLETED/TASK_FAILED EventType，task 终态走 STATE_TRANSITION payload to_status
  - 5 个关键决策 D1-D5 待 spec 前用户拍板
[00:48] GATE_RESEARCH: PAUSE | reason=4 重大冲突需用户拍板（虽然配置 default_behavior=auto）
[00:55] GATE_RESEARCH: USER_DECISIONS
  - D1 选 AutomationSchedulerService + CronTrigger（复用现有 + audit 统一）
  - D3 选 F102 范围内新增 task_store.list_tasks_in_time_range(start, end)
  - D4 选 LLM 汇总（cheap alias）—— 注意需 LLM 失败 fallback + token 成本接受度
  - D5 选 Discard + event_store filtered=True（与 F101 quiet hours 语义一致）
  - 待 spec 阶段定：D2 USER.md 字段格式细节 / WeeklyRoutine 是否纳入 / Hermes 缺失应对方案
[00:55] Phase 2: specify STARTED | agent=specify
[01:03] Phase 2: specify COMPLETED | artifacts=spec.md | duration=~8min
  - 17 AC (块 B 7 / 块 D 4 / 块 E 4 / 块 F 1 / task_store 1)
  - 15 FR + 5 NFR + 7 风险条目
  - WeeklyRoutine 不纳入 F102（spec 自决，YAGNI）
  - LLM fallback 选 (b) deterministic 模板
  - 2 OQ 留 plan Phase 0：OQ-1 tasks.created_at 索引 / OQ-2 cheap alias 可用性
  - 复杂度 MEDIUM，建议 4-5 Phase（Phase 0 侦察 + B + D + E + F + Final）
[01:03] Phase 3: clarify + checklist [PARALLEL] STARTED
[01:15] Phase 3: clarify + checklist [PARALLEL] COMPLETED | artifacts=clarify.md / checklists/requirements.md
  - clarify 7 CQ：CQ-1 channel 接口矛盾（同 CHK-3.2 BLOCKER）+ CQ-2 attention 算法 + CQ-3 cron 重载 + CQ-4 LLM token + CQ-5 bootstrap + CQ-6 时区 + CQ-7 空数据
  - checklist 21 项：15 通过 / 2 BLOCKER / 7 WARNING
  - 2 BLOCKER 由用户拍板 + 自决修订
[01:18] spec.md 修订（11 处编辑）：
  - 用户拍板：FR-B8 channels 参数扩展（CQ-1/CHK-3.2 BLOCKER 闭环）+ AC-B5 空数据不推送（CQ-7）
  - 自决：SD-7 attention_count 算法 + SD-9 LLM token budget + SD-10 时区语义 + AC-B6 cron 重启生效 + AC-B4 测试方法明确（CHK-1.2 BLOCKER 闭环）+ misfire 改 30s + cron 注册失败兜底 + AC-E3/E4 测试覆盖 + AC-B2 集成测试显式 + cheap alias 依赖标注 + 单文件拆分 daily_routine_config.py
  - 17 AC / 16 FR / 10 SD / 691 行
[01:18] GATE_DESIGN: PAUSE | hard_gate=true | 等待用户对 spec.md 最终拍板
[01:22] GATE_DESIGN: PASSED | user_decision=通过推进 Phase 4 plan
[01:22] Phase 4: plan STARTED | agent=plan
[01:30] Phase 4: plan COMPLETED | artifacts=plan.md | duration=~5min
  - 5 Phase 编排：A(实测/spec校正) → B/D(并行) → C → E → F
  - Phase A 实测预完成：OQ-1 索引已存在 / OQ-2 cheap alias 已配置（gpt-5.4 thinking_level:low）/ CQ-5 bootstrap=_bootstrap_optional_routines / channel_name 实测属性名校正
  - 17 AC 全部有 Phase 归属 + test file 映射
  - 风险无需立刻 escalation
[01:31] spec.md 3 处校正（plan Phase A 发现）:
  - FR-B8 channel.name → channel.channel_name（实测属性名）
  - FR-B8 + SD-1 新增 summary_channels "web" → "web_sse" 映射规则
  - AC-D3 含 channel_name == "telegram"/"web_sse" 精确断言
[01:31] Phase 5: tasks STARTED | agent=tasks
[01:38] Phase 5: tasks COMPLETED | artifacts=tasks.md | duration=~6min
  - 42 tasks / 22.5h / 17 AC + 16 FR 全覆盖
  - 3 HIGH 风险（T-C3 bootstrap race / T-C4 CancelledError re-raise / T-C5 token budget + fallback 边界）
  - Phase 分布: A=4 / B=12 / D=4 / C=12 / E=6 / F=4
[01:38] Phase 5.5: analyze STARTED | agent=analyze
[01:46] Phase 5.5: analyze COMPLETED | artifacts=analyze.md
  - 27 项检查 / 21 通过 / 0 BLOCKER / 3 WARNING / 3 LOW
  - GATE_TASKS 推荐 PASS：spec ↔ plan ↔ tasks 三向一致，17 AC + 16 FR 100% task 覆盖
  - 3 WARNING（plan §0.6 措辞 / Payload schema 放置 / T-C3 依赖过度保守）可实施时就地修正
[01:46] GATE_TASKS: PAUSE | critical=always | 等待用户对 tasks.md + Phase A 启动拍板
