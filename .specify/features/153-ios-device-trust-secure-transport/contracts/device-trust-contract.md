# F153 Device Trust Contract

## Canonical encoding

- F152 canonical JSON；
- UTC RFC3339 second；
- SHA-256 lowercase hex；
- base64url no-padding；
- P-256 X9.63 public key 65 bytes；
- DER ECDSA/SHA-256 signature。

## Enrollment envelope

```json
{
  "challenge_id": "…",
  "challenge_secret_sha256": "…",
  "display_name": "…",
  "mobile_origin": "https://…",
  "public_key_x963": "…",
  "timestamp": "…"
}
```

iOS 对 canonical bytes 签名。Gateway 先验证 secret hash/TTL/host，再验证 public key
thumbprint/signature，最后创建 pending device。

同一 owner 的 current public key 已对应 pending/active device 时，新鲜有效 challenge 只可
绑定并返回原 device id；不得插入第二个 device/key。不同 owner、revoked key 或历史 overlap
key 必须在同一事务内以 typed `409` fail closed，challenge/device/key 均不得出现半写入。

## Token challenge envelope

```json
{
  "device_id": "…",
  "mobile_origin": "https://…",
  "server_challenge_sha256": "…",
  "timestamp": "…",
  "token_challenge_id": "…"
}
```

active device 签名后换 opaque token。Challenge single-use；token TTL≤15m。

## Protected request

Headers：

- `Authorization: OctoDevice <opaque-token>`
- `X-Octo-Device-Timestamp: <UTC-second>`
- `X-Octo-Device-Nonce: <base64url>`
- `X-Octo-Device-Signature: <DER base64url>`

Gateway 构造 F152：

```text
RequestProofPayload(
  method=actual_method,
  canonical_path=actual_path,
  body_sha256=sha256(raw_body),
  timestamp=header_timestamp,
  nonce=header_nonce,
  token_id=stored_grant.token_id,
)
```

先 cryptographic verify，再由 F152 `authorize_device_request` 做 device/grant/capability/
expiry/binding；replay key 必须在 workload 前 durable consume。

## Response / error

错误返回固定：

```json
{"code":"DEVICE_*","message":"普通用户可理解的中文","retryable":false}
```

不得返回 token、signature、public key、owner email、Cloudflare header 或内部 exception。
Cloudflare HTML/非 JSON 在 iOS 端映射为 `edgeContractMismatch`，不得当作登录页面打开。

## Route capability

| Route | Capability |
|---|---|
| `GET /api/mobile/v1/ready` | `device.ready.read` |
| device profile projection | `device.profile.read` |

F154/F155/F156 新 route 必须显式映射 F152 finite capability；不存在 wildcard/default。
