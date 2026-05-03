# Feature Spec: Agent E2E Live Test Suite

**Feature ID**: 087
**Feature Slug**: agent-e2e-live-test-suite
**分支**: 087-agent-e2e-live-test-suite
**生成日期**: 2026-04-30
**调研基础**: `research/product-research.md`（268 行）+ `research/tech-research.md`（400 行）
**模式**: feature（完整调研已完成）

---

## 1. 背景与问题

OctoAgent 在 F084 / F086 完成 Harness 与 Context 重构后，质量基础设施仍存在两个真实缺口：

1. **e2e 测试退化**：现有 `apps/gateway/tests/e2e/test_acceptance_scenarios.py` 仅覆盖 5 个场景循环验证，未覆盖 13 能力域全貌；F082→F084 重构期间多次出现"单测全绿但实际 bootstrap 路径在用户机上是坏的"。
2. **Harness 缺回归保护**：F084 抽离的 ToolRegistry / SnapshotStore / ApprovalGate / DelegationManager / ThreatScanner 没有真实 LLM live call 级别的回归测试，prompt / tool schema / context 拼接漂移只能靠手动 happy path 抽查。

Codex Adversarial Review 能发现设计漏洞但**发现不了运行时漂移**。F087 是单测和 Codex Review 之外的**第三层防御**，定位为：每次 commit 前 30s-3min 内得到"13 能力域是否还活着"的明确信号。

## 2. 范围

### 2.1 13 能力域清单 + smoke/full 划分

| # | 能力域 | 套件 | 关键工具 / 服务 |
|---|------|-----|----------------|
| 1 | 工具调用基础 | **smoke** | `memory.write` |
| 2 | USER.md 全链路 | **smoke** | `user_profile.update` |
| 3 | Context 注入 / 冻结快照 | **smoke** | SnapshotStore |
| 11 | ThreatScanner block | **smoke** | ThreatScanner |
| 12 | ApprovalGate（SSE）| **smoke** | ApprovalManager |
| 4 | Memory observation→promote | full | ObservationRoutine + Candidates API |
| 5 | MCP 调用（真实 Perplexity，manual gate）| full | npm install `openrouter-mcp` + 真启 server + `mcp.openrouter_mcp.ask_model` 真打 `perplexity/sonar-pro-search`；需 `OCTOAGENT_E2E_PERPLEXITY_API_KEY` env 否则 SKIP |
| 6 | Skill 调用 | full | SkillRunner |
| 7 | Graph Pipeline | full | `graph_pipeline.start/status` |
| 8 | delegate_task / Worker 派发 | full | DelegationPlaneService |
| 9 | Sub-agent max_depth=2 拒绝 | full | DelegationManager |
| 10 | A2A 通信 | full | A2AConversation + DispatchEnvelope |
| 13 | Routine cron / webhook | full | APScheduler + Routine |

**smoke = 5 个**（用户每次对话都会触发的核心 happy path + 两个 safety net）。**full = 8 个**（复合 / 边缘 / 需等待场景）。

### 2.2 范围内

- `OctoHarness` 抽离（`gateway/main.py:289-892` lifespan ~600 行 → ~10 行）+ DI 钩子
- `McpInstallerService` 加 `mcp_servers_dir` 注入参数（生产代码改动）
- pytest marker 体系（`e2e_smoke` / `e2e_full` / `e2e_live`）+ `pytest-rerunfailures` 单次重试
- native shell pre-commit hook（`.githooks/pre-commit`）+ `make install-hooks`
- `octo e2e {smoke|full|<domain_id>|--list}` CLI 命令
- Hermes 模式双 autouse fixture（`_hermetic_environment` + `_reset_module_state`）
- 13 能力域 × 真实 LLM live case，每场景 ≥ 2 断言点

### 2.3 范围外

- CI 不跑（仅本地 pre-commit + 手动）
- Telegram channel e2e（已知排除项）
- 多模型对比 / 性能基准 / 失败 case 自动归档（远期）
- 引入 pre-commit framework（用 native shell 即可）
- ScriptedAdapter（不做，全用真实 LLM）

## 3. User Stories

### US-1（P1，smoke 锚定）— commit 前 3 分钟内得到核心能力域信号

作为 Connor，每次 `git commit` 时本地自动跑 5 个核心能力域 smoke 测试，**< 3min** 内得到 PASS / FAIL 结果，FAIL 时给出域名 + 期望 + 实际三行核心信息。

- **优先级理由**：F087 的核心价值——每次 commit 强制保险；不实现这条则整个 feature 无意义
- **独立测试**：在 `octoagent/` 下 `git commit -m "test"` 触发 hook，观察输出
- **验收**：
  1. Given pre-commit hook 已安装；When commit 一次；Then 90-120s 内输出 5 个域结果
  2. Given 任一 smoke 域 FAIL；When commit；Then 退出码非 0 且 commit 被阻塞
  3. Given 全部 PASS；When commit；Then 退出码 0 且 commit 进入

### US-2（P1）— full 套件手动覆盖 13 能力域

作为 Connor，重大 refactor 后或 push 前，手动 `octo e2e full` 跑全 13 域，< 10min 完成。

- **优先级理由**：smoke 仅 5 域，剩 8 域必须有手动入口；refactor 后人工 sanity check 的唯一手段
- **独立测试**：直接命令行触发，不依赖 hook
- **验收**：
  1. Given F084/F086 级 refactor 完成；When 跑 `octo e2e full`；Then ≤ 10min 输出 13 域结果
  2. Given Perplexity 远端故障；When `octo e2e full` 跑到 #5；Then 1 次 retry 后 SKIP（不 FAIL）

### US-3（P1）— 真实 LLM + 真实 Perplexity，零边际成本

作为 Connor，e2e 走真实 GPT-5.5 think-low（Codex OAuth 订阅内）+ 真实 Perplexity MCP，不允许 cassette / mock provider。

- **优先级理由**：Live Call 派路线的根本前提；mock 化等于 F087 完全失效
- **独立测试**：grep events 表 model 字段 + 检查 MCP 返回 URL 真实性
- **验收**：
  1. events 表内 LLM 调用 model 字段 ≠ "echo"
  2. MCP 调用返回真实 http(s) 链接（域 #5 验证）

### US-4（P2）— SKIP_E2E 紧急 bypass + 留痕

作为 Connor，紧急 commit 时 `SKIP_E2E=1 git commit ...` 显式跳过，但 commit message 不被自动改。

- **优先级理由**：紧急场景 escape hatch；不引入会拖慢 release
- **独立测试**：直接 env 注入 commit
- **验收**：env 注入后 hook 直接 exit 0；后续 push 前可手动补跑

### US-5（P2）— 单跑某能力域 debug

作为 Connor，定位单个域失败时 `octo e2e 11`（按 # 编号）单跑该域，含详细日志。

- **优先级理由**：debug 用户体验；hook 失败后定位刚需
- **独立测试**：CLI 直接触发
- **验收**：仅触发该域 fixture + LLM call，输出含完整 events 流

### US-6（P3）— 不污染日常 ~/.octoagent

作为 Connor，e2e 跑完后 `~/.octoagent/USER.md` / `MEMORY.md` / `mcp-servers/` / `auth-profiles.json` 内容均无变更。

- **优先级理由**：用户日常实例不被测试污染（曾被反复强调的硬约束）
- **独立测试**：跑前后 sha256 对比
- **验收**：跑前后 sha256 一致；e2e 全程操作在 `tmp_path` 内

### Edge Cases

- 宿主无 `~/.octoagent/auth-profiles.json` → e2e fixture `pytest.skip` 而非 FAIL
- Codex OAuth quota 耗尽 → 单 LLM call 返回 429；e2e 视为 ENV 问题，输出"quota exceeded"提示而非 FAIL（plan 阶段细化）
- module 单例 reset 漏 → "alone pass / together fail" → FR-27 要求精确清单
- Perplexity 网络抖动 → smoke 不含 #5；full #5 retry 1 次后 SKIP
- pre-commit hook 卡死 > 180s → 总 timeout 强杀；输出"timeout"
- 用户在非 `octoagent/` 目录 commit → hook `cd octoagent` 兜底；找不到时跳过

## 4. 功能需求 FR（含 5 个 open question 决议）

### 4.1 OctoHarness 抽离

- **FR-1** [必须][MUST] 抽离 `OctoHarness` 类到 `apps/gateway/src/octoagent/gateway/harness/octo_harness.py`，搬运 lifespan 业务逻辑；lifespan 函数最终 ≤ 20 行
- **FR-2** [必须][MUST] OctoHarness 暴露 4 个 DI 钩子：`credential_store: CredentialStore | None` / `llm_adapter: MessageAdapter | None` / `mcp_servers_dir: Path | None` / `data_dir: Path | None`
- **FR-3** [必须][MUST] 生产路径（main.py lifespan）DI 钩子全部传 None，行为与 F086 当前实现 byte-for-byte 一致
- **FR-4** [可选][SHOULD] OctoHarness 各 `_bootstrap_*` 方法返回组装好的子系统对象，最后 `commit_to_app(app)` 一次性挂载到 `app.state`

### 4.2 e2e 框架基础设施

- **FR-5** [必须][MUST] 注册 pytest markers：`e2e_smoke` / `e2e_full` / `e2e_live`（在 `octoagent/pyproject.toml`）
- **FR-6** [必须][MUST] 加 `pytest-rerunfailures>=14.0` 到 dev deps；e2e 测试加 `@pytest.mark.flaky(reruns=1, reruns_delay=2)`；**单元测试不加**
- **FR-7** [必须][MUST] 实现两条 autouse fixture（仿 Hermes 模式）：
  - `_hermetic_environment`：清凭证 env（OPENAI_API_KEY / SILICONFLOW_API_KEY / OPENROUTER_API_KEY / TELEGRAM_BOT_TOKEN 等）+ 重定向 `OCTOAGENT_DATA_DIR` / `OCTOAGENT_DB_PATH` / `OCTOAGENT_ARTIFACTS_DIR` / `OCTOAGENT_PROJECT_ROOT` 到 `tmp_path`，**不动 HOME**（子进程会炸）
  - `_reset_module_state`：reset OctoAgent module 单例（清单见 FR-26）
- **FR-8** [必须][MUST] 提供 `real_codex_credential_store` fixture：从宿主 `~/.octoagent/auth-profiles.json` **只读**复制到 tmp，构造 `CredentialStore(store_path=tmp)`；宿主不存在时 `pytest.skip`

### 4.3 13 能力域测试（**P3 决策修正：smoke = 集成层 / full = 真 LLM 验证**）

> **背景**：P3 实测后发现"5 smoke × 5x 循环 × 真 LLM call 5-30s/次"与"pre-commit ≤ 180s"物理冲突。用户决策（GATE_P3_DEVIATION）：smoke 套件作为**集成层断言**（直调 handler / OctoHarness 跑通 / 状态机断言），full 套件 13 域**全部真打 LLM 1x**作为真实能力验证。

- **FR-9** [必须][MUST] smoke 套件 5 域（#1 #2 #3 #11 #12）实现**集成层** e2e case：直调 handler / OctoHarness 真启动 / 状态机断言；每场景 ≥ 2 断言点；**不要求**真打 LLM；目标：byte-for-byte 等价 + 5x 循环稳定
- **FR-10** [必须][MUST] full 套件覆盖**全 13 能力域**（5 smoke 域真 LLM 版 + 8 原 full 域）真打 GPT-5.5 think-low 1x；每场景 ≥ 2 断言点；目标：真实 LLM 选工具能力 + 全栈 transport / harness / handler 验证
- **FR-11** [必须][MUST] **断言绝不依赖 LLM 字面输出**——以结构化断言为主：tool_call 序列、`WriteResult.status` / 关联 ID（task_id / memory_id / run_id）、SQLite state diff、events 表内容
- **FR-12** [必须][MUST] 单 LLM call timeout = 120s（e2e fixture 覆盖 `ProviderRouter(timeout_s=120.0)`，**不改生产**）
- **FR-13** [必须][MUST] LLM `max_steps` 上限 = 10
- **FR-14** [可选][SHOULD] 场景 #5（真实 Perplexity）单独放宽到 60s 单 call timeout（远端搜索）

### 4.4 Open Question 决议

#### OQ-1 决议（A2A e2e 测点）— 能力域 #10 含以下子断言

- **FR-15** [必须][MUST] 能力域 #10（A2A）覆盖**数据 schema + 关联键 + 状态机 + 审计**完整性，4 子断言：
  - **DispatchEnvelope 投递**：parent task 的 `events` 表存在 1 行 `SUBAGENT_SPAWNED` 事件（payload 含 `child_task_id` + `target_worker` + `a2a_conversation_id`）
  - **worker B 工具调用 ≥ 1**：`a2a_messages` 表 INBOUND RESULT 消息 `payload.tool_calls` 数组 ≥ 1 条
  - **Conversation 状态完成**：`a2a_conversations.status == 'completed'` + `completed_at` 非空
  - **Req/Resp + parent_task_id 链路完整**：OUTBOUND TASK request 1 行 + INBOUND RESULT response 1 行；child task `parent_task_id == parent.task_id`；message `task_id == child.task_id`；message `payload.metadata.parent_task_id == parent.task_id`
- **FR-15.1** [必须][MUST] **e2e 边界**：F087 域 #10 测试是**A2A 数据 schema 集成测试**（直调 `task_store` / `a2a_store` / `event_store` 主路径函数 + `EventType.SUBAGENT_SPAWNED` 等枚举），**不验证跨 runtime 真触发**。理由：
  - worker B 真跑需 SkillRunner + LLM call（不 deterministic + 触发 quota）
  - `A2AConversationStatus.COMPLETED` 转换由 orchestrator A2A inbound handler 触发，需要双 runtime 完整跑通
  - 单进程 ASGI test app 不支持跨 agent runtime daemon
- **FR-15.2** [可选][MAY] 真跨 runtime e2e（worker A LLM-driven `delegate_task` tool → `task_runner.launch_child_task` → child worker_runtime 完整闭环 → orchestrator `_persist_a2a_terminal_message` 转 COMPLETED）**推迟到 F088+**；F087 范围内由 schema integration 直调主路径覆盖。注：F087 followup 已清理孤悬的 in-process `SubagentExecutor` 死代码（详见 `docs/codebase-architecture/e2e-testing.md` §2.1）

#### OQ-2 决议（mcp_servers_dir 隔离）— **选择 A：生产代码加 DI**

理由：硬编码 `Path.home()` 是 e2e 污染宿主的根因；DI 改动小且长远受益（CLI / Standalone runner 都需要）。

- **FR-16** [必须][MUST] 修改 `apps/gateway/src/octoagent/gateway/services/mcp_installer.py`：`McpInstallerService.__init__` 增加 `mcp_servers_dir: Path | None = None` 参数；None 时回退到 `_DEFAULT_MCP_SERVERS_DIR`
- **FR-17** [必须][MUST] 生产 lifespan / OctoHarness 传 None，行为不变；e2e fixture 显式传 `tmp_path / "mcp-servers"`
- **FR-18** [必须][MUST] McpInstallerService 内**所有**对 `_DEFAULT_MCP_SERVERS_DIR` 的引用都改读 `self._mcp_servers_dir`（无遗漏，否则 e2e 仍污染宿主）

#### OQ-3 决议（Telegram channel e2e）— **不在 13 能力域内**

- **FR-19** [必须][MUST] Telegram channel **不纳入** F087 的 13 能力域；现有 mock 模式继续生效
- **FR-20** [必须][MUST] e2e fixture 不实例化真实 `TelegramService`（`TELEGRAM_BOT_TOKEN` 留空 → service 自动 degrade 路径），避免 e2e 启动时连 Telegram bot
- **FR-21** [可选][MAY] 未来若需 Telegram e2e，单独立项（专用 e2e bot + chat_id）

#### OQ-4 决议（e2e_smoke pre-commit 预算）

- **FR-22** [必须][MUST] e2e_smoke 套件总 timeout = **180s**；P3 实测 ~3-15s（远低于上限，因为 smoke 不真打 LLM）
- **FR-23** [必须][MUST] 单场景 timeout = 30s；网络抖动重试 1 次（`pytest-rerunfailures`）
- **FR-24** [必须][MUST] 超时降级策略：
  - smoke 单场景超时 → FAIL（commit 阻塞）
  - 场景 #5（Perplexity，仅 full）远端故障 → 1 次 retry 后 SKIP（不 FAIL）
  - **full 套件 LLM call 失败**：按 quota 协议（status_code=429 或 error_type=rate_limit）→ SKIP；其他真 bug → FAIL
- **FR-25** [必须][MUST] e2e_smoke 不真打 LLM（FR-9 决策），无 LLM 调用预算约束；e2e_full 单次循环 LLM 调用预算 ≤ 13 域 × 2 call/域 ≈ 26 次（避免 Codex OAuth 限频）

#### OQ-5 决议（module 单例 reset 全清单）

- **FR-26** [必须][MUST] `_reset_module_state` autouse fixture 至少 reset 以下单例（具体实现路径与清理方式留 plan 阶段精确化）：
  - `gateway/harness/tool_registry.py` 的 `_REGISTRY` 全局单例
  - `gateway/harness/snapshot_store.py` 实例缓存（由 OctoHarness lifecycle 管）
  - `gateway/harness/approval_gate.py` 的 `_session_approvals` 字典
  - `gateway/harness/delegation_manager.py` 的 `_active_children` 字典
  - `gateway/harness/threat_scanner.py` 的 pattern compiled cache（如有）
  - Memory Candidates 内存索引（如 `MemoryConsoleService` / `MemoryRuntimeService` 的 cache）
  - `gateway/services/agent_context.py` 的 `AgentContextService` 类属性（`_llm_service` / `_provider_router`）
  - `gateway/session_context.py` 全部 ContextVar
  - 所有 `gateway/services/` 与 `gateway/harness/` 下 module-level mutable 单例（dict / set / list），**plan 阶段做一次 grep `^_[a-z_]+ *=`**
- **FR-27** [必须][MUST] plan 阶段产出精确清单文档（路径 + 清理方式），漏一个就出现 "alone pass / together fail" 风险

### 4.5 pre-commit hook + CLI

- **FR-28** [必须][MUST] 创建 `.githooks/pre-commit` shell 脚本：检测 `SKIP_E2E=1` env → exit 0；否则 `cd octoagent && uv run pytest -m e2e_smoke --maxfail=1 -q`
- **FR-29** [必须][MUST] `Makefile` 加 `make install-hooks` target：`git config core.hooksPath .githooks`
- **FR-30** [必须][MUST] `octo e2e {smoke|full|<domain_id>|--list}` CLI 命令实现
- **FR-31** [必须][MUST] hook 失败输出格式：3 行核心信息（域名 / 期望 / 实际）+ 日志路径 + 跳过指引；**禁止**全量 dump LLM 响应
- **FR-32** [必须][MUST] `repo:check` 与 `octo e2e smoke` 分层：hook 内顺序为 `repo:check` → `octo e2e smoke`，前者 FAIL 直接短路（省 LLM 调用）

### 4.6 Secrets 注入

- **FR-33** [必须][MUST] 所有 secrets 通过 env var 注入：`OPENAI_API_KEY` / `SILICONFLOW_API_KEY` / `OPENROUTER_API_KEY` / Codex auth-profiles 路径
- **FR-34** [必须][MUST] **绝不**将 secrets 提交到 git（包括 fixture 文件 / 测试数据 / 默认 config）
- **FR-35** [必须][MUST] OPENROUTER_API_KEY 通过 `mcp.install(env={...})` 显式注入（McpInstaller `_SAFE_ENV_KEYS` 不透传）；e2e 真打 manual gate：`OCTOAGENT_E2E_PERPLEXITY_API_KEY` env 未设置时域 #5 SKIP；CI / pre-commit / `octo e2e smoke|full` 默认不跑真打。设置 env 后 fixup#12 走 npm install + 真启 server + 真调 OpenRouter Perplexity 全链路（package: `openrouter-mcp` v2.0.1, tool: `ask_model`，model: `perplexity/sonar-pro-search`）

### 4.7 Key Entities

- **OctoHarness**：lifespan 业务逻辑容器；持有 stores / services / DI 钩子；`bootstrap(app)` + `shutdown(app)` 双入口
- **e2e Domain**：13 个能力域，每个含 id / name / suite (smoke|full) / fixture / assertions
- **WriteResult**（已存在）：e2e 断言主要依赖载体（status / 关联 ID 字段）
- **A2AConversation / DispatchEnvelope**（已存在）：能力域 #10 持久化断言对象

## 5. 非功能需求 NFR

- **NFR-1** [性能] e2e_smoke 总耗时 ≤ 180s（目标 90-120s）；e2e_full ≤ 10min
- **NFR-2** [性能] 单 LLM call ≤ 120s；MCP 子进程 ≤ 120s（复用现有 `_SUBPROCESS_TIMEOUT_S`）
- **NFR-3** [可用性] hook 失败信息可读：3 行核心 + 日志路径，不刷屏
- **NFR-4** [可用性] `SKIP_E2E=1` 紧急 bypass，但留痕（不引入 `.skip-e2e` 文件式开关）
- **NFR-5** [安全] secrets 全部 env 注入，gitignore 严约束，fixture 不写明文 token
- **NFR-6** [安全] 日志 redact OPENROUTER_API_KEY / OAuth token / Telegram token
- **NFR-7** [稳定性] 5x 循环跑 e2e_smoke 0 regression（仿 `test_acceptance_scenarios.py` 现有范式）
- **NFR-8** [可观测] e2e 跑出的 events / artifacts 落到 `tmp_path` 下独立 SQLite，不污染宿主

## 6. 关键架构改动 / 不可逆决策

| 改动 | 类型 | 影响范围 | 备注 |
|------|------|--------|------|
| 抽离 `OctoHarness` 类 | 重构 | `gateway/main.py` lifespan + 新文件 `gateway/harness/octo_harness.py` | 净增 ~-140 行 [推断]；提升可测性 |
| `McpInstallerService` 加 `mcp_servers_dir` DI | 生产代码改动 | `gateway/services/mcp_installer.py` | OQ-2 决议 A；最干净方案 |
| `pytest-rerunfailures` 引入 | 依赖 | `pyproject.toml` dev deps | 仅 e2e 用，单测不加 |
| native shell pre-commit hook | 新增 | `.githooks/pre-commit` + Makefile | 不引入 pre-commit framework |
| 不做 ScriptedAdapter | 决策 | — | 全用真实 LLM；echo mode 仍保留供单测 |

## 7. 不做的事（用户已锁定，复述）

- ❌ CI 不跑 e2e（仅本地 pre-commit + 手动）
- ❌ 不引入 cassette / VCR / mock provider
- ❌ 不引入 pre-commit framework（python pkg 那个）
- ❌ Telegram channel 不纳入 F087
- ❌ 不做 ScriptedAdapter
- ❌ 不做多模型对比 / 性能基准 / 失败归档（远期）
- ❌ 测试代码内不手写 retry 循环（用 `pytest-rerunfailures`）
- ❌ 不用 `git commit --no-verify`（用 `SKIP_E2E=1`）

## 8. 关键不变量

1. **Constitution 全 10 条不破**（详见 tech-research §12 兼容性表，全部 ✅ / N/A）
2. **全量 ≥ 2038 测试 0 regression**（F086 基线；F087 引入新测试不允许导致老测试失败）
3. **Secrets 不进 git**：fixture 文件 / 测试数据 / 默认 config 严格 grep `API_KEY` / `TOKEN` / `SECRET` 都为空
4. **不污染 ~/.octoagent**：e2e 跑前后 USER.md / MEMORY.md / mcp-servers/ / auth-profiles.json sha256 一致
5. **prefix cache 不破**：能力域 #3 验证两次调用 frozen_prefix_hash 相同
6. **生产代码 byte-for-byte 等价**：OctoHarness 抽离后 lifespan 行为不变（除新 DI 钩子默认 None 路径）

## 9. Success Criteria SC

- **SC-1** 13 个能力域 e2e 场景全部实现，每场景 ≥ 2 断言点
- **SC-2** e2e_smoke 在 commit hook 触发，单次执行 ≤ 180s（实测多次 p95 ≤ 150s）
- **SC-3** e2e_full 单次执行 ≤ 10min（含真实 Perplexity）
- **SC-4** 5x 循环跑 e2e_smoke 0 regression
- **SC-5** Codex Adversarial Review 0 high finding（spec 阶段 + Phase implement 末尾各跑一次）
- **SC-6** OctoHarness 抽离后 lifespan ≤ 20 行；F086 基线测试全绿
- **SC-7** `~/.octoagent/{USER.md,MEMORY.md,mcp-servers/,auth-profiles.json}` 跑前后 sha256 一致
- **SC-8** Secrets grep 在仓库内为空（除 `.gitignore` 的 negative pattern）
- **SC-9** `SKIP_E2E=1 git commit` 显式跳过路径正常 exit 0
- **SC-10** F086 全量 ≥ 2038 测试基线 0 regression

## 10. Phase 拆分预览

| Phase | 主题 | 估算工时 | 关键交付 |
|-------|------|--------|--------|
| **P1** | OctoHarness 抽离 + DI 钩子 | 2-3d | `octo_harness.py` 新建；lifespan ≤ 20 行；F086 测试全绿 |
| **P2** | McpInstaller DI 改造 + e2e 基础设施 | 1-2d | mcp_servers_dir DI；pytest markers；rerunfailures；conftest 双 autouse |
| **P3** | smoke 套件 5 域实现 | 2-3d | 域 #1 #2 #3 #11 #12 完整 e2e + 断言；pre-commit hook 上线 |
| **P4** | full 套件 8 域实现 | 3-4d | 域 #4-#10 #13；含真实 Perplexity / A2A / Routine 时序 |
| **P5** | `octo e2e` CLI + 5x 循环回归 + Codex Review | 1-2d | CLI 命令；最终 acceptance；spec/plan 大改后 Codex review |

详细拆分留 plan 阶段。

## 11. 风险与依赖

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| 1 | Codex OAuth subscription 限频 | 中 | 中 | smoke ≤ 10 次 LLM call；full 手动触发 |
| 2 | Perplexity 远端抖动炸 commit | 中 | 中 | #5 仅 full；smoke 不含；retry 1 次后 SKIP |
| 3 | module 单例 reset 漏 | 中 | 高 | plan 阶段精确 grep；5x 循环验证 |
| 4 | xdist 并发 race（`-n auto` 时）| 低 | 中 | F087 默认串行跑 e2e；不开 xdist |
| 5 | OctoHarness 抽离破坏 prefix cache | 低 | 高 | 域 #3 直接断言 frozen_prefix_hash 一致 |
| 6 | LLM 响应非确定，断言脆弱 | 中 | 中 | 断言聚焦副作用而非 LLM 文本（FR-11）|
| 7 | hook 拖慢 commit 节奏被绕过 | 中 | 低 | 文档警示；SKIP_E2E 显式留痕 |

**外部依赖**：
- 宿主 `~/.octoagent/auth-profiles.json` 含真实 Codex OAuth token
- 宿主 `~/.octoagent/data/ops/mcp-servers.json` 含 OPENROUTER_API_KEY
- Codex 服务可用 + Perplexity API 可用

## 12. 复杂度评估（供 GATE_DESIGN 审查）

- **组件总数**：3（OctoHarness 类 + e2e fixture 模块 + CLI 命令）
- **接口数量**：4（OctoHarness DI 钩子）+ 1（McpInstaller `mcp_servers_dir` 参数）+ 1（`octo e2e` CLI）= **6**
- **依赖新引入数**：1（`pytest-rerunfailures>=14.0`）
- **跨模块耦合**：是（修改 `gateway/main.py` lifespan + `gateway/services/mcp_installer.py` + 新增 `gateway/harness/octo_harness.py`，共 3 个模块；不算高耦合，因为是抽离不是侵入）
- **复杂度信号**：递归 = 无 / 状态机 = 无（OctoHarness 是 builder）/ 并发控制 = 低（autouse fixture 串行跑；不开 xdist）/ 数据迁移 = 无 = **0 个信号**
- **总体复杂度**：**MEDIUM**

**判定**：组件 < 5 且 接口 < 8 且 复杂度信号 = 0 → 按规则本应 LOW；但 OctoHarness 抽离涉及 600 行业务逻辑搬运 + 4 个 DI 钩子 + 不能破 F086 基线，工程风险偏高，**人工审查建议关注 OctoHarness 抽离前后 byte-for-byte 等价性**，故定 MEDIUM。

---

## 附录 A — spec 阶段发现的新 risk / open issue

1. `apps/gateway/tests/e2e/test_acceptance_scenarios.py` 现有 5 域循环范式与新 13 域 e2e 的关系：plan 阶段需决策——是替换（旧测删除）还是叠加（旧测保留为额外回归）。建议**替换**，避免双源真相。
2. OctoHarness 抽离时散落的测试 helper（`_build_real_user_profile_handler` / `_ensure_audit_task` / `_insert_turn_events`）合并到 OctoHarness `test_factory()` classmethod，或独立 `test_helpers.py`——plan 阶段定。
3. `_reset_module_state` 的精确清单 grep 必须在 P2 早期跑，**不能拖到 P3 测试上线后**（漏一个 → 整个 smoke 套件 flake）。
4. Codex OAuth quota 耗尽场景的 e2e 行为（FAIL vs SKIP）spec 未严格锁定，建议 plan 阶段定为"输出 'quota exceeded' 提示并 SKIP"避免误报阻塞 commit。
