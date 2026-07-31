# F155 Apple 官方在线调研

- 日期：2026-07-29
- 范围：EventKit 日历读取权限、隐私声明、事件查询与系统边界
- 来源：仅 Apple 官方一手资料

## 证据点

1. [Accessing the event store](https://developer.apple.com/documentation/eventkit/accessing-the-event-store)
   明确 iOS 不提供 events/reminders read-only 权限；读取 events 必须申请 full
   access，而 full access 也允许创建、查看、编辑和删除。
2. [EKEventStore](https://developer.apple.com/documentation/eventkit/ekeventstore)
   `requestFullAccessToEvents` 是当前 events 读取授权 API；write-only 只允许创建，
   不能读取真实 calendars/events。
3. [Accessing Calendar using EventKit and EventKitUI](https://developer.apple.com/documentation/eventkit/accessing-calendar-using-eventkit-and-eventkitui)
   EventKit UI 可把创建/编辑交给系统，但这不是只读能力；F155 不使用 EventKit UI，
   也不能以系统 UI 证明 App 无写路径。
4. [Retrieving events and reminders](https://developer.apple.com/documentation/eventkit/retrieving-events-and-reminders)
   events 查询需要明确 start/end predicate；F155 以有限 future window 查询，
   不枚举全部历史。
5. [EKAuthorizationStatus](https://developer.apple.com/documentation/eventkit/ekauthorizationstatus)
   授权状态必须按系统有限枚举处理；unknown/new case fail closed。

## 对 F155 的强制约束

- UI 必须在系统 sheet 前说明 full access 的真实含义。
- `NSCalendarsFullAccessUsageDescription` 是唯一 calendar usage description；
  不配置 write-only key。
- 只调用 read/query symbols；任何 mutation symbol、EventKit UI、Reminders、
  Contacts 或 legacy fallback 都拒绝。
- 读取范围和字段必须在 App 层进一步最小化；OS full access 不能成为 bulk export
  或后台同步的理由。
