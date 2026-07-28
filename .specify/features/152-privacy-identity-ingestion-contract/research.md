# F152 Research

## Apple 平台事实

1. [Keychain Services](https://developer.apple.com/documentation/security/keychain-services/)
   是 Apple 提供的小型 secret/key 存储边界；私钥不能写入源码、UserDefaults 或普通
   文件。
2. [Secure Enclave](https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave)
   可生成不可导出的 256-bit EC 私钥；F153 必须以真机证明，Simulator 不能代替。
3. `kSecAttrAccessibleWhenUnlockedThisDeviceOnly` 的项目不随备份迁移，适合只在设备
   解锁时使用的设备凭证：
   [Apple 文档](https://developer.apple.com/documentation/security/ksecattraccessiblewhenunlockedthisdeviceonly)。
4. [App Attest](https://developer.apple.com/documentation/devicecheck) 可证明请求更可能
   来自合法 App 实例，但 Apple 明确说明它不能消除所有欺诈；因此它是风险信号，不是
   Octo 的唯一设备身份或撤销机制。
5. [URLSession](https://developer.apple.com/documentation/foundation/urlsession) 默认受
   App Transport Security 约束；原生 client 使用 HTTPS/ATS，不把 Web Cookie 或
   Cloudflare service token 当设备凭证。

## 健康与日历事实

1. [HealthKit 授权](https://developer.apple.com/documentation/HealthKit/authorizing-access-to-health-data)
   按 data type 请求，且 App 不能可靠区分“用户拒绝读取”和“没有数据”。产品状态必须
   合并为诚实的“无可读数据或权限受限”。
2. [HealthKit HIG](https://developer.apple.com/design/human-interface-guidelines/healthkit)
   要求只在功能需要时请求权限、解释用途并使用系统权限界面；F154 不在冷启动索取。
3. [EventKit event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store)
   明确不提供 read-only access；读取需要 full access。F155 必须把“OS full access /
   Octo implementation read-only”同时展示和验证。

## Cloudflare 边界

[Cloudflare service token](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)
由 client id/secret 认证。静态 secret 进入 App 包后无法保持 secret，因此明确禁止。
桌面 Web 的 Access application Cookie/JWT 继续归 F150；F153 必须建立独立设备
proof/capability，不能把浏览器 session 搬进 `URLSession`。

## 结论

- F152 先冻结数据/身份语义，F153 再用真机选择和证明 transport。
- App Attest 可作为附加 attestation，不替代 device key + challenge + revoke。
- raw sample 本地化、每次预览批准、Memory 二次选择和可追溯删除是不可放宽的不变量。
- EventKit 保留到 F155 用户决策；当前不创建 calendar capability 或代码。
