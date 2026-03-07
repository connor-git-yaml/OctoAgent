# Feature 017 技术调研：Unified Operator Inbox + Mobile Task Controls

**特性分支**: `codex/feat-017-operator-inbox-mobile-controls`
**调研日期**: 2026-03-07
**调研模式**: full（含在线调研）
**产品调研基础**: `research/product-research.md`

## 1. 调研问题

1. 当前代码里哪些能力已经足够支撑统一 operator inbox？
2. 哪些关键缺口会阻止 Web / Telegram 做真正的等价操作？
3. retry / cancel / ack / pairing request 应该落在哪一层，才能避免再造并行协议？
4. 如何保证动作审计落在既有事件链，而不是再开一套旁路日志？

## 2. 当前代码基线（AgentsStudy）

### 2.1 现有“控制面能力”是分散的，但可复用

- `octoagent/frontend/src/components/ApprovalPanel/ApprovalPanel.tsx`
- `octoagent/frontend/src/hooks/useApprovals.ts`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/approvals.py`
- `octoagent/packages/policy/src/octoagent/policy/approval_manager.py`

当前已经有完整的 approvals 列表与 resolve 链路：

- `GET /api/approvals`
- `POST /api/approve/{approval_id}`
- `ApprovalManager` 会写 `APPROVAL_REQUESTED / APPROVAL_APPROVED / APPROVAL_REJECTED / APPROVAL_EXPIRED` 事件

结论：017 不应该重做 approvals 状态机，而应直接消费这套 contract。

### 2.2 watchdog / journal 已经有“可操作语义”，但还没有 operator product surface

- `octoagent/apps/gateway/src/octoagent/gateway/services/task_journal.py`
- `octoagent/apps/gateway/src/octoagent/gateway/routes/watchdog.py`

`TaskJournalService` 已经提供：

- `running / stalled / drifted / waiting_approval` 分组
- `suggested_actions`
- `last_event_ts`
- `drift_summary`

结论：017 也不应该重做告警检测逻辑，而是把 journal 输出投影成 inbox item，并补足 acknowledge / quick action 入口。

### 2.3 cancel 已有后端链路，但 retry / ack 还没有对应 API

- `octoagent/apps/gateway/src/octoagent/gateway/routes/cancel.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/task_runner.py`

当前取消链路已经存在：

- `POST /api/tasks/{task_id}/cancel`
- `TaskRunner.cancel_task()` 会写 execution cancel 请求并更新任务 / task_job 状态

但当前没有：

- retry API
- alert acknowledge API
- pairing request approve/reject API
- 统一 operator action contract

结论：017 至少需要新增一层 `OperatorAction` 服务和 API，而不是在前端各自拼不同 POST。

### 2.4 retryable failure 有基础数据，但没有产品化入口

- `WorkerReturnedPayload.retryable`
- `OrchestratorRoutingError(..., retryable=True/False)`
- `task_jobs` 表保留 `user_text / model_alias / attempts / last_error`

`SqliteTaskJobStore.create_job()` 支持终态任务重新入队，但当前没有面向 operator 的统一入口；而且直接对原 task 重新执行会受到终态状态机约束。

结论：

1. 017 需要显式定义 retry 的用户语义；
2. 实现层大概率需要“保留原任务链路上的 operator audit，再创建新 attempt / successor execution”；
3. 这一点在 spec 阶段必须先澄清，否则 plan 阶段会反复摇摆。

### 2.5 Telegram 现在只有“通知能力”，没有“操作能力”

- `octoagent/packages/provider/src/octoagent/provider/dx/telegram_client.py`
- `octoagent/apps/gateway/src/octoagent/gateway/services/telegram.py`

现状：

- `TelegramBotClient.send_message()` 只支持发文本；
- `TelegramUpdate` 只建模 `message`，没有 `callback_query`；
- `TelegramGatewayService._extract_context()` 虽然会读取 callback 的 `message`，但实际仍要求 `context.text.strip()`，因此没有文本的 callback action 会被忽略；
- `notify_approval_event()` 只发送“请在 Web 端 approvals 面板完成处理”的文本。

结论：017 若要达成“Telegram inline keyboard 等价操作”，必须补三层：

1. Bot client：支持 inline keyboard、answer callback / edit message 等基础 API；
2. Update model：支持 `callback_query` 解析；
3. Gateway service：支持 callback action -> `OperatorAction` 映射，而不是把 callback 当普通消息。

### 2.6 pairing request 已有状态源，但没有 operator surface

- `octoagent/packages/provider/src/octoagent/provider/dx/telegram_pairing.py`
- `TelegramStateStore.list_pending_pairings()`

当前 pending pairings 已经有稳定存储：

- `user_id`
- `chat_id`
- `username`
- `display_name`
- `requested_at`
- `expires_at`
- `last_message_text`

但没有：

- Web API
- Web 展示
- operator approve/reject action
- 审计事件

结论：017 可以直接消费 `TelegramStateStore` 作为一类 inbox item 源，而不需要改 016 的 transport 逻辑。

### 2.7 前端当前没有统一 operator 页面

- `octoagent/frontend/src/App.tsx` 只有 `/` 和 `/tasks/:taskId`
- `TaskList.tsx` 当前挂的是 `RecoveryPanel`
- `TaskDetail.tsx` 只有事件时间线，没有 operator quick action

结论：017 需要新增明确的 operator inbox 页面或把首页升级成收件箱式控制页；继续叠加单个 panel 会让产品表面越来越碎。

## 3. 参考实现证据

### 3.1 OpenClaw：同一控制动作，多种表面

- `docs.openclaw.ai/cli/dashboard`
- `docs.openclaw.ai/cli/approvals`
- `docs.openclaw.ai/channels/pairing`

启示：

- dashboard 与聊天渠道不应拥有两套控制语义；
- pairing request 应该是 operator 工作项；
- control surface 必须是 actionable 的。

### 3.2 Agent Zero：实时介入与结果可恢复

- `github.com/agent0ai/agent-zero` README

启示：

- 控制面不仅要能操作，还要让用户知道动作是否已生效；
- 动作日志和保存/恢复能力是 operator 信心的一部分；
- “收到提醒后再去别处处理”不符合实时干预产品预期。

## 4. 方案对比

### 方案 A：继续在 approvals panel / task detail / Telegram 文本通知上分别加按钮

- 优点：实现路径短
- 缺点：
  - 继续维持三套入口；
  - 动作语义难统一；
  - 审计事件容易分叉；
  - pairing request 仍难纳入

### 方案 B：新增统一 `OperatorInbox` projection + `OperatorAction` contract（推荐）

- 优点：
  - 聚合源和动作语义一次冻结；
  - Web / Telegram / 未来 PWA 可复用；
  - 审计事件更容易统一
- 缺点：需要新增 projection、API、动作执行层和 Telegram callback 支持

### 方案 C：先做 Web inbox，Telegram 继续只通知

- 优点：实现最快
- 缺点：不满足 M2 的“渠道等价操作”硬要求，用户价值也明显打折

## 5. 技术决策建议

1. **统一模型**：新增 `OperatorInboxItem`，作为 query-time projection，而不是单独持久化表。
2. **统一动作**：新增 `OperatorActionRequest / Result` contract，Web 与 Telegram 共用。
3. **数据源聚合**：
   - approvals -> `ApprovalManager`
   - alerts -> `TaskJournalService`
   - retryable failures -> `task_jobs + task/events`
   - pending pairings -> `TelegramStateStore`
4. **审计事件**：新增 operator action 事件类型，至少记录 `action/source/actor/target/result`。
5. **Telegram 扩展**：补充 inline keyboard / callback query 最小支持，不重做 016 的 ingress 基础链路。
6. **Web 表面**：优先新增统一 inbox 页面，而不是继续在首页堆孤立卡片。

## 6. 风险与缓解

- 风险：retry 语义不清，导致实现层卡在“原 task 重跑还是新 attempt”
  - 缓解：spec 阶段先冻结用户语义为“从来源任务发起重试，并保留来源链路审计”。

- 风险：pairing request 没有 task_id，难落到现有 Event Store
  - 缓解：允许为 pairing action 使用 dedicated operator stream/task，但动作 contract 仍统一。

- 风险：Telegram callback 支持范围膨胀
  - 缓解：017 只做 operator inbox 所需的最小 callback action，不引入复杂菜单系统。

- 风险：Web 与 Telegram 动作并发导致重复处理
  - 缓解：动作服务必须返回幂等结果（success / already_handled / expired / stale_state）。

## 7. 在线补充结论（摘要）

详见 `research/online-research.md`。

- OpenClaw 强化了“统一控制面 + 渠道前推”的 operator 体验。
- Agent Zero 强化了“实时可介入 + 动作结果可信”的 operator 体验。
- Telegram 官方能力表明 inline keyboard / callback query 是 017 达成移动端等价操作的最小必要条件。

## 8. 结论

Feature 017 最佳技术路径是：

- 新增统一 `OperatorInbox` projection；
- 新增统一 `OperatorAction` contract；
- 复用 approvals / journal / task_jobs / telegram-state 既有能力；
- 让 Web 与 Telegram 成为同一动作层的两个交互表面。

这样既能补齐用户体验缺口，又不会破坏 011 / 016 / 019 已经交付的能力边界。
