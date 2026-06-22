# F105 v0.2 Cleanup — 实施计划

> 模式：spec-driver-fix（快速修复）；来源：fix-report.md
> 变更规模：3 文件（1 生产 + 2 测试），< 10 文件 / 1 模块（gateway），LOW 风险
> base：origin/master d6f0ec54

## Impact Assessment

- 影响文件数：3（直接修改）；跨包影响：无（全在 `apps/gateway/`）。
- 数据迁移：无；API/契约变更：无（`_maybe_enqueue` 私有方法；`_ingest_update` 返回类型不变）。
- **风险等级：LOW**。

## 变更清单

### Fix 1：telegram.py — 补 D17a `_maybe_enqueue` 守卫（enqueue-first）
- 新增 `_maybe_enqueue(task_id, text, created)`：与 slack.py L295-312 字节级同语义。
- 调用点替换 + **前移**到 binding/reply-thread 写入之前（投递优先，对齐 slack/discord）。
- created 路径零变更；duplicate 路径新增 CREATED 守卫下补队。

### Fix 2：test_telegram_service.py — FakeTaskRunner + dedup 断言 + 2 新测试
- `FakeTaskRunner` 加 `fail_next`（照搬 slack）。
- `test_authorized_dm_creates_task_and_dedupes_update` 断言 1→2（D17a 补队，与 slack
  `test_event_id_idempotent_on_retry` 对称——**意图保留式更新**，dedup 核心契约「同一 task /
  status=duplicate / created=False」不变，仅 enqueue 计数子断言反映新行为）。
- 新增 `test_telegram_retry_recovers_unenqueued_task`（首投失败→重投补队恢复）。
- 新增 `test_telegram_late_retry_after_success_no_requeue`（终态晚到重投不重入队）。

### Fix 3：test_f105v02_ingress.py — 2 fixture hermetic 化（含上轮 2 缺口补齐）
- 新增共享 `_patch_no_frontend_dist(monkeypatch)`：精准 patch `Path.exists`（仅 frontend/dist）。
- `unbootstrapped_app`：env + `_patch_no_frontend_dist`（2a 改用）。
- `bootstrapped_client`：monkeypatch env + `Path.home` + `_DEFAULT_STORE_DIR` +
  **`_DEFAULT_MCP_SERVERS_DIR`（上轮漏，本轮补）** + **`_patch_no_frontend_dist`（上轮漏 2b，本轮补）**。

## 回归风险评估

| 场景 | 风险 | 说明 |
|------|------|------|
| dedup 断言 1→2 | **预期行为对齐，非 regression** | 与 slack 对称；handoff §6"红旗"已论证为 spec 显式行为变更区的意图保留更新 |
| enqueue 前移 | LOW | slack/discord 已是此序；created 路径无可观测差异；binding 写入异常不再阻 enqueue |
| `Path.exists` / `Path.home` / 常量 patch | 无 | monkeypatch 自动清理；exists patch 双条件精准命中，不泄漏 |
| conftest `app` fixture 不动 | 无 | 其他依赖 `app` 的测试不受影响 |

## 验证方案（PYTHONPATH 锁定 worktree，禁 uv sync）

> 注意：实际包布局为 core/memory/policy/protocol/provider/sdk/skills/tooling + apps/gateway/src
> （上轮 plan 的包名单 worker/communication/... 已过时，本轮已纠正）。

1. **针对性**：`test_telegram_service.py` + `test_f105v02_ingress.py` 全过。
2. **脏实例双维度**：制造 `frontend/dist` + 毒化 HOME（malformed auth-profiles）→ 2 个 ingress 测试稳过 + 宿主无 `.corrupted`（无泄漏）。
3. **0 regression**：全量 gateway（排除 e2e_live/e2e_smoke）vs d6f0ec54 baseline。
4. **e2e_smoke**：8/8。

## 实施顺序
Fix 1（telegram）→ Fix 2（telegram 测试）→ Step 1 验证 → Fix 3（ingress fixture）→ Step 2-4 验证 → Codex review → 单次 commit。

## Constitution Check
- #1 Durability First：PASS（D17a 补队正是"落盘后未入队"窗口的 durability 修复）。
- #2/#3/#4/#9/#10：N/A（无新事件 / 无 tool schema / 无不可逆操作 / 无 LLM 决策 / 无权限路径改动）。
**无 VIOLATION。**
