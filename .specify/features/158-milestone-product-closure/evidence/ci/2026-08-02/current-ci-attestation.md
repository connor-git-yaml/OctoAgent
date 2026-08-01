# 当前分支累计 CI 与完整生产差异覆盖证明

- 日期：2026-08-02
- coverage/wiring 修复提交：`2b1fb5ec4a78ca1a3a2cba2343e0b45d5abc2204`
- Node 24 Actions 复验提交：`1723c84f9fb07ac8273c2a6bc6af52f7016cb1da`
- GitHub Actions run：`30720799929`
- run 结论：`success`
- jobs：`backend-deterministic / benchmark / frontend / l1-playwright / architecture`
  全部 `success`
- backend deterministic：`5760 passed / 14 skipped / 1 xfailed / 1 xpassed`
- scripted E2E：`18 passed`
- frontend：`70` files、`599 passed`
- L1 Playwright：`39 passed`
- benchmark：`2 passed`
- repository architecture：`PASS`
- run UTC：`2026-08-01T22:13:30Z` → `2026-08-01T22:30:51Z`

## 累计 branch-base 直接证明

非主分支 workflow 没有使用上一 push；它 fetch `origin/master` 并选择 merge-base
`db3214fff722c6f969baf99528a76fc03a1e21a1`。因此本 run 直接覆盖从 master 到当前 head
`1723c84f9fb07ac8273c2a6bc6af52f7016cb1da` 的完整累计生产差异。

下载 `backend-coverage-and-junit` artifact 后独立核验：

- head tree：`39cf06fda78c1f07b443379e7a4f9a0dfaefb47b`
- committed fingerprint：`55c613ae0d1c3c59536f070c8c4607b15b2c6cd53a7556898bdd5f7d99dcd4c4`
- LCOV SHA-256：`1e2a0d9011d5d0e1d4af892c9ddf56b2b351e272368e70b124c0b08e3c7129c2`
- committed report：`changed-lines-node24.json`
- report SHA-256：`fe07c15b1b2314217eda81f533a2890aebe4b44438483a80d713f5f20bbc47dd`
- backend JUnit SHA-256：`5d848bd7f46982cf9425a86179bcc3e6123c236465768883f19b4f628fc7e383`
- scripted JUnit SHA-256：`d5aa23543be1677abfa21f6ac4b7a934b3dac9295f3737c7341cce699196a925`
- 结论：`2192/2420 = 90.6% PASS`

上一累计通过 run `30719719793` 与其不可变 `changed-lines-cumulative.json` 继续保留，
作为 Node 24 Actions 迁移前的相同生产差异证明。

## 门禁缺口与处置

旧 workflow 对任意 push 使用 `github.event.before`，所以失败生产提交可能被后续
test/docs-only push 遗忘。F158 T050 已取得真实 RED 并修改同一 F151 wiring contract：
PR 使用目标 base、`master` push 使用 `event.before`、其他分支统一累计比较
`merge-base(origin/master, HEAD)`。run `30720799929` 已在 Node 24-compatible
`checkout/setup-python/setup-node/cache/upload-artifact` 与固定发布提交的
`setup-uv v8.3.2` 上再次生成累计 PASS 报告；GitHub annotations 中 Node 20 deprecation
为 0。三个并行 job 只留下相同 uv cache reserve warning，由 architecture job 成功保存
同一 cache；这是官方 first-writer-wins 的非阻断并发结果，不另触发一轮长 CI。
T050 状态保持 `WORKFLOW_REVERIFY_PASS`；旧 `549/608` 本地复算只保留为历史诊断，
不再承担当前证明。
