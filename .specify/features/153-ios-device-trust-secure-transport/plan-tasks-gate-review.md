# F153 Plan / Tasks Gate Review

## 判定

`GATE_TASKS=true`

## 审查

- 19 个 unique tasks，T000 已完成，T001-T018 unchecked。
- 每个行为能力先有 RED，再 GREEN/REFACTOR；外部 Cloudflare、Simulator runtime 和
  真机证据独立，不能被 local mock 替代。
- production budget 只覆盖 F153 protocol/store/gateway/iOS project 与现有 composition
  seams；HealthKit/EventKit/F156 companion UI 不在预算。
- iOS 生产只有一个 URLSession client；服务端只有一个 device-trust store、一个
  F152 policy owner和一个 route family。
- T014 是唯一 Cloudflare account/DNS/Access/tunnel 外部写任务。
- T015 是唯一 Secure Enclave/Keychain/网络切换真机验收。

## Implement 放行

只放行 T001 architecture authority。T001 通过后按 Tasks 顺序推进，无需为普通本地
实现逐步询问；任何外部账户/管理员/真机信任操作仍需当次确认。
