# Requirements Checklist: Feature 032 — OpenClaw Built-in Tool Suite + Live Graph/Subagent Runtime

## Upstream Reuse

- [x] 明确要求复用 Feature 025-B 的 project/workspace/secret 基线
- [x] 明确要求复用 Feature 026 的 control plane 与 Web 控制台
- [x] 明确要求复用 Feature 030 的 capability pack / ToolIndex / Delegation Plane / pipeline 基线
- [x] 明确要求复用 020/027/028 的 Memory 治理边界
- [x] 明确禁止重做控制台框架或旁路 ToolBroker / Policy / Event

## Product Scope

- [x] built-in tool catalog / availability / install hint / degraded truth 已纳入范围
- [x] `sessions/subagents` 工具族已纳入范围
- [x] `web/browser` 工具族已纳入范围
- [x] `gateway/cron/nodes` 工具族已纳入范围
- [x] `pdf/image/tts/canvas` 工具族已纳入范围
- [x] Memory 只读工具族已纳入范围

## Live Runtime Truth

- [x] `pydantic_graph` live bridge 已纳入范围
- [x] graph runtime 的 current node / checkpoint / replay / pause/resume 已纳入范围
- [x] subagent spawn 的真实 child runtime / session / lifecycle 已纳入范围
- [x] 主 Agent child work 的 `create / split / merge / cancel / inspect` 已纳入范围
- [x] 明确禁止只交付 target kind / metadata label 的“伪 runtime”

## Control Plane Integration

- [x] 明确要求 availability / degraded reason / runtime truth 接入现有 control plane
- [x] 明确要求 operator 能从现有 Web 入口看到 child work / subagent / graph run 状态
- [x] 明确要求至少一种 built-in tool action 通过真实控制面入口可调用
- [x] 明确禁止新造平行控制台

## Boundary Control

- [x] 明确排除 Telegram / Discord / Slack / WhatsApp action packs
- [x] 明确排除 remote nodes / companion surfaces / marketplace
- [x] 明确排除 Memory 写入快捷路径
- [x] 明确要求与后续 Feature 033+ 分边界

## Testing / Anti-Fake Gates

- [x] 明确要求测试覆盖“真实入口 -> 执行结果 -> control plane 可见”
- [x] 明确要求没有真实入口的工具不得标记 shipped
- [x] 明确要求 Graph 不得只停留在 label / metadata
- [x] 明确要求 split/merge 不得缺 `split`
