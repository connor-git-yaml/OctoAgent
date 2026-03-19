# Contract: ModelRerankerService

**Feature**: 065-memory-automation-pipeline (Phase 2)
**Date**: 2026-03-19
**Story**: US-6 (Retrieval Reranker 精排)

## 服务定位

`ModelRerankerService` 封装 Qwen3-Reranker-0.6B 本地 cross-encoder 模型，在 `MemoryService.recall_memory` 的粗排结果上进行精排，提升检索质量。

**调用入口**: `MemoryService._apply_recall_hooks()` -- 当 `hook_options.rerank_mode == MODEL` 时调用。

**关键约束**:
- 模型不可用时必须降级到 HEURISTIC（FR-017）
- 候选结果 < 2 条时跳过 rerank（FR-018）
- 查询热路径，延迟要求 < 500ms / 10 candidates

## 接口契约

### rerank

```python
async def rerank(
    self,
    query: str,
    candidates: list[str],
) -> RerankResult:
    """对候选文本进行 cross-encoder 精排。

    Args:
        query: 用户查询文本
        candidates: 候选记忆的文本列表（summary 或 content）

    Returns:
        RerankResult 包含与 candidates 一一对应的相关性得分。
        如果模型不可用或 candidates < 2，返回 degraded=True。

    Raises:
        不抛异常。所有错误转为 degraded 状态。
    """
```

### RerankResult

```python
@dataclass(slots=True)
class RerankResult:
    scores: list[float]           # 与 candidates 一一对应，越高越相关
    model_id: str = ""            # 使用的模型标识（如 "Qwen/Qwen3-Reranker-0.6B"）
    degraded: bool = False        # 是否降级
    degraded_reason: str = ""     # 降级原因
```

### is_available (property)

```python
@property
def is_available(self) -> bool:
    """模型是否已加载且可用。"""
```

## 模型契约

**模型 ID**: `Qwen/Qwen3-Reranker-0.6B`
**框架**: sentence-transformers `CrossEncoder` API
**输入格式**: `[{"query": str, "passage": str}]` 列表
**输出格式**: `list[float]` 相关性得分（logit，非归一化）
**设备**: CPU（MVP 阶段）
**推理延迟**: < 500ms / 10 candidates (CPU, Apple Silicon M-series)

### 模型加载策略

```python
# 后台异步加载（不阻塞启动）
async def _warmup_model(self) -> None:
    from sentence_transformers import CrossEncoder
    self._model = await asyncio.to_thread(
        CrossEncoder,
        self._RERANKER_MODEL_ID,
        trust_remote_code=True,
        device="cpu",
    )
```

**加载时机**: AgentContext 初始化时后台启动 warmup task（与 Qwen3-Embedding-0.6B 相同模式）
**首次下载**: sentence-transformers 自动从 HuggingFace 下载，预计 ~600MB
**重试策略**: 加载失败后设置 60 秒退避，避免频繁重试

## MemoryService 集成契约

### MemoryRecallRerankMode 扩展

```python
class MemoryRecallRerankMode(StrEnum):
    NONE = "none"
    HEURISTIC = "heuristic"
    MODEL = "model"          # Phase 2 新增
```

### _apply_recall_hooks 中的 MODEL 分支

**前置条件**:
- `hook_options.rerank_mode is MemoryRecallRerankMode.MODEL`
- `candidates` 非空
- `self._reranker_service is not None`

**执行逻辑**:

```
IF candidates.length < 2:
    跳过 rerank（无意义）
ELIF reranker.is_available:
    scores = reranker.rerank(query, candidate_texts)
    IF scores.degraded:
        降级到 HEURISTIC rerank
    ELSE:
        按 scores 重排 candidates
ELSE:
    降级到 HEURISTIC rerank
```

**输出 metadata 注入**:

每个 reranked candidate 的 `hit.metadata` 新增：
- `recall_rerank_score`: float -- reranker 打分
- `recall_rerank_mode`: "model" | "heuristic" -- 实际使用的模式
- `recall_rerank_model`: str -- 模型 ID（仅 model 模式有值）

### 候选文本构建

从 `MemorySearchHit` 构建 reranker 输入文本：

```python
candidate_text = hit.summary or hit.subject_key or ""
```

优先用 `summary`（内容摘要），次选 `subject_key`（主题标识）。

## 降级策略

| 场景 | rerank 调用返回 | MemoryService 行为 |
|------|----------------|-------------------|
| sentence-transformers 未安装 | degraded=True, reason="module not found" | 降级到 HEURISTIC |
| 模型下载失败 | degraded=True, reason="download failed" | 降级到 HEURISTIC |
| 模型加载中 (warmup) | degraded=True, reason="model loading" | 降级到 HEURISTIC |
| 推理异常 | degraded=True, reason="inference error: ..." | 降级到 HEURISTIC |
| candidates < 2 | degraded=True, reason="candidates < 2" | 跳过 rerank |
| 正常 | degraded=False, scores=[...] | 按分数重排 |

## 配置

### recall preferences

默认 `rerank_mode` 保持 `heuristic`。用户可通过以下方式启用 MODEL 模式：

1. `octo config memory` -> 设置 `rerank_mode: model`
2. agent_context preferences: `{"rerank_mode": "model"}`
3. memory.recall 工具参数: `rerank_mode=MemoryRecallRerankMode.MODEL`

### 模型路径

默认由 sentence-transformers 管理（`~/.cache/huggingface/`）。可通过环境变量 `TRANSFORMERS_CACHE` 自定义。

## 可观测性

- 结构化日志: `model_reranker_warmup_started`
- 结构化日志: `model_reranker_ready(model_id, embed_dim)`
- 结构化日志: `model_reranker_warmup_failed(error)`
- 结构化日志: `model_reranker_degraded(reason)`
- hit.metadata 中的 `recall_rerank_mode` / `recall_rerank_score` 供 debug 和分析

## 性能契约

| 指标 | 目标 | 测量方法 |
|------|------|---------|
| 模型加载时间 | < 5 秒（已缓存） | _warmup_model 耗时 |
| 推理延迟（10 candidates） | < 500ms (CPU, M-series) | rerank 方法耗时 |
| 内存占用 | < 1.5GB 增量 | 进程 RSS 增量 |
| 首次下载大小 | ~600MB | HuggingFace 模型文件 |
