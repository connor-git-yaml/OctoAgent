# F155 Trace

- 2026-07-29：F158 复核确认 F155 仅在 Blueprint 预留，未立项/实施。
- 2026-07-29：Apple 官方资料确认 events 读取需要 full access，iOS 不提供 read-only
  permission；write-only 不能读取真实 events。
- 2026-07-29：形成方案 A/B decision dossier。若保留，exact 范围为 events only、
  未来 24h/3d/7d、raw local-only、title 默认不上送、当次批准/单次分析/删除，
  calendar mutation path=0。
- 2026-07-29：Research PASS。用户尚未明确接受系统 full access，因此
  Product/Design/Tasks/Implement 均保持 false；production/test behavior=0。
- 2026-08-01：用户明确选择方案 A，接受 iOS 系统向 App 授予 calendar events
  full access，同时要求 Octo 产品、capability、protocol、route 与 EventKit 调用物理
  只读、mutation path=0。review identity=`user-f155-option-a-20260801`；
  Product Decision、Design、Tasks Gate 通过，Implement 仍关闭在 F153/F154 Verify
  与 F151 authority，production/test behavior 仍为 0。
