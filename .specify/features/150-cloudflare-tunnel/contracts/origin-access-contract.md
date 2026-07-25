# F150 Origin Access Contract

> 本合同只约束电脑 Web hostname 的 Cloudflare Access 回源，不是原生 iOS transport/device contract。

## 输入权威

1. canonical config 只提供 `mode`、`manifest_path` 与 `owner_email`。
2. manifest 只按 `cloudflare-web-access-manifest.v1.schema.json` 解析一次；config、guard、doctor 与 API projection 共享同一 typed object。
3. 远程身份只来自 `Cf-Access-Jwt-Assertion` 的验签结果。Cookie、`Cf-Access-Authenticated-User-Email`、`X-Forwarded-*` 与其它裸 header 不是身份。

## Request classification

| peer | Cloudflare/proxy marker | 结果 |
|---|---|---|
| loopback | 全部缺失 | `direct_local`，沿用既有本机模式 |
| loopback | 任一存在 | `cloudflare_access`，强制验证 Access JWT |
| non-loopback | 任意 | 拒绝；Gateway 本就不应监听该来源 |

marker 集合为所有 `Cf-Access-*`、`CF-*`、`Forwarded` 与 `X-Forwarded-*` header。分类只决定是否强制认证，不赋予任何 header 信任。

## JWT

- header：只读取一个 `Cf-Access-Jwt-Assertion`；重复 header、空白值或多个 token 拒绝。
- algorithm：只接受 `RS256`；key 必须来自 derived JWKS endpoint。
- JWKS：`<access_team_domain>/cdn-cgi/access/certs`；10 分钟 TTL、最多 32 keys、3 秒 timeout、unknown `kid` 单次 single-flight refresh。
- required claims：`iss`、`aud`、`exp`、`iat`、`sub`、`email`；`nbf` 存在时验证。
- 时间：injected UTC clock，最大 60 秒 skew；过期、future `iat`、尚未生效全部拒绝。
- identity：`email.strip().casefold()` 必须等于 `owner_email.strip().casefold()`；`sub` 只作为审计 subject，不扩大 owner。
- 错误：对外统一未授权，不泄漏 claim、token、key、team 或 owner；内部使用稳定 reason code 并进入既有限流。

## Mutation gate

远程 `POST|PUT|PATCH|DELETE` 同时要求：

1. Access JWT 已验证；
2. `Host` 等于 manifest hostname 或 `<hostname>:443`；
3. `Origin` 精确等于 `https://<hostname>`；
4. media type 精确为 `application/json`，可带参数。

任一缺失即拒绝。该无状态 Origin/Host gate 是浏览器 CSRF 边界；不得再引入 Octo session Cookie 或第二 CSRF secret。

## HTTP / SSE / WebSocket

HTTP 与 SSE 使用同一个 dependency 与 verifier。SSE 初连和每次 reconnect 都重新验证。未来若新增 WebSocket，只能复用本 contract，不得添加平行认证分支。
