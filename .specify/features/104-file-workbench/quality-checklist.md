# F104 文件工作台 v0.1 — Spec 质量检查清单

> ⚠️ **过期提示**：本清单基于 GATE_DESIGN 时 spec（A2 独立 / UNIQUE MAY / 隐式 `(task_id,name)`）。经 Codex review 两轮修正为**混合方案 / UNIQUE MUST / 显式 versionable + 非空 logical_file_id**，**以 `spec.md` §10 为准**。

> 对象：`.specify/features/104-file-workbench/spec.md`
> 上游：`research/tech-research.md`
> 日期：2026-06-06
> 图例：✅ 通过 / ⚠️ 需注意（不阻塞但建议处理）/ ❌ 不通过（需修复）

---

## 1. 需求完整性（User Story 验收 + 可独立测试 + MVP 构成）

| # | 检查项 | 结论 | 说明（指向 spec 位置）|
|---|--------|------|------|
| 1.1 | 每个 User Story 有 Given-When-Then 验收场景 | ✅ | US1（§4 AC 1-3）/ US2（AC 1-3）/ US3（AC 1-4）/ US4（AC 1-2）/ US5（AC 1-3）全部 GWT 格式 |
| 1.2 | 每个 User Story 有 Independent Test | ✅ | 5 个 Story 各有 "Independent Test" 段，构造-断言路径具体可执行（§4 各 Story）|
| 1.3 | P1 集合构成可独立交付的 MVP | ✅ | US1（diff 视图）+ US2（入口列表）+ US3（版本落盘地基）三条 P1 互补，明确"缺一不可"（US2/US3 Why this priority）|
| 1.4 | 优先级分层合理 | ✅ | P1=核心闭环 + 数据地基；P2=友好命名（缺失仍可用）；P3=边界降级。理由均落在"是否阻塞主路径"，判定一致（§4）|
| 1.5 | Priority 与 FR 必须/可选等级一致 | ✅ | US4(P2)→FR-016 SHOULD；US5(P3)→FR-018/019 MUST（降级是稳定性硬要求，合理升级，§5）|

---

## 2. FR 可测试性 + 可追溯（每条 FR 追到 ≥1 Story）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 2.1 | 每条 FR 标注溯源 Story | ✅ | FR-001~019 全部带 (Story X AC-Y) 标注；FR-020 溯源 tech-research §4（YAGNI MAY 项，可接受）|
| 2.2 | FR 可测试（含可观测断言点）| ✅ | FR 多为可断言行为（append 一条记录 / 单调递增 / 0 regression / 字段出现次数=0）。SC-003/004 给出量化判据 |
| 2.3 | 反向覆盖：每个 Story 有 FR 支撑 | ✅ | US1→FR-007/013/014/015；US2→FR-006/008/011/012；US3→FR-001~005；US4→FR-016；US5→FR-010/018/019 |
| 2.4 | MUST/SHOULD/MAY 用词规范 | ✅ | FR-016 SHOULD、FR-020 MAY 明确标注；其余 [必须] MUST。无含糊"应该尽量"类措辞 |
| 2.5 | 无悬空 FR（无 Story 的需求）| ✅ | 未发现无来源 FR |

---

## 3. 范围清晰（In/Out 无歧义 + 无蔓延）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 3.1 | In Scope 边界明确 | ✅ | §2.1 六类范围（版本保留/查询/聚合/Files Tab/友好展示/可观测）逐条具体 |
| 3.2 | Out of Scope 含归属与理由 | ✅ | §2.2 八项排除项均有"归属 Feature + 理由"（branch/blame→F107、behavior 文件→F107、改协作模型→不做等）|
| 3.3 | 无范围蔓延 | ✅ | 明确排除版本编辑/回滚、跨 task 聚合、任意两版对比、主表 schema 变更，收窄到"最新两版只读 diff" |
| 3.4 | v0.1 与 v0.2(F107) 切割清晰 | ✅ | 任意两版对比、全量时间线、git-aware 一致归 F107（§2.2 + §5 YAGNI）|

---

## 4. Constitution 合规（#1/#2/#6/#8 + 0 regression）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 4.1 | #1 Durability First | ✅ | FR-003 落盘 + 重启不丢；US3 AC-2；SC-002（100% 可取回）|
| 4.2 | #2 Everything-is-an-Event（append-only 不可篡改）| ✅ | FR-005 append-only 不更新不删除；§6.2 明示精神对齐 |
| 4.3 | #6 Degrade Gracefully | ✅ | FR-010（内容不可用占位）/FR-018（二进制）/FR-019（超大）三路降级，不抛未捕获异常 |
| 4.4 | #8 Observability is a Feature | ✅ | §1.1 整 Feature 价值即文件演变可观测；§2.1 "diff 查询路径可观测" |
| 4.5 | 0 regression 硬约束显式化 | ✅ | FR-004（不改主表/行为）+ SC-003（passed≥baseline da947ce）+ §6.3，方案 A 选型即为此服务 |

---

## 5. 非技术用户原则（FR-017 折叠 + CLAUDE.md Web UI/UX）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 5.1 | 技术字段折叠到 Advanced/折叠区 | ✅ | FR-017 MUST NOT 展示 artifact_id/版本号/storage_ref/hash；SD-6；SC-004 量化(出现次数=0) |
| 5.2 | 技术性 name 友好映射 | ✅ | FR-016 SHOULD + SD-5，映射缺失原样显示不报错（降级友好）|
| 5.3 | 主界面不堆砌技术审计副本 | ✅ | SD-4 只展示 ≥2 版本逻辑文件，自然过滤一次性技术产物（§3.3 核心产品设计点）|
| 5.4 | 引用 CLAUDE.md Web UI/UX 规范 | ✅ | §1.3 / FR-017 / §6.3 显式援引非技术用户原则 |

---

## 6. Edge Cases 覆盖

| # | 边界场景 | 结论 | 说明 |
|---|---------|------|------|
| 6.1 | 单版本文件 | ✅ | SD-4/CL-1 **完全隐藏**（GATE_DESIGN 拍板，无占位备选）；Edge Cases + SC-006 + 测试断言不可见 |
| 6.2 | 二进制 / 非 UTF-8 | ✅ | US5 AC-1 + FR-018 + Edge Cases |
| 6.3 | 超大文件 | ✅ | US5 AC-2 + FR-019 + SC-005（阈值内 1s / 超阈值降级）|
| 6.4 | 两版内容相同 | ✅ | US1 AC-3 + US5 AC-3 + FR-015（提示"无差异"不渲染空 diff）|
| 6.5 | 被清理/TTL 过期 artifact | ✅ | Edge Cases + FR-010（"内容不可用"占位，Constitution #6）|
| 6.6 | 大文件 storage_ref 路径版本 | ✅ | US3 AC-4 + FR-001（显式覆盖 inline 与 storage_ref 两种形态）+ Edge Cases；避免只测 inline 小文件的常见漏洞 |

---

## 7. 已拍板决策一致性（spec ↔ research）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 7.1 | 方案 A append 表（混合存储）| ✅ | spec §3.1 D-A / §3.4 SD-8：append-only 表 / put_artifact 加 versionable 参数后 append / 不改主表 / **混合存储（小文件 A2 副本 + 大文件指针）**（Codex review 修正：原"纯 A2 / key=(task_id,name)"已废）|
| 7.2 | jsdiff + 自建 CSS | ✅ | spec §3.1 D-DIFF / §6.1 与 research §5 候选 1 一致，明确排除 react-diff-viewer/diff2html |
| 7.3 | 逻辑文件身份 = 显式 versionable + 非空 logical_file_id | ✅ | spec SD-1/SD-9：**显式 `versionable=True` + 非空 `logical_file_id`** 取代隐式 (task_id,name)；v0.1 仅 progress-note 用户 step（排除 `__merged_history__` / tool_output / llm / chat-import）；版本号 **UNIQUE MUST**（Codex review 3 轮修正：原"key=(task_id,name)/无 UNIQUE/三类数据源"已废）|
| 7.4 | 写入集中在 put_artifact 一处 | ✅ | put_artifact 单一写方法（4 调用点）；SD-3 修正为**仅 versionable=True 才 append 版本**（非所有写入，0 regression 更彻底）|
| 7.5 | behavior 文件不走 artifact_store | ✅ | spec §2.2 排除 + research §7 边界厘清一致（归 F107）|

---

## 8. H1/H2/H3 守护（不改 Agent 协作模型）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 8.1 | 明确声明不触碰协作模型 | ✅ | §2.2 "改 Agent 协作模型 H1/H2/H3 → 不做"；§6.3 "守 H1/H2/H3，surface 层"；Feature 性质行(§11)定性 surface 改造 |
| 8.2 | 无 delegate/worker/subagent 路径改动 | ✅ | FR 全部落在 artifact_store backend + frontend Files Tab，无任何委托/会话/persona 触点 |

---

## 9. 验收可度量（SC 可测 + 技术无关）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 9.1 | SC 可量化 | ✅ | SC-001(≤2 点击)/SC-002(100% 可取回)/SC-003(passed≥baseline)/SC-004(技术字段=0)/SC-005(1s 渲染)/SC-006(100% 按 SD-4 处理)|
| 9.2 | SC 技术无关（描述成果非实现）| ⚠️ | 多数为用户可观测成果；SC-003 引用 baseline commit da947ce（工程内部锚点），属于 0 regression 必要约束、可接受，但严格意义偏实现侧 |
| 9.3 | SC 覆盖三条 P1 价值 | ✅ | diff 可见(SC-001/005)、落盘(SC-002/003)、非技术友好(SC-004/006)全覆盖 |

---

## 10. YAGNI 边界（被移除项理由充分）

| # | 检查项 | 结论 | 说明 |
|---|--------|------|------|
| 10.1 | 移除项有显式理由 | ✅ | §5 YAGNI-移除三项（任意两版对比/版本去重 MAY/全量时间线）各带理由 |
| 10.2 | 移除项有去向 | ✅ | 任意两版对比→F107；时间线→F107；去重降级为 FR-020 MAY |
| 10.3 | MAY 项不阻塞 MVP | ✅ | FR-020 去重明确"v0.1 不强制，先正确性后优化存储" |

---

## 11. 关键发现（GATE_DESIGN 重点关注）

1. **✅ CL-1 已闭环（GATE_DESIGN 拍板）**：用户选**完全隐藏单版本文件**（纯 diff 工具定位）。FR-012/SC-006 已统一为"单版本不进主列表 + 测试断言不可见"，无占位备选。原 ⚠️（产品定位二义）已消除。

2. **✅ 版本号并发已闭环（Codex review 修正）**：SD-2/FR-002 已升级——`UNIQUE(task_id, logical_file_id, version_no)` **MUST** + `BEGIN IMMEDIATE`/async lock 包住 MAX+1→INSERT + 冲突重试 + ts 兜底 + 并发回归测试。原 ⚠️（无并发控制）已消除。

---

## 汇总

- 检查项总数：**41**
- ✅ 通过：**37**
- ⚠️ 需注意：**1**（9.2 SC-003 baseline 锚点，0 regression 必要约束可接受）；原 6.1/§11-1（CL-1）+ §11-2（并发）已在 GATE_DESIGN + Codex review 闭环
- ❌ 不通过：**0**

spec 整体质量高：Story-FR-SC 三向可追溯、Constitution 四条显式合规、Edge Cases 覆盖 storage_ref 与清理场景、YAGNI 边界清晰。原两个 ⚠️（CL-1 定位 + 版本号并发）已在 GATE_DESIGN（完全隐藏）+ Codex review 4 轮（UNIQUE MUST + 显式 versionable + 混合存储 + __merged_history__ 排除）全部闭环，**以 spec §10 为准**。
