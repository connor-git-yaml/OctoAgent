---
required: true
mode: full
points_count: 3
tools:
  - web.run
queries:
  - "Telegram Bot API official docs getUpdates setWebhook message_thread_id secret_token"
  - "aiogram official docs webhook fastapi long polling"
findings:
  - "Telegram 官方 Bot API 规定 webhook 与 getUpdates 互斥；启用 webhook 后不能继续用 getUpdates 拉取更新。"
  - "Telegram webhook 必须是 HTTPS，且官方支持通过 secret token 头做请求鉴权；生产部署端口受限于官方允许列表。"
  - "Telegram Bot API 原生支持 message_thread_id / reply_to_message_id；aiogram 官方文档提供 webhook 与 long polling 的标准入口，可与自定义 Web 框架集成。"
impacts_on_design:
  - "Gateway 必须把 webhook 与 polling 设计成互斥运行模式，并在 fallback 切换时保持幂等。"
  - "webhook 路由必须校验 Telegram secret token，不能只靠 path obscurity。"
  - "thread/topic 映射应复用 Telegram 原生 message_thread_id，而不是自造 thread 规则。"
---

# 在线调研证据：Feature 016 Telegram Channel

## 调研点 1：Telegram Bot API 的 webhook / polling 互斥关系

**来源**:
- https://core.telegram.org/bots/api#getupdates
- https://core.telegram.org/bots/api#setwebhook

**结论**:
- 官方明确指出：当 bot 使用 webhook 时，`getUpdates` 不可用。
- 这意味着 016 不能把 webhook 与 polling 设计成并行双活；fallback 必须是“先停一个，再启另一个”的单实例切换。

**对设计的影响**:
- 需要在 Telegram runtime 中冻结互斥模式与状态快照
- mode 切换后仍要依赖 update 级幂等，避免模式切换窗口出现重复消息

## 调研点 2：Webhook 安全约束

**来源**:
- https://core.telegram.org/bots/api#setwebhook

**结论**:
- Telegram webhook 只接受 HTTPS 地址
- 官方支持 secret token header 作为 webhook 请求鉴权手段
- 官方对可用端口有限制，生产部署要考虑 TLS/反向代理

**对设计的影响**:
- 016 需要在 config / doctor / verifier 中显式区分“本地开发 polling”与“生产 webhook”
- webhook route 必须校验 secret token，缺失或不匹配时 fail-closed

## 调研点 3：Thread / Reply 与 aiogram 集成方式

**来源**:
- https://core.telegram.org/bots/api#sendmessage
- https://docs.aiogram.dev/en/latest/dispatcher/webhook.html
- https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html

**结论**:
- Telegram Bot API 原生支持 `message_thread_id` 与 `reply_to_message_id`
- aiogram 官方提供 webhook 与 long polling 两类 Dispatcher 入口，并支持对接自定义 Web framework

**对设计的影响**:
- 016 应直接保留 Telegram 原生 thread/reply 字段，减少自定义映射损耗
- 使用 aiogram 可以降低手写 update parsing / lifecycle 的维护负担，更贴合蓝图指定技术栈

