# Contract: M2 Acceptance Matrix

**Feature**: `023-m2-integration-acceptance`  
**Created**: 2026-03-07  
**Traces to**: FR-004 ~ FR-013

---

## 契约范围

本文定义 023 的单一验收事实源：

- M2 五个 gate
- 对应的联合验收场景
- 期望证据
- 最低通过标准

023 的测试、验收报告和里程碑结论必须以本矩阵为准。

---

## 1. Gate -> Scenario 映射

| Gate | 场景 ID | 场景名称 | 主要 surface | 最低通过标准 |
|---|---|---|---|---|
| `GATE-M2-ONBOARD` | `SCN-001` | 首次 working flow | CLI + provider + gateway + web | 新项目目录完成 config/doctor/onboard/pairing/first inbound task |
| `GATE-M2-CHANNEL-PARITY` | `SCN-002` | Operator parity | Web + Telegram | pairing/approval/retry/cancel/ack 至少具备联合证据 |
| `GATE-M2-A2A-CONTRACT` | `SCN-003` | A2A -> runtime | protocol + runtime | 至少一条成功路径和一条非成功路径 |
| `GATE-M2-MEMORY-GOVERNANCE` | `SCN-004` | Import -> memory -> recovery | CLI + memory + recovery | 导入结果进入 export/backup/restore 证据链 |
| `GATE-M2-RESTORE` | `SCN-005` | Recovery proof | CLI + gateway ops summary | restore dry-run 结果和 recovery summary 可追溯 |

---

## 2. Scenario 定义

### SCN-001 首次 working flow

**目标**:

- `octo config init`
- `octo doctor --live`
- `octo onboard --channel telegram`
- Telegram pairing
- 首条 Telegram 入站 task 创建

**关键证据**:

- `octoagent.yaml` / `litellm-config.yaml`
- `DoctorReport`
- `OnboardingSession`
- `telegram-state.json`
- Telegram task / events

**失败判定**:

- 缺 `.env` 导致主路径阻塞
- Telegram channel 仍需手工改 YAML
- onboarding 在只验证 bot 出站时就标记 ready

### SCN-002 Operator parity

**目标**:

- 同一 item 在 Web / Telegram 两端处理一致

**至少覆盖动作**:

- `APPROVE_PAIRING`
- `APPROVE_ONCE` / `DENY`
- `RETRY_TASK`
- `CANCEL_TASK`
- `ACK_ALERT`

**关键证据**:

- `OperatorActionResult`
- `OPERATOR_ACTION_RECORDED`
- item_id / outcome / handled_at

**失败判定**:

- 两端 item_id 不一致
- 一端成功另一端重复执行副作用
- 审计链分叉

### SCN-003 A2A -> runtime

**目标**:

- `DispatchEnvelope`
- `A2A TASK`
- runtime 执行
- `RESULT/ERROR`

**关键证据**:

- `A2AMessage`
- `WorkerResult`
- task status
- `WORKER_DISPATCHED` / `WORKER_RETURNED`

**失败判定**:

- 协议消息无法真实进入执行面
- runtime 状态与 A2A state 漂移

### SCN-004 Import -> memory -> recovery

**目标**:

- `octo import chats`
- Memory commit / fragment
- `octo export chats`
- `octo backup create`
- `octo restore dry-run`

**关键证据**:

- `ImportReport`
- memory fragments / SoR
- export manifest
- backup bundle
- recovery drill summary

**失败判定**:

- 导入结果未进入 export/backup
- restore dry-run 无法证明导入数据被覆盖

### SCN-005 Recovery proof

**目标**:

- 用 022 的既有能力证明系统具备恢复准备度

**关键证据**:

- `RestorePlan`
- `RecoverySummary`
- `latest-backup.json`
- `recovery-drill.json`

**失败判定**:

- 无法把最近一次 restore dry-run 与当前 backup 关联起来
- CLI / gateway summary 状态不一致

---

## 3. 通过规则

### Gate 级通过规则

每个 gate 通过必须满足：

1. 至少一条自动化场景通过；
2. 有明确本地证据；
3. 若存在已知边界，必须在验收报告中列为 remaining risk。

### Feature 级通过规则

023 通过必须满足：

1. `SCN-001` ~ `SCN-005` 均通过；
2. 未超出 023 定义范围；
3. 验收报告已生成；
4. 剩余风险已显式列出。

---

## 4. 禁止行为

- 不得以“已有局部测试”替代联合验收场景
- 不得只写文字说明而缺少自动化证据
- 不得为了通过矩阵而新增无关业务能力
- 不得隐去 remaining risks

---

## 5. 备注

本矩阵是 023 的 contract 文档；后续 `verification/verification-report.md` 必须逐项回填这里的 gate / scenario / evidence，而不是另写一套口径。
