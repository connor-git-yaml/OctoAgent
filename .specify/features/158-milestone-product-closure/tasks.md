# F158 Tasks

> Phase 0 Design/Tasks Gate 已通过。Implement 依任务顺序执行；任何未取得当前直接
> 证据的条目保持 unchecked。

## Phase 0：真值审计与 Gate

- [x] **T001** 固化权威 `origin/master`、工作树与 Feature/Milestone inventory
- [x] **T002** 建立 F158 Spec、Plan、Tasks、Trace 与初始交付审计
- [x] **T003** 渲染 Claude Design 不可变导出并提取早期 frame 的布局/视觉基线
- [x] **T004** 启动当前 Web，逐 route/state 采集真实截图与功能基线
- [x] **T005** 完成 M0-M12 requirement-evidence-gap-owner 审计矩阵
- [x] **T006** 冻结 Web 功能 E2E、视觉 E2E 与可访问性测试矩阵
- [x] **T007** 冻结 iOS F152-F156 owner、模拟器/真机与视觉回归测试矩阵
- [x] **T008** 完成 F158 Design Gate
- [x] **T009** 完成 F158 Tasks Gate

## Phase 1：F150/Web 用户可达性

- [x] **T010 [RED]** Settings 必须渲染 F150 remote-access status/action/Advanced 的合同
- [x] **T011 [GREEN]** 复用现有 adapter/view-model 接入 Settings composition
- [x] **T012 [REFACTOR]** 收敛视觉、无障碍与状态职责，不增加第二 transport/state
- [x] **T013 [L3]** 真实 Gateway/API/Settings 联调
- [ ] **T014 [L1]** 在当前个人部署完成真实 Access 登录、过期、登出、恢复、
  SPA/API/SSE 与 Settings 远程访问旅程（真实登录、对话、SSE 运行态和
  `SUCCEEDED` 事件链已通过；主动登出、重新认证挑战、用户完成重新登录及部署后
  Settings“远程访问已就绪”同步已通过；真实上游 502、Gateway 恢复、同 URL
  重载及历史消息恢复已通过；仅会话自然过期仍缺）
- [x] **T015 [ARTIFACT]** 生成 F150 verification report 并按真实状态纠正文档；
  报告当前结论仍为 `PARTIAL`，不等于 T014 或 F158 Verify 已通过

## Phase 2：Web 早期 Claude Design 视觉恢复

- [x] **T016 [RED]** 主工作台 desktop visual baseline
- [x] **T017 [GREEN]** 恢复紧凑左栏、中央对话主舞台与右侧运行卡片
- [x] **T018 [REFACTOR]** 保持现有功能/state/transport，清理后期视觉补丁
- [x] **T019 [RED→GREEN→REFACTOR]** Approvals / Tasks / Task Detail
- [x] **T020 [RED→GREEN→REFACTOR]** Automation / Settings
- [x] **T021 [RED→GREEN→REFACTOR]** Agents / Memory / Files
- [x] **T022 [RED→GREEN→REFACTOR]** Skills / MCP
- [x] **T023 [L1]** 全 Web 用户场景与异常态 E2E（当前完整 suite 39/39 pass，
  0 failed / 0 retry）
- [x] **T024 [VISUAL]** desktop 关键 frame 与状态 screenshot diff
- [x] **T025 [VISUAL/A11Y]** 390px Web 窄窗口、focus、reduced motion

## Phase 3：原生 iOS

- [x] **T026** 创建并通过 F152 Privacy/Identity/Ingestion Feature Gate；production
  Implement 仍等待 F152 T001 authority，F153 仍关闭
- [x] **T027** 创建并通过 F153 真机 transport/device-proof Feature：Simulator、
  dedicated mobile edge、真 iPhone Secure Enclave/Keychain/rotation/revoke/4G/restart
  与最终 Verify 均已通过
- [ ] **T028** 完成 F154 HealthKit Feature（Research/Design/Tasks Gate 已通过；
  production T001-T012/T014/T015 已完成；当前 Simulator scheme 与非真机回归通过，
  仅 T013 真实 HealthKit 权限/数据/生命周期和 T016 最终 Verify 等待 iPhone 再次可用）
- [ ] **T029** 完成 F155 EventKit Feature（方案 A 已接受：系统 full access、Octo
  物理只读；Research/Design/Tasks Gate 已通过，F153 Verify 已满足，Implement/Verify
  仍等待 F154 Verify；随后必须在唯一 F151 checker 中完成 F155 自身 T003 exact
  paths/symbols authority）
- [ ] **T030** 完成 F156 SwiftUI Native Companion Feature（Research、Design/Tasks 草案、
  40 场景矩阵与 current API recon 已创建；2026-08-02 当前 mobile routes=10
  （F153=7、F154=3）、F156 产品 gaps=8，上游 Verify、F156 自身 T003 authority、
  production 与 E2E 未完成）
- [ ] **T031 [SIMULATOR]** 原生 iOS 冷启动、导航、状态与视觉回归（F153 registration
  六态、a11y、AXXXL、Reduce Motion 与视觉基线已通过；F154-F156 完整产品仍缺）
- [x] **T032 [DEVICE]** F153 注册、ThisDeviceOnly Keychain、轮换、撤销、4G/断网恢复
  与整机重启后的冷启动验收；F154-F156 新权限/产品场景仍分别由 T028-T031 负责

## Phase 4：设计云端与交付

- [x] **T033** 在 Claude Design 标记、删除或改回后期不佳方案
- [x] **T034** 导出最终不可变 `.dc.html`、frame 与 superseded manifest
- [ ] **T035** 双端场景、功能、视觉、无障碍与安全 completion audit
- [ ] **T036** 同步 Blueprint、Milestone 与所有 Feature verification reports
- [ ] **T037** 干净检出、权威 CI、个人部署复验（上一批 Web 提交/CI/managed
  checkout 已通过；F153 真机 Verify 与 F154 当前非真机完整 scheme 已通过；当前分支
  `1723c84f` 的累计 branch-base run `30720799929` 五个 job success，完整分支直接由
  workflow 计算为 `2192/2420 = 90.6% PASS`；T050 已修复 test-only push 可遗忘旧
  生产差异的 workflow 盲点，并完成 Node 24 Actions 迁移复验。个人 managed checkout 已部署
  device-trust 重连修复 `d17c3e59`，`/health=200`；登录后真实对话/SSE/事件链、Access
  登出/重登录/Settings ready 与一次性错误恢复已通过，仍缺自然过期、F154-F156 产品
  闭包、当前 truth commit 的 CI、mainline 与最终物理重启复验）
- [ ] **T038** 提交、推送、合并并确认主线真实交付

## Phase 5：真实模型运行真值

- [x] **T039 [RED]** `doctor --live` 必须真实调用 ProviderRouter；认证失败必须 blocking
- [x] **T040 [RED]** preemptive OAuth/credential failure 不得进入 Echo fallback
- [x] **T041 [RED]** credential failure 必须把 Task/Worker 推进 FAILED 且不可重试
- [x] **T042 [GREEN]** 复用现有 config/router/store 实现 doctor live model probe
- [x] **T043 [GREEN]** 统一 credential/401/403 auth-fatal 分类与任务终态
- [x] **T044 [REFACTOR]** 收敛 auth 分类与 probe seams，不新增第二 provider 路径
- [x] **T045 [LIVE VERIFY]** 重新授权后运行真实 doctor、真实对话与终态/事件链审计：
  `model_live=PASS`（OpenAI Codex / `gpt-5.5`），Web 返回
  `F158_WEB_E2E_OK`，Task `01KYY3E1Q3GVEQYPB2HRCE3F88` 终态`SUCCEEDED`

## Phase 6：物理 Milestone 验收

- [ ] **T046 [PHYSICAL VERIFY]** 在用户明确确认可重启后执行一次真实 Mac 重启；登录后
  以 `launchctl`、`octo service status` 与 `/ready` 共同完成
  `ATT-129-BOOT`，禁止用本轮部署后的手工 `kickstart` 冒充开机自启动
- [x] **T047 [UX/ARCHITECTURE]** 在不绕过 F150/F158 authority 的前提下，把
  `credential_expiry` 的本地时间戳语义与 `doctor --live` 远端可用性语义明确区分，
  避免同一报告出现“所有凭证均有效”与 `model_live=FAIL`；Doctor 回归 `33 passed`，
  F158 精确 authority gate `1 passed`，repository architecture gate PASS

## Phase 7：运行时可靠性收口

- [x] **T048 [MEMORY HARDENING]** 修复内建 `memu` 对当前 SoR、tombstone 与
  `MemorySearchHit` 合同的漂移，并保证高级 backend 的候选仍经过 canonical recall
  hooks。相关回归 `288 passed`、repository architecture gate PASS；个人实例真实
  `memory.sync.resume` 以 `memu` 重放 70 个积压批次后剩余 0，新聊天任务
  `01KYZF2X3XWT6NN27TJSFJNZ3Y` 成功且后台同步后积压仍为 0
- [x] **T049 [DEVICE TRUST HARDENING]** 修复同一 Secure Enclave public key 重新连接
  时的 SQLite unique constraint：相同 owner/current key 复用原 device id，跨 owner、
  revoked/non-current key typed 409 且零半写入。F153 回归 `48 passed`、repository
  architecture gate PASS，提交 `d17c3e59` 已部署并恢复 `/health=200`
- [x] **T050 [CI HARDENING]** 用现有 F151 wiring contract 取得 RED，并让非主分支
  architecture/changed-lines coverage 统一累计比较 `merge-base(origin/master, HEAD)`；
  Pull Request 保持目标 base，`master` push 保持 `event.before`，防止失败生产提交被后续
  test/docs-only push 遗忘。必须以当前完整 LCOV 对原失败 base 的 `549/608 = 90.3%`
  复算和新 workflow CI 共同验证，禁止降 90% 门槛或增加豁免。首轮累计 run
  `30718445846` 已证明 base resolver 生效，并以 `1917/2484 = 77.2%` 诚实失败；随后
  找到 Provider pytest11 插件在 coverage 启动前 eager import 生产包的测量缺陷。插件
  已移至轻量 `octoagent.provider_pytest_plugin` 并延迟 gate import，fresh interpreter
  合同与 13 条隐私模型失败关闭分支已通过。提交 `2b1fb5ec` 的累计 run
  `30719719793` 五个 job 全部成功：backend `5759 passed`、scripted `18 passed`，
  committed branch-base 覆盖 `2192/2420 = 90.6% PASS`；未降门槛、未加豁免。
  后续 Node 24 Actions head `1723c84f` 的 run `30720799929` 再次五个 job 全绿：
  backend `5760 passed`、scripted `18 passed`，累计覆盖仍为 `90.6% PASS`，Node 20
  deprecation annotation=0。
