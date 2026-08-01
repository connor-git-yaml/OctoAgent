# F155 Implementation Plan

## Gate

- Research：PASS
- Product Decision：`OPTION_A_ACCEPTED_2026-08-01`
- Design/Tasks：PASS
- Implement：`CLOSED_ON_F153_F154_VERIFY_AND_F151_AUTHORITY`
- Verify：`CLOSED`

## Phase 0：决策与 architecture authority

1. 用户已明确选择保留 F155，并接受系统 full access + Octo App-read-only。
2. decision text/date/review identity 已写入 Gate：
   `user-f155-option-a-20260801`。
3. F154 Verify 后，在 F151 单一 checker 登记 exact paths/symbols。
4. RED 以 `F155_ARCHITECTURE_AUTHORITY_MISSING` 见红。
5. 拒绝 calendar mutation、Reminders/Contacts、第二 store/client/session/runner。

## Phase 1：local calendar read

1. Swift pure RED：finite status/window、overlap/dedup/DST、field allowlist、title opt-in。
2. 单 `EKEventStore` adapter，user-action full-access request。
3. finite coordinator、expiry、取消/离线/revoked/清理。
4. AST/package adversarial gate 证明 write symbols/capabilities/routes 为 0。

## Phase 2：F152/F153 network chain

1. 扩展 finite DeviceCapability 的三项 calendar capability。
2. 复用 F152 Protocol/store/policy/audit/deletion。
3. 新增唯一 mobile calendar router/service。
4. ProviderRouter 单次分析；auth-fatal/timeout/error fail closed。

## Phase 3：SwiftUI slice

1. F156 shell 未完成前，只在 connected state 提供 slice-specific navigation entry。
2. 完成 permission explanation、五态授权、empty/preview/approve/result/delete。
3. Claude early visual language + SwiftUI native semantics。

## Phase 4：运行证据

1. Swift/Python L4 与 Gateway SQLite L3。
2. Simulator function/visual/a11y，不冒充真权限。
3. 真 iPhone full-access sheet、真实事件、撤销、生命周期、网络切换、0 mutation。
4. 一次 approved real model；无 Echo/no raw fields。

## Phase 5：Verify

1. write-symbol/raw-field/secret/package/log/snapshot scan。
2. clean checkout、blast-radius regression、CI、architecture gate。
3. verification report、Blueprint/M12/F158 sync。
4. 通过后才能让 F156 展示 calendar production surface。
