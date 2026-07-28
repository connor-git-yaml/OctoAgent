# F158 Milestone Product Closure Verification Report

## 当前结论

- 日期：2026-07-28
- 状态：`PARTIAL`
- `GATE_VERIFY=false`
- 当前分支：`codex/f158-milestone-product-closure`
- 基线：`origin/master=db3214fff722c6f969baf99528a76fc03a1e21a1`
- 当前交付提交：`e84ffd435f742ba2784b85c074346ab63ecedbc1`

F158 已完成 Milestone/Blueprint/Feature 真值审计、F150 Settings 用户入口、桌面 Web
逐 route/state 功能 E2E、Claude 早期设计视觉恢复、视觉 regression、F152 privacy
authority 与 F153 device-trust/iOS target 代码闭环。确定性前端、后端和 repository
architecture gate 均通过。

整体 Goal 尚未完成：个人部署已更新为当前交付提交，但登录态 SPA/API/SSE 尚未复验，
个人实例的 OpenAI Codex refresh token 也已失效；本机没有可用 iOS Simulator
runtime，也没有连接真 iPhone；F154-F156 因 F153 Verify fail-closed 尚未开始；
主线确认仍未完成。

## 已通过

### Web 功能与视觉

- Vitest：`70 files / 598 passed`
- production build：PASS
- frontend complexity：PASS
- Playwright 完整回归：`39 passed / 0 failed / retries=0`
- Claude 主工作台与十个业务 surface pixel baseline：`14` 个
- 主工作台最终截图：
  `evidence/visual-baseline/2026-07-28/web-claude-restored-final-1440x900.png`
- 截图 SHA-256：
  `9363b49495fa52bfd57fe52b4f2d8707c2c1e3b45a889ac25c44232df3acd9e8`

桌面 Web 保留紧凑左栏、中央对话主舞台和右侧运行 rail。390px 只作为桌面 Web
窄窗口健壮性，不是手机产品入口。手机产品只走原生 iOS App。

### Claude Design 谱系

- 最终导出：
  `../149-web-pages-v2/design-output/2026-07-28/OctoAgent Web.dc.html`
- SHA-256：
  `1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`
- 必需 frame、逐 frame 验收表与 10×7 状态矩阵：PASS
- Spotify Design System、Figtree、radial gradient：`0`
- 最终 Task Detail 抽查：
  `evidence/visual-baseline/2026-07-28/claude-design-final-task-detail.png`
- 抽查 SHA-256：
  `201ae887e8072f40afc982a603693f91a254db27b99d21195fc2f60853b903e4`

后期大 Hero、径向发光、大圆角低密度卡片墙没有保留；实现以 Claude 最早期方案为
视觉语言基线，而不是让设计迁就旧 Web。

### 后端与架构

- F152/F153/F158 focused：`77 passed / 1 existing warning`
- 确定性后端完整回归：
  `5709 passed / 9 skipped / 1 xfailed / 1 xpassed`
- repository architecture gate：PASS
- 七个 F153/F158 新增多参数函数已收敛为 typed request/options/context；
  `PLR0913` 没有新增豁免。

真实 OAuth 的 `apps/gateway/tests/e2e_live` 不属于确定性 lane。本轮一次错误地把它
纳入完整回归后遇到宿主 OAuth refresh token 已复用；该外部状态失败没有计作回归
通过，也没有通过重试掩盖。

### iOS 当前可证明范围

- F153 Python/Gateway behavior：PASS
- iPhoneOS Release App target build：PASS
- iPhoneOS Debug XCTest target build：PASS
- source/release bundle secret scan：PASS
- Release executable SHA-256：
  `431e03e36616a7c9fa1a18c67064f6da1b651a223b9fceacd858140d654a3cbe`

这些证据只证明 target 可编译，不证明 App 已启动或真机安全属性成立。

## 个人部署现状

- Gateway loopback origin：`200`
- `octo.maojiwang.work` DNS/Access：登录重定向正常
- Cloudflare named tunnel：`4` 条 active connection，request error=`0`
- 本轮没有修改 Cloudflare 账户、DNS、Access application、tunnel 或凭证

仓库正式 managed-checkout installer 已把 `~/.octoagent/app` 更新为
`e84ffd435f742ba2784b85c074346ab63ecedbc1`，完成依赖同步和 production build；
checkout clean。重启 Gateway 后 loopback `/ready?profile=core` 与 `/` 均为 `200`，
个人域名返回预期 Access `302`，tunnel LaunchAgent running。部署 checkout 的
`claude-workbench.css` SHA 与分支均为
`c550467990e6cf04f9822d15da142a3758b422aea464fb2d2ab0f5612618a325`。

Chrome 能枚举已有 Access 登录页，但接管页面超时；本轮没有读取浏览器 cookie 或
local storage 绕过认证。因此登录后的 SPA、API、SSE、刷新、过期、重新认证、登出和
Settings remote-access 仍保持 MISSING。

Gateway 日志同时显示 OpenAI Codex refresh token 已被复用并在刷新时返回 401。ready
只证明 provider route 配置存在，不证明真实模型对话可用；重新登录 provider 与真实
对话复验是独立未完成项。

## 未通过与外部阻断

### iOS Simulator

- Xcode：`26.6 (17F113)`
- iPhoneOS SDK：`26.5`
- CoreSimulator：`1051.54.0`
- Xcode 要求：`1051.55.0`
- 已安装 Simulator runtime：`0`

runtime 安装需要 macOS 管理员授权。当前没有 Swift XCTest 行为执行、Simulator
冷启动、导航、状态、Dynamic Type、VoiceOver、Reduce Motion 或视觉 snapshot 证据。

### 真 iPhone

当前没有连接真 iPhone，因此 Secure Enclave、ThisDeviceOnly Keychain、注册/轮换/
撤销、Wi-Fi/蜂窝切换、前后台恢复与 Apple 权限场景均未验证。

### 后续 Feature

F154 HealthKit、F155 EventKit 与 F156 SwiftUI Companion 必须等待 F153 Simulator/
真机 Verify 通过，不允许用 target build 或 Web E2E 替代。

## Gate 判定

| Gate | 结果 |
|---|---|
| Milestone/Blueprint/Feature delivery audit | PASS |
| Desktop Web functional E2E | PASS |
| Desktop Web visual E2E | PASS |
| Claude early-design lineage cleanup | PASS |
| F150 local product entry | PASS |
| deterministic backend regression | PASS |
| repository architecture gate | PASS |
| iPhoneOS target compilation | PASS |
| GitHub Actions frontend / architecture / benchmark / L1 Playwright | PASS |
| GitHub Actions backend deterministic | PASS |
| personal deployment on current branch | PASS |
| authenticated personal SPA/API/SSE | MISSING |
| personal real-model conversation | BLOCKED（provider refresh token 401） |
| iOS Simulator functional/visual E2E | BLOCKED |
| real-device security/lifecycle | BLOCKED |
| F154-F156 product implementation | CLOSED |
| commit / push | PASS |
| complete CI | PASS |
| mainline confirmation | MISSING |

因此当前不能把 F158 或跨 Milestone Goal 标记完成。
