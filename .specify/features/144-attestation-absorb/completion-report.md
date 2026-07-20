# F144 验收自动吸收 — Completion Report

## 已交付

| 验收面 | 自动化归属 | 人工残余 |
|--------|-----------|----------|
| front-door mode×header 安全语义 | L4 矩阵 | 无 |
| F129 崩溃自愈 | `octo attest service` live 探针 | 无 |
| F129 开机自启 | 物理 reboot | `ATT-129-BOOT` |
| 写入审批 approve/reject | L3 scripted | 无 |

## 探针契约

- service 未安装：`not_enabled` + exit 0；release lane 解析 status 后判 FAIL。
- kill 后恢复为新 pid 且 `/ready` 通过：`pass` + exit 0。
- signal、恢复、pid 或 readiness 任一失败：`fail` + exit 1。
- dry-run 不发送 signal；JSON 模式 stdout 不混入 Rich 文本。

## 安全与测试

- production probe 依赖通过参数注入，单测不杀真实进程、不等待真实时钟。
- front-door 认证仍只有 `FrontDoorGuard` 一个入口。
- attestation 清单只保留物理不可自动化项，不保留 optional 死项。
