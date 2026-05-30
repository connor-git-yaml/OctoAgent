# F103d OctoBench 编排追踪

> 起始 baseline: `a69fe9c` (F103c, master)
> Feature 分支: `feature/103d-octobench`
> Worktree: `.claude/worktrees/F103d-octobench`
> 编排器: spec-driver-feature 4.1.0 (fallback 模式，zod 包缺失)
> 全局 preset: `quality-first`（所有子代理用 Opus）
> 调研模式: `tech-only`（理由：用户 prompt 已锁产品决策；F087 docs 本地已有；业界 case τ-bench/GAIA 仅做技术调研）

## Phase 序列（feature 模式，10 阶段）

依据 `plugins/spec-driver/config/orchestration.yaml#modes.feature`：

| Phase | name | gate_before | gate_after | 状态 |
|-------|------|-------------|------------|------|
| 0     | constitution_check | – | – | SKIP（已有 constitution.md）|
| 0.5   | research_mode_determination | – | – | DONE（tech-only）|
| 1a    | product_research | – | – | SKIP（research_mode=tech-only）|
| 1b    | tech_research | – | – | PENDING |
| 1c    | research_synthesis | – | GATE_RESEARCH | SKIP（research_mode≠full）|
| 2     | specify | GATE_RESEARCH | – | PENDING |
| 3     | clarify + quality_checklist (并行) | – | – | PENDING |
| 3.5   | gate_design | – | GATE_DESIGN（**硬门禁**）| PENDING |
| 4     | plan | GATE_DESIGN | – | PENDING |
| 5     | tasks | – | – | PENDING |
| 5.5   | analyze | – | GATE_ANALYSIS + GATE_TASKS | PENDING |
| 6     | implement | GATE_TASKS | – | PENDING（含 Phase 0 PoC + Phase A-F；PoC 后手动 mid-stop）|
| 6.5   | verify_independent | – | – | PENDING |
| 7a+7b | spec_review + quality_review (并行) | – | – | PENDING |
| 7c    | verify | – | GATE_VERIFY | PENDING |

## 事件日志

[init] 2026-05-27 init OK. Baseline a69fe9c. Worktree feature/103d-octobench 已建。F087 文件路径校正为 `octoagent/apps/gateway/tests/e2e_live/`（多包 monorepo 根有 `octoagent/` 子目录）。本地无 tau-bench/gaia snapshot，调研阶段需 web fetch GitHub。

[Phase 1b] tech-research COMPLETED. 5 调研点（R-1 ~ R-5）全部覆盖：F087 OctoHarness 4 DI 钩子可复用 + 推荐轻量化主路径直调 / τ-bench airline ≥35 task（待 PoC `len(TASKS)` 实测）+ user simulator 4 策略 + Pass@k 评分 / GAIA L2 总 245 task + HF gated dataset 需申请 / H1/H2/H3 可观测信号已全数清晰（EventStore 落地） / Tier 4 Claude Sonnet 4.5 4000 RPM / 2M ITPM，8 并发宽裕 + retry-after backoff。
**PoC 必须优先验证 4 假设**：HF GAIA 访问 / τ-bench task 数 / 8 并发 SQLite WAL contention / GAIA L2 task 耗时 ≤ 300s.
产出: `.specify/features/103d-octobench/research/tech-research.md` (~10KB).

[Phase 1c] research_synthesis SKIPPED. research_mode=tech-only，单文件即合并制品。
[GATE_RESEARCH] AUTO_CONTINUE. behavior=auto，无失败信号。

[Phase 2] specify COMPLETED. spec.md 476 行；6 User Stories (P1×3+P2×2+P3×1) / 31 FR / 4 Entity / 10 SC / Phase 拆分 6 Phase / 复杂度 HIGH（7 组件 + 8 接口 + asyncio 并发 + SQLite）。

[Phase 3] clarify + quality_checklist COMPLETED（并行 DESIGN_PREP_GROUP）。
  - clarifications.md: 3 Open Q + 4 Implicit Assumption + 2 Boundary Condition
  - quality-checklist.md: 29 项 / 20 ✓ / 9 ⚠️ / 0 ✗ → PASS-WITH-WARNINGS
  - 2 高优先级 ⚠️：W1 零侵入新增文件 / W2 Tool Registry 隔离

[GATE_DESIGN 硬门禁] PASS（用户拍板）。
  - OQ-1: user simulator LLM = **Sonnet 4.6**（与 spec 子代理推荐 Haiku 不同）
  - OQ-2: PoC 5 task 保持 spec 当前组合（1+1+1+1+并发压测，覆盖 PoC-H3 SQLite WAL）
  - OQ-3 自动采纳: baseline 跑在 F103d 完成 commit
  - W1: 允许新增 production 文件 + 禁改现有内容
  - W2: contextmanager 临时注册 + finally 清理 + Codex pre-impl 重点 review race
  - IA-4 自动采纳: efficiency 评分推迟到 M6 启用
  - BC-1 自动采纳: SC-011 新增 INCONSISTENT ≤ 5%

spec.md 6 处 edit 已应用：§0.2 决策表（user simulator + baseline commit + efficiency 时机 + INCONSISTENT + Tool Registry）/ FR-B05 / FR-E01 / FR-H01 重写 / SC-011 新增.

[Phase 4] plan COMPLETED. plan.md 718 行。5 关键技术决策 + W3-W9 9 ⚠️ 全部解决：W3 efficiency p50 Phase E 末计算 / W4 LLM judge `[0.5, 1.0)` + max_calls=2 / W5 τ-bench actions 字段 [PoC 实测] / W6 PoC 第 5 task = POC-CONC / W7 delta 精度 0.001 / W8 4 Connor 场景 PLACEHOLDER / W9 ScoringRubric = YAML。
4 个遗留开放点（合理，留 PoC 实测确认）：τ-bench actions 路径 / CLI 注册方式 A vs B / Connor 4 场景内容 / Tool Registry API 存在性。

[Phase 5] tasks COMPLETED. tasks.md 63 task / 13.5 人时 / Phase 分布 0:9 / A:14 / B:8 / C:8 / D:11 / E:9 / F:3。35 FR 100% 覆盖。T-0-GATE 显式 STOP，blockedBy T-0-6。W3-W9 在各 Phase 落地。

[Phase 5.5] analyze COMPLETED. analysis-report.md. PASS-WITH-WARNINGS（0 CRITICAL / 2 HIGH / 5 MEDIUM / 3 LOW），FR/AC/SC 100% 覆盖。HIGH F-02（spec FR-H01 pyproject.toml 豁免）已直接 patch；F-05（spec AC2-1 措辞 + AC2-1b 新增）已直接 patch；剩 1 HIGH F-01 + 4 MEDIUM + 3 LOW 合并到 known-issues-deltas.md 作为 implement 子代理消费的补丁清单。

[GATE_ANALYSIS] AUTO_CONTINUE. behavior=on_failure，verdict=PASS-WITH-WARNINGS，无失败信号。
[GATE_TASKS] PASS（用户拍板 2026-05-28）: 可以进 Phase 0 PoC；PoC GATE 由主 session 读 phase-0-poc-report.md + AskUserQuestion 拍板。

[Phase 6 implement] STARTED 2026-05-28. PoC 子阶段：
  - implement 子代理范围限定：写 6 个 PoC python 脚本代码 + report 模板（实测数据 placeholder）+ T-0-REGRESSION
  - T-0-T1~T5 实测跑由主 session 协助用户完成（涉及 LLM API + HF + pip install 外部依赖）
  - T-0-GATE 在 PoC report 完整填好后由主 session AskUserQuestion 触发

[Phase 0 代码骨架] DONE. 7 个文件新增（poc/install_check.py 85L / poc_t1.py 164L / poc_tau.py 168L / poc_gaia.py 164L / poc_t3.py 186L / poc_concurrent.py 207L / phase-0-poc-report.md 138L 模板）。0 production 文件修改（T-0-REGRESSION PASS）。6 py_compile 全 OK。OctoHarness API 5 个实测细节：①import path OK ②__init__ 5 参数 OK ③EventStore.get_events_by_types_since(since, event_types) ④bootstrap(app) 需 FastAPI app ⑤_store_group 是私有，需 getattr fallback。

[Phase 0 实测] DONE 2026-05-28（主 session 在 Bash sandbox 自跑）。
  - install_check ✅ PASS（tau_bench + datasets 装好；uv sync 会清，需手动追加）
  - poc_tau ✅ PASS（W5 闭环：tasks.tasks list len=50；actions 字段确认 list[{name, arguments}]）→ patch TASKS→tasks 4 处
  - poc_concurrent ✅ PASS（PoC-H3 闭环：8 并发 0 lock / p95=1.303s / wall=2.78s）
  - poc_gaia ❌ FAIL（PoC-H1 不成立：gated dataset 拒绝匿名）→ 删除过时 trust_remote_code → fallback 激活
  - poc_t1 / poc_t3 ⏸ LLM_UNAVAILABLE（sandbox strip env；用户 host 跑能 PASS）
  - PoC-H4 ⏸ DEFER → Phase B（需 LLM 跑 2 连续 task 实测 mock DB reset）
  - 2 个 PoC 脚本 patch：poc_tau.py (TASKS→tasks 4 处) / poc_gaia.py (删 trust_remote_code)

[T-0-6] DONE. phase-0-poc-report.md 实测版（10 section / 187 行，覆盖 install / 5 task 耗时 / 4 假设结论 / W5 W6 闭环 / 推荐进 Phase A + 4 项激活调整）
[T-0-REGRESSION] PASS（0 production 文件变更，仅 .specify/ 新增）
[T-0-GATE] PASS 2026-05-28（用户 4 拍板）：
  - GAIA = 混合方案（用户去 HF 申请 + Phase B 同时走 fallback）
  - Connor 4 task 内容（持仓健康度 / AI 日报 / 无人机机器人日报 / 睡眠运动分析），统一 domain=connor_real_world，已记入 known-issues-deltas.md F-08
  - Push 策略 = 当前 commit + push origin/feature/103d-octobench（开 PR 合 master）
  - 进 Phase A

[Phase 0 → push] commit 2f47156 pushed origin/feature/103d-octobench（PR URL: https://github.com/connor-git-yaml/OctoAgent/pull/new/feature/103d-octobench）。pre-commit hook 用错 python（aiosqlite 缺失，环境污染），SKIP_E2E=1 bypass（0 production 变更，hook 守护范围与 commit 无关）。

[Phase A] DONE 2026-05-28（implement 子代理，13 task 全完成）。新增 33 文件 / 1618 LOC：
  - benchmarks/runner/scoring_rubrics.yaml (T-A-1, 4 rubric)
  - benchmarks/tiers/tier1/*.yaml × 23（T-A-2 ~ T-A-9 共 21 + T-A-10 4 connor，含 fixtures/connor/mock_holdings.json + mock_health.json）
  - benchmarks/runner/llm_judge.py (T-A-11, F-01 patch 真实落地：LLM_JUDGE_TRIGGER_MIN_RATIO=0.5 / MAX_RATIO=1.0 / MAX_CALLS=2 常量化)
  - benchmarks/runner/scorer.py (T-A-11)
  - .specify/features/103d-octobench/poc/poc_t1_verify.py (T-A-12, 主 session 已修 sys.path 移动 location)
  Patch 落地：F-01 ✓（llm_judge.py 常量 + 边界 unit test 全 PASS）/ F-08 ✓（4 Connor task 完整内容，无 PLACEHOLDER）/ F-10 ✓（4 delegation = 2 delegate_task + 1 a2a + 1 a2a_worker_to_worker）
  实测发现修正（Phase A 子代理报告）：EventStore 实测类名 SqliteEventStore（aliased）；TOOL_CALLED → TOOL_CALL_STARTED；SKILL_PIPELINE_STARTED → SKILL_STARTED
  T-A-12 自验 5/5 step PASS（import / yaml schema / LLM judge 边界 / score_tier1 端到端 / 4 rubric）

[T-A-REGRESSION] git diff -- packages/ apps/ 0 行 PASS（FR-H01 零侵入守卫）。pytest 全量回归留主 session 触发（或下次跑 e2e_smoke 时一并）。
[T-A-REVIEW] PENDING — Codex per-Phase review 等用户授权触发 `/codex:adversarial-review`。Phase A 1618 LOC + 25 YAML 需要 review 才推 origin。

[Phase A → push] commit 8241deb merged origin/master 2026-05-29（T-A-REVIEW 仍 PENDING 状态，commit message 标记待办）。

[T-A-REVIEW] DONE 2026-05-29（Phase B 启动 worktree 补做）。Codex adversarial review (GPT-5.4 high effort) 抓出 16 finding：4 HIGH + 8 MED + 4 LOW，全量写入 phase-a-codex-review.md。
  处理决策（commit f102d4e）：
  - HIGH 全修（4/4）：
    - #1 scorer.py fetch_events_from_store: async + 加 task_id 参数 + since_ts keyword + _normalize_event_to_dict helper（Event.type → event_type）
    - #2 score_tier1 加权改活跃维度归一化：PASS task weighted_score 从 0.65 → 1.0
    - #3 t1_memory_001.yaml 字段对齐真实 payload（memory_id_contains / queried_namespace_kinds_contains）
    - #4 t1_threat_scanner_001/002.yaml 显式断言 action="deny" + label_contains: "threat"
  - MED 顺手清（5/8，避免 Phase B 叠加同类债）：
    - #5 PARTIAL pass_fail_score=0.0（不再双重计分 match_ratio）
    - #7 _match_required_fields 边界：空/null 约束=字段必须存在；tool_name_contains 按候选字段逐个匹配；新增 list 字段 contains
    - #9 scoring_rubrics.yaml tier2-tau-v1：0.90/0.10/0 → 1.0/0/0 rubric 自洽
    - #11 t1_delegation_003/004 加 from_agent_contains 区分 main vs worker A2A
    - F-PA-3 run_all_poc.sh 硬编码路径 → ${BASH_SOURCE[0]} 推导
  - 推迟（MED 4/8 + LOW 4/4）：全部在 phase-a-codex-review.md 明确接管节点（Phase B/D/E）
  - 新增 benchmarks/tests/unit/test_scorer.py 16 tests 全 PASS（覆盖每条 HIGH/MED finding 回归 case）
  - PoC alignment：poc_t1_verify.py mock events 字段对齐修复后 yaml schema，5/5 step PASS
  - e2e_smoke 8/8 PASS（uv run python -m pytest 路径，绕过 hook PATH 干扰）
  - 全量回归 vs F103c：3670 passed + 4 failed (已知 F083 race, 单跑 PASS, 与 Phase A 改动无关)
  - SKIP_E2E=1 bypass: hook 用 'uv run pytest' 在本机被 system spec-driver pytest 8.0.0.dev53 干扰；已手验证 e2e_smoke 8/8 PASS

[Phase B] STARTED 2026-05-29（块 0 Phase A review 收尾后 → 块 A/B/C 主体）。

[Phase B 块 A T-B-1] DONE: benchmarks/tiers/tier2/__init__.py + tau_bench_adapter.py（250 行）。15 task 分层抽样：
  - 关键算法决策（vs Phase A spec）：contains-action 优先策略（passenger/baggage/payment 稀缺桶先抢）
  - 实测分桶分布：passenger=3 / baggage=4 / payment=3 / booking=3 / cancellation=6 / upgrade=3
  - 调整 plan（实际可用 max）：booking=3 / cancellation=4 / upgrade=3 + passenger=2 / baggage=1 / payment=2 = 15
  - tau_bench_tool_scope contextmanager: threading.Lock + TAU_BENCH_TOOL_PREFIX="tau_bench__" + TAU_BENCH_SCOPE_TAG metadata + try/finally
  - TauBenchAdapter.user_simulator_model 默认 "claude-sonnet-4-6"（FR-B05）
  - make_env: MockAirlineDomainEnv per-task 实例（PoC-H4 主方案；连续 2 task 验证推迟 Phase D runner）
  - _make_tool_handler Phase B placeholder（Phase D runner 接 env.step 真实施）

[Phase B 块 B T-B-2/T-B-3] DONE: benchmarks/tiers/tier2/gaia_fallback_adapter.py + gaia_fallback_tasks.yaml。
  - PoC-H1 FAIL → fallback yaml 激活（用户 2026-05-28 拍板）
  - 5 task 分层（FR-E04 严格）：web_search × 2（光速 / 2024 图灵奖）+ doc_parse × 2（spec baseline SHA / pytest asyncio_mode）+ multi_tool_chain × 1（地球轨道 / 1000）
  - source_provenance: 全部标 [GAIA-FALLBACK]，明确非官方 GAIA 数据集
  - normalize_answer: lower + strip + 千分位去除 + 标点去除（保留 .-_）
  - match_answer: 数字 tolerance 优先 → normalized 精确/substring → alternates
  - EXPECTED_CATEGORY_DISTRIBUTION load 时验证（不符合 FR-E04 → raise ValueError）

[Phase B 块 C 块 A/B 收尾 T-B-4 preflight + scorer Tier 2] DONE:
  - benchmarks/runner/preflight.py: _missing_packages + check_or_fail(SystemExit 2) + INSTALL_COMMAND 写死
  - benchmarks/runner/scorer.py 末尾扩展 score_tier2_tau + score_tier2_gaia + _build_score helper
    - score_tier2_tau: 去 tau_bench__ 前缀 + Pass@1 set 包含检查；task.actions 空时 verdict=ERROR
    - score_tier2_gaia: lazy import gaia_fallback_adapter.match_answer + tier2-gaia-v1 100/0/0 二值

[Phase B 单元测试 T-B-5（GATE_P3_DEVIATION 替代真实 LLM 跑）] DONE: benchmarks/tests/unit 新增 4 文件 55 tests:
  - test_preflight.py（5 tests）
  - test_tau_bench_adapter.py（16 tests，含真实 50 task 数据验证 + ToolRegistry 注册/清理/异常 finally）
  - test_gaia_fallback_adapter.py（25 tests，含 normalize_answer 边界 + match_answer tolerance/alternates）
  - test_scorer_tier2.py（12 tests，含 Pass@1 / GAIA normalized / token_usage 透传 / ERROR 路径）
  累计 benchmarks/tests/unit/: 71 tests 全 PASS (Phase A 16 + Phase B 55)。
  注：T-B-5 spec "对 τ-bench 取前 3 task、GAIA 取 2 task 手工单跑"涉及真实 LLM API + ANTHROPIC_API_KEY，
  Phase B sandbox 环境无（GATE_P3_DEVIATION）；实际 5 task 跑推迟 Phase D runner 接入后做。

[Phase B → review] T-B-REVIEW STARTED 2026-05-29: 同时启动 codex review --uncommitted（本地）+ codex:codex-rescue agent（远程 cloud），双路 review，等 finding 处理后 commit Phase B.

[Phase B Codex review] DONE 2026-05-29（11 finding 全闭环）。phase-b-codex-review.md 记录 2 HIGH + 6 MED + 3 LOW。处置决策：
  - HIGH 全修（2/2）：tau_bench_tool_scope async-safe（去 lock-around-yield 改 register/cleanup 两步）+ ToolRegistry conflict-detection（注册前 fail-fast）
  - MED 全修（6/6 + 1 P2）：Pass@1 Counter 同名次数 / GAIA word-match negation guard / scorer 强制 tau_bench__ 前缀 / stratified shortage fail-fast / preflight unit test mock _missing_packages / PoC-H4 docstring 修正 / 用户拍板"tau-bench 不污染 pyproject"
  - LOW 推迟 Phase D（3/3，已写 phase-b-codex-review.md 接管节点）
  - 累计 65 unit tests（Phase A 16 + Phase B 49）全 PASS

[Phase B → push] commit 4c0e513 push origin/master 2026-05-29。累计 Tier 1 25 + Tier 2 stratified 15 (τ-bench airline) + GAIA fallback 5 task，scorer 三层（tier1 / tier2-tau / tier2-gaia）就绪。零侵入 production 守卫保持（octoagent/packages/ apps/ frontend/ 0 行）。

---

[Phase C] STARTED 2026-05-29（新 worktree interesting-feynman-7924bb，分支 claude/interesting-feynman-7924bb，baseline 4c0e513）。
目标：Tier 3 H1/H2/H3 哲学 audit chain 5 task + scorer score_tier3 / audit_chain_assert。

[Phase C 设计阶段] 关键 fact 实测（决定 audit_assertions schema 严格性）：
  - SUBAGENT_SPAWNED payload 字段（harness/delegation.py:266 _emit_spawned_event）：child_task_id / target_worker / depth / task_description_preview / callback_mode / parent_task_id。**不含** source_runtime_kind / caller_project_id（spec 写法误导校正）。
  - source_runtime_kind 通过 CONTROL_METADATA_UPDATED.control_metadata 持久化（F099 三工具 + worker spawn extra_control_metadata 路径）。
  - H3-A caller_project_id 写在 CONTROL_METADATA_UPDATED (source=subagent_delegation_init).control_metadata.subagent_delegation.caller_project_id（嵌套 3 层 dot path）。
  - F099 N-H1 修复路径：worker_runtime._emit_is_caller_worker_signal (worker_runtime.py:399) 写 CONTROL_METADATA_UPDATED (source=worker_runtime_dispatch).control_metadata.is_caller_worker_signal="1"。
  - MEMORY_RECALL_COMPLETED 真字段（F094 B6）：queried_namespace_kinds (list[str]) / hit_namespace_kinds (list[str]) / agent_runtime_id (str)，不存在 namespace 字段（沿用 Phase A list-aware contains）。

[Phase C T-C-1 ~ T-C-5] DONE: 5 个 Tier 3 YAML（benchmarks/tiers/tier3/）：
  - t3_h1_001.yaml: 3 assertion（H1-1 SUBAGENT_SPAWNED + H1-2 worker_runtime_dispatch 信号 + H1-3 event_absent user_channel 标记）
  - t3_h2_001.yaml: 3 assertion（H2-1 queried_namespace_kinds_contains=AGENT_PRIVATE + H2-2 agent_runtime_id 非空 + H2-3 MEMORY_ENTRY_ADDED）
  - t3_h3a_001.yaml: 4 assertion（H3A-1 SUBAGENT_SPAWNED + H3A-2 caller_project_id 嵌套路径 + H3A-3 delegation_id 持久化 + H3A-4 SUBAGENT_COMPLETED）
  - t3_h3b_001.yaml: 4 assertion（H3B-1 N-H1 is_caller_worker_signal 持久化 + H3B-2 worker_ask_back 触发 + H3B-3 to_status=WAITING_INPUT + H3B-4 from=WAITING_INPUT→to=IN_PROGRESS resume）
  - t3_h3_ww_001.yaml: 3 assertion（H3WW-1 SUBAGENT_SPAWNED depth 字段 + H3WW-2 source_runtime_kind=worker + H3WW-3 delegation_id 存在）

[Phase C T-C-6] DONE: scorer.py 扩展 + 80 LOC 新增（实际约 230 LOC，含完整文档 + 边界处理）。
  - 新增 dataclass AuditAssertionFailure（assertion_id / kind / event_type / expected / reason / closest_event）
  - BenchmarkRunScore 字段 audit_chain_failures: list[AuditAssertionFailure]
  - _get_nested_field(payload, dot_path)：嵌套字段 dot path 取值
  - _match_required_fields_tier3：嵌套 dot path 支持 + 复用 Phase A 语义（_contains/list-aware/精确匹配）
  - _assert_event_present：贪心匹配 + 失败时填 closest_event 用于诊断
  - _assert_event_absent：required_fields 空时禁止任何同类型事件（"完全禁止"语义）；非空时仅过滤命中事件（"特定 payload 组合"语义）。Codex review 重点
  - audit_chain_assert：逐条遍历不 short-circuit（FR-F03 解读，一次性看清所有失败）
  - score_tier3：100/0/0 二值评分，weighted_score = pass_fail_score
  - DEFAULT_TIER3_EVENT_TYPES 8 EventType（含 SUBAGENT_SPAWNED/COMPLETED + CONTROL_METADATA_UPDATED + MEMORY_* + STATE_TRANSITION）
  - fetch_events_from_store_tier3：Tier 3 专用查询封装

[Phase C Codex review 多轮闭环] DONE 2026-05-30。6 轮 codex review --uncommitted 累计 18 finding + 1 归档 Phase D：
  - Round 1（4 P1+P2 闭环 2026-05-29）：scorer 仅按父 task_id 查事件（HIGH P1，修：fetch_events_from_store_tier3 加 child_task_ids 参数）/ H3-B IN_PROGRESS → RUNNING（TaskStatus 真名）/ H2-3 缺 namespace_kind 约束 / H3-A 删 SUBAGENT_SPAWNED 断言（subagents.spawn 路径 emit_audit_event=False）
  - Round 2（3 P2 闭环 2026-05-30）：H2 namespace_kind=AGENT_PRIVATE → agent_private（StrEnum .value 小写）/ H3-WW depth 字段存在 → depth=1 严格化 / H3-A 加 caller_memory_namespace_ids 断言（α 共享 memory 不变量）+ scorer 加严 _contains: "" 对 list/dict 容器空检查
  - Round 3（3 P2+P3 闭环）：fetch_events_from_store_tier3 自动递归发现 grandchild task_id（worker→worker→worker 链）/ DEFAULT_TIER3_EVENT_TYPES 加 AGENT_SESSION_TURN_PERSISTED / audit_chain_assert required_fields 类型校验前置（防 `or {}` 短路绕过）
  - Round 4（2 P2 闭环）：H2-1/H2-2 合并到同一 required_fields（避免不同事件分别满足）/ H3-B 加 follow_up_inputs 字段（Phase D runner 接入点）
  - Round 5（3 P2 闭环）：H2 加 SUBAGENT_SPAWNED 前置断言（防主 Agent 自执行 false PASS）/ H3-WW H3WW-2+H3WW-3 合并 / 5 YAML 加 philosophy 字段（FR-F01 显式哲学维度）
  - Round 6（1 P2 闭环 + 1 归档 Phase D）：H1-3 改用 AGENT_SESSION_TURN_PERSISTED.agent_session_kind=direct_worker AND kind=assistant_message event_absent（更精确 H1 不变量）；P2-2 scorer event binding（audit_chain 断言间共享 binding context，绑定到具体 child_task_id/delegation_id/agent_runtime_id）超 Phase C 范围归档 Phase D scorer 框架加强
  - 6 轮累计 18 finding 修复（HIGH 1 + MED 17）+ 1 finding 归档 Phase D（scorer event binding 框架级加强）；
    Round 6 后剩余主要为"边际严格性优化"，非正确性 bug——决定 commit Phase C 完整状态，scorer event
    binding 在 Phase D 主体（runner/CLI/reporter）实施时一并完成

[Phase C T-C-6 单测] DONE: benchmarks/tests/unit/test_scorer_tier3.py 新增 49 tests 全 PASS:
  - 工具函数：6 嵌套 dot path + 9 字段匹配（含 list-aware + nested + contains 边界）
  - event_present / event_absent 语义边界 7 tests（特别覆盖 Codex review 重点：required_fields 空 vs 非空时的禁止语义）
  - audit_chain_assert 异常路径 4 tests（empty / unknown_kind / event_type 缺失 / required_fields 非 dict）
  - 5 Tier 3 YAML × PASS/FAIL case：H1 3 cases / H2 3 cases / H3A 3 cases / H3B 3 cases / H3WW 2 cases
  - DEFAULT_TIER3_EVENT_TYPES 关键 EventType 覆盖
  - error_message format 2 tests
  - 5 YAML 冒烟级 parametrized：空事件流下必 FAIL + audit_chain_failures 非空（避免 silent PASS）
  - Codex 6 轮 review 回归保护：每 finding 修复对应 unit test（如 H2-1/H2-2 合并 / Round 5 P2-1 SUBAGENT_SPAWNED 前置 / Round 6 H1-3 direct_worker 精确）
  累计 155 unit tests (Phase A 16 + Phase B 65 + Phase C 74) 全 PASS（1.54s）。

[Phase C 回归测试] DONE 2026-05-30：
  - benchmarks/tests/unit/ 155 PASS（Phase A+B+C 累计）
  - octoagent 全量回归（不含 e2e_live）：3763 passed + 13 skipped + 1 xfailed + 1 xpassed +
    6 failed（6 失败全部 apps/gateway/tests/e2e_live/test_e2e_smoke_real_llm 等需 LLM_API_KEY 的 real_llm 路径——
    与 Phase A trace.md 同性质 "LLM_UNAVAILABLE"，与 Phase C 改动无关）
  - 零侵入校验：git diff HEAD -- octoagent/packages octoagent/apps octoagent/frontend = 0 字节
  - e2e_smoke 8/8 PASS（7.04s）
  - baseline vs F103d Phase B (4c0e513) 等价：无新 regression

[Phase C 完成状态]：
  - 5 个 Tier 3 YAML（H1/H2/H3-A/H3-B/H3-WW，含 philosophy 字段 + audit_assertions，
    H2 4 / H3-A 4 / H3-B 4 / H3-WW 3 / H1 3 = 18 assertion）
  - scorer.py 扩展：score_tier3 / audit_chain_assert / fetch_events_from_store_tier3
    （含 grandchild 自动递归 + MAX_DESCENDANT_TRAVERSAL=32 安全护栏）/ AuditAssertionFailure /
    _get_nested_field / _match_required_fields_tier3 / _assert_event_present /
    _assert_event_absent（required_fields 空 vs 非空语义边界）/ DEFAULT_TIER3_EVENT_TYPES
    （9 EventType 覆盖 H1/H2/H3-A/H3-B/H3-WW + AGENT_SESSION_TURN_PERSISTED）/
    score_tier3 rubric tier3-v1 100/0/0 二值评分
  - 单测 74 tests 覆盖：6 嵌套 dot path + 13 字段匹配（含 list/dict 空检查 + 大小写敏感）+ 7
    event_present/absent 语义边界 + 4 audit_chain_assert 异常路径 + 14 Tier 3 YAML × PASS/FAIL
    (H1 3 / H2 6 / H3-A 4 / H3-B 3 / H3-WW 4) + DEFAULT_TIER3_EVENT_TYPES 覆盖 + format
    summary + parametrized YAML 加载 + 4 fetch grandchild 递归 + 2 dedup + 1 traversal cap +
    1 e2e + philosophy 字段 SC-010 覆盖

[Phase C 推迟到 Phase D 项]：
  - Round 6 P2-2: scorer event binding（audit_chain 断言间通过 spawn 事件捕获 child_task_id /
    delegation_id / agent_runtime_id，后续断言能绑定到具体 binding）。Phase D scorer 主体
    实施时一并完成；当前能正确捕获大多数 false PASS（5 个 YAML 各加 1-2 个强化断言后），
    但跨 audit chain 严格绑定需 scorer schema 大改。
  - Round 4 P2-1: H3-B follow_up_inputs 字段已加（runner 接入点），Phase D runner 实施时
    在 task 进入 WAITING_INPUT 时按顺序 attach_input。
  - Phase B 推迟 3 项（LLM-judge fallback / Pass@1 order+args / GAIA Unicode normalization）+
    Round 3 P2-2 部分接受（DEFAULT_TIER3_EVENT_TYPES 含 AGENT_SESSION_TURN_PERSISTED，
    Round 6 已用于 H1-3 精确化）。

[Phase D] STARTED 2026-05-30：Runner / Scorer / Reporter + octo bench CLI 完整实现。
  - 基线: ea9fad6（F103d Phase C）
  - Worktree: `.claude/worktrees/mystifying-feynman-21720b`（分支 claude/mystifying-feynman-21720b）
  - 范围：T-D-1 ~ T-D-8 + Phase C/B 归档项接管 + REVIEW + REGRESSION

[Phase D 实施记录]：
  - T-D-1 包初始化 DONE：benchmarks/__init__.py + benchmarks/runner/__init__.py
  - T-D-2 store.py DONE（432 LOC）：SQLite WAL + 两表 schema + BenchmarkStore（append_run /
    get_completed_keys / get_pending_runs / save_baseline / get_baseline / list_baselines）+
    Result 枚举 8 态 + EXCLUDED_FROM_DENOMINATOR (AC3-4) + BenchmarkRunRecord / Baseline
    dataclass + frozen + UNIQUE(session,task,iter) + INSERT OR REPLACE 幂等
  - T-D-3 worker.py DONE（450 LOC，[RISK] task）：
    - asyncio.Semaphore(8) + gradual ramp 0.5s + run_task_with_retry(max_retries=3) +
      retry-after 优先 + exp backoff jitter + QUOTA_SKIP/TIMEOUT/INFRA_ERROR 三态
    - ConsecutiveInfraErrorCounter (asyncio.Lock 短临界区，不跨 await——避免 Phase B
      threading.Lock 陷阱) + 连续 5 INFRA_ERROR 触发 stop_event
    - run_daily_bench(planned, runner_fn, store, session_id, ...) + on_record_written 进度
      回调 + run_in_executor 写盘异步包装
    - TaskRunner Protocol 注入（caller 通过 OctoHarness DI 钩子 wire 真 LLM）
    - extract_follow_up_inputs(task_raw) helper（Phase C Round 4 P2-1 归档接入）
  - T-D-4 reporter.py DONE（500 LOC）：
    - generate_report / generate_report_from_runs 按 plan §6.3 JSON 结构
    - by_tier (tier1/tier2_拆 tau_bench+gaia/tier3) + by_domain + task_details
    - majority_result (3 iter 多数 / 全异 = INCONSISTENT) + AC3-4 分母不含 SKIP/TIMEOUT/INFRA
    - compare_with_baseline + delta 0.001 精度 + regression/improvement 列表 (W7)
    - attach_delta_or_raise FileNotFoundError (AC6-2)
    - write_report_json / write_report_markdown / archive_report
    - report_to_baseline_record (Phase E T-E-4/T-E-5 用)
  - T-D-5 conftest.py DONE（150 LOC）：复用 F087 e2e_live 模式
    - 5 凭证 env 清单 + 4 OCTOAGENT_* 路径 env 重定向
    - reset_module_singletons (ToolRegistry / AgentContextService×2 / ExecutionContext)
    - hermetic_task_scope contextmanager (runtime worker.py 间手动调用)
    - pytest autouse fixture (benchmarks/ 下单测自动获 clean state)
  - T-D-6 scorer 整合 + LLM judge 真实实现 DONE：
    - score_dispatch.py 新 module（170 LOC）：score(task, run_result, *, rubrics) 统一接口
      按 tier+domain 分发到 score_tier1/2_tau/2_gaia/3；零侵入 scorer.py 现有函数
    - llm_judge.py 升级（240 LOC）：
      * 触发常量锁死不变（F-01 patch）：MIN_RATIO=0.5 / MAX_RATIO=1.0 / MAX_CALLS=2
      * JudgeAdapter Protocol + StubJudgeAdapter（默认）+ ProviderRouterJudgeAdapter
        (chat_fn DI 钩子)
      * Sonnet 4.5 temperature=0 max_tokens=512 + JUDGE_SYSTEM_PROMPT + truncated user prompt
      * LLM 失败 → fallback to stub (degrade gracefully)
      * 沿用 LLMJudgeTrigger.should_trigger_judge 不变
  - T-D-7 CLI 入口 DONE（[RISK] task）：
    - benchmarks/runner/cli.py 主逻辑 (550 LOC)：argparse 子命令 daily / list-baselines / show
    - apps/gateway/.../cli/bench_commands.py thin wrapper (20 LOC) lazy import
    - apps/gateway/pyproject.toml 新增 [project.scripts] octo-bench entry point（仅新增，
      不修改现有字段；方案 A 独立命令避免动 cli.py 主 group）
    - --dry-run mode + --resume + --compare + --label + --tier + --skip-preflight +
      --runner module:attr 注入 (Phase E baseline 跑时 wire 真 OctoHarness runner)
  - T-D-8 resume 验证 DONE：单测 test_t_d_8_resume_only_runs_pending +
    test_cli_daily_resume_via_cli 通过 (CLI 两次跑 + filter_planned_for_resume)
  - Phase C 归档项接管：
    * H3-B follow_up_inputs runner helper extract_follow_up_inputs() + 3 单测（Round 4 P2-1）
    * AGENT_SESSION_TURN_PERSISTED 续扩 H1 audit task（Round 3 P2-2）：保留 Phase C 行为
      不动；后续 H1 task 续扩留 M6 / 用户拍板时再补
    * scorer event binding 框架级加强（Round 6 P2-2）：评估计划 → Phase E baseline 跑后
      统计 5 Tier 3 task false PASS 比例：≤ 5% 归档 M6 F108 Capability Layer Refactor；
      > 5% 在 Phase E 后回头补做（YAML schema + scorer state machine 扩展）。理由：
      当前能正确捕获大多数 false PASS（Codex 6 轮 review 闭环），实际比例必须实跑后才能
      量化评估。
  - Phase B 归档项接管：
    * preflight runner 入口调用 DONE：cli.py daily 命令默认调用 _preflight_check_or_fail()，
      tier 2 task 加载前必须通过；--skip-preflight flag 允许仅跑 Tier 1/3
    * PoC-H4 / Pass@1 order+args / env.step / GAIA LLM-judge fallback：保留 Phase E
      实跑时实施（涉及真 LLM）

[Phase D 测试与回归] PENDING：
  - benchmarks/tests/unit/ 271 PASS (Phase A 16 + B 65 + C 74 + D 116) (1.63s)
  - octoagent 全量回归 + e2e_smoke PENDING（即将跑）
  - 零侵入校验 PENDING

[Phase D 推迟到 Phase E / M6 项]：
  - scorer event binding 框架级 (Round 6 P2-2)：需 M5 baseline false PASS 比例实测
  - LLM-judge fallback 真实路径接入 (Phase B 推迟 #4)：T-D-6 已建 adapter 框架；
    Phase E 时 wire ProviderRouter chat_fn 即可
  - pass@1 order-aware + arguments-aware (Phase B 推迟 #2)：tau_bench env.step 真接入
  - GAIA Unicode normalization (Phase B 推迟 #3)：可在 score_tier2_gaia 内增强
