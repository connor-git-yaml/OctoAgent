# F152 Privacy / Ingestion Contract

## Canonical serialization

- UTF-8 JSON；
- object key `sort_keys=true`；
- separators `(",", ":")`；
- 时间为 UTC RFC3339 秒精度；
- SHA-256 小写 64 hex；
- float 禁止进入 proof/consent hash；健康数值必须先归一化为 decimal string + unit。

## State transitions

```text
raw_sample
  -> normalized_fact
  -> review_bundle
  -> approved_analysis_packet
  -> analysis_result
  -> optional_memory_candidate
  -> existing_memory_review
```

允许在任一阶段进入 `expired`、`cancelled` 或 `deleting`。禁止跳级、回退复用、
同 consent 多次使用和 analysis-result 直写 Memory。

## Capability rules

1. exact match，不支持 `*`、prefix、regex 或 unknown；
2. token capability 与 route requirement 取交集，不做 fallback；
3. revoke 优先于 token expiry；
4. F154/F155 未 Verify 前，health/calendar capability 不存在；
5. Web Access identity 与 native device identity 不互相转换。

## Secret-negative rules

source、bundle、archive、log、audit、crash report、snapshot、test fixture 均不得出现：

- Cloudflare service client id/secret；
- Web Access Cookie/JWT；
- device private key；
- short-lived token；
- proof signature/attestation object；
- raw HealthKit/EventKit object or identifier；
- owner email。

## Deletion rules

删除请求必须沿 provenance graph 枚举所有派生对象，在 durable transaction 中逐项删除，
最后写 `DeletionReceipt`。未知或部分状态 fail closed；重入只能继续同一 request，不得
创建第二 receipt 或把 partial 当 completed。
