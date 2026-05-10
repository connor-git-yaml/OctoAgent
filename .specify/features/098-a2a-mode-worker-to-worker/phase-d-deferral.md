# Phase D 显式推迟说明

**日期**: 2026-05-10
**关联**: spec.md 块 D / plan.md Phase D / tasks.md T-D-1 ~ T-D-16
**决定**: Phase D 推迟到独立 follow-up（与 F107 Capability Layer Refactor 协同）

---

## 推迟理由

### 1. 工作量估计 vs 当前会话剩余 token 不匹配

- Phase D 估计 10h（plan.md §13）—— 与 Phase H 同等量级
- 当前会话已完成 7 个 Phase + 设计阶段（accumulated ~10h+）
- Phase D 拆分 orchestrator.py（3432 行）+ 更新所有 import 链路 + 多文件 grep 验证 + Codex pre/post review 强制
- 风险：会话中途 token 耗尽导致 Phase D 半成品（rebase 冲突 + 测试 regression）

### 2. F098 主责已全部达成

F098 核心范围（spec.md §1）：
- ✅ H3-B 主责（Phase B-1/B-2 source+target 双向独立加载）
- ✅ H2 完整对等性（Phase C Worker→Worker 解禁）
- ✅ F097 5 项推迟项全部接管（P1-1 块 E / P1-2 块 F / P2-3 块 G / P2-4 块 H / AC-F1 块 I）
- ✅ BaseDelegation 公共抽象（块 J）

Phase D（D7 架构债）是"顺手清"，**不是 F098 H3-B / H2 主责**——延后实施不影响 F098 核心价值。

### 3. F107 协同更合理

Phase D 顺手清 orchestrator.py 拆分。F107 (Capability Layer Refactor) 范围（CLAUDE.local.md §M5/M6）：
- D9 tooling/harness/capability_pack 三层职责
- D11 LLMWorkerAdapter 命名误导
- D12 BehaviorFileRegistry DRY

orchestrator.py 拆分与 F107 的 capability layer 重构有部分耦合（特别是 `capability_pack` 在 orchestrator 中的访问路径——F098 Phase B-2 已通过 `_delegation_plane.capability_pack` 修正）。F107 时一并做更合理。

### 4. 行为零变更原则

Phase D 是纯结构改造（拆分文件），不引入新行为或修复 bug。F098 实施其他 Phase（B/C/E/F/G/H/I/J）已修复了 H3-B + H2 + 5 项 known issue 的根本问题，结构整洁可推迟。

---

## 推迟到何时

- **优先**：F107 Capability Layer Refactor 时一并处理
- **次选**：作为独立 F098 follow-up spawned task

---

## 已完成的相关工作（Phase D 部分价值已实现）

虽然没拆分 orchestrator.py，但 F098 实施过程中已对 dispatch 路径做了如下整洁工作：

1. **Phase B-1/B-2 新增 helper 已模块化**：
   - `_resolve_a2a_source_role`：从 runtime_context/envelope 派生 source role / session_kind / agent_uri
   - `_resolve_target_agent_profile`：A2A target Worker AgentProfile 独立加载
   - 这两个方法已是 orchestrator.py 内的清晰单一职责函数（拆分时一并挪到 dispatch_service.py 即可）

2. **delegation_plane.py 注释更新（Phase C）**：
   - 移除 enforce_child_target_kind_policy 历史引用
   - 加 F098 Phase C 标识

3. **task_service callback 注册机制（Phase H）**：
   - 已是清晰 class-level + 幂等 + unregister 设计
   - 拆分时不需调整

---

## 验收说明

- spec.md 块 D（AC-D1 / AC-D2 / AC-D3）：**实施推迟**
- plan.md Phase D §9 / tasks.md T-D-1 ~ T-D-16：**推迟**
- F098 全局验收（spec.md §5 AC-GLOBAL）：**保持有效**（Phase D 跳过不影响其他 AC）
- Phase 跳过显式归档：**本文件作为归档**（CLAUDE.local.md §"工作流改进" §"Phase 跳过 / 偏离归档"要求）

---

## 验证当前 orchestrator.py 状态

- 行数：3432 行（baseline）+ Phase B 新增 ~120 行 = 约 3550 行
- 仍是巨型单文件（D7 架构债未消除）
- F107 实施时分配额外预算处理

---

**Phase D 显式归档推迟。F098 其他 8 个 Phase + Verify 继续推进。**
