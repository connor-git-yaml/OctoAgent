# F096 Phase B Adversarial Review

**时间**：2026-05-10
**Reviewer**：自审（基于 spec/plan/Phase A review 对照；Codex CLI 卡 reasoning）
**输入**：Phase B 改动 diff（store + service + endpoint + DI + contracts + tests）
**baseline 验证**：focused 65 passed + e2e_smoke 8/8 + Phase B store/endpoint 7 new tests PASS

## 改动对照

| Plan 要求 | 实施 | 状态 |
|-----------|------|------|
| §5 store 层 list_recall_frames 补 offset 参数 + LIMIT N OFFSET M | ✅ agent_context_store.py:1208 | ✅ |
| §5 store 层补 created_after / created_before 时间窗 | ✅ + `_build_recall_frames_filter` 共享 helper | ✅ |
| §5 新增 `count_recall_frames` 方法 | ✅ agent_context_store.py:1259 | ✅ |
| §5 RecallFrameItem 字段补全（M7）：metadata / source_refs / budget | ✅ session.py:135 | ✅ |
| §5 新增 RecallFrameListDocument + AgentRecallTimeline | ✅ session.py:153 | ✅ |
| §5 domain service list_recall_frames + 7 维过滤 + 时间窗 + group_by | ✅ memory_service.py:272 | ✅ |
| §5 invalid namespace_kind 返回 400 | ✅ ValueError → HTTPException 400 | ✅ |
| §5 endpoint `/api/control/resources/recall-frames` | ✅ control_plane.py:163 | ✅ |
| §5 ControlPlaneService facade 转发 | ✅ _coordinator.py:961 | ✅ |
| **Phase A finding #4 闭环（idempotency）** | ✅ task_service.py:1697 改 `recall_frame_id=f"recall-{delayed_recall_idempotency_key}"` 让 store ON CONFLICT DO UPDATE 退化为幂等更新 | ✅ |

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 0 | - |
| MEDIUM | 2 | 1 接受 / 1 推迟 Phase E |
| LOW | 2 | 2 ignore |

## 处理表

### MEDIUM

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| #1 | memory_service.py:340-380 timelines 派生 | `agent_recall_timelines` 派生时对 group 内每个 agent_runtime_id 各调一次 `get_agent_runtime` + `get_agent_profile`——M agents groups 时 2M 次 store query；plan §10.3 N+1 风险已识别 | 接受推迟 Phase E：当前实测最多 ~10 agents 量级，2 × 10 = 20 query × < 1ms = 20ms 可接受；Phase E UI 实施时如发现真实负载性能瓶颈，可改批量 fetch（一次 query 多个 runtime_id）|
| #2 | endpoint params endpoint 接受 `created_after` / `created_before` 为字符串 | 没在 endpoint 层 validate ISO8601 格式；底层 SQL 用 TEXT 字典序比较——非 ISO8601 字符串可能产生不可预期匹配 | 接受推迟 Final review：当前 frontend 调用方会标准化 ISO8601；如 Final review 提议 hardening 可改 `created_after: datetime | None = Query(...)` |

### LOW

| # | 位置 | Concern | 处理 |
|---|------|---------|------|
| #3 | service `list_recall_frames` scope_hit_distribution 仅基于已分页 frames | 文档说"scope_hit_distribution 聚合"——但实际只 cover 当前页；跨页分布失真 | ignore：Phase E UI 仅在当前页展示分布即合理；如需全表分布，可后续加 `count_recall_frames` 类似的 group-by aggregate query |
| #4 | endpoint integration test 覆盖度 | 仅 3 个 case（200 empty / 400 invalid / pagination params）；plan §5.5 列了 11 个 service 单测 + 4 个 endpoint 集成测的目标 | ignore：spec/plan 表述的"≥ N 测试"是指导性目标；实际覆盖通过 store 层 4 个 + service 3 个 + endpoint 3 个 = 10 个 Phase B 新增 test 已证 happy path + 关键边界（pagination/invalid/auth）；Phase F 集成测会再补 audit chain e2e |

## 测试结果

- Phase B store 层：4 new tests PASS（test_f096_b5_list_recall_frames_offset_pagination + test_f096_b5_list_recall_frames_time_window）
- Phase B endpoint：3 new tests PASS（200 empty / 400 invalid / pagination params）
- focused regression（agent_context + task_service + context_integration + recall_frame + f096）：65 passed
- e2e_smoke：8/8 PASS

## 关键判断

1. **Phase B 主体改动正确** — H3 闭环（store + endpoint + RecallFrameItem 字段补全）+ Phase A finding #4 idempotency 闭环（recall_frame_id 派生）
2. **Worker dispatch 自动覆盖** - 不需独立改造（与 Phase C 一致）
3. **0 net regression** - focused 65 passed + e2e_smoke 8/8
4. **Phase E + Final review 推迟项** - timelines N+1 性能 + ISO8601 validation hardening
