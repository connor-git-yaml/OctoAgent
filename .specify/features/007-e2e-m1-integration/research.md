# Feature 007 调研汇总

- 调研模式: `tech-only`
- 结论: 当前 004/005/006 核心能力已具备真实组件联调条件，007 的关键是补齐“无 mock 的端到端验证闭环”，而不是重写 Gateway 主链路。

## 关键证据

- OpenClaw: `_references/opensource/openclaw/src/agents/tool-policy-pipeline.ts`
- OpenClaw: `_references/opensource/openclaw/src/gateway/exec-approval-manager.ts`
- Agent Zero: `_references/opensource/agent-zero/python/tools/skills_tool.py`
- Agent Zero: `_references/opensource/agent-zero/python/tools/code_execution_tool.py`

## 建设性意见

1. 007 应聚焦“集成验收层”，避免把 M1.5 的运行时重构提前到 M1。
2. 先固化 `SkillRunner -> ToolBroker -> PolicyHook -> Approval` 的测试契约，再考虑 Gateway 主链路切换。
3. 对 MCP 一等工具注册保持范围外处理，但保留已确认的参考证据路径，避免基于错误路径做设计。

## 风险

- MCP 参考路径已确认：
  - `_references/opensource/agent-zero/python/helpers/mcp_handler.py`
  - `_references/opensource/agent-zero/prompts/agent.system.mcp_tools.md`
