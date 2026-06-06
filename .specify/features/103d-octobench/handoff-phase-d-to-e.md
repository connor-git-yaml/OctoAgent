# F103d Phase D → Phase E Handoff

> 写于：2026-05-30
> 阶段：Phase D 完成 → Phase E（M5 baseline 实跑）启动准备
> 上游：Phase D completion-report + Phase D codex review

## 1. Phase E 在 host 上必须的事

### 1.1 凭证 / 依赖

| 项 | 必需性 | 命令 |
|---|--------|------|
| `ANTHROPIC_API_KEY`（控变量 Sonnet 4.5）| 必需 | `export ANTHROPIC_API_KEY=...` |
| Tier 2 依赖：tau-bench / datasets | 必需（除 `--skip-preflight` 跑） | `uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets` |
| GAIA HuggingFace token（PoC-H1 fallback 路径用不到）| 仅原数据集，不必需 | `huggingface-cli login` 或 `export HF_TOKEN=...` |

### 1.2 Wire 真实 runner_fn（关键）

Phase D `octo-bench daily` 不带 `--runner` / `--dry-run` 时所有 task 返回
`INFRA_ERROR + "stub runner; Phase E should wire real LLM"`。Phase E 实跑前必须：

1. **新建一个 host-side runner module**（例 `my_octo_runner.py`，不在 git 内）：
   ```python
   # ~/my_octo_runner.py（用户私有）
   from benchmarks.runner.worker import TaskExecutionOutcome
   from benchmarks.runner.score_dispatch import RunResult, score
   from benchmarks.runner.scorer import load_scoring_rubrics

   _RUBRICS = load_scoring_rubrics(Path("benchmarks/runner/scoring_rubrics.yaml"))

   async def runner_fn(task_meta, iteration):
       # 1) 起 OctoHarness（F087 e2e_live 模式：data_dir=tmpdir / credential_store=$ANTHROPIC_API_KEY）
       async with OctoHarness(data_dir=tmpdir, credential_store=...) as harness:
           # 2) 跑 task（Tier 1/3：走 task_service.create_task + 等 SUCCEEDED；
           #    Tier 2 τ-bench：tau_bench_tool_scope 临时注册 + AirlineEnv.reset；
           #    Tier 2 GAIA fallback：直接 LLM call 拿 actual_answer）
           # 3) 从 EventStore 查事件 / 收集 tool_calls / actual_answer
           # 4) 调 score(task_meta.raw, RunResult(...), rubrics=_RUBRICS)
           # 5) 返回 TaskExecutionOutcome
           ...
   ```

2. **跑 baseline**：
   ```bash
   octo-bench daily \
     --label m5-baseline \
     --runner my_octo_runner:runner_fn \
     --commit $(git rev-parse HEAD) \
     --iterations 3 \
     --semaphore 8
   ```

### 1.3 LLM judge wire（Tier 1 partial 评分用）

如果 Tier 1 task 触发 LLM judge（`0.5 <= match_ratio < 1.0`），caller 需 wire 真实
ProviderRouter：

```python
from benchmarks.runner.llm_judge import LLMJudgeTrigger, ProviderRouterJudgeAdapter
from octoagent.provider.routing import ProviderRouter

def make_chat_fn(router: ProviderRouter):
    def chat_fn(messages, model, temperature, max_tokens):
        # router 同步 chat（或 wrap async → sync via asyncio.run）
        return router.chat_completion_sync(messages, model, temperature, max_tokens)
    return chat_fn

adapter = ProviderRouterJudgeAdapter(chat_fn=make_chat_fn(provider_router))
trigger = LLMJudgeTrigger(adapter=adapter)
# runner_fn 内 wire trigger 到 score_tier1 调用链
```

## 2. Phase E 任务清单（tasks.md T-E-1 ~ T-E-6）

按 spec/plan 任务定义：

| Task | 内容 | 状态 |
|------|------|------|
| T-E-1 | pre-flight 检查：`git diff packages/ apps/gateway/ apps/web/ = 0`；Connor 4 task 已拍板（非 PLACEHOLDER）；控变量 Sonnet 4.5 | PENDING |
| T-E-2 | 执行 `octo-bench daily --label m5-baseline`（50 task × 3 × 8 并发） | PENDING（SC-001 ≤ 1h） |
| T-E-3 | 验证 SQLite ≥ 150 BenchmarkRun（AC1-3）；INCONSISTENT ≤ 5%（SC-011） | PENDING |
| T-E-4 | 存档 m5-baseline.json + m5-baseline.md（AC1-2/AC4-1/AC4-2） | PENDING |
| T-E-5 | efficiency baseline 写入 `scoring_rubrics.yaml`（W3） | PENDING |
| T-E-6 | SC-001 wall clock 时间记录 | PENDING |

## 3. Phase D 推迟项（Phase E 实施时回头补做的判断条件）

### A. scorer event binding（Round 6 P2-2，phase-c review 归档 → phase-d 评估归档）

- **决策点**：M5 baseline 跑通后，统计 5 Tier 3 task 的 false PASS / false FAIL 比例
- **判定**：
  - ≤ 5%：归档 M6 F108 Capability Layer Refactor，不在 Phase E 做
  - \> 5%：Phase E 后回头补做（YAML schema + scorer state machine 扩展）
- **触发实测的方法**：跑完 M5 baseline 后，手工跑 5 个 Tier 3 task 同样 3 次，对比
  scorer 结论与"真实运行下哲学是否成立"的人工判断

### B. LLM-judge fallback 真实路径（Phase B 推迟 #4）

- T-D-6 已建 ProviderRouterJudgeAdapter 框架（chat_fn DI 钩子）
- Phase E 实跑时 wire 真实 ProviderRouter.chat_completion 即可
- 单测 `test_provider_adapter_calls_chat_fn` 已覆盖契约

### C. Pass@1 order-aware + arguments-aware（Phase B 推迟 #2）

- 目前 `score_tier2_tau` 用 Counter 比较多重集（已修 Phase B HIGH "set 误判"）
- 完整 order-aware + arguments 比对需接 tau_bench env.step + user_simulator
- **触发实施的判断**：Phase E 跑后若 τ-bench pass_rate 与官方 leaderboard 差距 > 20%，
  补做（说明 Counter-only 太宽松）

### D. GAIA Unicode normalization（Phase B 推迟 #3）

- 在 `score_tier2_gaia` 内增强 `match_answer` 逻辑（`unicodedata.normalize NFKC`）
- **触发实施的判断**：Phase E 跑后若 GAIA pass_rate 异常低（< 30%）且抽样发现答案
  仅 Unicode 差异（如 "1,000" vs "1000"）→ 补做

### E. tau_bench env.step + user_simulator 真接入（Phase B 推迟 #5）

- 目前 `_make_tool_handler` 占位 stub，未实际驱动 tau_bench 的 `AirlineEnv.step()`
- Phase E 实跑前必须实现：在 `tau_bench_adapter._make_tool_handler` 中真接 `env.step`
  + Sonnet 4.6 user_simulator

## 4. Phase E 完成后的 Phase F 起点

- `completion-report.md`：逐 AC 标注 PASS/FAIL/PARTIAL
- `handoff.md` 给 F104（M6 第 1 Feature 文件工作台 v0.1）：M5 baseline 数据快照 + 
  M6 各 Feature 验收门槛建议
- 清理 `.specify/features/103d-octobench/poc/` 临时脚本

## 5. Phase E 真跑 1h 上限验证方法

控制变量：Sonnet 4.5 / temperature=0 / `--semaphore 8` / `--ramp 0.5s`。

| Tier | task 数 | 单 task 平均耗时上限 | 总耗时上限（含 3 iter） |
|------|---------|---------------------|----------------------|
| Tier 1 | 25 | 60s | 25 × 3 × 60s / 8 并发 ≈ 9.4 min |
| Tier 2 τ-bench | 15 | 120s | 15 × 3 × 120s / 8 ≈ 11.25 min |
| Tier 2 GAIA fallback | 5 | 300s | 5 × 3 × 300s / 8 ≈ 9.4 min |
| Tier 3 | 5 | 60s | 5 × 3 × 60s / 8 ≈ 1.9 min |
| **合计上限** | 50 | — | **~32 min**（远低 SC-001 60 min）|

如果实际超 50 min：
- 第一步：用 `--tier 1,3` 跑（去 Tier 2 GAIA fallback），看是否 GAIA 单 task 触发
  long timeout
- 第二步：检查 LLM rate-limit 是否密集（retry 累积 → 调高 `--ramp 1.0s`）
- 第三步：单 task 看 `octo-bench show m5-baseline | jq '.task_details | sort_by(.iterations[0].duration)' | tail`

## 6. Phase E rollback 准则

如果 M5 baseline 跑出来异常（pass_rate < 30% / INCONSISTENT > 20% / runtime > 2h）：

- 第一步：`octo-bench daily --runner my_octo_runner:runner_fn --tier 1 --iterations 1`
  跑最小子集 sanity check
- 第二步：单跑 1 个 task，看 OctoHarness wire 是否正确
- 第三步：如果是 Phase D 代码 bug → 回到 worktree 加单测 → 修复
- 第四步：如果是 OctoHarness wire bug → 在 runner_fn 内调
- 第五步：M5 baseline 不接受半通的数据；推迟到全通后再 commit `m5-baseline.json`

## 7. Phase D 完成不变量

Phase D 完成后下列不变量必须保持，Phase E 实施时不得破坏：

- ✅ `git diff packages/ apps/web/ = 0`
- ✅ `apps/gateway/pyproject.toml` 仅 3 行新增 `[project.scripts]` + entry point
- ✅ `apps/gateway/.../cli/bench_commands.py` 新增 (24 行 thin wrapper)
- ✅ 277 unit tests PASS（Phase A 16 + B 65 + C 74 + D 122）
- ✅ octoagent 全量回归 3674 PASS 0 regression vs ea9fad6
- ✅ e2e_smoke 8/8 PASS
- ✅ 触发常量 LLM_JUDGE_TRIGGER_MIN/MAX/MAX_CALLS_PER_TASK 锁死
- ✅ ConsecutiveInfraErrorCounter / asyncio.Lock 不跨 await

如果 Phase E wire OctoHarness 时不得不修改 `packages/` / `apps/gateway` 现有代码
（例如要 expose ProviderRouter 同步 chat），应单独走一个 Feature 而不是污染 F103d。
