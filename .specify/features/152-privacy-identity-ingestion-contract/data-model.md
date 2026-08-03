# F152 Data Model

## Identity

### DeviceIdentity

- `device_id`
- `owner_id`
- `display_name`
- `public_key`
- `device_key_thumbprint`
- `attestation_state`
- `status`: `pending | active | rotating | revoked`
- `created_at`
- `last_seen_at`
- `revoked_at`

私钥不属于该模型，也不得离开设备。

### CapabilityGrant

- `grant_id`
- `owner_id`
- `device_id`
- `device_key_thumbprint`
- `capabilities`
- `audience`
- `issued_at`
- `expires_at`
- `token_id`

capabilities 是 exact finite enum，不接受字符串前缀或 wildcard。

### RequestProofPayload

- `method`
- `canonical_path`
- `body_sha256`
- `timestamp`
- `nonce`
- `token_id`

该 payload 的 canonical bytes 由 F152 冻结；签名生成/验证归 F153。

## Ingestion

### Provenance

- `source_kind`
- `source_object_hash`
- `owner_id`
- `device_id`
- `captured_at`
- `time_range`
- `data_types`

不得包含系统 raw identifier 或正文。

### RawSample

- local-only；
- 对应 Apple framework 的原始读取结果；
- 不允许 Gateway schema；
- session/24h TTL。

### NormalizedFact

- local-only default；
- 只保留明确 allowlist 字段、单位、时间和值；
- 必须指向 provenance。

### ReviewBundle

- `bundle_id`
- `purpose`
- `provenance`
- `fact_count`
- `field_manifest`
- `preview`
- `expires_at`

### ConsentGrant

- `consent_id`
- `bundle_sha256`
- `approved_packet_sha256`
- `purpose`
- `owner_id`
- `device_id`
- `approved_at`
- `expires_at`
- `used_at`

### ApprovedAnalysisPacket

- `packet_id`
- `purpose`
- `facts`
- `provenance`
- `consent_id`
- `created_at`
- `expires_at`

只能单次进入分析。

### AnalysisResult

- `result_id`
- `packet_id`
- `summary`
- `created_at`
- `retention_state`

### OptionalMemoryCandidate

- `candidate_id`
- `result_id`
- `packet_sha256`
- `provenance`
- `user_selected_text`
- `review_state`

必须进入既有 Memory review，不能直写。

### DeletionReceipt

- `request_id`
- `source_hash`
- `deleted_object_hashes`
- `retained_audit_hashes`
- `status`
- `started_at`
- `finished_at`
- `failure_reason`

正文删除后 receipt 只保存 hash。

## Audit

`PrivacyAuditEvent` 只允许：

- event/type/version；
- owner/device hashed identity；
- object hash；
- count/data type/capability；
- decision/result/reason code；
- UTC。

禁止 raw/normalized/packet/result 文本、token、signature、nonce、email、calendar id、
health sample id。
