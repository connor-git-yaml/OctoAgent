# F104 文件工作台 v0.1（diff 视图）— 技术实现计划

**Spec**: [spec.md](spec.md)（✅ Approved，Codex 5 轮 review 闭环 / round5 APPROVE / 0 HIGH，唯一事实源）
**Baseline**: `da947ce`（M5 收口 commit；F104 0 regression 基线）
**Plan date**: 2026-06-06
**M6 阶段**: M6 第 1 个 Feature（Surface 扩张首站）
**Downstream**: F107 文件工作台 v0.2（git-aware branch/commit/blame + behavior 文件版本可视化）

> ⚠️ **过程档案弃用声明**：`research/tech-research.md` / `clarifications.md` / `quality-checklist.md` 已加 SUPERSEDED banner，本 plan **不**据其旧文案（隐式 `(task_id,name)` 聚合 / 纯 A2 / UNIQUE MAY）规划。一切以 spec.md 为准（显式 `versionable` 标记 + 非空 `logical_file_id` + 混合存储 + UNIQUE MUST）。

---

## 0. 总览

### 0.1 背景

F104 是 M6 Surface 扩张第一个 Feature。F084 SnapshotStore 只服务 prefix-cache，**无 history/diff**；artifact `version` 字段只是计数器、旧版本内容不可取（`put_artifact` INSERT-only）。要做"上一版 vs 当前版"diff 必须**先在 backend 保留历史内容**——F104 不是纯 UI Feature。

核心哲学：F104 是 **surface 层**，不触碰主 Agent / Worker / Subagent 协作模型（守 H1/H2/H3）。后端通过 append-only `artifact_versions` 历史表保留版本内容（仅显式 `versionable=True` 写入），前端新增 Files Tab 提供 git 风格两版 diff，技术字段折叠到 Advanced 区（面向非技术用户）。

### 0.2 实测侦察结论（主 session 已实测，plan 直接采用）

| 项目 | 实测结论 | 对 plan 的影响 |
|------|---------|---------------|
| `put_artifact` 签名 | `artifact_store.py:47` `async def put_artifact(self, artifact, content: bytes \| None = None) -> None`，INSERT-only，无版本逻辑 | 加 `versionable: bool = False` + `logical_file_id: str \| None = None` keyword 参数 |
| 混合存储现状 | `artifact_store.py:58-78`：`content` 非空时按 `size >= ARTIFACT_INLINE_THRESHOLD`（4KB）或 非 UTF-8-safe → 写文件系统 + `storage_ref`；否则 inline 到 `parts[0].content`。`hash`/`size` 在 `put_artifact` 内由 `compute_hash_and_size` 计算 | 版本表分支判定复用同一阈值；小文件存内容副本列、大文件存 `storage_ref` 指针 + hash |
| commit 责任 | `put_artifact` **自身不 commit**；调用方（progress_note.py:135-136）`await conn.commit()`。`_maybe_merge_old_notes` 内部另有 commit（:308-309） | **versionable=True 改自包含事务**（put_artifact 内自 BEGIN/commit/rollback，§1.2 分支 B，Codex re-review critical）；versionable=False 默认路径保持调用方 commit（0 regression）|
| 版本号并发风险 | `StoreGroup` 单 `aiosqlite.connect`/进程（`store/__init__.py:69`）；但 spec SD-2/CL-4 确认 async coroutine 在 `MAX→await→INSERT` 间有让出点 → 仍需 `UNIQUE` + `BEGIN IMMEDIATE` + 重试 | DDL 加 `UNIQUE(task_id,logical_file_id,version_no)`；append 用 `BEGIN IMMEDIATE` 包住 `MAX+1→INSERT`，UNIQUE 冲突重试 |
| versionable 唯一来源（v0.1） | `progress_note.py:120-134` user step：`name=f"progress-note:{step_id}"`；`:276-292` 合并：`name="progress-note:__merged_history__"` | user step 传 `versionable=True, logical_file_id=f"progress-note:{step_id}"`；合并 **不传**（默认 False，SD-9 排除）|
| 删旧笔记守卫 | `progress_note.py:297-300` `hasattr(store,"delete_artifact"/"remove_artifact")` 守卫，`SqliteArtifactStore` 无此方法 = inert（A2 小文件副本天然防御未来真删）| 不改此逻辑；版本副本独立于主表行存活 |
| session 级联 | `session_delete.py:54` 事务前 `collect_storage_refs`；`:83` 事务内 `delete_artifacts_by_task_ids`；`:96` commit；`:101-111` 事务后 unlink 文件 | **CL-3 级联接入点**：`:83` 邻接加 `delete_artifact_versions_by_task_ids`（事务内，commit 前）|
| schema 范式 | `sqlite_init.py`：`_XXX_DDL`（`CREATE TABLE IF NOT EXISTS`）+ `_XXX_INDEXES` list + init `await conn.execute` 注册（:1627-1660）+ 索引循环（:1664-1679）；`worker_profile_revisions`（:354-367）含 `UNIQUE(profile_id,revision)` 为范式参考 | 新表纯新增 → 老库自动建空表 = 0 regression；DDL + INDEXES 仿 `worker_profile_revisions` |
| 主表 DDL | `artifacts` 表 `sqlite_init.py:70-85`（10 列）；F104 **零改动**（方案 A）| FR-004 0 regression：artifacts 主表不动一行 |
| API 鉴权（prompt 措辞校正） | 实测 `tasks.router` 通过 `main.py:341,345` `dependencies=[Depends(require_front_door_access)]` **路由级** front-door token 鉴权，**非** route handler 内 Bearer | 新 router 走 `app.include_router(..., dependencies=protected)` 同模式，**不**在 handler 内写鉴权 |
| route handler 范式 | `tasks.py:148` `async def ...(task_id, store_group=Depends(get_store_group))`；返回 dict/JSONResponse | 新 route 复用 `get_store_group` 依赖注入 |
| 前端路由挂载 | `App.tsx:8-16` lazy import；`:68` `<Route path="work" .../>`；`:55-75` 路由树 | 加 `const FilesCenter = lazy(...)` + `<Route path="files" .../>` |
| 前端 NavLink | `WorkbenchLayout.tsx:430-459` NavLink 数组（含 badge/renderNavDescription 逻辑）| 数组加 `{to:"/files",label:"文件"}` + `renderNavDescription` 文案 |
| 前端栈 | React 19 + React Router 7 + Vite + 原生 fetch（`api/client.ts`）+ 纯手工 CSS（tokens.css），**无** diff/UI 库 | 新增 `diff`（jsdiff）npm 包 + 自建 CSS diff 组件（D-DIFF） |

**结论（Codex re-review critical + 方案 B 修正）**：原"调用方统一 commit 使 FR-021 天然满足"在单连接 async 下**不可靠**（事务连接级跨 await，mixed-writer 污染）。修订：**versionable=True 路径自包含事务，走独立 `versionable_conn`**（方案 B，§1.2 分支 B，put_artifact 内 `_write_lock` + BEGIN IMMEDIATE + 主表 INSERT + 版本 INSERT + commit/rollback；失败 event 也走 versionable_conn），FR-021 由自包含事务 + 连接隔离保证；versionable=False 保持调用方主连接 commit（0 regression）。独立连接物理隔离 mixed-writer；T1.3 硬 gate 实测验证（§4 风险）。

### 0.3 Codebase Reality Check

| 目标文件 | 当前 LOC | 公开方法/接口数 | 已知 debt（与本次相关） |
|---------|---------|----------------|----------------------|
| `artifact_store.py`（修改：+versionable append + 4 查询方法 + 级联删除）| 209 | 7 public（put/get/list/get_content/collect_refs/delete_by_task/_resolve）| 文件头 docstring 标"骨架/Phase 6 完成"已过时（小 debt，可顺手清注释，不强制）|
| `sqlite_init.py`（修改：+1 DDL + 注册 + 索引）| ~1693 | DDL/index 注册函数 | 无相关 TODO/FIXME |
| `session_delete.py`（修改：+1 行级联调用）| 122 | 1 cascade 函数 | 无 |
| `progress_note.py`（修改：user step 传 versionable=True）| ~320 | execute/load/merge 3 主路径 | `delete_artifact` hasattr 守卫 inert（spec 已记录为防御性，不改）|
| `enums.py`（修改：+1 EventType `ARTIFACT_VERSION_APPEND_FAILED`）| ~250 | EventType StrEnum | 无 |
| `artifact.py`（**不改**）| 52 | Artifact/ArtifactPart model | 无 |
| `routes/files.py`（新建）| 0 → ~180 | 3-4 endpoint | 新文件 |
| `frontend FilesCenter.tsx`（新建）| 0 → ~220 | 两级导航页 | 新文件 |
| `frontend DiffView.tsx`（新建）| 0 → ~180 | diff 渲染组件 | 新文件 |
| `frontend api/files.ts`（新建，或并入 client.ts）| 0 → ~60 | 4 fetch 函数 | 新文件 |

**前置清理规则评估**：无目标文件同时满足 "LOC>500 且新增>50 行" + ">3 个相关 TODO/FIXME" + "明确代码重复"。`artifact_store.py` 仅 209 行（< 500），`sqlite_init.py` 虽 > 500 但本次仅新增 DDL 注册（< 50 行结构性新增）。**无需 CLEANUP 前置 task**。`artifact_store.py` 文件头过时 docstring 在 Phase 1 顺手更新即可（非阻塞）。

### 0.4 Impact Assessment

| 维度 | 评估 |
|------|------|
| **影响文件数** | 直接修改 5（artifact_store / sqlite_init / session_delete / progress_note / enums）+ 新建 4（files route + FilesCenter + DiffView + files api）+ 微改 2（App.tsx 路由 + WorkbenchLayout NavLink）= **11 文件**。间接受影响：`store/__init__.py`（无需改 StoreGroup——版本逻辑内聚在 SqliteArtifactStore）|
| **跨包影响** | 3 个顶层边界：`packages/core/`（artifact_store / sqlite_init / session_delete / enums）+ `packages/tooling/`（progress_note）+ `apps/gateway/`（files route）+ `frontend/`。后端逻辑集中在 artifact_store 一处 + 新表，非分散修改 |
| **数据迁移** | 新增 append-only 表（`CREATE TABLE IF NOT EXISTS`）。老库启动自动建空表，**无存量数据迁移**（默认 versionable=False，历史 artifact 不回填版本表）。artifacts 主表 schema **零变更** |
| **API/契约变更** | `put_artifact` 新增 2 个 keyword 参数（默认值向后兼容，现有调用 0 改动）；新增 4 个查询方法（纯新增）；新增 3-4 个 HTTP endpoint（纯新增，无 breaking）|
| **风险等级** | **MEDIUM**：影响文件 11（10-20 区间）+ 跨包影响 3（但后端高内聚）+ 涉及 schema 新增（数据层）。**未达 HIGH**（影响 < 20、无主表 schema 变更、无公共 Agent 协作 API 契约变更、无存量数据迁移）|

**MEDIUM 决议**：保持 5 Phase 节奏（spec §8 建议 6 段，本 plan 合并"侦察复核"进 Phase 1 前言）。**命中"数据库 schema 新增"重大架构变更节点** → 每 implement Phase 后 Codex per-Phase review + Final cross-Phase review 必走，每 Phase 后全回归 0 regression vs da947ce。

### 0.5 Constitution Check

| 原则 | 适用性 | 评估 | 说明 |
|------|--------|------|------|
| #1 Durability First | **重点** | 满足 | 版本内容 append-only 落盘到 `artifact_versions`（小文件独立副本）；进程重启历史不丢（FR-003）|
| #2 Everything is an Event | 适用 | 满足 | 版本记录 append-only 不可篡改（FR-005，唯一例外 = 删 task 级联，数据归属非篡改）；append 失败 emit `ARTIFACT_VERSION_APPEND_FAILED`（FR-021）|
| #3 Tools are Contracts | 适用 | 满足 | `put_artifact` 新参数有完整类型注解；不新增工具，progress_note schema 不变 |
| #4 Side-effect Must be Two-Phase | 不适用 | — | 版本写入是 append-only 持久化，非不可逆 side-effect；diff 查询只读 |
| #5 Least Privilege | **重点** | 满足 | SD-9 排除 `chat-import`（跨 scope 暴露）/ `llm-*` / `tool_output:*`；`logical_file_id` 隐含 task scope，不跨 task/scope 聚合 |
| #6 Degrade Gracefully | **重点** | 满足 | 内容不可用（FR-010 占位）/ 二进制（FR-018）/ 超大（FR-019）/ 无差异（FR-015）均降级提示，不崩溃 |
| #7 User-in-Control | 适用 | 满足 | Files Tab 只读；不引入自动化动作 |
| #8 Observability | **重点** | 满足 | 文件演变可观测（Feature 核心价值）；版本 append 失败 emit 事件；建表失败 fail-fast 阻断（不静默降级，FR-021）|
| #9 Agent Autonomy | **重点** | 满足 | `versionable` 判定在**写入方**显式传参（FR-022），`artifact_store` **不**硬编码 name 黑白名单——避免存储层规则替代 Agent 决策 |
| #10 Policy-Driven Access | 适用 | 满足 | API 复用 front-door token 路由级鉴权（`require_front_door_access`），不在 route handler 自行做权限拦截 |

**无 VIOLATION**。重点关注 #1/#5/#6/#8/#9。

### 0.6 Phase 数与复杂度

**5 Phase（1→2→3→4→5 串行，前端 3/4 在后端 2 完成后可与边界验证交错）**，MEDIUM 复杂度，命中"数据库 schema 新增"节点 → Codex per-Phase + Final cross-Phase review 必走。

---

## 1. 技术方案

### 1.1 `artifact_versions` 历史表 schema（D-A 方案 A / SD-8 混合存储）

新表 `_ARTIFACT_VERSIONS_DDL`（`sqlite_init.py`，仿 `worker_profile_revisions` 范式）：

```sql
CREATE TABLE IF NOT EXISTS artifact_versions (
    version_id        TEXT PRIMARY KEY,           -- ULID
    task_id           TEXT NOT NULL,
    logical_file_id   TEXT NOT NULL,              -- 写入方显式声明，MUST 非空（versionable=True 时）
    version_no        INTEGER NOT NULL,           -- 该逻辑文件 key 内单调递增，MAX+1
    artifact_id       TEXT NOT NULL,              -- 关联触发此版本的 artifact 行（审计/Advanced 区）
    ts                TEXT NOT NULL,              -- 写入时间，兜底排序键
    storage_kind      TEXT NOT NULL,              -- 'inline' | 'storage_ref'（混合存储分支标记）
    content           TEXT,                       -- 小文件：UTF-8 内容独立副本（inline 分支）；大文件 NULL
    storage_ref       TEXT,                       -- 大文件：指针（storage_ref 分支）；小文件 NULL
    size              INTEGER NOT NULL DEFAULT 0,
    hash              TEXT NOT NULL DEFAULT '',    -- SHA-256，两种分支都填

    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
    UNIQUE(task_id, logical_file_id, version_no)   -- CL-4 MUST：DB 层强唯一防线
);
```

索引 `_ARTIFACT_VERSIONS_INDEXES`：

```sql
-- 按逻辑文件 key 取版本列表 + 取 MAX(version_no)（FR-006/FR-007）
CREATE INDEX IF NOT EXISTS idx_artifact_versions_logical
    ON artifact_versions(task_id, logical_file_id, version_no DESC);
-- 按 task 聚合逻辑文件清单（FR-008） + 级联删除（CL-3）
CREATE INDEX IF NOT EXISTS idx_artifact_versions_task
    ON artifact_versions(task_id);
```

**SD-8 混合存储映射**（沿用 `put_artifact` 现有阈值判定，不重复阈值逻辑）：
- 小文件（inline 分支：`size < ARTIFACT_INLINE_THRESHOLD` 且 UTF-8-safe）：`storage_kind='inline'`，`content` 存独立副本（真独立、append-only，不因 artifacts 行被删/合并失效）→ SC-002 小文件 100% 可取回。
- 大文件（storage_ref 分支）：`storage_kind='storage_ref'`，`storage_ref` 存指针 + `hash`，`content=NULL`（不复制大文件副本，避免成本）→ 局限：随 artifacts storage_ref 生命周期，session/task 删除清理后不可取回 → FR-010 占位。SC-002 大文件 best-effort。

**注册**：`sqlite_init.py:1660` 邻接加 `await conn.execute(_ARTIFACT_VERSIONS_DDL)`；索引循环（:1664-1679）加 `+ _ARTIFACT_VERSIONS_INDEXES`。老库启动自动建空表 = 0 regression。

### 1.2 `put_artifact` 签名变更与版本号原子分配（FR-001/FR-002/FR-021/FR-022）

```python
async def put_artifact(
    self,
    artifact: Artifact,
    content: bytes | None = None,
    *,
    versionable: bool = False,            # FR-001 默认 False → 现有写入完全不进版本表
    logical_file_id: str | None = None,   # versionable=True 时 MUST 非空，无 name 回退（SD-1/Codex re-review）
) -> None:
```

逻辑（**两个互斥分支**，Codex re-review critical 修正：消除"调用方 commit"与"自包含事务"矛盾、先校验后写）：

**分支 A — `versionable=False`（默认）**：保持现状旧路径（现有混合存储分支 + 主表 INSERT + 调用方 commit），FR-004 行为零变更、0 regression；**完全不碰版本表、不开 `_write_lock`**（`if not versionable: return`）。

**分支 B — `versionable=True`**（自包含事务）：
1. **先校验**（任何 INSERT 之前）：`logical_file_id` 空/None → `raise ValueError`（无 name 回退，Codex round2 #5；避免空 id 时主表已写）。
2. 计算版本行字段：`storage_kind` 按 `artifact.storage_ref` 是否设置判定；`content` 取 inline 分支 `parts[0].content`（小文件）或 `None`（大文件）；`hash`/`size` 复用主表已算值。
3. **自包含事务 + 版本号原子分配**（SD-2/CL-4，**主表 INSERT 也在此事务内**）：

⚠️ Codex review 指出原"put_artifact 开 BEGIN / 调用方 commit"模型在单连接（StoreGroup 共享 `aiosqlite.Connection`）+ async 多 coroutine 下**不可靠**——SQLite 事务是连接级、跨 await 边界，其他 coroutine 可在同连接写入/提交，污染事务边界（破坏 FR-021 原子性 + 0 regression）。修订为 **versionable 路径自包含事务 + 写锁 + SAVEPOINT 重试**。

> **方案 B（用户选 B 真修隔离，Codex 修复 2）**：versionable 自包含事务**走独立写连接 `versionable_conn`**（不复用主连接 conn）——主连接上调用方默认 versionable=False 的未提交写不被 versionable 路径的 commit/rollback 卷入，mixed-writer 在事务层面物理隔离。下方伪代码用 `vconn` 代指 `self._versionable_conn`；主连接 `conn` 不参与 versionable 事务。失败 event 也走 `vconn` 提交（versionable 失败已 rollback 干净，详见下文 FR-021 失败策略）。

```python
# artifact_store.__init__: self._write_lock = asyncio.Lock(); self._versionable_conn (独立写连接)
# versionable=True 路径（自包含，走独立 vconn，不依赖调用方 commit、不碰主连接 conn）：
vconn = self._versionable_conn
async with self._write_lock:                       # 串行化写事务，防 mixed-writer 交错
    try:
        await vconn.execute("BEGIN IMMEDIATE")      # 一个事务、只 BEGIN 一次（独立 vconn）
        await vconn.execute("INSERT INTO artifacts ...")          # 主表（versionable 路径自己做）
        for attempt in range(MAX_VERSION_RETRY):    # =3
            await vconn.execute("SAVEPOINT sp_ver")
            next_no = COALESCE(MAX(version_no),0)+1 WHERE (task_id, logical_file_id)
            try:
                await vconn.execute("INSERT INTO artifact_versions ... version_no=next_no")
                await vconn.execute("RELEASE sp_ver"); break
            except aiosqlite.IntegrityError:         # UNIQUE 冲突
                await vconn.execute("ROLLBACK TO sp_ver")  # 撤版本 INSERT、保留主表行，不重 BEGIN
                if attempt == MAX_VERSION_RETRY-1: raise    # 耗尽 → 外层 rollback 整事务
        await vconn.commit()                         # 自 commit
    except Exception:
        await vconn.rollback()                       # 失败自 rollback（vconn 主表+版本都撤，无脏事务残留）
        raise
# best-effort 失败信号：rollback 之后、事务外 emit ARTIFACT_VERSION_APPEND_FAILED
#   ① structlog.warning（best-effort local log，不依赖 DB 写）
#   ② DB 可写时 append_event_committed(conn=vconn)（best-effort durable，DB locked 时写不进 → 仅 structlog）
```

**三项修正（Codex plan-review）**：
- **[critical] 自包含事务**：versionable 自己 BEGIN/commit/rollback，**不再依赖调用方 commit**——边界在 put_artifact 内闭合、不跨方法。progress_note user step 原 `conn.commit()`（:136）对 versionable 变 no-op，Phase 1 调整。
- **[high] SAVEPOINT 重试粒度**：一事务只 BEGIN 一次；版本 INSERT 用 SAVEPOINT，冲突 `ROLLBACK TO sp_ver`（保留主表行）后重 SELECT MAX+INSERT、**不重 BEGIN**；耗尽 rollback 整事务（两表均不留 → 失败注入断言：成功时 artifacts+versions 各 1 行匹配，耗尽时两表均 0 行）。
- **[high] best-effort 失败信号 + 自 rollback**：失败在 rollback 后、事务外 emit（progress_note catch 不 rollback 的缺陷由自包含 rollback 解决；失败事件不被事务回滚吞）。两轨均 best-effort（structlog local log + DB 可写时 versionable_conn event），见下文 FR-021 失败策略。

> ⚠️ **mixed-writer 约束（Codex critical 核心，T1.3 实测 + 用户选 B 真修隔离）**：原方案 versionable 与默认路径共享主连接，理论上"默认 INSERT 未 commit window × versionable BEGIN IMMEDIATE"在真并发下可能交错。**方案 B 真修**：versionable 走**独立 `versionable_conn`**，与主连接物理隔离——默认 versionable=False 路径在主连接的调用方 commit（0 regression）与 versionable 事务不再共享连接，mixed-writer 在事务层面消解。失败 event 也走 versionable_conn（避免把失败路径重新拉回主连接、转移 mixed-writer）。T1.3/T5.1 仍补 mixed versionable/default writer 并发测（验证隔离）；连接级更广义事务管理（如默认路径也独立连接）若评估 regression 可控为**架构 follow-up，超 F104 v0.1**。

**FR-021 失败策略（best-effort，Codex re-review high #2 + 方案 B 修正 emit 路径）**：
- 实测：`append_event_committed`（event_store.py:54）**独立提交 + task_seq 重试**（在 put_artifact rollback 后调用仍 durable、不被前面 rollback 吞）。**方案 B**：失败 event 走**独立 `versionable_conn`**（versionable 失败已 rollback 干净，连接处于干净状态），不走主连接——避免把调用方主连接上未提交的默认 versionable=False 写一并提前提交（否则调用方后续 rollback 失效，mixed-writer 转移到失败路径，Codex 修复 2）。
- **wiring**：StoreGroup 构造把 `event_store` + `versionable_conn` **注入 `SqliteArtifactStore`**。put_artifact versionable 失败 → 自 `rollback`（撤 versionable_conn 主表+版本，连接不留脏事务，修复 high #3）→ **双轨 best-effort 信号**：①`structlog.warning`（best-effort local log——经 logging_config 仅挂 StreamHandler 输出进程 stderr，无独立文件/审计 sink，可见性取决于环境是否持久化进程流，独立 sink 超 v0.1）②DB 可写时 `event_store.append_event_committed(ARTIFACT_VERSION_APPEND_FAILED, conn=versionable_conn)`（best-effort durable；event: `task_id=artifact.task_id` / `actor=SYSTEM` / payload=失败详情；DB locked / 不可写时写不进 → 降级仅 structlog）→ raise。
- progress_note 调用方降级 `persisted=False` 不阻断 Worker；连接干净。
- 建表失败（启动期）→ fail-fast 阻断服务（DDL 致命，不静默降级）。
- **T5.4 断言**：**DB 可写场景** events 表确有 `ARTIFACT_VERSION_APPEND_FAILED`（走 versionable_conn 独立提交，Codex 要求可审计）；**DB locked 场景** events 表无该 event 是预期（best-effort，不强求；SQLite 单写锁物理限制——失败 event 自身也写不进被锁的 DB），断言 structlog best-effort local log 降级被调用。

### 1.3 版本查询方法（artifact_store.py，FR-006~FR-010）

```python
async def list_versions(self, task_id: str, logical_file_id: str) -> list[ArtifactVersionMeta]:
    """FR-006：按逻辑文件 key 取版本列表（版本号 + 元信息，不含大内容）。
    ORDER BY version_no DESC, ts DESC（ts 兜底）。"""

async def get_current_and_previous(
    self, task_id: str, logical_file_id: str
) -> tuple[ArtifactVersionContent | None, ArtifactVersionContent | None]:
    """FR-007：取当前版（MAX version_no）与上一版（次大）内容。
    inline → content；storage_ref → 读文件，文件不存在/被清理 → availability='unavailable'（FR-010 占位）。
    < 2 版本 → previous=None。"""

async def list_versionable_files_for_task(
    self, task_id: str
) -> list[LogicalFileSummary]:
    """FR-008 第二级：列出该 task 下 version count >= 2 的逻辑文件（SD-4 过滤）。
    SELECT logical_file_id, COUNT(*) ... GROUP BY ... HAVING COUNT(*) >= 2。"""

async def list_tasks_with_versionable_files(self) -> list[str]:
    """FR-008 第一级（SD-7 两级导航第一级）：列出有 >=2 版本逻辑文件的 task_id 清单。
    SELECT DISTINCT task_id FROM (... GROUP BY task_id, logical_file_id HAVING COUNT(*)>=2)。"""

async def delete_artifact_versions_by_task_ids(self, task_ids: list[str]) -> int:
    """CL-3 级联：删 task 时清版本表（session_delete.py:83 邻接调用，事务内 commit 前）。
    append-only 唯一例外（数据归属，非篡改，FR-005）。"""
```

返回类型用 Pydantic BaseModel（`ArtifactVersionMeta` / `ArtifactVersionContent` / `LogicalFileSummary`，含 `availability: Literal["available","unavailable"]` 字段服务 FR-010）。

### 1.4 progress_note 写入方接入（FR-022 / SD-9）

`progress_note.py:134` user step 写入改：

```python
await artifact_store.put_artifact(
    artifact, content_json.encode("utf-8"),
    versionable=True,
    logical_file_id=f"progress-note:{input_data.step_id}",
)
```

`:290` `_maybe_merge_old_notes` 合并写入 **不动**（默认 `versionable=False`，SD-9 排除 `__merged_history__`）。`versionable` 判定在写入方（FR-022），`artifact_store` 不硬编码 name 模式（#9）。

### 1.5 HTTP API 契约（FR-009，新建 `routes/files.py`）

新 router，`main.py` 加 `app.include_router(files.router, tags=["files"], dependencies=protected)`（复用 front-door token 路由级鉴权，**非** Bearer/handler 内鉴权——prompt 措辞校正）。handler 用 `store_group=Depends(get_store_group)`。

| Method + Path | 用途 | 响应（关键字段）|
|---------------|------|---------------|
| `GET /api/files/tasks` | 两级导航第一级：有多版本逻辑文件的 task 清单（FR-008/SD-7）| `{ "tasks": [{ "task_id", "title" }] }`（title 可从 task_store 补）|
| `GET /api/files/tasks/{task_id}/logical-files` | 第二级：该 task 下 version≥2 逻辑文件（FR-008/FR-012）| `{ "files": [{ "logical_file_id", "display_name", "version_count" }] }`（display_name = SD-5 友好映射，后端可不映射、前端映射，二选一在 Phase 3 定）|
| `GET /api/files/tasks/{task_id}/logical-files/{logical_file_id}/diff` | 当前版 vs 上一版内容（FR-007/FR-013）| `{ "current": {version_no,content,availability,storage_kind}, "previous": {...}, "binary": bool, "oversize": bool }` |
| `GET /api/files/tasks/{task_id}/logical-files/{logical_file_id}/versions` | （可选）版本列表元信息，Advanced 区 | `{ "versions": [{ version_no, ts, size, hash }] }` |

`logical_file_id` 含 `:`（如 `progress-note:step-3`）→ path 需 URL-encode 或改用 query param（Phase 2 实测定，避免路由解析歧义）。响应 **不含** artifact_id（ULID）/storage_ref/hash 于主路径字段——这些归 `/versions` Advanced endpoint（FR-017/SC-004）。二进制/超大检测在后端预判（`binary`/`oversize` flag），前端据此降级（FR-018/FR-019）。

### 1.6 前端组件树（D-DIFF：jsdiff + 自建 CSS）

```
App.tsx (+lazy FilesCenter, +Route path="files")
WorkbenchLayout.tsx (+NavLink {to:"/files",label:"文件"})
src/pages/FilesCenter.tsx (新建，两级导航 SD-7/CL-2)
  ├─ 第一级：task 列表（GET /api/files/tasks）—— 参考 TaskList.tsx 选 task 模式
  └─ 第二级：选中 task 的逻辑文件列表（GET .../logical-files）
        ↓ 点击逻辑文件
src/components/files/DiffView.tsx (新建)
  ├─ GET .../diff → jsdiff diffLines(previous, current)
  ├─ 自建 CSS 渲染：新增行（绿）/ 删除行（红）/ 未变行（中性），tokens.css 配色
  ├─ 友好命名（SD-5）+ 技术字段折叠 <details> Advanced 区（FR-017）
  └─ 降级分支：无差异提示（FR-015）/ 二进制提示（FR-018）/ 超大截断（FR-019）/ 不可用占位（FR-010）
src/api/files.ts (新建，或并入 client.ts) —— 4 个 fetch 封装，复用 client.ts 错误处理范式
```

新增依赖：`diff`（jsdiff）+ `@types/diff`（devDependency）。**不**引入 react-diff-viewer / diff2html（D-DIFF 约束，契合纯手工 CSS）。

---

## 2. Phase 拆分

### Phase 1 — 后端版本表 + put_artifact versionable append（数据地基）

**范围**：`artifact_versions` DDL + `put_artifact` versionable 参数 + 版本号原子分配（BEGIN IMMEDIATE/重试）+ progress_note user step 接入 + session_delete 级联 + `ARTIFACT_VERSION_APPEND_FAILED` EventType。

**改动文件**：
- `sqlite_init.py`：+`_ARTIFACT_VERSIONS_DDL` + `_ARTIFACT_VERSIONS_INDEXES` + 注册 + 索引循环
- `enums.py`：+`ARTIFACT_VERSION_APPEND_FAILED` EventType
- `artifact_store.py`：`put_artifact` +2 参数 + 版本 append + BEGIN IMMEDIATE 事务边界 + `delete_artifact_versions_by_task_ids` + 返回类型 model（`ArtifactVersionMeta` 等，可放新 `models/artifact_version.py`）
- `progress_note.py`：user step 传 `versionable=True, logical_file_id`（合并写入不动）
- `session_delete.py`：`:83` 邻接加级联调用

**验收标准**：
- FR-001/FR-002/FR-003/FR-005/FR-021/FR-022 后端部分实现
- 连续 3 次 `versionable=True` 写入 → 3 版本、版本号单调递增且唯一（Story 3 AC-1）
- 进程重启后小文件版本内容可取回（Story 3 AC-2）
- 默认 `versionable=False` 路径：artifacts 主表行为与 baseline 完全一致（FR-004，Story 3 AC-3）
- `progress-note:__merged_history__` 合并写入 **不进** 版本表（SD-9 负向）
- 删 task 级联清版本表，无孤儿

**Codex review 点**：per-Phase（**重点：BEGIN IMMEDIATE 与调用方 commit 的事务边界协调 + 嵌套事务报错风险 + UNIQUE 冲突重试正确性 + 0 regression 默认路径**）
**回归 gate**：全回归 passed ≥ da947ce baseline，0 regression；e2e_smoke 8/8

### Phase 2 — 后端查询 + HTTP API

**范围**：4 查询方法（list_versions / get_current_and_previous / list_versionable_files_for_task / list_tasks_with_versionable_files）+ FR-010 占位 + 二进制/超大后端预判 + `routes/files.py` 4 endpoint + main.py 挂载。

**改动文件**：
- `artifact_store.py`：+4 查询方法
- `routes/files.py`（新建）：4 endpoint，`get_store_group` 依赖
- `main.py`：+`include_router(files.router, dependencies=protected)`

**验收标准**：
- FR-006/FR-007/FR-008/FR-009/FR-010 实现
- `list_versionable_files_for_task` 只返回 version≥2 逻辑文件（SD-4/FR-012）
- `get_current_and_previous`：< 2 版本 → previous=None；storage_ref 文件被删 → availability='unavailable'（FR-010，Edge Case）
- API 经 front-door token 鉴权（路由级）；响应主字段不含 artifact_id/storage_ref/hash（FR-017/SC-004 后端侧）
- `logical_file_id` 含 `:` 的路由解析正确

**Codex review 点**：per-Phase（重点：FR-010 占位不抛异常 + version≥2 过滤 SQL 正确性 + 鉴权挂载 + 技术字段是否泄漏进主响应）
**回归 gate**：全回归 0 regression；e2e_smoke 8/8

### Phase 3 — 前端 Files Tab 两级导航

**范围**：Route + NavLink + FilesCenter 两级导航（task 列表 → 逻辑文件列表）+ api/files.ts。

**改动文件**：
- `App.tsx`：+lazy FilesCenter + Route
- `WorkbenchLayout.tsx`：+NavLink + renderNavDescription
- `src/pages/FilesCenter.tsx`（新建）
- `src/api/files.ts`（新建）

**验收标准**：
- FR-011（Files Tab + NavLink + 路由 + 两级导航 SD-7/CL-2）
- FR-012（列出 version≥2 逻辑文件，单版本不进列表，SD-4/SC-006）
- SC-001（≤2 次点击到 diff：选 task + 点文件）
- 友好命名占位（Phase 4 完成 DiffView 后整体验证 SD-5）

**Codex review 点**：per-Phase（重点：两级导航状态管理 + 单版本完全隐藏 SC-006）
**回归 gate**：前端 build 通过；后端全回归 0 regression（前端改动不影响后端测试）

### Phase 4 — 前端 diff 视图

**范围**：DiffView 组件（jsdiff 生成 + 自建 CSS 渲染）+ 友好命名映射（SD-5）+ 技术字段折叠（FR-017）+ 降级分支（无差异/二进制/超大/不可用）。

**改动文件**：
- `package.json`：+`diff` + `@types/diff`
- `src/components/files/DiffView.tsx`（新建）
- `src/api/files.ts`：补 diff fetch
- diff CSS（tokens.css 或组件局部 CSS）

**验收标准**：
- FR-013（git 风格行级 diff，新增/删除/未变可区分，Story 1 AC-1）
- FR-014（默认对比最新两版，无任意版本选择器，Story 1 AC-2）
- FR-015（无差异提示，Story 1 AC-3）
- FR-016（友好命名 SHOULD，映射不到原样显示，Story 4 AC-1/AC-2）
- FR-017（技术字段折叠 Advanced 区，SC-004 主视图技术字段出现 0 次，Story 2 AC-3）
- FR-018（二进制提示）/ FR-019（超大降级）/ FR-010（不可用占位）
- SC-005（阈值内文本 1 秒内渲染）

**Codex review 点**：per-Phase（重点：jsdiff 大文本性能 + 降级分支完整性 + 技术字段 0 泄漏 SC-004）
**回归 gate**：前端 build 通过

### Phase 5 — 边界降级 + 验证 + Final review

**范围**：补全 FR-010/FR-018/FR-019 边界测试 + 并发 put_artifact 测试 + 负向测试 + 单版本不可见测试 + 全回归 + e2e_smoke + completion-report + handoff。

**验收标准**：
- 并发 `put_artifact(versionable=True)` 同 key → 无重复版本号（SD-2/FR-002 MUST 补的并发回归测试）
- 负向：`progress-note:__merged_history__`（SD-9）/ `tool_output:*`（非 versionable）**不进** Files Tab（Story 4 AC-3/AC-4，SC-006）
- 单版本逻辑文件 100% 不进主列表（SC-006 MUST 断言不可见）
- FR-021 回归测试：missing table / DB locked / UNIQUE 冲突重试 / 版本写失败回滚
- 大文件版本路径（storage_ref）取回 + session 删除后 FR-010 占位（Story 3 AC-4 / SC-002 best-effort）
- 全量回归 passed ≥ da947ce baseline，0 regression（SC-003）；e2e_smoke 8/8
- 22 FR + 6 SC 全覆盖矩阵；completion-report.md + handoff.md（给 F107）

**Codex review 点**：**Final cross-Phase review**（输入：Phase 1-5 全 diff + spec.md + plan.md；重点：事务原子性整体一致性 / 0 regression 彻底性 / SD-9 数据源边界 / 技术字段 0 泄漏 / 并发防线 / FR-010 占位鲁棒性）
**回归 gate**：0 HIGH 残留；全回归 0 regression；e2e_smoke 8/8

---

## 3. 测试策略（分层）

| 层级 | 范围 | 关键用例 |
|------|------|---------|
| **unit** | artifact_store 版本逻辑 | 版本号 MAX+1 单调 / inline vs storage_ref 分支字段 / `versionable=True` 空 logical_file_id raise / 4 查询方法 SQL / FR-010 占位 |
| **unit** | progress_note 接入 | user step 传 versionable=True / 合并写入不传（SD-9）|
| **集成** | put_artifact + 主表同事务 | 默认 versionable=False 路径与 baseline 等价（FR-004）/ 版本写失败主表回滚（FR-021）/ 重启后取回（FR-003）|
| **集成** | session_delete 级联 | 删 task 清版本表无孤儿（CL-3）/ 大文件 storage_ref 删除后 FR-010 占位 |
| **集成** | HTTP API | 4 endpoint 响应结构 / version≥2 过滤 / front-door 鉴权 / 主字段无技术字段（SC-004）|
| **并发** | put_artifact 同 key 竞态 | 并发 N 次 versionable 写入 → 断言无重复版本号（SD-2/FR-002 MUST）|
| **负向** | 数据源边界 | `__merged_history__` 不进版本表 / `tool_output:*` 非 versionable 不进 Files Tab（Story4 AC-3/AC-4）|
| **负向/边界** | 失败注入 | missing table fail-fast / DB locked 重试 / UNIQUE 冲突重试 / 版本写失败回滚（FR-021）|
| **前端** | build + 组件 | FilesCenter 两级导航 / 单版本不可见（SC-006）/ DiffView 三类行渲染 / 降级分支（FR-015/018/019）|
| **0 regression** | 全量回归 | 每 Phase 后 passed ≥ da947ce baseline（SC-003）；e2e_smoke 8/8 |

**回归基线命令**（每 Phase 后，用托管实例避免环境污染）：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F104-file-workbench/octoagent
uv run --no-sync python -m pytest -x -q --tb=short -p no:cacheprovider
uv run --no-sync python -m pytest -m e2e_smoke -x -q --tb=short
```

---

## 4. 风险与回滚

| 风险 | 严重度 | 实测结论 / Mitigation |
|------|--------|----------------------|
| **单连接共享事务边界（mixed-writer）** | **CRITICAL → 已修（方案 B）** | Codex plan-review：单连接 async 下"put_artifact 开 BEGIN / 调用方 commit"不可靠（事务连接级跨 await，其他 coroutine 污染/提前提交）。Mitigation（方案 B 真修）：versionable **自包含事务走独立 `versionable_conn`**（自 BEGIN/commit/rollback，与主连接物理隔离）+ `_write_lock` 串行化 + SAVEPOINT 重试 + best-effort 失败信号（§1.2，失败 event 也走 versionable_conn 不转移 mixed-writer）。默认 versionable=False 路径在主连接 commit（0 regression）与 versionable 事务不共享连接。T1.3/T5.1 补 mixed-writer 并发测验证隔离；默认路径也独立连接为架构 follow-up 超 v0.1 |
| **并发版本号重复 + 重试粒度** | MED | UNIQUE MUST + `_write_lock` + **SAVEPOINT 重试**（一事务一 BEGIN，冲突 ROLLBACK TO sp_ver 保留主表，耗尽 rollback 整事务）+ 并发回归测（SD-2/FR-002，Codex high）|
| **失败信号 best-effort（非永久 durable）** | MED | 失败事件双轨均 best-effort：①structlog best-effort local log（仅 StreamHandler 输出 stderr，无独立文件/审计 sink，环境持久化进程流时可见，超 v0.1）；②DB 可写时 versionable_conn best-effort durable event（rollback 后、事务外独立 emit 不被吞，DB locked 时写不进 → 降级仅 structlog，Constitution #8）。put_artifact 自 rollback 不留脏事务（Codex high #3）|
| **0 regression 被破坏** | MED | 默认 versionable=False 完全不碰版本表 + artifacts 主表零改 + 新表纯新增；每 Phase 后全回归 gate |
| **大文件版本不可取回** | LOW（已知局限）| SD-8 明确大文件 best-effort，FR-010 占位，SC-002 仅保证小文件 100%；v0.1 versionable 来源（progress-note）几乎都是小文本，主路径走 inline |
| **logical_file_id 含 `:` 路由解析** | LOW | Phase 2 实测：URL-encode 或改 query param |
| **jsdiff 大文本阻塞前端** | LOW | FR-019 超大降级（截断/提示）+ 后端 oversize flag 预判 + SC-005 1 秒内 |
| **技术字段泄漏主视图** | LOW | API 主响应不含 artifact_id/storage_ref/hash（归 /versions Advanced）+ 前端折叠 + SC-004 断言出现 0 次 |

**回滚策略**：F104 纯新增（新表 + 新参数默认值 + 新 route + 新前端页）。回滚 = revert 各 Phase commit；`artifact_versions` 空表残留无害（无 FK 反向依赖、不影响主表）；progress_note 去掉 versionable 参数即恢复 baseline 行为。无破坏性数据迁移，回滚成本低。

---

## 5. 提交策略

- 每 Phase 单独 commit：`feat(F104-Phase-N): <描述> + Codex review <N>H/<M>M 闭环`
- Phase 1：`feat(F104-Phase-1): artifact_versions 表 + put_artifact versionable append + 版本号原子分配 + progress_note 接入 + session 级联`
- Phase 2：`feat(F104-Phase-2): 版本查询方法 + Files API endpoints`
- Phase 3：`feat(F104-Phase-3): Files Tab 两级导航（Route + NavLink + FilesCenter）`
- Phase 4：`feat(F104-Phase-4): DiffView jsdiff 渲染 + 友好命名 + 技术字段折叠 + 降级分支`
- Phase 5：`docs(F104-Final): 边界/并发/负向验证 + cross-Phase review 闭环 + completion-report + handoff`
- **不主动 push origin/master**，等用户拍板（CLAUDE.local.md §Spawned Task 处理流程）

---

## 附录：关键文件路径索引

| 文件 | 路径（worktree 相对 octoagent/）| 操作 |
|------|------|------|
| artifact_store.py | `packages/core/src/octoagent/core/store/artifact_store.py` | 修改 |
| sqlite_init.py | `packages/core/src/octoagent/core/store/sqlite_init.py` | 修改（+DDL/索引）|
| session_delete.py | `packages/core/src/octoagent/core/store/session_delete.py` | 修改（+级联）|
| enums.py | `packages/core/src/octoagent/core/models/enums.py` | 修改（+EventType）|
| artifact_version.py | `packages/core/src/octoagent/core/models/artifact_version.py` | 新建（返回类型 model）|
| progress_note.py | `packages/tooling/src/octoagent/tooling/progress_note.py` | 修改（user step versionable）|
| files.py | `apps/gateway/src/octoagent/gateway/routes/files.py` | 新建 |
| main.py | `apps/gateway/src/octoagent/gateway/main.py` | 修改（+include_router）|
| App.tsx | `frontend/src/App.tsx` | 修改（+路由）|
| WorkbenchLayout.tsx | `frontend/src/components/shell/WorkbenchLayout.tsx` | 修改（+NavLink）|
| FilesCenter.tsx | `frontend/src/pages/FilesCenter.tsx` | 新建 |
| DiffView.tsx | `frontend/src/components/files/DiffView.tsx` | 新建 |
| files.ts | `frontend/src/api/files.ts` | 新建 |
| artifact.py | `packages/core/src/octoagent/core/models/artifact.py` | **不改**（方案 A 主表零变更）|
