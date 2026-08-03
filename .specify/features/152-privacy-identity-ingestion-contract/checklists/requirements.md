# F152 Requirements Checklist

## Product / Security

- [x] 原生 iOS 唯一手机入口，WebView/Safari 不计产品交付
- [x] App 内 Cloudflare service token/Web Cookie/长期 bearer 禁止
- [x] raw/normalized/review/approved/transcript/Memory 物理分层
- [x] 每次敏感分析 preview + explicit consent
- [x] Memory 只能作为 optional candidate 进入既有 review
- [x] 单设备撤销、轮换、TTL、nonce 与 proof binding 已冻结
- [x] 删除链和 durable non-sensitive audit 已冻结
- [x] HealthKit 授权不可区分事实已进入合同
- [x] EventKit OS full-access 决策门已进入合同

## Architecture

- [x] F152/F153/F154/F155/F156 owner 不重叠
- [x] 不创建第二 Memory/audit/identity/session/registry
- [x] core domain / policy decision / protocol projection / future gateway+iOS 分层明确
- [x] Claude Design 早期视觉语言与 SwiftUI 原生语义同时保留

## Gate

- [x] `GATE_RESEARCH=true`
- [x] `GATE_DESIGN=true`
- [x] `GATE_TASKS=true`
- [x] T001 authority RED→GREEN→REFACTOR
- [x] T002-T012 implementation
- [x] T014 Verify
- [x] F153 production 解锁
