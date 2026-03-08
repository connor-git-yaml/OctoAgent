# Data Model: Feature 024 — Installer + Updater + Doctor/Migrate

**Feature**: `024-installer-updater-doctor-migrate`
**Created**: 2026-03-08
**Source**: `spec.md` FR-001 ~ FR-020，Key Entities 节

---

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Install Attempt | `InstallAttempt` | 运行时返回，可选落盘 | 一次 installer 执行结果 |
| Managed Runtime Descriptor | `ManagedRuntimeDescriptor` | `data/ops/managed-runtime.json` | 受 installer 管理的 runtime 事实源 |
| Runtime State Snapshot | `RuntimeStateSnapshot` | `data/ops/runtime-state.json` | 当前 gateway 运行态快照 |
| Update Attempt | `UpdateAttempt` | `data/ops/latest-update.json` / history | 一次 update 全流程实例 |
| Update Phase Result | `UpdatePhaseResult` | `UpdateAttempt.phases[]` | 单阶段执行状态 |
| Migration Step Result | `MigrationStepResult` | `UpdatePhaseResult.migration_steps[]` | migrate registry 单步结果 |
| Upgrade Failure Report | `UpgradeFailureReport` | `UpdateAttempt.failure_report` | 升级失败共享报告 |
| Update Attempt Summary | `UpdateAttemptSummary` | API / CLI 读取对象 | Web recovery panel 主消费对象 |

---

## 1. 枚举

```python
class InstallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ACTION_REQUIRED = "ACTION_REQUIRED"


class UpdateTriggerSource(StrEnum):
    CLI = "cli"
    WEB = "web"
    SYSTEM = "system"


class UpdateOverallStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ACTION_REQUIRED = "ACTION_REQUIRED"


class UpdatePhaseName(StrEnum):
    PREFLIGHT = "preflight"
    MIGRATE = "migrate"
    RESTART = "restart"
    VERIFY = "verify"


class UpdatePhaseStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class RuntimeManagementMode(StrEnum):
    MANAGED = "managed"
    UNMANAGED = "unmanaged"


class RestartStrategy(StrEnum):
    COMMAND = "command"
    SELF_SIGNAL = "self_signal"


class MigrationStepKind(StrEnum):
    WORKSPACE_SYNC = "workspace_sync"
    CONFIG_MIGRATE = "config_migrate"
    FRONTEND_BUILD = "frontend_build"
    DATA_MIGRATE = "data_migrate"


class VerifyStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
```

说明：
- `ACTION_REQUIRED` 表示系统没有崩，但需要用户介入，例如未托管 runtime。
- `BLOCKED` 只用于阶段级别，表示 preflight 已阻塞后续阶段。

---

## 2. InstallAttempt — 安装执行结果

```python
class InstallAttempt(BaseModel):
    install_id: str
    project_root: str
    started_at: datetime
    completed_at: datetime | None = None
    status: InstallStatus
    dependency_checks: list[str] = Field(default_factory=list)
    actions_completed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    runtime_descriptor_path: str = ""
```

**语义**:
- installer 默认返回该结果；
- MVP 可不强制落盘，但在测试中必须结构化可断言；
- 成功安装后 `runtime_descriptor_path` 必须指向 canonical descriptor。

---

## 3. ManagedRuntimeDescriptor — 托管 runtime 事实源

```python
class ManagedRuntimeDescriptor(BaseModel):
    descriptor_version: int = 1
    project_root: str
    runtime_mode: RuntimeManagementMode = RuntimeManagementMode.MANAGED
    restart_strategy: RestartStrategy = RestartStrategy.COMMAND
    start_command: list[str]
    verify_url: str
    verify_profile: str = "core"
    workspace_sync_command: list[str] = Field(default_factory=list)
    frontend_build_command: list[str] = Field(default_factory=list)
    environment_overrides: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
```

**持久化文件**: `data/ops/managed-runtime.json`

**规则**:
- `start_command` 必须完整、可执行；
- descriptor 不得保存 secrets 明文；
- `workspace_sync_command` / `frontend_build_command` 允许为空，表示该步骤跳过；
- `verify_url` 默认应指向本地 gateway `/ready`。

---

## 4. RuntimeStateSnapshot — 当前运行态

```python
class RuntimeStateSnapshot(BaseModel):
    pid: int
    project_root: str
    started_at: datetime
    heartbeat_at: datetime
    verify_url: str
    management_mode: RuntimeManagementMode
    active_attempt_id: str | None = None
```

**持久化文件**: `data/ops/runtime-state.json`

**规则**:
- gateway 启动时必须写入；
- restart 前应读取其中的 `pid`；
- `heartbeat_at` 可在启动/关闭时刷新，不要求高频心跳。

---

## 5. UpdatePhaseResult — 单阶段执行结果

```python
class UpdatePhaseResult(BaseModel):
    phase: UpdatePhaseName
    status: UpdatePhaseStatus = UpdatePhaseStatus.NOT_STARTED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    migration_steps: list[MigrationStepResult] = Field(default_factory=list)
```

**规则**:
- 四个阶段必须始终存在于 `UpdateAttempt.phases`；
- 未进入的阶段保持 `NOT_STARTED`；
- migrate 阶段才允许携带 `migration_steps`。

---

## 6. MigrationStepResult — migrate registry 单步结果

```python
class MigrationStepResult(BaseModel):
    step_id: str
    kind: MigrationStepKind
    description: str
    status: UpdatePhaseStatus
    summary: str = ""
    applied_at: datetime | None = None
```

**说明**:
- 首批 registry 步骤预计包括：workspace sync、config migrate、frontend build；
- 未来可扩展 DB schema / service entrypoint migration，而不改 `UpdateAttempt` 顶层结构。

---

## 7. UpgradeFailureReport — 升级失败报告

```python
class UpgradeFailureReport(BaseModel):
    attempt_id: str
    failed_phase: UpdatePhaseName
    last_successful_phase: UpdatePhaseName | None = None
    message: str
    instance_state: str
    suggested_actions: list[str] = Field(default_factory=list)
    latest_backup_path: str = ""
    latest_recovery_status: str = ""
```

**规则**:
- 当 `UpdateAttempt.overall_status == FAILED` 或 `ACTION_REQUIRED` 时，必须存在；
- `instance_state` 用于区分“未迁移”“已迁移未重启”“已重启未验证”等情形；
- 恢复线索来自 022 recovery baseline。

---

## 8. UpdateAttempt — 一次完整升级尝试

```python
class UpdateAttempt(BaseModel):
    attempt_id: str
    trigger_source: UpdateTriggerSource
    dry_run: bool = False
    management_mode: RuntimeManagementMode
    project_root: str
    started_at: datetime
    completed_at: datetime | None = None
    overall_status: UpdateOverallStatus = UpdateOverallStatus.PENDING
    current_phase: UpdatePhaseName = UpdatePhaseName.PREFLIGHT
    phases: list[UpdatePhaseResult]
    failure_report: UpgradeFailureReport | None = None
```

**持久化文件**:
- `data/ops/latest-update.json`
- 可选 `data/ops/update-history/{attempt_id}.json`
- 运行中还会同步到 `data/ops/active-update.json`

**不变量**:
- `phases` 必须包含 4 个固定 phase；
- dry-run 成功时允许 `overall_status=SUCCEEDED` 且 restart/verify 为 `SKIPPED`；
- 非 dry-run 若 `restart` 未成功，`verify` 不得标记 `SUCCEEDED`。

---

## 9. UpdateAttemptSummary — CLI / Web 主消费对象

```python
class UpdateAttemptSummary(BaseModel):
    attempt_id: str = ""
    dry_run: bool = False
    overall_status: UpdateOverallStatus | None = None
    current_phase: UpdatePhaseName | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    management_mode: RuntimeManagementMode = RuntimeManagementMode.UNMANAGED
    phases: list[UpdatePhaseResult] = Field(default_factory=list)
    failure_report: UpgradeFailureReport | None = None
```

**用途**:
- `GET /api/ops/update/status`
- CLI 最终摘要
- RecoveryPanel 状态展示与轮询

**规则**:
- 若尚未执行任何 update，允许返回空 summary；
- Web 前端只依赖该 summary，不直接读取内部 store 文件。
