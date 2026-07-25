# T015 Uvicorn 回源 peer 完整性修复报告

## 问题描述

最终 named tunnel 发布验证中，Cloudflare Access 已认证的浏览器可以读取 SPA
与静态资源，但所有 owner-facing API 都返回
`403 FRONT_DOOR_ORIGIN_NOT_LOOPBACK`，因此 T015 没有形成有效 PASS 证据。

## 5-Why 根因追溯

| 层级 | 问题 | 发现 |
|---|---|---|
| Why 1 | 为什么受保护 API 返回 403？ | `FrontDoorGuard` 观察到的 client host 是公网访客 IP，而不是 loopback。 |
| Why 2 | 为什么真实 loopback 回源变成公网 IP？ | Uvicorn 默认启用 proxy headers，并信任来自 `127.0.0.1` 的 `X-Forwarded-For`。 |
| Why 3 | 为什么 production entry 允许该改写？ | 唯一 `uvicorn.run(...)` 调用没有显式关闭 `proxy_headers`。 |
| Why 4 | 为什么默认行为不符合 F150 合同？ | F150 的安全判断要求使用实际 TCP peer；`X-Forwarded-*` 只能作为不可信 marker，不能改写信任来源。 |
| Why 5 | 为什么现有测试没有发现？ | L4/L3 测试直接注入 ASGI peer 或使用 TestClient，没有覆盖 production module entry 与 Uvicorn ProxyHeadersMiddleware 的组合。 |

**Root Cause**：production module entry 依赖 Uvicorn 的 proxy-header 默认值，破坏了
F150“真实 TCP peer 必须为 loopback”的安全不变量。

**Root Cause Chain**：Access 浏览器 API 403 → Guard 收到公网 client →
Uvicorn 信任 loopback 代理的 XFF → module entry 未关闭 proxy headers →
测试未覆盖真实 production 启动边界。

## 影响范围

### 同源问题

| 文件 | 位置 | 修复动作 |
|---|---|---|
| `octoagent/apps/gateway/src/octoagent/gateway/__main__.py` | 唯一 `uvicorn.run` | 显式设置 `proxy_headers=False`。 |
| `octoagent/apps/gateway/tests/test_main.py` | production module-entry contract | 增加 exact RED，冻结 raw TCP peer 不被转发头改写。 |

### 类似模式

- `cloudflared` mode：当前真实阻断，必须修复。
- `trusted_proxy` mode：同样应以实际 TCP peer 判断代理 CIDR；关闭改写符合既有语义。
- `loopback` / `bearer`：直接访问不受影响；带代理 marker 的 loopback 请求继续按既有
  Guard fail closed。
- test-only L1 Gateway：不是 production entry，不作为第二修复点。

## 修复策略

### 方案 A（采用）

在唯一 production `uvicorn.run` 调用显式传入 `proxy_headers=False`。该方案保持
ASGI `scope["client"]` 为真实 TCP peer，同时仍把 `X-Forwarded-*` 原样留给
F150 classifier 作为不可信 marker。

### 方案 B（拒绝）

仅设置 `forwarded_allow_ips=""` 或在 Guard 内猜测/恢复原始 peer。前者语义不如
显式关闭清楚，后者会引入第二 peer authority，并可能再次错误信任可伪造 header。

## Spec 影响

F150 Spec 已明确“peer 为 loopback”且“`X-Forwarded-*` 不可信”，无需改变产品需求；
只需同步 Plan、Tasks、Trace 与部署/架构说明。
