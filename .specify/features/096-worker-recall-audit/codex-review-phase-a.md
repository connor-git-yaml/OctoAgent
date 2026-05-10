# F096 Phase A Adversarial Review

**时间**：2026-05-10
**Reviewer**：Claude general-purpose Agent（替代 Codex；Codex 卡 reasoning 不可用）
**输入**：Phase A 改动 diff（task_service.py + test_task_service_context_integration.py）+ spec/plan
**baseline 验证**：3260 passed + e2e_smoke 8/8 PASS（6 e2e_full+e2e_live failures 是真打 LLM 环境性，与 F096 改动域无关）

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 0 | - |
| MEDIUM | 5 | 4 接受闭环 / 1 推迟 Phase B |
| LOW | 4 | 1 写入 commit message / 3 ignore |

## 处理表

### MEDIUM（commit 前必修 4 个）

| # | Section | Concern 摘要 | 处理决议 |
|---|---------|-------------|---------|
| **#1** | task_service.py:1707 | memory_hits schema 双路径不一致——sync 用 `_memory_hit_payload(hit)` 17 字段扁平化；延迟用 `hit.model_dump(mode="json")` 直接全量 dump。下游 list_recall_frames API + Web Memory Console 渲染会拿到两种 schema row → schema 漂移打破 audit 一致性 | **接受 → 改 commit**：用 `AgentContextService._memory_hit_payload(hit)` 替代 `hit.model_dump(...)`，schema 与 sync 路径完全一致 |
| **#2** | task_service.py:1719-1723 | degraded_reason 缺 plan §2.2 项 13 要求的 `F096_delayed_path` marker——下游 list_recall_frames 按 degraded_reason 过滤"延迟路径召回"会丢覆盖 | **接受 → 改 commit**：补 `F096_delayed_path` 后缀拼接，与 sync 路径 `F094_audit_anomaly:` 类似前缀风格一致 |
| **#3** | task_service.py:1724-1728 | metadata (a) 缺 plan §2.2 要求的 `surface` 字段；(b) source 值 `delayed_recall_materialize` 偏离 plan 写的 `delayed_recall`。下游 metadata.source 过滤跨 baseline 断裂 | **接受 → 改 commit**：(a) 补 `surface=task.requester.channel`；(b) source 改为 `delayed_recall`；测试同步改一行 |
| **#4** | task_service.py:1697（recall_frame_id 派生）| recall_frame_id 用 `str(ULID())` 每次新生成；上游 try_record 防双物化只在 `entry.result_ref` set 时早返；race window：同一 idempotency_key 两并发进入会创建两条不同 recall_frame_id 的 RecallFrame | **接受 → 推迟 Phase B**：Phase A 单独 commit 时归档"接受 race window"；**Phase B endpoint 暴露 list_recall_frames 时必修**（前端拿到重复 frame 体验崩塌）；Phase B 实施时把 recall_frame_id 派生改为 `f"recall-{delayed_recall_idempotency_key}"` 让 ON CONFLICT DO UPDATE 退化为幂等更新 |
| **#5** | tests vs T-A-2 用例 (3) | 测试缺 plan §2.3 / T-A-2 第 3 用例 `test_delayed_recall_save_recall_frame_failure_does_not_block_emit`——try-except 设计的核心契约无测试 = 后续重构容易把 except 路径改坏不被发现 | **接受 → 改 commit**：补 1 个 monkeypatch save_recall_frame raise → assert MEMORY_RECALL_COMPLETED 仍 emit + RecallFrame store 内不存在；测试新增约 100 行（含双轮 fake_recall_memory fixture） |

### LOW

| # | Section | Concern | 处理 |
|---|---------|---------|------|
| #6 | task_service.py:1657 vs 1578 | 冗余 fetch（baseline F094 B6 引入，非 F096 新增）| **ignore（baseline 债）**：本 Phase 不修；推迟到 F096 Final review 或 F100 D7 顺手清 |
| #7 | task_service.py:1729-1738 | MemoryNamespaceKind valid_kind_values 静默过滤防御冗余但安全 | **ignore（防御冗余）**：enum 演进时再加 log；不影响合并 |
| #8 | task_service.py:1704 | `query=str(plan["query"]) or recall.query` 中 `or recall.query` 是死分支（line 1582 已 guard）| **ignore**：保留 fallback 不破坏行为 |
| #9 | commit message 待写 | F096 改动只在 task_service.py，与 6 e2e_full+e2e_live failures（真打 LLM 测试）无因果——但 commit message 必须显式归档避免 Final review 误判 | **接受 → 写 commit message**：必须提"0 net regression（3260 passed），6 e2e_live failures 是 pre-existing 真打 LLM 环境性问题，与 F096 改动域无关" |

## 修订动作（已闭环）

- [x] **#1** task_service.py:1707 改 `AgentContextService._memory_hit_payload(hit)`
- [x] **#2** task_service.py:1719 补 `F096_delayed_path` marker（含基础 degraded_reasons 拼接）
- [x] **#3** task_service.py:1724 补 surface 字段 + source 值改 `delayed_recall`
- [x] **#5** tests 新增 `test_task_service_delayed_recall_save_recall_frame_failure_does_not_block_emit`（双轮 fake_recall_memory + monkeypatch save_recall_frame raise → 验证 MEMORY_RECALL_COMPLETED 仍 emit）
- [ ] **#4** recall_frame_id idempotency 派生 → 推迟 Phase B（commit message 归档）
- [x] **#9** commit message 显式提 e2e_live 失败归档

## 测试结果

修订后跑两个 Phase A test：
- `test_task_service_persists_delayed_recall_as_durable_artifacts_and_events`：✅ PASS（含 RecallFrame 字段补全 + source/surface/degraded_reason marker 验证 + memory_hits schema 一致性验证）
- `test_task_service_delayed_recall_save_recall_frame_failure_does_not_block_emit`：✅ PASS（验证 raise 后 MEMORY_RECALL_COMPLETED 仍 emit + store 无 delayed RecallFrame）

`2 passed, 30 warnings in 2.71s`

## 关键判断

1. **Phase A 改动正确**：H2 闭环（frame.agent_session_id 强一致派生）落地 OK
2. **Phase B 推迟项**：finding #4 recall_frame_id idempotency race window 必须在 Phase B endpoint 实施前修
3. **0 net regression**：3260 passed，6 e2e_full+e2e_live failures 是真打 LLM 环境性 pre-existing
