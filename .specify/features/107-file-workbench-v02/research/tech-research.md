# F107 文件工作台 v0.2（git-aware）— 技术调研

**调研模式**：tech-only（块 A codebase 侦察由主 session 主导，本文件汇总 vendored 竞品源码深读 + Python git 库选型 + 非技术 UX）
**日期**：2026-06-21
**Baseline**：`f3d8a267`（master HEAD）

---

## 1. Hermes shadow-git（用户钦点设计输入）— workspace 真 git 蓝本

源码：`_references/opensource/hermes-agent/tools/checkpoint_manager.py`（1643 行，已实测精读）。

- **真 git，非自研**：`subprocess.run(["git", ...])`，但用**外部 store**（`~/.hermes/checkpoints/store/`，`git init --bare`）+ `GIT_DIR` / `GIT_WORK_TREE` / `GIT_INDEX_FILE` 环境变量重定向 → **用户项目目录里没有任何 `.git`**（`_git_env`，行 238-272）。
- **per-project 隔离**：`sha256(abs_path)[:16]` → `refs/hermes/<hash16>` 分支 tip + `indexes/<hash16>` 独立 index + `projects/<hash16>.json` 元数据。单一共享 store 靠 git 内容寻址跨项目/turn 去重（v2，避免 12 worktree 烧 500MB）。
- **快照时机 = 每 conversation turn 最多一次，在 file-mutating 工具执行前**（`tool_executor.py:392-414` 调 `ensure_checkpoint(work_dir, "before write_file")`；`conversation_loop.py:565` 每轮 `new_turn()` 清去重集；同目录同 turn 只快照一次）。commit message = 触发原因。
- **快照机制（plumbing-only）**：`git add -A`（per-project index）→ 删 >10MB 大文件 → `git diff-index --cached --quiet`（无变更跳过）→ `write-tree` → `commit-tree`（parent=上个 ref tip）→ `update-ref refs/hermes/<hash>`。**绕开 HEAD/分支**。
- **rollback**：`git checkout <hash> -- .`（整目录）或 `-- <file>`（单文件）；restore 前自动拍 pre-rollback 快照（可撤销撤销）；hash/path 注入防御（hash 4-64 hex 不以 `-` 开头；path `.resolve().relative_to(workdir)`）。
- **只版本 workspace/项目代码，显式排除行为/config 文件**（`DEFAULT_EXCLUDES`：`.git`/`.env`/`.venv`/`node_modules`/媒体/二进制）。**Hermes 不给行为文件做版本历史。**
- **降级**：`shutil.which("git")` 缺则静默禁用，主流程继续；默认 `enabled=False`（opt-in）。
- **UX**：`/rollback` 列 checkpoints（📸 + 编号 + 短 hash + 时间 + 原因 + `(N files,+X/-Y)`）；文件回滚同时撤销最后一个 chat turn（文件态↔对话态联动）。

## 2. 其它 vendored 竞品

- **agent-zero `_time_travel`**：唯一另一个真 git 先例。subprocess `commit-tree`/`update-ref` plumbing（同 Hermes，绕用户分支），但触发 **per-write + per-exec**（比 Hermes 细）+ 10s 去抖。GitPython 仅读元数据。
- **claude-code**：git worktree 隔离每 session，但**不自动 commit**，commit 留用户手动 `/commit`。
- **openclaw / pydantic-ai / memU**：无 workspace 文件版本化。
- **横向结论**：做"agent 文件真 git 版本化"的只有 Hermes + agent-zero，**两家都 subprocess plumbing，无人用 GitPython/dulwich/pygit2 做写路径**。

## 3. Python git 库选型

| 方案 | 需系统 git | 依赖摩擦 | async | blame | 维护 |
|------|:---:|------|------|:---:|------|
| **(a) subprocess 直调** | 是 | 零 Python 依赖 | `create_subprocess_exec` 原生非阻塞 | 全（所有 flag） | git 本体最稳 |
| (b) GitPython | 是（wrap git 二进制）| 纯 Python wheel | 同步须 offload | 经 git，粒度差 | 维护中 |
| (c) dulwich | 否（纯 Python）| 纯 Python wheel | 同步须 offload | flag 覆盖不全 | 活跃 |
| (d) pygit2 | 否（libgit2 绑定）| **打包痛**（受限/容器装不上）| 同步须 offload | 结构化 API 最好 | 跟 libgit2 |

**Constitution #6 裁决**：(c)/(d) 不需系统 git 看似最符合，但 (d) 在容器/受限环境 wheel 装不上（摩擦从"运行时缺 git"变"安装时缺 wheel"），(c) blame 短板 + 纯 Python 大仓慢。OctoAgent 已是 uv + Python 3.12 + **Docker 执行隔离**，基镜像装 `git` 是业界常态、成本极低。"优雅降级"≠"必须纯 Python 库"，而是把 git 调用包在探测+try 里失败只丢该 feature。

**结论（SD-2）**：**选 (a) subprocess 直调 plumbing**，与 Hermes/agent-zero 两先例一致。零 Python 依赖、blame/log/branch 全功能、`create_subprocess_exec` 契合 FastAPI async；唯一"缺"（依赖系统 git）用 Hermes 式 `shutil.which("git")` 探测降级满足 #6。**不选 GitPython**（仍依赖 git 二进制没省 #6 风险，blame 还更差）。

## 4. 行为文件版本化底座（共用 git vs 独立 SQLite）

**scoping 现实**（真实例 + 代码 `behavior_workspace/paths.py`）：
- `behavior/system/{file_id}` — **GLOBAL**，`~/.octoagent/behavior/system/`，不在任何 project 下。
- `behavior/agents/{slug}/{file_id}` — **GLOBAL per-agent**，不在 project 下。
- `projects/{slug}/behavior/{file_id}` — per-project。
- `~/.octoagent` 当前不是 git 仓；写路径 `write.py` = `mkdir + write_text` 非原子无历史；写经 REVIEW_REQUIRED（`misc_tools.py:258-264`）。

**方案 A（行为纳入 git）的三重硬墙**：① GLOBAL 的 system/agents 不属任何 project workspace → 一个 per-project repo 装不下，逼出多仓或"整个 `~/.octoagent` 一个仓"（卷入 SQLite db / `.env` / `project.secret-bindings.json` → **违 Constitution #5**）；② restore 必须过 REVIEW_REQUIRED → 裸 `git checkout` 绕过人审违 #4/#7，git 只剩"读旧内容"（一条 SELECT 就够）；③ 行为文件是少量小 md（USER.md 模板 1800 字符硬上限）→ git 的去重/大文件/blob 优势全用不到。

**方案 B（独立 SQLite `behavior_versions`，镜像 F104 `artifact_versions`）**：复用 `versionable_conn` 连接级写隔离 + `_write_lock` + SAVEPOINT；capture-before-overwrite 挂在 `commit_behavior_file_write`；**restore = 写新版**（旧版内容灌进 REVIEW_REQUIRED proposal `confirmed=False` → 用户确认 → 走现有写入门并自动记新版，天然吃人审，零特殊路径）；不依赖 git，无论有无 git 都可用（与 #6 降级咬合）。

**结论（SD-1）**：**hybrid 是 scoping 现实逼出的正解** —— workspace=git（大量文件，git 甜区），behavior=SQLite（GLOBAL+per-project 混合的受审小 md，SQLite 甜区）。Hermes 本身就佐证此分界（版本 workspace、排除行为/config）。

## 5. 非技术 UX（git 术语下沉）

| git 概念 | 主界面平实说法（consumer 产品实证）|
|------|------|
| commit / 快照 | **"版本" / "上一版"**（Google Docs / Dropbox / Apple）|
| log / 历史 | **"版本历史" / "修改记录"** |
| revert / checkout | **"恢复到此版本" / "还原"**（Google "Restore this version" / Dropbox "Roll back"）|
| blame / 逐行作者 | **"谁改的" / "最近修改"**（Google "Last edit"+人名）|
| branch | **基本不向 C 端暴露**；真要说"副本/另一个版本" |

来源：Google Docs / Notion / Dropbox / Apple macOS 文档版本历史。**结论（SD-8）**：主界面"版本历史 / 上一版 / 恢复到此版本 / 谁改的"四件套（与 F104 `/diff` 措辞一致）；commit hash / branch / blame 原始术语全归 Advanced/折叠区；**branch 不进主界面**（v0.2 branch 浏览是 Advanced 开发者功能）。

---

## 给 GATE_DESIGN 的硬墙 flag

1. **触发粒度是决策点**（SD-4）：Hermes per-turn vs agent-zero per-write。OctoAgent 无 CLI turn loop → 快照挂决策环工具写回路径（与 F124/F125 同区）。
2. **行为版本 ≠ workspace 版本，底座必须分开**（SD-1 硬墙）：若用户期望"全用一个 git"，需正面回应 GLOBAL scoping + secrets(#5) + REVIEW_REQUIRED 三重冲突。
3. **#6 降级是构造性要求**（SD-5）：subprocess git + 启动探测，缺则 workspace git 视图禁用、behavior 版本（SQLite）+ 主流程不受影响。
