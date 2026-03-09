# 产研汇总：Feature 033 Agent Profile + Bootstrap + Context Continuity

## 1. 结论摘要

Feature 033 的本质不是“加一个 memory 搜索工具”，而是补齐 OctoAgent 当前最核心的产品缺口：

- 有 project，但主 Agent 不按 project/profile 组织自己
- 有 memory，但主 Agent 不按 session/project 去读取 memory
- 有 bootstrap 模板，但只给 worker preflight 用
- 有 control plane，但无法解释响应背后的上下文 provenance

## 2. 参考映射

### 来自 OpenClaw

- 借鉴首启 bootstrap 的产品心智
- 借鉴 `AGENTS.md` 所体现的 session startup contract
- 借鉴 control UI 既展示又控制的 operator 入口

### 来自 Agent Zero

- 借鉴 project = instructions + memory + secrets + files 的统一隔离单位
- 借鉴 memory dashboard 对用户“可感知”的要求
- 借鉴把 skills/runtime 配置与 project 一起组织的整体工作单元设计

### 对 OctoAgent 的落地方式

- 不照搬文件即真相源的实现方式
- 采用 `Project + AgentProfile + OwnerProfile + ContextFrame + MemoryService` 的正式对象体系

## 3. 设计原则

1. **Canonical object first**：profile/bootstrap/context 必须先有正式对象，再谈 UI 或 markdown 导出
2. **Short-term != Long-term**：短期 continuity 与长期 Memory 分层治理
3. **Runtime first**：先接真实调用链，再补展示面
4. **Provenance visible**：控制台要解释“为什么这样回答”
5. **Degrade gracefully**：任何一层缺失都应显式 degraded，而不是静默退化

## 4. 推荐 Feature 标题

`Feature 033：Agent Profile + Bootstrap + Context Continuity`

这个标题比“Memory 接入”更准确，因为本次要解决的是：

- Agent 的正式定位
- Owner 的正式基础信息
- 首启 bootstrap
- 短期上下文连续性
- 长期 memory retrieval
- control-plane provenance

## 5. 推荐优先级

- 优先级：`P0 / cutover-blocking`
- 原因：这是“真的能不能作为长期助手使用”的基础门槛；如果 033 不落地，后续再做 M4 体验增强会继续建立在 stateless 主聊天之上。
