# Research: Memory Automation Pipeline -- Phase 3 技术决策

**Date**: 2026-03-19 | **Spec**: `spec.md` | **Plan**: `plan-phase3.md`

---

## Decision 1: ToM 推理架构 -- 独立服务 vs 扩展 DerivedExtractionService

**问题**: Theory of Mind 推理生成 `derived_type="tom"` 的记录，与 Phase 2 的 `DerivedExtractionService` 输出同为 DerivedMemoryRecord。应该扩展现有 DerivedExtractionService 还是新建独立服务？

**结论**: 新建 `ToMExtractionService` 独立服务

**理由**:
1. **Prompt 差异大**: entity/relation/category 提取是从事实中抽取结构化信息（命名实体识别 NER 类任务），而 ToM 推理是从事实中推断隐含的用户心智状态（推理/分析类任务），两者的系统 prompt、输出结构、confidence 评估标准完全不同
2. **独立降级**: ToM 推理失败不应影响 entity/relation/category 的提取结果。独立服务天然隔离故障域
3. **LLM 成本控制**: 将两种提取合并到一次 LLM 调用中会显著增加 prompt 长度和输出 token 数，且混合任务时 LLM 输出质量不稳定
4. **可选性**: ToM 是 MAY 级别功能，独立服务更容易在配置中启用/禁用

**被拒方案**:
- 在 DerivedExtractionService 的 prompt 中同时要求提取所有类型 -- prompt 过载，LLM 输出混乱
- 在 DerivedExtractionService.extract_from_sors() 末尾追加 ToM 调用 -- 方法职责混乱，且异常处理不清晰

---

## Decision 2: Temporal Decay 函数 -- 指数衰减 vs 线性衰减

**问题**: FR-020 要求对旧记忆施加时间衰减因子。应使用哪种衰减函数？

**结论**: 指数衰减（Exponential Decay），半衰期默认 30 天

**理由**:
1. **OpenClaw 验证**: OpenClaw 的 memU 系统使用指数衰减（半衰期 30 天），在实际个人 AI 场景中验证有效
2. **认知科学基础**: 人类记忆遗忘曲线（Ebbinghaus）接近指数衰减
3. **平滑性**: 指数衰减在时间轴上平滑连续，不会在某个截止点出现突变
4. **可配置性**: 半衰期参数直观（"30 天后重要性减半"），用户容易理解和调整

**公式**: `decay_factor = exp(-ln(2) / half_life_days * age_days)`

**参数选择**:
- 半衰期 30 天：1 天前的记忆 ~= 0.977，7 天前 ~= 0.851，30 天前 = 0.5，90 天前 ~= 0.125
- 这意味着 3 个月前的记忆权重降低到 12.5%，符合"旧信息降权但不完全忽略"的目标

**被拒方案**:
- 线性衰减 -- 在"旧但相关"的场景中降权过于激进（90 天后可能为 0）
- Step function（阈值切割）-- 不够平滑，临界点前后行为突变
- 不衰减 -- 无法解决旧信息噪声问题

---

## Decision 3: MMR 相似度度量 -- Jaccard Token vs Embedding Cosine

**问题**: MMR 需要计算候选结果之间的相似度。应使用 embedding cosine similarity 还是 Jaccard token similarity？

**结论**: Jaccard Token Similarity（简单 token 级别）

**理由**:
1. **无额外 IO**: recall hooks 中只能访问 MemorySearchHit 的文本字段（summary、subject_key），获取 embedding 向量需要额外查询 LanceDB，增加 IO 延迟
2. **subject_key 结构化**: OctoAgent 的 SoR 使用 `/` 分层的 subject_key（如 `用户偏好/编程语言`），token 级别的 Jaccard 已能有效区分不同主题
3. **性能优先**: MMR 在 recall 热路径上，Jaccard 计算是 O(n^2 * k) 纯 CPU 操作（n=candidates, k=avg_tokens），远快于 embedding 推理
4. **OpenClaw 验证**: OpenClaw 使用 Jaccard MMR (lambda=0.7) 在实际场景中表现良好

**参数选择**:
- MMR lambda = 0.7: 权衡相关性和多样性，偏重相关性。lambda=1.0 退化为纯相关性排序，lambda=0 退化为纯多样性
- 这与 OpenClaw 的设置一致

**被拒方案**:
- Embedding cosine similarity -- 需要额外 LanceDB 查询或 embedding 推理，在 recall 热路径上增加 50-200ms 延迟
- TF-IDF cosine -- 需要预计算语料 IDF，增加维护成本
- 无去重 -- 无法解决语义重复噪声问题

**后续改进**: 如果 Jaccard 去重效果不够理想（尤其在同义词场景），可以在后续版本中引入 embedding cosine。届时可以在 BuiltinMemUBridge.search() 返回时缓存 embedding 向量到 MemorySearchHit.metadata 中，避免额外 IO。

---

## Decision 4: Temporal Decay 和 MMR 的执行顺序

**问题**: Temporal Decay 和 MMR 在 _apply_recall_hooks 中应该以什么顺序执行？

**结论**: Rerank -> Temporal Decay -> MMR -> Top-K 截断

**理由**:
1. **Rerank 不应受时间偏差**: 语义相关性排序应该纯粹基于查询匹配度，不受记忆新旧影响。Rerank（HEURISTIC 或 MODEL）在前
2. **Decay 调整后再去重**: MMR 需要使用经过时间调整后的 relevance score 作为输入，确保去重时同时考虑相关性和时效性
3. **MMR 在截断前**: MMR 去重应该在 Top-K 截断之前执行，确保最终返回的 K 条结果既新鲜又多样

**被拒方案**:
- MMR -> Decay -> Top-K -- MMR 使用未经时间调整的 score，可能保留了虽然相关但已过时的记忆
- Decay + MMR 合并为单步 -- 逻辑纠缠，难以独立配置和调试

---

## Decision 5: 用户画像存储方式 -- SoR vs 独立表

**问题**: 用户画像应该存储在哪里？是复用 SoR 表还是新建专用表？

**结论**: 复用 SoR 表，`partition=profile`

**理由**:
1. **治理一致性**: 画像是关于用户的权威信息，天然属于 SoR 层级。使用 SoR 表可以走完整的 propose/validate/commit 治理流程，符合宪法原则 12
2. **版本化**: SoR 表已有 version 字段和 SUPERSEDED 机制，画像更新天然获得版本管理能力
3. **检索兼容**: 画像作为 SoR 记录，通过 `memory.recall(subject_hint="用户画像")` 即可检索，无需新增 API
4. **零表结构变更**: 不需要 migration，零基础设施变更

**存储结构**:
- `partition`: `profile`
- `subject_key`: `用户画像/基本信息`、`用户画像/工作领域`、`用户画像/技术偏好` 等
- `content`: 自然语言描述（完整句子）
- `metadata`: `{"source": "profile_generator", "generated_at": "ISO8601"}`

**被拒方案**:
- 新建 `user_profile` 表 -- 增加表维护成本，且绕过治理流程
- 存为 DerivedMemoryRecord -- Derived 是从 SoR 提取的结构化信息，而画像是聚合摘要，层级不同

---

## Decision 6: 画像生成频率 -- 每 24 小时 vs 事件驱动

**问题**: 画像应该多久更新一次？

**结论**: Scheduler 定时每 24 小时（cron: `0 2 * * *`，UTC 凌晨 2 点）

**理由**:
1. **画像变化缓慢**: 用户偏好、工作领域等不会每小时变化，每日更新足够
2. **LLM 成本**: 画像生成需要查询大量 SoR + Derived 记录并调用 LLM，每次成本较高
3. **低峰时段**: 凌晨 2 点执行，避开用户活跃时段的 LLM 资源竞争
4. **与 Consolidate 解耦**: 不在每次 Consolidate 后立即更新，避免 Consolidate 流程变慢

**被拒方案**:
- 每次 Consolidate 后立即更新 -- LLM 成本过高，且 Consolidate 流程已经包含 Derived + ToM 提取
- 事件驱动（SoR 变化触发）-- 实现复杂，且画像本身就是对全量数据的聚合，增量更新效果不好
- 每周 -- 间隔过长，用户可能感到画像过时

---

## Decision 7: ToM 记录的消费方式

**问题**: ToM 记录生成后，Agent 如何在对话中使用？

**结论**: Phase 3 只实现存储，消费侧通过现有 recall 自然检索

**理由**:
1. **最小实现**: Phase 3 的 US-7 spec 要求"系统生成 ToM 记录，后续 recall 可检索到"，不要求显式的 prompt 注入
2. **recall 兼容**: ToM 记录作为 DerivedMemoryRecord 存储在 derived_memory 表中，现有的 recall 流程在展开 SoR 结果时会附带相关 derived_refs
3. **渐进式增强**: 后续可以在 Agent 行为文件中添加指引（"如果检测到用户知识水平标签，调整回复复杂度"），让 Agent 主动利用 ToM 信息

**被拒方案**:
- 在 recall_memory 中特殊处理 derived_type=tom -- 过早优化，且违反 "优先提供上下文" 原则
- 自动注入 system prompt -- 需要修改 Agent 启动流程，Phase 3 范围外
