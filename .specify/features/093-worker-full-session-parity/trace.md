# F093 Worker Full Session Parity — Spec-Driver Trace

Feature dir: `.specify/features/093-worker-full-session-parity/`
Branch: `feature/093-worker-full-session-parity`
Baseline: `7e52bc6` (F092 完成点)
Mode: `feature`（完整 10 阶段编排，本 session 跑到 GATE_TASKS）
Research mode: `skip`（用户 prompt 已提供完整 SoT 引用 + M5 战略 + F091/F092 实施记录）

## 时间轴（设计阶段，2026-05-08）

| 时间 | Phase | 状态 | 制品 / 决策 |
|------|-------|------|-------------|
| ~19:49 | 0 constitution_check | COMPLETED | `.specify/memory/constitution.md` 已存在；M5 阶段 0 后未变 |
| ~19:49 | 0.5 research_mode_determination | COMPLETED | `research_mode=skip` |
| ~19:49 | 1a/1b/1c/1d research | SKIPPED | research_mode=skip |
| ~19:50-20:05 | 2 specify | COMPLETED | `spec.md` 336 行（含 §11 Clarifications by clarify subagent） |
| ~20:05-20:10 | 3 clarify+checklist | COMPLETED | clarify: 0 ambiguity（追加 §11）；checklist: 21 PASS |
| ~21:25 | 3.5 GATE_DESIGN | PASS | 用户拍板"继续进入 Plan 阶段" |
| ~21:30-21:37 | 4 plan | COMPLETED | `plan.md` 266 行；Open-1~Open-6 全部决策（拆分候选 C / 测试 TDD / 事件复用 / 仅 round-trip / grep verify / hook 不动） |
| ~21:38-21:40 | 5 tasks | COMPLETED | `tasks.md` 461 行；Phase C/A/B/D 颗粒化任务清单 |
| ~21:42 | 5.5 analyze + GATE_TASKS | PASS（用户决策） | 用户选"快照制品 + 本 session 收尾" |
| ~21:45 | snapshot commit | DONE | 所有制品落到 F093 分支 |

## 待续（下一 session）

| Phase | 工时估 | 入口建议 |
|-------|--------|----------|
| 6 Implement Phase C | ~2.5h | 新建 session 后跑 `/spec-driver:spec-driver-implement` 或手工跑 tasks.md Task C-0~C-6 |
| 6 Implement Phase A | ~4-5h | 紧跟 Phase C |
| 6 Implement Phase B | ~2h | 紧跟 Phase A |
| 7 verify + Final Codex review | ~2.5h | Phase D（D-1~D-5），写 completion-report，等用户拍板 push |

## 接手说明

详见 `handoff.md`（同目录）。

## Codex review 节点（待执行）

- pre-Phase 4 (plan.md) ▶ 推迟到下一 session 实施前做（建议 Phase C 启动前）
- per-Phase C / A / B ▶ 各自 Phase commit 前
- Final cross-Phase ▶ Phase D-2

不主动 push origin/master；F093 全部完成后归总报告等用户拍板。
