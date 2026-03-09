---
required: false
mode: skip
points_count: 0
tools: []
queries: []
skip_reason: "本轮目标是冻结 035 的产品边界、页面架构和既有系统接线；仓库内 vendored OpenClaw / Agent Zero 参考与本地代码基线已足够，不依赖实时在线资料。"
---

# Online Research: Feature 035 — Guided User Workbench + Visual Config Center

## 说明

本轮没有额外做在线检索，原因：

1. 需求核心是把现有 OctoAgent 能力与仓库内本地参考（OpenClaw / Agent Zero）收敛成正式 Feature。
2. 当前最关键的是“不能做成和已有系统脱节的新 UI”，这依赖本地代码和本地对标仓库，而不是最新网络信息。
3. 仓库内已有足够的一手参考：
   - `_references/opensource/openclaw/README.md`
   - `_references/opensource/openclaw/docs/start/wizard.md`
   - `_references/opensource/openclaw/docs/web/control-ui.md`
   - `_references/opensource/agent-zero/README.md`
   - `_references/opensource/agent-zero/docs/developer/architecture.md`

## 结论

- 本轮 research 以仓库内 canonical references 与当前代码基线为准即可。
- 如果进入 035 实施阶段需要选择具体的 UI primitive library、图表库或移动端交互方案，再补相应官方文档调研。
