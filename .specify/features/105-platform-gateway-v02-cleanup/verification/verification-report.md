# F105 v0.2 Cleanup — 验证报告

> base：origin/master d6f0ec54 / 分支：feature/105v02-cleanup-2
> PYTHONPATH 锁定 worktree（core/memory/policy/protocol/provider/sdk/skills/tooling + apps/gateway/src），禁 uv sync。

## 1. 针对性测试（24 passed）

`test_telegram_service.py` + `test_f105v02_ingress.py` 全过，含：
- `test_authorized_dm_creates_task_and_dedupes_update`（dedup 断言 1→2）✅
- `test_telegram_retry_recovers_unenqueued_task`（D17a 恢复，新）✅
- `test_telegram_late_retry_after_success_no_requeue`（终态守卫，新）✅
- `test_unbootstrapped_app_webhook_404_documented`（hermetic 2a）✅
- `test_harness_bootstrap_mounts_adapter_routers`（hermetic 2b）✅

## 2. Bug 复现（d6f0ec54 主仓 baseline）

主仓（有 `octoagent/frontend/dist` + 宿主 `~/.octoagent`）跑 OLD 2 测试 → **双失败**，复现 task 所述"对脏实例失败"：
- `test_unbootstrapped_app_webhook_404_documented`：`assert 405 == 404`（SPA catch-all）。
- `test_harness_bootstrap_mounts_adapter_routers`：`assert 405 == 200`（SPA catch-all 遮蔽 lifespan 注册的 telegram 路由）。
- （另：`test_plugin_watcher.py::test_start_degrades_without_watchdog` 也失败——见 §5 pre-existing。）

## 3. Hermetic 隔离证明（双脏维度受控注入）

### 3a 宿主读取泄漏探针（malformed auth-profiles → `.corrupted` 备份 = 读了宿主）
| 运行 | fixture | HOME=毒化目录 | 结果 | 宿主 `.corrupted` |
|------|---------|--------------|------|------------------|
| Run A | **NEW**（fix）| 是 | test PASS | **无**（未读宿主）✅ |
| Run B | OLD（git stash 回退）| 是 | test pass* | **出现** `auth-profiles.json.corrupted` + `plugins/`（读了宿主，泄漏被证实）|

\* OLD 也 pass 是因 malformed 被 CredentialStore 优雅降级（备份+空 store），但 `.corrupted` 备份+`plugins/` 目录证明 OLD 真读了宿主 → 非 hermetic。

### 3b frontend/dist 维度（SPA catch-all）
| 环境 | 2a | 2b（上轮缺口）|
|------|----|----|
| 仅修 telegram、未补 `bootstrapped_client` 的 frontend-dist 抑制 | PASS | **FAIL 405**（证实上轮 2b 缺口）|
| 本轮完整 fix | PASS | PASS |

### 3c 双维度同时（frontend/dist 存在 + 毒化 HOME）
NEW 2 个 ingress 测试 → **均 PASS**，且宿主**无 `.corrupted`** ✅。证明对真实例状态完全独立。

## 4. 0 regression（gateway 非 e2e，PYTHONPATH 锁定）

| | passed | failed |
|---|---|---|
| d6f0ec54 baseline（worktree 等价）| 2073 | 1（plugin_watcher）|
| feature/105v02-cleanup-2 | **2075** | **1**（plugin_watcher）|

Δ passed = **+2**（2 个新 D17a telegram 测试）；**0 新增 failure**。

## 5. Pre-existing failure（非本次 regression）

`apps/gateway/tests/services/test_plugin_watcher.py::test_start_degrades_without_watchdog`：
- 与本分支改动**字节级无关**（`git diff d6f0ec54 -- plugin_watcher*` 为空）。
- 在 d6f0ec54 clean baseline 上**同样失败**（主仓实测）。
- 根因：venv 已装 `watchdog` 库（F106 热重载依赖），测试断言 `start() is False`（期望 watchdog 缺席）→ `assert True is False`。属环境工件，非本任务范围。

## 6. e2e_smoke（必过门禁）

`pytest -m e2e_smoke` → **8 passed**，4326 deselected。✅

## 7. Codex adversarial review（H-1）

**结论：0 HIGH。** 2 MEDIUM + 3 LOW 全闭环（详见 `codex-review.md`）。

- **关键正面确认**：Codex 独立实读证实 enqueue-first 前移**安全**——telegram 回复路由取自
  `USER_MESSAGE` event metadata（非 conversation_binding），消除前移最大疑虑。
- **M1**（前移竞态）接受（验证非问题）；**M5**（dedup 断言契约）接受 + **新增
  `packages/core/tests/test_task_job_store.py::test_create_job_noop_on_live_job`** 在 job_store
  层显式证明 create_job 对 QUEUED/RUNNING live job no-op（"补 enqueue 不双执行"真实幂等）；
  **L2** 由该新测试覆盖；**L3**（status 写法）拒绝（跨平台 `_maybe_enqueue` 字节对称优先）；
  **L4**（slack 等价）接受。
- 新测试实测：`test_task_job_store.py` 4 passed（含新增）。

## 8. 改动文件汇总（最终）

| 文件 | 性质 | 说明 |
|------|------|------|
| `apps/gateway/src/.../services/telegram.py` | 生产（H-1）| `_maybe_enqueue` + enqueue-first |
| `apps/gateway/tests/test_telegram_service.py` | 测试 | FakeTaskRunner.fail_next + dedup 断言 + 2 D17a 测试 |
| `apps/gateway/tests/test_f105v02_ingress.py` | 测试 | 2 fixture hermetic 化（含 2 缺口补齐）|
| `packages/core/tests/test_task_job_store.py` | 测试（Codex M5 闭环）| +1 no-op 不变量测试（job_store 层）|
