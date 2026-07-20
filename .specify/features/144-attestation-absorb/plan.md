# F144 验收自动吸收 — Implementation Plan

1. 补齐 `test_frontdoor_auth.py` 的 mode×header 矩阵，钉住 Guard 单一入口。
2. 在 `attest_commands.py` 实现 service-only 探针与 JSON/exit-code 契约。
3. 用 FakeServiceManager、fake signal、虚拟 clock 与可编程 probe 覆盖全部分支。
4. 增加 L3 scripted 写入审批 approve/reject 双路径。
5. 将唯一物理残余写入 `attestation-checklist.md`。
6. 交给 F141 release lane 消费 service JSON status 与 attestation 签署状态。

验证顺序：L4 聚焦测试 → L3 scripted → e2e smoke → gate 脚本单测 → 文档同步。
