# F089 Tasks: MCP E2E Testing

**Spec**: `spec.md` v2 | **Plan**: `plan.md`

每条 task 含：依赖 / 输入 / 输出 / 验收。`[P]` 标记可并行。

---

## P3.0 Spike（GATE_SPIKE）

### T-001 Spike: mcp_registry config-driven 路径可行性
**依赖**：无
**输入**：spec §6.1 + plan §3 P3.0
**输出**：临时 `/tmp/f089-spike.py` + 验证脚本（不进 git）
**步骤**：
1. 写 plan §3 中的 10 行 `spike.py`
2. 在 e2e_live 单测里手动构造 `OctoHarness` + `mcp_registry.save_config(McpServerConfig(name="spike", command="python", args=[spike_path]))` + `await mcp_registry.refresh()`
3. 断言 `mcp_registry.list_servers()[0].status == "available"` + `tools` 含 `spike_echo`
4. 断言 `broker.discover()` 含 `mcp.spike.spike_echo`（按 mcp_registry 实际命名规则核对）

**验收**：spike 通过 → 进 T-002；失败 → 诊断 protocolVersion / handshake / 命名规则；spec §6.1 / FR-1 / FR-3 调整后重跑

---

## P3.1 Stub Server

### T-002 stub_server.py 主框架
**依赖**：T-001 通过
**输入**：spec FR-1 + FR-2
**输出**：`octoagent/tests/fixtures/mcp/stub_server.py`（~140 行，纯 stdlib）
**关键实现点**：
- `def main()` 主循环 `for line in sys.stdin: handle(json.loads(line))`
- `def handle(msg)`：
  - 检测 `"id" not in msg` → 静默 return（FR-1 notification）
  - dispatch by `msg["method"]`
- `_PROTOCOL_VERSION = "2024-11-05"`（与 mcp.client.session 兼容版本）
- `_TOOL_SCHEMA = {"name":"stub_echo","description":"...","inputSchema":{"type":"object","properties":{"message":{"type":"string"}},"required":["message"]}}`
- STUB_MODE 解析：`os.environ.get("STUB_MODE","happy")`，slow:N 用 split(":")[1]
- 错误响应 helper `_error(id_, code, msg)` 返 dict

**验收**：手动跑 `echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python stub_server.py` 看到合法 response

### T-003 stub_server.py 协议合规自检（NFR-7）
**依赖**：T-002
**输入**：spec NFR-7
**输出**：在 stub_server.py docstring 附 1 段 self-test 注释，描述如何手动触发 notification + tools/list 不污染流的 case
**验收**：人工 review 通过

---

## P3.2 Conftest 扩展

### T-004 conftest 加 hermetic env 清理 [P]
**依赖**：无（独立改动）
**输入**：spec FR-5
**输出**：`apps/gateway/tests/e2e_live/conftest.py` `_hermetic_environment` 尾部加 2 行 monkeypatch.delenv
**验收**：跑 `pytest -m e2e_smoke -q`（已有套件）依然 PASS（不破 F087）

### T-005 helpers/factories.py 加 `stub_server_path` fixture [P]
**依赖**：T-002
**输入**：spec FR-3 fixture 入口
**输出**：`apps/gateway/tests/e2e_live/helpers/factories.py` 加 `stub_server_path` fixture + 加入 `__all__`
**验收**：fixture import 路径 `Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "mcp" / "stub_server.py"` 存在

### T-006 conftest 加 `_cleanup_mcp_subprocesses` autouse fixture
**依赖**：T-004
**输入**：spec FR-6 + R-2
**输出**：conftest.py 新加 autouse fixture，teardown 阶段强制 `app.state.mcp_session_pool.close_all()`（容错：app.state 没 mcp_session_pool 就 skip）
**验收**：与现有 autouse fixture 不冲突；F087 套件不回归

---

## P3.3 5 个 e2e Case

### T-007 测试文件 + 共用 helper
**依赖**：T-005, T-006
**输入**：spec FR-3
**输出**：`apps/gateway/tests/e2e_live/test_e2e_mcp_local_stub.py` 头部（imports / pytestmark / 共用 helper `_register_stub` / `_unregister_stub` / `_broker_tool_names` / `_get_subprocess_pid`）

```python
pytestmark = [pytest.mark.e2e_smoke, pytest.mark.e2e_live]
```

**验收**：文件可被 pytest collect（`pytest --collect-only`）

### T-008 L1.1 happy path
**依赖**：T-007
**输入**：spec FR-3 L1.1
**输出**：`test_mcp_config_register_execute_happy_path` 函数
**断言**（≥ 4 独立点）：
- `mcp_registry.list_servers()[0].status == "available"`
- `f"mcp.stub_a.stub_echo"` 或 mcp_registry 实际命名规则的 tool name 在 `broker.discover()` 中
- `broker.execute(...)` 返回 ToolResult.is_error=False + output 含 `"echo: hello"`
- `delete_config + refresh` 后 `broker.discover()` 不含该工具

### T-009 L1.2 invalid command
**依赖**：T-007
**输入**：spec FR-3 L1.2
**输出**：`test_mcp_invalid_command_fails_cleanly`
**断言**（≥ 2 独立点）：
- `mcp_registry.list_servers()[0].status == "error"` + `error` 字段非空
- `broker.discover()` 不含 stub_echo

### T-010 L1.3 tool error propagates
**依赖**：T-007
**输入**：spec FR-3 L1.3
**输出**：`test_mcp_execute_tool_error_propagates`
**断言**（≥ 2 独立点）：
- env={"STUB_MODE": "error"} → broker.execute 返回 ToolResult.is_error=True
- 错误信息 / output 含 "stub error" 字符串

### T-011 L1.4 delete_config kills subprocess
**依赖**：T-007
**输入**：spec FR-3 L1.4 + R-2
**输出**：`test_mcp_delete_config_kills_subprocess`
**断言**（≥ 2 独立点）：
- 注册后通过 `psutil` / `mcp_session_pool` 内部 attr 抓子进程 pid（implementation detail：可能要走 `mcp_session_pool._pool` 私有 attr，能拿到就拿）
- delete_config + refresh 后 `os.kill(pid, 0)` raise OSError ESRCH（pid 不存在）

**fallback**：如果 mcp_session_pool 不暴露 pid，断言 broker.discover() 不含 + 显式 sleep 0.5s 后再断言（容错路径，记入 R-2 风险已知 limitation）

### T-012 L1.5 namespace isolation
**依赖**：T-007
**输入**：spec FR-3 L1.5
**输出**：`test_mcp_namespace_isolation_two_servers`
**断言**（≥ 3 独立点）：
- 两 config 注册后 `broker.discover()` 含两条独立工具名
- `broker.execute("mcp.stub_a.stub_echo", message="A")` → 返回 "echo: A"（按 stub-a 的 STUB_MODE）
- `broker.execute("mcp.stub_b.stub_echo", message="B")` → 返回 "echo: B"（按 stub-b 的 STUB_MODE，可不同 mode 验证隔离）

### T-013 L1 套件总耗时验证
**依赖**：T-008 ~ T-012
**输入**：spec NFR-1 / SC-2
**输出**：实测 `pytest -m e2e_smoke -k mcp_local_stub --durations=10`，记录每 case 耗时 + 总耗时
**验收**：总耗时 < 10s

---

## P4 L2 SKIP Gate Fallback

### T-014 改 SKIP gate（FR-4）[P]
**依赖**：无（与 P3 独立）
**输入**：spec FR-4
**输出**：`test_e2e_mcp_skill_pipeline.py:202` 加 fallback 分支（spec 已给代码）
**验收**：
- unset env → SKIP（默认 CI 行为）
- `OCTOAGENT_E2E_PERPLEXITY_API_KEY=sk-or-...` → 跑通
- `OCTOAGENT_E2E_USE_HOST_KEY=1`（且 ~/.claude.json 含 sk-or- key） → 跑通

### T-015 死代码 fallback unit test（SC-7）[P]
**依赖**：T-014
**输入**：spec SC-7（v2 修正版）
**输出**：在 `test_e2e_mcp_skill_pipeline.py` 文件加 1 个 unit test：
```python
def test_read_openrouter_api_key_fallback_returns_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        '{"some_section": {"OPENROUTER_API_KEY": "sk-or-test-mock"}}',
        encoding="utf-8",
    )
    from apps.gateway.tests.e2e_live.test_e2e_mcp_skill_pipeline import _read_openrouter_api_key
    assert _read_openrouter_api_key() == "sk-or-test-mock"
```
**验收**：unit test PASS；`grep -rn _read_openrouter_api_key octoagent/` 至少 3 处（定义 + FR-4 fallback + 本测试）

---

## P5 Codex Impl Review

### T-016 Codex adversarial impl review
**依赖**：T-013, T-015
**输入**：
- `spec.md` v2（含附录 B finding 闭环表）
- `git diff`（5+ 文件改动）
- 实测 `pytest -m e2e_smoke -k mcp_local_stub` 输出
**输出**：`.specify/features/089-mcp-e2e-testing/codex-impl-review.md`（finding 列表 + 处理决策）
**验收**：0 high；medium ≤ 3 全部处理；low 可 ignored 但 commit message 注明

### T-017 处理 Codex finding（迭代）
**依赖**：T-016
**输入**：T-016 finding
**输出**：按 finding 改代码 / spec / 文档；low ignored 写明理由
**验收**：T-016 重跑 GATE_REVIEW PASS

---

## P6 Verify + Doc + Commit

### T-018 全量回归 [P]
**依赖**：T-017
**输入**：spec SC-8
**输出**：`pytest -q` 完整输出 → `/tmp/f089-regression.log`
**验收**：3025+ passed（与 F088 baseline 持平），3 个 pre-existing fail 不增

### T-019 SC-10 hermetic env 验证 [P]
**依赖**：T-017
**输入**：spec SC-10
**步骤**：
1. `touch /tmp/f089-spike-host-mcp.json` 写入 invalid JSON
2. `OCTOAGENT_MCP_SERVERS_PATH=/tmp/f089-spike-host-mcp.json pytest -m e2e_smoke -k mcp_local_stub`
3. 验证 `cat /tmp/f089-spike-host-mcp.json` 与跑前一致（mcp_registry 不读它，因为 conftest delenv 了）

**验收**：跑后内容 sha256 与跑前一致

### T-020 docs/codebase-architecture/e2e-testing.md 同步
**依赖**：T-017
**输入**：spec FR-7
**输出**：在 `e2e-testing.md` 现有内容后追加 §11 "MCP testing strategy" 章节（spec FR-7 已列结构）
**验收**：文档新章节含 L1 / L2 / long-term 升级触发条件

### T-021 commit
**依赖**：T-018, T-019, T-020
**输入**：所有变更 + spec v2 + plan + tasks + Codex review 结论
**输出**：1 个 commit
**commit message**（草稿）：
```
test(e2e_live): F089 MCP local stub e2e + 域 #5 host-key fallback

围绕 MCP 主链路 CI 覆盖 + 域 #5 易用性 4 条改动，全部 0 生产代码。

## L1 Local Stub MCP Server e2e（5 个 case，e2e_smoke）

补全 MCP register / spawn / discover / execute / delete 主链路 CI 覆盖。
走 mcp_registry config-driven 路径（v1 spec 误假设 install_source="local" 存在，
Codex review 拒绝主结构后改走 save_config + refresh + delete_config）。

- L1.1 happy path：register → broker.discover → execute → delete
- L1.2 invalid command：spawn 失败 → status="error"
- L1.3 STUB_MODE=error：tool error 透传
- L1.4 delete_config kills subprocess：pid 真消失
- L1.5 namespace isolation：两 stub 独立工具名

## L2 域 #5 SKIP gate fallback

OCTOAGENT_E2E_USE_HOST_KEY=1 → 自动从 ~/.claude.json 读 OPENROUTER_API_KEY，
本地 1 行跑通；trust boundary 不变（CI 默认 SKIP）。

## hermetic env 扩展（FR-5）

conftest 清 OCTOAGENT_MCP_SERVERS_PATH + OCTOAGENT_E2E_USE_HOST_KEY 两条 env，
防 L1 跑时读宿主 mcp-servers.json + 防 L2 fallback 在 CI silent 激活。

## 救活死代码 + 显式 fallback 单测

_read_openrouter_api_key() 定义但 SKIP gate 没用——本次救活 + 加 unit test
显式覆盖 fallback 分支（SC-7 验证）。

## Codex Adversarial Review 闭环（spec v1→v2 + impl）

spec v1：4 high / 3 medium / 1 low 全闭环（spec.md 附录 B 详表）
- v1 主结构错（install_source="local" 不存在）→ v2 改 mcp_registry config-driven
- v1 hermetic env 漏 OCTOAGENT_MCP_SERVERS_PATH → v2 FR-5 闭环
- v1 stub 漏 notification 处理 → v2 FR-1 显式
- v1 crash_after 不可靠 → v2 删

impl review：见 codex-impl-review.md

## 验证

pytest -m e2e_smoke：N+5 PASS in <Ns
pytest -m e2e_full：11 P / 3 SKIP（与 F088 baseline 一致）
全量 pytest -q：3025+ PASS（0 regression）
SC-10：OCTOAGENT_MCP_SERVERS_PATH 预设 → L1 不读宿主，sha256 一致

不需要发 spawn task 跟踪——本次实施已闭环 spec + Codex review 全部 finding。
```

**验收**：commit 完成 + git status 干净 + pre-commit hook（含 L1 5 case）PASS

---

## 任务依赖图

```
T-001 spike
   │
   ├─→ T-002 stub framework
   │     ├─→ T-003 docstring
   │     └─→ T-005 fixture path [P]
   │
   ├─→ T-004 conftest env [P]
   ├─→ T-006 cleanup fixture
   │
   └─→ T-007 test file header
         ├─→ T-008 L1.1
         ├─→ T-009 L1.2
         ├─→ T-010 L1.3
         ├─→ T-011 L1.4
         ├─→ T-012 L1.5
         └─→ T-013 总耗时

T-014 SKIP gate [P]
   └─→ T-015 fallback unit test [P]

(T-013 + T-015) ──→ T-016 Codex review ──→ T-017 处理 finding
                                                    │
                                                    ├─→ T-018 全量回归 [P]
                                                    ├─→ T-019 SC-10 [P]
                                                    └─→ T-020 文档
                                                          │
                                                          └─→ T-021 commit
```

[P] = 可并行；其它串行。

## 任务总览

| Phase | Task # | 内容 | 时长估 |
|-------|--------|------|--------|
| P3.0 | T-001 | Spike | 1h |
| P3.1 | T-002, T-003 | Stub server | 2h |
| P3.2 | T-004, T-005, T-006 | Conftest 扩展 | 1h |
| P3.3 | T-007 ~ T-013 | 5 case + 验证 | 2.5h |
| P4 | T-014, T-015 | L2 fallback | 1.5h |
| P5 | T-016, T-017 | Codex review + 处理 | 1.5h |
| P6 | T-018 ~ T-021 | Verify + 文档 + commit | 1.5h |
| **总计** | 21 tasks | | **~11h** |
