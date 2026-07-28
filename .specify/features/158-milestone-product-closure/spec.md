# F158：Milestone 真实产品交付闭环

## 状态

- 日期：2026-07-28
- `GATE_RESEARCH=true`
- `GATE_DESIGN=true`
- `GATE_TASKS=true`
- Implement 已放行；完成声明仍由 Verify Gate 关闭

## 背景

M11 在 Blueprint 中被标记为完成，但本轮从 `origin/master`、真实截图、生产
import graph 与验证制品重新审计后，发现“代码或文档存在”被错误地等同于“用户可达且
视觉还原的产品已经交付”：

1. F150 的 Gateway、安全链、API 与 standalone `RemoteAccessSettings` 已进入主线，
   但该组件没有生产消费者、没有样式，也没有出现在 Settings composition 中。
2. F149/F148 的真实 Web 截图只保留深色、绿色强调和三栏骨架，没有忠实保持
   Claude Design 最早期方案的层级、留白、卡片节奏、信息密度、排版与视觉张力。
3. 现有 Playwright 只证明功能、响应式与无障碍，不存在基于
   `toHaveScreenshot` 或等价基线的视觉回归门。
4. F150 没有标准 `verification/verification-report.md`。
5. M12 的 F152-F156 仍只有 Blueprint 规划；仓库中没有 iOS 工程、Swift 源码或真机
   启动证据，不能声称已经执行 iOS 场景验收。

F158 是跨 Milestone 的交付审计、纠偏协调与最终验收 Feature。它直接拥有 M11
Web/F150 的收口与最终双端验收矩阵，但不夺走 F152-F156 的隐私、设备信任、
HealthKit、EventKit 与原生体验所有权。

## 产品目标

### FR-001 Milestone 真值审计

MUST 逐项检查 Blueprint 与 Milestone 中所有“完成”“稳定”“已交付”声明，建立
要求、owner、代码、真实运行、E2E、视觉证据、部署证据和文档证据的双向矩阵。
缺失、间接、不可复现或与真实 UI 矛盾的证据 MUST 判为未完成。

### FR-002 Web 可启动与场景闭包

MUST 从干净环境启动真实 Gateway 与 Web，并逐项验证：

- Cloudflare Access 登录、刷新、过期、登出、重新认证、403 资源权限与故障恢复；
- 首页/对话、任务列表、任务详情、审批、自动化、设置、智能体、记忆、文件、技能、
  MCP；
- loading、empty、recoverable error、origin 403、not-found、disconnected、409；
- Settings 中远程访问状态、打开电脑 Web、Access logout/recovery 与 Advanced 诊断；
- 390px 只作为 Web 窄窗口健壮性，不作为手机产品入口。

每个场景 MUST 有真实浏览器执行、稳定断言、失败截图/trace 与可复现命令。

### FR-003 原生 iOS 可启动与场景闭包

手机产品 MUST 只走原生 iOS App。MUST 按 F152→F153→F154→F155→F156 的安全顺序
建立并启动真实 SwiftUI 工程；不得以 Safari、WebView、React Native 或截图原型冒充。

在相应 owner Gate 通过后，至少覆盖：

- 冷启动、前后台切换、断网、重连与离线提示；
- 设备密钥生成、owner 辅助注册、短期 capability token、Keychain、轮换与单设备撤销；
- `/ready` 与最小 API 后再接对话、任务、审批、记忆候选、连接状态和通知；
- 无 Cloudflare service token、无 Web Cookie 冒充 device proof；
- HealthKit/EventKit 只在各自隐私与权限 Gate 通过后进入。

模拟器测试不能替代设备信任与 Apple 权限的真机证据。

### FR-004 Claude Design 最早期方案为唯一视觉基线

Web 与 iOS MUST 以 Claude Design project
`851e3fb2-2b5b-4251-a095-8a678b1b7fec` 的最早期方案为共同视觉语言基线。
实现 MUST 适配设计，禁止为了复用当前 Web 结构而让设计迁就代码。

Web 必须恢复并保持：

- 紧凑三栏工作台与清晰的会话/项目层级；
- 中央对话主舞台、蓝黑标题层、任务与工件卡片；
- 右侧运行状态、事件流和工作文件的分层卡片；
- 原稿的留白、信息密度、卡片节奏、排版和绿色强调；
- 普通用户语言、Advanced 技术信息隔离、键盘焦点和 reduced motion。

iOS 保持同一视觉气质，但 MUST 使用 SwiftUI、Apple 原生导航、手势、控件与
无障碍语义，不复制 Web DOM/三栏结构。

任何视觉偏离只允许因为明确功能合同、可用性、无障碍或 Apple 平台规范，并必须记录
原因、影响和证据。“实现方便”不是合法理由。

### FR-005 视觉 E2E 是硬门

MUST 为 Web 建立稳定截图基线和视觉 diff，覆盖早期设计中的主要 desktop frame、
关键状态与 390px Web 窄窗口；MUST 为 iOS 建立 Xcode/SwiftUI snapshot 或等价
可复现视觉回归，并补关键真机截图。

视觉 PASS 不能由 token、DOM marker、无 overflow 或人工文字描述替代。基线更新
MUST 经过明确设计审查，禁止测试自动覆盖。

### FR-006 清理 Claude Design 后期不佳方案

MUST 在真实 Claude Design 云端项目中识别后来产生且不符合早期方向的 frame、
variant 或视觉 patch；保留功能/状态合同，将其改回早期视觉语言或删除。最终必须重新
导出不可变设计制品，记录 project UUID、文件 SHA、frame 清单和 superseded 清单。

### FR-007 F150 用户可达闭包

MUST 将 F150 已有 remote-access adapter/view-model 接入 F149 Settings composition，
使用早期 Claude Design 视觉语言并提供完整 L4/L3/L1 合同。不得新建第二 fetch、
状态机、认证层或移动 Web 入口。MUST 生成正式 F150 verification report，并纠正文档中
任何把 standalone component 当成用户可达交付的表述。

### FR-008 交付证据

最终 MUST 同步 Spec、Tasks、Blueprint、Milestone、设计制品与 verification report，
并以以下证据证明完成：

- 干净检出构建和测试；
- Web 与 iOS 的真实启动命令/版本/截图/trace；
- 功能 E2E 与视觉回归 E2E；
- 真机专属场景证据；
- main 分支提交、权威 CI、个人部署验证；
- requirement-by-requirement completion audit。

### FR-009 真实模型就绪与失败终态

最终个人部署验收 MUST 使用当前配置的 ProviderRouter 发起一次受控真实模型调用。
`octo doctor --live` MUST 包含该调用并明确报告所用 alias/provider/model；仅检查凭证
文件存在、路由可解析、Telegram readiness 或 Gateway `/ready` 不得冒充模型可用。

缺失、过期、被撤销或 refresh 失败的凭证 MUST fail closed：

- 不得降级为 Echo 并显示伪成功；
- 用户任务必须在一次失败处理内进入 `FAILED` 终态，不能留在 `RUNNING`；
- `MODEL_CALL_FAILED.error_category` 必须为 `auth_error`；
- worker 结果必须 `retryable=false`，并提供重新授权指引；
- `doctor --live` 必须返回 blocking failure 和非零退出。

普通瞬态 provider/transport 故障仍可按既有 fallback/retry 合同处理，不得因本要求被
一概改成认证失败。

## 非目标与禁止项

- 禁止用手机浏览器或 WebView 冒充 iOS App，因为手机产品已明确为原生 iOS。
- 禁止用 DOM marker、设计 token、人工清单或“看起来接近”代替视觉 diff。
- 禁止为快速见绿引入第二 transport、auth、session、device、store、theme 或 registry。
- 禁止让通用 UI 推荐覆盖 Claude Design 早期稿；通用规则只补充无障碍与平台约束。
- 禁止将 M12 隐私/设备信任 Gate 折叠进一个无法审计的大型 iOS 实现。
- 禁止覆盖旧截图、旧设计导出或失败证据；替换必须可追溯。

## 成功标准

1. 审计矩阵中所有必须项均有直接、当前、可复现证据。
2. F150 Settings 远程访问入口在真实 Web 中可达并通过全层级测试。
3. Web 所有产品场景与关键异常态在真实启动后通过。
4. 原生 iOS App 能真实启动，且已获准场景在模拟器/真机层级分别通过。
5. Web 与 iOS 的视觉回归门通过，真实截图与早期 Claude Design 基线一致。
6. Claude Design 云端只保留通过审查的最终方向，后期不佳方案有明确 superseded 记录。
7. Blueprint/Milestone 不再存在“文档完成但产品不可达”的状态漂移。
8. 权威 CI 与个人部署验证通过，且正式 verification report 完整。
9. `octo doctor --live` 与个人部署真实对话均证明真实 provider 可用；认证失败回归
   证明任务快速进入 `FAILED` 且不会 Echo 假绿。
