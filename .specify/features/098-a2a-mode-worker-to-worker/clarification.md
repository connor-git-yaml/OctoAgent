# F098 Clarification — GATE_DESIGN 决策汇总

**日期**: 2026-05-10
**关联**: spec.md §0 Open Decisions + §8

---

## 决策模式

按 spec-driver-feature 规则，GATE_DESIGN 是硬门禁（必须用户明确决策）。本 Feature 由用户在 prompt 中已给出**推荐方向**（"按 handoff.md 修复方向"+"推荐"标注），spec 阶段实测验证后保持推荐不变。

**Batch 接受策略**：以下 9 项 OD 全部按 spec.md §8 推荐接受。

---

## OD 决策表

| OD | 决策点 | 候选 | 选定 | 决策理由（实测验证） |
|----|--------|------|------|--------------------|
| **OD-1** | P1-1 USER_MESSAGE 复用污染修复路径 | A 新 EventType `CONTROL_METADATA_UPDATED` / B `is_synthetic_marker` / C task store metadata | **A** | 实测 P1-1 影响 5+ consumers（context_compaction / chat / telegram / operator_actions / merge_control_metadata）；A 仅需 `merge_control_metadata` 演化（合并两类 events），其他 consumer 自然不受影响；B 需所有 consumer 加判断 (改动 5+ 处)；C 大改 |
| **OD-2** | P1-2 ephemeral runtime 复用修复路径 | A subagent 跳过 `find_active_runtime` / B delegation_id 派生独立 query key | **A** | 实测 `_ensure_agent_runtime` 中 subagent 信号已可识别（target_kind == subagent / agent_profile.kind == subagent）；A 仅在入口加 `if subagent_path: skip find_active_runtime` 即可；B 需 query key 演化 + find_active_runtime 签名改造 |
| **OD-3** | P2-3 事务边界 | A atomic 事务 / B 当前两步 + 重试 | **A** | F097 已颠倒顺序缓解但仍是 2 commit；A 需 EventStore.append_event_pending API 演化（新 API，向后兼容）；rollback 失败可恢复；B 缓解程度有限 |
| **OD-4** | P2-4 终态触发层 | A task_service._write_state_transition / B task_runner 多处 | **A** | task state machine 是终态权威；A 反向依赖通过 callback 注入解决（task_service 已有 `_terminal_state_callbacks` 类似机制基础）；B 是 F097 baseline 但分散+未覆盖所有终态路径 |
| **OD-5** | BaseDelegation 公共抽象 | A 提取父类 / B 各自独立 | **A** | F097 SubagentDelegation 与 F098 A2A delegation 共享 7+ 字段（delegation_id / parent_task_id / parent_work_id / child_task_id / spawned_by / created_at / closed_at / caller_agent_runtime_id）；A 抽象语义清晰；B 重复字段 DRY 差 |
| **OD-6** | agent_kind enum 演化 | A 新增 `worker_a2a` / B 保持 `worker` 通过 delegation_mode 区分 | **B** | agent_kind 是 agent 角色标识（main / worker / subagent），不应承载 dispatch 来源信息；delegation_mode 字段已是字符串（"main_delegate" / "subagent" / "a2a"）天然区分 |
| **OD-7** | Worker→Worker 解禁语义 | A spawn+通信合一 / B 仅通信 / C 仅 spawn | **A** | F098 当前 baseline `delegate_task(target_kind=worker)` 已是 spawn 路径；解禁=允许 Worker 调用此工具；如需"仅通信"语义留待 F099+（worker.send_message 工具）|
| **OD-8** | Phase G/H 顺序 | A H 先 G 后 / B G 先 H 后 / C 合并 | **A** | H 把 cleanup 挪到 task state machine 终态层（结构改造）；G 受益于 H 已统一的 hook（事务包装单一入口，避免分散事务化）；A 顺序最干净 |
| **OD-9** | A2A target Worker profile 加载 | A 按 requested_worker_profile_id 独立 / B 按 worker_capability 派生 / C 复用 source（baseline）| **A** | 实测 A2A baseline target_agent_profile_id 复用 source profile 是当前唯一阻断 H3-B 的 bug；A 直接 lookup（已有 envelope.metadata.requested_worker_profile_id）；B 是 fallback 路径（A 失败时用）；C 保持 baseline bug |

---

## 决策影响汇总

### 范围确认（plan 阶段不偏离）

- **9 个 implementation Phase**：B / C / D / E / F / G / H / I / J（spec.md §3 + §12）
- **Phase 顺序锁定**（spec.md §12）：E + F → B → C → I → H → G → J → D → Verify
- **新增 EventType**：1（`CONTROL_METADATA_UPDATED`）
- **新增 Pydantic model**：1（`BaseDelegation` 父类）
- **删除函数**：1（`enforce_child_target_kind_policy`）
- **大文件拆分**：1（orchestrator.py → orchestrator.py + dispatch_service.py）
- **task state machine 改造**：1（`_write_state_transition` + `_terminal_state_callbacks` 注册机制）
- **EventStore API 演化**：1（`append_event_pending` 新 API）

### 不在范围（明确锁定）

- F099 范围：worker.ask_back / source_type 泛化 / worker.send_message
- F100 范围：Decision Loop Alignment
- F107 范围：main direct AGENT_PRIVATE / WorkerProfile 完全合并 / 三层 capability layer refactor
- 独立 Feature：F096 Phase E frontend agent UI

### 关键风险（plan 阶段细化缓解）

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| **R1** Phase H task state machine 改造影响面大 | 中 | Phase H 必走 Codex review；callback 异常隔离 + 注入而非反向 import |
| **R2** Phase D orchestrator.py 拆分 import 链路兼容性 | 中 | 拆分时保留 orchestrator 模块的 re-export；外部 import 链路不变 |
| **R3** Phase E CONTROL_METADATA_UPDATED 向后兼容 | 中 | merge_control_metadata 合并两类 events；migration 测试历史 USER_MESSAGE 数据 |
| **R4** Phase G atomic 事务 EventStore API 演化 | 低 | append_event_pending 是新 API（向后兼容），逐步迁移 |
| **R5** Phase F subagent runtime 数量增长 | 低 | runtime 与 task 同生命周期；inactive runtime 不被复用 |
| **R6** worker→worker 死循环 | 低 | DelegationManager max_depth=2 已存在（F084）|

---

## 用户确认（GATE_DESIGN 拍板）

**spec.md v0.1 Draft + 9 OD 推荐方向 → 全部接受。**

**plan 阶段 SoT**：本 clarification.md 是 GATE_DESIGN 锁定后的权威文档。plan/tasks/implement 阶段不得偏离 OD-1 ~ OD-9 决策。

如发现实施时存在更优方向，必须显式归档（spec-driver-feature 规则 §"工作流改进"）。

---

**Clarification 完成。spec v0.1 → v0.2 GATE_DESIGN 锁定（9 OD 全部 batch 接受推荐）。**
