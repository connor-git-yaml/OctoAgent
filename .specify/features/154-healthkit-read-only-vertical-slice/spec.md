# F154：Apple 健康只读垂直切片

## 状态

- Research：PASS
- Design：PASS（2026-07-29 main review）
- Tasks：PASS（2026-07-29 main review）
- Implement：`CLOSED`；必须先满足 F153 `GATE_VERIFY=true`
- Verify：`CLOSED`

## 背景

M12 的下一步不是把 Web 包进手机，而是在原生 iOS App 中以最小权限读取用户主动选择
的 Apple 健康数据，并沿 F152 隐私链和 F153 device trust 完成一次可审计分析。

F154 只拥有“读取 → 本地聚合 → 预览 → 当次批准 → 单次分析 → 删除”垂直切片。
F156 仍拥有最终 companion shell；F154 不复制 Web 三栏，也不把注册页扩张成完整产品。

## 前置硬门

以下条件全部满足前，F154 production 与 entitlement 必须物理缺席：

1. F153 T014 mobile hostname/tunnel/Access Bypass live 正负探针通过；
2. F153 T015 真 iPhone Secure Enclave/Keychain/网络生命周期通过；
3. F153 T018 `GATE_VERIFY=true`；
4. F151 单一 architecture authority 批准 F154 exact paths/symbols。

Design/Tasks artifacts 和 test-only RED 合同可以提前完成，但不能借此解锁 HealthKit。

## 用户故事

### US1：用户主动读取最小健康概览

已连接 iPhone 的用户进入“健康概览”，明确点击“从 Apple 健康读取”，选择最近
24 小时、3 天或 7 天。App 只请求步数和睡眠读取权限，并在本地形成普通语言预览。

### US2：权限或数据状态诚实

如果系统不支持、系统限制、用户没有可读样本或读取权限受限，App 不猜测用户决定，
只显示可修复且真实的状态；冷启动不弹权限。

### US3：用户看见并批准一次分析

用户在发送前看见数据类型、时间窗、聚合值、用途、保留和删除说明。取消不会上传；
批准只对当前 preview/packet 有效且最多 15 分钟。

### US4：安全分析与删除

批准后的聚合事实通过 F153 device proof 发送，Gateway 复用 F152 store/policy/audit
和现有 ProviderRouter 进行一次分析。用户能删除 source packet 与所有可删除派生物；
结果不自动写 Memory。

## Functional Requirements

### FR-001 原生入口

F154 MUST 只存在于原生 SwiftUI App。不得新增 Safari、WebView、PWA、第二配对、
第二 session/device registry 或 Web Cookie 转换。

### FR-002 exact 数据类型

v0.1 只允许：

- `HKQuantityTypeIdentifier.stepCount`
- `HKCategoryTypeIdentifier.sleepAnalysis`

任何其它 HealthKit type、clinical record、workout、心率、位置、metadata 与 unknown
type MUST fail closed。

### FR-003 按使用时机授权

权限请求只能由用户点击“从 Apple 健康读取”触发。冷启动、注册、恢复、后台、通知、
deep link 与定时器不得请求权限。

### FR-004 read-only 物理边界

`toShare` 必须为空。HealthKit save/delete、background delivery、observer/anchored
continuous query、Clinical Health Records entitlement 和 `NSHealthUpdateUsageDescription`
必须物理缺席。

### FR-005 availability 与读取权限诚实性

所有 HealthKit 调用前 MUST 检查 availability。产品不得根据空结果推断“用户拒绝”；
空结果只能显示“没有可读数据或权限受限”。系统限制和不可用可使用独立状态。

### FR-006 时间窗

用户可选最近 24 小时、3 天或 7 天；默认 24 小时，最大 7 天。时间使用当前日历/
时区并显式处理 DST；未来样本、倒置范围和超范围请求拒绝。

### FR-007 步数聚合

步数按本地日边界聚合为 non-negative decimal string + `count`。不能把单条 sample
当作日总量；不能输出 source/device/metadata。

### FR-008 睡眠聚合

睡眠重叠区间 MUST 先裁剪、排序和去重。输出总睡眠时长，以及存在时的
awake/core/deep/REM/unspecified 分阶段分钟数；in-bed 不与 asleep 重复累计。

### FR-009 local-only raw

raw `HKSample`、UUID、source revision、device、metadata 和完整时间序列只能存在于
当前 adapter 调用栈，不落盘、不进 Keychain/UserDefaults/iCloud、日志、crash、
snapshot、audit、prompt 或网络。

### FR-010 normalized preview

本地预览只含：

- exact data type；
- UTC 秒精度时间窗和用户可读本地时间；
- decimal string、unit 与有限 sleep category；
- “数据可能不完整”说明；
- canonical SHA-256。

normalized preview 在取消、发送完成、App session 结束或 24 小时到期时清空。

### FR-011 用户预览

上传前 MUST 展示 exact 类型、时间窗、值、用途、保留期限和删除动作。preview 的
canonical hash 必须与后续 `ReviewBundle`/`ConsentGrant` 精确绑定。

### FR-012 当次批准

批准必须沿用 F152 `ConsentGrant`，绑定 bundle、packet、purpose、owner、device、
approved_at/expires_at；一次消费且最多 15 分钟。重放、过期、跨设备、跨 owner、
preview/packet 漂移均拒绝。

### FR-013 device capability

F154 Verify 时只新增以下 exact capability：

- `health.review.submit`
- `health.analysis.run`
- `health.source.delete`

它们必须由 F153 active device 的短期 token 承载；wildcard/prefix/default/fallback
和 Web identity 转换禁止。

### FR-014 mobile route isolation

所有 F154 route 必须在既有 `/api/mobile/v1/health/*` 下，并继续要求 mobile Host、
device proof、canonical body hash、timestamp、nonce 与 durable replay。Web host、
mobile 其它 path、未注册/撤销设备全部拒绝。

### FR-015 F152 chain

Gateway MUST 复用唯一 F152 `SqlitePrivacyIngestionStore`、state transition、policy、
audit 和 deletion receipt。禁止 Health 专用第二 store、第二 consent、第二 audit、
第二 Memory review 或正文 blob 表。

### FR-016 single analysis

分析只能在当次批准后显式触发一次，复用现有 ProviderRouter。认证失败、超时或模型
失败必须返回 typed failure，不能进入 Echo fallback、不能把失败写成分析完成。

### FR-017 prompt 最小化

模型输入只能含用户已批准的聚合事实、时间窗和用途；不得含 raw sample、Apple
identifier、source/device metadata、token、proof、owner email 或未批准类型。

### FR-018 result 与 Memory

分析结果必须绑定 approved packet，并允许用户删除。F154 不自动生成或写入长期
Memory；只有用户主动选择文本后才能产生 F152 `OptionalMemoryCandidate`，之后仍需
第二次明确确认。

### FR-019 durable audit

读取授权本身不写敏感 audit。review submit、consent、packet、analysis、deletion 与
拒绝只记录 F152 允许的 hash/count/type/capability/result/reason/UTC；正文和 Apple
identifier 必须为零。

### FR-020 删除

删除请求 MUST 清空本地 preview，并沿 F152 provenance 删除 review/approved packet/
result/pending candidate，最后返回 durable `DeletionReceipt`。partial failure 可重入，
不能把 partial 当 completed。

### FR-021 离线与撤销

本地读取/预览可以离线完成，但没有 device proof 时不能上传。设备 revoked 必须优先
于 token expiry，取消 pending 网络动作并保留本地删除能力。

### FR-022 SwiftUI 视觉与无障碍

采用 Claude Design 最早期近黑层级、单一绿色强调、细边框、紧凑卡片与普通用户语言，
同时使用 SwiftUI 原生 navigation/sheet/toolbar、Dynamic Type、VoiceOver、
Reduce Motion 和 44pt 操作区。不得复制 Web DOM/桌面三栏。

### FR-023 系统权限 UI

使用系统 Health 权限 sheet；App 只在请求前解释用途，不复制或伪造系统权限选择器。
用户界面称“Apple 健康”，不得暴露 `HealthKit` 技术名。

### FR-024 可验证性

MUST 有 Swift/Python L4、Gateway L3、Simulator UI/visual/a11y、真机权限与读取、
approved live model L2、secret/raw-field scan、clean checkout、CI 与 architecture
ratchet。Simulator 不能替代真机权限或 Secure Enclave 证据。

## Non-Goals

- 不读取心率、血氧、血糖、体重、workout、位置、医疗记录或生殖健康；
- 不向 Apple 健康写入或删除；
- 不后台同步、不 observer、不长期 raw/normalized cache；
- 不自动周期分析、不医疗诊断、不自动写 Memory；
- 不实现 F155 EventKit；
- 不实现 F156 完整对话、任务、审批、通知与 deep link 产品 shell。

## Success Criteria

### SC-001

冷启动、注册、恢复和后台 0 次 Health permission；用户动作后 exact 两类型请求。

### SC-002

L4 覆盖 step/sleep、重叠、DST、unknown、7 天上界与 empty/limited 状态，0 flaky/sleep。

### SC-003

Gateway 正负矩阵证明 mobile Host/proof/capability/replay/revoke 与 F152 lineage/delete。

### SC-004

Simulator 完成 unsupported/no-readable-data/review/approve/offline/revoked/delete 的
功能与视觉回归；AXXXL、VoiceOver、Reduce Motion 通过。

### SC-005

真 iPhone 完成系统授权 sheet、真实 step/sleep 读取、锁屏/前后台、Wi-Fi↔蜂窝、
撤销与删除；raw identifier/source/metadata 在 package/log/audit/request 中为 0。

### SC-006

一次真实模型分析通过；认证失败无 Echo fallback，删除后所有可删除派生内容不可读取。
