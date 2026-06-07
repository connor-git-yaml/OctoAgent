# Feature Specification: F104 文件工作台 v0.1（diff 视图）

**Feature ID**: F104
**Feature Branch**: `feature/104-file-workbench`
**Created**: 2026-06-06
**Status**: ✅ Approved 定稿（GATE_DESIGN + Codex adversarial review **5 轮闭环、round5 APPROVE / 0 HIGH** 2026-06-06：11 finding 1C+5H+5M 全修复，见 §3.4/§3.5/§10）→ 进入 Phase 4 plan
**M6 阶段**: M6 第 1 个 Feature（Surface 扩张首站）
**Upstream**: F084 SnapshotStore / artifact_store（已稳定）；F103d handoff §6 纠偏（F104 必须动 backend）
**Downstream**: F107 文件工作台 v0.2（git-aware：branch/commit/blame + behavior 文件版本可视化）
**Baseline**: da947ce（M5 收口 commit；F104 0 regression 基线）
**Feature 性质**: backend 版本保留改造（artifact_versions 历史表）+ frontend Files Tab 新增（surface 层，不改 Agent 协作模型）

---

## 0. 设计基础说明

F104 是 M6 Surface 扩张的第一个 Feature。块 A 实测侦察（`research/tech-research.md`）确认了三个关键事实，本 spec 在此之上展开：

1. **artifact 旧版本当前不可取**：`put_artifact` 是 INSERT-only，每次写入生成新 ULID，旧版本内容无法取回；`version` 字段只是计数器、无递增逻辑。要做"上一版 vs 当前版"必须先在 backend 保留历史内容——F104 不是纯 UI Feature。
2. **"逻辑文件"概念当前不存在**：artifact 按 ULID 隔离，无按 name 聚合的机制。要做版本对比必须先定义"逻辑文件身份"。**[Codex review 修正]** 原拟用隐式 `(task_id, name)` 聚合，但 Codex 揭示现有同名 artifact 多为技术审计产物（见事实 3），隐式聚合会误判 + 跨 scope 暴露 → v0.1 改用**显式 `versionable` 标记 + `logical_file_id`**：仅写入方显式声明的来源才进版本链（§3.2 SD-1）。
3. **现有同名 artifact 多为技术性审计副本（Codex high finding）**：`tool_output:{tool}`（同工具每次调用独立）/ `llm-response`·`llm-request-context`（**每轮对话是独立响应、非同一文件演进**）/ `chat-import`（把所有导入附件放全局 `ops-chat-import` task 且以文件名为 name，**跨 scope/导入批次暴露风险**，违 Constitution #5）这些 name 模式本质是 Agent 运行的审计产物，不是用户日常理解的"文件"。把它们当"同一文件多版本"做 diff 既无意义又误导。结论：v0.1 **不**隐式版本化这些来源，只对显式 `versionable=True` 的安全来源建版本（§3.2 SD-1 / §3.5 SD-9）；展示层再叠加非技术用户友好策略（§3.3）。

**[尊重已拍板技术方向]**：版本存储采用方案 A（append-only `artifact_versions` 历史表），diff 渲染采用 jsdiff（`diff` npm 包）+ 自建 CSS。两者作为约束记录在 §6 Constraints，FR 用 WHAT 表达，HOW 实现细节留给 plan。

---

## 1. 目标（Why）

### 1.1 让用户能看见"文件改了什么"

OctoAgent 在执行任务过程中会反复更新同一个 **versionable 逻辑文件**（v0.1 = 用户进度笔记 `progress-note:{step_id}` 同 step 多次更新；`tool_output`/`llm-*`/`chat-import` 等技术审计产物 **不** versionable、不在此列，§3.5 SD-9）。当前用户在 Workbench 里只能看到 artifact 的最终内容，无法知道"这一版和上一版相比改了哪里"。F104 在 Web Workbench 新增 Files Tab，对有多个版本的 versionable 逻辑文件提供 git 风格的"上一版 vs 当前版"diff 视图，让用户一眼看出新增/删除/修改的行。

这符合 Constitution #8（Observability is a Feature）：文件的演变过程应当可观测，而不是只剩最终态。

### 1.2 让历史版本可恢复地落盘

当前旧版本内容直接丢失。F104 通过 append-only 版本历史表，把每次 **versionable 写入**的内容都持久化（小文件独立副本，§3.5 SD-8），进程重启后历史不消失（Constitution #1 Durability First），且版本记录 append-only、不可篡改（Constitution #2 Everything is an Event 精神）。

### 1.3 不打扰非技术用户

F104 主界面面向普通非技术用户。Files Tab 不能变成一个堆满 `llm-request-context` / `tool_output:xxx` 的技术产物列表——v0.1 用**显式 `versionable` 标记从数据源头排除**这些审计产物（§3.5 SD-9），只有 versionable 安全来源（progress-note）进版本表。F104 的产品取舍：只展示"真正能 diff 的逻辑文件"（versionable + ≥2 版本），友好显示名 + 原始技术字段（artifact_id、版本号等）归折叠/Advanced 区。

---

## 2. 范围声明

### 2.1 In Scope（本 spec 负责）

- **后端版本保留**：新增 append-only `artifact_versions` 历史表；`put_artifact` 加 `versionable`/`logical_file_id` 参数，**仅 versionable=True** 时 append 版本记录；逻辑文件 key = `(task_id, logical_file_id)`；混合存储（小文件独立副本 + 大文件指针，SD-8）。
- **后端版本查询**：提供"按逻辑文件取版本列表"+"取上一版/当前版内容"的查询能力，并通过 API endpoint 暴露给前端。
- **逻辑文件聚合**：按 `(task_id, logical_file_id)` 聚合，能列出某个 task 下"有 ≥2 个版本"的逻辑文件清单。
- **Files Tab（前端）**：Web Workbench 新增 Files Tab（侧边栏 NavLink + 路由），展示逻辑文件列表 + 单文件"上一版 vs 当前版"git 风格 diff 视图。
- **非技术用户友好展示**：技术性 artifact name 友好显示映射（SHOULD 级）；原始技术字段归折叠/Advanced 区。
- **可观测性**：版本写入是 append-only 落盘行为；diff 查询路径可观测。

### 2.2 Out of Scope（明确排除）

| 排除项 | 归属 | 理由 |
|--------|------|------|
| **branch / commit / blame 浏览** | F107 v0.2 | git-aware 浏览需要完整版本图谱与 git 语义建模，v0.1 只做最轻量的两版对比 |
| **全量 git 历史** | F107 v0.2 | v0.1 只"上一版 vs 当前版"两个版本，不做多版本时间线浏览 |
| **任意两版本对比** | F107 或未来（标 MAY） | v0.1 固定对比"最新版"与"次新版"；任意版本两两选择对比留待 v0.2 |
| **behavior 文件（USER.md 等）版本可视化** | F107 | behavior 文件走 SnapshotStore + 文件系统直写，**不走 artifact_store**，是另一条数据通路 |
| **改 Agent 协作模型 H1/H2/H3** | 不做 | 文件工作台是 surface 层，不触碰主 Agent / Worker / Subagent 协作模型 |
| **artifacts 主表 schema 变更** | 不做 | 方案 A 只新增历史表，主表零改动以保证 0 regression |
| **版本内容编辑 / 回滚写回** | 未来 | v0.1 只读：看 diff，不支持在 UI 里编辑文件或回滚到旧版本 |
| **跨 task 的同名文件聚合** | 未来 | v0.1 逻辑文件 key 含 task_id，不跨 task 聚合 |

---

## 3. 关键决策摘要

### 3.1 用户已拍板决策（作为约束记录）

| ID | 事项 | 结论 |
|----|------|------|
| **D-A** | 版本历史存储方案 | **方案 A**：append-only `artifact_versions` 历史表，`put_artifact` 加 versionable 参数后 append；不改 artifacts 主表（0 regression 友好）；逻辑文件 key = `(task_id, logical_file_id)`（Codex 修正：显式 versionable，§3.2 SD-1）|
| **D-DIFF** | diff 渲染方案 | **jsdiff**（`diff` npm 包）生成 diff + 自建 CSS 渲染（契合纯手工 CSS 设计系统 + 非技术用户友好，避免通用 AI 风 UI），不引入 react-diff-viewer / diff2html 这类重依赖 |

### 3.2 逻辑文件身份与版本数据源（Spec 自决 + Codex review 修正）

**SD-1：逻辑文件身份 = 显式 `versionable` 标记 + `logical_file_id`（Codex high finding 修正）**

原拟 `(task_id, name)` 隐式聚合被 Codex review 推翻（理由见 §0 事实 3：技术审计产物隐式聚合会误导 + 跨 scope 暴露）。v0.1 改为**显式版本化**：

- `put_artifact` 新增 `versionable: bool = False` + `logical_file_id: str | None = None` 参数
- **默认 `versionable=False`**：现有所有写入不传 → **完全不进版本表**（0 regression 更彻底；技术审计产物天然排除）
- **仅显式 `versionable=True`** 的写入 append 版本；`versionable=True` 时 **MUST 提供非空 `logical_file_id`**（**无 `name` 回退**——回退会让漏传的调用点退回被否决的隐式 name 聚合，Codex re-review high 修正）。逻辑文件 key = `(task_id, logical_file_id)`
- **v0.1 versionable 来源 allowlist**：见 §3.5 SD-9（v0.1 仅 `progress-note:{step_id}` 进度演进，明确排除 `llm-*` / `tool_output:*` / `chat-import`）

**SD-2：版本号语义与并发唯一防线（Codex high finding 修正）**

版本号 = 该逻辑文件 key 当前已有版本数 + 1。"当前版" = 最大版本号；"上一版" = 次大版本号。版本号由系统在 append 时分配，用户不可见原始号（归 Advanced 区，§3.3）。

原 CL-4"单连接串行已足够"被 Codex 推翻——aiosqlite 单连接下 async coroutine 在 "读 MAX → await → INSERT" 间有让出点，并发写同 key 仍可能读到同一 MAX → 重复版本号。修正为：
- `UNIQUE(task_id, logical_file_id, version_no)` 约束 **升 MUST**（DB 层强唯一防线，非 MAY）
- 版本号分配用 `BEGIN IMMEDIATE` 事务（或 per-logical-file async lock）包住 "`MAX(version_no)+1` → INSERT"；UNIQUE 冲突则**重试**
- `ts` 兜底排序键（`ORDER BY version_no DESC, ts DESC`）
- 必须补**并发 `put_artifact(versionable=True)` 回归测试**（断言无重复版本号）

**SD-3：版本触发范围 = 仅 `versionable=True`（Codex high finding 修正）**

原"所有 `put_artifact` 都 append" → v0.1 **仅 `versionable=True` 的写入** append 版本记录。一举三得：①0 regression 更彻底（默认路径完全不碰版本表）；②排除技术审计产物（不标记即不版本化）；③数据源安全聚焦（§3.5 SD-9 allowlist）。展示层过滤（CL-1 ≥2 版本）在此之上叠加。

### 3.3 ★ 核心产品设计点：Files Tab 展示范围与命名（Spec 自决）

现有同名 artifact 多为技术性审计副本，直接平铺违反非技术用户原则。F104 的取舍：

**SD-4：v0.1 Files Tab 聚焦"有 ≥2 个版本的逻辑文件"**

只展示真正能做 diff 的逻辑文件（version count ≥ 2）。单版本 artifact（无上一版可比）不进 Files Tab 主列表——这既贴合"diff 视图"的功能定位，又自然过滤掉大量一次性技术产物。

**SD-5：逻辑文件友好显示映射（SHOULD 级）**

对 versionable 逻辑文件的 `logical_file_id` 提供友好显示名（v0.1 主要 `progress-note:{step_id}` → "进度笔记"）。映射表可预置未来来源（如 `tool_output:{tool}`→"工具输出"）作前向预留，但 **v0.1 这些来源不 versionable、不进 Files Tab**（Codex re-review 修正：避免 tool_output 验收不可达）。映射不到的原样显示。SHOULD 级——缺失不阻塞。

**SD-6：原始技术字段归 Advanced/折叠区**

artifact_id（ULID）、内部版本号、storage_ref、hash 等技术字段不出现在主视图，归入折叠/Advanced 区，普通用户看到的是友好文件名 + "上一版 vs 当前版" diff。

**CL-1 决议（GATE_DESIGN 用户拍板）**：**完全隐藏**单版本文件。Files Tab v0.1 定位为纯 diff 工具，主列表只放 version count ≥ 2 的逻辑文件；单版本文件不进列表。"任务产物总览"留待 F107 或独立 Feature。

---

### 3.4 GATE_DESIGN 决议固化（CL-1 ~ CL-4）

2026-06-06 GATE_DESIGN 硬门禁通过，澄清点最终决议如下：

| CL | 决议 | 归属 |
|----|------|------|
| **CL-1** 单版本文件可见性 | **完全隐藏**（纯 diff 工具，主列表仅 version≥2） | 用户拍板 |
| **CL-2** Files Tab 入口模型 | **(b) 先选 task 两级导航**：task 列表 → 该 task 多版本逻辑文件 → diff | 用户拍板 |
| **CL-3** 版本内容存储形态 | **混合方案**（小文件 A2 独立副本 + 大文件指针，Codex critical 修正）；删 task 级联清；SC-002 仅保证小文件 100%，大文件 best-effort | 用户拍板 + Codex 修正 |
| **CL-4** 版本号并发分配 | `UNIQUE(task_id,logical_file_id,version_no)` **MUST** + BEGIN IMMEDIATE/async lock + 冲突重试 + ts 兜底（Codex high 修正：单连接串行不防 async coroutine 让出点）| 自决 + Codex 修正 |

**SD-7（CL-2）入口模型**：Files Tab 两级导航。第一级列出"有多版本逻辑文件的 task"，第二级列出该 task 下 version≥2 的逻辑文件，点击进 diff。后端查询以 task 为维度（FR-008），不做跨 task 全局聚合（§2.2 Out of Scope）。满足 SC-001 ≤2 次点击（选 task + 点文件）。

**SD-8（CL-3）版本内容存储 = 混合方案（小文件 A2 独立副本 + 大文件指针）（Codex critical finding 修正）**

Codex 指出"纯 A2"措辞掩盖了大文件局限——大文件指针指向 artifacts 的 storage_ref，session 删除会 unlink 该文件 → 大文件历史并不独立于 artifacts 生命周期。v0.1 正名为**混合方案**并明确范围：
- **小文件**（< `ARTIFACT_INLINE_THRESHOLD` 4KB）：版本表**存内容副本**（真独立——append-only，不因 artifacts 行被删/合并而失效）。v0.1 versionable 来源（progress-note）几乎都是小文本 → 主路径走此分支。
- **大文件**（storage_ref）：版本表**存指针 + hash**（不复制大文件副本，避免成本；大文件 diff 本是 Edge Case，FR-018/019 已降级）。**明确局限**：大文件版本内容随 artifacts storage_ref 生命周期，session/task 删除清理后不可取回 → FR-010 占位。**SC-002 的"100% 可取回"仅保证小文件 inline；大文件 best-effort**（见 SC-002 修正）。
- **删 task**：版本表按 task_id 级联清理（与 `delete_artifacts_by_task_ids` 同事务），数据归属一致、无孤儿。
- **append-only**：正常路径不更新/删除版本记录；删 task 级联清理是唯一例外（数据归属，非篡改）。

---

### 3.5 数据源边界与失败策略（Codex review 修正新增）

**SD-9：v0.1 versionable 来源 allowlist + 安全边界（Codex high finding）**

显式 `versionable=True` 由**写入方**设置。v0.1 allowlist：

| 来源 | versionable | logical_file_id | 理由 |
|------|-------------|-----------------|------|
| `progress-note:{step_id}`（用户 step）| ✅ True | `progress-note:{step_id}` | 同 step 进度演进 = 同一项更新，diff 有意义，task-scoped 安全。**仅 `execute_progress_note` 的用户 step 记录** |
| `progress-note:__merged_history__` | ❌ False | — | **内部合并汇总 artifact**（progress_note.py:~280 维护性产物，非用户 step），versionable 会把维护内容暴露给普通用户（Codex round3 修正）|
| `llm-response` / `llm-request-context` | ❌ False | — | 每轮独立响应，非演进，diff 无意义 |
| `tool_output:{tool}` | ❌ False | — | 每次调用独立 |
| `chat-import` 附件 | ❌ False | — | 全局 ops task + 跨 scope 暴露风险（Constitution #5） |

- **安全边界**：`logical_file_id` 隐含 task scope（key 含 task_id），不跨 task/scope 聚合；versionable 来源必须 task-owned（排除全局 audit task）。
- **可扩展**：未来"用户文件编辑工具"写入时传 `versionable=True` 即自动纳入，无需改 F104 backend。
- **不在存储层硬编码黑白名单**：versionable 判定在**写入方**（progress_note 等）显式传参，`artifact_store` 只按参数 append（避免在存储层硬编码 name 模式 → Constitution #9 风险 + 技术债）。

**SD-10：版本 append 失败策略与事务边界（Codex medium finding）**

FR-001（每次 versionable 写入 append）vs FR-004（0 regression）的张力，明确：
- **同事务原子**：版本 append 与 `put_artifact` 主表 INSERT 在**同一 SQLite 事务**（一致性优先，Constitution #1/#2）。版本写失败 → 整体回滚（主表也不写）。
- **正常路径无 regression**：版本表 DDL 启动建好（`CREATE TABLE IF NOT EXISTS`）；UNIQUE 冲突走重试（SD-2）不抛用户；默认 `versionable=False` 路径完全不碰版本表。仅 DB 真故障（locked/磁盘满）下主+版本一起失败（原 artifact 写本也会失败，非新增 regression）。
- **迁移 fail-fast**：启动建表失败 → 阻断服务（不静默降级，Observability #8）。
- **可观测（双 best-effort 信号）**：版本 append 失败的信号分两轨，**两轨都是 best-effort**，不宣称 event 全覆盖、也不宣称信号永不丢失：
  - **① structlog.warning（best-effort local log）**：经 `logging_config` 仅挂 StreamHandler（输出进程 stderr，**无独立文件/审计 sink**——Logfire 默认 off 且 opt-in）；不依赖任何 DB 写，DB 整体不可写时仍会调用。**可见性取决于环境是否持久化进程流**（stdout/stderr 被收集时可见；否则进程退出即丢）；独立文件/审计 sink 超 v0.1 范围。
  - **② `ARTIFACT_VERSION_APPEND_FAILED` event（best-effort durable）**：DB 可写时在独立 versionable 写连接上 durable emit（不卷主连接事务，F104 Codex 修复 2）；**DB locked / 不可写时降级仅 structlog best-effort local log，不强求该 event**。
  - **根因（SQLite 单写锁物理限制）**：versionable append 走 `BEGIN IMMEDIATE` 拿写锁；当主连接/其他 writer 已持写锁（locked 场景），versionable 连接的版本写**与失败 event 写都被同一把写锁阻塞**至 `busy_timeout` 超时——DB 被锁时任何 DB 写（含失败 event 自身）物理上写不进。outbox/延迟重试是过度工程（超 v0.1 范围），故 locked 场景接受"失败 event 缺失、structlog 兜底"的务实降级。
- **测试**：必须补回归测试：missing table / DB locked / UNIQUE 冲突重试 / 版本写失败回滚。其中 **DB locked → 重试至 busy_timeout 后 raise（database is locked），断言 structlog 降级路径（event best-effort：locked 时 events 表无 `ARTIFACT_VERSION_APPEND_FAILED` 是预期，不强求该 event）**。

---

## 4. User Scenarios & Testing

### User Story 1 - 查看单个逻辑文件"上一版 vs 当前版"diff（Priority: P1）

作为使用 OctoAgent 的用户，当某个逻辑文件被改过至少两次后，我想在 Files Tab 里点开它，看到 git 风格的"上一版 vs 当前版"对比，清楚哪些行被新增、删除、修改。

**Why this priority**: 这是 F104 的核心价值，整个 Feature 的存在理由。没有它就没有"文件工作台 diff 视图"。它构成 MVP：只要这一条可用，用户就能看到文件演变，Feature 即交付价值。

**Independent Test**: 构造一个 task，对同一 `logical_file_id` 用 `versionable=True` 写入两个不同内容的版本，打开 Files Tab → 点开该逻辑文件 → 断言页面渲染出 git 风格 diff，新增行/删除行/未变行视觉可区分，且对应 jsdiff 计算结果。

**Acceptance Scenarios**:

1. **Given** 某 versionable 逻辑文件 `(task_id=T, logical_file_id=L)` 有两个版本（v1 内容 A、v2 内容 B，A≠B），**When** 用户在 Files Tab 点开该文件，**Then** 页面以 git 风格展示 v1（上一版）与 v2（当前版）的行级 diff，新增/删除/未变行有不同视觉标识。
2. **Given** 同一逻辑文件有 5 个版本，**When** 用户点开，**Then** 默认对比"最新版（当前版）"与"次新版（上一版）"，不展示中间历史版本（v0.1 范围）。
3. **Given** 上一版与当前版内容完全相同，**When** 用户点开，**Then** 页面明确提示"两版内容无差异"，不渲染空 diff（Edge Case）。

---

### User Story 2 - 浏览某任务下"有版本历史的逻辑文件"列表（Priority: P1）

作为用户，我想在 Files Tab 里看到当前任务下所有"被改过多次、有版本历史"的逻辑文件清单，用友好的名字呈现，以便选择要对比的文件。

**Why this priority**: diff 视图（Story 1）需要一个入口列表来选择文件。没有列表，用户无从进入 diff。它与 Story 1 共同构成 MVP——两者缺一不可。

**Independent Test**: 构造一个 task 含若干逻辑文件（部分单版本、部分多版本），打开 Files Tab → 断言列表只列出 version count ≥ 2 的逻辑文件（SD-4），且技术性 name 显示为友好名（SD-5），原始 artifact_id 不出现在主列表（SD-6）。

**Acceptance Scenarios**:

1. **Given** 某 task 含 3 个逻辑文件：F1（2 版本）、F2（4 版本）、F3（1 版本），**When** 用户打开 Files Tab，**Then** 列表显示 F1 和 F2，不显示 F3（单版本无 diff，SD-4）。
2. **Given** 逻辑文件 name 为 `progress-note:step-3`，**When** 列表渲染，**Then** 显示友好名（如"进度笔记"）而非原始 name 字符串（SD-5）；映射不到的 name 原样显示。
3. **Given** 用户处于 Files Tab 主视图，**When** 查看任一文件条目，**Then** 主视图不显示 artifact_id（ULID）、storage_ref、hash 等技术字段，这些归折叠/Advanced 区（SD-6）。

---

### User Story 3 - 版本历史可靠落盘且不破坏现有写入（Priority: P1）

作为系统 Owner，我需要每次 **versionable 写入**都把内容持久化到 append-only 版本历史，进程重启后历史不丢；同时现有 artifact 写入行为 100% 不变（默认 versionable=False 路径零改动，0 regression）。

**Why this priority**: 这是 Story 1/2 的数据地基——没有保留下来的历史内容，diff 无从计算。同时它承载 Constitution #1/#2 与 0 regression 的硬约束，是 MVP 的隐性必需项。

**Independent Test**: 对同一 `logical_file_id` 多次 `put_artifact(versionable=True)`，重启进程后查询版本历史 → 断言小文件版本内容可取回、版本号单调递增且唯一；同时跑全量回归 + 默认 `versionable=False` 路径 → 断言 artifacts 主表行为与 baseline da947ce 完全一致（0 regression）。

**Acceptance Scenarios**:

1. **Given** 对 `(task_id=T, logical_file_id=L)` 连续 `versionable=True` 写入 3 次不同内容，**When** 查询该逻辑文件版本历史，**Then** 返回 3 个版本，内容各自可取回，版本号单调递增且唯一。
2. **Given** 写入版本后进程重启，**When** 重新查询版本历史，**Then** 历史完整保留（落盘，Constitution #1）。
3. **Given** F104 改造后，**When** 跑全量回归测试，**Then** 现有 artifact 写入/读取行为与 baseline 一致，passed count ≥ baseline，0 regression。
4. **Given** 一个大文件 artifact（走 storage_ref 文件系统存储），**When** 写入新版本，**Then** 版本历史能正确关联并取回大文件历史内容（不只是小文件 inline 路径）。

---

### User Story 4 - 通过友好命名理解逻辑文件（Priority: P2）

作为非技术用户，我希望 Files Tab 用我能理解的语言呈现逻辑文件（v0.1 主要是 `progress-note:{step_id}` → "进度笔记"），而不是原始技术字符串。

**Why this priority**: 提升非技术用户体验，但不是功能能否运行的前提——映射缺失时原样显示仍可用。属于 SHOULD 级体验增强，故 P2。

**Independent Test**: 注入 v0.1 versionable 来源（progress-note）的多版本逻辑文件，断言列表显示友好名；注入未在映射表的 versionable logical_file_id，断言原样显示不报错；**负向**：注入 `tool_output:*` 重复写入（非 versionable），断言不出现在 Files Tab。

**Acceptance Scenarios**:

1. **Given** versionable 逻辑文件 `logical_file_id=progress-note:step-3`，**When** 列表渲染，**Then** 显示友好名（如"进度笔记"）。
2. **Given** versionable 逻辑文件 logical_file_id 未在友好映射表中，**When** 列表渲染，**Then** 原样显示，不报错、不留空。
3. **Given**（负向，Codex re-review 补）`tool_output:web_search` 被同工具多次写入但 `versionable=False`（SD-9 排除），**When** 打开 Files Tab，**Then** 该来源**不出现**在列表（不被误当版本化逻辑文件，避免误导性 diff）。
4. **Given**（负向，Codex round3 补）内部 `progress-note:__merged_history__` 合并汇总被多次写入（`versionable=False`，SD-9 排除），**When** 打开 Files Tab，**Then** 该合并历史**不出现**在列表（维护性产物不暴露给普通用户）。

> 注：友好映射表可预置未来 versionable 来源（如 `tool_output`→"工具输出"）作前向预留，但 v0.1 这些来源不 versionable、不进 Files Tab，故对它们在 v0.1 不可达（仅文档预留）。

---

### User Story 5 - 优雅处理无法 diff 的内容（Priority: P3）

作为用户，当文件是二进制、超大文件或两版完全相同时，我希望 Files Tab 给出清晰提示而不是崩溃或卡死。

**Why this priority**: 边界鲁棒性，保护体验与稳定性（Constitution #6 Degrade Gracefully），但不阻塞主路径，故 P3。

**Independent Test**: 分别注入二进制/超大/相同内容的两版逻辑文件，断言各自给出对应降级提示，页面不崩溃、不长时间卡死。

**Acceptance Scenarios**:

1. **Given** 逻辑文件两版均为二进制内容（非 UTF-8 可读文本），**When** 用户点开，**Then** 页面提示"二进制文件不支持行级 diff"，不尝试逐行渲染。
2. **Given** 逻辑文件版本内容超过设定阈值（超大文件），**When** 用户点开，**Then** 页面降级处理（提示过大或截断展示），不导致前端长时间卡死。
3. **Given** 上一版与当前版内容完全相同，**When** 用户点开，**Then** 明确提示"无差异"。

---

### Edge Cases

- **单版本逻辑文件**：只有 1 个版本，无上一版可比 → SD-4/CL-1 **不进 Files Tab 主列表**（完全隐藏，无占位备选，Codex re-review 修正）。
- **二进制 / 非 UTF-8 文件**：不做行级 diff，给出"二进制不支持 diff"提示（Story 5 AC-1）。
- **超大文件 diff 性能**：版本内容超阈值时降级（截断 / 提示），避免 jsdiff 在巨大文本上阻塞前端（Story 5 AC-2 / SC-005）。
- **两版内容完全相同**：明确提示"无差异"，不渲染空 diff（Story 1 AC-3 / Story 5 AC-3）。
- **被删除/清理的 artifact**：若某版本对应的底层内容已被清理（如 storage_ref 文件被删、TTL 过期），查询应优雅返回"内容不可用"占位，不抛未捕获异常（Constitution #6）。
- **大文件走 storage_ref 路径的版本**：版本历史必须正确处理 inline 与 storage_ref 两种存储形态，不能只覆盖小文件 inline（Story 3 AC-4）。

---

## 5. Requirements

### Functional Requirements

#### 后端版本保留（追溯 Story 3）

- **FR-001** [必须]：`put_artifact` MUST 新增 `versionable: bool = False` + `logical_file_id: str|None = None` 参数；**仅当 `versionable=True`** 时向 append-only `artifact_versions` 表 append 一条版本记录，逻辑文件 key = `(task_id, logical_file_id)`（`versionable=True` 时 `logical_file_id` **MUST 非空，无 name 回退**，Codex re-review 修正）。内容存储采用混合方案（SD-8）：小文件存独立内容副本，大文件存指针 + hash。默认 `versionable=False`（现有写入不传 → 不进版本表，0 regression）。（Story 3 AC-1/AC-4，SD-1/SD-3/SD-9）
- **FR-002** [必须]：系统 MUST 为每个逻辑文件 key 分配单调递增版本号。`UNIQUE(task_id, logical_file_id, version_no)` 约束 **MUST**；版本号在 `BEGIN IMMEDIATE` 事务（或 per-logical-file async lock）内 `MAX(version_no)+1` 计算，UNIQUE 冲突则重试；`ts` 兜底排序。MUST 补并发回归测试断言无重复版本号。（Story 3 AC-1，SD-2，Codex high 修正）
- **FR-003** [必须]：版本历史 MUST 落盘持久化，进程重启后历史不丢失。（Story 3 AC-2，Constitution #1）
- **FR-004** [必须]：F104 改造 MUST NOT 修改 artifacts 主表 schema 或改变现有 artifact 写入/读取行为；全量回归 0 regression vs baseline da947ce。（Story 3 AC-3，方案 A 约束）
- **FR-005** [必须]：版本记录写入 MUST 为 append-only，正常路径不更新、不删除既有版本记录（Constitution #2 精神）；**唯一例外**：删除 task/session 时版本表按 task_id 级联清理（与 `delete_artifacts_by_task_ids` 同事务，属数据归属一致、非篡改）。（Story 3 AC-1，SD-8）
- **FR-021** [必须]：版本 append MUST 与 `put_artifact` 主表写入**同一事务原子**（走独立 versionable 写连接，与主连接隔离，F104 方案 B / Codex 修复 2）——版本写失败整体回滚（一致性优先）；版本表 DDL 启动 `CREATE TABLE IF NOT EXISTS` 建好，建表失败 fail-fast 阻断服务；版本 append 失败 MUST 产生 **double-track best-effort 信号**：① structlog.warning（best-effort local log，不依赖 DB 写；经 logging_config 仅挂 StreamHandler 输出进程 stderr，无独立文件/审计 sink，可见性取决于环境是否持久化进程流，独立 sink 超 v0.1）；② `ARTIFACT_VERSION_APPEND_FAILED` event 为 **best-effort durable**（DB 可写时在独立 versionable 写连接 emit；DB locked / 不可写时降级仅 structlog best-effort local log，不强求 event——SQLite 单写锁物理限制，见 SD-10）。MUST 补回归测试：missing table / DB locked（重试至 busy_timeout 后 raise + 断言 structlog best-effort local log 降级、event best-effort 不强求）/ 版本写失败回滚。（SD-10，Codex medium / re-review round 2 + round 3 修正，Constitution #1/#6/#8）
- **FR-022** [必须]：`versionable` 判定 MUST 在**写入方**显式传参，`artifact_store` 不得在存储层硬编码 name 黑白名单（避免 Constitution #9 风险）。v0.1 versionable allowlist = `progress-note:{step_id}` **用户 step 记录**（§3.5 SD-9，**排除**内部 `progress-note:__merged_history__` 合并汇总）；`llm-*` / `tool_output:*` / `chat-import` / `__merged_history__` MUST NOT 标记 versionable（避免误导 diff + 跨 scope 暴露 + 维护性产物泄漏）。（SD-1/SD-9，Codex high/round3 修正，Constitution #5/#9）

#### 后端版本查询（追溯 Story 1 / Story 2）

- **FR-006** [必须]：系统 MUST 提供按逻辑文件 key 取版本列表的查询能力（返回版本号 + 元信息）。（Story 2 AC-1）
- **FR-007** [必须]：系统 MUST 提供取某逻辑文件"当前版"与"上一版"内容的查询能力（v0.1 仅最新两版）。（Story 1 AC-1/AC-2）
- **FR-008** [必须]：系统 MUST 提供按 task 聚合、列出该 task 下"version count ≥ 2 的逻辑文件"清单的查询能力；并提供"列出有多版本逻辑文件的 task"清单（两级导航第一级，SD-7）。（Story 2 AC-1，SD-4/SD-7）
- **FR-009** [必须]：上述版本/diff 查询能力 MUST 通过 HTTP API endpoint 暴露给前端。（Story 1/2 入口）
- **FR-010** [必须]：当某版本对应底层内容不可用时（混合方案下为**大文件指针**对应 storage_ref 文件被 session/task 删除清理；小文件 inline 副本独立不受影响），查询 MUST 优雅返回"内容不可用"占位，不抛未捕获异常。（Edge Case，Constitution #6，SD-8）

#### 前端 Files Tab（追溯 Story 1 / Story 2）

- **FR-011** [必须]：Web Workbench MUST 新增 Files Tab，含侧边栏 NavLink 与对应路由；采用两级导航（task 选择层 → 该 task 多版本逻辑文件，SD-7/CL-2）。（Story 2 入口）
- **FR-012** [必须]：Files Tab MUST 列出当前 task 下 version count ≥ 2 的逻辑文件，单版本文件不进主列表。（Story 2 AC-1，SD-4）
- **FR-013** [必须]：用户点开某逻辑文件时，Files Tab MUST 渲染"上一版 vs 当前版"的 git 风格行级 diff，新增/删除/未变行视觉可区分。（Story 1 AC-1）
- **FR-014** [必须]：diff 默认对比"最新版"与"次新版"，v0.1 MUST NOT 提供任意两版本选择对比。（Story 1 AC-2）
- **FR-015** [必须]：上一版与当前版内容完全相同时，Files Tab MUST 提示"无差异"而非渲染空 diff。（Story 1 AC-3，Story 5 AC-3）

#### 非技术用户友好展示（追溯 Story 2 / Story 4）

- **FR-016** [可选] SHOULD：Files Tab SHOULD 对 versionable 逻辑文件 `logical_file_id` 提供友好显示名（v0.1 = progress-note）；映射不到原样显示，不报错。`tool_output:*` 等非 versionable 来源不进 Files Tab（不作 v0.1 可达验收，Codex re-review 修正）。（Story 2 AC-2，Story 4，SD-5/SD-9）
- **FR-017** [必须]：Files Tab 主视图 MUST NOT 展示 artifact_id（ULID）、内部版本号、storage_ref、hash 等技术字段；这些 MUST 归入折叠/Advanced 区。（Story 2 AC-3，SD-6，CLAUDE.md Web UI/UX 规范）

#### 边界与降级（追溯 Story 5）

- **FR-018** [必须]：版本内容为二进制/非 UTF-8 时，Files Tab MUST 提示"二进制不支持 diff"，不尝试行级渲染。（Story 5 AC-1）
- **FR-019** [必须]：版本内容超过设定大小阈值时，diff 渲染 MUST 降级（截断或提示过大），不导致前端长时间卡死。（Story 5 AC-2，SC-005）
- **FR-020** [可选] MAY：系统 MAY 用内容 hash 对版本去重以缓解重复存储；v0.1 不强制（YAGNI 边界，先保证正确性再优化存储）。（tech-research §4 方案 A trade-off）

#### YAGNI 边界说明（被降级 / 移除的能力）

- **[YAGNI-移除]** 任意两版本对比：v0.1 固定最新两版（FR-014），任意版本对比移除到 F107（避免引入版本选择器 UI + 多版本查询复杂度）。
- **[YAGNI-移除]** 版本去重为 MAY（FR-020 而非 MUST）：v0.1 优先正确性与 0 regression，存储优化推后。
- **[YAGNI-移除]** 全量版本时间线浏览：移除到 F107（v0.2 git-aware）。

### Key Entities

- **逻辑文件（Logical File）**：用户视角的"一个文件"。身份由 `(task_id, logical_file_id)` 唯一确定（`logical_file_id` 由写入方在 `versionable=True` 时**显式声明、MUST 非空**，无 name 回退，Codex re-review 修正）。一个逻辑文件拥有 1..N 个版本。v0.1 只有 **versionable 来源**（§3.5 SD-9，v0.1 = progress-note）且 version count ≥ 2 的逻辑文件进入 Files Tab 主列表。它不是物理存储单元，而是对"同 task 同 logical_file_id 多次 versionable 写入"的聚合概念。
- **文件版本（File Version）**：逻辑文件在某一次写入时的内容快照。属性（概念层）：所属逻辑文件 key、版本号（单调递增 + UNIQUE）、写入时间、内容（小文件独立副本 / 大文件指针）、是否可用。append-only。"当前版" = 最大版本号，"上一版" = 次大版本号。

---

## 6. Constraints / Assumptions（约束与假设）

### 6.1 已拍板技术约束（HOW 已定，FR 不展开）

- **版本存储 = 方案 A**：append-only `artifact_versions` 历史表；`put_artifact` 内部 append；不改 artifacts 主表。
- **diff 渲染 = jsdiff（`diff` npm 包）+ 自建 CSS**：契合前端纯手工 CSS 设计系统（tokens.css）+ 非技术用户友好，避免通用 AI 风 UI；不引入 react-diff-viewer-continued / diff2html 等重依赖。

### 6.2 Constitution 合规约束

- **#1 Durability First**：版本历史落盘，重启不丢（FR-003）。
- **#2 Everything is an Event**：版本记录 append-only、不可篡改（FR-005）。
- **#6 Degrade Gracefully**：内容不可用 / 二进制 / 超大文件均降级提示，不崩溃（FR-010/FR-018/FR-019）。
- **#8 Observability is a Feature**：文件演变过程可观测（整个 Feature 价值）。

### 6.3 工程约束

- **0 regression** vs baseline da947ce：绝不破坏现有 artifact 写入行为（FR-004）。
- **面向非技术用户**：技术信息折叠到 Advanced 区（FR-017）。
- **守 H1/H2/H3**：F104 是 surface 层，不改主 Agent / Worker / Subagent 协作模型。

### 6.4 假设

- 假设显式 `versionable` 标记 + `logical_file_id`（v0.1 = progress-note）足以表达 v0.1 的"同一文件多版本"——Codex review 修正了原 `(task_id,name)` 隐式聚合假设（SD-1/SD-9）。
- 假设前端 Files Tab 挂载点稳定：App.tsx 路由 + WorkbenchLayout NavLink；`GET /api/tasks/{id}` 已返回 artifact 内容可作参考路径。

---

## 7. Success Criteria

### Measurable Outcomes

- **SC-001**：对一个有 ≥2 版本的逻辑文件，用户从打开 Files Tab 到看到该文件"上一版 vs 当前版"diff，操作步骤 ≤ 2 次点击。
- **SC-002**：对同一 versionable 逻辑文件多次写入后，**小文件（inline）版本内容 100%** 在进程重启后可取回（独立副本）；大文件（storage_ref 指针）为 best-effort——task 存活期可取回，session/task 删除清理后按 FR-010 占位（SD-8 混合方案范围，Codex critical 修正）。
- **SC-003**：F104 合入后全量回归 passed count ≥ baseline da947ce，0 regression（现有 artifact 行为零变更）。
- **SC-004**：Files Tab 主列表中，技术字段（artifact_id / 原始版本号 / storage_ref / hash）出现次数 = 0（全部归 Advanced/折叠区）。
- **SC-005**：对设定阈值内的文本文件，diff 视图在用户点开后 1 秒内完成渲染；超阈值文件触发降级提示而非卡死。
- **SC-006**：单版本逻辑文件 100% **不进 Files Tab 主列表**（CL-1 完全隐藏，纯 diff 工具，无占位备选——Codex re-review 修正），用户不会看到"点开却无 diff"的死路。测试 MUST 断言单版本条目不可见。

---

## 8. 复杂度评估（供 GATE_DESIGN 审查）

| 维度 | 值 | 备注 |
|------|-----|------|
| **新增组件数** | 3 | `artifact_versions` 历史表 + 版本服务/查询逻辑（artifact_store 内扩展）+ 前端 Files Tab（含 diff 视图组件） |
| **修改组件数** | 3 | `put_artifact`（加 versionable 参数 + append）+ progress_note 写入方（传 versionable=True，SD-9 唯一 v0.1 来源）+ Web Workbench（App.tsx + WorkbenchLayout） |
| **新增/修改接口数** | 4 | 版本列表查询 + 当前/上一版内容查询 + task 维度逻辑文件清单查询 + HTTP API endpoint（FR-006~FR-009） |
| **引入新外部依赖** | 1 | 前端 `diff`（jsdiff）npm 包；后端 0 新依赖 |
| **跨模块耦合** | 否（轻度）| 后端集中在 artifact_store 一处 + 新表；前端新增 Tab。不需要修改 2+ 个现有模块的接口契约 |
| **复杂度信号** | 2 | 数据迁移（新增 append-only 表）+ 并发控制（UNIQUE + BEGIN IMMEDIATE + 冲突重试，Codex high 修正）；无递归、无状态机 |
| **总体复杂度** | **MEDIUM** | 组件 3（< 3 边界）+ 接口 4（4-8 区间）+ 1 个复杂度信号（数据迁移）→ MEDIUM。后端是 0-regression 敏感的数据层改造，建议保留 GATE_DESIGN 人工审查 §3.3 产品取舍 + §3.3 的 NEEDS CLARIFICATION |

**MEDIUM 复杂度决议建议**：计划分 Phase（Phase 0 侦察复核 → 后端版本表 + put_artifact append → 后端查询 + API → 前端 Files Tab 列表 → 前端 diff 视图 → 边界降级 + 验证）。后端数据层改造每 Phase 后跑全量回归确认 0 regression；Codex per-Phase + Final cross-Phase review 必走（命中"数据库 schema 新增"重大架构变更节点）。

---

## 9. 待澄清事项（NEEDS CLARIFICATION 汇总）

✅ **全部澄清点已决议**（见 §3.4）：CL-1（完全隐藏）/ CL-2（先选 task 两级导航）/ CL-3（**混合方案**，Codex 修正）由用户拍板；CL-4（版本号 UNIQUE MUST，Codex 修正）自决。**无残留 NEEDS CLARIFICATION**。

---

## 10. Codex Adversarial Review 闭环（2026-06-06）

spec 大改后强制 Codex adversarial review（verdict: **needs-attention**），4 finding 全部核实成立并闭环：

| # | severity | finding | 处理 |
|---|----------|---------|------|
| 1 | **critical** | A2 大文件指针破坏"独立副本"承诺（session_delete unlink storage_ref → SC-002 对大文件不成立）| 正名**混合方案**（SD-8）+ SC-002 明确仅小文件 100%、大文件 best-effort |
| 2 | **high** | `(task_id,name)` 隐式聚合误判技术审计产物（llm-response 每轮独立 / chat-import 跨 scope 暴露）| 改**显式 `versionable` 标记 + `logical_file_id`**（SD-1/SD-3/SD-9 + FR-001/FR-022）；**用户拍板选项 1** |
| 3 | **high** | `MAX(version_no)+1` 无强唯一防线（async coroutine 让出点竞态）| UNIQUE **MUST** + BEGIN IMMEDIATE + 冲突重试 + 并发测试（SD-2/FR-002）|
| 4 | medium | 版本 append 失败策略未定义（0 regression vs 历史完整性张力）| 同事务原子 + 迁移 fail-fast + 可观测 + 回归测试（SD-10/FR-021）|

**finding 2 用户决策**：F104 v0.1 数据源边界 = **选项 1（显式 versionable 标记）**，v0.1 仅 `progress-note` 安全来源，明确排除 llm-response/tool_output/chat-import 技术审计产物。

**Codex re-review round 2（2026-06-06）**：spec 修复后 re-review verdict needs-attention，抓到 3 个**修复遗留的一致性** finding，全部闭环：

| # | severity | finding | 处理 |
|---|----------|---------|------|
| 5 | **high** | `logical_file_id` 可空 + 回退 `name` → 漏传调用点退回隐式 name 聚合后门 | 升强约束：`versionable=True` MUST 非空 `logical_file_id`、删 name 回退（SD-1/FR-001/Key Entities/US1·US3）|
| 6 | **high** | US4 用 `tool_output` 做友好命名验收，与 SD-9 排除矛盾、场景不可达 | US4 收窄到 progress-note + 补负向 AC（tool_output 不进 Files Tab）（US4/SD-5/FR-016）|
| 7 | medium | SC-006/Edge Cases 残留单版本占位备选，与 CL-1 完全隐藏冲突 | 删占位备选，只保留完全隐藏 + 测试断言不可见（SC-006/Edge Cases）|

**Codex re-review round 3（2026-06-06）**：verdict needs-attention，2 个文档同步/边界 finding，全部闭环：

| # | severity | finding | 处理 |
|---|----------|---------|------|
| 8 | **high** | tech-research.md 未标过期，§4/§7 仍写 `(task_id,name)` + tool/llm 数据源，plan 可能据此回退旧模型 | tech-research 顶部加 SUPERSEDED banner + §4/§7 标题标注（指向 spec）|
| 9 | medium | SD-9 把 progress-note 整体 versionable，未排除内部 `__merged_history__` 合并汇总 → 维护性产物会进 Files Tab | SD-9 加排除行 + FR-022 排除 + Story4 AC-4 负向验收 |

**Codex re-review round 4（2026-06-06）**：verdict needs-attention，2 个**残留旧措辞** finding，全部闭环：

| # | severity | finding | 处理 |
|---|----------|---------|------|
| 10 | **high** | §1.1/§1.2 目标层 + Story 3 仍用旧措辞（"工具多次调用演进" / "每次 artifact 写入"），实现可能据此回退 | §1.1/§1.2/Story3 统一改"versionable 写入 / progress-note 用户 step"，删工具输出演进示例 |
| 11 | medium | quality-checklist §6.1/§7/§11 仍以 ✅/⚠️ 断言旧模型（key=(task_id,name) / 无 UNIQUE / CL-1 悬而未决） | checklist §7 重写指向新模型 + §6.1/§11 标已闭环 |

**grep 复查（Codex rg 清单）**：`每次 artifact 写入` / `key=(task_id,name)` 非修正语境 / `UNIQUE MAY` / `悬而未决` → **spec.md 主事实源 0 残留**；research/clarifications/checklist 的旧措辞均在 SUPERSEDED banner / 修正语境下；trace 为历史日志。

**Codex re-review round 5（2026-06-06）= APPROVE ✅**：聚焦 spec.md 主事实源，**verdict approve，No material findings**。Codex 确认 versionable allowlist + 非空 logical_file_id（无 name 回退）+ 混合存储与 SC-002 范围 + UNIQUE MUST 并发防线 + `__merged_history__` 排除 + SD-10 同事务失败策略，在 Story/FR/SC/Edge 整体自洽，可进入 plan。

**Codex review 收敛历程**：round1 4 finding（1C+2H+1M 核心设计）→ round2 3（2H+1M 一致性）→ round3 2（1H+1M 文档同步+边界）→ round4 2（1H+1M 残留措辞）→ **round5 approve（0 finding）**。共 **11 finding（1C+5H+5M）全闭环**。
