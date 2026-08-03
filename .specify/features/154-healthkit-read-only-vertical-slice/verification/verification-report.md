# F154 Verification Report

## 当前结论

- 日期：2026-08-03；
- 状态：`PASS`；
- `GATE_VERIFY=true`；
- 当前验证提交：`4daf983f1c5893593d920f985ab671c2b7f4f1b1`；
- T001-T016 全部完成，F155 已解锁。

当前代码已经证明 HealthKit 只读垂直切片的 deterministic contract、Gateway 路由、
真实模型一次性分析、删除闭环、Simulator 功能/视觉/无障碍以及真 iPhone 权限、真实
24 小时/3 天/7 天预览、一次经授权的摘要上传和删除。2026-08-03 又完成了 preview-only
锁屏→解锁→前台恢复，证明未批准预览不会自动提交或分析，并能删除后返回空闲状态。
T013 因此完成。detached clean checkout、双触发远端 CI、evidence inventory、
verification/Blueprint/F158 truth sync 也已通过，F154 至此正式 Verify，F155 可按其既有
Tasks Gate 从自身 architecture authority 开始实施。

## 当前复验

### Python / Gateway / architecture

- F154 Gateway/Core/Policy/Protocol/authority focused：`25 passed / 0 failed`；
- F153 device-trust focused：`48 passed / 0 failed / 1 个既有 Pydantic warning`；
- repository architecture gate：PASS；
- F154 analysis 使用 managed `openai-codex / gpt-5.5` 完成恰好一次真实模型调用；
- 模型证据仅保留 metadata：`model_calls=1`、`memory_candidates=0`、
  `raw_forbidden_hits=0`，不保存 summary 内容或原始健康字段。

### T016 clean checkout、回归与远端 CI

在 detached worktree `/tmp/f158-f154-clean.6GMI5G/worktree` 对提交 `4daf983f` 执行，
执行前后工作树均 clean：

- F154 focused Backend：`24 passed / 0 failed`；
- repository architecture gate：PASS；
- iOS 完整 scheme：`36 total / 27 passed / 9 个明确 live-only skipped / 0 failed`；
- clean-checkout xcresult：
  `/tmp/f158-f154-clean-ios.PnWn7c/F154CleanCheckout.xcresult`；
- xcresult：178 files / 4,927,734 bytes；directory byte-map aggregate：
  `fdaa8995641a8830aea25125e7a0ba2320b348d98a21f0ca2467e73de3036e19`；
- generic iPhoneOS Release arm64 warnings-as-errors：`BUILD SUCCEEDED`，DerivedData：
  `/tmp/f158-f154-clean-release.LggLOd`。

同一提交的 push run `30782680229` 与 PR run `30782732897` 均为 success，两个 run 的
backend、frontend、Playwright、architecture、benchmark 五个 job 全部成功：

- push backend：`5760 passed / 14 skipped / 1 xfailed / 1 xpassed`，scripted
  `18 passed`；
- PR backend：`5760 passed / 14 skipped / 1 xfailed / 1 xpassed`，scripted
  `18 passed`；
- frontend：`70 files / 599 passed`；Playwright：`39 passed`；
- push 与 PR changed-lines 均为 `2192/2420 = 90.6% PASS`，base 为
  `db3214fff722c6f969baf99528a76fc03a1e21a1`；
- push LCOV SHA：`541e3daa34c4c005b48c7b8d23ce70eae2e4df529910fab3734439d5d58ab9e4`；
- PR LCOV SHA：`55d6b36018e49201a4ace022c2d937a2a4a59bbbccf2594af7e4febfd1790ac0`。

PR workflow 验证的是 GitHub merge commit `c17ad9dafbf1ae89d525bed23253a9793a720efc`，其
tree 与 push head 相同，均为 `e4b95674e9f7ab890f997612df1fe07ef4b32399`。缓存上传的
并发占位 warning 不影响任何 job 结论、测试结果或 coverage gate。

### iOS Simulator 完整 scheme

环境：

- Xcode：`26.6 (17F113)`；
- Simulator：`OctoAgent F158 iPhone 17 Pro`；
- iOS：`26.5 (23F77)`；
- device id：`3820A0E1-806E-4923-AC06-BA3A746F01DB`。

2026-08-03 clean checkout 的最终单次执行结果：

```text
result bundle:
/tmp/f158-f154-clean-ios.PnWn7c/F154CleanCheckout.xcresult

36 total / 27 passed / 9 skipped / 0 failed
files=178
bytes=4927734
directory byte-map aggregate=fdaa8995641a8830aea25125e7a0ba2320b348d98a21f0ca2467e73de3036e19
```

九个 skip 全是必须依赖真 iPhone credential/live flag 的显式 live 用例。F154 本次精确为：

- `HealthImportTests`：11/11 PASS；
- `HealthImportFlowUITests` 非 live：3/3 PASS；新增 lock-cycle live node 默认明确 skip；
- 冷启动、退后台与重启不自动请求健康权限：PASS；
- 12 个有限状态 Claude 早期视觉基线：PASS；
- Dynamic Type、VoiceOver 与 44pt 操作区：PASS。

Xcode 在该次测试启动阶段重复输出 `DebuggerLLDB.DebuggerVersionStore.StoreError`，但没有
重试或忽略失败；同一 process 最终返回 `TEST SUCCEEDED`，`xcresulttool` 汇总为
`failedTests=0`。

### 真 iPhone HealthKit 阶段性验证

同一原生 App、同一 `AppleHealthDataStore` 与同一 signed `DeviceTrustClient` 已证明：

- 冷启动和进入健康页面都不会自动请求权限，只有用户点按读取后才出现系统权限 sheet；
- read set 只含 step count 与 sleep analysis，write set 为空；
- 真实 24 小时、3 天、7 天窗口均形成诚实本地 preview 或明确无可读数据；
- preview-only 路径不会上传，前后台恢复后仍可由用户删除本地 preview；
- 唯一一次获批 7 天摘要完成 review 201、analysis 201、delete 200；
- 服务端 review、approved packet、analysis result、Memory candidate 最终为 `0/0/0/0`；
- 首次超时产生的两条遗留 source chain 已通过正式删除 API 清理，没有直接修改数据库；
- 三个删除回执均为 `completed`，只保留 metadata-only audit。

iOS analysis 请求预算已从通用 15 秒拆为 exact analysis 75 秒，URLSession resource
预算为 90 秒；其它请求仍保持 15 秒。修正后的真机 transaction 为
`1 passed / 0 failed / 0 skipped`，Simulator focused 回归为
`25 total / 21 passed / 4 live-only skipped / 0 failed`。2026-08-03 加入锁屏 fail-closed
seam 后，完整 Simulator scheme 为 `36 total / 27 passed / 9 live-only skipped / 0 failed`；
同一当前字节的 generic iPhoneOS Release arm64 warnings-as-errors 与 repository
architecture gate 均 PASS。

隐私安全的逐项证据见
[2026-08-02 真机阶段性报告](../evidence/real-device/2026-08-02/verification-report.md)。
原始 xcresult 只保存在本机 `/tmp`，没有把可能含聚合健康预览的截图复制进仓库。

### 真 iPhone preview-only 锁屏生命周期

2026-08-03 使用同一 production App、同一 live XCUI node 与 7 天本地 preview 完成一次
物理锁屏→解锁 transaction：

```text
result bundle:
/tmp/f158-f154-lockcycle-valid-result.U5jlbf/F154LockCycle.xcresult

1 total / 1 passed / 0 failed / 0 skipped
duration=20.308s
files=38
bytes=362425
directory byte-map aggregate=ac92cab0b27b00ce73cac0cadb8c3fa0f3004b96ef85d0359717bbbb626d37a3
```

测试先形成“等待你的批准”本地 preview，再真实观察 App 离开前台；用户解锁并返回
OctoAgent 后，preview 仍存在，且“提交中”“分析中”“已完成”均不存在。测试随后只点按
“删除本地预览”，最终回到“尚未读取”。transaction 的 approve flag 缺失、screenshot
capture 关闭，因此没有上传新摘要，也没有把本地聚合 preview 写入仓库证据。结构化隐私
安全 attestation 见
[2026-08-03 lock-cycle](../evidence/real-device/2026-08-03/lock-cycle-attestation.v1.json)。

执行前曾因既有 cloudflared connector 没有 active connection 导致 mobile hostname 返回
530；该次测试在 HealthKit 前置连接检查处停止，不计产品证据。只重启既有受管
LaunchAgent 后，config SHA 保持不变，公网 mobile route 恢复到 origin 的设备证明拒绝
响应，随后上述同一真机 transaction 一次通过。

## 已通过边界

| Gate | 结果 |
|---|---|
| finite step/sleep model 与 normalization | PASS |
| 单 `HKHealthStore` read-only adapter | PASS |
| preview/approve/analyze/delete 状态机 | PASS |
| F152 consent/lineage 与 F153 signed transport 复用 | PASS |
| Gateway review/analysis/deletion | PASS |
| ProviderRouter fail-closed、无 Echo | PASS |
| raw local-only / zero-retention / no Memory candidate | PASS |
| Simulator functional/visual/a11y | PASS |
| repository architecture / quality ratchet | PASS |
| real iPhone Health permission 与真实数据 | PASS |
| real iPhone approve/analyze/delete 与 zero-retention | PASS |
| real iPhone background/network/revoke 分层闭包 | PASS |
| real iPhone health preview lock→unlock lifecycle | PASS |
| final CI/evidence inventory/Blueprint sync | PASS |
| F154 Verify | PASS |

## 真机完成边界

T013 已由同一真 iPhone 的系统权限、真实 step/sleep、24 小时/3 天/7 天 preview、
preview-only 后台与物理锁屏恢复、一次经授权分析/删除，以及 F153 同一
`DeviceTrustClient` 的 Wi-Fi/纯蜂窝 4G、进程重启、owner revoke 与整机重启后的 revoke
persistence 共同闭合。锁屏 transaction 自身明确证明不会自动批准、不会提交，且仍可
删除本地 preview；没有用其它分层证据替代该 UI 物理场景。

首次 lock-cycle 行为尝试已真实生成本地 preview 并到达锁屏等待点，但 180 秒内设备
始终保持前台，故明确失败且不计证据；有效 transaction 已在同一生产字节下取代它。

仓库内最终 evidence inventory 精确为两件、7,950 bytes：

- `evidence/real-device/2026-08-02/verification-report.md`：
  SHA `d3575887f85cfcb641d4ddfe99e472d763c47ea61f168291df53d71768f5ac7f`；
- `evidence/real-device/2026-08-03/lock-cycle-attestation.v1.json`：
  SHA `f12d1d79810919631369ef42e34b209bef29d7b90b5dfb10a60f72468d381462`。

敏感原始 xcresult 继续只保存在本机 `/tmp`；仓库 evidence 不含健康值、设备标识、凭证
或截图。T016 与 `GATE_VERIFY=true` 已闭合，F155 正式解锁。
