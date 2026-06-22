# F105 v0.2 Cleanup — 任务清单

> 模式：spec-driver-fix；来源：plan.md。AC↔test 显式绑定（SDD 强化）。

## T1 — telegram.py D17a `_maybe_enqueue`（H-1）
- [x] 新增 `_maybe_enqueue`（slack 同语义）+ 调用点前移（enqueue-first）。
- **AC**：created 入队 / duplicate+CREATED 补队 / duplicate+终态不补队。
- **test**：`test_telegram_service.py::test_telegram_retry_recovers_unenqueued_task` +
  `::test_telegram_late_retry_after_success_no_requeue` +
  `::test_authorized_dm_creates_task_and_dedupes_update`（断言 1→2）。

## T2 — telegram 测试 D17a 覆盖
- [x] `FakeTaskRunner.fail_next` 注入。
- [x] dedup 断言更新（1→2，与 slack 对称）。
- [x] 2 新 D17a 测试（恢复 / 终态守卫）。

## T3 — ingress fixture hermetic 化（测试基础设施，可跳 review）
- [x] 共享 `_patch_no_frontend_dist`（精准 patch Path.exists/frontend-dist）。
- [x] `unbootstrapped_app`（2a）：env + frontend-dist 抑制。
- [x] `bootstrapped_client`（2b）：env(monkeypatch) + Path.home + `_DEFAULT_STORE_DIR` +
  `_DEFAULT_MCP_SERVERS_DIR` + frontend-dist 抑制。
- **AC**：2a webhook 404（无论 frontend/dist）；2b webhook 200 ignored（无论 frontend/dist + 宿主脏）。
- **test**：`test_f105v02_ingress.py::test_unbootstrapped_app_webhook_404_documented` +
  `::test_harness_bootstrap_mounts_adapter_routers`。

## T4 — 验证 + review
- [x] 针对性测试全过（24 passed）。
- [x] 脏实例双维度（frontend/dist + 毒化 HOME）→ 2 测试稳过 + 宿主无泄漏。
- [x] 0 regression vs d6f0ec54（gateway 非 e2e：baseline 2073 → 2075，+2 新测试，1 pre-existing plugin_watcher）。
- [x] e2e_smoke 8/8。
- [x] Codex adversarial review（H-1）。
- [x] completion-report + verification-report。
