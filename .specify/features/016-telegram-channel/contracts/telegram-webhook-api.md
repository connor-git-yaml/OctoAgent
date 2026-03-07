# Contract: Telegram Webhook API

## 入口

- `POST /api/telegram/webhook`

## 请求约束

- body 为 Telegram update JSON
- 当配置了 webhook secret 时，请求必须带 Telegram 官方 secret header
- 校验失败时返回拒绝结果，不创建 Task

## 行为约束

1. 先做 secret 校验
2. 再做 pairing / allowlist / group policy
3. 通过后规范化为 `NormalizedMessage`
4. 调用现有 `TaskService.create_task()`
5. 对重复 update 只允许幂等命中，不得重复建 Task

