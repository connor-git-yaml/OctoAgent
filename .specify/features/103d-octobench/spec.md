# F103d — OctoBench：M5 baseline + M6 验收基线

**Feature Branch**: `feature/103d-octobench`
**Created**: 2026-05-27
**Status**: Draft
**Phase**: M5 → M6 过渡阶段（第 3 个子项）
**Baseline**: commit `a69fe9c`（F103c 收尾后）

**Input**: CLAUDE.local.md §"M5 → M6 过渡阶段" F103d；tech-research.md (R-1 ~ R-5)。

---

## 0. 范围澄清（核心，先于 User Stories）

### 0.1 tech-research 调研结论与 PoC 假设清单

基于 tech-research.md R-1 ~ R-5，以下是本 Feature 的技术前提与待 PoC 验证的关键假设：

| 调研点 | 结论 | 对 spec 的影响 |
|--------|------|---------------|
| R-1 OctoHarness DI | 4 个 DI 钩子（credential_store / data_dir / mcp_servers_dir / llm_adapter）；完整 bootstrap 约 2-5s | benchmark task 隔离方案：Tier 1/3 走完整 harness；Tier 2 adapter 层直调 |
| R-1 F087 域分布 | 13 域中 5 个 smoke（不打 LLM）/ 8 个 full（直调或真实 LLM）| Tier 1 25 task 扩展 LLM 路径域（域 1/2/3 升级为真实 LLM + 域 4/8/9）|
| R-2 τ-bench airline | task 总数估 35-50，待 PoC `len(tasks.TASKS)` 实测；user simulator 基于 LLM；mock 数据库需 per-task reset | 抽取 15 task 分层覆盖 5 类操作；reset 机制待 PoC 验证 |
| R-3 GAIA Level 2 | 245 个 task，答案字符串精确匹配；HF gated dataset 需申请访问；单 task 最坏 300s | HF 访问申请是 P0 阻塞项；5 个 task 纳入 Daily Bench 耗时边际可行 |
| R-4 H1/H2/H3 信号 | EventStore query 为主（SUBAGENT_SPAWNED / MEMORY_RECALL_COMPLETED / BEHAVIOR_PACK_LOADED / CONTROL_METADATA_UPDATED）| Tier 3 5 个 task 通过 audit chain 断言哲学特性 |
| R-5 API rate limit | Sonnet 4.5 Tier 4：4000 RPM / 2M ITPM；8 并行安全；gradual ramp 防 spike 429 | 8 并行 + gradual ramp（错开 0.5s 启动）+ retry-after header 优先 |

**PoC 必须优先验证的 4 个假设（按优先级）**：

| 优先级 | 假设编号 | 假设内容 | 若不成立的降级方案 |
|--------|---------|---------|-----------------|
| P0 | PoC-H1 | HF gaia-benchmark/GAIA 访问申请成功 | 用公开样本（arxiv 附录）手工构造 5 个 Level 2 task，标注 `[GAIA-FALLBACK]` |
| P1 | PoC-H2 | τ-bench airline domain task 数 ≥ 15 | 抽样数量降低或补入 retail domain；抽样策略 spec Phase B 调整 |
| P2 | PoC-H3 | 8 并行 OctoHarness 实例的 SQLite WAL contention p95 latency 可接受（≤ 2s 额外 overhead） | 改为共享 store 方案（单 harness 实例复用，task 间仅清理 task_store 记录）|
| P3 | PoC-H4 | τ-bench airline mock 数据库 per-task reset 机制可行（无 side effect 污染）| 每个 adapter 实例独立 mock 数据目录 copy（file-based isolation）|

### 0.2 范围决策表（用户已拍板 + spec 阶段设计决策）

| 决策 | 选择 | 理由 |
|------|------|------|
| Daily Bench 耗时 | ≤ 1 hour | 用户已拍板 |
| 每 task 跑次数 | 3 次取多数（pass/fail majority vote） | 降低 LLM 随机性；3 次奇数可避免平票 |
| 并发度 | 8（asyncio.Semaphore(8)）+ gradual ramp | Sonnet 4.5 Tier 4 RPM 4000 充裕；启动错开 0.5s 防 spike |
| 控变量 LLM（被测 Agent） | Claude Sonnet 4.5，temperature=0，固定 seed | OctoAgent production 主用 alias |
| user simulator LLM（τ-bench） | Claude Sonnet 4.6 | 与被测 Agent Sonnet 4.5 隔离；保证 simulator 决策质量（GATE_DESIGN OQ-1 用户拍板）|
| 业界 task | τ-bench airline 15 + GAIA Level 2 5 | 排除 L3（单 task 3-10min 超 1h 约束）|
| Full Bench 150 task | 不在 F103d 范围，推 M6 中段 | 用户已拍板 |
| 实施方式 | Phase 0 PoC（1 天）+ Phase A-F（PoC 通过后）| mid-implement GATE 在 implement 阶段内部处理 |
| benchmark 目录 | 新建 `benchmarks/` 顶层 + CLI 入口允许新增独立文件 | 零侵入边界：不改现有 production 文件内容（GATE_DESIGN W1 用户拍板）|
| baseline 跑哪个 commit | F103d 完成 commit | benchmark 零侵入，等价 a69fe9c（GATE_DESIGN OQ-3 自动采纳）|
| 评分维度 | pass/fail（65%）+ partial（25%）+ efficiency（10%）| efficiency 评分启用时机推迟到 M6（M5 首次跑后用 p50 作为基准）（IA-4 自动采纳）|
| INCONSISTENT 容忍 | ≤ 5% | 见 SC-011（BC-1 自动采纳）|
| Tool Registry 隔离 | contextmanager 临时注册 + finally 清理 | FR-E01 改写（GATE_DESIGN W2 用户拍板，Codex pre-impl 重点审查 race）|
| 断点续跑 | SQLite 持久化每个 BenchmarkRun 结果 | 50 task × 3 次 = 150 次 LLM 执行，中断后可 resume |
| Codex review | pre-impl + 每 Phase 末 + Final cross-Phase | 标准模式（CLAUDE.local.md §"Codex Adversarial Review"）|

### 0.3 三层 task 结构

| Tier | 数量 | 类型 | 来源 | 评分方式 |
|------|------|------|------|---------|
| Tier 1 私有 | 25 task | OctoAgent 能力域 | F087 e2e_live 13 域扩展（升级为真实 LLM 路径）+ 4 个 Connor 真实场景 | EventStore query 断言 event chain |
| Tier 2 业界 | 20 task | τ-bench airline (15) + GAIA Level 2 (5) | Sierra-Research τ-bench repo + HuggingFace gaia-benchmark/GAIA | τ-bench：Pass@1（对照 user_simulator 期望 actions）；GAIA：字符串精确匹配 |
| Tier 3 创新点 | 5 task | H1/H2/H3 哲学差异化 | 基于 R-4 可观测信号设计 | EventStore audit chain 断言（H1/H2/H3 特定信号存在） |
| **合计** | **50 task** | | | |

**Tier 1 域分布（25 task）**：

| 能力域 | task 数 | 对应 F087 域 |
|--------|---------|------------|
| 基础工具调用（真实 LLM 决策） | 3 | 域 1 升级版 |
| USER.md 全链路（读写 + 观测） | 3 | 域 2 升级版 |
| 冻结快照（SnapshotStore 读写）| 2 | 域 3 升级版 |
| Memory（promote / recall）| 3 | 域 4 |
| Skill Pipeline（DAG 触发）| 2 | 域 6/7 |
| 委托（delegate_task + A2A）| 4 | 域 8/9 |
| max_depth 限制（深度超限保护）| 1 | 域 10 |
| Routine cron 触发 | 1 | 域 13 |
| ThreatScanner block（安全拦截）| 2 | 域 11 升级版 |
| Connor 真实场景 | 4 | 新增（待 Phase A 详细定义）|

**Tier 3 task 设计（5 task）**：

| Task ID | 哲学 | 验证维度 | 核心断言信号 |
|---------|------|---------|------------|
| T3-1 | H1 | 主 Agent 唯一 user-facing speaker | SUBAGENT_SPAWNED 存在 + 无 Worker session 直接 user-facing event |
| T3-2 | H2 | Worker AGENT_PRIVATE memory 隔离 | MEMORY_RECALL_COMPLETED.namespace=AGENT_PRIVATE；跨 Worker 读隔离 |
| T3-3 | H3-A | Subagent spawn-and-die + 共享 memory | SUBAGENT_COMPLETED 存在；`caller_memory_namespace_ids` 非空 |
| T3-4 | H3-B | A2A Worker ask_back 中途澄清 | WAITING_INPUT → resume 全链路；`is_caller_worker_signal` 持久化验证（N-H1 修复）|
| T3-5 | H3 | Worker→Worker A2A 委托（D14 解禁后合法）| source_runtime_kind=WORKER 显式；完整 audit chain |

### 0.4 双阶段 + Phase 拆分

| 阶段 | 内容 | 预估时间 | 产出 |
|------|------|---------|------|
| Phase 0 PoC | 手工跑 5 task（1 Tier 1 / 2 Tier 2 / 1 Tier 3 / 1 混合）+ adapter 可行性实测 | ≤ 1 天 | `phase-0-poc-report.md`；用户拍板后进 Phase A-F |
| Phase A | Tier 1 25 task YAML + EventStore 自动评分 | 2-3 天 | `benchmarks/tiers/tier1/*.yaml` + scorer |
| Phase B | Tier 2 adapter（τ-bench airline + GAIA L2）| 2-3 天 | `benchmarks/tiers/tier2/tau_bench_adapter.py` + `gaia_adapter.py` |
| Phase C | Tier 3 5 task H1/H2/H3 + 专用 audit scorer | 1-2 天 | `benchmarks/tiers/tier3/*.yaml` + audit scorer |
| Phase D | Runner + Scorer + Reporter 完整实现 | 2-3 天 | `benchmarks/runner/` 全套；`octo bench daily` CLI |
| Phase E | M5 baseline 跑（50 task × 3 × 8 并行）+ 报告归档 | 1 天 | `benchmarks/baselines/m5-baseline.json` + Markdown 报告 |
| Phase F | Final review + completion-report + handoff | 0.5 天 | `completion-report.md` + `handoff.md`（给 F104）|

**推荐 Phase 顺序**：Phase 0 PoC → （用户拍板）→ A → B → C → D → E → F。D 依赖 A/B/C 提供 task 定义，E 依赖 D 完整 runner。

---

## 1. User Scenarios & Testing

### User Story 1 — M5 baseline 首次建立（Priority: P1）

作为 Connor，我希望能运行 `octo bench daily`，在 1 小时内得到一份 50 task × 3 次运行的 pass rate 报告，作为 M5 完成状态的可量化基线，以便 M6 的每个 Feature 可以与之对比。

**Why P1**：这是 F103d 的核心目标。没有 M5 baseline，M6 的 Feature 验收就没有对比锚点。所有其他 User Story 都以此为前提。

**Independent Test**：在 F103c baseline 代码不变的情况下，完整跑一次 Daily Bench，检查产出报告文件存在且包含三层维度数据。

**Acceptance Scenarios**:

- **AC1-1**: **Given** F103c baseline 代码（a69fe9c），**When** 执行 `octo bench daily`，**Then** 50 task（Tier 1 × 25 + Tier 2 × 20 + Tier 3 × 5）全部被执行 3 次（含 QUOTA_SKIP 计入），实测总耗时 ≤ 1 hour
- **AC1-2**: **Given** AC1-1 完成，**When** 查看报告输出，**Then** 报告同时产出 JSON 文件（`baselines/m5-baseline.json`）+ Markdown 摘要，含：总 pass rate / 三层 pass rate / 平均 token_usage / 平均 duration
- **AC1-3**: **Given** AC1-1 完成，**When** 查看 SQLite 持久化存储，**Then** 150 条 BenchmarkRun 记录可查（每 task 3 次），含 task_id / iteration / result / duration / token_usage
- **AC1-4**: **Given** M5 baseline 已存档，**When** M6 Feature 完成后重跑同套 Daily Bench，**Then** 报告包含"vs M5 baseline"diff 视图（pass rate 变化 / 新增 regression / 新增 improvement）

### User Story 2 — PoC 决策门（Priority: P1）

作为 Connor，我希望在投入完整 50 task 实施之前，先看到 5 task PoC 的实测结论（耗时 + adapter 可行性），以便决定是否需要调整范围或方案。

**Why P1**：tech-research 识别了 4 个高风险假设（HF 访问 / τ-bench task 数 / SQLite WAL contention / mock DB reset）。PoC 不通过不进完整实施，是最重要的风险控制节点。

**Independent Test**：Phase 0 产出 `phase-0-poc-report.md`，包含 5 task 实测耗时 + 4 个假设的实测结论，用户据此拍板。

**Acceptance Scenarios**:

- **AC2-1**: **Given** Phase 0 PoC 代码就绪，**When** 手工跑 5 个精心选取的 task（Tier 1 × 1 + τ-bench airline × 1 + GAIA Level 2 × 1 + Tier 3 H1 × 1 + 并发压测 × 1，GATE_DESIGN OQ-2 用户拍板），**Then** 每个 task 的实测耗时（wall clock）被记录
- **AC2-1b**: **Given** AC2-1 第 5 个 task = 并发压测（POC-CONC），**When** 8 worker 并发同时跑 5 个 Tier 1 task（含 SQLite 读写），**Then** p95 latency 测量并记录 ≤ 2s 额外 overhead（PoC-H3 验证）
- **AC2-2**: **Given** PoC 跑完，**When** 查看 `phase-0-poc-report.md`，**Then** 4 个假设（PoC-H1 ~ PoC-H4）各有"成立 / 不成立 / 待确认"结论 + 降级方案说明
- **AC2-3**: **Given** `phase-0-poc-report.md` 产出，**When** 所有 4 个 P0/P1 假设成立，**Then** 用户拍板进入 Phase A-F；任一 P0 假设不成立 → 用户决策降级方案后再进

### User Story 3 — 容错与 429 graceful（Priority: P1）

作为 Connor，我希望 benchmark 在遇到 API rate limit（429）或单 task 超时时不 hard fail，整个 Daily Bench 跑完后再统一报告哪些 task 被跳过，以便 1 hour 内跑完而不需要人工干预。

**Why P1**：8 并发 × 3 次 = 150 次 LLM 调用，429 发生是大概率事件。hard fail 会让 benchmark 无法自动完成，破坏 M6 Feature 验收流程。

**Independent Test**：模拟 2 个 task 触发 429 + 1 个 task 超时，验证 benchmark 继续跑完剩余 task，报告中标记被跳过的 task。

**Acceptance Scenarios**:

- **AC3-1**: **Given** benchmark 运行中某个 task 的 LLM 调用返回 429，**When** retry-after header 存在，**Then** benchmark 精确等待 header 指定秒数后重试，最多重试 3 次；全部重试失败 → 标记 `QUOTA_SKIP`，不计入 pass rate 分母
- **AC3-2**: **Given** retry-after header 不存在，**When** 遇到 429，**Then** 使用 exponential backoff with jitter（初始 1s，最大 60s），最多重试 3 次
- **AC3-3**: **Given** 某 task 执行超过 5 分钟，**When** timeout 触发，**Then** 标记 `TIMEOUT`，不影响其他并发 task；报告中单独列出超时 task
- **AC3-4**: **Given** benchmark 全程跑完，**When** 任一 task 标记为 QUOTA_SKIP 或 TIMEOUT，**Then** 报告分母不计入这些 task（pass rate 仍有意义）+ 备注跳过原因

### User Story 4 — 三层维度可分层查看（Priority: P2）

作为 Connor，我希望报告能分别展示 Tier 1 私有 / Tier 2 业界 / Tier 3 创新点 的 pass rate，以便理解 OctoAgent 在不同维度的能力强弱。

**Why P2**：三层维度意义不同（内部能力 / 业界对齐 / 哲学差异化）。混在一起的总 pass rate 会掩盖重要信号。

**Independent Test**：运行完整 Daily Bench，在 JSON 报告中分别查找 `tier1` / `tier2` / `tier3` 字段的独立 pass rate 数据。

**Acceptance Scenarios**:

- **AC4-1**: **Given** Daily Bench 跑完，**When** 查看 JSON 报告，**Then** 顶层结构含 `summary`（总体）+ `by_tier`（tier1/tier2/tier3 独立数据）+ `by_domain`（各能力域细分）
- **AC4-2**: **Given** AC4-1，**When** 查看 Tier 2 数据，**Then** τ-bench airline 和 GAIA Level 2 各有独立 pass rate（不混合）
- **AC4-3**: **Given** AC4-1，**When** 查看 Tier 3 数据，**Then** T3-1 ~ T3-5 每个 task 的 audit 信号断言结果（哪个信号存在 / 不存在）可查

### User Story 5 — 断点续跑（Priority: P2）

作为 Connor，我希望 benchmark 在中途被中断（Ctrl+C / 进程崩溃）后能续跑，不需要从头开始，以便避免 1 小时 + 的重跑浪费。

**Why P2**：150 次 LLM 调用的 Daily Bench 在网络不稳或意外中断时若需全部重跑，成本很高。断点续跑保护已完成的进度。

**Independent Test**：跑到 50% 时强制中断，重新启动 `octo bench daily --resume <run_id>`，验证仅续跑未完成的 task。

**Acceptance Scenarios**:

- **AC5-1**: **Given** Daily Bench 跑到 50% 时被 Ctrl+C 中断，**When** 重新执行 `octo bench daily --resume <run_id>`，**Then** 仅续跑 SQLite 中状态为 PENDING / IN_PROGRESS 的 task，已完成的 task 直接使用存储结果
- **AC5-2**: **Given** AC5-1 续跑完成，**When** 查看最终报告，**Then** 报告数据与一次性完整跑等价（不重复计算已完成 task）

### User Story 6 — M6 Feature 验收对比（Priority: P3）

作为 Connor，我希望 M6 每个 Feature 完成后，能用 `octo bench daily --compare m5` 看出 regression 和 improvement，以便验证 Feature 没有破坏已有能力并且实现了预期提升。

**Why P3**：这是 F103d 建立 baseline 的最终目的，但 M6 Feature 还未开始，此用例在 F103d 范围内只需保证基础设施就位（M5 baseline 已存档 + diff 对比能力就位），具体 M6 验收在 M6 各 Feature 中执行。

**Independent Test**：运行 `octo bench daily --compare m5`，验证报告含 delta 数据。

**Acceptance Scenarios**:

- **AC6-1**: **Given** M5 baseline 已存档且 M6 某 Feature 改动已在 production 代码中，**When** 执行 `octo bench daily --compare m5`，**Then** 报告额外输出 `delta` 区块：Δ pass rate（总体 + 三层）+ 回退 task 列表 + 新增通过 task 列表
- **AC6-2**: **Given** 无 M5 baseline 存档时执行 `--compare m5`，**Then** 命令报错"M5 baseline not found，请先跑 octo bench daily 建立 baseline"，不静默失败

---

## 2. Edge Cases

- **8 并行遇到 429 波次**：gradual ramp（每路 worker slot 错开 0.5s 启动）+ 首优先 retry-after header + exponential backoff fallback；所有重试耗尽 → QUOTA_SKIP，不 hard fail；benchmark 结束时报告 QUOTA_SKIP 总数
- **HF GAIA 申请未通过（PoC-H1 不成立）**：降级使用公开样本（arxiv 2311.12983 附录样本）手工构造 5 个 Level 2 task，标注 `[GAIA-FALLBACK]`；GAIA 子域 pass rate 注明"非官方数据集"
- **τ-bench airline task 数 < 15（PoC-H2 不成立）**：从 retail domain 补充差额（τ-bench 原生支持 retail domain）；分层抽样策略在 Phase B 实施时按实测数量调整
- **SQLite WAL contention 不可接受（PoC-H3 不成立）**：降级为共享 store 方案（单 OctoHarness 实例 + task 执行后仅清理 task_store 特定记录），牺牲 task 间完全隔离换取并发稳定性
- **τ-bench mock 数据库 per-task reset 有 side effect 污染（PoC-H4 不成立）**：改为每个 adapter 实例独立 copy mock 数据目录到 tmpdir，file-based isolation
- **LLM API 完全不可用**（非 429，而是 5xx 或网络断）：单 task 标记 INFRA_ERROR，整个 benchmark 不停止；连续 5 个 task 均 INFRA_ERROR 时 benchmark 主动停止并报错"LLM API unavailable"
- **单 task 耗时 > 5 分钟**：标记 TIMEOUT + 释放 worker slot 给后续 task；GAIA Level 2 task 单独配置 timeout=8min（R-3 最坏 300s × 安全系数 1.6）
- **3 次运行结果不一致（非 majority）**：即 2 次 PASS 1 次 FAIL 仍记 PASS；若出现 PASS=1 FAIL=1 QUOTA_SKIP=1 → 有效样本 2 次，仍取多数（PASS=1 FAIL=1 时记 INCONSISTENT，不算入 pass rate）
- **benchmark 目录污染 production 代码**：`benchmarks/` 不加入 production FastAPI app 路由；不修改 `packages/` 或 `apps/gateway/` 下任何文件（强制约束）

---

## 3. Functional Requirements

### FR 系列 A — benchmark runner

- **FR-A01** [必须]: runner MUST 通过 `asyncio.Semaphore(8)` 控制最大并发，不超过 8 个 task 同时执行
- **FR-A02** [必须]: runner MUST 在启动 8 个 worker slot 时错开 0.5s（gradual ramp），防止 t=0 spike 触发 429
- **FR-A03** [必须]: runner MUST 为每个 task 配置独立超时（Tier 1/3 默认 5min；GAIA Level 2 默认 8min）；超时触发 → 标记 `TIMEOUT`，释放 worker slot
- **FR-A04** [必须]: runner MUST 对每个 task 执行 3 次（iterations），取 majority vote 作为最终 result
- **FR-A05** [必须]: 每个 BenchmarkRun 执行完成后立即写入 SQLite（append-only），支持断点续跑
- **FR-A06** [必须]: runner 支持 `--resume <run_id>` 模式，跳过 SQLite 中 result 已非 PENDING 的 task
- **FR-A07** [必须]: runner MUST 响应 429：优先读 `retry-after` header 等待；header 缺失时使用 exponential backoff with jitter（初始 1s，最大 60s）；最多重试 3 次；超限 → `QUOTA_SKIP`

### FR 系列 B — benchmark scorer

- **FR-B01** [必须]: scorer MUST 按三层不同评分逻辑处理：Tier 1/3 走 EventStore query 断言；Tier 2 τ-bench 走 Pass@1 对照；Tier 2 GAIA 走字符串精确匹配（含 normalized 比较：大小写不敏感 + 去首尾空格）
- **FR-B02** [必须]: scorer MUST 计算三维得分：pass/fail（65% 权重）+ partial（25% 权重，有 Tier 1 EventStore 断言部分通过时）+ efficiency（10% 权重，token_usage vs. task baseline 预算比）
- **FR-B03** [必须]: scorer MUST 支持 LLM-as-judge fallback（Tier 1 partial 评分时，EventStore 无法完全断言的语义场景）；LLM judge 使用同一控变量模型（Sonnet 4.5 temperature=0）
- **FR-B04** [必须]: Tier 3 scorer MUST 断言特定 audit 信号：EventStore query 返回指定 EventType + 指定字段值；信号存在 = pass，不存在 = fail（不允许模糊断言）
- **FR-B05** [必须]: τ-bench user simulator 使用 **Claude Sonnet 4.6**（与被测 Agent Sonnet 4.5 隔离）；user simulator 不计入控变量指标。选择 Sonnet 4.6 而非 Haiku 是为保证 simulator 决策质量足够 challenging（GATE_DESIGN OQ-1 用户拍板）

### FR 系列 C — benchmark reporter

- **FR-C01** [必须]: reporter MUST 同时产出 JSON（机器可读，供 --compare diff 消费）+ Markdown 摘要（人类可读）
- **FR-C02** [必须]: JSON 报告结构 MUST 含：`run_id` / `baseline_sha` / `created_at` / `summary`（总 pass rate + 三维得分）/ `by_tier`（tier1/2/3 独立数据）/ `by_domain`（能力域细分）/ `skipped`（QUOTA_SKIP + TIMEOUT 列表）
- **FR-C03** [必须]: `--compare <baseline_id>` 模式 MUST 额外输出 `delta` 区块：Δ pass rate / regression task 列表 / improvement task 列表
- **FR-C04** [必须]: reporter 写入 `benchmarks/baselines/` 目录并命名为 `{baseline_sha}-{timestamp}.json`；M5 baseline 额外创建 `m5-baseline.json` 软链接

### FR 系列 D — Tier 1 私有 task

- **FR-D01** [必须]: 25 个 Tier 1 task 全部 YAML 化（`benchmarks/tiers/tier1/*.yaml`），每个文件含：`task_id` / `tier` / `domain` / `prompt` / `expected_events`（EventStore 断言列表）/ `timeout_seconds` / `partial_signals`
- **FR-D02** [必须]: Tier 1 task MUST 使用真实 LLM 路径（不使用 stub transport）；每个 task 注入独立 `data_dir` 保证 store 隔离
- **FR-D03** [必须]: 覆盖 9 个能力域（见 0.3 域分布表）；Connor 真实场景 4 个 task 在 Phase A 详细定义（PoC 后）

### FR 系列 E — Tier 2 adapter

- **FR-E01** [必须]: τ-bench adapter（`benchmarks/tiers/tier2/tau_bench_adapter.py`）MUST：从 τ-bench repo 读取 airline domain task；per-task reset mock 数据库；用 **`contextlib.contextmanager` 临时注册 airline tools 到 production Tool Registry 单例**，在 `try/finally` 中保证清理（注册期 acquire lock + 注册条目打 benchmark scope tag 以便 audit / 不与 production tool name 冲突）；Phase 0 PoC MUST 实测临时注册无 race condition 后才进 Phase B；Codex pre-impl review 重点审查 race condition / 状态泄漏（GATE_DESIGN W2 用户拍板）
- **FR-E02** [必须]: τ-bench 15 个 task 按分层抽样（R-2 策略：booking/cancellation/upgrade/passenger/baggage/payment 6 类）；待 PoC-H2 实测 task 总数后最终确认
- **FR-E03** [必须]: GAIA adapter（`benchmarks/tiers/tier2/gaia_adapter.py`）MUST：从 HF 数据集加载 Level 2 task（PoC-H1 通过）或 fallback 公开样本；字符串 normalized 匹配评分
- **FR-E04** [必须]: 5 个 GAIA Level 2 task 按 R-3 分层抽样：web search 类 2 个 + 文档解析 2 个 + 多工具串联 1 个

### FR 系列 F — Tier 3 哲学 task

- **FR-F01** [必须]: 5 个 Tier 3 task YAML 化（`benchmarks/tiers/tier3/*.yaml`），每个文件含：`task_id` / `philosophy`（H1/H2/H3-A/H3-B/H3）/ `prompt` / `audit_assertions`（list，每条含 event_type + 必要 field 值）
- **FR-F02** [必须]: T3-1 ~ T3-5 分别覆盖 H1 / H2 / H3-A / H3-B / H3（各 1 个），不重复哲学维度
- **FR-F03** [必须]: Tier 3 scorer MUST 逐条断言 `audit_assertions`，全部通过 = PASS，任一不通过 = FAIL + 报告哪条断言失败

### FR 系列 G — PoC 专用

- **FR-G01** [必须]: Phase 0 PoC MUST 产出 `phase-0-poc-report.md`（在 `.specify/features/103d-octobench/` 下），含：5 task 实测耗时 / PoC-H1 ~ PoC-H4 成立结论 / 推荐 Phase A-F 是否调整范围 / 任何 blocker
- **FR-G02** [必须]: PoC 不需要完整 runner 实现，允许手工脚本跑 5 task；PoC 不写入 SQLite baseline

### FR 系列 H — 系统约束

- **FR-H01** [必须]: benchmark 模块 MUST 零侵入 production，定义如下（GATE_DESIGN W1 用户拍板，analyze F-02 patch）：
  - **禁止**修改 `packages/` / `apps/gateway/` / `apps/web/` 下任何**现有文件的现有字段/逻辑**（含 source / config / pyproject 已有内容）
  - **允许**新增独立模块（如 `apps/gateway/src/octoagent/gateway/cli/bench_commands.py` 或同级新文件）
  - **允许**在 pyproject.toml 等 config 文件**追加新条目**（entry point / dependency / dev-tool config），但**不修改已有条目的值**；新增条目算"新增独立内容"不算"修改现有内容"（明确豁免）
  - 所有 benchmark 业务逻辑必须封装在 `benchmarks/` 顶层目录下；CLI 层仅做 `from benchmarks.runner import ...` 的 thin wrapper（≤ 30 行）
  - Tier 1 task 执行时通过 OctoHarness 4 个 DI 钩子注入隔离，**不改 OctoHarness 自身代码**
  - 验证手段：Phase 末 REGRESSION task 用 `git diff --name-only origin/master -- packages/ apps/` 配合 `git diff` 内容审查，确保现有文件 0 字节变更（新增文件 0 出现在 diff 内容部分）
- **FR-H02** [必须]: 控变量严格：LLM = Claude Sonnet 4.5，temperature=0，seed 固定（benchmark CLI 参数可覆盖，但 M5 baseline 必须用默认值）
- **FR-H03** [必须]: 每个 Phase 完成后全量回归必须 0 regression vs F103c baseline（a69fe9c），e2e_smoke PASS
- **FR-H04** [必须]: benchmark task 的 token_usage 与 duration 必须被记录（BenchmarkRun 必填字段），供 efficiency 维度评分使用
- **FR-H05** [可选]: benchmark conftest 复用 F087 e2e_live 的 module singleton reset 清单（5 类凭证 env / 4 个 OCTOAGENT_* 路径 env / 5 项 module 单例），防止 task 间状态泄漏

**YAGNI 标注总结**：

| FR 范围 | 标注 | 说明 |
|---------|------|------|
| FR-A01~A07 Runner 核心 | [必须] | 去掉无法跑 benchmark |
| FR-B01~B04 Scorer 核心 | [必须] | 去掉无法评分 |
| FR-B05 Haiku user simulator | [可选] | 去掉用 Sonnet 4.5 也可运行，仅影响成本 |
| FR-C01~C04 Reporter | [必须] | 去掉无法产出 baseline |
| FR-D01~D03 Tier 1 task | [必须] | Tier 1 是 M5 能力核心验证 |
| FR-E01~E04 Tier 2 adapter | [必须] | 业界 benchmark 是 spec 核心要求 |
| FR-F01~F03 Tier 3 task | [必须] | H1/H2/H3 哲学验证是 OctoBench 差异化核心 |
| FR-G01~G02 PoC | [必须] | PoC 是风险门禁，不可跳过 |
| FR-H01~H04 约束 | [必须] | 零侵入 + 控变量是 benchmark 可信度基础 |
| FR-H05 singleton reset 复用 | [可选] | 去掉依然可跑，但 task 间可能有状态泄漏 |

---

## 4. Key Entities

### BenchmarkTask

task 定义单元，来自 YAML 文件或 adapter 加载。

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | str | 唯一标识（如 `T1-001` / `T2-tau-001` / `T3-H1-001`）|
| `tier` | int | 1 / 2 / 3 |
| `domain` | str | 能力域标签（如 `memory` / `delegation` / `tau_bench_airline`）|
| `prompt` | str | 发送给 Agent 的用户消息 |
| `expected_events` | list[dict] | Tier 1/3：EventStore 断言列表（event_type + field 值）|
| `expected_answer` | str \| None | Tier 2 GAIA：期望答案字符串 |
| `timeout_seconds` | int | 默认 300；GAIA Level 2 默认 480 |
| `partial_signals` | list[dict] \| None | 部分通过时的 EventStore 信号（供 partial 得分）|

### BenchmarkRun

单次 task 执行记录（每个 task × 3 iteration = 3 个 BenchmarkRun）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_id` | str | UUID |
| `bench_session_id` | str | 同一次 Daily Bench 的分组 ID |
| `task_id` | str | 关联 BenchmarkTask |
| `iteration` | int | 1 / 2 / 3 |
| `result` | str | PASS / FAIL / PARTIAL / TIMEOUT / QUOTA_SKIP / INFRA_ERROR / INCONSISTENT |
| `score` | float \| None | 三维加权得分（0.0 ~ 1.0） |
| `duration_seconds` | float | wall clock 时间 |
| `token_usage` | dict | `{input: int, output: int, cache_read: int}` |
| `audit_assertions` | list[dict] \| None | Tier 3：每条断言结果 |
| `created_at` | datetime | UTC |

### BenchmarkBaseline

一次 Daily Bench 完成后的聚合快照。

| 字段 | 类型 | 说明 |
|------|------|------|
| `baseline_id` | str | UUID |
| `commit_sha` | str | 对应 Git commit（如 `a69fe9c` for M5）|
| `label` | str | 人类可读标签（如 `m5-baseline`）|
| `aggregated_metrics` | dict | 总 pass rate + 三层 pass rate + 三维得分 + token 统计 |
| `task_results` | dict | task_id → majority result 映射 |
| `created_at` | datetime | UTC |
| `duration_minutes` | float | 总耗时 |

### ScoringRubric

每个 tier 的评分逻辑配置。

| 字段 | 类型 | 说明 |
|------|------|------|
| `rubric_id` | str | 唯一标识（如 `tier1-v1`）|
| `tier` | int | 1 / 2 / 3 |
| `pass_fail_weight` | float | 默认 0.65 |
| `partial_weight` | float | 默认 0.25 |
| `efficiency_weight` | float | 默认 0.10 |
| `pass_logic` | str | `event_store_assert` / `pass_at_1` / `string_match` / `audit_chain_assert` |
| `partial_logic` | str \| None | Tier 1 可选 `llm_judge` |
| `efficiency_baseline_tokens` | int \| None | 效率基准 token 数（per task domain）|

---

## 5. Success Criteria

- **SC-001**: M5 baseline 跑出：50 task × 3 次 × 8 并行，实测总耗时 ≤ 1 hour（wall clock）
- **SC-002**: PoC 产出 `phase-0-poc-report.md`，包含 5 task 实测耗时数据 + PoC-H1 ~ PoC-H4 四个假设的实测成立结论（或降级方案说明）
- **SC-003**: M5 baseline pass rate 被记录（真实数值，不预设目标）；JSON 报告存档在 `benchmarks/baselines/m5-baseline.json` 并可供 M6 Feature 对比
- **SC-004**: 全量回归 0 regression vs F103c baseline（a69fe9c），每个 Phase 后 e2e_smoke PASS
- **SC-005**: Tier 1 25 task / Tier 2 20 task / Tier 3 5 task 全部 YAML 化，可通过 `octo bench daily` 自动加载执行
- **SC-006**: 429 graceful：PoC + Phase E 实测中，0 个 task 因 429 hard fail（全部为 QUOTA_SKIP 或重试成功）
- **SC-007**: 中断后断点续跑：`--resume` 模式下续跑完成的 task 数量 ≥ 95% 已完成 task（不重复执行）
- **SC-008**: 报告含 M5 baseline 数据快照（JSON + Markdown）+ `handoff.md` 给 F104 含 M6 各 Feature 验收门槛建议（基于 M5 pass rate 推算 regression 警戒线）
- **SC-009**: benchmark 模块零侵入验证：grep 确认无 production 文件被修改（`packages/` / `apps/gateway/` / `apps/web/` 下 0 文件变更）
- **SC-010**: Tier 3 5 个 task 覆盖 H1 / H2 / H3-A / H3-B / H3 各 1 个，每个 task 的 audit 断言信号有文档说明（YAML `audit_assertions` 字段非空）
- **SC-011**: INCONSISTENT task 占比 ≤ 5%（3 次 majority vote 不一致的 task 占 50 个总 task 不超过 2-3 个；超过 → Phase E 末单独 RCA：是 LLM 随机性还是 scorer 健壮性问题）（BC-1 自动采纳）

---

## 6. Phase 拆分明细

### Phase 0 — PoC（目标：风险验证，≤ 1 天）

**目标**：手工验证 4 个关键假设，产出决策报告。

**内容**：
- 申请 HF GAIA 数据集访问（PoC-H1，P0 阻塞项）
- 手工脚本跑 5 task（1 Tier 1 基础工具 / 1 τ-bench airline / 1 GAIA Level 2 / 1 Tier 3 H1 / 1 并发压测用）
- 实测 `len(tau_bench.envs.airline.tasks.TASKS)` 确认 task 总数（PoC-H2）
- 测量 5 task 跑完时间 + 观察 SQLite WAL contention（PoC-H3）
- 验证 τ-bench mock DB per-task reset 可行性（PoC-H4）
- **产出**：`phase-0-poc-report.md`

**mid-implement GATE**：用户读取 `phase-0-poc-report.md` 后拍板进入 Phase A 或调整范围。

**Codex review**：Phase 0 完成后 pre-impl review（spec + plan 整体），不需要 per-PoC review。

### Phase A — Tier 1（目标：25 private task YAML + EventStore scorer）

**目标**：完成 Tier 1 全部 task 定义 + 自动评分逻辑。

**内容**：
- 按 0.3 域分布表定义 25 个 task YAML 文件
- 实现 `benchmarks/runner/scorer.py` Tier 1 评分逻辑（EventStore query + LLM judge fallback）
- 补充 Connor 真实场景 4 个 task 定义（PoC 后与用户确认场景内容）
- 单 task 手工验证（不跑全 25 个）

**Codex review**：Phase A 末 per-Phase review。

### Phase B — Tier 2 adapter（目标：τ-bench airline + GAIA L2 接入）

**目标**：两个业界 benchmark 的 adapter 实现 + PoC 确认的抽样策略落地。

**内容**：
- `tau_bench_adapter.py`：airline domain 接入 + mock DB per-task reset + Tool Registry 临时注册/清理
- `gaia_adapter.py`：HF 数据集加载（或 fallback 公开样本）+ 字符串 normalized 匹配
- 按 PoC-H2 实测 task 总数确认最终 15 task 分层抽样
- 单 adapter 5 task 验证

**Codex review**：Phase B 末 per-Phase review。

### Phase C — Tier 3 哲学 task（目标：H1/H2/H3 audit chain 断言）

**目标**：5 个 Tier 3 task 定义 + audit chain scorer。

**内容**：
- 5 个 YAML 文件（T3-1 ~ T3-5，对应 0.3 设计表）
- `benchmarks/runner/scorer.py` Tier 3 扩展：audit_assertions 逐条断言
- 验证 T3-4（ask_back N-H1 修复）的 audit 信号可被正确 query

**Codex review**：Phase C 末 per-Phase review。

### Phase D — Runner / Scorer / Reporter 完整实现（目标：`octo bench daily` CLI 就绪）

**目标**：完整 benchmark runner 主体 + CLI 入口。

**内容**：
- `benchmarks/runner/worker.py`：单 task 执行逻辑（OctoHarness 注入 / 直调主路径 + timeout + retry）
- `benchmarks/runner/scorer.py`：三层评分统一接口（Phase A/B/C 分别实现的 scorer 整合）
- `benchmarks/runner/reporter.py`：JSON + Markdown 报告 + `--compare` diff 逻辑
- SQLite schema + BenchmarkRun 持久化 + `--resume` 续跑模式
- `octo bench daily` CLI 命令注册（`apps/gateway/src/.../cli/bench_commands.py`）
- **gradual ramp + asyncio.Semaphore(8) 实现**

**Codex review**：Phase D 末 per-Phase review（这是最复杂 Phase，review 优先级最高）。

### Phase E — M5 baseline 跑（目标：存档基线数据）

**目标**：在 F103c baseline 代码上完整跑一次 Daily Bench，产出 M5 baseline。

**内容**：
- 确认 `git checkout a69fe9c` 状态下跑（或等价：benchmark 本身不改 production 代码，直接在当前分支跑即可）
- 50 task × 3 次 × 8 并行 完整执行
- 实测总耗时（验证 SC-001 ≤ 1 hour）
- 存档 `benchmarks/baselines/m5-baseline.json` + 创建 Markdown 摘要

**Codex review**：Phase E 末 Final cross-Phase review（整个 benchmark 模块的综合审查）。

### Phase F — Final + completion-report + handoff

**目标**：闭环文档 + 给 F104 的 handoff。

**内容**：
- `completion-report.md`：对照 spec 全部 AC 逐一标注通过情况
- `handoff.md`（给 F104 文件工作台）：M5 baseline 数据快照 + M6 各 Feature 验收门槛建议（基于 M5 pass rate 推算 regression 警戒线，如"Tier 1 pass rate 低于 M5 - 10% 触发 code review"）
- 清理 Phase 0 临时脚本 / poc report 归档

**Codex review**：Final cross-Phase 已在 Phase E 末完成；Phase F 是文档 Phase，不需要额外 review（命中"纯文档微改"豁免条件）。

---

## 7. 不在范围（明确排除）

| 排除项 | 理由 |
|--------|------|
| Full Bench 150 task | 推 M6 中段，用户已拍板 |
| GAIA Level 3 task | 单 task 3-10min，超 Daily Bench 1h 约束 |
| AppWorld / OS-World / WebArena | OctoAgent 没有 GUI/browser Agent 能力 |
| 横向对比 Hermes / Swarm / CrewAI | OctoBench 仅 OctoAgent 纵向版本对比 |
| 改动 OctoAgent production 代码 | benchmark 是纯观测工具，零侵入硬约束 |
| benchmark 结果自动触发 CI/CD 阻断 | M6 范围；F103d 只建立 baseline + 手动对比 |
| 多用户 / 多 Agent 并发压力测试 | Blueprint §0 锁单用户深度 |

---

## 8. 复杂度评估（供 GATE_DESIGN 审查）

- **组件总数**：4 个新增组件（Runner / Scorer / Reporter / SQLite persistence）+ 2 个 adapter（τ-bench / GAIA）+ 1 个 CLI 命令模块 = **7 个新增组件**
- **接口数量**：1 个 CLI 命令（`octo bench daily`）+ 3 个 scorer 接口（Tier 1/2/3 评分）+ 2 个 adapter 接口 + 1 个 reporter 接口 + 1 个 resume API = **8 个接口**
- **依赖新引入数**：τ-bench（Sierra-Research repo，pip 可安装）+ HuggingFace datasets（已广泛使用）= **2 个新外部依赖**
- **跨模块耦合**：benchmark 模块通过 OctoHarness DI 钩子调用 production 路径（只读，不修改）；OctoHarness 本身不变；`octo bench` CLI 入口需要在 CLI 注册表中新增 1 个命令 = **轻微跨模块，不修改现有接口**
- **复杂度信号**：asyncio 并发控制（Semaphore + gradual ramp）✓；SQLite 持久化 + resume 断点续跑（状态管理）✓
- **总体复杂度**：**HIGH**（组件 7 > 5；接口 8 = 上限；2 个复杂度信号）

**GATE_DESIGN 建议**：总体复杂度 HIGH，建议在 pre-impl Codex review 阶段重点审查 Runner 并发控制设计 + SQLite schema + Phase 拆分顺序是否合理。Phase D 是实施风险最高的 Phase，建议在 Phase D 前额外做一次 architecture review。
