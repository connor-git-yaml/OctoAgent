# F095 Worker Behavior Workspace Parity — Trace

baseline: 284f74d (F093 完成后)
分支: feature/095-worker-behavior-workspace-parity
worktree: .claude/worktrees/F095-worker-behavior-workspace-parity
F094 并行: feature/094-worker-memory-parity（独立 worktree）

## 编排决策

- research_mode: skip（需求来源 = M5 prompt + CLAUDE.local.md SoT）
- gate_policy: balanced（F093/F094 沿用）
- preset: 无覆盖

## Phase 进度

| 时间 | Phase | 状态 | 备注 |
|------|-------|------|------|
| skipped | Phase 1a/1b/1c/1d | research | 跳过（research_mode=skip）|
| completed | Phase 2 | specify v0.1 | spec.md 起草（含块 A 实测 + §6.1-6.5 决策）|
| completed | Phase 3.5 | GATE_DESIGN v0.1 | 用户拍板：spec 通过 + IDENTITY bug 顺手修 |
| completed | Phase 4 | plan v0.1 | plan.md 起草（A→B→C→D 顺序）|
| completed | baseline | test 计数 | `test_behavior_workspace.py` = 53 passed |
| completed | Codex review #1 | spec + plan adversarial | 5 high + 7 medium + 3 low，全接受；2 high 触发 USER/BOOTSTRAP 决策翻转 |
| completed | Phase 3.5 | GATE_DESIGN v0.2（决策翻转）| 用户拍板：USER 改扩入 / BOOTSTRAP 改不扩入 |
| completed | Phase 2/4 | spec/plan v0.2 重写 | 闭环全部 15 条 finding；Phase 顺序改 A→B→C→D→E（5 个 Phase）|
| in_progress | Phase 5 | tasks | tasks.md 起草 |

## 块 A 实测核心结论

1. baseline Worker 实际进 LLM 的 behavior 文件 = 4 个（不是 5），IDENTITY 被 envelope 二次过滤剥离
2. share_with_workers 是真实过滤层，与白名单 `AND` 叠加（agent_decision.py:329）
3. IDENTITY.worker.md 模板存在 + 派发路径完整，但 envelope 把 IDENTITY 剥了——模板渲染了 Worker 永远看不到（隐性 bug）
4. SOUL.worker.md / HEARTBEAT.worker.md / BOOTSTRAP.worker.md 模板均不存在
5. BEHAVIOR_LOADED 事件不存在（grep 0 命中）
6. F094 改动域（packages/memory/、recall preferences、agent_id）与 F095 工作域文件级低冲突（Codex M9 推动从"完全静态隔离"调整为"低冲突 + AC-7b 集成验证"）
7. **BOOTSTRAP.md 实测内容 = 主 Agent 用户首次见面对话脚本**（"你好！我是 __AGENT_NAME__"... "你希望我怎么称呼你"... 用 `behavior.propose_file` 改 IDENTITY/SOUL）→ 完全不适合 Worker
8. **USER.md 实测内容 = 用户长期偏好**（语言中文 / 信息组织 / 回复风格 / 确认偏好），无 user-facing 对话指令 → 适合 Worker

## Codex review #1 闭环

15 条 finding 全部接受。详见 codex-review-spec-plan.md。

**关键变更（v0.2）**：

- §6.1 USER.md → **扩入** Worker 白名单（v0.1 翻转）
- §6.2 BOOTSTRAP.md → **不扩入** Worker 白名单（v0.1 翻转）
- §6.4 最终白名单 8 文件 = `{AGENTS, TOOLS, IDENTITY, PROJECT, KNOWLEDGE, USER, SOUL, HEARTBEAT}`（去 BOOTSTRAP 加 USER）
- §6.5 BEHAVIOR_LOADED → BEHAVIOR_PACK_LOADED + BehaviorPack.pack_id 字段（让 F096 USED 可引用）
- AC-2b 加 prompt 拼接顺序断言
- AC-4 覆盖所有 Worker 创建入口
- AC-5 加 pack_source / pack_id payload
- AC-6 扩展为所有 non-WORKER profile 行为零变更
- AC-7b 加 F094/F095 双 agent_id 集成验证
- Phase 顺序改 A→B→C→D→E（B 模板先于 C 白名单扩展，避免 Worker 中间态看通用 SOUL/HEARTBEAT）
- plan §0.6/0.7/0.8/0.9 增 4 项实测前置任务
- spec §10 拆分"已决策"与"待完成"

## 关键决策（spec §6 v0.2）

- §6.1 USER.md → **扩入** Worker 白名单（v0.2 翻转）
- §6.2 BOOTSTRAP.md → **不扩入** Worker 白名单（v0.2 翻转）
- §6.3 share_with_workers → **保留字段（UI 用），去掉 envelope 过滤**；shared_file_ids 字段名保留 + docstring 显式语义变更
- §6.4 最终白名单 = 8 文件（FULL 9 - BOOTSTRAP）
- §6.5 BEHAVIOR_PACK_LOADED + pack_id（F095 范围 minimal，USED 留 F096）

## 制品

- spec.md v0.2
- plan.md v0.2
- codex-review-spec-plan.md（review #1 闭环表）
- trace.md（本文件）
- tasks.md（已 commit cfcc24d）

## Phase 0 实测发现（开工前）

| 项 | 实测结论 |
|----|----------|
| T0.1 e2e_smoke baseline | pre-commit hook 8 passed, 3197 deselected, 13.58s（cfcc24d 提交时通过）|
| T0.2 envelope contract audit | `shared_file_ids` 4 处命中：envelope 构造（agent_decision:335）、metadata（338-339）、metadata 转发（471）、Field 定义（models/behavior.py:285）；**无业务消费者依赖 share_with_workers=True 旧语义** → 选项 A（保留字段名 + docstring 说明）安全 |
| T0.3 EventStore 接口 | `EventStore.append_event(event)` 是 **async**；agent_decision.py 全文件 sync 函数 → **Phase D 工程约束**：emit 必须从 async caller 发出，不能在 `resolve_behavior_pack` 内直接 emit |
| T0.4 BehaviorPack.pack_id | 字段**已存在**（`models/behavior.py:99` `pack_id: str = Field(default="")`）；当前生成 `f"behavior-pack:{profile_id}"`（无 load_profile 维度，无 hash 内容）→ Phase D 扩展为含 load_profile + content hash |
| T0.5 Worker 创建入口 | production 仅 2 处：`worker_service.py:1383` `kind="worker"` + `agent_service.py:639` `kind="worker"` → Phase C 集成测覆盖至少其中之一 |

## Phase A 实施记录

| 时间 | 项 | 结果 |
|------|----|------|
| Phase A 实施 | `build_behavior_slice_envelope` 移除 `share_with_workers AND` 子句；docstring 显式说明语义变更 | done |
| Phase A 测试 | 新增 `test_agent_decision_envelope.py` 含 10 个测试（5 envelope + 2 ordering + 2 FULL zero-change + 1 ROLE source_file_ids）| done |
| Phase A 全量回归 | 3088 passed, 10 skipped, 113 e2e deselected, 0 regression | PASS |
| Phase A Codex review | 3 finding（0 high）：F1 测试空断言（修）/ F2 FULL zero-change 缺显式断言（加）/ F3 worker_slice metadata 字段语义变更（已 docstring 说明 + Phase 0 contract audit 闭环）| 闭环 |
| Phase A 测试（review 闭环后）| `test_agent_decision_envelope.py` + `test_behavior_workspace.py` = 63 passed | PASS |

## Phase B 实施记录

| 项 | 结果 |
|----|------|
| 模板新建 | SOUL.worker.md (~30 行) + HEARTBEAT.worker.md (~40 行) |
| variant 注册 | _BEHAVIOR_TEMPLATE_VARIANTS 加 (SOUL.md, True) + (HEARTBEAT.md, True)；最终 3 个 worker variant |
| 测试 | TestWorkerVariantTemplates (5) + TestWorkerWorkspaceFilesInit (3, 含 kind="worker" 路径) |
| 全量回归 | 3133 passed, 0 regression |
| Codex per-Phase review | 4 finding 闭环（2 medium + 2 low）：MED1 Memory 越权 / MED2 A2A 提前固化 / LOW1 fixture 路径覆盖 / LOW2 断言过脆 |
| commit | c1d2fd0 |

## Phase C 实施记录

| 项 | 结果 |
|----|------|
| 白名单扩展 | _PROFILE_ALLOWLIST[WORKER] 5 → 8 文件（去 BOOTSTRAP，加 USER + SOUL + HEARTBEAT）|
| 测试断言更新 | 3 个老断言反转 + 5 个新增（TestPhaseCWorkerAllowlistExpansion + e2e filesystem + e2e worker pack）|
| 全量回归 | 3139 passed, 0 regression |
| Codex per-Phase review | 3 finding（1 HIGH + 2 MEDIUM）：HIGH1 已 materialize 主 Agent 版迁移问题 → **deferred-followup**（推迟到 F107 capability layer refactor，completion-report 留人工迁移指引）；MED2/MED3 测试覆盖加 worker filesystem e2e |
| commit | f7c5e48 |

## Phase D 工程约束（T0.3 + Phase D 实施前实测）

`resolve_behavior_pack` sync；`EventStore.append_event` async；agent_decision.py 全文件 sync；
所有 production caller（worker_service.py:168 / 255、agent_service.py:111）调用 `build_behavior_system_summary` 也是 sync。
**没有现成的 async 边界**让 emit 直接接入。

实施选项：
- **选项 A**（最 invasive）：把整条调用链改 async — 影响 worker_service / agent_service / 外层 control_plane
- **选项 B**（脆弱）：`asyncio.get_running_loop().create_task()` fire-and-forget — task 异常无人捕获 + reliability 问题
- **选项 C**（推荐）：metadata 标 cache_state + pack_source；提供 helper `make_behavior_pack_loaded_event_payload`；caller 接入推迟到一个明确 async 边界（F096 实施时一并接入）

**当前决策**：Phase D 简化为 minimal 实现，spec AC-5 调整为分两阶段 — F095 提供 infrastructure（pack_id 改造 + payload schema + helper + 单测），实际 EventStore 接入推迟到 F096。理由：避免 F095 invasive 修改 worker_service/agent_service 调用链 async 化，同时 F096 自己定义 BEHAVIOR_PACK_USED 事件本就需要接入 EventStore，可一并完成。


## Phase D 实施记录

| 项 | 结果 |
|----|------|
| EventType.BEHAVIOR_PACK_LOADED 新增 | done |
| BehaviorPackLoadedPayload schema (10 字段) | done |
| _generate_behavior_pack_id helper（hash 化）| done |
| resolve_behavior_pack 三路径标 cache_state="miss" + pack_source（按 source_kind 区分）| done |
| make_behavior_pack_loaded_payload sync helper | done |
| 测试 +11（pack_id 4 / cache state 3 / payload 3 / mtime invalidation 1）| 89 passed |
| 全量回归 vs F094-merged baseline | 3191 passed, 0 net regression |
| Codex per-Phase review | 4 finding 闭环（HIGH1 pack_id sha256 / MED2 source_kind 区分 / MED3 RecallFrame 字段名澄清 / LOW4 mtime 测试）|
| commit | bbf2b4d |

## Phase E 实施记录（Final + 收尾）

| 项 | 结果 |
|----|------|
| rebase F094-merged master | 0 冲突；4 commits 重新编号 |
| 全量回归 vs F094-merged baseline | 3191 passed, 0 net regression（除已知 ThreatScanner baseline flake）|
| Final cross-Phase Codex review | 6 finding 闭环（2 HIGH spec/plan 与实施不一致 / 3 MED 收尾制品 + AC-4 + SOUL F094 冲突 / 1 LOW pack_id 长度）|
| spec/plan v0.3 修订 | AC-5 调为分两阶段 + AC-7b 间接关联 partial + AC-4 production 路径覆盖 + plan pack_id 长度 |
| SOUL.worker.md 修订（Final MED3）| 区分 AGENT_PRIVATE memory（任务自身事实，F094 已启用）vs USER 偏好（回报主 Agent）|
| CLAUDE.local.md F095 行更新 | 完整状态描述 + 关键决策 + deferred-followup |
| docs/blueprint.md 审计 | grep 无相关章节，无需同步 |
| docs/codebase-architecture/harness-and-context.md | handoff.md 留接口（Harness 结构未变；F107 同步更稳）|
| completion-report.md | 已产出 |
| handoff.md | 已产出（F096 接口 + F107 deferred）|

## 总览

- 5 commits（5df00ec docs / 1a09d2d Phase A / 69d69c8 Phase B / a665fc1 Phase C / bbf2b4d Phase D）+ 本 Phase E 收尾 commit
- 全部 35 Codex finding 闭环或显式 deferred（F096：BEHAVIOR_PACK_LOADED EventStore 接入 + AC-7b 完整集成测；F107：已 materialize 主 Agent 版迁移）
- 用户拍板节点：GATE_DESIGN v0.1 → v0.2 翻转 USER/BOOTSTRAP；Phase D 范围简化决策；F094 已合 master rebase 启动
- 测试：89 F095 测试 / 3191 全量 0 net regression
