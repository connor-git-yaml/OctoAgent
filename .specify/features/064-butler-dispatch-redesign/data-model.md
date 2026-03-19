# 数据模型变更: Feature 064 — Butler Dispatch Redesign

**Date**: 2026-03-19

---

## 概述

Feature 064 **不引入新的数据模型或 schema 变更**。所有变更都在现有模型的 metadata 字段（`dict[str, Any]`）中表达，保持向后兼容。

---

## Metadata 字段变更

### 1. Task metadata

| 字段 | 类型 | 引入 Phase | 说明 |
|------|------|-----------|------|
| `butler_execution_mode` | `str` | Phase 1 | 执行模式标识：`"direct"` = Butler 直接执行，`"single_loop"` = 现有单循环模式 |
| `butler_is_trivial` | `bool` | Phase 1 | `_is_trivial_direct_answer()` 判定结果 |

### 2. Work metadata（Phase 2）

| 字段 | 类型 | 引入 Phase | 说明 |
|------|------|-----------|------|
| `delegate_source` | `str` | Phase 2 | 委派来源：`"butler_tool_call"` = 通过 delegate_to_worker 工具触发 |
| `delegate_task_description` | `str` | Phase 2 | LLM 提供的任务描述 |
| `delegate_urgency` | `str` | Phase 2 | 任务紧急程度：`"normal"` / `"high"` |
| `delegate_context_capsule` | `dict` | Phase 2 | 自动构建的 A2A context capsule |

### 3. Event metadata（Phase 3）

| 字段 | 类型 | 引入 Phase | 说明 |
|------|------|-----------|------|
| `is_failover` | `bool` | Phase 3 | 是否为 failover 降级后的模型调用 |
| `failover_from_alias` | `str` | Phase 3 | 降级前的模型 alias（如 `"main"`） |
| `failover_trigger` | `str` | Phase 3 | 降级触发原因：`timeout` / `overload` / `context_overflow` / `rate_limit` |

### 4. OrchestratorDecisionPayload.route_reason

Phase 1 新增 route_reason 值：

| route_reason | 含义 |
|-------------|------|
| `butler_direct_execution:trivial` | Butler 直接执行（trivial 快速路径） |
| `butler_direct_execution:standard` | Butler 直接执行（标准路径） |

---

## 不变的模型

以下模型 **完全不变**:

- `Task` — 状态机、字段定义不变
- `Event` / `EventType` — 不新增事件类型（Phase 1-2），复用现有 MODEL_CALL_STARTED/COMPLETED 等
- `Artifact` — 结构不变
- `Work` — 结构不变，metadata 扩展
- `DispatchEnvelope` — 结构不变
- `AgentSession` / `ButlerSession` — 结构不变
- `A2AMessage` / `A2AConversation` — 结构不变
- `ButlerDecision` / `ButlerDecisionMode` — 结构不变，但 `DIRECT_ANSWER` 的处理语义变化（不再 fallthrough 到 Worker）
- `ButlerLoopPlan` — 不再生成新实例（Phase 1 跳过 model decision），但类型定义保留

---

## 向后兼容性

所有新增 metadata 字段都是可选的（`dict[str, Any]` 中的键值对）。缺少这些字段的历史数据不受影响。

查询示例:
```sql
-- 查询 Butler 直接执行的任务
SELECT * FROM events
WHERE json_extract(payload, '$.butler_execution_mode') = 'direct';

-- 查询 failover 降级的模型调用
SELECT * FROM events
WHERE json_extract(payload, '$.is_failover') = 1;
```
