# Feature Specification: F107 文件工作台 v0.2（git-aware）

**Feature ID**: F107
**Feature Branch**: `feature/107-file-workbench-v02`
**Baseline**: `f3d8a267`（master HEAD；0 regression 基线）
**M6 阶段**: Surface 扩张第 4 件（接 F104 文件工作台 v0.1）
**上游**: F104 完成报告 + handoff（`artifact_versions` 版本底座 + Files API + DiffView）
**性质**: hybrid 双轨重构 —— 动 backend（W2 真 git 集成 + W1 SQLite 行为版本表）+ FE

---

## 0. 设计基础说明

本 spec 建立在**块 A 实测侦察 + tech-only 调研**之上（`research/tech-research.md`）。三个独立侦察 + vendored 竞品源码深读得到的事实基础（全部以代码/真实例为准，非假设）：

1. **代码库零 git**：workspace（`projects/{slug}/workspace/`）是普通目录、不是 git 仓；全仓 `packages/`+`apps/` 无 `git init` / 无 subprocess 调 `git` / 无 GitPython·dulwich·pygit2。→ workspace 的 branch/commit/blame **没有现成 git 历史可浏览，必须从零引入 git 底座**。
2. **behavior 文件覆盖写、无历史**：`behavior_workspace/write.py` 的 `commit_behavior_file_write` 用 `mkdir + write_text`，旧版本直接丢弃；写经 REVIEW_REQUIRED（`misc_tools.py:258-264`）。
3. **唯一有版本历史的是 F104 `artifact_versions`（SQLite）**，只覆盖 task 的 progress-note 产物；SnapshotStore 只是 prefix-cache（无 diff/history）。
4. **workspace 有真实写入方**：`filesystem.write_text`（capability `agent_runtime`，`produces_write=True`）+ `terminal.run`，都经 `resolve_instance_root(deps)` 根在 `projects/{slug}/`。→ W2 的 git 会从 agent 的 filesystem/terminal 工作中累积真实 commit，非空壳。
5. **behavior scoping 是 GLOBAL+per-project 混合**：`behavior/system/`（GLOBAL）、`behavior/agents/{slug}/`（GLOBAL per-agent）、`projects/{slug}/behavior/`（per-project）。

**守 H1/H2/H3**：F107 是 surface 层，不触碰主 Agent / Worker / Subagent 协作模型。
**Constitution 锚点**：#8 Observability（版本历史可见可恢复）、#6 Degrade Gracefully（git 不可用整系统不崩）、#5 Least Privilege（git 不卷入 secrets）、#4/#7 Two-Phase + User-in-Control（恢复走人审）、#1 Durability。

---

## 1. 目标（Why）

F104 让用户看到「task 产物的上一版 vs 当前版」。F107 v0.2 把"可观测版本历史"扩到用户真正在意的两条文件通路：

- **W1（behavior 版本历史 + 恢复）**：用户通过 agent 长期演化自己的 behavior 文件（USER.md=偏好、IDENTITY/SOUL=人格）。今天零历史——agent 改了 USER.md 旧版即丢。F107 让用户**看到 behavior 文件被改过什么、何时改的、并能一键恢复到某一版**（恢复走 REVIEW_REQUIRED 人审）。这是最直接的用户价值。
- **W2（workspace 真 git 浏览）**：agent 的 filesystem/terminal 工作产物在 `projects/{slug}/workspace/` 累积。F107 给 workspace 引入**真 git 底座**（外部 store，用户目录无 `.git`），让用户**浏览 workspace 文件的版本历史 / 提交 / 谁改了哪一行（blame）**。同时为 M7 的 checkpoint/rollback 打底。

两轨共享 F104 的 DiffView 组件与"主界面通俗、git 术语下沉 Advanced"的范式。

---

## 2. 范围声明

### 2.1 In Scope

**W1 — behavior 文件版本历史 + 恢复（SQLite 底座）**
- 新建 `behavior_versions` SQLite 表（镜像 F104 `artifact_versions` 模式），capture-before-overwrite 挂在 `commit_behavior_file_write`。
- 覆盖全部 3 个 behavior scope（system / agent / project）。
- 版本时间线浏览 + **任意两版本 diff**（复用 F104 DiffView）。
- **恢复到某一版本**：旧版内容灌进 REVIEW_REQUIRED proposal → 用户确认 → 走现有写入门并自动记为新版（SD-6）。
- 落点：**Agent 中心就地扩展**（behavior 文件已在 Agent 中心）。

**W2 — workspace 真 git 浏览 + 回滚（subprocess git 底座）**
- 外部 store git（Hermes 蓝本：`GIT_DIR`/`GIT_WORK_TREE` 重定向，用户目录无 `.git`）over `projects/{slug}/workspace/`。
- subprocess `git` plumbing 快照（commit-tree/update-ref，绕用户分支）。
- 快照触发：**per-turn 决策环边界去重**（CL-1）。
- **浏览**：版本历史（提交列表）/ 单提交改了哪些文件 / blame（逐行谁改）/ 任意两提交 diff。
- **回滚（CL-4）**：把 workspace 文件/目录恢复到某次提交（`git checkout <commit> -- <path>`），经 **ApprovalGate Two-Phase 审批**（#4/#7）+ 回滚前自动 pre-rollback 快照。**仅恢复文件态**（对话/事件态联动留 M7）。
- 落点：**Files Tab**（git 浏览/回滚作为 Files Tab 的 workspace 视图；原始 git 术语归 Advanced）。
- **降级（#6 构造性）**：`shutil.which("git")` 启动探测，缺则 workspace git 视图整体禁用、Files API 返回"版本历史不可用"占位，behavior 版本（SQLite）+ 主聊天/工具/写入**全部照常**。

**共享**
- 抽出 F104 DiffView 为共享组件，W1（Agent 中心）+ W2（Files Tab）+ F104 既有（Files Tab）三处复用。
- 主界面平实措辞「版本历史 / 上一版 / 恢复到此版本 / 谁改的」；commit hash / branch / blame 原始术语归 Advanced（SD-8）。

### 2.2 Out of Scope（显式排除）

- **文件态↔对话态联动回滚**：v0.2 workspace 回滚（CL-4 已纳入）**仅恢复文件态**，不联动撤销 conversation/event 状态（Hermes 式回滚文件同时撤销对话 turn）。深度联动留 M7（与 sleep-time/checkpoint 同域）。
- **行为文件纳入 workspace git**：被 SD-1 硬墙否决（GLOBAL scoping + secrets#5 + REVIEW_REQUIRED 三重冲突）。behavior 走 SQLite。
- **把整个 `~/.octoagent` 或整个 `projects/{slug}/` 上 git**：卷入 SQLite db / `.env` / `project.secret-bindings.json`，违 #5。git 只覆盖 `projects/{slug}/workspace/`（SD-3）。
- **面向 C 端的 branch 创建/切换工作流**：branch 浏览仅 Advanced 只读（多数环境只有一条线性历史）。
- **F104 deferred 项**（非本 Feature 要求）：FR-020 hash 去重、DB-locked outbox/审计 sink、大文件独立副本——继续 deferred。
- **GitPython/dulwich/pygit2**：SD-2 选 subprocess。

---

## 3. 关键决策摘要

### 3.1 用户已拍板决策（作为约束记录，2026-06-21 AskUserQuestion）

| ID | 事项 | 结论 |
|----|------|------|
| **D-1** | git-aware 核心方向 | **真 git 集成 workspace**：workspace 变真 git 仓（外部 store），UI 浏览真 branch/commit/blame。把 shadow-git 一部分提前到 v0.2（非 M7）|
| **D-2** | behavior 版本恢复能力 | **带恢复**：behavior 文件版本历史 + 恢复到某一版本，恢复走 REVIEW_REQUIRED 人审 |
| **D-3** | behavior 历史界面落点 | **Agent 中心就地扩展**（behavior 文件已在 Agent 中心）；artifacts 版本仍留 Files Tab |

### 3.2 Spec 自决（调研驱动，GATE_DESIGN 确认）

**SD-1 hybrid 底座 = workspace 真 git + behavior SQLite**（调研硬墙逼出的正解）
- workspace（大量文件，git 甜区）→ 真 git；behavior（GLOBAL+per-project 混合、受审小 md，SQLite 甜区）→ 独立 SQLite `behavior_versions`。
- 否决"行为也纳入 workspace git"：① `behavior/system/`+`behavior/agents/` 是 GLOBAL 不属任何 project workspace；② 整 `~/.octoagent` 上 git 卷入 secrets/db 违 #5；③ restore 必须过 REVIEW_REQUIRED，裸 `git checkout` 违 #4/#7。Hermes 本身就版本 workspace、排除行为/config，佐证此分界。

**SD-2 git 库 = subprocess 直调 plumbing**（Hermes/agent-zero 两先例一致，无人用 Python git 库做写路径）
- 外部 store + `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` 重定向 → 用户目录无 `.git`。plumbing-only（`write-tree`/`commit-tree`/`update-ref`）绕 HEAD/用户分支。`asyncio.create_subprocess_exec` 非阻塞。

**SD-3 workspace git 范围 = `projects/{slug}/` 工作树 − 结构性敏感/另管兄弟**（Opus review HIGH-1 修正）
- **实测纠偏**：agent 的 `filesystem.write_text`/`terminal.run` 经 `resolve_instance_root` 根在 **`projects/{slug}/` 项目根**（非 `workspace/` 子目录，`_deps.py:264-272`），path policy 放行整个 `projects/{current_slug}/` 子树（`path_policy.py:151-158`）。→ 若 git 只版本 `workspace/`，agent 默认写在项目根的文件**捕获不到**、W2 历史近乎空（与 §0 事实 4 自相矛盾）。原 SD-3「workspace/ only」**否决**。
- **修正**：git 版本 **`projects/{slug}/` 整个工作树**，**deny-list 排除**结构性敏感/另管兄弟（skeleton 创建的**已知确定集合**，allowlist 级确定性、非猜文件名）：
  - **secrets/config（#5，复用 path_policy 单一事实源 #10）**：`project.secret-bindings.json` + path_policy `_BLACKLIST_FILES`（`octoagent.yaml` / `litellm-config.yaml` / `auth-profiles.json`）+ `_BLACKLIST_FILE_PREFIXES`（`**/.env*`）——**git deny-list 从 `path_policy.py` 同源派生，不维护平行清单**（Codex HIGH-B：原清单漏 .env/auth-profiles 等）。构造性不变量 + 测试（见 §6）。
  - `behavior/`（W1 SQLite 管，不双覆盖）+ `artifacts/`（F104 管，不双覆盖）。
  - Hermes 式 infra 排除：`.venv` / `node_modules` / 媒体二进制 / >N MB 大文件踢出 index。
  - 版本范围净覆盖：`workspace/` + `notes/` + `data/`（小文件）+ 项目根级 agent 文件。
- per-project 一个 ref **+ 独立 index**：`sha256(project_root_abs)[:16]` → `refs/octo/<hash16>` + `indexes/<hash16>`（LOW-7 索引隔离，并发安全）。
- **不改 filesystem/terminal 工具根**（避免行为变更 + 0-regression 风险；re-root 至 `workspace/` 的 option b 留未来评估）。

**SD-4 commit 触发 = per-`loop_step` 快照（决策环单轮；Opus HIGH-2 + Codex 复核修正）**
- **实测纠偏**：OctoAgent 无 Hermes 式 turn loop；agent 自由循环是 `for step in range(1, max_steps)`（`worker_runtime.py:593`），每 step = 一次 LLM round-trip。但**该 for-step 层看不到 LLM 返回的 pending tool 列表**（tool call 在 `_await_backend_execute` 内才产生），且 **skill pipeline 也经工具写文件**（`packages/skills/.../runner.py`，独立路径）。原"挂 worker_runtime for-step"不足（Codex 复核）。
- **修正（挂点 = broker，覆盖所有工具执行路径）**：所有 tool call（自由循环 + skill pipeline）都汇流经 broker 执行。快照挂 **broker before-execution**，但需**扩展 `ExecutionContext` 携带 project workspace 上下文 + per-`loop_step` 去重 token**（token 由 worker_runtime 每轮 loop_step 注入）。快照单元 = `loop_step`（语义等价 CL-1"决策环边界去重"）。**精确 plumbing（ExecutionContext 扩展字段 + token 注入路径）= plan 阶段实测**。
- **触发判定（不用关键词，守 #9）**：工具执行前，若 tool 是 file-mutating（`ToolMeta.produces_write=True` **或** `terminal.exec`——声明式工具契约，非关键词猜测），且本 loop_step 未快照过（去重 token），拍一次。**`terminal.exec` 一律快照前拍**（不分类命令破坏性 → 消解 `_is_destructive_command` 关键词违 #9；git `diff-index --quiet` 无变更则不产 commit，免费）。
- commit message = 触发原因。plan 阶段：实测 broker hook + ExecutionContext 扩展 + loop_step token 注入 + 验证覆盖自由循环 & skill pipeline 两路径。

**SD-5 降级（#6 构造性保证）**
- 启动 `shutil.which("git")` 探测一次缓存。缺失/不可用 → workspace git 视图禁用（Files API 占位"版本历史暂不可用"）；**behavior 版本（SQLite）+ 主流程零影响**。git 调用全包探测+try，失败只丢该 feature 不抛主流程。

**SD-6 behavior 恢复语义 = restore-as-new-version-through-REVIEW_REQUIRED**
- 恢复 = 读旧版内容 → 当作一次 `behavior_write_file(confirmed=False)` 灌进 REVIEW_REQUIRED proposal → 用户确认 → 走现有 `commit_behavior_file_write` 写入并 capture 为最新版。**不用裸写/裸 checkout**（守 #4/#7）。"恢复"本身也产生一条新版本（历史 append-only，不改写过去）。

**SD-7 behavior 版本触发与 key**（Opus review MEDIUM-4/5 修正）
- **触发在调用方**（scope-aware）：`misc_tools.behavior_write_file` + worker handler，此处已知 scope/agent_slug/project_slug/file_id；**不挂 scope-blind 的 `commit_behavior_file_write`**（该函数只收 path+content，反推 scope 脆弱）。
- **record-after + 首版 baseline**（非 capture-before，避免"末版丢失"gap）：写成功后记录新内容为一版（latest 必被记）；若文件盘上已有内容但无任何版本 → 先记盘内容为 baseline 版再记新版（用户有 v1 可 diff）。详见 FR-W1-2b。
- 逻辑文件 key = `(scope, agent_slug, project_slug, file_id)`（覆盖 3 scope；GLOBAL scope 的 agent_slug/project_slug 取约定空值，scope 字段作 discriminator）；版本号 `(key, version_no)` 单调递增 + UNIQUE。
- **共用 artifact_store 的 `_write_lock`**（不新建独立锁——同 versionable_conn 上两把独立锁各 `BEGIN IMMEDIATE` 会"transaction within transaction"）+ versionable_conn 隔离 + SAVEPOINT 重试。

**SD-8 非技术 UX**：主界面「版本历史 / 上一版 / 恢复到此版本 / 谁改的」四件套；commit hash / branch / blame / commit message 原始术语归 Advanced/折叠区；**branch 不进主界面**。沿用 F104"主响应不含技术字段、技术字段归 Advanced endpoint"范式。

**SD-9 DiffView 组件复用**：抽出 F104 `buildDiffLineRows` / `DiffBody` / `DiffLineList` / `AdvancedVersionMeta` 为共享组件（保持 jsdiff `diffLines` + `--cp-success-soft`/`--cp-danger-soft` + 6 降级分支），W1+W2+F104 三处消费。F104 既有调用零行为变更。

**SD-10 workspace 回滚语义 = ApprovalGate（异步）+ pre-rollback 快照 + 原子 checkout + 仅文件态**（CL-4；Opus review MEDIUM-6 加固）
- 回滚 = 把 workspace 文件/目录恢复到某次提交（`git checkout <commit> -- <path>`）。不可逆 side-effect → **Two-Phase**（#4/#7）：Plan（呈现"恢复到提交 X，影响 N 文件"）→ Gate（ApprovalGate）→ Execute；批准前不覆盖任何文件。
- **异步审批生命周期（不阻塞 HTTP）**：回滚请求创建审批（SSE push 通知）→ **HTTP 立即返回 pending id（202）**，**不在 handler 内 `await wait_for_decision` 阻塞**（最长 300s 占连接/worker）；用户经现有 `POST /api/approve/{id}` 批准 → 服务端执行回滚。复用 F099/F101 ApprovalGate SSE 承载审批 UX；**不用 policy ApprovalManager**（它假定 tool/SideEffectLevel 上下文）。
- **审批请求持久化（#1 Durability，Codex HIGH-A）**：harness ApprovalGate `_pending_handles` 仅在内存（`approval_gate.py:112`）→ 进程重启丢失待批回滚。F107 **新建 durable `workspace_rollback_requests` 记录**（request_id / target_commit / paths / status `pending|approved|rejected|executed|failed` / ts）；审批解析 enqueue 幂等 executor（by request_id）；**启动 rehydrate** pending/approved-未执行的回滚请求（或标记过期）。ApprovalGate 仅承载审批 UX，durability 落 request 表（参考 F101 WAITING_APPROVAL durable 范式）。
- **执行原子性/失败处理**：① pre-rollback 快照（可撤销撤销，且是失败恢复点）→ ② `git checkout <commit> -- <paths>`（多路径尽量原子；任一失败 → 报错 + pre-rollback 快照为恢复点，不留半回滚态）→ ③ 记新 commit（失败仅 log 非致命：checkout 已落，新 commit 只是历史标记）。
- **仅恢复文件态 = 具名风险（非脚注）**：回滚后 agent 对话/事件上下文仍描述旧盘态，下次读见新态 → **静默分歧**（agent 基于陈旧记忆行动）。v0.2 缓解：UI 明确提示 + **SHOULD 向 agent 下一轮注入系统提示"用户已回滚 workspace 到版本 X"**（轻量，不做 M7 全联动）。
- 注入防御复用 FR-W2-7：commit hash hex 校验 + path 越界防护（**校验基准 = 规范变量 `project_worktree_root` = `projects/{slug}/`，非 `workspace/`**，Codex LOW-F：SD-3 改 worktree 根后须一致）。

### 3.3 GATE_DESIGN 决议固化（CL-1~CL-4，2026-06-21 硬门禁通过）

| CL | 决议 | 归属 |
|----|------|------|
| **CL-1** commit 触发粒度 | **per-turn 决策环边界去重**（Hermes 模型；挂点 = 决策环 turn 边界 + 工具写回路径）| 用户拍板（= spec 推荐）|
| **CL-2** hybrid 底座 | **workspace=git + behavior=SQLite**（SD-1 三重硬墙确认）| 用户拍板（= spec 推荐）|
| **CL-3** Feature 切分 | **单 F107，两 wave 串行：W1（behavior）先 → W2（workspace git）后**；各 wave 末双评审 | 用户拍板（= spec 推荐）|
| **CL-4** W2 范围 | **浏览 + 回滚**（非只读）：workspace 回滚纳入 v0.2，经 ApprovalGate Two-Phase 审批 + pre-rollback 快照，**仅恢复文件态**（对话态联动留 M7）| 用户拍板（**扩展** spec 原推荐的只读）|

---

## 4. User Scenarios & Testing

> AC↔test 绑定：P1 故事 AC 紧邻标注预期 test 路径（SDD 强化）。后端测试 `octoagent/packages/core/tests/store/` + `octoagent/apps/gateway/tests/routes/`；前端 `frontend/src/**/*.test.tsx`。

### User Story 1 — 查看 behavior 文件版本历史 + diff（W1，Priority: P1）

作为用户，我在 Agent 中心打开某个 behavior 文件（如 USER.md），想看到它被改过的版本历史时间线，点任两版看 git 风格 diff，清楚 agent/我改了什么。

**Why P1**：W1 的核心价值与 MVP——没有它就看不到 behavior 演变。
**Independent Test**：对某 behavior 文件经 `commit_behavior_file_write` 写入 3 个不同内容版本，Agent 中心打开该文件 → 断言时间线列出 3 版、选任两版渲染行级 diff。
**绑定 test**：`packages/core/tests/store/test_behavior_versions.py`（后端版本表）、`frontend/src/domains/agents/BehaviorVersionHistory.test.tsx`（前端时间线+diff）。

**Acceptance Scenarios**:
1. **Given** USER.md 经 3 次 behavior 写入（内容 A→B→C），**When** 用户在 Agent 中心打开其版本历史，**Then** 时间线按时间倒序列出 3 版（含时间、平实说明），主视图不暴露 hash/version_no（归 Advanced）。
2. **Given** 选中"当前版 C"与"上一版 B"，**When** 渲染 diff，**Then** git 风格行级 diff，新增/删除/未变行视觉可区分（复用 F104 DiffView）。
3. **Given** 选中任意两非相邻版本（C vs A），**When** 渲染，**Then** 正确显示这两版的 diff（任意两版本对比，非仅相邻）。
4. **Given** 两版内容相同，**When** 渲染，**Then** 明确提示"两版无差异"（FR-015 复用）。

### User Story 2 — 恢复 behavior 文件到某一版本（W1，Priority: P1）

作为用户，我误改了 USER.md，想一键"恢复到上一个好的版本"，但因为 behavior 写是受审的，恢复要经过我的确认才真正落盘。

**Why P1**：D-2 用户钦点的恢复能力，W1 的差异化价值。
**Independent Test**：对某 behavior 文件存在 ≥2 版，触发"恢复到 v1"→ 断言产生一个 REVIEW_REQUIRED proposal（confirmed=False，内容=v1）；确认后 → 断言盘上内容=v1 且版本表多出一条新版（version_no=N+1，内容=v1）。
**绑定 test**：`apps/gateway/tests/routes/test_behavior_versions_restore.py`（恢复→proposal→确认→写入→记新版全链）。

**Acceptance Scenarios**:
1. **Given** USER.md 有 v1/v2/v3，**When** 用户点"恢复到 v1"，**Then** 系统生成 REVIEW_REQUIRED proposal（不直接落盘），proposal 内容 = v1，状态待确认（SD-6，守 #4/#7）。
2. **Given** 上述 proposal，**When** 用户确认，**Then** 盘上 USER.md 内容变为 v1，且版本表 append 一条新版（version_no=4，内容=v1），历史 append-only（v1/v2/v3 不被改写）。
3. **Given** 上述 proposal，**When** 用户拒绝/取消，**Then** 盘上内容不变（仍 v3），版本表不新增。
4. **Given**（负向）某 behavior 文件 review_mode 非 REVIEW_REQUIRED（如允许直写的文件），**When** 恢复，**Then** 仍按该文件既有 review_mode 语义处理（不为恢复路径特设绕过门）。

### User Story 3 — 浏览 workspace 文件版本历史与"谁改了哪一行"（W2，Priority: P1）

作为用户，agent 在我的 workspace 里写/改了代码文件，我想看到这些文件的提交历史、每次提交改了什么、以及某文件每一行最近是哪次操作改的（blame）。

**Why P1**：W2 的核心浏览价值（D-1）。
**Independent Test**：构造一个 project，经 `filesystem.write_text` 对 workspace 文件多次写入触发多次快照 → 断言 Files Tab workspace 视图列出提交历史、单提交文件清单、blame 逐行归属、任两提交 diff。
**绑定 test**：`apps/gateway/tests/services/test_workspace_git.py`（git store 快照/log/blame/diff）、`apps/gateway/tests/routes/test_workspace_git_api.py`（API）、`frontend/src/pages/WorkspaceGitView.test.tsx`。

**Acceptance Scenarios**:
1. **Given** workspace 文件 `main.py` 被 agent 改过 3 次（触发 3 次快照），**When** 用户在 Files Tab workspace 视图打开，**Then** 显示 3 条版本历史（平实：时间 + "改了什么"摘要；commit hash 归 Advanced）。
2. **Given** 某次提交，**When** 用户展开，**Then** 列出该提交涉及的文件 + 任选文件看 git 风格 diff。
3. **Given** `main.py` 当前版，**When** 用户看"谁改的"（blame），**Then** 逐行标注最近改动它的版本/时间（git 术语 blame 归 Advanced）。
4. **Given** git 二进制存在，**When** 触发文件写，**Then** 外部 store 累积 commit，且**用户的 `projects/{slug}/workspace/` 目录里不出现 `.git`**（外部 store + 重定向，SD-2）。

### User Story 4 — git 不可用时优雅降级（W2，Priority: P1）

作为系统 Owner，即使部署环境没装 git，整个系统也必须照常运行，只是 workspace git 浏览功能不可用。

**Why P1**：Constitution #6 硬约束，构造性保证（非 best-effort）。
**Independent Test**：stub `shutil.which("git")` 返回 None，启动系统 → 断言主聊天/工具/写入/behavior 版本全部正常，workspace git API 返回"暂不可用"占位、不抛 500、不阻断启动。
**绑定 test**：`apps/gateway/tests/services/test_workspace_git_degrade.py`。

**Acceptance Scenarios**:
1. **Given** 环境无 git，**When** 系统启动，**Then** 启动成功，探测结果缓存为"git 不可用"，不抛异常。
2. **Given** git 不可用，**When** agent 写 workspace 文件，**Then** 文件正常写入（快照静默跳过），主流程无影响。
3. **Given** git 不可用，**When** 用户打开 Files Tab workspace 视图，**Then** 显示友好占位"workspace 版本历史暂不可用（需要 git）"，不报错。
4. **Given** git 不可用，**When** 用户使用 behavior 版本历史（W1），**Then** **完全正常**（SQLite 底座不依赖 git）。

### User Story 5 — 回滚 workspace 文件到某一版本（W2，Priority: P1）

作为用户，agent 在 workspace 里改坏了某个文件（或我想退回更早的好状态），我想把 workspace 文件/目录恢复到某次提交——但这是覆盖当前文件的不可逆操作，必须经我明确确认才执行。

**Why P1**：CL-4 用户拍板把 rollback 纳入 v0.2，W2 从"只读浏览"升级为"可浏览可回滚"，是 W2 的核心能力之一。
**Independent Test**：workspace 文件多次快照（c1→c2→c3），触发"回滚到 c1" → 断言生成 ApprovalGate 审批（不直接覆盖）；批准后 → 断言文件内容=c1、回滚前有 pre-rollback 快照、产生新 commit；拒绝则 workspace 不变。
**绑定 test**：`apps/gateway/tests/services/test_workspace_git_rollback.py`、`apps/gateway/tests/routes/test_workspace_git_rollback_api.py`。

**Acceptance Scenarios**:
1. **Given** workspace 有提交 c1/c2/c3，**When** 用户请求"回滚到 c1"，**Then** 系统经 ApprovalGate 生成审批（Two-Phase Plan→Gate，不直接覆盖文件，守 #4/#7），呈现"将把 workspace 恢复到 c1（影响 N 文件）"。
2. **Given** 上述审批，**When** 用户批准，**Then** 先自动拍 pre-rollback 快照（可撤销此次撤销）→ `git checkout c1 -- <paths>` → workspace 文件=c1 状态 → 产生新 commit 记录此回滚。
3. **Given** 上述审批，**When** 用户拒绝/取消，**Then** workspace 文件 0 变化。
4. **Given** 回滚后，**When** agent 下次读这些文件，**Then** 看到回滚后新内容（v0.2 **仅恢复文件态，不联动撤销对话/事件态**，§2.2）。
5. **Given**（注入防御）回滚请求带恶意 commit hash（非 hex / 以 `-` 开头）或越界 path，**When** 处理，**Then** 拒绝（FR-W2-7）。

---

### User Story 6 — 通过平实语言理解版本历史（Priority: P2）

作为非技术用户，版本历史/恢复用我能懂的话呈现，git 原始术语（hash/branch/blame/commit message）藏在 Advanced，不干扰主界面。

**Why P2**：体验增强，非功能前提（SD-8）。
**Independent Test**：渲染 W1/W2 主视图 → 断言出现"版本历史/上一版/恢复到此版本/谁改的"，断言主视图 0 处出现 commit hash/branch；展开 Advanced → 断言技术字段在此可见。

**Acceptance Scenarios**:
1. **Given** 任一版本历史主视图，**When** 渲染，**Then** 用平实措辞，无 commit hash/branch/原始 blame 术语。
2. **Given** 用户展开 Advanced，**When** 查看，**Then** 技术字段（hash 前 8 / 完整时间 / branch ref / commit message）在此可见。
3. **Given** workspace 多分支（罕见），**When** 主视图，**Then** branch 不出现在主界面（仅 Advanced 只读）。

### Edge Cases
- behavior 文件**首次写入**（无"上一版"）→ 时间线仅 1 版，diff 区提示"首版无对比"（复用 F104 首版分支）。
- workspace 文件被 `terminal.run` 改动 → step 含 `terminal.run` 即快照前拍（**不分类命令破坏性**，守 #9；git 无变更则不产 commit），SD-4。
- behavior 恢复时盘上当前内容与目标版本相同 → proposal 仍生成但用户可见"无变化"，或前端提前提示。
- workspace 超大/二进制文件 → 快照按 exclude 列表踢出 index（Hermes >N MB）；diff 走 F104 oversize/binary 降级。
- git 探测时存在但运行时损坏（git 调用 returncode≠0）→ 单次操作降级返回占位，不缓存为永久不可用（区分"缺二进制"vs"单次失败"）。
- 并发 behavior 写同一文件 → `versionable_conn` + `_write_lock` + SAVEPOINT 串行化（复用 F104 SD-2 防线）。

---

## 5. Requirements

### 5.1 Functional Requirements — W1（behavior 版本历史 + 恢复）

- **FR-W1-1**：新建 `behavior_versions` SQLite 表（version_id ULID PK / scope / agent_slug / project_slug / file_id / version_no / ts / storage_kind / content / size / hash / UNIQUE(scope,agent_slug,project_slug,file_id,version_no)）。**沿用 F104 `artifact_versions` 的存储/隔离模式（versionable_conn + SAVEPOINT）但 key 不同**（artifact=`(task_id,logical_file_id)`，behavior=4 字段 scope key——sibling 非 mirror，MEDIUM-4）。
- **FR-W1-2**：版本记录在 behavior 写**调用方**（`misc_tools.behavior_write_file` + `worker_service._handle_behavior_write_file`，scope/slugs/file_id 已知）触发，不挂 scope-blind `commit_behavior_file_write`。
- **FR-W1-2b 记录模型 = record-after + 首版 baseline**：写成功后记新内容为一版（latest 必被记，无末版丢失）；文件盘上有内容但无版本时先记盘内容为 baseline 再记新版。skeleton materialization 直写不经此路径——W1 scope = "materialization 之后的 agent/UI 编辑"，baseline 兜首次编辑前盘内容。
- **FR-W1-2c 并发隔离**：behavior_versions 写**共用 artifact_store `_write_lock`**（不新建独立锁），versionable_conn 隔离 + SAVEPOINT；补并发测试（behavior-write ∥ artifact-write 无 "transaction within transaction"）。
- **FR-W1-3**：查询方法：`list_behavior_file_versions(key)` / `get_two_versions(key, v_a, v_b)`（任意两版懒加载，复用 F104 两阶段懒加载逻辑）/ `list_versioned_behavior_files(scope filter)`。
- **FR-W1-4**：behavior 版本 HTTP API（front-door protected）：列文件 / 列版本 / 任意两版 diff。主响应不含技术字段（SC-004 范式）。
- **FR-W1-5**：恢复端点：`restore` 接受 (key, target_version) → 生成 REVIEW_REQUIRED proposal（内容=目标版，confirmed=False），**不直接落盘**（SD-6）。
- **FR-W1-6**：proposal 确认后走现有 `commit_behavior_file_write` 写入 + capture 新版（version_no=N+1）；拒绝则无副作用。
- **FR-W1-7**：前端 Agent 中心扩展：behavior 文件版本历史时间线 + 任意两版 diff（复用 DiffView）+ "恢复到此版本"按钮（触发 proposal）+ Advanced 折叠技术字段。
- **FR-W1-8**：covers 全部 3 scope（system/agent/project）；GLOBAL scope 的 key 字段按约定填充（如 project_slug=""）。

### 5.2 Functional Requirements — W2（workspace 真 git 浏览）

- **FR-W2-1**：`WorkspaceGitStore`（subprocess git）：外部 bare store（约定路径如 `~/.octoagent/.../git-store`）+ 每 workspace 独立 `GIT_DIR`(共享 store)/`GIT_WORK_TREE`(=project 工作树)/**`GIT_INDEX_FILE`=`indexes/<hash16>`（独立 index，并发隔离）** 重定向；per-workspace ref `refs/octo/<sha256(project_root_abs)[:16]>`；plumbing 快照（add→write-tree→commit-tree→update-ref，绕 HEAD）。用户目录无 `.git`。**git 重定向仅经 per-subprocess `env=` 传入，绝不写 `os.environ`**（Codex MED-D：否则用户 `terminal.exec` 的 `git` 命令会静默打到 shadow store）；`terminal.exec` 显式 scrub `GIT_*` 环境变量 + 回归测试。补并发测试（两 workspace 并行快照不互污，LOW-7）。
- **FR-W2-2**：快照触发挂 broker before-execution（per-`loop_step` 去重，SD-4）；commit message = 触发原因。**并发安全（Codex MED-E）**：同 project 并发快照用 per-project async 锁 + `git update-ref <ref> <new> <old>` CAS + 冲突重试（防两任务同 parent → 后写覆盖丢 commit）。
- **FR-W2-3**：exclude 列表（`.venv`/`node_modules`/媒体/二进制）+ >N MB 大文件从 index 踢出（Hermes 模式）。
- **FR-W2-4**：浏览查询（subprocess git）：log（提交历史）/ show（单提交文件清单 + diff）/ blame（逐行）/ 任意两提交 diff。async 经 `create_subprocess_exec`。
- **FR-W2-5**：workspace git HTTP API（front-door protected）：历史列表 / 单提交 / blame / 两提交 diff。主响应平实，git 术语归 Advanced。
- **FR-W2-6**：**降级**：启动 `shutil.which("git")` 探测缓存；不可用 → 所有 W2 API 返回"暂不可用"占位（非 500）、快照静默跳过、不阻断启动/主流程（#6 构造性）。
- **FR-W2-7**：注入防御（Hermes 模式）：commit hash 校验（hex 长度 + 不以 `-` 开头防当 flag）；file path `.resolve().relative_to(project_worktree_root)` 防穿越（**基准 = `projects/{slug}/`，非 workspace/**，Codex LOW-F）。
- **FR-W2-8**：前端 Files Tab workspace 视图：提交历史 + 单提交 diff + blame + 平实措辞 + Advanced（hash/branch/message）。
- **FR-W2-9**：回滚端点 `rollback(workspace, target_commit, paths?)`：经 ApprovalGate **异步**（创建审批 → HTTP 返回 pending id 202 不阻塞），批准前不覆盖文件；**回滚请求落 durable `workspace_rollback_requests` 表 + 启动 rehydrate**（#1，SD-10/Codex HIGH-A）；Plan 呈现影响范围（目标提交 + 受影响文件数）。复用 F099/F101 ApprovalGate SSE 承载审批 UX，不用 policy ApprovalManager。
- **FR-W2-10**：回滚执行（原子/失败）：pre-rollback 快照（恢复点）→ `git checkout <commit> -- <paths>`（失败→报错+不留半回滚态）→ 记新 commit（失败仅 log 非致命）；**仅文件态**，不联动 conversation/event（M7），缓解 UI 提示 + SHOULD 注入下一轮系统提示。前端"恢复到此版本"按钮（Files Tab workspace 视图）触发审批流。

### 5.3 Functional Requirements — 共享

- **FR-S-1**：抽出 F104 DiffView（`buildDiffLineRows`/`DiffBody`/`DiffLineList`/`AdvancedVersionMeta`，当前是 `FilesCenter.tsx` 内局部函数）为共享组件；**抽取前先确认/补 F104 现有 diff 渲染的快照测试作 0-regression 守卫**（LOW-8），抽取后 F104 既有 Files Tab 行为零变更。
- **FR-S-2**：任意两版本 diff 能力（W1 需要 + W2 两提交 diff + F104 可选升级）统一：后端 `get_two_versions` 接受显式两版本号（F104 `get_current_and_previous` 的扩展缝），前端版本选择器。
- **FR-S-3**：失败可观测：behavior 版本 append 失败 → 复用 F104 双 best-effort 信号模式（structlog + event）；workspace 快照失败 → structlog + 不阻主流程。
- **FR-S-4 EventStore 事件矩阵（#2 Everything-is-an-Event，Codex MED-C）**：新增 EventType + payload 覆盖：behavior 版本记录（`BEHAVIOR_VERSION_RECORDED`）/ behavior 恢复 proposed·confirmed·rejected / workspace 快照 taken·skipped·failed / 回滚 requested·approved·rejected·executed·failed。F104 `ARTIFACT_VERSION_APPEND_FAILED` 作范式；通用 `APPROVAL_*` 不足以审计领域 side-effect 结果。

### 5.4 Key Entities

- **BehaviorVersion**（SQLite 行 / Pydantic）：scope / agent_slug / project_slug / file_id / version_no / ts / content / size / hash / storage_kind。
- **BehaviorVersionRestoreProposal**：复用现有 behavior write proposal 机制（REVIEW_REQUIRED），内容=目标版。
- **WorkspaceCommit**（git 派生视图）：commit hash / ts / 触发原因/message / changed_files / (+X/-Y)。
- **WorkspaceBlameLine**：line_no / content / 最近改动 commit + ts。
- **WorkspaceRollbackRequest**（**durable 表 `workspace_rollback_requests`**，#1）：request_id / target_commit / paths / 影响文件数 / status（pending|approved|rejected|executed|failed）/ ts。ApprovalGate SSE 承载审批 UX；durability + 启动 rehydrate 落此表（Codex HIGH-A）。

---

## 6. Constraints / Assumptions

- **0 regression vs `f3d8a267`**：默认路径（behavior 写不开版本 / 无 versionable artifact / 无 git）100% 等价；全量回归 ≥ baseline passed；e2e_smoke 必过。
- **PYTHONPATH 锁定验证**（worktree venv symlink gotcha，见 F104 handoff §5）：裸 pytest 跑主仓 master src，验证必须 `PYTHONPATH` 锁 worktree 全 packages/apps src。**禁 uv sync**。
- **git 是可选运行时依赖**：不进 pyproject 硬依赖；Docker 基镜像装 git 是部署建议（非代码强制）；缺失走 #6 降级。
- **behavior 写预算**：USER.md 等模板有字符硬预算（1800），版本表存独立副本不受模板预算约束，但 capture 不改写入预算逻辑。
- **secrets 隔离（#5，构造性不变量 + 测试）**：git 版本 `projects/{slug}/` 工作树时，**deny-list 从 `path_policy.py` `_BLACKLIST_*` 同源派生（单一事实源 #10）+ `project.secret-bindings.json` + `behavior/` + `artifacts/`**，作**构造性不变量**（启动校验 + `git ls-files` 测试断言 `.env*`/`auth-profiles.json`/`octoagent.yaml`/`litellm-config.yaml` 及上述路径永不进 index），非 best-effort exclude（Opus MED-3 + Codex HIGH-B）。全局 SQLite db 在 `~/.octoagent/data/sqlite/` 不在 project 树内。
- **Assumption**：v0.2 workspace 多为小文本/代码（git inline 甜区）；大文件走 exclude/oversize 降级。
- **双评审 panel（动 backend + 跨平台安全面）**：每 wave Codex + 第二模型（Opus）双评审，0 HIGH 残留（CLAUDE.local.md 强制节点：DB schema 新增 + LLM 工具新增 + 重大架构）。

---

## 7. Success Criteria

- **SC-1**：behavior 文件经 ≥2 次写入后，用户在 Agent 中心 ≤2 次点击看到任两版 diff。
- **SC-2**：behavior 恢复 100% 经 REVIEW_REQUIRED（0 处裸写绕过人审）；确认后盘内容=目标版且 append 新版；拒绝 0 副作用。
- **SC-3**：workspace 文件被 agent 改动后，用户能浏览其提交历史 + blame 逐行归属；用户 workspace 目录 0 个 `.git`（外部 store 验证）。
- **SC-4**：git 不可用时，主聊天/工具/写入/behavior 版本 100% 正常，workspace git API 返回占位非 500，启动不阻断（构造性 #6）。
- **SC-5**：主版本历史视图技术字段（commit hash/branch/原始 blame）出现 0 次（归 Advanced）。
- **SC-6**：全量回归 ≥ baseline `f3d8a267` 0 regression；e2e_smoke 8/8；F104 既有 Files Tab diff 行为零变更（DiffView 抽取后）。
- **SC-7**：behavior 版本 + workspace 快照失败均不阻断主流程（降级可观测）。
- **SC-8**：workspace 回滚 100% 经 ApprovalGate（0 处裸覆盖）；批准后 workspace 文件=目标提交且回滚前有 pre-rollback 快照（可撤销撤销）；拒绝 0 副作用。
- **SC-9**（#1）：待批回滚请求 restart-durable——进程重启后 pending/approved-未执行回滚经 rehydrate 不丢（或显式标记过期），0 静默丢失。
- **SC-10**（#5 构造性）：`git ls-files`（任意 project 工作树快照）断言 `.env*` / `auth-profiles.json` / `octoagent.yaml` / `litellm-config.yaml` / `project.secret-bindings.json` / `behavior/` / `artifacts/` **出现 0 次**。

---

## 8. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 评估 |
|------|------|
| 规模 | **XL**（两套独立底座：SQLite behavior 版本 + subprocess git；+ 共享 DiffView 抽取 + 两处前端）|
| 风险点 | W2 subprocess git 集成（新依赖 + 触发挂工具写回路径 + 降级构造性 + **回滚覆盖文件经 ApprovalGate**）= 最高风险；W1 复用 F104 成熟模式 = 中低风险 |
| backend 改动 | W1 新 SQLite 表 + 查询 + 恢复端点 + capture 挂 write.py；W2 新 WorkspaceGitStore + 触发 hook + 浏览 API + **回滚 ApprovalGate Two-Phase + pre-rollback 快照** + 降级探测 |
| 0 regression 抓手 | 默认路径全等价（behavior 不开版本/无 git）；DiffView 抽取须保 F104 既有调用零变更（字节级/快照测试对账）|
| 建议切分 | **W1（behavior，先）→ W2（workspace git，后）** 两 wave 串行；各 wave 末双评审（CL-3）|
| v0.2 cut-point | **回滚（CL-4）是最可能滑的切点**（叠在 HIGH-1/HIGH-2 修正 + 异步审批生命周期 + 原子性之上）：W2 先交付浏览（含 HIGH-1/2 修正），回滚最后落、可独立推迟（LOW-9）|
| 依赖 | F104 底座（artifact_versions 模式 / versionable_conn / DiffView）；Hermes checkpoint_manager 蓝本 |

---

## 9. 待澄清事项（GATE_DESIGN 已拍板，2026-06-21）

CL-1~CL-4 全部已决（见 §3.3 决议固化表）：
- **CL-1** ✅ per-turn 决策环边界去重。
- **CL-2** ✅ hybrid（workspace=git + behavior=SQLite）。
- **CL-3** ✅ 单 F107，W1（behavior）先 → W2（workspace git）后。
- **CL-4** ✅ W2 = 浏览 + 回滚（仅文件态），回滚经 ApprovalGate。

**留 plan 阶段细化**（非阻塞 spec）：
- CL-1 触发实现细节：OctoAgent 决策环 turn 边界的精确挂点（实测决策环代码）+ destructive terminal 命令覆盖判定。
- W2 回滚的 ApprovalGate 接入路径（复用现有 ApprovalGate SSE）+ pre-rollback 快照与新 commit 的事务边界。
- behavior_versions key 中 GLOBAL scope 字段（agent_slug/project_slug）的约定空值与 UNIQUE 行为。

---

## 10. 双评审 panel 闭环（spec 阶段）

**Opus 独立对抗 spec review（2026-06-21）**：2 HIGH + 4 MEDIUM + 4 LOW，全部 spec 阶段闭环（0 HIGH 残留）。

| # | sev | finding | 闭环 |
|---|-----|---------|------|
| HIGH-1 | 高 | git scope 与实际写根矛盾：agent 写根在 `projects/{slug}/`（非 workspace/），workspace/-only scope 捕获近空 | SD-3 改版本 project 工作树 − deny-list（secret-bindings/behavior/artifacts），覆盖 agent 真实写入 |
| HIGH-2 | 高 | "per-turn" 触发挂空：OctoAgent 无 turn loop，broker hook 无 turn token | SD-4 改 per-`loop_step` 挂 worker_runtime 循环；produces_write/terminal 判定（非关键词，连带消解 LOW-10）|
| MED-3 | 中 | scope 放宽后 secrets 进 git 风险 | §6 deny-list 升构造性不变量 + 测试 |
| MED-4 | 中 | behavior key 非 artifact mirror；capture 点 scope-blind | SD-7/FR-W1-1/2 改调用方触发（scope 已知）+ 明示 sibling 非 mirror |
| MED-5 | 中 | capture-before 末版丢失 + skeleton 旁路 + 独立锁"transaction within transaction" | FR-W1-2b record-after+首版 baseline + scope 排除 skeleton + FR-W1-2c 共用 _write_lock |
| MED-6 | 中 | 回滚 HTTP 阻塞审批 + 原子性 + 仅文件态分歧 | SD-10/FR-W2-9/10 异步审批（202+回调）+ 原子/失败处理 + 仅文件态升具名风险+缓解 |
| LOW-7 | 低 | 外部 store 并发 index 污染 | FR-W2-1 独立 `GIT_INDEX_FILE`=indexes/<hash16> + 并发测试 |
| LOW-8 | 低 | DiffView 抽取 0-regression 守卫缺失 | FR-S-1 抽取前补 F104 快照测试守卫 |
| LOW-9 | 低 | 回滚是最可能滑切点 | §8 v0.2 cut-point 行显式标注 |
| LOW-10 | 低 | destructive-terminal 关键词分类违 #9 | SD-4/Edge Case 改无条件快照（git no-op 兜底）|

**Codex（GPT-5.x）跨 provider review（2026-06-21）**：复核 Opus 闭环 + 抓 2 新 HIGH + 3 新 MED + 1 新 LOW（全部代码核实后闭环；0 HIGH 残留）。证明多 provider panel 必要——Codex 抓到 Opus 漏的基础设施错配（real loop 位置 / `terminal.exec` 名 / ApprovalGate 无 callback+无 durable / EventStore 缺口）。

| # | sev | finding | 闭环 |
|---|-----|---------|------|
| C-HIGH-A | 高 | 回滚审批非 restart-durable（`_pending_handles` 仅内存 `approval_gate.py:112`）违 #1 | SD-10/FR-W2-9/KeyEntity 新建 durable `workspace_rollback_requests` 表 + 启动 rehydrate |
| C-HIGH-B | 高 | git deny-list 漏 path_policy secret/config（`.env*`/`auth-profiles.json`/`octoagent.yaml`/`litellm-config.yaml`，`path_policy.py:54-62`）| SD-3/§6 改从 path_policy `_BLACKLIST_*` 同源派生（#10）+ `git ls-files` 测试 |
| C-HIGH-2v | 高 | Opus HIGH-2 修正不足：real loop 非 worker_runtime for-step（看不到 pending tool；skill pipeline 独立路径）；tool 名是 `terminal.exec` | SD-4 改挂 broker（覆盖两路径）+ 扩展 ExecutionContext 携 project 上下文 + loop_step token；`terminal.run`→`terminal.exec` |
| C-MED-C | 中 | EventStore 矩阵缺失（#2）：无 behavior 版本/快照/回滚事件 | FR-S-4 定义完整 EventType + payload 矩阵 |
| C-MED-D | 中 | git env var 经 os.environ 泄漏 → 用户 `terminal.exec` 的 git 打到 shadow store | FR-W2-1 仅 per-subprocess `env=` + terminal scrub `GIT_*` + 测试 |
| C-MED-E | 中 | 同 project 并发快照丢 commit（无 lock/CAS）| FR-W2-2 per-project async 锁 + `update-ref` CAS + 重试 |
| C-LOW-F | 低 | 回滚 path 校验基准 `workspace` 与 SD-3 新 worktree 根不一致 | FR-W2-7/SD-10 改规范变量 `project_worktree_root`=`projects/{slug}/` |

**双评审总计**：Opus 2H+4M+4L + Codex 2H+3M+1L（其中 Codex 复核翻 Opus HIGH-2 修正为不足并真修）= **全闭环 0 HIGH 残留**。implement 阶段每 wave 末再过 Codex per-Phase review（强制节点）。

