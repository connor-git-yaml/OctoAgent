---
required: true
mode: full
points_count: 2
tools:
  - perplexity-web-search
queries:
  - "site:docs.openclaw.ai openclaw onboarding wizard doctor pairing remediation dashboard first message verification"
  - "site:agent-zero.ai OR site:github.com/agent0ai/agent-zero Agent Zero interactive terminal intervene backup restore save chat usability installation"
findings:
  - OpenClaw 官方路径把 onboard 作为推荐入口，并把 doctor、pairing、dashboard 串成连续的用户操作面。
  - Agent Zero 把“first working chat”“可随时 intervene”“chat save/load 与 backup/restore”作为核心可用性信号。
impacts_on_design:
  - octo onboard 必须是首次使用主入口，而不是补充命令。
  - doctor 的输出必须从检查结果升级为 action-oriented remediation。
  - onboarding 必须支持中断恢复，否则无法建立用户对系统可恢复性的信心。
skip_reason: ""
---

# 在线调研证据（Feature 015）

## Findings

1. **OpenClaw 把 onboarding、doctor、pairing、dashboard 视为同一条用户路径**
- 在线检索结果指向 OpenClaw 官方文档与 CLI 页面，说明推荐用户首先运行 `openclaw onboard`，随后通过 doctor、pairing、dashboard 完成后续配置与验证。
- 这意味着“首次使用”不应被拆成若干孤立命令，而应由一个主入口负责串联。
- 参考：
  - https://docs.openclaw.ai/start/wizard
  - https://docs.openclaw.ai/cli/doctor
  - https://docs.openclaw.ai/cli/dashboard
  - https://docs.openclaw.ai/gateway/troubleshooting

2. **Agent Zero 从用户角度强调 first working chat、实时干预和保存恢复能力**
- 在线检索结果显示 Agent Zero 官方 README 与安装文档始终围绕“first working chat”“stop and intervene”“save/load chats”“backup/restore”。
- 这表明 onboarding 的用户承诺不只是“配置通过”，而是“系统真的能用，出了问题还能回来”。
- 参考：
  - https://github.com/agent0ai/agent-zero
  - https://agent-zero.ai/en/docs/installation/

## impacts_on_design

- 设计决策 D1：`octo onboard` 需要成为首次使用的主入口，而不是 `octo config` 和 `octo doctor` 之外的附加选项。
- 设计决策 D2：`octo doctor` 需要输出结构化 remediation，而不是仅有检查项和说明文本。
- 设计决策 D3：onboarding 需要显式支持 resume，保证用户修完外部依赖后能继续而不是重来。

## 结论

在线证据与本地 references 结论一致：Feature 015 的价值核心是“连续上手路径 + 可恢复 + 可动作化诊断”，而不是单独新增一条命令。
