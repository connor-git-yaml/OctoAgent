# F154 Verification Report

## 当前结论

- 日期：2026-08-03；
- 状态：`PARTIAL`；
- `GATE_VERIFY=false`；
- 当前提交：`1086d987a7037fbc1766e667dfc5f3f5664954f8`；
- T001-T015 已完成；只剩 T016 最终 Verify 未完成。

当前代码已经证明 HealthKit 只读垂直切片的 deterministic contract、Gateway 路由、
真实模型一次性分析、删除闭环、Simulator 功能/视觉/无障碍以及真 iPhone 权限、真实
24 小时/3 天/7 天预览、一次经授权的摘要上传和删除。2026-08-03 又完成了 preview-only
锁屏→解锁→前台恢复，证明未批准预览不会自动提交或分析，并能删除后返回空闲状态。
T013 因此完成；F155 production 仍须等待 T016 的最终 CI、evidence inventory、
verification/Blueprint/F158 truth sync 正式通过。

## 当前复验

### Python / Gateway / architecture

- F154 Gateway/Core/Policy/Protocol/authority focused：`25 passed / 0 failed`；
- F153 device-trust focused：`48 passed / 0 failed / 1 个既有 Pydantic warning`；
- repository architecture gate：PASS；
- F154 analysis 使用 managed `openai-codex / gpt-5.5` 完成恰好一次真实模型调用；
- 模型证据仅保留 metadata：`model_calls=1`、`memory_candidates=0`、
  `raw_forbidden_hits=0`，不保存 summary 内容或原始健康字段。

### iOS Simulator 完整 scheme

环境：

- Xcode：`26.6 (17F113)`；
- Simulator：`OctoAgent F158 iPhone 17 Pro`；
- iOS：`26.5 (23F77)`；
- device id：`3820A0E1-806E-4923-AC06-BA3A746F01DB`。

2026-08-03 当前未提交字节的最新单次执行结果：

```text
result bundle:
/tmp/f158-f154-current-simulator.j8ykha/F154CurrentSimulator.xcresult

36 total / 27 passed / 9 skipped / 0 failed
files=178
bytes=4950086
directory byte-map aggregate=9efd087edde28df1a8f4731b750e982c62cd07e5bd8a7ba41dc3832eb17248fb
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
| final CI/evidence inventory/Blueprint sync | PENDING |
| F154 Verify | CLOSED |

## 真机完成边界

T013 已由同一真 iPhone 的系统权限、真实 step/sleep、24 小时/3 天/7 天 preview、
preview-only 后台与物理锁屏恢复、一次经授权分析/删除，以及 F153 同一
`DeviceTrustClient` 的 Wi-Fi/纯蜂窝 4G、进程重启、owner revoke 与整机重启后的 revoke
persistence 共同闭合。锁屏 transaction 自身明确证明不会自动批准、不会提交，且仍可
删除本地 preview；没有用其它分层证据替代该 UI 物理场景。

2026-08-03 的首次 lock-cycle 行为尝试已真实生成本地 preview 并到达锁屏等待点，但
180 秒内 iPhone 始终保持前台，测试以明确 assertion 失败，不能计为通过证据。该
transaction 未设置 approve、禁用 screenshot，且在失败后终止 App 清除了 session-only
preview；没有上传健康摘要。下一次必须由用户在提示后实际按侧边键锁屏再解锁。

当前只剩 T016：完成当前 truth CI 终态、最终 evidence inventory、verification report 与
Blueprint/F158 truth sync 后，才能勾选 T016 并把 `GATE_VERIFY` 改为 `true`。
