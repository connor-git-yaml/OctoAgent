# F104 文件工作台 v0.1（diff 视图）— Tasks

**Spec**: [spec.md](spec.md)（22 FR / 6 SC / 5 Story / 10 SD，Codex 5 轮 approve / 0 HIGH 定稿）
**Plan**: [plan.md](plan.md)（5 Phase，MEDIUM 复杂度，命中"数据库 schema 新增"重大架构变更节点）
**Baseline**: `da947ce`（M5 收口 commit；F104 0 regression 基线）
**生成日期**: 2026-06-06
**M6 阶段**: M6 第 1 个 Feature（Surface 扩张首站）

---

## 0. 概览

| 指标 | 值 |
|------|-----|
| **总 Task 数** | 37（含 5 Codex review + 5 回归 gate；Phase1 含 T1.8t）|
| **Phase 分布** | Phase1=11 / Phase2=7 / Phase3=5 / Phase4=6 / Phase5=8 |
| **FR 覆盖** | 21/22 实现 + FR-020 deferred（YAGNI）|
| **SC 覆盖** | 6/6 |
| **CRITICAL/HIGH 风险 Task** | T1.3（CRITICAL 事务所有权 + mixed-writer 实测）/ T1.4（HIGH 自包含事务 + SAVEPOINT + durable）|

**Phase 执行顺序**：Phase1（数据地基）→ Phase2（后端查询+API）→ Phase3（前端两级导航）→ Phase4（前端 diff 视图）→ Phase5（边界/并发/负向验证 + Final review）。
前端 Phase3/4 在后端 Phase2 完成后可与边界验证交错。

**回归基线命令**（每 Phase 后，用托管实例避免环境污染）：
```bash
cd /Users/connorlu/Desktop/.workspace2.nosync/OctoAgent/.claude/worktrees/F104-file-workbench/octoagent
uv run --no-sync python -m pytest -x -q --tb=short -p no:cacheprovider
uv run --no-sync python -m pytest -m e2e_smoke -x -q --tb=short
```

**关键文件路径索引**（worktree 相对 `octoagent/`）：

| 简称 | 路径 | 操作 |
|------|------|------|
| artifact_store | `packages/core/src/octoagent/core/store/artifact_store.py` | 修改 |
| sqlite_init | `packages/core/src/octoagent/core/store/sqlite_init.py` | 修改 |
| session_delete | `packages/core/src/octoagent/core/store/session_delete.py` | 修改 |
| enums | `packages/core/src/octoagent/core/models/enums.py` | 修改 |
| payloads | `packages/core/src/octoagent/core/models/payloads.py` | 修改（+失败事件 payload）|
| store __init__ | `packages/core/src/octoagent/core/store/__init__.py` | 修改（StoreGroup 注入 event_store 到 artifact_store）|
| artifact_version | `packages/core/src/octoagent/core/models/artifact_version.py` | 新建 |
| progress_note | `packages/tooling/src/octoagent/tooling/progress_note.py` | 修改 |
| files route | `apps/gateway/src/octoagent/gateway/routes/files.py` | 新建 |
| main | `apps/gateway/src/octoagent/gateway/main.py` | 修改 |
| App.tsx | `frontend/src/App.tsx` | 修改 |
| WorkbenchLayout | `frontend/src/components/shell/WorkbenchLayout.tsx` | 修改 |
| FilesCenter | `frontend/src/pages/FilesCenter.tsx` | 新建 |
| DiffView | `frontend/src/components/files/DiffView.tsx` | 新建 |
| files api | `frontend/src/api/files.ts` | 新建 |

---

## Phase 1 — 后端版本表 + put_artifact versionable append（数据地基）

**目标**：`artifact_versions` 历史表落地 + `put_artifact` versionable append + 版本号原子分配 + progress_note 接入 + session 级联 + 失败事件。
**关键风险**：BEGIN IMMEDIATE 与调用方隐式事务的协调（plan §4 标 HIGH）。
**回归 gate**：T1.10。**Codex review**：T1.9（per-Phase，重点事务边界）。

---

### T1.1: 新建版本返回类型 model

- **Phase**: 1
- **详情**: 新建 `artifact_version.py`，定义 Pydantic BaseModel：`ArtifactVersionMeta`（version_no/ts/size/hash/storage_kind）、`ArtifactVersionContent`（version_no/content/storage_kind/`availability: Literal["available","unavailable"]`/size/hash）、`LogicalFileSummary`（logical_file_id/version_count/display_name 可选）。完整类型注解。`availability` 字段服务 FR-010。
- **改动文件**: artifact_version（新建）
- **依赖**: -
- **覆盖**: FR-006/FR-007/FR-010（返回契约基础）
- **测试**: 随后续方法 unit 验证

---

### T1.2: artifact_versions DDL + 索引 + 注册

- **Phase**: 1
- **详情**: 在 sqlite_init 加 `_ARTIFACT_VERSIONS_DDL`（plan §1.1 schema：version_id PK / task_id / logical_file_id NOT NULL / version_no / artifact_id / ts / storage_kind / content / storage_ref / size / hash + `FOREIGN KEY(task_id)` + `UNIQUE(task_id,logical_file_id,version_no)`），仿 `worker_profile_revisions` 范式（CREATE TABLE IF NOT EXISTS）。加 `_ARTIFACT_VERSIONS_INDEXES`（idx_artifact_versions_logical = `(task_id,logical_file_id,version_no DESC)` + idx_artifact_versions_task = `(task_id)`）。在 init 注册点（:1660 邻接）`await conn.execute(_ARTIFACT_VERSIONS_DDL)`，索引循环加 `_ARTIFACT_VERSIONS_INDEXES`。
- **改动文件**: sqlite_init
- **依赖**: -
- **覆盖**: FR-001/FR-002（UNIQUE MUST）/FR-003（落盘）/FR-021（建表 fail-fast）
- **测试**: T1.5 验证老库自动建空表（0 regression）+ 表结构

---

### T1.3: 【CRITICAL】事务所有权模型 + mixed-writer 并发实测

- **Phase**: 1
- **详情**: **独立实测 task**（Codex plan-review critical）。实测 ①aiosqlite 默认 `isolation_level` + 单连接（StoreGroup 共享）下事务行为；②验证 plan §1.2 **自包含事务**方案：versionable 路径 `async with self._write_lock` 内 `BEGIN IMMEDIATE → 主表 INSERT → SAVEPOINT 版本 → commit/rollback`（**自 commit、不依赖调用方**）；③**关键：mixed-writer 实测**——OctoAgent 实际写并发是单 task 顺序队列还是多 task 真并发共享连接？构造 versionable 写 + 默认 put_artifact 写 + 外部 commit/rollback **交错**场景，验证是否互相提交/污染。结论写 `phase-1-recon.md`（isolation_level + 写并发模型 + mixed-writer 是否触发 + 选定方案 + 残留约束）。若真并发触发污染 → 升级默认路径自 commit 或连接级事务管理（评估范围，可能架构 follow-up）。**硬 gate**：本实测结论是 Phase 1 是否升级 + T5.1 mixed-writer 测试形态的**决定项，不得跳过**（Codex re-review high #3）。
- **改动文件**: artifact_store（实测脚本，结论入文档）
- **依赖**: T1.2
- **覆盖**: FR-021（自包含原子前提）/FR-002（写锁）/FR-004（默认路径 0 regression）
- **测试**: 集成测：默认 versionable=False 与 baseline 等价 + mixed-writer 交错不污染（延 T5.1）
- **风险**: **CRITICAL** — Codex plan-review 重点；mixed-writer 范围取舍 GATE_TASKS 用户确认

---

### T1.4: 【HIGH】put_artifact versionable 自包含事务 + SAVEPOINT 重试 + best-effort 失败信号

- **Phase**: 1
- **详情**: `put_artifact` 签名加 `*, versionable: bool = False, logical_file_id: str | None = None`（plan §1.2）；artifact_store `__init__` 加 `self._write_lock = asyncio.Lock()`。逻辑：①versionable 路径主表 INSERT 移入自包含事务；默认 `versionable=False` 路径不变（FR-004）；②`if not versionable: return`（默认完全不碰版本表，SD-3）；③`versionable=True` 但 `logical_file_id` 空 → `raise ValueError`（无 name 回退，SD-1/Codex round2 #5）；④计算版本行 storage_kind/content/storage_ref/hash（SD-8 混合，复用主表已算值）；⑤**自包含事务**（Codex plan-review critical，按 T1.3 选定方案）：`async with self._write_lock:` → `BEGIN IMMEDIATE`（一次）→ 主表 INSERT → `for attempt in range(3)`: `SAVEPOINT sp_ver` → `MAX(version_no)+1` → 版本 INSERT → 冲突 `ROLLBACK TO sp_ver` 重试（**不重 BEGIN**）/ 成功 `RELEASE sp_ver` → `commit`（**自 commit**）；except → `rollback` + **事务外** emit `ARTIFACT_VERSION_APPEND_FAILED`（双 best-effort 信号：structlog best-effort local log + DB 可写时 versionable_conn best-effort durable event，详见 T1.7）+ raise。顺手更新过时文件头 docstring。
- **改动文件**: artifact_store
- **依赖**: T1.1, T1.2, T1.3
- **覆盖**: FR-001/FR-002/FR-005/FR-021/FR-022
- **测试**: T1.5（unit：MAX+1 单调 / inline vs storage_ref / 空 logical_file_id raise / SAVEPOINT 冲突重试成功时 artifacts+versions 各 1 行匹配）；并发/失败注入延 T5.1/T5.4
- **风险**: **HIGH** — Codex review 重点（自包含事务正确性 + SAVEPOINT 重试 + best-effort 失败信号 + 0 regression 默认路径）

---

### T1.5: put_artifact 版本逻辑 unit 测试

- **Phase**: 1
- **详情**: unit 测：①连续 3 次 `versionable=True` 写入同 key → 3 版本、version_no 单调递增且唯一（Story3 AC-1）；②inline 小文件 → `storage_kind='inline'` + content 副本非空；storage_ref 大文件 → `storage_kind='storage_ref'` + content=NULL + storage_ref/hash 非空（SD-8 两分支）；③`versionable=True` + 空 `logical_file_id` → raise ValueError（SD-1，无 name 回退）；④老库（无版本表场景前由 DDL 兜底）启动自动建空表。
- **改动文件**: `packages/core/tests/store/test_artifact_versions.py`（新建）
- **依赖**: T1.4
- **覆盖**: FR-001/FR-002/FR-022（断言）；Story3 AC-1
- **测试**: 本 task 即测试

---

### T1.6: 集成测 — 同事务原子 + 0 regression 默认路径 + 重启取回

- **Phase**: 1
- **详情**: 集成测：①默认 `versionable=False` 路径写入 → 版本表 0 行，artifacts 主表行为与 baseline da947ce 等价（FR-004，Story3 AC-3）；②版本 INSERT 与主表 INSERT 同事务——模拟版本写失败 → 主表也回滚（FR-021，延伸失败注入到 T5.4）；③进程重启（重建 StoreGroup）后查询小文件版本内容可取回（FR-003，Story3 AC-2）。
- **改动文件**: `packages/core/tests/store/test_artifact_versions.py`
- **依赖**: T1.4
- **覆盖**: FR-003/FR-004/FR-021；Story3 AC-2/AC-3
- **测试**: 本 task 即测试

---

### T1.7: ARTIFACT_VERSION_APPEND_FAILED EventType + payload + emit wiring

- **Phase**: 1
- **详情**（Codex re-review high #2 + 方案 B 修正，明确可实现 emit 路径）：①enums 加 `ARTIFACT_VERSION_APPEND_FAILED` EventType；payloads 加对应 payload（task_id / logical_file_id / reason / attempt）；②**wiring**：StoreGroup 构造**注入 `event_store` 到 `SqliteArtifactStore`**；失败 event 走**独立 versionable 写连接**（`append_event_committed(failed_event, conn=versionable_conn)`，方案 B / Codex 修复 2——versionable 失败已 rollback 干净，不卷主连接事务，避免 mixed-writer 转移到失败路径）；③put_artifact versionable 失败时（T1.4）rollback 后双轨 best-effort：`structlog.warning`（best-effort local log，仅 StreamHandler 输出进程 stderr，无独立文件/审计 sink）+ DB 可写时 `event_store.append_event_committed(failed_event, conn=versionable_conn)`（event: task_id=artifact.task_id / actor=SYSTEM / payload，best-effort durable，DB locked 时写不进、降级仅 structlog）。
- **改动文件**: enums, payloads, `store/__init__.py`（StoreGroup 注入 event_store + versionable_conn 到 artifact_store）, artifact_store
- **依赖**: T1.4
- **覆盖**: FR-021（可观测 best-effort）；Constitution #8
- **测试**: T5.4 断言 DB 可写场景 events 表确有该事件（走 versionable_conn）+ structlog best-effort local log；locked 场景 event 不强求

---

### T1.8: progress_note user step 接入 versionable=True + session_delete 级联

- **Phase**: 1
- **详情**: ①progress_note user step 写入（:134）改为 `put_artifact(artifact, content_json.encode("utf-8"), versionable=True, logical_file_id=f"progress-note:{input_data.step_id}")`（FR-022 / SD-9）；`_maybe_merge_old_notes` 合并写入（:290）**不动**（默认 versionable=False，SD-9 排除 `__merged_history__`）。②artifact_store 加 `delete_artifact_versions_by_task_ids(task_ids) -> int`（CL-3 级联，append-only 唯一例外）；session_delete `:83` 邻接（事务内、commit 前）加该级联调用。
- **改动文件**: progress_note, artifact_store, session_delete
- **依赖**: T1.4
- **覆盖**: FR-005（级联例外）/FR-022/SD-9；CL-3
- **测试**: T1.8t（unit：user step 传 versionable=True / 合并不传）；级联 + 负向 __merged_history__ 延到 T5.2/T5.3

---

### T1.8t: progress_note 接入 + 级联 unit/集成测

- **Phase**: 1
- **详情**: ①unit：progress_note user step 调用 put_artifact 时 `versionable=True` 且 `logical_file_id="progress-note:{step_id}"`；`_maybe_merge_old_notes` 调用时 `versionable` 默认 False（SD-9）。②集成：删 task → `delete_artifact_versions_by_task_ids` 清版本表无孤儿（CL-3）；版本副本独立于主表（artifacts 行删/合并不影响 inline 版本内容）。
- **改动文件**: `packages/tooling/tests/test_progress_note_versionable.py`（新建）+ `packages/core/tests/store/test_artifact_versions.py`
- **依赖**: T1.8
- **覆盖**: FR-005/FR-022/SD-9；CL-3
- **测试**: 本 task 即测试

---

### T1.9: Phase 1 Codex per-Phase review

- **Phase**: 1
- **详情**: Codex adversarial review（foreground 或 background）。**重点**：BEGIN IMMEDIATE 与调用方 commit 的事务边界协调 + 嵌套事务报错风险（T1.3 结论）+ UNIQUE 冲突重试正确性 + 默认 versionable=False 路径 0 regression 彻底性。处理 finding（high/medium 闭环，low 可归档）。
- **改动文件**: -（review）
- **依赖**: T1.1–T1.8t
- **覆盖**: 全 Phase1 FR 复核
- **测试**: -

---

### T1.10: Phase 1 回归 gate

- **Phase**: 1
- **详情**: 跑全量回归 + e2e_smoke（§0 命令）。断言 passed ≥ da947ce baseline，0 regression；e2e_smoke 8/8。Commit：`feat(F104-Phase-1): artifact_versions 表 + put_artifact versionable append + 版本号原子分配 + progress_note 接入 + session 级联`。
- **改动文件**: -（验证 + commit）
- **依赖**: T1.9
- **覆盖**: FR-004/SC-003
- **测试**: 全量回归 + e2e_smoke

---

## Phase 2 — 后端查询 + HTTP API

**目标**：4 查询方法 + FR-010 占位 + 二进制/超大后端预判 + `routes/files.py` 4 endpoint + main 挂载。
**回归 gate**：T2.7。**Codex review**：T2.6。

---

### T2.1: 4 个版本查询方法

- **Phase**: 2
- **详情**: artifact_store 加 4 方法（plan §1.3）：①`list_versions(task_id, logical_file_id) -> list[ArtifactVersionMeta]`（FR-006，ORDER BY version_no DESC, ts DESC）；②`get_current_and_previous(task_id, logical_file_id) -> tuple[ArtifactVersionContent|None, ...]`（FR-007，inline→content / storage_ref→读文件，文件不存在/被清理→`availability='unavailable'` FR-010 占位不抛异常 / <2 版本→previous=None）；③`list_versionable_files_for_task(task_id) -> list[LogicalFileSummary]`（FR-008 第二级，`GROUP BY logical_file_id HAVING COUNT(*)>=2` SD-4）；④`list_tasks_with_versionable_files() -> list[str]`（FR-008 第一级 SD-7，DISTINCT task_id of version≥2 逻辑文件）。
- **改动文件**: artifact_store
- **依赖**: T1.4
- **覆盖**: FR-006/FR-007/FR-008/FR-010
- **测试**: T2.2

---

### T2.2: 4 查询方法 unit 测试 + FR-010 占位

- **Phase**: 2
- **详情**: unit：①`list_versions` 排序正确；②`get_current_and_previous`：<2 版本→previous=None；storage_ref 文件被删→`availability='unavailable'` 不抛异常（FR-010 / Edge Case）；③`list_versionable_files_for_task` 只返回 version≥2 逻辑文件（SD-4/FR-012，单版本不返回）；④`list_tasks_with_versionable_files` 只含有 version≥2 逻辑文件的 task。
- **改动文件**: `packages/core/tests/store/test_artifact_versions.py`
- **依赖**: T2.1
- **覆盖**: FR-006/FR-007/FR-008/FR-010（断言）；SD-4
- **测试**: 本 task 即测试

---

### T2.3: routes/files.py 4 endpoint

- **Phase**: 2
- **详情**: 新建 `routes/files.py`（plan §1.5），handler 用 `store_group=Depends(get_store_group)`：①`GET /api/files/tasks`（FR-008/SD-7，返回有多版本逻辑文件的 task 清单，title 可从 task_store 补）；②`GET /api/files/tasks/{task_id}/logical-files`（FR-008/FR-012，version≥2 逻辑文件 + display_name + version_count）；③`GET .../logical-files/{logical_file_id}/diff`（FR-007/FR-013，current+previous 内容 + availability + storage_kind + `binary` + `oversize` flag）；④`GET .../logical-files/{logical_file_id}/versions`（可选，Advanced 区版本元信息）。**主响应不含** artifact_id/storage_ref/hash（FR-017/SC-004，归 /versions endpoint）。二进制/超大后端预判 `binary`/`oversize` flag（FR-018/FR-019 前端据此降级）。处理 `logical_file_id` 含 `:` 的路由解析（URL-encode 或改 query param，实测定）。
- **改动文件**: files route（新建）
- **依赖**: T2.1
- **覆盖**: FR-009/FR-010/FR-013/FR-017/FR-018/FR-019（后端 flag）
- **测试**: T2.5

---

### T2.4: main.py 挂载 files router（front-door 路由级鉴权）

- **Phase**: 2
- **详情**: main 加 `app.include_router(files.router, tags=["files"], dependencies=protected)`，复用 front-door token **路由级**鉴权（`require_front_door_access`），**不**在 handler 内写 Bearer 鉴权（plan §0.2 措辞校正 / Constitution #10）。
- **改动文件**: main
- **依赖**: T2.3
- **覆盖**: FR-009；Constitution #10
- **测试**: T2.5（鉴权断言）

---

### T2.5: HTTP API 集成测

- **Phase**: 2
- **详情**: 集成测：①4 endpoint 响应结构正确；②`/logical-files` 只返回 version≥2（SD-4/FR-012）；③front-door 鉴权生效（无 token 拒绝）；④主响应字段**不含** artifact_id/storage_ref/hash（SC-004 后端侧断言）；⑤`logical_file_id` 含 `:` 路由解析正确；⑥storage_ref 文件被删 → diff endpoint 返回 availability='unavailable' 不 500（FR-010）。
- **改动文件**: `apps/gateway/tests/routes/test_files.py`（新建）
- **依赖**: T2.4
- **覆盖**: FR-009/FR-010/FR-012/FR-017；SC-004
- **测试**: 本 task 即测试

---

### T2.6: Phase 2 Codex per-Phase review

- **Phase**: 2
- **详情**: Codex review。**重点**：FR-010 占位不抛异常 + version≥2 过滤 SQL 正确性 + 鉴权挂载（路由级非 handler）+ 技术字段是否泄漏进主响应（SC-004）。处理 finding。
- **改动文件**: -
- **依赖**: T2.1–T2.5
- **覆盖**: 全 Phase2 FR 复核
- **测试**: -

---

### T2.7: Phase 2 回归 gate

- **Phase**: 2
- **详情**: 全量回归 + e2e_smoke，0 regression / 8/8。Commit：`feat(F104-Phase-2): 版本查询方法 + Files API endpoints`。
- **改动文件**: -
- **依赖**: T2.6
- **覆盖**: SC-003
- **测试**: 全量回归 + e2e_smoke

---

## Phase 3 — 前端 Files Tab 两级导航

**目标**：Route + NavLink + FilesCenter 两级导航 + api/files.ts。
**回归 gate**：T3.5（前端 build + 后端回归不受影响）。**Codex review**：T3.4。

---

### T3.1: api/files.ts fetch 封装

- **Phase**: 3
- **详情**: 新建 `src/api/files.ts`，4 个 fetch 函数（listFileTasks / listLogicalFiles / getDiff / listVersions），复用 `client.ts` 错误处理范式（原生 fetch）。
- **改动文件**: files api（新建）
- **依赖**: T2.3（API 契约）
- **覆盖**: FR-009（前端消费）
- **测试**: T3.3 组件测

---

### T3.2: App.tsx 路由 + WorkbenchLayout NavLink

- **Phase**: 3
- **详情**: ①App.tsx 加 `const FilesCenter = lazy(...)` + `<Route path="files" .../>`（plan §0.2 :8-16/:68）；②WorkbenchLayout NavLink 数组（:430-459）加 `{to:"/files",label:"文件"}` + renderNavDescription 文案。
- **改动文件**: App.tsx, WorkbenchLayout
- **依赖**: -
- **覆盖**: FR-011（NavLink + 路由）
- **测试**: T3.3

---

### T3.3: FilesCenter 两级导航页

- **Phase**: 3
- **详情**: 新建 `src/pages/FilesCenter.tsx`（plan §1.6，SD-7/CL-2 两级导航）：第一级 task 列表（`GET /api/files/tasks`，参考 TaskList.tsx 选 task 模式）→ 第二级选中 task 的逻辑文件列表（`GET .../logical-files`，只显示 version≥2）。状态管理两级切换。友好命名占位（DiffView Phase4 整体验证 SD-5）。
- **改动文件**: FilesCenter（新建）
- **依赖**: T3.1, T3.2
- **覆盖**: FR-011/FR-012；SC-001（≤2 次点击：选 task + 点文件）；SC-006（单版本不进列表，前端侧）
- **测试**: 组件测：两级导航状态 + 单版本不可见（断言 version=1 条目不渲染，SC-006）；负向断言延到 T5.3

---

### T3.4: Phase 3 Codex per-Phase review

- **Phase**: 3
- **详情**: Codex review。**重点**：两级导航状态管理 + 单版本完全隐藏（SC-006）。处理 finding。
- **改动文件**: -
- **依赖**: T3.1–T3.3
- **覆盖**: FR-011/FR-012/SC-006 复核
- **测试**: -

---

### T3.5: Phase 3 回归 gate

- **Phase**: 3
- **详情**: 前端 build 通过；后端全量回归 0 regression（前端改动不影响后端测试）。Commit：`feat(F104-Phase-3): Files Tab 两级导航（Route + NavLink + FilesCenter）`。
- **改动文件**: -
- **依赖**: T3.4
- **覆盖**: SC-003
- **测试**: 前端 build + 后端回归

---

## Phase 4 — 前端 diff 视图

**目标**：DiffView（jsdiff + 自建 CSS）+ 友好命名映射 + 技术字段折叠 + 降级分支。
**回归 gate**：T4.6。**Codex review**：T4.5。

---

### T4.1: 引入 diff（jsdiff）依赖

- **Phase**: 4
- **详情**: `package.json` 加 `diff`（jsdiff）+ `@types/diff`（devDependency）。**不**引入 react-diff-viewer / diff2html（D-DIFF 约束）。
- **改动文件**: `frontend/package.json`
- **依赖**: -
- **覆盖**: D-DIFF 约束
- **测试**: build 验证

---

### T4.2: DiffView 组件 — jsdiff 生成 + 自建 CSS 行级渲染

- **Phase**: 4
- **详情**: 新建 `src/components/files/DiffView.tsx`（plan §1.6）：`GET .../diff` → jsdiff `diffLines(previous, current)`，自建 CSS 渲染新增行（绿）/删除行（红）/未变行（中性），tokens.css 配色。git 风格行级 diff。
- **改动文件**: DiffView（新建）, files api（补 diff fetch 若未补）
- **依赖**: T4.1, T3.1
- **覆盖**: FR-013（git 风格行级 diff）/FR-014（默认最新两版，无版本选择器）；Story1 AC-1/AC-2；SC-005（阈值内 1 秒渲染）
- **测试**: T4.4

---

### T4.3: 友好命名映射 + 技术字段折叠 + 降级分支

- **Phase**: 4
- **详情**: ①友好命名映射（SD-5/FR-016，`progress-note:{step_id}`→"进度笔记"；映射不到原样显示不报错）；②技术字段（artifact_id/版本号/storage_ref/hash）折叠到 `<details>` Advanced 区（FR-017/SC-004，主视图 0 次出现）；③降级分支：无差异提示（FR-015 / Story1 AC-3）/二进制提示（FR-018 / Story5 AC-1，据后端 binary flag）/超大截断提示（FR-019 / Story5 AC-2，据 oversize flag）/不可用占位（FR-010 / availability='unavailable'）。
- **改动文件**: DiffView
- **依赖**: T4.2
- **覆盖**: FR-010/FR-015/FR-016/FR-017/FR-018/FR-019；Story1 AC-3 / Story4 AC-1/AC-2 / Story5 AC-1/AC-2
- **测试**: T4.4

---

### T4.4: DiffView 组件测 — 三类行渲染 + 降级分支 + 技术字段折叠

- **Phase**: 4
- **详情**: 组件测：①三类行（新增/删除/未变）视觉可区分（FR-013）；②默认对比最新两版无选择器（FR-014）；③无差异→"无差异"提示不渲染空 diff（FR-015）；④二进制→提示不逐行渲染（FR-018）；⑤超大→截断/提示不卡死（FR-019）；⑥不可用→占位（FR-010）；⑦友好命名映射 + 映射不到原样显示（FR-016）；⑧主视图技术字段出现 0 次（SC-004 断言）。
- **改动文件**: `frontend/src/components/files/__tests__/DiffView.test.tsx`（新建）
- **依赖**: T4.3
- **覆盖**: FR-010/FR-013/FR-014/FR-015/FR-016/FR-018/FR-019；SC-004
- **测试**: 本 task 即测试

---

### T4.5: Phase 4 Codex per-Phase review

- **Phase**: 4
- **详情**: Codex review。**重点**：jsdiff 大文本性能（SC-005）+ 降级分支完整性 + 技术字段 0 泄漏（SC-004）。处理 finding。
- **改动文件**: -
- **依赖**: T4.1–T4.4
- **覆盖**: FR-013–FR-019/SC-004/SC-005 复核
- **测试**: -

---

### T4.6: Phase 4 回归 gate

- **Phase**: 4
- **详情**: 前端 build 通过。Commit：`feat(F104-Phase-4): DiffView jsdiff 渲染 + 友好命名 + 技术字段折叠 + 降级分支`。
- **改动文件**: -
- **依赖**: T4.5
- **覆盖**: -
- **测试**: 前端 build

---

## Phase 5 — 边界降级 + 并发/负向验证 + Final review

**目标**：补全并发/负向/失败注入/单版本不可见/大文件路径测试 + 全回归 + Final review + completion-report + handoff。
**回归 gate**：T5.8。**Codex review**：T5.7（Final cross-Phase）。

---

### T5.1: 【并发】put_artifact 同 key 无重复版本号 + mixed-writer（依 T1.3 实测条件化）

- **Phase**: 5
- **详情**（Codex re-review high #3，消除测试与范围取舍矛盾）：①并发 N 次 `put_artifact(versionable=True)` 同 key → 断言无重复 version_no（`_write_lock`+SAVEPOINT+UNIQUE 防线，**无条件必测**）；②**mixed-writer 形态依 T1.3 实测结论（硬 gate 联动）**：
  - 若 T1.3 实测 = 单 task 顺序队列（无共享连接真并发默认写）→ 测**调度串行化不变量**（versionable 写串行、版本号连续无空洞），合成 versionable+默认交错场景标 **xfail / 明确非目标**（v0.1 已知约束，**不**声明"互不污染"）；
  - 若 T1.3 实测 = 真并发 → Phase 1 已升级（默认路径也进统一写锁 / 自包含），此处**无条件**断言 mixed-writer 互不污染。
- **改动文件**: `packages/core/tests/store/test_artifact_versions_concurrency.py`（新建）
- **依赖**: T1.3, T1.4
- **覆盖**: FR-002/FR-021；SD-2
- **测试**: 本 task 即测试

---

### T5.2: 【负向】__merged_history__ 不进版本表

- **Phase**: 5
- **详情**: 负向测：`_maybe_merge_old_notes` 写 `progress-note:__merged_history__`（versionable=False，SD-9 排除）→ 断言**不进**版本表，**不进** Files Tab `list_versionable_files_for_task` 结果（Story4 AC-4）。
- **改动文件**: `packages/tooling/tests/test_progress_note_versionable.py` + `apps/gateway/tests/routes/test_files.py`
- **依赖**: T1.8, T2.1
- **覆盖**: FR-022/SD-9；Story4 AC-4
- **测试**: 本 task 即测试

---

### T5.3: 【负向】tool_output:* 非 versionable 不进 Files Tab

- **Phase**: 5
- **详情**: 负向测：`tool_output:web_search` 同工具多次写入但 `versionable=False`（SD-9 排除）→ 断言不出现在 Files Tab 列表（Story4 AC-3，不被误当版本化逻辑文件）。覆盖 `llm-*` / `chat-import` 同理（抽样断言）。
- **改动文件**: `apps/gateway/tests/routes/test_files.py`
- **依赖**: T2.1
- **覆盖**: FR-022/SD-9；Story4 AC-3
- **测试**: 本 task 即测试

---

### T5.4: 【失败注入】FR-021 自 rollback + SAVEPOINT 重试 + best-effort 失败信号 + 连接状态

- **Phase**: 5
- **详情**: 失败注入回归测（FR-021/SD-10，Codex plan-review high #2/#3 + re-review round 2/3 high）：①missing table → fail-fast；②**DB locked → 主连接 `BEGIN IMMEDIATE` 持写锁不释放 + versionable put_artifact → 重试至 `busy_timeout` 后 raise（`OperationalError: database is locked`）**；断言 **structlog 降级路径被调用（best-effort local log——仅 StreamHandler 输出进程 stderr，无独立文件/审计 sink）**；**event best-effort：locked 时 events 表无 `ARTIFACT_VERSION_APPEND_FAILED` 是预期，断言不强求该 event**（SQLite 单写锁物理限制——失败 event 自身也写不进被锁的 DB）；断言 raise + 主连接写锁未被破坏；③UNIQUE 冲突 → SAVEPOINT `ROLLBACK TO sp_ver` 重试至成功（断言 artifacts+versions 各 1 行匹配）或耗尽（断言两表均 0 行，整事务 rollback）；④版本写失败（**非 locked**，DB 可写）→ put_artifact **自 rollback**（主表+版本都撤，**断言连接不留脏事务**——后续 commit 不落盘无版本 artifact）+ **事务外** emit `ARTIFACT_VERSION_APPEND_FAILED`（`append_event_committed(conn=versionable_conn)` 独立提交；**断言 events 表确有该事件** + structlog best-effort local log，rollback 后仍 durable 不被吞——DB 可写场景 event best-effort 命中）；⑤progress_note 调用方降级 persisted=False 不阻断 Worker，连接状态干净（Codex high #3 修复验证）。
- **改动文件**: `packages/core/tests/store/test_artifact_versions.py`
- **依赖**: T1.4, T1.7
- **覆盖**: FR-021；SD-10
- **测试**: 本 task 即测试

---

### T5.5: 大文件 storage_ref 版本路径取回 + session 删除后 FR-010 占位

- **Phase**: 5
- **详情**: 集成测：①大文件 artifact（storage_ref 分支）写新版本 → 版本历史正确关联，task 存活期可取回（Story3 AC-4）；②session/task 删除清理 storage_ref 文件后 → 版本内容 best-effort 不可取回 → `availability='unavailable'` 占位不抛异常（FR-010 / SC-002 大文件 best-effort / SD-8 混合方案）。
- **改动文件**: `packages/core/tests/store/test_artifact_versions.py`
- **依赖**: T2.1
- **覆盖**: FR-010；Story3 AC-4；SC-002（大文件 best-effort）
- **测试**: 本 task 即测试

---

### T5.6: 单版本逻辑文件 100% 不进主列表（SC-006）

- **Phase**: 5
- **详情**: 断言测（后端 + 前端组件双侧）：单版本逻辑文件（version count=1）100% **不进** `list_versionable_files_for_task` + Files Tab 主列表（CL-1 完全隐藏，SC-006 MUST 断言不可见，无占位备选）。
- **改动文件**: `packages/core/tests/store/test_artifact_versions.py` + `frontend FilesCenter` 组件测
- **依赖**: T2.1, T3.3
- **覆盖**: FR-012；SC-006；CL-1
- **测试**: 本 task 即测试

---

### T5.7: Final cross-Phase Codex review

- **Phase**: 5
- **详情**: **Final cross-Phase review**（输入：Phase 1-5 全 diff + spec.md + plan.md）。**重点**：事务原子性整体一致性 / 0 regression 彻底性 / SD-9 数据源边界 / 技术字段 0 泄漏 / 并发防线 / FR-010 占位鲁棒性。处理 finding 至 0 HIGH 残留。
- **改动文件**: -
- **依赖**: T5.1–T5.6
- **覆盖**: 全 FR/SC 复核
- **测试**: -

---

### T5.8: 全回归 + 文档产出 + Final commit

- **Phase**: 5
- **详情**: ①全量回归 passed ≥ da947ce baseline，0 regression（SC-003）+ e2e_smoke 8/8；②产出 `completion-report.md`（对照 plan 5 Phase 实际 vs 计划 + Codex review 闭环表）+ `handoff.md`（给 F107：artifact_versions schema / 查询方法 / 两级导航 / 大文件 best-effort 局限 / FR-020 deferred）；③22 FR + 6 SC 覆盖矩阵确认。Commit：`docs(F104-Final): 边界/并发/负向验证 + cross-Phase review 闭环 + completion-report + handoff`。**不主动 push origin/master**，等用户拍板。
- **改动文件**: `completion-report.md`（新建）, `handoff.md`（新建）
- **依赖**: T5.7
- **覆盖**: SC-003；全覆盖矩阵
- **测试**: 全量回归 + e2e_smoke

---

## FR × Task 覆盖矩阵

| FR | 描述 | Task | 状态 |
|----|------|------|------|
| FR-001 | put_artifact +versionable/logical_file_id，仅 True append，混合存储 | T1.2/T1.4/T1.5 | ✅ |
| FR-002 | 单调递增版本号 + UNIQUE MUST + BEGIN IMMEDIATE 重试 + 并发测 | T1.2/T1.4/T5.1 | ✅ |
| FR-003 | 版本历史落盘，重启不丢 | T1.2/T1.6 | ✅ |
| FR-004 | 不改主表 schema，0 regression | T1.3/T1.6/T1.10 | ✅ |
| FR-005 | append-only，删 task 级联唯一例外 | T1.4/T1.8/T1.8t | ✅ |
| FR-006 | 按逻辑文件 key 取版本列表 | T2.1/T2.2 | ✅ |
| FR-007 | 取当前版/上一版内容 | T2.1/T2.2 | ✅ |
| FR-008 | task 维度逻辑文件清单 + 有多版本 task 清单（两级导航）| T2.1/T2.2 | ✅ |
| FR-009 | HTTP API endpoint 暴露 | T2.3/T2.4/T2.5/T3.1 | ✅ |
| FR-010 | 内容不可用占位不抛异常 | T2.1/T2.2/T4.3/T4.4/T5.5 | ✅ |
| FR-011 | Files Tab + NavLink + 路由 + 两级导航 | T3.2/T3.3 | ✅ |
| FR-012 | 列出 version≥2 逻辑文件，单版本不进列表 | T3.3/T5.6 | ✅ |
| FR-013 | git 风格行级 diff（新增/删除/未变可区分）| T4.2/T4.4 | ✅ |
| FR-014 | 默认最新两版，无任意版本选择器 | T4.2/T4.4 | ✅ |
| FR-015 | 无差异提示 | T4.3/T4.4 | ✅ |
| FR-016 | 友好命名 SHOULD，映射不到原样显示 | T4.3/T4.4 | ✅ |
| FR-017 | 技术字段折叠 Advanced 区 | T2.3/T2.5/T4.3/T4.4 | ✅ |
| FR-018 | 二进制提示 | T2.3/T4.3/T4.4 | ✅ |
| FR-019 | 超大降级 | T2.3/T4.3/T4.4 | ✅ |
| **FR-020** | **hash 去重（MAY）** | — | **⏸ deferred（YAGNI，v0.1 不实现）** |
| FR-021 | 同事务原子 + fail-fast + 失败 emit 事件 + 回归测 | T1.3/T1.4/T1.7/T5.4 | ✅ |
| FR-022 | versionable 判定在写入方，存储层不硬编码 name | T1.4/T1.8/T5.2/T5.3 | ✅ |

**FR 覆盖：21/22 实现 + FR-020 deferred。**

## SC × Task 覆盖矩阵

| SC | 描述 | Task | 状态 |
|----|------|------|------|
| SC-001 | ≤2 次点击到 diff（选 task + 点文件）| T3.3 | ✅ |
| SC-002 | 小文件 100% 重启可取回；大文件 best-effort | T1.6/T5.5 | ✅ |
| SC-003 | 全量回归 ≥ baseline，0 regression | T1.10/T2.7/T3.5/T4.6/T5.8 | ✅ |
| SC-004 | 主列表技术字段出现 0 次 | T2.5/T4.4 | ✅ |
| SC-005 | 阈值内文本 1 秒内渲染，超阈值降级 | T4.2/T4.3/T4.4 | ✅ |
| SC-006 | 单版本逻辑文件 100% 不进主列表 | T3.3/T5.6 | ✅ |

**SC 覆盖：6/6。**

## Story AC × Task 覆盖（抽样关键）

| Story AC | Task |
|----------|------|
| Story1 AC-1/AC-2（git diff / 默认最新两版）| T4.2/T4.4 |
| Story1 AC-3（无差异提示）| T4.3/T4.4 |
| Story2 AC-1（version≥2 列表）| T2.2/T3.3 |
| Story2 AC-3（主视图无技术字段）| T2.5/T4.3/T4.4 |
| Story3 AC-1（3 版本单调唯一）| T1.5 |
| Story3 AC-2（重启取回）| T1.6 |
| Story3 AC-3（0 regression）| T1.6/T1.10 |
| Story3 AC-4（大文件路径）| T5.5 |
| Story4 AC-1/AC-2（友好命名 / 原样显示）| T4.3/T4.4 |
| Story4 AC-3（tool_output 不进 Files Tab）| T5.3 |
| Story4 AC-4（__merged_history__ 不进）| T5.2 |
| Story5 AC-1/AC-2/AC-3（二进制/超大/无差异降级）| T4.3/T4.4 |

---

## Deferred 项清单

| 项 | 类型 | 理由 | 归属 |
|----|------|------|------|
| **FR-020** hash 内容去重 | MAY / YAGNI | v0.1 优先正确性与 0 regression，存储优化推后；**v0.1 不排 task** | F107 或未来 |
| 任意两版本对比 | YAGNI-移除 | v0.1 固定最新两版（FR-014），避免版本选择器 UI + 多版本查询复杂度 | F107 v0.2 |
| 全量版本时间线浏览 | YAGNI-移除 | v0.1 只两版对比 | F107 v0.2（git-aware）|
| branch/commit/blame 浏览 | Out of Scope | 需完整版本图谱 + git 语义建模 | F107 v0.2 |
| behavior 文件（USER.md）版本可视化 | Out of Scope | 走 SnapshotStore + 文件系统直写，非 artifact_store 通路 | F107 |
| 版本内容编辑 / 回滚写回 | Out of Scope | v0.1 只读 diff | 未来 |
| 大文件版本内容独立副本 | 已知局限 | SD-8 混合方案大文件存指针，session 删除后不可取回（best-effort）| 未来评估 |

**注**：plan 内未声明任何"Phase 内推迟"项；所有 Phase 1-5 task 均在本 v0.1 完成。
