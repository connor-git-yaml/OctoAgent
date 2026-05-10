# F097 Subagent Mode Cleanup — 需求澄清报告

**生成日期**: 2026-05-10
**基于 spec 版本**: v0.1
**澄清方法**: "信任但验证"——非 CRITICAL 问题自动解决，CRITICAL 问题上报用户拍板

---

## 自动解决的澄清（AUTO-CLARIFIED）

### Q-1：SubagentDelegation 持久化方式（写入何处）

**问题**: spec §3 Gap-A 指定 SubagentDelegation 为 Pydantic model，位置为 `core/models/`。但 spec 未明确该 model 是否需要写入 SQLite（独立表？扩展 task metadata？）。AC-E1 要求 `SubagentDelegation.closed_at` 同步更新，表明必须有持久化路径。

**影响**: AC-A1 / AC-A2 / AC-E1（closed_at 同步更新依赖持久化路径设计）

**选项**:
| 选项 | 描述 | 影响 |
|------|------|------|
| A | SubagentDelegation 序列化写入 task metadata（`child_task.metadata["subagent_delegation"]`）| 零 schema migration，利用 task store 已有持久化 |
| B | 独立 SQLite 表（`subagent_delegation`）| 更结构化，查询灵活，但需 schema migration |

**建议默认**: 选项 A——写入 child task metadata。理由：SubagentDelegation 生命周期与 child_task 完全绑定（AC-E1 cleanup 通过 child_task_id 反查），天然落在 task store；spec 明确"无数据迁移"（§15 复杂度评估），独立表引入 schema migration 违反此原则。tech-research 推荐"轻量 ephemeral 方案 A"与此一致。

**[AUTO-CLARIFIED: 选项 A，写入 child task metadata — 与 spec §15 "无数据迁移"原则一致]**

---

### Q-2：`_ensure_agent_session` 的 target_kind 信号传递路径

**问题**: spec §3 Gap-B 描述"当 `target_kind=subagent` 且有 `parent_agent_session_id` 时"创建 SUBAGENT_INTERNAL 路径，但 spec §13 也指出"plan 阶段需精确定位 `target_kind` 信号传递路径（从 child_message 到 `_ensure_agent_session` 的参数链）"。tech-research 实测的 `_ensure_agent_session`（agent_context.py:2337）当前接收的参数不含 `target_kind` 字段——需要明确信号来源：从 `control_metadata["target_kind"]` 读取，还是从 AgentRuntime 对象的 `delegation_mode` 字段读取。

**影响**: AC-B1 / AC-B2（路径判断准确性直接影响 Worker 路径是否误入 SUBAGENT_INTERNAL）

**选项**:
| 选项 | 描述 | 影响 |
|------|------|------|
| A | 从 `agent_runtime.delegation_mode == "subagent"` 判断 | 复用已有字段（F090 引入），无需改函数签名 |
| B | 从 `control_metadata["target_kind"]` 读取并传入 `_ensure_agent_session` 为新参数 | 语义更直接，但需改函数签名（可能影响所有调用方）|

**建议默认**: 选项 A——使用 `agent_runtime.delegation_mode == "subagent"` 作为判断条件。理由：`delegation_mode` 已通过 `delegation_mode_for_target_kind(SUBAGENT)` 返回 `"subagent"`（tech-research §3.4，delegation_plane.py:949），信号已存在于 agent_runtime 对象中；避免改函数签名降低 R2 风险（spec §14）。

**[AUTO-CLARIFIED: 选项 A，使用 agent_runtime.delegation_mode == "subagent" — 最小改动，复用 F090 引入的字段]**

---

### Q-3：ephemeral AgentProfile 的 closed_at 同步机制

**问题**: AC-C2 要求 ephemeral profile 的 `closed_at` 随 `SubagentDelegation.closed_at` 同步关闭，但 ephemeral profile 不写入持久化 store（AC-C1），那么 `closed_at` 只存在于内存中的 profile 实例。若进程重启，ephemeral profile 实例消失，只有 SubagentDelegation 在 task metadata 中持久化保留 closed_at。这两者是否需要独立同步，还是 ephemeral profile 的 closed_at 仅为运行时状态，不需要持久化检查点？

**影响**: AC-C2 / AC-E2（幂等保证的检查对象）

**选项**:
| 选项 | 描述 | 影响 |
|------|------|------|
| A | ephemeral profile 的 closed_at 是纯运行时字段，重启后随 profile 实例消失；cleanup 幂等性通过 SubagentDelegation.closed_at 持久化字段检查 | 实现简单，SubagentDelegation 是 single source of truth |
| B | ephemeral profile 的 closed_at 需要独立写入某处持久化（如 task metadata 的另一字段）| 冗余但完整 |

**建议默认**: 选项 A——ephemeral profile 的 `closed_at` 为纯运行时字段，AC-E2 幂等性通过 `SubagentDelegation.closed_at`（持久化在 task metadata）检查。理由：spec 明确 ephemeral profile "不写入持久化 profile store"（AC-C1），保持此原则的一致性；cleanup 幂等检查目标已有 SubagentDelegation 持久化字段。

**[AUTO-CLARIFIED: 选项 A，ephemeral profile closed_at 为纯运行时字段 — 与 AC-C1 ephemeral 原则一致]**

---

### Q-4：RuntimeHintBundle 中 `recent_worker_lane_*` 字段的拷贝范围

**问题**: AC-D1 要求拷贝 `surface` / `tool_universe` / `recent_worker_lane_*` / `recent_failure_budget` 字段，但 `recent_worker_lane_*` 是动态字段名（带通配符），spec 未列出具体字段名。tech-research §3.3 描述当前 `child_message.control_metadata` 不含这些字段，但也未列出具体字段。若拷贝范围过宽（如包含 recent conversation 摘要），可能带来不必要的 payload 膨胀。

**影响**: AC-D1（"字段值与 caller 原始值一致"的验证需要确定字段名列表）

**选项**:
| 选项 | 描述 | 影响 |
|------|------|------|
| A | 仅拷贝 `surface` 和 `tool_universe`（最小集）；`recent_worker_lane_*` 留 plan 阶段精确定位后决定 | 保守，不过拷贝 |
| B | 拷贝 `RuntimeHintBundle` 全部字段（完整 copy）| 简单，但可能引入不相关 hints |

**建议默认**: 选项 A——plan 阶段精确定位 RuntimeHintBundle 字段定义（`behavior.py:206`），然后仅拷贝 `surface` + `tool_universe` + 实测存在的 `recent_failure_budget`；`recent_worker_lane_*` 字段等 plan 阶段实测后决定是否包含。理由：spec §3 Gap-D 明确只拷贝有限字段，不是整体 copy；plan 阶段有"精确定位"任务，此问题归 plan 阶段。

**[AUTO-CLARIFIED: 选项 A，最小集拷贝，plan 阶段精确定位字段名 — 避免过度 copy，plan 阶段实测决定]**

---

### Q-5：cleanup hook 触发后 SubagentDelegation.closed_at 写入时机

**问题**: AC-E1 要求"子任务进入终态时，SubagentDelegation.closed_at 同步更新"，但 spec 未明确是：(a) 在同一事务内同步写入，还是 (b) cleanup hook 异步补写。task metadata 更新是否需要与 session close 在同一 SQLite 事务中（原子性），以避免 session=CLOSED 但 delegation.closed_at=None 的中间状态？

**影响**: AC-E1 / AC-E2（幂等保证在事务边界处依赖 closed_at 状态）

**选项**:
| 选项 | 描述 | 影响 |
|------|------|------|
| A | session close + SubagentDelegation.closed_at 在同一 cleanup 函数中顺序写入（不要求单一 SQLite 事务）| 简单，依赖现有 store 方法的幂等性 |
| B | 两次写入包含在同一 SQLite 事务内（需要事务支持）| 原子性强，但改动较大 |

**建议默认**: 选项 A——顺序写入，不要求单一事务。理由：SQLite WAL 已提供行级原子性；cleanup 的幂等性通过 AC-E2 单独保证；F097 "MEDIUM 复杂度"评估不包含新并发控制（spec §15），引入跨 store 事务超出复杂度边界。

**[AUTO-CLARIFIED: 选项 A，顺序写入不强制单一事务 — 与 MEDIUM 复杂度评估和现有 store 层设计一致]**

---

### Q-6：SUBAGENT_COMPLETED 事件是否需要新建

**问题**: AC-EVENT-1 说"若 F092 未实现则 F097 补充"SUBAGENT_COMPLETED 事件，但 spec 没有进一步澄清 F092 是否已实现此事件，也没有 SUBAGENT_COMPLETED 的 payload schema。tech-research 全文未提及 SUBAGENT_COMPLETED 事件——这表明 F092 可能未实现。F097 是否需要定义并 emit 此事件，还是仅在 spec 层标注为"待 plan 阶段实测确认"？

**影响**: AC-EVENT-1 / Gap-G（可观测性范围）

**建议默认**: 在 plan 阶段实测 EventStore 中是否有 `SUBAGENT_COMPLETED` 事件类型定义。若无，F097 补充 emit，payload 最小字段：`{delegation_id, child_task_id, final_status, closed_at}`，与 SUBAGENT_SPAWNED payload 风格一致。若已有则直接用。此为 plan 阶段侦察项，不需要 GATE_DESIGN 拍板。

**[AUTO-CLARIFIED: plan 阶段实测确认 SUBAGENT_COMPLETED 是否已存在；若无则 F097 补充 emit，payload 遵循 SUBAGENT_SPAWNED 风格]**

---

## CRITICAL 问题（需用户决策）

### CRITICAL-1：SubagentDelegation 与 cleanup hook 的 SQL 查询方式——task_id vs parent_worker_runtime_id

**上下文**: spec §3 Gap-E 推荐"cleanup 改用 task_id 维度查询"；tech-research R5 也指出 `list_subagent_sessions(parent_worker_runtime_id)` 查询在实际路径中可能信号不准确（R5 缓解策略：改用 task_id）。然而 spec §13 说"plan 阶段需精确定位 cleanup hook 的最佳挂载点"，并且 tech-research §5 说现有 `list_subagent_sessions` 是按 `parent_worker_runtime_id` 查的——而 AgentSession 是否有 `task_id` 字段用于直接过滤？spec 未确认 AgentSession 表是否有 task_id 索引。

这不是 plan 阶段问题，而是 **AC-E1 核心查询路径的设计决策**：

| 选项 | 描述 | 影响 |
|------|------|------|
| A | cleanup 通过 `child_task_id` 直接查 AgentSession（需确认 AgentSession 有 task_id 字段） | 精确，但 AgentSession 表可能无 task_id 字段 |
| B | cleanup 通过 `parent_work_id` → 反查 AgentRuntime → 查 parent_worker_runtime_id 链 | 多跳查询，复杂但不依赖新字段 |
| C | cleanup 在 spawn 时记录 `(child_task_id, subagent_session_id)` 映射，cleanup 直接用此映射 | 需要在 SubagentDelegation 中增加 `child_session_id` 字段 |

**推荐**: 选项 C——在 SubagentDelegation model 增加 `child_agent_session_id` 字段，spawn 时记录，cleanup 直接通过此字段定位 session。理由：避免 plan 阶段依赖实测结果的不确定性（A 取决于 schema，B 多跳复杂）；SubagentDelegation 本身已记录委托上下文，增加 session 引用最自然；AC-E2 幂等检查也可依赖此字段。

**需要用户拍板的问题**: SubagentDelegation model 是否增加 `child_agent_session_id` 字段（影响 AC-A1 字段列表）？还是 plan 阶段实测 AgentSession.task_id 后再决定？

---

### CRITICAL-2：BEHAVIOR_PACK_LOADED 的 `agent_kind` schema 版本兼容

**上下文**: spec §3 Gap-G 说 Gap-C 实施后 `agent_kind` 字段自动返回 `"subagent"`，并引用 F096 已稳定的 `make_behavior_pack_loaded_payload` schema。但 F096 设计时（`agent_decision.py:377` 注释）明确"不预占 `subagent`，由 F097 引入"——这表明 F096 消费方（如 frontend、EventStore 查询）可能已对 `agent_kind` 值做了枚举校验（仅允许 `"main"` / `"worker"`）。

F097 引入 `"subagent"` 值时，**是否需要 schema version bump 或向后兼容声明**？如果 F096 Phase E frontend（已被推迟）有 `agent_kind` 枚举限制，F097 直接写入 `"subagent"` 可能导致 frontend 解析失败。

| 选项 | 描述 | 影响 |
|------|------|------|
| A | 直接引入 `"subagent"` 值，无 schema version bump | 简单，但若有消费方硬编码 `["main","worker"]` 则 break |
| B | 增加 schema version bump（`BEHAVIOR_PACK_LOADED` payload `schema_version` 字段从 v1 → v2）| 兼容性强，消费方可按 version 处理 |
| C | plan 阶段实测所有 `BEHAVIOR_PACK_LOADED` 消费方，确认无枚举校验后直接引入 | 推迟决策到 plan 阶段实测 |

**推荐**: 选项 C——plan 阶段实测确认消费方（grep `agent_kind` 的消费点）是否有枚举限制。若无枚举限制直接引入（选 A），若有则做最小兼容扩展（避免全 schema version bump）。

**需要用户拍板的问题**: 是否在 F097 spec 中明确要求 plan 阶段做 `BEHAVIOR_PACK_LOADED` 消费方实测 + 兼容性声明？还是信任 spec §5 AC-COMPAT-1 的覆盖已足够？

---

## Session 总结

| # | 问题 | 类型 | 自动选择 | 理由摘要 |
|---|------|------|---------|---------|
| Q-1 | SubagentDelegation 写入 task metadata vs 独立表 | AUTO | 写入 child task metadata | 零 migration，生命周期天然绑定 |
| Q-2 | `_ensure_agent_session` 信号来源 | AUTO | delegation_mode == "subagent" | 复用 F090 字段，避免改函数签名 |
| Q-3 | ephemeral profile closed_at 同步机制 | AUTO | 纯运行时字段 | 与 AC-C1 ephemeral 原则一致 |
| Q-4 | RuntimeHintBundle 拷贝字段范围 | AUTO | 最小集，plan 阶段精确定位 | 避免过拷贝，plan 阶段实测决定 |
| Q-5 | cleanup 事务边界 | AUTO | 顺序写入不强制事务 | MEDIUM 复杂度边界内 |
| Q-6 | SUBAGENT_COMPLETED 事件存在性 | AUTO | plan 阶段实测确认 | 非 GATE_DESIGN 决策 |
| CRITICAL-1 | cleanup 查询路径设计（SubagentDelegation 是否加 child_session_id） | CRITICAL | 推荐选 C（加字段）| 影响 AC-A1 字段列表，不能留 plan 阶段不确定 |
| CRITICAL-2 | BEHAVIOR_PACK_LOADED agent_kind schema 兼容性 | CRITICAL | 推荐 plan 阶段实测后决定 | F096 消费方边界未知，可能 break frontend |

---

## Clarifications

### Session 2026-05-10

**自动解决 6 个澄清点（Q-1 至 Q-6）**，spec 相关章节按如下方式更新：

- **§3 Gap-A**：SubagentDelegation 持久化路径明确为写入 `child_task.metadata["subagent_delegation"]`（task store），无需独立 SQLite 表，与 §15 "无数据迁移"原则一致。`[AUTO-CLARIFIED: Q-1]`
- **§3 Gap-B**：`_ensure_agent_session` 判断条件明确使用 `agent_runtime.delegation_mode == "subagent"`，不改函数签名。`[AUTO-CLARIFIED: Q-2]`
- **§5 AC-C2**：ephemeral profile `closed_at` 为纯运行时字段，cleanup 幂等性通过 `SubagentDelegation.closed_at`（持久化在 task metadata）检查。`[AUTO-CLARIFIED: Q-3]`
- **§5 AC-D1**：拷贝字段列表为最小集（`surface` + `tool_universe`），`recent_worker_lane_*` 等字段在 plan 阶段 `behavior.py:206` 实测后精确列出。`[AUTO-CLARIFIED: Q-4]`
- **§3 Gap-E**：cleanup 采用顺序写入（session close 后补写 SubagentDelegation.closed_at），不要求单一 SQLite 事务。`[AUTO-CLARIFIED: Q-5]`
- **§5 AC-EVENT-1**：SUBAGENT_COMPLETED 事件存在性列为 plan 阶段侦察项，若不存在 F097 补充 emit，payload 最小字段 `{delegation_id, child_task_id, final_status, closed_at}`。`[AUTO-CLARIFIED: Q-6]`

**2 个 CRITICAL 问题上报 GATE_DESIGN 用户拍板**（见上方 CRITICAL-1 / CRITICAL-2）。
