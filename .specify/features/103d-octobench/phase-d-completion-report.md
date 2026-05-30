# F103d Phase D 归总报告（给主 session 拍板）

> 完成时间：2026-05-30
> Worktree：`.claude/worktrees/mystifying-feynman-21720b`
> 分支：`claude/mystifying-feynman-21720b`
> Baseline：master HEAD `ea9fad6` (F103d Phase C)

## 1. 改动文件清单 + 净增减行数

### 新增文件（所有都在 benchmarks/ 顶层，零侵入 production）

```
created:  benchmarks/__init__.py                                    (15 行)
created:  benchmarks/conftest.py                                    (191 行)
created:  benchmarks/runner/__init__.py                             (15 行)
created:  benchmarks/runner/store.py                                (432 行 SQLite + 两表)
created:  benchmarks/runner/worker.py                               (504 行 asyncio 8 并发)
created:  benchmarks/runner/reporter.py                             (500 行 JSON + Markdown + delta)
created:  benchmarks/runner/cli.py                                  (550 行 argparse 子命令)
created:  benchmarks/runner/score_dispatch.py                       (170 行 统一 dispatch)
created:  benchmarks/tests/unit/test_store.py                       (185 行 / 18 tests)
created:  benchmarks/tests/unit/test_worker.py                      (340 行 / 24 tests)
created:  benchmarks/tests/unit/test_reporter.py                    (288 行 / 22 tests)
created:  benchmarks/tests/unit/test_score_dispatch_and_judge.py    (327 行 / 33 tests)
created:  benchmarks/tests/unit/test_cli.py                         (380 行 / 19 tests)
created:  octoagent/apps/gateway/src/octoagent/gateway/cli/bench_commands.py  (24 行 thin wrapper)
```

### 修改文件（明确受控范围）

```
modified: benchmarks/runner/llm_judge.py    (+236 / -30，Phase A stub → Phase D adapter 框架)
modified: octoagent/apps/gateway/pyproject.toml  (+3 [project.scripts] block + entry point)
modified: benchmarks/conftest.py（已在新增列表）
modified: .specify/features/103d-octobench/trace.md     (Phase D 段追加)
```

### FR-H01 零侵入守护验证

```bash
$ git diff HEAD -- octoagent/packages octoagent/apps/web
(0 lines changed)

$ git diff HEAD -- octoagent/apps/gateway
 octoagent/apps/gateway/pyproject.toml | 3 +++
 1 file changed, 3 insertions(+)
```

`octoagent/apps/gateway/pyproject.toml` 改动严格只新增 `[project.scripts]` block + 1 行 entry point，0 现有字段修改。所有 `packages/` / `apps/web/` 0 字节变更。

## 2. 解决的问题（用户视角）

### Phase D 主交付

完整可用的 `octo-bench daily` 工具链：

1. **Runner**（worker.py）：asyncio.Semaphore(8) 并发 + 0.5s gradual ramp + retry-after 优先 + exp backoff jitter + 三态分类（QUOTA_SKIP / TIMEOUT / INFRA_ERROR）+ 连续 5 INFRA_ERROR 主动 stop_event 触发。**避免 Phase B threading.Lock 跨 await 陷阱**——所有共享状态用 asyncio.Lock 短临界区，counter 不持锁跨 await。

2. **Store**（store.py）：SQLite WAL + busy_timeout=30s + 两表 schema（benchmark_run / benchmark_baseline）+ append-only `INSERT OR REPLACE` 幂等 + per-thread connection（threading.local）+ resume 路径 `get_completed_keys` / `get_pending_runs(planned)`。

3. **Reporter**（reporter.py）：plan §6.3 完整 JSON 结构（summary + by_tier tier1/tier2_拆 tau_bench+gaia/tier3 + by_domain + task_details）+ Markdown 摘要 + `--compare` delta 区块（W7 0.001 精度 + regression / improvement 列表 + baseline not found 报错 AC6-2）+ archive_report 归档 + report_to_baseline_record。

4. **Conftest**（conftest.py）：复用 F087 e2e_live 模式（5 凭证 env + 4 OCTOAGENT_* 路径 env + 5 项 module singleton reset）双入口：pytest autouse fixture + runtime `hermetic_task_scope` contextmanager（worker.py 在 task 间手动调用）。

5. **Scorer dispatch**（score_dispatch.py + llm_judge.py 升级）：统一 `score(task, run_result, *, rubrics)` 接口按 tier+domain 分发；LLM judge 升级为 adapter Protocol 框架（StubJudgeAdapter 默认 + ProviderRouterJudgeAdapter Sonnet 4.5 temperature=0 路径）；**触发常量严格不变**（known-issues F-01）。

6. **CLI**（cli.py + bench_commands.py + pyproject.toml）：`octo-bench daily / list-baselines / show` 子命令 + `--label / --resume / --compare / --runner module:attr / --dry-run / --skip-preflight / --tier` 完整选项 + 方案 A 独立命令（不动现有 octo CLI 主 group）。

### Phase C 推迟项接管

| 推迟项 | 处理结果 |
|--------|---------|
| H3-B follow_up_inputs runner 接入（Round 4 P2-1）| ✅ DONE：`extract_follow_up_inputs(task_raw)` helper + 3 单测；runner_fn 模式文档化 |
| AGENT_SESSION_TURN_PERSISTED 续扩 H1 audit task（Round 3 P2-2）| ⏸ 保留 Phase C 行为不动；后续 H1 task 续扩留 M6 / 用户拍板时再补 |
| scorer event binding 框架级加强（Round 6 P2-2）| 📋 评估计划归档（见 §5）：Phase E baseline 跑后统计 false PASS 比例 ≤ 5% → M6 F108；> 5% → Phase E 后回头补做 |

### Phase B 推迟项接管

| 推迟项 | 处理结果 |
|--------|---------|
| preflight runner 入口调用 | ✅ DONE：cli.py daily 子命令调用 `_preflight_check_or_fail()`，Tier 2 加载前必须通过；`--skip-preflight` flag 允许仅跑 Tier 1/3 |
| PoC-H4 mock DB reset / Pass@1 order+args / tau_bench env.step / GAIA LLM-judge fallback | ⏸ 保留 Phase E 实跑时实施（涉及真 LLM + 真实 user simulator） |

## 3. 测试与回归结果

| 测试范围 | 结果 |
|---------|------|
| `benchmarks/tests/unit/` 累计 | **277 PASS**（Phase A 16 + Phase B 65 + Phase C 74 + Phase D 122）/ 1.70s |
| Phase D 新增 unit tests | 122 PASS（store 18 + worker 26 含 HIGH-1/3 回归 + reporter 22 + dispatch+judge 35 含 HIGH-4 回归 + cli 21 含 follow_up + MED-1 回归） |
| octoagent 全量回归（不含 e2e_live） | **3674 passed** + 10 skipped + 1 xfailed + 1 xpassed + 77 deselected / 117.59s |
| octoagent baseline 4c0e513 (Phase B) / ea9fad6 (Phase C) 对照 | 0 net regression（同样 3674 passed） |
| e2e_smoke | **8/8 PASS** / 2.09s |
| 零侵入校验 | `git diff HEAD -- octoagent/packages octoagent/apps/web = 0` ✅ |
| pyproject.toml 改动 | 仅 3 行新增 `[project.scripts]` + entry point，0 现有字段修改 ✅ |

## 4. Codex Adversarial Review 闭环结果

Phase D 末 Codex review（background 模式，`codex review --uncommitted`）。codex 0.133.0
在 sandbox `stdin is terminal: false` 限制下直接 exit 0 不产出 finding（与 F103b backend
中断同 pattern）——**主 session 接管**按 worker prompt §"Codex review 重点"8 维度逐条
手工 review，抓出 **4 HIGH + 1 MED + 3 LOW**：

| 编号 | 严重度 | Scope | 决策 | 单测回归 |
|------|--------|-------|------|---------|
| HIGH-1 | HIGH | worker.py `except BaseException` 吞 KeyboardInterrupt | **修** | `test_run_task_with_retry_keyboard_interrupt_propagates` + `_system_exit_propagates` |
| HIGH-2 | HIGH | worker.py `asyncio.get_event_loop().time()` deprecated | **修** | 现有测试隐式覆盖 |
| HIGH-3 | HIGH | worker.py `asyncio.gather(return_exceptions=False)` 拖垮整 batch | **修** | `test_run_daily_bench_continues_on_single_task_unhandled_exception` |
| HIGH-4 | HIGH | llm_judge.py `_clip_score` 不防 NaN/inf | **修** | `test_clip_score_nan_returns_stub_mid` + `_inf_returns_stub_mid` |
| MED-1 | MED | cli.py `resolve_runner` factory TypeError 静默吞 | **修** | `test_resolve_runner_factory_typeerror_warns_uses_target` |
| LOW-1 | LOW | reporter `compare_with_baseline` 不报告 missing-in-current task | **接受** | M6 删 task 时 silent 不算 regression 反而合理 |
| LOW-2 | LOW | cli `_dry_run_runner` 100% PASS 类似真 baseline | **接受** | commit_sha 标识可区分 |
| LOW-3 | LOW | store.py thread-local connection 不自动 close | **接受** | process 退出 sqlite 自动释放 |

**0 HIGH 残留**；详细 finding + reasoning + 8 维度手工 review trace 见
`.specify/features/103d-octobench/phase-d-codex-review.md`。

## 5. Phase D 推迟项（归档清单）

### 推迟到 Phase E 实跑时处理

1. **scorer event binding 框架级加强**（Round 6 P2-2 + Phase D 评估计划）：
   - 评估方式：M5 baseline 跑通后，统计 5 个 Tier 3 task 在真实 audit chain 下的 false PASS 比例
   - **比例 ≤ 5% → 归档 M6 F108 Capability Layer Refactor**（投入产出比低）
   - **比例 > 5% → Phase E 后回头补做**（YAML schema + scorer state machine 扩展）
   - 理由：当前能正确捕获大多数 false PASS（Codex 6 轮 review 闭环），实际比例必须实跑后才能量化评估

2. **LLM judge 真实 ProviderRouter 接入**：
   - T-D-6 已建 ProviderRouterJudgeAdapter 框架（chat_fn DI 钩子）
   - Phase E 时由 caller wire 真实 ProviderRouter `chat_completion` 调用即可
   - 单测 `test_provider_adapter_calls_chat_fn` 已覆盖契约

3. **Pass@1 order-aware + arguments-aware**（Phase B 推迟 #2）：
   - 目前 score_tier2_tau 用 Counter 比较多重集（已修 Phase B HIGH "set 误判"）
   - 完整 order-aware + arguments 比对需接 tau_bench env.step + user_simulator

4. **GAIA Unicode normalization**（Phase B 推迟 #3）：
   - 在 score_tier2_gaia 内增强 `match_answer` 逻辑（unicodedata.normalize NFKC）

5. **PoC-H4 mock DB reset 验证**（Phase B 推迟 #1）：
   - 现 `tau_bench__` 前缀 + threading.Lock 临时注册 contextmanager 已防泄漏
   - file-based isolation 兜底方案保留（Phase E 时按需启用）

### 归档到 M6 F108 Capability Layer Refactor

- D9 / D11 / D12 架构债（tooling/harness/capability_pack 三层职责 / LLMWorkerAdapter 命名 / BehaviorFileRegistry DRY）— M5 全闭环后做

## 6. 风险

### 已缓解

- **threading.Lock 跨 await 陷阱**（Phase B 教训）：worker.py 严格用 asyncio.Lock + 短临界区（不持锁跑 task）+ 已有 `test_run_daily_bench_no_threading_lock_across_await` 回归测保护
- **SQLite 8 并发写**：WAL + busy_timeout=30s + threading.local connection，PoC-H3 实测 p95 < 2s
- **零侵入 production**：每 Phase 末 `git diff -- packages/ apps/web/` = 0 验证；apps/gateway 仅 3 行新增 entry point + 1 新文件
- **触发常量漂移**：`test_llm_judge_trigger_constants_locked` 单测锁死

### 未消除（接受）

- **stub runner 默认行为**：Phase D `octo-bench daily` 不带 `--dry-run` / `--runner` 时所有 task 返回 INFRA_ERROR + "stub runner; Phase E should wire real LLM"。Phase E 实跑前必须 wire 真实 runner_fn（cli.py 已加 `--runner module:attr` 注入入口）
- **scorer event binding 跨链验证**：Round 6 P2-2 归档项，5 个 Tier 3 task false PASS 的理论风险（已通过 prompt + 强化断言缓解；M5 baseline 跑后量化评估）
- **CLI 命名等价**：spec.md 写 `octo bench daily`，方案 A 实际命令是 `octo-bench daily`。Phase F handoff 文档说明等价；不修改现有 `octo` 主 CLI group（FR-H01 严格遵守）

## 7. 推荐主 session 拍板

**建议先 review 再合入**（按 CLAUDE.local.md §"Spawned Task 处理流程"）：

**理由**：

1. Phase D 工作量较大（~3500 行新增 + 升级 llm_judge.py 200+ 行 + 116 单测 + Codex review 重点 8 维度）
2. worker.py asyncio 并发是 [RISK] task；Codex review 闭环结果（finding 数 + 处置）应进入主 session 决策
3. CLI 入口 `octo-bench` 是用户长期工具，命名 / 入口契约需主 session 确认是否与既有 `octo` workflow 对齐
4. Phase E baseline 跑要求用户在 host 上 wire 真实 LLM key — handoff.md 已说明，但主 session 应确认 Phase E 实跑节奏

**用户 review 通过后下一步**：

- Phase D 分支 `claude/mystifying-feynman-21720b` 已 commit（待 commit 阶段）
- `git push origin master`（rebase 在 master 之上后 push）
- Phase E 实跑：用户在 host 上跑 `octo-bench daily --label m5-baseline --runner my_octo_runner:make_runner`

**如果用户决定推迟**：

- 保留 worktree + 分支状态
- Phase E 启动前再次评估归档项优先级（特别是 scorer event binding）

## 8. Phase E 启动准备（详见 handoff.md）

Phase E 实跑必须的事（清单）：

1. **Host LLM Key**：`ANTHROPIC_API_KEY` 必须设置；Tier 2 GAIA L2 task 也可能需要 HuggingFace token
2. **Tier 2 依赖**：`uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets`（每次 worktree 重建后追加，不进 production pyproject）
3. **Runner wire**：用户实现 `my_octo_runner.make_runner() -> TaskRunner`，wire OctoHarness DI 钩子 + 真实 LLM 路径 + EventStore query
4. **LLM judge wire**：`ProviderRouterJudgeAdapter(chat_fn=my_provider_router.chat)` 注入 `LLMJudgeTrigger`
5. **执行**：`octo-bench daily --label m5-baseline --runner my_octo_runner:make_runner`
6. **验收**：SC-001 ≤ 1h 实测 / SC-011 INCONSISTENT ≤ 5% / AC1-3 ≥ 150 BenchmarkRun
7. **效率基准校准**：T-E-5 `calibrate_efficiency_baseline` 写入 `scoring_rubrics.yaml` 的 `efficiency_baseline_tokens`

详细 handoff 见 `.specify/features/103d-octobench/handoff.md`。
