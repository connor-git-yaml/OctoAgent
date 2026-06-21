# F107 → 下游 Handoff

**完成**: 2026-06-22 | **状态**: W1+W2 全完成，双评审 0 HIGH，0 regression，**未 push 等用户拍板**

---

## 1. 给 F108（Capability Layer Refactor）的输入

- **deny-list 双维护（Codex W2-L1）**：`workspace_git.py:_DENY_EXCLUDES` 与 `path_policy` 的 blacklist 独立硬编码。
  F108 收敛 capability 层时，从 path_policy 导出供 gitignore/exclude 渲染的公共常量，`workspace_git.py` 消费它，
  只保留结构性 git 专属排除（`.git/` 等）。否则未来 path_policy 新增敏感文件类型，git exclude 不会自动跟进（secrets 可能进 git 历史）。
- **W2-B 快照触发的"正确"落点**：现实现是 file-mutating 工具内写前快照（per-tool 粒度）。若 F108 给 broker
  补 ExecutionContext.project_root + loop_step（多层 threading），可把 `WorkspaceSnapshotHook` 上移到 broker BeforeHook
  做 per-loop_step 去重（更细的"一轮一快照"语义）。非必须——当前 git no-op 去重已行为等价。

## 2. 给 F120（F104 versionable 收窄）的输入

- W2 复用了 F104 的 `versionable_conn`（隔离写连接）+ `StoreGroup._versionable_write_lock`（共享写锁，
  artifact_store + behavior_version_store 共用）。F120 收窄 versionable 时注意 behavior_version_store 也挂在这条锁上。
- `behavior_versions` 表镜像 `artifact_versions`（同 record-after + baseline 范式）。F120 若改 artifact 版本存储方案，
  behavior 侧可同步演化。

## 3. 给 workspace git v0.3 / 后续的输入

- **EventStore 事件矩阵（Opus W2-M3 归档）**：当前 workspace 快照 + 回滚仅 durable 表审计（`workspace_rollback_requests`），
  无 EventStore 事件。若要 #2 完整可观测，给回滚生命周期（proposed/approved/executed/failed）+ 快照 commit emit 事件，
  范式参照 W1 的 `BEHAVIOR_VERSION_RECORDED`。注意快照高频（每次文件写），事件需采样或只记回滚。
- **post-snapshot（Codex W2-M2 归档）**：现仅写前快照，最后一次写入后无 git 记录。可在成功 mutating tool 后加
  best-effort post-snapshot，或在 history/rollback API 调用时先 flush 当前工作区。
- **ApprovalGate SSE 回滚卡**：W2-D 用 REST Two-Phase（propose/approve）。production ApprovalGate 有 durable 回调后，
  可把回滚审批统一进 ApprovalGate SSE 卡（UX 一致性 refinement）。
- **files-only 回滚分歧 surfacing（Opus L1）**：回滚后 Agent context 与文件可能不一致，可记/提示"已回滚到 \<commit\>"。
- **`/projects` 友好名**：现下拉显示归一化 slug，可经 project_store 映射友好名。
- **多 workspace 维度**：v0.2 只版本 `projects/{slug}/` 主工作树。GLOBAL behavior（`behavior/system`、`behavior/agents`）
  的版本历史走 W1 SQLite 轨，不进 workspace git。

## 4. 关键架构产出（下游可直接复用）

- **WorkspaceGitStore**（`gateway/services/workspace_git.py`）：subprocess 外部 bare store + 每 workspace
  `GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE` 重定向（用户目录无 `.git`）+ plumbing 快照（add→write-tree→commit-tree→
  update-ref CAS + per-workspace asyncio 锁）+ `_commit_in_workspace`（merge-base 守卫，**任何新 commit-taking 方法必须先过此守卫**）+
  `_safe_rel` / `is_valid_commit_hash` 注入防御 + deny-list secrets + `available` 降级探测。**单实例**经 capability_pack.workspace_git_store 暴露。
- **WorkspaceRollbackService**（`gateway/services/workspace_rollback.py`）：durable 状态机 + **CAS 占用**
  （`approve_and_execute` 用 `UPDATE ... WHERE status IN (pending,approved)` 防并发重复执行）+ rehydrate。
- **worktree 解析单一事实源**：浏览/回滚/快照三路必须用**同一** `project_root_dir(project_root, slug)`（= resolve_instance_root 内部函数）
  解析 worktree——这是 W2-E HIGH-B 的根因修复。任何新增 workspace 入口都走它，别再 `project_root / "projects" / slug` 裸拼。
- **前端 DiffBody**（`components/diff/DiffBody.tsx`）：W1 从 FilesCenter 抽出的共享 diff 渲染，W2 WorkspaceGitView 复用。

## 5. 实施经验沉淀（跨 Feature 通用）

- **双 provider 双评审实证再次有效**：spec 阶段 Codex 抓到 Opus 漏的基础设施错配；W2-E Codex+Opus **互补**抓出
  4 个真 HIGH（commit-scoping / slug-traversal / multi-project / 各自侧重），单 judge 会漏。重大架构变更必走双 provider。
- **W2-E 的两个 HIGH 都是"测试构造直接绕过了真实路径"**：单测/API 测都用已归一化的 slug + 同 workspace commit 直接构造，
  没走"工具写快照→API 读"的真实跨路径，所以原测试全绿却藏 HIGH。教训：**集成测要复现真实数据流的路径分叉**（写侧 vs 读侧不同代码算 worktree）。
- **测试运行**：`uv run --no-sync` 在并发 worktree 卡环境解析（>270s 假 hang）→ `.venv/bin/python -m pytest` + PYTHONPATH 锁 worktree（已存 memory）。

## 6. 合入建议

W1+W2 全完成，2 wave 双评审 0 HIGH，全量回归 **4014 passed / 0 failed**，前端 0 新 fail，e2e_smoke 8/8。
2 处 spec 偏离（W2-B 触发点 / W2-D 回滚审批）均 commit + completion-report 显式归档且更低风险。
**建议合入 origin/master，等用户拍板 push。**
