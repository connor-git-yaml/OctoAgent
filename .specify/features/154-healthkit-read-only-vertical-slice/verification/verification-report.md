# F154 Verification Report

## 当前结论

- 日期：2026-08-02；
- 状态：`PARTIAL`；
- `GATE_VERIFY=false`；
- 当前提交：`0ec5997dc79b072d255d4ea1a3f401d8ad22c4ea`；
- T001-T012、T014、T015 已完成；T013 真 iPhone 与 T016 最终 Verify 未完成。

当前代码已经证明 HealthKit 只读垂直切片的 deterministic contract、Gateway 路由、
真实模型一次性分析、删除闭环、Simulator 功能/视觉/无障碍以及当前分支的非真机完整
scheme。它尚未证明真实 Apple Health 权限 sheet、真实步数/睡眠读取、锁屏与网络切换
生命周期，因此不得解锁 F155 production，也不得把 Simulator DI 冒充真机 HealthKit。

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

单次执行结果：

```text
result bundle:
/tmp/f158-f154-current.2vvthi/Logs/Test/
Test-OctoAgent-2026.08.02_04-18-09-+0800.xcresult

33 total / 27 passed / 6 skipped / 0 failed
```

六个 skip 全是必须依赖真 iPhone credential/live flag 的 F153 用例。F154 本次精确为：

- `HealthImportTests`：10/10 PASS；
- `HealthImportFlowUITests`：3/3 PASS；
- 冷启动、退后台与重启不自动请求健康权限：PASS；
- 12 个有限状态 Claude 早期视觉基线：PASS；
- Dynamic Type、VoiceOver 与 44pt 操作区：PASS。

Xcode 在测试启动阶段重复输出 `DebuggerLLDB.DebuggerVersionStore.StoreError`，但没有
重试或忽略失败；同一 process 最终返回 `TEST SUCCEEDED`，`xcresulttool` 汇总为
`failedTests=0`。

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
| real iPhone Health permission 与真实数据 | MISSING |
| real iPhone lock/background/network lifecycle | MISSING |
| final CI/evidence inventory/Blueprint sync | PENDING |
| F154 Verify | CLOSED |

## 真机待办

T013 必须在真 iPhone 上逐项证明：

1. 只有用户点按“读取健康数据”后才出现 Apple 权限 sheet；
2. 只请求 stepCount 与 sleepAnalysis read access，write set 为空；
3. 真实 24h/3d/7d 步数和睡眠可形成本地 preview，未授权/无数据诚实显示；
4. approve 后只发送 canonical summary，raw identifier/source metadata 不出设备；
5. Wi-Fi/蜂窝、锁屏、前后台、进程重启、离线与设备撤销均 fail closed；
6. 删除后 review、packet、analysis 与 pending Memory candidate 清除，只保留 metadata audit。

完成以上真机证据、当前提交 CI 终态、最终 evidence inventory 与 Blueprint/F158 truth
sync 后，才能勾选 T013/T016 并把 `GATE_VERIFY` 改为 `true`。
