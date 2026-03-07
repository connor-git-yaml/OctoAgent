# Research Summary: Feature 017 — Unified Operator Inbox + Mobile Task Controls

**Feature**: `017-operator-inbox-mobile-controls`
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

1. 当前 OctoAgent 的 operator 能力已经存在，但入口碎片化：
   - approvals 有单独 panel
   - watchdog alerts 只在 journal 查询里
   - cancel 只有独立 API
   - Telegram 只有文本提醒
   - pending pairings 只在 `telegram-state.json`

2. 017 的核心不是“新页面”，而是“统一 projection + 统一 action contract”。

3. 最大实现缺口有三个：
   - 后端没有统一 `OperatorAction` 语义
   - Telegram 没有 inline keyboard / callback query 操作链
   - retry 不能直接复用现有终态 task，需要 successor task / attempt 语义

4. 用户体验上必须补的缺口：
   - Web/Telegram 跨端动作结果一致
   - 最近动作结果可见
   - pairing request 不再隐形
   - operator action 有审计链而不是旁路日志

5. 并行边界明确：
   - 不重写 approvals / watchdog / Telegram ingress 基线
   - 复用 011 / 016 / 019 已交付能力
   - 017 只在 projection、action、Telegram callback、Web surface 上增量扩展

---

## 设计决策

- 统一查询模型：`OperatorInboxItem`
- 统一动作模型：`OperatorActionRequest` / `OperatorActionResult`
- 审计事件：`OPERATOR_ACTION_RECORDED`
- retry 语义：创建 successor task / attempt，并把动作写回来源链路
- Telegram operator target：优先已批准 operator DM；无目标时降级为 Web-only

---

## Gate 结论

- `GATE_RESEARCH`: PASS
- `GATE_DESIGN`: PASS
- 当前可进入：`plan -> data-model -> contracts -> tasks`
