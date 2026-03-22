# Design Docs

`docs/design/` 收纳按主题拆开的设计专题文档。它们比 `docs/blueprint.md` 更聚焦单条链路，但不替代蓝图或代码级架构导览。

## 当前专题

- [`llm-provider-config-architecture.md`](./llm-provider-config-architecture.md)
  说明 Provider、模型 alias、setup、运行时激活以及与外部产品的对照。

## 适合什么时候看

- 你已经知道系统大方向，但想深挖某一条实现链路
- 想评审某个专题设计，而不是通读整个蓝图
- 需要判断某个子系统的历史背景与当前边界

## 与其他文档的关系

- 总体设计依据：[`../blueprint.md`](../blueprint.md)
- 当前真实代码架构：[`../codebase-architecture/README.md`](../codebase-architecture/README.md)
- 历史里程碑拆解：[`../milestone/README.md`](../milestone/README.md)
