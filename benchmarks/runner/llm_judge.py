"""
benchmarks/runner/llm_judge.py

LLM judge 触发逻辑模块（F-01 patch：触发常量在 Phase A 直接落地，不是 stub）。

触发条件常量（F-01 patch 强制落地，Phase D T-D-6 只升级 invoke_judge 实现）：
- LLM_JUDGE_TRIGGER_MIN_RATIO = 0.5   # match_ratio >= 0.5 才触发
- LLM_JUDGE_TRIGGER_MAX_RATIO = 1.0   # match_ratio < 1.0 才触发
- LLM_JUDGE_MAX_CALLS_PER_TASK = 2    # 每 task × iteration 最多 2 次

注意：任何修改触发常量的 PR 必须由 Codex review 拦下（known-issues-deltas F-01）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# F-01 patch：触发常量真实落地（Phase A 不是 stub，Phase D 只升级 invoke_judge 实现）
LLM_JUDGE_TRIGGER_MIN_RATIO = 0.5    # match_ratio >= 0.5 才触发 LLM judge
LLM_JUDGE_TRIGGER_MAX_RATIO = 1.0    # match_ratio < 1.0 才触发（= 1.0 即全通过，直接 PASS）
LLM_JUDGE_MAX_CALLS_PER_TASK = 2     # 每 task × iteration 最多调用 2 次 LLM judge


@dataclass
class JudgeResult:
    """LLM judge 返回结果"""
    score: float               # 0.0 ~ 1.0，越高越好
    reasoning: str             # judge 的评分理由
    is_stub: bool = False      # Phase A stub 标志（Phase D 升级后为 False）


@dataclass
class LLMJudgeTrigger:
    """
    LLM judge 触发器。

    职责：
    1. should_trigger_judge：根据 match_ratio 决定是否触发（真实判断，F-01 patch）
    2. invoke_judge：Phase A 为 stub 实现（返回固定 score=0.5）；Phase D 升级为真实 LLM 调用
    3. 每 task 最多调用 LLM_JUDGE_MAX_CALLS_PER_TASK 次（成本控制）
    """
    _call_count: int = field(default=0, init=False)

    def should_trigger_judge(self, match_ratio: float) -> bool:
        """
        判断是否应触发 LLM judge。

        触发条件（F-01 patch 强制实现，不是 stub）：
        - match_ratio >= LLM_JUDGE_TRIGGER_MIN_RATIO（0.5）
        - match_ratio < LLM_JUDGE_TRIGGER_MAX_RATIO（1.0）
        - 未超过每 task 最大调用次数（LLM_JUDGE_MAX_CALLS_PER_TASK=2）

        边界行为：
        - match_ratio < 0.5：直接 FAIL，不触发（事件匹配不足，无需 judge）
        - match_ratio = 1.0：直接 PASS，不触发（全部命中，无需 judge）
        - match_ratio in [0.5, 1.0)：触发 judge（partial 场景）
        """
        if self._call_count >= LLM_JUDGE_MAX_CALLS_PER_TASK:
            return False
        return LLM_JUDGE_TRIGGER_MIN_RATIO <= match_ratio < LLM_JUDGE_TRIGGER_MAX_RATIO

    def invoke_judge(
        self,
        task_id: str,
        prompt: str,
        expected_events: list[dict[str, Any]],
        actual_events: list[dict[str, Any]],
        match_ratio: float,
    ) -> JudgeResult:
        """
        调用 LLM judge 评分。

        Phase A：stub 实现（返回固定 score=0.5，is_stub=True）。
        Phase D（T-D-6）：升级为真实 LLM 调用（Sonnet 4.5 with prompt template）。

        注意：upgrade 时只修改此方法的实现，不修改触发常量和 should_trigger_judge。
        """
        if self._call_count >= LLM_JUDGE_MAX_CALLS_PER_TASK:
            # 超出最大调用次数，返回最后一次结果（使用默认中间分）
            return JudgeResult(
                score=0.5,
                reasoning=f"超出最大调用次数 {LLM_JUDGE_MAX_CALLS_PER_TASK}，使用默认中间分",
                is_stub=True,
            )

        self._call_count += 1

        # Phase A stub 实现：返回固定 score=0.5（Phase D 升级时替换此段）
        return JudgeResult(
            score=0.5,
            reasoning=(
                f"[Phase A Stub] task_id={task_id}, match_ratio={match_ratio:.3f}, "
                f"expected={len(expected_events)} events, actual={len(actual_events)} events. "
                f"Phase D 升级后将使用真实 LLM 评分。"
            ),
            is_stub=True,
        )

    def reset_call_count(self) -> None:
        """重置调用计数（每个新 task iteration 开始前调用）"""
        self._call_count = 0
