# Feature 020 产品调研：Memory Core + WriteProposal + Vault Skeleton

**日期**: 2026-03-07  
**范围**: 用户可感知的记忆行为、检索契约、敏感数据默认策略  
**参考**:
- `docs/blueprint.md` §8.7 / §9.9 / M2
- `docs/m2-feature-split.md` Feature 020
- `_research/16-R4-design-MemU-vNext记忆体系与微信插件化.md`
- `_references/opensource/openclaw/docs/reference/session-management-compaction.md`
- `_references/opensource/openclaw/src/cli/memory-cli.ts`
- `_references/opensource/agent-zero/python/helpers/memory.py`

## 结论摘要

Feature 020 的产品价值不是“把向量库接进来”，而是给系统建立一个可预测的长期记忆最小内核：

1. 用户问“现在是什么”时，系统必须稳定返回最新定稿，而不是在旧版本里猜。
2. 用户问“为什么这样”时，系统必须能追到证据和历史版本。
3. 敏感数据默认不能被普通检索拿到，哪怕系统内部已经存了下来。
4. 记忆检索不能把大段原文塞回上下文，必须先 search，再按需 get。

## 用户视角需求

### 1. 最新结论必须是一等公民

来自 MemU 设计和 blueprint 的共同要求是：`SoR.current` 必须独立于历史过程存在。

- `Fragments` 负责保留过程和证据。
- `SoR` 负责表达“当前定稿”。
- 默认问答策略必须区分“现状”与“过程”。

这直接决定 020 必须实现：

- `subject_key` 稳定主键
- `current/superseded` 版本状态
- 只读 current 的基础查询接口

### 2. 检索契约要先收敛，而不是先做 UI

OpenClaw 的启发不是其文件型 memory backend 本身，而是它把检索拆成：

- `search`: 返回少量片段、路径/引用、摘要
- `get`: 按需精读具体对象

对 OctoAgent 而言，这意味着 020 的 MVP 应先冻结两类读取能力：

- `search_memory()`：返回摘要、命中类型、证据引用
- `get_memory()`：返回单条 SoR/Fragment/Vault skeleton 详情

这样后续 Web UI、CLI、Chat Import、Context Manager 都能复用同一契约。

### 3. 敏感信息不能靠“搜索后脱敏”

Vault 的产品语义必须是“默认不可检索”，而不是“能搜到但输出前抹掉一部分”。

020 至少要完成：

- 明确的敏感分区路由
- Vault skeleton 持久化
- 未授权读取时默认拒绝
- 审计字段保留证据引用和摘要，而不是暴露原文

### 4. 020 不应该提前吞掉 Feature 021 / M3

从用户旅程看，020、021、M3 分界必须清楚：

- 020: Memory Core，解决长期记忆一致性
- 021: Chat Import Core，解决外部聊天去重、窗口化摘要和 chat scope 写入
- M3: Vault 授权检索、Memory 浏览、微信等插件

如果 020 把 chat import、微信适配、知识库增量更新一起做，会让“基础记忆内核”边界失控。

## 产品决策建议

### 应纳入 020

- `Fragments + SoR + Vault skeleton` 三类对象
- `WriteProposal -> validate -> commit` 写入闭环
- `search_memory()` / `get_memory()` 读取闭环
- `before_compaction_flush()` 预留钩子，但不落真正 compaction 引擎
- 默认分区策略：`health` / `finance` 入 Vault，普通分区走 SoR/Fragments

### 只预留不实现

- chat import ingest / dedupe / summarize
- 微信/Telegram 历史记录 adapter
- 向量索引和 embedding 更新 worker
- Vault 授权审批与浏览 UI
- 文档知识库 `doc_id@version` 增量更新

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 把上下文压缩和长期记忆混成一个模块 | 020 范围膨胀，后续 Feature 021/M3 难并行 | 明确 non-goal：020 不负责工作上下文 GC |
| 只做向量检索，不做 SoR.current 唯一约束 | “最新结论”不稳定 | `subject_key + status=current` 唯一约束写入数据库 |
| Vault 只做标签，不做默认拒绝 | 敏感信息泄漏 | 读取接口默认 deny，必须显式授权标志 |
| 读取接口一次返回太多正文 | 主上下文污染 | search/get 两段式读取 |

