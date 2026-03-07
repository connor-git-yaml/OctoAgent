# 产品调研报告: Chat Import Core

**特性分支**: `codex/feat-021-chat-import-core`
**调研日期**: 2026-03-07
**调研模式**: full（离线 references + 在线证据）

## 1. 需求概述

**需求描述**: 基于 `docs/m2-feature-split.md` 的 Feature 021，交付外部聊天导入通用内核，支持增量去重、窗口化摘要、按 chat scope 写入记忆，并补齐 M2 承诺的用户可触达入口。

**核心功能点**:
- 导入外部聊天并保证重复执行不重复写入
- 将导入内容隔离到独立 chat scope，而不是污染当前实时会话
- 以窗口化摘要 + 证据引用方式写入 Memory，而不是把长原文直接塞入主上下文
- 给用户明确的导入入口、dry-run 预览和导入结果报告

**目标用户**:
- 已经在 Telegram / 微信 / 其他聊天工具里积累了历史上下文，希望把历史信息迁入 OctoAgent 的 owner
- 需要迁移旧会话、补全项目背景，但不愿意接受“黑箱导入”的高级用户
- 后续要基于同一导入内核继续做微信导入插件的实现者

## 2. 用户问题与价值判断

### 当前用户痛点

1. 现在仓库只有 `020 Memory Core` 的治理 contract，没有真正的聊天导入入口；用户无法把历史聊天安全地导入系统。
2. `docs/blueprint.md` 和 `docs/m2-feature-split.md` 都要求 M2 的 Chat Import 可用，但当前拆解只覆盖“内核能力”，没有覆盖用户如何触发、预览和确认结果。
3. 如果没有去重账本和导入报告，用户无法判断重复导入是否真的安全，也无法知道哪些内容写进了哪个 scope。
4. 如果没有原文审计引用，窗口摘要会变成黑箱；导入后出错时，用户无法回看证据。
5. 如果导入直接写入主聊天 scope，后续实时聊天与历史导入会混在一起，检索和回放都会失真。

### 对用户真正有价值的结果

1. 用户能通过稳定入口执行一次或增量导入，并清楚知道会写入哪里。
2. 用户能先做 dry-run，看见“将导入多少条、跳过多少重复、会生成哪些 scope / summary / proposal”。
3. 用户导入后能拿到一份持久化报告，知道结果、游标、产物引用和失败原因。
4. 用户知道导入内容是“可审计、可检索、可隔离”的，而不是直接污染当前聊天上下文。

## 3. 参考产品复核

### OpenClaw：会话与 memory 明确分层

从本地 references 与在线文档可以确认，OpenClaw 把两类对象明确分开：

- session / transcript：服务于继续对话和会话管理；
- memory：服务于长期可检索知识与压缩后的持久上下文。

这对 021 的启发非常直接：导入的历史聊天不应伪装成当前 live session，也不应该直接追加到当前 transcript；它应该进入独立的 memory / import 路径，并保持 provenance。

### Agent Zero：用户能回看、编辑、导出

Agent Zero 的 README 和文档强调三件事：

- 聊天自动保存、可 load / save；
- memory 对用户是可见、可搜索、可编辑、可导出的；
- 长期数据迁移通过 backup / restore 与持久化文件完成，而不是黑箱缓存。

这说明 021 不能只做“把聊天文本喂给记忆层”。如果用户看不到导入结果、看不到来源、不能判断哪些内容被跳过或写入，导入功能就不成立。

## 4. MVP 范围建议

### Must-have（MVP 核心）

- `octo import chats` 最小 CLI 入口
- `--dry-run` 预览模式
- `ImportBatch / ImportCursor / ImportWindow / ImportSummary / ImportReport` 最小模型
- 增量去重：优先源消息 ID，其次 hash 去重
- 按 `scope_id=chat:<channel>:<thread_id>` 或等价 chat scope 写入
- 原始聊天窗口保留 artifact 引用，窗口摘要写 fragment
- 事实性写入只能通过 `WriteProposal -> validate -> commit`
- 导入完成后产出持久化报告（计数、cursor、scope、warnings、artifact refs）

### Nice-to-have（二期）

- 导入报告 Web 视图
- 导入批次重放 / 撤销辅助工具
- 更丰富的实体抽取和关系索引
- 导入后用户可视化 diff（哪些是重复、哪些是新写入）

### Future（远期）

- 微信导入插件 / 其他 source adapter
- 导入批次删除与选择性回滚
- 与 vault / 敏感数据治理联动
- 自动导入调度与增量订阅

### 优先级排序理由

M2 的重点不是“把所有 source adapter 做完”，而是先把用户可用的导入通道和安全的治理骨架建立起来。只有这样，后续微信导入插件和 023 集成验收才有稳定落点。

## 5. 对上游文档的影响判断

当前上游文档存在一个需要显式修正的缺口：

- `docs/m2-feature-split.md` 和 `docs/blueprint.md` 已要求 M2 的 Chat Import 具备用户可触达入口；
- 但 Feature 021 当前拆解没有把入口、dry-run 预览、导入报告列为显式需求。

结论：021 的 spec 应补入这三个能力，并在设计批准后回写 `blueprint` 与 `m2-feature-split`。

## 6. 结论与建议

### 总结

Feature 021 的真正价值不是“再加一个 memory 写入器”，而是把历史聊天导入做成一个用户敢用、能回看、能重复执行的治理闭环：有入口、有预览、有报告、有隔离、有证据。

### 对技术调研的建议

- 重点确认 020 memory contract 如何在同一项目 SQLite / Artifact / Event 体系内落地，而不是再开第二套导入存储孤岛。
- 重点确认去重账本、游标恢复、artifact provenance 和 proposal 审计如何组合成最小可实现架构。
- 重点确认 `octo import chats` 如何在“不承诺具体 source adapter”的前提下仍然对用户可用。

### 风险与不确定性

- 风险 1: 只做库层、不做入口，会导致 M2 对“可用入口”的承诺继续悬空。缓解：将 CLI 入口纳入 021 MVP。
- 风险 2: 导入直接写 SoR，会导致错误事实难以追溯。缓解：坚持 proposal + validation + commit。
- 风险 3: 没有导入报告会破坏用户信任。缓解：导入完成后持久化 `ImportReport`，保留 artifact refs 和 cursor。
