# F154 Health Read-Only Contract

## Apple adapter

```swift
protocol HealthDataStore {
    func isAvailable() -> Bool
    func requestReadAuthorization(
        for types: Set<HealthDataType>
    ) async throws
    func readPreview(
        window: HealthReadWindow,
        calendar: Calendar
    ) async throws -> HealthPreview
}
```

Production exact request set：

```text
read = { stepCount, sleepAnalysis }
share = {}
```

不提供 save/delete/background/observer 方法。

## Mobile routes

所有 route 都要求 existing mobile Host + F153 request proof。

| Method | Path | capability | input | output |
|---|---|---|---|---|
| POST | `/api/mobile/v1/health/reviews` | `health.review.submit` | F152 `ReviewBundle` + approved field projection | bundle hash/expiry |
| POST | `/api/mobile/v1/health/analyses` | `health.analysis.run` | `ConsentGrant` + `ApprovedAnalysisPacket` | `AnalysisResult` |
| DELETE | `/api/mobile/v1/health/sources/{source_hash}` | `health.source.delete` | canonical empty body | `DeletionReceipt` |

不存在 GET raw samples、bulk export、background sync 或 health write route。

## Review field allowlist

`field_manifest` 只能来自：

- `daily_steps[].local_day`
- `daily_steps[].count`
- `daily_steps[].unit`
- `sleep.window_start_utc`
- `sleep.window_end_utc`
- `sleep.total_asleep_minutes`
- `sleep.stage_minutes.awake`
- `sleep.stage_minutes.core`
- `sleep.stage_minutes.deep`
- `sleep.stage_minutes.rem`
- `sleep.stage_minutes.unspecified`
- `sleep.unit`
- `completeness_notice`

以下字段出现即拒绝：

- HealthKit/sample/source/device UUID；
- metadata/source revision；
- raw timestamp series；
- location、heart rate、clinical record；
- owner email/token/proof/signature；
- unknown field/type/category。

## Analysis

- purpose exact：`summarize_recent_activity_and_sleep`
- output：普通语言概览与非医疗的自我观察提示；
- 禁止 diagnosis/treatment/risk score；
- 认证失败、超时、provider error 不得生成 success result；
- 每个 consent 只创建一个 analysis；
- 结果不会自动创建 Memory candidate。

## Error codes

- `HEALTH_DATA_UNAVAILABLE`
- `HEALTH_DATA_RESTRICTED`
- `HEALTH_NO_READABLE_DATA_OR_LIMITED_ACCESS`
- `HEALTH_WINDOW_INVALID`
- `HEALTH_DATA_TYPE_DENIED`
- `HEALTH_PREVIEW_EXPIRED`
- `HEALTH_PREVIEW_HASH_MISMATCH`
- `HEALTH_RAW_FIELD_FORBIDDEN`
- `HEALTH_DEVICE_REVOKED`
- `HEALTH_CAPABILITY_DENIED`
- `HEALTH_REQUEST_REPLAYED`
- `HEALTH_CONSENT_INVALID`
- `HEALTH_ANALYSIS_FAILED`
- `HEALTH_DELETION_INCOMPLETE`

错误码用于稳定合同和日志，用户 UI 使用普通语言。
