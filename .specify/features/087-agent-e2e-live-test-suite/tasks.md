# Feature 087 — Tasks

**Feature ID**: 087
**Feature Slug**: agent-e2e-live-test-suite
**生成日期**: 2026-04-30
**上游制品**: spec.md (35 FR / 13 能力域) + plan.md (5 phase / 10 risk)

---

## 概览

- **总 task 数**：54
- **总工时估算**：~64-72h（约 8-9 工作日，与 plan 7-10 天相符）
- **关键路径**：T-P1-1 → T-P1-2 → T-P1-3..6 → T-P1-7 → T-P1-8 → T-P2-1 → T-P2-3 → T-P2-7 → T-P2-8 → T-P3-1..5 → T-P3-9 → T-P3-10 → T-P4-1..8 → T-P4-9 → T-P5-1 → T-P5-3 → T-P5-5 → T-P5-9 → T-P5-10 → T-P5-11
- **高风险 task 数（关联 R1-R10）**：18（其中 R1/R3/R10 各 ≥ 3 个）
- **依赖图自检**：无环，P{N} 与 P{N+1} 严格串行；P3 内部测试间可并行（不同测试文件）

---

## P1: e2e infra — OctoHarness 抽离 + DI 钩子（~2-3d）

### T-P1-1: lifespan 行号映射 + 11 段 marker 注释（[CLEANUP]，前置）
- **依赖**：无
- **工时**：~1.5h
- **DoD**：`apps/gateway/src/octoagent/gateway/main.py` 行 289-892 内插入 11 条 `# === _bootstrap_<name> START/END (lines X-Y) ===` 注释；不改任何代码；commit 单独提交便于 review
- **关联**：FR-1, R1 缓解

### T-P1-2: 新建 OctoHarness 骨架（类 + 4 DI 钩子 + 三入口签名）
- **依赖**：T-P1-1
- **工时**：~2h
- **DoD**：新建 `apps/gateway/src/octoagent/gateway/harness/octo_harness.py`；类签名与 plan §8.2 一致；`bootstrap` / `shutdown` / `commit_to_app` 方法存在但 body 为 `pass` 或 `NotImplementedError`；`uv run python -c "from octoagent.gateway.harness.octo_harness import OctoHarness"` 无 ImportError
- **关联**：FR-1, FR-2, R1 缓解

### T-P1-3: 搬运前 4 段 _bootstrap_*（paths/stores/tool_registry+snapshot/owner_profile） [并行]
- **依赖**：T-P1-2
- **工时**：~3h
- **DoD**：行 291-371 范围 4 个段落搬入 OctoHarness 对应 `_bootstrap_*` 方法；F086 全量测试仍全绿（局部跑 `uv run pytest apps/gateway/tests/ -q -x`）
- **关联**：FR-1, FR-3, R1 缓解, R5 缓解

### T-P1-4: 搬运中 4 段 _bootstrap_*（runtime_services/llm/capability_pack/mcp） [并行]
- **依赖**：T-P1-2
- **工时**：~3.5h
- **DoD**：行 373-565 范围 4 个段落搬入；mcp 段保留 `_DEFAULT_MCP_SERVERS_DIR` 行为（DI 改 P2 做）；F086 测试仍全绿
- **关联**：FR-1, FR-3, R1 缓解

### T-P1-5: 搬运后 3 段 _bootstrap_*（executors/optional_routines/control_plane） [并行]
- **依赖**：T-P1-2
- **工时**：~3h
- **DoD**：行 575-821 范围 3 段搬入；F086 测试仍全绿
- **关联**：FR-1, FR-3, R1 缓解

### T-P1-6: 搬运 shutdown 段（行 832-891）
- **依赖**：T-P1-3, T-P1-4, T-P1-5
- **工时**：~1.5h
- **DoD**：shutdown 段搬入 `OctoHarness.shutdown(app)`；`commit_to_app(app)` 一次性挂载所有 `app.state.*`；F086 测试仍全绿
- **关联**：FR-1, FR-4, R1 缓解

### T-P1-7: 改写 main.py lifespan 为 OctoHarness 调用（≤ 20 行）
- **依赖**：T-P1-6
- **工时**：~1.5h
- **DoD**：`apps/gateway/src/octoagent/gateway/main.py:lifespan` body ≤ 20 行；仅含 `harness = OctoHarness(...); await harness.bootstrap(app); harness.commit_to_app(app); yield; await harness.shutdown(app)` 等价骨架；删除原 ~590 行 inline 逻辑
- **关联**：FR-1, FR-3, SC-6, R1 缓解

### T-P1-8: byte-for-byte 等价验证 + F086 ≥ 2038 测试 0 regression
- **依赖**：T-P1-7
- **工时**：~2h
- **DoD**：`uv run pytest -q` 全量通过 ≥ 2038；启动 gateway 后 grep `app.state` 关键属性集合与 F086 基线 diff 为空（手动列 ~30 个 state attr 比对）；commit message 含 "F086 baseline 0 regression"
- **关联**：FR-3, SC-6, SC-10, R1 缓解, R5 缓解

---

## P2: 模型层 + e2e 基础设施 + 单例 reset 清单（~1-2d）

### T-P2-1: module 单例 reset 全 grep + 输出精确清单文档（最优先，R3 关键缓解）
- **依赖**：P1 完成
- **工时**：~3h
- **DoD**：执行 plan §3 Risk #3 全部 5 条 grep 命令；输出 `apps/gateway/tests/e2e_live/helpers/MODULE_SINGLETONS.md`（或 `__init__.py` docstring），列出每个单例的 module path / 变量名 / reset 方式 / import-time default；至少覆盖 plan §3 已知 8 类清单
- **关联**：FR-26, FR-27, R3 缓解

### T-P2-2: pytest markers 注册 + pytest-rerunfailures dev dep
- **依赖**：P1 完成
- **工时**：~0.5h
- **DoD**：`octoagent/pyproject.toml` 加 `[tool.pytest.ini_options].markers` 三条（`e2e_smoke` / `e2e_full` / `e2e_live`）+ dev deps 加 `pytest-rerunfailures>=14.0`；`uv sync` 成功；`pytest --markers` 输出含三条
- **关联**：FR-5, FR-6

### T-P2-3: McpInstallerService 加 mcp_servers_dir DI（生产代码改动，R10 关键缓解） [并行]
- **依赖**：T-P2-2
- **工时**：~2h
- **DoD**：`apps/gateway/src/octoagent/gateway/services/mcp_installer.py` `__init__` 加 `mcp_servers_dir: Path | None = None`；grep 替换全部 `_DEFAULT_MCP_SERVERS_DIR` 引用为 `self._mcp_servers_dir`（grep 命令保留在 commit message）；既有 mcp_installer 单测全绿（向后兼容验证）
- **关联**：FR-16, FR-17, FR-18, R10 缓解

### T-P2-4: OctoHarness._bootstrap_mcp 接入 mcp_servers_dir DI [并行]
- **依赖**：T-P2-3
- **工时**：~0.5h
- **DoD**：`OctoHarness._bootstrap_mcp` 内 `McpInstallerService(..., mcp_servers_dir=self._mcp_servers_dir)`；生产路径默认 None；F086 测试全绿
- **关联**：FR-2, FR-17

### T-P2-5: tests/fixtures/local-instance/ 脱敏模板 + .gitignore（R9 缓解）
- **依赖**：T-P2-2
- **工时**：~1.5h
- **DoD**：新建 `tests/fixtures/local-instance/octoagent.yaml.template` + `behavior/` 脱敏快照；`.gitignore` 含 `*.real`/`auth-profiles*.json`/`*API_KEY*` negative pattern；grep `API_KEY|TOKEN|SECRET` 在 fixture 目录返回空
- **关联**：FR-34, NFR-5, SC-8, R9 缓解

### T-P2-6: .env.e2e 模板 + OCTOAGENT_E2E_* env 命名规范文档
- **依赖**：T-P2-2
- **工时**：~0.5h
- **DoD**：新建 `tests/fixtures/local-instance/.env.e2e.template` 含 plan §7.2 三条 `OCTOAGENT_E2E_*` 变量注释；`.gitignore` 排除 `.env.e2e`
- **关联**：FR-33, NFR-5

### T-P2-7: e2e_live/conftest.py 双 autouse fixture 实现
- **依赖**：T-P2-1, T-P2-2
- **工时**：~3h
- **DoD**：新建 `apps/gateway/tests/e2e_live/conftest.py`；含两条 autouse：(a) `_hermetic_environment`（清 5 类凭证 env + 重定向 4 个 OCTOAGENT_* env + 不动 HOME + PYTHONHASHSEED=0）；(b) `_reset_module_state`（按 T-P2-1 清单逐个 reset）；含 30s SIGALRM 单场景 timeout 装置
- **关联**：FR-7, FR-26, R3 缓解

### T-P2-8: 主 fixture octo_harness_e2e（4 DI 钩子 + timeout 120s + max_steps 10）
- **依赖**：T-P2-4, T-P2-7
- **工时**：~2h
- **DoD**：新建 fixture `octo_harness_e2e` 在 conftest.py 或 helpers/factories.py；注入 `credential_store` / `llm_adapter` / `mcp_servers_dir=tmp/mcp-servers` / `data_dir=tmp`；`ProviderRouter(timeout_s=120.0)`；`max_steps=10`
- **关联**：FR-2, FR-12, FR-13

### T-P2-9: real_codex_credential_store fixture（OAuth profile 隔离） [并行]
- **依赖**：T-P2-7
- **工时**：~1.5h
- **DoD**：新建 `helpers/fixtures_real_credentials.py`；只读复制 `~/.octoagent/auth-profiles.json` → `tmp/auth-profiles.json`（chmod 0o600）；构造 `CredentialStore(store_path=tmp)`；宿主缺文件时 `pytest.skip(reason=...)`；不改 OCTOAGENT_HOME（避免子进程依赖坏死）
- **关联**：FR-8, NFR-5

### T-P2-10: helpers/assertions.py 断言工具集 [并行]
- **依赖**：T-P2-7
- **工时**：~2h
- **DoD**：实现 `assert_tool_called(events, name)` / `assert_event_emitted(events, type)` / `assert_writeresult_status(result, expected)` / `assert_file_contains(path, substr)` / `assert_no_threat_block(events)`；每个 helper 至少 1 个自身单测
- **关联**：FR-11

### T-P2-11: helpers/state_diff.py（sha256 工具，SC-7 支撑） [并行]
- **依赖**：T-P2-7
- **工时**：~1h
- **DoD**：`sha256_dir(path)` / `sha256_file(path)` / `module_singletons_snapshot()` 三函数；含单测；用于 SC-7 跑前后对比
- **关联**：SC-7, NFR-8

### T-P2-12: helpers/factories.py（**复制** _build_real_user_profile_handler 等到新位置）
- **依赖**：T-P2-8
- **工时**：~2h
- **DoD**：**复制**（不删除）旧 `_build_real_user_profile_handler` / `_ensure_audit_task` / `_insert_turn_events` 到 `tests/e2e_live/helpers/factories.py`；factories 函数有自身单测；**双源共存**直到 T-P5-1（与旧 `test_acceptance_scenarios.py` 一起删）
- **关联**：spec 附录 A.2
- **修复来源**：analysis.md MEDIUM-1（避免与 T-P3-6 双源验证 + T-P5-1 旧文件删除时序冲突）

### T-P2-13: Codex quota 429 → SKIP 信号（R3 缓解）
- **依赖**：T-P2-9
- **工时**：~1h
- **DoD**：在 fixture 或 `pytest_runtest_makereport` hook 内 catch `ProviderQuotaError` / HTTP 429 → `pytest.skip(reason="codex quota exhausted: ...")`；写一个 mock 429 的 sanity 测验证 SKIP 路径
- **关联**：FR-24, R3 缓解

### T-P2-14: pytest_collection_modifyitems hook 自动加 flaky marker
- **依赖**：T-P2-2
- **工时**：~0.5h
- **DoD**：conftest.py 注册 hook：所有 `e2e_smoke` / `e2e_full` 测试自动加 `@pytest.mark.flaky(reruns=1, reruns_delay=2)`；单测不加（验证：跑现有单测不应 rerun）
- **关联**：FR-6, FR-23

### T-P2-15: helpers/domain_runner.py（CLI 单跑用）
- **依赖**：T-P2-8
- **工时**：~1h
- **DoD**：`run_domain(domain_id: int)` 函数读 13 域注册表，转发到对应 pytest 节点 ID；P5 CLI 直接复用
- **关联**：FR-30

---

## P3: smoke 套件 5 域 + pre-commit hook（~2-3d）

### T-P3-1: 域 #1 工具调用基础（test_e2e_basic_tool_context.py 部分） [并行]
- **依赖**：P2 完成
- **工时**：~2.5h
- **DoD**：实现 case；≥ 2 断言点（events 含 `tool.call(name="memory.write")` + `WriteResult.memory_id` 非空 + `status="written"`）；marker `e2e_smoke + e2e_live`；单跑 PASS
- **关联**：FR-9, FR-11, SC-1

### T-P3-2: 域 #2 USER.md 全链路（test_e2e_basic_tool_context.py 部分） [并行]
- **依赖**：P2 完成
- **工时**：~2.5h
- **DoD**：≥ 2 断言（USER.md 含特定字符串 + WriteResult 返回 user_md_path + ThreatScanner.passed=True）；单跑 PASS
- **关联**：FR-9, FR-11, SC-1

### T-P3-3: 域 #3 Context 冻结快照（test_e2e_basic_tool_context.py 部分） [并行]
- **依赖**：P2 完成
- **工时**：~2.5h
- **DoD**：两次 LLM 调用断言 `frozen_prefix_hash` 一致 + 第二次断言含 owner 关键字；R5 直接缓解
- **关联**：FR-9, SC-1, R5 缓解

### T-P3-4: 域 #11 ThreatScanner block（test_e2e_safety_gates.py 部分） [并行]
- **依赖**：P2 完成
- **工时**：~2h
- **DoD**：构造含 invisible Unicode / pattern 的输入；断言 events 含 `threat.blocked` + USER.md sha256 跑前后不变；单跑 PASS
- **关联**：FR-9, SC-1

### T-P3-5: 域 #12 ApprovalGate SSE（test_e2e_safety_gates.py 部分） [并行]
- **依赖**：P2 完成
- **工时**：~3h
- **DoD**：触发需 approval 的工具；SSE 流含 `approval.pending` 事件 + 自动 approve 后 task `status="completed"`；2 断言点
- **关联**：FR-9, SC-1, Constitution #4

### T-P3-6: 旧 acceptance_scenarios.py 双源并存策略验证（plan Risk #1）
- **依赖**：T-P3-1..5
- **工时**：~0.5h
- **DoD**：跑 `pytest apps/gateway/tests/e2e/test_acceptance_scenarios.py` 仍全绿；commit message 显式注明"P5 末尾删除"
- **关联**：plan §3 Risk #1

### T-P3-7: .githooks/pre-commit native shell 脚本
- **依赖**：T-P3-1..5
- **工时**：~1.5h
- **DoD**：新建 `.githooks/pre-commit`（shebang `#!/usr/bin/env bash`，set -e）；检测 `SKIP_E2E=1` exit 0；否则 `cd octoagent && uv run pytest -m e2e_smoke --maxfail=1 -q`；总 timeout 180s（`timeout 180 ...`）；`chmod +x`
- **关联**：FR-28, FR-32, NFR-1, NFR-4

### T-P3-8: hook 失败输出格式（3 行核心 + 日志路径，FR-31）
- **依赖**：T-P3-7
- **工时**：~1h
- **DoD**：失败时输出 `[E2E FAIL] domain=<name>\n  expected: <...>\n  actual: <...>\n  log: <path>\n  bypass: SKIP_E2E=1 git commit`；不刷屏；不全量 dump LLM
- **关联**：FR-31, NFR-3

### T-P3-9: Makefile install-hooks target
- **依赖**：T-P3-7
- **工时**：~0.5h
- **DoD**：新建或追加 `Makefile`：`install-hooks: \n\tgit config core.hooksPath .githooks`；执行 `make install-hooks` 后 `git config --get core.hooksPath` == `.githooks`
- **关联**：FR-29

### T-P3-10: SKIP_E2E=1 bypass 路径验证 + 文档片段
- **依赖**：T-P3-7
- **工时**：~0.5h
- **DoD**：`SKIP_E2E=1 git commit -m "test"` exit 0；hook 输出 `[E2E] skipped via SKIP_E2E=1`；commit message 不被改写
- **关联**：FR-28, US-4, SC-9

### T-P3-11: smoke 5 场景 5x 循环 0 regression + 总耗时 ≤ 180s
- **依赖**：T-P3-1..10
- **工时**：~2h
- **DoD**：`for i in 1..5; do uv run pytest -m e2e_smoke -q; done` 全 PASS；单次实测 ≤ 180s（目标 90-120s）；LLM 调用计数 ≤ 10/次（grep events 表 model 字段）；R3 早期信号"alone pass / together fail"未出现
- **关联**：FR-22, FR-25, NFR-1, NFR-7, SC-2, SC-4, R3 验证

---

## P4: full 套件 8 域（~3-4d）

### T-P4-1: 域 #4 Memory observation→promote（test_e2e_memory_pipeline.py） [并行]
- **依赖**：P3 完成
- **工时**：~3h
- **DoD**：≥ 2 断言（candidate.confidence ≥ THRESHOLD + promote 后 `status="promoted"` + memory rows +1）
- **关联**：FR-10, SC-1

### T-P4-2a: 域 #5 真实 Perplexity 主路径（test_e2e_mcp_skill_pipeline.py 部分）
- **依赖**：P3 完成
- **工时**：~3h
- **DoD**：`mcp.install` 注入 OPENROUTER_API_KEY env + 调 `mcp__perplexity__search`；2 断言（tool.call.name 以 `mcp__` 前缀 + 返回 markdown 含至少 1 个 http(s) 链接）
- **关联**：FR-10, FR-35, US-3, R4 缓解

### T-P4-2b: 域 #5 单 LLM call timeout 60s + retry/SKIP 路径
- **依赖**：T-P4-2a
- **工时**：~1.5h
- **DoD**：在 conftest 或测试装饰器内对 #5 单 call timeout 改 60s；构造网络故障 mock 验证 retry 1 后 SKIP（不 FAIL）
- **关联**：FR-14, FR-24, R4 缓解

### T-P4-2c: 域 #5 跑后 mcp-servers/ sha256 不变（R10 验证）
- **依赖**：T-P4-2a
- **工时**：~0.5h
- **DoD**：测试 teardown 内断言 `sha256_dir(~/.octoagent/mcp-servers/)` 跑前后一致
- **关联**：SC-7, R10 验证

### T-P4-3: 域 #6 Skill 调用（test_e2e_mcp_skill_pipeline.py 部分） [并行]
- **依赖**：P3 完成
- **工时**：~2h
- **DoD**：≥ 2 断言（skill_runs +1 `status="success"` + Pydantic Output schema 验证通过）
- **关联**：FR-10, SC-1

### T-P4-4: 域 #7 Graph Pipeline（test_e2e_mcp_skill_pipeline.py 部分） [并行]
- **依赖**：P3 完成
- **工时**：~2.5h
- **DoD**：≥ 2 断言（graph_runs `status="completed"` + 所有 node checkpoint 落盘）
- **关联**：FR-10, SC-1

### T-P4-5: 域 #8 delegate_task / Worker 派发（test_e2e_delegation_a2a.py 部分） [并行]
- **依赖**：P3 完成
- **工时**：~3h
- **DoD**：≥ 2 断言（parent_task_id 链路完整 + a2a_messages 含 request + response 各 1 行）
- **关联**：FR-10, FR-15, SC-1

### T-P4-6: 域 #9 Sub-agent max_depth=2 拒绝（test_e2e_delegation_a2a.py 部分） [并行]
- **依赖**：P3 完成
- **工时**：~2h
- **DoD**：≥ 2 断言（events 含 `delegation.rejected` reason="max_depth" + 子 task 未创建）
- **关联**：FR-10, SC-1

### T-P4-7: 域 #10 A2A 通信完整 4 子断言（OQ-1）
- **依赖**：T-P4-5
- **工时**：~3.5h
- **DoD**：FR-15 锁定 4 子断言全部 PASS（DispatchEnvelope 投递 + worker B 工具调用 ≥ 1 + a2a_conversations.status=completed + a2a_messages req+resp 各 1 + parent_task_id 链路）
- **关联**：FR-15, SC-1

### T-P4-8: 域 #13 Routine cron / webhook（test_e2e_routine.py） [并行]
- **依赖**：P3 完成
- **工时**：~3h
- **DoD**：≥ 2 断言（routine_runs +1 `trigger_type="cron"` + 触发时刻误差 < 2s）
- **关联**：FR-10, SC-1

### T-P4-9: full 8 场景独立 PASS + 总耗时 ≤ 10min
- **依赖**：T-P4-1..8
- **工时**：~1.5h
- **DoD**：`uv run pytest -m e2e_full -q` 全 PASS；实测 ≤ 600s；含 #5 SKIP 路径不算 FAIL
- **关联**：NFR-1, SC-3

---

## P4.5: octo e2e CLI（合并入 P4 末，因 hook 已上线但 CLI 未实现）

### T-P4-10: octo e2e CLI 命令（含 --list / --loop=N / <domain_id>）
- **依赖**：T-P4-9
- **工时**：~2.5h
- **DoD**：新建 `apps/gateway/.../cli/e2e_command.py`；支持 `octo e2e smoke|full|<id>|--list|--loop=N`；`--list` 输出 13 域清单；`--loop=N` 跑 N 次循环（5x 范式泛化）；PASS/FAIL/SKIP 三态退出码
- **关联**：FR-30, US-2, US-5, plan §3 Risk #1

### T-P4-11: CLI 输出格式 + SKIP 留痕到 ~/.octoagent/logs/e2e/
- **依赖**：T-P4-10
- **工时**：~1h
- **DoD**：SKIP 全部场景特殊情况写入 `~/.octoagent/logs/e2e/quota-skip-<ts>.log`；常规失败输出 3 行核心 + 日志路径
- **关联**：FR-31, NFR-3, R3 缓解

---

## P5: 文档 + Codex review + 验收（~1-2d）

### T-P5-1: 删除旧 acceptance_scenarios.py + 旧 helper 引用清理
- **依赖**：P4 完成
- **工时**：~1.5h
- **DoD**：`apps/gateway/tests/e2e/test_acceptance_scenarios.py` 删除；**同时删除**旧位置的 `_build_real_user_profile_handler` / `_ensure_audit_task` / `_insert_turn_events`（T-P2-12 复制后的双源消除点）；grep `test_acceptance_scenarios` 引用全部清理；空目录删除；F086 ≥ 2038 测试仍全绿
- **关联**：plan §3 Risk #1, SC-10, analysis.md MEDIUM-1 修复闭环

### T-P5-2: 撰写 docs/codebase-architecture/e2e-testing.md
- **依赖**：P4 完成
- **工时**：~2h
- **DoD**：新建 `docs/codebase-architecture/e2e-testing.md`；覆盖：架构总览 / 13 域清单 / 跑法（pre-commit / CLI / 手动）/ SKIP_E2E / quota 处理 / module reset 维护指南 / fixture 路径速查；≥ 400 字
- **关联**：US-2 文档支撑

### T-P5-3: 5x 循环跑通 e2e_smoke 0 regression（SC-4 完整验证）
- **依赖**：T-P5-1
- **工时**：~1.5h
- **DoD**：`octo e2e smoke --loop=5` 全 PASS；记录 5 次单次耗时 + 总耗时；P95 ≤ 150s
- **关联**：NFR-7, SC-2, SC-4, R3 终验证

### T-P5-4: SC-7 sha256 跑前后一致验证
- **依赖**：T-P5-3
- **工时**：~0.5h
- **DoD**：跑 e2e 前后 sha256 比对 `~/.octoagent/{USER.md, MEMORY.md, mcp-servers/, auth-profiles.json}` 一致；记录 hash 值到 commit message
- **关联**：US-6, SC-7, R10 终验证

### T-P5-5: SC-8 secrets grep 验证（R9 终验证）
- **依赖**：T-P5-1
- **工时**：~0.5h
- **DoD**：`grep -rn "API_KEY\|TOKEN\|SECRET" --exclude-dir=.git --exclude=.gitignore --include="*.py" --include="*.md" --include="*.yaml" --include="*.json" octoagent/ tests/` 输出仅 negative pattern；commit message 含 grep 完整命令
- **关联**：FR-34, NFR-5, SC-8, R9 终验证

### T-P5-6: F086 ≥ 2038 测试基线 0 regression 终验
- **依赖**：T-P5-1
- **工时**：~1h
- **DoD**：`uv run pytest -q` 全量通过；测试数 ≥ 2038（含新增 13 e2e）；commit message 显式列测试数
- **关联**：SC-10

### T-P5-7: blueprint 同步 + Milestone 状态变更
- **依赖**：T-P5-2
- **工时**：~0.5h
- **DoD**：`docs/blueprint.md` 加 F087 完成条目；CLAUDE.md 内 Feature 列表加 F087 ✅；M5 阶段相关条目更新
- **关联**：CLAUDE.md Blueprint 同步规则

### T-P5-8: Codex Adversarial Review 触发（plan + implement 末尾）
- **依赖**：T-P5-3, T-P5-6
- **工时**：~1h（review 等待 + finding 收集）
- **DoD**：`/codex:adversarial-review` 已触发；finding 列表落地（high/medium/low 计数 + 描述）；commit message 含 "Codex review: N high / M medium 已处理 / K low ignored"
- **关联**：CLAUDE.local.md Codex Review 强制规则, SC-5

### T-P5-9: Codex finding 处理（high 必处理 / medium 评估）
- **依赖**：T-P5-8
- **工时**：~2h（保留 buffer，high finding 多时回到 P3/P4 修复）
- **DoD**：所有 high finding 接受改动或 commit message 显式 "Codex F<N> rejected: 理由"；medium 显式注明处理或拒绝；low 至少注明
- **关联**：CLAUDE.local.md, SC-5

### T-P5-10: 总验收 SC-1..SC-10 全过 + commit + push
- **依赖**：T-P5-1..9
- **工时**：~1h
- **DoD**：SC-1..SC-10 逐条标记✅；最终 commit message 含 SC 列表 + Codex review 状态；`git push origin 087-agent-e2e-live-test-suite` 通过 hook
- **关联**：SC-1..SC-10 全部

---

## 依赖图核心约束

- **P{N} → P{N+1} 严格串行**：跨 phase 不允许并行
- **P3 内 T-P3-1..5（5 域）可完全并行**：不同 case 文件 / 不同测试函数
- **P4 内 T-P4-1..8（8 域）可大部并行**：同测试文件内的域可串行（节省 fixture 启动），跨文件并行
- **P1 内 T-P1-3/4/5 可并行**：不同代码段搬运不冲突，最后 T-P1-6 合并
- **关键瓶颈**：T-P2-1（reset 清单）必须最先做，否则 P3 起所有测试都有 race 风险

## 高风险 task 速查（R1-R10 关联）

| Risk | 关联 task |
|------|----------|
| R1 (OctoHarness 等价) | T-P1-1, T-P1-2, T-P1-3, T-P1-4, T-P1-5, T-P1-6, T-P1-7, T-P1-8 |
| R3 (单例 reset 漏) | T-P2-1 (核心), T-P2-7, T-P2-13, T-P3-11, T-P5-3 |
| R4 (Perplexity 抖动) | T-P4-2a, T-P4-2b |
| R5 (prefix cache 破) | T-P1-3, T-P3-3 |
| R9 (fixture 误传 token) | T-P2-5, T-P5-5 |
| R10 (mcp_servers_dir 漏替换) | T-P2-3, T-P4-2c, T-P5-4 |

## FR 覆盖映射（35 FR → tasks）

| FR | tasks |
|----|------|
| FR-1 | T-P1-1, T-P1-2, T-P1-3, T-P1-4, T-P1-5, T-P1-6, T-P1-7 |
| FR-2 | T-P1-2, T-P2-4, T-P2-8 |
| FR-3 | T-P1-3..5, T-P1-7, T-P1-8 |
| FR-4 | T-P1-6 |
| FR-5 | T-P2-2 |
| FR-6 | T-P2-2, T-P2-14 |
| FR-7 | T-P2-7 |
| FR-8 | T-P2-9 |
| FR-9 | T-P3-1..5 |
| FR-10 | T-P4-1..8 |
| FR-11 | T-P3-1..5, T-P4-*, T-P2-10 |
| FR-12 | T-P2-8 |
| FR-13 | T-P2-8 |
| FR-14 | T-P4-2b |
| FR-15 | T-P4-5, T-P4-7 |
| FR-16 | T-P2-3 |
| FR-17 | T-P2-3, T-P2-4 |
| FR-18 | T-P2-3 |
| FR-19/20/21 | spec 决议（无 task，e2e fixture 不实例化 TelegramService 由 T-P2-7 hermetic 涵盖） |
| FR-22 | T-P3-7, T-P3-11 |
| FR-23 | T-P2-14 |
| FR-24 | T-P2-13, T-P4-2b |
| FR-25 | T-P3-11 |
| FR-26 | T-P2-1, T-P2-7 |
| FR-27 | T-P2-1 |
| FR-28 | T-P3-7, T-P3-10 |
| FR-29 | T-P3-9 |
| FR-30 | T-P2-15, T-P4-10 |
| FR-31 | T-P3-8, T-P4-11 |
| FR-32 | T-P3-7 |
| FR-33 | T-P2-6 |
| FR-34 | T-P2-5, T-P5-5 |
| FR-35 | T-P4-2a |

## SC 覆盖

| SC | tasks |
|----|------|
| SC-1 | T-P3-1..5, T-P4-1..8 |
| SC-2 | T-P3-11, T-P5-3 |
| SC-3 | T-P4-9 |
| SC-4 | T-P3-11, T-P5-3 |
| SC-5 | T-P5-8, T-P5-9 |
| SC-6 | T-P1-7, T-P1-8 |
| SC-7 | T-P2-11, T-P4-2c, T-P5-4 |
| SC-8 | T-P2-5, T-P5-5 |
| SC-9 | T-P3-10 |
| SC-10 | T-P1-8, T-P5-1, T-P5-6 |
