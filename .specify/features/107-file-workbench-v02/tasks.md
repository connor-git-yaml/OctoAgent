# F107 文件工作台 v0.2 — 任务分解 + 可追溯矩阵（tasks）

**接** plan.md。本文件 = 任务清单 + FR/AC↔Task↔Test 确定性追溯（SDD 强化）。

---

## W1 任务清单（behavior 版本 + 恢复）

| Task | 描述 | FR/SC | Test |
|------|------|-------|------|
| T-W1-A1 | `behavior_versions` 表 DDL + 索引 + UNIQUE | FR-W1-1 | test_behavior_versions.py::test_schema |
| T-W1-A2 | `BehaviorVersion` model（meta/content 分离）| FR-W1-1 | test_behavior_versions.py::test_model |
| T-W1-A3 | behavior_version_store：record-after+baseline / list / get_two_versions / list_files；共用 `_write_lock` | FR-W1-2b/2c/3 | test_behavior_versions.py::{test_record_after,test_baseline,test_two_versions,test_concurrent_no_nested_txn} |
| T-W1-A4 | EventType `BEHAVIOR_VERSION_RECORDED` + payload | FR-S-4 | test_behavior_versions.py::test_event_emitted |
| T-W1-B1 | capture 接 misc_tools + worker_service（scope-aware）| FR-W1-2 | test_behavior_capture.py::test_callsite_capture |
| T-W1-B2 | baseline 捕获（盘有内容无版本）| FR-W1-2b | test_behavior_capture.py::test_first_edit_baseline |
| T-W1-B3 | skeleton 直写不接版本（断言）| FR-W1-2b | test_behavior_capture.py::test_skeleton_bypass |
| T-W1-C1 | restore → REVIEW_REQUIRED proposal | FR-W1-5/SC-2/US2-AC1 | test_behavior_versions_restore.py::test_restore_proposal |
| T-W1-C2 | 确认 → 写入 + record 新版 | FR-W1-6/US2-AC2 | test_behavior_versions_restore.py::test_confirm_writes_and_records |
| T-W1-C3 | 拒绝 0 副作用 + restore 事件 | FR-S-4/US2-AC3 | test_behavior_versions_restore.py::{test_reject_noop,test_restore_events} |
| T-W1-D1 | behavior 版本 API（列文件/列版本/任意两版 diff）front-door | FR-W1-4/US1-AC1-3 | test_behavior_versions_api.py |
| T-W1-D2 | DiffView 抽取（抽前补 F104 快照守卫）| FR-S-1/SC-6/LOW-8 | FilesCenter.test.tsx（守卫）+ diff/DiffView.test.tsx |
| T-W1-D3 | Agent 中心时间线 + DiffView + 恢复按钮 + Advanced | FR-W1-7/US1/US2/US6 | BehaviorVersionHistory.test.tsx |
| T-W1-E | Codex per-wave review + 全量回归 0 reg + e2e_smoke | SC-6 | （回归 + review）|

## W2 任务清单（workspace git 浏览 + 回滚）

| Task | 描述 | FR/SC | Test |
|------|------|-------|------|
| T-W2-A1 | git 探测 + 降级缓存 | FR-W2-6/SC-4/US4 | test_workspace_git_degrade.py |
| T-W2-A2 | 外部 store + 独立 GIT_INDEX_FILE + **per-subprocess env（不写 os.environ）** | FR-W2-1/LOW-7/MED-D | test_workspace_git.py::{test_no_dotgit_in_workspace,test_index_isolation,test_env_not_in_environ} |
| T-W2-A3 | plumbing 快照 + deny-list（path_policy 同源）+ CAS + per-project 锁 | FR-W2-1/2/3/MED-E/SC-10 | test_workspace_git.py::{test_snapshot,test_denylist_no_secrets,test_concurrent_cas} |
| T-W2-A4 | 浏览 log/show/blame/两提交 diff | FR-W2-4/US3-AC1-3 | test_workspace_git.py::{test_log,test_blame,test_diff} |
| T-W2-A5 | 注入防御（hash hex + path relative_to project_worktree_root）| FR-W2-7/LOW-F/US5-AC5 | test_workspace_git.py::test_injection_defense |
| T-W2-B1 | ExecutionContext 扩展（project 上下文 + loop_step token）| SD-4/HIGH-2v | test_snapshot_trigger.py::test_context_carries_token |
| T-W2-B2 | broker hook：file-mutating（produces_write/terminal.exec）per-step 去重；覆盖自由循环+skill pipeline | FR-W2-2/SD-4 | test_snapshot_trigger.py::{test_freeloop,test_skill_pipeline,test_dedup_per_step} |
| T-W2-B3 | terminal.exec scrub GIT_* | MED-D | test_terminal_git_scrub.py |
| T-W2-B4 | EventType 快照 taken/skipped/failed | FR-S-4 | test_workspace_git.py::test_snapshot_events |
| T-W2-C1 | durable `workspace_rollback_requests` 表 + store + 启动 rehydrate | FR-W2-9/HIGH-A/SC-9 | test_workspace_git_rollback.py::{test_durable_record,test_rehydrate_on_restart} |
| T-W2-C2 | rollback：审批异步 202 + on-approve（pre-snapshot→checkout→新commit）| FR-W2-9/10/US5-AC1-2/SC-8 | test_workspace_git_rollback.py::{test_approval_async,test_execute_atomic} |
| T-W2-C3 | 仅文件态 + UI 提示 + SHOULD 系统提示注入 | SD-10/US5-AC4 | test_workspace_git_rollback.py::test_files_only_warning |
| T-W2-C4 | EventType rollback requested/approved/rejected/executed/failed | FR-S-4 | test_workspace_git_rollback.py::test_rollback_events |
| T-W2-D1 | workspace git API（历史/提交/blame/diff/回滚）front-door | FR-W2-5/8/US3 | test_workspace_git_api.py + test_workspace_git_rollback_api.py |
| T-W2-D2 | Files Tab workspace 视图（浏览+blame+回滚+Advanced）| FR-W2-8/US3/US5/US6 | WorkspaceGitView.test.tsx |
| T-W2-E | Codex per-wave review + 全量回归 + e2e_smoke + completion-report/handoff/living-docs | SC-6 | （回归 + review + 文档）|

---

## FR 覆盖检查（orphan 检测）
- W1：FR-W1-1✓ /2✓ /2b✓ /2c✓ /3✓ /4✓ /5✓ /6✓ /7✓ /8（3 scope）→ T-W1-A3 覆盖。
- W2：FR-W2-1~10 全覆盖（见上）。
- 共享：FR-S-1✓ /2（任意两版）→ get_two_versions /3✓ /4✓（事件矩阵）。
- SC-1~10 全覆盖（SC-5 平实措辞→US6 测试；SC-7→失败注入测试）。

## AC 覆盖检查（uncovered 检测）
- US1（behavior 历史+diff）AC1-4 → T-W1-D1/D3。
- US2（behavior 恢复）AC1-4 → T-W1-C1/C2/C3。
- US3（workspace 浏览+blame）AC1-4 → T-W2-A4/D1/D2。
- US4（git 降级）AC1-4 → T-W2-A1。
- US5（workspace 回滚）AC1-5 → T-W2-C1/C2/C3/A5。
- US6（平实语言）AC1-3 → 各前端测试断言主视图无技术字段。

## 实施顺序
W1-A → W1-B → W1-C → W1-D → W1-E(review/commit) → W2-A → W2-B → W2-C → W2-D → W2-E(review/commit/docs)。
每 Phase 末 focused regression；每 wave 末全量 + e2e_smoke + Codex per-Phase review（0 HIGH 才 commit）。
