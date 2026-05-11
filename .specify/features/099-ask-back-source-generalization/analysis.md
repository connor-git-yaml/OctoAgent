# F099 一致性分析报告（spec / plan / tasks）

**分析日期**：2026-05-11
**输入**：spec.md v0.2（GATE_DESIGN 已通过）/ plan.md v0.1 / tasks.md v0.1
**结论**：0 CRITICAL / 2 HIGH / 4 MEDIUM / 3 LOW；FR 18/18 任务覆盖；G-1~G-4 全部落实；F098 OD-1~OD-9 无偏离

---

## 1. 发现表

| ID | 类别 | 严重性 | 位置 | 摘要 | 建议 |
|----|------|--------|------|------|------|
| F-001 | AC 缺失 | HIGH | spec §4 | AC-B5 缺失。FR-B3（escalate_permission 审批返回值 + 不 raise）核心行为无 spec §4 权威 AC，Verify T-V-5 的 13 条 AC 验证表会漏 | 在 spec §4 追加 AC-B5（Given/When/Then） |
| F-002 | 不一致 | HIGH | spec §6 vs plan §-1 P-VAL-1 | spec §6 缓解写"超时返回 'timeout' 字符串"；plan §-1 P-VAL-1 实测 ApprovalGate 超时返回 "rejected"。tasks.md test_escalate_permission_timeout_returns_rejected 与 spec §6 文字矛盾 | 修订 spec §6 Risks 第 2 条："timeout" → "rejected"（P-VAL-1 实测） |
| F-003 | AC 缺失 | MEDIUM | spec §4（FR-B5 无 AC）| FR-B5 SHOULD 文档类，无测试可行 | 注明"FR-B5 由 handler docstring 覆盖，无独立 AC"（接受现状）|
| F-004 | AC 弱覆盖 | MEDIUM | spec §4 AC-B4 vs FR-E4 | AC-B4 仅写"进入 WAITING_APPROVAL"，未含"回 RUNNING + tool_result" | 拆 AC-B4 为 a/b 段（进入 + 状态回归） |
| F-005 | 覆盖缺口 | MEDIUM | AC-B4 SSE 推送层 | AC-B4 SSE 卡片由 mock 验证非 e2e | 接受现状（baseline ApprovalGate 已含 SSE，T-V-3 Final review 说明）|
| F-006 | AC 缺失 | MEDIUM | spec §4（FR-C4 无 AC）| FR-C4 降级 + warning 无 AC-C3 | 在 spec §4 追加 AC-C3（invalid → MAIN + warning + 0 exception）|
| F-007 | 规格不足 | LOW | spec §6 文字残留 | 与 F-002 同源 | 与 F-002 合并修订 |
| F-008 | 并行风险 | LOW | tasks T-B-2+T-B-3+T-B-4 | 三 handler 同文件 ask_back_tools.py，并行需手动 merge | tasks 加注 |
| F-009 | 规格不足 | LOW | tasks.md 标题 | "总任务数 41" 实际 36 | 修正为 36 |

---

## 2. FR ↔ Plan ↔ Task ↔ Test 覆盖矩阵

18 项 FR 全部任务覆盖（100%），其中：
- ✅ 完整覆盖：FR-B1, B2, B4, B6, C1, C2, C3, D1, D2, D3, D4, E1, E2, E3
- ⚠️ AC 缺失：FR-B3（→F-001）, FR-B5（→F-003）, FR-C4（→F-006）, FR-E4（→F-004）

## 3. GATE_DESIGN G-1~G-4 落实

| 决议 | 状态 | 证据 |
|------|------|------|
| G-1 OD-1~7 推荐 | ✅ | plan §0/§3，tasks 全程引用 |
| G-2 FR-C1 MUST | ✅ | plan §3 Phase C 步骤 5 含 automation/user_channel 完整派生，T-C-4/T-C-7 |
| G-3 P-VAL-1/2 验证 | ⚠️ | 验证完整但 P-VAL-1 结论未回写 spec §6（→F-002/F-007）|
| G-4 命名 + 常量化 | ✅ | plan §1 独立章节 + source_kinds.py 完整代码 |

## 4. F098 OD 不偏离

OD-1（CONTROL_METADATA_UPDATED 不污染 turns）/ OD-5（不继承 BaseDelegation）/ OD-7（不引入新 spawn 工具）/ OD-9（capability_pack 路径）—— 全部无偏离风险。OD-2/OD-3/OD-4 spec §5 Non-Goals 排除。

## 5. 任务依赖与测试覆盖

- 关键路径无循环（C → D → B → E → Verify）
- T-B-2/T-B-3/T-B-4 标"可并行"但同文件，需手动 merge（F-008）
- 单测/集成测/e2e 三层覆盖每个块

## 6. 跨 Feature 文件冲突

**Pass G: CLEAN** — F099 改动文件与 F097/F098（已合入 master）/ F100（无 tasks.md）无重叠

---

## GATE_TASKS 风险评估

**结论：建议修订 4 项（2 HIGH + 1 MEDIUM + 1 LOW）后进入实施**

修订工作量：~10 分钟（3 处 spec.md 编辑 + 1 处 tasks.md 编辑）

修订完成后三制品一致性 100%，可安全进入 Phase C 实施。

---

v0.1 - GATE_TASKS 分析完成
