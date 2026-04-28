# Tools Contract: Feature 084

> 工具接口契约，Constitution C3（Tools are Contracts）要求 schema 与代码签名一致。

## WriteResult Protocol（FR-2.4 / FR-2.7 / SC-012）

所有"写入型工具"（`tool_contract` 装饰器声明 `produces_write=True`）的 output_schema 必须以 `WriteResult` 为基础。`tool_contract` 装饰器在工具注册期通过 `typing.get_type_hints(handler, include_extras=True)` 解析 return annotation 并 enforce 此契约（必须能 eval `from __future__ import annotations` 引入的字符串注解），不符合则启动时 fail-fast。

```json
{
  "name": "WriteResult",
  "description": "所有写入型工具的统一返回契约。取代 USER.md 专属的 success/written_content 二字段，覆盖所有 produces_write=True 的工具。",
  "schema": {
    "type": "object",
    "required": ["status", "target"],
    "properties": {
      "status": {"type": "string", "enum": ["written", "skipped", "rejected", "pending"], "description": "pending 用于异步启动（如 mcp.install 启动 npm/pip job）"},
      "target": {"type": "string", "description": "写入目标的标识符（文件路径 / DB 表名 / 子任务 ID 等）"},
      "bytes_written": {"type": ["integer", "null"]},
      "preview": {"type": ["string", "null"], "description": "前 200 字符摘要（供 LLM 回显）"},
      "mtime_iso": {"type": ["string", "null"], "description": "ISO 8601 mtime（仅文件类工具）"},
      "reason": {"type": ["string", "null"], "description": "状态非 written 时的原因"}
    },
    "allOf": [
      {
        "comment": "条件必填：status != written 时 reason 必须为非空 string（与 Pydantic validator 对齐，防 F10/F16 契约分裂）。enum 含 pending：异步启动也必须有说明性 reason（可与 task_id 并存，task_id 是机器可读 ID，reason 是人类可读说明）",
        "if": {"properties": {"status": {"enum": ["skipped", "rejected", "pending"]}}},
        "then": {
          "required": ["reason"],
          "properties": {"reason": {"type": "string", "minLength": 1}}
        }
      }
    ]
  }
}
```

**覆盖工具清单（最低 ≥ 18 个 `produces_write=True` 写入型工具，**真实 tool name** 已与代码库核对）：

| 模块 | 工具（真实 name） | WriteResult 子类 |
|------|------|------|
| 配置 | `config.add_provider` / `config.set_model_alias` / `config.sync` / `setup.quick_connect` | `Config*Result` / `SetupQuickConnectResult` |
| MCP 安装 | `mcp.install` / `mcp.uninstall` | `Mcp*Result`（保留 `server_id` / `install_source`） |
| 子任务控制 | `subagents.spawn` / `subagents.kill` / `subagents.steer` / `work.merge` / `work.delete` | `Subagents*Result` / `Work*Result`（保留 `task_id` / `work_id` / `session_id` / `artifact_id` / `children[]` 等关联键） |
| 文件 | `filesystem.write_text` | `FilesystemWriteTextResult` |
| Memory | `memory.write` | `MemoryWriteResult`（保留 `memory_id` / `version` / `action` / `scope_id`） |
| Behavior / Canvas | `behavior.write_file` / `canvas.write` | `BehaviorWriteFileResult` / `CanvasWriteResult`（保留 `artifact_id` / `task_id`） |
| F084 新增 | `user_profile.update` / `user_profile.observe` | `UserProfileUpdateResult` / `ObserveResult` |

**关键设计：每个写工具用 WriteResult 子类，保留现有结构化字段**——`status` / `target` / `preview` 是基础回显字段；`task_id` / `memory_id` / `version` / `children` 等是后续 steer/kill/inspect/打开 artifact 的关联键，不能被压扁。

**显式不纳入清单的执行类工具**（`produces_write=False`，return type 不约束）：`browser.open` / `browser.navigate` / `browser.act`（浏览器 session 控制）/ `terminal.exec`（命令执行）/ `tts.speak`（音频输出）。这些工具的 `side_effect_level` 也是 REVERSIBLE+，但语义不是产生持久化写入物，强制 WriteResult 在语义上不准确。**注**：`graph_pipeline` 之前被误归到此类（其 `action="start"` 写 Task/Work + commit DB），已移到写入型清单（F15 修复）。

下面所有 user_profile 工具的 output_schema 都是 WriteResult 的扩展（继承基础字段，加上工具特定字段）。

---

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
    "description": "继承 WriteResult Protocol（顶部章节定义），加 user_profile 特定字段",
    "required": ["status", "target", "blocked", "approval_requested"],
    "properties": {
      "status": {"type": "string", "enum": ["written", "skipped", "rejected"], "description": "继承自 WriteResult"},
      "target": {"type": "string", "description": "USER.md 绝对路径"},
      "bytes_written": {"type": ["integer", "null"]},
      "preview": {"type": ["string", "null"], "description": "前 200 字符摘要（取代旧 written_content 字段）"},
      "mtime_iso": {"type": ["string", "null"]},
      "reason": {"type": ["string", "null"], "description": "如 threat_blocked / char_limit_exceeded / approval_pending"},
      "blocked": {"type": "boolean", "description": "Threat Scanner 是否拦截"},
      "pattern_id": {"type": ["string", "null"], "description": "Threat Scanner 命中的 pattern ID"},
      "approval_requested": {"type": "boolean", "description": "是否触发了 Approval Gate"}
    },
    "allOf": [
      {
        "comment": "条件必填（继承自 WriteResult，防 F10）：status != written 时 reason 必须为非空 string",
        "if": {"properties": {"status": {"enum": ["skipped", "rejected"]}}},
        "then": {
          "required": ["reason"],
          "properties": {"reason": {"type": "string", "minLength": 1}}
        }
      }
    ]
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
    "description": "继承 WriteResult Protocol（顶部章节定义），target=observation_candidates 表项",
    "required": ["status", "target", "queued"],
    "properties": {
      "status": {"type": "string", "enum": ["written", "skipped", "rejected"], "description": "user_profile.observe 是同步操作，不使用 pending 状态（窄化父类 enum）"},
      "target": {"type": "string", "description": "observation_candidates 或 observation_candidates:{candidate_id}"},
      "preview": {"type": ["string", "null"], "description": "fact_content 前 200 字符摘要"},
      "reason": {"type": ["string", "null"], "description": "low_confidence / queue_full / threat_blocked / duplicate"},
      "queued": {"type": "boolean", "description": "是否成功入队"},
      "candidate_id": {"type": ["string", "null"], "description": "入队成功时的候选 ID"},
      "dedup_hit": {"type": "boolean", "description": "是否命中 dedupe（source_turn_id+hash）"}
    },
    "allOf": [
      {
        "comment": "条件必填（继承自 WriteResult，防 F10）：status != written 时 reason 必须为非空 string",
        "if": {"properties": {"status": {"enum": ["skipped", "rejected"]}}},
        "then": {
          "required": ["reason"],
          "properties": {"reason": {"type": "string", "minLength": 1}}
        }
      }
    ]
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
