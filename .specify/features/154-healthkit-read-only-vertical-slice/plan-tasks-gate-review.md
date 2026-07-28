# F154 Design / Tasks Gate Review

- 日期：2026-07-29
- 审查者：main / F158 Milestone closure
- `GATE_RESEARCH=true`
- `GATE_DESIGN=true`
- `GATE_TASKS=true`
- `GATE_IMPLEMENT=false`

## 判定

PASS（artifacts only）。Spec、Plan 与 Tasks 已把 Apple 官方权限事实、最小数据类型、
raw/normalized/review/approved/result/Memory/delete 边界、F152/F153/F156 owner、
SwiftUI 视觉、Simulator/真机/L2/CI 证据逐项冻结。

## 为何 Implement 仍关闭

F153 当前真实状态是：

- mobile hostname/DNS/tunnel ingress/Access Bypass 未配置；
- Gateway doctor 报告 mobile device access disabled；
- 没有连接真 iPhone；
- F153 `GATE_VERIFY=false`。

因此本 Gate 不能被解释为允许 HealthKit entitlement、production code 或 route。
只有 F153 T014/T015/T018 全部通过后，T001 才能先以 architecture RED 开始。

## Scope Review

- exact iOS types：stepCount + sleepAnalysis；
- exact capabilities：review submit / analysis run / source delete；
- exact mobile routes：reviews / analyses / source deletion；
- HealthKit write/background/clinical/其它 type：0；
- second identity/session/store/client/policy/audit/runner：0；
- F156 shell/navigation ownership transfer：0。

## Quality Review

- 每个行为任务有稳定 oracle、accept/reject boundary 和测试层级；
- 真机证据不能被 Simulator 替代；
- 实现任务严格 RED→GREEN→REFACTOR；
- no raw-field、no Echo、deletion cascade 和视觉回归均为硬门。
