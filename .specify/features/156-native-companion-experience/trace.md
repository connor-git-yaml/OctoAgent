# F156 Trace

- 2026-07-29：F158 复核确认当前 iOS App 只交付 F153 registration；完整 companion
  conversation/tasks/inbox/settings 与 APNs 均未实施。
- 2026-07-29：Apple 官方调研确认原生 TabView/NavigationStack、Dynamic Type/
  VoiceOver/Reduce Motion、contextual notification permission、APNs provider/device
  token 边界和 background scheduling 非确定性。
- 2026-07-29：形成 four-tab product structure、typed deep-link、APNs body-negative、
  background-negative 与 40-row function/visual/device matrix。
- 2026-07-29：Research PASS；Design/Tasks draft complete，但 F153/F154/F155 与 API
  recon 未齐，Gate 保持 false，production/test behavior=0。
- 2026-07-31：完成 current Gateway/OpenAPI 只读 recon。OpenAPI canonical SHA 为
  `21e052dda301de65dea1bb192ade88039320db9460b329eeda3b78858dfdd7e3`；mobile edge
  exact 只有 5 条 F153 enrollment/token/ready/profile route，Chat/Task/Approval/Memory/
  APNs 共 8 项产品 contract gap。T002 完成，但 F153/F154/F155 上游未齐，Design/Tasks
  Gate 继续 false，production/test behavior 仍为 0。
- 2026-08-01：用户明确接受 F155 方案 A：系统 EventKit full access、Octo App/代码
  物理只读、写路径为零。F156 不再等待 A/B 决策，但仍等待 F153/F154/F155 Verify
  与 F151 authority；Design/Tasks/Implement 继续 false，production/E2E 仍为 0。
