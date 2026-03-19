# Contract: SessionMemoryExtractor

**Feature**: 067-session-driven-memory-pipeline
**Type**: Internal Service Contract
**Date**: 2026-03-19

---

## 服务签名

```python
class SessionMemoryExtractor:
    """Session 驱动的统一记忆提取服务。"""

    def __init__(
        self,
        agent_context_store: AgentContextStore,
        memory_store: SqliteMemoryStore,
        llm_service: LlmServiceProtocol | None,
        project_root: Path,
    ) -> None: ...

    async def extract_and_commit(
        self,
        *,
        agent_session: AgentSession,
        project: Project | None,
        workspace: Workspace | None,
    ) -> SessionExtractionResult:
        """从 Session 的新增 turns 中提取记忆并写入 SoR。

        全流程：
        1. 检查 session kind 是否需要提取（仅 BUTLER_MAIN / WORKER_INTERNAL / DIRECT_WORKER）
        2. try-lock: 如果该 session 已有正在进行的提取，跳过
        3. 查询 turn_seq > memory_cursor_seq 的新增 turns
        4. 无新 turn 时直接返回
        5. 构建提取输入（压缩 tool calls）
        6. 调用 LLM 提取（single call, fast alias）
        7. 解析 LLM 输出为 ExtractionItem[]
        8. 通过 propose-validate-commit 写入 SoR
        9. 创建溯源 Fragment 并关联 SoR
        10. 更新 memory_cursor_seq

        内部捕获所有异常——LLM 不可用时静默降级，不影响调用方。
        """
        ...
```

---

## 触发点

```python
# 在 AgentContextService.record_response_context() 末尾
# 文件: octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py

# 在 _record_private_tool_evidence_writeback 之后、commit 之前
if agent_session is not None:
    asyncio.create_task(
        self._session_memory_extractor.extract_and_commit(
            agent_session=agent_session,
            project=project,
            workspace=workspace,
        )
    )
```

---

## 前置条件

| 条件 | 不满足时的行为 |
|------|----------------|
| `agent_session.kind` in (`BUTLER_MAIN`, `WORKER_INTERNAL`, `DIRECT_WORKER`) | 跳过，返回 `skipped_reason="unsupported_session_kind"` |
| `llm_service is not None` | 跳过，返回 `skipped_reason="llm_unavailable"`，记录降级日志 |
| `新增 turns > 0`（cursor 之后有 turn） | 跳过，返回 `skipped_reason="no_new_turns"` |
| per-Session lock 可获取 | 跳过，返回 `skipped_reason="extraction_in_progress"` |
| scope_id 可推导 | 跳过，返回 `skipped_reason="no_scope"` |

---

## LLM 提取 Prompt

### System Prompt（概要）

```
你是一个记忆管理助手。从对话中提取以下类型的长期记忆：

1. facts — 用户偏好、个人事实、项目决策、关键结论
2. solutions — 问题-解决方案对（问题描述 + 解决步骤 + 适用条件）
3. entities — 人物、组织、项目等实体及其关系
4. tom — Theory of Mind 推理（用户的隐含需求、情绪状态、沟通风格）

输出 JSON 数组:
[
  {
    "type": "fact|solution|entity|tom",
    "subject_key": "主题/子主题",
    "content": "完整陈述句",
    "confidence": 0.8,
    "action": "add|update",
    "partition": "work|personal|...",
    // solution 特有
    "problem": "...",
    "solution": "...",
    "context": "...",
    // entity 特有
    "entity_name": "...",
    "entity_type": "person|org|project",
    "relations": [{"target": "...", "relation": "..."}]
  }
]

无值得记忆的内容时输出 []。
```

### User Prompt

```
以下是最近的对话内容（{turn_count} 轮）：

{formatted_turns}
```

### Model Config

- alias: `fast`（可配置）
- temperature: 0.3
- max_tokens: 4096

---

## 输出结果

```python
@dataclass(slots=True)
class SessionExtractionResult:
    session_id: str
    scope_id: str
    turns_processed: int = 0
    new_cursor_seq: int = 0
    facts_committed: int = 0
    solutions_committed: int = 0
    entities_committed: int = 0
    tom_committed: int = 0
    fragments_created: int = 0
    skipped_reason: str = ""
    errors: list[str] = field(default_factory=list)
```

---

## 错误处理

| 异常场景 | 行为 |
|----------|------|
| LLM 调用失败 | 静默跳过，cursor 不更新，记录 `session_memory_extraction_llm_failed` 日志 |
| LLM 输出解析失败 | 静默跳过，cursor 不更新，记录 `session_memory_extraction_parse_failed` 日志 |
| SoR 写入部分失败 | 接受部分结果，cursor 推进到已处理的 turn，记录每条失败的 error |
| scope_id 推导失败 | 跳过整次提取，记录 `session_memory_extraction_no_scope` 日志 |
| Session 在提取过程中被关闭 | 提取继续执行到完成（fire-and-forget），cursor 正常更新 |

---

## 观测性

### 结构化日志事件

- `session_memory_extraction_started`: session_id, scope_id, cursor_before, new_turns_count
- `session_memory_extraction_completed`: session_id, scope_id, cursor_after, facts, solutions, entities, tom, fragments
- `session_memory_extraction_skipped`: session_id, reason
- `session_memory_extraction_llm_failed`: session_id, scope_id, error_type, error
- `session_memory_extraction_parse_failed`: session_id, scope_id, response_preview
