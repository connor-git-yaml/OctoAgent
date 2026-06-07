# F104 文件工作台 v0.1（diff 视图）— Completion Report

**Feature ID**: F104
**Feature Branch**: `feature/104-file-workbench-v01`
**Baseline**: `da947ce`（M5 收口 commit；F104 0 regression 基线）
**M6 阶段**: M6 第 1 个 Feature（Surface 扩张首站）
**Downstream**: F107 文件工作台 v0.2（git-aware：branch/commit/blame + behavior 文件版本可视化）
**完成日期**: 2026-06-07
**报告基准**: trace.md（全程 Phase 0-5 + Final review + 方案 B 3 轮 review，最权威事实源）

---

## 1. 概述

F104 是 **M6 Surface 扩张的第一个 Feature**。目标：在 Web Workbench 新增 Files Tab，对有多版本的 versionable 逻辑文件提供 git 风格的"上一版 vs 当前版"diff 视图，让非技术用户一眼看出新增/删除/修改的行（Constitution #8 Observability is a Feature）。

**性质**：F104 不是纯 UI Feature——块 A 实测侦察确认 artifact 旧版本当前**不可取**（`put_artifact` INSERT-only / ULID PK / `version` 字段只是静态计数器），要做"上一版 vs 当前版"必须**先在 backend 保留历史内容**（印证 F103d handoff §6 纠偏）。因此 F104 = **backend 版本保留改造（append-only `artifact_versions` 历史表）+ frontend Files Tab 两级导航 + jsdiff diff 视图**。

**守 H1/H2/H3**：F104 是 surface 层，不触碰主 Agent / Worker / Subagent 协作模型。

**交付物**：
- 后端 `artifact_versions` append-only 历史表（schema + 4 查询方法 + 级联删除）
- `put_artifact` 加 `versionable` / `logical_file_id` 参数（默认 False，0 regression）
- Files HTTP API（4 endpoint，front-door 路由级鉴权）
- 前端 Files Tab 两级导航（task → 逻辑文件 → diff）+ jsdiff diffLines 行级高亮 + Advanced 折叠

---

## 2. Phase 实际 vs 计划

### 块 A（实测侦察 = Phase 1b tech-research 强化）

| 项 | 计划 | 实际 |
|----|------|------|
| 方法 | 调研 diff 库选型 | 主 session 主导 + 2 Explore 子代理并行（backend artifact/snapshot + frontend Workbench）+ 主 session 亲自核实 `artifact_store.py` |
| 核心结论 | — | artifact 旧版本**不可取**（INSERT-only / ULID PK / 静态计数器）→ 确认必须动 backend；artifact **无逻辑文件概念**（每次 put = 新 ULID 独立行）→ 需定义"逻辑文件身份"；SnapshotStore **无 diff/history**（仅 prefix-cache）不可复用；前端 React 19 + 纯手工 CSS **无 diff 库** |
| 关键决策 | — | 用户拍板 **方案 A**（append-only `artifact_versions` 表）+ **jsdiff + 自建 CSS**（契合纯手工 CSS + 非技术用户 UX） |

**偏离**：块 A 把"必须动 backend"从 handoff 纠偏假设升级为实测确认——这直接决定 F104 不是纯 UI Feature，spec 第一决策点（版本存储方案）由此而来。

---

### Phase 1 — 后端版本表 + put_artifact versionable append（数据地基）

| 维度 | 计划 | 实际 |
|------|------|------|
| schema | `artifact_versions` DDL + UNIQUE(task_id,logical_file_id,version_no) MUST | ✅ 落地（version_id ULID PK + storage_kind inline/storage_ref + content/storage_ref/size/hash + FK task_id + UNIQUE）|
| 事务模型 | plan §1.2 `BEGIN IMMEDIATE` 包主表 + 版本 | ❌ **T1.3 硬 gate 实测推翻**：默认 `isolation_level=''`，主表 INSERT 已开隐式事务，再 `BEGIN IMMEDIATE` 报 "cannot start a transaction within a transaction"→ 改 **`_write_lock`（asyncio.Lock）串行化 + 隐式事务 + SAVEPOINT 重试**（phase-1-recon.md 权威）|
| put_artifact | versionable 自包含事务 | ✅ 自包含（in_transaction 检查 raise + 文件写移入锁 + 失败 unlink-if-new + O_EXCL 原子独占）|
| 失败事件 | `ARTIFACT_VERSION_APPEND_FAILED` emit | ✅ enums + payload + StoreGroup 注入 event_store + `append_event_committed` 独立提交 |
| progress_note | user step 传 versionable=True | ✅ 接入；`_maybe_merge_old_notes` 不动（SD-9 排除 `__merged_history__`）|
| session 级联 | `delete_artifact_versions_by_task_ids` 事务内 | ✅ session_delete 邻接接入 |

**关键偏离（事务模型整条线）**：
- T1.3 实测推翻 plan 原 BEGIN IMMEDIATE 方案 → 改 `_write_lock` + 隐式事务 + SAVEPOINT。
- **后续 Final review 用户选 B**：versionable 路径再从"共享主连接隐式事务"改为 **独立 `versionable_conn`**（autocommit + BEGIN IMMEDIATE + 连接级写隔离）——彻底解决 mixed-writer（详见 §5）。

**测试**：34 passed（SAVEPOINT 重试 / 失败回滚 / 级联 / versionable 断言）；core+tooling 656 passed 0 regression。
**Commit**: `6fa4010`（746 回归 0 regression + e2e_smoke 8/8 + 3 轮 review 闭环）。

---

### Phase 2 — 后端查询 + HTTP API

| 维度 | 计划 | 实际 |
|------|------|------|
| 查询方法 | 4 方法（list_versions / get_current_and_previous / list_versionable_files_for_task / list_tasks_with_versionable_files）| ✅ 全部实现 |
| oversize 处理 | 后端预判 `oversize` flag | ❌→✅ **3 轮 review 收敛**：事后打标（read 全量）→ 读前 size 元数据拦截 → storage_ref 文件存在检查优先于 oversize → **两阶段懒加载**（先元数据 size 判定，inline 未超阈值再读 content）→ 读前 stat 实际文件防 TOCTOU/stale |
| 三态语义 | available / unavailable | ✅ available / unavailable / oversize 三态分离（storage_ref 文件不存在 → unavailable 优先于 oversize）|
| API | routes/files.py 4 endpoint | ✅ front-door 路由级鉴权 + 主响应无技术字段 + `logical_file_id` query param 承载（含 `:`/`/`）|

**偏离**：oversize 拦截从 plan 的"后端 flag 预判"演进为"读前 size 元数据 + 两阶段懒加载 + TOCTOU 防护"——3 轮 review 推动（high→high→medium，0 high 残留）。
**Commit**: `c4f33d9`（8 files +1166/-17；40 Phase2 测试 + 773 全回归 0 regression + ruff All checks passed）。

---

### Phase 3 — 前端 Files Tab 两级导航

| 维度 | 计划 | 实际 |
|------|------|------|
| API 封装 | api/files.ts 4 fetch | ✅ 走内部 `apiFetch`（front-door 鉴权，非裸 fetch）|
| 两级导航 | FilesCenter task → 逻辑文件 → diff | ✅ 实现 + loading/error/empty 三态 + 面包屑回退 |
| race 保护 | （plan 未显式要求）| ✅ **review 推动新增**：request token（useRef 单调 requestSeq）+ 响应前 seq 校验 + 回退使在途失效 |
| 技术字段 | 主视图无 vN | ✅ 主标题改"上一版"/"当前版"去 vN（review 推动，version_no 留 Phase 4 Advanced）|

**偏离**：①CSS token 修正（误用未定义 token → 实际 token）；②`fetchLogicalFileVersions` 提供但未消费（版本时间线留 Phase 4）；③未做 preview 浏览器验证（tsc + vitest 替代，worktree 无 dev server）。
**Commit**: `4cad03f`（7 files +953/-1；FilesCenter 8 passed + 全 vitest 0 regression + tsc 0 错）。

---

### Phase 4 — DiffView jsdiff 行级高亮 + Advanced 折叠（F104 核心 diff 视图）

| 维度 | 计划 | 实际 |
|------|------|------|
| diff 生成 | jsdiff `diffLines` | ✅ `buildDiffLineRows`（diffLines + added/removed/unchanged 逐行 + trailing newline 剔除）|
| 行级渲染 | 新增绿/删除红/未变中性 | ✅ `--cp-success-soft` + / `--cp-danger-soft` - / unchanged + data-diff-kind + useMemo |
| 降级分支 | 无差异/二进制/超大/不可用 | ✅ **6 降级分支**：binary / oversize / current null / 首版 / previous null / 无差异（FR-015）|
| Advanced 折叠 | 技术字段 details 折叠 | ✅ AdvancedVersionMeta（details 默认收起 + onToggle 懒加载 fetchLogicalFileVersions + loadedRef 单次 + seqRef 独立 race）；技术字段 vN/hash前8/size/storage_kind 仅此区 |

**偏离**：首版保留文案 + previous content=null 退回当前内容纯展示（FR-010 合理新增分支，plan 仅列 5 分支，实际 6）。
**Commit**: `40a708c`（5 files +686/-57；含 `diff@9` + `@types/diff` 依赖；FilesCenter 14 passed + 全 vitest 0 regression + tsc 0）。

---

### Phase 5 — 边界/并发/负向/失败注入测试 + Final review

| 维度 | 计划 | 实际 |
|------|------|------|
| 并发测试 | put_artifact 同 key 无重复版本号 + mixed-writer 条件化 | ✅ 12 次同 key 版本号连续 1..N + 16 inline/storage_ref 混合串行化 + 独立 lfid 序列 + mixed-writer（方案 B 后**去 xfail 改必过**，双向隔离断言）|
| 负向测试 | `__merged_history__` / `tool_output:*` 非 versionable 不进 | ✅ + llm/chat-import 抽样 |
| 失败注入 | missing table / DB locked / UNIQUE 重试 / 版本写失败回滚 | ✅ DB locked durable 双轨 best-effort + 版本失败 ARTIFACT_VERSION_APPEND_FAILED durable |
| 大文件 | storage_ref 取回 + 删除占位 | ✅ task 存活期可取回 + session 删除后 unavailable 占位 |
| 单版本 | 100% 不进主列表 | ✅ 后端 + 前端双侧断言 |

**回归**：backend 790 passed + 1 xfailed（方案 B 前）→ 方案 B 后 791 passed（worktree 真实代码，PYTHONPATH 锁定）+ e2e_smoke 8/8 + 前端 15 passed = 0 regression。

---

## 3. Codex Review 闭环表

| Phase / 节点 | 轮次 | finding（severity） | 处理 |
|------|------|---------|------|
| **Phase 1** | round 1 | [high] versionable 未检查 `in_transaction` → 污染调用方共享连接事务 / [medium] 文件写在锁外 + 失败不清理 | in_transaction 检查 raise + 文件写移入 `_write_lock` + 失败 unlink-if-new |
| | round 2 | [high] versionable 大文件失败路径覆盖既有文件内容 | 写前 exists 则 raise 拒绝覆盖 + `_process_content` 移入 try + 失败 unlink 本次新建 + test_existing_file_not_overwritten |
| | round 3 | [high#1] in_transaction 入口检查不阻止并发默认写加入 versionable 事务（= mixed-writer）/ [high#2] 大文件 exists() TOCTTOU + 失败清理跨 writer | **用户拍板折中**：high#2 修 O_EXCL 原子独占 + owns_file 标记；high#1 **归档**（mixed-writer = GATE_TASKS 拍板实测驱动，T1.3 实测顺序队列 v0.1 不触发，真并发正确性 = 连接级写锁架构 follow-up）|
| **Phase 2** | round 1 | [high] oversize 事后打标仍 read_bytes 全量读 → FR-019/SC-005 后端失效 / [medium] slash logical_file_id path 参数不匹配 | oversize 读前 size 元数据拦截 + logical_file_id path→query + 顺手清 Phase 1 6 E501 |
| | round 2 | [high] oversize 短路在 storage_ref 文件存在检查之前 → 已删文件误报 available+oversize 而非 unavailable / [medium] get_current_and_previous 一次性 SELECT content 超大 inline 已读入再标 oversize | storage_ref 先文件存在检查再 oversize（优先级）+ 两阶段懒加载（先元数据 size 判定，inline 未超阈值再读 content）|
| | round 3 | 0 high + 1 medium（storage_ref oversize 信任陈旧 DB size，read_bytes 前未 stat → TOCTOU/metadata stale 可绕过 FR-019）| 主 session 自改 stat 实际大小 `max(DB size, actual)` 判 oversize + 补 TOCTOU 测试（spy read_bytes_called==0）+ 清 SIM105 |
| **Phase 3** | round 1 | [high] openTask/openFile 异步竞态（旧响应无条件覆盖新选择）/ [medium] 主 diff 标题暴露内部版本号 vN | request token（useRef 单调 seq）+ 响应前 seq 校验 + 回退失效 + 2 乱序测试；vN 移除标题改"上一版"/"当前版" → **re-review approve** |
| **Phase 4** | round 1 | 0 high + 1 medium（FR-015 无差异分支缺失：相同内容当普通 diff 全 unchanged 行）| DiffBody 渲染前加 current.content===previous.content 无差异空态 + 2 测试（0 high 无需单独 re-review，Final 覆盖）|
| **Final cross-Phase** | round 1 | [high] versionable 写锁不隔离并发默认写（破坏 FR-004/FR-021）/ [medium] `__merged_history__` step_id 可绕过 SD-9 / [medium] DiffSide 主响应暴露 version_no/storage_kind | **high → 用户选 B 真修连接级写隔离**；2 medium：execute_progress_note step_id guard + 负向测试 / DiffSide 移除 version_no/storage_kind 仅留 content/availability/oversize |
| **方案 B（连接级写隔离）** | round 1 | [high] versionable_conn 未开 foreign_keys（连接级）→ 孤儿 + 行为分裂 / [high] 失败事件用主连接 commit → 卷入主连接未提交默认写 / [medium] versionable_conn=None 退化静默 / [medium] 测试 200+ teardown 只关主连接 | apply_write_connection_pragmas helper（FK=ON）+ 失败事件改用 versionable_conn commit + `_versionable_isolated` 退化拒绝 + teardown 272 处改 `store_group.close()` |
| | round 2 | [high] DB locked 场景失败事件吞 durable（versionable_conn 同锁 → 失败事件也写不进）/ [medium] teardown 86 处漏扫 | 务实降级：spec FR-021/SD-10 双轨措辞（structlog always durable + event best-effort，SQLite 单写锁物理限制 outbox 超 v0.1）+ locked-path 测试 + teardown 88 处/21 文件全仓 close() |
| | round 3 | 0 high + 2 medium（structlog "always durable 文件日志" 过度宣称——实测 logging_config 仅 StreamHandler 无文件 sink / plan.md 仍旧 FR-021 契约未同步）| 诚实降级 "best-effort local log" + plan 同步 + 三文件闭合 grep（0 high，文档闭合可收口）|

**轮次统计**：
- Phase 1：3 轮（共 4 high + 1 medium，high#1 归档 → Final 重提 → 方案 B 真修）
- Phase 2：3 轮（high→high→medium，0 high 残留）
- Phase 3：1 轮 → re-review approve
- Phase 4：1 轮（0 high + 1 medium）
- Final cross-Phase：1 轮（1 high + 2 medium）
- 方案 B：3 轮（2 high → 1 high → 0 high）
- **spec/plan 阶段另有**：spec 5 轮 review（11 finding 1C+5H+5M 全闭环，round5 APPROVE）+ plan+tasks 2 轮（1C+2H ×2）

**implement 阶段 high 全闭环，0 high 残留。**

---

## 4. FR / SC 覆盖确认

### FR 覆盖（引用 tasks.md FR×Task 矩阵）

**21/22 实现 + FR-020 deferred（YAGNI）**：

- FR-001~FR-005（后端版本保留）：✅ T1.2/T1.4/T1.5/T1.6/T1.8/T1.8t
- FR-006~FR-010（后端版本查询）：✅ T2.1/T2.2/T2.3/T2.4/T2.5/T4.3/T4.4/T5.5
- FR-011~FR-015（前端 Files Tab + diff）：✅ T3.2/T3.3/T4.2/T4.3/T4.4/T5.6
- FR-016/FR-017（友好展示 + 技术字段折叠）：✅ T2.3/T2.5/T4.3/T4.4
- FR-018/FR-019（二进制/超大降级）：✅ T2.3/T4.3/T4.4
- **FR-020**（hash 去重 MAY）：⏸ **deferred**（YAGNI，v0.1 不实现，不排 task）
- FR-021（同事务原子 + fail-fast + 失败 emit）：✅ T1.3/T1.4/T1.7/T5.4
- FR-022（versionable 判定在写入方）：✅ T1.4/T1.8/T5.2/T5.3

### SC 覆盖（6/6）

- SC-001 ≤2 次点击到 diff：✅ T3.3
- SC-002 小文件 100% 重启可取回 / 大文件 best-effort：✅ T1.6/T5.5
- SC-003 全量回归 ≥ baseline 0 regression：✅ T1.10/T2.7/T3.5/T4.6/T5.8
- SC-004 主列表技术字段出现 0 次：✅ T2.5/T4.4（+ Final review DiffSide 移除 version_no/storage_kind 加固）
- SC-005 阈值内文本 1 秒渲染，超阈值降级：✅ T4.2/T4.3/T4.4
- SC-006 单版本逻辑文件 100% 不进主列表：✅ T3.3/T5.6

---

## 5. 关键决策 / 偏离 / 已知限制

### 关键决策

1. **用户选 B（撤销 GATE_TASKS mixed-writer 推迟，真修连接级写隔离）**——与 F098"用户选 B 撤销推迟"同 pattern。GATE_TASKS 原拍板 mixed-writer = 实测驱动（T1.3 实测顺序队列 v0.1 不触发，真并发正确性归架构 follow-up）；Final review 重提 high 后用户选 B：versionable 走**独立 `versionable_conn`**（autocommit `isolation_level=None` + 手动 `BEGIN IMMEDIATE` 拿 SQLite 写锁 + busy_timeout=5000 跨连接串行 + `_write_lock` 串行化连接访问）→ versionable commit/rollback 只影响独立连接，不卷入主连接默认 versionable=False 写，mixed-writer 在事务层面物理消解。

2. **事务模型两次演进**：plan §1.2 `BEGIN IMMEDIATE`（默认配置不可行，T1.3 实测推翻）→ `_write_lock` + 隐式事务 + SAVEPOINT（Phase 1）→ 独立 `versionable_conn` + BEGIN IMMEDIATE（方案 B，autocommit 连接可用 BEGIN IMMEDIATE）。

3. **oversize 三态 + 两阶段懒加载**：storage_ref 文件不存在 → unavailable 优先于 oversize；inline 先读元数据 size 判定，未超阈值再懒加载 content；read 前 stat 实际文件防 TOCTOU。

### 已知限制（传递 F107）

1. **大文件 best-effort**：SD-8 混合方案——小文件 inline 存独立副本（SC-002 100% 重启可取回），大文件存 storage_ref 指针 + hash，session/task 删除清理后不可取回 → FR-010 unavailable 占位。v0.1 versionable 来源（progress-note）几乎都是小文本，主路径走 inline。

2. **FR-020 版本去重 deferred（YAGNI）**：v0.1 优先正确性与 0 regression，hash 内容去重推后 F107 或未来。

3. **DB locked 失败信号双 best-effort**：versionable BEGIN IMMEDIATE busy 失败时 versionable_conn 同锁 → 失败 event 也写不进（SQLite 单写锁物理限制）。双轨均 best-effort：① structlog.warning（best-effort local log——实测 logging_config 仅挂 StreamHandler 输出 stderr，**无独立文件/审计 sink**，可见性取决于环境是否持久化进程流）；② `ARTIFACT_VERSION_APPEND_FAILED` event（best-effort durable，DB 可写时 versionable_conn emit；locked 时降级仅 structlog）。outbox/延迟重试是过度工程（超 v0.1）。

4. **主连接 FK 历史 OFF**：实测主连接 `foreign_keys` 是 OFF（历史缺陷——`_migrate_legacy_tables` 临时关 + finally no-op）；方案 B `versionable_conn` 显式 FK=ON（孤儿防护）。主连接不动以避 7 regression（收窄不扩散，F107 评估）。

5. **worktree venv symlink 主仓 gotcha**：worktree `.venv` 是 symlink → 主仓 `.venv`（Jun6 并发操作），主仓 editable 指向主仓 master src → 裸 pytest 跑 master 代码（ImportError connection/MAX_VERSION_RETRY）。验证须用 `PYTHONPATH` 锁 worktree 全 packages/apps src。worktree venv 独立化是环境 follow-up（不阻 F104，git commit 内容不受 venv 影响）。

6. **11 pre-existing master 前端测试债**：Phase 3 后全 vitest 11 failed/170 vs baseline(stash -u) 11 failed/162——**11 failed 数量相同 = pre-existing master 前端测试债（与 F104 无关），F104 引入 0 regression**（新增文件全 passed）。

### 工程偏离

- 方案 B `versionable_conn` 可选默认 None 退化到主 conn（兼容 watchdog 直接构造 StoreGroup；退化时 `_versionable_isolated=False` 显式拒绝 versionable 写，不静默污染）。
- 测试 teardown 全仓 272 + 88 处改 `store_group.close()`（双连接关闭），grep 0 残留。

---

## 6. Commit 链

| Phase | Commit | 说明 |
|-------|--------|------|
| spec | `b65ed17` | 块A侦察 + GATE_DESIGN + Codex 5 轮 review 闭环 |
| plan+tasks | `787e1a1` | Codex 2 轮 review 事务正确性闭环 + GATE_TASKS 通过 |
| Phase 1 | `6fa4010` | artifact_versions 表 + put_artifact versionable 自包含事务 + progress_note 接入 + session 级联 |
| Phase 2 | `c4f33d9` | 后端查询 + Files HTTP API（两级导航 + diff + oversize 读前拦截）|
| Phase 3 | `4cad03f` | Files Tab 两级导航前端（task→逻辑文件→diff 基础视图）|
| Phase 4 | `40a708c` | DiffView jsdiff 行级高亮 + Advanced 版本元信息折叠 |
| Final | （本提交）| 方案 B 连接级写隔离 + 边界/并发/负向验证 + completion-report + handoff |

**不主动 push origin/master**，等用户拍板（CLAUDE.local.md §Spawned Task 处理流程）。
