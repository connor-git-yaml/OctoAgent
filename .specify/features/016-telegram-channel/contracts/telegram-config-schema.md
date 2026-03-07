# Contract: Telegram Config Schema

## 目标

冻结 `octoagent.yaml.channels.telegram` 的 MVP 字段，供 Gateway、doctor、onboard 和 verifier 共用。

## 必填语义

- `enabled=false` 时渠道不启动
- `mode=webhook` 时必须具备 webhook URL；若声明 secret，则 runtime 必须校验
- `mode=polling` 时不得同时启动 webhook runner
- `dm_policy` 默认 `pairing`
- `group_policy` 默认 `allowlist`

## 最小字段

```yaml
channels:
  telegram:
    enabled: true
    mode: webhook
    bot_token_env: TELEGRAM_BOT_TOKEN
    webhook_url: https://example.com/api/telegram/webhook
    webhook_secret_env: TELEGRAM_WEBHOOK_SECRET
    allow_users: ["123456789"]
    allowed_groups: ["-10011223344"]
    group_policy: allowlist
    group_allow_users: ["123456789"]
```

