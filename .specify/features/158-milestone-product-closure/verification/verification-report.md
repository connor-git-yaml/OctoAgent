# F158 Milestone Product Closure Verification Report

## 当前结论

- 日期：2026-07-31
- 状态：`PARTIAL`
- `GATE_VERIFY=false`
- 当前分支：`codex/f158-milestone-product-closure`
- 基线：`origin/master=db3214fff722c6f969baf99528a76fc03a1e21a1`
- 本轮核验起点提交：`0a377b0bed74e2940384098889d8e511f83bc67e`
- 当前通过完整 CI 的代码提交：`ebe8cd29f4c4c3b46e79edfed93a726cde537af6`
- 当前个人部署提交：`35d7aa14f6f146a47084e4725e11df83cf034152`

F158 已完成 Milestone/Blueprint/Feature 真值审计、F150 Settings 用户入口、桌面 Web
逐 route/state 功能 E2E、Claude 早期设计视觉恢复、视觉 regression、F152 privacy
authority 与 F153 device-trust/registration Simulator 闭环。确定性前端、后端和
repository architecture gate 均通过。

2026-07-29 在当前分支提交 `1d6b11704aa6` 再次从实际运行界面与测试入口复核：
本机 Gateway Web 能加载三栏工作台并从真实导航进入 Settings；F150
“从电脑安全访问 Octo”区块、脱敏网页地址/使用者、打开网页、退出登录、重新检查与
高级诊断全部可达。当前瞬时状态诚实显示 `pending_verification`，没有把 Access
`302` 冒充登录后 ready。随后同一当前字节的 Vitest 为 `70 files / 598 passed`，
Playwright 为 `39/39 passed / retries=0`；14 个 `claude-early-*` 像素基线未更新。
同一已启动的 iOS 26.5 / iPhone 17 Pro Simulator 完整 scheme 再次为
`12/12 passed`，本次 result bundle 位于
`/tmp/f158-ios-rerun.iZzEea/current.xcresult`。这些复验不改变真机与远程认证边界。

2026-07-31 在当前提交 `2ef6cc9e937a29e782afdc8645a1968a38c0812e` 上又执行一次
完整 scheme，仍为 `12 passed / 0 failed / 0 skipped`。六个 registration 状态逐一
实际启动并通过 committed Claude 早期像素基线，未更新 baseline；result bundle 为
`/tmp/f158-ios-current.2jqWRm/current.xcresult`，持久化摘要见 F153
`evidence/simulator/2026-07-31/verification-report.md`。同日只读部署审计确认
Gateway/cloudflared running、Web Access `302`，也确认现有 ingress 仍只有 Web
hostname，不能把本次 Simulator PASS 提升为 mobile live 或真机 PASS。

整体 Goal 尚未完成：个人部署已更新为当前交付提交，但登录态 SPA/API/SSE 尚未复验，
个人实例的 OpenAI Codex refresh token 也已失效；F153 Simulator 已通过但没有连接
真 iPhone，Cloudflare mobile live 与 F153 Verify 仍缺；F154 只完成 Research/Design/Tasks
Gate，F155 只完成 Research/产品决策档案，F156 完成含 40 场景矩阵的
Design/Tasks 草案与 current mobile API recon（5 routes / 8 product gaps），三者
production 和产品 E2E 均为 0；iOS
变更已提交、推送并在干净 detached worktree 复验；权威 GitHub Actions run
`30378276329` 五个 job 全绿，主线确认仍未完成。

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
- 确定性后端完整回归（无 coverage）：
  `5716 passed / 9 skipped / 1 xfailed / 1 xpassed`
- CI 等价 coverage 回归：
  `5715 passed / 10 skipped / 1 xfailed / 1 xpassed`，scripted gate `18 passed`
- changed-lines coverage：`38/42 = 90.5%`，PASS
- repository architecture gate：PASS
- 七个 F153/F158 新增多参数函数已收敛为 typed request/options/context；
  `PLR0913` 没有新增豁免。

真实 OAuth 的 `apps/gateway/tests/e2e_live` 不属于确定性 lane。本轮一次错误地把它
纳入完整回归后遇到宿主 OAuth refresh token 已复用；该外部状态失败没有计作回归
通过，也没有通过重试掩盖。

### 真实模型就绪与认证失败终态

个人部署探针先暴露两项假绿：`octo doctor --live` 没有真实模型调用；OAuth refresh
在 HTTP 前抛出 `CredentialExpiredError` 后进入 Echo fallback，使 task 长时间停留在
`RUNNING`。F158 因此新增 FR-009，并取得以下确定性 RED：

- preflight credential expired/missing 错误进入 Echo：2 failed
- doctor live probe seam 缺失：2 failed
- Task 未写 `error_category=auth_error`：1 failed
- Worker 仍返回 `retryable=true`：1 failed

实现后，doctor 复用生产 config、credential store 与 `ProviderRouter` 做一次无 Echo
fallback 的受控调用；`CredentialError`、`AuthenticationError` 与 HTTP 401/403 由
单一分类 seam 统一为 auth-fatal。相同选择器结果为 `6 passed`，相关 fallback、
doctor、TaskService、WorkerRuntime 四文件回归为 `66 passed`。普通瞬时 Provider
故障仍保持既有 fallback 与 retry 语义。

这只证明代码与确定性合同闭环。2026-07-29 00:53 再次对当前个人部署执行
`~/.octoagent/bin/octo doctor --live`，命令在约两秒内 `exit=1`，表格明确返回
`model_live=FAIL`，日志为 `CredentialExpiredError`、HTTP 401
`refresh_token_reused`、`doctor_model_live_failed auth_failure=True`。没有出现 Echo
成功或整体 PASS。这证明部署字节已经 fail closed，但当前 OAuth profile 仍需用户
重新授权；真实对话与部署事件链复验继续保持 MISSING，不以 mock/probe 注入结果
替代。

同一次真实输出还暴露了离线 `credential_expiry` 把“未达到本地过期时间”描述为
“所有凭证均有效”的误导。T047 的两个单缺陷测试先稳定见红，随后把该检查改为只声明
本地 `expires_at` 事实，并明确“本地过期时间检查通过不代表远端授权可用；运行
`octo doctor --live` 验证”。Doctor 全文件回归 `33 passed`，F158 精确 authority
gate `1 passed`；当前/旧版方法 AST 均被独立哈希锚定，篡改提示语会 fail closed，
没有绕过 F150 保护面。`model_live` 继续是远端可用性的唯一权威判定。

提交门还发现暂存态 `HEAD` baseline 可能已经包含当前 F158 live probe，而旧 checker
只接受更早一版 probe 哈希。该路径现只接受“冻结旧版”或“冻结当前版”两种 exact
baseline；未知第三种仍 fail closed。最终
`architecture all --base-ref origin/master --scope-mode repository` PASS。

### iOS 当前可证明范围

- F153 Python/Gateway behavior：42/42 PASS
- generic iPhoneOS Release build：PASS
- Release executable SHA-256：
  `1d41c70fa031c770b833af451e9d7adb2d5f720318fcdf9ff91c68d5855147e2`
- source/release bundle secret scan：六类 0 命中
- iOS 26.5 / iPhone 17 Pro Simulator 完整 scheme：12/12 PASS
- Swift unit：9/9 PASS
- UI：3/3 PASS
- registration 六态 pixel baseline：6/6 PASS
- AXXXL Dynamic Type、accessibility tree、Reduce Motion：PASS

普通字号六张 baseline 位于
`octoagent/apps/ios/OctoAgentUITests/__Snapshots__/`；AXXXL 证据和完整运行说明位于
`../153-ios-device-trust-secure-transport/evidence/simulator/2026-07-28/`；当前提交复验
位于 `../153-ios-device-trust-secure-transport/evidence/simulator/2026-07-31/`。

同一推送提交在独立 detached worktree
`/tmp/f158-ios-clean-worktree.YDqxgp/repo` 中再次执行完整 scheme，结果仍为
`12/12 PASS`，运行后工作树 clean；这不是复用原工作树的 DerivedData 或未提交字节。

这些证据把 F153 registration/device-trust 的 Simulator 范围提升为
`PROVEN_IN_BRANCH`，仍不证明真机 Secure Enclave/Keychain 或 F156 完整 companion。

## 个人部署现状

- Gateway loopback origin：`200`
- `octo.maojiwang.work` DNS/Access：登录重定向正常
- Cloudflare named tunnel：`4` 条 active connection，request error=`0`
- 本轮没有修改 Cloudflare 账户、DNS、Access application、tunnel 或凭证
- 2026-07-31 只读复核：Gateway/cloudflared 均 running，当前 ingress 仍只有 Web
  hostname；独立 mobile hostname/exact Bypass 仍未配置

仓库正式 managed-checkout installer 已把 `~/.octoagent/app` 更新到包含 Web、F150、
F153 Simulator 与真实模型终态修复的运行提交
`35d7aa14f6f146a47084e4725e11df83cf034152`，完成依赖同步和 production build；
checkout clean。重启 Gateway 后 loopback `/ready?profile=core` 与 `/` 均为 `200`，
个人域名返回预期 Access `302`，tunnel LaunchAgent running。部署目录的 11 个 CSS
文件与当前分支 build 逐字节相等，canonical path→SHA map 为
`6330b1b01cebdddfef3a21507a7e2874c1ee63e518651f2a99bb0e0613230e00`。
构建工具会为 JS chunk 生成不同文件名，因此没有把两次 build 的完整 asset
目录误报为逐字节相同。

内置浏览器与 Chrome 均能枚举或打开 Access 登录页，但在 DOM 读取/交互阶段持续
超时；本轮没有读取浏览器 cookie 或 local storage 绕过认证。因此登录后的 SPA、
API、SSE、刷新、过期、重新认证、登出和 Settings remote-access 仍保持 MISSING。

2026-07-29 的后续复核确认远程地址仍稳定返回 Cloudflare Access `302`，不再是
`Bad Gateway`。本机实际 Web 页面和 Settings 已通过浏览器读取；Chrome 现有标签仍
停在 Access 登录页，没有可复用的已认证 OctoAgent 页面。该事实只把“本机产品入口”
保持为 PASS，不能把远程认证旅程提升为 PASS。

Gateway 日志同时显示 OpenAI Codex refresh token 已被复用并在刷新时返回 401。ready
只证明 provider route 配置存在，不证明真实模型对话可用；重新登录 provider 与真实
对话复验是独立未完成项。

### M10 常驻服务物理边界

- 当前 Mac boot time：`2026-07-20 10:43:48 +0800`
- LaunchAgent plist birth/modified：
  `2026-07-04 16:19:24 +0800` / `2026-07-05 20:27:22 +0800`
- `launchctl`：`com.octoagent.gateway` loaded/running，`runatload`，pid=`9258`
- `octo service status`：installed/loaded/running/ready 均为是
- loopback `/ready?profile=core`：`200`

plist 早于本次系统 boot，证明描述符当时已经存在；但当前进程在本轮部署中执行过
手工 `kickstart -k`，所以现状不能证明它就是登录后自动启动并一直存活的原进程。
`ATT-129-BOOT` 仍必须由一次用户明确允许的物理重启及重启后复核完成，不以文件时间、
当前 pid 或手工重启冒充。

## 未通过与外部阻断

### iOS 剩余产品与外部边界

- Xcode：`26.6 (17F113)`
- iOS runtime：`26.5 (23F77)`
- F153 Simulator registration：PASS
- Cloudflare mobile hostname/Bypass/live probe：MISSING
- connected iPhone：`0`
- F154 HealthKit：Research/Design/Tasks Gate 通过，production/E2E=0
- F155 EventKit：Research/产品决策档案已建立，A/B 决定未给出，production/E2E=0
- F156 companion：Research/Design/Tasks 草案、40 场景矩阵和 current mobile API
  recon 已建立；当前 5 条 mobile route 仅覆盖 F153，8 项产品合同 gap 未实现，
  Gate/production/E2E=0

### 真 iPhone

当前没有连接真 iPhone，因此 Secure Enclave、ThisDeviceOnly Keychain、注册/轮换/
撤销、Wi-Fi/蜂窝切换、前后台恢复与 Apple 权限场景均未验证。

### 后续 Feature

F154 HealthKit、F155 EventKit 与 F156 SwiftUI Companion 的 production 必须等待 F153
Cloudflare live/真机 Verify 通过，不允许用 Simulator registration、target build
或 Web E2E 替代。

## Gate 判定

| Gate | 结果 |
|---|---|
| Milestone/Blueprint/Feature delivery audit | PASS |
| Desktop Web functional E2E | PASS |
| Desktop Web visual E2E | PASS |
| Claude early-design lineage cleanup | PASS |
| F150 local product entry | PASS |
| deterministic backend regression | PASS |
| repository architecture gate | PASS（含当前 T047 exact overlay） |
| iPhoneOS target compilation | PASS |
| GitHub Actions frontend / architecture / benchmark / L1 Playwright | PASS（当前提交 `ebe8cd29`） |
| GitHub Actions backend deterministic | PASS（run `30378276329`） |
| personal deployment | PASS（运行提交 `35d7aa14`；登录后旅程仍缺） |
| authenticated personal SPA/API/SSE | MISSING |
| personal real-model conversation | BLOCKED（provider refresh token 401） |
| M10 physical boot attestation | MISSING（等待明确物理重启） |
| F153 iOS Simulator functional/visual E2E | PASS |
| F154-F156 complete iOS product E2E | MISSING |
| real-device security/lifecycle | BLOCKED |
| F154-F156 product implementation | CLOSED |
| current iOS commit / push | PASS（`35d7aa14`，后续测试提交 `ebe8cd29`） |
| current iOS clean-checkout scheme | PASS（12/12） |
| current complete CI | PASS（run `30378276329`） |
| mainline confirmation | MISSING |

因此当前不能把 F158 或跨 Milestone Goal 标记完成。
