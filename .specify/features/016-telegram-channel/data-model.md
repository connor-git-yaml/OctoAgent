# Data Model: Feature 016 Telegram Channel

## 1. TelegramChannelConfig

表示 `octoagent.yaml.channels.telegram`。

核心字段：
- `enabled`
- `mode`: `webhook | polling`
- `bot_token_env`
- `webhook_url`
- `webhook_secret_env`
- `polling_timeout_seconds`
- `dm_policy`: `pairing | allowlist | open | disabled`
- `allow_users`
- `allowed_groups`
- `group_policy`: `allowlist | open | disabled`
- `group_allow_users`

## 2. TelegramPairingRequest

表示私聊首次接入待审批项。

核心字段：
- `code`
- `user_id`
- `username`
- `requested_at`
- `expires_at`
- `status`: `pending | approved | expired | rejected`

## 3. TelegramStateSnapshot

项目级持久化快照。

核心字段：
- `pending_pairings`
- `approved_users`
- `allowed_groups`
- `group_allowed_users`
- `polling_offset`
- `updated_at`

## 4. TelegramInboundContext

从 Telegram update 提炼出的最小上下文。

核心字段：
- `update_id`
- `chat_id`
- `chat_type`
- `sender_id`
- `sender_name`
- `message_id`
- `reply_to_message_id`
- `message_thread_id`
- `text`

## 5. TelegramSessionKey

从 inbound context 计算出的稳定路由标识。

核心字段：
- `scope_id`
- `thread_id`
- `reply_to_message_id`
- `message_thread_id`

## 6. TelegramOutboundMessage

发送到 Telegram 的基础回传模型。

核心字段：
- `chat_id`
- `text`
- `reply_to_message_id`
- `message_thread_id`
- `disable_notification`

