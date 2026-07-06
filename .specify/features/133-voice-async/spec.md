# F133 — voice 从 polling 剥离（语音处理异步化）

> M8 P1 · 规模 M · 分支 `feature/133-voice-async`（off master `89ca6bc8`，含 F131）
> v0.1 收窄：**入站 STT 剥离为主**；出站 TTS 路径范围外（挂 notify_task_result，不在 polling 热路径）。

---

## 0. 现状诊断（复核主 session 判断，行号以 master 89ca6bc8 为准）

| # | 判断 | 复核结论 | 证据 file:line |
|---|------|----------|----------------|
| ① | polling loop 串行分发 | **成立**。`_polling_loop` 内 `for update in updates: await self._ingest_update(update)` 逐条 await——一条慢 update 卡住同批后续 + 下一轮 getUpdates | `services/telegram.py:516-517` |
| ② | voice 整条 pipeline 内联 ingest 热路径 | **成立**。`_ingest_update:756-760` → `_handle_voice_message`（:831-921）内联做幂等预检 + get_file（:873）+ 流式下载（:877）+ STT 转写（:893）+ voice_mode 写入（:919）。60s 语音转写秒级~数十秒，期间同批文字消息全延迟 | `services/telegram.py:756,831-921` |
| ③ | STT backend 是否已线程卸载 | **已卸载**。`FasterWhisperBackend.transcribe` 已 `asyncio.to_thread(self._transcribe_sync, ...)`——event loop 本身不被 CPU 卡死（问题①不存在）。但 polling 协程仍串行 await 整条 pipeline——**这是 F133 的核心缺口（问题②）** | `voice/faster_whisper_backend.py:90-92` |
| ④ | 失败降级已有 | **成立**。`_reply_voice_degrade`（:923）覆盖 STT 不可用 / 超限 / 下载失败 / 转写失败 / 空转写五分支 | `services/telegram.py:850-907` |

**附带发现（baseline 隐性问题，F133 顺手闭合）**：webhook 模式下转写内联意味着 HTTP 响应等满整条 pipeline——超 Telegram webhook 超时会触发重投，重投副本与首投**并发**转写（F109 已归档的"并发幂等窗口"limitation）。F133 全局串行队列使同一消息的重复副本在 worker 内被幂等预检拦截（串行处理，第二条必然看到第一条已建的 task）——该窗口顺带闭合。

## 1. 设计（v0.1）

### 剥离机制

```
_ingest_update（热路径,毫秒级返回）
  └─ voice 且无文字 → _enqueue_voice_processing(context)
       ├─ self._voice_queue.put_nowait(context)      # 全局 FIFO 无界队列（item=轻量 context，无音频字节）
       ├─ lazy spawn _voice_worker_task（不存在/已死 → create_task,自愈）
       └─ return TelegramIngestResult(status="accepted", detail="voice_queued")   # route 层零改动（accepted→200）

_voice_worker_loop（后台串行 worker,并发上界=1）
  └─ loop: context = await queue.get()
       ├─ outcome = await _handle_voice_message(context)   # 原函数零改动：幂等预检+守卫+下载+转写+voice_mode
       ├─ outcome 是 IngestResult（降级/duplicate,已回复用户）→ 完成
       ├─ outcome 是 context（转写成功）→ await _ingest_text_context(outcome)   # baseline 后半段原样抽取
       └─ 意外异常 → log + _reply_voice_degrade（不静默,#6）; 单条失败不退出 loop
```

### 关键决策

- **D1 并发上界 = 1（全局串行队列）**：faster-whisper CPU-bound（int8/base 单转写已吃满多核），单 consumer 天然防多语音并发打爆 CPU；排队不丢；同 chat FIFO 天然保序（全局 FIFO ⊇ 同 chat FIFO），够 v0.1。
- **D2 H1 不变（转写后同路）**：baseline `_ingest_update` 的后半段（空文本检查→control command→建 task→enqueue→binding→reply-thread）**原样抽取**为 `_ingest_text_context`，文字消息与转写后的 voice 都走它——只挪"何时跑"，不改"跑什么"。
- **D3 整条 `_handle_voice_message` 挪后台（含轻量守卫）**：STT 不可用/超限等"便宜"分支的降级回复本身是网络 send（坏网下秒级）——留在 ingest 热路径会重新引入阻塞。挪后台后 ingest 路径对 voice 零网络 I/O。
- **D4 幂等预检留在 worker（处理时点）**：webhook 重投/崩溃重投产生的重复副本在队列里排队，串行 worker 处理第二条时幂等预检必然命中首条已建 task → duplicate 静默跳过。比 baseline 的"并发窗口"更严。
- **D5 lazy spawn + shutdown 显式 cancel**：worker 首条 voice 才拉起（无 voice 用户零后台任务；worker 意外死亡下条 voice 自愈重拉）；`shutdown()` 的 cancel 列表加 `_voice_worker_task`（沿用 F131 双 task 同款范式）；pending 队列项随进程丢弃（见 §3）。
- **D6 route 层零改动**：排队返回 `accepted + detail="voice_queued"`（route `accepted→200`，Telegram 快速确认，重投概率反而下降）。

### F131 协同（不许回退其语义）

- polling 退避/409/spool drain **零触碰**——F133 只改 `_ingest_update` 内 voice 分支 + 新增 worker，`_polling_loop`/`_spool_drain_loop`/`_send_or_spool` 不动。
- voice 剥离后 polling 一轮更快完成 → offset 更快确认 → F131 退避状态机行为不变（成功一轮 reset 语义无关 voice）。

## 2. AC（验收标准）

| AC | 断言 | 绑定测试 |
|----|------|----------|
| AC-1 存在性证明 | 慢 STT（挂起中）时发文字 update，文字消息在转写完成**之前**已建 task | `test_f133_voice_async.py::test_text_not_blocked_by_slow_voice` |
| AC-2 FIFO 保序 | 3 条 voice 排队全部最终处理，转写顺序=入队顺序 | `::test_multiple_voice_fifo_all_processed` |
| AC-3 shutdown 干净 | 转写挂起时 shutdown()：worker 被 cancel、无 orphan task、pending 项不再处理 | `::test_shutdown_cancels_voice_worker` |
| AC-4 失败不静默 | 慢转写期间失败 → degrade 回复仍发出（异步路径） | `::test_degrade_reply_in_async_path` |
| AC-5 幂等 | 同一 voice update 排队两次 → 只建 1 task、只转写 1 次 | `::test_duplicate_voice_updates_single_task` |
| AC-6 worker 韧性 | 转写后主链异常（store 故障）→ 用户收降级回复、worker 存活、下一条正常处理 | `::test_worker_survives_pipeline_error` |
| AC-7 H1 同路 | 转写成功建的 task 与直接发同文字建的 task 走同管道（text/metadata/enqueue 等价） | `::test_transcript_task_equals_text_task` |
| AC-回归 | 既有 voice 测试适配 drain 后语义全保（降级文案/voice_mode/幂等/e2e roundtrip）+ 受影响域 100 passed 基线 + 全量 0 regression + e2e_smoke | 既有套件 |

## 3. Durability trade-off 显式归档（v0.1 接受，不修）

**剥离后 offset 先行确认**：`_ingest_update` 对 voice 快速返回 → polling loop 本轮完成 → offset 确认 → Telegram 不再重发。若进程在"已确认、未转写完成"窗口崩溃，队列中的 voice 消息**丢失**（无降级回复、无 task）。

- **接受理由**：①与 F109 已归档的"并发幂等窗口"同类——单用户 + launchd 常驻（F129 崩溃自愈）下窗口极窄（秒级~数十秒/条）；②baseline 在 webhook 模式同样存在等价窗口（HTTP 200 后进程崩溃在 create_task 前）；③持久化需落盘音频引用 + 重启重放（file_id 有效期、幂等键跨重启对齐），实现代价与收益不成比（v0.1 判断），不为它膨胀范围。
- **graceful shutdown 同窗口**：stop() 时 pending 项直接丢弃（不发降级——shutdown 时网络 send 不可靠且拖慢退出）。
- **若未来要修**：F131 spool 同款思路（SQLite 落 file_id + context 快照，重启 drain 重放），独立小 Feature。

**次级 delta（一并归档）**：baseline 下转写后主链异常会冒泡到 polling loop 兜底 → 整批经 offset 重投重试；F133 后该异常发生在 worker（offset 已确认，无重投）→ 改为降级回复用户 + 日志（AC-6）。语义从"静默自动重试"变为"显式告知用户重试"——用户可见性反而更好（#6/#8）。

## 4. 范围外（观察记录，不动代码）

- **出站 TTS**：挂 `notify_task_result`（H1 后处理），不在 polling 热路径——Piper synthesize 也是 CPU-bound 但已 `asyncio.to_thread`（F110），且 notify 由 task 完成事件驱动、非 ingest 路径，阻塞面不同。观察到的问题记进 completion-report。
- Web 音频上传 / voice session 并发硬化 / spool 持久化 voice 队列。
