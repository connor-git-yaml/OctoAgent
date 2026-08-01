# 当前分支 CI 与完整生产差异覆盖证明

- 日期：2026-08-02
- coverage 修复提交：`2f6fcbc91408e14bc3f5291678bdfd018cc24473`
- GitHub Actions run：`30717516171`
- run 结论：`success`
- jobs：`backend-deterministic / benchmark / frontend / l1-playwright / architecture`
  全部 `success`
- backend deterministic：`5756 passed / 14 skipped / 1 xfailed / 1 xpassed`
- scripted E2E：`18 passed`
- frontend：`70` files、`599 passed`
- L1 Playwright：`39 passed`
- benchmark：`2 passed`
- repository architecture：`PASS`

## 同 push 报告不能单独作为完整证明

run `30717516171` 的 push event base 是上一提交
`0ec5997dc79b072d255d4ea1a3f401d8ad22c4ea`。该提交到 `2f6fcbc9` 只新增测试，故 workflow
原始 changed-lines 报告为 `0/0 PASS`。它证明本次 push 没有新增未覆盖生产行，但不能证明
上一轮 `541/608 = 89.0%` 的生产差异已经被新测试覆盖。

## 相同 LCOV 的完整范围复算

从 run `30717516171` 下载 `backend-coverage-and-junit` artifact，在 detached clean clone
`2f6fcbc9` 上使用同一 `repo-scripts/check-changed-lines-coverage.py`、同一 90% 门槛，改用
上一失败 run 的生产 base `d031809f98ffdd6df49e7ba1ed50a8af00cf0dc0` 复算：

- head tree：`923356595e07d4750159d2299b0bae092133a1e4`
- clean fingerprint：`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- LCOV SHA-256：`35e5942376f6866636e97995e4614669a1efa2e5f9b90fe48fd99bd7fbfbee37`
- committed report：`changed-lines-full-range.json`
- report SHA-256：`d01ac479230b2c0385c13e55c00f8640e14d0b6e4b68d7e7dbcdf88764d41fa8`
- 结论：`549/608 = 90.3% PASS`

## 门禁缺口与处置

旧 workflow 对任意 push 使用 `github.event.before`，所以失败生产提交可能被后续
test/docs-only push 遗忘。F158 T050 已取得真实 RED 并修改同一 F151 wiring contract：
PR 使用目标 base、`master` push 使用 `event.before`、其他分支统一累计比较
`merge-base(origin/master, HEAD)`。本证明在修复后的 branch CI 直接产生累计报告前保持
`LOCAL_RECOMPUTE_ACCEPTED / WORKFLOW_REVERIFY_PENDING`，不得被写成最终 workflow 证据。
