# F099 Ask-Back Channel + Source Generalization — Quality Checklist

**Spec 版本**: v0.1 Pre-GATE_DESIGN
**检查日期**: 2026-05-11
**检查人**: checklist 子代理

---

## 1. Content Quality（内容质量）

| 项 | 状态 | 说明 |
|----|------|------|
| 无实现细节（未提及具体语言/框架/API 实现方式）| ⚠️ PARTIAL | spec §3 块 B 提及 `asyncio.Queue`、`ask_back_tools.py` 等实现细节；这些属于"实现路径"描述，偏技术。其余部分以用户价值视角写成。 |
| 聚焦用户价值和业务需求 | ✅ PASS | §1 明确说明 3 个核心问题（Worker 缺上行通道、A2A source 不完整、source 类型硬编码）和预期收益，用户视角清晰。 |
| 面向非技术利益相关者编写 | ⚠️ PARTIAL | §0 GATE_DESIGN 表格和 §3 FR 含大量技术术语（dispatch_service、CONTROL_METADATA_UPDATED、asyncio.Queue）。§1 目标部分面向业务视角，但整体偏技术受众。 |
| 所有必填章节已完成 | ✅ PASS | §0~§9 + §YAGNI 表格全部存在，无缺失章节。 |

Notes: §3 实现路径描述混入了技术细节（`dispatch_service.py:858-876`、`execution_console.py:294-310` 等），属于 spec 中常见的边界模糊，但因 F099 是内部工程 Feature，利益相关者本身是工程团队，可接受。

---

## 2. Requirement Completeness（需求完整性）

| 项 | 状态 | 说明 |
|----|------|------|
| 无 [NEEDS CLARIFICATION] 标记残留 | ✅ PASS | spec.md 全文无 `[NEEDS CLARIFICATION]` 标记。 |
| 需求可测试且无歧义 | ⚠️ PARTIAL | FR-B5（工具描述提示 caller 信息）可测性低，clarification.md §4 也指出"建议降级为 non-AC 说明"。其余 FR 均明确可测。 |
| 成功标准可测量 | ⚠️ PARTIAL | AC-G1（0 regression）/ AC-B2（状态 WAITING_INPUT）/ AC-C1（source role=WORKER）等均可测量。但 FR-B3 的"审批通过返回 approved / 拒绝返回 rejected 均不 raise"在 AC-B4 中**未验证返回值**（clarification.md §4 指出此 AC 缺口）。缺少 AC-B5、AC-C3。 |
| 成功标准是技术无关的 | ⚠️ PARTIAL | AC-E1 引用 Event Store、TASK_STATE_CHANGED 等技术术语，为实现绑定的 AC。可接受（内部工程 Feature）。 |
| 所有验收场景已定义 | ⚠️ PARTIAL | clarification.md §4 发现以下 AC 缺口：(1) FR-B3 审批返回值未验证（缺 AC-B5）；(2) FR-C4 无效值降级未验证（缺 AC-C3）；(3) FR-E2 tool_call_id 匹配未验证；(4) FR-E4 "拒绝后 LLM 收到 rejected 可自主决策"无专属 AC。 |
| 边界条件已识别 | ✅ PASS | §6 Risks 包含 4 个风险（spawn source 注入破 baseline / escalate_permission 死锁 / compaction 上下文丢失 / 重启丢失 waiter），均有缓解策略。 |
| 范围边界清晰 | ✅ PASS | §5 Non-Goals 列出 10 项显式排除，与上游 F098 / 下游 F100 / 侧边 F107 范围明确划分。 |
| 依赖和假设已识别 | ✅ PASS | §2 Baseline-Already-Passed 表格列出 7 项已通项及实测证据；§9 复杂度评估明确"依赖新引入数=0"；clarification.md §3 列出 3 个开放问题（含假设待验证项）。 |

Notes:
- FR-C1 与 §YAGNI 检验存在**矛盾**：FR-C1 标注 MUST，但 YAGNI 表标注 [可选]（clarification.md §3 开放问题 1 指出）。此矛盾需 GATE_DESIGN 决议（参见下文关键发现）。
- clarification.md §3 开放问题 2（ApprovalGate 超时机制是否存在）是 spec 阶段未验证假设，风险 MEDIUM。

---

## 3. Feature Readiness（特性就绪度）

| 项 | 状态 | 说明 |
|----|------|------|
| 所有功能需求有明确的验收标准 | ⚠️ PARTIAL | FR-B5 / FR-C4 / FR-E4 的部分场景缺少专属 AC（详见上方 Requirement Completeness）。 |
| 用户场景覆盖主要流程 | ✅ PASS | 覆盖：(1) Worker ask_back → WAITING_INPUT → 用户回答 → 继续；(2) Worker escalate_permission → 审批 → 继续/失败；(3) worker→worker dispatch source 修复（AC-C1）；(4) 主 Agent dispatch 不受影响（AC-C2 后向兼容）。主要流程完整。 |
| 功能满足 Success Criteria 中定义的可测量成果 | ⚠️ PARTIAL | AC-G1 / AC-G2 / AC-G3 / AC-G4 全局 AC 覆盖主要成果。但 FR-B3 审批返回值未有可测量 AC，FR-C4 降级行为未有 AC。 |
| 规范中无实现细节泄漏 | ⚠️ PARTIAL | §3 块 B 实现路径提及 `supervision_tools.py`、`ask_back_tools.py`、`execution_context.request_input()`；§3 块 C 提及 `dispatch_service.py:858-876`。这些是工程决策而非业务需求的描述，属轻度实现细节泄漏，但在内部工程 spec 中属可接受范围。 |

---

## 4. Constitution 合规

| 条款 | 状态 | 说明 |
|------|------|------|
| C1 Durability First | ✅ PASS | FR-B1/B2 明确 RUNNING→WAITING_INPUT 状态变化；AC-E1 要求 Event Store audit trace；task_runner.attach_input() 重启路径 §2 已验证（已通项）。 |
| C2 Everything is an Event | ✅ PASS | FR-B4 要求三工具均 emit CONTROL_METADATA_UPDATED；FR-E3 要求 TASK_STATE_CHANGED 完整审计；AC-G4 全局约束。 |
| C3 Tools are Contracts | ✅ PASS | §3 块 B 三工具均有明确参数定义（名称/类型/语义）；AC-B1 验证注册。Output schema（tool_result 返回值）在 FR-B2/B3 中明确定义。 |
| C4 Side-effect Two-Phase | ✅ PASS | OD-F099-5 选 A 明确 escalate_permission 复用 ApprovalGate（Plan→Gate→Execute）；AC-B4 验证 WAITING_APPROVAL 进入；AC-G3 全局约束。 |
| C5 Least Privilege | ✅ PASS | §3 块 B 未提及 ask_back metadata 含 secrets；CONTROL_METADATA_UPDATED payload（§3 FR-D1）仅含 question/context/created_at，无凭证字段。 |
| C7 User-in-Control | ✅ PASS | escalate_permission 走 ApprovalGate SSE 路径；AC-B4 验证 SSE 审批卡片；FR-E4 说明用户可通过 SSE 审批。 |
| C9 Agent Autonomy | ✅ PASS | FR-B3 明确"由 LLM 根据返回值决策后续行为"；三工具均是 LLM 自主选择调用，无硬编码触发条件。 |
| C10 Policy-Driven Access | ✅ PASS | OD-F099-5 选 A 接入现有 Policy Engine；AC-G3 全局约束；§5 Non-Goals 明确 kind 级别过滤留 F107 策略层。 |

---

## 5. F098 OD-1~OD-9 不偏离检查

| OD | 状态 | 说明 |
|----|------|------|
| OD-1 CONTROL_METADATA_UPDATED 路径继承 | ✅ PASS | OD-F099-1 选 B 继承复用；FR-D2 / AC-D2 明确"不污染对话历史"，保持 F098 OD-1 修复成果。 |
| OD-2 subagent 路径跳过 find_active_runtime | ✅ PASS | spec §5 Non-Goals 明确 F098 已稳定的 A2A receiver 主路径不动；F099 不触碰 subagent runtime 独立路径。 |
| OD-3 atomic 妥协 | ✅ PASS | §5 Non-Goals 明确"atomic single-transaction（F098 OD-3 推迟 F107）"不在 F099 范围。 |
| OD-4 task_service 终态 callback | ✅ PASS | F099 不触碰 task_service 终态层；§5 Non-Goals 排除。 |
| OD-5 BaseDelegation 公共抽象 | ✅ PASS | OD-F099-2 选 B（三工具独立 handler，不继承 BaseDelegation）——语义上合理：ask_back 是"工具调用挂起"而非"任务委托"，与 BaseDelegation 场景不同，不违反 OD-5（OD-5 针对 spawn-and-die 的 delegation 抽象，F099 ask_back 不属于此类）。 |
| OD-6 agent_kind enum 不动 | ✅ PASS | F099 不新增 agent_kind enum 值；§5 Non-Goals 排除。 |
| OD-7 Worker→Worker 解禁 | ✅ PASS | 块 C source 注入是补充 F098 已知 LOW §3，不引入新 spawn 工具，不改变 Worker→Worker 解禁的机制。 |
| OD-8 Phase H 顺序 | N/A | F099 不涉及 F098 Phase H（cleanup hook 终态统一）。 |
| OD-9 A2A target Worker profile 通过 capability_pack | ✅ PASS | F099 不修改 A2A target profile 加载路径；§5 Non-Goals 排除。 |

---

## 6. YAGNI 检验

| 项 | 标注 | 状态 | 说明 |
|----|------|------|------|
| ask_back_tools.py（三工具注册模块）| [必须] | ✅ PASS | 核心功能载体，去掉后 F099 无法实现。 |
| CONTROL_METADATA_UPDATED emit（ask_back audit）| [必须] | ✅ PASS | Constitution C2 要求，无审计违反宪法。 |
| source_runtime_kind 注入（spawn 路径）| [必须] | ✅ PASS | F098 已知 LOW §3 修复，主责之一。 |
| escalate_permission → ApprovalGate | [必须] | ✅ PASS | Constitution C10，不走 Policy Engine 违反宪法。 |
| automation/user_channel 完整派生路径 | [可选] | ⚠️ PARTIAL | **FR-C1 标注 MUST 但 YAGNI 标注 [可选] 存在矛盾**（clarification.md §3 开放问题 1）。需 GATE_DESIGN 决议：建议 FR-C1 改为"定义常量 + warning fallback"（选 A）。 |
| BaseAskBackDelegation 抽象类 | [YAGNI-移除] | ✅ PASS | OD-F099-2 选 B 移除；去掉后功能完整实现，无隐藏依赖。 |
| A2AConversation.source_type 新字段 | [YAGNI-移除] | ✅ PASS | OD-F099-3 选 B 移除；当前无消费方需要此字段，去掉后 spec 闭环。 |
| ToolRegistry kind 过滤机制 | [YAGNI-移除] | ✅ PASS | §5 Non-Goals 明确排除；去掉后功能完整实现，无隐藏依赖。 |

---

## 7. 测试覆盖度

| 项 | 状态 | 说明 |
|----|------|------|
| 单测覆盖每个 FR | ⚠️ PARTIAL | §7 测试策略列出 4 个新建测试文件（块 B/C/D/E），预期 30-39 个测试，覆盖主要 FR。但 FR-B5（工具描述质量）、FR-C4（无效值降级）、FR-E4（拒绝后 LLM 自主决策）无专属测试说明。 |
| 集成测覆盖端到端流程 | ✅ PASS | §7 集成测：`test_task_runner.py` 扩展（端到端流程）+ `test_capability_pack_tools.py` 扩展（注册验证）。块 E 专门是端到端验证 Phase。 |
| 错误路径测试 | ⚠️ PARTIAL | §7 未明确说明"无效 source_runtime_kind / ApprovalGate 拒绝 / WAITING_INPUT 超时"的错误路径测试。FR-C4（无效值降级）有 FR 描述但 §7 未对应测试。clarification.md §4 建议补 AC-C3（invalid source_runtime_kind → default main + warning log）。 |
| 后向兼容测试 | ✅ PASS | AC-C2 明确验证"主 Agent 调 delegate_task 仍走 MAIN"；AC-G1（0 regression）覆盖整体后向兼容。 |

---

## 8. 实施风险

| 项 | 状态 | 说明 |
|----|------|------|
| Phase 顺序合理 | ✅ PASS | §8 建议 C → D → B → E（先简后难，先基础设施后主行为）；clarification.md §2 验证块 C 和块 B 无直接技术依赖，顺序合理。 |
| 跨模块改动可控（< 5 处）| ✅ PASS | §9 复杂度评估：跨模块耦合 1 处（dispatch_service.py）+ delegate_task_tool.py + delegation_tools.py（同一 delegation 域，3 处改动）。总改动点 ≤ 4，在阈值内。 |
| 无新建数据迁移 | ✅ PASS | §9 复杂度信号"无数据迁移"；spec 全文未提及 schema migration。 |
| 0 regression vs F098 baseline 可验证 | ✅ PASS | AC-G1 明确要求；§7 回归要求明确"≥ F098 baseline c2e97d5 passed 数"；e2e_smoke 8/8 通过作为 pre-commit hook 强制要求。 |

---

## 总结

| 维度 | PASS | PARTIAL | FAIL | N/A |
|------|------|---------|------|-----|
| Content Quality | 2 | 2 | 0 | 0 |
| Requirement Completeness | 4 | 4 | 0 | 0 |
| Feature Readiness | 1 | 3 | 0 | 0 |
| Constitution 合规 | 8 | 0 | 0 | 0 |
| F098 OD 不偏离 | 8 | 0 | 0 | 1 |
| YAGNI 检验 | 7 | 1 | 0 | 0 |
| 测试覆盖度 | 2 | 2 | 0 | 0 |
| 实施风险 | 4 | 0 | 0 | 0 |
| **合计** | **36** | **12** | **0** | **1** |

**结果**：36 PASS / 12 PARTIAL / 0 FAIL / 1 N/A

---

## 关键 GATE_DESIGN 风险（必须用户决议的 ⚠️ 项）

### ⚠️ G1（高优先级）：FR-C1 与 YAGNI 矛盾 — automation/user_channel 派生范围

FR-C1 标注 MUST（"派生独立路径"），但 §YAGNI 检验标注 [可选]，§5 Non-Goals 也排除"automation/user_channel source 完整派生路径"。clarification.md §3 开放问题 1 建议选 A（仅定义常量 + warning fallback）。

**需要用户决议**：FR-C1 的 MUST 是否降级为"定义枚举常量 + invalid value fallback to MAIN + warning log"？如果降级，FR-C1 描述需修订，现有 AC 设计无需新增（FR-C4 的 invalid 值 fallback 已覆盖此行为）。

### ⚠️ G2（中优先级）：escalate_permission ApprovalGate 超时机制未验证

spec §6 Risks 假设"ApprovalGate 现有超时机制（baseline 已有）"，但 clarification.md §3 开放问题 2 指出 phase-0-recon 未实测此机制是否存在。若不存在，escalate_permission handler 需自建 `asyncio.wait_for` + `asyncio.TimeoutError` 处理，增加约 20-30 行代码，且需决策超时时长配置方式（task profile / 工具参数 / 全局配置）。

**建议处理时机**：GATE_DESIGN 前补一次 grep（`grep -n "timeout" harness/approval_gate.py`），5 分钟可验证。

### ⚠️ G3（中优先级）：AC 缺口 — FR-B3 / FR-C4 / FR-E4 场景无专属 AC

以下场景缺少专属 AC（clarification.md §4 已识别）：
- **AC-B5 缺失**：escalate_permission 审批通过返回 "approved" / 拒绝返回 "rejected" 均不 raise（FR-B3 主要行为）
- **AC-C3 缺失**：无效 source_runtime_kind 降级为 MAIN + warning log（FR-C4 错误路径）
- **FR-E2 部分缺失**：tool_call_id 匹配验证未在 AC 中出现

建议在 plan 阶段补充这 3 项 AC，或在 checklist 问题解决前将这些 FR 降级为 SHOULD（可接受的场景缺口）。

---

v0.1 - 待 GATE_DESIGN 审查
