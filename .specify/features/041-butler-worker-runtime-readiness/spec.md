---
feature_id: "041"
title: "Butler / Worker Runtime Readiness + Ambient Context"
milestone: "M4"
status: "Implemented"
created: "2026-03-12"
updated: "2026-03-13"
research_mode: "codebase-scan"
blueprint_ref: "docs/blueprint.md M4 follow-up；docs/m4-feature-split.md；Feature 030（Capability Pack / Delegation Plane）、Feature 033（Agent Context Continuity）、Feature 039（Supervisor Worker Governance）、Feature 040（Guided Experience Acceptance）；Agent Zero current datetime / subordinate / browser agent"
predecessor: "Feature 030（Capability Pack / Delegation Plane）、Feature 033（Agent Context Continuity）、Feature 039（Supervisor Worker Governance）、Feature 040（Guided Experience Acceptance）"
parallel_dependency: "Feature 035 / 036 继续负责用户入口与 setup 主链；041 负责把 Butler -> Worker -> Project -> Tool 的默认运行面补成真实可用，而不是继续依赖隐含能力。"
---

# Feature Specification: Butler / Worker Runtime Readiness + Ambient Context

**Feature Branch**: `codex/041-butler-worker-runtime-readiness`  
**Created**: 2026-03-12  
**Updated**: 2026-03-13  
**Status**: Implemented  
**Input**: live usage 中出现了高频失败场景：用户问“今天天气怎么样”时，Butler 仍按“我不知道你在哪，也没有实时天气数据”的静态聊天方式回答，而不是利用已有的子 Worker 能力、Project 上下文和受治理网络工具完成任务。需要对比 Agent Zero 当前的内置上下文和工具组织方式，把 OctoAgent 缺的部分收敛成一个正式 Feature 041。  
**调研基础**: `research/product-research.md`、`research/tech-research.md`、`research/research-synthesis.md`

## Problem Statement

Feature 033 / 039 / 040 已经把主 Agent、上下文 continuity、worker review/apply 和 workbench 主链做成正式能力，但 live usage 仍暴露出一个关键 gap：

1. **主 Agent 缺少“当前环境事实”这一层默认上下文**  
   当前 `AgentContextService._build_system_blocks()` 只注入 `AgentProfile / OwnerProfile / ProjectContext / BootstrapSession / RecentSummary / MemoryRecall / RuntimeContext`，没有当前本地日期、时间、星期、timezone、locale 等信息。  
   结果是像“今天几号”“今天周几”“今天上海天气如何”这类问题，即使系统运行正常，也容易退化成“我没有这类信息”的回答。

2. **Butler 知道自己能派 Worker，但不知道默认应该怎样把“实时/外部世界问题”转成受治理执行**  
   当前 `bootstrap:general` 只强调 Butler 是 supervisor，不应自己做 web/browser/code 工作；但没有把“如果问题依赖实时外部信息，应优先委派 research/ops worker，并使用 governed web/browser path”说清楚。  
   这会让 Butler 在“不能自己上网”和“系统整体不能获取实时信息”之间混淆。

3. **子 Worker 的 bootstrap 缺少 runtime ambient facts 和 capability summary**  
   当前 `bootstrap:shared` 只注入 project/workspace/root，缺少：
   - 当前本地时间/日期
   - owner timezone / locale
   - 当前 surface / channel
   - 当前 runtime 实际可用工具族摘要（delegation / web / browser / memory）
   结果是 child worker 即使已经被创建，也未必知道自己应该用什么事实源和执行面。

4. **Worker 规划与权限表达还不够“面向真实世界查询”**  
   当前 `workers.review` 的 assignment 生成更像通用 split/repartition 规则；`_tool_profile_for_worker_type()` 只有 `dev -> standard`，其余默认 `minimal`。这让“需要网页导航、页面打开、站点点击”的 research/ops 任务很难被显式授予正确的执行面，也让 runtime truth 不够可解释。

5. **当前没有正式 acceptance 覆盖“今天 / 最新 / 天气 / 官网查询”这类高频外部事实场景**  
   Feature 040 已经闭合了 `setup -> workbench -> worker review/apply -> memory/operator/export/recovery` 主链，但还没有把“面向真实使用的 freshness query”纳入 release gate。

6. **041 的最终标准不是“结果能答出来”，而是整条 freshness 主链必须可审计且可降级**  
   当前实现已经把 freshness path 收口为 `ButlerSession -> A2AConversation -> WorkerSession -> RESULT -> ButlerReply`，并补上缺城市追问与 backend unavailable 的受控降级；因此 041 的完成态以“可审计、可解释、可验收”成立。

因此，041 要解决的不是“再加一个天气插件”，而是：

> 把 Butler、可由 Butler 创建的 Worker、Project/Workspace 作用域和受治理工具面组织成一条真正能处理“今天 / 最新 / 外部事实”问题的默认运行主链。

## Product Goal

交付一条默认可用的运行链：

- 主 Agent 默认知道当前本地时间、日期、timezone/locale 等 ambient runtime facts
- 主 Agent 遇到“实时/外部世界”问题时，会优先把任务解释为受治理 delegation，而不是直接声称自己没有实时能力
- 被 Butler 创建的 research / ops worker 会继承 project/workspace 作用域，并拿到清晰的 capability bootstrap
- Worker 在授权范围内可使用 `web.search / web.fetch / browser.*` 等现有工具完成查询
- freshness query 的默认主链是 `ButlerSession -> A2AConversation -> WorkerSession -> RESULT -> ButlerReply`
- runtime truth / control plane / tests 能解释“为什么这次回答用了哪个 worker、拿到了什么工具级别、为何能或不能回答”

## 2026-03-13 架构纠偏补记

041 的历史实现已经把 ambient runtime facts、freshness objective 识别、research tool profile 收口和工作台体验做到了可用状态，但它还没有完整闭合你当前要求的两个标准：

1. freshness query 必须是 **Butler 拥有的 delegation 主链**，而不是系统在 preflight 层直接改派 worker type
2. 执行 freshness query 的 Worker 必须拥有自己的 session/private memory/recall runtime，而不是只拿到一份临时 dispatch metadata

截至 2026-03-13 本轮收尾后，041 已完成 Butler-owned A2A runtime readiness、Worker private recall parity、缺城市追问与 backend unavailable acceptance，不再保留开放 blocker。

## Scope Alignment

### In Scope

- `AgentContextService`
  - 增加 ambient runtime block：本地日期、时间、星期、timezone、locale、surface/request 时间来源
  - 若 timezone/locale 缺失，显式记录 degraded reason，而不是静默省略
- `CapabilityPackService`
  - `bootstrap:shared` 注入 owner/project/runtime ambient facts 与 capability summary
  - `bootstrap:general` 明确“实时/最新/天气/网页查询”应优先委派给合适 worker
  - 补一个最小 deterministic built-in tool（如 `runtime.now`），让 agent/worker 在需要时可以工具化读取当前本地时间
- `A2AConversation` / `WorkerSession`
  - freshness query 走 Butler-owned delegation 主链
  - worker side continuity / recall runtime 可审计
- worker planning / governance
  - `workers.review` 对“今天 / 最新 / 天气 / 官网 / 查资料”类 objective 输出更明确的 worker_type / reason / tool_profile
  - `worker.review / worker.apply` 的结果继续保留 `requested_tool_profile`
  - research / ops worker 的默认 entitlement 或 plan-time tool_profile 需要足以覆盖 governed web/browser 执行
- acceptance / regression
  - 新增 freshness query acceptance matrix
  - 覆盖：
    - 今天日期/星期
    - 天气查询（有城市 / 无城市）
    - 最新资料/官网查询
    - web capability 不可用时的 graceful degradation

### Out of Scope

- 新接一家专用天气 API Provider
- 给主 Agent 直接开放 browser/code/full network 执行面
- 新建第二套 worker registry、project object 或 parallel backend
- 解决多模态、文件工作台、PWA、companion 等 M5 主题
- 在本阶段默认开放用户直连 Worker；当前仍由 Butler 作为 user-facing speaker

## Functional Requirements

- **FR-001**: 主 Agent 与 child worker 的 system/bootstrap context MUST 包含当前本地日期时间、星期、timezone、locale，并说明其来源。
- **FR-002**: 系统 MUST 提供一个 deterministic 的当前时间读取路径（prompt ambient block 或 built-in tool，推荐二者同时具备），使“今天/现在”类问题不依赖模型猜测。
- **FR-003**: `bootstrap:general` MUST 明确要求 Butler 在处理“实时/外部事实/最新”类问题时优先考虑 delegation，而不是把 lack of direct tool access 等同于系统整体不能回答。
- **FR-004**: `bootstrap:shared` MUST 向 child worker 暴露当前 project/workspace、owner timezone/locale、surface 与可用工具族摘要。
- **FR-005**: `workers.review` MUST 能识别天气、最新信息、官网查询、网页导航等 objective，并给出合适的 `worker_type / target_kind / tool_profile / reason`。
- **FR-006**: 由 Butler 创建的 research / ops worker MUST 能在受治理前提下使用现有 `web.search / web.fetch / browser.*` 路径；如 capability 不足，系统 MUST 在 review/apply/runtime truth 中显式解释原因。
- **FR-007**: `worker.apply` 派生出的 child task / child work MUST 继续保留 `project_id / workspace_id / tool_profile / spawned_by / plan_id` 等 lineage，确保“为何能访问网络”可审计。
- **FR-008**: runtime / control plane / workbench MUST 能显示 freshness query 相关的 runtime truth，包括 effective worker type、tool profile、degraded reason。
- **FR-009**: Feature 041 MUST 增加 acceptance tests，证明系统能够正确回答或优雅降级处理：
  - 今天日期/星期
  - 带城市的天气问题
  - 缺城市信息的天气问题
  - 最新网页资料/官网查询
- **FR-010**: graceful degradation MUST 仍遵守 Constitution：如果网络/浏览器不可用，系统可以说明限制，但不得忽略已存在的 project、worker、tool capability。
- **FR-011**: freshness query 的 canonical runtime path MUST 由 Butler 发起 delegation，并形成 durable `A2AConversation` / `A2AMessage` / `WorkerSession` 审计链，不得只依赖 preflight 直接改派 `worker_type`。
- **FR-012**: 执行 freshness query 的 Worker MUST 拥有自己的 private memory / recall runtime，并只能消费 Butler 明确转交的上下文胶囊或 A2A payload，而不是直接读取完整用户主会话。
- **FR-013**: control plane / workbench / acceptance evidence MUST 能直接展示 freshness query 的 `selected_worker_type`、`A2AConversation`、`WorkerSession`、`tool_profile` 与 degraded reason，证明这是 Butler-owned runtime chain。

## Success Criteria

- **SC-001**: 用户问“今天几号/周几”时，Butler 可直接基于 ambient runtime context 给出正确答案，不再表现成 stateless chat shell。
- **SC-002**: 用户问“北京今天会不会下雨”时，Butler 会优先判断是否缺城市信息；若城市已知且 web path 可用，会通过受治理 worker/tool 路径完成查询，而不是直接宣称自己没有实时数据。
- **SC-003**: 用户问“查一下某项目官网/最新文档”时，系统会创建或利用合适的 worker，并在 runtime truth 中留下工具和权限证据。
- **SC-004**: 控制台和工作台能够解释 child worker 的 effective tool profile，而不是只看到抽象的 split/merge 结果。
- **SC-005**: 041 回归测试通过后，Butler / Worker / Project / Tool 这条链可以按“默认 ready for real-world queries”对外描述。
- **SC-006**: 至少一条真实 freshness query 可以在 event chain / control plane 中回放出 `ButlerSession -> A2AConversation -> WorkerSession -> RESULT -> ButlerReply`，而不是只看到 route 升级为 research。

## Residual Risks

- 仅注入当前时间并不能自动解决“位置未知”的天气问题；仍需 Butler 先判断输入是否缺城市或 location 线索。
- 如果 `web.search` 背后的 provider 不稳定，系统仍会降级；但 041 要求把这种降级解释成“当前工具后端不可用”，而不是“系统本质上不会查”。
- 过度放宽 research / ops 的默认 tool profile 会引入权限扩张风险，因此 041 更推荐“基于 plan/objective 的显式 tool_profile 与 bootstrap summary”，而不是无差别放开。
