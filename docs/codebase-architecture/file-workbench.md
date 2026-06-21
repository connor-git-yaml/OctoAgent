# 文件工作台（File Workbench）— 实现级架构

> 来源：F104（v0.1 artifact diff）+ F107（v0.2 git-aware）。面向非技术用户的版本历史 / diff / 恢复。

## 0. 一句话

两条独立的版本历史轨道，按"被版本化的对象"分流：

| 对象 | 底座 | 写入触发 | 恢复 | Feature |
|------|------|----------|------|---------|
| **task artifact** | SQLite `artifact_versions` | artifact 写回（连接级写隔离） | —（只读 diff） | F104 |
| **behavior 文件** | SQLite `behavior_versions`（镜像上行） | `behavior.write_file` / control_plane 写 | `behavior.restore_version`（Two-Phase） | F107 W1 |
| **workspace 文件** | **真 git**（外部 bare store） | file-mutating 工具内写前快照 | `workspace-git` 回滚（durable Two-Phase） | F107 W2 |

**hybrid 的理由（F107 CL-2）**：workspace 是大量普通文件，git 的 diff/blame/checkout 是成熟轮子 → 真 git。
behavior 文件**不进 git**——三重硬墙：①scoping（GLOBAL vs per-project 混合，git 单仓难表达）；②secrets（#5，
behavior 目录邻接 secret-bindings）；③REVIEW_REQUIRED（behavior 写本就经审批，再叠 git 冗余）。故 behavior 走轻量 SQLite。

## 1. W1：behavior 版本历史（SQLite 轨）

- **表** `behavior_versions`（`core/store/sqlite_init.py`）：`(scope, agent_slug, project_slug, file_id, version_no, content, created_at, source)`。
  `record-after + baseline`：首次写入先补一条"修改前"baseline，再记新版 → 任意两版可 diff。
- **key 派生（W1-E HIGH 修复）**：写侧 key 必须从 **resolved 磁盘路径**派生（`behavior_version_key_from_path`），
  不能用原始 profile_id——否则自定义 Worker 的 AGENT_PRIVATE 历史与前端读 key 不一致（空白）。
  `behavior/agents/<slug>/` → AGENT_PRIVATE，`behavior/system/` → GLOBAL，`projects/<slug>/behavior/` → PROJECT。
- **写锁** `StoreGroup._versionable_write_lock`：与 F104 artifact_store **共用一把** asyncio 锁（避免并发写竞态）。
- **恢复** `behavior.restore_version`（`control_plane/worker_service._handle_behavior_restore_version`）：Two-Phase
  proposal→confirm→写新版（不原地覆盖，恢复也是一次新版本）+ 失效 behavior pack 缓存（W1-E M1）。
- **事件** `BEHAVIOR_VERSION_RECORDED`（#2）。
- **前端**：`domains/agents/BehaviorVersionHistory.tsx`（时间线 + 任意两版 diff + 恢复）落 Agent 中心；
  共享 `components/diff/DiffBody.tsx`（从 FilesCenter 抽出，jsdiff diffLines）。

## 2. W2：workspace 真 git（shadow-git 轨）

仿 Hermes shadow-git。核心类 `gateway/services/workspace_git.py` `WorkspaceGitStore`。

### 2.1 外部 store + 重定向（用户目录无 `.git`）

- 外部 bare store 在 `data/` 下；每次 subprocess 调用经 `_env()` 注入
  `GIT_DIR` / `GIT_WORK_TREE`（=`projects/{slug}/`） / `GIT_INDEX_FILE`（每 workspace 独立）。
  **永不 mutate `os.environ`**（每次构造新 dict）。每 workspace 一条 ref `refs/octo/<sha256(path)[:16]>`。
- 好处：用户工作树目录下不出现 `.git`，不污染 Agent 自己可能跑的 git 命令（terminal.exec 额外 scrub `GIT_*`）。

### 2.2 plumbing 快照 + CAS

- 快照 = `add -A`（受 deny-list）→ `write-tree` → `commit-tree`（parent=旧 ref）→ `update-ref` CAS。
  CAS 失败重试（并发快照）；per-workspace `asyncio.Lock` 串行化同 workspace。无改动 → `write-tree` 同 parent → 跳过（no-op 去重）。
- **触发（F107 W2-B，spec 偏离）**：原计划 broker BeforeHook + per-loop_step，因 ExecutionContext 缺
  project_root/loop_step + 多层 threading 过度侵入 → 改在 **file-mutating 工具内写前快照**
  （`filesystem_tools.write_text` / `terminal_tools.exec`，worktree 已就地解析）。粒度 per-tool，但 git no-op 去重抵消。

### 2.3 安全护栏（双评审硬验证 holds）

- **deny-list**（`_DENY_EXCLUDES` 写进 store `info/exclude`）：`.env*` / `auth-profiles.json` / `octoagent.yaml` /
  `project.secret-bindings.json` / `behavior/` / `artifacts/` 永不进 git（#5）。（双维护债 → F108 从 path_policy 导出）。
- **commit 限定 workspace**（W2-E Codex H1）：`_commit_in_workspace`（`merge-base --is-ancestor <commit> <ref>`）
  守卫 show_files/blame/file_diff/checkout——**任何 commit-taking 方法必须先过此守卫**，防跨 workspace hash 泄露/误 checkout。
- **worktree 解析单一事实源**（W2-E HIGH-B）：浏览/回滚/快照三路用**同一** `project_root_dir(project_root, slug)`
  （= `resolve_instance_root` 内部函数，归一化 slug 消解 `../`）。禁裸拼 `project_root/"projects"/slug`。
- **注入防御**：`is_valid_commit_hash`（hex 4-64，不以 `-` 开头）+ `_safe_rel`（拒绝绝对/`..`）+ pathspec 一律 `--` 后。
- **降级**（#6）：`shutil.which("git")` 缺失 → `available=False`，所有方法返回空/None/False，绝不抛/阻塞。

### 2.4 回滚（durable Two-Phase）

- 表 `workspace_rollback_requests`（6 态）+ `WorkspaceRollbackService`（`workspace_rollback.py`）。
- `approve_and_execute`：**CAS 占用**（`UPDATE ... WHERE status IN (pending,approved)`，防并发重复执行）→
  pre-snapshot（恢复点）→ `checkout_paths`（仅文件态 SD-10）→ post-snapshot（记此次回滚为新 commit）。
- 启动 `rehydrate`（harness）：重跑 approved→executed 间 crash 的回滚（#1，幂等：同 commit checkout 是 no-op）。
- **REST Two-Phase（W2-D，spec 偏离）**：`POST /api/workspace-git/rollback`（proposal 落 durable 表）+
  `/rollback/{id}/approve`（execute）+ `/reject`，与 W1-C 同范式。production ApprovalGate 有 durable 回调后再统一进 SSE 卡。

### 2.5 浏览 API + 前端

- 路由 `routes/workspace_git.py`（front-door protected）：`/projects`（有历史的项目下拉源）+ `/history` + `/commit`
  （文件清单）+ `/blame` + `/diff`（两提交，oversize/binary 不内联，200KB+NUL 探测 W2-E M2）。
- 前端 `pages/WorkspaceGitView.tsx`：经 `/projects` 解析活跃项目（**不写死 default**，W2-E HIGH-B）下拉 → 历史 →
  改了哪些文件 → DiffBody → 谁改的(blame) → 恢复到此版本（Two-Phase 确认）。FilesCenter additive 模式切换（任务产物/工作区版本）。

## 3. 共用单实例（关键不变量）

`WorkspaceGitStore` 全局**一个实例**：`capability_pack.workspace_git_store` 构造 → harness 读它挂 `app.state` +
注入 file-mutating 工具的 `ToolDeps._workspace_git` + 构造 `WorkspaceRollbackService`。三路（工具快照 / API 浏览 / 回滚 checkout）
共用同一 `GIT_INDEX_FILE` + per-workspace 锁，避免多实例 index 竞态。

## 4. 已知 limitations（F107 双评审归档）

- workspace 快照 per-tool 粒度 + 写后无 post-snapshot（最后一次写后崩溃窗口无 git 记录；pre-rollback 快照仍是恢复点）。
- git 快照/回滚无 EventStore 事件（durable 表为审计，事件矩阵沿用 W1 推迟）。
- files-only 回滚分歧（Agent context vs 回滚后文件）未主动 surface。
- deny-list 与 path_policy 双维护（F108 收敛）；`/projects` 下拉显示 slug 非友好名。
