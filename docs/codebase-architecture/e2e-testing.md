# E2E Testing — `apps/gateway/tests/e2e_live/`

> Feature 087 的产出文档。位置：`octoagent/apps/gateway/tests/e2e_live/`。
> 关联模块：`OctoHarness`（`octoagent/gateway/harness/octo_harness.py`）、
> `domain_runner`（`tests/e2e_live/helpers/domain_runner.py`）、
> `octo e2e` CLI（`apps/gateway/src/octoagent/gateway/cli/e2e_command.py`）。

## 1. 架构总览

F087 之前的 e2e 测试集中在 `apps/gateway/tests/e2e/test_acceptance_scenarios.py`
（5 域 5x 循环），已在 P5 删除。新方案围绕 4 条主轴：

1. **OctoHarness 抽离**：`OctoHarness.bootstrap()` 是真实启动路径的统一入口，
   `gateway/harness/octo_harness.py` 暴露 7 个 DI 钩子供 e2e 注入替换（全部默认
   None = 生产行为等价）：`credential_store` / `llm_adapter`（路径 A：
   FallbackManager 纯文本）/ `mcp_servers_dir` / `data_dir` / `plugins_dir`（F106）/
   `model_client`（F138，路径 B：SkillRunner 决策环脚本化——非 None 时无条件建
   SkillRunner、与 `OCTOAGENT_LLM_MODE` 解耦、不要求 provider 凭证）/ `clock`
   （F138，`app.state.clock` seam + watchdog 构造注入）。生产路径不感知测试存在。
   （F138 前本节曾宣称存在 `secret_store` / `transport_factory` 两个 DI——
   从未落地，已按实况修正。）
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
5. **F137 真 LLM 调用许可硬闸**：测试会话默认 gate=deny（provider 包 pytest11
   插件 + `octoagent/conftest.py` 双布线），漏网真调用抛
   `ModelRequestsNotAllowedError` 而非被 FallbackManager 静默降级 Echo 假成功；
   `e2e_live/conftest.py` 的 autouse fixture 按 **e2e_full marker** 自动翻
   allow（e2e_smoke 保持 deny——smoke 的「不打 LLM」从约定升级为构造性保证）。

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

### 2.0 F138 脚本化决策环套件（marker `e2e_scripted`，独立于 13 域注册表）

`test_e2e_scripted_decision_loop.py`（+ `test_octo_harness_model_client_di.py`）
是 M9 F138 引入的 **L3 确定性决策环层**——与上表 13 域正交，不进
DOMAIN_REGISTRY：

- **打通的缺口**：13 域中 smoke 从 `tool_broker.execute()` 切进（跳过"LLM 决定
  调哪个工具"），决策环**前半段**此前 L3 零覆盖。本套件用
  `octoagent.skills.testing.ScriptedModelClient`（脚本脑）经
  `OctoHarness(model_client=...)` DI 驱动**真** SkillRunner 多步循环 → 真
  tool_broker 派发 → 真回写（USER.md / MEMORY_ENTRY_ADDED），断言完整事件链。
- **零真 LLM / 零宿主 OAuth**：空 tmp `CredentialStore` + `provider_router.
  resolve_for_alias` bomb（路径 A/B 共同咽喉点）双保证——不依赖
  `real_codex_credential_store`，宿主无 OAuth 也恒可跑 → **CI-runnable**
  （M9 "L2/L3 只能宿主机跑" 洞的第一条补口）。
- **成本画像**：4 case 全 11 段 bootstrap + 决策环 < 3s（真 LLM 单 call 60-120s）。
- **已归入 pr lane（F141 兑现）**：pre-commit 跑 `-m "e2e_smoke or e2e_scripted"`；
  CI backend job 另有 e2e_scripted 专属步（e2e_live 目录内 CI-runnable 子集，
  含 F142 prompt 预算护栏）。
- **F144 扩展**：`test_e2e_scripted_write_approval.py`（同 marker）——脚本脑驱动
  `behavior.write_file` × **F136 服务端审批全链**（真 ApprovalGate/Manager +
  真 REST `POST /api/approve/{id}`，approve/reject 双路径），吸收 F135 gap-1
  「USER.md 初始化引导闭环」手工验证。注意两点范式差异：①须绑真
  `ExecutionRuntimeContext`（生产由 task_service.py:745 绑，misc_tools 裸调
  `get_current_execution_context` 未绑即 raise）；②`permission_preset=full`
  下审批仍触发 = 服务端审批不可被 policy 放宽绕过的最强断言。

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

跨 runtime 真触发 e2e 推迟到 F088+。**真路径范围**：worker A LLM 调
`delegate_task` tool → `DelegationManager.delegate` (gate) → `_launch_child` →
`pack_service._launch_child_task` → `task_runner.launch_child_task` → child
worker_runtime 起 SkillRunner → WorkerResult → `orchestrator._persist_a2a_terminal_message`
→ `A2AConversation.status = COMPLETED`。

> **注**：F087 域 #10 是 schema integration 测试（直调主路径写库），不是真 e2e。
> 历史上 Feature 064 P1 设计的 in-process `SubagentExecutor` 路径已在 Feature 084+
> 改造时被 task_runner 路径替代；F087 followup 已清理 `subagent_lifecycle.py`
> 死代码（commit 见 git log）。

## 3. 跑法

### 3.1 pre-commit hook（自动跑 smoke + scripted）

一次性安装：

```bash
make install-hooks
```

worktree-aware：linked worktree 只写 `--worktree` 配置（不污染主仓 `.git/config`）。
之后 `git commit` 自动跑 `pytest -m "e2e_smoke or e2e_scripted"`（F141 起纳入脚本化
决策环，实测 24 用例 ~8s），180s portable watchdog（python3 SIGTERM→SIGKILL 升级，
不依赖 macOS 上需 `brew install coreutils` 的 `timeout`）。F141 change-policy 路由：
staged 全部 ∈ {docs/**, *.md, .specify/**} → 跳过 e2e + 前端检查（sync-check 恒跑）；
gate 机器资产（`octoagent/tests/quarantine.json` / `attestation-checklist.md`）staged
时附跑对应校验器；生产 src 改动无伴随测试 → WARNING 不阻断。三模式 lane 编排
（pr/baseline/release，release 强制 live）见 `repo-scripts/lane.py` +
`octoagent/tests/AGENTS.md` §3。

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

## 9b. L1 UI E2E（F140，Playwright 薄输入 + 外部断言）

M9 四层金字塔的 L1 层，位于 `octoagent/frontend/e2e/`（@playwright/test，
chromium）。此前 L1 绝对零（前端 vitest 全 `vi.mock` 屏蔽 API）。

**形态**：build 一次 frontend dist → gateway 单进程 serve（`main.create_app`
的 SPA mount）→ Playwright 直打 gateway 端口，不起独立 vite server。后端由
`apps/gateway/tests/e2e_live/l1_support/serve_l1_gateway.py` 拉起：
`create_app(harness_factory=...)`（F140 D1 DI 缝，生产不传构造性不可达）+
hermetic 实例 root + **零真 LLM 三重防御**（F137 gate=deny env / 空凭证
CredentialStore / bootstrap 后 `resolve_for_alias` bomb——F138 keystone 同款）。
脚本脑 `l1_support/scenario_brain.py` 按 prompt-marker 路由（长驻 server 跨
测试消费，队列版会 desync）。

**薄输入纪律**：UI 内只做 ①输入（fill/click）②等稳定信号（testid 定位回复
气泡/gate，Playwright auto-wait，禁裸 sleep）。**断言全在 UI 外**（node）：
REST 事件链（`GET /api/tasks/{id}` 的 TOOL_CALL_*/MODEL_CALL_*）+ 文件系统
（工具写盘产物逐字节全等）+ wire 级 task_id（waitForResponse）。等待超时前
扫 UI 已知失败文案降级成定性失败（cc-haha desktop-smoke 范式）。

**场景（v0.1 两条）**：①chat 输入 → 脚本决策环 → `filesystem.write_text`
真执行 → SSE 回复渲染（真 EventSource 路径，jsdom 测不到）；②bearer 模式
FrontDoorGate 输 token 解锁 → 发消息全链路（SSE `access_token` query 鉴权）
+ storage 持久化模式断言。场景②首跑即抓出 api/client.ts Authorization 被
init.headers 覆盖的真 production bug（bearer 聊天必 401）。

**selector 契约**：锚点单一事实源 `frontend/e2e/selectors.ts`；
`frontend/testing/l1SelectorsContract.test.ts`（vitest）机械校验每锚点在
src/**.tsx 字面存在——删锚点 vitest 先红，不等 CI Playwright 炸。

**跑法**：`cd octoagent/frontend && npm run build && npm run test:e2e`
（webServer 自动拉起/复用两台 L1 gateway：loopback 8151 / bearer 8152）。
CI：`.github/workflows/feature-007-integration.yml` 的 `l1-playwright` job
（零 secret，free tier 安全）。

**已知约束**：①每个 L1 server 每 run 只承载一条发消息对话链（服务端会话恢复
使 marker 跨测试泄漏进决策环 prompt）；新增发消息测试走「+ 新建对话」UI 流
（v0.2）或独立 server。②审批点击场景 deferred——chat 主路径无确定性审批触发
器（F136 绑 execution session 而 chat inline 不绑；IRREVERSIBLE `cron.delete`
实测 broker 默认放行），见 F140 spec §0/§6。③**dist 存在时跑全量 backend 会
挂 f023 两测试**（master 存量潜伏 bug，F140 实测坐实：SPA `Mount("/")` 在
create_app 构造期注册，遮蔽 lifespan 期 harness 挂载的 telegram webhook 路由
→ POST 405；已派独立修复 task）——本地跑全量回归前 `rm -rf frontend/dist`，
或接受这 2 个与 F140 无关的失败；CI 不受影响（backend job 从不 build dist）。

## 10. 已知工程债

- **memory_candidates audit task 字段缺失**：F084 P5 spawn task 待修；当前
  e2e_full 域 #4 直调路径已绕开（`test_e2e_memory_pipeline.py` 不依赖 audit task）。
- **F083 race（`test_sc3_projection`）**：aiosqlite event loop 关闭顺序导致
  全量回归偶发 1 例 FAIL，重跑必过。F083 已记录，超 F087 scope。
- **OAuth profile 失效自然 SKIP**：`tests/fixtures/local-instance/auth-profiles*`
  无真实 token 时 e2e_full 真实 LLM 测自然 SKIP，不阻断 commit。

## 11. 本机 live 验收探针（`octo attest`，F144）

测试金字塔之外的**第五层**：跑在用户真实托管实例上的验收探针（验证吸收原则——
「请用户手工验证」必须被分层吸收，探针吸收的是 hermetic 测试物理够不到的
「真机链路」半边）。

| 命令 | 吸收对象 | 副作用 |
|------|---------|--------|
| `octo attest remote [--json]` | F130 AC-1 链路半边（tailscale→serve→/ready+SPA+bearer+SSE 验活） | 零（只读 GET） |
| `octo attest service [--dry-run] [--json]` | F129 AC-1 崩溃自愈（SIGKILL 真 pid → poll 恢复 → 新 pid） | 服务秒级闪断 |

- **三态协议**：`pass / not_enabled / fail`（exit 0/0/1）——`not_enabled` 是
  「能力未启用」不是失败；`fail` 才是回归信号（含「已启用但 tailscale 断链」）。
- **绝不进 CI**：真副作用 + 依赖真实实例。探针逻辑回归由 hermetic 单测
  `packages/provider/tests/dx/test_attest_commands.py` 在 CI 守（DI 全 fake：
  零真 tailscale / 零真 HTTP / 零真 kill / 虚拟时钟）。
- **F141 消费**：release lane 跑两探针 `--json` + attestation 清单签署
  （`docs/codebase-architecture/attestation-checklist.md`，物理不可自动化残余，
  首版仅 2 项）。契约细节见
  `.specify/features/144-attestation-absorb/handoff-to-F141.md`。

## 12. 相关文档

- `MODULE_SINGLETONS.md`（reset 清单实证）
- `docs/codebase-architecture/harness-and-context.md`（OctoHarness / SnapshotStore /
  ApprovalGate / DelegationManager 架构）
- `docs/codebase-architecture/testing-concurrency.md`（F083 并发加速、xdist 提速）
- `docs/codebase-architecture/attestation-checklist.md`（人工验收残余清单，F144）
- `.specify/features/087-agent-e2e-live-test-suite/spec.md` / `plan.md`（F087
  原始 spec / 实施 plan / Codex review 决策）
