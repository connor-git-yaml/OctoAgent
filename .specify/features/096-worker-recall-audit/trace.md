# F096 Worker Recall Audit & Provenance — Trace

## Baseline
- 分支：feature/096-worker-recall-audit
- 起点 commit：dd70854（F095 Final review，origin/master）
- F095 baseline 测试：3191 passed
- F094 baseline 测试：3029 passed (F094 自身) → F094 合入后 master 含 F094+F095

## 编排模式
- mode: feature
- research_mode: skip（F094/F095 handoff 已提供完整上下文，spec 阶段块 A 实测侦察替代正式 research）

## Phase 序列（计划，按 orchestration.yaml feature 模式）
- Phase 0 constitution_check ✅（init-project 报告已存在）
- Phase 0.5 research_mode_determination → research_mode = skip
- Phase 1a/1b/1c/1d → 跳过（research_mode=skip）
- Phase 2 specify ⏳
- Phase 3 clarify_and_checklist
- Phase 3.5 GATE_DESIGN
- Phase 4 plan
- Phase 5 tasks
- Phase 5.5 analyze
- Phase 6 implement（Phase 顺序 A→C→D→B→E→F，"先简后难"）
- Phase 6.5 verify_independent
- Phase 7a/7b spec_review + quality_review
- Phase 7c verify

## Codex review 节点
- pre-spec/plan adversarial review（Phase 4 后）
- per-Phase implement review（每个 implement Phase 后）
- Final cross-Phase review（Phase 7c 前）

## 启动记录
- 编排器 init-project.sh：constitution / config / gate_policy 已就位（HAS_GATE_POLICY=true，PROJECT_CONTEXT_MODE=dual）

## 进度记录（main session 1）

| 阶段 | 时间 | 状态 | 制品 |
|------|------|------|------|
| Phase 0 constitution_check | 起点 | ✅ NEEDS_CONSTITUTION=false |
| Phase 0.5 research_mode | 起点 | ✅ research_mode = skip（F094/F095 handoff 充分上下文）|
| Phase 2 specify | 块 A 实测后 | ✅ spec.md v0.1（402 行） + research/codebase-scan.md（块 A 实测合成）|
| Phase 4 plan | 5 项关键路径 trace 后 | ✅ plan.md v0.1（含 baseline 校正：路径 A 已 persist / build_task_context 是 LLM 决策环唯一入口 / Worker dispatch 复用主路径）|
| Phase 5 tasks | plan 后 | ✅ tasks.md（54 任务清单）|
| pre-spec/plan Codex review | tasks 后 | ⏳ codex exec foreground 跑（GPT-5.5 high reasoning，12+ 分钟仍在 reasoning）|

## baseline 关键事实（plan 阶段实测验证）

1. **`agent_context.py:914 save_recall_frame` 已存在** — 路径 A 同步 recall RecallFrame 已 persist（不需 F096 Phase A 补持久化）
2. **`build_task_context` (agent_context.py:591) 是 LLM 决策环唯一入口** — 调用方 `task_service.py:1250` 唯一；BEHAVIOR_PACK_LOADED + USED 都在此 emit
3. **Worker dispatch 路径** = build_task_context 主路径 — 复用 emit + RecallFrame 持久化，无需独立改造
4. **`render_behavior_system_block`（agent_decision.py:647-700）** 是 LLM system block 渲染入口（第三处 resolve_behavior_pack）
5. **`agent_context_store.list_recall_frames`（line 1152）** + **save_recall_frame（line 1053）** Store API 完整 ready

