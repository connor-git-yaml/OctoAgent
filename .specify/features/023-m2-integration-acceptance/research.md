# Research Summary: Feature 023 — M2 Integration Acceptance

**Feature**: `023-m2-integration-acceptance`  
**Created**: 2026-03-07  
**Mode**: full

---

## 输入材料

- [产品调研](./research/product-research.md)
- [技术调研](./research/tech-research.md)
- [在线调研](./research/online-research.md)
- [产研汇总](./research/research-synthesis.md)

---

## 关键结论

1. 023 不是新增能力 Feature，而是 M2 的收口 Feature：
   - 只允许修补阻塞用户闭环的最小断点
   - 不允许扩展新的业务域、新协议或新控制面

2. 当前真正缺的不是单点实现，而是联合验收层：
   - 015-022 基本都已有分段实现与局部测试
   - 但还没有一条自动化验收链把首次使用、渠道控制、A2A 执行、Memory/Import、backup/restore 串起来

3. 首次使用链存在三个真实断点，必须纳入 023 范围：
   - `octo config init` 与 `octo doctor --live` 的前置不一致
   - Telegram channel 配置没有进入可操作闭环
   - onboarding 的“首条消息验证”只证明 bot 出站，不证明用户入站进入系统

4. 023 至少要固定四条联合验收线：
   - 首次使用闭环：`config -> doctor -> onboard -> pairing -> first inbound task`
   - 操作面等价：同一 operator item 在 Web / Telegram 上的 approve / retry / cancel / ack / pairing 行为一致
   - 协议到执行面：`A2A TASK -> DispatchEnvelope -> WorkerRuntime / JobRunner -> RESULT/ERROR`
   - 数据可恢复链：`chat import -> memory commit -> backup/export -> restore dry-run`

5. 推荐策略是“少量 DX 修补 + 一组强验收测试 + 一份 M2 验收报告”：
   - DX 修补只针对用户会卡住的断点
   - 联合验收测试使用真实本地组件，允许外部网络/第三方 API 通过 mock transport 替身
   - 验收报告必须输出剩余风险，而不是只报测试通过

---

## 设计决策

- 023 允许修改已有 CLI / onboarding / verifier / gateway 路径，但只限修补闭环断点
- 023 的主交付物是验收测试与验收矩阵，不是新产品面板
- 首次 owner bootstrap 的主路径以 Web operator inbox pairing 为准，手工编辑状态文件只作为降级手段
- A2A 联合验收以协议转换和真实执行链打通为目标，不要求在 023 增加新的对外 API
- Memory / Chat Import / backup / restore 以现有 contract 为准，023 只验证联通性，不新增新数据模型到产品面

---

## Gate 结论

- `GATE_RESEARCH`: PASS
- `GATE_DESIGN`: 可进入实现规划
- 当前可以进入：`spec -> plan -> tasks`
