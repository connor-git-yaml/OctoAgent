# F133 — voice 从 polling 剥离 · Completion Report

> M8 P1 · 分支 `feature/133-voice-async`（off master `89ca6bc8`，含 F131/F132）
> 完成日期：2026-07-06 · 双评审：Codex 1 P2 闭环 + re-review 0 finding；Opus 对抗自审 2 归档项
> **未 push origin，等用户拍板。**

---

## 1. 诊断复核结论（主 session 判断 vs 实测）

| # | 主 session 判断 | 复核 | 证据（master 89ca6bc8）|
|---|----------------|------|------|
| ① | polling loop 串行分发 | ✅ 成立 | `telegram.py:516-517` `for update in updates: await self._ingest_update(update)` |
| ② | voice 整条 pipeline 内联 ingest | ✅ 成立 | `telegram.py:756-760` → `_handle_voice_message`(:831-921) 内联幂等预检+get_file(:873)+下载(:877)+转写(:893)+voice_mode(:919) |
| ③ | STT backend 是否已 to_thread | ✅ **已卸载**（`faster_whisper_backend.py:90-92`）——event loop 不被 CPU 卡死（问题①不存在）；**核心缺口=问题②**：polling 协程串行 await 整条 pipeline，60s 语音卡 polling 秒级~数十秒，同批文字消息全延迟 | — |
| ④ | 失败降级已有 | ✅ 成立 | `_reply_voice_degrade`(:923) 五分支 |

**附带发现**：webhook 模式 baseline 内联转写超 Telegram webhook 超时会触发重投，重投副本与首投**并发**转写（F109 已归档"转写前并发幂等窗口"）——F133 全局串行队列顺带闭合该窗口。

## 2. 设计与实际改动

### 剥离机制（全按 spec §1 落地，无偏离）

- `_ingest_update` voice 分支 → `_enqueue_voice_processing`：入全局 FIFO `asyncio.Queue` + lazy spawn worker，**立即返回** `accepted+voice_queued`（route 层 `accepted→200` 零改动）。
- `_voice_worker_loop`：单 consumer 串行（**并发上界=1**，faster-whisper CPU-bound 防并发打爆；全局 FIFO ⊇ 同 chat FIFO 保序）；`_handle_voice_message` **逻辑零改动**仅挪调用点；转写成功 → `_ingest_text_context`。
- `_ingest_text_context`：baseline `_ingest_update` 后半段（空文本检查→control command→建 task→enqueue→binding→reply-thread）**原样抽取**——git diff 确认抽取区域零修改行（H1：只挪"何时跑"，不改"跑什么"）。
- shutdown：cancel 列表加 `_voice_worker_task` + **显式清空 pending 队列**（Codex P2）；worker 意外异常 → `_VOICE_DEGRADE_PIPELINE` 降级回复（不静默，#6）。
- 幂等预检留在 worker 处理时点：串行使 webhook 重投/崩溃重投的重复副本必然命中首条已建 task → duplicate 跳过。

### F131 协同（零回退验证）

`_polling_loop` / `_spool_drain_loop` / `_send_or_spool` / 退避 / 409 识别**零触碰**（git diff 无一行落在这些函数）；F131 两个测试文件全绿。voice 剥离让 polling 一轮更快完成，退避状态机语义不变。

## 3. Durability trade-off 归档（v0.1 接受，不修）

1. **offset 先行确认窗口**（主）：voice 排队即返回 → polling 本轮完成 → offset 确认 → Telegram 不重发。进程在"已确认未转写"窗口崩溃 → 排队语音丢（无 task 无降级回复）。接受理由：与 F109 幂等窗口同类；launchd 常驻（F129）下窗口秒级~数十秒；webhook baseline 本就有等价窗口；持久化需落盘 file_id+重启重放（file_id 有效期/跨重启幂等对齐），代价收益不成比。`test_polling_loop_not_blocked_by_slow_voice` 对该窗口做了实证（转写挂起时 offset 已 903）。若未来要修：F131 spool 同款思路独立小 Feature。
2. **graceful shutdown 同窗口**：pending 显式丢弃（Codex P2 后与 spec 语义严格一致），不发降级回复（shutdown 时网络 send 不可靠）。
3. **次级 delta**：转写后主链异常从"polling 兜底 offset 重投静默重试"变为"降级回复显式告知用户重试"（offset 已确认无重投机会）——用户可见性更好。
4. **voice→text 跨类型乱序**（Opus 自审归档）：baseline 全局串行使 text 严格排在先到的 voice 后；F133 后 text 不等 voice（这是 Feature 目的本身）。连续"voice+补充 text"语义关联场景会先处理 text——单用户低频可接受，voice 间仍 FIFO。

## 4. 双评审闭环

| 轮 | 结果 | 处理 |
|----|------|------|
| Codex 一轮（`codex review --base master`）| **0 HIGH + 1 P2**：shutdown 只 cancel worker 不清队列——同实例 shutdown→startup 复用会复活 stale voice，违背 spec"随进程丢弃"语义 | **接受修复**（304eb583）：shutdown 里 `get_nowait+task_done` 成对清空（保 `queue.join()` 计数）；AC-3 加 `qsize==0` 断言 |
| Codex re-review | **0 finding**（"未发现会破坏现有行为或新增阻塞性问题的缺陷"，其自行跑了 voice/F133 测试）| 收敛 |
| Opus 对抗自审 | 18 项挑战面逐项过（task 泄漏/shutdown race/饥饿/乱序/F131 语义/并发 send/unbounded queue/loop 兼容/context 可变性等）。2 归档项：①voice→text 乱序（→§3.4）；②shutdown→startup 复用复活 pending（与 Codex P2 同源，已修）| 0 阻塞 |

**必须人裁清单：无**（Codex 与 Opus 自审无分歧项——P2 双方同源发现且已修复）。

## 5. 改动清单 + 回归

| 文件 | 变更 | 说明 |
|------|------|------|
| `apps/gateway/src/octoagent/gateway/services/telegram.py` | +105/-9 | `_enqueue_voice_processing` / `_voice_worker_loop` / `_ingest_text_context` 抽取 / shutdown 清队列 / `_VOICE_DEGRADE_PIPELINE` |
| `apps/gateway/tests/test_f133_voice_async.py` | +437（新）| 8 测试：AC-1 存在性证明×2（含 polling 集成版实证 offset 先行）/ AC-2 FIFO / AC-3 shutdown / AC-4 降级 / AC-5 幂等 / AC-6 worker 韧性 / AC-7 H1 同路 |
| `apps/gateway/tests/test_telegram_voice.py` | +85/-27 | 12 个 voice-ingesting 测试适配 drain 语义（`_drain_voice` helper + shutdown 收尾），既有断言全保 |
| `.specify/features/133-voice-async/`（spec + 本报告）| 新 | 诊断+设计+AC+trade-off 归档 |
| `docs/blueprint/milestones.md` + `docs/codebase-architecture/platform-gateway.md` §6 | 更新 | F133 标 ✅；§6 接入点改异步描述 + F109 幂等窗口标闭合 + F133 limitation（living-docs 闸）|

**净变化**：生产代码 +96 行；测试 +495 行。

**回归**：
- 受影响域（telegram×4 + f131×2 + f105×4 + f133）：baseline 100 → 改后 **108 passed**（+8 新增，0 regression）
- 全量（apps+packages，除 e2e_live）：baseline **4598 passed / 3 skipped / 1 xfailed / 1 xpassed** → 改后 **4606 passed**（=4598+8）/ skipped·xfailed·xpassed 完全一致，**0 regression**
- e2e_smoke：**8/8 passed**
- ruff：改动文件 4 项报错全 pre-existing（baseline 同报），新增代码 0 错误

**commit 链**（worktree 分支，未 push）：
- `29201d97` feat: 剥离主体 + 8 新测试 + 12 适配 + spec
- `304eb583` fix: Codex P2 shutdown 清队列
- （本 commit）docs: completion-report + milestones + platform-gateway §6

## 6. 范围外观察（不动代码）

- **出站 TTS**：`notify_task_result`(:1429) → `_try_send_voice_reply` → Piper synthesize（已 to_thread，F110）→ `send_voice`。调用链由 task 完成回调（`registry.notify_task_completion`）驱动，**不在 polling 热路径**——无 F133 同类问题。潜在观察点：TTS 合成（秒级）同步在 notify 链路内，会延迟该条回复送达但不阻塞 ingest；如未来 TTS 变慢可仿 F133 剥离，目前无证据需要。
- Web 音频上传 / voice session 并发硬化 / spool 持久化 voice 队列：均维持范围外。

## 7. Constitution / H1 合规

- **H1**：转写成功走 `_ingest_text_context`（baseline 原样抽取），主 Agent 输入无差别；worker 只做预处理不抢话。
- **#6 降级**：五分支降级保留 + worker 意外异常新增降级（不静默）；STT 未装/未配依旧优雅降级。
- **#1/#8**：`telegram_voice_queued`（含 queue_depth）/ `telegram_voice_async_pipeline_failed` 结构化日志；durability 窗口显式归档而非隐藏。

## 8. 建议

**建议合入 origin/master**。理由：0 HIGH 残留（Codex 收敛 + Opus 自审无分歧）；4606 passed 全量 0 regression + e2e_smoke 8/8；F131 语义零回退实证；durability trade-off 已显式归档并有测试实证；生产代码改动面小（单文件 +96 行）且抽取部分字节级零修改。
