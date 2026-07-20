# Attestation 残余清单（人工验收，物理不可自动化项）

> **宪章（验证吸收原则，2026-07-12 用户拍板，M9 全局约束）**：任何「请用户手工验证」
> 的输出视为体系缺陷——必须先尝试分层吸收（L4 单元 → L3 确定性 e2e/scripted →
> L1 UI 自动化 → 本机 live 探针 `octo attest`），**只有物理不可自动化的残余**才允许
> 在本清单落一行。
>
> **消费方**：F141 release lane——release gate 对本清单每个非 optional 项要求
> 「签署」（人工执行 action 后回填 `last_attested` 日期），而非假装可自动跑或漏掉。
> 机器可读源 = 下方 fenced YAML block（`id` 唯一；解析器取第一个 ```yaml block）。
>
> **增项纪律**：新增一行前必须先回答「为什么 L4 / L3 / L1 / `octo attest` 探针都
> 吸收不了」，理由写进 `why_physical`。能吸收的不许进清单（先去写测试/探针）。

## 机器可读源

```yaml
attestations:
  - id: ATT-129-BOOT
    source_ac: "F129 AC-1（开机自启半边）"
    why_physical: >-
      需要真实重启整台 Mac（launchd RunAtLoad 语义只能被真 reboot 验证）；
      崩溃自愈半边已被 `octo attest service` 吸收，本项只剩 reboot。
    action: "重启 Mac → 登录后 `octo service status` 显示运行中 + /ready 绿"
    frequency: release
    last_attested: null
    optional: false
```

## 字段语义

| 字段 | 含义 |
|------|------|
| `id` | 唯一标识（`ATT-<来源 Feature>-<短语>`），lane 以此追踪签署状态 |
| `source_ac` | 来源验收标准（哪条 AC 的物理残余） |
| `why_physical` | 为什么四层 + 探针都吸收不了（增项纪律的答卷） |
| `action` | 验收动作一行（人工执行的确切步骤） |
| `frequency` | 要求签署的节奏（`release` = 每次 release gate） |
| `last_attested` | 最近一次人工签署日期（`YYYY-MM-DD`；`null` = 从未） |
| `optional` | `true` = lane 记录但不阻断（如已被探针基本覆盖的体验项） |

## 与 `octo attest` 探针的分工

| 验收面 | 自动化归属 | 本清单残余 |
|--------|-----------|-----------|
| F129 崩溃自愈（kill → 拉起新 pid） | `octo attest service` | — |
| F129 开机自启（reboot → 自动运行） | 物理不可自动化 | ATT-129-BOOT |
| F135 gap-1（USER.md 写入 + 审批全链） | L3 scripted（`test_e2e_scripted_write_approval.py`） | — |

维护：本文件随吸收进度更新——某残余项被新机制吸收时**删行**，不留死项。
来源制品：`.specify/features/144-attestation-absorb/`。
