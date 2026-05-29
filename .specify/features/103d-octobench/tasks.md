# F103d — 任务清单（Tasks）

> 上游：spec.md / plan.md
> Phase 顺序：0（PoC）→ A → B → C → D → E → F
> Codex review：pre-impl（plan 完成后）+ 每 Phase A/B/C/D 末 + Phase E 末 Final cross-Phase

---

## 元数据

| 字段 | 值 |
|------|----|
| feature | F103d OctoBench |
| spec_ref | `.specify/features/103d-octobench/spec.md` |
| plan_ref | `.specify/features/103d-octobench/plan.md` |
| created | 2026-05-27 |
| baseline_commit | `a69fe9c`（F103c 收尾后） |
| 总 task 数 | 63 |
| 总估算时长 | ~13.5 人时（不含 Phase E M5 baseline 实际跑耗时） |

---

## Phase 0 — PoC（目标：风险验证，≤ 1 天）

**目标**：手工验证 4 个关键假设，产出决策报告，等用户拍板后进 Phase A。
**关联 FR**：FR-G01, FR-G02, FR-H01 / **关联 AC**：AC2-1, AC2-2, AC2-3

- [x] **T-0-1** [新增独立文件] 安装验证脚本：在 `.specify/features/103d-octobench/poc/` 下新建 `install_check.py`，验证 `tau-bench` pip 可安装（PyPI 或 git+URL）+ `datasets>=2.0` 安装状态；输出安装成功/失败及版本。**新增文件，不修改任何现有文件。** 估算：0.3h / 20 LOC。关联 FR-G02, IA-3（W6 安装前提）。

- [x] **T-0-T1** [新增独立文件] PoC 单 Tier 1 task 手工脚本：在 `.specify/features/103d-octobench/poc/poc_t1.py` 下，用 OctoHarness DI 钩子（独立 `data_dir=tmpdir`）手工执行 1 个基础工具调用 task，记录 wall clock 耗时 + EventStore 查询 `MEMORY_ENTRY_ADDED` 是否存在。**新增文件，不修改现有文件。** 估算：0.5h / 40 LOC。关联 AC2-1, FR-D02。

- [x] **T-0-T2-TAU** [新增独立文件, RISK] τ-bench adapter PoC 脚本：在 `.specify/features/103d-octobench/poc/poc_tau.py`，实测 `len(tau_bench.envs.airline.tasks.TASKS)`（验证 PoC-H2）；执行 `tasks.TASKS[0]`；`print(vars(task))` 确认 `actions` 字段名（W5 待 PoC 实测）；记录耗时。**新增文件。** 估算：0.5h / 50 LOC。关联 AC2-1, FR-E01, W5。[RISK] τ-bench API 与 Tool Registry 接口待确认，若 `deregister` 不存在记入报告。

- [x] **T-0-T3-GAIA** [新增独立文件, RISK] GAIA PoC 脚本：在 `.specify/features/103d-octobench/poc/poc_gaia.py`，`load_dataset("gaia-benchmark/GAIA", split="validation")` 验证 HF 访问（PoC-H1）；取 1 个 Level 2 task 执行；记录耗时。若 HF 访问失败则记录降级方案激活（arxiv fallback）。**新增文件。** 估算：0.5h / 40 LOC。关联 AC2-1, FR-E03, PoC-H1。[RISK] P0 阻塞项，HF gated dataset 访问申请可能未通过。

- [x] **T-0-T4-PHILOSOPHY** [新增独立文件] Tier 3 H1 PoC 脚本：在 `.specify/features/103d-octobench/poc/poc_t3.py`，手工执行 1 个 H1 哲学 task（委托 Worker 处理），查询 EventStore `SUBAGENT_SPAWNED` 存在；记录 audit 信号可查路径。**新增文件。** 估算：0.5h / 40 LOC。关联 AC2-1, FR-F01, R-4。

- [x] **T-0-T5-CONC** [新增独立文件] 并发压测 PoC 脚本：在 `.specify/features/103d-octobench/poc/poc_concurrent.py`，用 `asyncio.gather` 并行启动 8 个 OctoHarness 实例（各独立 `tmpdir`），测量 SQLite WAL p95 latency（W6 / PoC-H3）；验证无 `DB locked` 错误。**新增文件。** 估算：0.5h / 60 LOC。关联 AC2-1b, FR-A01, PoC-H3, W6。

- [x] **T-0-6** [新增独立文件] 产出 PoC 报告：填写 `.specify/features/103d-octobench/phase-0-poc-report.md`（按 plan §2.4 模板），含 5 task 实测耗时、PoC-H1~H4 成立结论、降级方案状态、τ-bench actions 字段实测结果（W5）、推荐下一步。估算：0.3h / 60 LOC（文档）。关联 FR-G01, AC2-2, SC-002。blockedBy：T-0-1, T-0-T1, T-0-T2-TAU, T-0-T3-GAIA, T-0-T4-PHILOSOPHY, T-0-T5-CONC。

- [ ] **T-0-GATE** **[必须停止，等用户拍板]** PoC mid-implement GATE：implement 工程师执行到此任务后**必须停止**，不得继续进入 Phase A。主 session 通过 AskUserQuestion 让用户读取 `phase-0-poc-report.md` 后决定：① 所有假设成立 → 进入 Phase A-F；② 任一 P0 假设不成立 → 等用户决策降级方案后再进。blockedBy：T-0-6。**[GATE]**

- [x] **T-0-REGRESSION** 零侵入验证：执行 `git diff HEAD -- packages/ apps/gateway/src apps/web/`，期望 0 文件变更（Phase 0 全部为新增 poc/ 目录下临时脚本）。blockedBy：T-0-6。

---

## Phase A — Tier 1（目标：25 private task YAML + EventStore scorer）

**目标**：完成 25 个 Tier 1 task YAML 定义 + scorer 初始版 + ScoringRubric YAML。
**关联 FR**：FR-D01, FR-D02, FR-D03, FR-B01, FR-B02, FR-B03 / **关联 AC**：AC1-1（前置）, AC4-1（前置）

- [ ] **T-A-1** [新增文件，W9] 新建 `benchmarks/runner/scoring_rubrics.yaml`（初始版），含 4 个 rubric（tier1-v1 / tier2-tau-v1 / tier2-gaia-v1 / tier3-v1），`efficiency_baseline_tokens: null`（Phase E 末填入）。格式严格按 plan §3.1 草稿。**新增文件，不修改现有文件。** 估算：0.2h / 50 LOC。关联 FR-B02, W3, W9。

- [ ] **T-A-2** [新增文件，P 可并行] 新建 `benchmarks/tiers/tier1/` 目录 + `t1_tool_call_*.yaml` × 3（基础工具调用域）。每文件含：`task_id` / `tier:1` / `domain:tool_call` / `prompt` / `expected_events`（MEMORY_ENTRY_ADDED 等）/ `timeout_seconds:300` / `partial_signals`。**新增文件。** 估算：0.3h / 60 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-3** [新增文件，P 可并行] 新建 `t1_user_md_*.yaml` × 3（USER.md 全链路域：读写 + 观测）。**新增文件。** 估算：0.3h / 60 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-4** [新增文件，P 可并行] 新建 `t1_snapshot_*.yaml` × 2（冻结快照域：SnapshotStore 读写）。**新增文件。** 估算：0.2h / 40 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-5** [新增文件，P 可并行] 新建 `t1_memory_*.yaml` × 3（Memory 域：promote / recall，含 MEMORY_ENTRY_ADDED + MEMORY_RECALL_COMPLETED 断言）。**新增文件。** 估算：0.3h / 60 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-6** [新增文件，P 可并行] 新建 `t1_skill_*.yaml` × 2（Skill Pipeline 域：DAG 触发）。**新增文件。** 估算：0.2h / 40 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-7** [新增文件，P 可并行] 新建 `t1_delegation_*.yaml` × 4（委托域：delegate_task + A2A，含 SUBAGENT_SPAWNED 断言）。**新增文件。** 估算：0.3h / 80 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-8** [新增文件，P 可并行] 新建 `t1_max_depth_001.yaml` × 1（max_depth 限制域）+ `t1_routine_001.yaml` × 1（Routine cron 触发域）。**新增文件。** 估算：0.2h / 40 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-9** [新增文件，P 可并行] 新建 `t1_threat_scanner_*.yaml` × 2（ThreatScanner block 安全拦截域）。**新增文件。** 估算：0.2h / 40 LOC。关联 FR-D01, FR-D03。

- [ ] **T-A-10** [新增文件，W8] 新建 Connor 真实场景 PLACEHOLDER × 4（`t1_connor_{1-4}.yaml`），状态 `status: PLACEHOLDER`，prompt 填 `"[CONNOR-SCENE-{N}: 待 PoC 后用户确认场景内容]"`，`expected_events: []`。**新增文件。** 估算：0.2h / 80 LOC。关联 FR-D01, FR-D03, W8，SC-005。blockedBy：T-0-GATE（需用户拍板场景内容后填入，此处先建 PLACEHOLDER）。

- [ ] **T-A-11** [新增文件] 新建 `benchmarks/runner/scorer.py` Tier 1 初始版：EventStore query 断言逻辑（`event_store_assert`）+ LLM judge stub（W4：触发条件 `0.5 <= match_ratio < 1.0`，`max_calls=2`，Phase A 为 stub 实现）。定义 `score_tier1(task, run_result, event_store) -> BenchmarkRunScore` 接口（类型签名）。**新增文件，不修改现有文件。** 估算：0.8h / 120 LOC。关联 FR-B01, FR-B02, FR-B03, W4。blockedBy：T-A-1。

- [ ] **T-A-12** 单 task 手工验证：用 T-A-11 scorer 对 T-A-5 中 `t1_memory_001.yaml` 手工跑一次，确认 EventStore 断言路径可工作。无新文件产出，仅在 poc/ 下写临时验证脚本（可复用 T-0-T1 框架）。估算：0.3h。blockedBy：T-A-11, T-A-5。

- [ ] **T-A-REVIEW** Phase A 末 per-Phase Codex review（foreground，小范围）：范围 = `benchmarks/runner/scoring_rubrics.yaml` + `benchmarks/tiers/tier1/*.yaml`（25 文件）+ `benchmarks/runner/scorer.py`（初始版）。重点：YAML schema 是否足够表达断言意图 / LLM judge 触发逻辑边界（W4）/ scoring_rubrics.yaml 权重设计是否合理。blockedBy：T-A-12。

- [ ] **T-A-REGRESSION** Phase A 末全量回归：`pytest octoagent/` 期望 ≥ 3674 passed（F103c baseline a69fe9c）+ 0 regression + `pytest -m e2e_smoke` PASS。`git diff HEAD -- packages/ apps/gateway/src apps/web/` 期望 0 变更。blockedBy：T-A-REVIEW。

---

## Phase B — Tier 2 adapter（目标：τ-bench airline + GAIA L2 接入）

**目标**：两个业界 benchmark adapter 实现。
**关联 FR**：FR-E01, FR-E02, FR-E03, FR-E04 / **关联 AC**：AC4-2（前置）

- [ ] **T-B-1** [新增文件, RISK] 新建 `benchmarks/tiers/tier2/__init__.py`（5 LOC）+ `benchmarks/tiers/tier2/tau_bench_adapter.py`（约 180 LOC）：airline domain task 加载；`_REGISTRY_LOCK` threading.Lock 保证临时注册互斥；`tau_bench_tool_scope` contextmanager（acquire lock → 注册工具（名加 `tau_bench__` 前缀）→ yield → finally 清理）；per-task mock DB reset（主方案 `AirlineEnv.reset()`；PoC-H4 不成立激活 file-based isolation）；`W5 待 PoC 实测确认 actions 字段名`（基于 T-0-T2-TAU 实测结果填入）；分层抽样 15 task（6 类操作）。**新增文件，不修改现有文件。** 估算：1.5h / 180 LOC。关联 FR-E01, FR-E02, PoC-H2, PoC-H4, W5。[RISK] Tool Registry 若无 `deregister_by_name` API，需 grep 确认现有接口后实现 wrapper（不改 production registry 本身）。blockedBy：T-0-GATE。

- [ ] **T-B-2** [新增文件, RISK] 新建 `benchmarks/tiers/tier2/gaia_adapter.py`（约 100 LOC）：`load_dataset("gaia-benchmark/GAIA", split="validation")` 加载（PoC-H1 通过）；Level 2 过滤；按 FR-E04 分层抽样（web search × 2 + 文档解析 × 2 + 多工具串联 × 1）；normalized 匹配（大小写不敏感 + strip + 数字格式统一）；fallback 路径：若 PoC-H1 不成立从 `gaia_fallback_tasks.yaml` 加载。**新增文件，不修改现有文件。** 估算：0.8h / 100 LOC。关联 FR-E03, FR-E04, PoC-H1。[RISK] HF gated dataset 访问，降级路径需同步产出。blockedBy：T-0-GATE。

- [ ] **T-B-3** [新增文件，可选] 若 PoC-H1 不成立，新建 `benchmarks/tiers/tier2/gaia_fallback_tasks.yaml`（手工构造 5 个 Level 2 样本，标注 `[GAIA-FALLBACK]`）。条件性 task，PoC-H1 不成立时激活。估算：0.5h / 60 LOC。关联 PoC-H1 降级。

- [ ] **T-B-4** [新增 scorer 扩展] 扩展 `benchmarks/runner/scorer.py` 增加 Tier 2 评分逻辑：`score_tier2_tau(task, run_result) -> BenchmarkRunScore`（Pass@1 对照 actions 字段）+ `score_tier2_gaia(task, run_result) -> BenchmarkRunScore`（normalized 字符串匹配）；τ-bench user simulator 使用 Sonnet 4.6（FR-B05）。**修改 scorer.py（Phase A 已新建），属于同文件迭代，不修改其他现有文件。** 估算：0.6h / 60 LOC。关联 FR-B01, FR-B05。blockedBy：T-B-1, T-B-2, T-A-11。

- [ ] **T-B-5** 5 task 验证：对 τ-bench 取前 3 task、GAIA 取 2 task 手工单跑，确认 adapter 正常执行 + scorer 返回有效结果。无新文件产出（临时脚本可复用）。估算：0.3h。blockedBy：T-B-4。

- [ ] **T-B-REVIEW** Phase B 末 per-Phase Codex review（background，高风险 race condition 审查）：范围 = `tau_bench_adapter.py` + `gaia_adapter.py` + scorer.py Tier 2 扩展。重点：Tool Registry 临时注册 race condition / 状态泄漏 / τ-bench 名称冲突防御 / contextmanager finally 路径覆盖。blockedBy：T-B-5。

- [ ] **T-B-REGRESSION** Phase B 末全量回归：`pytest octoagent/` 0 regression + e2e_smoke PASS + `git diff HEAD -- packages/ apps/gateway/src apps/web/` 0 变更。blockedBy：T-B-REVIEW。

---

## Phase C — Tier 3 哲学 task（目标：H1/H2/H3 audit chain 断言）

**目标**：5 个 Tier 3 task YAML + audit_chain_assert scorer。
**关联 FR**：FR-F01, FR-F02, FR-F03, FR-B04 / **关联 AC**：AC4-3

- [ ] **T-C-1** [新增文件，P 可并行] 新建 `benchmarks/tiers/tier3/t3_h1_001.yaml`（H1 管家 mediated，约 25 LOC）：prompt 委托 Worker 处理任务；`audit_assertions`: `SUBAGENT_SPAWNED.source_runtime_kind=main` 存在 + `AgentSession(kind=worker)` 无直接 user-facing event。**新增文件，不修改现有文件。** 估算：0.2h。关联 FR-F01, FR-F02, SC-010。

- [ ] **T-C-2** [新增文件，P 可并行] 新建 `benchmarks/tiers/tier3/t3_h2_001.yaml`（H2 Worker memory 隔离）：`audit_assertions`: `MEMORY_RECALL_COMPLETED.namespace=AGENT_PRIVATE` + `RecallFrame.agent_runtime_id` 可追溯。**新增文件。** 估算：0.2h。关联 FR-F01, FR-F02, SC-010。

- [ ] **T-C-3** [新增文件，P 可并行] 新建 `benchmarks/tiers/tier3/t3_h3a_001.yaml`（H3-A Subagent spawn-and-die）：`audit_assertions`: `SUBAGENT_SPAWNED` 存在 + `caller_project_id` 非空 + `SUBAGENT_COMPLETED` 存在。**新增文件。** 估算：0.2h。关联 FR-F01, FR-F02, SC-010。

- [ ] **T-C-4** [新增文件，P 可并行] 新建 `benchmarks/tiers/tier3/t3_h3b_001.yaml`（H3-B ask_back，N-H1 修复验证）：`audit_assertions`: `CONTROL_METADATA_UPDATED.is_caller_worker_signal=true` + task 经历 `WAITING_INPUT → IN_PROGRESS` 状态转换。**新增文件。** 估算：0.2h。关联 FR-F01, FR-F02, SC-010, T3-4 N-H1。

- [ ] **T-C-5** [新增文件，P 可并行] 新建 `benchmarks/tiers/tier3/t3_h3_ww_001.yaml`（H3 Worker→Worker A2A）：`audit_assertions`: `SUBAGENT_SPAWNED.source_runtime_kind=WORKER` + `BaseDelegation.delegation_id` 存在。**新增文件。** 估算：0.2h。关联 FR-F01, FR-F02, SC-010。

- [ ] **T-C-6** [新增 scorer 扩展] 扩展 `benchmarks/runner/scorer.py` 增加 Tier 3 `audit_chain_assert` 逻辑：`score_tier3(task, run_result, event_store) -> BenchmarkRunScore`，逐条遍历 `audit_assertions`，全部通过 = PASS，任一不通过 = FAIL + 报告哪条断言失败（含 event_type + field_name + 期望值 vs 实测值）。确认 T-C-4 N-H1 的 `CONTROL_METADATA_UPDATED` 信号 EventStore query 路径。**修改 scorer.py（同文件迭代）。** 估算：0.6h / 80 LOC。关联 FR-B04, FR-F03, AC4-3。blockedBy：T-C-1~T-C-5。

- [ ] **T-C-REVIEW** Phase C 末 per-Phase Codex review（foreground）：范围 = Tier 3 YAML 5 文件 + scorer.py audit_chain_assert 扩展。重点：audit_assertions 字段设计是否足够严格（避免 false positive）/ T3-4 N-H1 信号查询路径是否准确 / event_type_absent 断言语义是否正确实现。blockedBy：T-C-6。

- [ ] **T-C-REGRESSION** Phase C 末全量回归：`pytest octoagent/` 0 regression + e2e_smoke PASS + git diff 0 变更。blockedBy：T-C-REVIEW。

---

## Phase D — Runner / Scorer / Reporter 完整实现（目标：`octo bench daily` CLI 就绪）

**目标**：完整 benchmark runner 主体 + CLI 入口 + SQLite 持久化 + delta 报告。
**关联 FR**：FR-A01~A07, FR-B01~B04, FR-C01~C04, FR-H01~H05 / **关联 AC**：全部 AC

- [ ] **T-D-1** [新增文件，W9 确认] 新建 `benchmarks/__init__.py`（5 LOC）+ `benchmarks/runner/__init__.py`（5 LOC）。**新增文件，不修改现有文件。** 估算：0.1h / 10 LOC。关联 FR-H01。

- [ ] **T-D-2** [新增文件] 新建 `benchmarks/runner/store.py`（约 100 LOC）：SQLite schema（`benchmark_run` + `benchmark_baseline` 两表 + 索引，严格按 plan §6.1）；`BenchmarkStore` 类：`append_run()` append-only 写入 + `get_pending_runs(session_id)` 支持 resume + `get_baseline(label)` 查询。DB 路径：`benchmarks/baselines/bench.db`。**新增文件，不修改现有文件。** 估算：0.8h / 100 LOC。关联 FR-A05, FR-A06, AC1-3, AC5-1。

- [ ] **T-D-3** [新增文件, RISK] 新建 `benchmarks/runner/worker.py`（约 150 LOC）：`run_daily_bench(tasks, semaphore_size=8)` + `asyncio.Semaphore(8)` + gradual ramp（每 slot 错开 0.5s）+ `run_task_with_retry(task, max_retries=3)` + retry-after header 优先 + exponential backoff with jitter + `QUOTA_SKIP` / `TIMEOUT` / `INFRA_ERROR` 三态标记 + 连续 5 INFRA_ERROR 主动停止。**新增文件，不修改现有文件。** 估算：1.2h / 150 LOC。关联 FR-A01~A07, AC3-1~AC3-4, SC-006。[RISK] asyncio 8 并发全超时边界条件 + gradual ramp 与 semaphore 交互时序。blockedBy：T-D-2。

- [ ] **T-D-4** [新增文件] 新建 `benchmarks/runner/reporter.py`（约 120 LOC）：`generate_report(session_id, store) -> BenchmarkReport`；JSON 报告结构（plan §6.3 严格格式）含 `run_id / baseline_sha / created_at / summary / by_tier / by_domain / task_details`；Markdown 摘要生成；`--compare` 模式输出 `delta` 区块（W7：Δ pass rate 精确到 0.001 + regression 列表 + improvement 列表）；`baseline not found` 时报错（AC6-2）；写入 `benchmarks/baselines/{sha}-{ts}.json`。**新增文件，不修改现有文件。** 估算：1.0h / 120 LOC。关联 FR-C01~C04, AC1-2, AC1-4, AC4-1, AC4-2, AC6-1, AC6-2, W7。blockedBy：T-D-2。

- [ ] **T-D-5** [新增文件] 新建 `benchmarks/conftest.py`（约 60 LOC）：module singleton reset（复用 F087 e2e_live 模式）+ hermetic env（5 类凭证 env + 4 个 OCTOAGENT_* 路径 env + 5 项 module 单例）防 task 间状态泄漏（FR-H05）。**新增文件，不修改任何现有 conftest.py。** 估算：0.5h / 60 LOC。关联 FR-H05。

- [ ] **T-D-6** scorer.py 整合：将 Phase A/B/C 分别实现的 scorer 逻辑整合为统一接口 `score(task, run_result, event_store) -> BenchmarkRunScore`，按 `task.tier` 分发到对应评分逻辑。补充 LLM judge 真实实现（替换 Phase A stub，调用 Sonnet 4.5 temperature=0）。估算：0.5h / 40 LOC 净增。关联 FR-B01~B04. blockedBy：T-D-3，T-C-6。

- [ ] **T-D-7** [新增文件] 实现 CLI 入口，方案 A 优先：新建 `apps/gateway/src/octoagent/gateway/cli/bench_commands.py`（≤ 30 LOC，thin wrapper）+ 在 `apps/gateway/pyproject.toml` 新增 `octo-bench = "octoagent.gateway.cli.bench_commands:app"` entry point（**仅新增行，不修改现有字段**）。实施前 grep 确认 `cli/__init__.py` 是否现有文件，若是则严格用方案 A（独立命令），不修改 `__init__.py`。**bench_commands.py 为新增文件；pyproject.toml 仅新增一行 entry point。** 估算：0.3h / 30 LOC。关联 FR-H01, AC1-1, SC-005。blockedBy：T-D-3, T-D-4。[RISK] pyproject.toml 修改需确认不破坏现有 CLI 注册。

- [ ] **T-D-8** resume 续跑验证：手工模拟中断场景：跑 3 task 后 Ctrl+C，再执行 `--resume`，验证仅续跑未完成 task + 最终报告等价。无新文件，临时验证脚本。估算：0.3h。关联 AC5-1, AC5-2, SC-007。blockedBy：T-D-7。

- [ ] **T-D-REVIEW** Phase D 末 per-Phase Codex review（background，最高优先级）：范围 = 全部 `benchmarks/runner/` + `bench_commands.py`。重点：asyncio 8 并发全超时边界条件 / SQLite append-only 并发安全 / `--resume` 续跑逻辑（不重复执行已完成 task）/ Tool Registry contextmanager 与 runner 交互时序 / delta 视图格式正确性。blockedBy：T-D-8。

- [ ] **T-D-REGRESSION** Phase D 末全量回归：`pytest octoagent/` 0 regression + e2e_smoke PASS + `git diff HEAD -- packages/ apps/gateway/src apps/web/` 验证仅有 `bench_commands.py`（新增）和 `pyproject.toml`（新增 1 行），无其他变更。blockedBy：T-D-REVIEW。

---

## Phase E — M5 baseline 跑

**目标**：在 F103c baseline 代码上完整跑一次 Daily Bench，产出 M5 baseline，校准 efficiency 基准。
**关联 FR**：FR-H02, FR-H03 / **关联 AC**：AC1-1, AC1-2, AC1-3, SC-001, SC-003, SC-004

- [ ] **T-E-1** pre-flight 检查：确认 `git diff HEAD -- packages/ apps/gateway/ apps/web/` 为 0 变更（SC-009 零侵入验证）；确认 Connor 真实场景 4 个 task 已由用户拍板填入实际内容（`status: PLACEHOLDER` 的 task 不计入 Daily Bench）；确认控变量 LLM = Sonnet 4.5 temperature=0（FR-H02）。估算：0.2h。关联 FR-H01, FR-H02, SC-009。blockedBy：T-D-REGRESSION。

- [ ] **T-E-2** 执行 M5 baseline：`octo bench daily --label m5-baseline`，50 task × 3 次 × 8 并行，实测总耗时（SC-001 ≤ 1h），SQLite 实时写入每条 BenchmarkRun。估算：≤ 1h 实际跑 + 0.2h 监控。关联 AC1-1, AC1-3, SC-001。blockedBy：T-E-1。[RISK] 若实际耗时 > 1h 需 RCA（Tier 2 GAIA task 单任务 timeout 设置）。

- [ ] **T-E-3** 验证结果完整性：确认 SQLite 中 ≥ 150 条 BenchmarkRun 记录（AC1-3）；INCONSISTENT 占比 ≤ 5%（SC-011）；若超过 → RCA 记录（LLM 随机性 vs scorer 健壮性）。估算：0.2h。关联 AC1-3, SC-011。blockedBy：T-E-2。

- [ ] **T-E-4** 存档报告：`benchmarks/baselines/m5-baseline.json` + `benchmarks/baselines/m5-baseline.md` Markdown 摘要（AC1-2）。确认 JSON 含 `by_tier` + `by_domain` 完整结构（AC4-1, AC4-2）。估算：0.2h。关联 AC1-2, SC-003, SC-008。blockedBy：T-E-3。

- [ ] **T-E-5** W3 efficiency baseline 写入：执行 reporter.py 的 `calibrate_efficiency_baseline` 步骤（`--label m5-baseline` 时自动触发），按 domain 分组计算 PASS task 的 p50 token_usage（input + output，不含 cache_read），写入 `benchmarks/runner/scoring_rubrics.yaml` 的 `efficiency_baseline_tokens` 字段。将更新后的 `scoring_rubrics.yaml` 纳入 git。估算：0.2h。关联 FR-B02, W3, SC-003。blockedBy：T-E-4。

- [ ] **T-E-6** SC-001 验证：记录实测 wall clock 时间（≤ 1h），写入 phase-E 说明。关联 SC-001。blockedBy：T-E-2。

- [ ] **T-E-FINAL-REVIEW** Phase E 末 Final cross-Phase Codex review（background，整个 benchmark 模块综合审查）：范围 = 全部 `benchmarks/` 模块 + `bench_commands.py` + `pyproject.toml` 改动。重点：benchmark 模块整体架构是否引入隐性技术债 / SQLite schema 是否满足 M6 长期对比需求 / Tool Registry 隔离机制（race condition + 状态泄漏）/ 评分逻辑一致性（三层 scorer 逻辑漏洞）。blockedBy：T-E-5, T-E-6。

- [ ] **T-E-REGRESSION** Phase E 末全量回归（Final）：`pytest octoagent/` ≥ 3674 passed + 0 regression vs F103c baseline a69fe9c + e2e_smoke PASS。blockedBy：T-E-FINAL-REVIEW。

---

## Phase F — 文档闭环（Codex review 豁免）

**目标**：完成文档闭环 + 给 F104 的 handoff，清理临时文件。

- [ ] **T-F-1** [新增文件] 新建 `.specify/features/103d-octobench/completion-report.md`：逐 AC 标注通过情况（AC1-1~AC6-2 逐一 PASS/FAIL/PARTIAL + 说明），对照 spec FR 覆盖情况，Phase 实际执行情况 vs 计划偏离说明。估算：0.5h / 80 LOC。关联 SC-008。blockedBy：T-E-REGRESSION。

- [ ] **T-F-2** [新增文件] 新建 `.specify/features/103d-octobench/handoff.md`（给 F104 文件工作台）：M5 baseline 数据快照（pass rate 数值）+ M6 各 Feature 验收门槛建议（基于 M5 pass rate 推算 regression 警戒线：如"Tier 1 低于 M5 - 10% 触发 code review"）+ M5 baseline 文件路径索引 + Tier 3 audit 信号监控要点。估算：0.3h / 60 LOC。关联 SC-008。blockedBy：T-F-1。

- [ ] **T-F-3** 清理 PoC 临时文件：将 `.specify/features/103d-octobench/poc/` 下临时脚本标注"归档已完成"（或删除），`phase-0-poc-report.md` 保留作历史记录。估算：0.1h。blockedBy：T-F-1。

---

## 任务依赖图

```
Phase 0（串行）
  T-0-1 → T-0-T1, T-0-T2-TAU, T-0-T3-GAIA, T-0-T4-PHILOSOPHY, T-0-T5-CONC
  （以上 5 个 PoC task 可并行跑）
  ↓ 全部完成后
  T-0-6 → T-0-GATE（STOP）→ T-0-REGRESSION

Phase A（T-0-GATE 用户拍板后进入）
  T-A-1 ──────────────────────────────→ T-A-11
  T-A-2, T-A-3, T-A-4, T-A-5,          ↓
  T-A-6, T-A-7, T-A-8, T-A-9（并行）→ T-A-12 → T-A-REVIEW → T-A-REGRESSION
  T-A-10（PLACEHOLDER，可并行）

Phase B（T-A-REGRESSION 后进入）
  T-B-1, T-B-2（可并行）→ T-B-4 → T-B-5 → T-B-REVIEW → T-B-REGRESSION
  T-B-3（条件性，PoC-H1 不成立时激活）

Phase C（T-B-REGRESSION 后进入）
  T-C-1, T-C-2, T-C-3, T-C-4, T-C-5（可并行）→ T-C-6 → T-C-REVIEW → T-C-REGRESSION

Phase D（T-C-REGRESSION 后进入）
  T-D-1 → T-D-2 → T-D-3
                 ↘ T-D-4
  T-D-3 + T-D-4 → T-D-7 → T-D-8
  T-D-5（可在 T-D-1 后并行）
  T-D-6（blockedBy T-D-3 + T-C-6）
  T-D-7 → T-D-8 → T-D-REVIEW → T-D-REGRESSION

Phase E（T-D-REGRESSION 后进入）
  T-E-1 → T-E-2 → T-E-3 → T-E-4 → T-E-5
                 ↘ T-E-6
  T-E-5 + T-E-6 → T-E-FINAL-REVIEW → T-E-REGRESSION

Phase F（T-E-REGRESSION 后进入）
  T-F-1 → T-F-2 → T-F-3
```

**Phase 间依赖（串行）**：Phase 0 → A → B → C → D → E → F（每 Phase 末 REGRESSION 通过后进入下一 Phase）。

**Phase 内并行机会**：
- Phase A：T-A-2 ~ T-A-10（9 个 YAML 组）可并行生成
- Phase B：T-B-1 + T-B-2 可并行
- Phase C：T-C-1 ~ T-C-5（5 个 Tier 3 YAML）可并行
- Phase D：T-D-3 + T-D-4 可并行；T-D-5 可在 T-D-1 后独立并行
- Phase E：T-E-6 与 T-E-3~T-E-5 可并行

---

## FR 覆盖映射表

| FR | 对应 Task |
|----|-----------|
| FR-A01（Semaphore 8 并发）| T-D-3 |
| FR-A02（gradual ramp 0.5s）| T-D-3 |
| FR-A03（task 超时 / GAIA 8min）| T-D-3 |
| FR-A04（3 次 iterations + majority vote）| T-D-3 |
| FR-A05（SQLite append-only）| T-D-2 |
| FR-A06（--resume 续跑）| T-D-2, T-D-8 |
| FR-A07（429 retry-after + backoff）| T-D-3 |
| FR-B01（三层不同评分逻辑）| T-A-11, T-B-4, T-C-6, T-D-6 |
| FR-B02（三维得分权重）| T-A-1, T-D-6 |
| FR-B03（LLM judge fallback）| T-A-11, T-D-6 |
| FR-B04（Tier 3 audit chain 断言）| T-C-6 |
| FR-B05（τ-bench user simulator Sonnet 4.6）| T-B-4 |
| FR-C01（JSON + Markdown 报告）| T-D-4 |
| FR-C02（JSON 报告结构）| T-D-4 |
| FR-C03（--compare delta 区块）| T-D-4 |
| FR-C04（baselines/ 目录命名 + m5 软链接）| T-D-4, T-E-4 |
| FR-D01（25 Tier 1 YAML 化）| T-A-2~T-A-10 |
| FR-D02（真实 LLM 路径 + 独立 data_dir）| T-A-11, T-D-3 |
| FR-D03（9 域覆盖 + Connor 4 task）| T-A-2~T-A-10 |
| FR-E01（τ-bench contextmanager 临时注册）| T-B-1 |
| FR-E02（15 task 分层抽样）| T-B-1 |
| FR-E03（GAIA HF 加载 + normalized 匹配）| T-B-2 |
| FR-E04（GAIA 5 task 分层）| T-B-2 |
| FR-F01（5 Tier 3 YAML 化）| T-C-1~T-C-5 |
| FR-F02（5 哲学维度 H1/H2/H3-A/H3-B/H3）| T-C-1~T-C-5 |
| FR-F03（audit_assertions 逐条断言）| T-C-6 |
| FR-G01（phase-0-poc-report.md）| T-0-6 |
| FR-G02（PoC 手工脚本，不写 SQLite）| T-0-T1~T-0-T5-CONC |
| FR-H01（零侵入 production）| 所有 Phase REGRESSION |
| FR-H02（控变量 LLM Sonnet 4.5）| T-E-1 |
| FR-H03（每 Phase 0 regression）| 各 Phase REGRESSION |
| FR-H04（token_usage + duration 记录）| T-D-2, T-D-3 |
| FR-H05（singleton reset 复用）| T-D-5 |

---

## 风险 / 阻塞快速查询表

| Task | 风险描述 | 严重度 | 降级方案 |
|------|---------|--------|---------|
| T-0-T3-GAIA | HF GAIA gated dataset 访问失败（PoC-H1）| HIGH（P0 阻塞）| 激活 T-B-3 fallback yaml；GAIA pass rate 标注"非官方数据集" |
| T-0-T2-TAU | τ-bench 安装失败（IA-3）或 task 数 < 15（PoC-H2）| HIGH | git+URL 安装；task 数不足从 retail domain 补 |
| T-0-T5-CONC | SQLite WAL contention p95 > 2s（PoC-H3）| MEDIUM | 降级共享 store 方案（单 harness 实例） |
| T-B-1 | Tool Registry 无 deregister_by_name API（NEW-R2）| MEDIUM | Phase B 前 grep 确认；若无则实现 wrapper，不改 production registry |
| T-B-1 | τ-bench mock DB reset side effect（PoC-H4）| MEDIUM | file-based isolation（独立 tmpdir copy） |
| T-D-3 | asyncio 8 并发全超时边界条件 | MEDIUM | 连续 5 INFRA_ERROR 主动停止；timeout 触发释放 slot |
| T-D-7 | pyproject.toml entry point 修改破坏现有 CLI | MEDIUM | 方案 A 优先（独立 `octo-bench` 命令）；实施前 grep 确认现有 CLI 注册 |
| T-E-2 | M5 baseline 实际耗时 > 1h（SC-001）| MEDIUM | RCA：检查 GAIA timeout 设置（8min × 5 = 40min 占比）；调整并发度 |
| T-E-2 | Anthropic API 5xx / 网络断（NEW-R3）| MEDIUM | 单 task INFRA_ERROR；连续 5 个主动停止报错 |
| T-A-10 | Connor 真实场景 4 task PoC 后未及时拍板（NEW-R6）| LOW | PLACEHOLDER 不计入 Daily Bench 分母；SC-005 占位说明 |
