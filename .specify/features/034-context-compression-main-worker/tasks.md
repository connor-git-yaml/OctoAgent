# Feature 034 Tasks

## Phase 1: 研究与规范

- [x] T001 复核 Agent Zero 的 history compression / utility model / wait hook 实现
- [x] T002 确认 OctoAgent 主 Agent 与 Worker 的真实 prompt assembly 落点
- [x] T003 冻结 034 spec / research / verification 制品

## Phase 2: 真实接线

- [x] T004 在 `USER_MESSAGE` payload 中持久化完整 `text`
- [x] T005 新增 `ContextCompactionService`，从 task 事件与 artifact 重建多轮上下文
- [x] T006 在 `TaskService.process_task_with_llm()` 中用真实上下文替代“只传最新 user_text”
- [x] T007 超预算时调用 `summarizer` alias 压缩旧历史，保留最近轮次原文
- [x] T008 为每次主模型调用落 `llm-request-context` artifact
- [x] T009 compaction 成功时落 `context-compaction-summary` artifact 与 `CONTEXT_COMPACTION_COMPLETED` 事件

## Phase 3: Memory 与消费侧接缝

- [x] T010 把 compaction 结果通过 `MemoryMaintenanceCommand(kind=FLUSH)` 接入 Memory 治理层
- [x] T011 让 control-plane / operator 侧优先读取 `payload.text`
- [x] T012 明确 Subagent 绕过规则

## Phase 4: 降级与回归

- [x] T013 实现 summarizer 失败或空摘要时退回原始历史的降级路径
- [x] T014 补齐 chat 续对话 / compaction + flush / worker 复用 / subagent 绕过 / summarizer 失败 降级测试
- [x] T015 跑完定向 `ruff` 与 `pytest` 回归并写入 verification report

