# F096 Adversarial Review #1（spec + plan + tasks）

**时间**：2026-05-10
**Reviewer**：Claude general-purpose Agent（替代 codex review；codex 20+ 分钟 0 输出被 kill 后改用）
**输入**：spec.md v0.1 + plan.md v0.1 + tasks.md v0.1（baseline dd70854）
**模式**：foreground

> 备注：原计划用 `codex exec --sandbox read-only`（gpt-5.5 high reasoning xhigh），跑 20+ 分钟 0 字节输出被 kill。改用 Claude general-purpose Agent 跑（更快 + 工具调用更高效），review 质量经 spot-verify 实测确认（HIGH 1 通过 verify agent_decision.py:199-208 cache hit 路径 strip metadata 实测完全成立）。

## Findings 总览

| 严重度 | 数量 | 处理状态 |
|--------|------|----------|
| HIGH | 3 | 全接受（commit 前必修）|
| MEDIUM | 7 | 6 接受 + 1 推迟 F107 |
| LOW | 3 | 2 接受 + 1 ignore |

## 处理表

### HIGH（commit 前必修）

| # | Section | Concern 摘要 | 处理决议 |
|---|---------|-------------|---------|
| **H1** | plan §4.5 / T-D-4 | **方案 A "重复 resolve_behavior_pack" 在 BEHAVIOR_PACK_LOADED 上语义破损**：cache hit 路径（agent_decision.py:199-208）显式 strip `cache_state`/`pack_source` 标记。第一次 resolve 在 `_fit_prompt_budget` 内部装满 cache，后续重复 resolve 必命中 cache hit 路径，metadata.get("cache_state") == "miss" 永远 False → LOADED 永远不 emit → AC-D1/AC-D5 永远不通过 | **接受 → 改方案 B**：plan §4.5 / tasks T-D-4 改为方案 B（_fit_prompt_budget 返回值附加 loaded_pack 引用，第一次 resolve 时记录 cache_state="miss" 的 pack，向上 propagate 到 build_task_context async 层 emit）|
| **H2** | plan §2.2 / T-A-1 | **agent_session_id 派生策略过度复杂**：plan §2.2 设计的 task_metadata → session_state 反查 → 空字符串链路；实际 `_materialize_delayed_recall_once:1577` 已 fetch ContextFrame，**ContextFrame.agent_session_id（packages/core/.../agent_context.py:441）是直接可读的强一致来源**。fallback 空字符串污染 list_recall_frames(agent_session_id=...) 过滤 + 破坏 audit chain | **接受 → 改 plan**：plan §2.2 / tasks T-A-1 改为直接用 `frame.agent_session_id`（同一 frame 也用于派生 audit_agent_runtime_id 在 line 1660）。删除 task_metadata 派生 + session_state 反查链路 |
| **H3** | spec §3.1 块 B / plan §5 / T-B-5 | **store 层 list_recall_frames 缺 offset / created_after / created_before / count**——但 endpoint signature 全部声称支持。实测 store 层只支持 7 维等值过滤 + ORDER BY created_at + LIMIT，**无 offset、无时间窗、无 count**。tasks T-B-5 仅识别 count 缺失 | **接受 → 改 plan + tasks**：T-B-5 范围扩大为 store 层补 offset 参数 + created_after/created_before 字段过滤 + count_recall_frames 方法。endpoint signature 保持不变（spec §3.1 完整 7 维 + 时间窗 + 分页）|

### MEDIUM

| # | Section | Concern 摘要 | 处理决议 |
|---|---------|-------------|---------|
| **M4** | plan §4.4 / §3.3 D-1 | **agent_kind 字段 hasattr fallback 多余**：AgentProfile.kind 是 StrEnum 默认 "main"，hasattr 总成立；plan §4.3 注释 "main / worker / subagent" 但 subagent 值 F097 才有；F107 schema 演化时新增不向后兼容 | 接受 → plan §4.4 helper 改 `str(agent_profile.kind)` 直接派生；F096 emit 仅 `main` / `worker` 两值（不预占 `subagent`）；M9 schema_version 处理见 |
| **M5** | spec §3.4 E-2 | **frontend 路径 spec 与 plan 不一致**：spec §3.4 写 `frontend/src/pages/MemoryPage.tsx` + `frontend/src/components/memory/...`；实际全在 `frontend/src/domains/memory/`（plan §0.8 已校正） | 接受 → spec §3.4 E-2 路径全部改 `frontend/src/domains/memory/...`；同步 plan §6.1 |
| **M6** | plan §6.4 / T-E-3+T-E-4 | **Phase E URL state machine 没先例**：MemoryPage 当前 useState + workbench snapshot；plan §6.4 假设 useSearchParams 监听——无现有 pattern 可循。可能引起 view 切换 race / re-render bug | 接受 → plan §6.4 加：Phase E 单测必须含 useSearchParams race 用例（params 与 useState 双向同步 / view 切换 effect）；Phase E 加 1 个评估 task：是否抽离独立 useSearchParams hook helper |
| **M7** | spec §3.1 / plan §5.4 | **RecallFrameItem 字段不完整**：spec §3.1 声称"完整 16 字段"——实测 RecallFrameItem 仅 13 字段（缺 metadata / source_refs / budget）。plan §5.4 RecallFrameListDocument 没说补字段 | 接受 → spec §3.1 + tasks T-B-2 加：RecallFrameItem（packages/core/.../control_plane/session.py:135）扩展补缺失字段（metadata / source_refs / budget）；frontend types/index.ts 同步扩展 |
| **M8** | plan §3.2 / spec §3.2 块 C | **sync 路径 emit 缺 idempotency_key**：延迟路径用 `idempotency_key=f"{...}:event"`（task_service.py:1722）；sync 路径 plan §3.2 写"无 idempotency key"。同 task 多次 dispatch（resume / retry）会重复 emit 同 task_id 多个事件 | 接受 → plan §3.2 改：sync 路径 idempotency_key = `f"{recall_frame_id}:event"`（recall_frame_id 唯一对应 dispatch 实例）；spec §3.2 显式约定"一次 build_task_context 一次 emit"；tests 加 resume / retry 双 emit 用例 |
| **M9** | spec 全局 / plan §4.3 | **F107 schema 演化无预留**：BEHAVIOR_PACK_USED.agent_kind / RecallFrame.agent_runtime_id 含义 F107 完全合并 WorkerProfile 时可能改变。F096 没有 schema_version 字段 | **接受推迟到 F107**：F096 不引入 schema_version 字段（M5 阶段 1 范围内 schema 一次稳定即可）；spec §6 显式归档"F107 schema 演化时本事件可能 break"；BEHAVIOR_PACK_USED 不预占 `subagent` 值，由 F097 引入 |
| **M10** | tasks T-D-6 / T-F-1 / T-F-2 | **Phase F 集成测对 F095 fixture 紧耦合**：全部"复用 F095 handoff 提供的 fixture"——`test_end_to_end_worker_pack_to_envelope_with_worker_variants`。F095 fixture 修改时 F096 测试被动 break | 接受 → plan §7.4 加：F095 fixture 是稳定契约表面；如担心紧耦合，独立提取 `f095_worker_pack_fixture` 抽到 `tests/conftest.py`；不阻 F096 |
| **M11** | spec §1 / plan §1 | **F096 范围确实过大**：6 块（4 域 = backend memory + behavior + control_plane + frontend），54 task，按 F094/F095 节奏 ~5 commit 各 8 phase 估单 Feature 一周以上 | **接受 → 改 plan + tasks Phase 顺序**：A → C → **B** → D → E → F（B 提前到 D 之前；前端 E 依赖 B 的 endpoint，D 不依赖 E）。**不拆 F096a/F096b**——F096 是阶段 1 收尾整合点，承接 F094/F095 推迟项是约定 |

### LOW

| # | Section | Concern | 处理 |
|---|---------|---------|------|
| **L12** | plan §4.5 emit 时机 | emit 在 `_fit_prompt_budget` 返回后 + RecallFrame 创建/persist 之前——若后续 RecallFrame 创建失败，LOADED/USED 仍 emit，但 RecallFrame 没写——audit chain 临时断裂 | 接受 → plan §4.5 加：emit 改在 `await self._stores.conn.commit()` (line 936) 之后（更稳的事务语义；commit fail 时不会有事件）|
| **L13** | tasks T-A-3 / T-C-4 | `test_recall_frame_dual_path.py` 命名歧义（worker dispatch 也走 sync 主路径，不是第三条独立路径） | 接受 → tasks 改 `test_recall_frame_persist_and_emit_paths.py` |
| **L14** | review 关键判断 #2 | `_InlineReplyLLMService`（orchestrator.py:1112）路径走 build_task_context 会触发 BEHAVIOR_PACK_USED emit——这条路径不算"真实 LLM 决策"——可能污染 USED 频次语义 | ignore → F096 范围内 USED 频次定义为 "build_task_context 调用一次 emit 一次"；inline reply 是合法的 dispatch（虽然非用户驱动），emit 反映真实 dispatch 频次合理；plan 阶段评估推迟到实施阶段（如发现真实污染再调整）|

## 关键判断（reviewer 视角，已纳入 plan §0 update）

1. **F096 范围合理性**：整体可推进；Phase 顺序 A→C→**B**→D→E→F（B 提前），不拆 F096a/F096b
2. **plan §0.4 唯一入口主张**：实质成立——所有 dispatch 路径（message / chat route / worker_runtime InlineRuntimeBackend / GraphRuntimeBackend / orchestrator agent direct execution / clarification reply / spawn）都收敛到 `task_service._build_task_context:1122` → `agent_context.build_task_context:591`
3. **plan §4.5 重复 resolve 性能/正确性**：性能 fine（cache hit + dict lookup < 1µs），但**正确性破损**——方案 A invalid，必须改方案 B
4. **MEMORY_RECALL_COMPLETED 双路径不重复**：sync 与 delayed 互斥（delayed 总在 sync 后）；但 sync + retry/resume 仍可能多次 emit → idempotency_key 必加
5. **frontend 路径 spec 必须同步**：driver 原则 spec 是上游

## 修订动作（commit 前必做）

- [x] codex-review-spec-plan.md 产出（本文）
- [ ] spec.md 修订：M5 frontend 路径 + M9 schema 演化归档 + M7 RecallFrameItem 字段说明
- [ ] plan.md 修订：H1 方案 B / H2 frame.agent_session_id / H3 store 层扩展 / M4 agent_kind / M6 useSearchParams race / M8 sync idempotency_key / M10 fixture 契约 + L12 emit-after-commit + 关键判断 #2 入口主张 verify
- [ ] tasks.md 修订：M11 Phase 顺序 A→C→B→D→E→F / T-A-1 frame.agent_session_id / T-B-5 store 层扩展 / T-C-1 idempotency_key / T-D-2 删 hasattr / T-D-4 方案 B / T-A-3 命名 / T-B-2 RecallFrameItem 字段
- [ ] 修订完成后 commit spec + plan + tasks + codex-review-spec-plan.md + codebase-scan.md + trace.md（不主动 push）

## Review 偏离记录

- 触发方式偏离：`codex exec --sandbox read-only`（gpt-5.5）卡 reasoning 20+ 分钟 0 输出被 kill；改用 Claude general-purpose Agent 跑（更快）
- HIGH 1 spot-verify 通过：实测 agent_decision.py:199-208 cache hit 路径 strip metadata 完全成立
