# Contract: 废弃路径移除

**Feature**: 067-session-driven-memory-pipeline
**Type**: Deprecation Contract
**Date**: 2026-03-19

---

## 废弃路径清单

### Path 1: 响应完成后自动写入通用记忆 Fragment

**位置**: `agent_context.py` :: `_record_memory_writeback()` (行 1927+)

**当前行为**: Worker Agent 响应完成后，将 user text + model response + continuity summary 拼接为 writeback 文本，通过 `run_memory_maintenance(FLUSH)` 写入通用 Fragment。

**移除方式**: 删除 `_record_memory_writeback` 方法整体，以及 `record_response_context` 中对该方法的调用（行 1252-1263）。

**保留项**: `_record_private_tool_evidence_writeback` 方法保留（Private Tool 安全证据写入，与记忆提取正交）。

**验证**: Worker 响应完成后不再产生 `memory_writeback` 类型的 Fragment。

---

### Path 2: Compaction 流程中碎片化写入 Fragment

**位置**: `task_service.py` :: `_persist_compaction_flush()` (行 1832-1964)

**当前行为**: 上下文压缩时执行两步——先调用 FlushPromptInjector 注入静默 turn 让 LLM 写入 SoR，再调用 `run_memory_maintenance(FLUSH)` 写入压缩摘要 Fragment。

**移除方式**:
1. 删除 FlushPromptInjector 的调用逻辑（行 1866-1919 的 try 块）
2. 保留原有的 `run_memory_maintenance(FLUSH)` 调用——压缩摘要 Fragment 仍有上下文恢复价值
3. 注: 压缩摘要 Fragment 不再承担"待整合为 SoR"的职责，仅作为上下文恢复的参考

**验证**: Compaction 流程不再调用 FlushPromptInjector，不再产生 flush_prompt 类型的 SoR 写入。

---

### Path 3: Compaction 后注入静默记忆提取 turn

**位置**: `flush_prompt_injector.py` 整个文件

**当前行为**: FlushPromptInjector 在 Compaction 前注入一次静默 LLM 调用，让模型审视对话并通过 memory.write 写入 SoR。

**移除方式**: 删除 `flush_prompt_injector.py` 整个文件，移除所有 import 引用。

**受影响的引用点**:
- `task_service.py`: `_persist_compaction_flush` 中的 injector 调用
- `agent_context.py`: `get_flush_prompt_injector()` 方法（若存在）

**验证**: `flush_prompt_injector.py` 文件不存在，无任何代码引用 `FlushPromptInjector`。

---

### Path 4: Fragment 写入后自动触发 Consolidation

**位置**: `task_service.py` :: `_auto_consolidate_after_flush()` (行 1974+)

**当前行为**: `_persist_compaction_flush` 完成后 fire-and-forget 调用 `consolidation_service.consolidate_by_run_id()`，仅处理本次 Flush 产出的 Fragment。

**移除方式**: 删除 `_auto_consolidate_after_flush` 方法整体，以及 `_persist_compaction_flush` 中的 `asyncio.create_task(self._auto_consolidate_after_flush(...))` 调用（行 1953-1962）。

**保留项**:
- `ConsolidationService` 本身保留（Scheduler 定期 Consolidation 兜底、管理台手动触发仍需使用）
- `consolidate_all_pending` 方法保留（Scheduler 入口）
- `consolidate_scope` 方法保留（管理台入口）

**验证**: `_persist_compaction_flush` 不再调用 Consolidation，但 Scheduler 定期 Consolidation 和管理台手动 Consolidation 正常工作。

---

## 保留通道

| 通道 | 入口 | 说明 |
|------|------|------|
| Session 记忆提取管线 | `SessionMemoryExtractor.extract_and_commit()` | **新建**。每次 Agent 响应完成后 fire-and-forget 触发 |
| `memory.write` 工具 | Agent 对话中主动调用 | **保留**。Agent 主动写入通道 |
| Scheduler Consolidation | `ControlPlaneService._handle_memory_consolidate()` | **保留**。定期兜底整合未处理 Fragment |
| 管理台手动 Consolidation | `MemoryConsoleService.run_consolidate()` | **保留**。用户主动触发 |
| Private Tool 安全证据 | `_record_private_tool_evidence_writeback()` | **保留**。Vault 安全审计通道 |
