# Quickstart: Memory Automation Pipeline -- Phase 3

**前提**: Phase 1 (ConsolidationService, memory.write, Scheduler) 和 Phase 2 (DerivedExtractionService, FlushPromptInjector, ModelRerankerService) 已实现并正常运行。

---

## 1. Theory of Mind 推理（US-7）

### 开发顺序

1. 创建 `tom_extraction_service.py` -- 参考 `derived_extraction_service.py` 的结构
2. 修改 `consolidation_service.py` -- 在 Derived 提取后添加 ToM hook
3. 修改 `agent_context.py` -- 注入 ToMExtractionService
4. 编写测试

### 关键文件

```
packages/provider/src/octoagent/provider/dx/tom_extraction_service.py    # NEW
packages/provider/src/octoagent/provider/dx/consolidation_service.py     # MODIFY
apps/gateway/src/octoagent/gateway/services/agent_context.py             # MODIFY
```

### 验证方法

```python
# 手动触发 Consolidate（管理台或 API）
# POST /api/control-plane/actions
# {"action_id": "memory.consolidate"}

# 检查 derived_memory 表中是否有 derived_type="tom" 的记录
# GET /api/memory/derived?derived_types=tom
```

### 注意事项

- `ToMExtractionService` 构造需要 `SqliteMemoryStore`、`LlmServiceProtocol`、`project_root` -- 与 `DerivedExtractionService` 完全相同
- ConsolidationService 构造新增 `tom_extraction_service` 可选参数，为 None 时跳过 ToM 提取
- ToM 提取在 Derived 提取之后执行，两者互不影响

---

## 2. Temporal Decay + MMR 去重（US-8）

### 开发顺序

1. 修改 `models/integration.py` -- 扩展 MemoryRecallHookOptions 和 MemoryRecallHookTrace
2. 修改 `service.py` -- 在 `_apply_recall_hooks` 中添加 decay + MMR 阶段
3. 编写测试

### 关键文件

```
packages/memory/src/octoagent/memory/models/integration.py    # MODIFY
packages/memory/src/octoagent/memory/service.py               # MODIFY
packages/memory/tests/test_recall_decay_mmr.py                # NEW
```

### 验证方法

```python
# 在测试中构造不同年龄的 MemorySearchHit，验证 decay 排序
# 构造语义重复的 hits，验证 MMR 去重效果

# 通过 API 测试（需要在 hook_options 中启用）
# POST /api/memory/recall
# {
#     "query": "用户偏好",
#     "hook_options": {
#         "rerank_mode": "heuristic",
#         "temporal_decay_enabled": true,
#         "temporal_decay_half_life_days": 30.0,
#         "mmr_enabled": true,
#         "mmr_lambda": 0.7
#     }
# }
```

### 注意事项

- Temporal Decay 和 MMR 默认关闭（`enabled=False`），需要显式启用
- `_apply_temporal_decay` 使用 `math.exp`，不需要 numpy
- `_apply_mmr_dedup` 使用 Jaccard token similarity，不需要 embedding
- 执行顺序：post_filter -> rerank -> temporal_decay -> mmr_dedup -> top-K

---

## 3. 用户画像自动生成（US-9）

### 开发顺序

1. 创建 `profile_generator_service.py`
2. 修改 `control_plane.py` -- 注册 action handler + Scheduler 作业
3. 修改 `agent_context.py` -- 注入 ProfileGeneratorService
4. 编写测试

### 关键文件

```
packages/provider/src/octoagent/provider/dx/profile_generator_service.py    # NEW
apps/gateway/src/octoagent/gateway/services/control_plane.py                # MODIFY
apps/gateway/src/octoagent/gateway/services/agent_context.py                # MODIFY
```

### 验证方法

```python
# 手动触发画像生成
# POST /api/control-plane/actions
# {"action_id": "memory.profile_generate"}

# 检查 SoR 表中 partition=profile 的记录
# GET /api/memory/read?partition=profile

# 验证画像内容
# GET /api/memory/search?query=用户画像&partition=profile
```

### 注意事项

- 画像写入走完整 propose/validate/commit 治理流程
- SoR 记录数 < 5 且 Derived < 3 时跳过生成（避免低质量画像）
- Scheduler 作业 `system:memory-profile-generate` 在系统首次启动时自动创建
- 默认每天凌晨 2 点 UTC 执行

---

## 实现建议的整体顺序

1. **Task 3 (Temporal Decay + MMR)** -- 纯计算逻辑，无外部依赖，最容易实现和测试
2. **Task 1+2 (ToM)** -- 参考已有 DerivedExtractionService 模式，改动范围可控
3. **Task 4+5 (用户画像)** -- 涉及新 Scheduler 作业注册，参考已有 memory.consolidate 模式

## 测试策略

| 组件 | 测试类型 | 重点 |
|------|---------|------|
| ToMExtractionService | 单元测试 | mock LLM 响应，验证 derived_type=tom 的记录生成 |
| Temporal Decay | 单元测试 | 构造不同 created_at 的 hits，验证衰减因子和排序 |
| MMR 去重 | 单元测试 | 构造高相似度 hits，验证去重效果和 lambda 参数影响 |
| ProfileGeneratorService | 单元测试 | mock LLM + memory.write，验证 6 维度画像生成 |
| Consolidation + ToM | 集成测试 | 真实 Consolidate 后验证 ToM 记录是否正确生成 |
| recall + decay + MMR | 集成测试 | 端到端 recall 验证排序和去重效果 |
