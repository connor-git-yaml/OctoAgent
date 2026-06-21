# F107 文件工作台 v0.2 — 实施计划（plan）

**接** spec.md（双评审 0 HIGH 闭环）。本文件聚焦 **HOW / 文件级落点 / 阶段顺序 / 测试映射**，设计决策见 spec §3。
**Baseline**: `c031f477`（spec commit）→ 实施基线 `f3d8a267`（master）。
**切分**：单 F107，**W1（behavior 版本，先）→ W2（workspace git，后）**，各 wave 末 Codex per-Phase review（强制）。

---

## A. 架构落点总览（实测文件）

| 关注点 | 文件 | 改动 |
|--------|------|------|
| behavior_versions DDL | `packages/core/src/octoagent/core/store/sqlite_init.py` | 新增表（仿 artifact_versions:433-459）|
| BehaviorVersion model | `packages/core/src/octoagent/core/models/behavior_version.py` | 新建 |
| behavior 版本 store | `packages/core/src/octoagent/core/store/behavior_version_store.py` | 新建（复用 versionable_conn + 共用 artifact_store `_write_lock`）|
| 共用写锁 | `packages/core/src/octoagent/core/store/__init__.py`（StoreGroup）| 暴露共享 `_write_lock` 给 behavior store（FR-W1-2c）|
| 调用方 capture | `apps/gateway/.../builtin_tools/misc_tools.py:213-226`（behavior_write_file）+ `worker_service.py:560-570` | 记录新版（scope 已知，FR-W1-2/SD-7）|
| 恢复流 | misc_tools / 新 route | restore → REVIEW_REQUIRED proposal（SD-6）|
| behavior 版本 API | `apps/gateway/.../routes/behavior_versions.py` | 新建（front-door protected，仿 files.py）|
| EventType + payload | `models/enums.py` + `models/payloads.py` | 新增（FR-S-4 矩阵）|
| WorkspaceGitStore | `apps/gateway/.../services/workspace_git.py` | 新建（subprocess，外部 store，env 隔离）|
| 快照 hook | broker before-execution + `ExecutionContext`（`packages/tooling/.../models.py:283-300`）+ worker_runtime token 注入 | 扩展（SD-4，Codex C-HIGH-2v）|
| 回滚 durable 表 | `workspace_rollback_requests`（sqlite_init）+ store + startup rehydrate | 新建（#1，Codex C-HIGH-A）|
| git deny-list 同源 | 复用 `packages/tooling/.../path_policy.py:54-62` `_BLACKLIST_*` | 派生（#10，Codex C-HIGH-B）|
| terminal GIT_* scrub | `apps/gateway/.../builtin_tools/terminal_tools.py:64-70` | env scrub（Codex C-MED-D）|
| workspace git API | `apps/gateway/.../routes/workspace_git.py` | 新建 |
| DiffView 抽取 | `frontend/src/pages/FilesCenter.tsx:400-579` → `frontend/src/components/diff/` | 抽共享（FR-S-1，抽前补快照测试）|
| Agent 中心版本历史 | `frontend/src/domains/agents/*` | 新增时间线 + DiffView + 恢复按钮（W1）|
| Files Tab workspace 视图 | `frontend/src/pages/` | 新增 git 浏览 + 回滚（W2）|

---

## W1 — behavior 版本历史 + 恢复（先做，复用 F104，低风险）

### Phase W1-A：数据底座（backend）
- W1-A1 `behavior_versions` 表 DDL（`CREATE TABLE IF NOT EXISTS`，0-regression）+ 索引 `(scope,agent_slug,project_slug,file_id,version_no DESC)` + UNIQUE。
- W1-A2 `BehaviorVersion` Pydantic model（meta + content 分离，仿 ArtifactVersion）。
- W1-A3 `behavior_version_store`：`record_version(key, content)`（record-after + 首版 baseline，FR-W1-2b）/ `list_versions(key)` / `get_two_versions(key, va, vb)`（两阶段懒加载复用）/ `list_versioned_behavior_files(scope?)`。**共用 artifact_store `_write_lock`**（FR-W1-2c）+ versionable_conn + SAVEPOINT。
- W1-A4 EventType `BEHAVIOR_VERSION_RECORDED` + payload（FR-S-4）。
- **测试**：`packages/core/tests/store/test_behavior_versions.py`（表/record-after/baseline/任意两版/UNIQUE/并发 behavior∥artifact 无 "txn within txn"）。

### Phase W1-B：capture 接线（调用方，scope-aware）
- W1-B1 `misc_tools.behavior_write_file`（:213-226）+ `worker_service._handle_behavior_write_file`（:560-570）写成功后调 `record_version`（scope/agent_slug/project_slug/file_id 已知）。
- W1-B2 baseline 捕获：文件盘上有内容但无版本 → 先记盘内容再记新内容。
- W1-B3 skeleton 直写不接版本（W1 scope = materialization 之后编辑，spec FR-W1-2b）。
- **测试**：捕获接线 + baseline（`packages/core/tests/` + gateway 集成）。

### Phase W1-C：恢复流（REVIEW_REQUIRED）
- W1-C1 `restore(key, target_version)` → 读旧版 → 生成 REVIEW_REQUIRED proposal（confirmed=False，内容=旧版，SD-6）。
- W1-C2 确认 → 走现有 `commit_behavior_file_write` → 自动 record 新版（version_no=N+1）。
- W1-C3 EventType restore proposed/confirmed/rejected（FR-S-4）。
- **测试**：`apps/gateway/tests/routes/test_behavior_versions_restore.py`（恢复→proposal→确认→写入+记新版 / 拒绝 0 副作用 / 非 REVIEW_REQUIRED 文件按既有语义）。

### Phase W1-D：HTTP API + 前端（Agent 中心）
- W1-D1 `routes/behavior_versions.py`：列 versioned behavior 文件 / 列版本 / 任意两版 diff（主响应无技术字段，SC-005 范式）+ front-door protected。
- W1-D2 **DiffView 抽取**（FR-S-1）：抽前补 F104 现有 diff 快照测试（守卫）→ 抽 `buildDiffLineRows`/`DiffBody`/`DiffLineList`/`AdvancedVersionMeta` 到 `frontend/src/components/diff/`，FilesCenter 改 import（行为零变更）。
- W1-D3 Agent 中心：behavior 文件版本历史时间线 + DiffView（任意两版）+ "恢复到此版本"按钮（触发 W1-C proposal）+ Advanced 折叠。
- **测试**：`frontend/src/domains/agents/BehaviorVersionHistory.test.tsx` + DiffView 抽取后 F104 FilesCenter 回归。

### Phase W1-E：Codex per-wave review + 回归
- 全量回归 ≥ baseline 0 regression（PYTHONPATH 锁 worktree）+ e2e_smoke 8/8 + 前端 vitest。
- Codex per-Phase review → 0 HIGH → commit W1。

---

## W2 — workspace 真 git 浏览 + 回滚（后做，新底座，高风险）

### Phase W2-A：WorkspaceGitStore 底座（subprocess，degrade）
- W2-A1 启动 `shutil.which("git")` 探测缓存（缺→W2 整体降级，#6 构造性，SD-5）。
- W2-A2 外部 bare store + 每 workspace 独立 `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE=indexes/<hash16>`（**仅 per-subprocess `env=`，绝不写 os.environ**，Codex C-MED-D）。
- W2-A3 plumbing 快照 `snapshot(project_root, reason)`：`add -A`（deny-list 从 path_policy `_BLACKLIST_*` 同源 + 结构性 behavior/artifacts/secret-bindings + 大文件踢 index）→ write-tree → commit-tree → **`update-ref <ref> <new> <old>` CAS + per-project async 锁 + 重试**（Codex C-MED-E）。
- W2-A4 浏览：`log` / `show` / `blame` / 两提交 diff（async `create_subprocess_exec`）。
- W2-A5 注入防御（commit hash hex + path `.relative_to(project_worktree_root)`，FR-W2-7/Codex C-LOW-F）。
- **测试**：`apps/gateway/tests/services/test_workspace_git.py`（快照/log/blame/diff/CAS 并发/deny-list `git ls-files` 断言 secrets 0 次 SC-10）+ `test_workspace_git_degrade.py`（无 git 降级 SC-4）。

### Phase W2-B：快照触发 hook（broker，覆盖两路径）
- W2-B1 扩展 `ExecutionContext` 携 project workspace 上下文 + per-loop_step 去重 token（SD-4）。
- W2-B2 worker_runtime 每 loop_step 注入 token；broker before-execution：file-mutating（produces_write 或 `terminal.exec`）且本 step 未快照 → snapshot。
- W2-B3 `terminal_tools.py` scrub `GIT_*`（Codex C-MED-D）。
- W2-B4 EventType 快照 taken/skipped/failed（FR-S-4）。
- **测试**：覆盖自由循环 + skill pipeline 两路径触发；terminal GIT_* scrub 回归。

### Phase W2-C：回滚（durable + ApprovalGate 异步）
- W2-C1 durable `workspace_rollback_requests` 表 + store + **启动 rehydrate**（#1，Codex C-HIGH-A）。
- W2-C2 rollback 端点：创建审批（ApprovalGate SSE）+ durable 请求 → HTTP 202（不阻塞）；on-approve → pre-rollback 快照 → `git checkout <commit> -- <paths>`（失败不留半态）→ 记新 commit（失败仅 log）。
- W2-C3 仅文件态 + UI 提示 + SHOULD 注入下一轮系统提示（SD-10）。
- W2-C4 EventType rollback requested/approved/rejected/executed/failed（FR-S-4）。
- **测试**：`test_workspace_git_rollback.py`（审批→pre-snapshot→checkout→新commit / 拒绝 0 副作用 / 重启 rehydrate SC-9 / 注入防御）。

### Phase W2-D：HTTP API + 前端（Files Tab）
- W2-D1 `routes/workspace_git.py`：历史/单提交/blame/两提交 diff/回滚（front-door protected，主响应平实，git 术语 Advanced）。
- W2-D2 前端 Files Tab workspace 视图：提交历史 + DiffView + blame + "恢复到此版本"（触发审批）+ Advanced。
- **测试**：`frontend/src/pages/WorkspaceGitView.test.tsx`。

### Phase W2-E：Codex per-wave review + 回归 + 收尾
- 全量回归 0 regression + e2e_smoke + 前端 vitest。
- Codex per-Phase review → 0 HIGH → commit W2。
- completion-report + handoff + living-docs（blueprint 同步：harness-and-context / 新 workspace-git 文档）。

---

## B. 0-regression 抓手
- 默认路径全等价：behavior 不开版本记录前盘行为不变；无 versionable artifact；无 git（探测缺失走降级）。
- DiffView 抽取：抽前 F104 快照测试守卫（行为零变更）。
- PYTHONPATH 锁 worktree 全 packages/apps src（worktree venv symlink gotcha）；**禁 uv sync**。
- 每 Phase 末 focused regression；每 wave 末全量 + e2e_smoke + Codex。

## C. 风险序（先难者前置实测）
1. W2-B broker hook + ExecutionContext 扩展（plan 阶段实测精确挂点——最不确定）。
2. W2-A2 git env 隔离（os.environ 泄漏面）+ W2-A3 CAS 并发。
3. W2-C 回滚 durable + ApprovalGate 异步生命周期。
4. W1-D2 DiffView 抽取 0-regression。
