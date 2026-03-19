# Quickstart: Session 驱动统一记忆管线

**Feature**: 067-session-driven-memory-pipeline

---

## 概要

本 Feature 将记忆写入从 6 条分散路径收敛为 1 条统一的 Session 级入口。核心变更：

1. **新建** `SessionMemoryExtractor` 服务
2. **新增** `AgentSession.memory_cursor_seq` 字段
3. **废弃** 4 条旧记忆写入路径
4. **保留** Scheduler Consolidation 兜底 + memory.write 工具 + Private Tool 证据

---

## 快速上手

### 1. 理解触发流程

```
用户发消息 → Agent 响应 → record_response_context()
    → _record_private_tool_evidence_writeback()  (保留)
    → asyncio.create_task(extractor.extract_and_commit())  (新增)
    → commit
```

### 2. 关键文件

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `models/agent_context.py` | 修改 | AgentSession 新增 `memory_cursor_seq` |
| `store/agent_context_store.py` | 修改 | Schema 迁移 + turn 查询支持 after_seq |
| `services/session_memory_extractor.py` | **新建** | 核心提取服务 |
| `services/agent_context.py` | 修改 | 触发点注入 + 删除 `_record_memory_writeback` |
| `services/task_service.py` | 修改 | 删除 FlushPromptInjector 调用 + _auto_consolidate_after_flush |
| `provider/dx/flush_prompt_injector.py` | **删除** | 整个文件废弃 |

### 3. 数据流

```
AgentSession.memory_cursor_seq = 5
    ↓
查询 turns WHERE turn_seq > 5
    ↓
压缩 tool calls (保留 tool_name + summary)
    ↓
LLM 提取 (fast alias, single call)
    ↓
JSON → ExtractionItem[]
    ↓
propose_write → validate → commit (每条 SoR)
    ↓
创建 Fragment (evidence_for_sor_ids)
    ↓
memory_cursor_seq = 8 (最新 turn_seq)
```

### 4. 验证方法

```bash
# 运行单元测试
uv run pytest octoagent/apps/gateway/tests/test_session_memory_extractor.py -v

# 运行集成测试
uv run pytest octoagent/tests/integration/test_f067_session_memory_pipeline.py -v

# 验证旧路径已移除
grep -r "FlushPromptInjector" octoagent/  # 应无结果
grep -r "_record_memory_writeback" octoagent/  # 应无结果
grep -r "_auto_consolidate_after_flush" octoagent/  # 应无结果
```

### 5. 注意事项

- **不要**修改 `_record_private_tool_evidence_writeback`，它负责 Vault 安全证据写入
- **不要**删除 `ConsolidationService`，它仍作为 Scheduler 兜底和管理台入口
- 提取管线的 LLM alias 默认为 `fast`，可通过 memory config 切换
- 所有异常内部捕获，不会影响 Agent 响应流程
