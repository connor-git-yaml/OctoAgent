# F105 v0.2 Verification Report

**Baseline**: 088ce2d4 = 3931 passed / 0 failed（phase-1-recon §0 命令，代码落盘前固化）
**验证范式**: 全部 PYTHONPATH 锁定本 worktree + `uv run --no-sync python -m pytest`（禁 uv sync，共享 venv 纪律）

## 1. 每 Phase 回归账目（0 regression 链）

| Phase | 全量结果 | 增量 | 说明 |
|-------|----------|------|------|
| A ingress 契约 | **3936 passed / 0 failed** | +5 | test_f105v02_ingress.py 5 测试 |
| B Slack | **3962 passed / 1 failed → 闭环后全绿** | +26/+27 | 唯一 fail = test_f102 用 "slack" 当未知渠道样例（R5 预判命中，意图保留式换样例 + 新增正向测试） |
| C Discord | **3983 passed / 0 failed** | +19+1 | service 9 + route 10 |
| D CONFIGURED + resolver v2 | **3992 passed / 0 failed** | +9 | 含 v0.1 resolver 三级策略测试 0 修改全绿（v2 向后兼容实证） |
| E L1 惰性 chat_id | **3997 passed / 0 failed** | +5/升级1 | test_notification.py / test_f101_notification.py **0 修改全绿**（AC-3 机械校验） |
| Final 双评审修复 | **4003 passed / 0 failed** | +6 | CODEX-F-H1 reconcile 4 测试 + F-M1 畸形 header 矩阵 1 + OPUS2-L-1 disabled 断言 1 |

## 2. AC ↔ Test 机械校验表（spec §10 绑定逐条核对）

| AC | Test | 状态 |
|----|------|------|
| US-1 AC-1 | 全量回归命令（§1 账目） | ✅ 0 regression |
| US-1 AC-2 | e2e_smoke 8/8（pre-commit hook 每次 commit 实跑） | ✅ |
| US-1 AC-3 | test_f105v02_ingress::test_telegram_webhook_via_adapter_router_equals_baseline | ✅ |
| US-1 AC-4 | test_f105v02_ingress::test_unbootstrapped_app_webhook_404_documented | ✅ |
| US-2 AC-1 | test_slack_service::test_url_verification_challenge | ✅ |
| US-2 AC-2 | test_slack_service::test_dm_message_creates_task_and_binding | ✅ |
| US-2 AC-3 | test_slack_service::test_event_id_idempotent_on_retry | ✅ |
| US-2 AC-4 | test_slack_route::test_status_mapping[signature_invalid/timestamp_stale→401] + test_slack_service::test_signature_and_timestamp_rejections | ✅ |
| US-2 AC-5 | test_slack_service::test_unauthorized_and_bot_and_subtype_ignored + route 200 映射 | ✅ |
| US-2 AC-6 | test_slack_service::test_notify_task_result_replies_in_thread + test_foreign_channel_task_noop | ✅ |
| US-2 AC-7 | test_f105v02_outbound::test_slack_notification_resolves_dm_last_route + test_channel_only_runtime_binding_not_notified | ✅ |
| US-2 AC-8 | test_slack_service::test_retry_recovers_unenqueued_task + test_late_retry_after_success_no_requeue | ✅ |
| US-3 AC-1 | test_discord_route::test_ping_pong + test_discord_service::test_ping_pong | ✅ |
| US-3 AC-2 | test_discord_route::test_invalid_signature_401 + test_discord_service::test_invalid_signature_rejected | ✅ |
| US-3 AC-3 | test_discord_service::test_command_creates_task_idempotent | ✅ |
| US-3 AC-4 | test_discord_service::test_unauthorized_user_ephemeral_rejection | ✅ |
| US-3 AC-5 | test_discord_service::test_notify_task_result_rest_message | ✅ |
| US-4 AC-1 | test_f105v02_outbound::test_bootstrap_writes_configured_binding_idempotent（真双 bootstrap） | ✅ |
| US-4 AC-2/AC-3 | test_conversation_binding_store::test_configured_tier_and_runtime_precedence | ✅ |
| US-4 AC-4 | test_conversation_binding_store::test_configured_upsert_h1_rejects_agent_profile | ✅ |
| US-4 AC-5 | test_f105v02_outbound::test_h1_no_agent_profile_write_path_v02 | ✅ |
| US-5 AC-1/AC-2 | test_f105v02_telegram_lazy_chat_id::test_resolver_lazy_after_pairing + test_resolver_none_or_raises_degrades | ✅ |
| US-5 AC-3 | test_notification.py / test_f101_notification.py 0 修改全绿 | ✅ |
| FR-D1 棘轮 | test_conversation_binding_store::test_configured_upgrades_runtime_not_reverse | ✅ |
| FR-D6 resolver v2 | test_configured_upgrade_keeps_runtime_activity_rank + test_resolver_v2_runtime_only_and_configured_only_unchanged | ✅ |
| SC-4 | test_f105v02_ingress::test_harness_bootstrap_mounts_adapter_routers | ✅ |

**Orphan 检查**：spec FR-A1~A4 / B1~B7 / C1~C7 / D1~D6 / E1~E3 / F1~F3 在 plan FR↔Phase 映射全有落点；AC 表无未绑定项。

## 3. 测试改动对照（spec §2.3 红线核对）

| 文件 | 改动类型 | 论证 |
|------|----------|------|
| 契约红线列表（recon §7：telegram/notification/chat 9 文件） | **0 断言修改** | 全绿 |
| test_f105_platform_registry.py | FakeAdapter **补方法**（inbound_router→None） | FR-A4，Protocol 演化 additive，0 断言修改 |
| test_f102_daily_routine_config.py | 未知渠道样例 "slack"→"smoke_signal" + 新增 1 正向测试 | R5 预判：样例前提被 D15 失效，测试意图（未知→fallback）不变 |
| test_f105_channel_adapter.py | 冻结断言（_chat_id 内部态）升级为惰性语义断言 | spec 行为变更区（FR-E/US-5）显式移除的 limitation 本体；构造/闭包/None 语义断言保留 |
| 其余 | 纯新增 7 文件 / 追加函数 | additive |

## 4. Final 全量验证（双评审修复闭环后）

- 全量回归：**4003 passed / 0 failed / 10 skipped / 97 deselected / 1 xfailed / 1 xpassed**（vs baseline 3931 = +72 新测试，0 regression）
- e2e_smoke：8/8 PASS（pre-commit hook 每个 commit 实跑，未用 SKIP_E2E）
- uv.lock diff：仅 2 行（gateway→cryptography 直接依赖边，零版本漂移）
- H1 grep：runtime 入口签名无 agent_profile_id；configured 入口非空 raise；生产代码无任何传非空 agent_profile_id 的调用点
