# F156：原生 iOS Companion Experience

## 状态

- Research：PASS
- Design：`DRAFT_COMPLETE_GATE_CLOSED`
- Tasks：`DRAFT_COMPLETE_GATE_CLOSED`
- Implement：`CLOSED_ON_F154_F155_DECISIONS`
- Verify：`CLOSED`

## 背景

F153 已交付原生 registration 与 device trust 的 Simulator 范围，但当前 App 连接后
仍停留在注册状态页，不是完整 Companion。F156 拥有原生 iOS 的顶层导航、对话、
任务、审批、Memory candidate、连接状态与通知体验。

手机产品只走原生 SwiftUI App，不提供 Safari/WebView。视觉与交互以 Claude Design
最早期方案为共同语言基线；实现应适配设计，不得为了复用现有 Web 页面而把 App
改成桌面三栏，也不得把后来不够好看的设计引入原生端。

## 前置硬门

F156 production 开始前必须：

1. F153 `GATE_VERIFY=true`；
2. F154 要么 `GATE_VERIFY=true`，要么经产品决定从本次 companion 范围移除；
3. F155 必须完成方案 A/B 产品决定；若保留，则在其 Verify 前 calendar surface
   只能显示明确的“尚未开放”，不得伪造数据；
4. F151 单一 architecture authority 批准 F156 exact paths/symbols；
5. 当前 Gateway OpenAPI/事件/动作清单重新 recon，禁止 UI 发明后端不存在的动作。

第 5 项已由 `inventories/mobile-api-recon.v1.json` 完成当前字节只读 recon：现有
mobile hostname allowlist 只有 enrollment/token/ready/device-profile 五条 F153 路由；
Chat、Task、Approval、Memory 与 Web Notification 路由仍是 Web front-door 专用，
不能被 iOS 直接复用。其余四项前置门继续关闭。

安全边界的权威清单见 `threat-model.md`；其中 service token/WebView 绕过、
APNs 正文、后台敏感动作、快照泄漏与 revoked 竞争都必须在 Implement 前有对应
test owner。

## 产品结构

连接成功后使用四个原生顶层区域：

1. **对话**：会话列表、消息流、单行/多行 composer、发送与实时状态；
2. **任务**：运行中/等待/完成/失败任务、详情、允许的控制动作与工件；
3. **收件箱**：审批请求和 Memory candidate 的独立人工决定；
4. **设置**：连接/设备、通知、隐私、Apple 健康、可选日历和诊断。

四区使用 `TabView`，每区内部使用独立 `NavigationStack`。连接/撤销是全局状态，
不是第五套页面状态或另一份 session。

## 用户故事

### US1：从连接进入可用 Companion

设备连接后进入 App shell；离线、token 过期、server unavailable 或 revoked 时，
当前内容保持可读，危险动作禁用并显示可恢复状态。

### US2：对话

用户能查看会话、打开消息流、发送文本，并看见 sending/streaming/completed/failed。
原始 SSE/JSON、model/provider、token 和内部 event name 只在 Advanced 诊断。

### US3：任务

用户能看见任务列表/详情/进度/事件摘要/工件，并只调用 OpenAPI 已证明的 pause、
resume、cancel 或 retry 动作；不存在的动作不得由 UI 猜测。

### US4：审批与 Memory candidate

审批和 Memory candidate 进入收件箱，各自显示普通语言摘要、来源、影响和明确决定；
二者不能共用一个模糊“允许”动作，也不能自动批准。

### US5：连接、通知与敏感能力

设置页展示单一设备连接、撤销、通知权限和隐私删除。Health/Calendar 只在 owner
Feature 已通过时进入；APNs payload 只含 opaque route identity，不含消息、审批、
Memory、health 或 calendar 正文。

## Functional Requirements

### FR-001 原生唯一入口

F156 MUST 只使用 SwiftUI/UIKit 必要桥接。Safari、WebView、React Native、网页截图、
390px Web、第二 pairing/session/device registry 均不是手机产品实现。

### FR-002 单一身份与 transport

必须复用 F153 Secure Enclave/Keychain、短期 capability token、request proof、
single `DeviceTrustClient`/URLSession。不得创建 browser login、Web Cookie bridge、
Cloudflare service token、长期 bearer 或第二 HTTP/SSE client。

### FR-003 四区导航

顶层 exact tabs 为 `chat`、`tasks`、`inbox`、`settings`。Tab 只做导航不做动作，
每个 tab 保持自己的 `NavigationStack` path；deep link 只能选择已存在 destination。

### FR-004 finite product state

UI 只消费 canonical domain state，不从 HTTP status、raw event name、字符串包含或
多个本地 boolean 重新推断。连接、conversation、message、task、approval、
memory candidate 和 notification 均有 finite enum 和 unknown fail-closed。

### FR-005 对话

至少支持：

- conversation list：loading/empty/loaded/recoverable error/offline；
- detail：历史消息、pending send、streaming、completed、failed；
- composer：空内容禁用、单次 send identity、重复提交去重；
- reconnect 后从服务器权威状态恢复，不拼接重复 token/event。

不在 iOS 内实现独立 Agent runtime、model picker 或 prompt engine。

### FR-006 任务

任务列表和详情只消费 Gateway canonical task/event/artifact contract。允许动作必须由
capability 和 task state 双重决定；unknown/future state 只读显示且无危险动作。

### FR-007 收件箱

审批请求和 Memory candidate 使用不同 typed projection/command：

- approval：approve/reject，显示工具/文件/权限影响；
- Memory candidate：accept/reject/withdraw（以实际 Gateway contract 为准），
  显示将被长期保留的文本；
- 决定前必须看到 exact target/source 与结果；
- conflict/expired/already-decided 显示服务器事实，不本地覆盖。

### FR-008 连接状态

awaiting/connecting/connected/disconnected/offline/revoked 沿用 F153 唯一词表；
不复制第二套连接 reducer。revoked 优先于 token expiry，立即取消写动作并清除凭证。

### FR-009 敏感 slice ownership

F154 Health 和 F155 Calendar 只由各自 owner 交付读取/preview/consent/analysis。
F156 只提供导航 composition 和跨屏状态，不复制 adapter、store、schema 或 consent。

### FR-010 通知权限

通知只能在用户已看见其用途后请求；冷启动和 registration 不弹。用户拒绝不影响
App 主功能。不得用通知权限状态推断 APNs registration 成功。

### FR-011 APNs

APNs provider/server 只使用部署者显式配置的 Apple credentials；不得进入 App 包、
仓库、日志或 audit。device token 绑定现有 F153 device identity，轮换/注销/revoke
必须更新。payload 只含 notification type、opaque object id 和 collapse identity，
不含消息正文、审批内容、Memory 文本、Health/Calendar 数据或 owner email。

### FR-012 通知导航

点击通知先验证 active device、当前 capability 与服务器对象状态，再进入 typed
destination。unknown/expired/revoked notification 回到安全收件箱或连接页，不执行
动作。notification 本身不能 approve/reject/cancel/send。

### FR-013 背景边界

App 可以注册系统允许的有限 background refresh 以更新 badge/摘要，但不能承诺固定
轮询周期、常驻连接或准时执行。后台任务不得读取 Health/Calendar、请求 Apple 权限、
调用 LLM、自动决定审批/Memory 或发送对话。

### FR-014 状态恢复

只持久化轻量 navigation identity、tab selection 和非敏感 UI preference。
token 仅 Keychain；消息/approval/Memory/Health/Calendar 正文不得进入
SceneStorage/AppStorage/snapshot。回前台后以服务器权威状态刷新。

### FR-015 视觉基线

Claude Design 最早期方案是唯一视觉/交互基线：

- 上游不可变导出：
  `.specify/features/149-web-pages-v2/design-output/2026-07-28/OctoAgent Web.dc.html`；
- SHA-256：
  `1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`；
- Web 已接受的 14 个 `claude-early-*` baseline 是同一视觉语言的参考，
  不是把 Web DOM/三栏结构复制到 iPhone 的许可。

- 近黑背景与蓝黑主舞台；
- 单一绿色作为状态与主要动作强调；
- 低亮度连续卡片、细边框、清晰层级和克制密度；
- 有呼吸感的留白、紧凑但不拥挤的正文；
- 普通用户语言，技术细节只在 Advanced。

不得把现有 Web 的实现限制、后来不够好看的卡片堆叠或通用模板当作基线。

### FR-016 原生适配

必须用 iOS 原生 `TabView`、`NavigationStack`、sheet、toolbar、swipe-back、
safe-area、keyboard、Dynamic Type、VoiceOver、Reduce Motion、Differentiate
Without Color 和至少 44pt 操作区。必要的原生适配必须在 fidelity ledger 记录原因、
影响与截图，不得以“实现方便”为理由偏离。

### FR-017 Visual E2E

每个关键 scene/state 必须有可复现 Simulator screenshot baseline、pixel diff、
semantic/geometry oracle 和 accessibility tree。视觉通过不能只靠 token、DOM marker、
人工“看起来接近”或同实现自产 baseline。

### FR-018 功能 E2E

必须实际启动 Gateway 与 iOS App，逐条跑通 scenario matrix：

- normal/empty/loading/error/offline/revoked/conflict；
- conversation send/stream；
- task list/detail/actions/artifact；
- approval approve/reject；
- Memory candidate accept/reject；
- notification permission/receive/deep link；
- Health/Calendar owner 状态；
- background/foreground、network switch、token refresh/revoke。

### FR-019 真机

Simulator 不能替代 Secure Enclave、APNs、Apple 权限、lock/background、Wi-Fi↔cellular、
notification tap 与实际性能证据。最终必须在一台真实 iPhone 上跑完 approved matrix。

### FR-020 可观察性与隐私

普通 UI 不显示 JWT/AUD/JWKS/capability/proof/SSE/raw JSON/model alias。Advanced
可显示脱敏 request id、连接时间和 typed error。日志/audit/package/snapshot
secret/body scan 必须通过。

### FR-021 架构

禁止第二 transport、state store、session/device、notification registry、DTO
手写副本、compat layer 或后台 scheduler。Gateway contract→generated adapter→
application coordinator→pure projection→SwiftUI composition 是唯一方向。

## Non-Goals

- 不把完整 Web 管理台搬到 iPhone；
- 不在 App 内编辑 Agent/Skill/MCP/Provider 配置；
- 不做 iPad/macOS/watchOS；
- 不实现离线 Agent runtime 或本地 LLM；
- 不通过 notification 执行危险动作；
- 不在 F156 重写 F154/F155 采集逻辑。

## Success Criteria

### SC-001

真实 App 从冷启动到 connected shell、conversation send、task detail、approval、
Memory candidate 的核心旅程可完成，0 WebView/第二 auth。

### SC-002

scenario matrix 每一行有 L4/L3/L1/device owner、command、expected outcome、截图和
artifact；遗漏状态数为 0。

### SC-003

所有 committed visual baselines 在 clean checkout/CI 稳定通过；关键 scene 在
AXXXL、VoiceOver、Reduce Motion、high contrast 和 iPhone 尺寸矩阵无截断/遮挡。

### SC-004

真机 APNs 收到 notification 并安全 deep link；payload/body secret scan 为 0，
revoked device 无法读取或执行动作。

### SC-005

Claude early fidelity ledger 中无“实现方便”偏离；后来不够好看的设计元素在 source、
snapshot 与 design artifact active set 中为 0。

### SC-006

完整 M12 clean checkout、CI、个人部署、真实模型与双端 final audit 通过。
