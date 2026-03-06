# Feature 015 调研汇总

- 调研模式：`full`
- 在线调研：`perplexity-web-search`，2 个调研点（见 `research/online-research.md`）

## 关键参考证据

1. OpenClaw onboarding / doctor / pairing / dashboard：
   - `_references/opensource/openclaw/README.md`
   - `_references/opensource/openclaw/src/commands/onboard.ts`
   - `_references/opensource/openclaw/docs/gateway/doctor.md`
   - `_references/opensource/openclaw/docs/channels/pairing.md`
2. Agent Zero 安装与可干预体验：
   - `_references/opensource/agent-zero/knowledge/main/about/installation.md`
   - `_references/opensource/agent-zero/knowledge/main/about/github_readme.md`
3. 当前代码基线：
   - `octoagent/packages/provider/src/octoagent/provider/dx/cli.py`
   - `octoagent/packages/provider/src/octoagent/provider/dx/doctor.py`
   - `octoagent/packages/provider/src/octoagent/provider/dx/config_commands.py`

## 结论

1. 015 应以 `octo onboard` 编排层为主，而不是继续扩展 `octo init`。
2. 015 的 MVP 必须包含 onboarding session 恢复和 doctor remediation 结构化输出。
3. 015 应通过 channel verifier contract 与 016 并行，而不是自行实现 Telegram transport。
