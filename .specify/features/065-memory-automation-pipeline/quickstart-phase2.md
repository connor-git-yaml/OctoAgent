# Quickstart: Memory Automation Pipeline -- Phase 2

**Feature**: 065-memory-automation-pipeline
**Date**: 2026-03-19
**Prerequisites**: Phase 1 已实现并运行

## Phase 2 做了什么

Phase 2 在 Phase 1 的自动化管线基础上增加三个质量提升层：

1. **Derived Memory 自动提取** -- Consolidate 产出 SoR 后自动提取实体/关系/分类
2. **Flush Prompt 优化** -- Compaction 前让 LLM 主动判断哪些信息值得保存
3. **Reranker 精排** -- 记忆检索增加本地模型精排

## 验证 US-4: Derived Memory 自动提取

### 前置

Phase 1 的 Consolidate 流程可正常工作。

### 步骤

1. 确保有未整理的 Fragment（或手动创建测试数据）
2. 触发 Consolidate（管理台手动触发或等 Scheduler）
3. Consolidate 产出 SoR 后，检查 derived_memory 表

```bash
# 通过 SQLite 直接查看
sqlite3 data/memory.db "SELECT derived_type, subject_key, summary FROM derived_memory ORDER BY created_at DESC LIMIT 10;"
```

**预期结果**: 新产出的 SoR 对应至少一条 DerivedMemoryRecord（entity/relation/category 类型）。

**降级验证**: 如果 LLM 不可用，Consolidate 日志应包含 `consolidation_derived_extraction_failed` 警告，但 SoR 写入正常完成。

### 关键日志

```
consolidation_derived_extraction  scope_id=... extracted=3 errors=[]
```

## 验证 US-5: Flush Prompt 优化

### 前置

- Phase 1 的 memory.write 工具可正常工作
- LLM 服务可用

### 步骤

1. 启动一段足够长的对话（触发 Compaction）
2. 在对话中明确透露一些个人偏好或事实
3. 等待 Compaction 触发

**预期结果**:
- 日志中出现 `flush_prompt_completed` 表示 Flush Prompt 执行成功
- `writes` 字段 > 0 表示有信息被主动保存为 SoR
- 原有的 Fragment Flush 照常执行（不受 Flush Prompt 影响）

**降级验证**: 如果 LLM 不可用，日志出现 `flush_prompt_failed`，然后继续原有 Flush 流程。

### 关键日志

```
flush_prompt_completed  writes=2 skipped=false
```

## 验证 US-6: Reranker 精排

### 前置

- 安装 sentence-transformers: `uv pip install sentence-transformers`
- 首次使用时 Qwen3-Reranker-0.6B 会自动下载（~600MB）

### 步骤

1. 确保 Memory 中有多条语义相近但主题不同的 SoR 记录
2. 启用 MODEL rerank 模式：

```bash
# 通过 octo config 或直接修改 preferences
octo config memory --set rerank_mode=model
```

3. 执行 memory.recall 查询
4. 检查返回结果的 metadata

**预期结果**:
- `recall_rerank_mode: "model"` 表示使用了模型精排
- `recall_rerank_score` 字段有值
- 与查询最相关的记忆排在前列

**降级验证**:
- 不安装 sentence-transformers 时，设置 rerank_mode=model 后，日志出现 reranker 降级信息，回退到 heuristic
- 候选结果 < 2 条时，自动跳过 rerank

### 关键 metadata

```json
{
  "recall_rerank_mode": "model",
  "recall_rerank_score": 0.8723,
  "recall_rerank_model": "Qwen/Qwen3-Reranker-0.6B"
}
```

## 配置参考

### Reranker 模式切换

| 模式 | 说明 | 延迟 | 质量 |
|------|------|------|------|
| `none` | 不做 rerank | 最低 | 最低 |
| `heuristic` | 启发式规则排序（默认） | 低 | 中 |
| `model` | Qwen3-Reranker-0.6B 精排 | 中（< 500ms） | 高 |

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TRANSFORMERS_CACHE` | HuggingFace 模型缓存目录 | `~/.cache/huggingface/` |

## 故障排查

### Derived 提取无输出

1. 检查 Consolidate 是否成功产出 SoR（`consolidation_fact_committed` 日志）
2. 检查 `consolidation_derived_extraction` 日志的 `extracted` 字段
3. 如果 `errors` 非空，检查 LLM 连接和模型配置

### Flush Prompt 未触发

1. 确认对话长度足以触发 Compaction
2. 检查 `FlushPromptInjector` 是否在 AgentContext 中注册
3. 检查 LLM 服务是否可用

### Reranker 始终降级

1. 确认 sentence-transformers 已安装: `python -c "import sentence_transformers; print('OK')"`
2. 检查模型是否下载完成: `ls ~/.cache/huggingface/hub/models--Qwen--Qwen3-Reranker-0.6B/`
3. 检查 `model_reranker_warmup_failed` 日志
4. 尝试手动触发 warmup: 重启服务或等待退避时间（60 秒）后自动重试
