# 产研汇总: M2 Integration Acceptance

**特性分支**: `codex/feat-023-m2-integration-acceptance`  
**汇总日期**: 2026-03-07  
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md)  
**执行者**: 主编排器（非子代理）

## 1. 产品 x 技术交叉分析矩阵

| 验收目标 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---|---|---|---|---|---|
| 首次使用闭环 | P0 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| Web / Telegram operator parity | P0 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| A2A + JobRunner 联合验收 | P0 | 中 | 中 | ⭐⭐⭐ | 纳入 MVP |
| import / memory / recovery 联合验收 | P0 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| M2 验收报告 + 风险清单 | P1 | 高 | 低 | ⭐⭐⭐ | 纳入 MVP |
| 新的可视化验收面板 | P2 | 中 | 中 | ⭐⭐ | 推迟 |

## 2. 可行性评估

### 技术可行性

整体技术可行性高。原因：

1. 015-022 的主能力已交付；
2. 当前缺口主要是验收层和少量 DX 断点；
3. 本地 references 已给出足够的 cross-project 设计依据，无需另开新体系。

### 资源评估

- **预估工作量**: 中等偏大，主要集中在测试、少量 DX 修补和验收报告
- **关键技能需求**: pytest 集成测试、CLI DX、gateway routing、protocol/runtime 对齐
- **外部依赖**: 无需新增基础设施；第三方 API 可通过 mock transport 替身

### 约束与限制

- 023 不得演变成新一轮产品功能扩张
- 023 需要真实本地组件联合，而不是完全 fake
- 验收报告必须明确风险，不得只报 “PASS”

## 3. 风险评估

### 综合风险矩阵

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|---|---|---|---|---|---|
| 1 | 只补报告不补断点，首次使用链仍断 | 产品/技术 | 高 | 高 | 把三类 DX 断点纳入 023 范围 | 必须纳入 spec |
| 2 | 只补 DX 不补联合测试，M2 仍无证据 | 技术 | 高 | 高 | 四条联合验收线必须自动化 | 必须纳入 spec |
| 3 | Web/Telegram parity 继续只测局部动作 | 产品 | 中 | 中 | 验收矩阵显式列出动作全集 | 可控 |
| 4 | A2A 与 JobRunner 仍脱节 | 技术 | 中 | 高 | 增加协议到执行面的集成测试 | 可控 |
| 5 | import / recovery 继续平行不相交 | 技术 | 中 | 高 | 一条完整 durability chain 验收 | 可控 |
| 6 | 023 范围膨胀成 M2.5 | 产品 | 高 | 中 | tasks 明确 out of scope 边界 | 可控 |

### 风险分布

- **产品风险**: 3 项（高:2 中:1）
- **技术风险**: 3 项（高:2 中:1）

## 4. 最终推荐方案

### 推荐架构

Feature 023 采用“**断点修补 + 联合验收 + 验收报告**”方案：

1. 修补首次使用主链的最小 DX 断点；
2. 新增四条联合验收测试；
3. 输出 M2 验收矩阵与剩余风险清单；
4. 不新增新的业务能力、独立控制面或长期维护面板。

### 推荐技术栈

| 类别 | 选择 | 理由 |
|---|---|---|
| 联合验收 | `pytest` + `AsyncClient` + `CliRunner` + mock transport | 已有工具链成熟 |
| 首次使用修补 | `provider/dx` 现有 CLI / doctor / onboarding / verifier | 不重复造轮子 |
| operator parity | gateway 现有 `operator_inbox` / `operator_actions` / Telegram callback codec | 已有审计链与状态源 |
| A2A 联合验收 | `packages/protocol` + `gateway/services/orchestrator.py` + `worker_runtime.py` | 直接验证协议到执行面 |
| durability 联合验收 | `chat_import_service.py` + `backup_service.py` | 现有 durability boundary 已稳定 |

### 推荐实施路径

1. **Phase 1**: 明确 spec/plan/tasks 和验收矩阵
2. **Phase 2**: 修补首次使用断点
3. **Phase 3**: 落地四条联合验收测试
4. **Phase 4**: 输出验收报告与风险清单

## 5. MVP 范围界定

### 最终 MVP 范围

**纳入**:

- `config init` / `doctor` / `onboard` 首次使用闭环修补
- Web / Telegram operator parity 联合验收
- A2A + JobRunner 联合验收
- Memory / Chat Import / backup / restore 联合验收
- M2 验收报告与剩余风险清单

**排除**:

- 新增 channel 类型或新的 Telegram 功能
- destructive restore
- 新的 frontend 运维面板
- 新的 memory/import 策略
- 新的 A2A 对外 API

### MVP 成功标准

- 新项目目录可按主路径完成首次 working flow
- 同一 operator item 在 Web / Telegram 处理结果一致且可审计
- `A2A TASK -> runtime -> RESULT/ERROR` 有真实联合证据
- 导入后的数据能被 backup/export/restore 链消费
- 最终有一份可回看的 M2 验收报告与风险清单

## 6. 结论

023 应该被定义成 M2 的“验收整合 Feature”，而不是“最后再加点功能”。它的成功标志不是代码行数，而是：用户第一次配置时不再踩断点，系统各层能力有联合证据，团队能基于一份报告明确知道 M2 到哪里为止算完成、还有哪些边界留给后续里程碑。
