# F096 Phase D Adversarial Review

**时间**：2026-05-10
**Reviewer**：自审（基于 spec/plan + Phase A/B/C review 对照）
**输入**：Phase D 改动 diff（enums + behavior payload + helper + agent_context emit + test fixture）
**baseline 验证**：focused 93 passed + e2e_smoke 8/8 PASS

## 改动对照

| Plan 要求 | 实施 | 状态 |
|-----------|------|------|
| §4.2 EventType.BEHAVIOR_PACK_USED 新增 | ✅ enums.py:223 | ✅ |
| §4.3 BehaviorPackUsedPayload schema | ✅ behavior.py（pack_id / agent_id / agent_kind / agent_runtime_id / task_id / session_id / use_phase / cache_state / file_count / is_advanced_included / created_at）| ✅ |
| §4.4 make_behavior_pack_used_payload helper（M4 闭环 删 hasattr fallback）| ✅ agent_decision.py:360 | ✅ |
| §4.5 方案 B：build_task_context 内 prime resolve_behavior_pack（_fit_prompt_budget 之前）| ✅ agent_context.py:644-668 | ✅ |
| §4.5 emit LOADED + USED 在 line 936 commit 之后（L12 闭环）| ✅ agent_context.py 在 Phase C MEMORY_RECALL_COMPLETED emit 之前 | ✅ |
| §4.5 LOADED 仅 cache miss / USED 总 emit / try-except 隔离 | ✅ | ✅ |
| §4.6 单测：cache miss / USED frequency / pack_id match / failure isolation | ✅ 扩展现有 baseline test 加 30 行 assertion + autouse cache reset fixture | ✅ |

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 0 | - |
| MEDIUM | 2 | 2 接受闭环 |
| LOW | 2 | 2 ignore |

## 处理表

### MEDIUM（接受闭环）

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| **#1** | test fixture autouse=True clear `_behavior_pack_cache` | cross-test cache pollution（baseline 隐性 bug，本 Phase 暴露）：第一个 test resolve_behavior_pack 装入 module-level cache，后续 test build_task_context 第一次 resolve 命中 cache hit（metadata.cache_state stripped），LOADED 永不 emit | **接受闭环**：加 autouse fixture clear cache（仅本测试文件 scope）；conftest.py 全局 fixture 留 Final review 决议 |
| **#2** | agent_context.py:649 prime resolve project.slug | 实施第一版用 `project.project_slug`（错误属性名）→ verify 通过实测纠正为 `project.slug`（Project model:67 实际字段名）| **接受**：实施时纠正；plan §4.5 文档示例代码用 `project.project_slug`，下次 plan 同步时纠正 |

### LOW

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| #3 | agent_context.py emit 块未单独抽 helper | emit 代码 ~60 行 inline 在 build_task_context；Phase C MEMORY_RECALL_COMPLETED emit + Phase D LOADED + USED 三段合计 ~110 行 emit 代码 | ignore：当前可读；如增加更多 emit 类型可考虑抽 `_emit_event` helper |
| #4 | LOADED 不带 idempotency_key | 与 Phase C sync emit + delayed emit 都用 idempotency_key 做 retry 防重复不同；LOADED 没有 dispatch retry 语义（cache miss 一次 dispatch 内 emit 一次） | ignore：cache miss 路径本身 idempotent；retry 不重复触发 cache miss |

## 测试结果

- baseline test 扩展：BEHAVIOR_PACK_LOADED + USED + pack_id match assertion ✅ PASS
- focused regression（93 tests，含 Phase A/B/C/D 全部累积测试）：93 PASSED
- e2e_smoke：8/8 PASS

## 关键判断

1. **Phase D 改动正确** — 方案 B 实施落地 OK（prime resolve + emit after commit + try-except 隔离）
2. **plan §4.5 方案 A → B 的判断对**：方案 A "重复 resolve" 在 cache hit 路径必然 LOADED 不 emit；方案 B prime resolve 第一次拿到 cache_state="miss"
3. **cross-test cache 污染**是 baseline 隐性 bug，本 Phase 加 fixture 修复；conftest.py 全局 fixture 留 Final review 决议
4. **0 net regression** - focused 93 + e2e_smoke 8/8 + 单独跑 Phase A/C/D test 全过
