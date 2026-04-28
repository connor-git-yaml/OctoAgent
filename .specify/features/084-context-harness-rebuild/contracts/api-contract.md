# API Contract: Feature 084 — Memory Candidates + Snapshots

> 5 个新增 REST API endpoint，服务于 Web UI Memory 候选面板（FR-8）和 SnapshotRecord 查询（FR-2.3）。

## GET /api/memory/candidates

返回 `pending` 状态候选列表（FR-8.1）。

**响应**：
```json
{
  "candidates": [
    {
      "id": "uuid",
      "fact_content": "孩子刚上小学了",
      "category": "family",
      "confidence": 0.85,
      "created_at": "2026-04-28T10:00:00Z",
      "expires_at": "2026-05-28T10:00:00Z",
      "source_turn_id": "turn-uuid"
    }
  ],
  "total": 3,
  "pending_count": 3
}
```

---

## POST /api/memory/candidates/{id}/promote

Accept 候选（直接写入或编辑后写入 USER.md），FR-8.2。

**请求体**：
```json
{
  "edited_content": "孩子今年上小学一年级（可选，编辑后内容）"
}
```

**响应**：
```json
{
  "success": true,
  "candidate_id": "uuid",
  "written_to_user_md": true,
  "edited": false,
  "event_id": "uuid"
}
```

---

## POST /api/memory/candidates/{id}/discard

Reject 候选（FR-8.2）。

**响应**：
```json
{
  "success": true,
  "candidate_id": "uuid",
  "status": "rejected"
}
```

---

## PUT /api/memory/candidates/bulk_discard

批量 reject 所有 pending 候选（FR-8.3）。

**请求体**：
```json
{
  "candidate_ids": ["uuid1", "uuid2"]
}
```

**响应**：
```json
{
  "rejected_count": 2,
  "success": true
}
```

---

## GET /api/snapshots/{tool_call_id}

按 tool_call_id 查询 SnapshotRecord（FR-2.3）。

**响应**：
```json
{
  "id": "uuid",
  "tool_call_id": "uuid",
  "result_summary": "已写入：姓名 Connor Lu，时区 Asia/Shanghai，职业工程师",
  "timestamp": "2026-04-28T10:00:00Z",
  "ttl_days": 30,
  "expires_at": "2026-05-28T10:00:00Z"
}
```

**错误响应（404）**：
```json
{
  "error": "snapshot_not_found",
  "tool_call_id": "uuid"
}
```
