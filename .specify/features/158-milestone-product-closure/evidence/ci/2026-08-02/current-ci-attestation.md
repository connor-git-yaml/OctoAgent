# 当前分支累计 CI 与完整生产差异覆盖证明

- 日期：2026-08-02
- coverage/wiring 修复提交：`2b1fb5ec4a78ca1a3a2cba2343e0b45d5abc2204`
- GitHub Actions run：`30719719793`
- run 结论：`success`
- jobs：`backend-deterministic / benchmark / frontend / l1-playwright / architecture`
  全部 `success`
- backend deterministic：`5759 passed / 14 skipped / 1 xfailed / 1 xpassed`
- scripted E2E：`18 passed`
- frontend：`70` files、`599 passed`
- L1 Playwright：`39 passed`
- benchmark：`2 passed`
- repository architecture：`PASS`

## 累计 branch-base 直接证明

非主分支 workflow 没有使用上一 push；它 fetch `origin/master` 并选择 merge-base
`db3214fff722c6f969baf99528a76fc03a1e21a1`。因此本 run 直接覆盖从 master 到当前 head
`2b1fb5ec4a78ca1a3a2cba2343e0b45d5abc2204` 的完整累计生产差异。

下载 `backend-coverage-and-junit` artifact 后独立核验：

- head tree：`a518c89a0a8bcd1047f723ff86870d8de74e31a1`
- committed fingerprint：`55c613ae0d1c3c59536f070c8c4607b15b2c6cd53a7556898bdd5f7d99dcd4c4`
- LCOV SHA-256：`db3b26b174d0f2f8fd68f18f102c22eec43b541a4c41c6037c3c65f2f57f5f8d`
- committed report：`changed-lines-cumulative.json`
- report SHA-256：`7dee4209a0e9ed1bdbcb56e520843685837755dbbc5f05458d80a30b1c8d9aba`
- backend JUnit SHA-256：`6913e0f6fa130d3d932e592f7bb60787f15d5f40333e2e013648b1cf7f46d4ec`
- scripted JUnit SHA-256：`357226b38f10fc9c396e9a74baa9180c77bbb40da331af87a346fc3d917fc73b`
- 结论：`2192/2420 = 90.6% PASS`

## 门禁缺口与处置

旧 workflow 对任意 push 使用 `github.event.before`，所以失败生产提交可能被后续
test/docs-only push 遗忘。F158 T050 已取得真实 RED 并修改同一 F151 wiring contract：
PR 使用目标 base、`master` push 使用 `event.before`、其他分支统一累计比较
`merge-base(origin/master, HEAD)`。run `30719719793` 已直接生成累计 PASS 报告，T050
状态为 `WORKFLOW_REVERIFY_PASS`；旧 `549/608` 本地复算只保留为历史诊断，不再承担当前证明。
