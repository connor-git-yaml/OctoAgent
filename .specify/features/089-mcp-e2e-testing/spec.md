# Feature Spec: MCP E2E Testing — Local Stub + Vendor Manual Gate

**Feature ID**: 089
**Feature Slug**: mcp-e2e-testing
**分支**: 089-mcp-e2e-testing
**生成日期**: 2026-05-05
**版本**: v2（v1 spec 主结构被 Codex adversarial review 拒绝；v2 改走 mcp_registry config-driven 路径，全部 4 high / 3 medium / 1 low finding 闭环）
**调研基础**: F087 e2e_live 套件现状审视 + F088 followup（commit 1768eb2）+ Codex adversarial review on v1 spec
**模式**: story（非完整调研，复用 F087 既有架构 + mcp_registry 既有 API）

---

## 1. 背景与问题

F087 把 13 能力域纳入 e2e_live 套件后，**MCP 集成路径仍是 CI 盲区**：

1. 仓内 4 个 MCP 相关测试文件，**真实 register/execute 主链路 0 CI 覆盖**：
   - `tests/test_mcp_registry.py`：9 个 unit，只测 `load_configs` JSON shape + `list_tools` 过滤
   - `tests/test_capability_pack_tools.py`：unit，capability_pack 路径片段
   - `tests/tools/test_graph_pipeline_security.py`：与 MCP 无关
   - `tests/e2e_live/test_e2e_mcp_skill_pipeline.py::test_domain_5`：真 npm install + 真打远端 OpenRouter，**manual gate**（需 `OCTOAGENT_E2E_PERPLEXITY_API_KEY`）→ CI 永远 SKIP
2. **死代码 `_read_openrouter_api_key()`**（`test_e2e_mcp_skill_pipeline.py:73-100`）定义但 SKIP gate 没用，违反 CLAUDE.md "不保留死代码" 原则
3. F087 的 GATE_P3_DEVIATION（LLM 决策不确定性）让 MCP 链路验证额外脆弱

### 真痛点

- 用户报"装新 MCP 后工具不出现" → 没 e2e 能立刻定位是 config / spawn / broker / namespace 哪一段坏
- F088 followup 改 `mcp_registry` / `mcp_session_pool` 时只能跑 unit，不知道生产链路是否还活着
- F087 域 #5 manual gate 让 MCP 主链路质量信号永远是"未知"

### 不解决的问题（划清边界）

- 不解决 vendor 兼容矩阵（OctoAgent 当前集成 vendor 数量 = 1，过度设计）
- 不解决 cassette / record-replay（cassette 引擎本身的复杂度 > 单 stub 测试收益）
- 不解决 MCP spec 双向 conformance（spec v0.x 已稳定，stub 覆盖足够）
- **不测 npm/pip install 链路**（v2 关键决策，详见 §6.1）

## 2. 范围

### 2.1 范围内

| 改动 | 文件 | 行数估算 |
|------|------|---------|
| 本地 stdio MCP stub server（纯 stdlib） | `tests/fixtures/mcp/stub_server.py`（新增） | ~140 |
| 5 个 e2e_smoke case（config-register/execute/error/delete/namespace） | `apps/gateway/tests/e2e_live/test_e2e_mcp_local_stub.py`（新增） | ~220 |
| 域 #5 SKIP gate 加 host-key opt-in fallback（救活 `_read_openrouter_api_key`） | `apps/gateway/tests/e2e_live/test_e2e_mcp_skill_pipeline.py`（改） | ±25 |
| hermetic env 清理扩展（+ `OCTOAGENT_MCP_SERVERS_PATH` + `OCTOAGENT_E2E_USE_HOST_KEY`） | `apps/gateway/tests/e2e_live/conftest.py`（改） | +6 |
| MCP 测试章节 | `docs/codebase-architecture/e2e-testing.md`（追加） | +60 |
| stub server fixture 入口 | `apps/gateway/tests/e2e_live/helpers/factories.py`（追加 1 fixture） | +20 |

**总改动量**：~470 行。**0 生产代码**（验证 §11 风险条目 R-2）。

### 2.2 范围外（写明何时启动）

| 不做的事 | 启动触发条件 |
|---------|-------------|
| 给 `mcp_installer` 加真正的 `local` install_source 分支 | 用户需要 `octo mcp install <local-path>` CLI（即一等公民支持本地 server） |
| Stub server YAML scenario generator | stub 行为模式数 ≥ 8 |
| Cassette / record-replay 引擎 | 用户上报 MCP 兼容性 bug ≥ 2 个/月 |
| Vendor compatibility matrix | OctoAgent 集成 MCP server ≥ 5 |
| MCP spec JSON Schema 双向 conformance | MCP spec major 升级（v1.0+） |
| Drift detection cron | vendor matrix 已存在的前提下 |
| Production trace replay | 生产已上线 + 用户报障流程已建立 |

## 3. User Stories

### US-1（P1）开发者改 mcp_registry / mcp_session_pool 后，commit 前自动得到 MCP 主链路完整性信号

**Why P1**：F088 followup 已经发生过"改 mcp 服务但只跑 unit，回归到生产路径才暴露"的真实案例。pre-commit hook 必须能在 30s 内告诉开发者"MCP config→spawn→register→execute→delete 是否还活着"。

**独立验证**：跑 `pytest -m e2e_smoke -k mcp_local_stub` 5 个 case 全 PASS in < 10s。

**Acceptance**：
- Given 开发者修改了 `mcp_registry.refresh()` 引入 spawn 路径回归
- When 跑 pre-commit hook
- Then 至少 1 个 case FAIL，给出明确错误（broker.discover 不含 stub_echo / `mcp_registry.list_servers()[0].status == "error"` 等）

### US-2（P1）开发者新接入一个 vendor MCP server 时，stub 测试给出"OctoAgent 自己的 MCP client 实现没问题"的证据

**Why P1**：避免"调试新 vendor 但不知 bug 在 OctoAgent client 还是 vendor server"的双盲场景。stub 是已知正确的对照实现（仿 MCP 官方 SDK 的最小子集）。

**独立验证**：stub 5 个 case 全 PASS = OctoAgent client 实现正确，问题归 vendor。

### US-3（P2）开发者本地想真打 OpenRouter Perplexity（域 #5）时，1 行 env 即跑通，不再需要手动从 `~/.claude.json` 提 key

**Why P2**：当前域 #5 SKIP gate 只看 `OCTOAGENT_E2E_PERPLEXITY_API_KEY`，但用户的 `~/.claude.json` 已存有合法 key——需要让本地体验顺滑而不破坏 CI 默认行为。

**独立验证**：`OCTOAGENT_E2E_USE_HOST_KEY=1 octo e2e 5` 跑通；不设此 env 时 CI 仍 SKIP。

## 4. 功能需求 FR

### FR-1 stub MCP server 协议子集

stub server 必须实现 MCP stdio JSON-RPC 协议子集：

| Method | 行为 |
|--------|------|
| `initialize` | 返回 `{protocolVersion, capabilities, serverInfo}`；protocolVersion 与 OctoAgent mcp_session_pool 接受范围一致 |
| `tools/list` | 返回 1 个 tool：`stub_echo`（input schema：`{message: str}`） |
| `tools/call` (name=stub_echo) | happy 模式返回 `{content: [{type:"text", text:"echo: <message>"}]}` |
| `prompts/list` / `resources/list` / 未知 method | 返回 JSON-RPC error code -32601（method not found） |
| **任何无 `id` 字段的入站消息**（即 JSON-RPC notification） | **静默吞，不响应**（FR-1 关键修正：Codex Finding #5 闭环）|

**关键 notification**：MCP 客户端 `initialize` 成功后发送 `notifications/initialized`——stub 必须不回 response（违反 JSON-RPC 2.0 spec 会污染 stdio 流，导致后续 `tools/list` 不稳定）。

**协议合规**：JSON-RPC 2.0（`jsonrpc: "2.0"`，`id` 字段透传，error 含 code+message）。

### FR-2 stub 行为模式（env 切换）

启动时通过 env var 切换：

| `STUB_MODE` 值 | 行为 |
|----------------|------|
| `happy`（默认 / 未设置） | tools/call 返回 echo |
| `error` | tools/call 返回 JSON-RPC error code -32001 message "stub error" |
| `slow:<seconds>` | tools/call 等 N 秒后返回（验证 OctoAgent client 不卡死） |

**v2 删除 `crash_after:<N>` 模式**（Codex Finding #6 闭环）：现有 `mcp_session_pool.call_tool` 不会标记 dead session，crash 后第二次调用复用旧 session 失败，无法可靠测错误恢复。等 mcp_session_pool 加可靠 reconnect 路径后再加。

**禁用 YAML scenarios** —— FR-2 模式数 ≤ 3，不引入 generator。

### FR-3 e2e 测试用例（v2 重新设计 + v3 实施降级）

5 个 e2e_smoke case，全部纳入 e2e_smoke marker（pre-commit + CI）。**所有 case 通过 `mcp_registry.save_config()` + `mcp_registry.refresh()` 注入 stub server 配置**，不走 `mcp_installer.install()`（详见 §6.1 决策）。

**v3 实施降级**（Codex impl review Finding #1 闭环）：实施时发现 `broker.execute()` 在 pytest-asyncio "auto" 模式下走 mcp_session_pool 时稳定 30s hang（anyio cancel scope cross-task 限制——session 在 fixture bootstrap task open，test case task 调用违反不变量）。L1 case 改用：

- **协议层**：`await app.state.mcp_registry.call_tool(server_name=..., source_tool_name=..., arguments=...)` 直调 stdio JSON-RPC（不走 broker hook 链）
- **broker 注册层**：`broker.discover()` 验证 ToolMeta 字段（tool_group / parameters_json_schema），覆盖 `mcp_registry._build_tool_meta` 契约不回归

完整 `broker.execute` 路径（audit task / permission / TOOL_CALL_STARTED event）由独立 follow-up task 跟踪。

| Case ID | 名称 | 验证 |
|---------|------|------|
| L1.1 | `test_mcp_config_register_execute_happy_path` | save_config + refresh → broker.discover 含精确 `mcp.stub_a.stub_echo` + ToolMeta.tool_group=="mcp" + parameters_json_schema 含 "message" → mcp_registry.call_tool 返回 "echo: hello" → delete_config + refresh → broker.discover 不含 |
| L1.2 | `test_mcp_invalid_command_fails_cleanly` | save_config(command="/nonexistent/binary") → refresh → list_servers[0].status == "error" + broker.discover 不含 |
| L1.3 | `test_mcp_execute_tool_error_propagates` | env={"STUB_MODE": "error"} → mcp_registry.call_tool → 触发 mcp error（异常 / isError=True），错误信息含 "stub error" |
| L1.4 | `test_mcp_unregister_kills_subprocess` | **xfail**：register → 抓 pid → delete_config + refresh → pid 应不存活。当前生产 mcp_registry.refresh 不关闭已删除 config 的 session（mcp_registry.py:133-139 仅对 disabled config 调 close）+ mcp_session_pool 不暴露 pid—— xfail 跟踪 follow-up 修生命周期 |
| L1.5 | `test_mcp_namespace_isolation_two_servers` | 两 config（"stub-x" / "stub-y"，env 各异）→ broker.discover 含精确 `mcp.stub_x.stub_echo` + `mcp.stub_y.stub_echo` → 两次 call_tool 各返回各自 echo（cleanup 由 fixture teardown 兜底，不在 case body 内主动 unregister——anyio cancel scope cross-task 同源问题） |

每 case ≥ 2 独立断言点（与 F087 SC-2 一致）。

**stub fixture 入口**（`helpers/factories.py`）：

```python
@pytest.fixture
def stub_server_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "tests" / "fixtures" / "mcp" / "stub_server.py"
    )
```

### FR-4 域 #5 SKIP gate 改造

`test_e2e_mcp_skill_pipeline.py:202` SKIP gate 加 fallback：

```python
api_key = os.environ.get("OCTOAGENT_E2E_PERPLEXITY_API_KEY", "").strip()
if not api_key and os.environ.get("OCTOAGENT_E2E_USE_HOST_KEY") == "1":
    api_key = (_read_openrouter_api_key() or "").strip()
if not api_key or not api_key.startswith("sk-or-"):
    pytest.skip(...)
```

**Trust boundary 不变**：默认 SKIP；必须显式 `OCTOAGENT_E2E_USE_HOST_KEY=1` 才 fallback；CI / pre-commit 默认仍 SKIP。

### FR-5 hermetic env 清理扩展（v2 新增 / v3 收窄）

`conftest.py::_hermetic_environment` 扩展 **1 条** env 清理（v2 原计划 2 条，v3 收窄为 1 条——Codex impl review Finding #2 闭环）：

```python
# 已有：清 5 类凭证 env + 重定向 4 个 OCTOAGENT_* 路径 env
# 新增（FR-5）：
monkeypatch.delenv("OCTOAGENT_MCP_SERVERS_PATH", raising=False)
# 不清 OCTOAGENT_E2E_USE_HOST_KEY——它是用户 opt-in 信号（spec FR-4），
# cmdline 显式 set 必须能透到测试。conftest 静默清掉会让 SC-4 永远不可达。
```

理由：
- `OCTOAGENT_MCP_SERVERS_PATH` 不清 → mcp_registry 可能读宿主 mcp-servers.json，破 hermetic 隔离 → 清
- `OCTOAGENT_E2E_USE_HOST_KEY` 是 opt-in 信号 → **不清**，让用户 cmdline 显式 set 能生效（防 silent shell profile 激活的边界放在 fixture 内审计 log）

### FR-6 子进程清理保证

每个 L1 case 必须保证 stub server 子进程不泄漏到下个 case：

- happy path：`mcp_registry.delete_config()` + `mcp_registry.refresh()` 触发 session_pool 关闭
- 异常 path：autouse fixture `_cleanup_mcp_subprocesses` 兜底，跑前后扫 mcp_session_pool 残留 session 强制关闭
- L1.4 case 直接断言子进程 pid 已不存在（强制路径）

### FR-7 文档同步

`docs/codebase-architecture/e2e-testing.md` 追加 "MCP testing strategy" 章节：

- L1 = local stub via mcp_registry config-driven（CI 默认）；L2 = real vendor via mcp_installer.install (npm)（manual gate）
- 何时启动 long-term 升级（vendor matrix / cassette 等）
- 与 F087 GATE_P3_DEVIATION 关系：L1 不依赖 LLM 决策，是确定性 e2e

## 5. 非功能需求 NFR

| NFR ID | 描述 | 阈值 |
|--------|------|------|
| NFR-1 | L1 5 case 总耗时 | < 10s（pre-commit 不被拖慢） |
| NFR-2 | L1 零外部依赖 | 不需要 npm / 网络 / API key |
| NFR-3 | stub server 实现仅依赖 stdlib | `import sys, json, os, time` 四个；不引入 mcp 官方 SDK |
| NFR-4 | hermetic 不变量保持 | `~/.octoagent/mcp-servers/` + 宿主 `mcp-servers.json`（如存在）跑前后 sha256 一致 |
| NFR-5 | 跨 worktree 无并发竞争 | stub server 子进程跑在 hermetic tmp |
| NFR-6 | 0 regression | 全量 `pytest -q` PASS 数不降 |
| NFR-7 | stub server 处理 notification 正确 | spike test：stdio 流不被污染（接收 `notifications/initialized` 后下一次 tools/list 仍工作） |

## 6. 关键架构改动 / 不可逆决策

### 6.1（v2 关键决策）L1 走 mcp_registry config-driven，不走 mcp_installer.install

**v1 错误假设**：`mcp_installer.install(install_source="local", package_name=<path>)` 接受指向 .py 的绝对路径。**实际**：`InstallSource` 只有 `NPM` / `PIP` / `DOCKER` / `MANUAL`，没有 `LOCAL`；`MANUAL` 在 `_run_install()` 中抛 "不支持的安装来源"。

**v2 修正**：直接调 `mcp_registry.save_config(McpServerConfig(name=..., command="python", args=[stub_path], env={...}))` + `mcp_registry.refresh()`。这条路径走的是 `mcp_session_pool.open()` → `stdio_client(StdioServerParameters(command, args))` → 真 spawn 子进程。

**理由**：
- 0 生产代码改动（mcp_installer 不动）
- 真覆盖 register / spawn / discover_tools / broker.try_register 完整链路
- 真覆盖 delete_config / refresh / session_pool.close / unregister 完整清理链路
- install path（npm/pip）仍由域 #5 manual gate 真打覆盖

**取舍**：L1 不测 install 链路（npm install + verify_server）。但 install 是 npm/pip 子进程逻辑，跟 OctoAgent client 实现关系小，由域 #5 + 未来 vendor matrix 覆盖足够。

**不可逆性**：低——未来如果 mcp_installer 真加 `LOCAL` source 分支，L1 case 可以并存或迁移。

### 6.2 L1 不真测 LLM，与 F087 GATE_P3_DEVIATION 解耦

**决策**：L1 5 case 全部直调 `mcp_registry` / `broker` 入口，**不发 LLM prompt**，绕开 GATE_P3_DEVIATION 不确定性。

**理由**：MCP 主链路（config/spawn/register/execute）跟 LLM 决策正交。把"OctoAgent client 实现对不对"和"LLM 是否选择调用 MCP 工具"分开测，断言信号干净。

### 6.3 stub server 是 worktree 内 fixture，不进 site-packages

**决策**：`stub_server.py` 放 `octoagent/tests/fixtures/mcp/`，通过 `Path(__file__).resolve().parents[N]` 定位，不打包成可 import 模块。

**理由**：纯 fixture，单一调用点，不需要 import 路径稳定性。

### 6.4 stub 行为切换走 env var，不走 YAML

**决策**：stub server 行为模式（FR-2）通过 `STUB_MODE` env 控制，不引入声明式 scenarios YAML。

**理由**：当前模式数 ≤ 3，env 直观；YAML generator 复杂度 > 直接 if 分支。未来模式数 ≥ 8 时再升级（写在范围外触发条件）。

## 7. 不做的事（用户已锁定，复述）

- 不写 cassette 引擎（不值得为 1 个 vendor 投入）
- 不写 vendor compatibility matrix（vendor 数量 = 1）
- 不写 MCP spec JSON Schema 双向校验（spec 已稳定，stub 覆盖足够）
- 不写 production trace replay（生产未上线）
- **不动 `mcp_installer.py` / `mcp_registry.py` 生产代码**
- 不动 `/api/message` HTTP route 接受 control_metadata（trust boundary 不破）
- 不测 `mcp_installer.install` 链路（v2 关键决策，详见 §6.1）

## 8. 关键不变量

- **I-1**：L1 测试跑前后宿主 `~/.octoagent/mcp-servers/` 和（如存在）`~/.octoagent/data/ops/mcp-servers.json` sha256 一致（hermetic）
- **I-2**：L2 默认行为不变（unset 任一 env → SKIP）
- **I-3**：stub server 子进程绝不泄漏到下个 case（fixture cleanup + L1.4 强制断言）
- **I-4**：本 spec 0 生产代码改动（仅测试 + fixture + conftest + 文档）

## 9. Success Criteria SC

| SC ID | 标准 | 验证方式 |
|-------|------|----------|
| SC-1 | L1 5 case 全 PASS | `pytest -m e2e_smoke -k mcp_local_stub` |
| SC-2 | L1 总耗时 < 10s | 同上，记录 duration |
| SC-3 | L1 进 pre-commit hook | hook 跑 e2e_smoke 自动包含 L1 |
| SC-4 | L2 fallback 生效 | `OCTOAGENT_E2E_USE_HOST_KEY=1 octo e2e 5` 跑通 |
| SC-5 | L2 默认行为不变 | unset env → 仍 SKIP，CI 行为零变化 |
| SC-6 | hermetic 不变量保持 | 宿主 sha256 跑前后一致 |
| SC-7 | 死代码救活（v2 修正） | 加一个**测试用例**显式 set `OCTOAGENT_E2E_USE_HOST_KEY=1` + monkeypatch HOME 到含合法 mock `~/.claude.json` 的 tmp，断言 `_read_openrouter_api_key()` 真返回非 None；fallback 分支被覆盖 |
| SC-8 | 0 regression | 全量 `pytest -q` 仍 3025+ passed |
| SC-9 | 文档同步 | `e2e-testing.md` 含 MCP 测试章节 |
| SC-10（v2 新增） | hermetic env 扩展生效 | spike test：宿主预设 `OCTOAGENT_MCP_SERVERS_PATH=/some/host/path` → L1 跑后 `/some/host/path` 文件不存在或未被改 |

## 10. Phase 拆分预览

| Phase | 内容 | 时长 |
|-------|------|------|
| P1 Spec + Codex Review | 本文档 v2 + Codex re-review | 2.5h |
| P2 Plan + Tasks | `plan.md` + `tasks.md` | 1h |
| P3 Implement L1 | stub_server.py + 5 case + conftest 扩展 | 5h |
| P4 Implement L2 | SKIP gate fallback + 死代码救活 + 死代码 fallback 单测 | 1.5h |
| P5 Codex Impl Review | adversarial review on impl | 1h |
| P6 Verify + Doc + Commit | 跑测试 + 文档 + commit | 1h |
| **总计** | | **~12h（1.5 天）** |

## 11. 风险与依赖

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| R-1 stub server 协议实现 bug → 测试假阳性 | M | H | stub 自身写 1-2 个 unit test 验证（spec future-work；本期接受风险） |
| R-2 子进程清理不干净 → e2e 间相互污染 | M | M | autouse fixture 兜底 + L1.4 直接断言 pid + `mcp_session_pool.close_all()` 在 fixture teardown 显式调 |
| R-3 stub `notifications/initialized` 处理不当 → stdio 流污染让 L1.1 不稳定 | M | H | FR-1 显式要求 + NFR-7 spike test |
| R-4 `mcp_session_pool.open()` 对 stub server 的 protocolVersion 校验失败 | M | M | P3 实施前先跑 spike：写最小 stub 跑 mcp_registry.refresh 看 list_servers 返回 status |
| R-5 MCP 协议 v0.x 升级让 stub 过时 | L | L | spec future-work 写明"v0.x 升级时同步改 stub" |
| R-6 L2 fallback 让用户不小心烧 quota | L | L | conftest 清 `OCTOAGENT_E2E_USE_HOST_KEY`（FR-5）+ 显式 env opt-in；只调 `ask_model` 单次轻量调用 |

**依赖**：
- F087 e2e_live 套件 + OctoHarness DI 钩子
- `mcp_registry.save_config()` / `delete_config()` / `refresh()` API（已稳定）
- `mcp_session_pool.open()` 通过 `stdio_client(StdioServerParameters)` 真 spawn（已稳定）
- `~/.claude.json` 含 OPENROUTER_API_KEY（用户已确认存在，仅 L2 用）

## 12. 复杂度评估（GATE_DESIGN 用）

- **新增代码**：~380 行（140 stub + 220 tests + 20 fixture）
- **改动现有代码**：~31 行（SKIP gate ~25 + conftest ~6）
- **生产代码**：0
- **新模块边界**：1（`tests/fixtures/mcp/`）
- **新外部依赖**：0（纯 stdlib）
- **跨 feature 影响**：仅文档（`e2e-testing.md`）

**评级**：低复杂度。

## 附录 A — long-term 升级路径

本 spec 故意不实施以下，但留扩展点：

| 升级项 | 触发条件 | 实施位置 |
|--------|----------|----------|
| `mcp_installer.install(local=...)` 一等公民支持 | 用户要求 `octo mcp install <local-path>` CLI | `mcp_installer.py` 加 `InstallSource.LOCAL` + `_install_local()` |
| YAML scenario generator | stub 模式数 ≥ 8 | `tests/fixtures/mcp/scenarios/*.yaml` + `stub_server.py` 加 YAML loader |
| Cassette engine | 用户报 MCP 兼容性 bug ≥ 2 个/月 | `tests/fixtures/mcp/cassettes/*.yaml` + `tests/conftest.py` 加 hookwrapper |
| Vendor compatibility matrix | OctoAgent 集成 MCP server ≥ 5 | `tests/e2e_live/test_e2e_mcp_vendors.py` + `vendor_matrix.yaml` |
| MCP spec JSON Schema 双向 | MCP spec v1.0 | conftest 加 schema validator hookwrapper |
| Drift detection cron | vendor matrix 已存在 | `.github/workflows/mcp-drift.yml` + `vendor_matrix/<v>/capabilities.snapshot.json` |
| Production trace replay | 生产上线 + 用户报障流程 | `tests/fixtures/mcp/production-traces/` + replay harness |

每条触发条件都是**可观测**的——OctoAgent 不需要主观判断"是否该做"，看数据即可。

## 附录 B — Codex review finding 闭环表

### B.1 Codex v1 spec review（v1 → v2 主结构修订）

| Finding | Severity | 处理 | spec v2 闭环位置 |
|---------|----------|------|----------------|
| #1 `install_source="local"` 不存在 | high | 接受 | §6.1 + FR-3 改走 `mcp_registry.save_config()` |
| #2 spawn 路径假设错 | high | 接受 | §6.1 + FR-3 用 `command="python", args=[stub_path]` |
| #3 `OCTOAGENT_MCP_SERVERS_PATH` 未清 | high | 接受 | FR-5 + I-1 + SC-10 |
| #4 uninstall 闭环缺 | high | 接受 | FR-3 用 `mcp_registry.delete_config()`，不调 `installer.uninstall()` |
| #5 stub 漏 notification | medium | 接受 | FR-1 显式 + NFR-7 spike |
| #6 crash_after 不可靠 | medium | 接受 | FR-2 删 crash_after 模式 |
| #7 `OCTOAGENT_E2E_USE_HOST_KEY` 未清 | medium | 接受（v3 翻案，详见 B.2 #2） | FR-5 |
| #8 SC-7 grep 计数不严 | low | 接受 | SC-7 改为"显式测试覆盖 fallback 分支" |

### B.2 Codex v2 impl review（spec v2 → v3 实施降级 + 主结构小调）

实施时跑 P5 Codex adversarial impl review，发现 4 high / 2 medium / 1 low。处理：

| Finding | Severity | 处理 | spec v3 闭环位置 |
|---------|----------|------|----------------|
| #1 broker.execute 覆盖缺失 | high | 接受 | FR-3 v3 实施降级注释 + 加 broker.discover ToolMeta 契约断言 + spawn follow-up task 跟踪 broker.execute e2e |
| #2 SC-4 永久不可达（USE_HOST_KEY 被 conftest 清） | high | 接受（翻 v1 #7） | FR-5 v3 收窄：不清 USE_HOST_KEY env |
| #3 测试 helper 绕过生产 delete 路径 | high | 接受 | `_unregister_stub` helper 改为只调 delete_config + refresh，反映生产真实行为；spawn follow-up 修 mcp_registry 生命周期 bug |
| #4 L1.4 SKIP 伪装"未实现" | high | 接受 | L1.4 改 `pytest.xfail` strict=False，明确未实现 + follow-up 跟踪 |
| #5 teardown 静默吞异常 | medium | 部分接受 | 当前保留 catch（不破 F087）；spec 接受 limitation，follow-up 加 leak 探测 |
| #6 模糊匹配 stub_echo 假阳性 | medium | 接受 | 全部 case 改为精确 `mcp.<server_name>.stub_echo` 名字 |
| #7 dead code (_ensure_audit_task / ExecutionContext) | low | 接受 | 删 unused helper + import |

### B.3 v3 → v4 实施分歧补遗（spec push 时同步）

> **背景**：spec v3 完成 + 主 session commit `9c755e3` 后，3 个 spawn task
> 在独立 worktree 完成 follow-up，整体合入 origin/master 时**走的实施路径
> 比 spec v3 更深更治本**。spec 文档先于 master 实施合入，本节作为留档同步。

#### 实施分歧 #1：mcp_session_pool 引入 supervisor 模式（commit `1067943`）

**spec v3 §6.1 设计**：L1 直调 `mcp_registry.call_tool` 绕开 broker.execute，
理由是 broker.execute 在 pytest async cross-task 调用时挂死（推测 anyio
cancel scope 限制）。spec v3 接受降级 + spawn follow-up。

**master 实施**：spawn task #5（commit `1067943`）治本——
`McpSessionPool.open` 改为每个 server 起专属 supervisor `asyncio.Task`，
让 anyio AsyncExitStack（stdio_client + ClientSession）的 enter/exit 都在
同一 task lifecycle 内；主路径通过 `asyncio.MemoryObjectStream` 跨 task
talk to supervisor。新增 `test_mcp_broker_execute_full_audit_chain` e2e
真覆盖**完整 broker.execute 路径**（ApprovalManager + StoreGroup +
cross-task + 4 断言含 TOOL_CALL_STARTED / COMPLETED 事件）。

**spec v3 推迟到 follow-up 的 broker.execute 全链路覆盖在 master 已直接达成。**

#### 实施分歧 #2：mcp_registry 生命周期治本（commit `d039cb1`）

**spec v3 FR-3 L1.4**：标 `pytest.xfail strict=False`，注释"mcp_registry.refresh
不主动关闭已删除 config 的 session + mcp_session_pool 不暴露 pid，两 limitation
让 unregister 真停子进程不可观测"。

**master 实施**：spawn task #4（commit `d039cb1`）双修——
1. `_refresh_locked` 加 diff-close 段：扫
   `session_pool.known_server_names() - {c.name for c in configs}`，逐个 close
2. `McpSessionEntry.pid` 字段 + `McpSessionPool.get_pid()` /
   `known_server_names()` public API
3. fatal 解析失败时跳过 diff-close（防一次手抖关所有 MCP，Codex F1 high-1）
4. close 抛错改 `log.warning` 留痕（不再 silent，Codex F1 medium-1）

**spec v3 标 xfail 的 L1.4 case 在 master 已转 PASS**（`test_e2e_mcp_local_stub.py`
不再含 xfail mark）。

#### 实施分歧 #3：fixture leak 治本 + 兜底（commit `6269f3b`）

**spec v3 #5 medium 处理**：保留 octo_harness_e2e teardown `except: pass` 兜底；
spawn follow-up 加 psutil leak detection。

**master 实施**：spawn task #3（commit `6269f3b`）治本+兜底双层——
- **治本**：`_close_entry_unlocked` / `close_all` 改 collect-and-raise（让
  stdio 子进程关闭失败 surface 出来，不 silent）；`octo_harness_e2e` teardown
  **移除 except 兜底**，shutdown 错误自然上抛让 pytest 标 ERROR
- **兜底**：`_assert_no_stub_subprocess_leak` autouse fixture（cmdline 含
  `stub_server.py` → raise）+ `test_subprocess_leak_detection.py` 3 个 self-test
- 新增 dep `psutil>=5.9`

#### 实施分歧 #4：F087 follow-up 顺带治本（commit `ed8965f`）

不直接属于 F089 范围，但同期 spawn task #2 治本了 F087 域 #7/#8 永久 SKIP 真因：
1. `delegate_task` 加进 `CoreToolSet.default()`（与 720d045 给 graph_pipeline
   的处理对称）—— 让 LLM 第一轮拿到完整 schema，无需走 tool_search promote 两跳链路
2. `OctoHarness` lifespan 时序竞态修复——`GraphPipelineTool` 构造提前到
   `capability_pack.refresh()` 之前，避免 `_graph_pipeline_tool` 被快照成 None
   → `availability=UNAVAILABLE` → 永久不挂 schema

#### 测试用例命名 / 位置变化

spec v3 FR-3 描述 5 个 case 在 `test_e2e_mcp_local_stub.py`：

| spec v3 case | master 现状 |
|--------------|-------------|
| L1.1 `test_mcp_config_register_execute_happy_path` | 不存在——broker.execute 全链路覆盖移到 `test_e2e_mcp_broker.py::test_mcp_broker_execute_full_audit_chain` |
| L1.4 `test_mcp_unregister_kills_subprocess` | **保留**且不再 xfail（参 §B.3 #2）|
| L1.5 `test_mcp_namespace_isolation_two_servers` | 不存在——namespace 隔离由 unit 层（`test_mcp_session_pool.py`）覆盖 |
| 新增 | `test_mcp_pool_recovers_after_supervisor_death`（supervisor 死 → reconnect 防回归）|
| 新增 | `test_subprocess_leak_detection.py` 3 self-test |

**spec v3 的 5 case 数量约束被打破**——master 实际是 1（test_e2e_mcp_local_stub）
+ 2（test_e2e_mcp_broker） + 3（leak self-test） = 6 个 e2e + 多个 unit。

总体：**spec v3 是计划文档，master 实施在 spawn task 阶段叠加了 4 条治本路径**，
原计划"5 个 e2e_smoke case + 1 个 manual gate fallback"扩展为"6 个 e2e + 4 条
治本生产代码改动 + 新 dep psutil"。后续 review F089 演进史**以 master commit
为准**，本 spec 是 P1-P5 阶段的过程留档。
