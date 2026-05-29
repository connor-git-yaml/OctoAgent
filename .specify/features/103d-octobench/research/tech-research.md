# F103d OctoBench 技术调研

> **执行**: spec-driver:tech-research 子代理（quality-first preset, opus）
> **模式**: tech-only（独立模式，未参考产品调研）
> **完成时间**: 2026-05-27
> **baseline**: a69fe9c (F103c 收尾后)

---

## R-1：F087 OctoHarness 内部结构

### OctoHarness 4 个 DI 钩子

`OctoHarness.__init__` 接受以下可选参数，全部默认 `None`（生产路径 byte-for-byte 等价）：

| 钩子 | 签名类型 | 用途 | benchmark 复用方式 |
|------|---------|------|-------------------|
| `credential_store` | `CredentialStore \| None` | 替换 ProviderRouter 凭据来源（默认读 `~/.octoagent/auth-profiles.json`） | benchmark 注入 tmp credential store，指向 `ANTHROPIC_API_KEY`（Sonnet 4.5 专用），不污染宿主 |
| `llm_adapter` | `MessageAdapter \| None` | 替换 FallbackManager.primary（可 inject stub/echo/真实 adapter） | benchmark 不替换，使用生产 ProviderRouterMessageAdapter + 控变量模型 |
| `mcp_servers_dir` | `Path \| None` | 替换 MCP 安装目录 | benchmark 注入 tmp 目录，MCP server 不跑（benchmark task 不含 MCP domain） |
| `data_dir` | `Path \| None` | 替换 DB/artifacts/user_pipelines 根 | benchmark 每个 task 注入独立 tmp dir，保证 task 间 store 隔离 |

生命周期：`bootstrap(app)` → 11 段顺序初始化 → `commit_to_app(app)` no-op → yield → `shutdown(app)`。

**关键约束（对 benchmark 设计的影响）**：每个 benchmark task 若要完全隔离，需要：
1. 独立 `tmp_path` 作为 `data_dir` + `mcp_servers_dir`
2. 独立 FastAPI `app` 实例
3. bootstrap/shutdown 完整走一遍（约 2-5 秒启动开销）

**备选方案（更轻量）**：复用同一个 harness 实例，让 task 间共享 store，仅在 task 完成后清理 task_store 特定记录。代价：store 隔离性弱，适合 Daily Bench 快速并行场景。

### 13 能力域当前测试文件清单

| 域 # | 文件 | smoke/full | 测试性质 |
|------|------|-----------|---------|
| 1,2,3 | `test_e2e_basic_tool_context.py` | smoke | OctoHarness DI + stub transport，不打 LLM |
| 4 | `test_e2e_memory_pipeline.py` | full | 直调主路径 MemoryService，绕开 LLM |
| 5,6,7 | `test_e2e_mcp_skill_pipeline.py` | full | 域 5 manual gate（需 Perplexity key），域 6/7 直调 |
| 8,9,10 | `test_e2e_delegation_a2a.py` | full | 直调主路径，绕开 LLM（域 10 是 schema integration 测试） |
| 11,12 | `test_e2e_safety_gates.py` | smoke | OctoHarness DI + stub，不打 LLM |
| 13 | `test_e2e_routine.py` | full | cron 触发 |

**benchmark 可直接扩展的域**：Tier 1 的 25 个 task 应覆盖 13 个域中的有 LLM 路径域（1/2/3 smoke → 升级为真实 LLM）+ delegation（域 8/9）+ memory（域 4）。

### benchmark runner 复用 OctoHarness 的推荐方式

**推荐：轻量化复用，不启动完整 FastAPI lifespan**

直接构造 `StoreGroup` + 关键 service（`TaskRunner`, `CapabilityPackService`），绕开 Telegram/SSEHub/watchdog 等非核心段，降低每个 task 的启动成本。F087 域 #4/#8/#9/#10 已经验证直调主路径的可行性——benchmark 可沿用这个模式，对**有 LLM 决策**的 task 才走完整 OctoHarness bootstrap。

**关键风险**：
- 完整 OctoHarness bootstrap 每次约 2-5 秒，50 task × 8 并行 = 6-7 个 harness 实例同时启动，SQLite WAL 可能 contention（**PoC 必须验证的假设 #1**）
- Module singleton reset 清单（`_REGISTRY`、`AgentContextService` 属性、`ContextVar` 等）必须同步到 benchmark conftest，否则并行 task 间状态泄漏
- e2e conftest 中 `_SINGLE_SCENARIO_TIMEOUT_FULL_S = 240`，benchmark task 若有 LLM 多步推理需要更长 timeout（**PoC 必须验证的假设 #2**）

---

## R-2：τ-bench Sierra-Research 集成可行性

### 核心结论

**airline 域 task 总数**：文件显示 `tasks.py` 共 1456 行，目视可识别 ≥35 个 distinct task（含 flight booking/cancel/upgrade/baggage/payment 等类型）。τ-bench 原始 README 未直接公开任务数量——**待 PoC `len(tasks.TASKS)` 实测确认**，F103d 规划 15 个 airline task 是否成立需等 PoC。[推断] 基于 1456 行 + 每个 task 约 30-40 行，总数估计在 35-50 之间，抽取 15 个有充裕余地。

**user simulator 机制**：基于 LLM 的对话模拟，支持 4 种策略：
- `llm`（默认）：纯 LLM 模拟用户
- `react`：含推理步骤
- `verify`：LLM 验证模式
- `reflection`：反思模式

每个 task 含 `user_id` + `instruction` + `actions`（期望操作序列）。评分走 **Pass@k 指标**（k=1/2/3/4）。

**airline 工具与 OctoAgent Tool Registry 接入路径**：

τ-bench airline 域有自己的 `tools/` 目录（airline-native tools：搜班机、预订、退票等）。接入方案：
- **适配器层**：在 `benchmarks/tau_bench/adapter.py` 实现 `TauBenchToolAdapter`，把每个 airline tool 包装成 OctoAgent Tool Registry 可注册的 `ToolEntry`
- 不修改生产 Tool Registry，benchmark 启动时临时注册 airline tools，结束后清理
- **关键约束**：τ-bench airline tools 依赖 `data/` 目录里的 mock 数据库（flight/passenger 状态）——需要 per-task 重置 mock 状态，否则 task 间 side effect 污染（**PoC 必须验证的假设 #3**）

**15 task 抽样策略**（待 task 总数确认后执行）：
- 按 task type 分层：booking(4) / cancellation(3) / upgrade/downgrade(3) / passenger modification(2) / baggage(2) / payment(1)
- 优先选 difficulty 中等的 task（τ-bench README 提到有 "easy for gpt-4" 的标注，剔除过简 task）

**主要风险**：
1. user simulator 需要独立 LLM call（用 Claude Sonnet 4.5 or 更便宜 Haiku），会显著增加每个 task 的 token 消耗和耗时
2. mock 数据库 per-task reset 实现复杂度未知——**阻塞风险**，PoC 阶段必须验证

---

## R-3：GAIA Level 2 集成可行性

### 核心结论

**GAIA Level 2 task 总数**：245 个 task（公开数据），要求 5-10 步推理，属于"中等复杂度"。

**task 类型分布**（基于公开论文 + 社区报告）：
- Web search + 信息综合：约 40%
- 文档解析（PDF/表格/图像）：约 30%
- 多工具串联（代码执行 + 搜索 + 计算）：约 30%

**答案格式**：字符串精确匹配为主（短字符串、数字、日期、名词等），部分 task 有多个可接受答案。**不依赖 LLM-as-judge**，评分确定性高，适合 benchmark 对比。

**5 task stratified 抽样策略**：
- web search 类 2 个（覆盖 OctoAgent 的 Perplexity MCP 能力）
- 文档解析 2 个（测试 artifact 读取链路）
- 多工具串联 1 个（测试 skill pipeline 能力）

**HF 数据集访问**：`gaia-benchmark/GAIA` 在 HuggingFace，需要申请访问权限（gated dataset）。**阻塞风险**：申请流程可能需要 1-3 天——**PoC 阶段首要任务是申请 HF 访问**，同时准备可替代的公开样本（arxiv 论文附录有部分样本）。

**Level 2 task 平均耗时**：业界报告显示顶级 Agent 系统（GPT-5/Claude 系列）Level 2 单 task 约 60-300 秒（含多步 LLM 调用 + web search）。5 个 Level 2 task 纳入 Daily Bench 时按最坏 300s × 5 = 1500 秒计算，**需要独立 worker slot 或更宽松 timeout**——Daily Bench 1 hour 约束下 GAIA 5 个 task 勉强可行，需要 PoC 实测确认（**PoC 必须验证的假设 #4**）。

---

## R-4：H1/H2/H3 哲学 case 设计依据

### 可观测信号梳理

**H1 管家 mediated**（可观测信号）：
- `EventType.SUBAGENT_SPAWNED` event chain 出现，且 `source_runtime_kind=MAIN`（或 `source_runtime_kind` 缺省默认 main）
- `NOTIFICATION_DISPATCHED` event 存在，表明主 Agent 通过 NotificationService 主动向用户发送状态更新
- `A2AConversation` 中 result 经主 Agent 综合后才有 user-facing reply（task timeline：Worker RESULT → main agent turn → SSE 推送用户）
- `AgentSession.kind != "user_session"` 的 session 不出现在 user-facing 回复链中

**H2 Worker 对等性**（可观测信号）：
- `MEMORY_RECALL_COMPLETED` event 含 `namespace=AGENT_PRIVATE`（Worker 路径）
- `AgentSession` 表中存在 `kind=WORKER` 的 session，且包含 `rolling_summary` / `memory_cursor` 字段
- `BEHAVIOR_PACK_LOADED` event 含 `agent_kind=worker`，且 pack 文件清单包含 `IDENTITY.worker.md`
- `RecallFrame.agent_runtime_id` 可追溯到 `AgentRuntime.profile_id`（四层 audit chain：AgentProfile.profile_id → AgentRuntime.profile_id → BEHAVIOR_PACK_LOADED.agent_id → RecallFrame.agent_runtime_id）

**H3 双委托模式**（Subagent vs A2A 字段区分）：
- `SubagentDelegation` 持久化在 `child_task.metadata["subagent_delegation"]`（无独立 SQL 表），字段含 `caller_project_id`（共享 project 的证明）
- A2A `WorkerDelegation` 有独立 session（receiver 在自己 project），`source_runtime_kind` 显式设置
- `CONTROL_METADATA_UPDATED` event 含 `is_caller_worker_signal=True` 时，表明 Worker→Worker A2A 模式激活
- Subagent：`AgentProfile.kind=subagent`；A2A Worker：`AgentProfile.kind=worker`

### 5 个 Tier 3 task 设计建议

| Task | 哲学 | 验证维度 | 设计方向 |
|------|------|---------|---------|
| T3-1 | H1 | 主 Agent 唯一 user-facing speaker | 给主 Agent 一个需要委托给 Worker 才能完成的任务，断言 Worker 结果只通过主 Agent reply 到用户，EventStore 中无 Worker session 直接 emit user-facing event |
| T3-2 | H2 | Worker AGENT_PRIVATE memory 隔离 | Worker 写入一条 AGENT_PRIVATE fact，另一个 Worker 不能读到（namespace 隔离验证），RecallFrame 的 namespace 字段确认 |
| T3-3 | H3-A | Subagent spawn-and-die + 共享 memory | 主 Agent 创建 Subagent，Subagent 共享 caller AGENT_PRIVATE namespace（`caller_memory_namespace_ids` 字段非空），任务结束后 `SUBAGENT_COMPLETED` event 存在 |
| T3-4 | H3-B | A2A Worker ask_back 中途澄清 | Worker 用 `worker.ask_back` 工具，触发 `WAITING_INPUT` 状态，用户 reply 后 resume，全程 `is_caller_worker_signal` 持久化（N-H1 修复验证） |
| T3-5 | H3 | Worker→Worker 委托（D14 关闭后合法） | Worker A 通过 A2A 委托 Worker B，`source_runtime_kind=WORKER` 显式设置，audit chain 完整（F098 D14 解禁验证） |

---

## R-5：业界 benchmark 落地经验 + 并行 LLM API rate limit

### Claude Sonnet 4.5 Tier 4 限额

| 指标 | Tier 4 数值 |
|------|-----------|
| RPM | 4,000 |
| ITPM（uncached input tokens/min） | 2,000,000 |
| OTPM（output tokens/min） | 400,000 |
| 并发限制 | 未明确规定，**[推断] 受 RPM 约束**，8 并发理论上安全 |

**注意**：Sonnet 4.x rate limit 是 `claude-sonnet-4-6` / `claude-sonnet-4-5` / `claude-sonnet-4` 的**总额**，不是各自独立的。8 并发 Daily Bench 场景下，每分钟最多 4,000 个请求 / 8 = 每路最多 500 RPM，远低于限制，**rate limit 不是主要瓶颈**。

**关键优势**：prompt caching。OctoAgent system prompt 含大量固定内容（USER.md / IDENTITY / MEMORY 等），cache hit 时 `cache_read_input_tokens` **不计入 ITPM**，有效 throughput 大幅提升。

### 推荐 backoff 策略

Anthropic API 429 响应含 `retry-after` header（秒数）：

1. **首要策略**：读 `retry-after` header，精确等待指定秒数后重试（不是猜测）
2. **降级策略**（header 缺失时）：exponential backoff with full jitter
   - 初始等待：1s
   - 最大等待：60s
   - 公式：`min(60, random(0, 2^retry_count))`
3. **OctoAgent 现有机制**：`test_quota_skip.py` 已实现 HTTP 429 结构化匹配 → pytest SKIP（不 FAIL）。benchmark runner 应复用同样的结构化检测，把 429 标记为 `task_result=QUOTA_SKIP`，不计入 pass rate 分母

**加速限制提醒**（Anthropic 官方文档）：sharp traffic spike 也会触发 429，即使未超 RPM/ITPM 绝对限制。benchmark 启动时应采用 **gradual ramp**（不要 8 并发同时 t=0 打出去），建议错开 0.5s 启动每个 worker slot。

### 业界已知耗时数据点

τ-bench 官方提供 `historical_trajectories/` 预计算结果，可跳过实际 LLM 调用做 reproducibility 验证。τ-bench 50 task × 10 max_concurrency（原始设置）的实际耗时业界无公开详细数据——[推断] 按 airline task 单次平均 30-120 LLM tokens exchange，每 task 约 30-180 秒，8 并行 50 task 约 20-40 分钟，**Daily Bench 1 hour 约束可行，但 GAIA Level 2 task 是风险点**（单 task 可能 300s+）。

---

## PoC 阶段必须优先验证的关键假设（按优先级）

| 优先级 | 假设 | 验证方式 | 若不成立的影响 |
|--------|------|---------|--------------|
| P0 | HF gaia-benchmark/GAIA 访问申请成功 | 申请 HF 账号访问权限 | GAIA domain 用公开样本替代，Level 2 task 变为手动构造 |
| P1 | τ-bench airline domain task 数量 ≥ 15 | `python -c "from tau_bench.envs.airline import tasks; print(len(tasks.TASKS))"` | 抽样数量需降低或混入 retail domain |
| P2 | 8 并行 OctoHarness 实例的 SQLite WAL contention 可接受 | 跑 5 task PoC 测量 p95 latency | 需要改为共享 store 方案或降低并发度 |
| P3 | τ-bench mock 数据库 per-task reset 机制可行 | 实测 task 序列是否 side effect 污染 | τ-bench airline 集成方案需重设计 |
| P4 | GAIA Level 2 task 单次耗时 ≤ 300s（可挤进 1 hour） | 实测 1 个 Level 2 task 的实际耗时 | GAIA 移出 Daily Bench，改为 Full Bench（5h） |

---

## 总体架构建议

```
benchmarks/
├── runner/          # BenchmarkRunner：task 队列 + 8 并行 asyncio worker pool
│   ├── worker.py    # 单 task 执行：OctoHarness 或直调主路径（按 task type）
│   ├── scorer.py    # 统一评分接口：Tier 1 EventStore query / Tier 2 Pass@k / Tier 3 字符串匹配
│   └── reporter.py  # JSON report + SQLite baseline 存储
├── tiers/
│   ├── tier1/       # 25 private tasks（e2e_live 13 域扩展 + 真实 LLM 路径）
│   ├── tier2/       # tau_bench adapter + gaia adapter
│   └── tier3/       # 5 H1/H2/H3 philosophy tasks
└── conftest.py      # module singleton reset + hermetic env（复用 e2e_live 模式）
```

**评分机制**（三层统一接口）：
- Tier 1：查 EventStore，断言特定 event chain 存在（等价于 e2e_live 直调主路径断言）
- Tier 2 τ-bench：Pass@1（单次跑通率），对照 user_simulator 期望 actions 序列
- Tier 2 GAIA：字符串匹配（精确或 normalized）
- Tier 3：EventStore query（同 Tier 1 但验证 H1/H2/H3 特定 audit 信号）

---

## 不确定项 / Sources

**不确定项**:
- HF GAIA 数据集访问申请周期未知（可能 1-3 天）
- τ-bench airline task 确切数量待 PoC 实测
- GAIA Level 2 task 在 OctoAgent 上的实际耗时无先例数据

**Sources**:
- [Sierra-Research τ-bench GitHub](https://github.com/sierra-research/tau-bench)
- [τ-bench airline tasks.py](https://github.com/sierra-research/tau-bench/blob/main/tau_bench/envs/airline/tasks.py)
- [GAIA benchmark HuggingFace Dataset](https://huggingface.co/datasets/gaia-benchmark/GAIA)
- [Anthropic API Rate Limits](https://platform.claude.com/docs/en/api/rate-limits)
- [GAIA: a benchmark for General AI Assistants (arxiv)](https://arxiv.org/pdf/2311.12983)
- [H2O.ai GAIA benchmark results 2025](https://h2o.ai/blog/2025/h2o-ai-tops-the-general-ai-assistant-test/)
