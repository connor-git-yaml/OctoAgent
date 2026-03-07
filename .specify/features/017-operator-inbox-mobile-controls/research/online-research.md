---
required: true
mode: full
points_count: 3
tools:
  - perplexity-web-search
  - official-web-docs
queries:
  - "site:docs.openclaw.ai openclaw dashboard approvals pairing control ui telegram"
  - "site:github.com/agent0ai/agent-zero Agent Zero README intervene web ui save and load chats"
  - "Telegram Bot API inline keyboard callback_query official docs"
findings:
  - OpenClaw 的官方路径把 dashboard、exec approvals、pairing 视为同一条 operator control surface，而不是分散在不同工具里。
  - Agent Zero 把“可实时 intervene”“干净的 Web UI”“chat save/load 与自动落盘”作为核心可用性承诺，强调用户在执行过程中始终可控。
  - Telegram 官方 Bot API 的 inline keyboard + callback query 是移动端等价操作的标准机制；只有文本通知而没有 callback action，不构成真正的操作面。
impacts_on_design:
  - 017 需要把 approvals、alerts、retry/cancel、pairing request 聚合为统一 inbox，而不是继续分散在独立 panel、journal 和状态文件中。
  - Web 与 Telegram 必须复用同一动作语义和幂等规则；Telegram 不能停留在“提醒去 Web 端处理”。
  - operator action 必须有明确结果反馈与审计，否则移动端重复点击和跨端竞态会直接损伤可用性。
skip_reason: ""
---

# 在线调研证据（Feature 017）

## Findings

1. **OpenClaw 的 operator 体验是“统一控制面 + 渠道前推”，不是单独的提醒系统**
- OpenClaw 官方文档把 `openclaw dashboard` 定位为 Control UI，入口上直接覆盖 nodes、exec approvals、agents、system events。
- 同时，`openclaw approvals` 文档明确说明审批既可在 dashboard 管，也可被转发到聊天渠道处理。
- `channels/pairing` 文档把 DM pairing 作为默认安全前置步骤，说明 pairing request 也是 operator 需要处理的工作项，而不是隐藏实现细节。
- 这意味着 OctoAgent 的 017 不能只做一个 approvals 面板；它需要成为统一操作收件箱。
- 参考：
  - https://docs.openclaw.ai/cli/dashboard
  - https://docs.openclaw.ai/cli/approvals
  - https://docs.openclaw.ai/channels/pairing

2. **Agent Zero 的用户承诺是“执行过程中随时可介入”，而不是“看通知后再回控制台”**
- Agent Zero 官方 README 明确强调：
  - `Real-time Monitoring and Intervention`
  - `Stop and intervene - Sometimes you don't need to stop everything`
  - `Clean and fluid interface`
  - `Save and load chats`
  - `Logs are automatically saved`
- 这类承诺说明 operator control 的关键不是“知道发生了什么”，而是“在当前界面立即行动，并且动作有结果、有记录、可恢复”。
- 对 OctoAgent 而言，这直接要求 017 同时交付：
  - action-first 的 inbox；
  - 最近动作结果；
  - 跨端一致的审计链。
- 参考：
  - https://github.com/agent0ai/agent-zero

3. **Telegram 官方 Bot API 已经给出了移动端等价操作的标准交互：inline keyboard + callback query**
- Telegram Bot API 官方文档把 `InlineKeyboardMarkup` 与 `CallbackQuery` 作为消息内操作的标准手段。
- 如果系统只有文本通知，没有 callback action，就意味着：
  - 用户必须 context switch 回 Web；
  - 移动端不具备真正的 approve / cancel / ack / retry 能力；
  - 无法在 Bot 消息上直接回显动作结果。
- 因此，017 若要满足“渠道等价操作”，就必须显式引入 callback query 处理链路，而不只是复用现有文本消息接收。
- 参考：
  - https://core.telegram.org/bots/api#inlinekeyboardmarkup
  - https://core.telegram.org/bots/api#callbackquery

## impacts_on_design

- 设计决策 D1：017 的主交付不是“再加一个 approvals 页面”，而是统一 operator inbox projection。
- 设计决策 D2：Web 与 Telegram 必须共享同一 `OperatorAction` 语义与幂等处理，Telegram 不再只发送“请去 Web 端处理”的文本。
- 设计决策 D3：pairing request 必须成为一等 inbox item；否则默认安全前置会在用户视角上变成“消息静默卡住”。
- 设计决策 D4：所有 operator action 都必须产生结果反馈与审计记录，防止移动端重复点击、过期动作和跨端竞态造成混乱。

## 结论

在线证据和本地代码现状一致：Feature 017 的真实价值不是“把现有列表放到一页里”，而是把 `approvals / watchdog alerts / retryable failures / pending pairings` 变成一个统一、可操作、可审计、跨端一致的 control surface。
