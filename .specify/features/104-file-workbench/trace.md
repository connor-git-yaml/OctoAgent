# F104 文件工作台 v0.1（diff 视图）— Trace

> 模式：spec-driver feature（完整 10 阶段编排）
> preset：quality-first（全 Opus）/ gate_policy：balanced
> 基线：origin/master da947ce（M6 baseline）

## 初始化

- [init] worktree `feature/104-file-workbench-v01` @ da947ce（EnterWorktree 进入）
- [init] orchestrator-cli.mjs 缺 zod 依赖 → 主 session 手动编排（直接读 `config/orchestration.yaml`）
- [init] feature_dir = `.specify/features/104-file-workbench/`

## Phase 0 / 0.5（inline）

- [Phase 0] constitution_check：PASS（CLAUDE.md Constitution 10 条 + `.specify/memory/` 存在）
- [Phase 0.5] research_mode = **codebase-scan**（F104 是内部 surface Feature，无市场调研需求 → 跳过 product-research；diff 库选型为唯一在线调研点候选）

## 块 A 实测侦察（= Phase 1b tech-research 强化）

- [块A] 方法：主 session 主导 + 2 Explore 子代理并行（backend artifact/snapshot + frontend Workbench）+ 主 session 亲自核实 `artifact_store.py`
- [块A] 产物：`research/tech-research.md`
- [块A] 核心结论：
  - artifact 旧版本**不可取**（put_artifact INSERT-only / ULID PK / version 静态计数器）→ 确认 handoff §6 纠偏，F104 必须动 backend
  - artifact **无逻辑文件概念**（每次 put = 新 ULID 独立行）→ 需先定义"逻辑文件身份"
  - SnapshotStore **无 diff/history**（仅 prefix-cache 冻结快照）→ 不可复用
  - 前端 React 19 + 纯手工 CSS，**无 diff 库**；Files Tab 挂载点明确（App.tsx + WorkbenchLayout）
- [决策] 版本历史存储方案（spec 第一决策点）：用户拍板 **方案 A**（append-only `artifact_versions` 表，逻辑文件 key=(task_id,name)）
- [决策] diff 渲染：用户拍板 **jsdiff + 自建 CSS**（契合纯手工 CSS + 非技术用户 UX）
- [待澄清] artifact 版本数据来源（同一逻辑文件多版本是否有真实写入场景）→ specify 阶段实证

## Phase 2 specify（进行中）

- [实证] 版本数据来源专项侦察 → 确认有真实多版本源（progress-note/tool_output/llm-response，测试证实）；behavior 文件不走 artifact（F104 外）；暴露 UX 设计点（技术性 artifact 不应平铺给非技术用户）
- [Phase 2] 委托 spec-driver:specify（opus）起草 spec.md，含详尽上下文注入（方案 A + jsdiff + 侦察事实 + UX 设计点 + 范围 + Constitution/H1H2H3 约束）
- [Phase 2] spec.md 产出：5 User Story（P1×3/P2×1/P3×1）+ 20 FR + 6 SC + 2 Key Entity + 6 Edge Case + 1 CL，复杂度 MEDIUM
- [Phase 2] 主 session 审查发现 3 个设计 gap → 注入 clarify：
  - CL-2 Files Tab 入口与 task 上下文（全局 NavLink vs key 含 task_id）
  - CL-3 版本内容存储形态（A1 指针 vs A2 独立副本；旧 artifact 行实测未物理删除，FR-001/005/010 有张力）
  - CL-4 版本号并发分配竞态（SD-2 未提并发模型）

## Phase 3 clarify + checklist（并行 DESIGN_PREP_GROUP）

- [Phase 3] 委托 clarify（整理 CL-1~CL-4 + 推荐）+ checklist（spec 质量清单）并行
- [Phase 3] clarify → clarifications.md（CL-1~4，含代码核实纠正 progress_note 合并 inert）；checklist → quality-checklist.md（41 项 ✅37/⚠️4/❌0）
- [Phase 3] 主 session 审查：CL-3 对抗复核（一度倾向 A1，被 session_delete 物理删证据 + Constitution#2 说服 A2 + 大文件指针/删task级联细化）
- [GATE_DESIGN] 硬门禁通过：CL-1 完全隐藏 / CL-2 先选 task 两级导航 / CL-3 A2 独立副本（用户拍板）+ CL-4 自决
- [Phase 3] spec.md 决议固化：§3.4 + SD-7/SD-8 + Status=Approved + 8 处 FR/section 更新 + §9 清空
- [Phase 3] 启动 Codex adversarial review（background, id bof8ey7hh）审 spec 设计（commit 前强制，working-tree untracked 文档）
- [Phase 3] review 跑期间预读 plan backend 上下文（只读，不阻塞）：
  - sqlite_init.py migration 模式：新表用 `CREATE TABLE IF NOT EXISTS` DDL 常量 + init 注册 → artifact_versions 纯新增表天然 0-regression（老库自动建空表）
  - session_delete.py 级联事务：CL-3 接入点 = line 83-85（delete_artifacts）邻接加 `delete_artifact_versions_by_task_ids`，事务内（line 96 commit 前）；A2 大文件指针指向 artifacts storage_ref（已被 collect_storage_refs 清理覆盖），版本表只需删行
- [Phase 3] Codex review 完成（verdict needs-attention，4 finding 全部核实成立）：
  - [critical] A2 大文件指针破坏"独立副本"承诺（session_delete unlink storage_ref → 大文件历史不独立，SC-002 对 storage_ref 不成立）→ 自决修：正名混合方案 + 明确 SC-002 仅小文件 100%
  - [high] (task_id,name) 误判技术审计产物为同一文件（llm-response 每轮/tool_output 每次/chat-import 全局 task+文件名，跨 scope 暴露风险）→ ★ 动摇数据源根基 → 问用户拍板数据源边界
  - [high] MAX(version_no)+1 无强唯一防线（async coroutine 在 read MAX 与 INSERT 间有 await 点 → 重复版本号）→ 自决修：UNIQUE 升 MUST + 冲突重试 + BEGIN IMMEDIATE/async lock + 并发测试
  - [medium] 版本 append 失败策略未定义（同事务回滚 vs 吞错）→ 自决修：同事务原子 + 迁移 fail-fast + 失败可观测 + DB locked/missing table 回归测试
- [Phase 3] finding 2 用户拍板：选项 1 显式 versionable 标记（v0.1 仅 progress-note，排除 llm/tool/chat-import）
- [Phase 3] spec.md 4 finding 全闭环（17+ 处 Edit）：§0 事实 + §3.2 SD-1/2/3 + §3.4 SD-8 混合 + §3.5 SD-9/SD-10 + FR-001/002/005/010/021/022 + Key Entities + SC-002 + §8 + §10 Codex 闭环表
- [Phase 3] Codex re-review round 2 完成（needs-attention，3 finding 全部修复遗留的一致性 gap）：
  - [high] logical_file_id 可空+回退 name → 升强约束 MUST 非空、删回退
  - [high] US4 tool_output 友好命名验收与 SD-9 排除矛盾不可达 → 收窄 progress-note + 补负向 AC
  - [medium] SC-006/Edge Cases 占位备选与 CL-1 冲突 → 删占位
- [Phase 3] round 2 修复完成 + 同步过期文档 + 全文 grep 核查通过
- [Phase 3] Codex re-review round 3 完成（needs-attention，2 finding，收敛趋势 4→3→2）：
  - [high] tech-research.md 漏标过期（§4/§7 仍写旧模型）→ 加 SUPERSEDED banner + §4/§7 标注
  - [medium] SD-9 未排除内部 progress-note:__merged_history__ 合并汇总 → SD-9 排除行 + FR-022 + Story4 AC-4 负向
- [Phase 3] round 3 修复完成（文档同步 + allowlist 边界）
- [Phase 3] Codex re-review round 4（needs-attention，2 残留旧措辞）：
  - [high] §1.1/§1.2/Story3 旧措辞（工具多次调用演进/每次 artifact 写入）→ 统一改 versionable 措辞
  - [medium] checklist §6.1/§7/§11 旧 ✅/⚠️ 断言 → 重写指向新模型 + 标已闭环
- [Phase 3] round 4 修复完成 + grep 复查（Codex rg 清单）：spec.md 主事实源 0 残留；辅助文档 banner 覆盖
- [Phase 3] **Codex re-review round 5 = APPROVE ✅**（聚焦 spec.md，No material findings）。5 轮收敛：4→3→2→2→0 finding，共 11 finding 全闭环
- [GATE: spec 定稿] spec 阶段完成 → commit spec 制品 b65ed17（纯文档，SKIP_E2E）

## Phase 4 plan + Phase 5 tasks

- [Phase 4] 委托 spec-driver:plan（opus）→ plan.md（5 Phase + schema + 事务边界决策 + 测试分层 + 风险）
- [Phase 4] plan 子代理实测纠正：①put_artifact 不自 commit（FR-021 同事务天然满足）→ SD-2 BEGIN IMMEDIATE 与隐式事务冲突风险（标 Phase 1 HIGH 必实测 + Codex review 重点）；②API 鉴权实际是路由级 require_front_door_access（非 handler Bearer）
- [Phase 4] 主 session 审查 plan：忠实 spec + Constitution 10 条核查 + 事务边界 HIGH 处理得当；小发现 FR-020 MAY 未注明跳过（让 tasks 注明）
- [Phase 5] tasks.md 产出（38 task / 5 Phase，FR 21/22 + FR-020 deferred，SC 6/6，FR×Task 矩阵 = analyze 一致性核心）
- [Phase 4-5] plan+tasks Codex review（needs-attention，1C+2H，聚焦单连接事务正确性）：
  - [critical] BEGIN IMMEDIATE put_artifact 开/调用方 commit 在单连接 async 下不可靠（事务连接级跨 await，mixed-writer 污染）
  - [high] UNIQUE 重试无 SAVEPOINT 粒度（冲突 rollback 撤主表 / 不 rollback 下轮 BEGIN 失败）
  - [high] SD-10 失败回滚给不 rollback 的调用方（脏事务 + 失败事件被回滚吞）
- [Phase 4-5] 修复采纳 Codex 核心：versionable 自包含事务 + SAVEPOINT 重试 + durable 失败信号
- [Phase 4-5] plan+tasks re-review round 2（needs-attention，1C+2H 修复遗留矛盾/缺口）：
  - [critical] plan §0.2/§0 旧"调用方 commit"与 §1.2 自包含矛盾 + 逻辑顺序错 → 消除旧文案 + §1.2 改两互斥分支（先校验后事务，主表 INSERT 在事务内）
  - [high] durable 事件无 emit 路径（artifact_store 无 event_store）→ 实测 StoreGroup 持 event_store 共享 conn + append_event_committed 独立提交 → 注入 wiring + events 表断言
  - [high] T5.1 mixed-writer 测试与范围取舍矛盾 → 条件化（依 T1.3 硬 gate：顺序→串行化不变量+合成交错 xfail；并发→升级后测互不污染）
- [Phase 4-5] round 2 修复完成（plan §0.2/§0/§1.2 + §4 + tasks T1.3/T1.4/T1.7/T5.1/T5.4 + 文件索引）
- [Phase 4-5] round 3 re-review：codex 挂起卡住（输出仅 3 行 Turn started）→ 主 session 自查收敛（grep 无矛盾残留，line 40 修正语境；新事务模型 19 处一致贯穿）
- [GATE_TASKS] 硬门禁通过（用户拍板）：✅ 批准进 implement + ✅ mixed-writer = 实测驱动（T1.3 硬 gate）
- [GATE_TASKS] commit plan+tasks 制品 → implement（plan Phase 1 起）

## Implement（plan Phase 1-5）

### Phase 1（backend 版本表）
- [T1.3] 事务硬 gate 实测 → **推翻 BEGIN IMMEDIATE**（默认 isolation_level='' 报 "cannot start transaction within transaction"），选定 _write_lock + 隐式事务 + SAVEPOINT（phase-1-recon.md 权威）
- [T1.1-T1.8] 委托 implement 子代理实现（artifact_versions DDL + put_artifact versionable 自包含 + event wiring append_event_committed + progress_note 接入 + session 级联），主 session 审查 T1.4 事务正确性 + 补 T1.8t 断言
- [T1.5/T1.6/T1.8t] 测试 34 passed（SAVEPOINT 重试/失败回滚/级联/versionable 断言）；core+tooling 656 passed 0 regression；子代理改现有测试=签名适配非 mask
- [T1.9] Phase 1 Codex review：2 finding（[high] versionable 未检查 in_transaction→污染调用方共享连接事务 / [medium] 文件写在锁外+失败不清理）→ 修复（in_transaction 检查 raise + 文件写移入 _write_lock + 失败 unlink-if-new）+ 2 测试
- [T1.9] Phase 1 re-review round 2：1 high（versionable 大文件失败路径覆盖既有文件内容，medium 修复遗留）→ 修复（写前 exists 则 raise 拒绝覆盖 + _process_content 移入 try + 失败 unlink 本次新建）+ test_existing_file_not_overwritten（既有 bytes 不变）
- [T1.9] Phase 1 re-review round 3：2 high（均真并发场景，本质用户已拍板 mixed-writer）：
  - [high#1] in_transaction 入口检查不阻止并发默认写加入 versionable 事务（= GATE_TASKS 拍板的 mixed-writer）
  - [high#2] 大文件 exists() TOCTTOU + 失败清理跨 writer（真并发文件竞态）
  - 二者 v0.1 顺序队列不触发；完全正确 = 连接级写锁/独立连接（架构 follow-up 超 v0.1）
- [T1.9] 用户拍板**折中**：
  - high#2 修复：O_EXCL 原子独占创建（_process_content exclusive 参数）+ owns_file 标记（仅本次独占创建成功才失败清理，O_EXCL 失败=他人文件不误删）+ test_existing_file_not_overwritten（既有 bytes 不变）
  - high#1 **归档**：mixed-writer 事务污染 = GATE_TASKS 拍板的实测驱动；真并发完全正确 = 连接级写锁架构 follow-up（超 v0.1），已记 plan §4 风险表 + spec SD-8 已知约束；T1.3 实测顺序队列 v0.1 不触发
- [T1.10] Phase 1 收口：commit 6fa4010（746 回归 0 regression + e2e_smoke 8/8 + 3 轮 review 闭环）

### Phase 2（后端查询 + HTTP API）
- [T2.1-T2.5] 委托 implement 子代理：4 查询方法 + routes/files.py 4 endpoint + main.py front-door 路由级鉴权；主响应无技术字段（FR-017/SC-004）+ binary/unavailable 语义分离 + 友好命名兜底；762 回归 0 regression + files.py 审查通过
- [T2.6] Phase 2 Codex review：2 finding（[high] oversize 事后打标仍 read_bytes 全量读+返回超大内容 FR-019/SC-005 后端失效 / [medium] slash logical_file_id path 参数不匹配 diff/versions 路由）→ 委托子代理修：oversize 读前 size 元数据拦截 + logical_file_id path→query + 顺手清 Phase 1 6 E501（35 测试绿 + 768 回归 0 regression）
- [T2.6] Phase 2 re-review round 2：2 finding（[high] oversize 短路在 storage_ref 文件存在检查之前→超大已删文件误报 available+oversize 而非 unavailable，混淆三态 / [medium] get_current_and_previous 一次性 SELECT content，超大 inline 已被 SQL 读入再标 oversize 非真拦截）→ 委托子代理修：storage_ref 先文件存在检查再 oversize（优先级）+ 两阶段查询（先元数据 size 判定，inline 未超阈值再懒加载 content）（39 测试 + 772 回归 0 regression）
- [T2.6] Phase 2 re-review round 3：0 high + 1 medium（storage_ref oversize 信任陈旧 DB size，read_bytes 前未 stat 实际文件→TOCTOU/metadata stale 可绕过读前拦截 FR-019）→ 主 session 自改 stat 实际大小用 max(DB size, actual) 判 oversize + 委托子代理补 TOCTOU 测试（actual>db→oversize 不 read_bytes，spy read_bytes_called==0）+ 清 SIM105（contextlib.suppress）
- [T2.7] Phase 2 收口：oversize 3 轮 review 收敛（high→high→medium，0 high 残留）；40 Phase2 测试（store 27 + endpoint 13）+ 773 全回归 0 regression + ruff All checks passed（src+测试）→ commit c4f33d9（8 files +1166/-17，不 push）

### Phase 3（前端 Files Tab 两级导航）
- [env] worktree frontend 无 node_modules → npm install（exit 0）
- [T3.1-T3.6] 委托 implement 子代理：client.ts 4 fetch（走内部 apiFetch front-door 鉴权，非裸 fetch）+ types/index.ts 8 类型 + FilesCenter.tsx 两级导航（task→逻辑文件→diff 基础并排 pre）+ App.tsx lazy route + WorkbenchLayout 导航项「文件」+ FilesCenter.test 6 测试
- [审查] 主 session：两级导航 state/回退/三态完整 + binary/oversize/unavailable 降级文案 + 非技术 UX（display_name 不暴露技术字段）+ apiFetch 鉴权 + CSS token（--cp-soft/--cp-radius-md tokens.css 确在含 dark mode）；小观察 openTask/openFile 无 cancelled 保护（快速切换轻微 race，v0.1 可接受）
- [验证] tsc -b 0 错 + FilesCenter 6 + WorkbenchLayout 3 tests passed
- [回归] 前端全 vitest：Phase3 后 11 failed/168（25 文件）vs baseline(stash -u) 11 failed/162（24 文件）→ **11 failed 数量相同 = pre-existing master 前端测试债（与 F104 无关），Phase 3 引入 0 regression**（+1 文件 +6 tests 全 passed）
- [偏离] CSS token 修正（误用未定义 token→实际 token）+ fetchLogicalFileVersions 提供未消费（版本时间线后续 Phase）+ 未做 preview 浏览器验证（tsc+vitest 替代，worktree 无 dev server）
- [T3.review] Phase 3 Codex review：1 high + 1 medium（均成立）→ 委托子代理修：
  - [high] openTask/openFile 异步竞态（快速切换 task/file 旧响应无条件 setFiles/setDiff 覆盖新选择→显示错文件内容；主 session 审查曾低估为 v0.1 可接受，Codex 升 high 成立）→ request token（useRef 单调 seq）+ 响应前校验 seq 最新 + deferred promise 乱序返回测试
  - [medium] 主 diff 标题暴露内部版本号 v{N}（version_no 是后端内部计数器，违反 SC-004/CL-1 主视图 0 技术字段）→ 标题改"上一版"/"当前版"去 vN，version_no 留 Phase 4 Advanced 区 + 更新测试
- [T3.review] 修复完成：race token（useRef 单调 seq，openTask/openFile 入口 ++seq + 响应前 seq 校验 + 回退 backTo* ++seq 使在途失效）+ 2 乱序测试（deferred promise 点 A→回退→点 B，resolve A 再 B，断言 B 显示 A 丢弃，files+diff 两层）+ vN 移除；FilesCenter 8 passed + 全 vitest 11 failed/170 = 0 regression（11 pre-existing 不变）+ tsc 0 错 → re-review 确认
- [T3.review] Phase 3 re-review = **approve**（race token 覆盖完整 + 回退失效 + vN 移除，No material findings）→ commit 4cad03f（7 files +953/-1，diff 依赖留 Phase 4，不 push）

### Phase 4（DiffView jsdiff 行级高亮 + Advanced 折叠）— F104 核心 diff 视图
- [env] npm install diff@9 + @types/diff（package.json/lock 留 Phase 4 commit）
- [T4.x] 委托 implement 子代理：①buildDiffLineRows（jsdiff diffLines + added/removed/unchanged 逐行 + trailing newline 剔除）②DiffLineList 行级高亮（--cp-success-soft +/--cp-danger-soft -/unchanged + data-diff-kind + useMemo）③DiffBody 5 降级分支（binary/oversize/current null/首版/previous null）④AdvancedVersionMeta（details 默认收起 + onToggle 懒加载 fetchLogicalFileVersions + loadedRef 单次 + seqRef 独立 race；技术字段 vN/hash前8/size/storage_kind 仅此区，SC-004 主视图 0）
- [审查] 主 session：jsdiff trailing newline 处理对 + 降级分支完整 + Advanced 折叠 race 隔离 + 技术字段仅 Advanced；小观察 DiffLineList key=idx（静态渲染可接受）+ 较大文件（<256KB）全量 diff DOM（v0.1 可接受）
- [验证] tsc 0 + FilesCenter 12 passed（+4 Phase4）+ 全 vitest 11 failed/174 = 0 regression（11 pre-existing 不变）
- [偏离] 首版保留文案 + previous content=null 退回当前内容纯展示（FR-010 合理新增分支）
- [T4.review] Phase 4 Codex review：0 high + 1 medium（FR-015 无差异分支缺失：相同内容被当普通 diff 渲染全 unchanged 行/空文件空面板，spec.md:190/:301 要求明确"无差异"提示）→ 委托子代理修：DiffBody 渲染 DiffLineList 前加 current.content===previous.content 无差异空态 + 补相同非空/空文件测试
- [T4.review] 修复完成：无差异分支（FR-015，spec.md:301 [必须]）+ 2 测试（相同非空→无差异且无 data-diff-kind 行 / 两空文件→无差异）；0 high 无需单独 re-review（Final cross-Phase 覆盖）；FilesCenter 14 passed + 全 vitest 11 failed/176 = 0 regression + tsc 0 → commit Phase 4（含 diff 依赖）
- [T4.commit] commit 40a708c（5 files +686/-57，含 diff@9 + @types/diff 依赖，不 push）

### Phase 5（边界/并发/负向/失败注入测试 + Final review）
- [T5.1-T5.6] 委托子代理测试补全（检查 Phase1-4 已有只补缺）：T5.1 并发（新建 test_artifact_versions_concurrency.py：12 次同 key 版本号连续 1..N + 16 inline/storage_ref 混合串行化 + 独立 lfid 序列 + mixed-writer xfail strict=False 诚实标 v0.1 非目标不声明互不污染）+ T5.2 __merged_history__ 非 versionable 不进表/Files Tab + T5.3 tool_output/llm/chat-import 非 versionable 不进 + T5.4 失败注入（missing table fail-fast + 版本失败 durable ARTIFACT_VERSION_APPEND_FAILED，②③⑤Phase1 已覆盖）+ T5.5 大文件 storage_ref 取回 + 删除占位 + T5.6 单版本不进（后端已覆盖，前端补 1）
- [审查] T5.1 真断言版本号连续 + mixed-writer xfail 诚实（不 mask，明确 v0.1 限制 in_transaction guard RuntimeError）
- [回归] backend 783 passed + 1 xfailed（+10 Phase5）+ 前端 15 passed = 0 regression
- [T5.7] Final cross-Phase Codex review 启动（Phase 1-5 全 diff + spec + plan，重点事务原子性/0 regression/SD-9 边界/技术字段 0 泄漏/并发防线/FR-010 占位）
- [T5.7] Final review = needs-attention（1 high + 2 medium）：
  - [high] versionable 写锁不隔离并发默认写（versionable=False 不进 _write_lock + 共享连接，默认写在 in_transaction 检查后插入会被卷入 versionable 事务边界，破坏 FR-004/FR-021）→ **= GATE_TASKS 用户已拍板 mixed-writer 实测驱动归档项（T1.3 实测顺序队列 v0.1 不触发，真并发完全正确=连接级写锁架构 follow-up）；Final review 重提为 high → AskUserQuestion 用户再拍板**
  - [medium] __merged_history__ 仅合并路径排除，调用方传 step_id='__merged_history__' 可绕过 SD-9 进版本表 → 委托子代理修（execute_progress_note 保留命名空间 guard + 负向测试）
  - [medium] DiffSide 主响应暴露 version_no/storage_kind（违反 SC-004 主视图技术字段 0，前端虽不显示但 API 契约泄漏）→ 委托子代理修（DiffSide 移除 version_no/storage_kind 仅保留 content/availability/oversize；技术字段只走 /versions）
- [T5.7] 2 medium 修复完成：__merged_history__ step_id guard（execute_progress_note 强制 versionable=False + 负向测试）+ DiffSide 移除 version_no/storage_kind（后端+前端 types+测试断言改）；280 passed +1 xfailed + 前端 15 + tsc 0
- [T5.7-high] **用户选 B（撤销 GATE_TASKS mixed-writer 推迟，真修连接级写隔离）**——与 F098"用户选 B 撤销推迟"同 pattern。方案：versionable 独立连接彻底隔离（autocommit + BEGIN IMMEDIATE 拿 SQLite 写锁 + busy_timeout=5000 跨连接串行 + _write_lock 串行化连接访问；versionable commit/rollback 只影响独立连接不卷入主连接 versionable=False 写）→ 委托子代理实施 + 强制 Codex review
- [T5.7-high] 方案 B 实施完成：create_store_group versionable_conn（autocommit + busy_timeout）+ StoreGroup.close() + artifact_store versionable_conn + put_artifact versionable 用 versionable_conn + BEGIN IMMEDIATE + _insert_artifact_row 加 conn 参数 + mixed-writer 去 xfail 改必过（双向隔离断言）+ octo_harness/7 生产处 close 改；实测验证（autocommit BEGIN IMMEDIATE + WAL 跨连接可见 + busy 兜底不死锁）；786 passed + e2e_smoke 8 = 0 regression。偏离：versionable_conn 可选默认 None 退化到主 conn（兼容 watchdog 直接构造 StoreGroup，退化仅服务不触发 versionable 写的旧测试）+ 测试 200+ conn.close 未改（versionable_conn GC，范围控制）→ 启动方案 B Codex review
- [T5.7-high] 方案 B Codex review = needs-attention（2 high + 2 medium，独立连接引入新约束漏洞，印证大改动必 re-review）：
  - [high] versionable_conn 未开 foreign_keys（连接级）→ versionable 写绕过 task 外键孤儿 + 行为分裂 → 委托修（连接 PRAGMA helper 主+versionable 共用 foreign_keys=ON + 回归测）
  - [high] 失败事件 _emit_version_append_failed 用主连接 commit → 主连接持写锁致 versionable busy 失败时卷入主连接未提交默认写 → 委托修（失败事件改用 versionable_conn 干净独立连接 commit + 测主连接 pending 不被提交）
  - [medium] versionable_conn=None 退化静默用主连接保留污染窗口 → 委托修（记录 is_isolated，退化时 versionable=True 显式拒绝）
  - [medium] 测试 200+ teardown 只关主连接泄漏 versionable_conn → 委托修（批量改 store_group.close() + conftest）
- [T5.7-high] 方案 B 4 finding 修复完成：connection.py apply_write_connection_pragmas helper（versionable_conn FK=ON；主连接 FK 实测 OFF 是历史缺陷 _migrate_legacy_tables 临时关+finally no-op，收窄不动避 7 regression）+ 失败事件 append_event_committed conn=versionable_conn 路由（不卷主连接）+ _versionable_isolated 退化拒绝 + teardown 272 处改 close()；790 passed + e2e_smoke 8
- [T5.7-high] 方案 B re-review round 2（1 high + 1 medium）：
  - [high] locked 场景失败事件吞 durable（versionable BEGIN IMMEDIATE busy 失败时 versionable_conn 同锁→失败事件也写不进→仅 structlog，与 FR-021 宣称 DB locked durable event 不一致；根因 SQLite 单写锁物理限制，locked 时两连接都无法干净写 event）→ 委托务实降级修（outbox 过度工程）：修 spec FR-021/SD-10 明确双轨（structlog always durable + event best-effort）+ locked-path 测试
  - [medium] teardown 86 处漏扫（skills/integration/provider）→ 委托全仓 close()
- [T5.7-high] 方案 B round 2 修复完成：high locked 务实降级（spec SD-10/FR-021 双轨措辞 structlog always durable + event best-effort，SQLite 单写锁物理限制 outbox 超 v0.1；tasks T5.4 + locked-path 测试 TestDbLockedDegradesToStructlog）+ medium teardown 88 处/21 文件全仓 close()（grep 0 残留）
- [环境根因] **worktree .venv 是 symlink→主仓 .venv（Jun6 13:51 并发操作），主仓 editable 指向主仓 master src → 裸 pytest 跑 master（ImportError connection/MAX_VERSION_RETRY）**。修复：验证用 PYTHONPATH 锁 worktree 全 packages/apps src → concurrency 4 passed + 全回归 791 passed（worktree 真实代码 0 regression）。worktree venv 独立化是环境 follow-up（不阻 F104，git commit 内容不受 venv 影响）→ round 3 re-review
- [T5.7-high] 方案 B round 3 re-review = 0 high + 2 medium（HIGH 收敛 2H→1H→0H，剩文档诚实性）：
  - [medium] structlog "always durable 文件日志" 过度宣称（实测 logging_config 只 StreamHandler 无文件 sink，locked 时进程流不持久化则信号丢）→ 委托修（核实 logging_config + 诚实降级"best-effort local log"）
  - [medium] plan.md:191-196 仍旧 FR-021 契约（共享 conn + 必 durable event）未同步 spec/tasks best-effort → 委托 plan 同步 + 三文件闭合 grep
  - 处理后 0 high + 文档闭合可收口（grep 验证一致性，不必 re-review round 4）
