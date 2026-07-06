# F131 Telegram 可靠性 — Spec

> M8 P1 · off master `1e64ecd3` · 分支 `feature/131-telegram-reliability`
> 仿 OpenClaw `extensions/telegram/`（`polling-session.ts` + `telegram-ingress-spool.ts`）
> **原则**：先诊断三场景现状 + 定位真缺口，只补真缺口，不重造已有。

---

## 0. 现状诊断（改之前的真实行为 + 证据 file:line）

诊断对象：master `1e64ecd3` 的 Telegram polling 全链路
（`services/telegram.py` + `services/telegram_client.py` + `channels/registry.py` +
`provider/dx/telegram_pairing.py`）。

### 场景① polling 断线 / 网络抖动 → 现在会怎样？

**现状**：`TelegramGatewayService._polling_loop`（`services/telegram.py:376-403`）：

```python
while not self._stop_event.is_set():
    try:
        offset = self._state_store.get_polling_offset()
        updates = await self._bot_client.get_updates(offset=offset, timeout_s=self._polling_timeout_s)
        ... 逐条 _ingest_update + 推进 offset ...
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - 防御性兜底
        logger.warning("telegram_polling_loop_failed", exc_info=True)
        await asyncio.sleep(1.0)
```

真实行为：
- **不会崩溃、不会丢消息**：`get_updates` 抛任何异常（`httpx.ReadError` /
  `ConnectError` / `TelegramBotApiError`）被 `except Exception` 捕获 → loop 存活。
  offset 只在成功处理后才 `set_polling_offset`（:397-398），失败不推进 → Telegram
  下轮重发同批 update，**不丢消息**。✅ 这两条基线已达标。
- **缺口 A（真缺口）**：失败恢复用**扁平 `sleep(1.0)`**，无指数退避。持续断网/DNS
  故障时每秒硬打 Telegram API → **busy-loop 刷日志**（每秒一条 WARNING）。OpenClaw
  实证（`polling-session.ts:63-68` `TELEGRAM_POLL_RESTART_POLICY`）用
  `initialMs=30s / maxMs=600s / factor=2 / jitter=0.2` 指数退避正是为此。
- **缺口 B（真缺口）**：失败后**复用同一 httpx keep-alive TCP socket**（每次
  `get_updates` 新建 `AsyncClient` 但底层连接池行为 + Telegram 侧会话语义）。
  `telegram_client._request`（`telegram_client.py:157-161`）每次 `async with
  httpx.AsyncClient(...)` 已是新 client，此项风险低于 OpenClaw（其常驻 bot），
  但 409 场景下"stale socket 触发紧 409 循环"（OpenClaw `#69787` 注释
  `polling-session.ts:1595-1600`）仍需退避兜底。

### 场景② 409 双开（同 token 两处 getUpdates）→ 现在会怎样？

**现状**：Telegram 对第二个 `getUpdates` 返 HTTP 409 + `{"ok":false,
"error_code":409,"description":"Conflict: terminated by other getUpdates request"}`。
链路：`telegram_client._request`（`telegram_client.py:178-184`）见
`status_code != 200 or not ok` → 抛 `TelegramBotApiError(description,
status_code=409, payload=data)`。回到 `_polling_loop` 的 `except Exception`
（:401-403）→ 与普通网络错**完全同款处理**：`logger.warning
("telegram_polling_loop_failed")` + `sleep(1.0)`。

真实行为：
- **不会崩溃**。✅
- **缺口 C（真缺口）**：409 **无法与普通网络抖动区分**——日志只有泛化的
  `telegram_polling_loop_failed`，运维看不出"是不是双开了"。而 409 是**用户可修**
  的（关掉另一个 poller / 停掉 stale webhook），普通网络错不是。OpenClaw
  专门给 409 一条 user-fixable 诊断（`polling-session.ts:124-126`
  `TELEGRAM_GET_UPDATES_CONFLICT_HINT`）。
- **缺口 D（真缺口）**：409 也走 `sleep(1.0)` → 双开时**每秒紧 409 循环**
  （既刷本机日志，也每秒骚扰 Telegram + 干扰另一个合法 poller）。应退避。

### 场景③ 出站发消息失败 → 现在会怎样？

**两条出站路径**：
1. **任务完成回复**：`TaskRunner` → `platform_registry.notify_task_completion`
   → `TelegramChannelAdapter.notify_task_result`（`telegram_adapter.py:99-101`）
   → `TelegramGatewayService.notify_task_result`（`telegram.py:1011-1037`）
   → `bot_client.send_message`。
2. **审批/通知**：`TelegramApprovalBroadcaster.broadcast` → `notify_approval_event`。

**现状（关键）**：`channels/registry.py:80-96` `notify_task_completion`：

```python
for adapter in self._adapters.values():
    try:
        await adapter.notify_task_result(task_id)
    except Exception:
        log.warning("platform_completion_notify_failed", ..., exc_info=True)
```

真实行为：
- **缺口 E（真缺口 = 本 Feature 主目标）**：出站 `send_message` 抛异常
  （网络抖动 / Telegram 5xx / 限流 429）→ registry **只 log.warning 后丢弃，
  零重试、零补偿**。用户**永久收不到这条任务结果**——进程重启也不会重发
  （通知触发是一次性的，无待发队列）。这是"手机天天用"最痛的静默失败：
  Agent 干完活了，结果因一次网络抖动就永远送不到手机。
- 对比②入站有 Telegram 侧 offset 重发兜底；③出站**没有任何兜底**——一旦
  `notify_task_result` 那一刻网络不好，消息就丢了。

### 诊断结论汇总

| 场景 | 崩溃? | 丢消息? | 真缺口 |
|------|-------|---------|--------|
| ① 断线抖动 | 否 ✅ | 否 ✅（offset 兜底）| **A** 扁平 sleep 无退避 → busy-loop 刷日志 |
| ② 409 双开 | 否 ✅ | 否 ✅ | **C** 无法识别（日志泛化）; **D** 无退避紧循环 |
| ③ 出站失败 | 否 ✅ | **是 ❌** | **E** 零重试零补偿，进程重启丢——**主缺口** |

**已有、不重造**：
- 入站 offset 重发防丢（`telegram.py:397-398` + `telegram_pairing.py:341-345`）✅
- 入站 enqueue 补队窗口（`_maybe_enqueue` D17a，`telegram.py:477-495`）✅
- polling loop 不崩（`except Exception` 兜底）✅
- `TelegramBotApiError.status_code`/`payload` 已携带 409 判定所需信息 ✅
- OpenClaw 全套 ingress-spool（durable claim/lease/prune）— **不照搬**：那是常驻
  Node bot 的重型持久队列；Octo 单用户 + 已有 SQLite event store，出站 spool 用
  轻量 SQLite 表即可（见 §决策 DP-3）。

---

## 1. 目标与范围

补齐三缺口，让 Telegram 在"禁睡常驻 Mac + 手机天天用"下可靠：

- **G1（缺口 A/D）**：polling 失败恢复改**指数退避 + jitter**（替换扁平
  `sleep(1.0)`），断网/双开时不 busy-loop 刷日志、不骚扰 API；成功一轮后退避重置。
- **G2（缺口 C）**：**识别 409 conflict** 并给**用户可修的诊断日志**（区别于普通
  网络错），让运维一眼看出"双开了，去关另一个 poller / 停 stale webhook"。
- **G3（缺口 E，主目标）**：**出站补偿 spool**——`notify_task_result` /
  `notify_approval_event` 发送失败时入队（**SQLite 持久化**），后台重试；
  **进程重启不丢待发消息**，重启后自动 drain。

**范围外（显式不做）**：
- 入站 ingress spool（Telegram offset 重发已防丢，OpenClaw 的入站 spool 对我们是
  重复造轮子）——**REFUTED，不做**。
- webhook 模式改造（本 Feature 只碰 polling；webhook 由 Telegram 侧 retry 兜底）。
- voice 从 polling 剥离（那是 **F133** 的范围，不在此）。
- 限流 / token 加固（**F134**）。
- 多 bot account（OpenClaw 的 account-throttler；Octo §0 单用户单 bot）。

---

## 2. 验收标准（AC）+ test 绑定

> SDD 强化：每条 AC 紧邻标注 test 文件路径；verify 阶段 grep + pytest -k 机械校验。

- **AC-1（G1 退避）**：连续 N 次 `get_updates` 抛异常，第 k 次失败的 sleep 时长
  按指数退避递增（`base * factor^(k-1)`，封顶 `max`），含 jitter；成功一轮后
  下次失败从 base 重新开始。
  `[@test]` `apps/gateway/tests/test_f131_polling_reliability.py::test_backoff_grows_and_resets`
- **AC-2（G1 不 busy-loop）**：持续失败时，退避使单位时间内 `get_updates` 调用次数
  远低于扁平 sleep（验证退避序列前 5 次总等待 ≥ base*（1+2+4+8+16）量级）。
  `[@test]` `...::test_backoff_sequence_bounded`
- **AC-3（G2 识别 409）**：`get_updates` 抛 `TelegramBotApiError(status_code=409,
  description含 "conflict"/"getUpdates")` → loop 打印含 **conflict 诊断关键词**
  （"双开"/"另一个 poller"/"stale webhook"）的 WARNING，且**与普通网络错日志文案
  不同**（普通错不含该 hint）。
  `[@test]` `...::test_conflict_409_emits_distinct_hint`
  `...::test_network_error_no_conflict_hint`
- **AC-4（G2 409 退避）**：409 也走退避（不 busy-loop）；与普通错共用退避状态机。
  `[@test]` `...::test_conflict_409_backs_off`
- **AC-5（G3 spool 入队）**：`notify_task_result` 发送抛异常 → 消息落
  SQLite spool 表（含 chat_id / text / reply_to / thread_id / task_id）。
  `[@test]` `apps/gateway/tests/test_f131_outbound_spool.py::test_send_failure_enqueues_to_spool`
- **AC-6（G3 重试成功清账）**：spool 中的待发消息，后台 drain 重试成功后从表删除
  （不重复发）。
  `[@test]` `...::test_spool_drain_retries_and_clears_on_success`
- **AC-7（G3 重启不丢）**：spool 写盘后新建 store 实例（模拟进程重启）→ 待发消息
  仍在，可被 drain 取出。
  `[@test]` `...::test_spool_survives_process_restart`
- **AC-8（G3 首发成功不入队）**：`send_message` 首发成功 → spool 表为空
  （不引入无谓写盘）。
  `[@test]` `...::test_successful_send_does_not_spool`
- **AC-9（G3 重试退避 + 上限）**：spool 项重试失败 → 记录 attempts + 按退避延后
  下次重试；超 max_attempts 标记 failed（不无限重试打爆 Telegram）。
  `[@test]` `...::test_spool_retry_backoff_and_max_attempts`
- **AC-10（降级 Constitution #6）**：spool 表不可用 / drain 异常 → 只 log，不崩
  polling loop、不崩 notify 主链（出站失败本就是降级路径，spool 自身故障不得级联）。
  `[@test]` `...::test_spool_failure_degrades_gracefully`
- **AC-11（零回归）**：受影响测试模块 vs baseline 64 passed 0 regression；
  `test_telegram_service.py` / `test_f105v02_outbound.py` 原有出站行为不变
  （首发成功路径与 baseline 逐字节等价）。
  `[@test]` 全量受影响模块 + e2e_smoke

---

## 3. 关键设计决策（DP）

### DP-1 退避算法（G1/G2/AC-1~4）

新增 `_PollingBackoff` 纯函数状态机（`services/telegram.py` 内，或抽 helper）：
- `base=2.0s`（Octo polling_timeout 默认 15s，比 OpenClaw 常驻 bot 的 30s 更激进
  的起点合理——单用户实例断网后想快点恢复；封顶仍 `max=60s`）。
- `factor=2.0`，`jitter=±20%`（避免与另一 poller 同步共振，对齐 OpenClaw）。
- 成功处理一轮 update（无异常）→ `reset()`。
- **理由**：OpenClaw `TELEGRAM_POLL_RESTART_POLICY`（`polling-session.ts:63-68`）
  实证参数；Octo 收窄 base/max 适配单用户实例快恢复诉求。**不引入外部依赖**
  （纯 `random.uniform` + 局部 float 状态）。

### DP-2 409 识别（G2/AC-3）

新增 `_is_getupdates_conflict(exc) -> bool` helper：
- 判 `isinstance(exc, TelegramBotApiError) and exc.status_code == 409`
  且 `("getupdates" in desc.lower() or "conflict" in desc.lower())`
  （desc 取 `str(exc)` + `exc.payload.get("description")`）。
- 命中 → WARNING 文案含固定中文 hint 常量
  `_TELEGRAM_409_CONFLICT_HINT`（"检测到同一 bot token 被多处 getUpdates 占用
  （双开）：请关闭另一个 poller/脚本，或将该账号切到 webhook 模式"）。
- **理由**：镜像 OpenClaw `isGetUpdatesConflict`（`polling-session.ts:1638-1656`）
  的 error_code + method 双条件判定，避免把偶发 409 误判。

### DP-3 出站 spool 存储（G3/AC-5~10）

**决策：轻量 SQLite 表，不照搬 OpenClaw 文件系统 durable queue。**
- 新增表 `telegram_outbound_spool`（挂现有 SQLite，复用 store 连接范式）：
  `id INTEGER PK / channel TEXT / chat_id TEXT / text TEXT / reply_to_message_id
  TEXT / message_thread_id TEXT / task_id TEXT / attempts INTEGER / next_retry_at
  REAL / status TEXT(pending|failed) / created_at REAL / last_error TEXT`。
- 新增 `TelegramOutboundSpoolStore`（`packages/core` store 层，与
  `conversation_binding_store` 同目录同范式）：`enqueue` / `list_due(now)` /
  `mark_sent(id)`（=删行）/ `mark_retry(id, attempts, next_retry_at, err)` /
  `mark_failed(id)`。
- **理由**：①Octo 已是 SQLite-first（event store / task store / binding store
  全 SQLite），出站队列用同款事务保证 + 天然跨重启，比 OpenClaw 的
  per-file claim/lease **简单得多且够用**（单用户单进程，无多 worker 抢占）；
  ②OpenClaw 的 claim/lease/prune 是为多进程 + 常驻 bot 设计，Octo 单进程不需要
  分布式租约。**主动剔除 OpenClaw 重型部分**（防过度设计）。

### DP-4 spool drain 触发（G3）

- **enqueue 时机**：`notify_task_result` / `notify_approval_event` 的 `send_message`
  抛异常 → 入 spool（在现有 registry try/except 之内的 service 层，或 service
  自身捕获后入队——见 DP-6 边界）。
- **drain 时机**：①复用 `_polling_loop` 每轮 tick 顺带 `_drain_outbound_spool()`
  （polling 存活即周期性 drain，无需新后台 task——最省）；②`startup()` 时先 drain
  一次（重启后立即补发）。
- **理由**：polling loop 本就是常驻循环，挂载 drain 零新增 task/线程，生命周期
  与 polling 完全一致（shutdown 自动停）。webhook-only 模式无 polling loop →
  spool 仅靠 startup drain + 下次 notify 失败时同步 retry（webhook 模式出站失败
  概率同样存在，但 drain 依赖较弱；v0.1 接受，注明 limitation）。

### DP-5 重试退避 + 上限（G3/AC-9）

- spool 项 `next_retry_at = now + min(base * factor^attempts, max)`；
  `max_attempts=8`（约覆盖 base=5s → 5,10,20,40,80,160,320,640s ≈ 20min 累计），
  超限 `status=failed`（保留行供诊断，不删、不再 drain）。
- **理由**：出站重试太密会打爆 Telegram（429 反噬）；有限重试 + failed 落档
  符合 Constitution #8（可查失败原因）。

### DP-6 职责边界（H1 + 零回归）

- **不碰决策环 / AgentSession**：spool 纯粹在渠道出站层（`TelegramGatewayService`
  send 失败后的兜底），主 Agent 仍是唯一 user-facing speaker（H1 保持）。
- **enqueue 落点**：在 `TelegramGatewayService.notify_task_result` 内部——把
  `send_message` 包一层 `_send_or_spool()`：成功即返回（与 baseline 等价），
  失败入 spool（新行为）。registry 的 try/except 保持不变（spool 自身异常仍被
  registry 兜底）。**首发成功路径逐字节不变 → AC-11 零回归**。

---

## 4. 变更清单（预估）

| 文件 | 变更 | 缺口 |
|------|------|------|
| `services/telegram.py` | `_polling_loop` 退避 + 409 识别；`notify_task_result`/`notify_approval_event` 包 `_send_or_spool`；`_drain_outbound_spool`；`startup` drain | A/C/D/E |
| `packages/core/.../store/` 新增 `telegram_outbound_spool_store.py` | SQLite spool store | E |
| `packages/core/.../store/sqlite_init`（或等价建表处） | 建表 `telegram_outbound_spool` | E |
| harness `octo_harness.py` | 构造 spool store 注入 telegram_service | E |
| `apps/gateway/tests/test_f131_polling_reliability.py` 新增 | AC-1~4,10 | A/C/D |
| `apps/gateway/tests/test_f131_outbound_spool.py` 新增 | AC-5~11 | E |

---

## 5. 双评审重点（重大架构变更节点：新增 store + 出站语义）

- spool 是否引入"消息重复发"？（enqueue 幂等 + mark_sent 即删 + 首发成功不入队）
- drain 与 notify 并发是否重复取同一行？（单进程单 polling loop 串行 drain，
  无并发；webhook 模式 startup drain 是唯一 drainer）
- 退避状态跨"成功一轮"是否正确 reset？（AC-1 覆盖）
- spool store 故障是否级联崩 polling / notify？（AC-10 降级覆盖）
- 是否偏离 H1（spool 是否让渠道层"抢话"）？（否——只补发主 Agent 已生成的结果文本）
