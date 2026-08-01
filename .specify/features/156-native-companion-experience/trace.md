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
- 2026-08-02：在不占用用户 iPhone 的情况下重新生成 current FastAPI OpenAPI 并与
  mobile Host allowlist、DeviceTrust capability grant 和 owner route source 双向对账。
  canonical OpenAPI SHA 为
  `6fd9925ce7ddaf969c9422d8ad42ef4917a97f89985d0c04c3d09dab3b9866b5`；当前 exact
  mobile route 为 10 条：F153 device-trust 7 条、F154 Health review/analysis/delete
  3 条。实际 token 只签发 device ready/profile + Health 三项 capability；Core 中预留的
  conversation/task/approval/Memory 七项仍未签发，且对应 F156 product route 仍不存在，
  所以原 8 项 product gap 数量不变。同步纠正 architecture authority：F153 Verify=true、
  F155 方案 A 已接受但仍要求 F155 Verify。该刷新只修复 artifacts drift，不新增生产/
  测试或提前打开 Design/Tasks/Implement Gate。
