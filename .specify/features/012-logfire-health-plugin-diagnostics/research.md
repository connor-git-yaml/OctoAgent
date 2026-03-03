# Feature 012 调研汇总

- 调研模式：`full`
- 在线调研：`perplexity/sonar-pro-search`，3 个调研点（见 `research/online-research.md`）

## 关键参考证据

1. OpenClaw 插件加载器：
   - `_references/opensource/openclaw/src/plugins/loader.ts`
   - 核心启示：失败隔离 + diagnostics 聚合
2. Pydantic AI Logfire 文档：
   - `_references/opensource/pydantic-ai/docs/logfire.md`
   - 核心启示：FastAPI/HTTPX instrumentation 组合可形成链路可观测
3. Blueprint 演进要求：
   - `docs/blueprint.md`（`try_register` + `registry.diagnostics[]` + `/ready`）

## 结论

1. Feature 012 适合“增量改造”而非全链路重构。
2. 最高价值改动是 `ToolBroker.try_register()` 与 `/ready` 子系统结构化输出。
3. Logfire 应严格采用 fail-open，避免初始化异常导致启动失败。
