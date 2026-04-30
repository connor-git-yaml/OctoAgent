# Feature 087 — Spec/Plan/Tasks 一致性分析

**总评**：**PASS**（1 条 MEDIUM 已 inline 修复，5 条 LOW 进入 P5 文档同步阶段统一修）

---

## 各维度评分

| 维度 | 状态 | 说明 |
|------|------|------|
| FR 覆盖 | ✅ PASS | 35 条 FR 全覆盖；FR-19/20/21（Telegram 排除）显式由 hermetic fixture 涵盖 |
| SC 覆盖 | ✅ PASS | SC-1..SC-10 全覆盖；P5 验收 task 完整（T-P5-3/4/5/6/8/9/10） |
| 风险缓解 | ⚠️ WARN | R1-R10 全有 task 缓解；但 R2/R6/R7/R8 未列入 tasks "高风险 task 速查"表（实际散落在 T-P3-7/11、T-P2-10、T-P3-10），文档展示不全 |
| Phase 依赖 | ✅ PASS | P{N}→P{N+1} 严格串行；P1/P3/P4 内并行表达清晰；T-P2-1 标"最优先"避免 P3 race |
| 工时 | ⚠️ WARN | spec 7-10d / plan 9-14d / tasks 64-72h ≈ 8-9d 三者口径有 1-2d 偏差 |
| Constitution | ✅ PASS | 加强 6 条原则；每条都有具体 task 锚 |
| 关键 path | ✅ PASS | McpInstaller DI / OctoHarness / module reset 三个关键 path 锚定清晰 |
| 新发现 inconsistency | ⚠️ WARN | 1 MEDIUM + 5 LOW（详见下） |

---

## 详细问题清单

### MEDIUM-1（**已 inline 修**）：T-P2-12 与 T-P3-6 / T-P5-1 时序冲突

**原冲突**：
- T-P2-12 原 DoD：「迁移 `_build_real_user_profile_handler` 等 helper 到 `helpers/factories.py`；**旧文件中删除（双源真相消除）**」
- T-P3-6 DoD：「跑 `pytest apps/gateway/tests/e2e/test_acceptance_scenarios.py` 仍全绿」
- T-P5-1 DoD：「`apps/gateway/tests/e2e/test_acceptance_scenarios.py` 删除」
- 冲突：旧 acceptance 文件若依赖 helper，P2 删 helper 会让 P3-T6 双源验证失败

**修复方案**：T-P2-12 改为「**复制**到 helpers/factories.py（双写共存）」，删除动作合并到 T-P5-1（与旧 acceptance 文件一起删）。

### LOW-1：plan §9 / §13 与 tasks task 编号不同步
plan 文档用 `T-5-1..T-5-11`、tasks.md 实际编号 `T-P5-1..T-P5-10`。建议 P5 文档同步阶段统一。

### LOW-2：`octo e2e` CLI phase 归属漂移
plan §9 放 P5，tasks 前移到 P4.5（更合理，hook 上线后 debug 需要 CLI）。无害。

### LOW-3：风险速查表 R2/R6/R7/R8 未列出
tasks "高风险 task 速查"表仅列 R1/R3/R4/R5/R9/R10，应补全 R2/R6/R7/R8 的 task 关联。

### LOW-4：quickstart.md 交付点编号偏移
plan §13 写 P5-T4，tasks 实际为 T-P5-2。

### LOW-5：FR-19/20/21（Telegram 排除）形式上"无 task"
tasks "FR 覆盖映射"表显式标注由 T-P2-7 hermetic 涵盖，处理得当。

---

## Gatekeeper 建议

- **GATE_TASKS**：可继续（MEDIUM-1 已修，LOW 不阻塞 implement）
- **GATE_ANALYSIS**：可继续（on_failure 行为，无 failure 信号）

整体 spec/plan/tasks 三者一致性高。MEDIUM-1 已 inline 修，**不需要回到 plan/tasks 阶段大修**。
