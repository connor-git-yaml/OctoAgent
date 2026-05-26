# F103b Tasks — 任务分解

> 上游：spec.md / plan.md
> Baseline: `def6638`
> Branch: `feature/103b-blueprint-limitations`

---

## 任务列表（按 Phase 分组）

### Phase A: core-design.md 同步（F084/F101/F102/F081）

| ID | 任务 | AC 映射 | 估时 | 前置 |
|----|------|---------|------|------|
| A-T1 | 实测：grep harness/notification/routine 在 baseline 的真实状态 | AC-D1 | 10 min | - |
| A-T2 | 读 `docs/codebase-architecture/harness-and-context.md` + `provider-direct-routing.md` 数据源 | AC-A1/A2/A3/B3 | 15 min | A-T1 |
| A-T3 | 读 CLAUDE.local.md F101 / F102 实施记录 | AC-A4 | 10 min | A-T1 |
| A-T4 | 写 §8.5.7 Harness Layer（≥ 80 行）| AC-A1, FR-A1 | 25 min | A-T2 |
| A-T5 | 写 §8.6.6 ApprovalGate WAITING_APPROVAL（≥ 40 行）| AC-A2, FR-A2 | 15 min | A-T3 |
| A-T6 | 写 §8.7.6 Context Layer USER.md SoT（≥ 60 行）| AC-A3, FR-A3 | 20 min | A-T2 |
| A-T7 | 重写 §8.9 Provider Plane（标题改 + 4 子节同步，≥ 50 行）| AC-B3, FR-A5 | 25 min | A-T2 |
| A-T8 | 写 §8.10 Notification + Routine（≥ 100 行）| AC-A4, FR-A4 | 35 min | A-T3 |
| A-T9 | git add + commit Phase A | - | 5 min | A-T4~T8 |
| A-T10 | `pytest -m e2e_smoke` 回归 | I-2 | 5 min | A-T9 |
| A-T11 | Codex review per-Phase A（foreground，主 session fallback 可用）| - | 15-30 min | A-T10 |
| A-T12 | 闭环 Codex review high/medium | - | 视情况 | A-T11 |

**Phase A 总估时**：~3 小时

### Phase B: deployment-and-ops.md 同步（F081）

| ID | 任务 | AC 映射 | 估时 | 前置 |
|----|------|---------|------|------|
| B-T1 | 实测：grep litellm / docker-compose / 4000 在 baseline 状态 | AC-D1 | 10 min | A-T12 |
| B-T2 | 写 §12.1.4 ProviderRouter 直连（≥ 40 行）| AC-B1, FR-B1 | 25 min | B-T1 |
| B-T3 | 写 §12.9.1 末尾增补 ProviderRouter | AC-B2, FR-B2 | 10 min | B-T1 |
| B-T4 | git add + commit Phase B | - | 5 min | B-T2/T3 |
| B-T5 | `pytest -m e2e_smoke` 回归 | I-2 | 5 min | B-T4 |
| B-T6 | Codex review per-Phase B | - | 10-20 min | B-T5 |
| B-T7 | 闭环 Codex review high/medium | - | 视情况 | B-T6 |

**Phase B 总估时**：~1.5 小时

### Phase C: testing-strategy.md 同步（F083/F087/F089）

| ID | 任务 | AC 映射 | 估时 | 前置 |
|----|------|---------|------|------|
| C-T1 | 实测：F089 在 baseline 状态（决定 AC-C3 详略）| AC-C3, AC-D1 | 15 min | B-T7 |
| C-T2 | 读 `docs/codebase-architecture/{e2e-testing,testing-concurrency}.md` 数据源 | AC-C1/C2 | 10 min | C-T1 |
| C-T3 | 写 §13.1.1 测试并发优化（≥ 30 行）| AC-C1, FR-C1 | 15 min | C-T2 |
| C-T4 | 写 §13.11 E2E Live Test Suite（≥ 80 行）| AC-C2, FR-C2 | 30 min | C-T2 |
| C-T5 | 写 §13.12 MCP E2E Testing（视实测）| AC-C3, FR-C3 | 10-25 min | C-T1 |
| C-T6 | git add + commit Phase C | - | 5 min | C-T3/T4/T5 |
| C-T7 | `pytest -m e2e_smoke` 回归 | I-2 | 5 min | C-T6 |
| C-T8 | Codex review per-Phase C | - | 10-20 min | C-T7 |
| C-T9 | 闭环 Codex review high/medium | - | 视情况 | C-T8 |

**Phase C 总估时**：~2 小时

### Final: review + 回归 + 归总

| ID | 任务 | AC 映射 | 估时 | 前置 |
|----|------|---------|------|------|
| F-T1 | rebase F103c 完成的 master（如已 push）| I-6, AC-D4 | 5-15 min | C-T9 |
| F-T2 | Final cross-Phase Codex review（foreground）| - | 30-60 min | F-T1 |
| F-T3 | 若 Codex 网络中断：主 session 按 spec §8 接管 review | - | 视情况 | F-T2 |
| F-T4 | 闭环 Final review high/medium | - | 视情况 | F-T2/T3 |
| F-T5 | 全量回归（`pytest -m "not slow and not e2e_live"` ≥ 3649 passed）| I-2, AC-D4 | 5-10 min | F-T4 |
| F-T6 | e2e_smoke 5x 循环（`octo e2e smoke --loop=5`）| AC-D4 | 1 min | F-T5 |
| F-T7 | 写 codex-review-final.md | - | 15 min | F-T4 |
| F-T8 | 写 completion-report.md（含 3 文件 diff 统计 + 7 Feature 修订条目对照表）| - | 30 min | F-T7 |
| F-T9 | 写 handoff.md（M6 F104 决策建议）| - | 20 min | F-T8 |
| F-T10 | commit Final docs | - | 5 min | F-T7/T8/T9 |
| F-T11 | 主 session 归总报告 + 等用户拍板 | - | - | F-T10 |

**Final 总估时**：~2-3 小时

---

## 关键路径

```
GATE_DESIGN → A-T1 → A-T2/T3 → A-T4-T8 (核心写作) → A-T9-T12 (commit + review)
            → B-T1 → B-T2/T3 → B-T4-T7
            → C-T1 → C-T2 → C-T3-T5 → C-T6-T9
            → F-T1 (rebase F103c) → F-T2/T3 → F-T4-T6 (回归)
            → F-T7-T10 (报告) → F-T11 (归总等用户拍板)
```

总估时：~9 小时（3 Phase 实施 + Final review/回归/报告）

---

## 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Codex review 网络中断（F103 已遇到）| 中 | 中 | 主 session 接管 review（F103 fallback pattern）|
| F089 baseline 状态不明 | 中 | 低 | Phase C 实测先行，AC-C3 详略动态决定 |
| §8.9 重写误删保留段 | 低 | 中 | 先读完整原文 + 显式列保留 §8.9.1 |
| F103c 同时 push 引起 rebase 冲突 | 极低 | 低 | 不交叉文件，理论无冲突 |
| 内容准确性 vs 代码现状偏差 | 中 | 中 | Final review 重点 1 严格验证 + 主 session 接管 fallback |

---

## AC 全量映射

| AC | Phase | 任务 ID |
|----|-------|---------|
| AC-A1 §8.5.7 Harness Layer | A | A-T4 |
| AC-A2 §8.6.6 ApprovalGate | A | A-T5 |
| AC-A3 §8.7.6 Context Layer | A | A-T6 |
| AC-A4 §8.10 Notification+Routine | A | A-T8 |
| AC-B1 §12.1.4 ProviderRouter | B | B-T2 |
| AC-B2 §12.9.1 octo config | B | B-T3 |
| AC-B3 §8.9 Provider Plane 重写 | A | A-T7 |
| AC-C1 §13.1.1 测试并发 | C | C-T3 |
| AC-C2 §13.11 e2e_live | C | C-T4 |
| AC-C3 §13.12 MCP E2E | C | C-T5 |
| AC-D1 引用路径精确 | A/B/C | 全部 |
| AC-D2 链接不破坏 | A/B/C | 全部 |
| AC-D3 中文输出 | A/B/C | 全部 |
| AC-D4 回归 + e2e_smoke + rebase | Final | F-T1/T5/T6 |
