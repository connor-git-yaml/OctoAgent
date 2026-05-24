# F102 Clarify Questions

## CQ-1: AC-D3 channel 过滤与 notify_task_state_change 签名矛盾（spec 内部矛盾）

- **指向**: spec.md §4 AC-D3 / §5 FR-B7 / §8.2
- **问题描述**: AC-D3 要求当 `summary_channels: "telegram"` 时只推 Telegram、不推 Web SSE。但实测 `notify_task_state_change`（`notification.py:468`）签名中**没有 channel 过滤参数**，它直接对所有已注册 channel 循环调用 `channel.notify()`（第 561-563 行）。FR-B7 给出的调用样板也没有 channel 参数。这意味着 AC-D3 无法通过现有 NotificationService 接口实现，需要修改接口或增加新路径——这是 spec 内部矛盾，**必须在 plan 前解决**。
- **候选答案**:
  - A：在 `notify_task_state_change` 加 `channels: frozenset[str] | None = None` 可选参数，None 时推所有 channel（向后兼容），F102 传入 `summary_channels` 值
  - B：DailyRoutineService 自行持有 channel 引用，绕过 NotificationService 直接调用指定 channel——违反 H1 架构，拒绝
  - C：`summary_channels` 字段实质是"用户希望 daily summary 优先推哪里"的 UI 语义，而非当前强制路由——即 F102 不做 per-call channel 过滤，`summary_channels` 字段解析但在 F102 不生效，留 M6 实现
- **推荐**: A（向后兼容地扩展接口，最小改动，语义清晰）
- **影响**: 不解决则 AC-D3 形同虚设，plan 实施时会在接入层发现无法测试该 AC，直接阻塞

---

## CQ-2: attention_count 的具体计算算法未定义

- **指向**: spec.md §4 AC-E4 / §5 FR-B2 步骤 6 / §9 风险表
- **问题描述**: spec 在风险表说"F102 使用全局昨日 task 层面的 attention_count（WAITING_INPUT + WAITING_APPROVAL + failed 等状态）"，但 FR-B2 步骤 6 只说"汇总 attention_count"，AC-E4 只说"昨日结束时仍处于需关注状态的 task 数量"，三处描述语义不完全一致。核心歧义：
  1. 是"昨日结束时（yesterday_end 时刻）**仍处于**某状态"，还是"昨日内**曾经出现过** attention 状态"（累积计数）？
  2. 哪些 TaskStatus 算 attention？风险表给出了 "WAITING_INPUT + WAITING_APPROVAL + failed 等"，`worker_service.py:1456` 的实现是 `{"waiting_input", "waiting_approval", "paused", "escalated", "failed"}`——F102 是复用这个集合还是另定？
  3. "昨日结束时的状态"需要查每个 task 的最后一条 STATE_TRANSITION 事件——这相比简单查 task.status 字段多了一层复杂度，对"仍处于"语义的要求不同。
- **候选答案**:
  - A：使用 task 表当前 `status` 字段（查询时点的实时状态），属于昨日 task 中当前仍在 attention 状态集合的数量；复用 `worker_service.py` 的 attention_statuses 集合
  - B：通过昨日 task 的最后一条 STATE_TRANSITION 事件的 `to_status` 判断"昨日结束时刻"的状态——更准确但增加 N+1 event 查询复杂度
  - C：累积计数：昨日内曾经处于 attention 状态的 task 数（无论之后是否恢复）——误导性更强，拒绝
- **推荐**: A（简单、与现有 worker_service attention 语义一致、性能好；"task 表 status 字段"在 daily summary 时间点就是昨日结束时的状态）。spec 中 attention_statuses 应明确列出（复用 worker_service 的 5 个状态）
- **影响**: 不解决则 plan 实施时有多种理解，`attention_count` 测试 fixture 无法对齐

---

## CQ-3: cron 表达式动态重载机制缺失

- **指向**: spec.md §4 AC-B6 / §5 FR-B1
- **问题描述**: FR-B1 说 `replace_existing=True` 保证"重启后不重复注册"，AC-B6 说"系统启动时 startup() 执行"时 job 被注册。但没有说明：**用户在运行期修改 USER.md `daily_summary_time` 后，cron 表达式何时生效**？有两个隐性假设分支：
  - 分支 A：只有重启后 startup() 才重新读取并注册新的 cron 表达式（当前 spec 暗示的行为）
  - 分支 B：每次 routine 触发前重新读配置，动态更新 cron 表达式（需要 `scheduler.reschedule_job`）
  分支 A 意味着用户改了 daily_summary_time 后，今天还是在旧时间触发，明天才在新时间——这是可接受的 trade-off 但 spec 没有明说。
- **候选答案**:
  - A：重启生效（简单，`replace_existing=True` 的语义就是重启时覆盖已有 job）；spec 在 handoff 说明中注明"修改 daily_summary_time 需重启生效"
  - B：每次 routine 触发后（即 `_run_daily_summary` 完成后）重新读配置，若时间改变则调用 `scheduler.reschedule_job` 更新 cron——需要增加实现复杂度
- **推荐**: A（重启生效，YAGNI；F102 复杂度控制为 MEDIUM，动态重载超出范围）。spec AC-B6 应显式说明"重启后生效"
- **影响**: 不影响 plan 主路径，但影响用户说明文档和 handoff 内容

---

## CQ-4: LLM prompt 设计和 token budget 完全缺失

- **指向**: spec.md §5 FR-B3
- **问题描述**: FR-B3 给出了 fallback 模板格式（确定性，合理），但 LLM 路径的 prompt 设计完全缺失：
  1. prompt 内容是什么？把所有昨日 task 列表 + 每个 task 的 events 全部 stringify 后传给 LLM？
  2. token budget 是多少？`max_tokens=512` 是摘要输出长度上限，但输入侧（task 列表 + events）没有限制——昨日有 50 个 task 时输入可能爆炸
  3. 是否有 input token 截断策略？对任务量大的情况应该如何处理（截取最近 N 个 task？只取 failed + attention 的 task events？）
  这直接影响 LLM 路径的可靠性和质量，但 spec 没有设计。
- **候选答案**:
  - A：plan 阶段定义 prompt 模板（框架）+ input token budget（如最多 3000 token 输入）+ 超限时自动截取策略（优先保留 failed 和 attention task 的 events）；将此作为 Phase B 或 Phase E 的 implementation detail
  - B：spec 阶段就把 prompt 框架写进 FR-B3（至少 prompt 结构 + token 上限 + 截断策略）——增加 spec 精确度
- **推荐**: A（prompt 模板是实现细节，由 plan 阶段决定；但 spec 应在 FR-B3 补充"max input tokens 上限（建议 3000）"和"input 超限时优先保留 failed + attention task"的截断策略方向）
- **影响**: 不解决则 plan 实施时 FR-B3 LLM 路径实现质量无法验收，`AC-B3` fallback 测试也无法验证"LLM 路径真的走了"

---

## CQ-5: DailyRoutineService bootstrap 构造的具体位置

- **指向**: spec.md §5 FR-DI1
- **问题描述**: FR-DI1 说"在 `octo_harness._bootstrap_executors`（或等价 bootstrap 步骤）中构造并注入"，这个"或等价"掩盖了一个具体问题：`DailyRoutineService` 依赖 6 个组件（scheduler / task_store / event_store / notification_service / snapshot_store / provider_router），其中 `notification_service` 本身在 bootstrap 时有延迟绑定机制（`bind_snapshot_store` / `bind_event_store`）。如果 `DailyRoutineService` 在 `notification_service` 完成 bind 之前构造，startup() 可能在 notification_service 未完全就绪时调用。
- **候选答案**:
  - A：`DailyRoutineService` 在 `notification_service` bind 完成后构造（即在 `_bootstrap_executors` 末尾，或专门的 `_bootstrap_routines` 步骤之后）；plan Phase 0 侦察确认 notification_service bind 顺序
  - B：`DailyRoutineService.startup()` 加懒初始化检查（调用 notify 前确认 notification_service 已就绪）——增加防御性代码
- **推荐**: A（正确的 DI 顺序比防御性代码更清晰；plan Phase 0 必须实测 bootstrap 顺序）
- **影响**: 若顺序错误则 startup 时 scheduler 注册成功但第一次 cron 触发时 notification_service 状态不确定，难以复现的 race

---

## CQ-6: list_tasks_in_time_range 的时区语义（UTC vs 用户本地时间）

- **指向**: spec.md §4 AC-T1 / §6 NFR-3
- **问题描述**: AC-T1 说 `start=yesterday_start, end=yesterday_end`，但没有说明这两个 datetime 是 UTC 还是用户本地时间。NFR-3 说 `daily_summary_time` 按 `active_hours` 同一时区解释，cron 用 `user_timezone`——那么"昨日"的定义也应该是用户本地时间的 00:00-23:59：59，即 `yesterday_start = (today_local - 1day).replace(hour=0, minute=0, second=0)`，转换为 UTC 后再传入 SQL 查询。但 tasks 表的 `created_at` 列是 UTC 还是本地时间存储？这影响查询的正确性。
- **候选答案**:
  - A：tasks 表 `created_at` 存 UTC（OctoAgent 标准——所有时间戳均 UTC 存储）；`yesterday_start` / `yesterday_end` 按用户本地时区计算后转为 UTC datetime 传入查询；spec 在 FR-T1 明确说明参数必须是 aware datetime（UTC）
  - B：直接用 UTC 的昨日（00:00 UTC - 24:00 UTC）——与 cron 触发时间语义不一致，用户在 UTC+8 时区时"昨日"会错位 8 小时
- **推荐**: A（一致性：cron 用 user_timezone，查询的"昨日"也应按 user_timezone 定义，转 UTC 后查 DB）。spec AC-T1 应补充"start/end 为 UTC-aware datetime，由调用方负责本地时区→UTC 转换"
- **影响**: 不解决则跨时区用户收到的"昨日摘要"内容时间段错误

---

## CQ-7: 空数据摘要的推送决策（用户噪音问题）

- **指向**: spec.md §4 AC-B5
- **问题描述**: AC-B5 说昨日无任务时"摘要内容为'昨日无 Worker 任务'，通知按 LOW 推送"。但实际用户场景：如果用户周末不使用 OctoAgent，连续两天都会收到"昨日无 Worker 任务"的 LOW 通知——这是噪音。spec 没有处理这个情况，也没有提供关闭空数据通知的选项。
- **候选答案**:
  - A：空数据时不推送通知（静默跳过），仍写 `ROUTINE_COMPLETED` event（`worker_count=0`）；用户不会收到无意义的"无任务"通知
  - B：按 spec AC-B5 推送 LOW 通知（现有设计）；用户可通过调低 notification 优先级或在 quiet hours 内过滤
  - C：新增 USER.md 字段 `skip_empty_summary: "true"/"false"` 控制——引入新字段，增加复杂度
- **推荐**: A（更好的默认 UX；空数据场景 routine 不失败，event 有记录，audit 链完整；不推送不影响 Constitution）。spec AC-B5 改为"昨日无任务时，写 ROUTINE_COMPLETED（worker_count=0, fallback=false），不推送通知，不写 ROUTINE_FAILED"
- **影响**: 影响 AC-B5 测试预期和 plan 实施代码分支

---

*共 7 条 CQ。CQ-1 是 spec 内部矛盾（接口签名不支持 channel 过滤），必须在 plan 前解决。未列入的候选盲区判断：AC-B4 quiet hours heads-up 机制（spec 风险表已明确 LOW + D5 决策 no 补发，trade-off 已接受，无需澄清）；FR-DI1 "或等价"措辞模糊（CQ-5 已覆盖）。*
