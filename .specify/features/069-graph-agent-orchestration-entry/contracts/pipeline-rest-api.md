# 契约：Pipeline REST API

**Feature**: 065
**模块**: `octoagent/apps/gateway/src/octoagent/gateway/routes/pipelines.py`
**前缀**: `/api/pipelines` 和 `/api/pipeline-runs`

---

## 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/pipelines` | 列出所有已注册 Pipeline |
| GET | `/api/pipelines/{pipeline_id}` | 获取单个 Pipeline 详情 |
| POST | `/api/pipelines/refresh` | 触发 PipelineRegistry 重新扫描 |
| GET | `/api/pipeline-runs` | 列出 Pipeline run |
| GET | `/api/pipeline-runs/{run_id}` | 获取单个 run 详情 |

---

## 响应模型

### PipelineItemResponse

```python
class PipelineItemResponse(BaseModel):
    """GET /api/pipelines 列表元素。"""
    pipeline_id: str
    description: str
    version: str
    tags: list[str]
    trigger_hint: str
    source: str                  # "builtin" / "user" / "project"
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
```

### PipelineListResponse

```python
class PipelineListResponse(BaseModel):
    """GET /api/pipelines 响应体。"""
    items: list[PipelineItemResponse]
    total: int
```

### PipelineDetailResponse

```python
class PipelineDetailResponse(PipelineItemResponse):
    """GET /api/pipelines/{pipeline_id} 响应体。"""
    author: str
    source_path: str
    content: str                          # PIPELINE.md Markdown body
    nodes: list[PipelineNodeResponse]     # 完整节点拓扑
    entry_node: str

class PipelineNodeResponse(BaseModel):
    """Pipeline 节点详情。"""
    node_id: str
    label: str
    node_type: str             # skill / tool / transform / gate
    handler_id: str
    next_node_id: str | None
    retry_limit: int
    timeout_seconds: float | None
```

### PipelineRunItemResponse

```python
class PipelineRunItemResponse(BaseModel):
    """GET /api/pipeline-runs 列表元素。"""
    run_id: str
    pipeline_id: str
    task_id: str
    work_id: str
    status: str                # created / running / waiting_input / waiting_approval / ...
    current_node_id: str
    pause_reason: str
    created_at: str            # ISO 8601
    updated_at: str
    completed_at: str | None
```

### PipelineRunListResponse

```python
class PipelineRunListResponse(BaseModel):
    """GET /api/pipeline-runs 响应体。"""
    items: list[PipelineRunItemResponse]
    total: int
    page: int
    page_size: int
```

### PipelineRunDetailResponse

```python
class PipelineRunDetailResponse(PipelineRunItemResponse):
    """GET /api/pipeline-runs/{run_id} 响应体。"""
    state_snapshot: dict[str, Any]
    input_request: dict[str, Any]
    approval_request: dict[str, Any]
    metadata: dict[str, Any]
    retry_cursor: dict[str, int]
    checkpoints: list[PipelineCheckpointResponse]

class PipelineCheckpointResponse(BaseModel):
    """Checkpoint 详情。"""
    checkpoint_id: str
    node_id: str
    status: str
    replay_summary: str
    retry_count: int
    created_at: str
```

---

## 端点详细说明

### GET /api/pipelines

**查询参数**: 无

**响应**: `200 OK` → `PipelineListResponse`

**示例响应**:
```json
{
  "items": [
    {
      "pipeline_id": "deploy-staging",
      "description": "将代码部署到 staging 环境",
      "version": "1.0.0",
      "tags": ["deploy", "staging", "ci-cd"],
      "trigger_hint": "当用户要求部署到 staging 时使用",
      "source": "builtin",
      "input_schema": {
        "branch": {"type": "string", "description": "分支名", "required": true},
        "skip_tests": {"type": "boolean", "description": "跳过测试", "default": false}
      },
      "output_schema": {
        "deploy_url": {"type": "string", "description": "部署地址"}
      }
    }
  ],
  "total": 1
}
```

### GET /api/pipelines/{pipeline_id}

**路径参数**: `pipeline_id` (string)

**响应**:
- `200 OK` → `PipelineDetailResponse`
- `404 Not Found` → `{"error": "pipeline not found: 'xxx'"}`

### POST /api/pipelines/refresh

**请求体**: 无

**响应**: `200 OK` → `PipelineListResponse`（重新扫描后的完整列表）

### GET /api/pipeline-runs

**查询参数**:
| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `page` | int | 1 | 页码 |
| `page_size` | int | 20 | 每页数量（上限 100） |
| `pipeline_id` | string | — | 按 Pipeline ID 筛选 |
| `status` | string | — | 按状态筛选 |
| `task_id` | string | — | 按 Task ID 筛选 |

**响应**: `200 OK` → `PipelineRunListResponse`

### GET /api/pipeline-runs/{run_id}

**路径参数**: `run_id` (string)

**响应**:
- `200 OK` → `PipelineRunDetailResponse`
- `404 Not Found` → `{"error": "pipeline run not found: 'xxx'"}`

---

## 错误响应格式

与现有 `/api/skills` 保持一致：

```json
{
  "error": "human-readable error message"
}
```

HTTP 状态码：
- `404`：资源不存在
- `400`：参数无效
- `500`：内部错误
