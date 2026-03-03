# Research Summary: Feature 010 Checkpoint & Resume Engine

## 调研模式

- `full`
- 离线调研：产品 + 技术 + 开源实现
- 在线调研：4 个证据点（详见 `research/online-research.md`）

## 关键结论

1. 当前代码尚未具备 checkpoint 数据面（无 checkpoint 表、无 checkpoint 指针）。
2. 当前重启恢复语义为“直接失败”，不满足 FR-TASK-4 的“从最后成功 checkpoint 恢复”。
3. 推荐采用“节点级 checkpoint + resume 状态机 + 副作用幂等键”方案。

## 推荐进入下一阶段的输入

- `spec.md`: 需求与验收标准
- `data-model.md`: CheckpointSnapshot/ResumeLease/SideEffectLedger
- `contracts/checkpoint-runtime-api.md`: 存储与运行时契约
- `plan.md`: 实施步骤与风险控制
