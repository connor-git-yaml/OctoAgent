# OctoBench 控变量 LLM alias 配置指引

> Phase E 第 1 步（runner 进 git）+ 第 2 步（用户 host 真跑）共用配置。
> 控变量决策见 CLAUDE.local.md §"Benchmark 控变量 LLM 配置（F103d OctoBench 默认）"

## TL;DR

`benchmarks/runner/octo_runner.py` 的 `runner_fn` 默认使用 model alias **`bench`**。
runner 不在 repo 内配置 alias（属于 instance 配置）。用户需在 `~/.octoagent/octoagent.yaml`
内加一个 `bench` alias，指向 SiliconFlow DeepSeek-V3.2。

## 一次性配置（M5 baseline + M6 各 Feature 共用）

### Step 1：确认 SiliconFlow provider 已配置

在 `~/.octoagent/octoagent.yaml` 的 `providers:` 段下应已存在：

```yaml
providers:
  siliconflow:
    transport: openai_chat
    api_base: https://api.siliconflow.cn/v1
    auth:
      kind: api_key
      env: SILICONFLOW_API_KEY
```

如已存在跳过；如不存在则按上述模板新增。

### Step 2：在 `model_aliases` 段新增 `bench` alias

在 `~/.octoagent/octoagent.yaml` 的 `model_aliases:` 段下追加：

```yaml
model_aliases:
  # 现有 main / cheap / rerank 等保持不变
  main:
    # ... 现有配置 ...

  # F103d OctoBench 控变量 LLM（不污染 production main/cheap）
  bench:
    provider: siliconflow
    model: deepseek-ai/DeepSeek-V3.2
    temperature: 0.0
    max_tokens: 2048
```

**不要把 bench alias 用作 production model**——它只服务 benchmark runner。

### Step 3：确认 `SILICONFLOW_API_KEY` 在 `.env`

`~/.octoagent/.env`（**不在版本管理**，由 Constitution #5 保护）：

```bash
SILICONFLOW_API_KEY=sk-xxxxxxxxxxxxxxxx
```

key 来源：在 [SiliconFlow 控制台](https://cloud.siliconflow.cn) 申请。
若 key 在对话上下文中泄漏过，benchmark 跑完建议轮换。

### Step 4：验证 alias 可用

最简单的健康检查（不需要起 OctoHarness）：

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/
python - <<'PY'
import asyncio
from pathlib import Path

import sys
sys.path.insert(0, str(Path('.').resolve() / 'octoagent' / 'apps' / 'gateway' / 'src'))
sys.path.insert(0, str(Path('.').resolve() / 'octoagent' / 'packages' / 'core' / 'src'))

from octoagent.provider.routing.alias_registry import AliasRegistry, build_alias_registry
project_root = Path.home() / '.octoagent'
registry: AliasRegistry = build_alias_registry(project_root)
resolved = registry.resolve_alias('bench')
print('bench alias →', resolved)
PY
```

期望输出：

```
bench alias → ResolvedAlias(provider='siliconflow', model='deepseek-ai/DeepSeek-V3.2', ...)
```

如报 `KeyError: bench` → 检查 Step 2 是否正确写入 `model_aliases`.

## 第 2 步：跑 M5 baseline

凭证 + alias 就绪后，host 上的命令：

```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/
export OCTOAGENT_BENCH_ROOT="$(pwd)"

# 可选：覆盖默认 model alias（runner_fn 默认读 OCTOAGENT_BENCH_MODEL or 'bench'）
# export OCTOAGENT_BENCH_MODEL=bench

# 跑 Tier 1 + Tier 3 子集（不需要 tau_bench / datasets）
octo-bench daily \
  --label m5-baseline-tier13 \
  --runner benchmarks.runner.octo_runner:runner_fn \
  --tier 1,3 \
  --iterations 3 \
  --semaphore 8 \
  --skip-preflight \
  --commit "$(git -C octoagent rev-parse HEAD)"

# 跑全 50 task（含 Tier 2 τ-bench + GAIA fallback）
# 必须先：uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets
octo-bench daily \
  --label m5-baseline \
  --runner benchmarks.runner.octo_runner:runner_fn \
  --iterations 3 \
  --semaphore 8 \
  --commit "$(git -C octoagent rev-parse HEAD)"
```

## 预期成本（DeepSeek-V3.2 via SiliconFlow）

> 数据来源：DeepSeek-V3.2 公开定价（2026-Q2）：输入 ~0.14 USD / M token，输出 ~0.28 USD / M token

50 task × 3 iterations = 150 runs。

- Tier 1（25 task × 3）= 75 runs，单 task 平均 ~3-8K input + 1-2K output tokens
- Tier 2 τ-bench（15 task × 3）= 45 runs，多轮交互，单 task 平均 ~15-30K input + 3-5K output
- Tier 2 GAIA（5 task × 3）= 15 runs，单轮答题，~1-2K input + 0.5-1K output
- Tier 3（5 task × 3）= 15 runs，单 task 平均 ~5-10K input + 1-3K output

总 token 上限估算：
- input ~1.5M token × 0.14 USD/M = 0.21 USD
- output ~0.3M token × 0.28 USD/M = 0.084 USD
- **合计 ~$0.30 / 跑一次完整 M5 baseline**

LLM-as-judge（PARTIAL 路径，最多 2 次/task）额外少量 tokens；可忽略。

## 第 2 步真跑成功的关键 checklist

- [ ] `~/.octoagent/octoagent.yaml` 含 `bench` alias 指向 SiliconFlow DeepSeek-V3.2
- [ ] `~/.octoagent/.env` 含 `SILICONFLOW_API_KEY`
- [ ] Step 4 alias resolve 验证通过
- [ ] `git diff packages/ apps/ = 0`（runner 改动只在 benchmarks/）
- [ ] tau_bench / datasets 已安装（如跑全 50 task；若 `--tier 1,3 --skip-preflight` 不需要）
- [ ] 当前 master HEAD = F103c 收尾 commit（M5 baseline 是 F103c baseline，非 Phase D 工程改动后）

## 不要做的事

- ❌ 把 `bench` alias 与 `main` / `cheap` 混用（污染 production）
- ❌ 把 `SILICONFLOW_API_KEY` 写进 octoagent.yaml（密钥必须在 .env）
- ❌ 用 ANTHROPIC_API_KEY 跑 M5 baseline（默认 runner_fn 读 bench alias；要换得改 octo_runner.DEFAULT_BENCH_MODEL_ALIAS）
- ❌ 在 worktree 内（如 `.claude/worktrees/.../`）执行——`OCTOAGENT_BENCH_ROOT` 必须指 master 根
- ❌ 跳过 Step 4 验证就开跑（alias 拿不到 → 全部 task 报 INFRA_ERROR）

## 故障排查

| 症状 | 可能原因 | 解决 |
|------|---------|------|
| 所有 task 返回 `INFRA_ERROR`，error 含 `KeyError: 'bench'` | alias 未在 octoagent.yaml | 走 Step 2 |
| 所有 task 返回 `INFRA_ERROR`，error 含 `Provider auth failed` | SILICONFLOW_API_KEY 未配 / 失效 | 走 Step 3 |
| 部分 task 返回 `QUOTA_SKIP`，连续 5 后停止 | SiliconFlow 限流 | 等几分钟重跑 `--resume <session_id>` |
| 全部 Tier 2 τ-bench 返回 `INFRA_ERROR` 含 `tau_bench not installed` | 缺包 | `uv pip install "git+https://github.com/sierra-research/tau-bench.git"` |
| 跑 1h+ 还在跑 | semaphore 过低 / GAIA 单 task 超时 | 看 handoff §5 上限验证表 |
