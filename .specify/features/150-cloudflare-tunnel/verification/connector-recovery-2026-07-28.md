# F150 connector recovery：2026-07-28

## 结论

个人部署出现的 `Bad Gateway` 不是 Gateway 或 Web 代码故障。故障发生在本机
`cloudflared` connector 到 Cloudflare edge 的出站连接：

- Gateway loopback origin 实际返回 `HTTP 200`；
- 个人部署子域的 A/AAAA 记录存在；
- 未认证 HTTPS 请求实际进入 Cloudflare Access，并返回登录重定向；
- 长时间运行的 connector 没有 active connection；
- connector 日志显示 edge 被解析到本机代理/TUN 使用的 `198.18.0.0/15`
  Fake-IP 地址段，TCP `7844` 持续超时。

系统 DNS 恢复返回真实 Cloudflare edge 地址后，只重启既有
`work.maojiwang.octoagent-cloudflared` LaunchAgent。未修改 DNS、Access
application、tunnel、凭证或 Gateway 配置。重启后的事实为：

- `cloudflared tunnel info` 显示同一个 named tunnel 有 active connector；
- connector metrics 显示 `cloudflared_tunnel_ha_connections 4`；
- `cloudflared_tunnel_request_errors 0`；
- 个人部署子域仍返回 Cloudflare Access 登录重定向。

## 验证命令

以下命令在个人部署机器执行；hostname、tunnel UUID、connector UUID、账户标识和
公网 IP 不写入仓库：

```bash
curl --noproxy '*' -D - http://127.0.0.1:8000/
dig +short <personal-hostname> A
dig +short <personal-hostname> AAAA
curl --noproxy '*' -o /dev/null -w '%{http_code}\n' https://<personal-hostname>/
cloudflared tunnel info <tunnel-uuid>
curl --noproxy '*' http://127.0.0.1:20241/metrics
```

实际断言：

| 边界 | 实际结果 | 判定 |
|---|---:|---|
| Gateway loopback origin | `200` | PASS |
| 个人部署 hostname DNS | A 与 AAAA 非空 | PASS |
| Cloudflare Access 未认证入口 | `302` | PASS |
| connector active connections | `4` | PASS |
| connector origin proxy errors | `0` | PASS |

## 未完成边界

本报告不把 Access 登录重定向解释为登录后产品通过。仍需使用真实用户认证态完成：

1. 登录后的 SPA 加载；
2. owner-facing API；
3. Gateway SSE；
4. 刷新、过期、重新认证和登出；
5. Settings 中的 remote-access 用户入口。

在这些浏览器步骤完成前，个人部署状态为
`CONNECTOR_RECOVERED_AUTHENTICATED_PRODUCT_PENDING`。
