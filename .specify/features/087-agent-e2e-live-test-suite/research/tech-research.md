# 技术调研报告: Agent E2E Live Test Suite (Feature 087)

**特性分支**: `087-agent-e2e-live-test-suite`
**调研日期**: 2026-04-30
**调研模式**: 在线（代码库 + Hermes 参考）
**产品调研基础**: 无（独立模式 — 本次技术调研未参考产品调研结论，直接基于需求描述执行）

## 1. 调研目标

**核心问题**:
- A2A-Lite 在主线是否仍活跃？是否值得作为 13 能力域之一？
- e2e 启动时如何在 tmp 目录下复用真实的 Codex OAuth token（不污染日常实例）？
- Perplexity MCP 集成路径与敏感信息（API key）注入方式
- `gateway/main.py` lifespan 600+ 行如何抽离成可被 e2e 复用的 OctoHarness
- Hermes 两条 autouse fixture 移植到 OctoAgent 时具体清哪些 module 单例
- pytest marker / pre-commit hook 如何落地（仓库当前无 hook 体系）
- 真实 LLM 调用的 timeout / retry 应在哪一层加

**需求范围（来自需求描述）**:
- 13 个能力域 e2e（含 A2A）；本地 pre-commit 跑；CI 不跑
- 真实 GPT-5.5 think-low via Codex OAuth；真实 Perplexity MCP
- 不做 mock；失败重试 1 次；单 LLM call timeout 120s

## 2. A2A-Lite 主线现状（最关键）

### 结论：**A2A 仍在主线活跃，应保留为 13 能力域之一**。

证据：
- `octoagent/packages/core/src/octoagent/core/models/__init__.py:6-11` 顶层导出
  `A2AConversation` / `A2AConversationStatus` / `A2AMessageDirection` / `A2AMessageRecord`
- `octoagent/packages/core/src/octoagent/core/models/a2a_runtime.py:1-50` 定义 durable 模型
  （`MainAgentSession → WorkerSession` 的载体），有 `task_id` / `work_id` / `context_frame_id`
  / `trace_id` / `metadata` 完整字段 → 是真实持久化模型，非占位
- `apps/gateway/src/octoagent/gateway/services/delegation_plane.py:1-50` 引用
  `DispatchEnvelope` / `Work` / `WorkLifecyclePayload` / `OrchestratorRequest` —— A2A
  envelope 的运行时载体，由 DelegationPlaneService 在 main.py:616-622 实例化注入
  CapabilityPackService
- Blueprint `docs/blueprint/api-and-protocol.md:26-40` 仍把 A2A-Lite 列为 §10.2 协议

### e2e 触发路径（推断 [推断]）

worker A 给 worker B 发 envelope 在 OctoAgent 里实质是：
1. 主任务在 worker A 进入 turn → LLM 决策调 `delegate.create_subtask` 或类似
   delegation 工具
2. DelegationPlaneService 创建 Work + 写 A2AConversation，向 worker B 投递
   DispatchEnvelope
3. worker B 起 AgentSessionTurn，消费 envelope → 工具调用 → 写 A2AMessageRecord 回流

**spec 阶段需要确认**：13 能力域中"A2A 必测"的具体测点是什么？建议至少覆盖：
- 父子任务 delegation（delegate 工具触发 → 子 task 真起 + 完成 + 结果回传父）
- A2AConversation 持久化字段写入正确（status / message_count）
- 子任务失败时父任务收到 ERROR envelope

## 3. Codex OAuth Profile 隔离方案

### 关键问题
`CredentialStore` 默认路径 **硬编码** `Path.home() / ".octoagent" / "auth-profiles.json"`：
- `packages/provider/src/octoagent/provider/auth/store.py:32` —
  `_DEFAULT_STORE_DIR = Path.home() / ".octoagent"`
- `packages/provider/src/octoagent/provider/auth/store.py:71-83` —
  构造函数接受 `store_path: Path | None = None`，传 None 才回退到默认路径

**没有 OCTOAGENT_HOME 这种 env**：`packages/core/src/octoagent/core/config.py` 只有
`OCTOAGENT_DATA_DIR` / `OCTOAGENT_DB_PATH` / `OCTOAGENT_ARTIFACTS_DIR`；
main.py:114 用的是 `OCTOAGENT_PROJECT_ROOT`。

### 推荐方案：双轨 — env 重定向 + 显式注入

**方案 A（推荐）**：e2e fixture 显式构造 CredentialStore 注入 ProviderRouter

```python
# tests/e2e/conftest.py
@pytest.fixture
def real_codex_credential_store(tmp_path, monkeypatch):
    # 1. 复制宿主 auth-profiles.json 到 tmp（只读真实 token，不写回宿主）
    src = Path.home() / ".octoagent" / "auth-profiles.json"
    if not src.exists():
        pytest.skip("No real Codex OAuth profile available")
    dst = tmp_path / "auth-profiles.json"
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o600)
    return CredentialStore(store_path=dst)
```

OctoHarness `bootstrap()` 接受 `credential_store` 参数，注入到 `ProviderRouter(...,
credential_store=...)`（router_router.py:73-78 已支持）。

**方案 B（备选）**：增加 `OCTOAGENT_HOME` env 让 `_DEFAULT_STORE_DIR` 优先读它

需要改生产代码（`store.py:32`），范围比方案 A 大；除非 OctoHarness 抽离时本来要改
`config.py` 引入 home 概念，否则**优先 A**。

### Token 刷新关注点
ProviderRouter 内部 OAuthResolver 每次 resolve 时**从 store 现读凭证**
（provider_router.py:14-15 注释明确说），刷新会落盘到注入的 tmp store 文件。
e2e 跑完 tmp 自动清理，**真实 OAuth token 在宿主的 auth-profiles.json 不会被 e2e 写**
（除非 e2e 主动 write — 不会发生）。

## 4. Perplexity MCP 集成路径

### 调用链
- 工具入口：`apps/gateway/src/octoagent/gateway/services/builtin_tools/mcp_tools.py:1-60`
  - 6 个工具：`mcp.servers.list` / `mcp.tools.list` / `mcp.tools.refresh` /
    `mcp.install` / `mcp.install_status` / `mcp.uninstall`
  - `mcp.install` entrypoints **只允许 agent_runtime**（不允许 web 直接调）→ e2e
    必须通过 LLM 触发，不能直接 HTTP POST
- 安装服务：`apps/gateway/src/octoagent/gateway/services/mcp_installer.py`
  - `_DEFAULT_MCP_SERVERS_DIR = Path.home() / ".octoagent" / "mcp-servers"`
    （L29，又一处硬编码 home — e2e 需关注）
  - `_DEFAULT_INSTALLS_PATH = Path("data/ops/mcp-installs.json")` （L30，相对路径）
  - **子进程 env 安全基线**（L37-51）：`_SAFE_ENV_KEYS = (PATH, HOME, USER, LANG,
    LC_ALL, TERM, SHELL, TMPDIR)` → **不会自动透传 OPENROUTER_API_KEY**，必须通过
    `user_env` 参数显式注入

### e2e 安全注入方案
1. fixture 从宿主 `~/.octoagent/data/ops/mcp-servers.json` 读 OPENROUTER_API_KEY
   值（明文存放，需 redact 后再写日志）
2. 通过 mcp.install 工具调用时的 `env` 参数注入：
   `{"OPENROUTER_API_KEY": "<redacted-but-real>"}`
3. 测试断言序列：
   - LLM turn 1 → `mcp.install(package="openrouter-perplexity", env={...})` →
     `install_id` 返回，status = installing
   - 轮询 `mcp.install_status(install_id)` 直到 `installed`
   - LLM turn 2 → 调用 `perplexity_search`（动态注册到 ToolBroker 后）→ 真实搜索结果

### 风险
- mcp_servers_dir 也是 `Path.home()` 硬编码 → e2e 跑装包会污染宿主 `~/.octoagent/mcp-servers/`
  **建议 spec 阶段决策**：
  - 选项 1：给 McpInstallerService 加 `mcp_servers_dir` 注入参数（最干净）
  - 选项 2：e2e 用 monkeypatch 改 `_DEFAULT_MCP_SERVERS_DIR`
  - 选项 3：接受污染，每次 e2e 装到固定 tmp 路径并清理

## 5. OctoHarness 抽离方案

### lifespan 行号映射（`gateway/main.py:289-892`，共 ~600 行）

| 段落 | 行号 | 抽离去向 |
|------|------|---------|
| project_root + duplicate_roots warn + frontdoor + update_service | 291-303 | `OctoHarness._bootstrap_paths` |
| Store 初始化 + memory_db + project migration | 306-314 | `OctoHarness._bootstrap_stores` |
| ToolRegistry scan + skeleton + startup_records + SnapshotStore | 321-353 | `OctoHarness._bootstrap_tool_registry_and_snapshot` |
| OwnerProfile sync | 355-371 | `OctoHarness._bootstrap_owner_profile` |
| MCP dirs + telegram_service + approval_manager + tool_broker | 373-445 | `OctoHarness._bootstrap_runtime_services` |
| ProviderRouter + LLMService + alias_registry | 447-508 | `OctoHarness._bootstrap_llm`（接受 credential_store / llm_adapter 注入） |
| capability_pack + skill_discovery + pipeline_registry | 515-543 | `OctoHarness._bootstrap_capability_pack` |
| MCP registry + installer | 546-565 | `OctoHarness._bootstrap_mcp` |
| skill_runner + delegation_plane + task_runner | 575-637 | `OctoHarness._bootstrap_executors` |
| auth_config_drift + graph_pipeline_tool + observation_routine | 641-724 | `OctoHarness._bootstrap_optional_routines` |
| watchdog scheduler + operator services + control_plane | 728-821 | `OctoHarness._bootstrap_control_plane` |
| shutdown 段 | 832-891 | `OctoHarness.shutdown()` |

### 抽离后 lifespan
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    harness = OctoHarness(project_root=_resolve_project_root())
    await harness.bootstrap(app=app)
    try:
        yield
    finally:
        await harness.shutdown(app=app)
```
**lifespan 从 ~600 行降到 ~10 行**；OctoHarness 自身约 600 行（搬运为主，新增的
仅 dataclass 包装 + DI 接口）；测试 helper（`_build_real_user_profile_handler` /
`_ensure_audit_task` / `_insert_turn_events`）合并为 OctoHarness 的辅助类方法或
`OctoHarness.test_factory()` classmethod。

**净 LOC 影响**：lifespan -590；新增 OctoHarness +650（含 docstring + DI 钩子）；
测试 helper 合并 -200（推断 [推断]，需 spec 阶段精确清点散落 helper）；**净增约 -140 行**
+ 大幅提升可测性。

### DI 钩子（e2e 必需）
- `credential_store: CredentialStore | None`（第 3 节）
- `llm_adapter: MessageAdapter | None`（强制注入真实 ProviderRouter；echo mode 走
  原 OCTOAGENT_LLM_MODE=echo 路径不变）
- `mcp_servers_dir: Path | None`（第 4 节）
- `data_dir: Path | None`（避免读 `OCTOAGENT_DATA_DIR` env，e2e 直接传入 tmp）

## 6. Hermes Fixture 移植清单

### Hermes 模式回顾（`_references/opensource/hermes-agent/tests/conftest.py`）
- `_hermetic_environment`（L232-294，autouse）：
  - 清所有 `*_API_KEY` / `*_TOKEN` / `*_SECRET` 等凭证 env（约 60+ 个名单 + 后缀匹配）
  - 重定向 `HERMES_HOME` 到 tmp（**不动 HOME** — 子进程会炸）
  - 固定 TZ=UTC / LANG=C.UTF-8 / PYTHONHASHSEED=0
  - 屏蔽 AWS IMDS（`AWS_EC2_METADATA_DISABLED=true` 等）
- `_reset_module_state`（L321-403，autouse）：清以下模块的 module-level 单例
  - `tools.approval._session_approved` / `_session_yolo` / `_permanent_approved` / 等
  - `tools.interrupt._interrupted_threads`
  - `gateway.session_context` 9 个 ContextVar
  - `tools.env_passthrough` / `tools.credential_files` 的 ContextVar
  - `tools.file_tools._read_tracker` / `_file_ops_cache`

### OctoAgent 需 reset 的 module 清单（基于代码库扫描 [推断]）

| Module 路径 | 单例 / ContextVar | Reset 方式 |
|-------------|------------------|----------|
| `apps/gateway/src/octoagent/gateway/harness/tool_registry.py` | `_REGISTRY` 全局单例（`get_registry()`） | `_REGISTRY = None` 触发重建 |
| `apps/gateway/src/octoagent/gateway/harness/snapshot_store.py` | 无（实例化注入），但 `app.state.snapshot_store` 跨测残留 | 由 OctoHarness lifecycle 管理 |
| `apps/gateway/src/octoagent/gateway/services/agent_context.py` | `AgentContextService.set_llm_service` / `set_provider_router` 是 classmethod 改类属性 | reset 为 None |
| `apps/gateway/src/octoagent/gateway/harness/approval_gate.py`（Hermes 类比） | session allowlist `_session_approvals` | 清字典 |
| `apps/gateway/src/octoagent/gateway/harness/delegation_manager.py` | `_active_children` | 清字典 |
| `packages/policy/.../approval_override_store.py` | `ApprovalOverrideCache` 实例无单例（注入），但全局 cache 需 reset | 实例由 harness 管理 |
| `packages/provider/.../auth/store.py` | `CredentialStore` 实例（注入到 router） | 由 harness 管理 |
| 凭证 env 名单 | OPENAI_API_KEY / OPENROUTER_API_KEY / TELEGRAM_BOT_TOKEN / 等 | 抄 Hermes `_CREDENTIAL_NAMES` 子集 |
| OctoAgent 行为 env | OCTOAGENT_LLM_MODE / OCTOAGENT_PROJECT_ROOT / OCTOAGENT_DATA_DIR / OCTOAGENT_DB_PATH / OCTOAGENT_ARTIFACTS_DIR / OCTOAGENT_VERIFY_URL / OCTOAGENT_GATEWAY_PORT / OCTOAGENT_EVENT_PAYLOAD_MAX_BYTES | 全清，e2e fixture 显式 set |

**spec 阶段必须做的精确扫描**：grep `^_[a-z_]+ *=` 在 `apps/gateway/src/octoagent/gateway/harness/`
和 `apps/gateway/src/octoagent/gateway/services/`，找出所有 module-level mutable
单例（dict / set / list），逐个评估是否需要 reset。

### TZ 决策
Hermes 用 UTC；OctoAgent 用户在中国时区（USER.md 含 timezone 字段）。e2e 建议**保持
用户时区**（不强制 UTC），因为 OwnerProfile sync 会读时区影响 system prompt，强制
UTC 会导致 e2e 测的不是真实用户语境。

## 7. pytest / pre-commit 集成

### 现状
- `octoagent/pyproject.toml:63-80`: `[tool.pytest.ini_options]` 已配置 testpaths +
  asyncio_mode=auto；**未注册任何 markers**；**无 pytest-rerunfailures**
- `octoagent/conftest.py:1-50`: 只有 `tmp_db_path` / `tmp_artifacts_dir` / `db_conn`
  fixture + `pytest_sessionfinish` thread cleanup hook
- 仓库**无** `.pre-commit-config.yaml`、**无** `.githooks/pre-commit`、**无**
  `repo-scripts/` 下的 hook（实际 grep 验证）
- 已装 `pytest-xdist>=3.8.0`（pyproject.toml:28），但**默认不开 -n auto**

### 推荐方案

**marker 注册** — 加到 `pyproject.toml`：
```toml
[tool.pytest.ini_options]
markers = [
    "e2e_smoke: 关键路径冒烟（pre-commit 跑，<60s）",
    "e2e_full: 全量 13 能力域（手动触发，<10min）",
    "e2e_live: 真实 LLM/MCP 调用（与 e2e_smoke/full 正交）",
]
```

**失败重试** — 加 `pytest-rerunfailures>=14.0` 到 dev 依赖；e2e marker 上加
`@pytest.mark.flaky(reruns=1, reruns_delay=2)`（仅 e2e；单元测试**不**加 — 单测
应稳定）。

**pre-commit hook 实现**（推荐 native hook，不引入 pre-commit framework）：
- 路径：`.githooks/pre-commit`（shell 脚本）+ 项目 `Makefile` 加 `make install-hooks`
  执行 `git config core.hooksPath .githooks`
- 内容（草案）：
  ```bash
  #!/usr/bin/env bash
  set -e
  if [ "$OCTO_SKIP_E2E" = "1" ]; then exit 0; fi
  cd octoagent
  uv run pytest -m e2e_smoke --maxfail=1 -q
  ```
- 提供 `OCTO_SKIP_E2E=1` 紧急 bypass（不可滥用，spec 阶段定 governance）

**为什么不用 pre-commit framework**：仓库当前完全无 pre-commit 配置，引入额外
依赖+缓存目录+ python venv 隔离反而增加复杂度；shell hook 直接跑 `uv run pytest`
最简单。

## 8. Timeout / Retry 工程实现

### 当前 timeout 配置点
- `packages/provider/.../provider_router.py:75` — `ProviderRouter(..., timeout_s: float = 60.0)`
  → httpx.AsyncClient 传 `Timeout(timeout_s, connect=10.0)`
- `packages/provider/.../provider_client.py` — 单次 LLM 调用走 router 的 http_client
  → 继承 60s timeout

### 需求：单 LLM call 120s timeout

**推荐**：e2e fixture **覆盖 ProviderRouter 构造参数** `timeout_s=120.0`，**不动生产
代码**：
```python
provider_router = ProviderRouter(
    project_root=tmp_project_root,
    credential_store=real_codex_credential_store,
    timeout_s=120.0,  # e2e 专用
)
```

### 失败重试
- 生产层：`FallbackManager(primary, fallback)` 已实现 primary 失败回落 fallback
- e2e 层：单 test function 的 retry 走 `pytest-rerunfailures` 的
  `@pytest.mark.flaky(reruns=1)` —— 比 try/except 自旋干净
- **不要**在 e2e 测试代码内手写 retry 循环（容易 mask 真实 race，违反 Hermes
  "patterns mask race" 原则）

### MCP 子进程 timeout
`mcp_installer.py:34-35`：`_SUBPROCESS_TIMEOUT_S = 120` / `_VERIFY_TIMEOUT_S = 15`
已经是 120s，e2e 直接复用，无需调整。

## 9. 风险与未知

| # | 风险 | 概率 | 影响 | 缓解 / 待决策 |
|---|------|------|------|-------------|
| 1 | Codex OAuth token 在 e2e 高频运行下耗尽 quota | 中 | 中 | spec 阶段定 e2e_smoke 的 LLM 调用预算（如 ≤ 5 次 / commit）；e2e_full 手动触发 |
| 2 | `Path.home() / ".octoagent" / "mcp-servers"` 硬编码导致 e2e 污染宿主 | 高 | 中 | spec 决策：选给 McpInstallerService 加 dir 注入 vs monkeypatch |
| 3 | A2A e2e 测点定义不清 | 中 | 中 | spec 阶段确认：父子 delegation / 持久化 / 错误回流 至少哪几条 |
| 4 | OctoAgent module 单例清单未精确扫描 | 中 | 高（漏一个就出现 "alone pass / together fail"）| spec 阶段做一次系统 grep + 列出全清单 |
| 5 | pre-commit hook 60s 预算不够（需求未给阈值）| 中 | 低 | spec 阶段定 smoke 子集大小（建议 3-5 条最关键测） |
| 6 | Perplexity API key 明文存放在测试 fixture | 低 | 高 | 必须用 env var 注入而非提交到仓库；fixture 从宿主 mcp-servers.json 读 |
| 7 | Telegram / 通知类工具的 e2e 真实触发会发送实际消息 | 中 | 中 | spec 阶段决策：要么用专门 e2e Telegram bot/chat（隔离），要么对通知类工具采用"准 mock"——保留协议层 e2e、最末端 send 替换 |
| 8 | 13 能力域之一是否包含 Telegram channel？需求未明确 | — | — | spec 阶段澄清 |
| 9 | OAuth token 刷新触发的 disk write 是否会影响 prefix cache snapshot | 低 | 低 | OAuth refresh 写的是 auth-profiles.json，不影响 USER.md/MEMORY.md snapshot；可忽略 |

### Hermes Reference 文件确认
- ✅ `_references/opensource/hermes-agent/tests/conftest.py` 存在，483 行，结构如调研描述
- ✅ `_references/opensource/hermes-agent/tests/e2e/conftest.py` 存在（platform mock 工具）
- ⚠️ `tests/test_toolsets.py` / `tests/tools/test_cron_prompt_injection.py` /
  `tests/tools/test_delegate.py` 未本次直接打开（spec 阶段编写实际测试时再读）

## 10. 架构方案对比（新建 OctoHarness vs 复用 lifespan）

| 维度 | 方案 A: 抽离 OctoHarness | 方案 B: e2e 直接拉起 lifespan |
|------|---------------------|---------------------------|
| 概述 | lifespan 业务逻辑搬到 OctoHarness 类，DI 钩子注入 | e2e fixture 直接 `app = create_app()` + `LifespanManager(app)` |
| 性能 | 启动同 | 启动同 |
| 可维护性 | 高（生产 / 测试同源 + 显式 DI） | 中（每次改 lifespan e2e 都可能炸） |
| 改造成本 | 高（搬运 600 行 + 写 DI） | 低（零改造） |
| e2e 注入能力 | 强（credential_store / llm / mcp_dir 都可注入） | 弱（只能改 env，硬编码 home 改不了） |
| 长期演进 | 好（OctoHarness 可被 CLI / 测试 / Standalone runner 复用）| 差（lifespan 越长越烂） |

### 推荐：方案 A

理由：
1. CredentialStore / mcp_servers_dir 硬编码 home，**不抽离就无法 e2e**
2. 散落的测试 helper（`_build_real_user_profile_handler` 等）已经是事实上的
   "OctoHarness 雏形"，只是没收敛
3. F084 已经做了 ToolRegistry / SnapshotStore / ApprovalGate / DelegationManager
   的中央化，OctoHarness 是这一脉络的自然延续
4. 长期看 CLI（如 `octo run-task`）独立运行时也需要 OctoHarness（不再依赖 FastAPI lifespan）

## 11. 设计模式推荐

1. **Builder + DI 容器**（OctoHarness）：lifespan 改 builder pattern，每个
   `_bootstrap_*` 方法返回组装好的子系统，最后 `harness.commit_to_app(app)`
2. **Hermetic Test Fixture**（autouse env / module reset）：直接抄 Hermes 的
   `_hermetic_environment` + `_reset_module_state` 双 autouse 模式
3. **Live State Two-Tier**（凭证）：宿主 auth-profiles.json 是 SoT，e2e tmp
   是只读副本 — 与 F084 SnapshotStore "frozen + live" 同构，符合既有架构语言
4. **Marker-driven Test Tier**（pytest e2e_smoke / e2e_full / e2e_live）：用
   marker 把"快"和"全"分层，pre-commit 只跑 smoke

## 12. 产品-技术对齐度

### 13 能力域覆盖（基于需求摘要 [推断]）

| 能力域 (推断) | 技术覆盖 | 关键依赖 |
|------------|---------|--------|
| 任务调度 / TaskRunner | ✅ | OctoHarness + ProviderRouter |
| Tool Broker / 工具调用 | ✅ | tool_broker + LargeOutputHandler |
| Approval / 二段式 | ✅ | ApprovalManager + override repo |
| Memory / Candidates | ✅ | MemoryConsoleService + MemoryRuntimeService |
| Skill Pipeline | ✅ | SkillRunner + PipelineRegistry |
| Delegation / A2A | ✅ | DelegationPlaneService |
| MCP（Perplexity 真实搜索）| ⚠️ | mcp_servers_dir 硬编码风险（§4 / §9 风险 #2） |
| Provider 直连 | ✅ | ProviderRouter + Codex OAuth |
| Watchdog | ✅ | WatchdogScanner + scheduler |
| Operator Inbox / Action | ✅ | OperatorActionService |
| Telegram channel | ⚠️ | 真实发消息风险（§9 风险 #7）|
| 其他 2 域 | — | spec 阶段需明确 |

### Constitution 兼容性

| 约束 | 兼容性 | 说明 |
|------|------|------|
| Durability First | ✅ | e2e 跑真 SQLite + artifacts，未 mock |
| Everything is an Event | ✅ | 真实 event_store 写入 |
| Tools are Contracts | ✅ | 不改 tool schema |
| Side-effect Two-Phase | ✅ | mcp.install 仍走 ApprovalGate（spec 决策是否在 e2e 自动 approve） |
| Least Privilege | ⚠️ | OPENROUTER_API_KEY 在 fixture 流转，需保证不落日志（structlog redact）|
| Degrade Gracefully | ✅ | echo mode 仍可用 |
| User-in-Control | N/A | e2e 是机器跑 |
| Observability | ✅ | logfire 可保留 |
| Agent Autonomy | ✅ | e2e 不引入硬编码规则 |
| Policy-Driven Access | ✅ | 走 ApprovalManager 不绕 |

## 13. 结论与建议

### 总结

技术调研确认 Feature 087 的核心路径**全部可行**，但有 **2 个生产代码硬编码**
和 **3 个 spec 阶段必决策的 open question** 必须在编 spec 时解决。

**推荐主架构**：抽离 OctoHarness（方案 A）+ Hermes 双 autouse fixture 模式 +
显式 CredentialStore / mcp_servers_dir 注入 + pytest marker 分层（e2e_smoke /
e2e_full）+ native shell pre-commit hook + pytest-rerunfailures 单次重试。

### 给 spec 阶段的 open question 清单（必须决策）

1. **A2A e2e 测点**：父子 delegation / 持久化 / 错误回流哪几条进入 13 能力域？
2. **mcp_servers_dir 隔离方式**：给生产代码加 DI（推荐）vs monkeypatch vs 接受污染？
3. **Telegram channel e2e 边界**：真发消息（用专用 bot）vs 协议层 e2e + send 末端
   替换？
4. **e2e_smoke 预算**：pre-commit 跑几条 / 多长时间上限（建议 ≤ 5 测 ≤ 60s）？
5. **module 单例 reset 全清单**：spec 阶段做一次精确 grep（不能漏，漏一个就 flake）

### 给产研汇总的建议（独立模式无产研，此处空）

无产品调研，本节略。
