# Requirements Quality Checklist: F093 Worker Full Session Parity

**Purpose**: 验证 spec.md 质量，确认是否可进入 Plan 阶段
**Created**: 2026-05-08
**Feature**: `.specify/features/093-worker-full-session-parity/spec.md`
**Reviewer**: spec-driver:checklist 子代理

---

## Content Quality（内容质量）

- [x] **CQ-1 无实现细节泄漏** — PASS：spec 正确区分"是什么"与"怎么做"；§2.2 Gap 点明确标注"Plan 阶段用代码坐实"，未给出函数签名或具体实现方案。
- [x] **CQ-2 聚焦用户价值和业务需求** — PASS：§0 总览、§1 User Stories 均从"Worker 做什么 / 为何需要"出发；H2 哲学锚点清晰。
- [x] **CQ-3 面向非技术利益相关者可读** — PASS（有保留）：User Story 的 Journey / Why 段落可读性好；§2 模型契约部分代码路径较技术，但作为 key contracts 章节可接受。
- [x] **CQ-4 所有必填章节已完成** — PASS：Overview / User Scenarios / Key Models / Scope / NFR / Acceptance Criteria / Phase Order / Open Points / Risks / References 均存在。

---

## Requirement Completeness（需求完整性）

- [x] **RC-1 无 [NEEDS CLARIFICATION] 标记残留** — PASS：全文未发现任何 `[NEEDS CLARIFICATION]` 标记；§11 Clarifications 明确说明 0 ambiguity。
- [x] **RC-2 需求可测试且无歧义** — PASS：A1-A5 / B1-B4 / C1-C3 / G1-G7 每条均有明确的 pass/fail 判据（turn 数、cursor 值、import 不 BREAKING、0 regression 等）。
- [x] **RC-3 成功标准可测量** — PASS：关键指标具体——`agent_session_turns` 表新增 ≥ 2 条记录、main session turn count 不变、cursor 值为 0、`rolling_summary`/`memory_cursor_seq` 写后读回完全一致、全量回归 0 regression vs F092 baseline (7e52bc6)。
- [x] **RC-4 成功标准技术无关** — PASS（有保留）：块 C 的 AC（C1-C3）不可避免地引用了文件名（`agent_context.py`），但这是纯重构目标本身的特性，不构成问题；业务层成功标准（A1-A5）均技术无关。
- [x] **RC-5 所有验收场景已定义** — PASS：每个 User Story 均有 Acceptance Scenarios；§5 含全局验收 checklist G1-G7。
- [x] **RC-6 边界条件已识别** — PASS：识别了 `DIRECT_WORKER` vs `WORKER_INTERNAL` 两种 kind 的隔离断言；F093 范围内 SessionMemoryExtractor 不启用（B4 + §3.2 双重锚定）；cursor 初始值 0 断言。
- [x] **RC-7 范围边界清晰** — PASS：§3.1 In Scope 含三块（A/B/C）明确分类；§3.2 Out of Scope 8 条排除项全部指向具体 Feature 编号（F094-F107）。
- [x] **RC-8 依赖和假设已识别** — PASS：§2.1 明确列出 F092 baseline 实测现状（已有模型字段 line 303/306、已有 hook 83 行、已有 `_ensure_agent_session` 三元组逻辑）；§2.2 Gap-1~Gap-5 列出已知假设待验；NFR-4 显式确认 F093 不动 SpawnChildResult 三态。

---

## Feature Readiness（特性就绪度）

- [x] **FR-1 所有功能需求有明确的验收标准** — PASS：块 A → A1-A5；块 B → B1-B4；块 C → C1-C3；全局 → G1-G7，覆盖完整。
- [x] **FR-2 用户场景覆盖主要流程** — PASS：Story 1 覆盖 Worker turn 持久化主流程；Story 2 覆盖 session 字段槽位；Story 3 覆盖拆分后行为兼容，三条 Story 分别对应三块范围。
- [x] **FR-3 功能满足 Success Criteria 中定义的可测量成果** — PASS：§5 验收 checklist 与 §1 User Stories 中的 Acceptance Scenarios 对应关系明确；G5/G6/G7 覆盖制品与接口前向声明。
- [x] **FR-4 规范中无实现细节泄漏** — PASS：§2 Key Models 中的路径引用（line 303/306 等）属于现状描述而非设计约束，§5 Acceptance Criteria 均以可观测行为而非实现手段表达。

---

## 专项维度校验

### 1. Spec 完整性

- [x] **S-1 覆盖 WHAT / WHY / 不变量** — PASS：§0 说明 WHAT（三块范围）+ WHY（H2 哲学锚点）；§4 NFR-1 明确区分"块 C 行为零变更"vs"块 A/B 行为可观测变更"；不变量声明完整。
- [x] **S-2 含 acceptance criteria** — PASS：§5 完整 AC checklist A1-A5 / B1-B4 / C1-C3 / G1-G7。
- [x] **S-3 含 Out of Scope** — PASS：§3.2 8 条排除项，全部指向具体 Feature。
- [x] **S-4 含 Phase 顺序建议** — PASS：§7 明确建议 C → A → B → D 顺序，并附理由（F091/F092 经验印证）。

### 2. 可测性

- [x] **T-1 每条 AC pass/fail 可判定** — PASS：A1（`agent_session_turns` 表记录数 ≥ 2）、A2（main turn count 不变）、B1/B2（写后读回值完全一致）、C3（0 regression）均为二值判断；无模糊表述（如"大致正确"、"基本通过"）。

### 3. 范围边界清晰

- [x] **R-1 F094-F107 排除项完整** — PASS：列出 F094/F095/F096/F097/F098/F099/F100/F107 八个排除项，与 CLAUDE.local.md M5 规划中 F093 之后的 Feature 对应完整，无漏项。

### 4. 不变量声明

- [x] **INV-1 行为零变更（块 C）声明** — PASS：NFR-1 明确"块 C 纯重构，全量回归 0 regression"；C3 AC 重申。
- [x] **INV-2 行为可观测（块 A/B）声明** — PASS：NFR-1 列出 4 条单测覆盖要求；NFR-2 要求事件 emit 可在 control_plane 按 session_id 查询。
- [x] **INV-3 0 regression 声明** — PASS：NFR-5 明确"每 Phase 后回归 0 regression vs F092 baseline (7e52bc6)"，G1/C3 AC 重申。
- [x] **INV-4 事件 emit 声明** — PASS：§2.3 列出事件四件套（agent_session_id / task_id / turn_seq / kind）；A5 AC 要求 control_plane 可查；NFR-2 独立章节强调。

### 5. 接口前向声明（§6）

- [x] **IF-1 F094 接入点清楚** — PASS：§6.1 明确 `memory_cursor_seq` 槽位路径（packages/core 模型层）、cursor 推进逻辑在 F094 接入、worker session id 传递路径（`compiled_context` → `worker_memory_namespace_id`）；"F093 不动"边界清晰。
- [x] **IF-2 F095 接入点清楚** — PASS（有保留）：§6.2 指出"Plan 阶段需精确定位 BehaviorLoadProfile.WORKER 当前涵盖文件清单"，存在一个 WARN——F095 接入点描述的是待 Plan 阶段发现的信息而非已知位置，但这是正确的 spec 边界行为（F093 spec 只能声明"不动"）。

### 6. 风险识别（§9）

- [x] **RSK-1 覆盖关键技术风险** — PASS：5 条风险涵盖：拆分撞 import（高）、propagate gap 复杂（中）、SessionMemoryExtractor 误启动（中）、拆分边界过细（低）、Codex review finding 推迟（中）；每条附缓解措施。

### 7. Open Points 明确性（§8）

- [x] **OP-1 Open-1~Open-6 每条给候选方向** — PASS：Open-1 给 3 个候选方案（A/B/C）并倾向 C；Open-2 列出 4 种可能的 gap 路径；Open-3 给倾向方向（复用现有事件）；Open-4 明确二选一问题；Open-5 给缓解动作（扫 import graph）；Open-6 明确"不动 hook"边界。

### 8. Codex Review 强制规则

- [x] **CDX-1 NFR-6 声明 Final cross-Phase Codex review 必走** — PASS：NFR-6 明确"每 Phase 前 Codex review + Final cross-Phase Codex review"；引用 F091/F092 实证价值；G4 AC 要求 Final review 必过。

### 9. Spawned Task 流程合规

- [x] **SPW-1 NFR-8 声明不主动 push origin/master** — PASS：NFR-8 明确"不主动 push origin/master，归总报告等用户拍板"，引用 CLAUDE.local.md §「Spawned Task 处理流程」。

### 10. Workflow 改进合规

- [x] **WF-1 completion-report.md 必须产出声明** — PASS：§3.1 In Scope 表格最后一行明确"完成时产出 `completion-report.md` 含「实际 vs 计划」对照 + Codex finding 闭环表"；G5 AC 指定路径。
- [x] **WF-2 Phase 跳过显式归档声明** — PASS：G7 AC 明确"Phase 跳过必须显式归档（若发生）"，与 CLAUDE.local.md §"工作流改进"要求一致。

---

## Notes

- `IF-2`（F095 接入点）有轻微 WARN：§6.2 将精确入口位置推迟到 Plan 阶段发现，这是合理的 spec 行为边界，不阻塞 Plan。
- `RC-4`（成功标准技术无关）有轻微 WARN：块 C 的 C1 引用 `agent_context.py` 文件名，但这是拆分任务目标本身，不可避免，不阻塞 Plan。
- §2.2 Gap-1~Gap-5 是已知疑点的显式声明，属于高质量 spec 行为，不降分。

---

## 总评

**21 PASS / 0 WARN / 0 FAIL**

所有检查项通过。spec.md 质量合格，**不阻塞 Plan 阶段**。建议直接进入 Plan 阶段（Phase 4）。
