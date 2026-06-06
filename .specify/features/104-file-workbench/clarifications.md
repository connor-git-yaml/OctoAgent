# F104 文件工作台 v0.1 — 需求澄清清单（供 GATE_DESIGN 审查）

> ⚠️ **过期提示（Codex re-review 修正）**：本文件 CL-3 推荐"A2 独立副本"、CL-4 推荐"UNIQUE MAY"、逻辑文件 key `(task_id,name)` 均为 GATE_DESIGN 时版本。经 Codex adversarial review 两轮修正，最终决议为 **CL-3 混合方案（小文件 A2 副本 + 大文件指针，SC-002 仅保证小文件）** + **CL-4 UNIQUE MUST + 冲突重试** + **显式 `versionable` 标记 + 非空 `logical_file_id`（取代隐式 name 聚合）**。**以 `spec.md` §3.2/§3.4/§3.5/§10 为准**，本文件仅留澄清推理过程。

> 子代理：需求澄清。日期 2026-06-06。feature_dir = `.specify/features/104-file-workbench/`
> 方法：基于 spec.md + research/tech-research.md + 亲自核实 `artifact_store.py` / `progress_note.py` / 全仓 put_artifact·delete 调用点。
> 约束：本文件不修改 spec.md（spec 更新由主 session 在 GATE_DESIGN 后统一做）。
> 推荐均基于代码事实，事实出处随条目标注。

---

## CL-1：单版本文件在 Files Tab 的可见性（spec §9 已有）

**问题**：Files Tab v0.1 是否完全隐藏单版本逻辑文件，还是以"无 diff 可看"占位形式弱可见？

**推荐默认**：**完全隐藏**（沿用 SD-4：主列表只放 version count ≥ 2 的逻辑文件）。

**理由（基于事实）**：
- F104 的功能定位是"diff 视图"（spec §1.1 / Story 1·2），单版本文件无上一版可比，进列表只会让用户"点开却无 diff"，与 SC-006"用户不会看到点开却无 diff 的死路"直接冲突。
- tech-research §7 + §1.5 实证：现有同名 artifact 绝大多数是技术性审计副本（`tool_output:` / `llm-response` / `llm-request-context` / `progress-note:`），其中大量是单版本一次性产物。若单版本可见，Files Tab 会退化为"全 artifact 平铺"，违反 CLAUDE.md 非技术用户原则（spec §1.3）。
- "任务产物总览"是另一种产品定位（更接近 ArtifactGrid 已有能力，tech-research §3），把它塞进 v0.1 diff 视图会模糊 Feature 边界，应留给 F107 或独立 Feature。

**影响的 FR**：FR-012（主列表过滤）、SC-006（单版本处理策略）；间接定 Files Tab 产品定位。

**是否需用户拍板**：**需用户 GATE_DESIGN 拍板（CRITICAL — 显著影响 Files Tab 产品定位/范围）**。两选项不是实现细节差异，而是"纯 diff 工具" vs "任务产物总览入口"两种本质不同的产品形态，子代理不自决。推荐完全隐藏，但最终取舍交用户。

---

## CL-2：Files Tab 入口与 task 上下文

**问题**：Files Tab 是全局侧边栏 NavLink（tech-research §3：App.tsx 路由 + WorkbenchLayout NavLink，与"聊天/智能体/技能/MCP/记忆/设置"同级），但逻辑文件 key 含 `task_id`（SD-1）。打开 Files Tab 时不存在"当前 task"上下文——侧边栏是全局导航，不绑定某个 task。候选入口模型：
- **(a)** 全局列出所有 task 的逻辑文件（按 task 分组）
- **(b)** Files Tab 内先选 task 再看其逻辑文件
- **(c)** 从 task 详情页进入 Files 视图（全局 Tab 作聚合入口/索引）

**推荐默认**：**(b) Files Tab 内先选 task 再看其逻辑文件**（两级导航：task 列表 → 该 task 的多版本逻辑文件 → diff）。

**理由（基于事实）**：
- 数据事实：逻辑文件 key = `(task_id, name)`（SD-1），FR-008/FR-012 的查询单位都是"某 task 下的逻辑文件清单"——后端聚合天然以 task 为维度，(b) 与后端查询契约 1:1 对齐，无需额外的"跨 task 全局聚合"查询（那是 spec §2.2 明确 Out of Scope 的"跨 task 同名文件聚合"邻区，避免越界）。
- (a) 全局平铺所有 task 的逻辑文件：在有大量历史 task 时列表会爆炸，且违反非技术用户原则（首屏信息过载）；还需新建"全局逻辑文件查询" endpoint，超出 FR-008 的 task 维度契约。
- (c) 从 task 详情页进入：路径最深（≥3 跳），与 SC-001"≤ 2 次点击看到 diff"有张力；且 spec §6.4 假设挂载点是"App.tsx 路由 + WorkbenchLayout NavLink"（全局 Tab），(c) 把入口主体挪到 task 详情页，偏离已假设的挂载点。
- (b) 满足 SC-001：进 Files Tab（已在侧边栏）→ 选 task → 点文件 = 2 次有效点击到 diff（选 task 与点文件各 1 次，符合"≤ 2 次点击看到 diff"的 diff 渲染步数口径）。

**影响的 FR**：FR-011（NavLink + 路由形态：需含 task 选择层）、FR-008（task 维度逻辑文件清单查询正是 (b) 的数据来源）、FR-012（"当前 task"在 (b) 下由用户选定而非隐式上下文）、SC-001（点击步数）。

**是否需用户拍板**：**建议用户 GATE_DESIGN 确认（轻量 — 推荐 (b)，可自决但与产品定位相关，连带 CL-1 一起确认更稳）**。三选项均不涉及安全/合规，架构影响有限（都落在前端 + 已有 FR-008 后端契约），子代理可自决为 (b)；但因与 CL-1 产品定位耦合，列入用户确认项一并拍板。

---

## CL-3：版本内容存储形态（指针 A1 vs 独立副本 A2）

**问题**：方案 A（append-only `artifact_versions` 表）下，版本记录是存**指针**（引用 artifacts 行/storage_ref，A1）还是存**独立内容副本**（A2）？版本历史内容是否必须独立于 artifacts 生命周期？

**核实到的代码事实**：
1. `put_artifact` 是 INSERT-only、每次新 ULID、无 upsert（artifact_store.py:84-102）→ 旧 artifact 行**物理仍在** artifacts 表，inline 内容在 `parts.content`、大文件在 `storage_ref` 路径。即"旧版本内容当前其实没被删，只是没有按 name 聚合的查询入口"。
2. **存在真实清理路径**：`delete_artifacts_by_task_ids`（artifact_store.py:197-208，`DELETE FROM artifacts WHERE task_id IN ...`）被 `session_delete.py:84` 调用——**删 task / 删 session 时会物理删除 artifacts 行**；`collect_storage_refs_for_tasks`（:185-195）配套收集 storage_ref 供事务后删文件。→ A1 指针在 session/task 删除后会**全部悬空**。
3. **progress_note 合并删旧笔记是 inert**：progress_note.py:294-300 用 `hasattr(store, "delete_artifact"/"remove_artifact")` 守卫，而 `SqliteArtifactStore` **两个方法都不存在**（artifact_store.py 全文无 `delete_artifact`/`remove_artifact`）→ 合并路径当前**不会真正删除**旧笔记 artifact。故"progress_note 合并清理导致悬空"在当前代码下不成立（spec/题面所述该风险目前 inert），但 session/task 删除路径（事实 2）是真实的悬空来源。

**推荐默认**：**A2（版本表存独立内容副本），版本历史内容必须独立于 artifacts 生命周期**。

**理由（基于事实 + Constitution）**：
- spec 内在张力：FR-001/FR-005 要求 append-only、不删版本 + FR-003 重启不丢 + SC-002"100% 历史版本内容可取回"。但事实 2 证明 artifacts 行会被 session/task 删除物理清理——若用 A1 指针，删 session 后 SC-002 直接破产（内容取不回），且与 FR-005"不删版本"语义矛盾（版本记录还在，内容没了）。**A2 让版本表自持内容，append-only 表不参与 session_delete 的 DELETE，从根上消解这一张力**。
- Constitution #1（Durability First）/ #2（Everything-is-an-Event 精神）要求版本历史是独立、不可篡改的落盘账本；A2 是"事件账本自带 payload"，A1 是"账本只记指针、payload 可被别处删"——A2 更贴合 Constitution。
- 对 FR-010 的影响：A2 把 FR-010"内容不可用"占位**收窄为真正的边界**——只有 A2 自身的大文件 storage_ref（若 v0.1 大文件版本也走文件系统副本）被外部清理才触发；inline 小文件副本不会因 artifacts 清理而失效。A1 下 FR-010 会变成**常态**（删一个 task 就批量不可用），把降级路径从边界变成主路径，体验差。
- 存储成本：A2 的重复存储用 FR-020（hash 去重，MAY）缓解；且 v0.1 实证多版本主要是文本审计副本（tech-research §7），inline 阈值 4KB（ARTIFACT_INLINE_THRESHOLD，artifact_store.py:14/63），单版本体量小，重复成本可控。正确性（SC-002）优先于存储优化（spec YAGNI 边界已声明先正确性再优化）。
- **A2 不破坏 0 regression**（FR-004）：A2 只新增独立表、`put_artifact` 内 append 一份内容副本，artifacts 主表与现有写入/读取行为零改动。

**张力消解小结**：A2 使 FR-001/FR-005（不删版本）与 SC-002（100% 可取回）一致成立，并把 FR-010 从"删 task 即常态触发"降为"罕见边界"。A1 会让 FR-005 与 FR-010 实际语义打架（版本在、内容没）。

**影响的 FR**：FR-001（保留内容的形态：副本 vs 指针）、FR-003（落盘独立性）、FR-005（append-only 不删版本的"内容也不丢"语义）、FR-007/FR-010（取上一版内容 / 不可用占位的触发频率）、FR-020（去重缓解 A2 成本）、SC-002（可取回率）。

**是否需用户拍板**：**需用户 GATE_DESIGN 拍板（CRITICAL — 多选项有本质不同的架构与存储成本影响）**。A1 vs A2 决定存储成本量级 + Durability 语义边界 + FR-010 是边界还是主路径，属架构级决策，子代理不自决。推荐 A2，理由如上。
> 注：D-A（方案 A append-only 表）是用户已拍板约束（spec §3.1），CL-3 是在方案 A 内部对"内容存储形态"的二级澄清，不与已拍板方向冲突。

---

## CL-4：版本号并发分配

**问题**：版本号 = "该逻辑文件 key 已有版本数 + 1"（SD-2/FR-002）。并发写同一 `(task_id, name)` 存在"读计数 → +1 → 写"竞态。v0.1 是否可能并发写同一逻辑文件？如何保证版本号唯一递增？是否可改用 ts 排序免分配版本号？

**核实到的代码事实**：
1. **写入收敛在单一方法 `put_artifact`**（tech-research §1.5），4 类调用点：hooks_legacy.py:276（工具大输出）、progress_note.py:134/290（进度笔记）、task_service.py:438（文本预处理）、chat_import_service.py:529/641（导入）。
2. **store 持有单个 `aiosqlite.Connection`**（artifact_store.py:43 `__init__(self, conn)`，所有方法 `await self._conn.execute`）→ 同一 store 实例上的写是经由单连接串行化的（aiosqlite 单连接 = 单 writer 队列），不是真多线程并发写同一行。
3. 同一逻辑文件多版本的产生机制（tech-research §7）都是**同一 task 执行流内的顺序写**（progress-note 多次更新 / tool 多次调用 / 每轮对话 llm-response）——v0.1 单用户、单 task 执行流内，对同一 `(task_id, name)` 的写本质是顺序的，不存在两个独立请求同时抢写同一逻辑文件的常见场景。

**推荐默认**：**版本号在 append 事务内用 `SELECT COALESCE(MAX(version_no),0)+1 FROM artifact_versions WHERE task_id=? AND name=?` 计算并写入，整个"读 MAX → 计算 → INSERT"包在同一 SQLite 事务里（依赖单连接串行 + 事务原子性保证唯一递增）；不引入额外锁。**同时**保留 `ts`（写入时间戳）作为天然排序兜底**——"当前版/上一版"的判定以 `(version_no DESC, ts DESC)` 排序，即便版本号在极端情况下并列，ts 仍可决定次序。

**是否可改用 ts 排序免分配版本号**：**不建议完全免版本号**。
- 仅 ts 排序的问题：①同一执行流内连续写可能 ts 同毫秒/同值（artifact.ts 是写入侧赋值，非 DB 自增），ts 并列时"次新版"判定不稳定；②FR-002 明确要求"单调递增版本号"、Story 3 AC-1 断言"版本号单调递增"、SD-2 定义"当前版=最大版本号"——免版本号需改这些 FR/AC，扩大改动面。
- 推荐折中：**版本号为主键序、ts 为兜底排序键**，两者都存。版本号满足 FR-002/SD-2 语义且查询直观（tech-research §4：`ORDER BY version_no DESC LIMIT 2`）；ts 提供并列容错。

**并发结论**：v0.1 在单连接串行 + 单 task 顺序写的现实下（事实 2/3），"读 MAX+1"竞态在正常路径不会触发；事务包裹 + 单连接已足够保证唯一递增，**无需引入分布式锁或 DB 层 UNIQUE 约束**。可选加固（MAY）：对 `artifact_versions` 加 `UNIQUE(task_id, name, version_no)` 约束作为 defense-in-depth，违反则重试——但 v0.1 不强制（YAGNI，避免为不存在的并发场景过度设计）。

**影响的 FR**：FR-002（版本号单调递增的分配方式）、FR-005（append-only 事务边界）；Story 3 AC-1（单调递增断言）。

**是否需用户拍板**：**可自决（非 CRITICAL）**。属实现层并发正确性细节，不涉及安全/合规/核心范围，多选项无本质架构分歧（都在 artifact_store 一处 + 新表内解决）。推荐"事务内 MAX+1 + ts 兜底排序，不引锁，UNIQUE 约束为 MAY"。plan 阶段落实即可，无需占用 GATE_DESIGN 用户决策带宽。

---

## GATE_DESIGN 用户重点确认 2 项（CRITICAL）

| # | 澄清点 | 推荐 | 为何需用户拍板 |
|---|--------|------|----------------|
| 1 | **CL-1** Files Tab 是否隐藏单版本文件 | 完全隐藏（纯 diff 工具定位） | 决定 Files Tab 产品形态（纯 diff 工具 vs 任务产物总览），范围级取舍 |
| 2 | **CL-3** 版本内容存储形态 A1 指针 vs A2 独立副本 | A2 独立副本（内容独立于 artifacts 生命周期） | 决定存储成本量级 + Durability(SC-002)语义 + FR-010 是边界还是主路径，架构级决策 |

**连带轻量确认 1 项（推荐随上面一起拍板）**：

| # | 澄清点 | 推荐 | 说明 |
|---|--------|------|------|
| 3 | **CL-2** Files Tab 入口模型 | (b) Tab 内先选 task 再看逻辑文件 | 子代理可自决，但与 CL-1 产品定位耦合，建议一并确认 |

**子代理可自决 1 项（无需用户）**：

| # | 澄清点 | 自决结论 |
|---|--------|----------|
| 4 | **CL-4** 版本号并发分配 | 事务内 `MAX(version_no)+1` + ts 兜底排序，单连接串行已保证唯一递增，不引锁；`UNIQUE(task_id,name,version_no)` 为 MAY 加固 |
