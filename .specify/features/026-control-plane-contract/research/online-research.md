---
required: true
mode: tech-only
points_count: 3
tools:
  - openrouter-perplexity-web-search
queries:
  - "OpenClaw onboarding config protocol wizard session config schema uiHints official docs"
  - "OpenClaw slash commands control UI cron jobs sessions official docs"
  - "Agent Zero projects scheduler dashboard memory dashboard official docs"
findings:
  - OpenClaw 官方 onboarding/config protocol 已把 `wizard session` 与 `config.schema + uiHints` 定义成可被多端消费的协议对象，而不是某个单独 UI 的内部状态。
  - OpenClaw 的 slash commands、Control UI、cron jobs、sessions 文档共同说明 control plane 应先冻结动作语义与对象投影，再让不同表面各自渲染/触发。
  - Agent Zero 把 `Project`、`Task Scheduler`、`Memory Dashboard` 都提升为产品对象，证明 M3 的 project/session/automation/diagnostics 不应继续停留在零散脚本或 route-specific JSON。
impacts_on_design:
  - 026 第一阶段必须先冻结统一 contract，不能让 CLI/Web/Telegram 各自定义 wizard、project、session、automation、diagnostics DTO。
  - `uiHints` 应是 transport-agnostic 的元数据 sidecar，允许 Web 深度消费，同时允许 CLI/Telegram 忽略不支持的 hint。
  - `action_id` 必须成为跨表面的统一动作语义锚点，slash command、按钮、CLI command 只是 alias。
skip_reason: ""
---

# 在线调研证据（Feature 026）

## Findings

1. **OpenClaw 已把 onboarding 与 config 定义成协议，不是页面私有状态**

- 在线搜索结果显示，OpenClaw 官方 onboarding/config protocol 暴露了 `wizard.start`、`wizard.next`、`wizard.status`、`wizard.cancel` 以及 `config.schema` 这一组协议接口，并把响应统一到 `session`、`schema`、`uiHints`、`version`、`generatedAt` 这类稳定字段上。
- 这与本地参考 [onboarding-config-protocol.md](../../../../_references/opensource/openclaw/docs/experiments/onboarding-config-protocol.md) 一致，说明 026 第一阶段应先冻结共享 contract，再让 Web/CLI 各自决定怎么渲染。
- 对 OctoAgent 的直接影响是：`wizard session` 与 `config schema + uiHints` 必须作为 control-plane contract 的正式资源对象，而不是继续散落在 CLI service 或 Web route 中。

2. **OpenClaw 的 control surface 也是先有统一动作与对象，再有 UI/命令面**

- OpenClaw 官方文档分别公开了 slash commands、Control UI、cron jobs 和 sessions，说明其控制面并不是“Web 专用”或“命令专用”，而是建立在共用的会话、动作、调度对象之上。
- 可直接参考的官方入口：
  - https://docs.openclaw.ai/tools/slash-commands
  - https://docs.openclaw.ai/web/control-ui
  - https://docs.openclaw.ai/automation/cron-jobs
- 这支持 026 的核心判断：Telegram/Web/CLI 不应分别发明自己的动作语义；相同的 `action_id` 应在不同表面共享同一请求/结果语义，只允许 alias 和呈现方式不同。

3. **Agent Zero 已把 project / scheduler / memory 提升为产品对象**

- Agent Zero 官方 docs 把 Projects、Task Scheduler、Memory Dashboard 作为 dashboard 导航与 API/产品对象的一部分，而不是低层实现细节。
- 可参考的官方入口：
  - https://www.agent-zero.ai/p/docs/
  - https://www.agent-zero.ai/p/docs/task-scheduler/
  - https://test.agent-zero.ai/p/docs/memory/
- 这说明 OctoAgent 的 M3 也需要把 `project selector`、`automation job`、`diagnostics summary`、`session/chat projection` 作为稳定产品 contract 冻结下来，供后续 025-B / 026-B 并行消费。

## impacts_on_design

- 设计决策 D1：026 第一阶段先冻结 versioned control-plane contract，覆盖 `wizard session`、`config schema + uiHints`、`project selector`、`session/chat projection`、`automation job`、`diagnostics summary` 六类资源。
- 设计决策 D2：定义共用 `action/command registry`，以 `action_id` 作为唯一动作语义锚点；Telegram slash command、Web 按钮、CLI command 只作为 surface alias。
- 设计决策 D3：`uiHints` 只表达字段元数据与交互意图，不绑定 React/Rich/Telegram 的具体组件实现。
- 设计决策 D4：`diagnostics summary` 应作为聚合摘要对象，原始日志/Event Stream/深度控制台留给后续 runtime console 子线，而不是塞进本阶段 contract。

## 结论

在线证据与本地 blueprint/m3 拆解是一致的：Feature 026 的第一阶段不是先做页面，而是先冻结一组多端共用的 control-plane resource contract、action registry 和兼容策略。只有先过这个 gate，025-B 与 026-B 才能并行而不漂移。
