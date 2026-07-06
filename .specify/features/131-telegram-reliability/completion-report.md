# F131 Telegram 可靠性 — Completion Report

> M8 P1 · 分支 `feature/131-telegram-reliability`（off master `1e64ecd3`）
> 完成日期：2026-07-06 · 双评审：Opus 自审 1 HIGH + Codex 4 轮（0 HIGH 残留）

---

## 1. 现状诊断结论（改之前的真实行为）

详见 `spec.md` §0。三场景现状 + 真缺口：

| 场景 | 崩溃? | 丢消息? | 真缺口 | 证据 file:line |
|------|-------|---------|--------|----------------|
| ① 断线抖动 | 否 | 否（offset 重发兜底）| **A** 扁平 `sleep(1.0)` 无退避 → busy-loop 刷日志 | `telegram.py:401-403`（master）|
| ② 409 双开 | 否 | 否 | **C** 无法识别（日志泛化 `telegram_polling_loop_failed`）; **D** 无退避紧循环 | `telegram_client.py:178-184` + `telegram.py:401-403`（master）|
| ③ 出站失败 | 否 | **是** | **E** 零重试零补偿，进程重启丢——**主缺口** | `channels/registry.py:80-96` |

**关键发现**：入站已有 Telegram offset 重发防丢（不重造入站 spool）；出站是真空——`notify_task_result` send 失败被 registry 只 log.warning 后永久丢弃。

---

## 2. 实际补了什么（只补真缺口，未重造已有）

### G1 polling 指数退避（缺口 A/D）
- `_polling_loop` 扁平 `sleep(1.0)` → 指数退避 + jitter（`_compute_poll_backoff`，base=2s/max=60s/factor=2/jitter=±20%，成功一轮 reset）。
- 退避 sleep 走 `wait_for(stop_event, timeout=delay)`，shutdown 立即醒来（不空等满 delay）。

### G2 409 双开识别（缺口 C）
- `_is_getupdates_conflict()`：`error_code==409` 且描述含 getUpdates/conflict（镜像 OpenClaw `isGetUpdatesConflict`，双条件防误判）。
- 409 命中 → WARNING 含固定 hint `_TELEGRAM_409_CONFLICT_HINT`（用户可修：关另一 poller / 切 webhook），与普通网络错日志文案区分。
- 409 也走退避（缺口 D），共用退避状态机。

### G3 出站补偿 spool（缺口 E，主目标）
- 新增 `telegram_outbound_spool` 表 + `SqliteTelegramOutboundSpoolStore`（`packages/core`，仿 `SqliteNotificationStore` 范式，挂 StoreGroup 主 conn）。
- `notify_task_result` 文字路径 + `notify_approval_event` **无 inline keyboard** 路径经 `_send_or_spool`：send 失败入队落盘，返回 None。
- 带 inline keyboard 的 `approval:requested` **不 spool**（延后送达按钮失效的审批卡片比丢弃更糟，且审批有 SSE/operator-inbox 独立 durability）——显式设计决策。
- drain 走独立 `_spool_drain_loop` 后台任务（polling + webhook 都起，首轮立即 drain 做重启补偿，随后周期 30s）；成功 mark_sent 删行 / 失败退避 mark_retry / 超 8 次 mark_failed 落档。
- 进程重启：`_spool_drain_loop` 首轮 + 新 store 实例读同一 SQLite → 待发消息不丢（AC-7）。

---

## 3. 改动清单 + commit

| 文件 | 净变更 | 说明 |
|------|--------|------|
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | +~230 | 退避 + 409 识别 helper；`_send_or_spool` / `_enqueue_outbound_spool` / `_drain_outbound_spool` / `_spool_drain_loop`；startup/shutdown 双 task |
| `packages/core/src/octoagent/core/store/telegram_outbound_spool_store.py` | +176（新）| spool store |
| `packages/core/src/octoagent/core/store/sqlite_init.py` | +37 | `telegram_outbound_spool` DDL + index |
| `packages/core/src/octoagent/core/store/__init__.py` | +7 | StoreGroup 注册 spool store |
| `apps/gateway/tests/test_f131_polling_reliability.py` | +~240（新）| AC-1~4 + 退避溢出防护 |
| `apps/gateway/tests/test_f131_outbound_spool.py` | +~430（新）| AC-5~11 + P1/P2 回归 |

**commit 链**（本 worktree，未 push）：
- `cd480526` docs: spec + 三场景诊断
- `ec5ff87a` feat: 退避+409+spool store+drain
- `7fef4562` test: 17 tests
- `0bd2b43d` style: ruff
- `276a446d` fix: Opus 自审 HIGH-1（disable_notification 默认 True）
- `dac5ca6f` fix: Codex P1 webhook drain + P2 reply-thread + drain 锁
- `2b43dd16` fix: Codex 二轮 P1 后台 drain loop + P2 exp 封顶防溢出
- `687abb69` fix: Codex 三轮 P2 drain 全解耦后台任务

---

## 4. 回归 + 双评审

### 回归
- **受影响 146 tests 0 regression**（telegram service/voice/operator/route + f105v02 ingress/outbound + f105 adapter + f131×2 + core store）。
- **core 全包 593 passed**（store 变更波及面）。
- baseline（master `1e64ecd3`）受影响域 64 passed → 改后同域全绿 + 39 新 F131 tests。
- 首发成功路径逐字节等价（AC-11）——`_send_or_spool` disable_notification 默认 True 对齐 baseline。
- ruff：新增文件全 clean；telegram.py / sqlite_init.py / store __init__ 残留 E501/I001 均 pre-existing（master 亦报，非本 Feature 引入，未触碰）。

### 双评审（0 HIGH 残留）
- **Opus 自审**：抓 1 HIGH——`_send_or_spool` disable_notification 默认 False 会把任务结果回复从静音改成有声（AC-11 回归，现有测试未断言该 flag 漏网）→ 默认改 True + 回归锁测试。
- **Codex 4 轮**：
  - 一轮：P1 webhook drain gap（webhook 无 drain 触发）+ P2 reply-thread root 丢失 → 修（inbound 触发 drain + 持久化 root id）。
  - 二轮：P1 webhook drain 在请求路径同步跑（超时重投风险）+ P2 **OverflowError**（持续失败 streak→无界，~1025 次 factor^exp 溢出崩 loop，**真 bug**）→ 修（独立后台 drain loop + exp 封顶）。
  - 三轮：P2 startup/polling 主路径同步 await drain（50×10s 拖住启动）→ 修（drain 全解耦独立后台任务）。
  - 四轮：确认收敛（见本报告尾）。

---

## 5. 用户上手（真机怎么验）

### 验证 ① 断线重连
1. 常驻实例 polling 模式跑起（`octo service status` 确认）。
2. 断网（拔网线 / 关 WiFi）10-30s → `octo logs -f` 应看到 `telegram_polling_loop_failed streak=N retry_in_s=X.X`，retry_in_s 随 streak 指数增长（2→4→8…→60 封顶），**不是每秒刷屏**。
3. 恢复网络 → 下一轮成功，日志停止告警，之前发的消息不丢（Telegram offset 重发）。

### 验证 ② 409 双开
1. 实例正常 polling 中，另开一个终端用同一 bot token 手动 `curl` getUpdates（或再跑一个实例）。
2. `octo logs -f` 应看到 `telegram_polling_conflict_409 ... hint=检测到同一 bot token 被多处 getUpdates 占用（双开冲突）：请关闭另一个 poller/脚本，或将该账号切到 webhook 模式`。
3. 关掉第二个 poller → 冲突日志停止，恢复正常收消息。

### 验证 ③ 出站 spool 补偿
1. 给 Agent 发个会产生回复的任务。
2. 在回复即将发出时制造网络故障（或临时 block api.telegram.org）→ `octo logs` 看到 `telegram_outbound_spooled spool_id=N`（消息入队）。
3. 恢复网络 → 后台 drain loop（≤30s 一轮）自动补发，`octo logs` 看到 `telegram_outbound_spool_delivered spool_id=N`，手机收到延迟送达的回复。
4. **重启验证**：入队后立即 `octo restart` → 重启后 `_spool_drain_loop` 首轮补发，消息不丢。
5. 查 spool 表：`sqlite3 ~/.octoagent/data/sqlite/*.db "SELECT id,chat_id,status,attempts FROM telegram_outbound_spool"`——成功送达的行已删，超 8 次重试的标 `failed`（保留诊断）。

---

## 6. 已知 limitations（living-docs 漂移闸）

- **审批请求（带按钮）出站失败仍丢**：`approval:requested` 带 inline keyboard 不 spool（设计决策，见 §2）——审批有 SSE/operator-inbox 兜底，但纯 Telegram 场景下审批卡片一次发送失败不补偿。若未来需要，须设计"审批卡片重建 + 时效校验"而非裸文本 spool。
- **spool 无跨会话去重**：同一 task 若被 notify 多次（重入）可能入多条 spool（继承 F110 DEFER FINDING-3 基线，通知幂等域，非本 Feature 引入新风险）。
- **drain 批量上限 50/轮**：极端大 backlog（>50 条积压）需多轮 drain（每轮 30s）逐步清空——单用户场景几乎不触及。
- **failed 行不自动清理**：超重试上限标 failed 的行永久保留供诊断，无 TTL/上限清理（OpenClaw 有 prune，Octo 单用户量小暂不需要；未来可加）。

---

## 7. Constitution / H1 合规

- **#6 降级**：spool store 缺失 / drain 异常 / send 失败全降级不崩（polling loop / notify 主链 / startup 均不受 spool 故障级联）。
- **#1 Durability**：出站待发消息落 SQLite，进程重启不丢。
- **#8 Observability**：spool 入队/送达/失败/409 冲突全有结构化日志；failed 行可查。
- **H1**：spool 只补发主 Agent 已生成的结果文本，不抢话、不碰决策环 / AgentSession——主 Agent 仍是唯一 user-facing speaker。
