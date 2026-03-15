# Tasks - Feature 054

## T001 - 重构 Memory 配置模型

- 状态：已完成
- 将 `memory.backend_mode / bridge_transport / bridge_url / bridge_command` 从普通用户主路径迁出
- 新增 retrieval model bindings：
  - `memory_reasoning`
  - `memory_expand`
  - `memory_embedding`
  - `memory_rerank`
- 规划 Advanced/operator 兼容入口承接旧 transport 配置

## T002 - 定义 Builtin Memory Engine 运行语义

- 状态：已完成
- 把当前 `local_only` 升级为“内建 Memory Engine”
- 明确 `main` alias 是最小启动前提
- 明确内建 retrieval 与增强 retrieval 的用户语言

## T003 - 选定并接入默认内建 embedding

- 状态：已完成
- 已选定 `Qwen3-Embedding-0.6B` 作为默认主力层，并保留双语 hash embedding 作为兜底
- 默认层优先尝试本地 Qwen runtime；若本机运行时暂不可用，会自动回退到 hash embedding，再降级到 lexical / metadata fallback
- 回退状态会通过 retrieval profile 与 index health 明确暴露

## T004 - 定义 RetrievalModelBinding 与 fallback 规则

- 状态：已完成
- 落地 `memory_reasoning / memory_expand / memory_embedding / memory_rerank`
- 定义 `main -> reasoning/expand` fallback
- 定义 `builtin embedding -> lexical` fallback
- 定义 `heuristic rerank` fallback

## T005 - 定义共享 retrieval platform 数据模型

- 状态：已完成
- 新增：
  - `EmbeddingProfile`
  - `IndexGeneration`
  - `IndexBuildJob`
  - `CutoverWindow`
  - `CorpusKind`
- 统一支持 `memory | knowledge_base`

## T006 - 实现 projection-only 索引策略

- 状态：已完成
- 明确 canonical store 与 projection store 的职责
- 让 retrieval generation / build job 只描述 projection，不迁移 record 本体
- 保证切 embedding 时不迁移 record 本体

## T007 - 实现 generation 生命周期与后台作业

- 状态：已完成
- 落地 `queued / scanning / embedding / writing_projection / catching_up / validating / ready_to_cutover / completed / cancelled / failed / rolled_back`
- 引入 watermark + catch-up
- 实现 build job 持久化与事件观测

## T008 - 落地 cancel / cutover / rollback 语义

- 状态：已完成
- cutover 前允许 cancel
- cutover 后保留 rollback 窗口
- 禁止 active profile 被删除或在迁移中失效

## T009 - 把 MemU 风格加工链接到 facts / Vault 候选

- 状态：已完成
- 让引擎产出 fact/vault candidates 与 evidence refs
- 保持 proposal / approval / audit / grant 不变
- 避免引入新的硬编码加工策略

## T010 - 重做 `Settings > Memory` 产品面

- 状态：已完成
- 移除 transport 主路径
- 展示当前 retrieval bindings
- 展示默认内建层与质量升级入口
- 展示 embedding migration progress card

## T011 - 暴露后台迁移可视化与控制入口

- 状态：已完成
- 在 Memory 页面展示当前 generation 状态
- 在 Work / Background Jobs 中展示 index build 进度
- 提供 `cancel` 与 operator `rollback` 入口

## T012 - 为未来知识库接入预留共享 contract

- 状态：已完成
- 让知识库 corpus 可复用 `EmbeddingProfile / IndexGeneration`
- 预留不同 corpus 的 chunking / metadata policy
- 补齐最小 contract tests

## T013 - 兼容迁移现有 `local_only / memu(command/http)` 实例

- 状态：已完成
- 设计配置迁移与回滚方案
- 保留内部兼容 transport 解析
- 从 README / CLI / Settings / Feature 045 中移除旧主心智

## T014 - 验证与回归

- 状态：已完成
- 覆盖 builtin embedding + fallback
- 覆盖 migration progress / cancel / cutover / rollback
- 覆盖 Memory recall 在迁移期间不断流
- 覆盖 facts / Vault candidate 治理边界
- 覆盖 shared retrieval platform contract

## T015 - 文档同步

- 状态：已完成
- 更新 `docs/blueprint.md`
- 更新 `octoagent/README.md`
- 更新 CLI help / config docs
- 按需要回写 milestone split 文档
