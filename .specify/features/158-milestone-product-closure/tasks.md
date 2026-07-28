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
- [x] **T014 [L1]** Access 登录、过期、登出、恢复与 Settings 远程访问旅程
- [x] **T015 [VERIFY]** 生成 F150 正式 verification report 并纠正文档完成状态

## Phase 2：Web 早期 Claude Design 视觉恢复

- [x] **T016 [RED]** 主工作台 desktop visual baseline
- [x] **T017 [GREEN]** 恢复紧凑左栏、中央对话主舞台与右侧运行卡片
- [x] **T018 [REFACTOR]** 保持现有功能/state/transport，清理后期视觉补丁
- [x] **T019 [RED→GREEN→REFACTOR]** Approvals / Tasks / Task Detail
- [x] **T020 [RED→GREEN→REFACTOR]** Automation / Settings
- [x] **T021 [RED→GREEN→REFACTOR]** Agents / Memory / Files
- [x] **T022 [RED→GREEN→REFACTOR]** Skills / MCP
- [x] **T023 [L1]** 全 Web 用户场景与异常态 E2E（完整 suite 38 pass / 1
  once-only conditional skip；fresh approval fixture 另行 1/1 pass）
- [x] **T024 [VISUAL]** desktop 关键 frame 与状态 screenshot diff
- [x] **T025 [VISUAL/A11Y]** 390px Web 窄窗口、focus、reduced motion

## Phase 3：原生 iOS

- [x] **T026** 创建并通过 F152 Privacy/Identity/Ingestion Feature Gate；production
  Implement 仍等待 F152 T001 authority，F153 仍关闭
- [ ] **T027** 创建并通过 F153 真机 transport/device-proof Feature
  （T001-T013/T016/T017 已完成；Simulator registration 功能/视觉已通过，
  Cloudflare live/真机/Verify 仍阻断）
- [ ] **T028** 创建并通过 F154 HealthKit Feature
- [ ] **T029** 创建并通过 F155 EventKit 决策门与 Feature
- [ ] **T030** 创建并通过 F156 SwiftUI Native Companion Feature
- [ ] **T031 [SIMULATOR]** 原生 iOS 冷启动、导航、状态与视觉回归（F153 registration
  六态、a11y、AXXXL、Reduce Motion 与视觉基线已通过；F154-F156 完整产品仍缺）
- [ ] **T032 [DEVICE]** 注册、Keychain、轮换、撤销、断网恢复和 Apple 权限真机验收

## Phase 4：设计云端与交付

- [x] **T033** 在 Claude Design 标记、删除或改回后期不佳方案
- [x] **T034** 导出最终不可变 `.dc.html`、frame 与 superseded manifest
- [ ] **T035** 双端场景、功能、视觉、无障碍与安全 completion audit
- [ ] **T036** 同步 Blueprint、Milestone 与所有 Feature verification reports
- [ ] **T037** 干净检出、权威 CI、个人部署复验（上一批 Web 提交/CI/managed
  checkout 已通过；当前 iOS 提交/推送与 detached clean-checkout 12/12 已通过，
  run `30378276329` 五个 job 全绿；登录后 SPA/API/SSE 与真实模型对话仍缺）
- [ ] **T038** 提交、推送、合并并确认主线真实交付

## Phase 5：真实模型运行真值

- [x] **T039 [RED]** `doctor --live` 必须真实调用 ProviderRouter；认证失败必须 blocking
- [x] **T040 [RED]** preemptive OAuth/credential failure 不得进入 Echo fallback
- [x] **T041 [RED]** credential failure 必须把 Task/Worker 推进 FAILED 且不可重试
- [x] **T042 [GREEN]** 复用现有 config/router/store 实现 doctor live model probe
- [x] **T043 [GREEN]** 统一 credential/401/403 auth-fatal 分类与任务终态
- [x] **T044 [REFACTOR]** 收敛 auth 分类与 probe seams，不新增第二 provider 路径
- [ ] **T045 [LIVE VERIFY]** 重新授权后运行真实 doctor、真实对话与终态/事件链审计
