# F103d OctoBench — Clarifications

**日期**: 2026-05-27
**基于**: spec.md（Draft，F103c baseline a69fe9c）

---

## Open Questions（需用户拍板）

### OQ-1：FR-B05 user simulator 用 Haiku 还是 Sonnet 4.5？

**spec 现状**：FR-B05 标注 `[AUTO-RESOLVED]`，τ-bench user simulator 使用 Haiku（成本低）而非 Sonnet 4.5，理由是"不影响被测 Agent 控变量"。

**冲突**：用户原约束"控变量 LLM = Claude Sonnet 4.5"（FR-H02）。user simulator 虽非被测对象，但它决定了 τ-bench 评分质量——如果 user simulator 用 Haiku，弱模型可能产生不合理的 user action 序列，导致 Pass@1 偏低、baseline 数据可信度下降。

**推荐**：[RECOMMENDED] **用 Haiku**——user simulator 是 τ-bench 基础设施，不是 OctoAgent 被测路径；Haiku 的 cost-perf 比在 user simulation 场景足够，且 τ-bench 原论文就是这样设计的。但请用户确认此豁免是接受的。

**选项**：

| 选项 | 描述 | 影响 |
|------|------|------|
| A（推荐） | user simulator 用 Haiku，被测 Agent 仍用 Sonnet 4.5 | τ-bench 15 task × 3 次成本显著降低；与 τ-bench 原论文设计一致 |
| B | user simulator 也用 Sonnet 4.5 | 成本约 5-10x 上升；baseline 语义更严格 |

---

### OQ-2：Phase 0 PoC 5 task 组合与用户 prompt 存在偏差

**spec 现状**：spec §0.4 写"1 Tier 1 基础工具 / 1 τ-bench airline / 1 GAIA Level 2 / 1 Tier 3 H1 / 1 并发压测用"，§6 Phase 0 写"1 Tier 1 / 1 τ-bench / 1 GAIA / 1 Tier 3 H1 / 1 并发压测"。

**偏差来源**：用户原 prompt（CLAUDE.local.md §F103d）提"2 Tier 1 + 2 Tier 2 + 1 Tier 3"，spec 子代理改成"1 Tier 1 + 2 Tier 2（τ-bench + GAIA 各 1）+ 1 Tier 3 + 1 并发压测"，实质上减少了 1 个 Tier 1 task，增加了 1 个并发压测（不属于任何 tier）。

**推荐**：[RECOMMENDED] **接受 spec 的 5 task 组合**——并发压测（验证 PoC-H3 SQLite WAL contention）是 P2 假设的核心验证手段，比第 2 个 Tier 1 task 更有风险识别价值。但用户原 prompt 与 spec 存在偏差，需确认。

**选项**：

| 选项 | 描述 | 影响 |
|------|------|------|
| A（推荐） | 保持 spec 当前组合（1 T1 + τ-bench + GAIA + 1 T3 + 并发压测） | 覆盖 PoC-H3 并发假设；adapter 可行性（τ-bench + GAIA 各 1） |
| B | 回到用户原 prompt（2 T1 + 1 τ-bench + 1 GAIA + 1 T3） | 多 1 个 Tier 1 私有能力验证，但牺牲并发压测覆盖 |

---

### OQ-3：Phase E M5 baseline 到底跑在哪个状态？

**spec 现状**：Phase E §6 说"确认 `git checkout a69fe9c` 状态下跑（**或等价**：benchmark 本身不改 production 代码，直接在当前分支跑即可）"——两个选项用"或等价"连接，但并不等价。

**问题**：F103d 完成后，当前分支比 a69fe9c **多出 `benchmarks/` 目录**（新增文件）。两种跑法产出的 baseline commit_sha 不同（a69fe9c vs. F103d 完成 commit）。M6 各 Feature 拿哪个 commit_sha 对比？

**推荐**：[RECOMMENDED] **在当前分支（F103d 完成 commit）跑 baseline**，`baseline_sha` 记录实际跑时的 commit。理由：benchmark 目录是纯观测工具，不影响 OctoAgent production 路径；用 F103d commit 作为 sha 更诚实（含 benchmark 代码本身），对 M6 对比有意义。a69fe9c 仅作 `handoff.md` 中的"F103c production 代码状态"备注。

**选项**：

| 选项 | 描述 | 影响 |
|------|------|------|
| A（推荐） | 在 F103d 完成 commit 跑，sha 记 F103d commit | 简洁；benchmark 代码不影响被测路径 |
| B | checkout a69fe9c 再跑 | sha 纯净但操作繁琐；需临时 stash benchmark 代码 |

---

## Implicit Assumptions（隐含假设，请确认）

### IA-1：HF GAIA 公开 fallback 样本真实存在

spec 提"arxiv 2311.12983 附录"有公开 Level 2 样本。实际上 arxiv 论文附录通常只有少量示例（3-5 个），且可能不含 Level 2 难度的完整答案。如果 PoC-H1（HF 访问申请）不成立，fallback 样本能否凑齐 5 个有答案的 Level 2 task 存疑。

**建议**：PoC Phase 0 同步验证 arxiv 附录内容是否足够，不要假设 fallback 已就绪。

### IA-2：`octo bench` CLI 注册到 `apps/gateway/` 不违反零侵入原则

spec FR-H01 要求"不修改 `apps/gateway/` 下任何现有文件"，但 Phase D 把 CLI 命令注册在 `apps/gateway/src/.../cli/bench_commands.py`（**新建文件**）。新建 vs. 修改现有文件的边界 spec 未明确——CLI 注册表（`__init__.py` 或 `cli_app.py`）是否也要修改？如需修改现有文件则与 FR-H01 冲突。

**建议**：Phase D 实施前确认 CLI 入口的注册方式（新建文件 OK，但注册到现有 CLI group 需改现有文件）。

### IA-3：τ-bench `pip install` 可行

spec 说"Sierra-Research τ-bench repo，pip 可安装"。τ-bench 是学术 repo，可能需要 `pip install -e git+...` 或本地 clone，不一定有 PyPI 包。PoC-H4 假设之外，安装本身可能是 blocker。

**建议**：PoC Phase 0 第一步验证 `pip install tau-bench` 或等价命令可行。

### IA-4：efficiency 基准 token 数（per task domain）来源

`ScoringRubric.efficiency_baseline_tokens` 字段在 spec 实体模型中定义，但 spec 未说明每个能力域的 baseline token 数从哪来（手工定义？第一次跑后自动校准？）。如果是手工定义，Phase A 工作量会显著增加。

**建议**：明确 baseline token 数来源。推荐第一次（M5 baseline）跑完后用 p50 作为各域基准，efficiency 维度从 M6 第一个 Feature 开始才有实际意义。

---

## Boundary Conditions（影响 spec 决策的边界）

### BC-1：INCONSISTENT 频次容忍上限

spec Edge Cases 定义了 INCONSISTENT（PASS=1 FAIL=1 有效样本中平票），但未说明允许多少 INCONSISTENT 才认为 M5 baseline 有统计意义。如果 50 task 中 20% INCONSISTENT，baseline 数据质量成问题。

**建议**：SC-001 或 SC-003 加一条"INCONSISTENT task 占比 ≤ X%（如 20%），否则报警提示用户重新评估任务设计"。用户拍板 X 的值。

### BC-2：PoC-H2 不成立时 P1 决策路径

spec AC2-3 说"任一 P0 假设不成立 → 用户决策"，但 PoC-H2（τ-bench task 数 < 15）是 P1 假设——P1 不成立时谁决策、是否也需要用户介入？spec 未明确。

**建议**：Phase 0 mid-implement GATE 应覆盖所有假设不成立情形（P0 和 P1），而非仅 P0。推荐统一：任一假设（P0/P1/P2/P3）不成立均写入 `phase-0-poc-report.md`，用户读报告时统一拍板。

---

## 已自动解决（供参考，无需用户逐一复核）

以下 `[AUTO-RESOLVED]` 项基于明确的用户历史决策，不需要额外确认：

| 项 | 自动决策 | 依据 |
|----|---------|------|
| 控变量 LLM = Sonnet 4.5, temperature=0 | 接受 | 用户在 CLAUDE.local.md §F103d 明确指定 |
| 业界 task = τ-bench + GAIA（排除 L3） | 接受 | 用户已拍板；L3 超 1h 约束 |
| Full Bench 150 task 不在 F103d | 接受 | 用户已拍板 |

---

## 执行摘要

**阶段**: 需求澄清
**产出制品**: `.specify/features/103d-octobench/clarifications.md`
**关键发现**: 识别 3 个 Open Questions（需用户拍板）+ 4 个 Implicit Assumptions + 2 个 Boundary Conditions；无项被自动写回 spec.md（OQ 级别均需用户决策）

**关键 clarify 一句话总结**：spec 最需要用户确认的 3 点——τ-bench user simulator 用 Haiku 的成本/质量取舍（OQ-1）、Phase 0 PoC task 组合偏离原 prompt（OQ-2）、M5 baseline 跑在哪个 commit 上（OQ-3）——三点均影响 baseline 数据可信度，建议用户在 PoC 启动前一次性拍板。
