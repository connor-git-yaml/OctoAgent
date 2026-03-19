# Contract: Temporal Decay + MMR 去重

**Phase**: 3 | **US**: 8 | **FR**: 020, 021

## 接口概览

Phase 3 的 Temporal Decay 和 MMR 不是独立服务，而是 `MemoryService._apply_recall_hooks()` 内部的两个计算阶段。

### MemoryRecallHookOptions 扩展

```python
class MemoryRecallHookOptions(BaseModel):
    # ... Phase 1+2 已有字段 ...

    temporal_decay_enabled: bool = False
    temporal_decay_half_life_days: float = 30.0  # 半衰期天数
    mmr_enabled: bool = False
    mmr_lambda: float = 0.7  # 0=纯多样性, 1=纯相关性
```

### 内部方法

```python
# MemoryService 新增方法

def _apply_temporal_decay(
    self,
    candidates: list[tuple[int, int, int, MemorySearchHit]],
    *,
    half_life_days: float = 30.0,
) -> list[tuple[int, int, int, MemorySearchHit]]:
    """对候选结果应用指数时间衰减并重排。"""
    ...

def _apply_mmr_dedup(
    self,
    candidates: list[tuple[int, int, int, MemorySearchHit]],
    *,
    max_hits: int,
    mmr_lambda: float = 0.7,
) -> list[tuple[int, int, int, MemorySearchHit]]:
    """Maximal Marginal Relevance 去重选择。"""
    ...

@staticmethod
def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard token 集合相似度。"""
    ...
```

## Temporal Decay 规格

**公式**: `decay_factor = exp(-ln(2) / half_life_days * age_days)`

**参数**:

| 参数 | 类型 | 默认值 | 约束 | 说明 |
|------|------|--------|------|------|
| `half_life_days` | `float` | `30.0` | `> 0` | 半衰期天数 |

**衰减参考值（half_life=30d）**:

| 记忆年龄 | decay_factor |
|----------|-------------|
| 1 天 | 0.977 |
| 7 天 | 0.851 |
| 30 天 | 0.500 |
| 90 天 | 0.125 |
| 180 天 | 0.016 |

**输入**: rerank 后的 candidates 列表
**输出**: 按 `existing_score * decay_factor` 重排的 candidates 列表，metadata 中新增 `recall_temporal_decay_factor` 和 `recall_decay_adjusted_score`

## MMR 去重规格

**算法**: 迭代贪心选择

```
for i = 1 to K:
    selected[i] = argmax_{d in remaining} (
        lambda * relevance(d) - (1-lambda) * max_{s in selected} similarity(d, s)
    )
```

**参数**:

| 参数 | 类型 | 默认值 | 约束 | 说明 |
|------|------|--------|------|------|
| `mmr_lambda` | `float` | `0.7` | `[0, 1]` | 相关性 vs 多样性权衡 |
| `max_hits` | `int` | 来自 recall 参数 | `>= 1` | 最终返回的最大条数 |

**相似度度量**: Jaccard token similarity（基于空格分词后的 token 集合交并比）

**输入**: decay 调整后的 candidates 列表
**输出**: MMR 选择后的 candidates 列表（长度 <= max_hits），metadata 中新增 `recall_mmr_rank`

## 执行顺序

```
post_filter -> rerank (HEURISTIC/MODEL) -> temporal_decay -> mmr_dedup -> top-K
```

## 前置条件

- `temporal_decay_enabled=True` 时才执行 decay
- `mmr_enabled=True` 且 `candidates > 1` 时才执行 MMR
- 两者默认关闭（Phase 3 渐进式验证）

## 后置条件

- candidates 列表按调整后分数排序
- metadata 中记录所有计算参数和中间结果
- MemoryRecallHookTrace 中记录执行状态

## 降级行为

Temporal Decay 和 MMR 都是纯数学计算，无外部依赖，不存在降级场景。异常（如 division by zero）通过防御性编程避免（half_life_days 最小为 1.0，union 为 0 时 Jaccard 返回 0.0）。
