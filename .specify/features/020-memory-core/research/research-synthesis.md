# Feature 020 产研汇总：Memory Core + WriteProposal + Vault Skeleton

**日期**: 2026-03-07  
**输入**: `product-research.md` + `tech-research.md` + `online-research.md`

## 交叉分析矩阵

| 维度 | 产品结论 | 技术结论 | Feature 020 决策 |
|---|---|---|---|
| 最新结论稳定性 | 用户必须稳定拿到“当前定稿” | `subject_key + current/superseded` 是最小不变量 | SoR 表使用 `status=current` 唯一索引 |
| 过程可追溯 | 用户需要追问“为什么” | `Fragments` append-only 最稳 | 独立 `memory_fragments` 表，不提供 update API |
| 敏感信息保护 | Vault 必须默认不可检索 | 读取接口层 default deny 最可靠 | `search/get` 默认拒绝 Vault，显式授权后才读 |
| 检索体验 | 不能把大段原文塞进上下文 | 两段式 `search/get` 最合适 | 冻结 `search_memory()` / `get_memory()` 契约 |
| 上下文压缩衔接 | 需要为未来 compaction 留接口 | flush 钩子比直接耦合 compaction 更稳 | 预留 `before_compaction_flush()`，当前只产 proposal/fragment |

## MVP 范围

### 本次交付

- `Fragments + SoR + Vault skeleton` 数据模型
- `WriteProposal -> validate -> commit` 服务闭环
- SQLite 持久化和唯一 current 约束
- 默认排除 Vault 的读取接口
- 单元/集成测试

### 明确不做

- Chat Import Core
- 微信/Telegram 历史消息 adapter
- 向量检索与 embedding 写入
- Vault 授权审批和浏览界面
- 工作上下文 GC / 滑动窗口压缩引擎

## 风险矩阵

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| 020 范围侵入 021/M3 | High | chat import / compaction / UI 容易被误拉进来 | 在 spec/plan 写明 non-goal，并只保留接口 |
| 没有 current 唯一约束 | Critical | 直接破坏长期记忆可信度 | 用 SQLite partial unique index 硬约束 |
| Vault 仅靠调用方自觉跳过 | Critical | 默认行为不安全 | 服务层集中实现 default deny |
| 搜索直接返回大正文 | Medium | 污染主上下文 | search 只返回摘要，详情用 get |

## 推荐方案

采用“SQLite 先冻结治理契约，向量检索后置”的方案。

理由：

1. 最符合当前 blueprint 和 constitution 的治理优先级。
2. 能先让 Feature 021/023 获得稳定 Memory contract。
3. 对现有代码库侵入最小，只需新增 `packages/memory` 和 workspace 接入。

