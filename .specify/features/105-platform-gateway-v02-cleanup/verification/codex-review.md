# Codex Adversarial Review — F105 v0.2 Cleanup（H-1）

> 命令：`codex exec -s read-only -c model_reasoning_effort=high`（codex-cli 0.133.0），范围 uncommitted diff，
> 聚焦 telegram.py H-1/D17a 逻辑（测试 hermetic 化按 task 标"可跳 review"，仅顺带）。
> 结论：**0 HIGH**。2 MEDIUM + 3 LOW，全部闭环（见下表）。

## Finding 闭环表

| # | Sev | Codex 判断 | 处理 | 闭环方式 |
|---|-----|-----------|------|----------|
| 1 enqueue 前移竞态 | medium | telegram 路径下**非实质问题** | **接受（无需改）** | Codex 实读证实：telegram 完成回复 `_resolve_reply_target()` 取自 `USER_MESSAGE` event metadata（telegram_chat_id/message_id/thread_id/reply_thread_root_id），**不读 conversation_binding**（telegram.py:970）；approval/通知走 `first_approved_user()`。故 enqueue 前移不影响回复路由。推荐的"同步秒回回归测试"列为 deferred 加固项（非缺陷）。 |
| 2 duplicate 补队双执行 | low | 正常路径非问题 | **接受 + 加固** | 真实 `create_job` 对 live job INSERT OR IGNORE no-op。**新增 `test_create_job_noop_on_live_job`** 显式证明（见 #5）。 |
| 3 状态兼容写法 | low | 不是问题 | **拒绝（带理由）** | `str(getattr(task.status,"value",task.status))` 与 slack/discord `_maybe_enqueue` **逐字一致**（slack.py:310 / discord.py:300）。handoff §3 H-1 明确"照搬 slack"，跨平台对称性（Codex M4 亦认可）优先于 telegram 内部复用 `_status_value()`——改单侧会破坏三平台 `_maybe_enqueue` 字节对称。None→"None" 不误判 CREATED，无功能风险。 |
| 4 与 slack 语义等价 | low | 不是问题 | **接受** | 逻辑等价，仅 docstring 异。polling 复用 `_ingest_update`，offset 在成功后才推进，enqueue 抛错自然重读同 update。推荐的 polling retry 测试列为 deferred。 |
| 5 既有测试断言 1→2 | medium | **测试契约风险**（非生产 regression）| **接受 + 关键加固** | FakeRunner 不推进状态→duplicate 补 enqueue 到 2 次是 D17a 预期（与 slack `test_event_id_idempotent_on_retry` 对称）。Codex 核心顾虑：FakeRunner 的"2 次调用" vs 真实系统"只执行 1 次"差异可能被测试掩盖。**已在正确层（job_store）补 `test_create_job_noop_on_live_job`**：证明 create_job 对 QUEUED/RUNNING live job 是 no-op（返 False、不重置），即"补 enqueue 不双执行 live job"的真实幂等保证。与既有 `test_create_job_requeues_terminal_job`（终态可重入队）互补，覆盖此前**未测**的非终态 no-op 分支。 |

## 关键正面确认（Codex 独立实读验证）

- **enqueue-first 前移安全**：telegram 回复路由不依赖 conversation_binding（取 event metadata），消除了"前移引入回复丢失竞态"的最大疑虑——这正是本次顺序决策（对齐 slack/discord）的核心风险点，Codex 独立证伪。
- **无 high 级真实漏洞**；`_maybe_enqueue` 三平台语义等价。

## handoff §6"测试断言是行为契约"红旗的处理

dedup 断言 1→2 是 **spec D17a 显式行为变更区的意图保留式更新**（dedup 核心契约"同一 task / status=duplicate /
created=False"不变，仅 enqueue 计数子断言反映 D17a 补队），与 slack 既合入的 `test_event_id_idempotent_on_retry`
**完全对称**——非"可改契约断言"的新先例。新增 job_store no-op 测试进一步把"真实只执行一次"显式固化，
回应 Codex"不要让 FakeRunner 掩盖真实幂等"的顾虑。

## Deferred（非本次缺陷，列入 handoff 顺手项）

- telegram"同步秒回回复到原 message/thread"回归测试（M1 推荐的加固，当前回复路径已被 Codex 证实正确）。
- polling enqueue-retry 测试（M4 推荐的加固）。
- 二者均属"加固覆盖"而非修复，规模小，下个触碰 telegram 的 Feature 顺手。
