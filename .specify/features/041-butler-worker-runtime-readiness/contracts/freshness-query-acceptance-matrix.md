# Feature 041 Freshness Query Acceptance Matrix

## 目标

把“今天 / 天气 / 官网 / 最新资料”这类真实世界查询纳入正式验收面，避免系统再次退回 stateless chat shell 式回答。

## 矩阵

| Case ID | 场景 | 期望行为 | 当前验证方式 | 证据 |
|---|---|---|---|---|
| FQ-001 | 用户问“今天几号 / 周几 / 现在几点” | 主 Agent 或 child worker 可基于 ambient runtime context 直接读取当前本地时间，不依赖模型猜测 | 自动化 | `apps/gateway/tests/test_task_service_context_integration.py::test_build_ambient_runtime_facts_formats_local_datetime_and_fallbacks`；`apps/gateway/tests/test_capability_pack_tools.py::test_runtime_now_tool_returns_owner_local_time_payload` |
| FQ-002 | 用户问“北京今天会不会下雨” | Butler 不应再说“系统没有实时数据”；应识别为 freshness query，并把计划收口为 research worker + governed tool profile | 自动化 | `apps/gateway/tests/test_capability_pack_tools.py::test_workers_review_uses_standard_profile_for_freshness_queries` |
| FQ-003 | 用户问“今天天气怎么样”但没有城市 | Butler 应先判断缺城市/位置参数，再继续走受治理 delegation 路径；不得把问题错误解释成“系统没有实时能力” | Prompt/bootstrap 约束 + 待更高层集成测试 | `agent_context.py` 默认 instruction overlays；`capability_pack.py` 中 `bootstrap:general` 文案 |
| FQ-004 | 用户问“查一下官网 / 最新文档 / 最新公告” | `workers.review` 应生成 research worker，并给予可解释的 `tool_profile=standard`；child work 继承 lineage | 自动化 | `apps/gateway/tests/test_capability_pack_tools.py::test_workers_review_uses_standard_profile_for_freshness_queries`；`apps/gateway/tests/test_capability_pack_tools.py::test_subagents_spawn_preserves_freshness_tool_profile_and_lineage` |
| FQ-005 | web/browser backend 当前不可用 | 系统可以降级，但必须把限制解释成当前工具后端或环境限制，而不是宣称系统整体不具备外部信息能力 | 部分覆盖，待更高层 acceptance | `build_ambient_runtime_facts()` degraded reasons；后续需补 gateway-level unavailable path 测试 |

## 当前结论

- FQ-001 / FQ-002 / FQ-004 已有自动化回归证据
- FQ-003 目前通过 prompt/bootstrap 约束落地，仍建议后续补 chat-level 集成测试
- FQ-005 目前只有基础 degraded 语义与运行时解释，仍建议后续补 web backend unavailable 的专门验收
