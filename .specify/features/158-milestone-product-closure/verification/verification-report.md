# F158 Milestone Product Closure Verification Report

## 当前结论

- 日期：2026-08-02；
- 状态：`PARTIAL`；
- `GATE_VERIFY=false`；
- 当前分支：`codex/f158-milestone-product-closure`；
- 当前已验证代码/测试提交：`2f6fcbc91408e14bc3f5291678bdfd018cc24473`；
- 当前个人部署代码提交：`d17c3e59879ee09dd79ba77fcebf9729e662730e`；
- 当前代码/测试 CI：run `30717516171`，head `2f6fcbc91408e14bc3f5291678bdfd018cc24473`，
  五个 job 全部成功；backend=`5756 passed`、scripted=`18 passed`、frontend=`599
  passed`、Playwright=`39 passed`、benchmark=`2 passed`、architecture=PASS。同 push
  changed-lines 为 `0/0`，不能冒充完整范围；下载该 run 的 LCOV 后对上一失败 base
  `d031809f…` 在 detached clean clone 复算为 `549/608 = 90.3% PASS`。F158 T050 已用
  RED→GREEN 修复 branch base 遗忘问题，等待推送后的累计 workflow 复验。

当前 Goal **没有完成**。桌面 Web、Claude 最早期设计视觉恢复、个人部署远程访问、
真实 OpenAI Codex 对话、F153 原生 iOS device-trust 以及 F154 非真机实现已经形成
可复核证据；仍缺 F154 真 iPhone HealthKit 旅程、F155 EventKit production、F156
完整 Companion product、F150 自然过期边界、最终 mainline
确认和一次用户提前知情的 Mac 物理重启验收。

手机产品只走原生 iOS App。390px 只表示桌面 Web 窄窗口健壮性，不是手机浏览器产品；
Web 与 iOS 都以 Claude Design 最早期方案的层级、留白、卡片节奏、排版与视觉张力为
共同基线，不以现有旧 Web 样式反向改设计。

## 当前 Gate 总表

| 范围 | 当前判定 | 说明 |
|---|---|---|
| F149 Desktop Web 功能与视觉 | PASS | 完整功能/视觉 E2E 与 Claude 早期视觉基线已通过 |
| F150 个人远程访问 | PARTIAL | 登录、登出、重登录、Settings ready、真实对话/SSE/终态通过；自然过期仍待实时时间边界 |
| F151 runtime architecture | PASS（历史留档受限） | 当前 clean-checkout repository architecture PASS，canonical index/report 已提交；历史 raw TDD archive 不自包含且禁止伪造 |
| F152 privacy authority | PASS | consent/lineage/zero-retention authority 已供 F153/F154 复用 |
| F153 device trust | PASS | `GATE_VERIFY=true`，真 iPhone security/lifecycle 与 Cloudflare live 已闭环 |
| F154 HealthKit read-only | PARTIAL | T001-T012、T014、T015 完成；T013 真机与 T016 Verify 未完成 |
| F155 EventKit read-only | CLOSED | Design/Tasks truth 已纠正；等待 F154 Verify 后执行自身 T003 exact authority，production=0 |
| F156 native Companion | CLOSED | recon 已纠正为 10 条 mobile route、5 个已签发 capability、8 个 product gap，production=0 |
| 当前 CI | PASS（完整范围复算；workflow hardening 待复验） | run `30717516171` 五 job 全绿；同一 LCOV 对原生产 base 复算 `549/608 = 90.3%`，未降门槛；T050 累计 branch base 本地 5/5 PASS，待推送 CI |
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

F150 仍缺真实等待得到的会话自然过期证据。Hermetic Playwright 已覆盖过期与一次性
错误恢复合同，不能代替真实时间边界；因此 F150 在 F158 总验收中保持 `PARTIAL`。

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

### F154 当前非真机实现

当前已验证代码/测试提交 `2f6fcbc91408e14bc3f5291678bdfd018cc24473` 上：

- F154 Gateway/Core/Policy/Protocol/authority：`25 passed / 0 failed`；
- F153 device-trust focused：`48 passed / 0 failed / 1 existing warning`；
- repository architecture gate：PASS；
- iOS 26.5 / iPhone 17 Pro Simulator 完整 scheme：
  `33 total / 27 passed / 6 live-only skipped / 0 failed`；
- F154 `HealthImportTests`：10/10 PASS；
- F154 `HealthImportFlowUITests`：3/3 PASS；
- 12 个有限状态 Claude 早期视觉 baseline、Dynamic Type、VoiceOver、44pt：PASS；
- result bundle：
  `/tmp/f158-f154-current.2vvthi/Logs/Test/`
  `Test-OctoAgent-2026.08.02_04-18-09-+0800.xcresult`。

Xcode 的 `DebuggerLLDB.DebuggerVersionStore.StoreError` 为同一成功 process 中的非致命
诊断；没有补跑，最终为 `TEST SUCCEEDED / failedTests=0`。六个 skip 全是显式真机
live-device 用例。

这证明 F154 deterministic、Gateway、Simulator 功能/视觉/a11y，不证明真实 Apple
Health 权限 sheet、真实步数/睡眠、锁屏或网络切换。完整边界见
`../154-healthkit-read-only-vertical-slice/verification/verification-report.md`。

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

1. F154 T013：真 iPhone HealthKit permission、真实数据、本地 preview、canonical summary、
   Wi-Fi/蜂窝、锁屏、前后台、离线、revoke、delete 旅程；
2. F154 T016：累计 branch-base workflow 复验、evidence inventory 与 Verify；
3. F155：EventKit 只读 production、测试、Simulator/真机 Verify；
4. F156：原生 Companion 八项 product gap、SwiftUI 场景、功能/视觉 E2E；
5. F150：个人部署真实会话自然过期边界；
6. T050 累计 branch-base workflow CI 与后续 truth-only commit 边界；
7. mainline rebase/recon、最终全量回归、secret/architecture/visual inventory；
8. Goal 最后一次 Mac 物理重启与启动后 Gateway/tunnel/doctor/Web/iOS 复核。

第 8 项不会在用户使用手机期间执行，也不会未经提前通知重启 Mac。

## Verify 判定

当前已交付范围有真实、分层且可定位的证据，但 F154-F156 产品闭包、F151/F150 剩余
authority/live 边界、mainline 与物理重启均未完成。因此：

```text
GATE_VERIFY=false
F158=PARTIAL
Goal=IN_PROGRESS
```
