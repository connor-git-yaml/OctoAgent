---
required: true
mode: full
points_count: 3
tools:
  - perplexity/sonar-pro-search
queries:
  - "Pydantic Logfire FastAPI instrumentation distributed tracing best practices trace_id span_id propagation"
  - "plugin loader diagnostics fail-open resilient startup best practice health check service"
  - "tool registry try_register diagnostics pattern duplicate registration fail-safe"
skip_reason: ""
---

# 在线调研证据（Feature 012）

## Findings

1. **Logfire + FastAPI 的最小路径清晰**
- 建议使用 `logfire.configure()` + `logfire.instrument_fastapi(app)`，并通过 HTTPX instrumentation 支持跨服务上下文传播。
- 对本 Feature 的影响：可在 gateway 生命周期中强化配置与异常降级逻辑。

2. **插件加载应优先 fail-open + diagnostics**
- 失败不应导致主流程不可用，建议保留可运行主链路并提供结构化诊断。
- 对本 Feature 的影响：ToolBroker 注册流程应提供 `try_register` 语义和诊断累积视图。

3. **注册冲突场景需要显式诊断而非吞异常**
- duplicate registration 是常见问题，推荐结构化输出冲突来源和建议动作。
- 对本 Feature 的影响：`registry_diagnostics` 需要包含 tool_name、error_type、message、ts。

## impacts_on_design

- 设计决策 D1：保留 `register()` 的严格失败语义，同时新增 `try_register()` 用于可降级启动流程。
- 设计决策 D2：`/ready` 增加 `subsystems` 检查块，默认包含可用组件并允许缺失组件返回 `unavailable`。
- 设计决策 D3：Logfire 初始化失败只记录 warning，不中断 app 启动（对齐 C6）。

## 关键参考链接

- https://logfire.pydantic.dev/docs/integrations/web-frameworks/fastapi/
- https://logfire.pydantic.dev/docs/how-to-guides/distributed-tracing/
- https://opentelemetry.io/docs/specs/semconv/gen-ai/
- https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md
