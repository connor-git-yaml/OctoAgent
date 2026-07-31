# F155 Requirements Checklist

## Research / Product

- [x] Apple 没有 calendar read-only permission
- [x] full access 系统能力包含 create/edit/delete
- [x] 方案 A/B 与推荐理由明确
- [ ] 用户明确选择 A 或 B
- [x] 未来 24h/3d/7d，最大 7d
- [x] title local visible / packet default absent
- [x] Claude early visual language + SwiftUI native semantics

## Privacy / Architecture

- [x] raw identifiers/notes/location/attendees/URL 不落盘不上送
- [x] calendar write capability/route/protocol/EventKit symbol target=0
- [x] F152 review/consent/packet/result/candidate/delete/audit 复用
- [x] F153 Host/device proof/replay/revoke 复用
- [x] ProviderRouter only；auth failure no Echo
- [x] no second store/client/session/policy/audit/runner
- [x] F156 shell ownership不转移

## Gate

- [x] GATE_RESEARCH=true
- [ ] GATE_PRODUCT_DECISION=true
- [ ] GATE_DESIGN=true
- [ ] GATE_TASKS=true
- [ ] F153 GATE_VERIFY=true
- [ ] F154 GATE_VERIFY=true
- [ ] T003-T013 RED→GREEN→REFACTOR
- [ ] T014 Simulator
- [ ] T015 real iPhone
- [ ] T016 live model
- [ ] T018 Verify
