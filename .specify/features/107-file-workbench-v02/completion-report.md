# F107 文件工作台 v0.2（git-aware）— Completion Report

**Feature**: F107 | **Baseline**: `f3d8a267` | **完成**: 2026-06-22
**模式**: feature（完整编排，hybrid 双轨）| **范围**: XL

---

## 1. 一句话总结

文件工作台从 v0.1（artifact diff 只读）升级为 v0.2 git-aware：**W1** 给 behavior 文件加版本历史 +
任意两版 diff + Two-Phase 恢复；**W2** 给 workspace 引入**真 git**（外部 store + plumbing 快照）实现
浏览历史 / 改了哪些文件 / 谁改的（blame）/ 恢复到此版本。面向非技术用户的平实 UX，git 术语下沉。

**hybrid 决策（CL-2 用户拍板）**：workspace=真 git（subprocess 外部 store）/ behavior=SQLite
（`behavior_versions`，镜像 F104 `artifact_versions`）。行为文件**不纳入 git**——scoping + secrets#5 +
REVIEW_REQUIRED 三重硬墙否决。

## 2. Phase 实际 vs 计划

| Phase | 计划 | 实际 | 偏离 |
|-------|------|------|------|
| Phase 0 + 块 A 侦察 | codebase 实测 | 3 并行 Explore + 主核实：**代码库零 git** / workspace 有真实写入方 | — |
| 决策点 1 + Phase 1b | 方向 + tech-only 调研 | 用户选真 git+恢复+Agent 中心；Hermes shadow-git 蓝本 | — |
| specify + GATE_DESIGN | spec + 4 CL | spec 固化 + 4 CL 拍板（含 CL-4 扩到回滚） | — |
| spec 双评审 | 单 Codex | **Opus + Codex 双 provider**（重大架构） | Codex 抓 Opus 漏的基础设施错配，证双 provider 必要 |
| **W1-A~C** | behavior 版本底座 + capture + 恢复 | 表 + store（record-after+baseline）+ 双调用方 capture + Two-Phase restore | — |
| **W1-D** | 读 API + 前端 | 3 读 API + DiffView 抽取 + BehaviorVersionHistory + Agent 中心入口 | — |
| **W1-E** | 双评审 | Opus 1H+1M+3L / Codex 4M → H1（path 派生 key）修复 | — |
| **W2-A** | WorkspaceGitStore | subprocess 外部 store + 重定向 + plumbing + CAS + deny-list + 注入防御 | — |
| **W2-B** | broker BeforeHook + per-loop_step 触发 | **改 file-mutating 工具内写前快照** | ⚠️ **偏离**：ExecutionContext 缺 project_root/loop_step + 多层 threading 过度侵入；改 per-tool 粒度（git no-op 去重抵消） |
| **W2-C** | durable 回滚状态机 | `workspace_rollback_requests` + pre→checkout→post + rehydrate | — |
| **W2-D** | API + 前端，回滚经 ApprovalGate SSE | route + harness 接线 + WorkspaceGitView + **REST Two-Phase 回滚** | ⚠️ **偏离**：SD-10 原 ApprovalGate SSE 卡 → 显式 REST Two-Phase（propose/approve），与 W1-C 同范式 |
| **W2-E** | 双评审 + 收尾 | Codex 2H+2M+1L / Opus 1H+3M+3L → 2 HIGH + CAS + oversize + 多项目修复 | — |

### 2 处 spec 偏离的合理性（已在 commit message + 本报告显式归档）

- **W2-B（触发点）**：原 plan 标注"plan-stage 实测"——实测 broker BeforeHook 拿不到 worktree（ExecutionContext
  无 project_root），per-loop_step 去重需贯穿 worker_runtime→llm_service→SkillExecutionContext→runner 四层加字段。
  改为在**已解析 worktree 的 file-mutating 工具**（filesystem.write_text / terminal.exec）内写前快照。代价 = 粒度变
  per-tool（非 per-loop_step），但 **git 对无改动快照本就 no-op 去重**，行为等价。是务实且更低风险的落点。
- **W2-D（回滚审批）**：SD-10 原案 ApprovalGate SSE 审批卡。实测 production ApprovalGate 无 durable 回调
  （Codex spec-review 已抓 C-HIGH-A）。改 **REST Two-Phase**（POST /rollback=proposal 落 durable 表 / POST
  /rollback/{id}/approve=execute / reject），与 W1-C behavior 恢复**同范式**。durable 请求 + 显式 approve 端点
  同样满足 Two-Phase（#4/#7）+ #1 durability；ApprovalGate SSE 统一卡为后续 UX refinement。

## 3. 双评审闭环

### W1-E（`7799fe2f`）
| # | 严重度 | 来源 | 处理 |
|---|--------|------|------|
| H1 | HIGH | Opus | AGENT_PRIVATE 历史对自定义 Worker 空白 → `behavior_version_key_from_path` 从 resolved 路径派生写 key（与前端读 key 逐字一致）。**修复** |
| M1 | MED | Opus | 恢复后失效 behavior pack 缓存。**修复** |
| L2 | LOW | Codex | 跳过重复 baseline。**修复** |
| 其余 | MED/LOW | Codex | 并发同文件写竞态 / control_plane+restore 无事件 / 降级冗余 → **归档** |

### W2-E（`eff0b1cd`）— Codex 2H+2M+1L / Opus 1H+3M+3L
| # | 严重度 | 来源 | 处理 |
|---|--------|------|------|
| H1 | HIGH | Codex | commit 未限定 workspace（跨 workspace hash 泄露/误 checkout）→ `_commit_in_workspace`（merge-base --is-ancestor）守卫 4 方法。**修复** |
| H2 | HIGH | Codex + Opus | 浏览/回滚 worktree 与工具写快照解析不一致 + slug path traversal → API `_worktree` 用 `project_root_dir`（同归一化消解 `../`）+ 前端经 `/projects` 下拉不写死 default。**修复** |
| M1 | MED | Codex | 并发 approve 重复执行 → CAS 原子占用。**修复** |
| M2 | MED | Opus | diff oversize/binary 撑爆响应 → 200KB 上限 + NUL 探测。**修复** |
| M2(C) | MED | Codex | 写后状态未快照（snapshot-before 语义）→ **归档**（pre-snapshot 已是回滚恢复点；post-snapshot 留 refinement） |
| M3+L1 | MED+LOW | Opus | git 快照/回滚无 EventStore 事件（#2）+ files-only 回滚分歧未 surface → **归档**（durable 表为审计，沿用 W1 事件矩阵推迟） |
| L1(C) | LOW | Codex | deny-list 与 path_policy 双维护 → **归档**（F108 顺手收敛） |

**已清（双评审实测确认 holds，非问题）**：deny-list secrets 排除（#5，Opus 真 `git add -A` 复现）/
单 store 实例（startup `_bootstrapped` 守卫）/ 降级每路径（#6）/ env 隔离（不 mutate os.environ）/
path 注入（`_safe_rel` + hash hex + `--` 后 pathspec）/ rehydrate 幂等 / checkout 原子。

**0 HIGH 残留**。

## 4. 测试 & 回归

- **W1**：~27 后端测 + 4 组件测；后端全量 3983 passed 0 failed（vs ~3899 baseline）。
- **W2**：27 后端测（git 10+commit-scoping / rollback 6+CAS / snapshot 3 / API 5+projects）+ 4 组件测。
- **最终全量后端回归**（W2-E 后）：**4014 passed / 0 failed / 2 skipped**（vs W2-D 4011，+3=新 W2-E 测）。0 regression。
- **前端**：174 passed（11 failed = master F104 既有债，本 Feature 0 新增 fail）+ tsc clean。
- **ruff**：新文件全 clean；既有大文件（capability_pack/octo_harness）新增 0 真 E501。
- **e2e_smoke**：每 commit 8/8（pre-commit hook）。

## 5. 已知 limitations（living-docs gate）

1. **workspace 快照粒度 per-tool**（非 per-loop_step）+ **写后无 post-snapshot**：最后一次写入后、下次写入前的
   崩溃窗口无 git 记录（Codex W2-M2 归档）。pre-rollback 快照仍捕获未提交改动作恢复点。
2. **git 快照/回滚无 EventStore 事件**（#2，Opus M3 归档）：durable `workspace_rollback_requests` 表 +
   `BEHAVIOR_VERSION_RECORDED`（W1）为审计；workspace 快照侧事件矩阵沿用 W1 推迟。
3. **files-only 回滚分歧未 surface**（Opus L1）：回滚仅文件态（SD-10），Agent 下一轮 context 与回滚后文件可能不一致，
   未主动提示。单用户 + pre-snapshot 可恢复，风险低。
4. **deny-list 与 path_policy 双维护**（Codex L1）：`_DENY_EXCLUDES` 独立硬编码，未来 path_policy 增敏感类型不自动跟进 →
   F108 顺手从 path_policy 导出公共常量。
5. **`/projects` 下拉显示 slug 非友好名**：v0.2 用归一化目录名（人类可读够用）；project_store 友好名映射留 refinement。
6. **W1 deferred**：并发同文件写竞态 / control_plane+restore 事件覆盖（data durable）。

## 6. Living-docs drift

- **新增** `docs/codebase-architecture/file-workbench.md`（或并入既有）：workspace git substrate + behavior 版本双轨。
- **blueprint.md / milestones.md**：F107 标完成 + M6 第 4 件收口。
- 详见 commit `<living-docs>`。

## 7. 主要文件清单

**W1**：`core/models/behavior_version.py` / `core/store/{sqlite_init,behavior_version_store,__init__}.py` /
`core/behavior_workspace/paths.py` / `gateway/services/behavior_versioning.py` /
`gateway/services/builtin_tools/misc_tools.py` / `gateway/services/control_plane/worker_service.py` /
`gateway/routes/behavior_versions.py` / 前端 `components/diff/DiffBody.tsx` + `domains/agents/{BehaviorVersionHistory,AgentEditorSection}.tsx`。

**W2**：`gateway/services/workspace_git.py` / `gateway/services/workspace_rollback.py` /
`core/models/workspace_git.py` / `gateway/routes/workspace_git.py` /
`gateway/services/builtin_tools/{_deps,filesystem_tools,terminal_tools}.py` /
`gateway/services/capability_pack.py` / `gateway/harness/octo_harness.py` / `gateway/main.py` /
前端 `pages/WorkspaceGitView.tsx` + `pages/FilesCenter.tsx`（additive 模式切换）+ `api/client.ts`。

## 8. 建议

**建议合入 origin/master**：W1+W2 全完成，2 wave 双评审 0 HIGH 残留，0 regression，e2e_smoke 8/8。
2 处 spec 偏离均有 commit + 本报告显式归档且更低风险。等用户拍板 push。
