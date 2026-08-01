# F154 Requirements Checklist

## Research / Product

- [x] Apple read denial 不可观测事实已冻结
- [x] 权限只在明确用户动作时请求
- [x] exact types 仅 stepCount + sleepAnalysis
- [x] 24h/3d/7d 且最大 7 天
- [x] no-readable-data/limited-access 诚实状态
- [x] 普通 UI 使用“Apple 健康”而非 HealthKit
- [x] Claude early visual language + SwiftUI native semantics

## Privacy / Architecture

- [x] raw sample/identifier/source/device/metadata 不落盘不上送
- [x] toShare empty；save/delete/background/observer/clinical absent
- [x] F152 review/consent/packet/result/candidate/delete/audit 全复用
- [x] F153 Host/device proof/capability/replay/revoke 全复用
- [x] ProviderRouter 唯一分析入口；auth failure no Echo
- [x] Memory 仍需 F152 第二次确认
- [x] no second store/client/session/policy/audit/runner
- [x] F156 final companion shell ownership不转移

## Gate

- [x] GATE_RESEARCH=true
- [x] GATE_DESIGN=true
- [x] GATE_TASKS=true
- [x] F153 GATE_VERIFY=true
- [x] T001 architecture authority
- [x] T002-T003 Swift model/normalization RED→GREEN→REFACTOR
- [x] T004-T011 RED→GREEN→REFACTOR
- [x] T012 Simulator
- [ ] T013 real iPhone
- [ ] T014 live model
- [x] T015 architecture/quality
- [ ] T016 F154 Verify
- [ ] F155 unlocked
