# Data Model: Feature 022 — Backup/Restore + Export + Recovery Drill

**Feature**: `022-backup-restore-export`
**Created**: 2026-03-07
**Source**: `spec.md` FR-001 ~ FR-016，Key Entities 节

---

## 实体总览

| 实体 | 对应模型 | 持久化位置 | 说明 |
|---|---|---|---|
| Backup Bundle | `BackupBundle` | `data/backups/*.zip` + `latest-backup.json` | 一次可迁移备份的摘要与产物 |
| Backup Manifest | `BackupManifest` | bundle 内 `manifest.json` | bundle 的结构化元数据 |
| Backup File Entry | `BackupFileEntry` | `BackupManifest.files[]` | bundle 内单文件/目录条目 |
| Restore Plan | `RestorePlan` | 运行时结果 + `recovery-drill.json` | restore dry-run 的结构化计划 |
| Restore Conflict | `RestoreConflict` | `RestorePlan.conflicts[]` | 覆盖风险、缺失文件、版本不兼容等 |
| Export Manifest | `ExportManifest` | `data/exports/*.json` | chats/session 导出结果 |
| Recovery Drill Record | `RecoveryDrillRecord` | `data/ops/recovery-drill.json` | 最近一次 dry-run 验证状态 |
| Recovery Summary | `RecoverySummary` | API 响应 | Web/CLI 共用的最新恢复准备度摘要 |

---

## 1. 枚举

```python
class BackupScope(StrEnum):
    SQLITE = "sqlite"
    ARTIFACTS = "artifacts"
    CONFIG = "config"
    CHATS = "chats"


class SensitivityLevel(StrEnum):
    NONE = "none"
    METADATA_ONLY = "metadata_only"
    OPERATOR_SENSITIVE = "operator_sensitive"


class RestoreConflictSeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class RestoreConflictType(StrEnum):
    PATH_EXISTS = "path_exists"
    MISSING_REQUIRED_FILE = "missing_required_file"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    TARGET_UNWRITABLE = "target_unwritable"
    INVALID_BUNDLE = "invalid_bundle"


class RecoveryDrillStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
```

---

## 2. BackupFileEntry — bundle 条目

```python
class BackupFileEntry(BaseModel):
    scope: BackupScope
    relative_path: str = Field(min_length=1)
    kind: Literal["file", "directory"]
    required: bool = True
    size_bytes: int = 0
    sha256: str = ""
```

**说明**:
- `relative_path` 一律相对 bundle 根，如 `sqlite/octoagent.db`
- `required=False` 用于可选目录或空结果导出
- `sha256` 对文件有效，目录可为空

---

## 3. BackupManifest — bundle 元数据

```python
class BackupManifest(BaseModel):
    manifest_version: int = 1
    bundle_id: str
    created_at: datetime
    source_project_root: str
    scopes: list[BackupScope]
    files: list[BackupFileEntry]
    warnings: list[str] = Field(default_factory=list)
    excluded_paths: list[str] = Field(default_factory=list)
    sensitivity_level: SensitivityLevel = SensitivityLevel.METADATA_ONLY
    notes: list[str] = Field(default_factory=list)
```

**约束**:
- 必须包含 `sqlite/octoagent.db`
- config metadata 至少包含 `config/octoagent.yaml`
- `excluded_paths` 必须显式写出默认排除的 `.env` / `.env.litellm`

---

## 4. BackupBundle — 最近 backup 摘要

```python
class BackupBundle(BaseModel):
    bundle_id: str
    output_path: str
    created_at: datetime
    size_bytes: int
    manifest: BackupManifest
```

**持久化**:
- bundle 本体：`data/backups/*.zip`
- 最近一次摘要：`data/ops/latest-backup.json`

---

## 5. RestoreConflict — 恢复前问题

```python
class RestoreConflict(BaseModel):
    conflict_type: RestoreConflictType
    severity: RestoreConflictSeverity
    target_path: str = ""
    message: str
    suggested_action: str = ""
```

**示例**:
- `PATH_EXISTS` + `BLOCKING`: 目标已有 `octoagent.yaml`
- `MISSING_REQUIRED_FILE` + `BLOCKING`: bundle 缺少 `sqlite/octoagent.db`
- `CHECKSUM_MISMATCH` + `BLOCKING`: manifest 与实际 bundle 不一致
- `TARGET_UNWRITABLE` + `WARNING`: 默认输出目录不可写，建议改 `--output`

---

## 6. RestorePlan — dry-run 计划

```python
class RestorePlan(BaseModel):
    bundle_path: str
    target_root: str
    compatible: bool
    checked_at: datetime
    manifest_version: int | None = None
    restore_items: list[BackupFileEntry] = Field(default_factory=list)
    conflicts: list[RestoreConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
```

**判定规则**:
- 任意 `severity=BLOCKING` => `compatible=False`
- 仅 warnings 时 => `compatible=True`

**持久化**:
- 最近一次结果写入 `data/ops/recovery-drill.json`

---

## 7. ExportFilter / ExportManifest — chats 导出

```python
class ExportFilter(BaseModel):
    task_id: str | None = None
    thread_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class ExportTaskRef(BaseModel):
    task_id: str
    thread_id: str
    title: str
    status: str
    created_at: datetime


class ExportManifest(BaseModel):
    export_id: str
    created_at: datetime
    output_path: str
    filters: ExportFilter
    tasks: list[ExportTaskRef] = Field(default_factory=list)
    event_count: int = 0
    artifact_refs: list[str] = Field(default_factory=list)
```

**约束**:
- 允许导出空结果；空结果不是错误
- `filters` 至少完整记录本次筛选边界
- `artifact_refs` 只记录元数据引用，不强制复制 artifact 正文

---

## 8. RecoveryDrillRecord — 最近一次恢复验证

```python
class RecoveryDrillRecord(BaseModel):
    status: RecoveryDrillStatus = RecoveryDrillStatus.NOT_RUN
    checked_at: datetime | None = None
    bundle_path: str = ""
    summary: str = ""
    failure_reason: str = ""
    remediation: list[str] = Field(default_factory=list)
    plan: RestorePlan | None = None
```

**语义**:
- `NOT_RUN`: 尚未执行任何 dry-run
- `PASSED`: 最近一次 dry-run 无 blocking conflicts
- `FAILED`: 最近一次 dry-run 存在 blocking conflicts 或 bundle 无效

---

## 9. RecoverySummary — API / UI 摘要

```python
class RecoverySummary(BaseModel):
    latest_backup: BackupBundle | None = None
    latest_recovery_drill: RecoveryDrillRecord | None = None
    ready_for_restore: bool = False
```

**计算规则**:
- `latest_recovery_drill is None` => `ready_for_restore=False`
- `latest_recovery_drill.status == PASSED` => `ready_for_restore=True`
- 其他情况 => `False`

---

## 10. 审计事件（可选 Event Store 接入）

若在 022 内接入现有 Event Store，需要以下 payload：

```python
class BackupLifecyclePayload(BaseModel):
    bundle_id: str
    output_path: str
    scope_summary: list[str]
    status: Literal["started", "completed", "failed"]
    message: str = ""
```

对应 `EventType`：
- `BACKUP_STARTED`
- `BACKUP_COMPLETED`
- `BACKUP_FAILED`

说明：
- 由于现有 Event Store 强制绑定 `task_id`，此类事件必须依附 dedicated operational task；
- 若本阶段暂不接 Event Store，payload 结构仍应保留，用于状态文件与结构化日志统一。

---

## 11. 持久化路径约定

```text
project_root/
├── data/
│   ├── backups/
│   │   └── octoagent-backup-*.zip
│   ├── exports/
│   │   └── octoagent-chat-export-*.json
│   └── ops/
│       ├── latest-backup.json
│       └── recovery-drill.json
```

**原子写入策略**:
- 所有 `data/ops/*.json` 使用临时文件 + `os.replace()`
- JSON 损坏时备份为 `*.corrupted`

---

## 12. 与其他 Feature 的边界

- **与 021**：只消费 task/event/artifact 最小投影，不使用 import cursor/window 语义
- **与 020**：不接 memory/vault 恢复
- **与 023**：023 读取 `latest-backup` + `recovery-drill` 判断是否具备可恢复能力
