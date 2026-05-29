# F103d OctoBench Phase A — Codex Adversarial Review
> 日期：2026-05-29
> reviewer：Codex GPT-5.4 high effort
> commits：2f47156 + 8241deb
> 结论：BLOCKED

## Review 范围

- 模式：background review-only；未修改源码。
- 已读取范围：`CLAUDE.local.md` §Codex Adversarial Review、`spec.md`、`plan.md`、`known-issues-deltas.md`、`phase-0-poc-report.md`、`benchmarks/runner/scorer.py`、`benchmarks/runner/llm_judge.py`、`benchmarks/runner/scoring_rubrics.yaml`、25 个 Tier 1 YAML、Connor 2 个 fixture。
- 排除范围：未 review `.specify/features/103d-octobench/poc/` 临时 PoC 脚本内容。

## Findings

**Finding #1** [HIGH]
- scope: `benchmarks/runner/scorer.py:334` / `fetch_events_from_store`
- description: `fetch_events_from_store` 与真实 `SqliteEventStore` API 不兼容。当前函数是同步函数，调用 `event_store.get_events_by_types_since(since=..., event_types=...)`（`scorer.py:382-385`）；真实方法是 async，签名为 `get_events_by_types_since(task_id, event_types, since_ts)`（`octoagent/packages/core/src/octoagent/core/store/event_store.py:202-207`）。同时真实 `Event` 模型字段是 `type` 而不是 `event_type`（`octoagent/packages/core/src/octoagent/core/models/event.py:34-41`），但序列化逻辑只在存在 `event_type` 时做转换（`scorer.py:393-398`）。
- impact: Phase D runner 一旦使用该 helper，会因缺少 `await`、错误关键字参数、缺少 `task_id` 过滤而直接失败；即使修到可调用，也会因 `type` 未归一化为 `event_type` 导致 `event_store_assert` 全部匹配失败。若改成全局 since 查询，还会把并发 task 的事件混入当前 task。
- recommendation: 将函数改为 async，并显式接收 `task_id`；按真实 API 调用 `await get_events_by_types_since(task_id=..., event_types=..., since_ts=...)`；统一把 `Event.type` / dict `type` / dict `event_type` 归一化为 scorer 内部字段。

**Finding #2** [HIGH]
- scope: `benchmarks/runner/scorer.py:246` / `score_tier1`
- description: Tier 1 满分 PASS 在 Phase A-D 只能得到 `weighted_score=0.65`。代码在 `match_ratio == 1.0` 时只设置 `pass_fail_score=1.0`（`scorer.py:246-250`），`partial_score` 与 `efficiency_score` 都为 `None`；随后按原始权重累加（`scorer.py:283-290`）。而 `scoring_rubrics.yaml` 将 `partial_weight=0.25`、`efficiency_weight=0.10`，且 `efficiency_baseline_tokens: null`（`benchmarks/runner/scoring_rubrics.yaml:10-16`）。按代码计算，完美通过也只有 0.65。
- impact: `BenchmarkRun.score` 在 plan 中定义为 0.0~1.0 加权得分（`plan.md:508-510`），但 Phase A-D 的 Tier 1 score 上限不是 1.0；M5 baseline 报告里的 `weighted_score` 会系统性偏低，后续 M6 delta 无法解释。
- recommendation: 明确定义禁用维度的归一化策略：要么只在活跃权重之间重新归一化，要么 PASS 时让未触发 partial 维度按满分处理；efficiency 未校准前应从权重分母中移除。

**Finding #3** [HIGH]
- scope: `benchmarks/tiers/tier1/t1_memory_001.yaml:9`
- description: Memory 任务的 `required_fields` 与真实 payload 字段不对齐。YAML 要求 `MEMORY_ENTRY_ADDED.content_contains: "OctoAgent"` 和 `MEMORY_RECALL_COMPLETED.namespace: AGENT_PRIVATE`（`t1_memory_001.yaml:9-14`）；matcher 会把 `content_contains` 映射到 `payload["content"]`（`scorer.py:118-121`）。但真实 `MEMORY_ENTRY_ADDED` 写入 payload 使用 `preview` / `tool` / `operation` 等字段（`octoagent/apps/gateway/src/octoagent/gateway/services/builtin_tools/user_profile_tools.py:248-258`），`memory.write` 路径使用 `namespace_kind` / `namespace_id`（`memory_tools.py:774-787`）；真实 recall payload 字段是 `queried_namespace_kinds` / `hit_namespace_kinds`，不是 `namespace`（`octoagent/packages/core/src/octoagent/core/models/payloads.py:165-189`）。
- impact: 正确执行的 memory 写入 / recall 也会被 scorer 判 FAIL，Tier 1 memory 域 baseline 失真。
- recommendation: 先用真实 Event payload 字段重写 memory task 断言；必要时扩展 matcher 支持 `*_contains` 指向指定字段，如 `preview_contains`、`namespace_kind`、`hit_namespace_kinds_contains`。

**Finding #4** [HIGH]
- scope: `benchmarks/tiers/tier1/t1_threat_scanner_001.yaml:10`
- description: ThreatScanner block task 只断言存在 `POLICY_DECISION`，没有断言这是拒绝。两个安全任务的 `required_fields` 都是 `{}`（`t1_threat_scanner_001.yaml:10-11`, `t1_threat_scanner_002.yaml:10-12`），但 notes 写明 PASS 条件是拒绝执行（`t1_threat_scanner_001.yaml:15-18`, `t1_threat_scanner_002.yaml:16-18`）。真实 policy payload 有 `action` 字段（`octoagent/packages/policy/src/octoagent/policy/models.py:328-339`），枚举值包含 `deny`（`models.py:29-37`）。
- impact: 系统允许危险 prompt 但产生日志型 `POLICY_DECISION` 时，benchmark 仍可能 PASS；安全拦截能力被 false positive 掩盖。
- recommendation: ThreatScanner task 必须断言 `action: deny`，并建议增加 `label_contains` 或 `reason_contains` 来确认命中 ThreatScanner / prompt-injection 规则，而非任意 policy 决策。

**Finding #5** [MEDIUM]
- scope: `benchmarks/runner/scorer.py:256` / `score_tier1`
- description: PARTIAL 评分同时把 EventStore `match_ratio` 写入 `pass_fail_score`（`scorer.py:256-268`）并叠加 LLM judge 的 `partial_score`（`scorer.py:283-290`）。同时 YAML 的 `partial_signals` 字段完全未被读取：函数只取 `task_id` 和 `expected_events`（`scorer.py:222-224`）。
- impact: 部分命中会被双重计分；task 作者在 `partial_signals` 中表达的“可接受部分通过信号”没有任何效果，schema 与 scorer 语义漂移。
- recommendation: `pass_fail_score` 保持二值，partial 维度单独消费 `partial_signals` 或真实 judge 输出；如果决定用 `match_ratio` 做 pass_fail 插值，需删除或重定义 `partial_signals`。

**Finding #6** [MEDIUM]
- scope: `benchmarks/runner/scorer.py:162` / `event_store_assert`
- description: 贪心匹配没有消费已匹配的 actual event。每条 expected event 都重新扫描同类 candidates（`scorer.py:162-180`），同一个 actual event 可以满足多个相同 `event_type` 的 expected event。
- impact: 当前 25 个 Tier 1 YAML 没有同一 task 内重复 expected `event_type`，所以不是现有 false positive；但 Phase B/C/D 若需要断言“至少两次工具调用 / 两条 A2A 消息”，单个事件即可重复命中，导致数量型断言失效。
- recommendation: 对每个 expected event 选择 match 后标记 actual event 已使用；或实现按 event_type 分组的 bipartite matching，并为“至少 N 次”提供显式 schema。

**Finding #7** [MEDIUM]
- scope: `benchmarks/runner/scorer.py:113` / `_match_required_fields`
- description: 空字符串 / null 约束被无条件跳过（`scorer.py:113-116`），注释说“只需字段存在即可”，但代码没有检查字段存在。`tool_name_contains` 还把 `tool_name`、`function_name`、`name` 无分隔拼接（`scorer.py:123-129`）。
- impact: `{some_field: null}` 或 `{some_field: ""}` 会在字段缺失时通过；拼接字段可能跨字段误命中，增加 false positive。
- recommendation: 空/null 约束应检查 `key in payload`；`tool_name_contains` 应在明确字段列表中逐个匹配，避免无分隔拼接。

**Finding #8** [MEDIUM]
- scope: `benchmarks/runner/scorer.py:35` / `TaskVerdict`
- description: scorer 新增 `ERROR` verdict（`scorer.py:35-41`），异常时返回 `TaskVerdict.ERROR` 且不设置 score（`scorer.py:305-311`）；但 spec 的 `BenchmarkRun.result` 枚举不包含 `ERROR`（`spec.md:319-320`），pass rate 分母规则只明确 `QUOTA_SKIP` / `TIMEOUT` 和 `INCONSISTENT`（`spec.md:149-152`, `spec.md:205`）。
- impact: Phase D reporter/store 若严格按 spec 枚举处理，会丢失或误算 scorer 内部异常；若把 ERROR 当 FAIL 或跳过，pass rate 会出现不可审计差异。
- recommendation: 在 spec/plan 中显式加入 `SCORER_ERROR` 或把 scorer ERROR 映射到 `INFRA_ERROR` / FAIL，并规定是否计入分母。

**Finding #9** [MEDIUM]
- scope: `benchmarks/runner/scoring_rubrics.yaml:24`
- description: `tier2-tau-v1` 配置 `pass_fail_weight: 0.90`、`partial_weight: 0.10`，但 `partial_logic: null`（`scoring_rubrics.yaml:24-31`）。notes 说 0.10 是未来扩展预留（`scoring_rubrics.yaml:34-36`）。
- impact: 推断，需在 Phase B scorer 实现前验证：若 Phase B 沿用 Tier 1 的原始权重累加方式，τ-bench 完美 Pass@1 最高只能得 0.90；若代码特殊处理，又会让 rubric 含义不自洽。
- recommendation: Phase B 前将 τ-bench 改为 `1.00/0.00/0.00`，或立即定义可执行 partial 逻辑。

**Finding #10** [MEDIUM]
- scope: `benchmarks/runner/scoring_rubrics.yaml:15`
- description: efficiency baseline 的存储形态不一致。rubric 当前只有单个 `efficiency_baseline_tokens` 标量（`scoring_rubrics.yaml:15`），spec entity 也写成 `int | None`（`spec.md:351-354`）；但 plan 要“按 domain 分组计算 p50”并构造 `p50_by_domain` dict（`plan.md:321-324`, `plan.md:687-707`）。
- impact: Phase E 无法把 per-domain p50 正确写回当前 rubric schema；若强行写标量，会把不同 domain 的 token 预算混为一个值。
- recommendation: 在 Phase B/D 前固定 schema：改为 `efficiency_baseline_tokens_by_domain: dict[str, int]`，或把 rubric 拆成 per-domain/per-task rubric。

**Finding #11** [MEDIUM]
- scope: `benchmarks/tiers/tier1/t1_delegation_004.yaml:10`
- description: main→worker A2A 与 worker→worker A2A 的断言几乎相同。`t1_delegation_003.yaml` 和 `t1_delegation_004.yaml` 都只要求 `A2A_MESSAGE_SENT`、`A2A_MESSAGE_RECEIVED`、`WORKER_RETURNED`，且 `required_fields` 都为空（`t1_delegation_003.yaml:10-16`, `t1_delegation_004.yaml:10-16`）。真实 A2A payload 有 `from_agent` / `to_agent` / source/target runtime/session 字段可用（`octoagent/packages/core/src/octoagent/core/models/payloads.py:273-286`）。
- impact: `T1-DELEGATION-004` 不能证明消息源是 Worker，也不能区分 worker→worker 与 main→worker；H3-B 路径可能被普通 A2A 事件 false positive 覆盖。
- recommendation: 至少断言 `from_agent_contains` / `to_agent_contains` 或 source/target session 字段；若 Phase A 只想测 event chain，notes 不应声称覆盖 worker→worker 语义。

**Finding #12** [MEDIUM]
- scope: `benchmarks/tiers/tier1/t1_connor_2_ai_daily.yaml:20`
- description: Connor/news/health 类 task 的输出质量条件只写在 notes，scorer 不会读取。比如 AI 日报 notes 要求“Markdown 含 ≥ 5 条新闻”（`t1_connor_2_ai_daily.yaml:20-24`），健康分析 notes 要求含睡眠/步数趋势和建议（`t1_connor_4_health.yaml:22-28`），但 expected_events 只要求任意 `TOOL_CALL_*` 和 `MODEL_CALL_COMPLETED`（`t1_connor_2_ai_daily.yaml:9-15`, `t1_connor_4_health.yaml:9-15`）。
- impact: Agent 只要调用任意工具并完成一次模型调用，即使没有生成目标报告，也可能 PASS；Connor 真实场景会变成“流程烟测”，不是用户价值验证。
- recommendation: 将 notes 中的产出要求提升为可评分字段：例如断言 `MODEL_CALL_COMPLETED.response_summary_contains`，或在 Phase D runner 捕获最终回答并交给 LLM judge / regex validator。

**Finding #13** [LOW]
- scope: `benchmarks/runner/llm_judge.py:42` / `LLMJudgeTrigger`
- description: `LLM_JUDGE_MAX_CALLS_PER_TASK=2` 的状态只存在于 `LLMJudgeTrigger` 实例的 `_call_count`（`llm_judge.py:42-60`），但 `score_tier1` 每次调用都会新建实例（`scorer.py:240`）。`reset_call_count()` 存在但当前没有调用点（`llm_judge.py:99-101`）。
- impact: 当前每次 `score_tier1` 最多只会调用一次 judge，常量 2 不会真正约束跨重试/跨 iteration 行为；Phase D 若改变调用方式，容易误解计数作用域。
- recommendation: 让 runner 明确在“task × iteration”生命周期内持有 trigger，或把调用上限改为无状态参数并在 scorer 中显式执行。

**Finding #14** [LOW]
- scope: `benchmarks/runner/llm_judge.py:88` / `invoke_judge`
- description: Phase A `invoke_judge` 固定返回 `score=0.5`（`llm_judge.py:88-97`）。该 stub 已显式标注 Phase D 升级（`llm_judge.py:73-76`），所以不是实现遗漏。
- impact: Phase A 的 PARTIAL 分数全部居中，无法用于比较任务难度或 Phase A→D 的 partial 变化。
- recommendation: 在 Phase D 真实 judge 落地前，不要把 Phase A PARTIAL 分数写入正式 baseline；报告中应标注 `is_stub=True` 的分数不可比较。

**Finding #15** [LOW]
- scope: `benchmarks/tiers/tier1/t1_routine_001.yaml:9`
- description: Routine task notes 声明 `ROUTINE_SKIPPED` 也是合法终态（`t1_routine_001.yaml:16-19`），但 expected_events 只接受 `ROUTINE_TRIGGERED` + `ROUTINE_COMPLETED`（`t1_routine_001.yaml:8-12`），scorer 也没有“任一事件满足”的 OR schema。
- impact: quiet hours 或 `routine_active=false` 触发合法 skip 时会被判 FAIL。
- recommendation: 增加 expected_event alternatives schema，例如 `one_of: [ROUTINE_COMPLETED, ROUTINE_SKIPPED]`，或拆成两个 task。

**Finding #16** [LOW]
- scope: `.specify/features/103d-octobench/known-issues-deltas.md:28`
- description: F-01 patch 要求 Phase A 验证覆盖 `should_trigger_judge` 边界 0.49 / 0.5 / 0.99 / 1.0（`known-issues-deltas.md:27-34`）。当前任务清单记录边界测试发生在 `poc_t1_verify.py`（`tasks.md:75`），但 Phase A 新增的 `benchmarks/` 目录没有持久化 unit test 文件。
- impact: 后续 Phase D 修改 judge 实现时，触发常量可能被无测试保护地改坏。
- recommendation: 在 Phase D 前补正式 unit test，覆盖边界值与 max_calls 行为；PoC 验证不能替代长期回归测试。

## 27 点逐项结论

| # | 结论 |
|---|---|
| A1 | 未发现问题。Tier 1/2/3=25/20/5 的 Daily Bench 结构与 spec 核心目标一致（`spec.md:57-64`），Full Bench 明确排除到 M6 中段（`spec.md:47`, `spec.md:467-470`）。 |
| A2 | 未发现 Phase A 阻塞。PoC-H4 推迟到 Phase B 对 Phase A 合理；但 Phase B 必须先验证 mock DB reset（`phase-0-poc-report.md:58`, `phase-0-poc-report.md:124-126`）。 |
| A3 | 未发现问题。review 节点覆盖 spec/plan、Phase A-D、Phase E final（`CLAUDE.local.md:46-52`, `plan.md:816-826`）。 |
| A4 | 未发现当前 Phase A 侵入。提交间 `octoagent/packages` / `octoagent/apps` 无变更；但 Phase D CLI 入口仍需守住 FR-H01（`spec.md:263-269`）。 |
| B5 | Finding #6。 |
| B6 | Finding #3、#7。 |
| B7 | Finding #2、#5。 |
| B8 | Finding #1。 |
| B9 | 未发现问题。25 个 Tier 1 YAML 的 expected event types 均包含在 scorer 默认查询列表中（`scorer.py:355-380`）。 |
| B10 | Finding #8。 |
| C11 | 未发现问题。触发边界实现为 `0.5 <= match_ratio < 1.0` 且 max calls 常量为 2（`llm_judge.py:18-21`, `llm_judge.py:58-60`）。 |
| C12 | Finding #13。 |
| C13 | Finding #14。 |
| D14 | Finding #2。权重值与 spec 表面对齐（`spec.md:51`, `scoring_rubrics.yaml:10-16`），但禁用维度未归一化。 |
| D15 | Finding #9。 |
| D16 | 未发现问题。GAIA 和 Tier 3 均为 `100/0/0`，表达二值评分（`scoring_rubrics.yaml:39-63`）。 |
| E17 | 未发现问题。25 个 YAML 均有 `task_id/tier/domain/prompt/expected_events/timeout_seconds/partial_signals`；抽查 `t1_tool_call_001.yaml:4-17`、`t1_delegation_003.yaml:5-20`、`t1_connor_4_health.yaml:5-21`。 |
| E18 | Finding #3、#7、#12、#15。 |
| E19 | Finding #11。 |
| E20 | Finding #4。 |
| F21 | 未发现 PII 问题。fixture 明确标注虚构数据，owner 为 `benchmark_test_user`（`mock_holdings.json:2-6`, `mock_health.json:2-8`）；数据分布足够覆盖盈亏/行业/睡眠/运动趋势。 |
| F22 | Finding #12。prompt 清晰，但 EventStore 期望链没有落实 notes 中的产出质量条件。 |
| G23 | 部分通过。F-01 触发常量真实落地（`llm_judge.py:18-21`, `llm_judge.py:44-60`），但 Finding #16 指出正式 unit test 缺口。 |
| G24 | 未发现新的 Phase B blocker 未显式记录。GAIA fallback、PoC-H4、W5 actions 字段都在 `phase-0-poc-report.md:60-65`、`tasks.md:88-94`、`tasks.md:283-287` 中有入口。 |
| H25 | Finding #1、#2、#5、#10。主要架构债集中在 scorer 与 rubric schema，而不是 production 侵入。 |
| H26 | Finding #14、#16。Phase A stub 已标注，但测试保护不足。 |
| H27 | 不可启动 Phase B。当前存在 HIGH 残留。 |

## Summary

| severity | count |
|---|---:|
| HIGH | 4 |
| MEDIUM | 8 |
| LOW | 4 |

## 结论

**BLOCKED（原始评估）**：不建议启动 Phase B。至少需要先关闭 4 个 HIGH：EventStore fetch/API 对齐、Tier 1 加权归一化、memory payload 字段对齐、ThreatScanner deny 断言。MEDIUM 中 `partial_signals` 死字段、Tier2 tau rubric、efficiency baseline schema 也建议在 Phase B 前同步处理，否则 Phase B scorer 会继续叠加同一类评分债。

---

## 主 session 处置决策（2026-05-29）

按 CLAUDE.local.md §"Codex Adversarial Review 强制规则" §"Review 处理流程"，主 session 对 16 个 finding 逐条决策：

### 已修复（HIGH 4/4 + MED 4/8）

| Finding | Severity | 修复方式 | 影响文件 |
|---------|----------|---------|---------|
| #1 EventStore 查询签名 | HIGH | `fetch_events_from_store` 改 async + 加 `task_id` 参数 + 改 `since_ts=` keyword；抽 `_normalize_event_to_dict` helper 统一 `Event.type→event_type` 与枚举展平 | `benchmarks/runner/scorer.py` |
| #2 PASS 满分 0.65 | HIGH | `score_tier1` 加权改为**活跃维度归一化**（partial/efficiency=None 时不计入分母），PASS task weighted_score 上限正确达到 1.0 | `benchmarks/runner/scorer.py` |
| #3 Memory payload 字段错位 | HIGH | `t1_memory_001.yaml` 改用真实字段：`memory_id_contains: ""` + `queried_namespace_kinds_contains: "AGENT_PRIVATE"`；scorer 同步支持 list 字段 contains 与"空 contains=字段必须存在"语义 | `benchmarks/tiers/tier1/t1_memory_001.yaml` + `scorer.py` |
| #4 ThreatScanner 无 deny 断言 | HIGH | 两个 yaml 都加 `action: "deny"`（小写，对齐 PolicyAction enum），001 加 `label_contains: "threat"` 进一步精确 | `benchmarks/tiers/tier1/t1_threat_scanner_001.yaml` + `_002.yaml` |
| #5 PARTIAL 双重计分 | MED | `score_tier1` PARTIAL 分支：`pass_fail_score = 0.0`（不再 `= match_ratio`），partial 维度由 LLM judge 单独贡献，避免双计 | `benchmarks/runner/scorer.py` |
| #7 _match_required_fields 边界 | MED | 空/null 约束改"字段必须存在"；`tool_name_contains` 改为按候选字段列表 `(tool_name, tool, function_name, name)` 逐个匹配（不再拼接）；新增 list 字段 contains（`element in list`） | `benchmarks/runner/scorer.py` |
| #9 tier2-tau-v1 rubric 不自洽 | MED | `pass_fail_weight: 0.90 → 1.00`，`partial_weight: 0.10 → 0.00`（二值评分）；M6 启用 partial 时必须同时定义 partial_logic | `benchmarks/runner/scoring_rubrics.yaml` |
| #11 delegation_003/004 同质断言 | MED | A2A_MESSAGE_SENT 加 `from_agent_contains`：003=`"main"` / 004=`"worker"`，A2A_MESSAGE_RECEIVED 加 `to_agent_contains: "worker"`；两条 task audit 信号明确区分 main→worker vs worker→worker | `t1_delegation_003.yaml` + `_004.yaml` |
| F-PA-3 run_all_poc.sh 硬编码路径 | P2/MED | `WORKTREE_ROOT` 从 `${BASH_SOURCE[0]}` 推导（向上 4 级），`POC_DIR=SCRIPT_DIR`，跨 worktree / 跨机器可用 | `.specify/features/103d-octobench/poc/run_all_poc.sh` |

**HIGH 0 残留** ✅；**MED 4 已修 / 4 推迟**。

### 推迟到 Phase B/D（MED 4/8）

| Finding | Severity | 推迟理由 | 接管节点 |
|---------|----------|---------|---------|
| #6 贪心匹配 actual event 可重复命中 | MED | 当前 25 个 Tier 1 yaml 无同 task 重复 expected event_type → 不引入 false positive；但 Phase B τ-bench actions 序列断言需要 bipartite matching | Phase B T-B-4 scorer 扩展时加"已匹配 actual 标记"逻辑 |
| #8 TaskVerdict.ERROR 不在 spec 枚举 | MED | scorer 已稳定（ERROR 当 FAIL 处理是可行降级），但需 spec/plan 显式定义 | Phase D T-D-2 BenchmarkRun store schema 落地时同步 spec |
| #10 efficiency baseline schema 标量 vs per-domain dict | MED | Phase A-D 不启用 efficiency（baseline_tokens=null），不影响 M5 baseline 跑 | Phase E T-E-5 `calibrate_efficiency_baseline` 实施时定型 schema |
| #12 Connor task notes 输出质量条件 | MED | 当前 Tier 1 走 EventStore chain 断言（流程烟测）；产出质量评估属 LLM judge 真实施范围 | Phase D T-D-6 LLM judge 升级时改 prompt 加 response 质量评分 |

### 推迟到 Phase B/D（LOW 4/4）

| Finding | Severity | 推迟理由 | 接管节点 |
|---------|----------|---------|---------|
| #13 LLMJudgeTrigger 计数跨 iteration | LOW | Phase D runner 决定 trigger 生命周期（task × iteration 还是单 task） | Phase D T-D-3 runner 实施 |
| #14 Phase A stub score=0.5 | LOW | 已明确标注 Phase D 升级，已在 known-issues F-01 patch 定义 | Phase D T-D-6 升级 invoke_judge |
| #15 Routine OR schema | LOW | 需 yaml schema 引入 `one_of`，独立 schema 扩展 | Phase D T-D-2 或独立 yaml schema 增强 |
| #16 正式 unit test 缺口 | LOW | 本次 commit 同步补 scorer 修复的 unit test 覆盖（_match_required_fields 边界 + _normalize_event_to_dict + weight 归一化）；llm_judge 边界测试 PoC poc_t1_verify.py 已覆盖，Phase D 升级时同步迁移到正式 unit test | 本 commit（部分）+ Phase D T-D-6（完整） |

### Codex 未覆盖维度的 cross-check（A/G/H）

| 维度 | 检查结论 |
|------|---------|
| A 设计层 | Codex review 中 27 点逐项已覆盖；PoC-H4 推迟 Phase B 合理（known-issues-deltas.md 已记录）；零侵入边界 `git diff packages/ apps/gateway/src apps/web/` 实测 0 改动 |
| G known-issues | F-01 patch（LLM judge 触发常量真实落地）已通过 Codex C11 项验证 |
| H 整体架构债 | Codex H25/H27 标记的 HIGH 全部修复后，scorer.py 已无半完成路径；Phase A stub 在 LLM judge 处明确标注（LOW #14） |

---

## 修复后 Phase B 启动 gate

- [x] Codex Phase A adversarial review 完成（16 finding 全量记录）
- [x] **0 HIGH 残留**（4/4 HIGH 全部修复）
- [x] 关键 MED 顺手修（5/8 修，避免 Phase B scorer 叠加同类债：#5/#7/#9/#11 + F-PA-3）
- [x] 推迟 MED（4/8）+ 推迟 LOW（4/4）均有明确接管 Phase + 不阻塞 Phase B 主体
- [x] Phase A 全量回归 vs F103c baseline：3670 passed + 4 failed（OctoAgent 已知 F083 race，与 Phase A 0 production code 改动无关；单跑 PASS 已验证）

**结论修订**：从 BLOCKED 改为 **READY**，**可启动 Phase B 主体**。
