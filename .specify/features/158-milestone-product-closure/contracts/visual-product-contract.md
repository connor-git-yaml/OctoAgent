# F158 Web/iOS 视觉产品合同

## 权威基线

- Claude Design project：`851e3fb2-2b5b-4251-a095-8a678b1b7fec`
- 不可变导出：
  `.specify/features/149-web-pages-v2/design-output/2026-07-21/OctoAgent Web.dc.html`
- 导出 SHA-256：
  `a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`
- 首要 desktop frame：`#1a 对话工作台（主视图）`
- 直接渲染证据：
  `evidence/visual-baseline/2026-07-28/claude-design-early-1a-browser.jpg`

该早期 frame 是视觉方向的上游事实。F148/F149 当前生产 UI、后期 Claude Design
variant、通用 UI 推荐与实现方便都不能覆盖它。

## Web 不变量

1. Desktop 使用紧凑三栏：
   - 左：品牌、项目/会话层级、运行提示、产品导航；
   - 中：蓝黑标题层、对话主舞台、委派/任务/工件卡、底部输入；
   - 右：本会话运行状态、事件流、工作文件。
2. 左栏首先服务项目与会话，品牌不能占用一个大 hero 卡。
3. 中央空状态只在真的没有对话时出现；确定性验收 fixture 必须展示真实消息层级、
   委派卡、工件卡与运行提示。
4. 右栏不能退化为一个大空状态；有运行数据时必须按早期稿分层展示。
5. 绿色只作强调、成功和主动作；标题背景保持早期稿的蓝黑层次。
6. 卡片、圆角、留白、字号和行高采用紧凑工作台节奏，禁止后期“大圆角 + 大空白 +
   低密度”覆盖。
7. 普通页面只出现用户语言；诊断字段进入可折叠 Advanced。
8. 键盘焦点、语义标签、reduced motion、文本缩放和对比度不得为了视觉还原退化。
9. 390px 是桌面 Web 的窄窗口健壮性，不是手机产品入口；允许单列重排，但视觉语言
   保持一致。

## iOS 不变量

1. 手机产品仅为原生 SwiftUI App；禁止 Safari、WebView 或 React Native 冒充。
2. 继承早期稿的深色层次、绿色强调、紧凑卡片节奏、信息层级与普通用户语言。
3. 使用 Apple 原生 navigation、sheet、toolbar、gesture、Dynamic Type、
   VoiceOver 与 Reduce Motion；禁止复制 Web DOM 或硬塞三栏。
4. 认证使用 F153 的 device trust/capability，不复用 Web Cookie，不内置
   Cloudflare service token。
5. 所有平台性偏离必须在 visual-deviation ledger 中记录原因、影响、owner 与证据。

## 合法偏离

只接受以下原因：

- 明确功能合同；
- 可用性；
- 无障碍；
- iOS 原生平台规范。

“现有代码难改”“便于复用当前 Web”“通用模板更流行”均不是合法理由。

## 视觉验收

- Web：Playwright 固定浏览器、字体、viewport、时区、动画和确定性数据；
  `toHaveScreenshot` 或等价像素 diff。失败必须保留 actual/diff/expected。
- iOS：固定 simulator runtime/device/locale/content size 的 SwiftUI snapshot；
  关键安全与权限路径再补真机截图。
- 基线文件只允许由明确的设计审查任务更新；测试不得自动接受新截图。
- DOM marker、token 名、无 overflow 和文字自述只能作辅证，不能单独判 PASS。

## 后期设计清理

云端设计项目中的每个后期 frame/variant 必须归入：

- `KEEP_EARLY_BASELINE`
- `KEEP_FUNCTION_RESTYLE_TO_EARLY`
- `SUPERSEDED_DELETE`
- `PLATFORM_SPECIFIC_IOS`

最终导出必须包含 lineage/superseded manifest，且 Spotify 或第二主题依赖为零。
