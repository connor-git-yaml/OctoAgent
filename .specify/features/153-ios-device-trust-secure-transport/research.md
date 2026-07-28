# F153 Research

## Apple 平台事实

1. Apple 的
   [Secure Enclave P-256 signing key](https://developer.apple.com/documentation/cryptokit/secureenclave/p256/signing/privatekey)
   只提供 P-256，并支持指定 access control、恢复 key reference 和签名。
2. `ThisDeviceOnly` Keychain class 不随备份迁移。后台恢复需要
   `AfterFirstUnlockThisDeviceOnly`；它仍不代表设备当前处于解锁状态，所有 Keychain
   错误必须显式呈现。
3. [URLSession](https://developer.apple.com/documentation/foundation/urlsession)
   受 ATS 约束，适合原生 HTTPS transport；它不会把 F150 的 browser
   `CF_Authorization` Cookie 自动变成可靠设备身份。
4. App Attest 只能增加“合法 App 实例”信号，不能替代 owner approval、device key、
   per-request proof、revoke 与 capability。

## Cloudflare 候选复核

### 交互式 Access browser session

Cloudflare self-hosted Access 以 browser login/application Cookie 为主。HttpOnly、
domain binding 与认证 UI 都不适合作为原生 `URLSession` 的设备身份；把登录页塞进
WebView 又违反产品边界。否决。

### Service token

[Service token](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)
是 client id + secret。写进 App 包无法保密；让 Gateway 长期持有 Cloudflare API
credential 为每台设备创建 token，又复制了 Cloudflare account lifecycle。否决。

### mTLS

[Cloudflare Access mTLS](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/mutual-tls-authentication/)
可用 `Service Auth + Valid Certificate`，但要求 CA、client certificate 发放、更新与
撤销。Cloudflare-managed client certificate API 又要求 zone-level write token。
在当前个人部署模型中，这会新增 Cloudflare credential owner、证书 lifecycle 和
Keychain client identity transport，且不能直接替代 F152 request-level capability。
保留为未来高保障部署选项，不作为 F153 默认。

### 独立 mobile API + origin device proof

Cloudflare
[Bypass](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/)
会关闭匹配流量的 Access 安全控制和日志，官方不建议把它当长期用户认证。F153 因此只
把它当“让原生 HTTPS 请求抵达 origin”的 edge routing 决定，并明确不宣称 Access
保护。Origin 仍对每个请求执行 device proof；mobile hostname 的其它路径全拒绝。

该方案的优点：

- 不在 App 或 Gateway 保存 Cloudflare account secret；
- 复用同一 named tunnel，不引入 Worker/第二网络；
- device capability/revoke 与 F152 直接同构；
- 每个自托管部署只替换 hostname/tunnel facts。

代价：

- mobile API 的扫描/拒绝流量不进入 Access identity logs；
- Gateway device proof 成为关键安全边界；
- live 前必须单独验证 Cloudflare application path/priority，不能只看配置文本。

## 注册设计

注册必须从已经由 F150 Access 验证的电脑 Web 发起：

1. Web 创建 2 分钟 challenge，展示 QR/deep link；
2. iOS 生成 Secure Enclave key，用 key 签 challenge envelope；
3. Gateway 只保存 challenge secret hash、public key、thumbprint 与 pending state；
4. Web 显示 device name/thumbprint/attestation，owner 显式批准；
5. active device 再签 server token challenge，换 15 分钟 opaque token；
6. 后续 token renewal 只依赖 active device key，不保存 refresh token。

二维码泄漏最多允许别人提交一个 pending key，不能直接激活。Owner 必须核对 fingerprint；
错误提交由 owner 拒绝并重建 challenge。

## 结论

- 默认 transport：mobile hostname + same tunnel + origin proof。
- F150 Web Access 与 F153 device trust 是两个产品入口、两个 trust contract，但不是
  两套远程网络基础设施。
- Secure Enclave/Keychain/网络切换只能由真机验收；Simulator 只做纯逻辑和 UI。
- 外部 Cloudflare application/Bypass/DNS/tunnel ingress 变更需要独立 live 授权。
