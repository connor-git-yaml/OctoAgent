# F089 实施 Plan: MCP E2E Testing — Local Stub + Vendor Manual Gate

**Spec**: `spec.md` v2（339 行）
**总工作量**: ~12h（1.5 天）
**生产代码改动**: 0
**关键决策**: §6.1 走 mcp_registry config-driven，不走 mcp_installer.install

---

## 1. Phase 时序与依赖

```
P1 Spec ✅ ─────────────────┐
                             │
P2 Plan + Tasks ─────────────┼─→ P3 Implement L1 ─→ P5 Codex Review ─→ P6 Verify+Commit
                             │       ↓                    ↑
                             │   P3.0 Spike (1h)          │
                             │       ↓                    │
                             │   P3.1 Stub server         │
                             │       ↓                    │
                             │   P3.2 Conftest 扩展        │
                             │       ↓                    │
                             │   P3.3 5 case              │
                             │                            │
                             └─→ P4 Implement L2 ─────────┘
```

**P3 / P4 可并行**——独立文件，无依赖。但本次单人单线串行做。

## 2. 关键里程碑（Gates）

| Gate | 触发 | 通过条件 |
|------|------|---------|
| GATE_SPIKE | P3.0 spike 完成 | 写 10 行最简 stub + save_config + refresh，确认 broker.discover 含目标工具；不通过 → 回到 spec 调整 §6.1 |
| GATE_L1 | P3.3 5 case 完成 | 5 case 全 PASS in < 10s，无子进程泄漏 |
| GATE_L2 | P4 完成 | host-key fallback 测试 PASS，默认 SKIP 行为不变 |
| GATE_REVIEW | P5 Codex impl review 完成 | 0 high finding，medium ≤ 3 全部处理 |
| GATE_COMMIT | P6 verify 完成 | 全量 pytest 0 regression vs F088 baseline |

## 3. 实施策略

### P3.0 Spike（关键 risk 缓解，单独阶段）

**目的**：在写完整 stub server 前，先用 10 行最简 echo server 验证 §6.1 假设：

```python
# 一次性 spike，跑完即弃
import json, sys
while True:
    line = sys.stdin.readline()
    if not line: break
    msg = json.loads(line)
    if "id" not in msg: continue  # FR-1 notification handling
    method = msg["method"]
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name":"spike","version":"0.1"}}
    elif method == "tools/list":
        result = {"tools": [{"name":"spike_echo","inputSchema":{"type":"object"}}]}
    else:
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"error":{"code":-32601,"message":"unknown"}})+"\n"); sys.stdout.flush(); continue
    sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":result})+"\n"); sys.stdout.flush()
```

跑 spike：
```python
config = McpServerConfig(name="spike", command="python", args=["/abs/spike.py"], enabled=True)
mcp_registry.save_config(config)
await mcp_registry.refresh()
assert "spike" in [s.server_name for s in mcp_registry.list_servers()]
assert mcp_registry.list_servers()[0].status == "available"
assert "spike_echo" in [t.name for t in mcp_registry.list_tools(server_name="spike")]
```

**通过**：进 P3.1。**失败**：诊断（protocolVersion 校验？stdio handshake 超时？）+ 回 spec 调整 FR-1。

### P3.1 stub_server.py（140 行）

实现 FR-1 + FR-2：
- 主循环 `while line := sys.stdin.readline()`
- 解析 JSON-RPC 2.0 message
- **检测无 id 字段（notification）→ 静默吞**（不 response）
- 路由 method：
  - `initialize` → 返 `{protocolVersion, capabilities:{tools:{}}, serverInfo}`
  - `tools/list` → 返 1 个 tool `stub_echo`
  - `tools/call` (name=stub_echo) → 按 STUB_MODE 分支：happy/error/slow
  - 其它 method → JSON-RPC error -32601
- env 切换：读 `STUB_MODE` env，slow:N 用 `time.sleep(N)`

代码风格：
- 纯 stdlib（`sys`/`json`/`os`/`time`）
- 顶部 docstring 描述协议子集 + STUB_MODE
- 无外部 import，无 type hints（保持简单）
- 文件可独立 `python tests/fixtures/mcp/stub_server.py` 跑（手动 stdin 输入测试）

### P3.2 conftest 扩展（FR-5 + 新 fixture）

`apps/gateway/tests/e2e_live/conftest.py`：
```python
# _hermetic_environment（已有）尾部加：
monkeypatch.delenv("OCTOAGENT_MCP_SERVERS_PATH", raising=False)
monkeypatch.delenv("OCTOAGENT_E2E_USE_HOST_KEY", raising=False)
```

`apps/gateway/tests/e2e_live/helpers/factories.py` 加 `stub_server_path` fixture。

新增 autouse fixture `_cleanup_mcp_subprocesses`（FR-6）：teardown 阶段强制 `mcp_session_pool.close_all()` 兜底。

### P3.3 5 case（test_e2e_mcp_local_stub.py，220 行）

每 case 通用 helper：

```python
async def _register_stub(harness, name: str, *, mode="happy") -> str:
    """save_config + refresh，返回 server_id 字符串"""

async def _unregister_stub(harness, name: str) -> None:
    """delete_config + refresh"""

async def _broker_tool_names(broker) -> set[str]:
    """broker.discover() 名字集合"""
```

5 个 case 串行写：L1.1 → L1.5（spec FR-3 已列）。

### P4 SKIP gate fallback（FR-4 + SC-7 显式覆盖）

`test_e2e_mcp_skill_pipeline.py:202` 改 SKIP gate（spec FR-4 代码）。

**SC-7 显式覆盖**：新加 1 个 unit test `test_read_openrouter_api_key_fallback_returns_key`（同文件 / unit marker）：
- monkeypatch HOME 到含 mock `~/.claude.json`（{"OPENROUTER_API_KEY": "sk-or-test-mock"}）的 tmp dir
- 调 `_read_openrouter_api_key()` 直接断言返回 "sk-or-test-mock"

这样 SC-7 验证"fallback 分支被显式测试覆盖"，不再靠 grep 计数。

### P5 Codex Impl Review

调 `codex:codex-rescue` subagent，输入：
- `spec.md` v2（含 v1 finding 闭环表）
- impl diff（`git diff`）
- 实测 `pytest -m e2e_smoke -k mcp_local_stub` 输出

让 Codex review：
- spec v2 主结构修订是否真闭环 v1 的 4 high
- impl 是否引入新 finding（特别是 stub server 协议合规、子进程泄漏）
- conftest 扩展是否破坏 F087 既有 fixture 行为

### P6 Verify + Commit

- 跑 `pytest -m e2e_smoke`：全 PASS（含 L1 5 case）
- 跑 `pytest -m e2e_full`：11 PASS / 3 SKIP（域 #5 / #7 / #8 SKIP，跟 F088 baseline 一致）
- 跑全量 `pytest -q`：3025+ passed（与 F088 baseline 0 regression）
- SC-10 验证：set `OCTOAGENT_MCP_SERVERS_PATH=/tmp/spike-host-mcp.json` → 跑 L1 → 验证文件不存在 / 未被改
- 文档：`docs/codebase-architecture/e2e-testing.md` 加 §11 MCP testing strategy
- commit message：参考 F087 followup commit（中文，标注 Codex review 闭环）

## 4. 测试策略

### 测试金字塔（本期）

```
              ┌──────────────┐
              │ L2 Manual Gate│  ← 域 #5（已存在，本期改 SKIP gate fallback）
              └──────────────┘
              ┌──────────────┐
              │   L1 Local   │  ← 5 个新 case（本期主体）
              │  Stub Server │
              └──────────────┘
              ┌──────────────┐
              │  Unit Tests  │  ← 死代码 fallback 显式覆盖（本期 +1）
              └──────────────┘
```

每层覆盖目标：
- Unit：`_read_openrouter_api_key()` fallback 分支
- L1：MCP register / spawn / discover / execute / delete 真实链路（不打 LLM）
- L2：domain #5 真打远端（manual gate，本期 fallback 加 1 行 env 易用性）

### CI / pre-commit 策略

| 触发 | 跑哪些 | budget |
|------|--------|--------|
| pre-commit hook | unit + L1 + 已有 e2e_smoke | < 30s（L1 < 10s） |
| `octo e2e smoke --loop=5` | L1 5x | < 60s |
| `octo e2e full` | L1 + L2（manual gate 决定 L2 是否真跑） | 视域 #5 manual gate |
| 全量 `pytest -q` | unit + L1 + e2e_full + 其它 | ~5min |

## 5. 验证 checklist

P6 commit 前，**必须**全部 ✅：

- [ ] L1 5 case PASS（SC-1）
- [ ] L1 总耗时 < 10s（SC-2）
- [ ] L1 进 pre-commit（SC-3）
- [ ] L2 fallback 跑通（SC-4）
- [ ] L2 默认 SKIP 不变（SC-5）
- [ ] hermetic 不变量（SC-6 + SC-10）
- [ ] 死代码 fallback 单测覆盖（SC-7）
- [ ] 0 regression（SC-8）
- [ ] e2e-testing.md 同步（SC-9）
- [ ] Codex impl review 闭环（GATE_REVIEW）

## 6. 回滚策略

L1 / L2 解耦——任一失败可独立回滚：

- L1 全部失败 → revert P3.1 + P3.3，保留 P3.2（conftest 扩展无害）+ P4
- L2 失败 → revert P4，保留 L1
- conftest 改动破坏 F087 → revert P3.2，L1 改用 monkeypatch within case 兜底

最坏情况：整体 revert 5 个文件，回到 F088 baseline，损失 0。
