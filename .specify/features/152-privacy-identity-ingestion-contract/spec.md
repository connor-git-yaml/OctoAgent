# F152：Privacy、Identity 与 Ingestion Contract

## 状态

- 日期：2026-07-28
- `GATE_RESEARCH=true`
- `GATE_DESIGN=true`
- `GATE_TASKS=true`
- `GATE_VERIFY=true`
- T001-T014 已完成并通过 focused/full regression、secret scan 与 verification report；
  F153/iOS production 已解锁，但 F158 总 Goal、提交、CI 和原生 iOS 交付仍未完成

## 背景

M12 要把 Octo 带到原生 iOS，并在后续垂直切片读取 HealthKit 与 EventKit。它不是把
Web 包进 App，也不是先做页面再补安全。健康、日程和设备身份引入新的高敏感数据边界，
必须先冻结以下问题：

1. 设备如何被 owner 明确注册、轮换和单独撤销；
2. 哪些数据只留在设备，哪些可以进入 Gateway；
3. 原始样本、归一化事实、分析输入、对话和 Memory 如何物理分层；
4. 用户何时预览、批准、删除，系统如何留下不含敏感正文的 durable audit；
5. 哪些 capability 可以授予原生 App，哪些永远不得进入 App 包。

F152 只拥有跨端隐私、身份语义和 ingestion contract。F153 拥有真实设备密钥、
registration challenge、proof-of-possession、Keychain 和 transport；F154/F155
分别拥有 HealthKit/EventKit；F156 拥有 SwiftUI 产品体验。

## 产品决定

1. 手机产品只有原生 iOS App。Safari、WebView、React Native 与 390px Web 都不是
   手机产品交付。
2. iOS App 不能内置 Cloudflare service token、owner credential、长期 bearer 或
   Web Access Cookie。
3. 设备私钥永不离开生成它的设备；F153 优先使用 Secure Enclave，能力不可用时只能
   进入明确降级审查，不能静默落到源码/文件。
4. 短期凭证必须绑定 owner、device public-key fingerprint、capability、audience、
   expiry 与唯一 token id，并要求请求级 proof-of-possession。
5. HealthKit 与 EventKit 数据默认只在设备本地存在。没有当次用户预览和批准，不得
   进入 Gateway、LLM transcript 或 Memory。
6. Memory 是不可信检索证据，不是指令；敏感事实不能因为被写入 Memory 就获得更高
   权威。
7. F155 保留为明确决策门：Apple EventKit 读取需要系统 full access；如果用户不接受，
   F155 从 M12 移除，禁止把系统权限描述成 read-only。

## Functional Requirements

### FR-001 数据层级必须物理区分

MUST 定义并验证以下阶段，不得使用一个通用 JSON blob 贯穿：

`raw_sample → normalized_fact → review_bundle → approved_analysis_packet →
analysis_result → optional_memory_candidate`

每个阶段必须有独立类型、允许字段、provenance、TTL、owner/device scope 和删除语义。

### FR-002 原始样本设备本地优先

`raw_sample` MUST 默认只存在于原生 App 的进程内或受保护本地暂存区。Gateway 不得
接收 HealthKit/EventKit 原始对象、未裁剪 metadata、系统 identifier 或完整历史导出。

### FR-003 每次敏感分析都先预览批准

MUST 在生成 `approved_analysis_packet` 前向用户展示：

- 数据来源与时间范围；
- 将发送的确切字段与数量；
- 分析目的；
- 是否可能生成 Memory candidate；
- 取消与删除路径。

批准必须绑定 packet hash、purpose、device、owner、expiry，不能复用为未来分析。

### FR-004 明确进入 LLM 的最小包

只有 `approved_analysis_packet` 可以进入一次性 Agent/LLM 分析。MUST 排除：

- 设备私钥、token、attestation/assertion；
- HealthKit/EventKit 系统对象与不可解释 metadata；
- 未获批准的样本、联系人、参与者、位置或日历正文；
- 其他时间范围或其他 data type；
- Memory 中召回的指令性内容。

### FR-005 Memory 写入二次选择

分析结果不得自动写 Memory。只有用户明确选择的 `optional_memory_candidate` 可以进入
既有 Memory review 流程；该候选必须保持 provenance、源 packet hash、撤回/delete
link，并继续按不可信证据处理。

### FR-006 Device capability 最小化

MUST 冻结有限 capability vocabulary；未知 capability fail closed。F153 初始只允许：

- `device.ready.read`
- `device.profile.read`
- `conversation.read`
- `conversation.send`
- `task.read`
- `approval.read`
- `approval.decide`
- `memory_candidate.read`
- `memory_candidate.decide`

HealthKit/EventKit capability 在各自 Feature Verify 前 MUST 物理缺席。

### FR-007 凭证与 proof 绑定

短期凭证 MUST 包含并验证：

- `owner_id`
- `device_id`
- `device_key_thumbprint`
- `capabilities`
- `audience`
- `issued_at`
- `expires_at`
- `token_id`

每个受保护请求还 MUST 绑定 method、canonical path、body hash、timestamp 与 nonce 的
设备签名。token 被复制到另一设备后不能单独使用。

### FR-008 单设备撤销与轮换

MUST 支持：

- 只撤销一个 device，不登出其他设备或桌面 Web；
- key rotation 先注册新 key，再使旧 key 进入有界 overlap，随后不可用；
- token expiry 后不得 refresh-by-ambient；必须以仍有效 device proof 换取；
- owner 可查看设备名称、最近使用时间和 capability 摘要，不显示 key/token。

### FR-009 durable audit

注册、批准、发送、分析、Memory candidate、删除、撤销、轮换和拒绝 MUST 形成 durable
audit。Audit 不得保存 raw sample、analysis packet 正文、token、签名、owner email、
系统 calendar/health identifier；只保存 hash、count、type、scope、结果、reason code
与时间。

### FR-010 删除必须覆盖派生链

用户删除一个 source packet 时，MUST 能定位并删除：

- 本地 raw/normalized cache；
- Gateway review/approved packet；
- 未提交或已提交的 analysis payload；
- 可删除的 analysis result；
- 尚未确认或已写入的 Memory candidate。

保留的 audit 只能证明删除发生，不得保留正文。删除失败必须可恢复，不能宣称完成。

### FR-011 TTL 与最小保留

- raw/normalized local draft：默认会话结束或 24 小时，以更早者为准；
- review bundle：24 小时；
- approved packet：一次使用或 15 分钟，以更早者为准；
- short-lived device token：最多 15 分钟；
- nonce：最多 5 分钟且单次使用；
- audit：由部署者保留策略控制，但只能含非敏感 metadata。

任何放宽必须经过新的 Design Gate。

### FR-012 HealthKit 权限诚实性

F154 MUST 按 data type、按使用时机请求读取权限。产品不得根据“查不到样本”推断用户
拒绝，因为 HealthKit 有意不向 App 暴露完整读取授权事实。

### FR-013 EventKit full-access 决策

F155 实施前 MUST 取得用户对“系统授予 full access、Octo 产品和代码保持 read-only”
的明确决定。若继续：

- 写入/删除 EventKit 的 capability、protocol 和调用点必须为零；
- UI 在系统授权前说明权限事实；
- 只读取有限时间范围并先预览。

### FR-014 视觉与平台边界

iOS 继承 Claude Design 最早期方案的深色层次、绿色强调、卡片节奏、信息密度与普通
用户语言，但必须使用 SwiftUI、Apple 原生导航、sheet、toolbar、Dynamic Type、
VoiceOver 和 Reduce Motion。不得复制 Web DOM 或桌面三栏。

### FR-015 可验证性

MUST 有：

- Pydantic/JSON schema exact contract；
- property/adversarial L4；
- deterministic Gateway L3；
- iOS pure/unit 与 Keychain/crypto integration；
- Simulator UI；
- 注册、撤销、Apple 权限与 Secure Enclave 的真机证据；
- secret/log/source/package scan。

## 非目标

- F152 不创建 iOS 页面、Xcode 工程或网络 client。
- F152 不实现 HealthKit/EventKit 读取。
- F152 不选择 F153 的最终 transport，只有安全不变量。
- F152 不创建第二 Memory、audit、identity 或 session 系统。
- F152 不改变 F150 桌面 Web Access。

## 成功标准

1. threat model、data model、capability、consent、TTL、deletion 和 audit 合同无歧义。
2. 每个生产字段与 owner Feature/task 双向可追踪。
3. unknown stage/capability/provenance/consent fail closed。
4. secret/raw sample 不进入日志、Memory、持久化 audit 或 App 包。
5. F153 可在不重写 F152 合同的前提下实现真机 device trust。
