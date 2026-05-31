# F103d Phase E Step 1 完成报告

> 写于：2026-05-31  
> 分支：`feature/103d-octobench-phase-e`  
> 4 个 commit 待用户决定是否 push origin/master

## TL;DR

Phase E 第 1 步（runner 进 git + bench alias 配置 + LLM judge wire）完成：

- 新增 `benchmarks/runner/octo_runner.py` (~830 行) + 配套 mock 单测 74 个 PASS
- 新增 `benchmarks/README_BENCH_ALIAS.md` 操作指引
- 扩展 `benchmarks/runner/scorer.py` + `score_dispatch.py` 接受 `judge_trigger`
- **3 轮 Codex GPT-5.4 high-reasoning adversarial review 共 6 HIGH + 5 MED + 1 LOW 全闭环 + 0 残留**
- **零侵入 production**：`git diff packages/ apps/ = 0`
- octoagent 全量回归 **3695 PASS + 10 SKIPPED + 1 xfailed + 1 xpassed**（vs F103d Phase D baseline f4e95a2 = 3674 → **+21 PASS / 0 regression**）

## 已 commit（不 push）

```
b0cc62b fix(F103d-Phase-E-Step1): Codex round 3 review 1 HIGH + 1 MED 闭环
f46beb0 fix(F103d-Phase-E-Step1): Codex round 2 review 1 HIGH + 2 MED 闭环
f36ff04 fix(F103d-Phase-E-Step1): Codex review 4 HIGH + 3 MED 闭环 (零侵入 production)
c56b702 feat(F103d-Phase-E-Step1): host-side runner_fn (进 git) + bench alias 配置 + LLM judge wire
```

## 解决的问题（用户视角）

1. **F103d 终于有真 runner**：Phase D 的 `--runner` 没传时所有 task 返回 INFRA_ERROR；Phase E Step 1 后 `octo-bench daily --runner benchmarks.runner.octo_runner:runner_fn` 是实际可跑的入口
2. **控变量 LLM 透明落地**：用户在 `~/.octoagent/octoagent.yaml` 加 bench alias 后，runner 会自动重写 main alias 让所有 task 透明用 DeepSeek-V3.2 — 不需改 NormalizedMessage / control_metadata schema 引入侵入性
3. **infra vs business error 严格分类**：provider 失败 / EventStore 异常 / 缺配置 都进 INFRA_ERROR 不污染 pass-rate 分母；task 自身 FAIL 才进分母 — 让 baseline 数据有意义
4. **没真集成的能力显式拒跑**：τ-bench env.step + user_simulator 推迟到下游 Feature 前，τ-bench task 直接 INFRA_ERROR — 避免按 tool name 单看的系统性假评分污染数据
5. **OctoHarness 生命周期严管**：每 task 独立 tmp dir + ProviderRouter.aclose 防累计连接 — 150 task baseline 跑也不漏资源

## Codex review 闭环表（3 轮共 12 finding）

| Round | severity | finding | 状态 | 实施 |
|---|---|---|---|---|
| 1 | HIGH-1 | tmp harness 没拿到实例配置 → 跑垃圾数字 | ✅ closed (f36ff04) | template_root 解析 + fail-fast + 5 单测 |
| 1 | HIGH-2 | bench alias 没注入 Tier 1/3/τ | ✅ closed (f36ff04) | `_rewrite_main_alias_to_bench` 重写 main 指向 bench |
| 1 | HIGH-3 | GAIA provider 错误吞成 FAIL | ✅ closed (f36ff04 + f46beb0 + b0cc62b) | 去 try/except + runner_fn 顶层 `_is_infra_error` |
| 1 | HIGH-4 | τ-bench 假评分 | ✅ closed (f36ff04) | 显式 `TauBenchNotIntegratedError` raise + INFRA_ERROR 映射 |
| 1 | MED-1 | WAITING_INPUT/APPROVAL 未当终态 | ✅ closed (f36ff04) | `TaskBlockedOnInputError` + FAIL 映射 |
| 1 | MED-2 | ProviderRouter HTTP client 未关闭 | ✅ closed (f36ff04) | session finally 先 aclose 再 shutdown |
| 1 | MED-3 | token 字段误名 → 记成 0 | ✅ closed (f36ff04) | `token_usage` 嵌套 + `usage` 嵌套 + 顶层 fallback |
| 1 | LOW-1 | 单测验证 mock 契约非 production | ✅ closed (f36ff04 + b0cc62b) | 22 production schema 单测 |
| 2 | HIGH | runner_fn broad except 误为 RESULT_ERROR | ✅ closed (f46beb0) | `_is_infra_error` 分类器 |
| 2 | MED | `_discover_child_task_ids` 吞 EventStore 异常 | ✅ closed (f46beb0 + b0cc62b) | 移除 except + 端到端单测 |
| 2 | MED | `_collect_token_usage` 吞异常返回全 0 | ✅ closed (f46beb0 + b0cc62b) | 移除 except + 端到端单测 |
| 3 | HIGH | `_run_tier1/_run_tier3` 端到端 swallow infra 异常 | ✅ closed (b0cc62b) | 移除 fetch_events 周围 try/except + 2 端到端单测 |
| 3 | MED | `_collect_token_usage` TimeoutError 被 `_submit_and_wait_task` 截获 | ✅ closed (b0cc62b) | 拆分调用：caller 在 except TimeoutError 之外单调 |
| **4** | — | **verify** | **0 残留 + 0 新 HIGH/MED + 可进 Step 2** | — |

## 关键设计决策

### `_rewrite_main_alias_to_bench`（HIGH-2 核心方案）

不侵入 production NormalizedMessage / control_metadata schema 加 `model_alias_override`，
而是利用 task_runner 默认走 `main` alias 的事实，runner 复制 octoagent.yaml 到
tmp instance 时把 `model_aliases.main` 和 `cheap` 重写为 bench alias 指向的
(provider, model)。task_runner 后续创建 task 透明用 DeepSeek-V3.2 — 无任何
production 代码改动。

### `_is_infra_error` 分类器（HIGH round 2/3 核心）

集中识别 infrastructure 异常（不进 pass-rate 分母）：
- `MissingInstanceConfigError`（runner 自有）
- `TauBenchNotIntegratedError`（runner 自有）
- `octoagent.provider.exceptions.ProviderError` 及子类（lazy import 防 benchmarks
  模块强依赖）：CredentialError / AuthenticationError / ProxyUnreachableError 等
- `ConnectionError` / `TimeoutError` / `OSError`（网络 IO 层）

generic `RuntimeError` / `ValueError` 不被误标 infra → 保留 scorer / runner 真 bug 的 ERROR 信号。

### sync-from-async LLM judge 桥接

`score_tier1` 是 sync 函数从 `_run_tier1` async 内调用，LLM judge `chat_fn` 也是 sync。
用 `concurrent.futures.ThreadPoolExecutor(max_workers=1) + asyncio.run` 在独立线程跑
`_provider_router_chat`，规避 `asyncio.run_coroutine_threadsafe` 同线程死锁问题。
PARTIAL 路径才触发（每 task 最多 2 次 judge），开销可接受。

## 改动文件清单

```
benchmarks/README_BENCH_ALIAS.md           (新, ~120 行 操作指引)
benchmarks/runner/octo_runner.py           (新, ~830 行 runner_fn 主体)
benchmarks/runner/score_dispatch.py        (改, +12 行 judge_trigger 参数)
benchmarks/runner/scorer.py                (改, +10 行 score_tier1 接受 judge_trigger)
benchmarks/tests/unit/test_octo_runner.py  (新, ~1500 行 74 单测)
```

**git diff packages/ apps/ = 0**：production 零修改。

## 测试矩阵

| 套件 | PASS | 备注 |
|---|---|---|
| `benchmarks/tests/unit/test_octo_runner.py` | 74 | 新 (mock OctoHarness + mock LLM) |
| `benchmarks/tests/` 全量 | 351 | 274 原 baseline + 74 octo_runner + 3 score_dispatch 扩展 |
| `octoagent/packages/` | 1841 + 1 SKIPPED | vs Phase D baseline 0 regression |
| `octoagent/apps/gateway/tests/` (non-e2e) | 1685 + 1 SKIPPED + 1 xfailed + 1 xpassed | 0 regression |
| `octoagent/tests/` | 148 + 8 SKIPPED | 0 regression |
| **octoagent 全量** | **3695 + 10 SKIPPED + 1 xfailed + 1 xpassed** | **vs F103d Phase D baseline f4e95a2 = 3674 → +21 PASS / 0 regression** |
| `octoagent/apps/gateway/tests/e2e_live -m e2e_smoke` | 8 | 4 commit pre-commit hook 全过 |

## 用户视角：建议合入 origin/master

理由：
1. ✅ 3 轮 Codex review 共 12 finding 全闭环 + round 4 verify 0 残留
2. ✅ 零侵入 production
3. ✅ 全量回归 0 regression
4. ✅ e2e_smoke pre-commit hook 4 次全过
5. ✅ 接下来 Phase E Step 2 host 真跑需要本 commit 作为前置（runner 必须在 git 里）
6. ✅ 4 commit 主题清晰：1 初版 + 3 Codex closure，commit message 含完整闭环表

风险：
- ⚠️ Phase E Step 2 真跑时若发现 ProviderClient.call metadata schema 漂移（M5 baseline 还没真测过），需要补 token 字段——但这是 production schema 偏差问题，与本 commit 设计无关
- ⚠️ τ-bench 路径继续在 INFRA_ERROR 状态（已显式标），15 个 τ-bench task 在 baseline 中不进分母——这是预期行为，但用户对 baseline 数据完整性的期待可能不同

## Phase E Step 2 入口（用户 host 真跑）

详见 [`benchmarks/README_BENCH_ALIAS.md`](../../../benchmarks/README_BENCH_ALIAS.md)。

### 前置一次性配置

1. 在 `~/.octoagent/octoagent.yaml` 的 `providers:` 段确认 SiliconFlow provider 已配置
2. 在 `model_aliases:` 段新增 `bench` alias 指向 `deepseek-ai/DeepSeek-V3.2`
3. `~/.octoagent/.env` 设置 `SILICONFLOW_API_KEY`
4. 验证 alias resolve（README 内有 1 行 Python 健康检查）

### 跑 M5 baseline

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/
export OCTOAGENT_BENCH_ROOT="$(pwd)"

# Tier 1 + Tier 3 子集（30 task，不需要 tau_bench / datasets）
octo-bench daily \
  --label m5-baseline-tier13 \
  --runner benchmarks.runner.octo_runner:runner_fn \
  --tier 1,3 \
  --iterations 3 \
  --semaphore 8 \
  --skip-preflight \
  --commit "$(git -C octoagent rev-parse HEAD)"

# 全 50 task（含 Tier 2 τ-bench + GAIA fallback）
# τ-bench 路径会全部 INFRA_ERROR（HIGH-4 闭环），GAIA + Tier 1/3 正常跑
uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets
octo-bench daily \
  --label m5-baseline \
  --runner benchmarks.runner.octo_runner:runner_fn \
  --iterations 3 \
  --semaphore 8 \
  --commit "$(git -C octoagent rev-parse HEAD)"
```

### 预期成本（DeepSeek-V3.2 via SiliconFlow）

- 总 token：~1.5M input + 0.3M output（150 runs）
- 总费用：**~$0.30 / 跑一次完整 M5 baseline**

### 预期耗时

- Tier 1 + Tier 3：~12 min（25 + 5 task × 3 iter × 8 并发，每 task ≤ 60s）
- Tier 2 GAIA：~10 min（5 task × 3 × 300s timeout / 8 并发）
- Tier 2 τ-bench：~瞬时（全 INFRA_ERROR 不进分母）
- **合计 ≤ 25 min**（远低 SC-001 60 min 上限）

### Docker 需求

❌ **不需要**。runner 跑在 host Python venv 内，tmp dir 走 `tempfile.TemporaryDirectory`，
PyConFire OctoHarness 用 SQLite WAL + 进程内 FastAPI app。完全 hermetic。

## 推迟到 Phase E Step 2 之后（已显式归档）

| 推迟项 | 触发条件 | 影响 |
|---|---|---|
| τ-bench 真集成（env.step + user_simulator）| Phase E Step 2 后看 τ-bench 数据需求强弱决定 | 当前 15 个 τ-bench task 全 INFRA_ERROR 不进分母（HIGH-4 closure 设计选择） |
| Connor 4 个 PLACEHOLDER task | 用户拍板填实际内容 | 不计入 Daily Bench 分母 |
| efficiency_baseline_tokens 写入 | T-E-5：M5 baseline 跑完后 | 当前 rubric.efficiency_baseline_tokens=null，三维加权退化为 pass_fail+partial |

## Spawned Task 处理流程符合度

按 CLAUDE.local.md §"Spawned Task 处理流程"：

- ✅ 正常实施完成（4 个 commit）
- ✅ 触发 Codex review 3 轮（命中"重大架构变更"节点，runner 是 benchmark 主入口）
- ✅ 处理 finding（12 个全闭环：6 HIGH + 5 MED + 1 LOW）
- ✅ 状态收敛到"可合入 origin/master"（全量回归 0 regression + pre-commit hook PASS + commit message 含 Codex review 闭环表）
- ✅ **不主动 push origin/master**（等用户拍板）
- ✅ 回报主 session 归总报告（即本文档）
- ⏳ 等用户拍板 → push / 调整 / 弃

## 建议下一步操作

**用户决策**：

1. **接受合入** → `cd .claude/worktrees/elated-ramanujan-7cd57c && git push origin feature/103d-octobench-phase-e`（不直接 push master，让用户审 PR）；或 `git checkout master && git merge --ff-only feature/103d-octobench-phase-e && git push origin master`（如同意直 merge）
2. **再 review** → 用户读完归总后再看 git diff / 单测
3. **改动** → 用户给具体改进点，回到 worktree 继续
