# F153 Threat Model

## 资产

- Secure Enclave private key 与 Keychain reference；
- registration/token challenge secret；
- opaque short-lived token；
- device public key、thumbprint、status 与 capability grant；
- request proof nonce/signature；
- mobile hostname/tunnel routing facts；
- non-sensitive device audit。

## Trust boundaries

1. owner browser + F150 Cloudflare Access；
2. QR/deep link handoff；
3. iOS process / Secure Enclave / Keychain；
4. Internet + Cloudflare edge / named tunnel；
5. Gateway mobile host/path classifier；
6. crypto verifier + F152 policy；
7. durable SQLite device-trust store。

## 威胁与控制

| 威胁 | 影响 | 必须控制 |
|---|---|---|
| App 包提取 service token | 任意人远程访问 | App/源码/bundle static secret=0 |
| QR 被拍照 | 攻击者提交 pending key | 2 分钟、single pending、owner核对fingerprint |
| challenge/token 泄漏到日志 | 认证材料扩散 | 只存hash；header/body日志redaction；secret scan |
| token 被复制 | 另一设备复用 | token + device signature双重要求 |
| request replay | 重复mutation | nonce/timestamp/body/path绑定，durable single-use |
| path/Host混淆 | mobile旁路访问Web | exact hostname/path；query/dot/double slash拒绝 |
| Bypass被扩大 | Web或管理面公开 | separate app exact path；live negative probes |
| 伪造软件key | 降低设备保障 | Secure Enclave真机证据；App Attest risk state |
| 被撤销设备续用 | 未授权访问 | 每次查device status；revoke优先于token exp |
| rotation无限overlap | 旧key长期有效 | overlap≤15m；旧thumbprint原子终止 |
| Keychain迁移/备份 | 跨设备复制 | AfterFirstUnlockThisDeviceOnly |
| Cloudflare HTML当JSON | 错误状态/泄漏 | content-type/status typed rejection |
| 无限retry | 重复请求/耗电 | bounded policy、idempotency、explicit offline |
| Simulator冒充真机 | 安全结论错误 | device checklist只接受真实iPhone证据 |

## Fail closed

unknown algorithm/key encoding/capability、duplicate header、wrong host/path/body hash、
stale/future time、nonce replay、expired challenge/token、pending/rotating/revoked device、
attestation failed、Cloudflare login HTML、Keychain/Secure Enclave unavailable、partial
database state 均必须拒绝；禁止回退 anonymous、Web Cookie、service token、software
production key 或第二 transport。
