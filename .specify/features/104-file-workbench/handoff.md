# F104 → F107 Handoff（文件工作台 v0.1 → v0.2 git-aware）

**From**: F104 文件工作台 v0.1（diff 视图）—— M6 第 1 个 Feature
**To**: F107 文件工作台 v0.2（git-aware：branch/commit/blame + behavior 文件版本可视化）
**Baseline 演进**: `da947ce`（M5 收口）→ F104 Final（方案 B 连接级写隔离）
**日期**: 2026-06-07

---

## 1. F104 v0.1 交付内容

- **后端**：append-only `artifact_versions` 历史表 + `put_artifact` versionable/logical_file_id 参数 + 4 查询方法 + 级联删除 + 失败事件。
- **前端**：Files Tab 两级导航（task → 逻辑文件）+ 单文件"上一版 vs 当前版"jsdiff 行级 diff 视图 + Advanced 技术字段折叠。
- **范围**：仅 `progress-note:{step_id}` 用户 step 进版本表（SD-9 allowlist，显式 `versionable=True`）；v0.1 固定对比"最新版 vs 次新版"两版。

---

## 2. 架构就位（F107 复用）

### 2.1 `artifact_versions` schema

```sql
CREATE TABLE IF NOT EXISTS artifact_versions (
    version_id        TEXT PRIMARY KEY,           -- ULID
    task_id           TEXT NOT NULL,
    logical_file_id   TEXT NOT NULL,              -- 写入方显式声明，versionable=True 时 MUST 非空（无 name 回退）
    version_no        INTEGER NOT NULL,           -- 逻辑文件 key 内单调递增 MAX+1
    artifact_id       TEXT NOT NULL,              -- 关联触发此版本的 artifact 行（Advanced 审计）
    ts                TEXT NOT NULL,              -- 写入时间，兜底排序键
    storage_kind      TEXT NOT NULL,              -- 'inline' | 'storage_ref'（混合存储分支）
    content           TEXT,                       -- 小文件：UTF-8 独立副本（inline）；大文件 NULL
    storage_ref       TEXT,                       -- 大文件：指针（storage_ref）；小文件 NULL
    size              INTEGER NOT NULL DEFAULT 0,
    hash              TEXT NOT NULL DEFAULT '',    -- SHA-256，两分支都填
    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
    UNIQUE(task_id, logical_file_id, version_no)   -- CL-4 MUST：DB 层强唯一防线
);
```
索引：`idx_artifact_versions_logical (task_id, logical_file_id, version_no DESC)` + `idx_artifact_versions_task (task_id)`。

**F107 价值**：表已含完整版本图谱基础字段（version_no / ts / hash / storage_kind）；任意两版本对比、版本时间线浏览只需扩查询，无需改 schema。

### 2.2 查询方法签名（`artifact_store.py`）

```python
async def list_versions(task_id, logical_file_id) -> list[ArtifactVersionMeta]
    # FR-006：版本列表元信息，ORDER BY version_no DESC, ts DESC
async def get_current_and_previous(task_id, logical_file_id) -> tuple[ArtifactVersionContent|None, ArtifactVersionContent|None]
    # FR-007：当前版（MAX version_no）+ 上一版（次大）；两阶段懒加载（先元数据 size 判定，inline 未超阈值再读 content）
    # <2 版本 → previous=None；storage_ref 文件不存在 → availability='unavailable'（FR-010）
async def list_versionable_files_for_task(task_id) -> list[LogicalFileSummary]
    # FR-008 第二级：GROUP BY logical_file_id HAVING COUNT(*)>=2（SD-4 过滤单版本）
async def list_tasks_with_versionable_files() -> list[str]
    # FR-008 第一级（两级导航第一级）：DISTINCT task_id of version≥2 逻辑文件
async def delete_artifact_versions_by_task_ids(task_ids) -> int
    # CL-3 级联（session_delete 事务内 commit 前调用）；append-only 唯一例外
```
返回类型：Pydantic `ArtifactVersionMeta` / `ArtifactVersionContent`（含 `availability: Literal["available","unavailable"]`）/ `LogicalFileSummary`（`models/artifact_version.py`）。

### 2.3 versionable 独立连接（方案 B —— F104 核心架构产出）

mixed-writer 隔离的关键基础设施，F107 必须沿用：

- **`versionable_conn`**：StoreGroup 构造独立写连接（autocommit `isolation_level=None` + 手动 `BEGIN IMMEDIATE` 拿 SQLite 写锁 + 连接级 `foreign_keys=ON` + `busy_timeout=5000` 跨连接串行）。versionable 路径走此连接 —— commit/rollback 只影响独立连接，**不卷入主连接默认 versionable=False 写**（0 regression）。
- **`connection.py apply_write_connection_pragmas`** helper：统一写连接 PRAGMA（versionable_conn FK=ON）。
- **`StoreGroup.close()`**：关**双连接**（主 conn + versionable_conn）。F107 新增测试 teardown 必须用 `store_group.close()`（不能只关主连接）。
- **`_versionable_isolated`** 退化标记：versionable_conn=None（兼容 watchdog 直接构造 StoreGroup）时显式拒绝 versionable 写，不静默污染。
- **`_write_lock`**（asyncio.Lock）：串行化 versionable 连接访问 + SAVEPOINT 重试（UNIQUE 冲突 ROLLBACK TO sp_ver 保留主表行）。

### 2.4 失败信号双 best-effort

versionable append 失败 → 自 rollback（versionable_conn 干净）→ 双轨：① `structlog.warning`（best-effort local log，不依赖 DB 写；实测 logging_config 仅 StreamHandler 输出 stderr，无独立文件/审计 sink）；② `ARTIFACT_VERSION_APPEND_FAILED` event（best-effort durable，DB 可写时 `append_event_committed(conn=versionable_conn)` 独立提交；DB locked 时降级仅 structlog —— SQLite 单写锁物理限制）。

### 2.5 HTTP API（`routes/files.py`）

| Method + Path | 用途 |
|---------------|------|
| `GET /api/files/tasks` | 两级导航第一级：有多版本逻辑文件的 task 清单 |
| `GET /api/files/tasks/{task_id}/logical-files` | 第二级：version≥2 逻辑文件 + display_name + version_count |
| `GET /api/files/tasks/{task_id}/diff?logical_file_id=` | 当前版 vs 上一版（`logical_file_id` 用 **query param** 承载含 `:`/`/` —— path param 路由解析歧义已实测）|
| `GET /api/files/tasks/{task_id}/versions?logical_file_id=` | 版本列表元信息（Advanced 区）|

- **鉴权**：`include_router(..., dependencies=protected)` front-door token 路由级（非 handler Bearer，Constitution #10）。
- **SC-004**：主响应（DiffSide）**仅** content / availability / oversize —— Final review 已移除 version_no / storage_kind（技术字段只走 `/versions`）。

### 2.6 前端组件（`frontend/`）

- **FilesCenter.tsx**（`src/pages/`）：两级导航 + apiFetch 鉴权 + race token（requestSeq）+ loading/error/empty 三态 + 面包屑回退。
- **DiffView**（DiffBody / DiffLineList）：jsdiff `diffLines` 行级高亮（`--cp-success-soft` + / `--cp-danger-soft` -）+ 6 降级分支（binary / oversize / current null / 首版 / previous null / 无差异 FR-015）。
- **AdvancedVersionMeta**：details 默认收起 + onToggle 懒加载 `fetchLogicalFileVersions`（已提供，F104 Phase 4 消费）；技术字段 vN/hash前8/size/storage_kind 仅此区。
- 依赖：`diff@9` + `@types/diff`（已装）；不引入 react-diff-viewer / diff2html（D-DIFF 约束）。

---

## 3. F107 v0.2 范围建议

1. **任意两版本对比**（v0.1 仅 current vs previous，FR-014 固定最新两版）：
   - 复用 `artifact_versions` + `list_versions`；扩展 `get_current_and_previous` → 任意 `version_no` 对的内容查询方法（两阶段懒加载逻辑可直接复用）。
   - 前端补版本选择器 UI（v0.1 无）+ 时间线浏览。

2. **branch / commit / blame 浏览**（v0.2 git-aware 主体）：基于 version_no + ts + hash 构建版本图谱与 git 语义建模。

3. **behavior 文件版本历史可视化**（USER.md 等）：behavior 文件走 SnapshotStore + 文件系统直写，**不走 artifact_store** —— 是另一条数据通路，F107 需单独建模（不能直接复用 artifact_versions）。

4. **大文件独立副本 / git 存储**（解决 v0.1 best-effort 局限）：v0.1 大文件存 storage_ref 指针，session/task 删除清理后不可取回。F107 评估大文件独立副本或 git blob 存储。

5. **FR-020 版本去重**（hash 去重，v0.1 deferred YAGNI）：评估按内容 hash 去重缓解重复存储。

6. **DB locked durable**（v0.1 best-effort local log）：评估 outbox / 独立审计 sink，让 locked 场景失败信号也可持久化（v0.1 受 SQLite 单写锁物理限制 + 无文件 sink）。

---

## 4. 已知限制传递（F107 必读）

| 限制 | 说明 | F107 处理建议 |
|------|------|--------------|
| **mixed-writer 已隔离（方案 B）** | versionable 走独立 `versionable_conn` 物理隔离；F107 沿用，新增写路径若也需隔离同样走 versionable_conn 模式 | 沿用 versionable_conn；新写路径 teardown 用 store_group.close() |
| **大文件 best-effort** | 大文件存 storage_ref 指针，session/task 删除后不可取回（SC-002 仅小文件 inline 100%）| 评估大文件独立副本 / git 存储 |
| **FR-020 deferred** | hash 去重 v0.1 不做 | 评估实现 |
| **worktree venv gotcha** | worktree `.venv` symlink → 主仓，裸 pytest 跑 master src（ImportError）；验证须 `PYTHONPATH` 锁 worktree 全 packages/apps src | F107 起 worktree venv 独立化（环境 follow-up）|
| **主连接 FK 历史 OFF** | 主连接 `foreign_keys` OFF（`_migrate_legacy_tables` 历史缺陷）；versionable_conn 显式 FK=ON。主连接不动避 7 regression | F107 评估主连接 FK 收口（涉及 7 测试，需独立评估）|
| **DB locked best-effort** | locked 时失败信号双轨均 best-effort（structlog local log + event）；logging_config 仅 StreamHandler 无文件 sink | F107 评估 outbox / 审计 sink |
| **11 pre-existing 前端测试债** | master baseline 已存在 11 failed/前端测试（与 F104 无关）| 与 F104 无关，独立修复 |

---

## 5. 数据通路边界提醒（F107 不要踩坑）

- `artifact_versions` 服务的是 **artifact_store 通路**（task 产物，progress-note 等）。
- **behavior 文件**（USER.md / IDENTITY.md 等）走 **SnapshotStore + 文件系统直写**，是独立通路——F107 做 behavior 文件版本可视化时**不能复用** artifact_versions，需单独建模（spec §2.2 Out of Scope 已明确）。
- F104 守 H1/H2/H3（surface 层），不触碰 Agent 协作模型；F107 同样守此边界。
