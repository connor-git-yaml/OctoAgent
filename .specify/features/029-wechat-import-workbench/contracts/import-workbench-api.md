# Contract: Import Workbench Control Plane API

**Feature**: `029-wechat-import-workbench`  
**Created**: 2026-03-08  
**Traces to**: FR-005 ~ FR-012, FR-018 ~ FR-021

---

## 契约范围

本文定义 029 在现有 control-plane 上新增的最小 import workbench 资源与动作。

---

## 1. Canonical Resources

### 1.1 `GET /api/control/resources/import-workbench`

返回 `ImportWorkbenchDocument`。

#### 200 响应示意

```json
{
  "resource_type": "import_workbench",
  "resource_id": "imports:workbench",
  "active_project_id": "project-default",
  "active_workspace_id": "workspace-default",
  "summary": {
    "source_count": 1,
    "recent_run_count": 2,
    "resume_available_count": 1,
    "warning_count": 1,
    "error_count": 0
  },
  "sources": [],
  "recent_runs": [],
  "resume_entries": [],
  "warnings": []
}
```

### 1.2 `GET /api/control/resources/import-sources/{source_id}`

返回 `ImportSourceDocument`。

### 1.3 `GET /api/control/resources/import-runs/{run_id}`

返回 `ImportRunDocument`。

### 规则

- resources 必须可被 snapshot 聚合
- recent runs / resume entries 必须是 durable projection，而不是一次性 action cache

---

## 2. Canonical Actions

### 2.1 `import.source.detect`

#### 请求

```json
{
  "action_id": "import.source.detect",
  "surface": "web",
  "params": {
    "source_type": "wechat",
    "input_path": "/path/to/export",
    "media_root": "/path/to/media",
    "format_hint": "html"
  }
}
```

#### 结果

- `completed`: 返回 `ImportSourceDocument`
- `rejected`: 输入不可识别或 source type 不受支持

### 2.2 `import.preview`

#### 请求

```json
{
  "action_id": "import.preview",
  "params": {
    "source_id": "wechat-source-001",
    "mapping_id": "mapping-001"
  }
}
```

#### 结果

- `completed`: 返回 `ImportRunDocument` with `dry_run=true`
- `rejected`: mapping 缺失 / 输入非法

### 2.3 `import.mapping.save`

保存 `ImportMappingProfile`。

### 2.4 `import.run`

执行真实导入。

### 2.5 `import.resume`

从 `ImportResumeEntry` 恢复导入。

### 2.6 `import.report.inspect`

读取最近导入报告详情。

---

## 3. Action Result Rules

所有结果必须返回统一 `ActionResultEnvelope`：

```json
{
  "request_id": "01J...",
  "correlation_id": "01J...",
  "action_id": "import.preview",
  "status": "completed",
  "code": "IMPORT_PREVIEW_READY",
  "message": "已生成导入预览",
  "data": {},
  "resource_refs": [
    {
      "resource_type": "import_run",
      "resource_id": "import-run:01J..."
    }
  ]
}
```

### 最小错误码集合

- `IMPORT_SOURCE_UNSUPPORTED`
- `IMPORT_SOURCE_INVALID`
- `IMPORT_MAPPING_REQUIRED`
- `IMPORT_MAPPING_INVALID`
- `IMPORT_PREVIEW_FAILED`
- `IMPORT_RUN_FAILED`
- `IMPORT_RESUME_BLOCKED`
- `IMPORT_REPORT_NOT_FOUND`

---

## 4. Status Semantics

`ImportRunDocument.status` 最小状态集合：

- `preview`
- `ready_to_run`
- `running`
- `failed`
- `action_required`
- `resume_available`
- `completed`
- `partial_success`

### 规则

- preview 必须显式标明 `dry_run=true`
- 存在 warnings 但主流程完成时，可以是 `completed`
- 部分附件/materialization/MemU sync 失败时，应为 `partial_success`

---

## 5. Snapshot Integration

`GET /api/control/snapshot` 中应聚合 import workbench 摘要：

- `resources.imports` 或等价 key
- `registry` 中包含 import actions

### 规则

- 029 不允许前端只靠 `POST action -> 读 result` 工作
- workbench state 必须能被 snapshot / resources 重新构建

---

## 6. 禁止行为

- 不允许只保留 `import.run` 单动作而没有 workbench resource
- 不允许 preview 产生持久化副作用
- 不允许 recent runs / resume 信息只存在前端本地状态
