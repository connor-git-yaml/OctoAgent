# F105 v0.2 Cleanup — 完成报告（归总）

> 模式：spec-driver-fix（4 阶段：诊断→规划→修复→验证）
> 分支：`feature/105v02-cleanup-2`（base origin/master **d6f0ec54**）
> 状态：实现完成 + Codex review 闭环（0 HIGH）+ 全量验证通过。**未 push，等用户拍板。**

## 1. 背景

本件是上一轮（base cd9a56c3，pre-F107）卡住未提交工作的**重跑 + 纠偏**。上轮在过期基线上
改对了主体，但：①基线过期（落后 master 20 个 F107 commit）；②从未 commit；③漏 2 个 hermetic
缺口。本轮在干净 d6f0ec54 重做，补齐缺口，并经 Codex 对抗审查再加固。

> 上一轮的陈旧 worktree（`feature/105v02-cleanup` @ cd9a56c3）+ 误建的同名分支已清理，
> 未提交工作已存为参考 patch（`/tmp/F105v02-prev-attempt.patch`）。

## 2. 改动文件清单（净 +95 / -8 行，4 文件）

| 文件 | 性质 | 净行数 | 说明 |
|------|------|--------|------|
| `apps/gateway/src/.../services/telegram.py` | **生产（H-1）** | +23/-2 | `_maybe_enqueue`（D17a）+ enqueue-first 调用点前移 |
| `apps/gateway/tests/test_telegram_service.py` | 测试 | +~95 | `FakeTaskRunner.fail_next` + dedup 断言 1→2 + 2 D17a 测试 |
| `apps/gateway/tests/test_f105v02_ingress.py` | 测试 | +~70/-22 | 2 fixture hermetic 化（共享 `_patch_no_frontend_dist` + 三重宿主隔离）|
| `packages/core/tests/test_task_job_store.py` | 测试（Codex M5 闭环）| +42 | `test_create_job_noop_on_live_job`（live job 不双执行不变量）|

（另：误删后已 `git checkout` 完整恢复的 `octoagent/frontend/` 不计入改动——git status 仅上述 4 文件。）

## 3. 解决的问题（用户视角）

1. **Telegram 重投不再丢任务（H-1 / durability）**：此前 telegram 仅首投建任务时入队；若
   "任务已落盘但入队前进程崩/异常"，平台 webhook 重投/polling 重读会判 duplicate 直接丢弃，
   该任务永久卡死不执行。现对齐 slack/discord 的 D17a 恢复范式——重投在任务仍未启动时补入队，
   平台 retry 成为该窗口的恢复机会。Constitution #1 Durability First。
2. **2 个 ingress 测试不再受开发机状态影响**：此前在"前端 build 过（`frontend/dist` 存在）"或
   "宿主 `~/.octoagent` 脏"的开发机上必失败（实测主仓 d6f0ec54 双失败）。现完全 hermetic，
   任何开发机状态下稳定通过——消除"换台机器/build 过前端就红"的假失败。

## 4. Codex adversarial review 闭环（0 HIGH）

命令 `codex exec -s read-only -c model_reasoning_effort=high`，范围 uncommitted diff，聚焦 H-1。
**2 MEDIUM + 3 LOW 全闭环**（详见 `verification/codex-review.md`）：

| # | Sev | 处理 |
|---|-----|------|
| 1 enqueue 前移竞态 | medium | **接受（验证非问题）**：Codex 实读证实 telegram 回复路由取自 USER_MESSAGE event metadata，不读 conversation_binding → 前移安全 |
| 5 dedup 断言契约 1→2 | medium | **接受 + 加固**：新增 `test_create_job_noop_on_live_job` 在 job_store 层证明"补 enqueue 不双执行 live job"的真实幂等 |
| 2 duplicate 双执行 | low | 由 #5 新测试覆盖 |
| 3 status 写法 | low | **拒绝（带理由）**：跨平台 `_maybe_enqueue` 字节对称（handoff §3 "照搬 slack"）优先于 telegram 内部 `_status_value()` 复用 |
| 4 slack 等价 | low | 接受（验证等价）|

**关键正面确认**：Codex 独立证伪了"enqueue 前移引入回复丢失竞态"这一最大疑虑。

## 5. 验证结果（PYTHONPATH 锁定 worktree，禁 uv sync）

- **改动面联合**：telegram_service + ingress + job_store **28 passed**。
- **Bug 复现 + 修复证明**：d6f0ec54 主仓 baseline 跑 OLD 2 ingress 测试 → 双失败（405）；
  本分支在"frontend/dist 存在 + 毒化 HOME（malformed auth-profiles）"双脏维度下 → **均 PASS**
  且宿主无 `.corrupted` 泄漏。
- **0 regression**：gateway 非 e2e + job_store = **2079 passed / 1 failed**；baseline 等价
  2076 → **+3**（2 D17a + 1 job_store no-op），**无新增 failure**。
- **唯一 failure**：`test_plugin_watcher.py::test_start_degrades_without_watchdog`——与本分支
  **字节级无关**（git diff 为空）、在 d6f0ec54 clean baseline **同样失败**（venv 装了 watchdog →
  "degrades without watchdog" 断言恒假）。**pre-existing 环境工件，非本次 regression**，非本任务范围。
- **e2e_smoke**：8/8 passed。
- **ruff**：本次代码 0 error；仅剩 1 个 **pre-existing** I001（`test_telegram_service.py:3`
  import 排序，baseline 同样命中，属未改的 import 块，不在本任务 scope；pre-commit hook 不跑 ruff）。

## 6. 上轮 2 个缺口（本轮补齐，重点）

1. **`bootstrapped_client`（2b）未抑制 SPA catch-all**：上轮只给 2a 的 `unbootstrapped_app`
   patch 了 `Path.exists`，2b 在"无 frontend/dist"的纯净 worktree 侥幸过，**脏环境（主仓/pre-commit
   跑 master）仍 405**。本轮抽共享 `_patch_no_frontend_dist` 给两个 fixture（已复现并验证修复）。
2. **`_DEFAULT_MCP_SERVERS_DIR` 未隔离**：上轮 patch 了 `Path.home` + `_DEFAULT_STORE_DIR`，
   漏了 mcp_installer 的 import-time 常量（McpInstaller(None) fallback，patch Path.home 无效）。
   本轮补齐（当前宿主 mcp-servers 为空属良性，但留作真实例状态依赖隐患）。

## 7. Deferred（非缺陷，列 handoff 顺手项）

- telegram "同步秒回回复到原 message/thread" 回归测试（Codex M1 加固建议；回复路径已验证正确）。
- telegram polling enqueue-retry 测试（Codex L4 加固建议）。
- pre-existing I001（test_telegram_service.py import 排序）——任意触碰该 import 块的 Feature 顺手清。
- 二者均属加固覆盖，规模小。

## 8. 风险与建议

- **风险：低**。生产改动仅 telegram.py 1 文件 ~21 净行，与 slack/discord 字节对称；created
  路径行为零变更；duplicate 补队幂等由 job_store no-op 兜底（新测试已证）。
- **dedup 断言 1→2 的 handoff §6"红旗"**：已论证为 spec D17a 显式行为变更区的**意图保留式更新**
  （与 slack 已合入测试对称），非"可改契约断言"新先例。
- **建议：建议合入 origin/master**。0 HIGH / 0 regression / e2e_smoke 8/8 / 双脏维度稳过。
  按约束**未 push，等用户拍板**。
