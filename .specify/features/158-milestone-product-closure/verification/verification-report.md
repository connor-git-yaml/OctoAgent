# F158 Milestone Product Closure Verification Report

## 当前结论

- 日期：2026-08-03；
- 状态：`PARTIAL`；
- `GATE_VERIFY=false`；
- 当前分支：`codex/f158-milestone-product-closure`；
- 当前已验证代码/测试提交：`4daf983f1c5893593d920f985ab671c2b7f4f1b1`；
- 当前个人部署代码提交：`d17c3e59879ee09dd79ba77fcebf9729e662730e`；
- 当前最终 F154 CI：push run `30782680229` 与 PR run `30782732897`，五个 job 均
  success。非主分支 resolver 精确选择 `origin/master` merge-base `db3214ff`；两个
  backend deterministic 均为
  `5760 passed / 14 skipped / 1 xfailed / 1 xpassed`，scripted `18 passed`，committed
  changed-lines 对完整分支为 `2192/2420 = 90.6% PASS`。LCOV SHA 为
  push `541e3daa34c4c005b48c7b8d23ce70eae2e4df529910fab3734439d5d58ab9e4`、PR
  `55d6b36018e49201a4ace022c2d937a2a4a59bbbccf2594af7e4febfd1790ac0`。

当前 Goal **没有完成**。桌面 Web、Claude 最早期设计视觉恢复、个人部署远程访问、
真实 OpenAI Codex 对话、F153 原生 iOS device-trust，以及 F154 真 iPhone 权限、真实
preview、一次获批分析和删除闭环已经形成可复核证据；F150 的真实会话自然过期也已
通过。F154 的物理锁屏→解锁生命周期、clean checkout、双 CI 与最终 Verify 也已通过；
仍缺 F155 EventKit production、F156 完整 Companion、最终 mainline 确认和一次用户
提前知情的 Mac 物理重启验收。

手机产品只走原生 iOS App。390px 只表示桌面 Web 窄窗口健壮性，不是手机浏览器产品；
Web 与 iOS 都以 Claude Design 最早期方案的层级、留白、卡片节奏、排版与视觉张力为
共同基线，不以现有旧 Web 样式反向改设计。

## 当前 Gate 总表

| 范围 | 当前判定 | 说明 |
|---|---|---|
| F149 Desktop Web 功能与视觉 | PASS | 完整功能/视觉 E2E 与 Claude 早期视觉基线已通过 |
| F150 个人远程访问 | PASS | 登录、登出、重登录、自然过期、Settings ready、真实对话/SSE/终态与一次性错误恢复通过 |
| F151 runtime architecture | PASS（历史留档受限） | 当前 clean-checkout repository architecture PASS，canonical index/report 已提交；历史 raw TDD archive 不自包含且禁止伪造 |
| F152 privacy authority | PASS | consent/lineage/zero-retention authority 已供 F153/F154 复用 |
| F153 device trust | PASS | `GATE_VERIFY=true`，真 iPhone security/lifecycle 与 Cloudflare live 已闭环 |
| F154 HealthKit read-only | PASS | T013 真机权限、24h/3d/7d preview、批准/分析/删除、物理锁屏生命周期与 T016 clean checkout/双 CI 全部通过，`GATE_VERIFY=true` |
| F155 EventKit read-only | READY | Design/Tasks PASS，F153/F154 Verify 已满足；从自身 T003 exact authority 开始 Implement，production 当前仍为 0 |
| F156 native Companion | CLOSED | recon 已纠正为 10 条 mobile route、5 个已签发 capability、8 个 product gap，production=0 |
| 当前 CI | PASS | push `30782680229` 与 PR `30782732897` 五个 job 全绿；累计 `origin/master` merge-base 范围 `2192/2420 = 90.6% PASS`，未降门槛或加豁免 |
| 当前个人部署 | PASS（已部署范围） | managed checkout 运行 `d17c3e59`，`/health=200` |
| M10 物理开机 attestation | MISSING | 只允许 Goal 末尾提前通知用户后重启一次 Mac |
| F158 Verify / mainline | MISSING | 仍有上述产品与外部边界 |

## 已通过的产品范围

### Desktop Web 与 Claude Design

- Vitest：`70 files / 599 passed`；
- production build：PASS；
- frontend complexity：PASS；
- Playwright：`39 passed / 0 failed / retries=0`；
- Claude 主工作台与业务 surface 视觉 baseline：14 个，未更新快照；
- 最终主工作台截图：
  `evidence/visual-baseline/2026-07-28/web-claude-restored-final-1440x900.png`；
- 截图 SHA-256：
  `9363b49495fa52bfd57fe52b4f2d8707c2c1e3b45a889ac25c44232df3acd9e8`；
- Claude Design 最终导出：
  `../149-web-pages-v2/design-output/2026-07-28/OctoAgent Web.dc.html`；
- 导出 SHA-256：
  `1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`；
- Spotify Design System、Figtree、radial gradient 残留：0。

后期大 Hero、径向发光、大圆角低密度卡片墙没有作为实现基线。实现保留 Claude 最早
版本的紧凑左栏、中央对话主舞台、右侧运行 rail、近黑层级与单荧光绿强调。

### 个人部署、Access 与真实模型

- Web hostname：个人部署 `octo.maojiwang.work`；
- iOS hostname：个人部署 `ios.maojiwang.work`；
- 手机 hostname 是每个部署可变的实例配置，不是产品硬编码；
- Cloudflare named tunnel 与 Web/mobile Host/path isolation 已通过；
- 真实 Chrome 已完成 Access 登录、主动登出、重登录与 Settings ready；
- 真实消息通过 SSE 进入运行态，OpenAI Codex 返回精确 `F158_WEB_E2E_OK`；
- Task 终态为 `SUCCEEDED`，浏览器 console warning/error 为 0；
- `octo doctor --live` 真实调用 `openai-codex / gpt-5.5` 并通过；
- 当前 managed checkout 运行提交 `d17c3e59879ee09dd79ba77fcebf9729e662730e`；
- 部署后 `/health=200`。

2026-08-03，同一既有 Chrome 标签在 reload 前仍为 OctoAgent；普通 reload 后由
Cloudflare Access 在进入 SPA/API/SSE 前送回登录页。没有读取 Cookie/localStorage/JWT，
没有清理浏览器状态、请求验证码或重新登录。这是经过真实时间后的自然过期，不是
fixture；与此前主动登出、重登录、Settings ready、真实对话/SSE 和一次性 Gateway 502
恢复共同闭合 F150。隐私安全记录见
`evidence/web/2026-08-03/access-session-natural-expiry.md`。

### F153 原生 iOS device trust

F153 当前 verification report 为 `PASS / GATE_VERIFY=true`。已通过：

- Protocol/Core/Gateway 当前 focused 回归；
- repository architecture authority；
- generic iPhoneOS Release build 与 source/bundle secret scan；
- Simulator 功能、Claude 早期视觉、Dynamic Type、VoiceOver、44pt 与 Reduce Motion；
- Cloudflare Web/mobile 正负 live matrix；
- 真 iPhone Secure Enclave、ThisDeviceOnly Keychain、owner approval；
- signed ready/profile、replay 拒绝、token expiry、key rotation；
- 4G、background、进程重启恢复；
- owner revoke 与整机重启后的 revoke persistence。

完整证据以
`../153-ios-device-trust-secure-transport/verification/verification-report.md` 为准。
F153 PASS 只解锁 F154，不会提前证明 HealthKit、EventKit 或 Companion。

### F154 最终 Verify

最终验证提交 `4daf983f1c5893593d920f985ab671c2b7f4f1b1` 的产品范围：

- F154 Gateway/Core/Policy/Protocol/authority：`25 passed / 0 failed`；
- F153 device-trust focused：`48 passed / 0 failed / 1 existing warning`；
- repository architecture gate：PASS；
- iOS 26.5 / iPhone 17 Pro Simulator 当前完整 scheme：
  `36 total / 27 passed / 9 live-only skipped / 0 failed`；
- F154 `HealthImportTests`：11/11 PASS；
- F154 `HealthImportFlowUITests` 非 live：3/3 PASS；lock-cycle live node 默认明确 skip；
- 12 个有限状态 Claude 早期视觉 baseline、Dynamic Type、VoiceOver、44pt：PASS；
- result bundle：
  `/tmp/f158-f154-current-simulator.j8ykha/F154CurrentSimulator.xcresult`；
- result byte-map aggregate：
  `9efd087edde28df1a8f4731b750e982c62cd07e5bd8a7ba41dc3832eb17248fb`。

Xcode 的 `DebuggerLLDB.DebuggerVersionStore.StoreError` 为同一成功 process 中的非致命
诊断；没有补跑，最终为 `TEST SUCCEEDED / failedTests=0`。九个 skip 全是显式真机
live-device 用例。

在同一真 iPhone 上又完成了：权限只由用户动作触发、step count/sleep analysis
read-only、真实 24 小时/3 天/7 天本地 preview，以及唯一一次经用户批准的 7 天摘要
review→analysis→delete。删除完成后服务端 review、approved packet、analysis result、
Memory candidate 均为零；首次超时遗留的两条 source chain 也通过产品删除 API 清理，只
保留 metadata audit 与 completed receipt。没有在仓库或报告中保存健康值、source hash、
设备标识或 screenshot。

真机有效上传 transaction、Simulator focused 21/25（4 个 live-only skip）、Release arm64
warnings-as-errors 与 repository architecture gate 均通过。preview-only 锁屏→解锁→前台
恢复也已在 2026-08-03 的独立 transaction 通过：`1 passed / 0 failed / 0 skipped`，
20.308 秒；xcresult 38 files / 362425 bytes，directory byte-map aggregate 为
`ac92cab0b27b00ce73cac0cadb8c3fa0f3004b96ef85d0359717bbbb626d37a3`。恢复后 preview
仍在，未自动提交/分析，随后只删除本地 preview 并回到空闲状态。完整边界见
`../154-healthkit-read-only-vertical-slice/verification/verification-report.md`，隐私安全的
真机明细见
`../154-healthkit-read-only-vertical-slice/evidence/real-device/2026-08-02/verification-report.md`。

2026-08-03 的首次 lock-cycle 尝试成功到达本地 preview 与锁屏提示，但设备 180 秒内
始终保持前台，故测试明确失败且不计证据；approve flag 缺失、screenshot 关闭，失败后
终止 App 清除 session-only preview，没有再次上传健康摘要。

该无效历史随后已由有效 transaction 取代。有效 transaction 的 approve flag 缺失、
screenshot capture 关闭，仓库只保留不含健康值、设备标识、凭证或截图的结构化
attestation。detached clean checkout 又完成 F154 Backend 24/24、repository architecture、
iOS scheme 27 passed + 9 live-only skipped 与 generic iPhoneOS Release PASS；push/PR 双 CI
均为五 job success。因此 F154 `GATE_VERIFY=true`，F155 已解锁。

## 当前架构与质量事实

- desktop Web 只有一个 Gateway application host 与唯一前端 api/client seam；
- mobile 只有原生 iOS client，不存在手机 WebView、第二 pairing/session/device；
- F153 与 F154 共用唯一 device-trust、signed transport、F152 privacy authority；
- F156 当前 canonical OpenAPI SHA：
  `6fd9925ce7ddaf969c9422d8ad42ef4917a97f89985d0c04c3d09dab3b9866b5`；
- mobile routes 共 10 条：F153 7 条、F154 3 条；
- 实际签发 capability 共 5 条；
- Companion 声明但尚未签发 capability 共 7 条；
- F156 product contract gaps 共 8 项；
- F155 authority scope SHA：
  `cd4cab6654ded23b860de4b641cd4a671c78359bcdd0e4f1f3ef7d49e70a6d0d`；
- F156 recon SHA：
  `62af2c5dca0ab90b5a513c88405d7547ee5ab862711e2a0c00ea7433b4d2e753`。

F155 只纠正 Gate 前置真值，没有 EventKit production/test 行为；F156 只纠正真实 API
recon 与 Gate 前置，没有聊天、任务、审批、Memory、通知 production。

## 当前未完成清单

1. F155：EventKit 只读 production、测试、Simulator/真机 Verify；
2. F156：原生 Companion 八项 product gap、SwiftUI 场景、功能/视觉 E2E；
3. mainline rebase/recon、最终全量回归、secret/architecture/visual inventory；
4. Goal 最后一次 Mac 物理重启与启动后 Gateway/tunnel/doctor/Web/iOS 复核。

第 4 项不会在用户使用手机期间执行，也不会未经提前通知重启 Mac。

## Verify 判定

当前已交付范围有真实、分层且可定位的证据，但 F155-F156 产品闭包、mainline 与物理
重启均未完成。因此：

```text
GATE_VERIFY=false
F158=PARTIAL
Goal=IN_PROGRESS
```
