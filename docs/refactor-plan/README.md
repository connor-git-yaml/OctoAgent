# Refactor Plans

`docs/refactor-plan/` 用来收纳“已经确认有结构性坏味道，但还没进入正式 feature 实现”的重构方案文档。

这类文档的定位是：

- 解释当前实现为什么开始变脏
- 给出参考产品或开源项目的对照
- 提出目标架构、迁移步骤与风险控制

当前文档：

- [`capability-pack-simplification.md`](./capability-pack-simplification.md)
  Capability Pack / 默认工具面 / 编排工具暴露层次的重构方案。

与其他文档的关系：

- 总体设计依据：[`../blueprint.md`](../blueprint.md)
- 当前真实代码架构：[`../codebase-architecture/README.md`](../codebase-architecture/README.md)
- 设计专题：[`../design/README.md`](../design/README.md)
