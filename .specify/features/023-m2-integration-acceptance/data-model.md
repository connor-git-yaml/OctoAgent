# Data Model: Feature 023 — M2 Integration Acceptance

**Feature**: `023-m2-integration-acceptance`  
**Created**: 2026-03-07  
**Source**: `spec.md` FR-001 ~ FR-016，Key Entities 节

---

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Acceptance Scenario | `AcceptanceScenario` | 测试代码 / 验收矩阵 | 一条可独立执行的联合验收路径 |
| Acceptance Step | `AcceptanceStep` | 测试代码 / 验收矩阵 | 场景中的单个动作和期望证据 |
| Acceptance Evidence | `AcceptanceEvidence` | 验收报告 | 某条验收路径的本地证据 |
| First-Use Checkpoint | `FirstUseCheckpoint` | 测试断言 / 验收报告 | 首次使用链的阶段性完成标志 |
| Operator Parity Record | `OperatorParityRecord` | 测试断言 / 审计摘要 | 同一 operator item 在多端处理的结果 |
| A2A Execution Trace | `A2AExecutionTrace` | 测试断言 / 验收报告 | A2A 到 runtime 的协议-执行链证据 |
| Recovery Evidence Set | `RecoveryEvidenceSet` | 测试断言 / 验收报告 | 导入结果进入 export/backup/restore 的联合证据 |
| Verification Report Entry | `VerificationReportEntry` | `verification/verification-report.md` | 单个 M2 gate 的结论、命令与风险 |

---

## 1. AcceptanceScenario

```python
class AcceptanceScenario(BaseModel):
    scenario_id: str
    gate_id: str
    title: str
    priority: Literal["P0", "P1"]
    user_story: Literal["US1", "US2", "US3", "US4", "US5"]
    summary: str
    steps: list[AcceptanceStep]
    expected_evidence: list[str]
```

**说明**:

- `gate_id` 直接对应 `GATE-M2-*`
- 一个 Scenario 应可被单独执行和单独判定 PASS / FAIL
- `expected_evidence` 用于驱动验收报告输出

---

## 2. AcceptanceStep

```python
class AcceptanceStep(BaseModel):
    step_id: str
    actor: str
    surface: Literal["cli", "web", "telegram", "protocol", "runtime", "report"]
    action: str
    expected_outcome: str
    notes: str = ""
```

**说明**:

- 023 的重点是跨 surface 联合验收，因此 `surface` 是关键字段
- `actor` 可是 `owner`、`system`、`telegram-user`、`operator-web` 等

---

## 3. AcceptanceEvidence

```python
class AcceptanceEvidence(BaseModel):
    evidence_id: str
    scenario_id: str
    kind: Literal["task", "event", "artifact", "report", "api_response", "cli_output"]
    ref: str
    summary: str
```

**说明**:

- 证据不要求新增数据库表，主要存在于测试断言与报告中
- `ref` 可以是 task_id、event type、artifact_id、文件路径或接口路径

---

## 4. FirstUseCheckpoint

```python
class FirstUseCheckpoint(BaseModel):
    checkpoint_id: str
    config_ready: bool = False
    doctor_ready: bool = False
    channel_ready: bool = False
    pairing_approved: bool = False
    inbound_task_detected: bool = False
    onboarding_ready: bool = False
```

**说明**:

- `inbound_task_detected` 是 023 新增的关键判据
- `onboarding_ready` 不能在 `inbound_task_detected=False` 时为 `True`

---

## 5. OperatorParityRecord

```python
class OperatorParityRecord(BaseModel):
    item_id: str
    item_kind: str
    handled_by_surface: Literal["web", "telegram"]
    outcome: str
    audit_event_type: str
    second_attempt_outcome: str | None = None
```

**说明**:

- 用于描述 “同一 item 在两端的处理一致性”
- `second_attempt_outcome` 主要用于 `already_handled` / `stale_state`

---

## 6. A2AExecutionTrace

```python
class A2AExecutionTrace(BaseModel):
    trace_id: str
    task_id: str
    dispatch_id: str
    task_message_id: str
    result_message_id: str | None = None
    error_message_id: str | None = None
    runtime_status: str
    a2a_state: str
```

**说明**:

- `runtime_status` 与 `a2a_state` 必须保持一致
- 成功路径使用 `result_message_id`
- 非成功路径使用 `error_message_id` 或等价字段

---

## 7. RecoveryEvidenceSet

```python
class RecoveryEvidenceSet(BaseModel):
    import_batch_id: str
    import_scope_id: str
    artifact_refs: list[str]
    fragment_ids: list[str]
    proposal_ids: list[str]
    export_manifest_id: str | None = None
    backup_bundle_id: str | None = None
    recovery_status: str | None = None
```

**说明**:

- 用于描述导入结果是否真正进入 recovery boundary
- `proposal_ids` 可为空，但若为空应明确是 fragment-only 路径

---

## 8. VerificationReportEntry

```python
class VerificationReportEntry(BaseModel):
    gate_id: str
    status: Literal["PASS", "FAIL", "PARTIAL"]
    scenarios: list[str]
    commands: list[str]
    key_evidence: list[str]
    remaining_risks: list[str]
```

**说明**:

- 023 的最终报告以 gate 为中心，而不是以文件或模块为中心
- `PARTIAL` 用于明确边界场景或已知限制

---

## 9. 不变量

1. 023 不新增独立持久化表；这些模型主要服务于 spec、测试与报告。
2. `FirstUseCheckpoint.onboarding_ready=True` 前，必须已经满足：
   - `config_ready=True`
   - `doctor_ready=True`
   - `pairing_approved=True`
   - `inbound_task_detected=True`
3. `OperatorParityRecord.item_id` 必须跨 surface 保持一致，不允许 Web / Telegram 各自派生不同主键。
4. `A2AExecutionTrace.runtime_status` 与 `a2a_state` 必须可通过既有 state mapper 追溯一致。
5. `RecoveryEvidenceSet` 中若存在 `backup_bundle_id`，则后续必须可关联到 `recovery_status` 或 `restore dry-run` 证据。

---

## 10. 与现有模型的关系

| 023 实体 | 依赖的既有模型 | 关系 |
|---|---|---|
| FirstUseCheckpoint | `OnboardingSession` / `DoctorReport` / `ChannelStepResult` | 从既有 DX 模型中提炼联合验收状态 |
| OperatorParityRecord | `OperatorActionResult` / `OPERATOR_ACTION_RECORDED` 事件 | 用于跨端对齐与报告输出 |
| A2AExecutionTrace | `DispatchEnvelope` / `A2AMessage` / `WorkerResult` | 连接协议层与执行层 |
| RecoveryEvidenceSet | `ImportReport` / `BackupBundle` / `ExportManifest` / `RestorePlan` | 连接导入、记忆与恢复链 |

---

## 结论

023 的 data model 不是新的产品主数据，而是“验收与报告主数据”。它们的作用是让 023 能用统一语言描述：

- 首次使用是否真的闭环；
- 多渠道控制是否真的等价；
- A2A 是否真的进入执行面；
- 导入数据是否真的进入恢复边界；
- M2 是否真的具备可以对外宣布的验收证据。
