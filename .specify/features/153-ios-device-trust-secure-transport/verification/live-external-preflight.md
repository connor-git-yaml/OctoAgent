# F153 T014 Cloudflare Mobile Live Preflight

## 状态

- 初始预检日期：2026-07-31
- 2026-08-01 状态：`EXECUTED_PARTIAL`
- 本文保留写前事实和执行清单；它本身不是 T014 live evidence。
- 用户已授权并完成 DNS/tunnel/Access/个人实例启用；负向 live 结果见
  `../evidence/live/2026-08-01/negative-matrix.md`。真机正向 device-proof 链仍缺。

## 当前直接事实

- 个人部署 Web hostname：`octo.maojiwang.work`
- 计划中的原生 iOS hostname：`ios.maojiwang.work`
- named tunnel：
  `19957901-f4e1-4cb0-b387-37258436644d`（`octoagent-personal`）
- 当前 tunnel 有 4 条 active connection。
- 写前 ingress 只有：
  `octo.maojiwang.work -> http://127.0.0.1:8000`；2026-08-01 已在同一 tunnel 增加
  `ios.maojiwang.work -> http://127.0.0.1:8000`。
- 写前 `ios.maojiwang.work` 没有 DNS；2026-08-01 已解析并通过 HTTPS 负向矩阵。
- 写前个人实例 `octoagent.yaml` 没有启用 `mobile_device_access`；2026-08-01 已通过
  manifest/config 启用且 doctor 报告 PASS。
- `CLOUDFLARE_API_TOKEN` 不在当前进程环境；本机有合法 `cloudflared`
  account certificate，因此 DNS route 可由 `cloudflared` 创建，Access application
  仍需使用已登录 Cloudflare Dashboard。
- Chrome、Browser extension 与 native host 的机械诊断均通过；现有 Cloudflare
  Dashboard 标签可枚举，但自动化接管/导航仍超时。执行 Dashboard 写操作前必须取得
  用户对打开新 Chrome profile 窗口的单次授权。

## 冻结安全边界

1. 继续使用同一个 named tunnel 和同一个 loopback Gateway；禁止第二 tunnel、
   Worker verifier、WARP、WebView 或移动端 service token。
2. 新 Access application 只覆盖：
   `ios.maojiwang.work/api/mobile/v1/*`。
3. 该 application 只有一条 `Bypass / Include Everyone` policy。Bypass 只跳过
   Cloudflare Access，不是设备身份边界。
4. Gateway 继续以 Host/path allowlist、Secure Enclave device proof、短期 opaque
   token、timestamp、nonce、signature、capability、replay store 和 revoke
   fail closed。
5. `octo.maojiwang.work` 现有 Web Access application、Allow policy 与 owner session
   不得修改。
6. `maojiwang.work` 只属于本次个人部署事实，不进入产品默认值、源代码常量或测试
   oracle。

Cloudflare 官方当前合同：

- 更具体的 application path 优先，不继承较宽路径的 policy：
  <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/>
- 公开 endpoint 应创建独立 path application，并将 Bypass 缩到最小范围：
  <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/common-policies/>
- Bypass 会关闭 Access enforcement 和 Access request logging：
  <https://developers.cloudflare.com/cloudflare-one/access-controls/policies/>

## 获准后的唯一执行顺序

### 1. 写前快照

记录以下对象的 SHA-256、size 与有效字段，禁止输出 credential 字节：

- `~/.cloudflared/config.yml`
- `~/.octoagent/octoagent.yaml`
- 当前 tunnel list/connection
- 当前 Web Access application domain/path/policy
- `ios.maojiwang.work` 当前 DNS 结果

### 2. 创建 tunnel DNS route

```bash
cloudflared tunnel route dns \
  19957901-f4e1-4cb0-b387-37258436644d \
  ios.maojiwang.work
```

禁止 `--overwrite-dns`。若 hostname 已出现任何冲突记录，立即停住，不覆盖。

### 3. 扩展同一 ingress

在 catch-all `http_status:404` 前加入：

```yaml
- hostname: ios.maojiwang.work
  service: http://127.0.0.1:8000
```

随后只重启 `com.octoagent.cloudflared`，确认 4 条 connection 恢复且 Web hostname
仍返回 Access 登录边界。

### 4. 创建精确 Access application

在已登录 Cloudflare Dashboard 创建新的 self-hosted application：

- name：`OctoAgent Native iOS Device API`
- domain：`ios.maojiwang.work`
- path：`/api/mobile/v1/*`
- policy action：`Bypass`
- include：`Everyone`

不得编辑、复用或放宽现有 `OctoAgent Personal Web` application。

### 5. 启用 origin device-proof

在个人实例根创建 `mobile-device-access-manifest.v1.json`：

```json
{
  "version": 1,
  "web_hostname": "octo.maojiwang.work",
  "mobile_hostname": "ios.maojiwang.work",
  "tunnel_id": "19957901-f4e1-4cb0-b387-37258436644d",
  "loopback_origin": "http://127.0.0.1:8000",
  "mobile_path_prefix": "/api/mobile/v1/",
  "edge_policy": "access-bypass-origin-device-proof"
}
```

在个人实例 `octoagent.yaml` 增加：

```yaml
mobile_device_access:
  enabled: true
  manifest_path: mobile-device-access-manifest.v1.json
```

随后执行普通 Gateway restart。该 restart 不是 M10 物理开机 attestation。

## Live 正负矩阵

| 请求 | 期望 |
|---|---|
| `GET https://octo.maojiwang.work/`，未登录 | Cloudflare Access `302` |
| Web hostname 上任一 `/api/mobile/v1/*`，未登录 | 仍由 Web Access 拦截，不得被 mobile Bypass 覆盖 |
| `GET https://ios.maojiwang.work/` | origin `404`，不得渲染 Web SPA |
| mobile hostname 的 `/health`、`/docs`、`/openapi.json`、owner route | origin `404` |
| `GET /api/mobile/v1/ready`，无 device proof | JSON `401 / DEVICE_PROOF_HEADERS_INVALID`，不得是 Access HTML/302 |
| mobile route 携带 Web Cookie、CF Access JWT 或 service-token header | JSON `401 / MOBILE_WEB_CREDENTIAL_REJECTED` |
| enrollment/token challenge 的非法/空 payload | origin typed JSON error，不得是 Access HTML |
| 有效 owner challenge → iOS enrollment → owner approve | pending → active |
| 有效 P-256 challenge → opaque token → signed `/ready` | `200`，capability 精确 |
| nonce replay、错误 Host/path/body、过期/revoked token | fail closed |
| revoke/rotation 后 | 目标设备失效；Web session 与其它设备无副作用 |

`octo doctor` 必须将 `mobile_device_access` 报为 PASS，并只显示脱敏 hostname。

## 回滚

发生任何 schema、route、Access 或 device-proof 异常时按以下顺序回滚：

1. 先禁用或删除新的 path-specific Bypass application，使 edge fail closed。
2. 将个人实例 `mobile_device_access.enabled` 恢复为 `false`/移除该 section，恢复
   `octoagent.yaml` 写前字节并重启 Gateway。
3. 从 `~/.cloudflared/config.yml` 删除唯一新增的 iOS ingress，恢复写前字节并重启
   cloudflared。
4. 在 Cloudflare Dashboard 删除 `ios.maojiwang.work` 的 tunnel CNAME；当前
   `cloudflared tunnel route dns` CLI 没有 delete 子命令，禁止用覆盖命令冒充回滚。
5. 复验 Web Access、Gateway ready 与 tunnel connection 均恢复写前事实。

## T014 可接受证据

- 写前/写后 DNS、tunnel ingress 与 Access application/policy 的脱敏事实；
- manifest/config SHA、schema validation 与 Doctor；
- Web/mobile 正负 live probe 的 exact command、status、content-type 与稳定 code；
- owner challenge/enrollment/approve/token/signed ready/replay/revoke 的完整运行记录；
- Cloudflare/Gateway 日志 secret scan；
- 失败时的回滚记录；
- 不包含 Cloudflare credential、Access Cookie/JWT、challenge secret、device token、
  private key 或原始 signature。

只有上述 live matrix 完成，T014 才可勾选。Simulator 与本 preflight 均不能替代。
