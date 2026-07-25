# F150 Data Model

## `FrontDoorConfig` 增量

| 字段 | 类型 | 约束 |
|---|---|---|
| `mode` | literal | 在既有 `loopback|bearer|trusted_proxy` 上新增唯一 `cloudflared` |
| `manifest_path` | project-relative path or null | `cloudflared` 时 required；项目配置根内普通文件，拒绝 symlink/越界 |
| `owner_email` | string or null | `cloudflared` 时 required；trim、有效 email、casefold 后比较 |

其它 Cloudflare facts 不进入 root config，也不存在环境变量 override。

## `CloudflareWebAccessManifestV1`

精确 schema 位于 `contracts/cloudflare-web-access-manifest.v1.schema.json`。它是电脑 Web Access 的 immutable typed config input，不是 credential、runtime state 或 iOS transport contract。

## `RemoteAccessStatus`

| 字段 | 类型 | 来源 |
|---|---|---|
| `state` | `unconfigured|pending_verification|ready|fault` | config + manifest + service/probe facts 派生 |
| `hostname` | string or null | validated manifest |
| `owner_email` | masked string or null | canonical config；普通 UI 只显示脱敏值 |
| `last_verified_at` | UTC timestamp or null | 当前进程最近成功探测，不持久化 |
| `reason_code` | stable enum or null | typed validation/doctor result |
| `recovery_action` | stable enum or null | application projection |

禁止字段：JWT、Cookie、credential path content、service token、JWKS body、raw header、Access identity claim dump。

## `CloudflarePrincipal`

只在单次电脑 Web request scope 内存在：`subject`、normalized `email`、`issued_at`、`expires_at`、`key_id`。不得持久化、写入日志或发送给普通 UI。

## iOS 设备数据边界

F150 不创建 iOS device、challenge、capability token 或 revoke record。它们是 F153 threat model 和真机 transport spike 的输出，不能由 Web manifest、Access Cookie 或 `CloudflarePrincipal` 推导。F153 可以复用同一 named tunnel，但必须单独冻结设备身份 schema。

## 状态推导

- `unconfigured`：mode 不是 `cloudflared`，或 cloudflared fields 尚未配置。
- `pending_verification`：config/manifest schema 有效，但本进程尚无完整 service + origin probe 成功事实。
- `ready`：config/manifest、service、loopback origin 与 Access/JWKS probe 全部成功。
- `fault`：已声明 cloudflared mode，但任一 required fact 无效或当前 probe 失败。

状态不可手写、不可通过前端 patch、不可在数据库中另存。
