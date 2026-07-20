# F134 Bearer 安全加固 — Feature Spec

> 当前边界：Bearer 是 Gateway 的通用认证模式，不再承担具体远程访问方案的启停或手机分发流程。

## 1. 目标

1. 对错误 bearer credential 做失败限流，减少暴力尝试和日志噪声。
2. 确保正确 credential 不会被攻击者通过共享来源桶锁死。
3. 防止 SSE query credential 出现在 `uvicorn.access` 日志。
4. 保持 `loopback / bearer / trusted_proxy` 现有认证语义与错误契约稳定。

## 2. 失败限流

- 60 秒窗口内累计 10 次错误 credential 后，错误请求进入 300 秒 lockout。
- 限流采用 verify-first：先验证 credential；正确 credential 始终放行并清空对应失败状态。
- `loopback` 模式不进入 bearer limiter；`trusted_proxy` 维持自身认证路径。
- lockout 返回 429 + `Retry-After`，普通认证失败返回 401。
- 不信任客户端提供的转发 header 作为限流 key。

## 3. Access log 脱敏

- `uvicorn.access` 有独立 handler 且 `propagate=False`，必须单独挂脱敏 filter。
- filter 对最终格式参数执行 `redact_sensitive_text`，覆盖 SSE query credential。
- 安装必须幂等，多次 `setup_logging` 不重复挂载。
- 脱敏规则命中后只替换 secret，不破坏状态码、路径结构与诊断信息。

## 4. 验收

- 错误 credential 达阈值后为 429，`Retry-After` 正确。
- lockout 期间正确 credential 仍为 200 并清计数。
- bearer + 各类 proxy hint header 的正确 credential 不被误拒。
- access log 中 bearer、JWT、query credential 均被脱敏。
- loopback/trusted_proxy 既有矩阵零回归。

## 5. 非目标

- 生成、展示或写入 bearer token。
- 远程隧道的安装、启停、状态和 live 探针。
- 把长期 bearer token 分发给手机浏览器。
- 在本 Feature 引入 SSE ticket 或新的认证入口。
