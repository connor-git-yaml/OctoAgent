# F103d Known Issues & Deltas（implement 子代理必读补丁清单）

> **来源**: analyze 子代理（Phase 5.5）的 10 条 findings，扣除已在 spec/clarifications 直接 patch 的 2 条（F-02 / F-05）
> **范围**: implement 子代理执行各 task 时必须按本文 patch 调整代码或文档
> **不修改 spec/plan/tasks 主体**的原因：避免 GATE_TASKS 前文件再变；用补丁清单方式让 implement 闭环

## HIGH（必须按以下处置）

### F-01 — LLM judge stub 升级时机约定

**位置**: tasks T-A-11 / T-D-6

**问题**: T-A-11（Phase A）描述为"stub 实现"，T-D-6（Phase D）"整合升级为真实实现"。若两者触发条件不同 → Tier 1 partial 评分 Phase A vs Phase D 不可重现。

**Patch（implement 子代理执行时必须遵守）**:

1. **T-A-11 实施时**：
   - 创建 `benchmarks/runner/llm_judge.py`
   - 实现 `LLMJudgeTrigger` class，含**触发条件常量**（不是 stub）：
     ```python
     # plan §3.1 明确的常量，本 patch 强制 Phase A 直接落地：
     LLM_JUDGE_TRIGGER_MIN_RATIO = 0.5    # match_ratio >= 0.5 才触发
     LLM_JUDGE_TRIGGER_MAX_RATIO = 1.0    # match_ratio < 1.0 才触发
     LLM_JUDGE_MAX_CALLS_PER_TASK = 2     # 每 task × iteration 最多 2 次
     ```
   - `should_trigger_judge(match_ratio: float) -> bool` 直接用以上常量判断
   - `invoke_judge(...)` 在 Phase A 可以是简单 stub（返回固定 score=0.5），但**触发逻辑必须真实**
   - Phase A 验证：unit test 覆盖 `should_trigger_judge` 边界（0.49 / 0.5 / 0.99 / 1.0）

2. **T-D-6 实施时**：
   - 只升级 `invoke_judge(...)` 真实实现（调用 Sonnet 4.5 with prompt template）
   - **不允许修改触发常量**——任何修改触发条件的 PR 必须由 Codex review 拦下

3. **Codex Phase A review 检查项**: 触发条件常量是否已落入代码 + unit test 覆盖完整

---

## MEDIUM

### F-03 — AC1-4 delta 精度未同步

**位置**: spec AC1-4 / FR-C03

**Patch**: T-D-4 实施 reporter 时严格按 plan §6.3 输出：
- pass rate delta 精度 0.001（即 0.1%，3 位小数）
- regression 列表每条含 `{task_id, failed_assertion, baseline_result, current_result}` 完整字段
- improvement 列表同结构（baseline=FAIL → current=PASS）
- 单元测试覆盖 `--compare` 输出格式

### F-04 — RCA INCONSISTENT 产出物

**位置**: tasks T-E-3

**Patch**: T-E-3 实施时增加分支：
```
if inconsistent_ratio > 0.05:
    生成 `.specify/features/103d-octobench/rca-inconsistent.md`
    内容包含：
      - 各 INCONSISTENT task 列表（task_id + 3 次结果分布）
      - RCA 假设（LLM 随机性 / scorer 健壮性 / prompt 模糊）
      - 决策：重跑 / 调整 task prompt / 接受偏差
    主 session 用 AskUserQuestion 让用户拍板
elif inconsistent_ratio <= 0.05:
    继续 T-E-4 不阻塞
```

### F-06 — Phase F Final review 命名

**位置**: spec §6 Phase F

**Patch**: implement 执行 Phase F 时在 `completion-report.md` 开头明确"Final cross-Phase Codex review 已在 Phase E 末 (T-E-FINAL-REVIEW) 完成，Phase F 文档 review 豁免（CLAUDE.local.md §'不需要做的节点' 命中纯文档微改）"

### F-07 — clarifications.md OQ-1 状态同步

**位置**: `.specify/features/103d-octobench/clarifications.md` OQ-1

**Patch**: implement Phase F 末（T-F-1 或类似）将 clarifications.md OQ-1 状态从"推荐 Haiku"改为：
```
**OQ-1 RESOLVED (2026-05-27 via GATE_DESIGN)**：user simulator LLM = **Sonnet 4.6**（最终拍板，与子代理推荐 Haiku 不同）
原因：保证 simulator 决策质量足够 challenging；user simulator 不计入控变量。
影响：spec FR-B05 已更新；token 成本提高但 Tier 4 RPM/TPM 充裕。
```

---

## LOW

### F-08 — Connor 4 task 内容 + 域归属（GATE_DESIGN 问题 2 用户拍板 2026-05-28）

**位置**: spec FR-D03 / tasks T-A-10

**Connor 真实场景 4 task 具体内容**（用户拍板，T-A-10 实施时直接填入，不再 PLACEHOLDER）：

| Task ID | 文件名 | 领域 | prompt 模板 | 期望 EventStore 事件链 | mock 数据文件 |
|---------|--------|------|------------|---------------------|--------------|
| T1-CONNOR-1 | `t1_connor_1_portfolio.yaml` | 持仓健康度报告 | "我的持仓在 `mock_holdings.json`，请生成一份持仓健康度报告，含估值变动 / 集中度 / 行业敞口 / 风险提示 / rebalance 建议" | `SKILL_PIPELINE_STARTED` → `TOOL_CALLED(filesystem.read_text)` → `MEMORY_RECALL_COMPLETED` → `WORKER_LOG_EMITTED`（中间分析）→ markdown 产出 | `benchmarks/tiers/tier1/fixtures/connor/mock_holdings.json`（含 5-10 持仓 / 股票名 / 数量 / 成本价 / 当前价 / 行业标签）|
| T1-CONNOR-2 | `t1_connor_2_ai_daily.yaml` | AI 领域日报 | "出一份近 24h 的 AI 领域日报，5-10 条新闻，每条带 1 句话评论" | `TOOL_CALLED(web.search)` / `TOOL_CALLED(mcp.perplexity)` ≥ 1 次 → markdown 产出含 ≥ 5 条新闻 | 无（依赖实时 web）|
| T1-CONNOR-3 | `t1_connor_3_robotics_daily.yaml` | 无人机 / 机器人日报 | "出一份近 24h 的无人机 + 机器人（含具身智能 / humanoid）领域日报" | 同 T1-CONNOR-2 | 无 |
| T1-CONNOR-4 | `t1_connor_4_health.yaml` | 睡眠 / 运动分析 | "我的 iOS 健康数据在 `mock_health.json`，分析近 7 天睡眠 / 运动趋势 + 给健康建议" | `TOOL_CALLED(filesystem.read_text)` → markdown 产出含睡眠时长趋势 + 步数趋势 + 建议 | `benchmarks/tiers/tier1/fixtures/connor/mock_health.json`（7 天 × {sleep_hours, hrv, steps, exercise_minutes, resting_hr}）|

**统一 domain 标签**: `connor_real_world`（SC-005 中算第 10 域，基础 9 域 + connor_real_world，不影响域覆盖统计）

**Codex Phase A pre-impl review 重点**:
- 4 task prompt 模板表述清晰（不模糊）
- EventStore 期望事件链合理（不过严不过松）
- mock_holdings.json / mock_health.json 数据合理 + 不含真实个人 PII
- T1-CONNOR-2/3 不依赖 Perplexity 强相关（可能 quota fail）—— scorer 应允许 `web.search` 任意 1+ 次 OK

### F-09 — SC-008 跨 Phase 关联

**位置**: tasks T-F-2

**Patch**: implement T-F-2 实施 handoff.md 时在文件头加注 `Validates SC-008 (jointly with T-E-4)`。Phase F 末验证 SC-008 时同时检查 baselines/m5-baseline.json (T-E-4 产出) + handoff.md (T-F-2 产出) 都存在。

### F-10 — 委托域 4 task 分布

**位置**: spec §0.3 / tasks T-A-7

**Patch**: implement T-A-7 创建 4 个委托 yaml 时按以下分布：
- 2 个 `delegate_task` 单 spawn 场景（不同 capability）
- 2 个 `A2A Worker` 场景（含 1 个 Worker→Worker，与 Tier 3 T3-5 设计差异化：T1 验证 audit chain 完整性，T3-5 验证 D14 解禁后的哲学正确性）

Tier 1 与 Tier 3 委托 task **断言维度不同**：Tier 1 看 event chain 存在性（流程跑通即可），Tier 3 看 audit 信号语义（H3 哲学正确）。

---

## 处置 checklist

- [ ] F-01 patch 在 Phase A T-A-11 实施时检查（最关键）
- [ ] F-03 patch 在 Phase D T-D-4 实施时检查
- [ ] F-04 patch 在 Phase E T-E-3 实施时检查
- [ ] F-06 patch 在 Phase F completion-report 撰写时检查
- [ ] F-07 patch 在 Phase F clarifications 同步时检查
- [ ] F-08 / F-09 / F-10 在对应 Phase 实施时按 patch 处理（LOW，不阻塞但建议）

implement 子代理：本文档与 tasks.md 同优先级消费。
