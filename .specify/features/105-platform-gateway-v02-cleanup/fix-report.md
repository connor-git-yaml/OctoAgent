# F105 v0.2 Cleanup — 问题修复报告（Phase 1 诊断）

> 模式：spec-driver-fix（快速修复）
> 分支：feature/105v02-cleanup-2（base origin/master **d6f0ec54**）
> 范围：F105 v0.2 handoff §3 H-1（telegram enqueue 同型窗口）+ 2 个非 hermetic ingress 测试
> 说明：本件是上一轮（base cd9a56c3，pre-F107）卡住未提交工作的**重跑 + 纠偏**——
> 上轮工作正确但建在过期基线且漏 2 个 hermetic 缺口（见下 §2 的"上轮缺口"标注）。

## 问题描述（2 项小修）

1. **H-1 telegram enqueue 同型窗口**：v0.2 给 slack/discord webhook 重投做了 D17a 恢复范式
   （"duplicate 且 task 仍 CREATED → 补 enqueue"），telegram ingest 路径同型窗口未补齐——
   telegram 仅 `created` 时 enqueue，webhook 重投/polling 重读判 duplicate 不补队。
2. **2 个非 hermetic ingress 测试**：`apps/gateway/tests/test_f105v02_ingress.py` 的
   `test_unbootstrapped_app_webhook_404_documented` + `test_harness_bootstrap_mounts_adapter_routers`
   依赖真实环境状态（宿主 `~/.octoagent` / 前端 `frontend/dist` build 产物），对脏开发实例失败。

---

## 问题 1：H-1 telegram enqueue 同型窗口

### 5-Why 根因追溯

| 层级 | 问题 | 发现 |
|------|------|------|
| Why 1 | telegram 重投为何丢任务？ | duplicate 路径不调 enqueue（`_ingest_update` 原 L403：`if created and self._task_runner is not None`）|
| Why 2 | 为何只在 created 时 enqueue？ | telegram 早于 slack/discord 实现，未引入 D17a 状态守卫 |
| Why 3 | 为何 created-only 不够？ | "create_task 落盘成功 → enqueue 前进程崩/异常"存在窗口；该 task 永久停在 CREATED 不被执行 |
| Why 4 | 为何 retry 不能恢复？ | 平台重投（webhook retry / polling 重读）判 idempotency duplicate，telegram 直接返回不补队 |
| Why 5 | 为何未被捕获？ | telegram service 无 D17a 测试覆盖；FakeTaskRunner 无 `fail_next` 注入能力 |

**Root Cause**：telegram 缺 slack/discord 已有的 `_maybe_enqueue` 状态守卫——平台 retry 是
"落盘未入队"窗口的唯一恢复机会，telegram 把它丢弃了。

### 影响范围扫描

#### 同源参考（已正确，无需改）
| 文件 | 位置（d6f0ec54）| 模式 |
|------|------|------|
| `slack.py` | L287 调用 / L295-312 | `_maybe_enqueue` D17a 守卫（**参考样板**，enqueue-first）|
| `discord.py` | L274 调用 / L290-302 | `_maybe_enqueue` 同语义 |

#### 需修复（telegram）
| 文件 | 位置 | 修复动作 |
|------|------|----------|
| `telegram.py` | `_ingest_update`（L398-409）| 新增 `_maybe_enqueue` + 调用点前移（enqueue-first，对齐 slack/discord）|

### 修复策略（推荐，已实施）

新增 `_maybe_enqueue`（与 `slack._maybe_enqueue` 字节级同语义，复用已 import 的 `TaskStatus`）：
created 直接入队；duplicate 仅当 `task.status == CREATED` 时补入队。调用点**前移**到
`_record_conversation_binding` / `_remember_inbound_reply_thread` 之前（投递优先，对齐
slack/discord 顺序，消除"binding 写入异常阻断 enqueue"的次生窗口）。

**关键 review 点**：
- created 路径行为零变更（仍 `enqueue(task_id, context.text)`）。
- duplicate 路径**新增**补队（仅 CREATED）——行为修复，由 dedup 测试断言更新体现。
- 顺序由原 binding→remember→enqueue 改为 **enqueue→binding→remember**（handoff §3 H-1
  明确"照搬 slack `_maybe_enqueue` 状态守卫模式"，slack 即 enqueue-first）。
- polling 路径（`_polling_loop` → `_ingest_update`）自动受益。

### Spec 影响
- 行为修复对齐 spec D17a（slack/discord 已实现，telegram 补齐）；无需改 spec.md。
- handoff §3 H-1 标注"动 telegram 行为面，需走 review"——命中 Codex review 节点。

---

## 问题 2：2 个非 hermetic ingress 测试

### 根因（两条独立的"读真实例"路径，d6f0ec54 主仓实测**两测试均失败**）

实测：在 d6f0ec54 主仓（有 `octoagent/frontend/dist` build 产物 + 宿主 `~/.octoagent`）跑这 2 个
测试 → **2a `assert 405==404` / 2b `assert 405==200` 双失败**（复现了 task 所述"对脏实例失败"）。

#### 根因 A：`frontend/dist` 存在 → SPA catch-all 把 404/200 污染成 405
- `create_app`（main.py:384-386）在 `frontend_dist.exists()` 时 `app.mount("/", SpaStaticFiles(html=True))`。
- 这是 catch-all：POST `/api/telegram/webhook` 落到 StaticFiles（不接受 POST）→ **405**。
- 2a（不跑 lifespan，路由本就未注册）：405 ≠ 期望 404。
- 2b（跑 lifespan，路由已挂载）：SPA mount 在 create_app 早于 lifespan 注册 telegram 路由 →
  catch-all 仍命中 → 405 ≠ 期望 200。
- **上轮缺口**：上轮只给 2a 的 `unbootstrapped_app` patch 了 `Path.exists`，**2b 的
  `bootstrapped_client` 未 patch**——2b 仅在"无 frontend/dist"环境（如纯净 worktree）侥幸过，
  脏环境（主仓/pre-commit 跑 master）仍 405。本轮已补齐（共享 `_patch_no_frontend_dist`）。

#### 根因 B：宿主 `~/.octoagent` 被 lifespan bootstrap 读取（仅 2b）
- lifespan 以 DI 全 None 构造 `OctoHarness`（生产路径，main.py），回退宿主：
  - call-time `Path.home()/.octoagent/{mcp-servers,pipelines,plugins,skills}` + USER.md 候选。
  - import-time 常量 `_DEFAULT_STORE_DIR`（auth/store.py:32，credential）+
    `_DEFAULT_MCP_SERVERS_DIR`（mcp_installer.py:29，McpInstaller fallback）。
- 实测信号：毒化 HOME（malformed auth-profiles.json）+ OLD fixture → 宿主出现
  `auth-profiles.json.corrupted` 备份 + `plugins/` 目录（证实 OLD 真读了宿主）。
- **上轮缺口**：上轮 patch 了 `Path.home` + `_DEFAULT_STORE_DIR`，**漏了
  `_DEFAULT_MCP_SERVERS_DIR`**（McpInstaller(None) 内部 fallback 到 import-time 常量，
  patch Path.home 无效）。本轮已补齐。

### 修复策略（推荐，已实施）
- `_patch_no_frontend_dist(monkeypatch)` 共享 helper：精准 patch `Path.exists`，仅
  `name=="dist" and parent.name=="frontend"` 返 False（其余真实判定），两个 fixture 共用。
- `bootstrapped_client`：monkeypatch env（替手动 os.environ，自动清理）+ `Path.home` → tmp +
  `_DEFAULT_STORE_DIR` → tmp + `_DEFAULT_MCP_SERVERS_DIR` → tmp + `_patch_no_frontend_dist`。
- `unbootstrapped_app`：env + `_patch_no_frontend_dist`，不跑 lifespan。

### Spec 影响
- 纯测试基础设施修复，无 spec 影响。测试意图（US-1 AC-4 / SC-4）完全保留，仅消除环境状态依赖。

---

## 范围总结

- 受影响文件：3 个（telegram.py 生产 + 2 个测试文件）。**< 10 文件 / 1 模块（gateway）→ 适合 fix 模式。**
- 生产代码改动：telegram.py 新增 ~18 行方法 + 调用点前移（H-1，命中 Codex review 节点）。
- 测试改动：1 断言更新（行为对齐 slack）+ FakeTaskRunner.fail_next + 2 新 D17a 测试 +
  2 fixture hermetic 化（含上轮 2 个缺口补齐）。
- 0 regression vs d6f0ec54；e2e_smoke 必过；改后 2 个 ingress 测试对脏实例（frontend/dist +
  宿主双维度）稳过。
