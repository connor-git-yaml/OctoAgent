---
feature_id: "054"
title: "Builtin Memory Engine & Shared Retrieval Platform"
created: "2026-03-15"
updated: "2026-03-15"
status: "Planned"
---

# Plan - Feature 054

## 1. 目标

把当前 Memory 从“`local_only / memu(command/http)` 配置模式”升级为：

- 内建 Memory Engine 的产品语义
- 平台级 retrieval models 绑定
- canonical store + vector projection 的长期分层
- 可视化、可取消、不中断服务的 embedding 迁移
- Memory 与未来知识库共享 retrieval platform

## 2. 非目标

- 不在本轮交付完整知识库产品
- 不把 governance 下放给 MemU 或其他外部/内建引擎
- 不在本轮直接删除所有 transport 兼容代码
- 不顺手重写 facts / Vault 数据模型
- 不切换 LanceDB 技术选型

## 3. 设计原则

### 3.1 Canonical First

Memory 的权威记录始终保留在 OctoAgent 自己的治理存储中。向量索引只做 projection，不承载事实真源。

### 3.2 Intelligence by MemU, Governance by OctoAgent

加工智能优先走 MemU 风格引擎；proposal、commit、grant、approval、audit 仍由 OctoAgent 负责。

### 3.3 Builtin First

默认先让系统“可用”，再让用户“升级质量”。普通用户不用先理解 deployment。

### 3.4 Zero-Downtime Reindex

embedding 变更不能中断现网检索。旧 generation 持续服务，新 generation 后台构建、追平、校验，再 cutover。

### 3.5 One Retrieval Platform

Memory 与知识库共用同一套 retrieval platform；只允许在 corpus policy 上差异化，不允许复制一套 embedding/index 生命周期。

## 4. 参考证据

### 内部基线

- `docs/blueprint.md`
- `.specify/features/028-memu-deep-integration/`
- `.specify/features/038-agent-memory-recall-optimization/`
- `.specify/features/045-memory-panel-clarity/`
- `octoagent/packages/provider/src/octoagent/provider/dx/memory_backend_resolver.py`
- `octoagent/packages/memory/src/octoagent/memory/service.py`
- `octoagent/packages/memory/src/octoagent/memory/backends/sqlite_backend.py`
- `octoagent/frontend/src/domains/settings/SettingsPage.tsx`

### 外部参考

- OpenClaw memory local-first 心智与本地 embedding 路径
- OpenClaw 对“默认可用 + 可升级 provider”的产品表达
- 双语轻量 embedding 候选模型：
  - `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  - `Alibaba-NLP/gte-multilingual-base`

## 5. 设计切片

### Slice A - Memory 产品语义重置

- 把 `local_only` 升级为“内建 Memory Engine”
- 从普通用户主路径移除 transport 心智
- 明确 `main` alias 是最小启动前提

### Slice B - Retrieval Model Binding

- 新增 `memory_reasoning / memory_expand / memory_embedding / memory_rerank`
- 设计平台默认与 future knowledge base 共用方式
- 明确 fallback 规则

### Slice C - Builtin Embedding Layer

- 选择默认内建 embedding 方案；当前落地优先级为 `Qwen3-Embedding-0.6B`，hash embedding 仅保留兜底
- 设计本地下载、缓存、健康检查与降级
- 明确“内建 embedding 不可用 -> lexical fallback”

### Slice D - Index Generation & Build Jobs

- 定义 `EmbeddingProfile / IndexGeneration / IndexBuildJob / CutoverWindow`
- 引入 watermark + catch-up
- 定义 cancel / cutover / rollback

### Slice E - Governance Boundary

- 让 MemU 风格引擎产出 candidates
- facts / Vault 写入继续走 proposal / approval / audit
- 为 evidence refs 和 provenance 保持正式 contract

### Slice F - Shared Retrieval Platform

- 抽象 `CorpusKind = memory | knowledge_base`
- 让 LanceDB projection、index lifecycle、后台 jobs 共用
- 明确 Memory 与知识库各自的 chunking/policy 入口

### Slice G - UI / CLI / Work Visibility

- 重做 `Settings > Memory`
- 新增 migration progress card
- 在 Work / Background Jobs 暴露 index build 生命周期
- 规划 operator-only advanced transport compatibility 入口

### Slice H - 兼容迁移

- 兼容现有 `local_only / memu(command/http)` 配置
- 从配置 schema、control plane hints、CLI、README、Feature 045 表述中迁出旧心智
- 规划旧实例迁移策略与回滚

## 6. 关键架构决策

### 6.1 不迁移记录本体，只迁移 projection

切 embedding 不迁移 fragments/facts/vault，只重建 projection。这是长期稳定性的核心约束。

### 6.2 内建 embedding 是默认层，不只是 fallback

这能显著降低首次使用门槛，也让外部 alias 真正成为“升级”。

### 6.3 `cancel` 与 `rollback` 分开

- `cancel`: cutover 前终止新 generation 构建
- `rollback`: cutover 后回退到旧 generation

### 6.4 transport 保留为兼容层

实现层短期可以继续复用 `MemUBackend` 的 command/http adapter，但产品层不再要求用户理解这些概念。

## 7. 风险

- 若内建 embedding 过重，会拉高首次下载和本地资源压力
- 若 migration 状态机设计不严谨，会出现“双 generation 半生效”问题
- 若 facts / Vault 候选与治理链边界不清，会重新引入越权写入风险
- 若共享 retrieval platform 抽象不完整，知识库仍会二次分叉
- 若旧 transport 文案不及时清理，用户会同时看到两套相互冲突的心智

## 8. 验证方式

- retrieval config schema / fallback 规则单测
- builtin embedding 可用性与降级测试
- index generation 状态机单测
- cancel / cutover / rollback 集成测试
- Memory recall 在迁移期间继续可用的回归测试
- Settings / Memory / Work UI 行为测试
- future knowledge base contract 测试（至少 mock corpus）

## 9. blueprint 回写要求

实施阶段必须同步更新：

- `docs/blueprint.md` 中关于 `local_only / memu(command/http)` 的产品表述
- `README` 中 Memory 配置说明
- CLI `octo config memory *` 帮助文案
- Feature 045 中关于 transport 配置入口的既有说明

## 10. 当前规划结论

这不是“把 local_only 再写得好懂一点”的小修，而是 Memory 架构的产品化纠偏：

- 用户只看到“内建记忆 + 可升级质量”
- OctoAgent 保留治理权
- MemU 负责加工智能
- 向量库只做 projection
- embedding 迁移有正式状态机
- Memory 与知识库从第一天起共用 retrieval platform
