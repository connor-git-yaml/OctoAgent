# Spec Review: Feature 023 — M2 Integration Acceptance

**特性分支**: `codex/feat-023-m2-integration-acceptance`  
**审查日期**: 2026-03-07  
**审查范围**: FR-001 ~ FR-016

## 结论

- 结论: **PASS**
- 说明: 023 已把 M2 从“分段 Feature 可用”收束成“用户可验证的联合闭环”，并保持在既有 contract 和既有产品面内完成。

## FR 对齐检查

| FR | 状态 | 证据 |
|---|---|---|
| FR-001 | ✅ | `doctor.py` 对 YAML runtime config 存在时的 `.env` / `.env.litellm` 改为 `SKIP`，首次使用主路径不再被旧前置阻塞 |
| FR-002 | ✅ | `config_bootstrap.py` / `config_commands.py` 新增 Telegram bootstrap 参数与 CLI 入口 |
| FR-003 | ✅ | `telegram_verifier.py` 的 `first_message` 改为检测真实 Telegram 入站 task |
| FR-004 | ✅ | `test_first_use_acceptance_config_gateway_pairing_and_onboarding_resume` 覆盖首次 working flow |
| FR-005 | ✅ | `test_operator_parity_acceptance_web_and_telegram_share_same_pairing_item` + gateway parity 回归证明同 item 语义一致 |
| FR-006 | ✅ | 同一 pairing item Web 处理后，Telegram callback 返回 `already_handled` 且不重复副作用 |
| FR-007 | ✅ | `test_a2a_task_message_can_drive_worker_runtime_and_return_result` 覆盖 TASK -> runtime -> RESULT |
| FR-008 | ✅ | `test_a2a_task_message_timeout_maps_to_error_and_failed_state` 补齐非成功路径 |
| FR-009 | ✅ | `packages/protocol/tests/test_a2a_models.py` + `apps/gateway/tests/test_execution_api.py` 继续覆盖 interactive/input-required 映射 |
| FR-010 | ✅ | `backup_service.py` 允许显式导出 `ops-chat-import`，使导入结果进入 export chain |
| FR-011 | ✅ | 023 durability 验收覆盖 artifact/export/backup/restore summary |
| FR-012 | ✅ | `contracts/m2-acceptance-matrix.md` 已冻结五个 M2 gate |
| FR-013 | ✅ | 当前 `verification/verification-report.md` 已记录命令、结论、remaining risks、out-of-scope |
| FR-014 | ✅ | 未新增新业务域、destructive restore、外部新 API 或新控制面 |
| FR-015 | ✅ | 真实本地 gateway/store/task runner/memory/recovery 参与验收，外部 Telegram/LLM 仅做 fake client 替身 |
| FR-016 | ✅ | 所有补丁都在现有 CLI/runtime/export contract 内收敛，没有重写主语义 |

## 边界与说明

- 023 的 operator parity 主证据是 pairing item；approval / retry / cancel / ack 的多动作矩阵目前通过 017 的 gateway 回归补足。
- 023 的 A2A interactive 证据由既有 018/019 测试共同支撑；本特性新增的是 TASK 成功链和 timeout 失败链。
- `restore` 仍保持 dry-run 范围，符合 022 的边界，不在 023 内扩展 destructive apply。
