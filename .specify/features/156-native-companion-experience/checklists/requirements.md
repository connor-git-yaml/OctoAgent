# F156 Requirements Checklist

## Product / Design

- [x] 原生 iOS 唯一手机入口
- [x] exact four tabs：chat/tasks/inbox/settings
- [x] approvals 与 Memory candidate typed separation
- [x] Claude early 是视觉基线，现有 Web/later design 不是
- [x] SwiftUI native navigation/a11y divergence ledger
- [x] 40-row startup/function/visual scenario matrix
- [x] current Gateway actions recon（10 mobile routes：F153=7、F154=3；F156 product gaps=8）
- [x] F155 方案 A 决策（系统 full access、Octo 物理只读）

## Architecture / Privacy

- [x] F153 single identity/client/proof
- [x] generated contract→coordinator→projection→view
- [x] no second transport/state/session/device/notification registry
- [x] APNs payload no sensitive body
- [x] notification never executes dangerous action
- [x] SceneStorage/AppStorage no sensitive content
- [x] background no Health/Calendar/LLM/approval/Memory/send

## Gate

- [x] GATE_RESEARCH=true
- [ ] GATE_DESIGN=true
- [ ] GATE_TASKS=true
- [x] F153 GATE_VERIFY=true
- [ ] F154 GATE_VERIFY=true
- [x] F155 decision resolved
- [ ] F155 GATE_VERIFY=true
- [ ] T002-T019 RED→GREEN→REFACTOR
- [ ] T020 Simulator E2E
- [ ] T021 visual/a11y
- [ ] T022 real iPhone
- [ ] T025 Verify
