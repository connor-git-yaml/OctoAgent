# Quickstart: Memory Automation Pipeline (Phase 1)

**Feature**: 065-memory-automation-pipeline
**Date**: 2026-03-19

## 快速概览

Phase 1 实现三个核心能力：
1. Agent 对话中主动写入记忆 (`memory.write`)
2. Compaction Flush 后自动整理 Fragment 为 SoR
3. 定时自动整理积压的 Fragment

## 实现顺序

```
Task 1: ConsolidationService 提取
  ↓
Task 2: memory.write 工具    Task 3: Flush 后自动 Consolidate    Task 5: MemoryConsoleService 重构
  ↓                           ↓
  (独立)                     Task 4: Scheduler 注册
```

**建议开发顺序**: Task 1 -> Task 5 -> Task 2 -> Task 3 -> Task 4

- Task 1 + Task 5 先完成 ConsolidationService 提取和 MemoryConsoleService 重构（基础设施）
- Task 2 可独立开发（不依赖 ConsolidationService）
- Task 3 依赖 Task 1（需要 ConsolidationService）
- Task 4 依赖 Task 3（需要确认 Consolidate 流程可用）

## 涉及文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `packages/provider/.../consolidation_service.py` | NEW | ConsolidationService 核心实现 |
| `packages/provider/.../memory_console_service.py` | MODIFY | 委托 ConsolidationService，删除迁移代码 |
| `apps/gateway/.../capability_pack.py` | MODIFY | 新增 memory.write 工具 |
| `apps/gateway/.../task_service.py` | MODIFY | Flush 后触发异步 Consolidate |
| `apps/gateway/.../control_plane.py` | MODIFY | 注册默认 Consolidate 定时作业 |
| `apps/gateway/tests/test_memory_write_tool.py` | NEW | memory.write 工具测试 |
| `apps/gateway/tests/test_consolidation_service.py` | NEW | ConsolidationService 单元测试 |
| `apps/gateway/tests/test_auto_consolidate.py` | NEW | Flush 后自动 Consolidate 测试 |

## 关键代码路径

### memory.write 工具

```
capability_pack.py: memory_write()
  -> _resolve_runtime_project_context()
  -> _resolve_memory_scope_ids()
  -> memory_service.get_current_sor()          # 判断 ADD/UPDATE
  -> memory_service.propose_write()
  -> memory_service.validate_proposal()
  -> memory_service.commit_memory()
  -> return JSON
```

### Flush 后自动 Consolidate

```
task_service.py: _persist_compaction_flush()
  -> run_memory_maintenance(FLUSH)
  -> asyncio.create_task(_auto_consolidate_after_flush)  # fire-and-forget
     -> consolidation_service.consolidate_by_run_id()
        -> 查询 run_id 关联的 fragments
        -> LLM 提取事实
        -> propose_write -> validate -> commit (逐条)
        -> 标记 consolidated_at
```

### Scheduler 定期 Consolidate

```
startup:
  control_plane.py: 检查/创建 AutomationJob("system:memory-consolidate")
  automation_scheduler.py: startup() -> sync_job() -> APScheduler.add_job()

运行时:
  APScheduler cron "0 */4 * * *"
  -> _run_scheduled_job("system:memory-consolidate")
  -> execute_action("memory.consolidate")
  -> _handle_memory_consolidate()
  -> memory_console_service.run_consolidate()
  -> consolidation_service.consolidate_all_pending()
```

## 验证检查清单

### memory.write (US-1)

- [ ] 启动 Agent 会话，告诉 Agent 一项偏好信息
- [ ] 验证 Agent 调用了 memory.write 工具
- [ ] 通过 memory.read 验证 SoR 记录已创建
- [ ] 关闭会话，开新会话，通过 memory.recall 验证可检索

### Flush 后自动 Consolidate (US-2)

- [ ] 启动足够长的对话触发 Compaction Flush
- [ ] 检查日志中出现 `auto_consolidate_after_flush` 条目
- [ ] 验证 Flush 产出的 Fragment 被标记 `consolidated_at`
- [ ] 验证至少一条新 SoR 记录生成

### Scheduler 定期 Consolidate (US-3)

- [ ] 系统启动后检查 AutomationJob 列表中包含 `system:memory-consolidate`
- [ ] 手动创建若干无 `consolidated_at` 标记的 Fragment
- [ ] 通过管理台手动触发 memory.consolidate action
- [ ] 验证 Fragment 被处理，SoR 生成

## 降级场景验证

- [ ] LLM 服务不可用时，Consolidate 记录 warning 日志，Fragment 保持未整理
- [ ] Flush 后 Consolidate 失败不影响 Compaction 主流程返回
- [ ] Scheduler Consolidate 单个 scope 失败不影响其他 scope
