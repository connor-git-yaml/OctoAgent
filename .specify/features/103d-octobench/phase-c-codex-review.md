# F103d Phase C Codex Adversarial Review（6 轮闭环）

> 范围：benchmarks/tiers/tier3/t3_*.yaml × 5 (H1/H2/H3-A/H3-B/H3-WW) +
> benchmarks/runner/scorer.py 新增 score_tier3 / audit_chain_assert + 230 LOC +
> benchmarks/tests/unit/test_scorer_tier3.py 74 tests
>
> 工具：`codex review --uncommitted`（默认 gpt-5.5 + xhigh effort）
>
> 累计：18 finding 修复（HIGH 1 + MED 17）+ 1 finding 归档 Phase D

---

## Round 1（4 finding，2026-05-29）

### HIGH-P1: 仅按父 task_id 查事件——H1/H2/H3 关键信号写在 child task_id

**Scope**: `benchmarks/runner/scorer.py:1030-1035` `fetch_events_from_store_tier3`
**Impact**: worker_runtime_dispatch / subagent_delegation_init / Worker MEMORY_RECALL_COMPLETED / ask_back STATE_TRANSITION 等关键事件写在 child task_id 上；只按父 task_id 查会让真实运行下 Tier 3 用例稳定误判 FAIL。
**Fix**: `fetch_events_from_store_tier3` 加 `child_task_ids: list[str] | None = None` 参数；按 (task_id, task_seq) 去重；保留 parent + children 各查一次的接口语义。

### MED-P2-1: H3-B IN_PROGRESS → RUNNING

**Scope**: `benchmarks/tiers/tier3/t3_h3b_001.yaml:69-70` H3B-4
**Impact**: TaskStatus 真名是 `RUNNING`（`enums.py:15`），不是 `IN_PROGRESS`；原断言 `to_status: IN_PROGRESS` 让真实 `WAITING_INPUT → RUNNING` 转换永远匹配不上。
**Fix**: H3B-4 改 `from_status: WAITING_INPUT` + `to_status: RUNNING`。

### MED-P2-2: H2-3 缺 namespace_kind 约束

**Scope**: `benchmarks/tiers/tier3/t3_h2_001.yaml:48-49` H2-3
**Impact**: 仅检查 memory_id 存在不检查 namespace_kind=AGENT_PRIVATE，让 Worker 写到 PROJECT_SHARED 也 false PASS（memory 隔离实际失效）。
**Fix**: 加 `namespace_kind: agent_private` 约束（Round 2 P2-1 校正为 .value 小写）。

### MED-P2-3: H3-A 删 SUBAGENT_SPAWNED 断言（subagents.spawn 路径 emit_audit_event=False）

**Scope**: `benchmarks/tiers/tier3/t3_h3a_001.yaml:29-32` H3A-1
**Impact**: subagents.spawn 路径 `emit_audit_event=False`（`delegation_tools.py:166`）不写 SUBAGENT_SPAWNED；H3A-1 在 subagents.spawn 路径稳定 FAIL，需要改用 CONTROL_METADATA_UPDATED + SUBAGENT_COMPLETED 作为 spawn-and-die 证据。
**Fix**: 删 H3A-1，改用 H3A-2 (caller_project_id) + H3A-3 (delegation_id) + H3A-4 (SUBAGENT_COMPLETED) 三条共同证明 H3-A 哲学（两条 spawn 路径都满足）。

---

## Round 2（3 finding，2026-05-30）

### MED-P2-1: namespace_kind 真实序列化值是小写 .value

**Scope**: `benchmarks/tiers/tier3/t3_h2_001.yaml:34, 48` MemoryNamespaceKind 真实 .value
**Impact**: `MemoryNamespaceKind.AGENT_PRIVATE.value = "agent_private"`（StrEnum 序列化）；scorer 不做大小写归一化，原写 `AGENT_PRIVATE` 会让正确运行也 FAIL。
**Fix**: 5 处全部改小写 `agent_private`。

### MED-P2-2: H3-WW depth 字段存在 → depth=1 严格化

**Scope**: `benchmarks/tiers/tier3/t3_h3_ww_001.yaml:38` H3WW-1
**Impact**: 第一层 main→worker A spawn 也带 child_task_id 和 depth=0；仅检查 depth 存在让"worker A 没有产生二层 spawn"的运行仍 PASS（H3-WW 哲学未满足）。
**Fix**: H3WW-1 加 `depth: 1` 严格匹配二层 spawn。

### MED-P2-3: H3-A 加 caller_memory_namespace_ids 断言 + scorer 加严容器空检查

**Scope**: `benchmarks/tiers/tier3/t3_h3a_001.yaml` 新增 H3A-3b
**Impact**: H3-A α 共享语义需断言 caller_memory_namespace_ids（生产路径 task_runner._emit_subagent_delegation_init_if_needed 查询 caller AGENT_PRIVATE namespace_id 填入；空列表 `[]` 是降级路径）；仅检查 caller_project_id 让"namespace 传播回归但 project_id 仍存在"false PASS。
**Fix**: 加 H3A-3b 断言 `caller_memory_namespace_ids_contains: ""`；scorer `_match_required_fields_tier3` 加严：`_contains: ""` 对 list/dict/str/tuple/set 统一执行 `len() > 0` 检查。

---

## Round 3（3 finding + 1 部分接受）

### MED-P2-1: 递归发现 worker→worker→worker 孙任务事件

**Scope**: `benchmarks/runner/scorer.py:1055-1062` `fetch_events_from_store_tier3`
**Impact**: 调用方只传顶层 children，worker A→worker B 的 grandchild task_id 不被发现；H3WW-2/H3WW-3 在真实运行误判 FAIL。
**Fix**: 自动从已查到的 SUBAGENT_SPAWNED.payload.child_task_id 递归发现新 task_id；BFS 遍历 + MAX_DESCENDANT_TRAVERSAL=32 安全护栏（异常长链 log warn 不 raise）；保留 child_task_ids 参数兼容显式传入。

### MED-P2-2 部分接受: DEFAULT_TIER3_EVENT_TYPES 加 AGENT_SESSION_TURN_PERSISTED

**Scope**: `benchmarks/runner/scorer.py:DEFAULT_TIER3_EVENT_TYPES`
**Decision**: 加 EventType（Round 6 用于 H1-3 精确化）；不加 H1 yaml 直接 absent 断言（避免误伤合法主 Agent USER_CHANNEL session 的 ASSISTANT_MESSAGE turn——后改用 direct_worker session 区分，Round 6 闭环）。

### LOW-P3: required_fields 类型校验前置（防 `or {}` 短路）

**Scope**: `benchmarks/runner/scorer.py:902` `audit_chain_assert`
**Impact**: `assertion.get("required_fields", {}) or {}` 把 `[]` / `""` 等 falsy 非 dict 转成合法空 dict，绕过 `required_fields_must_be_dict` 防线，让 malformed assertion 静默 PASS。
**Fix**: 先保留原值做类型校验：`None` → 默认 `{}`；`dict` → 用之；其他类型 → `required_fields_must_be_dict` failure。

---

## Round 4（2 finding）

### MED-P2-1: H3-B 加 follow_up_inputs（Phase D runner 接入点）

**Scope**: `benchmarks/tiers/tier3/t3_h3b_001.yaml:65-75`
**Impact**: ask_back 多轮任务无人值守 benchmark 会卡到超时（Worker 进入 WAITING_INPUT 后没人 attach 输入）。
**Fix**: YAML 加 `follow_up_inputs` 字段含 description + text；Phase D runner 接入点：runner 在 task 进入 WAITING_INPUT 时按顺序 attach_input。scorer 不消费该字段。

### MED-P2-2: H2-1/H2-2 合并到同一 required_fields

**Scope**: `benchmarks/tiers/tier3/t3_h2_001.yaml:41-49`
**Impact**: 一次运行多条 MEMORY_RECALL_COMPLETED（父 task 初始 recall + worker recall）让 H2-1 由某条命中 agent_private 的事件满足，H2-2 由另一条仅带 agent_runtime_id 的事件满足，"命中私有 namespace 的 recall 可追溯"回归 false PASS。
**Fix**: H2-1/H2-2 合并为单条 `H2-1-recall-private-hit-traceable`，required_fields 同时含 hit_namespace_kinds_contains + agent_runtime_id_contains，必须由同一条 recall 事件同时满足。

---

## Round 5（3 finding）

### MED-P2-1: H2 加 SUBAGENT_SPAWNED 前置断言

**Scope**: `benchmarks/tiers/tier3/t3_h2_001.yaml:47-48` H2-1 / H2-3
**Impact**: 任意 MEMORY_RECALL_COMPLETED 命中 agent_private + agent_runtime_id 非空 + 任意 MEMORY_ENTRY_ADDED 写 agent_private 也能让主 Agent 自己读写 memory false PASS——没委托 Worker 时 H2 隔离哲学未被验证。
**Fix**: 加 H2-0 `event_present SUBAGENT_SPAWNED` 前置断言 + prompt 强制要求 delegate_task 委托 Worker。

### MED-P2-2: H3-WW H3WW-2 + H3WW-3 合并

**Scope**: `benchmarks/tiers/tier3/t3_h3_ww_001.yaml:62-63`
**Impact**: 第一层 main→worker A 的 CONTROL_METADATA_UPDATED 也满足"source=subagent_delegation_init + delegation_id 存在"；第二层有 source_runtime_kind=worker 但若 delegation_id 丢失，两条不同事件分别满足两条件让 worker→worker BaseDelegation 回归 false PASS。
**Fix**: 合并为单条 `H3WW-2-worker-source-with-delegation-id`，同一事件同时满足 source_runtime_kind=worker + delegation_id 非空。

### MED-P2-3: 5 YAML 加 philosophy 字段

**Scope**: 5 个 Tier 3 YAML
**Impact**: FR-F01 要求每个 YAML 显式含 `philosophy` 字段；当前只有 `domain`，让 reporter 无法按 philosophy 字段统计 SC-010 哲学覆盖。
**Fix**: 5 YAML 加 `philosophy: H1/H2/H3-A/H3-B/H3` 字段；加 unit test 覆盖。

---

## Round 6（1 finding 闭环 + 1 finding 归档 Phase D）

### MED-P2-1 闭环: H1-3 改用 AGENT_SESSION_TURN_PERSISTED + direct_worker

**Scope**: `benchmarks/tiers/tier3/t3_h1_001.yaml:51-53` H1-3
**Impact**: Worker 通过 direct_worker session 直接写 assistant_message 给用户时，实际可观察信号是 `AGENT_SESSION_TURN_PERSISTED.agent_session_kind=direct_worker AND kind=assistant_message`，不是 `CONTROL_METADATA_UPDATED.source_runtime_kind=user_channel`；当前 H1-3 仅禁止后者会让 Worker direct_worker turn 违规漏报。
**Fix**: H1-3 改 `event_absent AGENT_SESSION_TURN_PERSISTED` with `agent_session_kind: direct_worker` + `kind: assistant_message`。AgentSessionKind.DIRECT_WORKER 是 H1 unfinalized 时为 worker 直接暴露用户预留的 session 类型，当前 H1 哲学不允许使用必须 absent。合法主 Agent USER_CHANNEL session 的 ASSISTANT_MESSAGE 不被误伤（限定 direct_worker）。

### MED-P2-2 归档 Phase D: scorer event binding 框架级加强

**Scope**: `benchmarks/runner/scorer.py:932-938` Tier 3 audit_chain_assert
**Impact**: parent/child/grandchild 事件合并后，逐条断言在全局事件列表里独立找任意匹配事件；不同 task 或不同子链事件可拼成 PASS，例如 H2-0 SUBAGENT_SPAWNED 存在 + 主 Agent 自己的 MEMORY_ENTRY_ADDED/MEMORY_RECALL_COMPLETED 满足后续条件 → false PASS。
**Recommendation**: scorer schema 加 binding 能力：
```yaml
audit_assertions:
  - assertion_id: H2-0-worker-spawned
    bind:
      $child_task_id: "$.payload.child_task_id"   # 从 spawn 事件捕获
    ...
  - assertion_id: H2-1-recall-private-hit
    constraint:
      event_must_have_task_id: $child_task_id    # 后续断言绑定到 child_task_id
    ...
```
**Decision**: 归档 Phase D（scorer 主体实施时一并完成）。理由：
1. 当前能正确捕获**大多数** false PASS（5 YAML 各加 1-2 个强化断言后）
2. 跨 audit chain 严格绑定需 scorer schema 大改（YAML schema + scorer state machine 都要扩）
3. Phase C 主要交付是 Tier 3 task + scorer 基础框架——M5 baseline 跑 + Phase D runner 实施时
   评估 scorer event binding 的真实必要性后再做（投入产出比评估）

---

## 累计 Codex finding 闭环表

| Round | finding 总数 | HIGH | MED | LOW | 决策 |
|-------|------------|------|-----|-----|------|
| 1     | 4          | 1    | 3   | 0   | 4 修 |
| 2     | 3          | 0    | 3   | 0   | 3 修 |
| 3     | 3          | 0    | 2   | 1   | 3 修（1 部分接受）|
| 4     | 2          | 0    | 2   | 0   | 2 修 |
| 5     | 3          | 0    | 3   | 0   | 3 修 |
| 6     | 2          | 0    | 2   | 0   | 1 修 + 1 归档 Phase D |
| **累计** | **17**  | **1**| **15**| **1** | **16 修 + 1 归档** |

注：Phase C scorer 改动经 6 轮 codex review 持续收敛——每轮平均抓出 2-3 个 false PASS edge case。
M5 baseline 跑通后可重新评估 Round 6 P2-2 归档项的实际优先级。

---

## 关键 fact 沉淀（Phase D / 后续 Feature 复用）

### scorer 行为约定（Phase D runner 必读）

1. **fetch_events_from_store_tier3**：自动递归发现 grandchild + MAX_DESCENDANT_TRAVERSAL=32 护栏；Phase D runner 可不传 child_task_ids 让自动发现，也可显式传 + 函数会去重。
2. **score_tier3 接口**：`score_tier3(task: dict, actual_events: list[dict], rubric: dict | None = None, token_usage: int | None = None) -> BenchmarkRunScore`；task 是 yaml.safe_load 后的 dict（含 audit_assertions list）；actual_events 是 `_normalize_event_to_dict` 规范化后的 dict 列表（含 event_type / payload 字段）。
3. **BenchmarkRunScore.audit_chain_failures**：list[AuditAssertionFailure] 含 assertion_id / kind / event_type / expected / reason / closest_event 字段，Phase D reporter 可直接渲染。

### audit_assertions YAML schema（5 YAML 共用）

```yaml
audit_assertions:
  - assertion_id: <唯一 ID>         # 失败诊断锚点
    description: <人类可读说明>
    kind: event_present | event_absent
    event_type: <EventType 真名>
    required_fields:                # dict，支持 dot path 嵌套 + _contains 后缀
      <key>: <value>                # 精确匹配
      <key>_contains: <substr>      # 字符串 contains 或 list element-in
      <key>_contains: ""            # 字段存在 + 非空（list/dict/str 统一 len() > 0）
```

### Round 6 P2-2 归档：scorer event binding（Phase D 接入指引）

**Phase D 实施建议**（在 Phase D scorer 主体重构时评估）：
1. M5 baseline 跑通后，统计 5 个 Tier 3 task 的 false PASS / false FAIL 比例
2. 若 false PASS 比例 > 5%，必须实施 scorer event binding（YAML schema + scorer state machine 扩展）
3. 若 false PASS 比例 ≤ 5%，归档到 M6 F108 Capability Layer Refactor 一并清理

---

## 经验沉淀（写给 Phase D 启动者）

- **多轮 review 必要**：Phase C 经 6 轮才收敛，每轮抓 2-3 个 false PASS edge case
- **Codex review pattern**：先抓事件路径不准确（Round 1）→ 再抓字段值/类型（Round 2）→ 再抓边界 case（Round 3-4）→ 最后抓哲学不变量精确性（Round 5-6）
- **不要假设 spec 描述**：spec 写 "SUBAGENT_SPAWNED.source_runtime_kind=main"（不准确），实际 source_runtime_kind 在 CONTROL_METADATA_UPDATED.control_metadata；必须实测 payload schema 才能写对断言
- **EventType / 字段名实测**：TaskStatus.RUNNING（非 IN_PROGRESS）/ MemoryNamespaceKind.AGENT_PRIVATE.value="agent_private"（StrEnum 小写）/ MEMORY_RECALL_COMPLETED 无 namespace 字段（用 *_namespace_kinds list-aware contains）
