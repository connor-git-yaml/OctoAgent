# F103d OctoBench 编排追踪

> 起始 baseline: `a69fe9c` (F103c, master)
> Feature 分支: `feature/103d-octobench`
> Worktree: `.claude/worktrees/F103d-octobench`
> 编排器: spec-driver-feature 4.1.0 (fallback 模式，zod 包缺失)
> 全局 preset: `quality-first`（所有子代理用 Opus）
> 调研模式: `tech-only`（理由：用户 prompt 已锁产品决策；F087 docs 本地已有；业界 case τ-bench/GAIA 仅做技术调研）

## Phase 序列（feature 模式，10 阶段）

依据 `plugins/spec-driver/config/orchestration.yaml#modes.feature`：

| Phase | name | gate_before | gate_after | 状态 |
|-------|------|-------------|------------|------|
| 0     | constitution_check | – | – | SKIP（已有 constitution.md）|
| 0.5   | research_mode_determination | – | – | DONE（tech-only）|
| 1a    | product_research | – | – | SKIP（research_mode=tech-only）|
| 1b    | tech_research | – | – | PENDING |
| 1c    | research_synthesis | – | GATE_RESEARCH | SKIP（research_mode≠full）|
| 2     | specify | GATE_RESEARCH | – | PENDING |
| 3     | clarify + quality_checklist (并行) | – | – | PENDING |
| 3.5   | gate_design | – | GATE_DESIGN（**硬门禁**）| PENDING |
| 4     | plan | GATE_DESIGN | – | PENDING |
| 5     | tasks | – | – | PENDING |
| 5.5   | analyze | – | GATE_ANALYSIS + GATE_TASKS | PENDING |
| 6     | implement | GATE_TASKS | – | PENDING（含 Phase 0 PoC + Phase A-F；PoC 后手动 mid-stop）|
| 6.5   | verify_independent | – | – | PENDING |
| 7a+7b | spec_review + quality_review (并行) | – | – | PENDING |
| 7c    | verify | – | GATE_VERIFY | PENDING |

## 事件日志

[init] 2026-05-27 init OK. Baseline a69fe9c. Worktree feature/103d-octobench 已建。F087 文件路径校正为 `octoagent/apps/gateway/tests/e2e_live/`（多包 monorepo 根有 `octoagent/` 子目录）。本地无 tau-bench/gaia snapshot，调研阶段需 web fetch GitHub。

[Phase 1b] tech-research COMPLETED. 5 调研点（R-1 ~ R-5）全部覆盖：F087 OctoHarness 4 DI 钩子可复用 + 推荐轻量化主路径直调 / τ-bench airline ≥35 task（待 PoC `len(TASKS)` 实测）+ user simulator 4 策略 + Pass@k 评分 / GAIA L2 总 245 task + HF gated dataset 需申请 / H1/H2/H3 可观测信号已全数清晰（EventStore 落地） / Tier 4 Claude Sonnet 4.5 4000 RPM / 2M ITPM，8 并发宽裕 + retry-after backoff。
**PoC 必须优先验证 4 假设**：HF GAIA 访问 / τ-bench task 数 / 8 并发 SQLite WAL contention / GAIA L2 task 耗时 ≤ 300s.
产出: `.specify/features/103d-octobench/research/tech-research.md` (~10KB).

[Phase 1c] research_synthesis SKIPPED. research_mode=tech-only，单文件即合并制品。
[GATE_RESEARCH] AUTO_CONTINUE. behavior=auto，无失败信号。

[Phase 2] specify COMPLETED. spec.md 476 行；6 User Stories (P1×3+P2×2+P3×1) / 31 FR / 4 Entity / 10 SC / Phase 拆分 6 Phase / 复杂度 HIGH（7 组件 + 8 接口 + asyncio 并发 + SQLite）。

[Phase 3] clarify + quality_checklist COMPLETED（并行 DESIGN_PREP_GROUP）。
  - clarifications.md: 3 Open Q + 4 Implicit Assumption + 2 Boundary Condition
  - quality-checklist.md: 29 项 / 20 ✓ / 9 ⚠️ / 0 ✗ → PASS-WITH-WARNINGS
  - 2 高优先级 ⚠️：W1 零侵入新增文件 / W2 Tool Registry 隔离

[GATE_DESIGN 硬门禁] PASS（用户拍板）。
  - OQ-1: user simulator LLM = **Sonnet 4.6**（与 spec 子代理推荐 Haiku 不同）
  - OQ-2: PoC 5 task 保持 spec 当前组合（1+1+1+1+并发压测，覆盖 PoC-H3 SQLite WAL）
  - OQ-3 自动采纳: baseline 跑在 F103d 完成 commit
  - W1: 允许新增 production 文件 + 禁改现有内容
  - W2: contextmanager 临时注册 + finally 清理 + Codex pre-impl 重点 review race
  - IA-4 自动采纳: efficiency 评分推迟到 M6 启用
  - BC-1 自动采纳: SC-011 新增 INCONSISTENT ≤ 5%

spec.md 6 处 edit 已应用：§0.2 决策表（user simulator + baseline commit + efficiency 时机 + INCONSISTENT + Tool Registry）/ FR-B05 / FR-E01 / FR-H01 重写 / SC-011 新增.

[Phase 4] plan COMPLETED. plan.md 718 行。5 关键技术决策 + W3-W9 9 ⚠️ 全部解决：W3 efficiency p50 Phase E 末计算 / W4 LLM judge `[0.5, 1.0)` + max_calls=2 / W5 τ-bench actions 字段 [PoC 实测] / W6 PoC 第 5 task = POC-CONC / W7 delta 精度 0.001 / W8 4 Connor 场景 PLACEHOLDER / W9 ScoringRubric = YAML。
4 个遗留开放点（合理，留 PoC 实测确认）：τ-bench actions 路径 / CLI 注册方式 A vs B / Connor 4 场景内容 / Tool Registry API 存在性。

[Phase 5] tasks COMPLETED. tasks.md 63 task / 13.5 人时 / Phase 分布 0:9 / A:14 / B:8 / C:8 / D:11 / E:9 / F:3。35 FR 100% 覆盖。T-0-GATE 显式 STOP，blockedBy T-0-6。W3-W9 在各 Phase 落地。

[Phase 5.5] analyze COMPLETED. analysis-report.md. PASS-WITH-WARNINGS（0 CRITICAL / 2 HIGH / 5 MEDIUM / 3 LOW），FR/AC/SC 100% 覆盖。HIGH F-02（spec FR-H01 pyproject.toml 豁免）已直接 patch；F-05（spec AC2-1 措辞 + AC2-1b 新增）已直接 patch；剩 1 HIGH F-01 + 4 MEDIUM + 3 LOW 合并到 known-issues-deltas.md 作为 implement 子代理消费的补丁清单。

[GATE_ANALYSIS] AUTO_CONTINUE. behavior=on_failure，verdict=PASS-WITH-WARNINGS，无失败信号。
[GATE_TASKS] PASS（用户拍板 2026-05-28）: 可以进 Phase 0 PoC；PoC GATE 由主 session 读 phase-0-poc-report.md + AskUserQuestion 拍板。

[Phase 6 implement] STARTED 2026-05-28. PoC 子阶段：
  - implement 子代理范围限定：写 6 个 PoC python 脚本代码 + report 模板（实测数据 placeholder）+ T-0-REGRESSION
  - T-0-T1~T5 实测跑由主 session 协助用户完成（涉及 LLM API + HF + pip install 外部依赖）
  - T-0-GATE 在 PoC report 完整填好后由主 session AskUserQuestion 触发

[Phase 0 代码骨架] DONE. 7 个文件新增（poc/install_check.py 85L / poc_t1.py 164L / poc_tau.py 168L / poc_gaia.py 164L / poc_t3.py 186L / poc_concurrent.py 207L / phase-0-poc-report.md 138L 模板）。0 production 文件修改（T-0-REGRESSION PASS）。6 py_compile 全 OK。OctoHarness API 5 个实测细节：①import path OK ②__init__ 5 参数 OK ③EventStore.get_events_by_types_since(since, event_types) ④bootstrap(app) 需 FastAPI app ⑤_store_group 是私有，需 getattr fallback。

[Phase 0 实测] DONE 2026-05-28（主 session 在 Bash sandbox 自跑）。
  - install_check ✅ PASS（tau_bench + datasets 装好；uv sync 会清，需手动追加）
  - poc_tau ✅ PASS（W5 闭环：tasks.tasks list len=50；actions 字段确认 list[{name, arguments}]）→ patch TASKS→tasks 4 处
  - poc_concurrent ✅ PASS（PoC-H3 闭环：8 并发 0 lock / p95=1.303s / wall=2.78s）
  - poc_gaia ❌ FAIL（PoC-H1 不成立：gated dataset 拒绝匿名）→ 删除过时 trust_remote_code → fallback 激活
  - poc_t1 / poc_t3 ⏸ LLM_UNAVAILABLE（sandbox strip env；用户 host 跑能 PASS）
  - PoC-H4 ⏸ DEFER → Phase B（需 LLM 跑 2 连续 task 实测 mock DB reset）
  - 2 个 PoC 脚本 patch：poc_tau.py (TASKS→tasks 4 处) / poc_gaia.py (删 trust_remote_code)

[T-0-6] DONE. phase-0-poc-report.md 实测版（10 section / 187 行，覆盖 install / 5 task 耗时 / 4 假设结论 / W5 W6 闭环 / 推荐进 Phase A + 4 项激活调整）
[T-0-REGRESSION] PASS（0 production 文件变更，仅 .specify/ 新增）
[T-0-GATE] PASS 2026-05-28（用户 4 拍板）：
  - GAIA = 混合方案（用户去 HF 申请 + Phase B 同时走 fallback）
  - Connor 4 task 内容（持仓健康度 / AI 日报 / 无人机机器人日报 / 睡眠运动分析），统一 domain=connor_real_world，已记入 known-issues-deltas.md F-08
  - Push 策略 = 当前 commit + push origin/feature/103d-octobench（开 PR 合 master）
  - 进 Phase A
