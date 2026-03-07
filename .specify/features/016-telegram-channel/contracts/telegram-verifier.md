# Contract: Telegram Verifier

## 目标

把 015 的 `channel verifier` contract 落到真实 Telegram 渠道。

## `availability()`

返回不可用的典型原因：
- 未启用 `channels.telegram`
- 缺少 `bot_token_env`
- mode 配置不完整

## `run_readiness()`

必须至少验证：
- bot token 可读
- Bot API 可达
- mode 配置自洽
- pairing / allowlist 基础状态可解析

## `verify_first_message()`

MVP 行为：
- 优先向第一个已批准 DM 用户发送测试消息
- 若当前无 approved user，则返回 `ACTION_REQUIRED`，引导先完成 pairing

