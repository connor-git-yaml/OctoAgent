# F158 Milestone 交付审计

## 审计基线

- 审计日期：2026-07-31
- `origin/master`：`db3214fff722c6f969baf99528a76fc03a1e21a1`
- F149 最终 design export：
  `1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`
- F149 初始审计 export（历史对照）：
  `a2db08ea0eb39278558e61e87a355b042c98ad24273b201940925f310271d98a`
- Claude Design project：`851e3fb2-2b5b-4251-a095-8a678b1b7fec`

## 初始结论

| 要求 | 当前证据 | 判定 | 缺口 | owner |
|---|---|---|---|---|
| M11 Web 视觉保持早期 Claude Design | 早期 frame、恢复后 1440 截图、14 个 pixel snapshot、逐 surface 结构合同、云端最终导出、当前个人部署 CSS | PROVEN_BRANCH_CI_DEPLOYED | 当前运行提交已部署；登录后旅程与总 completion audit 仍缺 | F158 / F149 |
| F150 远程访问 Settings 用户可达 | production consumer、样式、L4/L1、真实截图与当前个人部署 | PROVEN_BRANCH_CI_DEPLOYED | Access 前边界通过；登录后 Settings 仍受浏览器接管超时阻断 | F158 / F150 |
| F150 正式验证报告 | `verification/verification-report.md`、个人部署 connector recovery、浏览器接管诊断与 2026-07-31 只读复验 | PARTIAL | Gateway/cloudflared running、loopback health 200、Access 302、Settings projection `pending_verification`；Chrome/扩展/native host 均健康但受控 DOM 超时，需用户允许新 profile 并完成登录 | F158 / F150 |
| Web 功能 E2E | 11 个 Playwright spec / 39 nodes | PROVEN_BRANCH_CI_DEPLOYED | 2026-07-31 当前分支完整 `39/39`、retries=0；当前 runtime 已部署 | F158 |
| Web 视觉 E2E | geometry/computed-style + 14 个 pixel snapshots | PROVEN_BRANCH_CI_DEPLOYED | 主框架与 9 surface + 真实任务详情进入像素门；部署 CSS map 与分支一致 | F158 |
| 390px Web 健壮性 | 10 surface 参数化 Playwright + F150 narrow journey | PROVEN_IN_BRANCH | overflow/focus/a11y/reduced motion 通过；不是手机产品 | F158 |
| 原生 iOS 可启动 | iOS 26.5 / iPhone 17 Pro Simulator 完整 scheme 12/12 + detached clean-checkout 12/12 | PROVEN_PUSHED（F153 scope） | F153 T001-T013/T016/T017 registration 已真实启动并复验；F154 Research/Design/Tasks Gate 已通过但 production=0；F155 只完成 Research/决策档案；F156 只完成草案、40 场景矩阵与 API recon；完整 companion 与真机仍缺 | F153-F156 |
| iOS 功能 E2E | Swift unit 9/9、Simulator UI 3/3、六态冷启动；F153 live 写前 focused 39 pass | PARTIAL | F153 registration 已证，mobile live 写入/回滚矩阵已冻结但未执行；F154 exact HealthKit 场景与测试矩阵已冻结但未执行；F155/F156 已有设计合同但行为 E2E=0；真机及 F154-F156 行为仍缺 | F153-F156 |
| iOS 视觉回归 | 六状态 pixel baseline、AXXXL 截图、a11y/Reduce Motion | PARTIAL | F153 registration 视觉已证；F154 已冻结 Claude early + SwiftUI native visual contract，但完整 companion/HealthKit/EventKit/真机视觉仍缺 | F153-F156 / F158 |
| Claude Design 后期不佳方案已清理 | 2026-07-28 云端写回、不可变导出、谱系与结构/资产机械核验 | PROVEN_BRANCH_CI_DEPLOYED | 最终总 completion audit 尚未完成 | F158 |
| F151 当前 runtime/architecture | 当前 `architecture all`、verification report metadata、CI architecture/backend | PROVEN_CURRENT | 当前提交相对当前主线 architecture all exit0；产品架构合同可继续作为当前交付依据 | F151 / F158 |
| F151 历史 TDD evidence 可复验性 | canonical v2 269-record hash chain、干净检出 verify | CONTRADICTED | `origin/master` 已前移；immutable base 下又因 ignored `evidence/local` 缺失而失败，旧 raw 未找到；不得伪造或用当前 GREEN 重跑替代 | F151 / F158 |
| F157 CI 回归修复 | `master=db3214ff`；CI run `30198514576` 五个 job 全绿；T001-T013 全完成 | PROVEN | 无 | F157 / F158 |
| M10 物理启动验收 | plist 早于当前 boot；launchd/service/ready 当前均健康 | INCOMPLETE | 本轮部署曾手工 kickstart，仍缺一次明确物理重启后的 ATT-129-BOOT | 原 owner / F158 audit |

## M0-M12 全量真值矩阵

| Milestone | Blueprint 声明 | 当前复核 | F158 判定 | 进入最终闭环前要求 |
|---|---|---|---|---|
| M0 | 完成 | Task/Event/Artifact/SSE/ready 代码与历史 Feature 仍在 | PROVISIONAL | 干净检出核心回归 + Web 主链复验 |
| M1 | 完成 | 当前 ProviderRouter/ToolBroker/Policy 已取代部分历史 LiteLLM 路径 | PROVISIONAL | 以当前架构运行模型/工具/审批/secret 回归，禁止用退役 Proxy 证据 |
| M1.5 | 完成 | Orchestrator/Worker/Checkpoint/Watchdog 当前实现存在 | PROVISIONAL | 当前 runtime 下恢复、幂等、watchdog 与 trace 回归 |
| M2 | 完成 | Telegram/A2A/Memory/backup 等 owner 存在；历史 Docker backend 已明确不交付 | PROVISIONAL | 以当前边界复验 channel/action/memory/restore；不恢复 Docker 叙述 |
| M3 | 完成 | control plane、projects、memory、setup 与工作台代码存在 | PROVISIONAL | 用户场景与 current contract 回归；清理文档中的历史兼容措辞 |
| M4 | 完成 | guided workbench、setup governance、runtime safety、supervisor owner 存在 | PROVISIONAL | 当前 UI/API/决策环组合回归 |
| M5 | 完成 | F084-F103 架构/Agent 对等系列有 completion 记录 | PROVISIONAL | F151 架构门 + 当前 deterministic 全量回归 |
| M6 | 完成 | F104-F122 surface/地基代码与完成记录存在 | PROVISIONAL | 当前全量门与相关 surface E2E |
| M7 | 完成 | memory/learning owner 与 defer 条件有记录 | PROVISIONAL | 当前 memory/learning 行为和 defer 条件复验 |
| M8 | 功能完成 | service/Telegram/cron/voice owner 存在 | PROVISIONAL | 常驻服务与个人部署复验 |
| M9 | 完成 | 四层测试门、F151/F157 corrective 与 exact master CI 已闭环 | PROVEN | 最终总审计继续复用 run `30198514576` 与当前 F158 branch CI |
| M10 | 功能完成 | F145/F134/F146/F147 主线存在 | INCOMPLETE | ATT-129-BOOT 物理重启 attestation |
| M11 | 完成 | Web 可启动；F150 Settings 可达；主框架与 10 个业务 surface（含任务详情）视觉及功能 E2E 通过；Claude 云端谱系已清理并导出；当前 runtime 已部署 | PROVEN_BRANCH_CI_DEPLOYED | 登录后个人旅程与最终 completion audit |
| M12 | In Progress | F152 Verify；F153 T001-T013/T016/T017 已实现，Simulator registration 功能/视觉通过；mobile live preflight 与 39 个 focused origin 合同通过但未执行外部写入；F154 Research/Design/Tasks Gate 已通过；F155 Research/决策档案和 F156 40 场景 Design/Tasks 草案/API recon 已建立 | PARTIAL | Cloudflare mobile live、真机、F153 Verify、F155 A/B 决定、F154-F156 Implement/Verify 与完整 iOS E2E |

该矩阵的 `PROVISIONAL` 不是重新否定历史交付，而是区分“历史报告存在”与“当前
Milestone Goal 已在同一 commit/环境复验”。最终 completion audit 只允许将取得当前
直接证据的行提升为 `PROVEN`。

## 2026-07-28 直接视觉对照

本轮从同一 `origin/master=db3214ff` 工作树实际完成前端 production build，并以
hermetic L1 Gateway 启动当前 Web；同时直接渲染 F149 不可变设计导出中的
`#1a 对话工作台（主视图）`。两份浏览器截图保存在：

- `evidence/visual-baseline/2026-07-28/claude-design-early-1a-browser.jpg`
  - SHA-256：
    `7262ef63a1fea81caf97371071527ba22f843f5f01efeb8b1821179aefb01cbf`
- `evidence/visual-baseline/2026-07-28/current-web-browser.jpg`
  - SHA-256：
    `f9f77e9d8ff75cbcc38688b49c0561baa65c4b7266192f5d4e21c0b5b72a149a`

当前 Web 可真实启动，但主工作台视觉与早期基线存在产品级差异：

1. 左栏被放大的品牌卡和稀疏导航占据，早期稿的项目/会话密度、选中态和运行提示弱化。
2. 中栏由真实对话、委派卡、工件卡与运行提示组成的主舞台，退化为大面积空状态和大输入框。
3. 早期稿蓝黑标题层、实时快照、全局任务状态胶囊与消息层级未保留。
4. 右栏早期稿的任务进度、事件流、工作文件三层卡片，在当前空态中缺少相同的信息架构与视觉节奏。
5. 当前使用大圆角、大留白、低密度卡片；早期稿为更紧凑的工作台密度。该差异不能由
   “深色 + 绿色强调 + 三栏”视为等价。

初始截图因此将 M11 视觉交付判为 `CONTRADICTED`。F158 随后完成主工作台第一轮
恢复：左栏收敛为 256px 的项目/会话与产品导航，中央恢复蓝黑标题层、紧凑消息层与
底部胶囊输入，右栏恢复连续运行状态 rail。当前真实浏览器证据：

- `evidence/visual-baseline/2026-07-28/web-claude-restored-final-1440x900.png`
  - SHA-256：
    `9363b49495fa52bfd57fe52b4f2d8707c2c1e3b45a889ac25c44232df3acd9e8`
- `evidence/visual-baseline/2026-07-28/web-claude-restored-final-1124x900.jpg`
  - SHA-256：
    `0b1f6b59c44b748d2276f7e88d51a3d4378d40e83d486094c7d13803c00fe4c2`

Playwright 新增固定 1440×900/dark 的结构合同与四个真实 pixel snapshot：

- sidebar brand：
  `24c36c726256ddc1fedd8f35e14391a5596b1852774fc1af424465491acec8bc`
- navigation row：
  `4bfc0ffdb0a9fd3d9889ca05f152f6a9fec12bfa4e6d4be50f1b965985c56679`
- composer：
  `382a1b13f2de7fbdf3858a7a901a99fbb453252627e41a0f1817443c11cae2cb`
- run-panel head：
  `cc510fc04ab9a731a92fa47afa8cc47ac6b4d3d584b17e4de26ab2bb5c88ff24`

视觉合同先因 snapshot 不存在真实失败，建立经人工查看的基线后同一 selector 通过。
本轮继续补齐 9 个业务 surface 与真实任务详情 pixel baselines，并在当前字节下完成
完整 Browser L1、390px Web robustness 和 deterministic L4 state matrix。M11 因此从
`CONTRADICTED` 提升为 `PROVEN_IN_BRANCH`。Claude Design 云端 lineage 随后完成：
最终导出 SHA
`1d497d8cc4e8a06e9f2bff296784d4648e0bb0784a73c8fe4f8a7bd9812132f7`，
`4a`–`4o`、20 行验收表与 10×7 状态矩阵机械闭合，后期 radial glow、大 Hero 与
卡片墙原位改回早期视觉。当前运行提交已进入个人部署，11 个 CSS 文件的
path→SHA map 与分支 build 逐字节一致；登录后 Access 旅程和总 completion audit
完成前仍不能提升为最终 `PROVEN`。

## 机械事实

1. `SettingsCenter` 现在将唯一 `RemoteAccessSettings` 作为 `SettingsPage`
   composition slot 传入；adapter/view-model 没有复制。
2. `.remote-access-settings*` 与早期视觉语言样式位于独立 production stylesheet。
3. `octoagent/frontend/e2e/visual-claude-baseline.spec.ts` 已使用
   `toHaveScreenshot`，并同时验证三栏 geometry 与计算后颜色/边框。
4. 仓库已存在唯一 `octoagent/apps/ios/OctoAgent.xcodeproj`、6 个 production Swift
   files 与 1 个 XCTest file；原生 App/XCTest target 在 iPhoneOS arm64 编译链接成功。
5. F153 canonical root 是
   `.specify/features/153-ios-device-trust-secure-transport/`；不存在第二
   `153-native-ios-remote-access` Feature。
6. F149 `review/design-fidelity.md` 以人工层级描述、无 overflow、单 `<main>` 和
   390px 合同判 PASS，不能证明视觉一致。
7. `ui-ux-pro-max` 的通用搜索建议“单栏极简”，与用户指定早期三栏工作台冲突；
   本 Feature 明确拒绝其布局建议，只采用可访问性、焦点、动态内容语义与 SwiftUI
   生命周期建议。
8. `npm ci` 与 `npm run build` 在 F158 工作树成功；真实 Gateway 以 loopback L1
   fixture 启动在 `127.0.0.1:8151`，当前主工作台可在浏览器渲染。
9. 初始 Web Playwright 的 `f149-a-wave.spec.ts` 曾因把追加的
   `TASK_DRIFT_DETECTED` 错算进固定前缀而失败；修正为“前两条固定 + 追加 drift”
   后，早期全量结果为 `19 passed / 1 conditional skip / 0 failed / retries=0`。
10. 全前端单元结果为 `70 files / 598 tests passed`；production build 与 frontend
    complexity gate 均通过。该结果只证明单元/构建边界；逐 surface/state 的完成证据
    由下面第 13、14 项的 Playwright 与像素基线单独提供。
11. F153 focused Protocol/Core/Gateway/authority 回归为 `42 passed`；generic
    iPhoneOS Release build 通过。Release `.app` 恰含 3 个文件，executable
    SHA-256 为
    `1d41c70fa031c770b833af451e9d7adb2d5f720318fcdf9ff91c68d5855147e2`；
    source/bundle 对个人域名、邮箱、Cloudflare secret/Web Cookie、private key 与真实
    device token 扫描为 0。
12. 官方 iOS 26.5 runtime 已安装，唯一 iPhone 17 Pro Simulator 完整 scheme 为
    `12 passed`（Swift unit 9/9、UI 3/3）。六个 registration 状态视觉先取得
    baseline-missing RED，经人工复审后同一 selector 6/6 通过；AXXXL Dynamic Type
    与 Reduce Motion setting=1 下的 accessibility UI 复验均通过。
13. 2026-07-31 当前字节结果为 production build PASS、complexity PASS、Vitest
    `70 files / 598 passed`、Playwright `39 passed / retries=0`；没有执行 snapshot
    update。逐命令与当前基线 SHA 见
    `evidence/web/2026-07-31/verification-report.md`。
14. `visual-claude-surfaces.spec.ts` 的 10 个 PNG 经 generation 与 no-update 两轮
    各 `10 passed`；动态时间使用 test-only visibility 归一化，最终基线不存在
    Playwright 默认洋红 mask。完整 SHA 与场景矩阵见
    `evidence/web/2026-07-28/verification-report.md`。
15. 2026-07-31 F153 当前分支完整 scheme 再次为
    `12 passed / 0 failed / 0 skipped`，其中 unit 9、UI 3；六个 registration 状态
    实际启动并通过 committed Claude 早期像素基线，未更新 baseline。持久化摘要见
    `../153-ios-device-trust-secure-transport/evidence/simulator/2026-07-31/verification-report.md`。
16. 当前真值提交 `dc8b1b417fa0cb79a90c1aa290a2dc44e11fcad4` 的权威
    GitHub Actions run `30602259193` 五个 job 全绿；backend 为
    `5715 passed / 14 skipped / 1 xfailed / 1 xpassed`，scripted lane `18 passed`，
    L1 Playwright `39 passed`。同一提交已通过正式 installer 进入个人 managed
    checkout，部署后 ready/health/root 均为 `200`，公网为 Access `302`，F150
    projection 为 `pending_verification`。这些事实证明当前代码、CI 与部署身份一致，
    不证明 Access 登录后旅程、真实模型、mobile live 或真机。
17. F153 外部写入前只读预检确认现有 tunnel 4 条 connection、仅 Web ingress、
    mobile DNS/config 均未建立；执行与回滚清单位于
    `../153-ios-device-trust-secure-transport/verification/live-external-preflight.md`。
    当前 device-trust/mobile focused selector 为 `39 passed / 1 existing warning`，
    但没有任何 Cloudflare、DNS、Access、实例或服务写入。
18. F150 浏览器接管前机械诊断确认 Chrome、扩展与 native host 均健康；受控导航/DOM
    仍超时。当前 Provider 正式恢复入口是
    `~/.octoagent/bin/octo setup --provider openai-codex`，成功路径会自动执行
    `octo doctor --live`。两项均仍需要用户参与，未被静态诊断提升为产品 PASS。
19. 当前提交再次运行 `check-runtime-architecture.py all --base-ref origin/master
    --scope-mode repository` 为 exit0；但 F151 canonical evidence 的 committed verify
    使用当前 `origin/master` 时以 `EVIDENCE_BASE_REF_INVALID` 失败，使用 immutable
    `9d5e1e48…` 时又以 `EVIDENCE_ARTIFACT_MISSING` 失败。独立重算 269 条 record hash
    与 chain 为 0 错误，说明 index metadata 完整、raw archive 不自包含。完整记录见
    `evidence/f151/2026-07-31/verification-report.md`。
20. `docs/blueprint/deployment-and-ops.md` 与
    `docs/codebase-architecture/remote-access.md` 已从“等待真机 spike 选择 edge
    方案”同步为 F153 已选架构：同一 named tunnel/loopback、部署专属 mobile
    hostname、仅 `/api/mobile/v1/*` 的 path-specific Bypass 与 origin P-256 device
    proof；同时继续明确 Cloudflare live/真机未完成，未把设计选择冒充部署事实。
21. 同一部署蓝图的 active 运维段已移除退役 LiteLLM readiness/fallback、物理
    kernel/worker pool 与 Caddy/Docker 公网入口叙述：当前 `/ready` 只做
    sqlite/artifacts/disk/ProviderRoute 本地结构检查，真实联网由
    `octo doctor --live`/真实任务验证；runtime 与 Watchdog 均位于单 Gateway host，
    公网只走 named tunnel。修改后 repository `architecture all` 继续 exit0。
22. Blueprint 索引与 Milestone 总表已把 active Provider Plane、模块职责、API
    边界、部署入口与安全风险同步为当前实现：ProviderRouter direct、单 Gateway
    application host、message-native A2A、OS user service + named tunnel，且不再把
    LiteLLM、物理 Kernel/Worker 或 Docker sandbox 当作当前保证。F151 行区分“当前
    architecture proven”与“historical raw archive 不自包含”；F150 行区分“产品
    代码 stable”与“当前个人实例 verification pending”。M12 原生 edge 也已由
    “等待三选一 spike”改为已选 path-specific mobile bypass + origin device proof，
    同时保留 live/真机未完成事实。
23. Blueprint 子文档与实现级架构导览进一步按当前源码逐项复核：AgentRuntime、
    AgentSession、MemoryNamespace、RecallFrame 与 A2AConversation 示例已对齐当前
    Pydantic 字段；Gateway 根装配改为 `main.py → OctoHarness → ProviderRouter`；
    Orchestrator 方法名、进程内 RuntimeBackend、User Plugin Loader 与 observability
    物理位置均改为当前事实。历史 docker-compose 继续仅作审计背景，不再写成等待用户
    手工同步的部署模板。
24. 部署蓝图遗留的“容器升级、Docker 日志驱动”也已从 active 运维步骤降为历史；
    当前升级明确走同一 `UpdateService` 的
    `PREFLIGHT → MIGRATE → RESTART → VERIFY` durable attempt，重启走
    launchd/systemd user service，当前日志事实以进程内 RotatingFileHandler 与
    service fd 重定向为准。

## F153 当前设计映射与偏离记录

- 保留 Claude 初稿的近黑底、低亮度紧凑卡片、细边框、单一绿色强调、紧凑层级与
  信息密度；没有引入后期大面积空白、泛化大圆角或第二主题。
- iPhone 不复制 Web 三栏，因为这会破坏原生小屏导航与可访问性；改用
  `NavigationStack`、单列卡片、52pt action、Dynamic Type、VoiceOver 与
  Reduce Motion。这是平台/可用性偏离，不是为了迁就现有 Web 实现。
- F153 只交付设备连接状态，不提前实现 F156 的聊天/任务/审批/Memory UI；因此当前
  App 不能作为最终 companion 视觉完成证据。
- F153 registration 范围已有 source、真实 Simulator、六张 baseline 与 AXXXL 截图，
  可提升为该范围的 `PROVEN_IN_BRANCH`；由于 F156 完整 companion 与真机截图仍缺，
  整体 iOS 视觉还原继续保持 `PARTIAL`。

## M10 物理自启动直接审计

2026-07-29 的只读审计确认：

- 当前系统自 `2026-07-20 10:43:48 +0800` 启动；
- `~/Library/LaunchAgents/com.octoagent.gateway.plist` 的 birth/modified 分别为
  `2026-07-04 16:19:24 +0800` 与 `2026-07-05 20:27:22 +0800`；
- launchd 报告 `runatload`、loaded/running、pid `9258`，`octo service status`
  报告 installed/loaded/running/ready 均通过，loopback ready 为 `200`。

这些事实证明服务描述符在当前 boot 前已安装、当前服务健康，但本轮产品部署执行过
手工 `kickstart -k`。因此无法从当前 pid 反推“登录时自动启动成功”，也不能签署
`ATT-129-BOOT`。唯一诚实闭环是用户允许一次物理重启，登录后立即复核 launchd、
service status 与 ready；该动作具有中断性，F158 不会自行执行。

## 证据等级

- `PROVEN`：当前字节、真实运行与对应范围的可复现测试共同证明。
- `PROVISIONAL`：有直接证据，但未在 F158 最终环境复验。
- `PARTIAL`：只覆盖要求的一部分。
- `INCOMPLETE`：实现或证据明确缺失。
- `CONTRADICTED`：真实状态与完成声明冲突。
- `MISSING`：没有可用证据。
- `UNPROVEN`：只有说明或间接证据。

任何非 `PROVEN` 项都不能进入最终完成声明。
