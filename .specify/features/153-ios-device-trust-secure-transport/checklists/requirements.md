# F153 Requirements Checklist

## Product / Security

- [x] 原生 iOS 唯一手机入口；Safari/WebView 不计交付
- [x] 同一 named tunnel；无第二远程网络
- [x] Web Access 与 device trust 不互相转换
- [x] App 内 service token/Web Cookie/长期 bearer=0
- [x] Secure Enclave、Keychain、challenge、token、request proof 精确合同
- [x] 单设备 revoke/rotation 与 15 分钟 TTL
- [x] mobile Host/path 与 Web owner route 隔离
- [x] Bypass 风险和 live negative probe 已冻结
- [x] App Attest 是风险信号，不是 identity
- [x] `maojiwang.work` 只可作为个人部署事实

## Architecture / UX

- [x] 单 SQLite/StoreGroup、单 capability policy、单 URLSession client
- [x] F152/F153/F154/F155/F156 owner 不重叠
- [x] HealthKit/EventKit path 在 F153 保持 absent
- [x] Claude 初稿视觉语言 + SwiftUI 原生语义
- [x] Simulator 与真机证据不混淆

## Gate

- [x] `GATE_RESEARCH=true`
- [x] `GATE_DESIGN=true`
- [x] `GATE_TASKS=true`
- [x] T001 authority
- [x] T002 Protocol contract
- [x] T003 durable device-trust store
- [x] T004 P-256/token/proof/policy
- [x] T005 Web owner registration routes
- [x] T006 mobile enrollment/token routes
- [x] T007 protected ready/device profile routes
- [x] T008 mobile manifest/Doctor/Host-path isolation/production composition
- [x] T009 native Xcode/Secure Enclave/ThisDeviceOnly/signer DI compile contract
- [x] T010 single URLSession/canonical proof/typed error/bounded retry compile contract
- [x] T011 native registration states/Claude visual language/a11y compile contract
- [x] T012 focused Python/Gateway regression、iPhoneOS target build 与 bundle secret scan
- [x] T012 scheme-level generic build 与 Swift XCTest（iOS 26.5 Simulator，12/12）
- [x] T013 Simulator cold start、state/visual/a11y E2E（六状态视觉回归 + Dynamic Type + Reduce Motion）
- [x] 2026-07-31 当前提交完整 scheme 复验（12/12，六态 baseline 未更新）
- [x] T016 single client/store/auth、complexity、audit/source/bundle ratchet
- [x] T017 Blueprint/F150/F158 truth sync 与 iOS 设计偏离记录
- [x] T012-T013/T016 本地 Implement prerequisites
- [ ] T014 Cloudflare live 与 T015 真机
- [ ] T018 Verify
- [ ] F154 解锁
