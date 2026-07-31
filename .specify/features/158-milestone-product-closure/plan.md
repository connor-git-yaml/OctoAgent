# F158 实施计划

## Gate

- Research：通过
- Design：通过
- Tasks：通过
- Implement：进行中
- Verify：关闭

## 原则

1. 先证明当前状态，再修改代码或设计。
2. M11 Web/F150 缺口由 F158 收口；M12 安全能力继续由 F152-F156 分阶段拥有。
3. 真实启动、真实浏览器、真实模拟器/真机和视觉 diff 是交付证据；静态 marker 只能作辅证。
4. Claude Design 早期稿优先于通用 UI 建议。`ui-ux-pro-max` 的无障碍、焦点、
   动态内容语义与 SwiftUI 生命周期建议可以补充，但其“单栏极简”推荐与本产品早期三栏
   设计冲突，明确拒绝。

## 波次

### Phase 0：真值与基线

1. 固化 `origin/master`、Milestone、Blueprint、F149/F150/F151/F157 证据。
2. 渲染 Claude Design 导出与当前 Web，建立逐 frame 对照。
3. 盘点 Web route/state/action 与 iOS 规划能力。
4. 形成 requirement-evidence-gap-owner 矩阵并完成 Design/Tasks Gate。

Phase 0 Gate 于 2026-07-28 通过。通过只代表纠偏合同和执行顺序可实施，不代表
Milestone 已交付。

### Phase 1：F150 与 Web 产品可达性

1. 为 Settings remote-access composition 建立行为 RED。
2. 接入已有 adapter/view-model，不复制 transport/state。
3. 按早期 Claude Design 视觉语言实现 Settings 区块。
4. 补 F150 正式 verification report 和文档纠偏。

### Phase 2：Web 视觉恢复

1. 以早期主工作台 frame 重构 shell 的视觉 composition，保留现有功能合同。
2. 逐页恢复 F149 surface 的层级、留白、卡片节奏、排版和信息密度。
3. 建立确定性数据 fixture 与 Playwright visual projects。
4. 对关键 desktop/state/390 Web 窄窗口执行 screenshot diff。

### Phase 3：M12 原生 iOS 能力链

1. F152：隐私、身份与 ingestion contract。
2. F153：真实 iPhone transport/device-proof spike。
3. F154：HealthKit 垂直切片。Research/Design/Tasks Gate 已于 2026-07-29 通过，
   exact 范围收敛为 stepCount + sleepAnalysis、24h/3d/7d、本地聚合、当次批准、
   单次分析与删除；production 仍由 F153 T014/T015/T018 硬门关闭。
4. F155：EventKit OS full-access 决策门与只读实现。Research/决策档案已建立，
   当前等待用户在“接受 OS full access 但 Octo 代码物理只读”与
   “将 F155 移出 M12”之间明确选择；production=0。
5. F156：SwiftUI 对话、任务、审批、记忆、连接与通知体验。Research、
   Design/Tasks 草案、40 行启动/功能/视觉矩阵与 current mobile API recon 已建立；
   recon 确认 5 条 F153 mobile route 与 8 项产品 contract gap。上游 Gate 未齐，
   Design/Tasks/Implement 仍为 false。
6. 每个 Feature 独立 RED→GREEN→REFACTOR 与 verification report。

### Phase 4：双端运行验收

1. 从干净环境启动 Gateway/Web 与 iOS。
2. 按场景矩阵执行功能 E2E、异常态、认证、断网、恢复和撤销。
3. 执行 Web/iOS 视觉回归、无障碍与性能底线。
4. 真机专属能力只接受真机证据。
5. M10 常驻服务只接受一次明确物理重启后的登录自启动、`octo service status` 与
   `/ready` 联合证据；部署后的手工 `kickstart` 不能替代 `ATT-129-BOOT`。

### Phase 5：Claude Design 与最终交付

1. 在云端项目中删除或改回后期不佳设计。
2. 导出新的不可变设计制品与 frame/superseded manifest。
3. 同步 Blueprint、Milestone、Feature 状态与 verification reports。
4. 提交、推送、权威 CI、个人部署复验和 completion audit。

Phase 5 的设计子项 1–2 已于 2026-07-28 完成：云端必需 frames 已原位改回
`1a` 的紧凑工作台视觉，未发现可安全删除的重复 frame；最终导出与谱系/验收
manifest 已不可变回存。当前交付提交已推送、权威 CI 五个 job 全绿，个人
managed checkout 已更新并通过 loopback/Access 边界复验；登录后个人产品旅程、
iOS F153 Simulator 与 detached clean-checkout scheme 已通过；当前修复提交的权威
CI 五个 job 全绿。F154 Research/Design/Tasks Gate 已完成，但没有 HealthKit
production 字节；F155 已建立 Research/产品决策档案，F156 已建立 40 场景
Design/Tasks 草案与 API recon，但两者 production 均为 0。真机、F153 Verify、
F154-F156 Implement/Verify、主线合并与总
completion audit 仍未完成。

### Phase 6：真实模型运行真值

1. 用确定性 RED 证明 `octo doctor --live` 当前没有发起模型调用。
2. 用确定性 RED 证明 preemptive OAuth refresh 失败会被 Echo fallback 掩盖。
3. 复用唯一 ProviderRouter/config/credential store 实现 doctor live probe；不得创建
   第二 provider client 或健康状态 registry。
4. 将 `CredentialError` 与 401/403 统一归入 auth-fatal 边界，使任务进入
   `FAILED`、worker `retryable=false`，同时保持非认证故障的既有 fallback。
5. 用户完成一次重新授权后，执行真实 `doctor --live` 与真实模型任务，保存当前
   alias/provider/model、终态、事件链和无 Echo 证据。

2026-07-29 对当前个人部署再次执行真实 `octo doctor --live`，命令在
`CredentialExpiredError` / `refresh_token_reused` 上稳定非零退出，并明确记录
`auth_failure=True`；该结果证明 fail-closed 已进入部署，但不等于真实模型可用。

## 架构边界

- Web：现有 `api/client → platform query/action → pure projection → UI`。
- F150：单 FrontDoorGuard、单 remote-access projection、单 Settings consumer。
- iOS：SwiftUI + 单 native transport；设备信任与 Web browser session 分离。
- 双端共享产品语义、状态词表和视觉语言，不共享 UI 组件实现。
