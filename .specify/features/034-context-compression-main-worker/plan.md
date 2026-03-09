# Implementation Plan: Feature 034 — Main/Worker Context Compaction

**Branch**: `034-context-compression-main-worker` | **Date**: 2026-03-09 | **Spec**: `.specify/features/034-context-compression-main-worker/spec.md`  
**Input**: `.specify/features/034-context-compression-main-worker/spec.md` + `research/research-synthesis.md`

## Summary

Feature 034 把 Agent Zero 的“utility model 帮主模型压缩历史”思路真正接入到 OctoAgent 的主运行链，但做了两点本地化收敛：

1. **接入点收敛到 `TaskService`**：因为主 Agent、chat route、task route 和 `WorkerRuntime` 最终都汇聚到 `TaskService.process_task_with_llm()`，这里是唯一值得做真实接线的点。
2. **compaction 与 Memory 治理解耦**：压缩只负责生成 request snapshot、summary artifact 和 `FLUSH` maintenance evidence，不直接改 SoR。

当前实现已经落地并通过定向回归。

## Technical Context

**Language/Version**: Python 3.12+  
**Primary Dependencies**: `fastapi`, `httpx`, `pytest-asyncio`, `aiosqlite`, `structlog`, `octoagent-memory`, `octoagent-provider`  
**Storage**: SQLite Event Store + Artifact Store + Memory governance store  
**Testing**: `ruff`, `pytest`  
**Target Platform**: gateway/task runtime + worker runtime  
**Constraints**:

- 真实接线必须落在 `TaskService`，不能只做 util/demo
- Subagent 不接入
- 压缩只能通过 Memory flush hook 回灌
- `summarizer` 失败时不能把主模型调用一起拖死

## Constitution Check

| Constitution 原则 | 适用性 | 评估 | 说明 |
|---|---|---|---|
| 原则 1: Durability First | 直接适用 | PASS | 完整用户文本、request snapshot、compaction summary 和 maintenance run 都持久化 |
| 原则 2: Everything is an Event | 直接适用 | PASS | 新增 `CONTEXT_COMPACTION_COMPLETED` 事件 |
| 原则 6: Degrade Gracefully | 直接适用 | PASS | summarizer 失败时退回原始历史 |
| 原则 8: Observability is a Feature | 直接适用 | PASS | artifact + event + memory flush evidence 三层可追溯 |
| 原则 11: Context Hygiene | 直接适用 | PASS | old history 由 cheap alias 压缩，recent turns 保持原文 |
| 原则 12: 记忆写入必须治理 | 直接适用 | PASS | compaction 仅走 maintenance flush hook，不旁路 SoR |

## Project Structure

### 文档制品

```text
.specify/features/034-context-compression-main-worker/
├── spec.md
├── plan.md
├── research/
│   ├── product-research.md
│   ├── tech-research.md
│   ├── online-research.md
│   └── research-synthesis.md
├── checklists/
│   └── requirements.md
├── tasks.md
└── verification/
    └── verification-report.md
```

### 源码变更布局

```text
octoagent/
├── apps/gateway/
│   ├── src/octoagent/gateway/services/
│   │   ├── context_compaction.py
│   │   ├── task_service.py
│   │   ├── control_plane.py
│   │   └── operator_actions.py
│   └── tests/
│       └── test_context_compaction.py
└── packages/core/
    └── src/octoagent/core/models/
        ├── enums.py
        └── payloads.py
```

## Implementation Phases

### Phase 1: 研究与运行链定位

- 对照 Agent Zero `History.compress()`、`organize_history` / `organize_history_wait`
- 确认 OctoAgent 真正的 prompt assembly 落点是 `TaskService.process_task_with_llm()`
- 明确主 Agent / Worker / Subagent 三者边界

### Phase 2: 事件与数据补强

- `USER_MESSAGE` 持久化完整 `text`
- 新增 `CONTEXT_COMPACTION_COMPLETED`
- 新增 compaction payload

### Phase 3: 上下文重建与压缩

- 新建 `ContextCompactionService`
- 从事件 + artifact 重建多轮对话
- 估算 token，必要时调用 `summarizer`
- 生成实际请求上下文和 request snapshot

### Phase 4: Memory 治理接缝

- compaction 成功时生成 summary artifact
- 调用 `MemoryService.run_memory_maintenance(FLUSH)`
- 写入 evidence refs 和 run id

### Phase 5: 降级与验证

- `summarizer` 失败/空结果退回原始历史
- Worker 路径复用，Subagent 绕过
- 补齐 chat / worker / operator / control-plane 相关回归

## Non-goals

- 复刻 Agent Zero 的后台线程式压缩调度
- 给 Subagent 增加上下文压缩
- 实现新的 Memory Console / Runtime Console 页面
- 直接把 compaction 摘要写成 SoR 事实

