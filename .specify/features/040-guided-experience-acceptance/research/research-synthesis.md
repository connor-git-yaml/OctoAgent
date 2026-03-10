# Research Synthesis: Feature 040 M4 Guided Experience Integration Acceptance

## 结论

040 的最小正确做法不是继续加功能，而是把下面三段串起来：

1. `Home` 读 036 的 setup readiness
2. `SettingsCenter` 先 `setup.review` 再决定是否 `setup.apply`
3. `WorkbenchBoard` 把 039 的 worker review/apply 变成可批准流程
4. `ChatWorkbench` 把 `context_continuity` 的 degraded state 显式说清楚

## 原因

- OpenClaw 证明了 onboarding -> dashboard 的连续路径很重要，但它的 dashboard 更偏 admin surface。
- Agent Zero 证明了“系统是否可用、当前 agent 层级和状态是否清楚”应该成为首页的一部分，而且 memory/context 要有来源可见性。
- OctoAgent 当前最大的接缝不在 runtime 主链，而在 workbench 没有消费 036/039/033 已经存在的 canonical contract。
