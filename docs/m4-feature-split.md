# M4 Feature Split（Current Upgrade Wave v0.2）

## 1. 目标

M4 现在不再等同于“语音 / companion / 远程陪伴”。从 Feature 032 开始，仓库已经进入一轮更现实的升级波次，核心目标变成四件事：

- 把默认 Web 入口从 operator console 改成普通用户能直接上手的 guided workbench
- 把初始化配置、权限、tools/skills readiness 收口成可 review / apply 的 canonical setup 主链
- 把 runtime lineage、上下文、memory recall、安全边界补成真实运行事实，而不是控制台投影
- 把主 Agent / Work / Worker(Subagent/Graph) 的三层结构补成内建能力

因此，**当前 M4 的真实范围** 是：

- `032 / 034 / 035 / 036 / 037 / 039`
- `033` 与 `038` 都是已完成的 M3 carry-forward；它们服务 M4，但不并入 M4 编号面
- `040` 已闭合 guided experience release gate；随后由 `041` 收口 live usage 暴露的 runtime readiness / freshness query 缺口

## 2. 当前 Feature 队列

| Feature | 状态 | 作用 |
|---|---|---|
| 032 | Implemented | Built-in tools、graph/subagent runtime、child work split/merge、runtime truth |
| 034 | Implemented | 主 Agent / Worker 多轮上下文压缩与 memory flush 审计链 |
| 035 | In Progress | Guided Workbench：`Home / Chat / Work / Memory / Settings / Advanced`，已接入 setup readiness、worker review/apply、context degraded |
| 036 | Implemented | Guided Setup Governance：`setup-governance / setup.review / setup.apply / agent_profile.save / policy_profile.select / skills.selection.save` |
| 037 | Implemented | Runtime control context hardening，解决 selector drift 与 lineage 漂移 |
| 039 | Implemented | Supervisor worker governance + internal A2A dispatch，补齐三层结构 |
| 040 | Implemented | M4 串联验收与用户旅程闭环（见 §4） |
| 041 | Passed | Butler / Worker runtime readiness：ambient current time、freshness query delegation、worker web/tool readiness（见 §5） |

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

- `general` worker 默认只保留 supervisor 工具组
- `workers.review` built-in tool
- `worker.review / worker.apply` control-plane actions
- child work `requested_tool_profile` runtime truth
- orchestrator live dispatch 内部 A2A roundtrip

039 之后，主 Agent / Work / Worker(Subagent/Graph) 三层结构已经成为默认主链，而不是蓝图概念。

## 4. 是否还需要一个“串联全部功能”的 M4 Feature

**需要。**

原因不是“还缺一个大而全功能包”，而是当前 M4 还缺一个正式的集成验收 Feature 来把 035 / 036 / 039 收口成单条用户旅程。否则会继续出现“能力都在，但用户入口和治理链条没完全闭环”的问题。

建议保留为：

### Feature 040：M4 Guided Experience Integration Acceptance

状态：**Passed**

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

- 040 的 acceptance gates 已全部闭合
- M4 当前波次不再受 033 / 036 blocker 影响

约束：

- 只能消费 015/017/025/026/030/035/036/037/039 的正式 contract
- 必须在 UI/acceptance 中显式展示 `context_continuity` 的运行状态；若未来退化，不能假装 continuity 已闭环
- 不新增新的产品对象，只做集成、验证、缺口补齐

## 5. Live Usage 暴露的后续缺口：Feature 041

040 通过之后，当前升级波次又暴露出一个不应再留到“以后再说”的缺口：

- Butler 已经具备创建 child worker 的系统能力，但在“今天几号 / 今天天气 / 查一下最新官网”这类日常问题上，仍可能像 stateless chat shell 一样回答
- child worker 也已经具备 `web.search / web.fetch / browser.*` 等受治理工具，但 bootstrap 和默认 runtime context 没有把这些能力组织成“显然可用”的默认运行面
- 当前 system/bootstrap context 里也缺少“当前本地时间 / 日期 / timezone / locale”这层 ambient facts

因此需要新增：

### Feature 041：Butler / Worker Runtime Readiness + Ambient Context

状态：**Passed**

目标：

- 把当前本地时间、日期、timezone/locale 作为 ambient runtime context 正式接入主 Agent 与 child worker
- 让 Butler 在“实时 / 外部世界 / 最新资料”问题上优先考虑 delegation，而不是直接宣称没有实时能力
- 让 research / ops worker 的 governed web/browser 执行面和 tool_profile 更可解释、更可验收
- 把“今天 / 天气 / 官网 / 最新资料”纳入正式 acceptance matrix

已落地：

- `AgentContextService` 已加入 ambient runtime facts（日期 / 时间 / 星期 / timezone / locale / surface / source）
- `CapabilityPackService` 已新增 `runtime.now`，并把 freshness delegation 写入 `bootstrap:shared / general / research / ops`
- `workers.review / subagents.spawn / work.split` 已按 objective 选择更可解释的 `worker_type / tool_profile`
- Workbench / Control Plane 已能展示 freshness runtime truth 与 degraded reason
- 已形成 `contracts/freshness-query-acceptance-matrix.md` 与 041 release verification report

当前结论：

- 041 已通过 targeted release gates
- 当前 M4 升级波次现在可以按“默认具备真实世界 freshness query readiness，但仍遵守 supervisor-only 与 governed tools 边界”的口径对外描述

约束：

- 041 不是给主 Agent 重新挂 web/browser/code 执行面；Butler 仍是 supervisor-only
- 041 不以专用天气 API 为前提；优先复用现有 `web.search / web.fetch / browser.*`
- 041 必须保留 runtime truth：谁去查、拿到什么 tool profile、为何降级，都要可解释

## 6. 非伪实现门禁

当前 M4 波次必须满足以下门禁，否则不能视为完成：

1. 主 Agent 默认必须是 supervisor，而不是继续直接持有 web/browser/code 等执行面。
2. `work -> child work -> subagent/graph` 必须是 durable 主链，并能在 control plane / workbench 被解释。
3. live dispatch 必须真正经过 A2A 归一化，而不是只有 adapter/tests。
4. Workbench / Settings / Setup 必须直接复用 canonical resources/actions，不得造 `settings/*`、`setup/*` 私有 backend。
5. setup 必须存在统一的 review/apply 语义，CLI 与 Web 不得各讲一套。
6. 035/040 必须显式展示 `context_continuity` 运行状态；若未来出现 degraded，不能把“缺上下文连续性”隐藏在默认行为里。
7. 若系统已经具备 delegated web/browser path，则 Butler/Worker 不得把“自己不直接上网”误表述成“系统整体不能处理实时/外部事实问题”。

## 7. 移入 M5 的内容

以下内容不再属于当前 M4 升级波次，统一后移到 M5：

- 文件/工作区工作台（file browser / editor / diff / git-aware workspace inspector）
- 语音与多模态交互表面（STT / TTS / voice session / richer multimodal chat surfaces）
- Progressive Web App / companion surfaces / remote tunnel polish
- 更完整的通知中心与 attention model（提醒、升级提示、后台任务完成通知、多端同步提示）

这些能力都建立在 035/036/039 彻底收口之后再做，避免继续把“入口未闭环”和“未来表面增强”混在一个里程碑里。
