# Feature 017 产品调研：Unified Operator Inbox + Mobile Task Controls

**特性分支**: `codex/feat-017-operator-inbox-mobile-controls`
**调研日期**: 2026-03-07
**调研模式**: full（产品 + 技术 + 在线补充）

## 1. 目标重述

Feature 017 的目标不是“把审批列表换个壳”，而是把 OctoAgent 当前分散的 operator 工作面收敛成统一入口：

- approvals 不再单独挂在一个独立 panel；
- watchdog alerts 不再只存在于 journal 查询；
- retry / cancel 不再需要用户记忆隐藏接口或进入不同页面；
- Telegram 不再只充当文本通知器，而是成为可执行操作的移动端入口。

从用户视角，017 要解决的是一个非常具体的问题：

> 当系统每天都在跑任务时，operator 需要一个地方，能立刻看到“现在有哪些待处理动作”，并且能在 Web 或 Telegram 上直接处理，而不是在 approvals、journal、task detail、日志和 Telegram 通知之间来回切换。

## 2. 用户价值

### Owner / 日常操作者

- 在一个入口里看到所有待处理事项：审批、漂移告警、可重试失败、待处理 pairing request。
- 在出门、会议中或移动场景下，直接在 Telegram 上完成高频动作，而不是只能“收到提醒后回电脑”。
- 看到动作之后立即知道结果：成功、被其他端抢先处理、已过期、目标状态已变化。

### 风险控制视角

- 所有 approve / deny / retry / cancel / acknowledge 都在同一审计链里，后续可回放、可追责。
- pending 数量、过期时间、最近动作结果对 operator 可见，减少遗忘和误操作。
- pairing request 不再隐藏在 `telegram-state.json` 里，避免“默认安全前置”在用户侧表现成“机器人没反应”。

## 3. 竞品体验启示

### 3.1 OpenClaw

- `dashboard` 被明确定位为 Control UI，而不是只读看板。
- `approvals` 既能在 dashboard 操作，也能被转发到聊天渠道，体现的是“同一控制动作，多种交互表面”。
- `pairing` 被放在默认安全路径中，说明待配对请求本身就是 operator 的工作项。

直接启示：

1. operator 工作项必须聚合，而不是藏在多处实现细节里。
2. pairing request 不能只写进状态文件，不然用户会把“未授权消息”误解成系统无响应。
3. 渠道消息如果只是通知，不带动作，就达不到“渠道等价操作”。

### 3.2 Agent Zero

- 强调 `Real-time Monitoring and Intervention`，不是“异步告警后手工排查”。
- 强调“随时 stop and intervene”，不是让用户先退出当前执行面。
- 强调 chat save/load 和自动落盘，说明动作必须有结果反馈与可恢复语义。

直接启示：

1. inbox 必须是 action-first，而不是 information-first。
2. 近期动作结果必须显式可见，否则 operator 不知道刚才那次点击有没有生效。
3. action log 必须保留来源渠道（web/telegram），不然跨端协作不可追溯。

## 4. 当前 OctoAgent 用户缺口

### 4.1 控制入口碎片化

当前代码里已经存在：

- `ApprovalPanel` / `useApprovals`：只能处理 pending approvals；
- `GET /api/tasks/journal`：只能看 journal 分组和建议动作；
- `POST /api/tasks/{task_id}/cancel`：只暴露取消；
- `TelegramGatewayService.notify_approval_event()`：只能发“请去 Web 端处理”的通知文本；
- `TelegramStateStore.list_pending_pairings()`：pairing request 只存在于本地状态文件。

这意味着用户必须在多个地方拼装“我现在该做什么”。

### 4.2 Telegram 还不是移动端控制面

当前 Telegram 能做的是：

- 接收新消息；
- 发送任务结果通知；
- 发送审批提醒文本。

但做不到：

- inline approve / deny；
- cancel / retry / alert acknowledge；
- pairing request 处理；
- 动作结果回写同一条消息或同一线程。

这不是“稍差一点的体验”，而是根本还没有移动端等价操作。

### 4.3 任务控制缺少动作结果闭环

用户当前看不到统一的：

- pending 总数；
- 谁快过期；
- 最近一次 operator action 的结果；
- 哪些动作已经被其他端处理。

这会直接导致两个问题：

1. operator 不知道该先处理哪个；
2. 跨端竞态时容易重复点击、重复干预。

### 4.4 pairing request 的产品表面缺失

M2 里 Telegram 默认走 pairing / allowlist 安全前置，但当前没有统一 UI 呈现 pending pairings。

如果 017 不把它纳入 inbox，用户会遇到这种体验：

- Telegram 私聊发来消息；
- Bot 回复 pairing code；
- owner 不知道应该去哪里看到和处理这个请求。

这是非常典型的“底层设计正确，但用户体验断裂”。

## 5. 范围边界

### In Scope（本 Feature 必做）

- 统一 operator inbox（approvals / alerts / retryable failures / pending pairings）
- Web 快速操作入口
- Telegram inline keyboard 等价操作
- 统一动作语义与幂等反馈
- operator action 审计与最近动作结果展示

### Out of Scope（本 Feature 不做）

- 不新增原生 mobile app
- 不重写 ApprovalManager / Task Journal / Telegram ingress 本体
- 不在 017 内实现完整 JobRunner console（归 Feature 019）
- 不引入新的长期运维后台系统

## 6. 成功标准（产品视角）

1. operator 可以在单一 inbox 中看到待审批、漂移告警、可重试失败和 pending pairing request。
2. operator 可以在 Web 或 Telegram 上直接完成核心动作，而不是收到提醒后再跳回另一端。
3. 每个动作之后，系统都能告诉用户结果：成功、过期、已处理、无效或被拒绝。
4. 所有动作都能在后续回放中看见来源、对象、时间和结果。
5. pairing request 不再是隐式状态，而是用户可见、可处理的 operator item。

## 7. 产品风险

- 风险 1：017 变成“把现有 approvals 列表搬到主页”
  - 策略：必须同时纳入 alerts / retryable failures / pending pairings。

- 风险 2：Telegram 仍然只做通知
  - 策略：把 callback query / inline keyboard 作为 017 的 MVP 一部分，而不是后续优化。

- 风险 3：retry / ack 没有统一动作语义
  - 策略：先冻结统一 `OperatorAction` contract，再让 Web 与 Telegram 复用。

- 风险 4：pairing request 纳入范围导致 feature 膨胀
  - 策略：017 只消费 016 已交付的 `TelegramStateStore` 契约，不改 Telegram transport 基础链路。

## 8. 结论

Feature 017 合理且必要。它的本质不是“UI 聚合”，而是 OctoAgent 从“能跑任务”走向“可每天稳定操作”的关键控制层。

建议的 MVP 是：

1. 统一 inbox projection；
2. Web + Telegram 共用动作契约；
3. 动作结果与审计闭环；
4. pending pairing request 明确产品化。
