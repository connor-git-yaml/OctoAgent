---
required: true
mode: full
points_count: 4
tools:
  - web
queries:
  - "https://docs.openclaw.ai/start/wizard"
  - "https://docs.openclaw.ai/channels/pairing"
  - "https://docs.openclaw.ai/tools/skills"
  - "https://docs.openclaw.ai/concepts/oauth"
  - "https://github.com/agent0ai/agent-zero/blob/main/README.md"
  - "https://github.com/agent0ai/agent-zero/blob/main/docs/guides/projects.md"
skip_reason: ""
---

# Online Research: Feature 036 — Guided Setup Governance

## 在线调研点 1：OpenClaw 把 onboarding 和 control UI 做成同一条产品链

来源：

- [OpenClaw Wizard](https://docs.openclaw.ai/start/wizard)
- [OpenClaw Control UI](https://docs.openclaw.ai/web/control-ui)

观察：

- OpenClaw 官方文档把 `onboard` 视为推荐起点，而不是附属命令。
- Control UI 支持 chat、channels、sessions、skills、config、logs 等统一入口。
- 浏览器访问控制依赖 token / pairing / trusted local flow，而不是裸露的默认入口。

对 036 的启发：

- OctoAgent 的 setup 不应停留在 CLI 提示，而应成为 Web/CLI 共用的正式产品流程。
- Setup 完成后必须自然衔接到常用工作台，而不是停在“你现在可以自己记命令了”。

## 在线调研点 2：OpenClaw 的 pairing / auth profiles / skills status 都是显式治理对象

来源：

- [OpenClaw Pairing](https://docs.openclaw.ai/channels/pairing)
- [OpenClaw OAuth / Auth Profiles](https://docs.openclaw.ai/concepts/oauth)
- [OpenClaw Skills](https://docs.openclaw.ai/tools/skills)

观察：

- Pairing code、owner approval、allowlist、TTL 都有明确产品语义。
- Auth profiles 是 per-agent 可管理对象，支持优先级和多个 profile。
- Skills 页面强调 requirements、install、trusted/untrusted，而不是只有启用开关。

对 036 的启发：

- Channel、Provider、Skills 的安全边界必须进入 setup review。
- “是否启用”必须和“为什么可用/不可用、缺什么、风险是什么”一起呈现。

## 在线调研点 3：Agent Zero 把 provider/model/settings 作为常驻设置面

来源：

- [Agent Zero README](https://github.com/agent0ai/agent-zero/blob/main/README.md)
- [Agent Zero Projects Guide](https://github.com/agent0ai/agent-zero/blob/main/docs/guides/projects.md)

观察：

- Agent Zero 文档持续把 Web UI、settings、projects、memory、scheduler 作为主要入口。
- Projects 是 secrets、instructions、memory 和 skills 的治理边界。
- provider/model/API key/base URL 是用户可直接理解和修改的第一层配置。

对 036 的启发：

- OctoAgent 的 Provider / Agent / Skills 也必须成为设置中心的一等公民。
- project/workspace 边界不能只存在于底层 store，setup 中也要明确解释继承与作用域。

## 在线调研点 4：Agent Zero 的 skills import 提供 scope/preview/conflict 语义

来源：

- `agent-zero/webui/components/settings/skills/import.html` 本地参考
- 官方仓库 settings 结构与 README 说明

观察：

- skill 导入不是“提交后再看结果”，而是先选择 project / profile scope、namespace 和冲突策略。
- 这让 skills 从“隐式插件”变成“可治理资源”。

对 036 的启发：

- OctoAgent 的 Tools / Skills / MCP 也需要 setup 级别的 scope 和 readiness 表达。
- 仅靠 capability pack 列表不足以支撑普通用户安全启用能力。

## 结论

- 在线资料与本地代码结论一致：好的 Agent 产品不会把初始化、安全和能力治理拆散。
- 036 应明确把 `Provider / Channel / Agent Profile / 权限 / Tools / Skills` 收敛成同一条 setup-governance 主链。
