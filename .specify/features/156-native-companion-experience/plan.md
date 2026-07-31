# F156 Implementation Plan

## Gate

- Research：PASS
- Design/Tasks Draft：complete
- Design/Tasks Gate：`CLOSED_ON_UPSTREAM_DECISIONS`
- Implement/Verify：`CLOSED`

## Phase 0：recon / authority

1. F153 Verify、F154 状态与 F155 A/B 决定完成。
2. 从 current OpenAPI/event/action inventory 建 exact mobile contract map。
3. F151 checker 登记 F156 exact paths/symbols，先取得 architecture RED。
4. 冻结 Claude early source SHA、scene mapping 和 later-design negative set。

## Phase 1：generated contract / finite state

1. 生成而非手写 conversation/task/approval/Memory DTO。
2. pure finite reducers/projections/navigation/deep-link。
3. 复用 F153 single client，扩展 protected request/SSE seam，不创建第二 transport。

## Phase 2：shell / conversation

1. `CompanionRoot` + four-tab `TabView` + per-tab `NavigationStack`。
2. conversation list/detail/composer/send/stream/reconnect。
3. deterministic function + visual RED→GREEN→REFACTOR。

## Phase 3：tasks / inbox

1. task list/detail/artifact/actions。
2. approval typed decisions。
3. Memory candidate typed second confirmation。
4. conflict/expired/offline/revoked adversarial。

## Phase 4：settings / notifications

1. connection/device/privacy + F154/F155 owner composition。
2. contextual notification permission。
3. APNs token registration/provider service/payload allowlist/deep link。
4. bounded background refresh；no sensitive automatic action。

## Phase 5：runtime evidence

1. Swift/Python L4、Gateway real SQLite/SSE/APNs fake L3。
2. Simulator 40-scenario applicable subset、visual/a11y matrix。
3. real iPhone APNs/background/network/revoke/owner slices。
4. clean checkout、CI、secret/snapshot/package scan。

## Phase 6：Verify / final Milestone

1. 40-row matrix 双向闭合。
2. Claude early fidelity ledger 与 later-design negative scan。
3. F156 verification report。
4. Blueprint/M12/F158 sync。
5. F158 final Web+iOS completion audit。
