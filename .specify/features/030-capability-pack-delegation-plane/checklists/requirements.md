# Requirements Checklist: Feature 030

## Scope

- [x] 仅包含 bundled capability pack、ToolIndex、Skill Pipeline、Work/Delegation、多 Worker registry、control-plane 增量扩展
- [x] 未引入 M4 remote nodes / companion surfaces
- [x] 未重做 026 control plane 基础框架
- [x] 明确保留单 Worker / 静态工具集降级路径

## Governance

- [x] 动态工具注入仍走 ToolBroker / Policy Engine / manifest / audit 链
- [x] pipeline side-effect 路径不得绕过治理面
- [x] work / pipeline 关键状态必须 durable
- [x] 关键运行变化必须进入事件链

## Compatibility

- [x] 兼容 025-B project/workspace 基线
- [x] 兼容 018 A2A-Lite envelope
- [x] 兼容 019 execution / jobrunner 基线
- [x] 兼容 026 control-plane canonical surface

## UX / Product Surface

- [x] control plane 必须展示 tool hit、route reason、work ownership、pipeline/runtime status
- [x] Telegram / Web 必须共享新增 delegation/pipeline action semantics
- [x] degraded / fallback 必须显式暴露，而不是静默行为

## Testing

- [x] 包含 ToolIndex 单测
- [x] 包含 pipeline checkpoint/replay/pause/retry 测试
- [x] 包含 work lifecycle / routing / fallback 测试
- [x] 包含 control-plane API / frontend integration / e2e 测试
