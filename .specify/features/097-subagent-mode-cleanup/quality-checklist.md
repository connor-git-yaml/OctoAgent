# Quality Checklist: F097 Subagent Mode Cleanup

**Purpose**: 验证 spec.md（v0.1）是否满足进入 plan 阶段的质量标准
**Created**: 2026-05-10
**Feature**: `.specify/features/097-subagent-mode-cleanup/spec.md`
**Reviewer**: spec-driver:checklist 子代理

---

## 维度一：Constitution 兼容性（10 条硬规则）

### 审查项 #1：C1 Durability First
**审查问题**：SubagentDelegation、Session cleanup、RecallFrame 持久化路径是否在 spec 中明确？
**当前状态**：✅ 满足
**证据**：§11 Constitution 兼容性表明 SubagentDelegation 持久化、Session cleanup 写入 SQLite、RecallFrame 不删除（Gap-E 第 4 条）
**建议**：无需修改

### 审查项 #2：C2 Everything is an Event
**审查问题**：所有新行为（Subagent spawn / session close / behavior pack load）是否有对应事件 emit？
**当前状态**：⚠️ 部分满足
**证据**：AC-EVENT-1 验证 SUBAGENT_SPAWNED（delegate_task 路径）和 SUBAGENT_COMPLETED；但 OD-2 明确 subagents.spawn 路径当前 emit_audit_event=False，Session CLOSED 状态变更未声明对应事件；spec §11 C2 备注"OD-2 影响"
**建议**：在 AC-EVENT-1 中补充：Session status 变为 CLOSED 时是否 emit SESSION_CLOSED 事件（或说明"无需新事件，状态写入 SQLite 即满足 C1，C2 由 SUBAGENT_COMPLETED 覆盖"）。明确表态，避免 plan 阶段自行解读。

### 审查项 #3：C3 Tools are Contracts（工具 schema 与代码签名一致性）
**审查问题**：F097 是否会影响现有工具 schema（delegate_task / subagents.spawn）？若有，是否声明？
**当前状态**：✅ 满足
**证据**：spec §4 Out of Scope 和 §12 均无 delegate_task schema 变更；Gap-D 拷贝 RuntimeHintBundle 是内部实现（control_metadata），不改变工具 schema
**建议**：无需修改

### 审查项 #4：C4 Side-effect Must be Two-Phase（不可逆操作两阶段）
**审查问题**：Session cleanup 是不可逆操作，是否有两阶段保护（Plan → Gate → Execute）或幂等保证？
**当前状态**：✅ 满足
**证据**：AC-E2 明确幂等保证（已 CLOSED 的 session 重复触发不报错，closed_at 保持首次值）；cleanup 基于 Task 终态触发而非用户操作，不需要额外 Gate

### 审查项 #5：C8 Observability is a Feature
**审查问题**：每个新 Gap 是否有对应可观测手段（event / audit trail）？
**当前状态**：⚠️ 部分满足
**证据**：Gap-A（SubagentDelegation）/ Gap-C（ephemeral AgentProfile）/ Gap-G（BEHAVIOR_PACK_LOADED.agent_kind=subagent）均有 audit trail；Gap-B（SUBAGENT_INTERNAL session 路径）有 AC-B1 但未声明对应查询手段；Gap-D（RuntimeHintBundle 拷贝）仅有 AC-D1 状态验证，无对应 event emit
**建议**：在 Gap-D 的 AC 或可选事件中说明"RuntimeHintBundle 拷贝不 emit 独立事件"的理由（例如"spawn 时已由 SUBAGENT_SPAWNED 事件覆盖上下文快照"），避免 plan 阶段遗漏。

### 审查项 #6：C9 Agent Autonomy（无硬编码规则）
**审查问题**：Gap-B 的新路径判断（target_kind=subagent 时走 SUBAGENT_INTERNAL）是否属于系统层判断（允许），非 LLM 决策替代？
**当前状态**：✅ 满足
**证据**：§11 C9 明确"spawn 时机和工具选择由 LLM 自主决策；F097 仅增加基础设施"；路径判断是系统 dispatch 层（非 LLM 决策路径）

### 审查项 #7：C10 Policy-Driven Access（权限决策单一入口）
**审查问题**：ephemeral AgentProfile（Gap-C）是否绕过了 DelegationManager gate？
**当前状态**：✅ 满足
**证据**：§10 Edge Cases "Subagent spawn 失败（DelegationManager gate 拒绝）"明确 gate 仍生效；§11 C10 声明 DelegationManager gate 继续生效；ephemeral profile 在 gate 通过后才创建

---

## 维度二：AC 可测试性

### 审查项 #8：AC-A1/A2 可测试性
**审查问题**：SubagentDelegation model 的 AC 是否有清晰的通过标准？
**当前状态**：✅ 满足
**证据**：AC-A1 列出所有必须字段名；AC-A2 声明默认值、类型注解和单测要求（字段校验 + round-trip）——可直接转化为 pytest 测试用例

### 审查项 #9：AC-C1 ephemeral profile 的"不写入持久化表"如何验证？
**审查问题**：AC-C1 声明"不写入 worker_profile / agent_profile 表"，但 verification 方式未说明
**当前状态**：⚠️ 部分满足
**证据**：AC-C1 有明确结论（无新增行），但 spec 未说明如何验证（查表 count？查 store 调用？）
**建议**：在 AC-C1 补充验证方法：例如"通过 `SELECT COUNT(*) FROM agent_profile` 在 spawn 前后对比，或通过 mock agent_context_store 验证 save_agent_profile 未被调用"

### 审查项 #10：AC-D1 "值与 caller 原始值一致" 的测试锚点
**审查问题**：AC-D1 要求字段值与 caller 原始值一致，但 caller 的 RuntimeHintBundle 字段名未在 spec 中枚举完整
**当前状态**：⚠️ 部分满足
**证据**：§3 Gap-D 列出 4 类字段（surface / tool_universe / recent_worker_lane_* / recent_failure_budget），但 AC-D1 仅说"包含从 caller 拷贝的 surface / tool_universe / recent_worker_lane_* 字段"——缺 recent_failure_budget
**建议**：AC-D1 补充 recent_failure_budget 字段，与 Gap-D 描述对齐

### 审查项 #11：AC-F1/F2/F3 pending 状态处理
**审查问题**：AC-F 为 pending 状态（依赖 OD-1 拍板），plan 阶段是否可处理？
**当前状态**：✅ 满足（已明确标注）
**证据**：§5 Gap-F AC 注意事项明确"GATE_DESIGN 阶段用户拍板后才锁定"；Phase F 实施前为 pending——plan 阶段应跳过 Phase F 或标为"待 OD-1 决策后注入"

### 审查项 #12：AC-AUDIT-1 验证路径的具体工具
**审查问题**：AC-AUDIT-1 的四层 audit chain 验证方式是否明确？
**当前状态**：✅ 满足
**证据**：AC-AUDIT-1 明确通过 `list_recall_frames(agent_runtime_id=subagent_runtime_id)` 验证，与 F096 已有 endpoint 接口一致；四层对齐路径与 §7 中 ephemeral AgentProfile 描述一致

---

## 维度三：AC 覆盖度

### 审查项 #13：6 个 Gap 的 AC 覆盖完整性
**审查问题**：每个 Gap（A/B/C/D/E/F）是否都有对应 AC？
**当前状态**：✅ 满足
**证据**：Gap-A → AC-A1/A2；Gap-B → AC-B1/B2；Gap-C → AC-C1/C2；Gap-D → AC-D1/D2；Gap-E → AC-E1/E2/E3；Gap-F → AC-F1/F2/F3（pending）；Gap-G → AC-G1

### 审查项 #14：向后兼容 AC 覆盖（Worker / main 路径保护）
**审查问题**：现有 Worker / main 路径是否有足够的 non-regression AC？
**当前状态**：✅ 满足
**证据**：AC-B2（DIRECT_WORKER / WORKER_INTERNAL / MAIN_BOOTSTRAP 三路径单测继续通过）；AC-D2（Worker spawn 不受 RuntimeHintBundle 拷贝影响）；AC-COMPAT-1（现有 agent_kind 值不变）；AC-GLOBAL-1（全量回归 ≥ 3191）

### 审查项 #15：SUBAGENT_COMPLETED 事件 AC 缺失
**审查问题**：AC-EVENT-1 提到"Subagent 完成后写入 SUBAGENT_COMPLETED 事件（若 F092 未实现则 F097 补充）"，但 F092 实际完成情况未在 spec 中确认
**当前状态**：⚠️ 部分满足
**证据**：tech-research 未明确 SUBAGENT_COMPLETED 在 baseline 的存在状态；AC-EVENT-1 条件式表述"若 F092 未实现则"是不确定状态，plan 阶段需侦察
**建议**：在 §13 依赖/Plan 阶段需精确定位中补充"确认 SUBAGENT_COMPLETED 事件在 F092 baseline 是否存在"作为 Plan 阶段必做的侦察项

### 审查项 #16：SubagentDelegation 持久化存储路径缺失 AC
**审查问题**：Gap-A 定义了 SubagentDelegation model，但未声明其持久化方式（写入哪个表 / 是否写 EventStore / 是否写独立表）
**当前状态**：❌ 缺失
**证据**：spec §3 Gap-A 仅说"位置：新建文件或扩展 delegation.py"；§11 C1 说"随 Task 持久化"，但 spec 正文无"SubagentDelegation 写入数据库"的 AC；AC-A1/A2 仅测 model 字段和序列化，无存储 AC
**建议**：补充 AC-A3 或在 Gap-A 描述中明确持久化策略：例如"SubagentDelegation 随 Task 状态一同落入 SQLite（delegation_records 表 / task metadata / 独立表），plan 阶段决定具体 schema"。若刻意延迟到 plan 阶段决策，需在 spec 中显式说明。

---

## 维度四：Open Decisions 显著性

### 审查项 #17：OD-1 在 spec 中的可读性
**审查问题**：OD-1 Memory α/β/γ 是否在 spec 中足够显著，plan 阶段能直接读取并跳过 Phase F？
**当前状态**：✅ 满足
**证据**：§8 Open Decisions 有完整的三选项 trade-off 表 + tech-research 推荐意见（优先 α）+ "需要用户拍板的问题"清单；AC-F 明确 pending 标注；§3 Gap-F 标注"Open Decision（见第 8 节）"

### 审查项 #18：OD-2 当前 AC-EVENT-1 与 OD-2 决策的关系
**审查问题**：OD-2 尚未拍板，AC-EVENT-1 按"保持 F092 现状"编写是否会误导 plan 阶段？
**当前状态**：✅ 满足
**证据**：AC-EVENT-1 末尾注释明确"待拍板后调整"；§8 OD-2 说明了"AUTO-RESOLVED 备注"机制；plan 阶段可识别此为条件式 AC

---

## 维度五：F098 接入点完整性

### 审查项 #19：F097 与 F098 边界清晰度
**审查问题**：_enforce_child_target_kind_policy 保持不动、BaseDelegation 延迟、AC-F1 推迟是否清晰声明？
**当前状态**：✅ 满足
**证据**：§4 Out of Scope 明确列出 F098 全部主题；§12 F098 接入点前向声明有三条明确接入点；§16 YAGNI 检验中 BaseDelegation 标注 YAGNI-移除

### 审查项 #20：BEHAVIOR_PACK_LOADED agent_kind 向后兼容 F098 演化
**审查问题**：F097 引入 "subagent" agent_kind 值后，F098 扩展时是否有向后兼容声明？
**当前状态**：✅ 满足
**证据**：§12 接入点明确"F098 实施 A2A Receiver 时需扩展此字段为新的 agent_kind 值（向后兼容，F097 数据可读）"

---

## 维度六：依赖清单完整性

### 审查项 #21：F092/F094/F096 baseline 假设显式性
**审查问题**：前置依赖（F092 plane.spawn_child / F094 AGENT_PRIVATE / F096 audit chain）是否显式声明已就位？
**当前状态**：✅ 满足
**证据**：§13 依赖"必须就位（已就位）"列出三项，含具体代码位置（delegation_plane.py:953 等）；§2 BAP-3 对应 F092

### 审查项 #22：Plan 阶段必做侦察项完整性
**审查问题**：§13 "Plan 阶段需精确定位" 3 条是否覆盖所有已知高风险不确定点？
**当前状态**：⚠️ 部分满足
**证据**：3 条覆盖 cleanup hook 挂载点 / target_kind 信号链 / ephemeral profile 创建时机；但审查项 #15 发现"SUBAGENT_COMPLETED 事件 baseline 存在性"也是高不确定点，未列入
**建议**：在 §13 Plan 阶段需精确定位中补充"确认 SUBAGENT_COMPLETED 事件在 cc64f0c baseline 的实现状态"

---

## 维度七：测试策略覆盖

### 审查项 #23：每个 Gap 的测试分层（单测 vs 集成测）
**审查问题**：spec 中是否声明了各 Gap 的测试类型（unit / integration / e2e）？
**当前状态**：⚠️ 部分满足
**证据**：AC-A2 要求单测；AC-B2 要求单测；AC-E3 要求 list_recall_frames 仍可返回数据（集成测）；AC-G1 要求 EventStore 中查验（集成测）；但 Gap-C、Gap-D 的测试分层未在 spec 中明确说明测试类型
**建议**：在 AC-C1 / AC-D1 中明确测试分层（单测 mock vs 集成测调用真实 spawn 路径）

### 审查项 #24：User Story 验收场景与 AC 对应关系
**审查问题**：4 个 User Story 的验收场景是否与 §5 AC 保持一致、不冲突？
**当前状态**：✅ 满足
**证据**：US-1 验收场景对应 AC-G1 + AC-A1 + AC-AUDIT-1；US-2 对应 AC-D1/D2；US-3 对应 AC-E1/E2/E3；US-4 对应 AC-F1/F2/F3（pending）——全部一致

---

## 维度八：可观测性（C8 补充检查）

### 审查项 #25：gap-E session cleanup 的可观测手段
**审查问题**：Session CLOSED 状态变更是否有查询手段？audit trail 是否覆盖？
**当前状态**：✅ 满足
**证据**：AC-E1 通过 AgentSession status 字段验证（可 SQL 查询）；AC-E3 通过 list_recall_frames 验证 RecallFrame 保留；SubagentDelegation.closed_at 同步更新提供另一维度

---

## 维度九：行为变更声明准确性

### 审查项 #26：F097 "非行为零变更" 声明
**审查问题**：spec 是否正确区分"现有路径不变 + Subagent 是新路径"？（与 F090/F091/F092 的行为零变更原则区别）
**当前状态**：✅ 满足
**证据**：§1 目标明确"从隐性实现细节变为显式可观测的一等公民"——这是新能力，非零变更重构；§4 Out of Scope 和 AC-COMPAT-1 / AC-D2 明确现有 main / worker 路径不受影响；§16 YAGNI 检验区分必须 vs YAGNI-移除

---

## 维度十：回归基线可达性

### 审查项 #27：≥ 3191 passed 目标与 F097 改动范围
**审查问题**：F097 涉及 agent_context.py / capability_pack.py / task runner 3+ 模块修改，≥ 3191 目标是否现实？
**当前状态**：✅ 满足
**证据**：AC-GLOBAL-1 声明"0 regression vs F096 baseline (cc64f0c)，目标 ≥ 3191"；§15 复杂度 MEDIUM，无状态机/并发新增，改动模式与 F093-F095 相似（约 +40-70 测试）；BAP-1 至 BAP-9 表明 baseline 已有大量通过基础，回归风险可控

---

## 总结：GATE_DESIGN 判定

### 必须修复（❌）：1 项

| 编号 | 问题 | 建议修复 |
|------|------|---------|
| #16 | SubagentDelegation 持久化存储路径在 spec 和 AC 中完全缺失——§11 C1 仅说"随 Task 持久化"但无具体路径说明，AC-A1/A2 不测存储行为 | 补充 AC-A3：明确 SubagentDelegation 存入哪个 SQLite 表 / 如何持久化；或在 Gap-A 描述中明确"持久化策略在 plan 阶段决定"并在 §13 Plan 阶段侦察项中列出 |

### 建议修复（⚠️）：7 项（不阻塞 GATE_DESIGN，但建议 plan 前回写）

| 编号 | 问题 | 建议 |
|------|------|------|
| #2 | Session CLOSED 时无 event emit 说明（C2 缺口）| 在 AC-EVENT-1 补充 SESSION_CLOSED 事件立场 |
| #5 | Gap-D RuntimeHintBundle 拷贝无对应 event | 补充"无独立 event" 的理由声明 |
| #9 | AC-C1 ephemeral profile 无验证方式说明 | 补充 mock store 或 SELECT COUNT 方案 |
| #10 | AC-D1 缺少 recent_failure_budget 字段 | 与 Gap-D 描述对齐 |
| #15 | SUBAGENT_COMPLETED 事件 baseline 存在性未确认 | 加入 §13 Plan 阶段侦察项 |
| #22 | §13 Plan 阶段侦察项未包含 SUBAGENT_COMPLETED 侦察 | 补充第 4 条侦察项 |
| #23 | Gap-C、Gap-D 测试分层未明确 | 在 AC-C1/D1 补充"单测 mock"或"集成测"说明 |

---

**GATE_DESIGN 判定：GO with caveats**

❌ 项数：1（SubagentDelegation 持久化路径缺失，轻量修复）
⚠️ 项数：7（可在 plan 前回写，不阻塞推进）
✅ 项数：18
❓ 项数：0

**判定理由**：spec 主体完整，6 个 Gap 均有 AC 覆盖，Constitution 兼容性声明完整，F098 接入点清晰，Open Decisions 显著可读。唯一 ❌ 项（#16 持久化路径）是 spec 逻辑漏洞但轻量可修复——在 Gap-A 补充"持久化策略 plan 阶段决定"声明或补 AC-A3 即可解除。建议修复 ❌ 项后即可启动 plan 阶段，⚠️ 项并行回写 spec。
