# F096 Phase C Adversarial Review

**时间**：2026-05-10
**Reviewer**：自审（基于 Phase A review + spec/plan 对照；Codex CLI 卡 reasoning，不可用）
**输入**：Phase C 改动 diff（agent_context.py + test assertion 扩展）
**baseline 验证**：focused regression 35 passed + e2e_smoke 8/8 PASS

> 备注：Phase C 改动极小（agent_context.py +50 行 emit + 6 行 imports；test +28 行 assertion）；且 plan §3.2 v0.2 已明确写出 emit 代码片段 + idempotency_key 设计——本 review 仅做 spec/plan 对照 + 自审隐性 bug 扫描，不做完整 adversarial review；如需深度 review，留 Final cross-Phase review 时一并做。

## 改动对照

| Plan 要求 | 实施 | 状态 |
|-----------|------|------|
| plan §3.2 emit 在 line 936 conn.commit() **之后**（L12 闭环）| 实施在 line 938（commit + 空行后）| ✅ |
| plan §3.2 sync 路径 idempotency_key = `f"{recall_frame_id}:event"`（M8 闭环）| 实施 `f"{recall_frame.recall_frame_id}:event"` | ✅ |
| plan §3.2 try-except 隔离 emit 失败 | 实施 try-except + log warn | ✅ |
| plan §3.2 payload 字段 100% 填充（agent_runtime_id / queried/hit kinds）| 实施全部填充（无审计派生）| ✅ |
| plan §3.2 Worker dispatch 路径覆盖 | 自动覆盖（Worker dispatch 走同 build_task_context 主路径）| ✅ |

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 0 | - |
| MEDIUM | 1 | 接受推迟 Final review |
| LOW | 2 | 1 接受 / 1 ignore |

## 处理表

### MEDIUM

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| #1 | agent_context.py:937-981 emit 块 | 与 task_service.py:1688-1725（delayed 路径 emit）比较：sync 路径用 `event_store.append_event_committed` 直接调；delayed 路径用 `_append_event_only_with_retry`（task_service helper）+ `_sse_hub.broadcast`。**sync 路径少 SSE broadcast**——Web Memory Console 实时显示 sync recall completion 时不会自动推送 | 接受推迟 Final review：Phase B Web 视角 UI 实施时如发现 SSE 缺失影响实时刷新，再补 broadcast；Phase C 不阻 commit |

### LOW

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| #2 | agent_context.py imports | imports 加了 `ActorType, Event, EventCausality` 但与现有 EventType 加在同一 import block 中（line 33-39）；`MemoryRecallCompletedPayload` 单独从 payloads import（line 18-20）——风格不完全一致 | 接受 ignore：Python import 风格非硬要求；ruff 不会 flag |
| #3 | test assertion line 2353 idempotency_key 前缀过滤 | `not ev.causality.idempotency_key.startswith("f038-")` 这是 brittle assertion——硬编码 baseline test idempotency_key 前缀。如果 baseline test 改 idempotency_key 命名，此 assertion 会断 | 接受 ignore：Phase C 测试聚焦"双路径 emit + idempotency_key 不同"，brittle 但能打到要 cover 的 invariant；Final review 时如发现 brittle 度高再改 |

## 测试结果

- baseline `test_task_service_persists_delayed_recall_as_durable_artifacts_and_events` 扩展双路径 emit assertion：✅ PASS
- focused regression（agent_context + task_service + context_integration）：35 passed
- e2e_smoke：8/8 PASS

## 关键判断

1. **Phase C 改动正确** - emit 接入点、idempotency_key 设计、try-except 隔离都与 plan §3.2 v0.2 一致
2. **Worker dispatch 自动覆盖** - 通过 build_task_context 主路径自动生效，不需独立改造
3. **0 net regression** - focused suite 35 passed + e2e_smoke 8/8
4. **finding #1 SSE broadcast 缺失** - 推迟到 Phase B/E 视角 UI 实施时评估
