# Tools Contract: Feature 084

> 工具接口契约，Constitution C3（Tools are Contracts）要求 schema 与代码签名一致。

## user_profile.update

**entrypoints**: `web`, `agent_runtime`, `telegram`  
**side_effect_level**: `irreversible`（replace/remove 经 Approval Gate；add 直接执行）

```json
{
  "name": "user_profile.update",
  "description": "写入/更新 USER.md 档案内容。支持 add（追加新条目）、replace（精确替换指定文本）、remove（删除指定条目）三种操作。replace 和 remove 为不可逆操作，将触发 Approval Gate 审批。",
  "input_schema": {
    "type": "object",
    "properties": {
      "operation": {
        "type": "string",
        "enum": ["add", "replace", "remove"],
        "description": "操作类型"
      },
      "content": {
        "type": "string",
        "description": "add 时：新条目内容；remove 时：同 target_text"
      },
      "old_text": {
        "type": "string",
        "description": "replace 时：被替换的原始文本（精确 substring 匹配）"
      },
      "target_text": {
        "type": "string",
        "description": "remove 时：要删除的目标文本（精确 substring 匹配）"
      }
    },
    "required": ["operation", "content"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "success": {"type": "boolean"},
      "written_content": {"type": "string", "description": "写入内容前 200 字符摘要（LLM 回显用）"},
      "blocked": {"type": "boolean"},
      "pattern_id": {"type": "string", "description": "Threat Scanner 命中的 pattern ID"},
      "approval_requested": {"type": "boolean", "description": "是否触发了 Approval Gate"}
    }
  }
}
```

---

## user_profile.read

**entrypoints**: `web`, `agent_runtime`, `telegram`  
**side_effect_level**: `none`

```json
{
  "name": "user_profile.read",
  "description": "读取 USER.md 当前内容（live state，非快照），返回 § 分隔符解析后的条目列表。",
  "input_schema": {"type": "object", "properties": {}},
  "output_schema": {
    "type": "object",
    "properties": {
      "entries": {
        "type": "array",
        "items": {"type": "string"},
        "description": "§ 分隔符解析后的条目列表"
      },
      "total_chars": {"type": "integer"},
      "char_limit": {"type": "integer", "description": "当前上限（50000）"}
    }
  }
}
```

---

## user_profile.observe

**entrypoints**: `web`, `agent_runtime`, `telegram`  
**side_effect_level**: `reversible`（写入 candidates，未直接写 USER.md）

```json
{
  "name": "user_profile.observe",
  "description": "Agent 在对话中发现用户新事实时，将候选事实写入 candidates 队列等待用户审核，不直接写入 USER.md。",
  "input_schema": {
    "type": "object",
    "properties": {
      "fact_content": {"type": "string", "description": "候选事实内容"},
      "source_turn_id": {"type": "string", "description": "来源对话轮次 ID"},
      "initial_confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "初始置信度（< 0.7 时不写入队列）"}
    },
    "required": ["fact_content", "source_turn_id", "initial_confidence"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "queued": {"type": "boolean"},
      "candidate_id": {"type": "string"},
      "reason": {"type": "string", "description": "未入队时的原因（如 low_confidence, queue_full）"}
    }
  }
}
```

---

## delegate_task

**entrypoints**: `agent_runtime`  
**side_effect_level**: `reversible`

```json
{
  "name": "delegate_task",
  "description": "将子任务派发给指定 Worker，主 Agent 立即返回（async 模式）或等待结果（sync 模式）。max_depth=2，max_concurrent_children=3。",
  "input_schema": {
    "type": "object",
    "properties": {
      "target_worker": {"type": "string", "description": "目标 Worker 名称"},
      "task_description": {"type": "string", "description": "任务描述"},
      "callback_mode": {"type": "string", "enum": ["async", "sync"]},
      "max_wait_seconds": {"type": "integer", "default": 300, "description": "sync 模式超时"}
    },
    "required": ["target_worker", "task_description", "callback_mode"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "task_id": {"type": "string"},
      "status": {"type": "string", "enum": ["spawned", "completed", "failed", "rejected"]},
      "result_summary": {"type": "string"},
      "error": {"type": "string"}
    }
  }
}
```
