# F153 Data Model

## MobileDeviceAccessManifestV1

- `version=1`
- `web_hostname`
- `mobile_hostname`
- `tunnel_id`
- `loopback_origin`
- `mobile_path_prefix="/api/mobile/v1/"`
- `edge_policy="access-bypass-origin-device-proof"`

全部为非敏感部署事实。hostname 不得 hard-code `maojiwang.work` 或任何默认域。

## RegistrationChallenge

- `challenge_id`
- `owner_id`
- `secret_sha256`
- `mobile_origin`
- `created_at`
- `expires_at`（≤2m）
- `state`: `open | submitted | approved | rejected | expired | consumed`
- `pending_device_id`

## DeviceEnrollment

- `challenge_id`
- `display_name`
- `public_key_x963`
- `device_key_thumbprint`
- `challenge_signature_der`
- `attestation_state`
- `attestation_object`（只在验证调用内，禁止 durable body）

## DeviceTokenChallenge

- `token_challenge_id`
- `device_id`
- `challenge_sha256`
- `created_at`
- `expires_at`（≤2m）
- `used_at`

## StoredCapabilityGrant

- F152 `CapabilityGrant`
- `token_sha256`
- `revoked_at`
- `created_from_challenge_id`

token value 不持久化。

## UsedRequestProof

- `replay_key`
- `device_id`
- `token_id`
- `expires_at`（≤5m）
- `consumed_at`

## iOS Keychain

- Secure Enclave key reference；
- `device_id`；
- canonical `server_origin`；
- current opaque token + expiry。

禁止 owner email、Cloudflare credential、Web Cookie、challenge history、private-key bytes。
