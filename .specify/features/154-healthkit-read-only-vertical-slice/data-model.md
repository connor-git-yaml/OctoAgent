# F154 Data Model

## iOS local-only

### `HealthReadWindow`

- `start: Date`
- `end: Date`
- `preset: last24Hours | last3Days | last7Days`
- invariant：`0 < duration <= 7 days`

### `HealthDataType`

- `stepCount`
- `sleepAnalysis`

其它值不得 decode 成默认类型。

### `DailyStepSummary`

- `localDay: YYYY-MM-DD`
- `count: canonical decimal string`
- `unit: "count"`

### `SleepStage`

- `awake`
- `core`
- `deep`
- `rem`
- `unspecified`

### `SleepSummary`

- `windowStartUtc`
- `windowEndUtc`
- `totalAsleepMinutes: canonical decimal string`
- `stageMinutes: exact finite map`
- `unit: "min"`

### `HealthPreview`

- `previewID`
- `capturedAtUtc`
- `window`
- `dataTypes`
- `dailySteps`
- `sleep`
- `completenessNotice`
- `canonicalSha256`
- `expiresAtUtc`（session end 或 24 小时，以更早者为准）

不允许：HealthKit UUID、source、device、metadata、raw time series、owner email、token。

### `HealthImportPhase`

- `unavailable`
- `idle`
- `requestingPermission`
- `noReadableDataOrLimitedAccess`
- `reviewing`
- `submitting`
- `analyzing`
- `completed`
- `offline`
- `revoked`
- `deleting`
- `deletionFailed`

## Gateway / F152 reused

F154 不新增第二套通用模型，只把 `HealthPreview` 投影到：

1. `ReviewBundle`
2. `ConsentGrant`
3. `ApprovedAnalysisPacket`
4. `AnalysisResult`
5. optional `OptionalMemoryCandidate`
6. `DeletionReceipt`

`Provenance.source_kind=healthkit`，`data_types` exact 为：

- `apple_health.step_count`
- `apple_health.sleep_analysis`

`source_object_hash` 是本地 preview canonical hash，不是 Apple sample identifier。

## Capability additions after F153 Verify

- `health.review.submit`
- `health.analysis.run`
- `health.source.delete`

capability 不得进入 F153 固定 token allowlist，直到 F154 实现/验证相应 route。

## Retention

- raw sample：调用栈；不持久化；
- normalized preview：session end 或 24 小时；
- review bundle：24 小时；
- approved packet：一次使用或 15 分钟；
- analysis result：用户可删除；
- optional Memory candidate：F152 既有 review 生命周期；
- audit：只含非敏感 metadata。
