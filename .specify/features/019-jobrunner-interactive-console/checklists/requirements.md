# Requirements Checklist: Feature 019

- [x] Execution 控制台统一语义覆盖状态、日志、步骤、取消、artifacts、attach_input
- [x] 控制台状态通过结构化模型暴露，而不是零散 dict
- [x] 人工输入进入同一 task event 链并可回放
- [x] 高风险输入接入现有 approval gate
- [x] 不新增持久化表，复用现有 durable 基线
- [x] 重启后等待输入任务不会被 startup recovery 误失败
