# F103d → M6 Handoff（OctoBench baseline + 验收门槛）

> 写于：2026-05-31（Phase F）
> 给：M6 全部 Feature（F104 起）
> **注意**：本文是 F103d **整体** → M6 的 handoff。Phase D→E 中间 handoff 已归档为 `handoff-phase-d-to-e.md`。

---

## 1. M5 baseline 快照（M6 各 Feature 对比锚点）

**状态**：preliminary（Tier1+3 / 1-iter / DeepSeek-V3.2 控变量）。文件：
- `benchmarks/baselines/preliminary-m5-tier13-1iter-deepseek.json`
- `benchmarks/baselines/preliminary-m5-tier13-1iter-deepseek.md`

| 指标 | 值 |
|------|-----|
| total_tasks | 30（Tier1 25 + Tier3 5）|
| pass_rate | **0.276** |
| weighted_score | 0.300 |
| tier1 | 0.333 (8/25) |
| tier3 | 0.000 (0/5) |
| duration | 44.1 min |
| token | input 2.49M / output 39K |

**各域**：tool_call 100% / connor_real_world 75% / snapshot 50% / user_md 33% / 其余（delegation/memory/philosophy/threat_scanner/skill_pipeline/routine）0%。

---

## 2. 能力画像三分类（M6 验收必读）

| 类别 | 域 | M6 用法 |
|------|-----|---------|
| **扎实**（scorer 公正 + 能力过硬）| tool_call 100% / connor_real_world 75% / snapshot 50% | **M6 回归护栏**：F108 重构后这三域不掉 = 零 regression 证据 |
| **控变量限制**（DeepSeek 画像非缺陷）| delegation/max_depth/philosophy 全 0% | DeepSeek 从不主动 delegate_task。换 production LLM 复跑才有真数据；M6 改进这些域需用强 model 验证 |
| **scorer/task 待修**（false FAIL）| threat_scanner 0% / memory 部分 | threat_scanner task 需重设计（见 §4 L3）；memory Phase F 已修 namespace 断言 |

---

## 3. M6 各 Feature regression 警戒线建议

跑 M6 对比用 `octo-bench daily --compare` + DeepSeek-V3.2 同控变量：

- **扎实域硬护栏**：tool_call < 90% / connor_real_world < 65% / snapshot < 40% → **触发 code review**（疑似 regression）
- **weighted_score 总体** < 0.27（baseline 0.30 - 0.03）→ 触发 review
- **控变量限制域**：不设警戒线（DeepSeek 本就 0%，M6 改进要用强 model 单独验证）
- **跑对比前必做**：先修 threat_scanner task（否则永远假 0 污染 delta）

---

## 4. M6 启动前 backlog（F103d 遗留，不阻塞 M6 但建议尽早清）

| # | 项 | 工作量 | 跑 M6 对比前必做？ |
|---|-----|--------|-------------------|
| L1 | 性能：OctoHarness 轻量 bootstrap（tool registry importlib scan 抽到 runner 进程级，octo_runner.py 已是全局单例）| 1-1.5 天 + review | 否（但全量跑前必做）|
| L2 | Tier2 τ-bench 真跑接入（runner_fn Tier2 分派 + env.step + user_simulator）| 3-5 天 | 否 |
| L3 | threat_scanner 2 task 重设计：prompt 诱导 memory 写入触发 scan + 断言改 `MEMORY_ENTRY_BLOCKED`（payload 含 pattern_id / severity=BLOCK / input_content_hash）| <0.5 天 | **是**（否则假 0 污染）|

---

## 5. 控变量 LLM 配置（M6 复用）

见 CLAUDE.local.md §"Benchmark 控变量 LLM 配置"：
- Provider：SiliconFlow（octoagent.yaml 已配，api_base 末尾 **不带 /v1**——a6b51fc 修复后 provider_client 幂等处理，但 instance 配置仍建议不带）
- Model：`deepseek-ai/DeepSeek-V3.2`，temperature=0
- bench alias：octoagent.yaml model_aliases 加 `bench` → siliconflow/deepseek-ai/DeepSeek-V3.2（不污染 main/cheap）
- Key：`~/.octoagent/.env` SILICONFLOW_API_KEY（不进版本管理）

---

## 6. F104 文件工作台 v0.1 启动纠偏（重要）

端到端 review + workflow 查证：**F104 原假设"纯 UI 复用 SnapshotStore"不成立**。

- **SnapshotStore**（`gateway/harness/snapshot_store.py`）只服务 prefix-cache 冻结快照，**无 history / diff 能力**
- artifact `version` 字段只是计数器，**旧版本内容不可取**（artifact_store 不存历史版本）
- 所以 F104 v0.1 **必须动 backend**：spec 第一决策点 = 版本历史存储方案（扩 artifact_store 存历史版本 / 或 workspace git 化）
- v0.1 范围收窄：单文件"上一版 vs 当前版"diff；branch/blame 推 F107（文件工作台 v0.2）

---

## 7. octo-bench CLI 用法（M6 复用）

```bash
# 跑 baseline（host，需 SILICONFLOW_API_KEY）
octo-bench daily --label <m6-feature-name> --runner benchmarks.runner.octo_runner:runner_fn \
  --commit $(git rev-parse HEAD) --iterations 3 --semaphore 8 --model bench

# 对比 M5 baseline
octo-bench daily --compare <m5-baseline-label> ...

# 列出 baseline / 看详情
octo-bench list-baselines
octo-bench show <session_id>
```

依赖：`uv pip install "git+https://github.com/sierra-research/tau-bench.git" datasets`（Tier2 用；保持手动不进 pyproject）。
