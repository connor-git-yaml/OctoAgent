# F144 实施计划（Plan）

**Base**: origin/master `2bd8679b`；**实测 baseline**（本 worktree，PYTHONPATH 锁，`-m "not e2e_live"`）：**4872 passed / 11 skipped / 106 deselected / 1 xfailed / 1 xpassed（173s）**。

## Phase 顺序（先简后难，每 Phase 独立 commit + hook 过）

### Phase A：frontdoor 矩阵补格（交付①，纯测试新增）
- 扩 `octoagent/apps/gateway/tests/test_frontdoor_auth.py`：
  - 新增模块级轻量 fixture `guard_app`：minimal FastAPI + `FrontDoorGuard(tmp_path)` 作 dependency，两条路由（`GET /api/probe` 普通受保护路由 + `GET /api/stream/probe` SSE 前缀路由）；`ASGITransport(client=…)` 模拟源 IP；mode 经 `OCTOAGENT_FRONTDOOR_MODE` 等 env monkeypatch（与既有测试同机制）。
  - `TestFrontDoorModeHeaderMatrix`：spec §3.1 A1-A8 共 17 格（A1/A2 各 5 格参数化 `_PROXY_HINT_HEADERS`，从生产模块 import 该常量保单一事实源）。
- 既有 8 格零修改。
- 验证：`pytest apps/gateway/tests/test_frontdoor_auth.py`。

### Phase B：`octo attest` 探针 + hermetic 单测（交付②，主体）
- 新 `packages/provider/src/octoagent/provider/dx/attest_commands.py`：
  - 数据模型：`AttestCheck{name, ok: bool|None, detail, hint}` + `AttestReport{probe, status: pass/not_enabled/fail, checks, next_steps}`（dataclass，`to_json_dict()`）。
  - 纯逻辑函数（DI 全注入，供单测）：
    - `run_remote_probe(*, tailscale_probe, env_reader, config_loader, http_client_factory) -> AttestReport`（§D-4 五检查链；token 只入 header/query 不入 report）。
    - `run_service_probe(*, manager_factory, kill_fn, ready_prober, sleep_fn, dry_run) -> AttestReport`（§D-5；SIGKILL，D-3 决策注释写明偏离证据链）。
  - Click 层：`attest_group` + `attest remote [--json] [--verbose]` / `attest service [--dry-run] [--json]`；渲染沿用 `console_output.render_panel`；exit code：fail=1 其余 0。
- `cli.py`：`main.add_command(attest_group)`（+1 import +1 行）。
- 新 `packages/provider/tests/dx/test_attest_commands.py`：
  - FakeCommandRunner（镜像 test_tailscale_helper）/ fake env_reader / `httpx.MockTransport` 假远端 / FakeServiceManager（status 序列可编程：kill 前后 pid 不同）/ fake kill 记录 / fake prober 序列 / fake sleep 累计。
  - 覆盖 spec AC-B / AC-C 全分支 + FR-B3 token sentinel 泄漏扫描 + FR-B5/C5 只读与零 sudo 机械断言 + dry-run 零 kill。
- 验证：`pytest packages/provider/tests/dx/test_attest_commands.py` + 全量抽查。

### Phase C：gap-1 scripted 用例（交付③）
- 新 `apps/gateway/tests/e2e_live/test_e2e_scripted_write_approval.py`：
  - 顶部 `pytest.importorskip("octoagent.skills.testing")`（F138 §3.5 防御）。
  - fixture：keystone 同款（OctoHarness + ScriptedModelClient + bomb + 空 CredentialStore + local-instance 模板）+ `app.include_router(approvals.router)`。
  - `test_scripted_write_approval_approved_lands`（spec FR-D1）/ `test_scripted_write_approval_rejected_no_write`（FR-D2）。
  - 并发批准：`asyncio.create_task` 驱动决策环 + poll `approval_manager.get_pending_approvals()` + ASGI `POST /api/approve/{id}`。
- 验证：单跑本文件 + F138 keystone 文件不回归。

### Phase D：清单 + living-docs + handoff（交付④）
- 新 `docs/codebase-architecture/attestation-checklist.md`（spec §D-8）。
- living-docs：`docs/blueprint/milestones.md` F144 行 ✅；`docs/codebase-architecture/e2e-testing.md` 新节；`remote-access.md` / `service-and-logging.md` 各一小段。
- `.specify/features/144-attestation-absorb/handoff-to-F141.md`。

### Phase E：终门 + 双评审 + completion-report
- 全量回归（同 baseline 命令）目标 = 4872 + 新增全过、0 fail；e2e_smoke 8/8（hook 已每 commit 跑）；scripted 两文件（keystone + 新）全绿。
- `codex review --base origin/master`（final）+ Opus 对抗自审（spec §4.3 挑战面）；finding 闭环后补 fix commit + re-review。
- completion-report.md（含吸收对账表 + 真机探针执行指引）。

## 红线核对（执行期每 commit 自查）

- [ ] 不碰 frontend/** / playwright / `.github/workflows/` / pyproject / 925bc29b 标的 4 文件
- [ ] 探针零 sudo / 不改配置 / 不代跑 remote enable
- [ ] token 值不出现在任何输出/日志/报告
- [ ] worktree 禁 `uv sync`；验证全走 PYTHONPATH 锁 + `--no-sync`
- [ ] commit message 中文、无 Co-Authored-By；小步提交
