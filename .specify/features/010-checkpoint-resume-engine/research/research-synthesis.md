# Feature 010 产研汇总：Checkpoint & Resume Engine

## 输入材料

- 产品调研: `research/product-research.md`
- 技术调研: `research/tech-research.md`
- 在线补充: `research/online-research.md`
- 上游约束: `docs/blueprint.md`（FR-TASK-4, M1.5 约束）

## 1. 统一结论

1. Feature 010 的核心价值是“恢复正确性”，不是“吞掉所有失败”。
2. 当前代码具备任务持久化基础，但缺少 checkpoint 数据面和恢复状态机。
3. 需要把“恢复幂等”作为显式需求：恢复成功 ≠ 副作用不重复。

## 2. 方案决策

### 选型：节点级 Checkpoint + Resume 状态机（采纳）

- Checkpoint 在节点边界写入（含 schema_version、status、state snapshot）
- Resume 从最近成功 checkpoint 恢复，进入受控状态机执行
- 恢复过程生成结构化事件并可审计

### 不采纳方案

- 仅靠 task_jobs 重试的粗粒度恢复
- 直接引入外部 durable 编排服务（超出当前里程碑）

## 3. MVP 范围锁定

### In

- checkpoint 表/模型/存储接口
- 恢复器与启动恢复入口
- 恢复幂等与冲突控制
- 事件与日志可观测
- 故障注入测试（重启、损坏、重复恢复）

### Out

- Watchdog/Drift（Feature 011）
- 全链路 Logfire/Plugin diagnostics（Feature 012）
- 多 Worker 调度策略扩展

## 4. 风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| 恢复并发导致重复执行 | 高 | 任务级恢复租约 + 幂等键 |
| 快照损坏导致恢复崩溃 | 高 | 快照校验 + 安全失败 + 可重试 |
| 事务边界不清导致“半提交” | 高 | checkpoint+关键事件同事务提交 |
| 版本升级导致旧快照不可读 | 中 | schema_version + 兼容解码策略 |

## 5. Gate 结论

- `GATE_RESEARCH`: PASS（离线调研 + 在线调研均完成，points=4）
- `GATE_DESIGN`: READY（可进入 spec/plan/tasks）

## 6. 执行建议

1. 先补 Core 数据模型与存储接口，再改 TaskRunner 恢复路径。
2. 先锁住恢复语义测试（红灯），再实现业务逻辑（绿灯）。
3. 将“重复恢复不重复副作用”作为验收阻断项，而非可选优化。
