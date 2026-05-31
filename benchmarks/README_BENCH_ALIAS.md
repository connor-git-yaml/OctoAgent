# OctoBench 控变量 LLM alias 配置指引

> Phase E 第 1 步（runner 进 git）+ 第 2 步（host 真跑）共用配置。
> 控变量决策见 CLAUDE.local.md §"Benchmark 控变量 LLM 配置（F103d OctoBench 默认）"。
> **本文档已按 2026-05-31 host 实跑结果修正**（api_base / alias schema / 启动方式 3 个坑）。

## TL;DR

`benchmarks/runner/octo_runner.py` 的 `runner_fn` 默认使用 model alias **`bench`**。
runner 不在 repo 内配置 alias（属于 instance 配置）。host 需在 `~/.octoagent/octoagent.yaml`
内加 `bench` alias 指向 SiliconFlow DeepSeek-V3.2，并确保 `SILICONFLOW_API_KEY` 在
`~/.octoagent/.env`。runner 起每个 task 时把真实 `~/.octoagent/octoagent.yaml` 复制到
tmp instance 并**把 main + cheap alias 重写到 bench**（控变量统一，task 默认走 main →
DeepSeek）。

## ⚠️ 三个实跑暴露的坑（务必先读）

| 坑 | 现象 | 解法 |
|----|------|------|
| **api_base 不能含 `/v1`** | provider_client.py:715 chat 路径用 `f"{api_base}/v1/chat/completions"` **硬加 /v1**；若 api_base 配 `https://api.siliconflow.cn/v1` → 拼出 double `/v1/v1/` → **404 Not Found** | api_base 配 `https://api.siliconflow.cn`（**不含 /v1**）。provider_client 自己加。`_build_responses_url` 智能处理 /v1，但 chat/embeddings/messages 都硬加——provider_client 不一致，见文末 known issue |
| **entry point shebang 坏** | `octo-bench` 命令 `command not found`（即使激活 venv）——entry point 脚本 shebang 写死了创建时的 worktree venv 路径 | 不用 `octo-bench` entry point，改用 `python -m benchmarks.runner.cli`（见下方启动命令） |
| **alias schema** | alias 配置字段是 `provider/model/description/thinking_level`，**不是** `temperature/max_tokens` | 按下方 Step 2 正确格式 |

## 一次性配置（M5 baseline + M6 各 Feature 共用）

### Step 1：确认 SiliconFlow provider（api_base 不含 /v1）

`~/.octoagent/octoagent.yaml` 的 `providers:` 段（list 格式）应有：

```yaml
providers:
- id: siliconflow
  name: SiliconFlow
  enabled: true
  transport: openai_chat
  api_base: https://api.siliconflow.cn      # ⚠️ 不含 /v1！provider_client 自己加
  auth:
    kind: api_key
    env: SILICONFLOW_API_KEY
```

### Step 2：在 `model_aliases` 段加 `bench` alias

```yaml
model_aliases:
  # 现有 main / cheap / rerank 保持不变（runner 会复制到 tmp 并重写 main→bench）
  bench:
    provider: siliconflow
    model: deepseek-ai/DeepSeek-V3.2        # SiliconFlow 实测可用（curl 200）
    description: F103d OctoBench 控变量 LLM（DeepSeek-V3.2，不污染 main/cheap/rerank）
    thinking_level: null
```

> **model id 说明**：CLAUDE.local.md 写 DeepSeek-V3.2。SiliconFlow 2026-05 实际可用的
> DeepSeek 系列含 `deepseek-ai/DeepSeek-V3.2`（curl 200 验证）、V3.1-Terminus、V3、
> V4-Flash、V4-Pro。控变量锁 V3.2。如 V3.2 下线，换 V3.1-Terminus（同档）。

### Step 3：确认 `SILICONFLOW_API_KEY` 在 `.env`

`~/.octoagent/.env`（**不在版本管理**，Constitution #5）：

```bash
SILICONFLOW_API_KEY=sk-xxxxxxxxxxxxxxxx
```

> ⚠️ key 若在对话/日志中明文出现过，benchmark 跑完务必去 SiliconFlow 控制台轮换。

### Step 4：验证配置（不起 OctoHarness）

```bash
REPO=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent
export SILICONFLOW_API_KEY=$(grep '^SILICONFLOW_API_KEY=' ~/.octoagent/.env | cut -d= -f2-)

# 4a. alias registry 能 resolve bench（返回 'bench' = 已注册，非 fallback 'main'）
$REPO/octoagent/.venv/bin/python -c "
from pathlib import Path
from octoagent.gateway.main import _build_runtime_alias_registry
reg = _build_runtime_alias_registry(Path.home()/'.octoagent')
print('bench resolve →', reg.resolve('bench'))
"

# 4b. model id 真能调用（HTTP 200）
curl -s -o /dev/null -w "%{http_code}\n" https://api.siliconflow.cn/v1/chat/completions \
  -H "Authorization: Bearer $SILICONFLOW_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-ai/DeepSeek-V3.2","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
# 期望 200
```

## 跑 M5 baseline（用 python -m，不用 octo-bench entry point）

```bash
REPO=/Users/connorlu/Desktop/.workspace2.nosync/OctoAgent
export SILICONFLOW_API_KEY=$(grep '^SILICONFLOW_API_KEY=' ~/.octoagent/.env | cut -d= -f2-)
COMMIT=$(git -C $REPO rev-parse HEAD)

# Tier 1 + 3 子集（30 task × 3 = 90 runs，不需要 tau_bench/datasets）
PYTHONPATH=$REPO OCTOAGENT_BENCH_ROOT=$REPO $REPO/octoagent/.venv/bin/python \
  -m benchmarks.runner.cli daily \
  --label m5-baseline-tier13 \
  --runner benchmarks.runner.octo_runner:runner_fn \
  --tier 1,3 --iterations 3 --semaphore 8 --skip-preflight \
  --commit $COMMIT

# 全 50 task（含 Tier 2 τ-bench + GAIA fallback）——先装依赖
#   uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets
# 然后去掉 --tier / --skip-preflight
```

> runner 每 task 起独立 OctoHarness（tmp data_dir 隔离），复制 `~/.octoagent/octoagent.yaml`
> 到 tmp 并重写 main+cheap→bench。`OCTOAGENT_BENCH_TEMPLATE_ROOT` env 可覆盖 instance 来源。

## 成本与耗时（2026-05-31 host 实测）

- 单 Tier 1 task 实测 ~28s（含 OctoHarness bootstrap + DeepSeek agent loop 多轮）
- 单 task input token ~66K（agent loop 多轮带完整 system prompt + tools schema），output ~700
- 90 runs（Tier1+3 × 3）实测 wall clock：见 `benchmarks/baselines/m5-baseline-tier13.md`
- 成本估算：~6M input × $0.14/M + ~0.06M output × $0.28/M ≈ **$0.85 / 跑一次 Tier1+3**
- 不需要 Docker（OctoHarness 内存起，task 不进 Docker 沙箱）

## 故障排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `octo-bench: command not found` | entry point shebang 写死 worktree 路径 | 用 `python -m benchmarks.runner.cli` |
| 全 task INFRA_ERROR，日志 `404 Not Found model=...` | api_base 含 /v1 → double /v1 | Step 1：api_base 去 /v1 |
| 全 task INFRA_ERROR，`KeyError: 'bench'` | bench alias 没配 | Step 2 |
| LLM 调用 `Provider auth failed` | SILICONFLOW_API_KEY 未 export 进进程 env | 跑前 `export SILICONFLOW_API_KEY=...` |
| task 走 echo fallback（reply 含"模型调用连续失败"） | model 404 / key 失效 | Step 4 验证 model + key |
| `QUOTA_SKIP` 连续 5 → 停 | SiliconFlow 限流 | 等几分钟 `--resume <session_id>` |

## 已知 production 缺陷（本次实跑发现，记录待修）

1. **provider_client `/v1` 拼接不一致**（octoagent/packages/provider/.../provider_client.py）：
   `_build_responses_url`(L152) 智能处理 api_base 是否含 /v1，但
   `chat`(L715) / `embeddings`(L859) / `messages`(L1075) 都硬编码 `f"{api_base}/v1/..."`。
   配 api_base 含 /v1 时 chat 路径 double /v1 → 404。**根治**：让 chat/embeddings/messages
   也走智能 /v1 处理（像 _build_responses_url）。当前 workaround：instance api_base 去 /v1。
2. **watchdog RepeatedFailureDetector datetime bug**：多 task 并发跑时刷
   `watchdog_detector_error "can't compare offset-naive and offset-aware datetimes"`。
   不影响 task verdict（后台监控 warning），但污染日志。
3. **session_memory_extraction shutdown race**：task 完成后 harness shutdown 时
   memory extraction LLM 调用偶发 `no active connection`（DB 已关）。不影响 verdict。

> 上述 3 项均为 production 既有缺陷（benchmark 首次大规模真跑 siliconflow chat 才暴露），
> 不属于 benchmark runner bug。建议独立 Feature / spawn task 修复（不阻塞 M5 baseline）。

## 不要做的事

- ❌ api_base 配 `https://api.siliconflow.cn/v1`（double /v1 → 404）
- ❌ 用 `octo-bench` entry point（shebang 坏；用 python -m）
- ❌ 把 `SILICONFLOW_API_KEY` 写进 octoagent.yaml（密钥必须在 .env）
- ❌ 把 `bench` alias 用作 production main/cheap（污染生产）
- ❌ 在 worktree 内执行（`OCTOAGENT_BENCH_ROOT` 必须指 master 根）
