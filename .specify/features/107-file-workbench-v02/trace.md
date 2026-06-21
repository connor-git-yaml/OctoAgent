# F107 文件工作台 v0.2（git-aware）— Spec Driver Trace

**Feature**: F107 | **Branch**: `feature/107-file-workbench-v02` | **Baseline**: `f3d8a267`
**模式**: feature（完整编排）| **研究模式**: tech-only（块 A codebase 侦察主 session 主导）

---

## Phase 0 — 初始化 + 块 A 实测侦察
- 创建 worktree `feature/107-file-workbench-v02` off `origin/master` (f3d8a267)。
- init-project：constitution/config/gate_policy 就位；无既有 F107 制品；F104（104-file-workbench）作参考。
- **块 A 侦察（3 并行 Explore + 主 session 核实）**核心结论：
  - **代码库零 git**（workspace 普通目录 / 无 GitPython·dulwich·subprocess git）→ workspace 无现成 git 历史，需从零引入。
  - behavior 文件覆盖写无历史（`behavior_workspace/write.py`）；SnapshotStore 仅 prefix-cache 无 history。
  - 唯一版本历史 = F104 `artifact_versions`（SQLite，task-scoped）。
  - workspace 有真实写入方：`filesystem.write_text` + `terminal.run` 根在 `projects/{slug}/`（W2 非空壳）。
  - behavior scoping = GLOBAL(`behavior/system`,`behavior/agents`) + per-project(`projects/{slug}/behavior`) 混合。

## 决策点 1（AskUserQuestion，2026-06-21）— F107 核心方向
- 主 session 把"workspace 无 git 历史 vs 范围要求 branch/commit/blame"的冲突产品化呈现。
- **用户拍板**：D-1 真 git 集成 workspace / D-2 behavior 版本带恢复 / D-3 behavior 历史落 Agent 中心。
- → F107 升级为 XL hybrid 双轨（workspace 真 git + behavior 版本恢复）。

## Phase 1b — tech-only 调研
- 1 general-purpose agent 深读 Hermes（用户钦点）+ agent-zero/竞品 vendored 源码 + Python git 库选型 + 非技术 UX。
- 产出 `research/tech-research.md`。核心：
  - Hermes shadow-git = 真 git + 外部 store + GIT_DIR 重定向（用户目录无 .git）+ plumbing + per-turn 去重 + checkout 恢复；**只版本 workspace、排除 behavior/config**。
  - git 库 → **subprocess 直调**（Hermes/agent-zero 两先例，无人用 Python git 库做写路径）。
  - 行为底座 → **独立 SQLite**（hybrid；scoping + secrets#5 + REVIEW_REQUIRED 三重硬墙否决"行为纳入 git"）。
  - UX → 主界面"版本历史/上一版/恢复到此版本/谁改的"，git 术语下沉 Advanced。

## Phase specify — spec 起草
- 主 session 主导（决策中心化），匹配 F104 house style。
- 产出 `spec.md`：scope（W1 behavior + W2 workspace git，hybrid）+ 决策（D-1~3 用户 / SD-1~9 自决）+ 5 User Story（含 AC↔test 绑定）+ FR(W1/W2/共享) + SC-1~7 + 复杂度 XL + CL-1~4 决策点。

## GATE_DESIGN（硬门禁）— ✅ 通过（2026-06-21）
- 呈现 spec 摘要 + 4 CL 决策点。**用户拍板**：
  - CL-1 ✅ per-turn 决策环边界去重。
  - CL-2 ✅ hybrid（workspace=git + behavior=SQLite）。
  - CL-3 ✅ 单 F107，W1 先 → W2 后。
  - CL-4 ✅ **浏览 + 回滚**（扩展 spec 原推荐只读）：回滚经 ApprovalGate + pre-rollback 快照 + 仅文件态。
- spec 已固化 4 CL 决议（§3.3）+ 新增 SD-10 回滚语义 + US5 回滚故事 + FR-W2-9/10 + SC-8。

## 双评审 panel（spec 大改 commit 前，强制节点）— ✅ 闭环 0 HIGH
- **Opus 独立对抗 review**：2 HIGH（git scope↔写根矛盾 / per-turn 触发挂空）+ 4 MED + 4 LOW，全闭环（spec §10 表）。
- **Codex（GPT-5.x）跨 provider review**：复核 Opus 闭环 + 抓 2 新 HIGH（回滚审批非 durable 违 #1 / git deny-list 漏 path_policy secrets）+ 翻 Opus HIGH-2 修正为不足（real loop 非 worker_runtime；tool 名 `terminal.exec`；挂 broker）+ 3 新 MED（EventStore 矩阵 / git env 泄漏 / 并发快照 CAS）+ 1 LOW。全部代码核实后闭环。
- **多 provider panel 实证价值**：Codex 抓到 Opus 漏的基础设施错配（ApprovalGate 无 callback+无 durable / skills/runner.py / terminal.exec / path_policy blacklist）——证明重大架构变更必须双 provider。
- **总计** Opus 2H+4M+4L + Codex 2H+3M+1L = 0 HIGH 残留。spec 全部固化闭环。

## spec commit — 进行中
- 本地 commit spec（不 push，等用户拍板，CLAUDE.local.md §Spawned Task）。
- 下一步：plan + tasks → implement W1（behavior 版本，复用 F104 低风险先落）→ 每 wave 末 Codex per-Phase review → W2（workspace git）→ verify → completion-report/handoff/living-docs。
