---
description: 推进当前里程碑一轮——更新 master、check 增量、按需调研、调整里程碑(决策回用户拍板)、并行派发下一步 Feature
argument-hint: "[可选:本轮侧重，如 '重点查 E2E' / '跳过调研直接派发' / '只 check 不调研']"
---

你是 OctoAgent 的里程碑推进助手。用户调用本命令是要把**当前里程碑**(不限定 M6，按里程碑文档实际进度)推进一轮。
`$ARGUMENTS` 是本轮可选侧重；若非空则优先遵循它覆盖默认深度。

## 0. 背景(每次必读，context 可能已压缩)
- 里程碑权威:`docs/blueprint/milestones.md`(版本管理) + `CLAUDE.local.md`(私有详细规划，gitignored)。当前进度、下个 Feature、已规划的债务/重构候选、设计哲学 H1/H2/H3 都在这两份。
- 铁律:执行可分发(子任务/worker)，**决策需集中、需用户拍板**(立哪些 Feature、排序、大改是否独立里程碑)——不要自动替用户拍板。
- 提交规矩:不 force push;commit 不加 Co-Authored-By;改里程碑后同步两份文档并提交 origin/master;worker/spawn 分支不主动 push、等用户拍板。

## 1. 先看增量(不要默认全量重跑)
- `git fetch` + ff 到最新 origin/master;`git log <上次 HEAD>..HEAD` 看落了什么(哪些 Feature 合入、动了哪些文件)。
- **先 check 刚合入的代码**:在里程碑文档把完成项标 ✅ + 记录;多个 Feature 并行合入要查交叉冲突/回归;计划外冒出的新 Feature 也补记录。

## 2. 判断要不要 workflow——按增量决定深度
workflow 很贵(每个约 100–300 万 token)。先估 ROI 再决定，**别无脑全量**:
- **竞品/SDD 调研**:vendored 竞品源码在 `_references/opensource/`(Hermes / OpenClaw / Agent Zero / Pydantic AI / Claude Code) + 业界 SDD/agent 趋势。已做过的别重复跑全量;只在有明显新角度、或距上次有重大新代码时再跑，且**聚焦增量**、别重复旧结论。每条 gap 反向验证"我们是否其实已有"，防幻觉(Perplexity 数字尤其存疑)。
- **多个 Feature 刚并行合入** → 优先跑"合并后集成 review"(交叉影响 + 全量回归 + 下个 Feature 就绪度)，而非再调研。
- 增量小 / 调研已透 → 用轻量 inline(git diff + grep + 读关键文件)代替 workflow，别为跑而跑。
- 检查三维度:①竞品机制对比 ②代码坏味道/架构债(巨型文件、双轨残渣、命名漂移、概念泄漏、死代码) ③E2E 缺口。
- workflow 产出只保留**已对抗验证**的发现;研究类防幻觉、代码类核实是否真存在于当前 master。

## 3. 调整里程碑——决策回用户拍板
- 发现汇成**人话版**:产品/用户视角讲清"为什么改 + 影响用户什么"，别堆技术细节(见 CLAUDE「向开发者要决策时产品化、别陷在细节」)。
- 大范围改动(XL / 跨多子系统 / 改协作模型) → 建议**独立里程碑**，别塞当前里程碑。
- 需用户定的岔路(立哪些 Feature、排序、拆不拆) → 用 AskUserQuestion 给清晰选项 + 推荐项，等拍板。
- 拍板后同步 `docs/blueprint/milestones.md`(权威，精简) + `CLAUDE.local.md`(详细)，commit + push origin/master。

## 4. 开下一步 Feature——能并行就发多个
- 看哪些 Feature 文件不冲突 → 各发一个 worktree spec-driver prompt 并行;冲突的串行 + 说明原因。
- 每个 prompt 自包含:背景(cold-start) + 诊断先复核(findings 可能未验证、行号会漂移) + 范围 + 验证 + 约束(Codex review;重大架构变更加第二模型多评审 panel;0 regression vs baseline;不主动 push 等拍板;产 completion-report + 走 living-docs 漂移闸)。
- worktree 建好后在其 `octoagent/` 跑 `uv sync` 重指 editable 安装到本 worktree(否则裸 pytest 跑主仓 master = **假 0 regression**)。
- pre-commit e2e 报 `aiosqlite ModuleNotFoundError` 多半是 `.venv` editable `.pth` 指向旧 worktree，主仓 `octoagent/` 重跑 `uv sync` 可治本;否则 `SKIP_E2E=1` 临时绕(仅纯文档/已手验过)。

## 5. 本轮收尾报告(给用户)
①master 这轮的变化 ②做了什么检查/调研 + 结论(含"要不要 workflow"的成本判断) ③里程碑改了什么 ④下一步发了哪些 prompt(并行/串行 + 原因)。
