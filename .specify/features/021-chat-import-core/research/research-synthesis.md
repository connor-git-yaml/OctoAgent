# 产研汇总: Chat Import Core

**特性分支**: `codex/feat-021-chat-import-core`
**汇总日期**: 2026-03-07
**输入**: [product-research.md](product-research.md) + [tech-research.md](tech-research.md)
**执行者**: 主编排器（非子代理）

## 1. 产品 x 技术交叉分析矩阵

| MVP 功能 | 产品优先级 | 技术可行性 | 实现复杂度 | 综合评分 | 建议 |
|---------|-----------|-----------|-----------|---------|------|
| `octo import chats` 最小入口 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| `--dry-run` 导入预览 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| cursor + dedupe ledger | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| 原始聊天 artifact + 窗口摘要 | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| SoR fact proposal 接入 020 contract | P1 | 高 | 中 | ⭐⭐⭐ | 纳入 MVP |
| Web 导入面板 | P2 | 中 | 中 | ⭐⭐ | 二期 |
| 具体微信 / Slack adapter | P2 | 中 | 高 | ⭐ | 推迟 |

## 2. 可行性评估

### 技术可行性

整体技术可行性高。原因：

1. 020 已经冻结了 memory 治理 contract；
2. core 已有 Event / Artifact / `NormalizedMessage`；
3. 022 已证明运维型 CLI + operational task + 状态文件的交付路径可行。

021 的核心难点不在“能否写出来”，而在“如何把导入结果做成用户可信的系统行为”。

### 资源评估

- **预估工作量**: 中等，主要集中在 import schema / store / service / CLI / 测试
- **关键技能需求**: SQLite schema 设计、artifact/event 审计链、memory contract 复用、CLI UX
- **外部依赖**: 无强制新依赖，优先复用现有 workspace package

### 约束与限制

- 021 不应实现具体 source adapter
- 021 不应回改 020 contract
- 021 若不开用户入口，会继续违背 M2 的“可触达入口”目标

## 3. 风险评估

### 综合风险矩阵

| # | 风险 | 来源 | 概率 | 影响 | 缓解策略 | 状态 |
|---|------|------|------|------|---------|------|
| 1 | 只做内核不做入口，M2 目标继续悬空 | 产品 | 高 | 高 | 将 `octo import chats` 与 `--dry-run` 纳入 MVP | 需纳入 spec |
| 2 | 导入重复执行造成重复写入 | 技术 | 中 | 高 | 引入持久化 dedupe ledger | 可控 |
| 3 | 导入内容污染 live session / 主上下文 | 产品/技术 | 中 | 高 | 强制 chat scope 隔离 + 原文 artifact 化 | 可控 |
| 4 | 事实提取误写 SoR | 技术 | 中 | 高 | fragment-only 默认路径 + proposal 审计 | 可控 |
| 5 | 再开新数据库导致 022 backup 不完整 | 技术 | 中 | 中 | 使用项目主 SQLite | 可控 |
| 6 | 021 过早吞并微信 adapter / Web 面板 | 产品 | 高 | 中 | 明确 out of scope | 可控 |

### 风险分布

- **产品风险**: 3 项（高:2 中:1 低:0）
- **技术风险**: 3 项（高:2 中:1 低:0）

## 4. 最终推荐方案

### 推荐架构

Feature 021 采用“**导入内核 + 最小 CLI 入口 + 持久化报告**”方案：

1. 新增 import schema / store / service；
2. 通过 `octo import chats` 提供用户入口；
3. 提供 `--dry-run` 做预览；
4. 原文窗口进入 Artifact，摘要进入 Fragment，事实候选通过 Proposal 走 020 仲裁；
5. 生命周期通过 `ops-chat-import` operational task 写 Event Store。

### 推荐技术栈

| 类别 | 选择 | 理由 |
|------|------|------|
| 导入核心 | `packages/memory` 内新增 import 子模块 | 与 020 contract 同域，便于共享连接和模型 |
| 用户入口 | `packages/provider/dx` CLI 命令 | 与 015/022 的 CLI first 风格一致 |
| 持久化 | 项目主 SQLite + existing artifacts dir | 与 022 backup/export 对齐，减少新存储孤岛 |
| 审计 | Event Store dedicated operational task | 满足 Everything is an Event，且复用现有约束 |

### 推荐实施路径

1. **Phase 1 (MVP)**: import models/store/service、CLI、dry-run、report、核心测试
2. **Phase 2**: richer source adapters、Web report view、批次管理
3. **Phase 3**: import governance 扩展（回滚、删除、敏感数据联动）

## 5. MVP 范围界定

### 最终 MVP 范围

**纳入**:
- `octo import chats`：满足 M2 用户入口承诺
- `--dry-run`：建立用户信任，避免黑箱导入
- ImportBatch / Cursor / Window / Summary / Report：形成 durability 闭环
- dedupe ledger：保证重复执行安全
- artifact provenance + fragment summary：保证原文可审计且不污染上下文
- proposal 驱动的 SoR 写入：保证治理一致性

**排除（明确不在 MVP）**:
- 微信 / Slack / Telegram 历史具体 adapter：后续 Feature / M3 承接
- Web 导入面板：不是 021 的最小闭环
- 导入批次回滚：需要额外治理能力，不纳入本轮

### MVP 成功标准

- 用户可通过 CLI 完成一次 dry-run 或实际导入
- 对同一批次重复执行时，不会重复写入相同消息
- 导入后能得到持久化报告，知道写入 scope、artifact refs 和 next cursor
- 导入生成的原文和摘要可回放，不污染不相关 chat scope

## 6. 结论

### 综合判断

021 应该从“通用内核”升级为“可实际使用的导入闭环”。这不是范围膨胀，而是补齐当前 M2 规划中遗漏的可用性要件。最合理的边界是：交付 generic import core、CLI 入口、dry-run 与报告；不交付具体 source adapter 和完整 Web 管理面。

### 置信度

| 维度 | 置信度 | 说明 |
|------|--------|------|
| 产品方向 | 高 | `blueprint`/`m2 split` 与竞品证据都指向“入口 + 可回看 + 可隔离” |
| 技术方案 | 高 | 020/022/现有 core 能力已提供足够基础 |
| MVP 范围 | 高 | 可用性补缺明确，同时边界仍收敛 |

### 后续行动建议

- 进入需求规范阶段时，把 `CLI 入口 / dry-run / 导入报告` 列为正式 FR
- 在 `GATE_DESIGN` 明确提示：当前 021 规范会要求后续回写 `blueprint` 与 `m2-feature-split`
