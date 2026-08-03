# F155 Data Model

## iOS local-only

### `CalendarReadWindow`

- `start: Date`
- `end: Date`
- `preset: next24Hours | next3Days | next7Days`
- `calendarIdentifier: String`（时区/历法的有限标识，不是 EventKit calendar id）

### `CalendarPreviewEvent`

- `localID: String`（由本次 preview canonical fields hash 生成）
- `title: String`（local-only）
- `startUTC: String`
- `endUTC: String`
- `isAllDay: Bool`
- `availability: busy | free | tentative | unavailable`
- `hasRecurrence: Bool`

不得包含 EventKit event/calendar/source/external identifier、notes、URL、location、
attendees、organizer、alarm 或 recurrence rule。

### `CalendarPreview`

- `window`
- `eventCount`
- `busyMinutes`
- `allDayCount`
- `events`
- `includeTitlesInPacket: Bool`（默认 false，不持久化为下一次默认）
- `canonicalSHA256`
- `expiresAt`

## F152 retained stages

F155 不新增 durable domain entity。Gateway 只使用 F152：

- ReviewBundle
- ConsentGrant
- ApprovedAnalysisPacket
- AnalysisResult
- OptionalMemoryCandidate
- DeletionReceipt

## 数据流

```text
EKEventStore full access
  -> CalendarEventStore adapter（raw 临时）
  -> CalendarPreview（local-only）
  -> 用户选择 title opt-in + preview
  -> F152 consent
  -> F153 signed mobile request
  -> F152 store/policy/audit
  -> ProviderRouter single analysis
```
