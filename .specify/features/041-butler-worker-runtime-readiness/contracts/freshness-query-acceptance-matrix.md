# Feature 041 Freshness Query Acceptance Matrix

## 目标

把“今天 / 天气 / 官网 / 最新资料”这类真实世界查询纳入正式验收面，避免系统再次退回 stateless chat shell 式回答。

## 矩阵

| Case ID | 场景 | 期望行为 | 当前验证方式 | 证据 |
|---|---|---|---|---|
| FQ-001 | 用户问“今天几号 / 周几 / 现在几点” | 主 Agent 或 child worker 可基于 ambient runtime context 直接读取当前本地时间，不依赖模型猜测 | 自动化 | `apps/gateway/tests/test_task_service_context_integration.py::test_build_ambient_runtime_facts_formats_local_datetime_and_fallbacks`；`apps/gateway/tests/test_capability_pack_tools.py::test_runtime_now_tool_returns_owner_local_time_payload` |
| FQ-002 | 用户问“北京今天会不会下雨” | Butler 不应再说“系统没有实时数据”；应识别为 freshness query，并通过 `ButlerSession -> A2AConversation -> WorkerSession` 把任务委派给 research worker，留下 governed tool profile 与 message 审计 | 自动化 | `apps/gateway/tests/test_capability_pack_tools.py::test_workers_review_uses_standard_profile_for_freshness_queries`；`apps/gateway/tests/test_orchestrator.py::TestOrchestrator::test_freshness_query_runs_research_child_then_butler_reply` |
| FQ-003 | 用户问“今天天气怎么样”但没有城市 | Butler 应先判断缺城市/位置参数，再继续走受治理 delegation 路径；不得把问题错误解释成“系统没有实时能力”；若进入 worker 路径，仍应保留 Butler-owned A2A 事实链 | 自动化 | `apps/gateway/tests/test_orchestrator.py::TestOrchestrator::test_freshness_weather_without_location_clarifies_before_delegation`；`agent_context.py` 默认 instruction overlays；`capability_pack.py` 中 `bootstrap:general` 文案 |
| FQ-004 | 用户问“查一下官网 / 最新文档 / 最新公告” | Butler 应通过 `A2AConversation` 委派 research worker，并给予可解释的 `tool_profile=standard`；child work / worker session 继承 lineage | 自动化 | `apps/gateway/tests/test_capability_pack_tools.py::test_workers_review_uses_standard_profile_for_freshness_queries`；`apps/gateway/tests/test_capability_pack_tools.py::test_subagents_spawn_preserves_freshness_tool_profile_and_lineage`；`apps/gateway/tests/test_orchestrator.py::TestOrchestrator::test_freshness_query_runs_research_child_then_butler_reply` |
| FQ-005 | web/browser backend 当前不可用 | 系统可以降级，但必须把限制解释成当前工具后端或环境限制，而不是宣称系统整体不具备外部信息能力 | 自动化 | `apps/gateway/tests/test_orchestrator.py::TestOrchestrator::test_freshness_backend_unavailable_returns_environment_limited_reply`；`build_ambient_runtime_facts()` degraded reasons |

## 当前结论

- FQ-001 已有自动化回归证据
- FQ-002 / FQ-004 现在已有 message-native A2A 主链验收，能回放 `Butler -> Research child task -> A2AConversation -> WorkerSession -> ButlerReply`
- FQ-003 已有 Butler 显式追问位置的自动化验收，不再只依赖 prompt/bootstrap 约束
- FQ-005 已有 backend unavailable 的 gateway-level 自动化验收，系统会把限制解释成当前工具后端 / 环境限制
