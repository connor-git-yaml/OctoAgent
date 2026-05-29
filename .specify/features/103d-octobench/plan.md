# F103d — OctoBench 实施计划（Plan）

> 上游：spec.md（GATE_DESIGN 已拍板）/ tech-research.md / clarifications.md / quality-checklist.md
> Phase 顺序：0（PoC）→ A → B → C → D → E → F
> Codex review：pre-impl（本 plan 完成后）+ 每 Phase 末（A/B/C/D）+ Phase E 末 Final cross-Phase

---

## 元数据

| 字段 | 值 |
|------|----|
| feature | F103d OctoBench |
| spec_ref | `.specify/features/103d-octobench/spec.md` |
| created | 2026-05-27 |
| baseline_commit | `a69fe9c`（F103c 收尾后） |
| 总体复杂度 | HIGH（spec §8 评估：7 组件 / 8 接口 / 2 新依赖 / 2 复杂度信号）|
| Phase 数量 | 7（Phase 0 + A + B + C + D + E + F）|

---

## 0. 架构总览

### benchmarks/ 目录结构

```
benchmarks/
├── __init__.py
├── conftest.py              # module singleton reset + hermetic env（复用 F087 e2e_live 模式）
├── runner/
│   ├── __init__.py
│   ├── worker.py            # 单 task 执行：OctoHarness DI 注入 / 超时 / 重试
│   ├── scorer.py            # 统一评分接口（Tier 1/2/3 三层）+ LLM judge fallback
│   ├── reporter.py          # JSON + Markdown 报告生成 + --compare diff 逻辑
│   ├── store.py             # SQLite BenchmarkRun / BenchmarkBaseline 持久化
│   └── scoring_rubrics.yaml # ScoringRubric 持久化（W9 决策：YAML，基于 spec §4 结构）
├── tiers/
│   ├── tier1/               # 25 个 YAML task 文件
│   │   └── t1_*.yaml
│   ├── tier2/
│   │   ├── tau_bench_adapter.py   # airline domain + Tool Registry 临时注册
│   │   └── gaia_adapter.py        # HF GAIA L2 加载 + normalized 匹配
│   └── tier3/               # 5 个 YAML task 文件（H1/H2/H3-A/H3-B/H3）
│       └── t3_*.yaml
└── baselines/               # 产出目录（gitignored 或纳入 git 视用户决定）
    ├── m5-baseline.json     # Phase E 产出（符号链接或直接文件）
    └── {sha}-{ts}.json      # 历史快照

apps/gateway/src/octoagent/gateway/cli/
└── bench_commands.py        # CLI 薄层（≤ 30 行，from benchmarks.runner import ...）
                             # 【新增文件，不修改现有文件内容，符合 FR-H01 + IA-2 决策】
```

### 与 OctoAgent core 的边界

```
benchmarks/ (纯观测层)
    │
    │  OctoHarness DI 钩子（credential_store / llm_adapter / mcp_servers_dir / data_dir）
    ▼
OctoHarness (F087, 不修改)
    │
    │  bootstrap → yield → shutdown
    ▼
OctoAgent production 路径（packages/ / apps/gateway/，不修改任何现有文件）
    │
    │  EventStore query（只读）
    ▼
scorer.py → reporter.py → SQLite baselines/
```

### CLI 入口注册方式（IA-2 解决）

`bench_commands.py` 是**新增文件**，通过 `pyproject.toml` 的 `[project.scripts]` 或 Typer `app.add_typer` 注册。如需修改现有 `cli/__init__.py` 引入 `bench_commands`，须确认该文件是否为"现有文件"——若是，则改用独立入口点（`octo-bench` 独立命令），不修改任何现有 `cli/` 文件。**Phase D 实施前由 implement 工程师 grep 确认后选择路径，plan 预留两个方案**：

- 方案 A（优先）：`pyproject.toml` 新增 entry point `octo-bench = "octoagent.gateway.cli.bench_commands:app"`
- 方案 B（备选）：复用现有 `octo` CLI group，修改现有 `__init__.py`（需提前确认是否属于 FR-H01 例外）

---

## 1. 文件清单（Plan-First 原则）

### 1.1 新增文件（完整列表）

| 文件路径 | 估算 LOC | Phase |
|---------|---------|-------|
| `benchmarks/__init__.py` | 5 | Phase D |
| `benchmarks/conftest.py` | 60 | Phase D |
| `benchmarks/runner/__init__.py` | 5 | Phase D |
| `benchmarks/runner/worker.py` | 150 | Phase D |
| `benchmarks/runner/scorer.py` | 200 | Phase A/B/C/D 整合 |
| `benchmarks/runner/reporter.py` | 120 | Phase D |
| `benchmarks/runner/store.py` | 100 | Phase D |
| `benchmarks/runner/scoring_rubrics.yaml` | 50 | Phase A |
| `benchmarks/tiers/tier1/t1_*.yaml` × 25 | ~20/文件 = 500 | Phase A |
| `benchmarks/tiers/tier2/tau_bench_adapter.py` | 180 | Phase B |
| `benchmarks/tiers/tier2/gaia_adapter.py` | 100 | Phase B |
| `benchmarks/tiers/tier3/t3_*.yaml` × 5 | ~25/文件 = 125 | Phase C |
| `apps/gateway/src/octoagent/gateway/cli/bench_commands.py` | 30 | Phase D |
| `.specify/features/103d-octobench/phase-0-poc-report.md` | 60 | Phase 0 |
| `benchmarks/baselines/m5-baseline.json` | 产出文件 | Phase E |
| **合计（代码）** | **~1625 LOC** | |

### 1.2 不动文件（FR-H01 零侵入守卫）

以下文件**绝对不能修改**（每 Phase 完成后 `git diff -- packages/ apps/gateway/ apps/web/` 验证 0 变更）：

- `packages/core/src/octoagent/core/models/` 下所有文件
- `apps/gateway/src/octoagent/gateway/services/` 下所有文件
- `apps/gateway/src/octoagent/gateway/harness/` 下所有文件
- `apps/web/` 下所有文件
- 任何现有 `conftest.py`（benchmark 使用独立 `benchmarks/conftest.py`）

### 1.3 修改文件

| 文件 | 修改内容 | 备注 |
|------|---------|------|
| `pyproject.toml`（apps/gateway 级） | 新增 `octo-bench` 或 `octo bench` entry point 注册（方案 A/B 选一）| 仅新增字段，不修改现有字段 |

**修改文件总计：1 个文件（仅新增 entry point 行，不修改现有内容）。**

---

## 2. Phase 0 PoC 详细方案

### 2.1 目标

手工验证 4 个关键假设（+ W6 新增并发压测 AC），产出 `phase-0-poc-report.md`，用户拍板后进 Phase A。

### 2.2 5 个 PoC task 规格（GATE_DESIGN 已拍板：1 T1 + τ-bench + GAIA + 1 T3 + 1 并发压测）

**W6 解决**：spec AC2-1 的"并发压测"对应本 plan 新增 AC2-1b：5 task 中第 5 个专门测 8 worker 并发时的 SQLite WAL p95 latency。

| PoC Task | Tier | Prompt（示意） | 期望信号 / 评分逻辑 |
|---------|------|--------------|-----------------|
| POC-T1 | 1 | "记住'project deadline is 2026-06-01'这条事实" | EventStore 含 `MEMORY_ENTRY_ADDED` event |
| POC-TAU | 2 | τ-bench airline booking task（从 tasks.TASKS[0] 取）| Pass@1：user_simulator actions 全部成功执行 |
| POC-GAIA | 2 | GAIA Level 2 task（若 PoC-H1 通过则从 HF 加载；否则用 arxiv 2311.12983 附录样本 1）| 字符串精确匹配（normalized）|
| POC-T3 | 3 | "帮我完成任务 X，需要你委托 Worker 处理" | EventStore 含 `SUBAGENT_SPAWNED` + 无 Worker 直接 user-facing event（H1 验证）|
| POC-CONC | 压测 | 并发跑上述 5 task × 1 次，8 worker slot 同时启动 | 测量：① SQLite WAL p95 write latency（目标 ≤ 2s）；② 无 DB locked 错误 |

### 2.3 PoC 验证脚本设计（伪代码）

**假设 PoC-H1（HF GAIA 访问）验证**：

```
# PoC 阶段首步
from datasets import load_dataset
ds = load_dataset("gaia-benchmark/GAIA", split="validation", trust_remote_code=True)
level2_tasks = [t for t in ds if t["Level"] == 2]
# 成立条件：len(level2_tasks) >= 5
# 不成立：检查 arxiv 论文附录，手工构造 [GAIA-FALLBACK] 样本
```

**假设 PoC-H2（τ-bench task 数）验证**：

```
from tau_bench.envs.airline import tasks as airline_tasks
print(len(airline_tasks.TASKS))
# 成立条件：count >= 15
# 不成立：检查 retail domain：from tau_bench.envs.retail import tasks
```

**假设 PoC-H3（SQLite WAL contention）验证（W6 AC2-1b）**：

```
import asyncio, time, sqlite3
# 用 asyncio.gather 并行跑 8 个 OctoHarness bootstrap（各独立 tmp data_dir）
async def run_one_task(tmp_dir):
    t0 = time.perf_counter()
    async with harness_context(data_dir=tmp_dir) as harness:
        # 执行简单 task，写一条 event
        await harness.task_service.create_task(...)
    return time.perf_counter() - t0

latencies = await asyncio.gather(*[run_one_task(tmpdir_n) for n in range(8)])
p95 = sorted(latencies)[int(len(latencies)*0.95)]
# 成立条件：p95 ≤ 2.0s 额外 overhead（扣除单 task baseline）
```

**假设 PoC-H4（τ-bench mock DB reset）验证**：

```
adapter = TauBenchAdapter(tasks.TASKS[:2])
# 跑 task[0]，期间 mock DB 被修改（booking 创建）
result1 = await adapter.run_single(tasks.TASKS[0])
# reset 后跑 task[1]，验证 mock DB 回到初始状态
adapter.reset_mock_db()
# 检查 task[0] 期间创建的 booking 是否不再存在
assert booking_count() == 0  # 成立条件：DB 已还原，无 side effect
```

### 2.4 phase-0-poc-report.md 模板

```markdown
# Phase 0 PoC Report — F103d OctoBench

日期: {date}
操作者: {operator}

## 1. 安装验证
- [ ] tau-bench pip install 可行（IA-3）
- [ ] HF datasets 访问正常

## 2. 5 Task 实测结果
| Task | 耗时（秒）| 结果 | 备注 |
|------|---------|------|------|
| POC-T1 | | | |
| POC-TAU | | | |
| POC-GAIA | | | |
| POC-T3 | | | |
| POC-CONC（并发）| | | SQLite p95 latency: Xs |

## 3. 假设验证结论
| 假设 | 成立？ | 降级方案是否需要激活？|
|------|--------|---------------------|
| PoC-H1 HF GAIA 访问 | | |
| PoC-H2 τ-bench task 数 ≥ 15 | | |
| PoC-H3 SQLite WAL p95 ≤ 2s | | （W6：p95 = X 秒）|
| PoC-H4 mock DB reset 无污染 | | |

## 4. τ-bench task 实测数量
- `len(airline_tasks.TASKS)` = {N}（≥15 成立 / <15 需激活降级）

## 5. GAIA fallback 状态
- arxiv 附录公开 Level 2 样本数 = {N}（≥5 可用 / <5 需额外构造）

## 6. 推荐
- [ ] 进入 Phase A（所有 P0/P1 假设成立）
- [ ] 激活以下降级方案后进入 Phase A：{列表}
- [ ] 用户需要重新拍板范围（P0 假设不成立，GAIA 子域需调整）

## 7. Blocker
{如有，列出}
```

### 2.5 mid-implement GATE

Phase 0 完成后，implement 工程师**必须停止**，等用户读取 `phase-0-poc-report.md` 并通过 AskUserQuestion 拍板（所有 P0/P1 假设成立进 Phase A；任一 P0 假设不成立等用户决策降级方案）。BC-2 解决：P1 假设不成立也写入报告，统一由用户拍板。

---

## 3. Phase A — Tier 1（25 个私有 task YAML + EventStore scorer）

### 3.1 关键技术决策

**W8 解决**：Connor 真实场景 4 个 task 在 Phase A 留 `[CONNOR-SCENE-{1-4}]` 占位符：

```yaml
# benchmarks/tiers/tier1/t1_connor_1.yaml
task_id: T1-CONNOR-1
tier: 1
domain: connor_real_world
prompt: "[CONNOR-SCENE-1: 待 PoC 后用户确认场景内容]"
expected_events: []  # PoC 后填入
timeout_seconds: 300
partial_signals: null
status: PLACEHOLDER  # Phase A 完成前由用户拍板真实场景
```

**W4 解决（LLM judge 触发条件）**：

scorer.py 中 LLM judge 的触发条件（Tier 1 partial 评分）：

- **触发条件**：`len(expected_events) >= 2` **且** `matched_events / len(expected_events)` 在 `[0.5, 1.0)` 区间（即 ≥ 50% 通过但未全通过）
- **不触发条件**：`matched_events == 0`（直接 FAIL）或 `matched_events == len(expected_events)`（直接 PASS）
- **成本控制**：每个 task × iteration 最多触发 2 次 LLM judge call；超过 → 使用最后一次 judge 结论
- **W4 明确**：scorer 中加 `partial_judgment_logic: "if 0.5 <= match_ratio < 1.0: trigger_llm_judge(max_calls=2)"`

**W9 解决（ScoringRubric 持久化）**：选择 **YAML 文件**（`benchmarks/runner/scoring_rubrics.yaml`），理由：

- benchmark 启动时加载到内存，无 schema migration 负担
- 可纳入 git 版本管理，保证 M5 vs M6 评分规则一致性可审计
- 避免 SQLite schema 变更影响历史数据对比

ScoringRubric YAML 草稿：

```yaml
rubrics:
  - rubric_id: tier1-v1
    tier: 1
    pass_fail_weight: 0.65
    partial_weight: 0.25
    efficiency_weight: 0.10
    pass_logic: event_store_assert
    partial_logic: llm_judge
    efficiency_baseline_tokens: null  # W3：Phase E 末跑完后用 p50 填入
    llm_judge_trigger: "0.5 <= match_ratio < 1.0"
    llm_judge_max_calls: 2

  - rubric_id: tier2-tau-v1
    tier: 2
    pass_fail_weight: 0.90
    partial_weight: 0.10
    efficiency_weight: 0.00
    pass_logic: pass_at_1
    partial_logic: null
    efficiency_baseline_tokens: null

  - rubric_id: tier2-gaia-v1
    tier: 2
    pass_fail_weight: 1.00
    partial_weight: 0.00
    efficiency_weight: 0.00
    pass_logic: string_match
    partial_logic: null
    efficiency_baseline_tokens: null

  - rubric_id: tier3-v1
    tier: 3
    pass_fail_weight: 1.00
    partial_weight: 0.00
    efficiency_weight: 0.00
    pass_logic: audit_chain_assert
    partial_logic: null
    efficiency_baseline_tokens: null
```

**W3 解决（efficiency baseline 来源）**：

- Phase A ~ D：`efficiency_baseline_tokens: null`（M5 首次跑前无基准可用）
- Phase E 末：跑完 M5 baseline 后，按 domain 分组计算 p50 token_usage（input+output 之和），写入 `scoring_rubrics.yaml` 的 `efficiency_baseline_tokens` 字段
- M6 起：efficiency 维度正式启用（非 null 时参与加权计算）
- **写入机制**：reporter.py 在 Phase E 末增加一次"rubric calibration"步骤，自动计算 p50 并更新 YAML 文件

### 3.2 Tier 1 YAML 文件结构

```yaml
# 示例：benchmarks/tiers/tier1/t1_memory_001.yaml
task_id: T1-MEMORY-001
tier: 1
domain: memory
prompt: "请记住这条事实：project codename = OctoAgent。然后告诉我项目代号是什么。"
expected_events:
  - event_type: MEMORY_ENTRY_ADDED
    required_fields:
      content_contains: "OctoAgent"
  - event_type: MEMORY_RECALL_COMPLETED
    required_fields:
      namespace: AGENT_PRIVATE
timeout_seconds: 300
partial_signals:
  - event_type: MEMORY_ENTRY_ADDED  # 至少写入成功，即使未 recall
```

### 3.3 Phase A 文件清单

- `benchmarks/tiers/tier1/t1_*.yaml` × 21（25 个中 4 个 CONNOR 占位符）
- `benchmarks/tiers/tier1/t1_connor_{1-4}.yaml`（PLACEHOLDER 状态）
- `benchmarks/runner/scorer.py`（初始版：Tier 1 EventStore query 逻辑 + LLM judge stub）
- `benchmarks/runner/scoring_rubrics.yaml`（初始版，efficiency_baseline_tokens = null）

### 3.4 Codex review

Phase A 末 per-Phase review，重点：YAML schema 结构是否足够表达断言意图 / LLM judge 触发逻辑边界。

---

## 4. Phase B — Tier 2 adapter（τ-bench airline + GAIA L2）

### 4.1 τ-bench adapter 关键技术决策

**FR-E01 Tool Registry 临时注册 contextmanager（GATE_DESIGN W2，Codex pre-impl 重点审）**：

```python
# benchmarks/tiers/tier2/tau_bench_adapter.py（伪代码）

import threading
_REGISTRY_LOCK = threading.Lock()

@contextlib.contextmanager
def tau_bench_tool_scope(tool_registry_singleton, airline_tools: list[ToolEntry]):
    """
    临时注册 τ-bench airline tools 到 production Tool Registry。
    acquire 全局 lock → 注册（打 scope tag = "tau_bench_benchmark"）→ yield → finally 清理
    """
    with _REGISTRY_LOCK:
        # 检查无 name 冲突（tau_bench 工具名加 "tau_bench__" 前缀）
        registered_names = []
        try:
            for tool in airline_tools:
                prefixed_entry = tool.with_name_prefix("tau_bench__")
                tool_registry_singleton.register_temp(prefixed_entry, scope="tau_bench_benchmark")
                registered_names.append(prefixed_entry.name)
            yield
        finally:
            # 无论如何清理，保证不泄漏到 production
            for name in registered_names:
                tool_registry_singleton.deregister_by_name(name)
```

**Race condition 防御**：

- `_REGISTRY_LOCK`（threading.Lock）保证同一时刻只有一个 τ-bench task 在注册/清理工具
- 工具名加 `tau_bench__` 前缀，与 production 工具名空间隔离
- `scope="tau_bench_benchmark"` tag 供 audit 查询（而非依赖工具名过滤）
- finally 块保证清理，即使 task 执行中途异常

**注意**：若 Tool Registry 单例不提供 `register_temp` / `deregister_by_name` API，Phase B 实施前需确认现有接口（如 `tool_registry.register()` 是否可安全 deregister）。**PoC Phase 0 MUST 实测此路径无 race condition 后才进 Phase B**。

**W5 解决（τ-bench Pass@1 期望 actions 文件格式/来源）**：

基于 tech-research.md R-2：每个 τ-bench task 含 `user_id` + `instruction` + `actions`（期望操作序列）。`actions` 字段是 τ-bench 内置字段，格式为 `list[dict]`，每条含 `name`（工具名）+ `kwargs`（调用参数）+ `output`（期望返回）。

adapter 解析方式（待 PoC 实测确认字段名，见 W5 标注）：

```python
# W5：待 PoC 实测 tasks.TASKS[0] 结构，确认字段名
task = airline_tasks.TASKS[task_idx]
expected_actions = task.actions  # 或 task["actions"] 或 task.expected_outputs
# PoC 阶段必须 print(vars(task)) 确认实际字段名

# Pass@1 评分：user_simulator 跑完后，检查所有 expected action 是否被执行
# 评分方式：agent 实际调用的工具序列与 expected_actions 匹配率
# match_ratio = len(matched_calls) / len(expected_actions)
# Pass@1 = (match_ratio == 1.0) → PASS; else FAIL
```

**此处标注 "待 PoC 实测确认（见 W5）"**：τ-bench `actions` 字段的确切 Python 访问路径在 PoC 阶段通过 `inspect.getmembers(task)` 实测后写入 Phase B 实施文档。

**mock DB per-task reset（PoC-H4）**：

- 主方案（PoC-H4 成立）：τ-bench `AirlineEnv.reset()` 方法重置内存状态
- 降级方案（PoC-H4 不成立）：每个 adapter 实例独立 copy mock 数据目录到 `tmpdir`，file-based isolation

### 4.2 GAIA adapter 关键技术决策

- HF 数据集加载：`datasets.load_dataset("gaia-benchmark/GAIA", split="validation")`
- fallback 路径：若 PoC-H1 不成立，从 `benchmarks/tiers/tier2/gaia_fallback_tasks.yaml` 加载手工构造 task（PoC 阶段同步验证 arxiv 附录样本数量）
- 字符串 normalized 匹配：大小写不敏感 + strip + 数字格式统一（"1,000" → "1000"）

### 4.3 Phase B 文件清单

- `benchmarks/tiers/tier2/tau_bench_adapter.py`（约 180 行）
- `benchmarks/tiers/tier2/gaia_adapter.py`（约 100 行）
- `benchmarks/tiers/tier2/gaia_fallback_tasks.yaml`（PoC-H1 不成立时激活）
- `benchmarks/runner/scorer.py`（新增 Tier 2 评分逻辑）

### 4.4 Codex review

Phase B 末 per-Phase review，**重点**：Tool Registry 临时注册 race condition / 状态泄漏 / τ-bench 名称冲突防御（FR-E01 Codex 重点审范围）。

---

## 5. Phase C — Tier 3 哲学 task（H1/H2/H3 audit chain 断言）

### 5.1 5 个 Tier 3 task 设计

| Task | 哲学 | Prompt（示意）| audit_assertions |
|------|------|-------------|-----------------|
| T3-1 | H1 管家 mediated | "分析用户需求并委托 Worker 处理，然后向我汇报结果" | `SUBAGENT_SPAWNED.source_runtime_kind=main`；EventStore 中无 `AgentSession(kind=worker)` 直接 emit user-facing event |
| T3-2 | H2 Worker 对等性 | "在你的私有 memory 中记录一条只有你自己知道的事实，另一个 Agent 不能访问" | `MEMORY_RECALL_COMPLETED.namespace=AGENT_PRIVATE`；`RecallFrame.agent_runtime_id` 可追溯到 `AgentRuntime.profile_id` |
| T3-3 | H3-A Subagent | "创建一个临时 Subagent 帮我完成子任务，完成后 Subagent 自动结束" | `SUBAGENT_SPAWNED` 存在；`child_task.metadata.subagent_delegation.caller_project_id` 非空；`SUBAGENT_COMPLETED` 存在 |
| T3-4 | H3-B A2A ask_back | "在执行任务过程中，如果需要额外信息，请向我请求后再继续" | `CONTROL_METADATA_UPDATED.is_caller_worker_signal=true`（N-H1 修复验证）；task 经历 `WAITING_INPUT → IN_PROGRESS` 状态转换 |
| T3-5 | H3 W→W A2A | "作为 Worker，委托另一个 Worker 处理子任务" | `SUBAGENT_SPAWNED.source_runtime_kind=WORKER` 显式；完整 audit chain：`source_runtime_kind` + `BaseDelegation.delegation_id` 存在 |

YAML 格式（Tier 3 专用字段）：

```yaml
# benchmarks/tiers/tier3/t3_h1_001.yaml
task_id: T3-H1-001
tier: 3
philosophy: H1
domain: philosophy_h1
prompt: "分析用户需求并委托 Worker 处理，然后向我汇报结果。用户需求：帮我统计一下今天是星期几。"
audit_assertions:
  - event_type: SUBAGENT_SPAWNED
    required_fields:
      source_runtime_kind: main
    description: "主 Agent 是委托发起方，source_runtime_kind 必须为 main"
  - event_type_absent: AGENT_SESSION_TURN_PERSISTED
    filter:
      session_kind: user_session
      source: worker
    description: "Worker 不直接向用户发回复"
timeout_seconds: 300
partial_signals: null
```

### 5.2 Phase C 文件清单

- `benchmarks/tiers/tier3/t3_h1_001.yaml`
- `benchmarks/tiers/tier3/t3_h2_001.yaml`
- `benchmarks/tiers/tier3/t3_h3a_001.yaml`
- `benchmarks/tiers/tier3/t3_h3b_001.yaml`（T3-4，N-H1 修复验证）
- `benchmarks/tiers/tier3/t3_h3_ww_001.yaml`（T3-5）
- `benchmarks/runner/scorer.py`（新增 Tier 3 audit_chain_assert 逻辑）

### 5.3 Codex review

Phase C 末 per-Phase review，重点：audit_assertions 字段设计是否足够严格（避免 false positive）/ T3-4 N-H1 信号查询路径是否准确。

---

## 6. Phase D — Runner / Scorer / Reporter 完整实现

### 6.1 SQLite Schema

```sql
-- BenchmarkRun 表（每 task × iteration = 1 行）
CREATE TABLE IF NOT EXISTS benchmark_run (
    run_id          TEXT PRIMARY KEY,      -- UUID
    bench_session_id TEXT NOT NULL,         -- 同一次 Daily Bench 的分组 ID
    task_id         TEXT NOT NULL,
    tier            INTEGER NOT NULL,       -- 1/2/3
    domain          TEXT NOT NULL,
    iteration       INTEGER NOT NULL,       -- 1/2/3
    result          TEXT NOT NULL,          -- PASS/FAIL/PARTIAL/TIMEOUT/QUOTA_SKIP/INFRA_ERROR/INCONSISTENT
    score           REAL,                   -- 三维加权得分 0.0~1.0（nullable）
    duration_seconds REAL NOT NULL,
    token_input     INTEGER,
    token_output    INTEGER,
    token_cache_read INTEGER,
    audit_assertions_json TEXT,             -- JSON 序列化 list[dict]（Tier 3）
    error_message   TEXT,
    created_at      TEXT NOT NULL           -- ISO 8601 UTC
);

-- BenchmarkBaseline 表（每次完整 Daily Bench = 1 行）
CREATE TABLE IF NOT EXISTS benchmark_baseline (
    baseline_id     TEXT PRIMARY KEY,       -- UUID
    commit_sha      TEXT NOT NULL,
    label           TEXT,                   -- 如 "m5-baseline"
    aggregated_metrics_json TEXT NOT NULL,  -- JSON 序列化
    task_results_json TEXT NOT NULL,        -- task_id → majority result 映射
    duration_minutes REAL,
    created_at      TEXT NOT NULL
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_run_session ON benchmark_run(bench_session_id);
CREATE INDEX IF NOT EXISTS idx_run_task ON benchmark_run(task_id);
CREATE INDEX IF NOT EXISTS idx_run_result ON benchmark_run(result);
```

DB 文件路径：`benchmarks/baselines/bench.db`（与 JSON 报告同目录，不进入 production `data_dir`）。

### 6.2 asyncio.Semaphore(8) + gradual ramp 实现要点

```python
# benchmarks/runner/worker.py（关键逻辑，伪代码）

async def run_daily_bench(tasks: list[BenchmarkTask], semaphore_size: int = 8):
    sem = asyncio.Semaphore(semaphore_size)
    
    async def run_with_ramp(task: BenchmarkTask, slot_idx: int):
        # gradual ramp：每个 worker slot 错开 0.5s 启动
        await asyncio.sleep(slot_idx * 0.5)
        async with sem:
            return await run_task_with_retry(task)
    
    # 全部 task 提交，semaphore 自然限流
    results = await asyncio.gather(
        *[run_with_ramp(t, i % semaphore_size) for i, t in enumerate(tasks)],
        return_exceptions=True
    )
    return results

async def run_task_with_retry(task: BenchmarkTask, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await execute_single_task(task)
        except RateLimitError as e:
            if e.retry_after:
                await asyncio.sleep(e.retry_after)
            else:
                # exponential backoff with full jitter
                wait = min(60, random.uniform(0, 2 ** attempt))
                await asyncio.sleep(wait)
            if attempt == max_retries - 1:
                return BenchmarkRun(result="QUOTA_SKIP", ...)
        except asyncio.TimeoutError:
            return BenchmarkRun(result="TIMEOUT", ...)
        except Exception as e:
            if is_infra_error(e):
                # 连续 5 个 INFRA_ERROR 时 benchmark 主动停止
                infra_error_count.increment()
                return BenchmarkRun(result="INFRA_ERROR", ...)
```

### 6.3 reporter.py JSON 报告结构

```json
{
  "run_id": "uuid",
  "baseline_sha": "a69fe9c...",
  "created_at": "2026-05-27T10:00:00Z",
  "summary": {
    "total_tasks": 50,
    "pass_rate": 0.72,
    "weighted_score": 0.68,
    "token_usage": {"input": 1200000, "output": 300000, "cache_read": 800000},
    "duration_minutes": 47.3,
    "skipped": {"QUOTA_SKIP": 2, "TIMEOUT": 1}
  },
  "by_tier": {
    "tier1": {"pass_rate": 0.80, "tasks": 25, "passed": 20},
    "tier2": {
      "tau_bench": {"pass_rate": 0.67, "tasks": 15, "passed": 10},
      "gaia": {"pass_rate": 0.60, "tasks": 5, "passed": 3}
    },
    "tier3": {"pass_rate": 0.60, "tasks": 5, "passed": 3}
  },
  "by_domain": {
    "memory": {"pass_rate": 0.83, "tasks": 3},
    "delegation": {"pass_rate": 0.75, "tasks": 4}
  },
  "task_details": [
    {
      "task_id": "T1-MEMORY-001",
      "majority_result": "PASS",
      "iterations": [
        {"iteration": 1, "result": "PASS", "score": 0.85, "duration": 12.3, "tokens": 1200},
        {"iteration": 2, "result": "PASS", "score": 0.90, "duration": 11.8, "tokens": 1150},
        {"iteration": 3, "result": "FAIL", "score": 0.30, "duration": 13.1, "tokens": 1300}
      ],
      "inconsistency_note": null
    }
  ]
}
```

**W7 解决（AC1-4 delta 视图格式）**：`--compare` 模式额外输出 `delta` 区块：

```json
"delta": {
  "baseline_id": "m5-baseline",
  "compared_at": "2026-06-15T10:00:00Z",
  "summary": {
    "pass_rate_delta": "+0.05",      // 精确到 0.001（0.1%）
    "tier1_delta": "+0.08",
    "tier2_delta": "+0.02",
    "tier3_delta": "+0.00"
  },
  "regressions": [
    {
      "task_id": "T1-MEMORY-001",
      "m5_result": "PASS",
      "current_result": "FAIL",
      "failed_assertions": ["MEMORY_RECALL_COMPLETED.namespace != AGENT_PRIVATE"]
    }
  ],
  "improvements": [
    {"task_id": "T2-TAU-007", "m5_result": "FAIL", "current_result": "PASS"}
  ]
}
```

delta 精度规范：pass rate 变化精确到小数点后 3 位（0.001 = 0.1%）；regression 列表包含完整 task_id + 失败的具体断言描述（非仅"FAIL"）。

### 6.4 Phase D 文件清单

- `benchmarks/runner/worker.py`（约 150 行）
- `benchmarks/runner/scorer.py`（整合 A/B/C 三层评分，约 200 行）
- `benchmarks/runner/reporter.py`（约 120 行）
- `benchmarks/runner/store.py`（约 100 行）
- `benchmarks/__init__.py`、`benchmarks/runner/__init__.py`（各 5 行）
- `benchmarks/conftest.py`（约 60 行）
- `apps/gateway/src/octoagent/gateway/cli/bench_commands.py`（≤ 30 行 thin wrapper）

### 6.5 Codex review

Phase D 末 per-Phase review（最高优先级）。重点：
- asyncio 并发控制的边界条件（8 并发全部超时时行为）
- SQLite append-only 写入并发安全
- `--resume` 续跑逻辑（不重复执行已完成 task）
- Tool Registry contextmanager 与 runner 的交互时序

---

## 7. Phase E — M5 baseline 跑

### 7.1 执行步骤

1. 确认 production 代码状态：`git diff HEAD -- packages/ apps/gateway/ apps/web/`（应为 0 变更，SC-009 验证）
2. 确认 Connor 真实场景 4 个 task 已由用户拍板填入实际内容（PLACEHOLDER 状态的 task 不计入 Daily Bench）
3. 执行：`octo bench daily --label m5-baseline`
4. 观测：实时查看进度（每 task 完成后 SQLite 即持久化）
5. 验证 SC-001：wall clock ≤ 1 hour
6. 验证 SC-011：INCONSISTENT task 占比 ≤ 5%（超过 2-3 个触发 RCA）
7. 存档：JSON 报告复制到 `benchmarks/baselines/m5-baseline.json` + 创建 Markdown 摘要

### 7.2 W3 efficiency baseline 写入（Phase E 末）

**W3 明确解决**：Phase E 完整跑完后，reporter.py 执行"rubric calibration"步骤：

```python
def calibrate_efficiency_baseline(bench_results: list[BenchmarkRun], rubrics_path: Path):
    """
    按 domain 分组计算 p50 token_usage（input + output）。
    写入 scoring_rubrics.yaml 的 efficiency_baseline_tokens 字段。
    仅在 --label m5-baseline 时执行（一次性校准）。
    """
    domain_tokens: dict[str, list[int]] = defaultdict(list)
    for run in bench_results:
        if run.result == "PASS":  # 仅用 PASS 的 task 做基准
            total_tokens = (run.token_input or 0) + (run.token_output or 0)
            domain_tokens[run.domain].append(total_tokens)
    
    p50_by_domain = {
        domain: sorted(tokens)[len(tokens) // 2]
        for domain, tokens in domain_tokens.items()
        if len(tokens) >= 3  # 样本数 >= 3 才有统计意义
    }
    
    # 写入 scoring_rubrics.yaml
    update_rubric_efficiency_baseline(rubrics_path, p50_by_domain)
```

**p50 计算方式**：各 domain 内全部 PASS task 的 `(token_input + token_output)` 取中位数；cache_read_tokens 不计入（不同 task 缓存命中率差异大）。写入后 `scoring_rubrics.yaml` 进 git commit（M6 各 Feature 对比时用同一基准）。

### 7.3 Phase E 文件变更

- `benchmarks/baselines/m5-baseline.json`（新产出）
- `benchmarks/baselines/m5-baseline.md`（Markdown 摘要）
- `benchmarks/runner/scoring_rubrics.yaml`（efficiency_baseline_tokens 字段填入 p50 数值）

### 7.4 Codex review

Phase E 末 **Final cross-Phase review**（整个 benchmark 模块综合审查）：

- benchmark 模块整体架构是否引入隐性技术债
- SQLite schema 是否满足 M6 长期对比需求
- Tool Registry 隔离机制审查（race condition + 状态泄漏）
- 评分逻辑一致性（Tier 1/2/3 三层 scorer 是否有逻辑漏洞）

---

## 8. Phase F — 文档闭环

**内容**：

- `completion-report.md`：逐 AC 标注通过情况
- `handoff.md`（给 F104）：M5 baseline 数据快照 + M6 Feature 验收门槛建议（基于 M5 pass rate 推算 regression 警戒线，如 "Tier 1 pass rate 低于 M5 - 10% 触发 code review"）
- 清理 Phase 0 临时脚本 + poc report 归档

**Codex review**：不需要（命中"纯文档微改"豁免条件）。

---

## 9. 关键技术选型总结

### 9.1 τ-bench / GAIA pip 依赖

| 依赖 | 安装方式 | 版本锁定建议 |
|------|---------|------------|
| `tau-bench` | `pip install git+https://github.com/sierra-research/tau-bench.git@{commit}` 或 `pip install tau-bench`（待 PoC 验证 PyPI 包存在）| 锁定 commit hash，防止 repo 更新影响 baseline 可重复性 |
| `datasets`（HuggingFace）| `pip install datasets>=2.0` | 项目已有或新增；版本 ≥ 2.0 支持 gated dataset |

**IA-3 解决**：PoC Phase 0 第一步验证安装可行性，若 PyPI 包不存在则用 git+URL 方式。

### 9.2 OctoHarness 4 DI 钩子注入方式

| 钩子 | Tier 1/3 task 注入值 | Tier 2 task 注入值 |
|------|-------------------|-----------------|
| `credential_store` | 指向 `ANTHROPIC_API_KEY` 的临时 CredentialStore（控变量：Sonnet 4.5） | 同左 |
| `llm_adapter` | `None`（使用生产 ProviderRouterMessageAdapter）| `None` |
| `mcp_servers_dir` | 指向空 tmpdir（benchmark task 不含 MCP domain）| 同左 |
| `data_dir` | 每个 task 独立 tmpdir（PoC-H3 成立）或共享 store（PoC-H3 不成立）| 同左 |

Tier 2 τ-bench task 不走完整 OctoHarness bootstrap（R-1 调研：直调主路径），仅用 Tool Registry 临时注册。

### 9.3 τ-bench user simulator

**GATE_DESIGN OQ-1 已拍板**：user simulator 使用 **Claude Sonnet 4.6**（非 Haiku，保证 simulator 决策质量 challenging；被测 Agent 仍为 Sonnet 4.5，控变量不受影响）。

---

## 10. 风险与降级方案

| 风险 ID | 风险描述 | 严重度 | 降级方案 |
|--------|---------|--------|---------|
| PoC-H1 | HF GAIA 访问申请失败 | HIGH（P0 阻塞）| 手工构造 5 个 [GAIA-FALLBACK] 样本（arxiv 附录）；GAIA 子域 pass rate 注明"非官方数据集" |
| PoC-H2 | τ-bench task 数 < 15 | MEDIUM（P1）| 从 retail domain 补充差额；分层抽样策略 Phase B 调整 |
| PoC-H3 | SQLite WAL contention p95 > 2s | MEDIUM | 降级为共享 store 方案（单 OctoHarness 实例，task 间仅清理 task_store 记录）|
| PoC-H4 | τ-bench mock DB per-task reset 有 side effect | MEDIUM | file-based isolation（每个 adapter 实例独立 copy mock 数据到 tmpdir）|
| NEW-R1 | τ-bench pip install 失败（IA-3）| HIGH | `pip install git+https://github.com/sierra-research/tau-bench.git`；PoC 第一步验证 |
| NEW-R2 | Tool Registry 无 deregister API | MEDIUM | Phase B 前确认现有 API；若无则实现 wrapper 方案（不修改 production registry 本身）|
| NEW-R3 | Anthropic API 完全不可用（5xx / 网络断）| MEDIUM | 单 task 标记 INFRA_ERROR；连续 5 个 INFRA_ERROR benchmark 主动停止并报错 |
| NEW-R4 | GAIA Level 2 单 task 耗时 > 5min | MEDIUM | GAIA task 独立配置 timeout=8min（spec FR-A03 已覆盖）|
| NEW-R5 | INCONSISTENT 占比 > 5%（SC-011）| LOW | Phase E 末单独 RCA：LLM 随机性 vs scorer 健壮性；调整 task prompt 或提高重试次数 |
| NEW-R6 | Connor 真实场景 4 task PoC 后未及时拍板 | LOW | PLACEHOLDER task 不计入 Daily Bench 分母；SC-005 说明占位状态 |

---

## 11. Phase 顺序与并行策略

```
Phase 0（PoC，≤1 天）
    │
    │ mid-implement GATE：用户拍板后进入
    ▼
Phase A（Tier 1，2-3 天）
    │  YAML + scorer 初始版
    ▼
Phase B（Tier 2 adapter，2-3 天）
    │  τ-bench + GAIA
    ▼
Phase C（Tier 3，1-2 天）
    │  H1/H2/H3 audit scorer
    ▼
Phase D（Runner 完整，2-3 天）
    │  整合 A/B/C，CLI 就绪
    ▼
Phase E（M5 baseline，1 天）
    │  50 task × 3 次 × 8 并行
    │  W3 efficiency baseline 写入
    ▼
Phase F（文档，0.5 天）
```

**A/B/C 是否可并行**：建议串行。理由：B 的 τ-bench 工具注册 API 依赖 scorer.py 的 Tier 2 评分接口（Phase A 已定义接口），B/C 并行会导致 scorer.py 同时被两个 Phase 修改，增加 merge 成本。Phase D 整合三层时若有接口偏差也更难排查。

---

## 12. Codex Review 时机表

| 时机 | 范围 | 模式 |
|------|------|------|
| **pre-impl review（本 plan 完成后立即）** | spec.md + plan.md | background（范围较大）|
| Phase A 末 | YAML schema + scorer 初始版 | foreground（单 Phase，小范围）|
| Phase B 末 | τ-bench adapter + Tool Registry 临时注册（重点 race condition）| background（高风险，race condition 审查）|
| Phase C 末 | Tier 3 YAML + audit_chain scorer | foreground |
| Phase D 末 | Runner 完整实现 + CLI（最高优先级）| background（最复杂 Phase）|
| **Phase E 末 Final cross-Phase（必走）** | 整个 benchmarks/ 模块综合 | background |
| Phase F | 文档 only，**豁免**（命中"纯文档微改"豁免条件）| 不需要 |

---

## 13. Acceptance Mapping

| AC | 对应 Phase | 核心实现 |
|----|----------|---------|
| AC1-1（50 task × 3 次，≤ 1h）| Phase E | runner + 8 并发 + gradual ramp |
| AC1-2（JSON + Markdown 报告）| Phase D/E | reporter.py |
| AC1-3（150 条 BenchmarkRun）| Phase D/E | store.py SQLite |
| AC1-4（vs M5 delta 视图）| Phase D | reporter.py `--compare`，delta 精度 0.1% |
| AC2-1（5 task PoC 耗时记录）| Phase 0 | 手工脚本 |
| AC2-1b（8 worker SQLite p95 测量，W6）| Phase 0 | PoC 并发压测 task |
| AC2-2（phase-0-poc-report.md）| Phase 0 | PoC 报告模板 |
| AC2-3（用户拍板 GATE）| Phase 0 末 | mid-implement GATE |
| AC3-1（retry-after 精确等待）| Phase D | runner/worker.py |
| AC3-2（exponential backoff）| Phase D | runner/worker.py |
| AC3-3（timeout → TIMEOUT 标记）| Phase D | runner/worker.py |
| AC3-4（分母不计 SKIP/TIMEOUT）| Phase D | reporter.py |
| AC4-1（JSON by_tier 结构）| Phase D/E | reporter.py |
| AC4-2（τ-bench + GAIA 独立 pass rate）| Phase D/E | reporter.py |
| AC4-3（Tier 3 audit 信号逐条可查）| Phase C/D | scorer.py + reporter.py |
| AC5-1（--resume 续跑）| Phase D | store.py + runner |
| AC5-2（续跑报告等价）| Phase D/E | reporter.py |
| AC6-1（--compare m5 delta 区块）| Phase D | reporter.py |
| AC6-2（baseline 不存在时报错）| Phase D | reporter.py 异常处理 |

---

## 14. Codebase Reality Check

### 目标文件现状（零侵入：无现有文件被修改）

本 Feature 全部为**新增文件**，无目标文件需要 Reality Check。

FR-H01 零侵入验证策略：

```bash
# 每 Phase 完成后验证
git diff HEAD -- packages/ apps/gateway/src apps/web/
# 期望输出：空（0 文件变更）

# 允许的例外：apps/gateway/src/.../cli/bench_commands.py（新增文件，不修改现有文件）
git status --short apps/gateway/src/octoagent/gateway/cli/bench_commands.py
# 期望输出：?? （untracked 新增）
```

---

## 15. Impact Assessment

| 维度 | 评估 |
|------|------|
| 影响文件数 | 新增 ~15 个文件；修改 0 个现有文件（pyproject.toml entry point 新增行） |
| 跨包影响 | 仅 `benchmarks/`（新 顶层目录） + `apps/gateway/cli/`（新增 1 文件）；不跨 `packages/` 边界 |
| 数据迁移 | 无（新增独立 `benchmarks/baselines/bench.db`，不影响 production `~/.octoagent/` 数据）|
| API/契约变更 | 无（新增 `octo bench` CLI 命令，不修改现有 CLI API）|
| **风险等级** | **LOW**（影响文件 < 10 个现有文件；无跨包影响；无数据迁移；无现有接口修改）|

**注**：总体复杂度 HIGH（spec §8 评估），但 Impact 风险为 LOW（因为是纯新增，无现有代码修改）。这两个维度独立评估，不冲突。

---

## 16. Constitution Check

| 原则 | 适用性 | 评估 | 说明 |
|------|--------|------|------|
| Durability First（1）| 适用 | PASS | FR-A05 append-only SQLite；AC5-1 断点续跑 |
| Everything is an Event（2）| 部分适用 | PASS（豁免）| benchmark runner 本身不要求走 EventStore；spec §5.2 已豁免 |
| Tools are Contracts（3）| 适用 | PASS | scorer 接口有明确类型签名；ScoringRubric YAML 是单一事实源 |
| Side-effect Two-Phase（4）| 不适用 | N/A | benchmark 无不可逆操作 |
| Least Privilege（5）| 适用 | PASS | OctoHarness DI 注入，credential_store 隔离，不暴露生产 secrets |
| Degrade Gracefully（6）| 适用 | PASS | QUOTA_SKIP / TIMEOUT / INFRA_ERROR 三态降级；连续 5 INFRA_ERROR 主动停止 |
| User-in-Control（7）| 适用 | PASS | Phase 0 mid-implement GATE 要求用户拍板 |
| Observability（8）| 适用 | PASS | SQLite 持久化 + JSON/Markdown 报告；每 task 状态可查 |
| Agent Autonomy（9）| 适用 | PASS | Tier 1 使用真实 LLM 路径；FR-B03 LLM judge fallback |
| Policy-Driven Access（10）| 不适用 | N/A | benchmark 不涉及工具访问控制策略 |
