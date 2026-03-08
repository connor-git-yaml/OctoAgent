# OpenClaw -> OctoAgent Migration Rehearsal

**Feature**: `031-m3-user-ready-acceptance`
**Date**: 2026-03-08
**Input Snapshot**: `_references/openclaw-snapshot/`

## 结论

- 结果：**PASS（可迁移，但需要按清单执行人工步骤）**
- 结论：OctoAgent 当前 `master` 已具备把 OpenClaw snapshot 迁入 M3 主路径的最低能力，但正式 cutover 前仍需 owner 手动处理 secrets、device pairing 与 cron 语义差异。

## Snapshot 摘要

- `openclaw.json` 已包含 `wizard / auth / channels / gateway / memory / models / tools / skills / agents` 等主配置面。
- `cron/jobs.json` 含 `18` 个 automation jobs，可作为 OctoAgent `automation.create` 的迁移输入。
- `credentials/` 下存在 Telegram / Discord pairing 与 allow-list 元数据；这些需要迁移为 OctoAgent 的 secret refs + 重新 pairing，而不是直接复用明文文件。
- `workspace/projects/` 下存在多组项目目录，说明 OpenClaw 侧已经形成 project-like 工作组织。
- `workspace/projects/wechat-memory/data/raw/wechat_20260220/*.jsonl` 提供了 WeFlow 风格微信导出；031 已补齐 `.jsonl` 导入支持，用作正式 rehearsal 输入格式。

## 映射策略

| OpenClaw 资产 | OctoAgent 目标对象 | 迁移方式 | 备注 |
|---|---|---|---|
| `workspace/projects/<slug>` | `Project` + `primary Workspace` | 手动创建或脚本批量创建 | 031 只做 rehearsal，不直接批量写生产实例 |
| `openclaw.json.auth.profiles` | Secret Store bindings / auth profiles | 重新配置 `SecretRef` | 不直接复制明文 credential 值 |
| `cron/jobs.json` | `automation.create` jobs | 逐条映射 `name / schedule / target action` | 需要人工确认 action 语义差异 |
| WeFlow `.jsonl` 微信导出 | Import Workbench `source_type=wechat` | 直接通过 029/031 导入路径 | 031 已补 `.jsonl` adapter |
| pairing / allow-list 元数据 | channel/device 管理入口 | 重新配对 | 不直接复用旧 runtime token |
| memory / vault 相关资料 | import -> fragment / proposal / vault review | 导入后由 Memory Console 审核 | 不旁路 SoR / WriteProposal 治理 |

## Rehearsal 步骤

1. 确认目标 OctoAgent 实例已完成 `Feature 025-B` 与 `Feature 026` 基线，具备 project、secret、control plane。
2. 为目标 OpenClaw 项目创建同名或映射后的 OctoAgent `Project`。
3. 将 provider / gateway / channel secrets 改写为 `SecretRef`，不复制旧明文文件。
4. 使用 Import Workbench 导入 WeFlow `.jsonl` 微信导出，验证 `detect -> mapping -> preview -> run` 主路径。
5. 在 Memory Console 中复核 import 产生的 fragments / proposals / vault refs。
6. 按需重建 automation jobs，校对 schedule 与 action semantics。
7. 记录 deferred items，并准备 rollback：保持原 OpenClaw snapshot 只读，OctoAgent 仅导入副本。

## 已验证点

- WeFlow `.jsonl` 已可被 `ImportWorkbenchService.detect_source()` 正确识别并进入 preview/run 主路径。
- `project.select`、`automation.create`、`delegation work inheritance` 已由 031 acceptance tests 覆盖。
- front-door boundary 已强制要求 loopback / bearer / trusted_proxy，不会把迁移中的控制面默认暴露到公网。

## 人工步骤 / Deferred Items

- 需要人工重新录入或重绑 provider/channel credentials，不能直接带入旧明文。
- 需要人工重做 Telegram / Discord pairing。
- 需要人工确认 `cron/jobs.json` 到 OctoAgent action registry 的映射，尤其是旧 job payload 中的 agent / session target 语义。
- browser extension / canvas / 非 M3 surface 不在本轮迁移范围内。

## Rollback 方案

- 保留 `_references/openclaw-snapshot/` 作为只读基线。
- OctoAgent 迁移使用单独 project / workspace，不覆盖原始 snapshot。
- 若导入结果不满足预期，可删除对应 OctoAgent project bindings / import runs / automation jobs，回退到 rehearsal 前状态。
