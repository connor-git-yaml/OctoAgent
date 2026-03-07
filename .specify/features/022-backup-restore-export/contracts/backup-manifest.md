# Contract: Backup Bundle & Manifest

**Feature**: `022-backup-restore-export`
**Created**: 2026-03-07
**Traces to**: FR-003, FR-004, FR-005, FR-007, FR-010

---

## 契约范围

本文定义 backup bundle 的目录布局、`manifest.json` 的最小字段，以及 `RestorePlan`/`ExportManifest` 对 bundle 元数据的消费方式。

---

## 1. Bundle Layout

```text
octoagent-backup-YYYYmmdd-HHMMSS.zip
├── manifest.json
├── sqlite/
│   └── octoagent.db
├── config/
│   ├── octoagent.yaml
│   └── litellm-config.yaml
└── artifacts/
    └── ...
```

### 默认排除

- `.env`
- `.env.litellm`
- `.venv`
- `node_modules`
- `__pycache__`
- `.pytest_cache`

---

## 2. `manifest.json`

### 最小结构

```json
{
  "manifest_version": 1,
  "bundle_id": "01J...",
  "created_at": "2026-03-07T12:00:00Z",
  "source_project_root": "/abs/path",
  "scopes": ["sqlite", "artifacts", "config", "chats"],
  "files": [
    {
      "scope": "sqlite",
      "relative_path": "sqlite/octoagent.db",
      "kind": "file",
      "required": true,
      "size_bytes": 12345,
      "sha256": "..."
    }
  ],
  "excluded_paths": [".env", ".env.litellm"],
  "sensitivity_level": "metadata_only",
  "warnings": [],
  "notes": []
}
```

### 规则

- `manifest_version` 未识别时，`restore dry-run` 必须阻塞
- `sqlite/octoagent.db` 缺失时，`restore dry-run` 必须阻塞
- `excluded_paths` 必须显式列出默认排除项
- `sensitivity_level` 必须可用于 CLI/Web 提示

---

## 3. `RestorePlan`

### 最小结构

```json
{
  "bundle_path": "/abs/path/data/backups/foo.zip",
  "target_root": "/abs/path/project",
  "compatible": false,
  "checked_at": "2026-03-07T12:05:00Z",
  "manifest_version": 1,
  "restore_items": [],
  "conflicts": [
    {
      "conflict_type": "path_exists",
      "severity": "blocking",
      "target_path": "/abs/path/project/octoagent.yaml",
      "message": "目标配置已存在",
      "suggested_action": "改用空目录或先手动备份现有配置"
    }
  ],
  "warnings": [],
  "next_actions": ["修复 blocking conflicts 后重新运行 octo restore dry-run"]
}
```

### 规则

- `compatible=false` 表示至少存在一个 blocking conflict
- `conflicts` 必须是结构化数组，不能只输出纯文本
- `next_actions` 至少包含一条可执行建议

---

## 4. `ExportManifest`

### 最小结构

```json
{
  "export_id": "01J...",
  "created_at": "2026-03-07T12:10:00Z",
  "output_path": "/abs/path/data/exports/chats.json",
  "filters": {
    "task_id": null,
    "thread_id": "task-123",
    "since": null,
    "until": null
  },
  "tasks": [
    {
      "task_id": "01J...",
      "thread_id": "task-123",
      "title": "hello",
      "status": "SUCCEEDED",
      "created_at": "2026-03-07T11:00:00Z"
    }
  ],
  "event_count": 24,
  "artifact_refs": ["artifact-1", "artifact-2"]
}
```

### 规则

- 空结果允许，`tasks=[]` / `event_count=0` 不算失败
- `filters` 必须完整保留输入边界
- `artifact_refs` 只要求元数据引用，不强制内联 artifact 正文
