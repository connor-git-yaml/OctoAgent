# Tech Research: Feature 041 Butler / Worker Runtime Readiness + Ambient Context

## Agent Zero 对照证据

1. Agent Zero 会把当前本地时间直接注入运行上下文  
   证据：
   - `_references/opensource/agent-zero/python/extensions/message_loop_prompts_after/_60_include_current_datetime.py`
   - `_references/opensource/agent-zero/prompts/agent.system.datetime.md`
   结论：
   - 当前日期时间不是依赖用户口头提供，而是系统默认 ambient context 的一部分。

2. Agent Zero 把 subordinate 调用视为默认能力  
   证据：
   - `_references/opensource/agent-zero/python/tools/call_subordinate.py`
   - `_references/opensource/agent-zero/prompts/agent.system.tool.call_sub.md`
   结论：
   - “需要更合适的 agent 去做事”在 Agent Zero 中不是附加技巧，而是系统默认工作方式。

3. Agent Zero 还为浏览器任务保留了专门的 subordinate/browser 组织方式  
   证据：
   - `_references/opensource/agent-zero/python/tools/browser_agent.py`
   - `_references/opensource/agent-zero/prompts/agent.system.tool.browser.md`
   结论：
   - 浏览器/网络执行面不是直接糊在主 agent 上，而是被组织成可调用、可持续、可解释的子执行体。

4. Agent Zero 也没有专门的 location / weather 内建工具  
   证据：
   - `search_engine`
   - `browser_agent`
   - `document_query`
   - `a2a_chat`
   结论：
   - Agent Zero 默认解决“天气/最新/外部世界”问题的方式，不是专门天气 API，而是当前本地时间 + 默认外部信息工具 + subordinate 执行。

## OctoAgent 当前代码发现

1. 主 Agent 当前 system blocks 缺少时间/时区环境事实  
   证据：
   - `octoagent/apps/gateway/src/octoagent/gateway/services/agent_context.py`
   - `_build_system_blocks()` 只输出 `AgentProfile / OwnerProfile / OwnerOverlay / ProjectContext / BootstrapSession / RecentSummary / MemoryRecall / RuntimeContext`
   结论：
   - OwnerProfile 虽然保存了 `timezone="UTC"`、`locale="zh-CN"`，但并没有转化成运行中的 ambient context。

2. Worker bootstrap 目前只注入 project/workspace 基础信息  
   证据：
   - `octoagent/apps/gateway/src/octoagent/gateway/services/capability_pack.py`
   - `bootstrap:shared` 仅包含 `project / workspace / workspace root / ToolBroker governance`
   结论：
   - child worker 不知道当前本地时间，不知道 owner timezone/locale，也不知道自己当前实际有哪些工具族。

3. Butler bootstrap 只强调“不要自己做 web/browser/code”，没有把 freshness query 的 delegation 讲清楚  
   证据：
   - 同文件 `bootstrap:general`
   结论：
   - Butler 容易把“自己不应直接浏览网页”误解释成“系统整体不能查实时信息”。

4. Worker planning 对 freshness query 不够显式  
   证据：
   - `CapabilityPackService.review_worker_plan()`
   - `_classify_worker_type()`
   - `_tool_profile_for_worker_type()`
   结论：
   - split/repartition 规则已有，但对天气、最新信息、官网查询等 objective 没有一套更明确的 assignment/tool_profile 策略。

5. 现有 web/browser 工具已经存在，但默认运行面没有把它们组织成“显然可用”  
   证据：
   - `web.search`
   - `web.fetch`
   - `browser.open / status / navigate / snapshot / act / close`
   结论：
   - OctoAgent 不是没有工具，而是 Butler/Worker/runtime bootstrap 没有把这些工具变成默认可认知、可委派、可验收的能力链。

## 设计结论

1. 041 应优先补 ambient runtime context，而不是先补专用天气 API；这也和 Agent Zero 的默认组织方式一致。
2. 041 必须把“freshness query -> delegation -> worker -> governed tools -> runtime truth”做成一条链。
3. 041 不应破坏 039 的 supervisor-only 原则；主 Agent 仍不直接拿执行面。
4. 041 需要 acceptance tests，因为缺口已经体现在真实使用，而不是只存在于 spec 推演。
