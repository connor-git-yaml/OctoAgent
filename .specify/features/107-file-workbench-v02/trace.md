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

## spec/plan/tasks commit — ✅
- spec `c031f477` + plan/tasks `32e6f61f`（本地，不 push 等拍板）。

## W1 实施（behavior 版本历史 + 恢复）— 后端 ✅ / 前端待续
- **W1-A** `f52185b2`：behavior_versions 表 + store（record-after + 首版 baseline）+ 共享写锁（Codex MED-5）。10 测试 + 59 store 回归 0 reg。
- **W1-B** `a84f8c00`：capture 接两调用方（misc_tools + control_plane，scope-aware key MED-4）+ BEHAVIOR_VERSION_RECORDED 事件（#2）。8 测试 + 101 behavior 回归 0 reg。
- **W1-C** `62b400fb`：behavior.restore_version action（Two-Phase proposal→confirm→record 新版，SD-6 守 #4/#7）。4 测试 + 143 control_plane 回归 0 reg。
- **W1-D 后端** `3288490a`：3 读 API（files/versions/diff，front-door protected，主响应 0 技术字段 SC-004，任意两版 FR-S-2）。5 API 测 + ruff clean。
- **W1 后端累计**：~27 新测试，0 regression，ruff clean，e2e_smoke 8/8（每 commit）。

### 实施关键经验沉淀
- **测试运行**：`uv run --no-sync` 在并发 worktree 环境会卡环境解析（实测 >270s 假 hang）→ 改 **`.venv/bin/python -m pytest` + PYTHONPATH 锁 worktree**（0.3s 真跑）。
- **stale 进程污染**：机器有 F098/F096 等老 worktree 的僵死 pytest/uv 进程，加剧 uv 解析卡顿。

## W1 前端 + W1-E — ✅ 完成
- **W1-D 前端** `2e012471`：DiffView 抽取（FR-S-1，FilesCenter 15 守卫测试全过=零变更）+ api/client behavior 版本函数 + BehaviorVersionHistory.tsx（时间线+任意两版 diff+恢复 Two-Phase）+ AgentEditorSection additive 入口。4 新组件测 + tsc clean + 0 新前端 regression（11 failed=master 既有债）。
- **W1-E 双评审** `7799fe2f`：Opus 1H+1M+3L / Codex 4M。
  - **H1（HIGH）修复**：AGENT_PRIVATE 历史对自定义 Worker 空白 → behavior_version_key_from_path 从实际 resolved 路径派生写 key，与前端读 key 逐字一致。
  - M1（MED）：恢复后失效 behavior pack 缓存。L2（LOW）：跳过重复 baseline。
  - deferred 已知限制：Codex MED#2 并发同文件写竞态 / MED#7 control_plane+restore 无事件（data durable）/ L1 Two-Phase 前端自带确认 / L3 降级分支冗余。
- **全量后端回归 3983 passed 0 failed**（vs ~3899 baseline）。**W1 全完成（后端+前端+UI+双评审 0 HIGH）。**

## W2 实施（workspace 真 git 浏览 + 回滚）— 进行中
- 最大最高风险 wave：subprocess 外部 store git + per-loop_step 快照挂 broker + durable 回滚 + 降级 + CAS。
- W2-A 底座 → W2-B 触发 hook → W2-C 回滚 → W2-D API+前端 → W2-E review。
