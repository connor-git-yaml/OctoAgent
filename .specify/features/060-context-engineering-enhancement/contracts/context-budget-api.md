# API Contract: Context Budget Planning

**Feature**: 060 Context Engineering Enhancement
**Module**: `gateway/services/context_budget.py`
**Date**: 2026-03-17

## 概览

ContextBudgetPlanner 是一个内部服务模块（非 HTTP API），在上下文构建开始时统一规划各组成部分的 token 预算分配。本文档定义其公共接口契约。

---

## ContextBudgetPlanner.plan()

### 签名

```python
def plan(
    self,
    *,
    max_input_tokens: int,
    loaded_skill_names: list[str] | None = None,
    memory_top_k: int = 6,
    has_progress_notes: bool = False,
    progress_note_count: int = 0,
) -> BudgetAllocation
```

### 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `max_input_tokens` | `int` | Yes | - | 总 token 预算上限，来自 ContextCompactionConfig |
| `loaded_skill_names` | `list[str] \| None` | No | `None` | 当前 session 已加载的 Skill 名称列表 |
| `memory_top_k` | `int` | No | `6` | Memory 回忆的 top_k 配置 |
| `has_progress_notes` | `bool` | No | `False` | 当前 task 是否有进度笔记 |
| `progress_note_count` | `int` | No | `0` | 进度笔记数量 |

### 返回值

`BudgetAllocation` 数据类，各字段满足以下不变量：

```
system_blocks_budget + skill_injection_budget + memory_recall_budget + progress_notes_budget + conversation_budget <= max_input_tokens
conversation_budget >= 800
```

### 行为规则

1. **正常路径**: 按预估常量计算各部分预算，`conversation_budget` 为剩余空间
2. **预算不足**: 如果 `max_input_tokens` 扣除系统块预算后不足 800 token 给对话，按以下优先级缩减：
   - 首先缩减 `progress_notes_budget` 到 0
   - 其次缩减 `memory_recall_budget` 到 `memory_top_k * 30`（最小值）
   - 再次缩减 `skill_injection_budget` 到 0（触发 Skill 截断）
   - 最后 `conversation_budget` 取 `max_input_tokens - system_blocks_budget` 的正数部分，下限 800
3. **无 Skill**: `loaded_skill_names` 为 None 或空列表时，`skill_injection_budget = 0`
4. **estimation_method**: 反映当前 `estimate_text_tokens()` 使用的算法（"tokenizer"/"cjk_aware"/"legacy_char_div_4"）

### 错误处理

- `max_input_tokens < 800`: 返回 `BudgetAllocation` 且 `conversation_budget = max_input_tokens`（所有预算给对话，系统块依赖 `_fit_prompt_budget()` 兜底）

---

## 调用点

### TaskService._build_task_context()

```python
# 060 改动：在调用 build_context() 之前先规划预算
budget = self._budget_planner.plan(
    max_input_tokens=self._context_compaction._config.max_input_tokens,
    loaded_skill_names=dispatch_metadata.get("loaded_skill_names", []),
    memory_top_k=self._resolve_memory_top_k(dispatch_metadata),
    has_progress_notes=await self._has_progress_notes(task_id),
    progress_note_count=await self._count_progress_notes(task_id),
)

compiled = await self._context_compaction.build_context(
    ...,
    conversation_budget=budget.conversation_budget,  # 060 新增
)
```

### AgentContextService.build_task_context()

```python
# 060 改动：接收 BudgetAllocation 供 _fit_prompt_budget() 参考
async def build_task_context(
    self,
    *,
    task: Task,
    compiled: CompiledTaskContext,
    dispatch_metadata: dict[str, Any],
    worker_capability: str | None = None,
    runtime_context: RuntimeControlContext | None = None,
    recall_plan: RecallPlan | None = None,
    budget_allocation: BudgetAllocation | None = None,  # 060 新增
    loaded_skills_content: str = "",                     # 060 新增
) -> CompiledTaskContext
```

---

## 与 _fit_prompt_budget() 的关系

| 职责 | ContextBudgetPlanner | _fit_prompt_budget() |
|------|---------------------|---------------------|
| 角色 | 上游规划者 | 下游安全网 |
| 时机 | 上下文构建开始时 | 系统块组装后 |
| 精度 | 预估（基于经验常量） | 精确（基于实际 token 计数） |
| 决策 | 分配各部分预算 | 在预算内寻找最优系统块组合 |
| 失败模式 | 预估偏差 > 20% | 暴力搜索兜底 |

BudgetPlanner 的目标是让 `_fit_prompt_budget()` 在大多数情况下（> 80%）不需要降级修剪即可找到满足预算的组合。
