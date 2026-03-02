# Feature 007 产研汇总（review 后优化版）

## 输入材料

- 产品调研: `research/product-research.md`
- 技术调研: `research/tech-research.md`
- Blueprint: `docs/blueprint.md`（§14 M1 验收）
- M1 拆分: `docs/m1-feature-split.md`（Feature 007 定位）

## 统一结论

1. Feature 007 的正确定位
- 集成验收特性，不是新增业务能力特性。
- 关键输出应是“跨模块可验证证据”和“风险清单”。

2. 方案决策
- 采用“真实组件联调 + 验收闭环”（推荐方案 A）。
- 不在本轮改 Gateway 主链路，不引入 M2 范围能力。

3. 对 Phase 1-3 的改进点（本轮已采纳）
- 增加产品视角调研，避免只做代码视角。
- 明确 IN/OUT，防止 007 scope 膨胀。
- 在 spec 中提前固化风险接受（参考路径缺失、模型客户端边界）。

## 关键风险

- MCP 参考路径已确认：
  - `_references/opensource/agent-zero/python/helpers/mcp_handler.py`
  - `_references/opensource/agent-zero/prompts/agent.system.mcp_tools.md`
- SkillRunner 真实生产模型客户端暂未统一（本轮使用受控测试客户端验证集成契约）。

## 执行策略

- [回退:串行] 本轮按串行推进（调研 -> 规范 -> 规划 -> 实现 -> 验证），
  并在验证阶段集中做回归测试，确保稳定性。
