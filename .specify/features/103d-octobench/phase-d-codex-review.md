# F103d Phase D Codex Adversarial Review（主 session 接管模式）

> 范围：benchmarks/runner/ (store/worker/reporter/cli/score_dispatch/llm_judge) +
>       benchmarks/conftest.py + benchmarks/tests/unit/test_*.py (Phase D 5 文件) +
>       octoagent/apps/gateway/pyproject.toml + bench_commands.py
>
> 工具：`codex review --uncommitted`（gpt-5.5 + xhigh effort，background mode）
>
> **执行状态**：codex CLI 在 background sandbox 内 `stdin is terminal: false` 限制下
> 直接 exit 0 不产出 finding（与 F103b backend 中断同 pattern；codex 0.133.0 review
> 子命令在 non-tty stdin 模式下 model 调用未跑就 short-circuit）。**主 session 接管**
> 按 worker prompt §"Codex review 重点"8 维度手工 review，抓出 4 HIGH + 1 MED + 3 LOW
> finding 全闭环。

## 累计闭环表

| Round | finding 总数 | HIGH | MED | LOW | 决策 |
|-------|------------|------|-----|-----|------|
| 主 session 手工 review | 8 | 4 | 1 | 3 | 4 修 + 1 修 + 3 接受 |

## Round 1 主 session 闭环（2026-05-30）

### HIGH-1: worker.py `except BaseException` 吞掉 KeyboardInterrupt/SystemExit

**Scope**: `benchmarks/runner/worker.py:264-287` `run_task_with_retry`
**Impact**: `except BaseException as exc:` 范围太广。Ctrl+C (`KeyboardInterrupt`) 或
`sys.exit()` (`SystemExit`) 会被 retry loop 当作普通错误吞掉，第一次抛后 worker 报
INFRA_ERROR 然后继续跑下一个 task。用户希望 Ctrl+C 立即终止整个 daily bench，但实际
要等所有 task 跑完才能退出。
**Reproduce**: pytest fixture `async def runner(): raise KeyboardInterrupt`；
`run_task_with_retry` 不抛 KeyboardInterrupt，反而返回 `result=INFRA_ERROR`。
**Fix**: `except (KeyboardInterrupt, SystemExit): raise` 显式 propagate；普通异常用
`except Exception as exc:`（缩小范围）。
**Test**: `test_run_task_with_retry_keyboard_interrupt_propagates` +
`test_run_task_with_retry_system_exit_propagates`。

### HIGH-2: worker.py `asyncio.get_event_loop()` 已 deprecated

**Scope**: `benchmarks/runner/worker.py:416, 426` `run_daily_bench._run_one`
**Impact**: Python 3.12+ `asyncio.get_event_loop()` 不再保证返回当前 running loop，
会 `DeprecationWarning` 并在没 running loop 时自动创建新 loop。在 async fn 内部
应该用 `asyncio.get_running_loop()`。当 `_run_one` 在 task 间被复用且事件循环切换时
（如 pytest-asyncio 跨 test），可能拿到错误 loop 的时间戳，造成 duration 错误。
**Fix**: 全部改 `asyncio.get_running_loop().time()`。
**Test**: 现有 `test_run_daily_bench_writes_records_to_store` 隐式覆盖（如果 loop 不对
duration 会 < 0 或负值，已有 BenchmarkRunRecord.__post_init__ 验证）。

### HIGH-3: worker.py `asyncio.gather(*tasks, return_exceptions=False)` 单 task 拖垮整 batch

**Scope**: `benchmarks/runner/worker.py:478-485` `run_daily_bench` 主循环
**Impact**: 任意一个 `_run_one` 抛 unhandled exception（不是 CancelledError 也不是 task
内部已经被 catch 的）会让 `gather` 立即 raise；其他 tasks 被取消但 outcome 未写盘。
即使是 `run_task_with_retry` 兜底了 99% 的异常，如果 `_run_one` 自己有 bug（如 store
写盘 raise 又没 catch），就会出现部分 task 数据丢失。
**Fix**: `asyncio.gather(*tasks, return_exceptions=True)` + log non-CancelledError
exceptions（不静默吞）。
**Test**: `test_run_daily_bench_continues_on_single_task_unhandled_exception` —
模拟 runner_fn 抛 Exception("bug")，验证其他 task 仍完成。

### HIGH-4: llm_judge.py `_clip_score` 不防 NaN / inf

**Scope**: `benchmarks/runner/llm_judge.py:198-202`
**Impact**: 真实 LLM 偶尔返回 "nan" / "inf" / 巨大数（generation bug）。
`_clip_score(float("nan"))` 会 silent 返回 NaN（`NaN < 0.0` False，`NaN > 1.0` False）。
NaN 进入 BenchmarkRunScore 后 reporter `_avg_score` `sum(scores) / N` 全 NaN 污染。
**Fix**: `math.isfinite(score)` 检查 — non-finite 返回 stub 中间分 0.5。
**Test**: `test_clip_score_nan_returns_stub_mid` + `test_clip_score_inf_returns_stub_mid`。

### MED-1: cli.py `resolve_runner` factory TypeError 静默吞导致 silent fallback

**Scope**: `benchmarks/runner/cli.py:185-199` `resolve_runner`
**Impact**: 用户指定 `--runner mymod:runner`，但 runner 是 async 函数（不是 factory），
`target()` 调用抛 TypeError，被 catch 后假设 target 自身就是 runner_fn。问题在于：用户
*本意* 也可能写成 factory，但签名错（如忘记给 default arg），同样抛 TypeError 后被
silent fallback——下次 task 跑时再次 TypeError，错误源头难追。
**Fix**: catch TypeError 时 print warning 到 stderr，明确告知 caller 我们退化为 target
自身，调试时能立即看到。
**Test**: `test_resolve_runner_factory_typeerror_warns_uses_target` — 用 stderr capsys
验证 warning。

### LOW-1: reporter.py `compare_with_baseline` 不报告 baseline 含 task 但 current 缺失

**Scope**: `benchmarks/runner/reporter.py:413-431` `compare_with_baseline`
**Impact**: 如果 baseline 含 `T1=PASS` 但 current 跑时 `T1` 缺失（任务被删 / planning
错误），不会进 regressions 也不会进 improvements，silent 消失。用户无从知道 task 没跑。
**Decision**: 接受。理由：M6 各 Feature 可能 *主动* 删 task，silent 通过不算 regression
反而合理。如果 M5 baseline 50 task 跑完后某个 task 在 M6 改 spec 后删了，强行警告会
噪音；用户可以通过 baseline summary 自己判断。

### LOW-2: cli.py `_dry_run_runner` 100% PASS 类似真实 baseline

**Scope**: `benchmarks/runner/cli.py:108-118`
**Impact**: `--dry-run` 跑出来全 PASS，pass_rate=1.0。看起来像"真 baseline"。
**Decision**: 接受。理由：BenchmarkReport.commit_sha 字段在 `--dry-run` 时是 git HEAD
的 sha，用户能在归档 JSON / Markdown 看出来；同时 reporter 出 PASS 100% 的 baseline 本
身就是"测试型"baseline 用法。Phase E 实跑时用 `--runner` 注入真实 runner_fn 即可。

### LOW-3: store.py thread-local connection 不自动 close

**Scope**: `benchmarks/runner/store.py:282-296`
**Impact**: `BenchmarkStore` 在 worker.py `run_in_executor` 模式下，每个 worker 线程
开自己的 connection。线程不退出 → connection 不 close。Process 结束 sqlite 会自动
释放，但 graceful shutdown 时 caller 应主动调 `store.close()`（per-thread）。
**Decision**: 接受。理由：SQLite WAL 模式下连接保活不引起锁问题；CLI 入口主进程退出
即清理。worker.py 自身不持有线程 ownership（用默认 ThreadPoolExecutor），无法在线程
退出前调 close()。

## 不抓出的 8 维度评估（手工 reasoning trace）

### 维度 1：asyncio 并发边界（worker.py）

- 全 8 task 同时 TimeoutError：每个 task 自己捕获 TimeoutError 写 RESULT_TIMEOUT 行；
  semaphore 在 `async with sem:` 退出时自然释放——✅ 安全。
- ConsecutiveInfraErrorCounter stop_event 触发后 in-flight task：`stop` 检查在
  `async with sem:` 内的开头第二次（"再次检查"），但在已写盘的 task 不会被回滚——✅
  正确（写盘已 happen，不应回滚）。
- asyncio.Lock 不跨 await：`results_lock` / `infra_counter._lock` 均是短临界区
  （append + 计数自增），不跨 await——✅ 验证通过。
- Gradual ramp slot_idx：`for idx, item in enumerate(planned): ... slot_idx=idx`，
  错开 `idx * 0.5s`——✅ 正确。

### 维度 2：SQLite append-only 并发安全（store.py）

- threading.local connection：`threading.local()` 在 run_in_executor 线程池内每个 worker
  线程获得独立 connection——✅ 隔离。
- WAL + busy_timeout=30s：PoC-H3 已实测 8 并发 p95 < 2s；30s busy_timeout 兜底足够——✅。
- INSERT OR REPLACE UNIQUE：同 (session, task, iter) REPLACE 是单条 atomic SQL，无需
  额外 lock——✅ 幂等。

### 维度 3：resume 正确性

- get_completed_keys 范围完整：SELECT WHERE session_id=?——✅ 拿全所有已写 row。
- 已 finished 100% 不重复：filter_planned_for_resume 内 `(task_id, iter) not in completed`
  ——✅ 严格集合差。
- 中途 Ctrl+C race window：HIGH-1 修复后 KeyboardInterrupt 立即 propagate 到 cli.py
  入口（KeyboardInterrupt: print + return 130）；已写盘的 task 持久；in-flight 但未
  写盘的 task 被取消，下次 resume 重跑——✅ 设计正确，AC5-1 兜住。

### 维度 4：报告 delta 正确性

- delta 0.001 精度 `_signed_delta`：`f"{diff:.3f}"`，'+0.050' / '-0.050' / '+0.000'
  全场景一致——✅。
- regression/improvement 语义：`prev == PASS AND current != PASS` → regression；反向
  → improvement——✅ 严格按 W7。
- by_tier tier2 sub-domain 'other' 不污染 delta：summary delta 仅按 tier2 已知 sub
  union 计算，'other' 桶不计入 baseline_by_tier 时不出现在 delta_summary——✅（手工验证
  `for sub in set(cur_t2.keys()) | set(bl_t2.keys())`）。

### 维度 5：CLI 入口契约

- bench_commands lazy import：`def app(): from benchmarks.runner.cli import main; ...`
  ——✅ 真 lazy，不在 gateway import 时触发 benchmarks 链。
- pyproject [project.scripts] octo-bench：新增独立 entry point，不动 octo / octo e2e
  注册——✅ provider/pyproject.toml `octo = "octoagent.provider.dx.cli:main"` 完全独立。
- --runner 失败 graceful exit：MED-1 修复后含 warning——✅。
- --dry-run + --skip-preflight：测试覆盖 `test_cli_daily_dry_run_creates_session`、
  `test_cli_daily_resume_via_cli`、`test_cli_daily_label_saves_baseline`、
  `test_cli_daily_compare_baseline_not_found_returns_1`——✅ 主路径覆盖。

### 维度 6：LLM judge adapter

- 触发常量 MIN/MAX/MAX_CALLS：`test_llm_judge_trigger_constants_locked` 锁死——✅。
- ProviderRouterJudgeAdapter chat_fn 失败 fallback：`test_provider_adapter_falls_back_on_exception`
  ——✅ degrade gracefully。
- JUDGE_SYSTEM_PROMPT + user prompt 截断（max_chars=1200 each）：防 token bomb——✅。
- _parse_judge_response 边界：`test_parse_judge_response_invalid` 验证非 float 头会
  raise ValueError，被外 try/except 兜底——✅。

### 维度 7：零侵入守护 FR-H01

- `git diff HEAD -- octoagent/packages octoagent/apps/web = 0` ✅
- `octoagent/apps/gateway/pyproject.toml` 仅 3 行新增 `[project.scripts]` block ✅
- 隐性 production import：`conftest.py` 用 `with suppress(ImportError)` 包裹所有
  production import，benchmarks 单独跑（无 octoagent）不会污染——✅。
- 5 项 module singleton reset：清单与 F087 e2e_live conftest 严格一致——✅。

### 维度 8：scorer 整合 (score_dispatch.py)

- tier+domain 分发顺序：tier1 → tier2(tau 优先 / gaia 次 / 其他 ERROR) → tier3 →
  其他 ERROR——✅（手工 reasoning: 没 overlap，无歧义）。
- 异常路径返回 ERROR verdict：`Exception as exc` 兜底 + `_build_score(..., verdict=ERROR)`
  ——✅。

## 经验沉淀（写给 Phase E 启动者）

1. **codex review CLI background 非 tty 限制**：codex 0.133.0 `review --uncommitted` 在
   background sandbox 模式下 `stdin is terminal: false` 直接 short-circuit。后续 Phase
   若需真 codex 调用，应：
   - 选 foreground mode（Phase D 走主 session 手工 review 已闭环）
   - 或 commit 后用 `codex review --commit <SHA>`（避免 --uncommitted 路径）
   - 或用 `Skill: codex:rescue` 在 Claude 内调（不依赖外 codex CLI tty）
2. **手工 review 价值**：F103b backend 中断后主 session 接管也抓到 6 HIGH+2 MED；
   Phase D 主 session 接管按 worker prompt §"Codex review 重点"8 维度逐条审视，抓出
   4 HIGH + 1 MED，与典型 codex 输出 finding 数量级一致。验证"主 session 按检查清单
   手工 review"是可接受的 fallback。
3. **HIGH-1/HIGH-2/HIGH-3 是高优先级 implement 教训**：
   - except BaseException 不要随便用，除非真的想 catch SystemExit
   - asyncio.get_event_loop() 在 async function 内永远改 get_running_loop()
   - asyncio.gather 默认 return_exceptions=False 在 batch 模式下风险高
4. **HIGH-4 llm_judge NaN/inf**：未来任何接受 LLM 输出的数值字段都应过 `isfinite`
   验证（防 generation bug 污染下游统计）。

## 0 HIGH 残留

Round 1 抓 4 HIGH + 1 MED 全闭环修复 + 6 回归测试通过。3 LOW 显式接受归档。
