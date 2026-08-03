# F155 产品调研

## 用户问题

用户希望 Octo 在原生 iPhone 上理解近期日程负载和冲突，但不能让 Agent 在日历里
偷偷新增、修改或删除内容，也不能把整本日历默默上传。

## v0.1 产品切片

1. 已连接设备进入“近期日程”。
2. App 先说明 iOS full access 与 Octo App-read-only 的差异。
3. 用户继续后系统请求 events full access。
4. 用户选择未来 24 小时、3 天或 7 天。
5. 本地预览显示时间块、全天状态和标题。
6. 上传默认不含标题；用户可为本次分析开启标题。
7. 用户取消、删除本地预览或批准一次分析。
8. 结果默认不写 Memory；Memory candidate 仍需第二次确认。

## 用户状态

- 等待产品决定；
- 尚未请求权限；
- 正在请求系统权限；
- full access 可用；
- denied / restricted / unsupported；
- 没有未来日程；
- 本地预览；
- 等待批准；
- 发送/分析；
- 完成；
- 离线；
- device revoked；
- 删除中/完成/失败可重试。

## 文案边界

- 普通 UI 说“日历”“完整访问权限”“Octo 只读取”，不说 EventKit、predicate、
  identifier、capability。
- 不说“系统只给只读权限”或“最低权限”。
- 不承诺后台持续感知。
- 视觉延续 Claude Design 早期近黑、细边框、绿色强调和卡片节奏；权限与导航使用
  SwiftUI/Apple 原生语义。

## 明确推迟

- Reminders、Contacts、location、attendees、notes、URLs；
- calendar create/edit/delete/RSVP；
- 后台同步和自动分析；
- 完整 F156 companion shell。
