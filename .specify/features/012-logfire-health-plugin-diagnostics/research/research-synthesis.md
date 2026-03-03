# 产研汇总: Feature 012 Logfire + Health/Plugin Diagnostics

## 1. 执行结论

- 需求 012 建议立即推进，采用最小增量方案。
- 本轮不引入重型架构重构，聚焦可观测与诊断闭环。
- 关键成功条件：`/ready` 子系统可见、ToolBroker 注册诊断可见、Logfire 配置可降级。

## 2. 产品 x 技术矩阵

| 产品诉求 | 技术落点 | 验收证据 |
|---------|---------|---------|
| 快速定位故障层级 | `/ready` 聚合子系统状态 | 健康检查测试覆盖新增字段 |
| 插件/工具失败不致命 | `ToolBroker.try_register()` + diagnostics | 注册失败单测 + 诊断结构断言 |
| 观测链路可追踪 | Logfire 开关与关键字段透传 | observability 测试 + trace 字段断言 |

## 3. MVP 范围（锁定）

### In
- Logfire 初始化增强（可开关、失败降级）。
- ToolBroker 诊断能力（`try_register`, `registry_diagnostics`）。
- `/ready` 子系统检查增强（含工具注册诊断摘要）。
- 单元/集成测试与 verification 报告。

### Out
- 不引入独立 observability 服务。
- 不在本轮完成插件进程级隔离。
- 不重构 Gateway 主业务链路。

## 4. 风险矩阵

| 风险 | 等级 | 缓解 |
|------|------|------|
| trace 上下文不一致 | 中 | 增加字段一致性测试 |
| 健康检查耦合新组件 | 中 | 全部检查走容错分支，不阻断核心 ready 判断 |
| diagnostics 语义不稳定 | 低 | 使用固定模型与断言测试 |

## 5. 推荐实施顺序

1. 先改 ToolBroker（低耦合，高收益）。
2. 再改 `/ready` 子系统聚合逻辑。
3. 最后补 Logfire 初始化与观测测试。

## 6. Gate 建议

- `GATE_RESEARCH`: PASS（研究产物齐全，含在线调研）。
- `GATE_DESIGN`: 建议进入用户审批后执行实现。
