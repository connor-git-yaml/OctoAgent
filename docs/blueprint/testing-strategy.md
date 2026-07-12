# §13 测试策略（Testing Strategy）

> 本文件是 [blueprint.md](../blueprint.md) §13 的完整内容。

---

## 13. 测试策略（Testing Strategy）

> 测试策略按层级递进：基础设施 → 单元 → LLM 交互 → 集成 → 编排 → 安全 → 可观测 → 回放 → 韧性。
> 每层都需与 Constitution（C1-C8）和成功判据（S1-S6）对齐，见 §13.10 覆盖矩阵。

### 13.1 测试基础设施（Test Infrastructure）

> 本节原为设计愿景（写于 M0 期，部分从未落地且术语过时）。M9（质量保证体系）
> 起逐项落地/校正为实况，落地状态标注如下。

- **框架**：pytest + pytest-asyncio（`asyncio_mode = "auto"`）✅ 已落地
- **全局 LLM 安全锁** ✅ **F137 已落地**（原愿景 `ALLOW_MODEL_REQUESTS` 的实际形态）：
  - `octoagent.provider.model_request_gate`——env `OCTOAGENT_ALLOW_MODEL_REQUESTS`
    缺省 allow（生产零感知）；测试布线（provider 包 pytest11 entry-point 插件
    `octoagent_model_request_gate` + `octoagent/conftest.py` 冗余布线）默认置 deny。
  - 闸点在 `ProviderClient.call()` / `embed()` 入口第一行（早于 auth resolver 的
    preemptive refresh 网络副作用）；漏网真调用抛 `ModelRequestsNotAllowedError`
    （`RuntimeError` 子类），FallbackManager / llm_service / SkillRunner /
    memory bridge 各 swallow 站点对其先行 re-raise——**漏网必炸、合法 Echo
    降级不误伤**（异常类型区分，照 401/403 skip-fallback 先例）。
  - opt-in：e2e_full marker（e2e_live conftest 自动开闸）/ `allow_model_requests()`
    context / 显式 env `=1`（布线尊重显式 env）。
- **确定性 LLM 脚本件**（原愿景 TestModel/FunctionModel 的等价物）✅ **F138 已落地
  FunctionModel 半边**：
  - `octoagent.skills.testing.ScriptedModelClient`（FunctionModel 等价）：按队列返回预置
    `SkillOutputEnvelope`（含 tool_calls），经 `OctoHarness(model_client=...)` DI 驱动**真**
    决策环（SkillRunner → tool_broker → 回写），零真 LLM / 零宿主 OAuth（marker `e2e_scripted`）
  - TestModel 等价件（`SchemaTestAdapter`，按工具 JSON schema 自动填参扫 63 工具广度）：
    **deferred**（F138 Phase 2，见 `.specify/features/138-scripted-llm-harness/spec.md` §2.2）
- **L1 UI E2E** ✅ **F140 已落地**（Playwright 薄输入 + 外部断言，仿 cc-haha
  desktop-smoke）：`octoagent/frontend/e2e/` 两场景（chat→脚本决策环→真工具写盘
  →SSE 渲染 / bearer FrontDoorGate token 流程 + SSE query 鉴权），UI 仅输入通道，
  断言走 REST 事件链 + 文件系统 + storage 取值；`create_app(harness_factory=...)`
  DI 缝起 hermetic 真 gateway（gate=deny + 空凭证 + resolve bomb 零真 LLM）；
  data-testid 契约由 vitest 机械校验；CI `l1-playwright` job（零 secret）。
  详见 `docs/codebase-architecture/e2e-testing.md` §9b。
- **事件断言便利件** ✅ **F142 已落地 dirty-equals 半边**：dev-dep `dirty-equals`
  + 3 处范式样例（`test_us4_llm_echo.py` STATE_TRANSITION full-shape /
  `test_control_plane_api.py` action data 契约 / `packages/policy/tests/test_models.py`
  `IsNow(delta)` 治时间窗断言），样例注释标「F142 范式样例」供后续增量套用；
  inline-snapshot **defer**（需 ruff 写回流程 + xdist 惰性 stub + fix/review
  约定三件配套，F141 lane 稳定后独立评估）。
- **确定性护栏 family（F142）** ✅：①第三方库语义钉住 `octoagent/tests/lib_semantics/`
  （anyio/httpx 真本地 TLS server 钉「流中断异常 ∈ 瞬态重试 family」+ bench 事故
  空 message ReadError 签名；APScheduler 真 CronTrigger 钉 Monday=0；piper
  importorskip 真库签名；aiosqlite 已真库覆盖显式略过）；②prompt token 预算护栏
  `apps/gateway/tests/e2e_live/test_prompt_budget_guard.py`（system 面硬 cap 10300 +
  工具 schema 面 13000 + 关键短语在场 + 退役内容负向扫描，cap 实测校准记录见文件
  docstring）；③provider wire 边界用例族 `test_provider_client_wire_boundaries.py`
  （malformed JSON ×3 transport + 真 httpx LineDecoder 粘包/半包穿透 + U+2028
  切行静默丢 delta 行为钉住 + 2MB 行现状钉住；行缓冲无上限评估归档=不动生产）；
  ④xdist_group 18 文件分组 + CI backend job 翻 `-n auto --dist=loadgroup`
  （本地全量串行 378s → ~26s）；F137 CI-skip 4 欠账处置=2 治根因移除 + 2 永久豁免。
- **VCR 录制回放** 📋 规划 **F139**（已收窄：仅 provider transport 层 wire 真样本
  回归，不承担 agent-loop 用例降层；LiteLLM 已于 F081 退役，录制对象为 provider
  直连三 transport）。
- **CI 门禁** ✅ **F137 已落地**：`.github/workflows/feature-007-integration.yml`
  （workflow 名 `ci`）跑确定性层（全 testpaths 排除 e2e_live，gate=deny 构造性
  零真调用，`-n auto --dist=loadgroup` 并行【F142 翻转，时序敏感文件 xdist_group 分组钉同 worker】+ junit artifact）+ 前端 job（complexity 阻断 + vitest 阻断，
  存量红 6 文件以 `--exclude` 记欠账归 F143/独立 fix task）；pre-commit hook 挂
  前端 complexity 检查（`SKIP_FRONTEND_CHECK=1` bypass）。
- **三模式 lane 门禁** ✅ **F141 已落地**（操作契约与判定表的单一事实源 =
  `octoagent/tests/AGENTS.md`，本节只记策略骨架不复制表格）：
  - `repo-scripts/lane.py`（pr / baseline / release）——pr 的 canonical 执行点
    仍是 pre-commit hook（F141 起 pytest 面扩为 `-m "e2e_smoke or e2e_scripted"`
    + change-policy staged 路由：纯 docs fastpath 跳 e2e/前端、gate 机器资产
    staged 附跑校验器、生产 src 无伴随测试 WARNING）；baseline = 合 master 前
    本地全量编排；**release = 真机部署前强制 live**——`-m real_llm` live lane
    （skip 即 FAIL：exit 0 且 passed≥1 且 unexpected_skip=0）+ `octo attest
    service→remote` 探针（解析 --json status 字段；service not_enabled 恒
    FAIL，remote not_enabled 默认 FAIL、`--allow-not-enabled` 降 WARN）+
    attestation 清单签署核对（`--require-signed`）；`SKIP_E2E` 在 release
    无效、`--skip` 拒 live/attestation lanes。
  - 新 marker **`real_llm`**：真发起 LLM 调用的**事实**子集（现 2 个文件）；
    `e2e_full` 保持**意图**信号（gate 开闸键）——lane 切分不复用 e2e_full，
    防 7 个确定性 e2e_full 域文件的 PASS 掩护 live lane 假绿。
  - **flaky quarantine manifest**：`octoagent/tests/quarantine.json`（六字段）
    + `check-quarantine.py`（过期即门禁 FAIL，CI/lane 全模式）+ 根 conftest
    定向 `flaky(reruns=1)`。blanket rerun 全部退役：CI `--reruns 1` 过渡桥已删
    （F137 预留 F141 回收）；e2e_live conftest blanket 收窄 e2e_full-only
    （live 变异性政策）；F142 两个绝对时长性能断言保 `skipif` 不入册（永久
    豁免无 exit criteria，入册会把「过期强制复查」污染成例行盖章）。
  - **changed-lines coverage 门**（CI backend job）：裸 `--cov`（source =
    pyproject `[tool.coverage.run]` 9 个 src 目录）产 lcov ∩ git diff 新增行，
    ≥90% 否则 FAIL（`check-changed-lines-coverage.py` 机械计算；存量不背债；
    escape hatch = HEAD commit message `[cov-exempt]` 大声记录）；scope 底线/
    棘轮两重门显式 defer。门禁脚本自身在 `octoagent/tests/gate/` 有单测
    （cc-haha 教义：门禁脚本必须被测试）。
- **测试目录结构**（实况；原愿景的 replay/evals 独立目录从未建立）：测试分布在
  各包 `packages/*/tests` + `apps/gateway/tests`（含 `e2e_live/` 真 LLM 套件）+
  顶层 `tests/integration`（Echo 全栈），由 `octoagent/pyproject.toml` testpaths
  统一编排；评估能力由 OctoBench（仓库根 `benchmarks/`）承担。

#### 13.1.1 测试并发优化（Feature 083）

> 详见 `docs/codebase-architecture/testing-concurrency.md`。

Feature 083 之前测试套件有两个开发体验痛点：

| # | 症状 | 实测 |
|---|------|------|
| 1 | **thread shutdown hang**：pytest 跑完后进程不退出 | macOS sample 显示 100% 时间在 `Py_FinalizeEx → wait_for_thread_shutdown`；实测 30+ 分钟才被 kill |
| 2 | **xdist 并发未启用**：装了 `pytest-xdist 3.8.0` 但配置缺失 → 默认串行 ~93s 全量回归 |

Feature 083 采用实用主义策略：

| 方面 | 决策 |
|------|------|
| thread shutdown hang | ✅ 修复（`pytest_sessionfinish` hook 显式 `loop.shutdown_default_executor()`）—— 进程退出 30+ min → ~20s |
| `os.environ` fixture 污染 | ✅ 改 `monkeypatch.setenv` |
| Race #1（`ExecutionConsoleService.attach_input` 读窗口）| ✅ P5 治本（`_read_task_with_waiting_input_retry`） |
| Race #2（runner restart + recovery 路径）| ⏭️ 移交 Feature 084（治本超 F083 scope） |
| 单 sleep + assert 长尾（~72 处 `await asyncio.sleep(N) + assert` 模式）| ⏭️ 移交 Feature 084 |
| `test_attach_input_after_restart` 测试 polling 加严 | ✅ P6 双状态联合等待 + 1s → 5s 窗口 |
| **xdist 默认启用** | ❌ 撤销——风险大于收益（task_runner 状态机测试 ~20% 失败率） |
| **xdist opt-in** | ✅ 文档化：本地开发子包并发 `pytest -n auto packages/core/tests/`；CI 全量回归仍用默认串行 |

修复后默认行为：pytest 报告 ~103s / 进程实际退出 ~104s / 5 次连续 100% 稳定。`-n auto` 加速 5.5x（~17s 报告 / ~20s 退出），代价是高 CPU 负载下 task_runner 状态机测试偶发 race ~20%（治本超 scope）。

### 13.2 单元测试（Unit）

- **domain models**：
  - Task 状态机转移覆盖：所有合法转移路径 + 非法转移拒绝
  - Pydantic validator / serializer 正确性（NormalizedMessage / Event / Artifact）
  - Artifact parts 多部分结构校验（对齐 A2A Part 规范）
- **event store 事务一致性**：写事件 + 更新 projection 必须在同一事务内（原子性）
- **tool schema 反射一致性**（contract tests）：schema 生成与函数签名 + 类型注解 + docstring 一致（对齐 C3）
- **policy engine 决策矩阵**：allow / ask / deny 全路径覆盖；Policy Profile 优先级测试
- **A2AStateMapper 映射**：内部状态 ↔ A2A TaskState 双向映射幂等性；终态一一对应
- **成本计算逻辑**：按 model alias 聚合 tokens/cost 正确性（对齐 S6）
- **memory 模型**：SoR current 唯一性约束；Fragments append-only 不可变性（对齐 S5）

### 13.3 LLM 交互测试（LLM Interaction）

> Agent 系统最核心也最难测的部分。借鉴 Pydantic AI 的 TestModel / FunctionModel 模式。

- **FunctionModel 等价件（✅ F138 已落地）**：`octoagent.skills.testing.ScriptedModelClient`
  按序脚本精确控制 LLM 决策输出（含多步 tool_call 链），经 `OctoHarness(model_client=...)`
  DI 进真决策环——keystone 套件
  `apps/gateway/tests/e2e_live/test_e2e_scripted_decision_loop.py` 验证
  "脚本 LLM 决定调工具 → tool_broker 真派发 → USER.md/事件真回写" 全链确定性（L3，
  此前决策环前半段 L3 零覆盖）
- **TestModel 等价件（deferred，F138 Phase 2）**：`SchemaTestAdapter` 按注册工具
  schema 自动生成合法参数、扫全工具广度——见 F138 spec §2.2 deferred 范围
- **Provider 抽象层路由**：ProviderRouter alias 解析 + 三 transport（LiteLLM 已于
  F081 退役）；wire 层真样本回归归 F139
- **非确定性输出策略**：
  - 验证输出结构 / 必填字段 / 类型，而非精确字符串
  - 使用 dirty-equals（`IsStr` / `IsNow(delta)` / `IsPartialDict`）做模糊匹配（✅ F142 已引入，范式样例 ×3 处）
  - 关键路径使用 ScriptedModelClient 保证确定性
- **预留：LLM-as-Judge 评估**：pydantic-evals 或自定义 judge 函数，评判 Agent 输出质量（后续迭代）

### 13.4 集成测试（Integration）

- **task 全流程**：从 `ingest_message` → task 创建 → Worker 派发 → stream events → 终态
- **approval flow**：ask → approve → resume；ask → reject → REJECTED 终态（对齐 C4 / C7）
- **worker 执行**：JobRunner docker backend 启动 / 执行 / 产物回收 / 超时处理
- **memory arbitration**：WriteProposal → 冲突检测 → commit；SoR current/superseded 转换一致性（对齐 S5）
- **Skill Pipeline checkpoint**：
  - 正常路径：Pipeline 从起点到终点，验证每个节点 checkpoint 写入
  - 恢复路径：从任意中间 checkpoint 恢复，不重跑已完成节点
  - 中断路径：WAITING_APPROVAL → 审批后从中断点继续
- **多渠道消息路由**：同一 thread_id 的消息落到同一 scope_id；不同渠道的消息隔离（对齐 S4）
- **SSE 事件流**：`/stream/task/{task_id}` 端到端验证事件顺序与完整性

### 13.5 编排与循环测试（Orchestration & Loop）

> Orchestrator 和 Workers 都是 Free Loop，需要专门验证循环控制与异常恢复。

- **Orchestrator 路由决策**：给定 NormalizedMessage，验证目标分类、Worker 选择、risk_level 评估
- **Worker Free Loop 终止**：验证正常完成、budget 耗尽、deadline 到期、用户取消等终止条件
- **死循环检测**：
  - 输出相似度阈值（连续 N 轮输出 similarity > 0.85 → 强制中断）
  - 最大迭代次数限制（硬上限）
  - 测试验证检测机制能正确触发并生成 ERROR 事件
- **Worker 崩溃恢复**：模拟 Worker 进程中断，验证从 Event 历史恢复状态、从最后 checkpoint 续跑（对齐 C1 / S1）
- **多 Worker 协作**：Orchestrator 派发子任务 → Workers 并行执行 → 事件回传 → Orchestrator 汇总

### 13.6 安全与策略测试（Security & Policy）

- **Docker 沙箱隔离**：验证工具执行在容器内，无法访问宿主文件系统 / 网络（除白名单）
- **secrets 不泄漏**：验证 Vault 中的 secrets 不出现在 LLM 上下文、Event payload、日志输出中（对齐 C5）
- **Two-Phase 门禁端到端**：不可逆操作必须经历 Plan → Gate → Execute；跳过 Gate 的请求被拒绝（对齐 C4）
- **工具权限分级**：
  - `read-only` 工具：默认 allow，无需审批
  - `reversible` 工具：默认 allow，可配置为 ask
  - `irreversible` 工具：默认 ask，必须审批后执行
- **未签名插件拒绝**：未通过 manifest 校验的插件默认禁用

### 13.7 可观测性与成本测试（Observability & Cost）

- **事件完整性**：每个 task 的关键步骤必须产生对应 event（对齐 C2 / C8）：
  - `TASK_CREATED` / `MODEL_CALL_STARTED` / `MODEL_CALL_COMPLETED` / `TOOL_CALL` / `TOOL_RESULT` / `STATE_TRANSITION` / `ARTIFACT_CREATED`
  - 缺失任何关键 event 类型 → 测试失败
- **成本追踪正确性**：验证每个 task 的 tokens / cost 聚合与实际 `MODEL_CALL_COMPLETED` 事件 payload 一致（对齐 S6）
- **Logfire span 完整性**：关键操作（LLM 调用、工具执行、状态转移）必须生成 OTel span
- **structlog 输出**：验证日志包含 task_id / trace_id / span_id 等结构化字段，便于关联查询

### 13.8 回放测试（Replay / Golden Tests）

> 利用事件溯源的天然优势，验证系统确定性与可重现性（对齐 S2）。

- **golden test 场景清单**（10 个典型任务事件流）：
  1. 简单问答：单轮 USER_MESSAGE → MODEL_CALL_STARTED → MODEL_CALL_COMPLETED → 回复
  2. 工具调用：MODEL_CALL_STARTED → MODEL_CALL_COMPLETED → TOOL_CALL → TOOL_RESULT → 回复
  3. 多轮对话：多次 USER_MESSAGE 交替
  4. 审批通过：APPROVAL_REQUESTED → APPROVED → 继续执行
  5. 审批拒绝：APPROVAL_REQUESTED → REJECTED → REJECTED 终态
  6. 长任务 + checkpoint：多节点 Pipeline，中间有 CHECKPOINT_SAVED
  7. 任务取消：用户主动 CANCELLED
  8. 工具失败 + 重试：TOOL_CALL → ERROR → 重试 → 成功
  9. 子任务派发：Orchestrator → Worker 子任务 → 回传
  10. 崩溃恢复：中断 → 从 checkpoint 恢复 → 完成
- **一致性断言**：replay 后的 tasks projection、artifacts 列表、终态必须与原始执行一致
- **event schema 兼容**：不同 `schema_version` 的事件 replay 时正确解析
- **发布门禁**：凡涉及 Event schema 或 projection 逻辑变更，必须通过历史事件回放套件（不通过禁止合并）

### 13.9 降级与恢复测试（Resilience）

> 验证 Constitution C1（Durability First）和 C6（Degrade Gracefully）。

- **进程崩溃恢复**：
  - 模拟 kernel/worker 进程崩溃后重启
  - 所有未完成任务在 UI 中可见，且能 resume 或 cancel（对齐 S1）
- **Provider 不可用**：
  - 模拟 LLM Provider 返回 429 / 500 / 超时
  - 验证 FallbackManager 降级机制触发（LiteLLM 已于 F081 退役），切换到备选路径
  - 验证事件记录失败原因（对齐 C6）
- **插件崩溃隔离**：
  - 单个插件 / 工具抛异常不导致整体系统不可用
  - 自动 disable 故障插件并记录 incident（对齐 C6）
- **SQLite WAL 并发一致性**：
  - 模拟两个 task 同时写事件，验证 projection 最终一致
  - 模拟数据库崩溃，验证 WAL 恢复后 projection 可从 events 重建
- **网络中断**：Telegram / Web 渠道断连后重连，消息不丢失、不重复

### 13.10 测试覆盖对齐矩阵

| Constitution / 成功判据 | 对应测试 |
|------------------------|---------|
| C1 Durability First | §13.8 回放测试、§13.9 崩溃恢复 |
| C2 Everything is Event | §13.7 事件完整性 |
| C3 Tools are Contracts | §13.2 contract tests |
| C4 Side-effect Two-Phase | §13.4 approval flow、§13.6 Two-Phase 门禁 |
| C5 Least Privilege | §13.6 secrets 不泄漏 |
| C6 Degrade Gracefully | §13.9 Provider / 插件降级 |
| C7 User-in-Control | §13.4 approval flow、§13.5 用户取消 |
| C8 Observability is Feature | §13.7 事件完整性、Logfire span |
| S1 重启后可恢复 | §13.9 进程崩溃恢复 |
| S2 任务可完整回放 | §13.8 golden tests |
| S3 高风险需审批 | §13.4 approval flow、§13.6 权限分级 |
| S4 多渠道一致性 | §13.4 多渠道消息路由 |
| S5 记忆一致性 | §13.2 memory 模型、§13.4 memory arbitration |
| S6 成本可见 | §13.2 成本计算、§13.7 成本追踪 |

---

### 13.11 E2E Live Test Suite（Feature 087）

> 详见 `docs/codebase-architecture/e2e-testing.md`。

F087 把旧 `test_acceptance_scenarios.py` 5 域循环替换为 **13 能力域 e2e_live 套件**——基于真实 LLM 路径或直调主路径，hermetic 隔离 + pre-commit hook 默认跑 smoke 子集。

**OctoHarness 抽离**（`octoagent/apps/gateway/src/octoagent/gateway/harness/octo_harness.py`）：

| DI 钩子 | 用途 |
|---------|------|
| `credential_store` | 替换 ProviderRouter 凭据来源（fake_store / real_codex_credential_store）|
| `secret_store` | 注入测试 secret |
| `transport_factory` | 注入 stub / 真实 transport 工厂 |
| `clock` | 注入可控时钟（routine cron 等时间相关测试）|

内置约束：**120s ProviderRouter timeout** + **30s SIGALRM 单测 watchdog**。

**13 能力域清单**（注册表权威源：`tests/e2e_live/helpers/domain_runner.py::DOMAIN_REGISTRY` + `gateway/cli/e2e_command.py::_DOMAIN_REGISTRY` 双源同步）：

| 域 # | 名称 | marker |
|------|------|--------|
| 1 | 工具调用基础 | e2e_smoke |
| 2 | USER.md 全链路（threat scanner 通过）| e2e_smoke |
| 3 | 冻结快照 + Live State 二分（prefix cache 不爆）| e2e_smoke |
| 4 | Memory observation → promote → audit | e2e_full |
| 5 | 真实 Perplexity MCP install + invoke（**manual gate**：`OCTOAGENT_E2E_PERPLEXITY_API_KEY`）| e2e_full |
| 6 | Skill 调用（强类型 contract）| e2e_full |
| 7 | Graph Pipeline（DAG checkpoint）| e2e_full |
| 8 | `delegate_task`（worker 派发）| e2e_full |
| 9 | Sub-agent `max_depth=2` 边界 | e2e_full |
| 10 | A2A-Lite 双向通信（schema 集成）| e2e_full |
| 11 | ThreatScanner block（恶意 prompt + USER.md 不变）| e2e_smoke |
| 12 | ApprovalGate session allowlist + SSE 审批 | e2e_smoke |
| 13 | Routine cron + webhook 触发 | e2e_full |

**关键设计权衡**（GATE_P3_DEVIATION）：

- **smoke 5 域**（#1 #2 #3 #11 #12）：保留集成层（OctoHarness DI + stub transport），断言调用骨架（USER.md 写入 / 事件 emit / threat scanner block 路径）
- **full 8 域中 4 个**（#4 #5 #8 #9 #10）：P4 fixup 下沉为**直调主路径**（绕开 LLM agent loop），直接构造 `MemoryService` / `MCPInstaller` / `DelegationManager`
- **真实 LLM e2e**：保留在 `test_e2e_smoke_real_llm.py` 基线对照，需 `.env.e2e` 凭证才跑，不在 pre-commit 默认路径

理由：LLM 决策不稳定性（同样 prompt 不同步 token sampling）让 5x 循环 0 regression DoD 不可达；Codex P4 review 接受 GATE_P3_DEVIATION 决策。代价是 13 域不全是端到端"真实跑"；收益是 5x 循环 0 regression（P5 实测 4s/iter），pre-commit hook 可用、可信、不阻断开发节奏。

**Hermetic 隔离**：双 autouse fixture 重置 5 类凭证 env（OpenAI / Anthropic / OpenRouter / SiliconFlow / Codex OAuth）+ 4 个 OCTOAGENT_* 路径 env + 5 项 module 单例（清单见 `tests/e2e_live/helpers/MODULE_SINGLETONS.md`）。

**`octo e2e` CLI**（`octoagent/apps/gateway/src/octoagent/gateway/cli/e2e_command.py`）：

```bash
octo e2e smoke              # 跑 smoke 5 域
octo e2e full               # 跑 full 8 域
octo e2e 7                  # 跑域 #7 单测
octo e2e --list             # 列 13 域
octo e2e smoke --loop=5     # smoke 5x 循环
```

退出码：0 = 全 PASS（SKIP 不算 FAIL，但写入 `~/.octoagent/logs/e2e/quota-skip-*.log`）；1 = 至少 1 FAIL；2 = 参数错误。

**pre-commit hook**：

```bash
make install-hooks           # 一次性安装（worktree-aware）
git commit                   # 自动跑 pytest -m e2e_smoke
SKIP_E2E=1 git commit ...    # 紧急 bypass
```

180s **portable watchdog**：python3 SIGTERM→SIGKILL 升级（不依赖 macOS 上需 `brew install coreutils` 的 `timeout`）。

**SC-7 运行时不变量**：跑前后比对 sha256，全部一致：
- `~/.octoagent/behavior/system/USER.md`
- `~/.octoagent/auth-profiles.json`
- `~/.octoagent/mcp-servers/`（递归）

P5 实测 F087 e2e_full 跑前后 sha256 完全一致（hermetic 隔离生效）。

**已知工程债**：
- `memory_candidates audit task` 字段缺失（F084 P5 spawn task 待修；当前 e2e_full 域 #4 直调路径已绕开）
- F083 race `test_sc3_projection`（aiosqlite event loop 关闭顺序导致全量回归偶发 1 例 FAIL，重跑必过；超 F087 scope）
- OAuth profile 失效自然 SKIP（无真实 token 时 e2e_full 真实 LLM 测自然 SKIP，不阻断 commit）

### 13.12 MCP E2E Testing（Feature 089）

> 详见 `.specify/features/089-mcp-e2e-testing/spec.md`（v2 版本）。

Feature 089 补 F087 留下的 **MCP 集成路径 CI 盲区**——F087 把 13 域纳入 e2e_live 后，真实 MCP register/spawn/execute 链路依然依赖手动 gate（`OCTOAGENT_E2E_PERPLEXITY_API_KEY`，CI 永远 SKIP）。

**v2 关键决策**（v1 spec 被 Codex adversarial review 拒绝后重做）：
- 走 **mcp_registry config-driven** 路径，**不测 npm/pip install 链路**（避免 npm 网络抖动 / install 副作用）
- 0 生产代码改动（纯测试新增）

**baseline def6638 实施状态**（**部分落地**，与 spec 5-case 完整套件有偏差）：

| 文件 | 状态 | 用途 |
|------|------|------|
| `apps/gateway/tests/e2e_live/_mcp_stub_server.py` | ✅ 落地（stub helper）| 本地 stdio MCP server（纯 stdlib）|
| `apps/gateway/tests/e2e_live/test_e2e_mcp_local_stub.py` | ✅ 落地（1 test：`test_mcp_unregister_kills_subprocess`）| stub subprocess 启停 + leak detection |
| `apps/gateway/tests/e2e_live/test_subprocess_leak_detection.py` | ✅ 落地 | 进程残留检测（autouse 验证 leak） |
| `apps/gateway/tests/e2e_live/test_e2e_mcp_broker.py` | ✅ 落地 | MCP broker 集成 |
| `apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py` 域 #5 SKIP gate fallback | 🚧 部分（v2 spec 列入"应做"，baseline 实施程度待 F089 后续推进确认）|

剩余 4 个 e2e_smoke case（config-register / execute / error / delete）+ hermetic env 扩展（`OCTOAGENT_MCP_SERVERS_PATH` / `OCTOAGENT_E2E_USE_HOST_KEY`）+ docs/e2e-testing.md MCP 章节追加为 F089 v2 spec **未完结的剩余范围**（建议 M6 期间完成）。

**为什么不测 npm install**：测试要 hermetic + < 10s + CI 跑得起，但 npm install 网络抖动 / vendor 包变化 / install 副作用都让 CI 不稳定。stub-based 测试已能覆盖 OctoAgent client 实现层（register / spawn / discover / execute / unregister 主链路），是否兼容真实 vendor server 留给 vendor manual gate（域 #5）+ 用户报障驱动。
