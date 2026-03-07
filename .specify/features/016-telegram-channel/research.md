# Feature 016 调研汇总

- 调研模式：`full`
- 在线调研：`web.run` 官方文档，3 个调研点（见 `research/online-research.md`）

## 关键参考证据

1. 本地蓝图与里程碑拆解
   - `docs/blueprint.md`
   - `docs/m2-feature-split.md`
   - `.specify/memory/constitution.md`
2. 本地参考实现
   - `_references/opensource/openclaw/docs/channels/telegram.md`
   - `_references/opensource/openclaw/docs/channels/pairing.md`
   - `_references/opensource/openclaw/extensions/telegram/src/channel.ts`
   - `_references/opensource/openclaw/src/channels/plugins/normalize/telegram.ts`
3. 当前代码基线
   - `octoagent/apps/gateway/src/octoagent/gateway/routes/message.py`
   - `octoagent/apps/gateway/src/octoagent/gateway/services/task_service.py`
   - `octoagent/packages/provider/src/octoagent/provider/dx/channel_verifier.py`
   - `octoagent/packages/provider/src/octoagent/provider/dx/onboarding_service.py`

## 结论

1. 016 的核心不是“再加一个 webhook 路由”，而是把 Telegram 变成真正可用的外部渠道，补齐 `pairing + allowlist + session routing + outbound reply` 的闭环。
2. 当前仓库已经有 `NormalizedMessage -> Task -> Event -> SSE` 的通用管道，以及 015 交付的 `channel verifier` 接缝；016 应复用这些能力，而不是重写新的任务系统。
3. 推荐把 Telegram transport 固定在 Gateway 层，Kernel/Worker 继续只消费 `NormalizedMessage` 和任务事件，保持与蓝图一致。
4. 016 的 MVP 必须同时覆盖真实 Telegram verifier、入站去重、DM/群/topic 路由、出站回复语义；operator inbox 与移动端等价控制留给 017。

