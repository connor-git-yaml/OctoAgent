"""Feature 060: 全局 token 预算规划器。

在上下文构建开始时统一规划各组成部分的 token 预算分配，
消除压缩层/装配层/Skill 注入三段断裂。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import structlog

from .context_compaction import (
    ContextCompactionConfig,
    estimation_method,
    estimate_text_tokens,
)

log = structlog.get_logger()


@dataclass(frozen=True)
class BudgetAllocation:
    """全局 token 预算分配结果。"""

    max_input_tokens: int
    """总 token 预算上限（来自 ContextCompactionConfig）"""

    system_blocks_budget: int
    """系统块预算（AgentProfile + Owner + Behavior + ToolGuide + AmbientRuntime + Bootstrap + RuntimeContext）"""

    skill_injection_budget: int
    """已加载 Skill 内容预估 token 数"""

    memory_recall_budget: int
    """Memory 回忆预估 token 数"""

    progress_notes_budget: int
    """Worker 进度笔记预估 token 数"""

    conversation_budget: int
    """对话历史预算 = max_input_tokens - 各系统组件预算"""

    estimation_method: str
    """token 估算方法: "tokenizer" | "cjk_aware" | "legacy_char_div_4" """

    detail: dict[str, Any] = field(default_factory=dict)
    """调试用详情，如各组件的预估明细"""


class ContextBudgetPlanner:
    """在上下文构建开始时统一规划各组成部分的 token 预算分配。

    目标：让 `_fit_prompt_budget()` 在大多数情况下（> 80%）不需要降级修剪
    即可找到满足预算的组合。
    """

    # 系统块基础开销预估（基于代码审查的经验值）
    _SYSTEM_BLOCKS_BASE: int = 1800
    # AgentProfile ~80 + Owner ~150 + Behavior 400-1200 + ToolGuide 200-400
    # + AmbientRuntime ~120 + Bootstrap ~100 + RuntimeContext ~100

    _SKILL_PER_ENTRY: int = 250  # 每个 Skill 平均 token
    _MEMORY_PER_HIT: int = 60  # 每个 memory hit 平均 token
    _PROGRESS_NOTE_PER_ENTRY: int = 80  # 每条进度笔记平均 token
    _SESSION_REPLAY_BUDGET: int = 400  # SessionReplay 预留
    _MIN_CONVERSATION_BUDGET: int = 800  # 对话预算下限

    def __init__(
        self,
        *,
        config: ContextCompactionConfig | None = None,
    ) -> None:
        self._config = config or ContextCompactionConfig.from_env()

    def plan(
        self,
        *,
        max_input_tokens: int,
        loaded_skill_names: list[str] | None = None,
        memory_top_k: int = 6,
        has_progress_notes: bool = False,
        progress_note_count: int = 0,
    ) -> BudgetAllocation:
        """计算各组成部分的 token 预算分配。

        Args:
            max_input_tokens: 总 token 预算上限
            loaded_skill_names: 当前 session 已加载的 Skill 名称列表
            memory_top_k: Memory 回忆的 top_k 配置
            has_progress_notes: 当前 task 是否有进度笔记
            progress_note_count: 进度笔记数量

        Returns:
            BudgetAllocation 数据类，各字段满足不变量：
            - 各部分之和 <= max_input_tokens
            - conversation_budget >= 800
        """
        # 极端情况：max_input_tokens 太小
        if max_input_tokens < self._MIN_CONVERSATION_BUDGET:
            return BudgetAllocation(
                max_input_tokens=max_input_tokens,
                system_blocks_budget=0,
                skill_injection_budget=0,
                memory_recall_budget=0,
                progress_notes_budget=0,
                conversation_budget=max_input_tokens,
                estimation_method=estimation_method(),
                detail={"reason": "max_input_tokens_below_minimum"},
            )

        # 1. 系统块基础开销
        system_blocks_budget = self._SYSTEM_BLOCKS_BASE + self._SESSION_REPLAY_BUDGET

        # 2. Skill 注入预估
        skill_names = loaded_skill_names or []
        skill_injection_budget = len(skill_names) * self._SKILL_PER_ENTRY

        # 3. Memory 回忆预估
        memory_recall_budget = memory_top_k * self._MEMORY_PER_HIT

        # 4. 进度笔记预估
        progress_notes_budget = 0
        if has_progress_notes and progress_note_count > 0:
            inject_count = min(progress_note_count, 5)
            progress_notes_budget = inject_count * self._PROGRESS_NOTE_PER_ENTRY

        # 5. 计算对话预算
        overhead = (
            system_blocks_budget
            + skill_injection_budget
            + memory_recall_budget
            + progress_notes_budget
        )
        conversation_budget = max_input_tokens - overhead

        detail: dict[str, Any] = {
            "system_blocks_base": self._SYSTEM_BLOCKS_BASE,
            "session_replay_budget": self._SESSION_REPLAY_BUDGET,
            "skill_count": len(skill_names),
            "memory_top_k": memory_top_k,
            "progress_note_count": progress_note_count,
            "overhead_total": overhead,
            "initial_conversation_budget": conversation_budget,
        }

        # 6. 预算不足时按优先级缩减
        if conversation_budget < self._MIN_CONVERSATION_BUDGET:
            detail["budget_reduction"] = True
            # 首先缩减进度笔记到 0
            if progress_notes_budget > 0:
                overhead -= progress_notes_budget
                progress_notes_budget = 0
                conversation_budget = max_input_tokens - overhead

            # 其次缩减 memory 到最小值
            if conversation_budget < self._MIN_CONVERSATION_BUDGET:
                min_memory = memory_top_k * 30  # 最小 memory 预算
                if memory_recall_budget > min_memory:
                    saved = memory_recall_budget - min_memory
                    memory_recall_budget = min_memory
                    overhead -= saved
                    conversation_budget = max_input_tokens - overhead

            # 再次缩减 Skill 到 0
            if conversation_budget < self._MIN_CONVERSATION_BUDGET:
                if skill_injection_budget > 0:
                    overhead -= skill_injection_budget
                    skill_injection_budget = 0
                    conversation_budget = max_input_tokens - overhead

            # 缩减 memory 到 0
            if conversation_budget < self._MIN_CONVERSATION_BUDGET:
                if memory_recall_budget > 0:
                    overhead -= memory_recall_budget
                    memory_recall_budget = 0
                    conversation_budget = max_input_tokens - overhead

            # 最后缩减 system_blocks_budget
            if conversation_budget < self._MIN_CONVERSATION_BUDGET:
                conversation_budget = self._MIN_CONVERSATION_BUDGET
                # 回算 system_blocks_budget 使总和不超 max_input_tokens
                remaining = (
                    max_input_tokens
                    - conversation_budget
                    - skill_injection_budget
                    - memory_recall_budget
                    - progress_notes_budget
                )
                system_blocks_budget = max(0, remaining)

        # 最终不变量检查：确保总和 <= max_input_tokens
        total = (
            system_blocks_budget
            + skill_injection_budget
            + memory_recall_budget
            + progress_notes_budget
            + conversation_budget
        )
        if total > max_input_tokens:
            # 安全兜底：按优先级逐项缩减非对话预算
            excess = total - max_input_tokens
            if excess > 0 and progress_notes_budget > 0:
                reduction = min(progress_notes_budget, excess)
                progress_notes_budget -= reduction
                excess -= reduction
            if excess > 0 and memory_recall_budget > 0:
                reduction = min(memory_recall_budget, excess)
                memory_recall_budget -= reduction
                excess -= reduction
            if excess > 0 and skill_injection_budget > 0:
                reduction = min(skill_injection_budget, excess)
                skill_injection_budget -= reduction
                excess -= reduction
            if excess > 0 and system_blocks_budget > 0:
                reduction = min(system_blocks_budget, excess)
                system_blocks_budget -= reduction
                excess -= reduction
            # 重新计算对话预算
            conversation_budget = max(
                self._MIN_CONVERSATION_BUDGET,
                max_input_tokens - system_blocks_budget - skill_injection_budget - memory_recall_budget - progress_notes_budget,
            )

        detail["final_conversation_budget"] = conversation_budget

        return BudgetAllocation(
            max_input_tokens=max_input_tokens,
            system_blocks_budget=system_blocks_budget,
            skill_injection_budget=skill_injection_budget,
            memory_recall_budget=memory_recall_budget,
            progress_notes_budget=progress_notes_budget,
            conversation_budget=conversation_budget,
            estimation_method=estimation_method(),
            detail=detail,
        )
