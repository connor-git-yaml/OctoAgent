# F134 Bearer 安全加固 — Implementation Plan

## 改动面

| 文件 | 内容 |
|------|------|
| `apps/gateway/.../frontdoor_auth.py` | `_FailureRateLimiter`、verify-first 接线、429 错误 |
| `apps/gateway/.../logging_config.py` | `uvicorn.access` 脱敏 filter 与幂等安装 |
| `apps/gateway/tests/test_frontdoor_auth.py` | 限流与既有模式矩阵 |
| logging / core redaction tests | access log 与通用 secret 脱敏契约 |

## 顺序

1. 实现 limiter 纯状态机和受控 clock 单测。
2. 接入 bearer Guard，验证正确 credential 不被 lockout。
3. 给 `uvicorn.access` 安装幂等 filter。
4. 跑 front-door 矩阵、日志脱敏、gateway 回归与 e2e smoke。
5. 同步架构文档与 completion report。

## 风险

- limiter 作为 Guard 实例状态可能跨测试泄漏：每格使用独立 Guard fixture。
- 共享 loopback 来源不能采用 check-before-verify，否则单个错误客户端可锁死 owner。
- access logger 不经过 root handler：必须对其 handler 直接挂 filter。
