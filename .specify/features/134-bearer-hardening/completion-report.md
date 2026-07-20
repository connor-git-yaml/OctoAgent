# F134 Bearer 安全加固 — Completion Report

## 结果

已交付并保留两项能力：

1. `FrontDoorGuard` 内部的 verify-first 失败限流；
2. `uvicorn.access` logger 的幂等 secret 脱敏 filter。

具体远程隧道编排、token 自动生成和远程 live 探针不属于当前代码架构，相关历史描述已移除。

## 安全语义

- 60 秒内 10 次错误 credential 触发 300 秒 lockout；错误请求返回 429。
- 正确 credential 始终先被验证，可放行并清除失败状态，避免共享来源 DoS owner。
- bearer 正确 credential 与 proxy hint header 组合继续放行。
- `uvicorn.access` 的路径参数在输出前经过通用脱敏，SSE query credential 不落盘。
- limiter 位于现有 Guard 内，未创建第二认证入口。

## 验证面

- front-door mode×header 矩阵；
- limiter 阈值、窗口、lockout、正确 credential 恢复；
- logging filter 幂等与 secret 扫描；
- gateway 确定性测试和 e2e smoke。
