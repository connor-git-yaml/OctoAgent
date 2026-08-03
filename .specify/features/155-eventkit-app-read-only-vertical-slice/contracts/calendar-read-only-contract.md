# F155 Calendar App-Read-Only Contract

## iOS adapter

```swift
protocol CalendarEventStore {
    func authorizationStatus() -> CalendarAuthorizationStatus
    func requestFullAccess() async throws -> Bool
    func readPreview(
        window: CalendarReadWindow,
        calendar: Calendar
    ) async throws -> CalendarPreview
}
```

不存在 save/remove/commit/reset/editor/chooser 方法。

## Mobile routes

| Method | Path | capability | input | output |
|---|---|---|---|---|
| POST | `/api/mobile/v1/calendar/reviews` | `calendar.review.submit` | F152 ReviewBundle + allowlisted projection | bundle hash/expiry |
| POST | `/api/mobile/v1/calendar/analyses` | `calendar.analysis.run` | ConsentGrant + ApprovedAnalysisPacket | AnalysisResult |
| DELETE | `/api/mobile/v1/calendar/sources/{source_hash}` | `calendar.source.delete` | canonical empty body | DeletionReceipt |

所有 route 继续要求 F153 mobile Host/request proof。不存在 PUT/PATCH、event creation、
calendar mutation 或 bulk export route。

## Approved field allowlist

默认允许：

- `window_start_utc`
- `window_end_utc`
- `event_count`
- `busy_minutes`
- `all_day_count`
- `events[].start_utc`
- `events[].end_utc`
- `events[].is_all_day`
- `events[].availability`
- `events[].has_recurrence`

仅当当前 consent 的 `include_titles=true` 时允许：

- `events[].title`

始终禁止：

- EventKit/calendar/event/source/external identifier；
- notes、URL、location、attendees、organizer、alarms、recurrence rule；
- token、proof、signature、owner email；
- unknown field。

## Stable errors

- `CALENDAR_PRODUCT_DECISION_REQUIRED`
- `CALENDAR_FULL_ACCESS_REQUIRED`
- `CALENDAR_ACCESS_DENIED`
- `CALENDAR_ACCESS_RESTRICTED`
- `CALENDAR_UNSUPPORTED`
- `CALENDAR_WINDOW_INVALID`
- `CALENDAR_PREVIEW_EXPIRED`
- `CALENDAR_PREVIEW_HASH_MISMATCH`
- `CALENDAR_RAW_FIELD_FORBIDDEN`
- `CALENDAR_WRITE_PATH_FORBIDDEN`
- `CALENDAR_DEVICE_REVOKED`
- `CALENDAR_CAPABILITY_DENIED`
- `CALENDAR_REQUEST_REPLAYED`
- `CALENDAR_CONSENT_INVALID`
- `CALENDAR_ANALYSIS_FAILED`
- `CALENDAR_DELETION_INCOMPLETE`
