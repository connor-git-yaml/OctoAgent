# Feature 085 — 架构清理与简化（Architecture Cleanup & Consolidation）

> 作者：Connor + 主线 review
> 引入：2026-04-29（F084 收尾后立即开 F085）
> Baseline：commit `cda8e00`（F084 全部完成 + F41/F42 critical fix），全量 2034 passed
> 模式：story（轻量 spec，无调研阶段）

## 1. 背景

F084 收尾时主线做了一轮架构 retrospective，发现 11 个改进项分 4 个优先级。F085 处理 P0+P1 共 6 项（架构合理性 bug + dead code 清理）。P2-P3（过渡期妥协 + reference gap）留给后续 Feature。

## 2. 改进项清单（P0+P1）

| # | 类别 | 问题 | 影响 |
|---|------|------|------|
| **T1** | P0 dead code | `GatewayToolBroker`（apps/gateway/services/tool_broker.py）0 调用方，~150 行包括 F25 ApprovalGate 集成代码白费 | F25 修复（WARN→ApprovalGate）实际不生效；spec FR-4.1 协同未真接通 |
| **T2** | P0 安全 gap | `subagents.spawn` 直接调 `launch_child`，**绕过 DelegationManager** 约束（max_depth=2 / max_concurrent=3 / blacklist）| LLM 可绕过 spec FR-5 约束创建无限递归 sub-agent |
| **T3** | P0 重复代码 | 系统 audit task 创建逻辑在 PolicyGate / ApprovalGate / operator_actions 3 处重复；测试 fixture 8 处重复 `RequesterInfo` 模板 | F41 暴露的 schema 必填错误就是模板未抽象的代价；类似错误未来还会发生 |
| **T4** | P1 dead 字段 | `OwnerProfile.bootstrap_completed` / `last_synced_from_user_md` 字段在 model 但 DDL 没列；F35 修复绕过它直接读 USER.md | 字段永远不持久化，model 层面是 dead 噪音 |
| **T5** | P1 dead code | `user_profile_tools.py:262` `try-except (ImportError, AttributeError): pass` 永远不触发 | Phase 2 占位 dead code |
| **T6** | P1 目录混乱 | `gateway/tools/` 仅 2 个文件，导致 `register_all` 显式 explicit imports 抵消 AST scan 自动发现 | 违反"工具自动发现"原则；新加工具有两个目录可选造成混乱 |

## 3. 关键不变量（必须遵守）

| 不变量 | 说明 |
|--------|------|
| **2034 passed / 0 regression** | F085 baseline 是 F084 末态 2034 passed；任何步骤后 ≥ 2034（允许 +增加 不允许 -减少 但 1 个 sc3 flaky 例外）|
| **22+ E2E 断言全部不变** | `/tmp/f084_e2e_verify.py` 25/25 passed 是黄金路径基准；T1-T6 任何步骤后跑此脚本必须仍然 ≥ 22 通过 |
| **6 个 spec SC grep 仍 = 0** | BootstrapSession / bootstrap.complete / is_filled / UserMdRenderer / BootstrapIntegrityChecker / bootstrap_orchestrator |
| **每步独立 commit** | T1-T6 各自独立 commit，便于 git revert 单步定位 regression |
| **每步前后跑 Codex review** | T7 最终独立 Codex review 处理 finding；中间步骤如有怀疑也跑 |

## 4. 验收准则

| SC | 验证 |
|----|------|
| SC-085-1 | `grep -F "GatewayToolBroker"` 在源码中 = 0（仅注释/commit message 可能命中） |
| SC-085-2 | subagents.spawn handler 内调用 `DelegationManager.delegate()`，超约束时返回 `WriteResult(status="rejected", reason="depth_exceeded/...")` 不实际派发 |
| SC-085-3 | `_ensure_audit_task` 在 PolicyGate / ApprovalGate / operator_actions 全部从一处 helper 引入（不再 3 份独立实现）|
| SC-085-4 | `grep -F "bootstrap_completed"` 在 OwnerProfile model 字段 = 0 |
| SC-085-5 | user_profile_tools.py 中 dead try-except 删除；保留 sync hook 真调用 |
| SC-085-6 | `gateway/tools/` 目录不存在 / 已合并到 `builtin_tools/`；register_all 不再有"防 F20 critical"显式 import 注释 |
| **SC-085-7（总）** | 全量 ≥ 2034 passed；E2E ≥ 22 passed；净删 dead code ≥ 200 行；Codex review 0 high finding |

## 5. 范围约束

**In**：上述 6 个改进项 + 配套测试改造 + 文档更新

**Out**：
- P2 GraphPipelineResult.detail / broker.py 双路径序列化（留 F086 Tooling Cleanup）
- P3 clarify 工具 / Routine 抽象 / behavior 目录标准化（留 F087+）
- 新功能（无）

## 6. 用户决策（已确认）

- 用户选 A 方案（P0 + P1 共 6 项）
- 工时预算 ~7.5h
- 每步独立 commit + 验证 + 防跑偏

---

完整任务计划见 `tasks.md`。
