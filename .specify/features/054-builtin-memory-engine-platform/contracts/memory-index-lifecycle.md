# Contract - Memory / Knowledge Retrieval Index Lifecycle

## 1. 目标

为 Memory 与未来知识库定义同一套 projection lifecycle contract，保证：

- canonical 数据不迁移
- embedding 切换不断流
- generation 可并存、可取消、可回滚

## 2. Canonical vs Projection

### Canonical Store

继续由 OctoAgent 本地治理层持有：

- `fragment`
- `fact`
- `vault`
- `proposal`
- `audit`
- `evidence`

这些记录的主键、版本与治理状态不依赖 embedding profile。

### Projection Store

向量层只保存可重建 projection：

- `generation_id`
- `corpus_kind`
- `record_id`
- `chunk_id`
- `embedding_profile_id`
- `text_hash`
- `chunker_version`
- `pipeline_version`
- `vector_dim`
- `vector`
- `metadata`
- `updated_at`

## 3. 核心实体

### EmbeddingProfile

- `profile_id`
- `alias_id`
- `provider_id`
- `resolved_model`
- `dim`
- `kind = builtin | alias`
- `status = ready | degraded | unavailable`
- `created_at`

### IndexGeneration

- `generation_id`
- `corpus_kind = memory | knowledge_base`
- `corpus_id`
- `embedding_profile_id`
- `state`
- `watermark`
- `processed_count`
- `total_count`
- `error_count`
- `started_at`
- `completed_at`
- `cutover_at`
- `cancelled_at`

### IndexBuildJob

- `job_id`
- `generation_id`
- `state`
- `progress_pct`
- `eta_seconds`
- `last_error`
- `last_checkpoint`

## 4. 状态机

### Generation / Job States

- `queued`
- `scanning`
- `embedding`
- `writing_projection`
- `catching_up`
- `validating`
- `ready_to_cutover`
- `completed`
- `cancelled`
- `failed`
- `rolled_back`

### 语义

- `queued`: 已受理，尚未开始扫描
- `scanning`: 枚举 canonical 数据并计算工作量
- `embedding`: 生成向量
- `writing_projection`: 写 projection store
- `catching_up`: 回放构建期间的新写入增量
- `validating`: 校验维度、数量、水位与查询健康
- `ready_to_cutover`: 可切换，但尚未切换
- `completed`: 已切到新 generation
- `cancelled`: 用户在 cutover 前取消
- `failed`: 构建失败
- `rolled_back`: 曾 cutover 但后续已回退

## 5. 切换规则

### Active Generation

- 每个 `corpus_kind + corpus_id` 同时只能有一个 `active generation`
- 查询永远只读取 active generation

### Target Generation

- 迁移期间可存在一个 `target generation`
- target generation 在 `completed` 前不可接管线上查询

### Cutover

触发条件：

- generation 已 `ready_to_cutover`
- watermark 已追平
- validation 通过

切换行为：

- 原子更新 `active_generation_id`
- 原 active generation 进入保留窗口
- 新 generation 进入 `completed`

## 6. Cancel / Rollback

### Cancel

- 仅允许在 cutover 前执行
- 取消对象是 `target generation`
- cancel 后：
  - 旧 generation 保持 active
  - 新 generation 标记为 `cancelled`

### Rollback

- 仅允许在 cutover 后、保留窗口内执行
- rollback 后：
  - 旧 generation 恢复 active
  - 新 generation 标记为 `rolled_back`

## 7. 增量追平

为避免 migration 期间漏掉新增对话与新导入文档，需要：

- 构建开始时记录 `watermark`
- 全量扫描结束后读取 `watermark` 之后的增量日志
- catch-up 完成后才允许进入 `validating`

## 8. 约束

- active embedding profile 不可删除
- 若 active profile provider 失效，系统必须显式降级为 lexical / metadata recall
- 不允许直接覆盖旧 generation 的向量数据
- 不允许 Memory 与知识库私自定义第二套 generation 状态机

## 9. UI 展示要求

Settings / Memory / Work 中至少展示：

- 当前 active profile
- target profile
- 当前阶段
- 已处理 / 总量
- 百分比
- ETA
- 最近错误
- `cancel` / `rollback` 可用性
