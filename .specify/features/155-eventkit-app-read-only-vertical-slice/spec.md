# F155：日历只读垂直切片（系统 Full Access 决策门）

## 状态

- Research：PASS
- Product Decision：`OPTION_A_ACCEPTED_2026-08-01`
- Design：PASS
- Tasks：PASS
- Implement：`CLOSED_ON_F154_VERIFY_AND_F151_AUTHORITY`
- Verify：`CLOSED`

## 背景

F155 的产品目标是让原生 iOS App 在用户明确发起时，读取未来有限时间内的日程，
在设备本地形成预览，并沿 F152/F153 的隐私与设备信任链执行一次分析。

Apple 当前 EventKit 没有 read-only 系统权限。读取日历必须请求 full access；该
权限从系统能力上允许 App 创建、编辑和删除事件。Octo 能做到的是在产品、类型、
capability、协议、代码与测试层物理删除所有写路径，而不是把系统权限说成只读。

## 必须由用户明确决定

### 方案 A：保留 F155

用户接受以下准确说明：

> iOS 会授予 Octo 对日历事件的完整访问权限。Octo 的产品与代码只读取日程，
> 不创建、不修改、不删除日历事件；所有写入相关 capability、route、protocol 和
> EventKit 调用点都必须为零。

### 方案 B：从 M12 移除 F155

不请求日历权限；F156 不展示日历入口，也不以截图、手工输入或 Web Cookie 伪装
日程感知。M12 其余原生 iOS、Health 与 companion 能力继续推进。

2026-08-01，用户明确选择方案 A，并接受上述系统 `full access` 与 Octo
App-read-only 的差异。决策 review identity 为
`user-f155-option-a-20260801`。该决定只解除产品决策门，不等于授权提前添加
entitlement、usage description、Swift/Python production、行为 RED 或 Gateway route。

## 前置硬门

即使用户选择方案 A，以下条件全部满足前 Implement 仍关闭：

1. F153 `GATE_VERIFY=true`；
2. F154 `GATE_VERIFY=true`；
3. F151 单一 architecture authority 批准 F155 exact paths/symbols；
4. 决策文案、日期与 review identity 写入本 Feature Gate record。

其中 F153 `GATE_VERIFY=true` 已于 2026-08-02 满足；当前剩余前置是 F154
`GATE_VERIFY=true` 与 F151 对 F155 exact paths/symbols 的独立 authority RED→GREEN。

## 用户故事

### US1：在授权前看懂真实权限

用户点击“查看近期日程”后，App 先用普通语言说明 iOS 会授予 full access，以及
Octo 只读、不写、不删除的代码承诺；只有用户继续才打开系统权限 sheet。

### US2：读取有限未来日程

用户选择未来 24 小时、3 天或 7 天。App 只读取与窗口重叠的 calendar events，
在本地显示时间、时长、全天状态和标题；notes、URL、attendees、organizer、
location、calendar/source identifier 与 external identifier 不进入预览或网络。

### US3：最小化预览并批准一次分析

预览默认只发送忙闲时间块。用户可为当前分析明确选择“同时发送日程标题”；取消时
不上传。批准只绑定当前 preview/packet，最多 15 分钟且只消费一次。

### US4：分析、删除与 Memory 二次确认

聚合后的日程沿 F153 device proof 与 F152 ingestion chain 进入一次
ProviderRouter 分析。结果可删除且不会自动写 Memory；用户主动选择结果文本后，
仍需 F152 独立二次确认。

## Functional Requirements

### FR-001 原生入口

F155 MUST 只存在于原生 SwiftUI App。不得新增 Safari、WebView、PWA、第二配对、
第二 session/device registry 或 Web identity 转换。

### FR-002 系统权限诚实

UI MUST 在系统 sheet 前明确说明：

- iOS 读取日历需要 full access；
- 系统权限从能力上也允许创建、编辑和删除；
- Octo 产品和代码只读取；
- 用户可随时在系统设置撤销。

不得使用“只读系统权限”“最低权限”或等价误导文案。

### FR-003 exact 系统 API

只允许 `EKEventStore.requestFullAccessToEvents`。Reminders、Contacts、write-only
events 与 legacy authorization API 不在 v0.1。

### FR-004 App-read-only 物理边界

以下必须物理缺席：

- `EKEventStore.save`、`remove`、`commit`、`reset`；
- `EKEventEditViewController`、`EKCalendarChooser`；
- calendar write/delete capability、route、protocol、service method；
- Agent/tool/notification/deep-link 触发的 calendar mutation；
- `NSCalendarsWriteOnlyAccessUsageDescription`。

### FR-005 授权触发

系统权限只能由用户点击“继续并打开系统设置”触发。冷启动、注册、恢复、后台、
通知、deep link、定时器和 F156 shell 不得请求日历权限。

### FR-006 授权状态

至少区分 `notDetermined`、`fullAccess`、`denied`、`restricted` 与
`unsupported`。拒绝/限制提供系统设置说明，但不能循环弹权限或自动跳设置。

### FR-007 时间窗

只允许从当前时刻开始的未来 24 小时、3 天或 7 天；默认 24 小时、最大 7 天。
查询包含与窗口重叠的进行中事件，输出前裁剪到窗口。倒置、未来超界、非法时区和
未知 preset fail closed。

### FR-008 exact 读取字段

本地 adapter 只可读取并归一化：

- title（本地预览，最长 256 Unicode scalar）；
- start/end；
- isAllDay；
- availability；
- recurrence presence boolean。

notes、structured location、URL、attendees、organizer、alarms、calendar/source、
event identifier、external identifier、last modified、metadata 全部禁止。

### FR-009 默认标题不上传

本地预览可显示 title，但 approved packet 默认只含 busy interval、all-day、
availability 与 recurrence presence。只有用户在当前 preview 中明确开启
“同时发送日程标题”时，title 才进入 field manifest；该选择不持久化为默认值。

### FR-010 本地规范化

事件按 `(start, end, all_day, availability, normalized_title)` 排序和去重；重叠
区间不得重复计算忙碌分钟。取消、发送完成、App session 结束或 24 小时到期时，
raw EventKit object 与 normalized preview 清空。

### FR-011 raw zero-retention

`EKEvent`、calendar/source/event identifier、attendee、organizer、URL、location、
notes、alarm 与完整 recurrence rule 不得落盘、进 Keychain/UserDefaults/iCloud、
日志、crash、snapshot、audit、prompt 或网络。

### FR-012 用户预览

上传前 MUST 展示时间窗、事件数量、忙碌分钟、全天数量、标题发送开关、用途、
保留期限与删除动作。preview canonical hash 与 F152 ReviewBundle/ConsentGrant
精确绑定。

### FR-013 当次批准

批准沿用 F152 ConsentGrant，绑定 bundle、packet、purpose、owner、device、
approved_at/expires_at；一次消费且最多 15 分钟。重放、过期、跨设备、跨 owner、
title 开关或 packet 漂移均拒绝。

### FR-014 finite capability

仅允许：

- `calendar.review.submit`
- `calendar.analysis.run`
- `calendar.source.delete`

不得存在 calendar create/update/delete capability、wildcard/prefix/default/fallback。

### FR-015 route isolation

所有 route 位于既有 `/api/mobile/v1/calendar/*`，继续要求 mobile Host、
F153 device proof、canonical body hash、timestamp、nonce 与 durable replay。
Web host、其它 mobile path、未注册或 revoked device 拒绝。

### FR-016 F152 chain

Gateway MUST 复用唯一 F152 store/policy/audit/deletion 与 Memory review。禁止第二
calendar store、consent、audit、Memory pipeline、正文 blob 表或分析 runner。

### FR-017 单次分析

purpose exact 为 `summarize_upcoming_schedule_load`。分析只描述时间安排、冲突和
可选准备提示，不向日历写入任何内容。认证失败、超时和 provider error typed
fail closed，禁止 Echo fallback。

### FR-018 result / Memory / deletion

结果绑定 approved packet，可由用户删除，不自动写 Memory。删除沿 F152 provenance
清理 review、packet、result、pending candidate，并返回 durable DeletionReceipt。

### FR-019 audit

audit 只允许 hash、event count、busy minute count、window preset、title included
boolean、capability、result/reason 与 UTC。标题、正文、identifier、owner email、
token、proof 和签名不得出现。

### FR-020 离线与撤销

本地读取/预览可离线；无 device proof 不得上传。device revoked 优先于 token expiry，
取消 pending 网络动作并保留本地清理能力。

### FR-021 SwiftUI 视觉与无障碍

沿用 Claude Design 最早期近黑层级、单一绿色强调、细边框、紧凑卡片和普通用户
语言，并使用 SwiftUI `NavigationStack`、sheet、toolbar、Dynamic Type、
VoiceOver、Reduce Motion 和至少 44pt 操作区。不得复制 Web DOM 或桌面三栏。

### FR-022 验证

MUST 有 Swift/Python L4、Gateway L3、Simulator UI/visual/a11y、真机系统权限/
真实日程、approved live model L2、write-symbol/raw-field/secret scan、clean
checkout、CI 与 architecture ratchet。Simulator 不替代真机权限。

## Non-Goals

- 不读取 Reminders、Contacts、邮件、位置、attendees、notes 或 URL；
- 不创建、编辑、删除、接受/拒绝邀请或移动日历事件；
- 不后台持续同步、不 observer 驱动上传、不自动周期分析；
- 不自动写 Memory；
- 不实现 F156 完整 companion shell；
- 不把 EventKit UI 当作 App-read-only 证明。

## Success Criteria

### SC-001

授权前文案准确说明 full access；冷启动/后台/通知请求权限次数为 0。

### SC-002

静态与 AST 门证明 calendar write/delete capability、route、protocol 和 EventKit
调用点为 0，且 adversarial alias/dynamic dispatch 不能绕过。

### SC-003

L4 覆盖进行中/全天/重叠/重复/recurring/DST/标题开关/7 天上界与 forbidden fields。

### SC-004

Simulator 覆盖五种授权状态、preview/title opt-in/approve/offline/revoked/delete
的功能与视觉回归；AXXXL、VoiceOver、Reduce Motion 通过。

### SC-005

真 iPhone 完成系统 full-access sheet、真实日历读取、撤销、锁屏/前后台与网络切换；
0 次 calendar mutation，forbidden raw fields 在 package/log/audit/request 中为 0。

### SC-006

一次 approved 真模型分析通过；认证失败无 Echo fallback；删除后可删除派生内容
不可读取。
