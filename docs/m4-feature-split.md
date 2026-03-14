# M4 Feature Split（Current Upgrade Wave v0.2）

## 1. 目标

M4 现在不再等同于“语音 / companion / 远程陪伴”。从 Feature 032 开始，仓库已经进入一轮更现实的升级波次，核心目标变成四件事：

- 把默认 Web 入口从 operator console 改成普通用户能直接上手的 guided workbench
- 把初始化配置、权限、tools/skills readiness 收口成可 review / apply 的 canonical setup 主链
- 把 runtime lineage、上下文、memory recall、安全边界补成真实运行事实，而不是控制台投影
- 把主 Agent / Work / Worker(Subagent/Graph) 的三层结构补成内建能力

因此，**当前 M4 的真实范围** 是：

- `032 / 034 / 035 / 036 / 037 / 039`
- `033` 与 `038` 都是 M3 carry-forward，但截至 2026-03-13 只完成了 Butler / project-scoped 主链，尚未完成全 Agent parity
- `040` 已闭合 guided experience release gate；但它不再自动代表 039/041 的运行语义已完全收口
- `041` 已把 freshness query 升级成 `ButlerSession -> A2AConversation -> WorkerSession -> ButlerReply` 主链，并已闭合缺城市追问与 backend unavailable acceptance

## 2. 当前 Feature 队列

| Feature | 状态 | 作用 |
|---|---|---|
| 032 | Implemented | Built-in tools、graph/subagent runtime、child work split/merge、runtime truth |
| 034 | Implemented | 主 Agent / Worker 多轮上下文压缩与 memory flush 审计链 |
| 035 | In Progress | Guided Workbench：`Home / Chat / Work / Memory / Settings / Advanced`，已接入 setup readiness、worker review/apply、context degraded；`/memory` 已补齐用户态 display model |
| 036 | Implemented | Guided Setup Governance：`setup-governance / setup.review / setup.apply / agent_profile.save / policy_profile.select / skills.selection.save`，并已把 Memory 配置统一成 `local / memu-command / memu-http` |
| 037 | Implemented | Runtime control context hardening，解决 selector drift 与 lineage 漂移 |
| 039 | Implemented | supervisor-only、Worker governance、message-native A2A 与 durable `A2AConversation / A2AMessage / WorkerSession` 已闭合 |
| 040 | Implemented | M4 串联验收与用户旅程闭环（见 §4） |
| 041 | Implemented | ambient facts、Butler-owned freshness delegation、worker private recall、surface truth 与缺城市/环境受限 acceptance 已闭合 |
| 048 | Draft | `Home / Settings / Chat` 普通用户主路径清晰化：单一主行动、最少必要配置、等待态与折叠式协作进度 |
| 049 | Implemented | Butler behavior workspace 与 agentic decision runtime：少量显式 md、runtime hints、`ButlerDecision`、Web 只读 behavior 面 + CLI `ls/show/init` |

## 3. 各 Feature 边界

### Feature 032：OpenClaw Built-in Tool Suite + Live Runtime Truth

状态：**Implemented**

本轮已经做实：

- built-in tool catalog 与 availability / degraded / install hint
- `subagents.spawn / work.split / work.merge` 的 durable child task / child work 主链
- `graph_agent` 的真实 backend 接线
- control plane runtime truth 可视化

它是 M4 的 runtime surface 基线，不再继续承担“主 Agent 是否真是 supervisor”的职责。

### Feature 034：Context Compression for Main Agent / Worker

状态：**Implemented**

本轮已经做实：

- 主 Agent / Worker 真实多轮上下文组装
- 超预算 summarizer compaction
- artifact / event / memory flush evidence chain

034 是 M4 的上下文成本治理基线，不负责 setup、UI 或 worker governance。

### Feature 035：Guided User Workbench + Visual Config Center

状态：**Implemented**

已落地：

- 新 shell 与一级导航：`Home / Chat / Work / Memory / Settings / Advanced`
- `/` 默认进入 `Home`
- `Home` / `SettingsCenter` / `ChatWorkbench` / `WorkbenchBoard` / `MemoryCenter`
- `AdvancedControlPlane` 收编旧控制面
- 仍直接消费 canonical control-plane resources/actions，不新增平行 backend
- `/memory` 已补齐用户态 display model，过滤 internal writeback / 占位摘要，并把 derived 信息改写成可读表达

后续增强：

- 033 context provenance / 034 compaction evidence 的更细粒度可视化
- richer chat/work detail、memory 渐进展开与更完整测试矩阵

### Feature 036：Guided Setup Governance

状态：**Implemented**

已落地：

- `setup-governance / policy-profiles / skill-governance` canonical resources
- `setup.review`
- `setup.apply`
- `agent_profile.save`
- `policy_profile.select`
- `config.ui_hints` 中的 `front_door.*` 与 Telegram 安全字段显式化

已补齐：

- `skills.selection.save`
- `SettingsCenter` 的 skills 默认范围保存
- `octo init / octo project edit --apply-wizard / octo onboard` 对 canonical `setup.review / setup.apply` 的 CLI adapter
- `Settings > Memory` 与 `octo config memory show/local/memu-command/memu-http` 的统一配置语义

### Feature 037：Runtime Context Hardening

状态：**Implemented**

本轮已经做实：

- `RuntimeControlContext`
- delegation/runtime/task/context frame 的 lineage 收口
- selector drift 修复
- `session.focus/export` 与 backup/export 的 session authority 收敛

037 是 M4 安全性和串联稳定性的底座，已经完成。

### Feature 039：Supervisor Worker Governance + Internal A2A Dispatch

状态：**Implemented**

本轮已经做实：

- `general` worker 仍以 Butler/supervisor 为主，但在治理允许时可直接持有有界任务需要的 `project / artifact / document / session / network / browser / memory` 等工具组
- `workers.review` built-in tool
- `worker.review / worker.apply` control-plane actions
- child work `requested_tool_profile` runtime truth
- orchestrator 的 `ButlerSession -> A2AConversation -> WorkerSession` durable 主链
- 一等 `A2A_MESSAGE_SENT / RECEIVED / RESULT / UPDATE / HEARTBEAT / CANCEL` 运行审计对象
- `WorkerSession`、private recall、tool writeback 与 control-plane/runtime truth 对齐

当前结论：

- 039 的核心运行语义已经闭合，不再停留在“有 A2A adapter / envelope 归一化”
- 后续增强若有，重点会落在更丰富的 guided surface，而不是再去补核心 runtime 语义

## 4. 是否还需要一个“串联全部功能”的 M4 Feature

**需要。**

原因不是“还缺一个大而全功能包”，而是当前 M4 还缺一个正式的集成验收 Feature 来把 035 / 036 / 039 收口成单条用户旅程。否则会继续出现“能力都在，但用户入口和治理链条没完全闭环”的问题。

建议保留为：

### Feature 040：M4 Guided Experience Integration Acceptance

状态：**Passed（guided experience 视角）**

目标：

- 验证 `setup.review/apply -> Home readiness -> Chat -> worker.review/apply -> approval/input -> Memory -> export/recovery` 是一条连续路径
- 验证 035/036/039 之间没有平行 backend、没有权限漂移、没有 runtime truth 漂移
- 为 M4 形成类似 031 的 release gate / acceptance report

已落地：

- `Home` 改用 `setup_governance.review` / `next_actions` 做 readiness
- `SettingsCenter` 走 `setup.review -> setup.apply`
- `Work` 页面支持 `worker.review / worker.apply`
- `Chat` 页面显式展示 `context_continuity` degraded state
- `Memory` 页面串起 `memory -> operator -> export/recovery`
- frontend/backend acceptance tests 覆盖 setup/work/chat/memory 四条接缝
- 已形成 `contracts/m4-acceptance-matrix.md` 与 M4 release gate 报告

当前结论：

- 040 的 guided experience acceptance gates 已全部闭合
- 但 040 不再被视为 039/041 运行语义最终完成的替代证据

约束：

- 只能消费 015/017/025/026/030/035/036/037/039 的正式 contract
- 必须在 UI/acceptance 中显式展示 `context_continuity` 的运行状态；若未来退化，不能假装 continuity 已闭环
- 不新增新的产品对象，只做集成、验证、缺口补齐

## 5. Live Usage 暴露的后续缺口：Feature 041

040 通过之后，当前升级波次又暴露出一个不应再留到“以后再说”的缺口：

- Butler 已经具备创建 child worker 的系统能力，但在“今天几号 / 今天天气 / 查一下最新官网”这类日常问题上，仍可能像 stateless chat shell 一样回答
- child worker 也已经具备 `web.search / web.fetch / browser.*` 等受治理工具，但 bootstrap 和默认 runtime context 没有把这些能力组织成“显然可用”的默认运行面
- 当前 system/bootstrap context 里也缺少“当前本地时间 / 日期 / timezone / locale”这层 ambient facts
- 当前 weather/latest 这类问题虽然常能被路由到 research worker，但主链仍偏向“系统预判后直接切 worker”，而不是“Butler 先拥有问题，再通过 A2AConversation 委派给 Worker”

因此需要新增：

### Feature 041：Butler / Worker Runtime Readiness + Ambient Context

状态：**Implemented**

目标：

- 把当前本地时间、日期、timezone/locale 作为 ambient runtime context 正式接入主 Agent 与 child worker
- 让 Butler 在“实时 / 外部世界 / 最新资料”问题上优先考虑 delegation，而不是直接宣称没有实时能力
- 让 research / ops worker 的 governed web/browser 执行面和 tool_profile 更可解释、更可验收
- 把“今天 / 天气 / 官网 / 最新资料”纳入正式 acceptance matrix
- 把 freshness query 的默认执行主链收口为 `ButlerSession -> A2AConversation -> WorkerSession -> RESULT -> ButlerResponse`

已落地：

- `AgentContextService` 已加入 ambient runtime facts（日期 / 时间 / 星期 / timezone / locale / surface / source）
- `CapabilityPackService` 已新增 `runtime.now`，并把 freshness delegation 写入 `bootstrap:shared / general / research / ops`
- `workers.review / subagents.spawn / work.split` 已按 objective 选择更可解释的 `worker_type / tool_profile`
- Workbench / Control Plane 已能展示 freshness runtime truth 与 degraded reason
- 已形成 `contracts/freshness-query-acceptance-matrix.md` 与 041 release verification report

当前结论：

- 041 已通过 targeted release gates 中“ambient facts + Butler-owned freshness delegation + runtime truth surface”这部分
- 截至 2026-03-13，041 已闭合主链、缺城市追问与 backend unavailable acceptance，可按完整 feature 签收

约束：

- 041 的初始目标不是把主 Agent 退化成“普通执行 worker”；Butler 仍是 supervisor 与最终责任人，但在治理允许且任务有界时可以直接使用已挂载工具完成小步执行
- 041 不以专用天气 API 为前提；优先复用现有 `web.search / web.fetch / browser.*`
- 041 必须保留 runtime truth：谁去查、拿到什么 tool profile、为何降级，都要可解释
- 041 的最终完成标准不是“research route 命中了”，而是“用户可审计地看到 Butler 委派给 Worker，Worker 以独立 session/memory/recall 完成任务”

## 5.1 2026-03-14 live usage 暴露的下一轮产品化缺口：048 / 049

基于真实用户体验复盘，M4 在运行时闭环之外又暴露出两条不宜继续靠零散 patch 收口的缺口：

### Feature 048：Guided Surface Clarity Refresh

状态：**Implemented**

目标：

- 重做 `Home / Settings / Chat` 的普通用户主路径表达
- 首页先回答“当前状态、影响、下一步”
- 设置首屏先回答“最少要配置什么”
- 聊天等待态显示折叠式协作进度，而不是长时间空白

边界：

- 不重做 047 的前端架构层
- 不修改 039/041 的 runtime truth，只负责把现有事实源翻译成普通用户语言

### Feature 049：Butler Behavior Workspace & Agentic Decision Runtime

状态：**Draft**

目标：

- 把默认行为从代码里的场景特判迁移到显式 behavior workspace
- 用少量核心 markdown 文件承载 Butler 行为，并通过 runtime hints 交给 Agent 自主判断
- 借鉴 OpenClaw 的文件可见性和 Agent Zero 的分层装配，但保留 OctoAgent 的 governance / A2A / durability

当前实现：

- 当前阶段只把 `AGENTS.md / USER.md / PROJECT.md / TOOLS.md` 作为默认核心文件
- `RuntimeHintBundle + RecentConversation + ButlerDecision preflight` 已进入 Butler 主链
- generic `delegate_research / delegate_ops` 已成为可执行预路由，不再只停留在 decision contract
- deterministic 场景树已收口为 compatibility fallback，并带 provenance
- Web 已提供 `Settings -> Behavior Files` 只读 operator 视图；CLI 已提供 `octo behavior ls/show/init`

### Feature 051：Session-Native Agent Runtime & Recall Loop

状态：**In Progress**

目标：

- 把 `AgentSession` 收口成 transcript-native 真相源，而不是继续主要依赖 task/event reconstruction
- 把 `ToolUniverseHints` 接进 ButlerDecision，让模型先看到真实挂载工具再判断
- 把 memory 主链推进到 agent-led recall，并给 behavior workspace 补预算 / 截断 / overlay contract

当前计划：

- Slice A `behavior budget + tool universe hints` 已完成
- Slice B phase 2 已完成：`AgentSession` 除正式 `recent_transcript / rolling_summary` 外，已补齐 `AgentSessionTurn` store；`user / assistant / tool_call / tool_result / context_summary` 会写入 `agent_session_turns`，`RecentConversation / session.export / session.reset` 已优先消费该 store
- Slice C phase 2 已完成：Butler chat 默认改成 `agent-led hint-first`；`planner_enabled` profile 下的 `ButlerDecision + RecallPlan` 已收口为统一 `ButlerLoopPlan`，direct-answer 路径会把 recall 计划作为 `precomputed_recall_plan` 注入主调用；当 MemU backend 可用时，`MemorySearchOptions` 会把 `expanded_queries / focus_terms / rerank_mode / post_filter_mode` 下发到高级 backend search path；Worker 保持 `detailed_prefetch`
- Slice D 已完成：compatibility fallback 已收缩为 guardrail，仅保留天气缺地点边界与天气 follow-up 恢复语义
- Slice E 已完成：`AgentSessionTurn` replay/sanitize 投影与默认 general Butler `single_loop_executor` 已接回主链；后续只剩可选增强，而非主缺口

## 6. 非伪实现门禁

当前 M4 波次必须满足以下门禁，否则不能视为完成：

1. 主 Agent 默认必须是 supervisor 与最终责任人；但在治理允许且任务有界时，可以直接持有并使用受治理执行面，不再为了形式上的分层强行拒绝直接工具调用。
2. `work -> child work -> subagent/graph` 必须是 durable 主链，并能在 control plane / workbench 被解释。
3. live dispatch 必须真正经过 `ButlerSession -> A2AConversation -> WorkerSession` 的 message-native A2A 主链，而不是只有 adapter/tests。
4. Workbench / Settings / Setup 必须直接复用 canonical resources/actions，不得造 `settings/*`、`setup/*` 私有 backend。
5. setup 必须存在统一的 review/apply 语义，CLI 与 Web 不得各讲一套。
6. 035/040 必须显式展示 `context_continuity` 运行状态；若未来出现 degraded，不能把“缺上下文连续性”隐藏在默认行为里。
7. 若系统已经具备 delegated web/browser path，则 Butler/Worker 不得把“自己不直接上网”误表述成“系统整体不能处理实时/外部事实问题”。
8. `WorkerSession` 不得继续退化为 loop/backoff/tool_profile 一类运行态对象；它必须是完整的 internal conversation / memory / recall carrier。
9. 049 必须把默认行为主路径从代码特判迁移到 `behavior files + runtime hints + ButlerDecision`；天气/推荐/排期等问题不得继续扩张为新的硬编码分类树。
10. 051 必须继续把 `AgentSession`、tool universe、memory recall 收口到 agent-native 主链；不能长期停留在弱引用 session 和 system-prefetch-only recall 上。

## 7. 移入 M5 的内容

以下内容不再属于当前 M4 升级波次，统一后移到 M5：

- 文件/工作区工作台（file browser / editor / diff / git-aware workspace inspector）
- 语音与多模态交互表面（STT / TTS / voice session / richer multimodal chat surfaces）
- Progressive Web App / companion surfaces / remote tunnel polish
- 更完整的通知中心与 attention model（提醒、升级提示、后台任务完成通知、多端同步提示）

这些能力都建立在 035/036/039 彻底收口之后再做，避免继续把“入口未闭环”和“未来表面增强”混在一个里程碑里。

## 8. 架构重构实施蓝图

本轮纠偏后，`039 / 041` 不再适合继续按“局部补丁”推进。正式实施顺序见：

- `docs/agent-runtime-refactor-plan.md`

执行原则：

- 继续复用 `033 / 038 / 039 / 041` 作为承载 Feature
- 先做 `AgentRuntime / AgentSession / MemoryNamespace / RecallFrame`
- 再做 `A2AConversation / WorkerSession`
- 最后再把 freshness / research / workbench acceptance 切到新主链
