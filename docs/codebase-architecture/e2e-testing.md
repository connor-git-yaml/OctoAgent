# E2E Testing — `apps/gateway/tests/e2e_live/`

> Feature 087 的产出文档。位置：`octoagent/apps/gateway/tests/e2e_live/`。
> 关联模块：`OctoHarness`（`octoagent/gateway/harness/octo_harness.py`）、
> `domain_runner`（`tests/e2e_live/helpers/domain_runner.py`）、
> `octo e2e` CLI（`apps/gateway/src/octoagent/gateway/cli/e2e_command.py`）。

## 1. 架构总览

F087 之前的 e2e 测试集中在 `apps/gateway/tests/e2e/test_acceptance_scenarios.py`
（5 域 5x 循环），已在 P5 删除。新方案围绕 4 条主轴：

1. **OctoHarness 抽离**：`OctoHarness.bootstrap()` 是真实启动路径的统一入口，
   `gateway/harness/octo_harness.py` 暴露 4 个 DI 钩子（`credential_store` /
   `secret_store` / `transport_factory` / `clock`）供 e2e 注入 stub 替换；
   生产路径不感知测试存在。
2. **Hermetic 隔离**：双 autouse fixture（`tests/e2e_live/conftest.py`）每个测试
   重置 5 类凭证 env、重定向 4 个 `OCTOAGENT_*` 路径 env 到 tmp 目录、按
   `helpers/MODULE_SINGLETONS.md` 清单逐条 reset 5 项 module 级单例
   （`_REGISTRY` / `AgentContextService` 两类属性 / `_CURRENT_EXECUTION_CONTEXT`
   ContextVar / `_tiktoken_encoder`）；不动 `HOME`（子进程依赖）。
3. **直调主路径**：smoke 在集成层（DI 注入 stub transport）；full 8 域中 3 个域
   （#4 memory promote / #8/#9/#10 delegation）在 P4 fixup 阶段下沉为
   **直调主路径**，绕开 LLM 决策不确定性，断言固定行为
   （详见 §9 GATE_P3_DEVIATION）。**域 #5 fixup#12** 改为真 e2e（真 npm install
   `openrouter-mcp` + 真启 stdio MCP server + 真调远端 OpenRouter Perplexity
   `ask_model` 工具），但走 **manual gate**——`OCTOAGENT_E2E_PERPLEXITY_API_KEY`
   env 未设置时 SKIP，CI / pre-commit / `octo e2e smoke|full` 默认不跑真打。
4. **OctoHarness 内置 120s ProviderRouter timeout**：单测内不会无限挂在网络上。
   外加 30s SIGALRM 单场景 watchdog（`signal.alarm(30)` 包 e2e 函数体；仅主线程
   生效，pytest-asyncio "auto" 模式下 e2e 在主线程）。

## 2. 13 能力域清单

| 域 # | 名称 | marker | 文件 |
|------|------|--------|------|
| 1 | 工具调用基础 | e2e_smoke | `test_e2e_basic_tool_context.py` |
| 2 | USER.md 全链路（threat scanner 通过） | e2e_smoke | `test_e2e_basic_tool_context.py` |
| 3 | 冻结快照 + Live State 二分（prefix cache 不爆） | e2e_smoke | `test_e2e_basic_tool_context.py` |
| 4 | Memory observation → promote → audit | e2e_full | `test_e2e_memory_pipeline.py` |
| 5 | 真实 Perplexity MCP install + invoke（**manual gate**：需 `OCTOAGENT_E2E_PERPLEXITY_API_KEY`，否则 SKIP；走真 npm install `openrouter-mcp` + 真启 server + 真调 `ask_model` 远端 OpenRouter Perplexity）| e2e_full | `test_e2e_mcp_skill_pipeline.py` |
| 6 | Skill 调用（强类型 contract） | e2e_full | `test_e2e_mcp_skill_pipeline.py` |
| 7 | Graph Pipeline（DAG checkpoint） | e2e_full | `test_e2e_mcp_skill_pipeline.py` |
| 8 | `delegate_task`（worker 派发） | e2e_full | `test_e2e_delegation_a2a.py` |
| 9 | Sub-agent `max_depth=2` 边界 | e2e_full | `test_e2e_delegation_a2a.py` |
| 10 | A2A-Lite 双向通信（schema 集成，详见下方边界说明） | e2e_full | `test_e2e_delegation_a2a.py` |
| 11 | ThreatScanner block（恶意 prompt + USER.md 不变） | e2e_smoke | `test_e2e_safety_gates.py` |
| 12 | ApprovalGate session allowlist + SSE 审批 | e2e_smoke | `test_e2e_safety_gates.py` |
| 13 | Routine cron + webhook 触发 | e2e_full | `test_e2e_routine.py` |

smoke = 5 域（#1 #2 #3 #11 #12）；full = 8 域（其余）。注册表权威源：
`tests/e2e_live/helpers/domain_runner.py::DOMAIN_REGISTRY` 与
`gateway/cli/e2e_command.py::_DOMAIN_REGISTRY` 双源（CLI 单跑用 `-k` keyword 匹配，
pytest 不支持 prefix node ID）；改 13 域必须同步两处。

### 2.1 域 #10（A2A）e2e 边界说明

F087 域 #10 测试性质：**A2A 数据 schema + 关联键 + 状态机 + 审计完整性的集成
测试**，**不验证跨 runtime 真触发**。

为何不真跨 runtime 跑：
- worker B 真跑需 SkillRunner.run(model_client) → 真打 LLM，决策不 deterministic
  且消耗 quota
- `A2AConversationStatus.COMPLETED` 状态转换由 orchestrator A2A inbound handler
  在 worker A 收到 INBOUND RESULT 时触发，需要双 agent runtime 完整跑通
- 单进程 ASGI test app 不支持跨 agent runtime daemon（A2A 投递依赖 agent runtime
  独立 lifecycle）

域 #10 测试策略：直调主路径 `task_store.create_task` / `a2a_store.save_conversation`
/ `a2a_store.append_message` / `event_store.append_event_committed` +
`A2AConversation` / `A2AMessageRecord` Pydantic 模型 + `EventType.SUBAGENT_SPAWNED`
等枚举，验证 4 子断言（详见 spec.md FR-15）。

跨 runtime 真触发 e2e 推迟到 F088+（接入 worker B inline asyncio daemon + A2A
poll task）。F087 范围内由 unit / integration 测试覆盖：
- `tests/unit/services/test_subagent_lifecycle.py` — `spawn_subagent` /
  `_create_subagent_executor` / `SubagentExecutor._record_a2a_message` 主路径
- `tests/integration/test_a2a_dispatch_flow.py` — A2A daemon + outbound /
  inbound 完整流转

## 3. 跑法

### 3.1 pre-commit hook（自动跑 smoke）

一次性安装：

```bash
make install-hooks
```

worktree-aware：linked worktree 只写 `--worktree` 配置（不污染主仓 `.git/config`）。
之后 `git commit` 自动跑 `pytest -m e2e_smoke`，180s portable watchdog（python3
SIGTERM→SIGKILL 升级，不依赖 macOS 上需 `brew install coreutils` 的 `timeout`）。

### 3.2 `octo e2e` CLI

```bash
octo e2e smoke              # 跑 smoke 5 域
octo e2e full               # 跑 full 8 域
octo e2e 7                  # 跑域 #7 单测（domain_runner 转发到 pytest -k）
octo e2e --list             # 列 13 域
octo e2e smoke --loop=5     # smoke 5x 循环（仿 run_5x.sh）
```

退出码 0 = 全 PASS（SKIP 不算 FAIL，但写入 `~/.octoagent/logs/e2e/quota-skip-*.log`）；
1 = 至少 1 个 FAIL；2 = 参数错误。

### 3.3 手动 pytest

```bash
cd octoagent
uv run pytest -m e2e_smoke -q          # smoke
uv run pytest -m e2e_full -q           # full
uv run pytest apps/gateway/tests/e2e_live/test_e2e_safety_gates.py -v  # 单文件
bash apps/gateway/tests/e2e_live/run_5x.sh  # 5x 循环（独立脚本，不写进 conftest）
```

## 4. SKIP_E2E=1 紧急 bypass

遇紧急 commit 且 e2e 阻塞时：

```bash
SKIP_E2E=1 git commit -m "..."
```

hook 立即 `exit 0` 并打印 `[E2E] skipped via SKIP_E2E=1`。**只用于真实紧急场景**
（如热修线上）；不要默认开启。

## 5. quota 429 处理

LLM provider 返回 429 时，`tests/e2e_live/test_quota_skip.py` 的协议匹配会把
该测试标记为 SKIP（不是 FAIL），避免外部 quota 抖动让 commit 阻断。匹配走
**结构化 protocol**：`HTTP 429` + provider 标识响应 JSON，**禁 substring 匹配**
（避免任何包含 `quota` 字样的 LLM 输出文本误命中）。SKIP 行写入
`~/.octoagent/logs/e2e/quota-skip-<ts>.log`；CI 上看到 SKIP 多发应排查
真实 quota 配置而不是放任。

## 6. module 单例 reset 维护指南

任何新增 module 级 stateful 单例（`ContextVar` / `lru_cache` / 类属性
缓存 / `_REGISTRY` 字典 / 字面量 list/dict/set 赋值）必须同步更新
`tests/e2e_live/helpers/MODULE_SINGLETONS.md`，并在 `conftest.py` 的
`_reset_module_state` autouse fixture 中加 reset 逻辑。否则**测试间状态泄漏**
会让 e2e 在 5x 循环时偶发失败（典型症状：单跑 PASS / 套跑 FAIL）。

实证 grep 命令清单见 `MODULE_SINGLETONS.md` 顶部，新增模块时跑一遍这 5 条 grep
对照清单。

## 7. fixture 路径速查

| 资源 | 路径 | 用途 |
|------|------|------|
| local-instance 模板 | `octoagent/tests/fixtures/local-instance/` | hermetic root 初始内容（USER.md / behavior/ 骨架） |
| `.env.e2e` | `octoagent/tests/fixtures/.env.e2e`（用户自填，git 忽略） | 真实 LLM e2e 凭证（仅 full 用） |
| auth-profiles | `~/.octoagent/auth-profiles.json` | 真实 OAuth profile（fixtures_real_credentials 复制到 tmp） |
| `octo_harness_e2e` fixture | `helpers/factories.py` | 注入 4 DI 钩子的 OctoHarness 单例 |
| `real_codex_credential_store` | `helpers/fixtures_real_credentials.py` | 复制真实 OAuth profile 到 tmp（不 mutate） |

## 8. 运行时不变量（SC-7 验证）

跑前后比对 sha256，全部一致：

- `~/.octoagent/behavior/system/USER.md`
- `~/.octoagent/auth-profiles.json`
- `~/.octoagent/mcp-servers/`（递归）

P5 验证记录：F087 e2e_full 跑前后 sha256 完全一致（hermetic 隔离生效）。

## 9. 关键设计权衡：smoke=集成层 / full=直调主路径（GATE_P3_DEVIATION）

P3 阶段 spec 原计划 13 域全部走"真实 LLM"路径，P3 实施时遇到关键 finding：
LLM 决策不稳定性（同样 prompt 不同步 token sampling）让 5x 循环 0 regression
DoD 不可达。Codex P4 review 接受 GATE_P3_DEVIATION 决策——

- **smoke 5 域**：保留集成层（OctoHarness DI + stub transport），断言**调用骨架**
  （USER.md 写入 / 事件 emit / threat scanner block 路径）。验证目标：harness
  装配 + context 流。
- **full 8 域中 4 个**（#4 #5 #8 #9 #10）：在 P4 fixup#7-10 下沉为**直调主路径**
  （直接构造 `MemoryService` / `MCPInstaller` / `DelegationManager`），绕开
  LLM agent loop。验证目标：核心服务行为，不验 LLM。
- **真实 LLM e2e**：保留在 `test_e2e_smoke_real_llm.py`（基线对照），需 `.env.e2e`
  凭证才跑，不在 pre-commit 默认路径。

代价：13 域不全是端到端"真实跑"。收益：5x 循环 0 regression（P5 实测 4s/iter），
pre-commit hook 可用、可信、不阻断开发节奏。

## 10. 已知工程债

- **memory_candidates audit task 字段缺失**：F084 P5 spawn task 待修；当前
  e2e_full 域 #4 直调路径已绕开（`test_e2e_memory_pipeline.py` 不依赖 audit task）。
- **F083 race（`test_sc3_projection`）**：aiosqlite event loop 关闭顺序导致
  全量回归偶发 1 例 FAIL，重跑必过。F083 已记录，超 F087 scope。
- **OAuth profile 失效自然 SKIP**：`tests/fixtures/local-instance/auth-profiles*`
  无真实 token 时 e2e_full 真实 LLM 测自然 SKIP，不阻断 commit。

## 11. 相关文档

- `MODULE_SINGLETONS.md`（reset 清单实证）
- `docs/codebase-architecture/harness-and-context.md`（OctoHarness / SnapshotStore /
  ApprovalGate / DelegationManager 架构）
- `docs/codebase-architecture/testing-concurrency.md`（F083 并发加速、xdist 提速）
- `.specify/features/087-agent-e2e-live-test-suite/spec.md` / `plan.md`（F087
  原始 spec / 实施 plan / Codex review 决策）
