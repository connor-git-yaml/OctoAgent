# 在线调研记录：Feature 033 Agent Profile + Bootstrap + Context Continuity

## 调研模式

- mode: `full`
- points_used: 3 / 5

## Point 1 - OpenClaw 把 bootstrapping 定义成首启身份建立流程

- 来源：https://docs.openclaw.com/start/bootstrapping
- 结论：
  - OpenClaw 明确把 bootstrapping 定义为 first-run ritual，而不是普通配置步骤
  - 首启时会 seed `AGENTS.md`、`BOOTSTRAP.md`、`IDENTITY.md`、`USER.md`
  - bootstrapping 完成后移除 `BOOTSTRAP.md`，形成“一次性建立身份”的产品心智
- 对 033 的启发：
  - OctoAgent 也需要首启身份建立流程
  - 但 canonical truth 应放在正式对象/store，再导出 materialized markdown view

## Point 2 - OpenClaw 已把 Control UI bootstrap 成独立 contract

- 来源：https://docs.openclaw.com/web/control-ui
- 结论：
  - OpenClaw 的 Control UI 不是纯 dashboard，而是围绕 bootstrap config、identity、sessions、channels 和 operator actions 的统一入口
  - 控制面在产品上承担“看见状态 + 改变状态 + 恢复状态”的三位一体职责
- 对 033 的启发：
  - 033 不能只在运行时补 prompt
  - 必须把 profile/bootstrap/context provenance 接到现有 control plane

## Point 3 - Agent Zero 的近期交付继续强化 projects + memory + skills 的统一工作单元

- 来源：
  - https://github.com/frdel/agent-zero/releases
  - https://github.com/frdel/agent-zero
- 结论：
  - Agent Zero 持续把 projects、memory management、skills 等能力作为同一工作单元的一部分来强化
  - 这说明“项目隔离 + 记忆可感知 + skills/runtime 配置”是长期成立的产品方向，不是一次性的 onboarding 花活
- 对 033 的启发：
  - OctoAgent 的 `project -> agent profile -> memory` 必须是同一条运行时主链
  - 不能把 project 和 memory 只做成后台能力，把主 Agent 继续留在 stateless chat 模式

## 未展开的在线点

- Agent Zero 的项目教程与 memory dashboard 更详细实现，本次优先使用仓库内 vendored reference 做精读，避免因为站点索引不稳定而误引非官方二手材料。
