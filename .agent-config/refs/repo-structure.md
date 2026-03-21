# 目标 Repo 结构

```
octoagent/
  pyproject.toml / uv.lock
  apps/
    gateway/          # OctoGateway（渠道适配 + 消息标准化 + SSE 转发）
    kernel/           # OctoKernel（Orchestrator + Policy + Memory）
    workers/          # 自治智能体（ops/research/dev，Free Loop + Skill Pipeline）
  packages/
    core/             # Domain Models + Event Store + Artifact Store
    protocol/         # A2A-Lite envelope + NormalizedMessage
    plugins/          # 插件加载器 + Manifest + 能力图
    tooling/          # 工具 schema 反射 + Tool Broker
    memory/           # SoR/Fragments/Vault + 写入仲裁
    provider/         # LiteLLM client wrapper + 成本模型
    observability/    # Logfire + structlog + Event Store metrics
  plugins/
    channels/         # telegram/ web/ wechat_import/
    tools/            # filesystem/ docker/ ssh/ web/
  frontend/           # React + Vite Web UI（M0 起步）
  data/               # sqlite/ artifacts/ vault/（.gitignore）
```
