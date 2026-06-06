# F104 文件工作台 v0.1（diff 视图）— 技术调研（块 A 实测侦察）

> 🚨 **SUPERSEDED 部分过期（Codex review 3 轮修正）**：本文 §4 方案 A 的逻辑文件 key `(task_id, name)` 与 §7 把 Tool Output / LLM Request·Response 列为"真实多版本数据源"，**已被 Codex review 否决**。**最终决议**：显式 `versionable` 标记 + **非空 `logical_file_id`**；v0.1 versionable 来源**仅 `progress-note:{step_id}` 用户 step 记录**（排除内部 `progress-note:__merged_history__` 合并汇总），明确排除 `tool_output` / `llm-*` / `chat-import`（误导性 diff + 跨 scope 风险）。**以 `spec.md` §3.2/§3.5/§10 为准**，本文 §4/§7 仅存侦察推理过程。

> 模式：codebase-scan（research_mode=auto → 内部 surface Feature，跳过 product-research）
> 日期：2026-06-06
> 方法：主 session 主导 + 2 Explore 子代理并行（backend / frontend）+ 主 session 亲自核实 `artifact_store.py`
> 上游纠偏：`.specify/features/103d-octobench/handoff.md` §6（F104 必须动 backend，非纯 UI）

## 0. 结论速览

| 维度 | 事实 | 影响 |
|------|------|------|
| artifact 旧版本 | **不可取**（put_artifact INSERT-only，每次新 ULID） | F104 必须动 backend |
| 逻辑文件概念 | **不存在**（artifact 按 ULID 隔离，无按 name 聚合） | 需先定义"逻辑文件身份" |
| SnapshotStore | **无 diff/history**（仅 prefix-cache 冻结快照） | 不可复用 |
| 前端 diff 库 | **无**（React 19 + 纯手工 CSS） | 需选型/自建 |
| Files Tab 挂载点 | App.tsx 路由 + WorkbenchLayout NavLink | 明确 |

**spec 第一决策点**：版本历史存储方案（§4），推荐**方案 A**（append-only 版本表），待用户拍板。

## 1. artifact_store 现状（主 session 亲自核实 artifact_store.py）

### 1.1 数据模型 — `octoagent/packages/core/src/octoagent/core/models/artifact.py:32-52`
字段：`artifact_id`(ULID, PK) / `task_id` / `ts` / `name` / `description` / `parts[]` / `storage_ref` / `size` / `hash` / `version`(int, 默认 1)

### 1.2 SQL schema — `octoagent/packages/core/src/octoagent/core/store/sqlite_init.py:70-85`
`artifacts` 表，`artifact_id TEXT PRIMARY KEY`，`parts` 列存 JSON。

### 1.3 存储策略（双层）— `artifact_store.py:58-102`
- 小文件（< `ARTIFACT_INLINE_THRESHOLD` 4KB 且 UTF-8 safe）→ inline 到 `parts.content`（SQLite）
- 大文件 → 文件系统 `{artifacts_dir}/{task_id}/{artifact_id}`，`storage_ref` 记录路径

### 1.4 旧版本不可取（核实结论）
- `put_artifact`（`artifact_store.py:84-102`）：纯 `INSERT`，**无** `ON CONFLICT`/upsert
- `artifact_id` = ULID，每次写生成新行（`hooks_legacy.py:287` `str(ULID())`）
- `version` 字段（行 100）直接写入，**无递增逻辑**（全仓无 `version +=`）
- 查询方法：`get_artifact(id)` / `list_artifacts_for_task(task_id, ORDER BY ts ASC)` / `get_artifact_content` —— **无** version/history 查询

### 1.5 写入路径（唯一写方法 `put_artifact`，4 个调用点）
- `octoagent/packages/tooling/.../hooks_legacy.py`（工具大输出自动存储）
- `octoagent/packages/tooling/.../progress_note.py`（进度笔记 + 多笔记合并，2 处）
- `octoagent/apps/gateway/.../services/task_service.py`（文本预处理 / 任务初始化）

> 关键：因写入集中在 `put_artifact` 单一方法，版本历史改造可**集中在一处**，4 个调用方无需逐个改。

## 2. SnapshotStore 现状 — `octoagent/apps/gateway/src/octoagent/gateway/harness/snapshot_store.py`
- 存：内存冻结快照（system prompt，session 内不变）+ live state + `snapshot_records` 表（工具调用摘要 TTL 30d）
- 能力：`write_through` / `append_entry` / `persist_snapshot_record`
- **无** diff / history / 取历史版本 → 确认纠偏，**不可复用**作为 diff 数据源

## 3. 前端 Workbench 现状 — `octoagent/frontend`
- 栈：React 19 + React Router 7 + Vite + 原生 fetch；**无 UI 库**；纯手工 CSS（`tokens.css` 设计系统）；marked + dompurify
- Workbench：侧边栏导航（非 top tab）
  - 导航 NavLink：`components/shell/WorkbenchLayout.tsx:420-459`（聊天/智能体/技能/MCP/记忆/设置）
  - 路由：`App.tsx:55-75`
  - 新增 Files Tab：App.tsx 加 `<Route path="files">` + WorkbenchLayout 加 NavLink
- 已有可复用：`components/TaskVisualization/ArtifactGrid.tsx`（artifact 列表网格）
- API：`GET /api/tasks/{task_id}` 返回 artifacts（含 content）；后端 `gateway/routes/tasks.py:148-229`；**无 diff/版本 endpoint**
- diff 库：**无**；UX 面向非技术用户（技术信息折叠到 Advanced 区，CLAUDE.md:170-172）

## 4. 版本历史存储方案候选（★ spec 第一决策点）[⚠️ 部分 SUPERSEDED — 逻辑文件 key 改为非空 logical_file_id，见顶部 banner]

核心前提：当前无"逻辑文件"概念。要做"上一版 vs 当前版"，三件事缺一不可：
(1) 定义**逻辑文件身份**（哪些 artifact 算同一文件的不同版本）
(2) **保留历史内容**
(3) 提供**取上一版**查询

### 方案 A：append-only 版本表（artifact_versions）— ★ 推荐
- 新增 `artifact_versions` 表（append-only）；`put_artifact` 内部 append 一条版本记录
- 逻辑文件 key = `(task_id, name)`（v0.1）；版本号 = 该 key 已有版本数 + 1
- 取上一版：`WHERE file_key=? ORDER BY version_no DESC LIMIT 2`
- 改造：`put_artifact` 一处 + 新表 + 迁移 + 查询方法 + API endpoint
- ✅ 不改 `artifacts` 主表 → **0 regression 友好**；append-only 合 Constitution #2（Everything-is-an-Event）；改造集中
- ⚠️ 内容可能重复存储（可用 hash 去重 / 共享 storage_ref 缓解）；`file_key` 语义需 spec 定义；v0.2 branch/blame 需额外设计

### 方案 B：workspace 文件系统 git 化
- artifact 落 per-task/project git 工作树，每次 `git commit`；diff = `git diff`
- ✅ git 原生 diff/history/blame/branch → F107 v0.2 天然支持
- ❌ v0.1 over-engineering；双层存储（小文件 SQLite inline）与 git 文件树冲突 → 大改 `put_artifact`（**高 regression**）；引入 git 子进程依赖（违 Constitution #6 Degrade Gracefully 精神）；workspace 概念需新建；ULID 文件名不直观（需映射 logical name）

### 方案 C：artifacts 主表加 `prev_artifact_id` 指针 + content-addressed
- artifacts 加列指向上一版；content-hash 命名文件
- ❌ **侵入主表 schema**；改文件命名逻辑（regression 风险）；指针链遍历不如版本号查询清晰

### 推荐：方案 A
v0.1 只需单文件上一版 → 方案 A 最轻量且 0 regression 友好；append-only 符合 Everything-is-an-Event；改造集中在 `put_artifact` 一处。v0.2 git-aware 若确需 branch，再评估迁移（v0.1 不锁死方向）。

## 5. diff 渲染方案候选（前端）
前端无 diff 库 + 最小依赖哲学 + 面向非技术用户：
- 候选 1（倾向）：`diff`（jsdiff）生成 diff + 自建 CSS 渲染 —— 轻量、样式可控、契合 tokens.css + 非技术用户友好
- 候选 2：`react-diff-viewer-continued` —— 开箱即用 split/unified，但依赖较大 + 样式定制受限
- 候选 3：`diff2html` —— git diff HTML 渲染，偏技术风格，与非技术用户 UX 取向有张力
- 最终在 spec/plan 阶段定（GATE_DESIGN 确认）

## 6. 关键洞察 & spec 待澄清
1. **"文件" = artifact**（milestones：F104=artifact diff，F107=behavior 文件版本可视化）
2. **逻辑文件身份**：`(task_id, name)` 是否足够？当前写入路径是否会复写同名 artifact（产生真实"多版本"）——需 spec 澄清，否则版本链聚合不出内容
3. **版本触发范围**：哪些写入算"新版本"？全部 `put_artifact` 还是仅特定路径
4. **diff 粒度**：v0.1 文本行级 diff；二进制/大文件按 size 阈值降级处理
5. **前端可见范围**：Files Tab 展示哪些 artifact（守 CLAUDE.md 非技术用户原则，避免暴露 raw 技术产物）→ 见 §7 实证后的 UX 设计点

## 7. 版本数据来源实证（消除风险）[⚠️ 部分 SUPERSEDED — tool_output/llm-* 不 versionable，仅 progress-note 用户 step 记录，见顶部 banner]

主 session 专项侦察确认：`(task_id, name)` 逻辑文件 key **有真实多版本数据源**：

| 来源 | name 模式 | 多版本机制 | 证据 |
|------|----------|-----------|------|
| Progress Notes | `progress-note:{step_id}` | 同 step_id 多次调用 → 多 artifact | `progress_note.py:124` + 测试 `test_progress_note.py:151-171`（实测 3 版本）|
| Tool Output | `tool_output:{tool_name}` | 同工具多次调用 → 多 artifact | `hooks_legacy.py:264` |
| LLM Request/Response | `llm-request-context` / `llm-response` | 每轮对话 → 新 artifact | `task_service.py:1361/2291` |

- 数据库**明确允许** `(task_id, name)` 重复（无 UNIQUE 约束，`sqlite_init.py:71-89`）→ 方案 A 数据可行
- **边界厘清**：USER.md 等 behavior 文件走 SnapshotStore + 文件系统直写，**不走 artifact_store** → F104 范围外（F107 处理）；`file_write_tool` 直接 FS 操作，也不走 artifact

### ⚠️ 新增 UX 设计点（spec 必须处理）
现有同名 artifact 多为**技术性审计副本**（tool_output / llm-response / llm-request-context / progress-note），非用户日常理解的"文件"。F104 主界面面向非技术用户（CLAUDE.md:170-172）→ Files Tab 直接平铺会违反"技术信息折叠"原则。spec 需决策 Files Tab 的**展示范围与命名**（候选：仅展示 version≥2 的逻辑文件 / 友好名称映射 / 技术性 artifact 归 Advanced 区）。
