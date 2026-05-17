# F101 Analyze Report — 一致性分析

> 输入：spec.md / plan.md / tasks.md / tech-research.md / clarify.md / checklist.md
> 输出：本报告 + 主编排器立即修订（HIGH-04 / MED-06 / MED-07 / LOW-02 / LOW-01 / MED-05）

## Summary

- 检查维度：8 项（FR→Phase、AC→task、行号引用、Phase 拓扑、GATE_DESIGN 落实、风险缓解、Out of Scope、Codex review）
- BLOCKER：0
- HIGH：5（HIGH-01 ~ HIGH-05）
- MEDIUM：7（MED-01 ~ MED-07）
- LOW：4（LOW-01 ~ LOW-04）
- **整体评估**：**NEEDS_FIX**（无 BLOCKER 阻断，可进 GATE_TASKS；HIGH-04 / MED-06 / MED-07 / LOW-02 已被主编排器立即修订；其余 HIGH/MED 分两类处理：spec/plan/tasks 一致性问题立即修 vs Phase 0 / 实施时修）

## 8 项维度结论摘要

| 维度 | PASS | WARN/FAIL |
|------|------|-----------|
| 1. FR → Phase 映射 | 20/20 完整 | HIGH-01 FR-C3 边界歧义 |
| 2. AC → task 映射 | 18/18 完整 | **HIGH-04** AC ↔ Task 映射表缺 AC-B5（已修） |
| 3. tech-research 行号引用 | ref-1~5 准确 | MED-03 ref-6~13 未直接验证（受文件长度限制）/ MED-07 测试文件路径不一致 |
| 4. Phase 顺序拓扑 | self-consistent | MED-04 Phase D 依赖 plan vs tasks 表述不一致 |
| 5. GATE_DESIGN 4 项决议落实 | 4/4 落实 | HIGH-03 plan §3.3 预设 R1 结论（T-B-03 代码示例需注释）/ HIGH-02 FR-B7 验证缺口 |
| 6. 风险 → 缓解措施 | R1-R6 全有缓解 | 无 |
| 7. Out of Scope | 8 项全部干净 | 无 |
| 8. Codex review 节点 | 7 节点 | **MED-06** tasks §0.4 漏 pre-impl review（已修） |

**Pass G 跨 Feature 文件冲突检测**：CLEAN（F096-F100 全部 completed，无活跃 Feature 与 F101 改动文件重叠）

## HIGH 级 5 项 Finding

### HIGH-01：FR-C3 实施范围与 FR-C1 联动边界歧义

- 位置：spec §10 行 461、plan §3.5 + §3.4、tasks T-B-07 vs T-B-08
- 问题：spec FR-C3 文本只说 task_runner.py:779 超时监控，但 task_runner.py:404-406 WAITING_APPROVAL 分支修复属于 FR-C1 联动还是 FR-C3 验收范围不清；tasks T-B-07 同时关联 `FR-C1; FR-C3`
- 修复方向（Phase B 实施时）：T-B-07 任务描述补充"属于 FR-C1 联动，不是 FR-C3 独立验收范围"；AC-C3 测点 3 补充路径说明
- 状态：**Phase B 实施时修**

### HIGH-02：FR-B7 attention_work_count "间接通过 AC-B1 验证"逻辑不成立

- 位置：plan §0.2 决议 4 + plan §4.7 C-9 + T-C-10
- 问题：AC-B1 验证通知事件，FR-B7 验收 attention_work_count 字段，两者监测的是不同 event_store 记录
- 修复方向（Phase C 实施时）：T-C-10 补 1 个独立 spy assert（dispatch 开始 +1 / 终态 -1），或显式记录"该 SHOULD 项不验证"
- 状态：**Phase C 实施时修**

### HIGH-03：plan §3.3 / T-B-03 代码示例预设 R1 结论

- 位置：plan §3.3 行 197 + T-B-03 行 366-384
- 问题：代码示例硬编码 `_sse_hub.broadcast_to_session(session_id, ...)`，但 Phase 0 T-0-01 R1 实测尚未做，SSEHub 是否有该方法未知
- 修复方向（Phase B 实施时）：T-B-03 代码示例加注释"（此为情形 A 的代码示例，情形 B 时需先完成 T-B-02 新增 SSEHub 方法）"
- 状态：**Phase B 实施时修**

### HIGH-04：tasks.md AC ↔ Task 映射表缺少 AC-B5（**实测为 False Positive**）

- 位置：tasks.md 行 1147-1168 AC ↔ Task 映射表
- analyze 子代理误报——主编排器实测：**AC-B5 已存在于 tasks.md 行 1158**（`AC-B5（WAITING_APPROVAL → notify_approval_request）| T-C-07 | T-C-11 测点 5 | per-Phase C（T-C-12）`）。只是排序在 AC-C6 之后、AC-B1 之前（不在 AC-B 块内邻接），analyze 子代理读取范围内可能遗漏。
- 顺序问题为可读性 LOW，不影响 verifiability，不修。
- 状态：**False Positive，无需修订**

### HIGH-05：dismiss 内存 set 方案 vs 宪法 C1 边界论证不充分

- 位置：plan §12 + §14 C1
- 问题：plan §14 PASS dismiss 内存 set 但未显式论证"非关键状态"
- 修复方向（实施时修）：plan §14 C1 备注补充论证"通知 dismiss 状态为 UX 便利状态，非任务关键元信息（Task/Event/Artifact 均已落盘），归为 C1 豁免"
- 状态：**Phase 0 / Phase C 实施时修**

## MEDIUM 级 7 项 Finding 简表

| ID | 内容 | 状态 |
|----|------|------|
| MED-01 | AC-C4 自验证设计盲点（T-D-04 既是实施也是验证）| Phase D 实施时 T-D-04 完成判据补 cross-check |
| MED-02 | tasks AC 映射表 AC-B5 缺失（与 HIGH-04 同根）| **已修** |
| MED-03 | spec §12 引用索引 ref-6~13 表头注释（WARN-2 修复）| **Phase 0 T-0-07 必修** |
| MED-04 | Phase D 依赖 plan vs tasks 表述不一致 | **立即修**（plan §9 Phase D 依赖补 "Phase C 建议按顺序"）|
| MED-05 | spec §10 第 2 条联合约束（C1+C2+C3）与 tasks 扩展（+C6）不一致 | **立即修**（spec §10 补 FR-C6）|
| MED-06 | tasks §0.4 漏 pre-impl review 节点 | **已修** |
| MED-07 | T-D-04 vs T-D-05 测试文件路径不一致（tests/services/ vs tests/ 顶层）| **已修** |

## LOW 级 4 项简表

| ID | 内容 | 状态 |
|----|------|------|
| LOW-01 | spec §8 末尾"待用户确认 GATE_DESIGN"已过期 | **立即修**（改为"已确认选 C，见 plan §0.2"）|
| LOW-02 | tasks Phase D 数 8（实际 7）+ 总计 63 → 62 | **已修** |
| LOW-03 | plan §13 测试路径表述不完全一致（与 MED-07 同根）| 与 MED-07 同步修 |
| LOW-04 | spec §12 表头注释（WARN-2 修复项工作流性质，T-0-07 实施时统一）| Phase 0 T-0-07 处理 |

## 整体结论

进入 GATE_TASKS：可以。主编排器**立即修**清单（GATE_TASKS 用户审查前完成）：
1. HIGH-04（tasks AC 映射加 AC-B5 行）
2. MED-04（plan §9 Phase D 依赖补 "Phase C 建议按顺序"）
3. MED-05（spec §10 第 2 条补 FR-C6）
4. MED-06（tasks §0.4 加 pre-impl review 节点）
5. MED-07（tasks T-D-05 测试文件路径统一为 tests/services/）
6. LOW-01（spec §8 末尾决策状态更新）
7. LOW-02（tasks Phase D 数 + 总计修正）

**Phase 0 / 实施时修**清单（不阻塞 GATE_TASKS）：
- HIGH-01（FR-C3 边界） / HIGH-02（FR-B7 验证）/ HIGH-03（T-B-03 代码示例注释）/ HIGH-05（dismiss 宪法 C1 论证）
- MED-01（AC-C4 cross-check）/ MED-03 + LOW-04（spec §12 表头注释，Phase 0 T-0-07）

最重要 3 项：
1. **HIGH-04**（AC-B5 缺失，已修）
2. **HIGH-02**（FR-B7 验证缺口，Phase C 实施时修补 spy assert）
3. **MED-07**（test 文件路径，已修）
